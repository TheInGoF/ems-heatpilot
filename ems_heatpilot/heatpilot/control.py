"""Schreibzugriffe über Home Assistant: Thermostate (Bedienung im Reiter „Heizen“, Regelschleife, seit 0.34),
das Warmwasser-Soll (Weg W, seit 0.38), Vorlauf-Soll und Heizbetrieb (Weg B, seit 0.41) und Benachrichtigungen
(seit 0.38). Die einzige Stelle, die Dienste aufruft.

Erlaubt sind ausschließlich diese Aufrufe auf die ``climate.*``-Entitäten der gewählten Räume:

* ``tado.set_climate_timer`` – Tado hält die Temperatur für die Dauer und kehrt danach
  selbst zum Zeitplan zurück (nur Entitäten der Tado-Cloud-Integration),
* ``climate.set_temperature`` – Soll eines Raums (Bedienung, Regelschleife, lokaler Boost),
* ``climate.set_hvac_mode`` – Zeitplan (``auto``), Aus (``off``) oder Manuell (``heat``).

Dazu seit 0.38 genau ein Schreibweg zum Kessel: ``number.set_value`` auf die eine eingestellte Entität des
Warmwasser-Solls (``dhw_entity``, nur mit Schalter „heatpilot“ gesetzt), ganzzahlig und nur zwischen Minimum und
Maximum aus den Einstellungen (``set_dhw``; die Regel dazu steht in ``dhw_control``) – und nur, wenn die Entität
taugt (``dhw_entity.entity_problem``: verfügbar, Einheit °C, kein Heizkreis, kein anderer Kesselwert wie das
Vorlauf-Soll). Und Meldungen nur an die eingestellten Ziele (``notify``): notify-Entitäten über
``notify.send_message``, ältere Dienste als ``notify.<dienst>``; scheitert eine Entität (nicht am Titel, kein
Zeitüberlauf, keine getrennte Verbindung – die Meldung könnte schon angekommen sein) und gibt es den Dienst
``notify.mobile_app_<name>``, einmal über ihn (seit 0.41) – nicht, wenn der Dienst selbst als Ziel gewählt ist.

Seit 0.41 (Weg B, nur mit Schalter „Kessel über HeatPilot: heatpilot“ gesetzt) genau zwei weitere Schreibwege zum
Kessel: ``number.set_value`` auf die eine Entität des Vorlauf-Solls (``set_flow``: ganzzahlig, 35 °C bis
``max_flow_temp`` und höchstens die Fußboden-Grenze mit den Messwerten von jetzt, ``floor_limit.flow_cap``; innerhalb
der Grenzen der Entität) und ``switch.turn_on``/``turn_off`` auf die eine Entität des Heizbetriebs (``set_heating``) –
nur, wenn die Entität taugt (``boiler_entity``) und kein Regler am Bus ist (``boiler_lock``); eingeschaltet wird nur,
wenn das Vorlauf-Soll am Kessel laut Home Assistant höchstens bei der Grenze liegt oder HeatPilot gerade einen Wert
darunter geschrieben hat (``_flow_safe``). Sind Vorlauf-Soll und Warmwasser-Soll dieselbe Entität, sind beide
Schreibwege gesperrt (``conflict``). Die Antipendelzeit und alle anderen Kesselwerte bleiben unberührt.

Seit 0.42 (Desinfektion auslösen) genau ein weiterer: ``switch.turn_on``/``turn_off`` auf die eine Entität des Schalters
„Desinfizieren“ (``set_disinfect``; ``disinfect_entity.check_call``: nur diese, ohne Daten, tauglich) – nur mit Termin
und Warmwasser-Soll „HeatPilot setzt“ gesetzt, und nur, solange nichts sperrt (``disinfect_lock``: kein Regler am Bus,
keine Pause von Weg W). Nie dieselbe Entität wie der Heizbetrieb.

Alles andere – andere Dienste, andere ``number``-Entitäten, Werte außerhalb der Grenzen, Entitäten außerhalb der
gewählten Räume – wird hart abgewiesen (``PermissionError``).
Ebenso seit 0.36.3 jeder Schreibvorgang auf den Heizkreis von EMS-ESP (``climate.…_thermostat_hc1``),
auch wenn er als Raum gewählt ist: Gehört das Ziel zu einem Raum mit ``Room.ems`` (Gerät von EMS-ESP,
siehe ``ems_lock``), wird es hart abgewiesen (``PermissionError``) – sonst ginge ``climate.set_temperature``
über Home Assistant an den Regler. An EMS-ESP selbst schickt HeatPilot nur Lese-Anfragen (``emsesp.py``,
GET); auch das Warmwasser-Soll geht über Home Assistant. Jeder erfolgreiche Schreibvorgang landet im Protokoll
``control_log`` (Raum bzw. Entität, Dienst, Wert, Grund).

Ein Raum ist eine Gruppe (``discovery.Room``): mit Raumregler wird nur dieser beschrieben,
sonst alle Ventile des Raums. Boost auf Knopfdruck: Tado-Cloud-Entitäten über den Tado-Timer,
alle anderen (z. B. HomeKit) mit eigenem Timer – Ablauf in ``boosts`` gemerkt, die Regelschleife
stellt danach den Wunschzustand bzw. das Soll von vorher wieder her (``end_boost``). Ein gespeicherter Boost
auf den Heizkreis von EMS-ESP (aus 0.36.2, vor der Sperre) lässt sich so nicht zurückstellen: Er wird beim
Aufbau verworfen und steht als Hinweis im Reiter „Heizen“ (``notes``), bis das Regler-Soll von Hand zurück ist.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from . import boiler_entity, disinfect_entity, floor_limit
from .boiler_rule import CHECK_MAX_S, CHECK_TOL_K, FLOW_MIN, PUMP_FLOW
from .config import NOTIFY_TARGET
from .dhw_entity import entity_problem
from .discovery import Room
from .ems_lock import LOCK_TEXT
from .ha_client import HAConnectionLost

log = logging.getLogger(__name__)

# ("number", "set_value") nur für das Warmwasser-Soll (Weg W, 0.38) und das Vorlauf-Soll (Weg B, 0.41),
# ("switch", "turn_on"/"turn_off") nur für den Heizbetrieb (Weg B, 0.41) und den Schalter der Desinfektion (0.42)
ALLOWED = {("tado", "set_climate_timer"), ("climate", "set_temperature"), ("climate", "set_hvac_mode"),
           ("number", "set_value"), ("switch", "turn_on"), ("switch", "turn_off")}
SERVICES_S = 600                         # Dienste von HA (Rückfall auf notify.mobile_app_…) so lange merken
NOTIFY_ENTITY_SERVICE = "send_message"   # Aktion für notify-Entitäten (HA ab 2024.6)
NOTIFY_MAX = 1000                        # Meldungen kürzen
MODES = ("auto", "off", "heat")
TEMP_MIN, TEMP_MAX = 5.0, 25.0
DUR_MIN, DUR_MAX = 15, 12 * 60
KV = "control.boosts"
KV_EMS_LEFT = "control.ems_boosts_left"  # verworfene Boosts auf den Heizkreis von EMS-ESP (0.36.3), für den Hinweis
TADO_PLATFORM = "tado"  # nur diese Integration hat den Timer-Dienst
HINT_SCHEDULE = "Zeitplan in der Tado-App fortsetzen"


class ControlError(ValueError):
    pass


def _hhmmss(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Control:
    def __init__(self, ha, store, rooms: list, states: dict | None = None, dhw_entity: str | None = None,
                 dhw_limits: tuple[float, float] | None = None, notify_targets=(), boiler: dict | None = None,
                 boiler_lock=None, disinfect: str | None = None, disinfect_lock=None) -> None:
        """``rooms``: ``discovery.Room`` (Gruppen) oder nur climate-IDs (jede ihr eigener Raum).
        ``states``: HA-Zustände (``engine.states``), um Betriebsart und Soll vor dem Schreiben zu kennen.
        ``dhw_entity``/``dhw_limits`` (0.38): die eine ``number``-Entität des Warmwasser-Solls und (Minimum, Maximum)
        – nur mit Schalter „heatpilot“ gesetzt, sonst None (dann ist ``set_dhw`` gesperrt).
        ``notify_targets`` (0.38): die eingestellten Benachrichtigungsziele (``notify.*``).
        ``boiler`` (0.41, Weg B): {"flow": number-Entität, "heating": switch-Entität, "max": max_flow_temp,
        "floors": Fußboden-Räume (``floor_limit.floor_rooms``), "pump": „nur Pumpe“ erlaubt (0.46.5, Standard ja)} –
        nur mit Schalter „heatpilot“ gesetzt, sonst None
        (dann sind ``set_flow``/``set_heating`` gesperrt); ``boiler_lock``: Aufruf, der den Grund liefert, solange
        nicht geschrieben werden darf (Regler am Bus). ``disinfect``/``disinfect_lock`` (0.42): der eine Schalter der
        Desinfektion (nur mit Termin und „HeatPilot setzt“, sonst None) und der Aufruf mit dem Grund, solange nicht
        geschaltet werden darf."""
        self.ha = ha
        self.store = store
        self.dhw_entity = dhw_entity if dhw_entity and dhw_entity.startswith("number.") else None
        self.dhw_limits = dhw_limits
        self.notify_targets: set[str] = {t for t in notify_targets or () if NOTIFY_TARGET.match(str(t))}
        b = boiler or {}
        self.flow_entity = b.get("flow") if str(b.get("flow") or "").startswith("number.") else None
        self.heat_entity = b.get("heating") if str(b.get("heating") or "").startswith("switch.") else None
        self.flow_max, self.floors, self.boiler_lock = b.get("max"), list(b.get("floors") or ()), boiler_lock
        self.flow_pump = bool(b.get("pump", True))  # 0.46.5: kein Bedarf – Vorlauf-Soll ab PUMP_FLOW unter 35 °C
        ok = str(disinfect or "").startswith("switch.") and disinfect != self.heat_entity
        self.disinfect_entity, self.disinfect_lock = (disinfect if ok else None), disinfect_lock
        # dieselbe Entität für Vorlauf- und Warmwasser-Soll: beide Schreibwege gesperrt (Weg B, 0.41)
        self.conflict = (f"Vorlauf-Soll und Warmwasser-Soll sind dieselbe Entität ({self.flow_entity}) – beide "
                         "Schreibwege gesperrt" if self.flow_entity and self.flow_entity == self.dhw_entity else None)
        if self.conflict:
            log.error(self.conflict)
        self._flow_sent: tuple[int, float] | None = None  # zuletzt geschriebenes Vorlauf-Soll (Wert, Zeit)
        self._services: tuple[float, dict] | None = None
        self.groups: dict[str, Room] = {}
        for r in rooms:
            room = r if isinstance(r, Room) else Room(name=r, climate=r)
            self.groups[room.climate] = room
        # alles, was überhaupt beschrieben werden darf (Schlüssel und Mitläufer) – ohne den Heizkreis von EMS-ESP
        self.ems: set[str] = {c for room in self.groups.values() if room.ems for c in room.climates}
        self.rooms: set[str] = {c for room in self.groups.values() for c in room.climates} - self.ems
        self.states = states if states is not None else {}
        # laufende Boosts je Raum: {temp, end, local, before, before_mode, targets}
        self.boosts: dict[str, dict] = dict(store.get(KV) or {})
        self.ems_left: dict[str, dict] = dict(store.get(KV_EMS_LEFT) or {})
        self._drop_ems_boosts()

    def _drop_ems_boosts(self) -> None:
        """Gespeicherte Boosts auf den Heizkreis von EMS-ESP (aus 0.36.2) verwerfen – zurückschreiben ist gesperrt.

        Sie bleiben als Hinweis (``notes``): Das Regler-Soll steht noch auf dem Boost und muss von Hand zurück.
        """
        dropped = {r: b for r, b in self.boosts.items() if r in self.ems or set(b.get("targets") or ()) & self.ems}
        if not dropped:
            return
        for r, b in dropped.items():
            self.boosts.pop(r)
            log.warning("Boost auf %s verworfen (%s): %s", r, LOCK_TEXT, self._left_text(r, b))
        self.ems_left.update(dropped)
        self.store.put(KV_EMS_LEFT, self.ems_left)
        self._save()

    def _left_text(self, room: str, b: dict) -> str:
        name = self.groups[room].name if room in self.groups else room
        before = b.get("before")
        back = f"auf {before:g} °C " if isinstance(before, (int, float)) else ""
        return (f"{name}: Boost {b.get('temp', 0):g} °C aus einer früheren Version steht noch am Regler – HeatPilot "
                f"schreibt nicht an den Heizkreis, Regler-Soll von Hand {back}zurückstellen")

    def notes(self) -> list[str]:
        """Hinweise für den Reiter „Heizen“: verworfene Boosts auf den Heizkreis von EMS-ESP, deren Soll noch am
        Regler steht. Hat jemand das Soll geändert oder den Heizkreis ausgeschaltet, fällt der Hinweis weg."""
        out, done = [], []
        for r, b in self.ems_left.items():
            current = self.target(r)
            if self._state(r) and (current is None or abs(current - float(b.get("temp", 0))) >= 0.1):
                done.append(r)
            else:
                out.append(self._left_text(r, b))
        if done:
            self.ems_left = {r: b for r, b in self.ems_left.items() if r not in done}
            self.store.put(KV_EMS_LEFT, self.ems_left)
            self.store.commit()
        return out

    # ------------------------------------------------------------------ Prüfungen
    def _check_rooms(self, rooms) -> list[str]:
        rooms = [r for r in (rooms or []) if isinstance(r, str)]
        if not rooms:
            raise ControlError("Kein Raum gewählt")
        bad = [r for r in rooms if r not in self.groups or not r.startswith("climate.")]
        if bad:
            raise ControlError(f"Unbekannter Raum: {', '.join(bad)}")
        return rooms

    @staticmethod
    def _temp(value, step: float = 0.5) -> float:
        """Auf den Schritt gerundet (Standard 0,5 K; die Ventilführung, 0.40, nutzt den des Geräts), 5–25 °C."""
        if not 0 < step <= 5:
            raise ControlError("Schrittweite muss zwischen 0 und 5 K liegen")
        try:
            t = round(round(float(value) / step) * step, 2)
        except (TypeError, ValueError):
            raise ControlError("Temperatur fehlt") from None
        if not TEMP_MIN <= t <= TEMP_MAX:
            raise ControlError(f"Temperatur muss zwischen {TEMP_MIN:g} und {TEMP_MAX:g} °C liegen")
        return t

    def _state(self, entity_id: str) -> dict:
        return self.states.get(entity_id) or {}

    def _mode(self, entity_id: str) -> str | None:
        return self._state(entity_id).get("state")

    def target(self, entity_id: str) -> float | None:
        """Aktuelles Soll laut HA (None bei „aus“ oder unbekannt)."""
        st = self._state(entity_id)
        if not st or st.get("state") == "off":
            return None
        return _num(st.get("attributes", {}).get("temperature"))

    def platform(self, entity_id: str) -> str | None:
        for room in self.groups.values():
            for d in room.devices:
                if d["climate"] == entity_id:
                    return d.get("platform")
        return None

    def is_tado(self, entity_id: str) -> bool:
        return self.platform(entity_id) == TADO_PLATFORM

    # ------------------------------------------------------------------ Schreiben
    async def _call(self, domain: str, service: str, entity_id: str, data: dict, reason: str = "Knopfdruck",
                    action: str | None = None) -> None:
        if entity_id in self.ems:  # EMS-ESP nur lesen – auch als Raum gewählt (0.36.3)
            raise PermissionError(f"{domain}.{service} auf {entity_id} ist gesperrt: {LOCK_TEXT}")
        if (domain, service) not in ALLOWED:
            raise PermissionError(f"{domain}.{service} auf {entity_id} ist gesperrt")
        if domain == "number" and self.conflict and entity_id == self.flow_entity:
            raise PermissionError(self.conflict)
        if domain == "number" and self.flow_entity and entity_id == self.flow_entity:
            self._check_flow(entity_id, data)  # nur das Vorlauf-Soll, nur in den Grenzen (Weg B, 0.41)
        elif domain == "number":
            self._check_dhw(entity_id, data)  # nur das eingestellte Warmwasser-Soll, nur in den Grenzen (0.38)
        elif domain == "switch" and self.disinfect_entity and entity_id == self.disinfect_entity:  # Desinfektion (0.42)
            disinfect_entity.check_call(entity_id, self.disinfect_entity, data, self._state(entity_id),
                                        self.disinfect_lock() if self.disinfect_lock is not None else None)
        elif domain == "switch":
            self._check_heating(entity_id, data, service == "turn_on")  # nur der Heizbetrieb (Weg B, 0.41)
        elif entity_id not in self.rooms:
            raise PermissionError(f"{domain}.{service} auf {entity_id} ist gesperrt")
        await self.ha.call({"type": "call_service", "domain": domain, "service": service,
                            "service_data": data, "target": {"entity_id": entity_id}}, timeout=30)
        if service == "set_hvac_mode":
            reason = f"{reason} ({data.get('hvac_mode')})"
        value = data.get("temperature", data.get("value"))
        if domain == "switch":
            value = int(service == "turn_on")
        if domain == "number" and entity_id == self.flow_entity:
            self._flow_sent = (int(value), time.time())
        self.store.add_control_log(int(time.time()), entity_id, action or service, value, reason)

    def _check_dhw(self, entity_id: str, data: dict) -> None:
        """Warmwasser-Soll (Weg W): nur die eine eingestellte ``number``-Entität, nur ``value``, ganzzahlig,
        Minimum ≤ Wert ≤ Maximum (Einstellungen und, wenn HA sie meldet, die Grenzen der Entität); die Entität muss
        taugen (``dhw_entity.entity_problem``: verfügbar, Einheit °C, kein Heizkreis, kein anderer Kesselwert).
        Alles andere hart abgewiesen."""
        if not self.dhw_entity or entity_id != self.dhw_entity or not self.dhw_limits:
            raise PermissionError(f"number.set_value auf {entity_id} ist gesperrt – nur das eingestellte "
                                  "Warmwasser-Soll bei „HeatPilot setzt“")
        value = data.get("value")
        lo, hi = self.dhw_limits
        if set(data) != {"value"} or isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise PermissionError(f"Warmwasser-Soll {value!r} gesperrt – nur ganze Grad von {lo:g} bis {hi:g} °C")
        problem = entity_problem(entity_id, self._state(entity_id))
        if problem:
            raise PermissionError(f"{problem} – gesperrt")
        attrs = self._state(entity_id).get("attributes") or {}
        e_lo, e_hi = _num(attrs.get("min")), _num(attrs.get("max"))
        if (e_lo is not None and value < e_lo) or (e_hi is not None and value > e_hi):
            raise PermissionError(f"Warmwasser-Soll {value} °C außerhalb der Grenzen von {entity_id} "
                                  f"({e_lo:g}…{e_hi:g} °C)" if e_lo is not None and e_hi is not None
                                  else f"Warmwasser-Soll {value} °C außerhalb der Grenzen von {entity_id}")

    def _boiler_guard(self, entity_id: str, what: str) -> None:
        """Weg B: gesperrt ohne Schalter „heatpilot“, für eine andere Entität und solange ein Regler am Bus ist."""
        if self.boiler_lock is not None and (why := self.boiler_lock()):
            raise PermissionError(f"{what} auf {entity_id} gesperrt: {why}")

    def _flow_hi(self) -> tuple[int, str | None]:
        """Höchstes Vorlauf-Soll jetzt: ``max_flow_temp`` und die Fußboden-Grenze mit den Messwerten von jetzt
        (``floor_limit.flow_cap``) – mit dem Grund, wenn die Fußboden-Grenze tiefer liegt."""
        hi = int(self.flow_max)
        cap, why = floor_limit.flow_cap(self.floors, floor_limit.read(self.floors, self.states))
        if cap is not None and math.floor(cap + 1e-9) < hi:
            return math.floor(cap + 1e-9), why
        return hi, None

    def _check_flow(self, entity_id: str, data: dict) -> None:
        """Vorlauf-Soll (Weg B): nur die eine Entität, nur ``value``, ganzzahlig, 35 °C ≤ Wert ≤ ``max_flow_temp`` (bzw.
        seit 0.46.5 „nur Pumpe“: genau ``PUMP_FLOW`` bzw. der Mindestwert der Entität, unter 35 °C, wenn erlaubt) und
        ≤ Fußboden-Grenze jetzt (``floor_limit.flow_cap`` mit den Messwerten), innerhalb der Grenzen der Entität; die
        Entität muss taugen (``boiler_entity.flow_problem``). Alles andere hart abgewiesen."""
        if not self.flow_entity or entity_id != self.flow_entity or self.flow_max is None:
            raise PermissionError(f"number.set_value auf {entity_id} ist gesperrt – nur das Vorlauf-Soll bei „Kessel "
                                  "über HeatPilot“")
        value, (hi, why) = data.get("value"), self._flow_hi()
        whole = isinstance(value, int) and not isinstance(value, bool)
        attrs = self._state(entity_id).get("attributes") or {}
        e_lo, e_hi = _num(attrs.get("min")), _num(attrs.get("max"))
        # nur Pumpe (0.46.5): genau ``PUMP_FLOW`` bzw. der Mindestwert der Entität darüber, unter 35 °C
        idle = PUMP_FLOW if e_lo is None else max(PUMP_FLOW, math.ceil(e_lo - 1e-9))
        pump = whole and self.flow_pump and value == idle < FLOW_MIN
        if set(data) != {"value"} or not whole or not (FLOW_MIN <= value <= hi or pump):
            limit = f" ({why})" if why else ""
            raise PermissionError(f"Vorlauf-Soll {value!r} gesperrt – nur ganze Grad von {FLOW_MIN} bis {hi} °C{limit}")
        problem = boiler_entity.flow_problem(entity_id, self._state(entity_id))
        if problem:
            raise PermissionError(f"{problem} – gesperrt")
        if (e_lo is not None and value < e_lo) or (e_hi is not None and value > e_hi):
            raise PermissionError(f"Vorlauf-Soll {value} °C außerhalb der Grenzen von {entity_id}")
        self._boiler_guard(entity_id, "Vorlauf-Soll")

    def _check_heating(self, entity_id: str, data: dict, on: bool = False) -> None:
        """Heizbetrieb (Weg B): nur die eine ``switch``-Entität, ohne Daten; sie muss taugen (``heat_problem``).
        Einschalten nur, wenn das Vorlauf-Soll am Kessel nicht über der Grenze liegt (``_flow_safe``)."""
        if not self.heat_entity or entity_id != self.heat_entity or data:
            raise PermissionError(f"switch auf {entity_id} ist gesperrt – nur der Heizbetrieb bei „Kessel über "
                                  "HeatPilot“")
        problem = boiler_entity.heat_problem(entity_id, self._state(entity_id))
        if problem:
            raise PermissionError(f"{problem} – gesperrt")
        if on and (why := self._flow_safe()):
            raise PermissionError(f"Heizbetrieb an gesperrt: {why}")
        self._boiler_guard(entity_id, "Heizbetrieb")

    def _flow_safe(self) -> str | None:
        """Darf der Heizbetrieb an? None = ja: Das Vorlauf-Soll laut Home Assistant liegt höchstens bei der Grenze
        jetzt, oder HeatPilot hat in den letzten 5 min einen Wert bis zur Grenze geschrieben (Home Assistant zeigt ihn
        oft erst Sekunden später). Sonst der Grund – über der Grenze bzw. unbekannt heizt der Kessel womöglich zu heiß
        in einen Fußboden."""
        if not self.flow_entity or self.flow_max is None:
            return "kein Vorlauf-Soll zum Prüfen"
        hi, why = self._flow_hi()
        sent = self._flow_sent
        if sent is not None and time.time() - sent[1] <= CHECK_MAX_S and sent[0] <= hi:
            return None
        shown = _num(self._state(self.flow_entity).get("state"))
        if shown is None:
            return "Vorlauf-Soll am Kessel unbekannt"
        if shown > hi + CHECK_TOL_K:
            return f"Vorlauf-Soll am Kessel {shown:g} °C liegt über {hi} °C{f' ({why})' if why else ''}"
        return None

    async def set_flow(self, value: int, reason: str) -> None:
        """Vorlauf-Soll schreiben (Weg B, seit 0.41): ``number.set_value`` – Prüfung in ``_check_flow``."""
        if not self.flow_entity:
            raise PermissionError("Vorlauf-Soll: Schreiben gesperrt (Schalter nicht „heatpilot“ oder keine Entität)")
        await self._call("number", "set_value", self.flow_entity, {"value": value}, reason, action="flow_set")

    async def set_heating(self, on: bool, reason: str) -> None:
        """Heizbetrieb schalten (Weg B, seit 0.41): ``switch.turn_on``/``turn_off`` – Prüfung in ``_check_heating``."""
        if not self.heat_entity or not isinstance(on, bool):
            raise PermissionError("Heizbetrieb: Schalten gesperrt (Schalter nicht „heatpilot“ oder keine Entität)")
        await self._call("switch", "turn_on" if on else "turn_off", self.heat_entity, {}, reason,
                         action="heating_on" if on else "heating_off")

    async def set_disinfect(self, on: bool, reason: str) -> None:
        """Schalter der Desinfektion (seit 0.42): ``switch.turn_on``/``turn_off`` – Prüfung in ``_call``."""
        if not self.disinfect_entity or not isinstance(on, bool):
            raise PermissionError("Desinfektion: Schalten gesperrt (kein Termin, Warmwasser-Soll nicht „HeatPilot "
                                  "setzt“ oder keine Entität)")
        await self._call("switch", "turn_on" if on else "turn_off", self.disinfect_entity, {}, reason,
                         action="disinfect_on" if on else "disinfect_off")

    async def set_dhw(self, value: int, reason: str) -> None:
        """Warmwasser-Soll schreiben (Weg W, seit 0.38): ``number.set_value`` auf ``dhw_entity`` – die Prüfung
        macht ``_call``/``_check_dhw``; ohne eingestellte Entität (Schalter nicht „heatpilot“) gesperrt, ebenso, wenn
        sie auch das Vorlauf-Soll ist (``conflict``)."""
        if self.conflict:
            raise PermissionError(self.conflict)
        if not self.dhw_entity:
            raise PermissionError("Warmwasser-Soll: Schreiben gesperrt (Schalter nicht „HeatPilot setzt“ oder keine "
                                  "Entität)")
        await self._call("number", "set_value", self.dhw_entity, {"value": value}, reason)

    async def notify(self, target: str, title: str, message: str) -> None:
        """Meldung an ein eingestelltes Ziel (seit 0.38). notify-Entität (steht in den Zuständen von HA):
        ``notify.send_message`` mit ``entity_id``; sonst der Dienst ``notify.<name>`` (z. B. die Handy-App).
        Andere Ziele hart abgewiesen. Kennt eine ältere HA den Titel bei ``send_message`` nicht, einmal ohne."""
        if target not in self.notify_targets:
            raise PermissionError(f"Benachrichtigung an {target} ist gesperrt – nur eingestellte Ziele")
        message = str(message)[:NOTIFY_MAX]
        if target in self.states:
            payload = {"type": "call_service", "domain": "notify", "service": NOTIFY_ENTITY_SERVICE,
                       "service_data": {"message": message, "title": title}, "target": {"entity_id": target}}
            try:
                await self.ha.call(payload, timeout=30)
            except Exception as err:  # noqa: BLE001 – HA 2024.6/2024.7 kennen „title“ bei send_message nicht
                if "title" in str(err) or "extra keys" in str(err):
                    log.debug("notify.send_message mit Titel abgelehnt (%s) – ohne Titel", err)
                    await self.ha.call({**payload, "service_data": {"message": f"{title}: {message}"}}, timeout=30)
                    return
                if self._maybe_sent(err):
                    raise  # Zeitüberlauf, Verbindung getrennt: vielleicht doch angekommen – nicht doppelt schicken
                service = await self._mobile_service(target)
                if service is None or f"notify.{service}" in self.notify_targets:
                    raise  # kein Dienst – oder er ist selbst gewählt und bekommt die Meldung ohnehin
                log.info("notify.send_message auf %s gescheitert (%s) – einmal über notify.%s", target, err, service)
                await self.ha.call({"type": "call_service", "domain": "notify", "service": service,
                                    "service_data": {"message": message, "title": title}}, timeout=30)
            return
        await self.ha.call({"type": "call_service", "domain": "notify", "service": target.split(".", 1)[1],
                            "service_data": {"message": message, "title": title}}, timeout=30)

    def _maybe_sent(self, err: Exception) -> bool:
        """Kann die Meldung trotz Fehler angekommen sein? Dann kein Rückfall, sonst käme sie doppelt."""
        return self.maybe_arrived(err)

    def maybe_arrived(self, err: Exception) -> bool:
        """Kann ein Aufruf trotz Fehler angekommen sein (Ausgang unklar)? Zeitüberlauf, getrennte Verbindung (auch
        wenn Home Assistant gerade nicht verbunden ist). Auch für die Desinfektion (0.42): dann entscheidet die
        Prüfung nach 60 s, ob sie läuft."""
        if isinstance(err, (asyncio.TimeoutError, TimeoutError, ConnectionError, HAConnectionLost)):
            return True
        connected = getattr(self.ha, "connected", None)
        return "Verbindung getrennt" in str(err) or (connected is not None and not connected.is_set())

    async def _mobile_service(self, target: str) -> str | None:
        """Dienst ``notify.mobile_app_<name>`` zur Entität ``target`` (Name = Entität bzw. Anzeigename), wenn es ihn
        laut ``get_services`` gibt – None sonst oder wenn die Dienste nicht lesbar sind."""
        if self._services is None or time.time() - self._services[0] > SERVICES_S:
            try:
                self._services = (time.time(), await self.ha.get_services() or {})
            except Exception as err:  # noqa: BLE001 – ohne Dienste kein Rückfall
                log.debug("Dienste nicht lesbar (%s) – kein Rückfall auf notify.mobile_app_…", err)
                return None
        have = set((self._services[1] or {}).get("notify") or {})
        obj = target.split(".", 1)[1]
        name = str((self._state(target).get("attributes") or {}).get("friendly_name") or "")
        slug = "_".join("".join(c if c.isalnum() else " " for c in name.lower()).split())
        cands = [obj if obj.startswith("mobile_app_") else f"mobile_app_{obj}"]
        cands += [f"mobile_app_{slug}"] if slug else []
        return next((c for c in cands if c in have), None)

    async def _ensure_heat(self, entity_id: str, reason: str) -> None:
        """Vor einem Soll: Betriebsart ``heat`` sicherstellen (HomeKit-Räume stehen sonst evtl. auf ``off``,
        Tado-Cloud-Räume auf ``auto`` – dort würde das Soll nur bis zum nächsten Zeitplanwechsel gelten)."""
        st = self._state(entity_id)
        if not st:
            return  # Zustand unbekannt: nichts erzwingen
        modes = st.get("attributes", {}).get("hvac_modes") or []
        if st.get("state") != "heat" and (not modes or "heat" in modes):
            await self._call("climate", "set_hvac_mode", entity_id, {"hvac_mode": "heat"}, reason)

    async def _set(self, entity_id: str, temp: float, reason: str) -> None:
        await self._ensure_heat(entity_id, reason)
        await self._call("climate", "set_temperature", entity_id, {"temperature": temp}, reason)

    def _save(self) -> None:
        now = time.time()
        self.boosts = {r: b for r, b in self.boosts.items() if b["end"] > now or b.get("local")}
        self.store.put(KV, self.boosts)
        self.store.commit()

    async def _each(self, rooms: list[str], what: str, fn) -> dict:
        done, errors = [], []
        for r in rooms:
            try:
                await fn(r)
                done.append(r)
            except Exception as err:  # noqa: BLE001 – je Raum melden, die anderen trotzdem schalten
                errors.append(f"{r}: {err}")
        self._save()
        log.info("%s: %s%s", what, ", ".join(done) or "–", f" · Fehler: {'; '.join(errors)}" if errors else "")
        return {"ok": not errors, "done": done, "errors": errors}

    # ------------------------------------------------------------------ Bedienung
    async def boost(self, rooms, temp, minutes) -> dict:
        rooms, t = self._check_rooms(rooms), self._temp(temp)
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ControlError("Dauer fehlt") from None
        if not DUR_MIN <= minutes <= DUR_MAX:
            raise ControlError(f"Dauer muss zwischen {DUR_MIN} min und {DUR_MAX // 60} h liegen")
        end = int(time.time()) + minutes * 60
        reason = f"Boost {minutes} min"

        async def one(r):
            room = self.groups[r]
            targets = room.write_targets
            local = not all(self.is_tado(e) for e in targets)
            before = self.target(r)
            before_mode = self._mode(r)
            for e in targets:
                if self.is_tado(e):
                    await self._call("tado", "set_climate_timer", e,
                                     {"temperature": t, "time_period": _hhmmss(minutes)}, reason)
                else:
                    await self._set(e, t, reason)
            self.boosts[r] = {"temp": t, "end": end, "local": local, "before": before,
                              "before_mode": before_mode, "targets": targets}

        return await self._each(rooms, f"Boost {t:g} °C für {minutes} min", one)

    async def schedule(self, rooms, desired: dict[str, float] | None = None) -> dict:
        """„Zeitplan“: Tado-Cloud-Räume zurück in den Tado-Zeitplan (beendet auch einen Timer).
        Andere Entitäten haben keinen Zeitplan: mit ``desired`` (Steuerung heatpilot) wird der
        Wunschzustand geschrieben, sonst gibt es nur den Hinweis (``hint``)."""
        hints = []

        async def one(r):
            room = self.groups[r]
            for e in room.write_targets:
                if self.is_tado(e):
                    await self._call("climate", "set_hvac_mode", e, {"hvac_mode": "auto"}, "Zeitplan")
                elif desired and desired.get(r) is not None:
                    await self._set(e, self._temp(desired[r]), "Zeitplan (HeatPilot)")
                else:
                    hints.append(room.name)
            self.boosts.pop(r, None)

        res = await self._each(self._check_rooms(rooms), "Zeitplan", one)
        if hints:
            res["hint"] = f"{', '.join(dict.fromkeys(hints))}: {HINT_SCHEDULE}"
        return res

    async def set_temp(self, room, temp) -> dict:
        r, t = self._check_rooms([room])[0], self._temp(temp)

        async def one(r):
            for e in self.groups[r].write_targets:
                await self._call("climate", "set_temperature", e, {"temperature": t}, "Soll von Hand")
            self.boosts.pop(r, None)

        return await self._each([r], f"Soll {t:g} °C", one)

    async def set_mode(self, rooms, mode) -> dict:
        """Betriebsart eines Raums oder mehrerer („alle aus“ im Dialog)."""
        rs = self._check_rooms([rooms] if isinstance(rooms, str) else rooms)
        if mode not in MODES:
            raise ControlError("Unbekannte Betriebsart")

        async def one(r):
            for e in self.groups[r].write_targets:
                await self._call("climate", "set_hvac_mode", e, {"hvac_mode": mode}, "Betriebsart von Hand")
            self.boosts.pop(r, None)

        return await self._each(rs, f"Betriebsart {mode}", one)

    # ------------------------------------------------------------------ Regelschleife
    async def apply(self, room: str, temp: float, reason: str, step: float = 0.5) -> None:
        """Automatischer Schreibvorgang der Regelschleife: Betriebsart ``heat`` sicherstellen, dann Soll.
        Nur für gewählte Räume; Temperatur 5–25 °C, auf ``step`` gerundet (seit 0.40: die Ventilführung schreibt in
        der Schrittweite des Thermostats, sonst 0,5 K); Fehler werden dem Aufrufer gemeldet."""
        r, t = self._check_rooms([room])[0], self._temp(temp, step)
        for e in self.groups[r].write_targets:
            await self._set(e, t, reason)

    def expired_boosts(self, now: float | None = None) -> list[tuple[str, dict]]:
        """Lokale Boosts, deren Zeit um ist (Tado-Timer beendet Tado selbst)."""
        now = now or time.time()
        return [(r, b) for r, b in self.boosts.items() if b.get("local") and b["end"] <= now]

    async def end_boost(self, room: str, setpoint: float | None, reason: str = "Boost zu Ende",
                        fallback: float | None = None) -> bool:
        """Lokalen Boost beenden: Wunschzustand ``setpoint`` schreiben, ohne ihn das Soll von vorher, ohne beides
        (0.46.8) ``fallback`` (Soll laut Zeitplan) – nie „auto“; „aus“ nur, wenn es weder Soll noch Zeitplan gibt.
        Nur, wenn das eigene Boost-Soll noch steht – hat jemand inzwischen anders gedreht, nichts tun.
        Gibt True zurück, wenn geschrieben wurde."""
        b = self.boosts.pop(room, None)
        self._save()
        if not b or room not in self.groups:
            return False
        current = self.target(room)
        if current is None or abs(current - b["temp"]) >= 0.1:
            log.info("Boost %s: Soll wurde inzwischen geändert – nichts wiederhergestellt", room)
            return False
        setpoint = next((v for v in (setpoint, b.get("before"), fallback) if v is not None), None)  # 0.46.8
        wrote = False
        for e in self.groups[room].write_targets:
            if setpoint is not None:
                await self._set(e, self._temp(setpoint), reason)
                wrote = True
            elif b.get("before_mode") == "off":
                await self._call("climate", "set_hvac_mode", e, {"hvac_mode": "off"}, reason)
                wrote = True
        return wrote

    def active(self, targets: dict | None = None, now: float | None = None) -> dict:
        """Laufende Boosts mit Restzeit – nur solange das Soll noch dem Boost entspricht."""
        now = now or time.time()
        out = {}
        for r, b in self.boosts.items():
            target = (targets or {}).get(r, b["temp"])
            if b["end"] > now and target is not None and abs(float(target) - b["temp"]) < 0.1:
                out[r] = {**b, "left_min": max(1, round((b["end"] - now) / 60))}
        return out
