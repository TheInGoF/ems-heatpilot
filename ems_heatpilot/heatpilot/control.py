"""Schreibzugriffe auf die Thermostate: Bedienung im Reiter „Heizen“ und Regelschleife (0.34).

Erlaubt sind ausschließlich diese Aufrufe auf die ``climate.*``-Entitäten der gewählten Räume:

* ``tado.set_climate_timer`` – Tado hält die Temperatur für die Dauer und kehrt danach
  selbst zum Zeitplan zurück (nur Entitäten der Tado-Cloud-Integration),
* ``climate.set_temperature`` – Soll eines Raums (Bedienung, Regelschleife, lokaler Boost),
* ``climate.set_hvac_mode`` – Zeitplan (``auto``), Aus (``off``) oder Manuell (``heat``).

Alles andere – andere Dienste und Entitäten außerhalb der gewählten Räume – wird hart abgewiesen.
Eine eigene Sperre für EMS-ESP gibt es hier nicht: Wählt jemand den Heizkreis von EMS-ESP
(``climate.…_thermostat_hc1``) als Raum, ginge ``climate.set_temperature`` über Home Assistant an
den Regler. Die Erkennung schlägt ihn deshalb nicht vor (``discovery._EMS_CLIMATE``), die Doku rät
davon ab; an EMS-ESP selbst schickt HeatPilot nur Lese-Anfragen (``emsesp.py``, GET). Jeder
erfolgreiche Schreibvorgang landet im Protokoll ``control_log`` (Raum, Dienst, Wert, Grund).

Ein Raum ist eine Gruppe (``discovery.Room``): mit Raumregler wird nur dieser beschrieben,
sonst alle Ventile des Raums. Boost auf Knopfdruck: Tado-Cloud-Entitäten über den Tado-Timer,
alle anderen (z. B. HomeKit) mit eigenem Timer – Ablauf in ``boosts`` gemerkt, die Regelschleife
stellt danach den Wunschzustand bzw. das Soll von vorher wieder her (``end_boost``).
"""

from __future__ import annotations

import logging
import time

from .discovery import Room

log = logging.getLogger(__name__)

ALLOWED = {("tado", "set_climate_timer"), ("climate", "set_temperature"), ("climate", "set_hvac_mode")}
MODES = ("auto", "off", "heat")
TEMP_MIN, TEMP_MAX = 5.0, 25.0
DUR_MIN, DUR_MAX = 15, 12 * 60
KV = "control.boosts"
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
    def __init__(self, ha, store, rooms: list, states: dict | None = None) -> None:
        """``rooms``: ``discovery.Room`` (Gruppen) oder nur climate-IDs (jede ihr eigener Raum).
        ``states``: HA-Zustände (``engine.states``), um Betriebsart und Soll vor dem Schreiben zu kennen."""
        self.ha = ha
        self.store = store
        self.groups: dict[str, Room] = {}
        for r in rooms:
            room = r if isinstance(r, Room) else Room(name=r, climate=r)
            self.groups[room.climate] = room
        # alles, was überhaupt beschrieben werden darf (Schlüssel und Mitläufer)
        self.rooms: set[str] = {c for room in self.groups.values() for c in room.climates}
        self.states = states if states is not None else {}
        # laufende Boosts je Raum: {temp, end, local, before, before_mode, targets}
        self.boosts: dict[str, dict] = dict(store.get(KV) or {})

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
    def _temp(value) -> float:
        try:
            t = round(float(value) * 2) / 2
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
    async def _call(self, domain: str, service: str, entity_id: str, data: dict, reason: str = "Knopfdruck") -> None:
        if (domain, service) not in ALLOWED or entity_id not in self.rooms:
            raise PermissionError(f"{domain}.{service} auf {entity_id} ist gesperrt")
        await self.ha.call({"type": "call_service", "domain": domain, "service": service,
                            "service_data": data, "target": {"entity_id": entity_id}}, timeout=30)
        if service == "set_hvac_mode":
            reason = f"{reason} ({data.get('hvac_mode')})"
        self.store.add_control_log(int(time.time()), entity_id, service, data.get("temperature"), reason)

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
    async def apply(self, room: str, temp: float, reason: str) -> None:
        """Automatischer Schreibvorgang der Regelschleife: Betriebsart ``heat`` sicherstellen, dann Soll.
        Nur für gewählte Räume; Temperatur 5–25 °C; Fehler werden dem Aufrufer gemeldet."""
        r, t = self._check_rooms([room])[0], self._temp(temp)
        for e in self.groups[r].write_targets:
            await self._set(e, t, reason)

    def expired_boosts(self, now: float | None = None) -> list[tuple[str, dict]]:
        """Lokale Boosts, deren Zeit um ist (Tado-Timer beendet Tado selbst)."""
        now = now or time.time()
        return [(r, b) for r, b in self.boosts.items() if b.get("local") and b["end"] <= now]

    async def end_boost(self, room: str, setpoint: float | None, reason: str = "Boost zu Ende") -> bool:
        """Lokalen Boost beenden: Wunschzustand ``setpoint`` schreiben, ohne ihn das Soll von vorher.
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
        if setpoint is None:
            setpoint = b.get("before")
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
