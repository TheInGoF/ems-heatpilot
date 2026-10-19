"""Herzstück: Zustände sammeln, Modelle lernen, Auswertungen berechnen.

Phase 1 = Beobachten: Das Add-on liest nur und schreibt nichts an die Therme.
Es veröffentlicht eigene ``sensor.heatpilot_*``-Sensoren und eine Weboberfläche.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, deque
from datetime import datetime, timedelta

from . import advice, analysis, flowstudy, guards, plausibility, readiness, schedule, sleep, solar, weekly
from .config import Config
from .dhw import AFTER_CHARGE_H, DhwModel, DhwShadow, DrawWatch, tail_rising
from .live import LiveBuffer
from .discovery import BOILER_ROLES, HEAT_MODES, THERMOSTAT_ROLES, Discovered
from .models import DAILY_MIN_HOURS, ROOM_STEP_H, HouseModel, RoomModel
from .presence import PersonLearner, arrival_unknown, haversine_km, household_long_away, next_arrival, short_trip
from .store import Store
from .vacation import Vacation

log = logging.getLogger(__name__)

DHW_RISE_KH = 6.0  # so schnell steigt der Speicher nur beim Laden (Auskühlen: < 2 K/h)
DHW_HOLD_MAX_S = 90 * 60  # nach dem Ladezähler höchstens so lange „lädt“ annehmen
# Warmwasser-Soll-Zeitprogramm des Reglers (z. B. nachts 45 statt 55 °C) aus den Messwerten
DHW_SET_DAYS = 14         # so weit zurück
DHW_SET_SLOT_MIN = 5      # Raster nach Wanduhr
DHW_SET_SHARE = 0.7       # so viele Tage müssen im Slot denselben Wert zeigen
DHW_SET_MIN_DAYS = 3
DHW_SET_CACHE_S = 3600

ENABLE_DELAY_MAX_MIN = 15
HEAT_SEASON_24H = 15.0          # darüber (24-h-Mittel außen) lernt das Hausmodell nicht
HOUSE_DAYS_CATCH_UP = 31        # so viele offene Tage holt das Tagesmodell höchstens nach
GAS_FREE_KWH = 0.05             # Stunde gilt als gasfrei (Auskühlen lernen)
NIGHT_HOURS = (22, 23, 0, 1, 2, 3, 4, 5)  # ohne Sonne
# Warmwasser-Grundbedarf aus Sommerstunden (24-h-Mittel draußen so warm: sicher keine Heizung,
# das Gas ist Warmwasser) – für Langzeitstunden ohne Warmwasser-Angabe vor dem Hauslernen abgezogen
DHW_BASE_SUMMER_24H = 17.0
DHW_BASE_MIN_HOURS = 14 * 24
DHW_BASE_MIN_CELL = 4         # so viele Sommerstunden je Wochentag/Stunde, sonst Mittel der Stunde
ON_STATES = {"on", "true", "1", "heat", "heating",
             "an", "ein", "ja", "wahr", "aktiv"}  # EMS-ESP mit deutscher Sprache: „an“/„aus“
STALE_S = 12 * 3600
KV_MANAGED_UNTIL = "schedule.managed_until"  # bis dahin hat HeatPilot selbst gesteuert: nicht aus Tado lernen
# Fremdwärme je Raum (Kamin, Ofen, viele Gäste): der Raum wird deutlich wärmer, ohne dass der
# Heizkörper es erklären kann – solche Schritte lernt das Raummodell nicht, solche Stunden das Haus nicht
EXT_RATE_KH = 0.8        # unerklärter Anstieg ab so viel K/h …
EXT_MIN_K = 0.4          # … und mindestens so viel K im Schritt
EXT_HEAT_SHARE = 0.10    # Heizkörper liefert praktisch nichts: Eingang ≤ 10 % des möglichen (Vorlauf − Raum) …
EXT_VALVE_MAX = 5.0      # … und das Ventil war in diesem und im vorigen Schritt nie weiter offen (%): ein eben
#                          noch offener Heizkörper gibt nach dem Schließen weiter Wärme ab, und ein Thermostat
#                          am Heizkörper misst den warmen Körper erst verzögert – beides sähe sonst wie Fremdwärme aus
EXT_SUN_MAX = 0.1        # darunter keine Sonne auf den Fassaden – dann darf auch ein junges Modell erkennen
EXT_DAY_HOURS = range(7, 20)  # ohne Sonnendaten (kein Standort) wird in diesen Stunden nicht erkannt: könnte Sonne sein
EXT_REF_H = 2.0          # Bezug ist die höchste Temperatur der letzten so vielen Stunden: nach Stoßlüften muss der Raum
#                          erst wieder über sein altes Niveau steigen – die Erholung aus Wänden und Möbeln bei
#                          geschlossenem Ventil (Nachtabsenkung) ist keine Fremdwärme
EXT_END_K = 0.3          # Ereignis endet, wenn der Raum wieder bis auf so viel K am Start ist …
EXT_END_VALVE = 30.0     # … oder der Heizkörper wieder deutlich öffnet (Ventil-Mittel eines Schritts, %) …
EXT_MAX_H = 12           # … spätestens nach so vielen Stunden
EXT_DAY_SKIP_H = 3.0     # Tag mit so vielen Stunden Fremdwärme lernt das Tagesmodell nicht (zählt nur als Vortag)
EXT_COUNT_DAYS = 7       # Zähler je Raum in der Übersicht
PERSON_UNKNOWN = {None, "", "unknown", "unavailable"}  # Tracker ohne Wert: weder zu Hause noch weg
# Wettervorhersage (seit 0.36.1): ein Vorhersagepunkt gilt nur so nah am gefragten Zeitpunkt – sonst keiner
# (eine Ankunft in drei Tagen bekommt nicht den letzten Punkt einer 24-h-Vorhersage)
FORECAST_MAX_GAP_MIN = 90
FORECAST_AHEAD_H = 12  # Kachel „Vorhersage“: Minimum, Maximum und Trend der nächsten so vielen Stunden


def num(value) -> float | None:
    if isinstance(value, str):
        value = value.strip().replace(",", ".")  # z. B. EMS-ESP-Zähler "4321,57"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN raus


def flag(value) -> int | None:
    if value is None or value in ("unknown", "unavailable"):
        return None
    if isinstance(value, bool):
        return int(value)
    if num(value) is not None:
        return int(num(value) > 0)
    return int(str(value).lower() in ON_STATES)


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def aware(dt: datetime) -> datetime:
    """Zeitpunkt mit Zeitzone – ohne Zone gilt er als Ortszeit."""
    return dt if dt.tzinfo is not None else dt.astimezone()


def parse_time(value) -> datetime | None:
    """ISO-Zeit (auch mit „Z“) oder datetime als Zeitpunkt mit Zone; ohne Zone Ortszeit, unlesbar None."""
    if isinstance(value, datetime):
        return aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def midnight(day: datetime) -> int:
    """Beginn dieses Kalendertags (Ortszeit) – auch am Tag der Zeitumstellung richtig (23/25 h).

    Nicht ``replace(hour=0)`` auf einem Zeitpunkt mit fester Zeitzone: Nach der Umstellung
    läge Mitternacht sonst eine Stunde daneben.
    """
    return int(datetime.combine(day.date(), datetime.min.time()).timestamp())


class Engine:
    def __init__(self, cfg: Config, store: Store, found: Discovered) -> None:
        self.cfg = cfg
        self.store = store
        self.found = found
        self.states: dict[str, dict] = {}
        self.ems: dict[str, dict] = {}  # direkt von EMS-ESP gelesen (optional)
        # Kesselsteuerungen laut EMS-ESP (seit 0.36.1, nur Anzeige): {Kurzname: {found, writeable, value …}},
        # main.poll_emsesp liest sie beim Start und stündlich; ohne EMS-ESP bleibt es leer
        self.ems_controls: dict[str, dict] = {}
        self.ems_controls_ts: float | None = None
        self.gas_factor = 1.0 if (found.gas_unit or "").lower() == "kwh" else cfg.gas_kwh_per_m3
        self.house = HouseModel.from_dict(store.get("model.house")) if store.get("model.house") else HouseModel()
        self.rooms: dict[str, RoomModel] = {}
        fresh_rooms: set[str] = set()
        for room in found.rooms:
            saved = store.get(f"model.room.{room.climate}")
            floor = room.climate in cfg.floor_heating_rooms
            model = RoomModel.from_dict(saved) if saved else None
            if model is None or model.floor != floor:  # neu oder Fußboden-Einstellung geändert
                model = RoomModel(room.name, floor=floor)
                fresh_rooms.add(room.climate)
            model.room = room.name  # Umbenennen in den Einstellungen gilt auch für gelernte Modelle
            self.rooms[room.climate] = model
        self.seed_room_priors()
        self.vacation = Vacation(cfg.vacations, cfg.vacation_entity, cfg.vacation_arrival_time)
        self.vacation.returned = set(store.get("vacation_returned", []))
        self.persons: dict[str, PersonLearner] = {}
        for p in found.persons:
            saved = store.get(f"model.person.{p}")
            self.persons[p] = PersonLearner.from_dict(saved) if saved else PersonLearner(p)
        saved = store.get("model.dhw")
        self.dhw = DhwModel.from_dict(saved) if saved else DhwModel()
        self.dhw_shadow = DhwShadow()
        # Zeiten/Temperaturen: Einstellungen sind Startwerte, gelernt wird aus Tado
        self.schedule_learned: dict | None = store.get("schedule.learned")
        # Schlafenszeit aus dem Stromverbrauch (0.36): gespeicherte Nächte → Wochenplan, damit die
        # Steuerung vom ersten Takt an die richtigen Zeiten hat; wirkt nur mit night_plan_auto
        self._sleep_ts = 0.0
        self.sleep_nights: dict = (store.get(sleep.KV_NIGHTS) or {}) if found.power else {}
        self._sleep = sleep.summary(self.sleep_nights, int(time.time()), cfg, found.power, found.power_exclude)
        cfg.night_plan_learned = sleep.learned_plan(self._sleep["plan"]) if found.power else None
        self.eff = schedule.effective(cfg, self.schedule_learned)
        self._schedule_ts = 0.0
        self.live = LiveBuffer()  # 30-s-Werte der letzten 12 h für den Reiter „Live“
        self.room_spikes = plausibility.SpikeFilter()
        self._dhw_prev: tuple[int, float] | None = None  # (ts, Speichertemperatur) fürs Erkennen von Ladungen
        self.dhw_inferred = 0      # Messpunkte, in denen die Ladung nur aus dem Temperaturanstieg erkannt wurde
        self.dhw_flag_seen = False  # kam jemals ein Ladesignal?
        self._rejected: set[str] = set()  # zuletzt verworfene Messwerte (außerhalb der Grenzen)
        self._prev_dhw: tuple[int, float | None, float | None] | None = None  # (ts, Speicher-T, Brenner-kW)
        self.dhw_rec: dict | None = None
        self._advice: tuple[float, list, list] | None = None
        self.forecast: list[dict] = []
        self.forecast_ts: float | None = None  # Zeit des letzten Abrufs (main setzt sie, build übernimmt sie)
        self.last_plan: dict | None = None
        self._last_burner: int | None = None
        # Live-Zähler (Nachlauf, Taktsperre) – ereignisgenau statt 5-min-Raster
        self._live_ts: float | None = None
        self._live_prev: dict = {}
        self._dhw_now = 0  # zuletzt erkannte Warmwasserladung (Ladezähler, Temperaturanstieg), nicht nur das Signal
        self._demand: bool | None = None
        self._mode_wait_start: float | None = None  # Beginn der aktuellen Wartephase
        self._last_enabled: int | None = None
        off = store.query("SELECT MAX(ts) AS t FROM burner_events WHERE state = 0")
        self._last_off_ts: float | None = off[0]["t"] if off else None
        self.control_loop = None  # controlloop.ControlLoop, von main gesetzt (Steuerung seit 0.34)
        self._live_entities = {
            *found.climate_ids(),
            *(found.boiler.get(k) for k in ("burner_on", "burner_power", "dhw_active", "min_period")),
            found.thermostat.get("hc_mode"),
        }
        self._last_room_step: int | None = store.get("last_room_step")
        self._last_hour: int | None = store.get("last_hour")
        # laufende Fremdwärme-Ereignisse je Raum (start_ts, t_start, peak, rise) – überleben einen Neustart
        self.room_ext: dict[str, dict] = {c: e for c, e in (store.get("model.room_ext") or {}).items() if c in self.rooms}
        self._room_valve_prev: dict[str, float] = {}  # größte Ventilöffnung des vorigen Raumschritts
        self._room_temp_hist: dict[str, list[tuple[int, float]]] = {}  # (ts, Temperatur) am Ende der letzten Raumschritte
        if fresh_rooms and self._last_room_step is not None:
            self.relearn_rooms(fresh_rooms)

    # ------------------------------------------------------------------ Zustände
    def on_state(self, entity_id: str, state: dict | None) -> None:
        if state is None:
            self.states.pop(entity_id, None)
            return
        self.states[entity_id] = state
        if entity_id in self._live_entities:
            self.update_live()

    def set_ems(self, values: dict[str, dict]) -> None:
        self.ems = values
        self.update_live()

    def _val(self, entity_id: str | None):
        if not entity_id or entity_id not in self.states:
            return None
        return self.states[entity_id].get("state")

    def _boiler(self, role: str):
        ems = self.ems.get("boiler")
        if ems:
            for key in BOILER_ROLES.get(role, ()):
                if key in ems:
                    return ems[key]
        return self._val(self.found.boiler.get(role))

    def _thermo(self, role: str):
        ems = self.ems.get("thermostat")
        if ems:
            for key in THERMOSTAT_ROLES.get(role, ()):
                if key in ems:
                    return ems[key]
        return self._val(self.found.thermostat.get(role))

    def burner_on(self) -> int | None:
        on = flag(self._boiler("burner_on"))
        if on is None:
            p = num(self._boiler("burner_power"))
            on = None if p is None else int(p > 0)
        return on

    def dhw_special(self) -> int | None:
        """Legionellenschaltung oder Einmalladung läuft – solche Ladungen lernt das Modell nicht."""
        vals = [flag(self._boiler(r)) for r in ("dhw_disinfecting", "dhw_onetime")]
        if all(v is None for v in vals):
            return None
        return int(any(vals))

    def _vacation_entity_state(self) -> str | None:
        ent = self.cfg.vacation_entity
        return self.states.get(ent, {}).get("state") if ent else None

    def on_vacation(self, ts: float | None = None) -> bool:
        return self.vacation.active(local(time.time() if ts is None else ts), self._vacation_entity_state())

    def track_vacation_return(self, ts: float, persons: list[tuple]) -> None:
        """Ist jemand ≥ 10 min zu Hause, ist der Urlaub vorbei – egal was geplant war."""
        anyone = any(is_home for _, is_home, _ in persons)
        if self.vacation.note_presence(local(ts), anyone, self._vacation_entity_state()):
            self.store.put("vacation_returned", sorted(self.vacation.returned))

    def heat_enabled(self) -> int | None:
        """Heizfreigabe am Regler: Betriebsart Tag = heizen, Nacht = aus (Absenkmodus Frostschutz)."""
        mode = self._thermo("hc_mode")
        if mode is None or mode in ("unknown", "unavailable"):
            return None
        return int(str(mode).strip().lower() in HEAT_MODES)

    def setpoint_demand(self) -> bool | None:
        """Wärmebedarf aus Tado-Soll vs. Ist – reagiert sofort, ohne Ventil-Verzögerung.

        Bedarf ab 0,2 K unter Soll, kein Bedarf wenn alle Räume am Soll sind,
        dazwischen bleibt der letzte Zustand (Hysterese).
        """
        gaps = []
        for r in self.found.rooms:
            st = self.states.get(r.climate)
            if not st:
                continue
            a = st.get("attributes", {})
            temp, target = num(a.get("current_temperature")), num(a.get("temperature"))
            if st.get("state") == "off" or target is None:
                gaps.append(-99.0)
            elif temp is not None:
                gaps.append(target - temp)
        if not gaps:
            return None
        if max(gaps) >= 0.2:
            self._demand = True
        elif max(gaps) <= 0.0:
            self._demand = False
        return self._demand

    def update_live(self, now: float | None = None, record_events: bool = True) -> None:
        """Zeit seit dem letzten Aufruf den passenden Tageszählern gutschreiben.

        Warmwasser gilt nach der erkannten Ladung (infer_dhw), nicht nur nach dem Ladesignal – viele
        Kessel melden keins, dann zählte jede Speicherladung als „Brenner ohne Wärmebedarf“.
        """
        now = time.time() if now is None else now
        burner = self.burner_on()
        if burner is not None and burner != self._last_burner:
            if record_events:
                self.store.add_burner_event(int(now), burner)
            if burner == 0 and self._last_burner == 1:
                self._last_off_ts = now
            self._last_burner = burner

        prev = self._live_prev
        if self._live_ts is not None and prev:
            dt = min(now - self._live_ts, 2 * self.cfg.sample_interval_s)
            day = local(self._live_ts).date().isoformat()
            if dt > 0:
                if prev["overrun"]:
                    self.store.add_daily(day, "overrun_min", dt / 60)
                    if prev["kw"] is not None:
                        self.store.add_daily(day, "overrun_kwh", prev["kw"] * dt / 3600)
                if prev["blocked"]:
                    self.store.add_daily(day, "blocked_min", dt / 60)
                if prev["waiting"]:
                    self.store.add_daily(day, "waiting_min", dt / 60)
                if prev["mode_wait"]:
                    self.store.add_daily(day, "mode_wait_min", dt / 60)

        dhw = flag(self._boiler("dhw_active")) or self._dhw_now or 0
        demand = self.setpoint_demand()
        enabled = self.heat_enabled()
        period = num(self._boiler("min_period"))
        power, nom = num(self._boiler("burner_power")), self.nominal_kw()
        locked = bool(
            period and self._last_off_ts is not None and now - self._last_off_ts < period * 60
        )
        if enabled is not None:
            if self._last_enabled is not None and enabled != self._last_enabled:
                # viele Wechsel am Tag = Automationen schalten gegeneinander
                self.store.add_daily(local(now).date().isoformat(), "mode_switch_count", 1)
            self._last_enabled = enabled
        mode_wait = burner == 0 and demand is True and enabled == 0 and not locked
        if mode_wait and self._mode_wait_start is None:
            self._mode_wait_start = now
        elif not mode_wait and self._mode_wait_start is not None:
            # Kurze Wartephasen = Verzögerung der Automation, lange = gewollte Sperre (Nacht/weg)
            minutes = (now - self._mode_wait_start) / 60
            day_ = local(self._mode_wait_start).date().isoformat()
            if minutes <= ENABLE_DELAY_MAX_MIN:
                self.store.add_daily(day_, "enable_delay_min", minutes)
                self.store.add_daily(day_, "enable_delay_count", 1)
            self._mode_wait_start = None
        self._live_prev = {
            # Brenner heizt, obwohl laut Tado-Soll kein Raum mehr Wärme braucht
            "overrun": burner == 1 and not dhw and demand is False,
            "kw": None if power is None or nom is None else power / 100 * nom,
            # Räume wollen Wärme, Brenner darf wegen Taktsperre nicht
            "blocked": burner == 0 and not dhw and demand is True and locked,
            # Räume wollen Wärme, Brenner aus (egal warum)
            "waiting": burner == 0 and demand is True,
            # Räume wollen Wärme, aber der Regler steht (noch) auf Nacht – z. B. weil
            # die Automation nur alle 10 min prüft
            "mode_wait": mode_wait,
        }
        self._live_ts = now

    def outdoor_now(self) -> float | None:
        vals = [plausibility.in_range("t_out", num(self._val(e))) for e in self.found.outdoor]
        vals = plausibility.robust_outdoor([v for v in vals if v is not None])
        if vals:
            return min(vals)  # wie bisher: Minimum der Außenfühler
        if self.found.weather and self.found.weather in self.states:
            return num(self.states[self.found.weather]["attributes"].get("temperature"))
        return None

    def power_sum(self, entities: list[str], strict: bool) -> float | None:
        """Summe der Leistungssensoren in W (kW ×1000; Einheit aus dem Zustand, sonst die der Erkennung).

        ``strict``: fehlt ein Sensor oder hat er keinen Wert, ist die Summe unbekannt (None) – eine
        Phasensumme ohne eine Phase wäre falsch. Sonst zählt ein fehlender Sensor 0 (Ausschlüsse).
        Ohne Sensoren None.
        """
        if not entities:
            return None
        total = 0.0
        for e in entities:
            st = self.states.get(e)
            v = num(st.get("state")) if st else None
            if v is None:
                if strict:
                    return None
                continue
            unit = st.get("attributes", {}).get("unit_of_measurement") or self.found.power_units.get(e) or "W"
            total += v * 1000 if str(unit).strip().lower() == "kw" else v
        return total

    def snapshot(self) -> tuple[dict, list[tuple], list[tuple]]:
        burner_pow = num(self._boiler("burner_power"))
        burner_on = self.burner_on()
        dhw = flag(self._boiler("dhw_active"))
        heating = flag(self._boiler("heating_pump"))
        if heating is None and burner_on is not None:
            heating = int(burner_on and not dhw)
        home, unknown = 0, False  # bekannte Anwesende; irgendeine Person ohne Zustand?
        persons = []
        for p in self.found.persons:
            st = self.states.get(p)
            if not st:
                unknown = True
                continue
            state = st.get("state")
            # Tracker ohne Wert: nicht „weg“, sondern unbekannt (gespeichert als leer, nicht gelernt)
            is_home = None if state in PERSON_UNKNOWN else int(state == "home")
            if is_home is None:
                unknown = True
            else:
                home += is_home
            attrs = st.get("attributes", {})
            dist = None
            if self.found.home_zone and "latitude" in attrs:
                dist = haversine_km(*self.found.home_zone, attrs["latitude"], attrs["longitude"])
            persons.append((p, is_home, dist))
        sample = {
            "t_out": self.outdoor_now(),
            "flow": num(self._boiler("flow_temp")),
            "sel_flow": num(self._boiler("sel_flow_temp")),
            "ret": num(self._boiler("return_temp")),
            "burner_pow": burner_pow,
            "burner_on": burner_on,
            "heating": heating,
            "dhw": dhw,
            "gas": num(self._val(self.found.gas)),
            "min_period": num(self._boiler("min_period")),
            # Zahl der bekannten Anwesenden; ist niemand bekannt zu Hause und eine Person unbekannt,
            # bleibt offen, ob jemand da ist (None: Warmwasser hält den Boden mit Reserve, nicht das Minimum)
            "home_count": (None if home == 0 and unknown else home) if self.found.persons else None,
            "boiler_temp": num(self._boiler("boiler_temp")),
            "calc_flow": num(self._thermo("hc_calc_flow")),
            "heating_off": flag(self._boiler("heating_off")),
            "burn_starts": num(self._boiler("burner_starts")),
            "heat_starts": num(self._boiler("heat_starts")),
            "heat_min": num(self._boiler("heat_runtime")),
            "hc_day": self.heat_enabled(),
            "hc_seltemp": num(self._thermo("hc_seltemp")),
            "remote_temp": num(self._thermo("hc_remotetemp")),
            "pump_mod": num(self._boiler("pump_mod")),
            "dhw_temp": num(self._boiler("dhw_temp")),
            "flame": num(self._boiler("flame_current")),
            "demand_pct": num(self._val(self.found.demand_sensor)),
            "dhw_set": num(self._boiler("dhw_seltemp")),
            "vacation": int(self.on_vacation()),
            # seit 0.25: Warmwasser-Ladezähler, Sonderladung (Legionellen/Einmalladung), Anlagendruck
            "dhw_starts": num(self._boiler("dhw_starts")),
            "dhw_special": self.dhw_special(),
            "syspress": num(self._boiler("sys_press")),
            # seit 0.36: Hausleistung (Summe der Leistungssensoren; fehlt einer, bleibt sie offen) und
            # die Verbraucher, die nicht zählen (Waschmaschine, Wallbox …)
            "power_w": self.power_sum(self.found.power, strict=True),
            "power_ex_w": self.power_sum(self.found.power_exclude, strict=False),
            # nur für die Live-Ansicht (nicht in der Datenbank): läuft die Heizungspumpe?
            "pump_on": (int(num(self._boiler("pump_mod")) > 0) if num(self._boiler("pump_mod")) is not None
                        else flag(self._boiler("heating_pump"))),
        }
        rooms = [row for row in (self.room_reading(r) for r in self.found.rooms) if row is not None]
        return self._check_ranges(sample, rooms, persons)

    def _device_reading(self, dev: dict) -> tuple | None:
        """(Temperatur, Soll, Ventil, Feuchte) eines Thermostats; None, wenn es keinen Zustand hat."""
        st = self.states.get(dev["climate"])
        if not st:
            return None
        a = st.get("attributes", {})
        valve = num(self._val(dev.get("heating_power")))
        if valve is None:
            valve = 100.0 if a.get("hvac_action") == "heating" else 0.0
        hum = num(self._val(dev.get("humidity")))
        if hum is None:
            hum = num(a.get("current_humidity"))
        target = num(a.get("temperature")) if st.get("state") != "off" else None
        return num(a.get("current_temperature")), target, valve, hum

    def room_reading(self, r) -> tuple | None:
        """(climate, Temperatur, Soll, Ventil, Feuchte) eines logischen Raums.

        Mit Raumregler zählen dessen Temperatur und Soll; sonst ist die Raumtemperatur das Mittel
        aller Geräte der Gruppe, das Soll das des Schlüssels. Das Ventil ist immer das Mittel aller
        Geräte (Fehlt der Schlüssel, fehlt der Raum – wie bisher.)
        """
        head = self._device_reading(r.devices[0])
        if head is None:
            return None
        others = [x for x in (self._device_reading(d) for d in r.members) if x is not None]
        temps = [head[0]] if r.controller else [x[0] for x in (head, *others) if x[0] is not None]
        valves = [x[2] for x in (head, *others) if x[2] is not None]
        hums = [x[3] for x in (head, *others) if x[3] is not None]
        return (r.climate, sum(temps) / len(temps) if temps else None, head[1],
                sum(valves) / len(valves) if valves else None, hums[0] if hums else None)

    def _check_ranges(self, sample: dict, rooms: list[tuple], persons: list[tuple]):
        """Werte außerhalb physikalischer Grenzen verwerfen (z. B. EMS-Platzhalter 3276,7)."""
        rejected = set()
        for key, value in sample.items():
            if key in plausibility.RANGES and value is not None:
                sample[key] = plausibility.in_range(key, value)
                if sample[key] is None:
                    rejected.add(key)
        clean = []
        for climate, temp, target, valve, hum in rooms:
            row = (climate, plausibility.in_range("room_temp", temp), plausibility.in_range("room_target", target),
                   plausibility.in_range("valve", valve), plausibility.in_range("humidity", hum))
            if (temp is not None and row[1] is None) or (valve is not None and row[3] is None):
                rejected.add(f"{climate}")
            clean.append(row)
        self._rejected = rejected
        return sample, clean, persons

    def add_solar(self, ts: int, sample: dict) -> None:
        """Sonneneinstrahlung auf Ost/Süd/West zum Zeitpunkt – aus Standort, Uhrzeit und Bewölkung."""
        if not self.found.home_zone:
            return
        cloud = None
        if self.found.weather and self.found.weather in self.states:
            cloud = num(self.states[self.found.weather].get("attributes", {}).get("cloud_coverage"))
        lat, lon = self.found.home_zone
        sample["sun_e"], sample["sun_s"], sample["sun_w"] = solar.facade_inputs(ts, lat, lon, cloud)

    def infer_dhw(self, ts: int, sample: dict) -> None:
        """Warmwasserladung erkennen, wenn das Ladesignal fehlt oder nie 1 wird.

        Steigt die Speichertemperatur bei laufendem Brenner um mindestens DHW_RISE_KH,
        lädt der Kessel den Speicher (3-Wege-Ventil auf Warmwasser). Dann bekommt der
        Heizkreis in dieser Zeit nichts.
        """
        flag, t = sample.get("dhw"), sample.get("dhw_temp")
        # Ladezähler des Kessels: jede Erhöhung ist der Beginn einer Speicherladung (exakt)
        starts, prev_starts = sample.get("dhw_starts"), getattr(self, "_dhw_starts_prev", None)
        if starts is not None:
            self._dhw_starts_prev = starts
            if prev_starts is not None and 0 < starts - prev_starts <= 5 and not flag:
                sample["dhw"] = flag = 1
                self.dhw_counted = getattr(self, "dhw_counted", 0) + 1
                self._dhw_hold = {"since": ts, "hot": False}
        # Ladung läuft weiter, auch wenn der Speicher gerade fällt (gleichzeitig gezapft) – bis der
        # Brenner aus ist, der Vorlauf nicht mehr über dem Speicher liegt oder 90 min um sind
        hold = getattr(self, "_dhw_hold", None)
        if hold is not None and not flag:
            flow = sample.get("flow")
            if flow is not None and t is not None and flow > t:
                hold["hot"] = True
            done = (not sample.get("burner_on") or ts - hold["since"] > DHW_HOLD_MAX_S
                    or (hold["hot"] and flow is not None and t is not None and flow <= t + 2.0))
            if done:
                self._dhw_hold = None
            else:
                sample["dhw"] = flag = 1
        if flag:
            self.dhw_flag_seen = True
        prev = self._dhw_prev
        if t is not None and (prev is None or ts - prev[0] >= 100):
            self._dhw_prev = (ts, t)
        if flag or t is None or prev is None or not sample.get("burner_on"):
            if flag is None and t is not None:
                sample["dhw"] = 0
            return
        dt_h = (ts - prev[0]) / 3600
        flow = sample.get("flow")
        valve_dhw = flow is None or flow >= t + 5.0  # Speicher wird nur warm, wenn das Kesselwasser heißer ist
        if valve_dhw and 0 < dt_h <= 0.25 and t - prev[1] >= 0.5 and (t - prev[1]) / dt_h >= DHW_RISE_KH:
            sample["dhw"] = 1
            self.dhw_inferred += 1
        elif flag is None:
            sample["dhw"] = 0
        if sample["dhw"] and self._boiler("heating_pump") is None:
            sample["heating"] = 0  # Ventil auf Warmwasser: Heizkreis bekommt nichts

    def filter_room_spikes(self, ts: int, rooms: list[tuple]) -> list[tuple]:
        """Einzelne Temperatur-Spitzen je Raum verwerfen (vor dem Speichern)."""
        out = []
        for climate, temp, target, valve, hum in rooms:
            kept = self.room_spikes(climate, ts, temp)
            if temp is not None and kept is None:
                self.store.add_daily(local(ts).date().isoformat(), "ignored_room_spike", 1)
                log.info("Raum %s: Temperatursprung auf %.1f °C ignoriert (Einzelspitze)", climate, temp)
            out.append((climate, kept, target, valve, hum))
        return out

    def count_rejected(self, ts: int) -> None:
        if self._rejected:
            self.store.add_daily(local(ts).date().isoformat(), "ignored_values", len(self._rejected))
            log.debug("Unplausible Werte verworfen: %s", sorted(self._rejected))

    # ------------------------------------------------------------------ Takt
    def tick(self, now_ts: int | None = None) -> None:
        now_ts = now_ts or int(time.time())
        ts = now_ts - now_ts % self.cfg.sample_interval_s
        sample, rooms, persons = self.snapshot()
        log.debug("Messwerte %s: %s | Räume %s | Personen %s", local(ts).strftime("%H:%M"),
                  {k: v for k, v in sample.items() if v is not None},
                  [(r[0], r[1], r[2], r[3]) for r in rooms], [(p, h, None if d is None else round(d, 1))
                                                            for p, h, d in persons])
        self.count_rejected(ts)
        self.infer_dhw(ts, sample)
        self._dhw_now = int(bool(sample.get("dhw")))
        self.update_live(now_ts)  # nach der Ladeerkennung: weiß, ob gerade Warmwasser lädt
        self.add_solar(ts, sample)
        rooms = self.filter_room_spikes(ts, rooms)
        self.track_vacation_return(ts, persons)
        vacation = self.on_vacation(ts)
        sample["vacation"] = int(vacation)
        sample["away_long"] = int(self.observe_persons(ts, persons, vacation))
        self.store.add_sample(ts, sample)
        self.store.add_room_samples((ts, *r) for r in rooms)
        self.store.add_person_samples((ts, *p) for p in persons)
        self.dhw_step(ts, sample)
        self.update_sleep(now_ts)  # Nachtzeiten aus dem Strom (0.36) – vor dem Wunschzustand, stündlich
        if self.control_loop is not None:  # Steuerung (0.34): Wunschzustände, Schreibvorgänge vormerken
            self.control_loop.plan(ts, sample, rooms)
        self.store.commit()

        step = ts - ts % int(ROOM_STEP_H * 3600)
        if self._last_room_step is None:
            self._last_room_step = step
        while self._last_room_step + int(ROOM_STEP_H * 3600) <= step:
            self.learn_rooms(self._last_room_step, self._last_room_step + int(ROOM_STEP_H * 3600))
            self._last_room_step += int(ROOM_STEP_H * 3600)

        hour = ts - ts % 3600
        if self._last_hour is None:
            self._last_hour = hour
        while self._last_hour + 3600 <= hour:
            self.aggregate_hour(self._last_hour)
            self._last_hour += 3600
            if local(self._last_hour).hour == 0:
                self.take_snapshot(self._last_hour)
        self.update_schedule(now_ts)
        self.save()

    def observe_persons(self, ts: int, persons: list[tuple], vacation: bool) -> bool:
        """Personen verfolgen (live und bei der Neuberechnung gleich); True = alle lange weg.

        Position immer, Gewohnheiten nicht im Urlaub. Sind alle lange weg (Reise ohne
        eingetragenen Urlaub), lernen Zapfprofil und Raummodelle nicht – eigenes Flag
        ``away_long``, der Urlaubsmodus (Anzeige, Rückkehrplan) bleibt davon unberührt.
        Person ohne Zustand (fehlt, 'unknown', 'unavailable'): in diesem Schritt nicht
        beobachten und nicht als lange weg zählen.
        """
        now = local(ts)
        known = 0
        for p, is_home, dist in persons:
            if is_home is None:
                continue
            self.persons[p].observe(now, bool(is_home), dist, learn=not vacation)
            known += 1
        return known == len(self.persons) and household_long_away(list(self.persons.values()))

    def record_live(self, now_ts: int | None = None) -> None:
        sample, rooms, _ = self.snapshot()
        self.live.add(now_ts or int(time.time()), sample, rooms)

    def update_schedule(self, now_ts: int | None = None, max_age_s: int = 3600) -> None:
        """Nachtzeiten und Temperaturen aus Tado neu lernen (stündlich).

        Bei Steuerung ``heatpilot`` schreibt HeatPilot die Zeiten selbst – daraus zu lernen wäre
        zirkulär: nichts lernen, nur merken, bis wann gesteuert wurde (``KV_MANAGED_UNTIL``); nach dem
        Zurückschalten zählen erst Nächte danach.
        """
        now_ts = now_ts or int(time.time())
        if now_ts - self._schedule_ts < max_age_s:
            return
        self._schedule_ts = now_ts
        if self.cfg.is_managing():
            self.store.put(KV_MANAGED_UNTIL, now_ts)
            return
        learned = schedule.learn(self.store, now_ts, set(self.cfg.floor_heating_rooms),
                                 {r.climate for r in self.found.rooms}, self.found.persons,
                                 since_ts=self.store.get(KV_MANAGED_UNTIL))
        eff = schedule.effective(self.cfg, learned)
        if eff.as_dict() != self.eff.as_dict():
            log.info("Zeiten/Temperaturen: %s (gelernt aus %d Nächten)", eff.as_dict(), learned["nights"])
        self.schedule_learned, self.eff = learned, eff
        self.store.put("schedule.learned", learned)

    def update_sleep(self, now_ts: int | None = None, max_age_s: int = 3600) -> None:
        """Schlafenszeiten aus dem Hausstromverbrauch (0.36) nachrechnen – stündlich, nur mit Leistungssensoren.

        Gerechnet werden nur fertige Nächte, die noch nicht gespeichert sind (nach der Datenübernahme beim
        ersten Takt alle). Daraus der Wochenplan in ``cfg.night_plan_learned`` – unabhängig von der
        Steuerung; wirksam nur mit ``night_plan_auto``, sonst Anzeige.
        """
        now_ts = now_ts or int(time.time())
        if not self.found.power or now_ts - self._sleep_ts < max_age_s:
            return
        self._sleep_ts = now_ts
        self.sleep_nights = sleep.learn(self.store, now_ts)
        self._sleep = sleep.summary(self.sleep_nights, now_ts, self.cfg, self.found.power, self.found.power_exclude)
        learned = sleep.learned_plan(self._sleep["plan"])
        if learned != self.cfg.night_plan_learned:
            log.info("Nachtzeiten aus dem Stromverbrauch (%d Nächte, %s): %s", self._sleep["nights"],
                     "wirksam" if self._sleep["active"] else "nur Anzeige",
                     " ".join(f"{d['day']} {d['start'] or '–'}/{d['end'] or '–'}" for d in self._sleep["plan"]))
            self.cfg.night_plan_learned = learned
            self.eff = schedule.effective(self.cfg, self.schedule_learned)

    def sleep_summary(self) -> dict:
        """Zusammenfassung der gelernten Nachtzeiten (gecacht aus update_sleep) für Oberfläche und API."""
        return self._sleep

    # ------------------------------------------------------------------ Lernen
    def seed_room_priors(self) -> None:
        """Noch ungelernte Raummodelle starten mit der gemessenen Auskühlzeit dieses Hauses statt einem Pauschalwert."""
        if self.house.balance_plausible:
            for m in self.rooms.values():
                m.adopt_house_tau(self.house.tau_cool_h)

    def relearn_rooms(self, climates: set[str]) -> None:
        """Neue/umgestellte Raummodelle sofort aus der gespeicherten Historie lernen."""
        first = self.store.query("SELECT MIN(ts) AS t FROM room_samples")[0]["t"]
        if first is None:
            return
        step = int(ROOM_STEP_H * 3600)
        log.info("Lerne Räume %s aus der gespeicherten Historie neu", sorted(climates))
        for st in range(first - first % step + step, self._last_room_step, step):
            self.learn_rooms(st, st + step, only=climates)

    def learn_rooms(self, start: int, end: int, only: set[str] | None = None) -> None:
        sys_rows = {
            r["ts"]: r
            for r in self.store.query(
                "SELECT ts, flow, heating, burner_on, dhw, pump_mod, t_out, vacation, away_long, sun_e, sun_s, sun_w "
                "FROM samples WHERE ts BETWEEN ? AND ?",
                (start, end),
            )
        }
        for climate, model in self.rooms.items():
            if only is not None and climate not in only:
                continue
            rows = self.store.query(
                "SELECT ts, temp, valve FROM room_samples WHERE room=? AND ts BETWEEN ? AND ? ORDER BY ts",
                (climate, start, end),
            )
            rows = [r for r in rows if r["temp"] is not None]
            if len(rows) < 3 or rows[-1]["ts"] - rows[0]["ts"] < 0.8 * ROOM_STEP_H * 3600:
                continue
            inputs, floor_inputs, outs, suns, overs, valves = [], [], [], [], [], []
            vacation = False
            for r in rows:
                s = sys_rows.get(r["ts"])
                if not s or s["flow"] is None or s["t_out"] is None:
                    continue
                vacation = vacation or bool(s["vacation"]) or bool(s["away_long"])  # Urlaub oder alle lange weg
                # Wärme kommt an, solange die Heizungspumpe Wasser umwälzt – auch nach dem
                # Brennerlauf (Nachlauf, Taktpausen). Fehlt die Pumpendrehzahl: Pumpen-, sonst Brennersignal.
                if s["pump_mod"] is not None:
                    active = int(s["pump_mod"] > 0)
                else:
                    active = s["heating"] if s["heating"] is not None else (s["burner_on"] or 0)
                if s["dhw"]:
                    active = 0  # Ventil auf Warmwasser
                warm = max(s["flow"] - r["temp"], 0) * (active or 0)
                inputs.append(warm * (r["valve"] or 0) / 100)
                floor_inputs.append(warm)  # Fußboden: kein Ventil, heizt immer mit
                overs.append(max(s["flow"] - r["temp"], 0))  # was der Heizkörper höchstens liefern könnte
                valves.append(r["valve"])  # None = kein Ventilwert
                outs.append(s["t_out"])
                if s["sun_s"] is not None:
                    suns.append((s["sun_e"], s["sun_s"], s["sun_w"]))
            if len(inputs) < 3:
                continue
            t0, t1 = rows[0]["temp"], rows[-1]["temp"]
            dt_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600
            heat, floor_in = sum(inputs) / len(inputs), sum(floor_inputs) / len(floor_inputs)
            t_out = sum(outs) / len(outs)
            solar = tuple(sum(v[i] for v in suns) / len(suns) for i in range(3)) if suns else None
            known = [v for v in valves if v is not None]
            if known:
                ext = self.ext_heat_step(climate, model, start, end, t0, t1, dt_h, heat, floor_in, t_out, solar,
                                         sum(overs) / len(overs), sum(known) / len(known), max(known))
            else:  # ohne Ventilwert ist Heizkörperwärme nicht von Fremdwärme zu trennen: nicht erkennen
                self._room_valve_prev.pop(climate, None)
                ext = climate in self.room_ext
            # Fenster offen o. ä., Urlaub oder alle lange weg, Fremdwärme: Estrich nachführen, aber nicht lernen
            update = abs(t1 - t0) <= 2.0 and not vacation and not ext
            model.learn(t0, t1, heat, t_out, dt_h, floor_input=floor_in, update=update, solar=solar)

    def ext_heat_step(self, climate: str, model: RoomModel, start: int, end: int, t0: float, t1: float,
                      dt_h: float, heat_input: float, floor_input: float, t_out: float,
                      solar: tuple[float, float, float] | None, flow_over: float, valve: float,
                      valve_max: float) -> bool:
        """Fremdwärme-Ereignis des Raums verfolgen; True = dieser Schritt gehört zu einem Ereignis.

        Beginn: unerklärter Anstieg (gemessen minus Modell) ≥ EXT_RATE_KH und ≥ EXT_MIN_K im Schritt,
        der Heizkörper liefert praktisch nichts (auch im Schritt davor, EXT_VALVE_MAX), und Sonne bzw.
        Fußboden sind entweder nicht im Spiel oder schon gelernt – sonst würde ein junges Modell seine
        eigene Lernchance als Ereignis verwerfen (Rückkopplung). Ein Abfall (Fenster auf) ist kein
        Ereignis, die Erholung danach auch nicht: der Raum muss über das höchste Niveau der letzten
        EXT_REF_H Stunden steigen, das ist dann der Startwert. Ende: siehe EXT_END_* / EXT_MAX_H.
        """
        valve_prev = self._room_valve_prev.get(climate)
        self._room_valve_prev[climate] = valve_max
        hist = [(ts, t) for ts, t in self._room_temp_hist.get(climate, ()) if start - EXT_REF_H * 3600 <= ts <= start]
        self._room_temp_hist[climate] = hist + [(end, t1)]
        ref = max([t0, *(t for _, t in hist)])  # Niveau vor einem Abfall (Lüften), sonst t0
        ev = self.room_ext.get(climate)
        if ev is not None:
            ev["peak"] = max(ev["peak"], t1)
            ev["rise"] = round(ev["peak"] - ev["t_start"], 2)
            limit = ev["start_ts"] + EXT_MAX_H * 3600
            if t1 <= ev["t_start"] + EXT_END_K or valve >= EXT_END_VALVE or end >= limit:
                self.store.add_room_event(ev["start_ts"], min(end, limit), climate, ev["rise"])
                del self.room_ext[climate]
                log.info("Raum %s: Fremdwärme vorbei (%s–%s, +%.1f K)", climate,
                         local(ev["start_ts"]).strftime("%d.%m. %H:%M"), local(min(end, limit)).strftime("%H:%M"), ev["rise"])
            return True
        if dt_h <= 0 or t1 - t0 < EXT_MIN_K or t1 - ref < EXT_MIN_K:
            return False
        unexplained = (t1 - t0) / dt_h - model.explained_rate(heat_input, t_out, (t0 + t1) / 2, solar)
        if unexplained < EXT_RATE_KH:
            return False
        if heat_input > EXT_HEAT_SHARE * flow_over or valve_prev is None or max(valve_max, valve_prev) > EXT_VALVE_MAX:
            return False  # der Heizkörper liefert(e) – das muss lernbar bleiben
        if solar is None and local(start).hour in EXT_DAY_HOURS:
            return False  # ohne Sonnendaten (kein Standort) kann es tagsüber Sonne sein – nicht erkennen, lernen
        sunny = solar is not None and max(solar) >= EXT_SUN_MAX
        floor_on = model.floor and floor_input > EXT_HEAT_SHARE * flow_over
        if (sunny or floor_on) and not model.inputs_learned(solar, floor=floor_on):
            return False  # Sonne/Fußboden noch nicht gelernt: erst lernen, dann erkennen
        self.room_ext[climate] = {"start_ts": start, "t_start": ref, "peak": t1, "rise": round(t1 - ref, 2)}
        log.info("Raum %s: Fremdwärme ab %s (+%.1f K, %.1f K/h unerklärt, Ventil %.0f %%)", climate,
                 local(start).strftime("%d.%m. %H:%M"), t1 - ref, unexplained, valve)
        return True

    def ext_frac(self, hour: int) -> float:
        """Anteil der Stunde, in dem irgendein gewählter Raum Fremdwärme hatte (abgeschlossen oder laufend)."""
        spans = [(r["start_ts"], r["end_ts"]) for r in self.store.query(
            "SELECT start_ts, end_ts, room FROM room_events WHERE end_ts > ? AND start_ts < ?", (hour, hour + 3600))
            if r["room"] in self.rooms]
        spans += [(ev["start_ts"], ev["start_ts"] + EXT_MAX_H * 3600) for ev in self.room_ext.values()]
        covered, cur = 0, None  # Vereinigung der Abschnitte innerhalb der Stunde
        for a, b in sorted((max(a, hour), min(b, hour + 3600)) for a, b in spans):
            if b <= a:
                continue
            if cur is not None and a <= cur[1]:
                cur[1] = max(cur[1], b)
            else:
                covered += cur[1] - cur[0] if cur else 0
                cur = [a, b]
        covered += cur[1] - cur[0] if cur else 0
        return covered / 3600

    def ext_summary(self, now_ts: int) -> dict[str, dict]:
        """Je Raum: laufendes Fremdwärme-Ereignis und Zahl der Ereignisse der letzten EXT_COUNT_DAYS Tage."""
        counts = Counter(r["room"] for r in self.store.query(
            "SELECT room FROM room_events WHERE start_ts >= ?", (now_ts - EXT_COUNT_DAYS * 86400,)))
        out = {}
        for c in self.rooms:
            ev = self.room_ext.get(c)
            out[c] = {
                "ext_event": None if ev is None else {"since": local(ev["start_ts"]).isoformat(), "rise_k": ev["rise"]},
                "ext_events_7d": counts.get(c, 0) + (1 if ev else 0),
            }
        return out

    def nominal_kw(self) -> float | None:
        return self.cfg.boiler_nominal_kw or num(self._boiler("nom_power"))

    def aggregate_hour(self, hour: int, learn: bool = True) -> None:
        rows = self.store.query(
            "SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (hour, hour + 3600)
        )
        if len(rows) < 2:
            if learn:
                self.learn_house_days(hour - 3600)  # Stunde fehlt (Neustart): offene Tage trotzdem abschließen
            return
        # Gas bis zum ersten Wert der nächsten Stunde – sonst fehlen die letzten 5 min jeder Stunde
        gas_rows = self.store.query(
            "SELECT ts, gas FROM samples WHERE ts >= ? AND ts <= ? AND gas IS NOT NULL ORDER BY ts",
            (hour, hour + 3600),
        )
        gas = [r["gas"] for r in gas_rows]
        raw_kwh = None
        if len(gas) >= 2:
            raw_kwh = (gas[-1] - gas[0]) * self.gas_factor
        recent = [r["gas_kwh"] for r in self.store.query(
            "SELECT gas_kwh FROM hourly WHERE ts >= ? AND ts < ? AND gas_kwh IS NOT NULL", (hour - 7 * 86400, hour))]
        day = local(hour).date().isoformat()
        gas_kwh, spike = None, None
        if raw_kwh is not None and raw_kwh < 0:
            self.store.add_daily(day, "ignored_gas_negative", 1)
            log.info("Gaszähler %s: Stand gesunken (%.1f kWh) – Stunde nicht gelernt", local(hour).strftime("%d.%m. %H:00"), raw_kwh)
        elif raw_kwh is not None:
            status, gas_kwh = plausibility.gas_hour(raw_kwh, rows, self.nominal_kw(), recent)
            if status == "spike":
                spike = raw_kwh
                self.store.add_daily(day, "ignored_gas_spike", 1)
                self.store.add_daily(day, "gas_spike_kwh", raw_kwh)
                log.info("Gaszähler %s: Sprung um %.1f kWh (Nachtrag?) – nicht gelernt, zählt nur im Verbrauch",
                         local(hour).strftime("%d.%m. %H:00"), raw_kwh)
            elif status == "missed":
                self.store.add_daily(day, "ignored_gas_missed", 1)
                log.info("Gaszähler %s: Brenner lief, Zähler zählte nur %.2f kWh – Stunde nicht gelernt",
                         local(hour).strftime("%d.%m. %H:00"), raw_kwh)
        t_out = [r["t_out"] for r in rows if r["t_out"] is not None]
        dhw = [r["dhw"] for r in rows if r["dhw"] is not None]
        home = [r["home_count"] for r in rows if r["home_count"] is not None]
        vac = [r["vacation"] for r in rows if r["vacation"] is not None]
        t_in = self._mean_room_temp(hour, hour + 3600)
        # Anteil des Gases fürs Warmwasser: nach Brennerleistung gewichtet (eine 15-min-Ladung mit
        # voller Leistung ist mehr als ein Viertel der Stunde, wenn der Kessel sonst taktet)
        pw_all = pw_dhw = 0.0
        for r in rows:
            if r["burner_on"]:
                pw = r["burner_pow"] if r["burner_pow"] is not None else 100.0
                pw_all += pw
                if r["dhw"]:
                    pw_dhw += pw
        dhw_energy = (pw_dhw / pw_all) if pw_all > 0 and dhw else (0.0 if dhw else None)
        row = (
            hour,
            sum(t_out) / len(t_out) if t_out else None,
            t_in,
            gas_kwh,
            sum(dhw) / len(dhw) if dhw else None,
            sum(1 for h in home if h > 0) / len(home) if home else None,
            sum(vac) / len(vac) if vac else None,
            spike,
            dhw_energy,
            self.ext_frac(hour),
        )
        self.store.add_hourly([row])
        self.store.commit()
        if learn:
            self.learn_house_hour(hour)

    def ignored(self, days: int = 7) -> dict:
        """Was in den letzten Tagen nicht gelernt wurde – für Selbsttest und Übersicht."""
        since = local(time.time() - days * 86400).date().isoformat()
        tot: dict[str, float] = {}
        for vals in self.store.daily(since).values():
            for k, v in vals.items():
                if k.startswith("ignored_") or k == "gas_spike_kwh":
                    tot[k] = tot.get(k, 0) + v
        models = [self.house.rls, self.dhw.loss, *(m.rls for m in self.rooms.values())]
        return {
            "days": days,
            "gas_spikes": int(tot.get("ignored_gas_spike", 0)),
            "gas_spike_kwh": round(tot.get("gas_spike_kwh", 0.0), 1),
            "gas_missed_hours": int(tot.get("ignored_gas_missed", 0)),
            "gas_negative": int(tot.get("ignored_gas_negative", 0)),
            "room_spikes": int(tot.get("ignored_room_spike", 0)),
            "values": int(tot.get("ignored_values", 0)),
            "learn_clipped": sum(m.clipped for m in models),
            "learn_rejected": sum(m.rejected for m in models),
        }

    def _mean_room_temp(self, start: int, end: int) -> float | None:
        r = self.store.query(
            "SELECT AVG(temp) AS t FROM room_samples WHERE ts >= ? AND ts < ? AND temp IS NOT NULL",
            (start, end),
        )
        return r[0]["t"] if r and r[0]["t"] is not None else None

    def learn_house_hour(self, hour: int) -> None:
        """Lernt die Stunde *vor* ``hour`` – mit beiden Nachbarn für eine mittige Ableitung.

        Gas der Stunde h passt zur Temperaturänderung über h (Mitte h−1 bis Mitte h+1),
        nicht zur Differenz der Stundenmittel h und h−1 (die läge eine halbe Stunde früher).
        """
        h = hour - 3600
        self.learn_house_days(h)
        rows = self.store.query("SELECT * FROM hourly WHERE ts IN (?, ?, ?)", (h - 3600, h, hour))
        by = {r["ts"]: r for r in rows}
        prev, cur, nxt = by.get(h - 3600), by.get(h), by.get(hour)
        if not cur or cur["gas_kwh"] is None or cur["t_in"] is None or cur["t_out"] is None:
            return
        if not prev or not nxt or prev["t_in"] is None or nxt["t_in"] is None:
            return
        d_in = (nxt["t_in"] - prev["t_in"]) / 2
        # Heizperiode: das gleitende 24-h-Mittel draußen, nicht die einzelne kühle Sommernacht
        r24 = self.store.query("SELECT AVG(t_out) AS t, COUNT(*) AS n FROM hourly "
                               "WHERE ts > ? AND ts <= ? AND t_out IS NOT NULL", (h - 86400, h))
        if not r24 or r24[0]["n"] < 12 or r24[0]["t"] > HEAT_SEASON_24H:
            return
        dhw = cur["dhw_frac"]
        ext = cur["ext_frac"] or 0  # Fremdwärme (Kamin, Ofen) in einem Raum: weder Auskühlen noch Bilanz lernen
        gas_free = all(r["gas_kwh"] is not None and r["gas_kwh"] <= GAS_FREE_KWH for r in (prev, cur, nxt))
        if gas_free and not dhw and not ext and local(h).hour in NIGHT_HOURS:
            last = self.store.query("SELECT MAX(ts) AS t FROM hourly WHERE ts < ? AND (gas_kwh IS NULL OR gas_kwh > ?)",
                                    (h, GAS_FREE_KWH))[0]["t"]
            self.house.learn_cooling(cur["t_in"], cur["t_out"], d_in, 99.0 if last is None else (h - last) / 3600)
        if (dhw is not None and dhw > 0) or ext > 0:
            return  # Warmwasserladung verfälscht die Heizbilanz (auch eine kurze verschiebt kWh), Fremdwärme auch
        self.house.learn(self.heating_gas(cur), cur["t_in"], cur["t_out"], d_in)

    def learn_house_days(self, h: int) -> None:
        """Alle Kalendertage vor dem der Stunde ``h`` abschließen (Tageswerte lernen), die noch offen sind.

        Nicht an die Stunde 00 gebunden: Fehlt sie (Add-on oder HA um Mitternacht neu gestartet),
        schließt die nächste gelernte Stunde den Tag ab. Ist ``h`` die Stunde 23, ist ihr Tag schon
        vollständig. Ein schon gelernter Tag wird vom Hausmodell nicht noch einmal gezählt, deshalb
        beginnt es nach einem Neustart gefahrlos beim Vortag.
        """
        d = local(h)
        end = d.date() + timedelta(days=1 if d.hour == 23 else 0)  # erster noch nicht abgeschlossener Tag
        open_day = self.__dict__.get("_house_day_open") or end - timedelta(days=1)
        open_day = max(open_day, end - timedelta(days=HOUSE_DAYS_CATCH_UP))
        while open_day < end:
            self.learn_house_day(midnight(datetime.combine(open_day, datetime.min.time())))
            open_day += timedelta(days=1)
        self._house_day_open = end

    def learn_house_day(self, ts: int) -> None:
        """Tagesmittel (Heizgas, innen–außen) des Kalendertags von ``ts`` lernen – siehe HouseModel.learn_day.

        Ein Urlaubstag zählt nur als Zustand für die Folgetage (sein Unterschied innen–außen), nicht als
        Lerntag – ebenso ein Tag mit mindestens EXT_DAY_SKIP_H Stunden Fremdwärme (Kaminabend).
        """
        day = local(ts)
        span = (midnight(day), midnight(day + timedelta(days=1)))
        rows = self.store.query("SELECT * FROM hourly WHERE ts >= ? AND ts < ? AND gas_kwh IS NOT NULL "
                                "AND t_in IS NOT NULL AND t_out IS NOT NULL", span)
        if len(rows) < DAILY_MIN_HOURS:
            return
        vacation = any((r["vacation_frac"] or 0) > 0.5 for r in rows)
        ext_h = self.store.query("SELECT COALESCE(SUM(ext_frac), 0) AS h FROM hourly WHERE ts >= ? AND ts < ?", span)[0]["h"]
        gas = []
        for r in rows:
            frac = r["dhw_energy_frac"] if r["dhw_energy_frac"] is not None else r["dhw_frac"]
            gas.append(self.heating_gas(r) if frac is None else r["gas_kwh"] * (1 - min(max(frac, 0.0), 1.0)))
        n = len(rows)
        self.house.learn_day(day.date().isoformat(), sum(r["t_in"] - r["t_out"] for r in rows) / n,
                             sum(r["t_out"] for r in rows) / n, sum(gas) / n,
                             learn=not vacation and ext_h < EXT_DAY_SKIP_H)

    def heating_gas(self, row) -> float:
        """Heizgas einer Stunde: bei Langzeitstunden (Warmwasser unbekannt) ohne den üblichen Warmwasserbedarf."""
        gas = row["gas_kwh"]
        if row["dhw_frac"] is None and row["dhw_energy_frac"] is None:
            base = self.dhw_base()
            if base:
                d = local(row["ts"])
                gas = max(gas - base[(d.weekday(), d.hour)], 0.0)
        return gas

    def dhw_base(self) -> dict[tuple[int, int], float] | None:
        """Warmwasser-Gas je Wochentag und Stunde (kWh) aus den Sommerstunden – täglich neu berechnet.

        Vor HeatPilot weiß die Langzeitstatistik nicht, wann Warmwasser geladen wurde. Im Sommer
        ist alles Gas Warmwasser (inkl. Legionellenschaltung zur festen Stunde) – dieses Profil
        wird im Winter von den Langzeitstunden abgezogen, sonst lernt das Haus das Warmwasser mit.
        """
        cached = self.__dict__.get("_dhw_base")
        if cached is not None and time.time() - cached[0] < 86400:
            return cached[1]
        rows = self.store.query("SELECT ts, t_out, gas_kwh FROM hourly ORDER BY ts")
        cells: dict[tuple[int, int], list[float]] = {}
        window: deque[tuple[int, float]] = deque()
        total, n = 0.0, 0
        for r in rows:
            if r["t_out"] is not None:
                window.append((r["ts"], r["t_out"]))
                total += r["t_out"]
            while window and window[0][0] <= r["ts"] - 86400:
                total -= window.popleft()[1]
            if r["gas_kwh"] is None or len(window) < 12 or total / len(window) < DHW_BASE_SUMMER_24H:
                continue
            d = local(r["ts"])
            cells.setdefault((d.weekday(), d.hour), []).append(r["gas_kwh"])
            n += 1
        base = None
        if n >= DHW_BASE_MIN_HOURS:
            by_hour = {hr: [v for (wd, h2), vals in cells.items() if h2 == hr for v in vals] for hr in range(24)}
            base = {}
            for wd in range(7):
                for hr in range(24):
                    v = cells.get((wd, hr), [])
                    if len(v) < DHW_BASE_MIN_CELL:
                        v = by_hour[hr]
                    base[(wd, hr)] = sum(v) / len(v) if v else 0.0
        self._dhw_base = (time.time(), base)
        return base

    def take_snapshot(self, ts: int) -> None:
        t_out = self.outdoor_now()
        self.store.add_snapshot(
            ts, "house", "", self.house.summary(self.cfg.design_outdoor_temp, t_out, 20.0)
        )
        for climate, m in self.rooms.items():
            self.store.add_snapshot(ts, "room", climate, m.summary(t_out or 5.0, self.cfg.max_flow_temp))
        self.store.commit()

    def flow_study(self, max_age_s: int = 3600) -> dict:
        """Vorlauf gegen Takten (stündlich neu – wertet 90 Tage aus)."""
        now = time.time()
        cached = getattr(self, "_flow_study", None)
        if cached is None or now - cached[0] > max_age_s:
            ok = guards.check_house(self.house)["ok"]
            self._flow_study = (now, flowstudy.study(self.store, offset_kw=self.house.offset_kw if ok else None,
                                                     heating_limit_k=self.house.heating_limit_k if ok else None))
        curve = flowstudy.curve_hint(num(self._thermo("hc_designtemp")), num(self._thermo("hc_seltemp")),
                                     num(self._thermo("hc_minexttemp")), num(self._thermo("hc_offsettemp")))
        return {**self._flow_study[1], "curve": curve}

    def week(self, days: int = weekly.WINDOW_DAYS, room: str | None = None, max_age_s: int = 600) -> dict:
        """Wochenraster Mo–So × Stunde (alle 10 min neu)."""
        now = time.time()
        cache = self.__dict__.setdefault("_week", {})
        key = (days, room)
        if key not in cache or now - cache[key][0] > max_age_s:
            rooms = [r.climate for r in self.found.rooms]
            house_ok = guards.check_house(self.house)["ok"]
            data = weekly.collect(self.store, int(now), days, self.house, rooms, room, house_ok)
            data["rooms"] = [{"climate": r.climate, "name": r.name} for r in self.found.rooms]
            data["room"] = room
            data["house_ok"] = house_ok
            if len(cache) > 8:
                cache.clear()
            cache[key] = (now, data)
        return cache[key][1]

    def presence(self, hours: float = 24) -> dict:
        """Wie HeatPilot Anwesenheit und Heimkehr deutet – je Person, nur Entfernungen."""
        now_ts = int(time.time())
        now = local(now_ts)
        since = now_ts - int(hours * 3600)
        rows: dict[str, dict] = {p: {"ts": [], "dist_km": [], "home": []} for p in self.persons}
        for r in self.store.query("SELECT ts, person, home, dist_km FROM person_samples WHERE ts >= ? ORDER BY ts",
                                  (since,)):
            if r["person"] in rows:
                d = rows[r["person"]]
                d["ts"].append(r["ts"])
                d["dist_km"].append(None if r["dist_km"] is None else round(r["dist_km"], 2))
                d["home"].append(r["home"])
        out = []
        for p, learner in self.persons.items():
            e = learner.eta(now)
            out.append({
                "person": p, "name": (self.states.get(p) or {}).get("attributes", {}).get("friendly_name")
                or p.replace("person.", ""),
                "home": learner.home, "dist_km": e["dist_km"], "speed_kmh": e["speed_kmh"],
                "eta": e["eta"].isoformat() if e["eta"] else None, "source": e["source"],
                "confidence": e["confidence"], "status": learner.status(now),
                "approach_speed_kmh": round(learner.approach_speed_kmh, 1),
                "arrivals_learned": learner.arrivals_learned, "long_away": learner.long_away,
                "habit": learner.habit_hours(),
                "arrivals": [{**a, "short": short_trip(a)} for a in learner.arrivals[-10:][::-1]],
                "quality": learner.arrival_quality(), "series": rows[p],
            })
        if self.on_vacation(now_ts):
            arrival = self.vacation.arrival(now, list(self.persons.values()))
        else:
            arrival = next_arrival(list(self.persons.values()), now)
        unknown = None if self.on_vacation(now_ts) else arrival_unknown(list(self.persons.values()), now)
        return {"now": now.isoformat(), "hours": hours, "persons": out,
                "anyone_home": any(p.home for p in self.persons.values()),
                "unknown_from": unknown.isoformat() if unknown else None,
                "vacation": self.on_vacation(now_ts),
                "arrival": None if not arrival else {k: (v.isoformat() if isinstance(v, datetime) else v)
                                                      for k, v in arrival.items()}}

    def tado_advice(self, max_age_s: int = 900) -> dict:
        """Empfehlungen an Tado (höchstens alle 15 min neu berechnet – wertet 3 Wochen aus)."""
        now = time.time()
        if self._advice is None or now - self._advice[0] > max_age_s:
            items = advice.build(self)
            resolved = advice.track(self.store, items)
            self._advice = (now, items, resolved)
        return {"items": self._advice[1], "resolved": self._advice[2]}

    def note_changes(self, s: dict) -> None:
        """Wichtige Zustandswechsel ins Log schreiben – nachvollziehbar, was das Add-on denkt."""
        plan = (s.get("plan") or {}).get("best") or {}
        current = {
            "Hausmodell plausibel": s["guards"]["house"]["ok"],
            "Warmwasser-Modell plausibel": s["guards"]["dhw"]["ok"],
            "plausible Räume": s["guards"]["rooms_ok"],
            "Urlaub": s["vacation"]["active"],
            "Ankunft-Quelle": (s.get("arrival") or {}).get("source"),
            "Vorheizen ab": (plan.get("start") or "")[:16] or None,
            "Warmwasser-Empfehlung": (s["dhw"].get("recommendation") or {}).get("target"),
            "Heizfreigabe": s.get("hc_mode"),
            "Bedarf laut Tado-Soll": s.get("setpoint_demand"),
        }
        prev = getattr(self, "_noted", None)
        if prev is not None:
            for key, value in current.items():
                if prev.get(key) != value:
                    log.info("Änderung: %s %s → %s", key, prev.get(key), value)
        self._noted = current

    def dhw_recommend(self, ts: int, sample: dict) -> tuple[dict, float]:
        """Empfehlung für den Speicher jetzt; gibt (Prüfung, Ziel für den Schatten) zurück."""
        now = local(ts)
        home = sample.get("home_count")
        arrival = unknown = None
        if sample.get("vacation"):
            ret = self.vacation.arrival(now, list(self.persons.values()))
            home, arrival = 0, ret["eta"] if ret else None
        elif home == 0:
            learners = list(self.persons.values())
            nxt = next_arrival(learners, now)
            arrival = nxt["eta"] if nxt else None
            unknown = arrival_unknown(learners, now)  # Gewohnheit lernt noch: nicht auf Minimum
        check = guards.check_dhw(self.dhw)
        if check["ok"]:
            self.dhw_rec = self.dhw.recommend(now, None if home is None else home > 0, arrival, self.cfg,
                                              unknown_from=unknown, current_set=num(sample.get("dhw_set")))
            return check, self.dhw_rec["target"]
        # unsicheres Modell: keine Empfehlung, Schatten folgt dem heutigen Soll
        self.dhw_rec = {"target": None, "reason": "lernt noch: " + "; ".join(check["reasons"])}
        return check, sample.get("dhw_set") or self.cfg.dhw_comfort_temp

    def dhw_step(self, ts: int, sample: dict) -> None:
        """Warmwasser lernen und die Spar-Strategie im Schatten mitlaufen lassen."""
        t_cur = sample.get("dhw_temp")
        nom = self.nominal_kw()  # Einstellung vor Kessel-Sensor (der liefert oft keine Zahl)
        kw = None
        if sample.get("burner_pow") is not None and nom:
            kw = sample["burner_pow"] / 100 * nom
        prev, self._prev_dhw = self._prev_dhw, (ts, t_cur, kw)
        # Haushalt leer: Zapfprofil sichern; wird daraus „alle lange weg“, die leeren Stunden davor
        # zurücknehmen (wie bei den Personen)
        self.dhw.note_household(sample.get("home_count"), bool(sample.get("away_long")))
        if prev is None:
            # erst ab dem zweiten Messpunkt lernen – die Empfehlung gibt es aber sofort
            self.dhw_recommend(ts, sample)
            return
        dt_h = (ts - prev[0]) / 3600
        now = local(ts)
        vacation = bool(sample.get("vacation"))
        frozen = vacation or bool(sample.get("away_long"))  # Urlaub oder alle lange weg: Zapfprofil nicht lernen
        # hätte mehr Reserve das Ziel der Strategie bis eben angehoben? Nur dann hilft sie bei zu
        # kaltem Wasser (Stand vor dem Schritt; auch der Boden „keine Zapfung erwartet“ mit jemandem
        # zu Hause ist Minimum + Reserve)
        bound = bool((self.dhw_rec or {}).get("margin_bound"))
        res = self.dhw.step(now, dt_h, prev[1], t_cur, bool(sample.get("dhw")), kw,
                            learn_usage=not frozen, special=bool(sample.get("dhw_special")),
                            dhw_set=num(sample.get("dhw_set")), burner_kw_prev=prev[2] if len(prev) > 2 else None)
        check, shadow_target = self.dhw_recommend(ts, sample)
        sh = self.dhw_shadow.step(
            self.dhw, t_cur, shadow_target, res["draw_k"], dt_h, self.cfg.dhw_comfort_temp,
            dhw_min=self.cfg.dhw_min_temp, margin_bound=bound, dhw_max=self.cfg.dhw_max_temp,
            mirror=t_cur if res.get("special") else None,  # Sonderladung des Reglers trifft beide Speicher
        )
        if sh["raise_margin"] and check["ok"] and not frozen:  # Urlaub / lange weg: Reserve bleibt eingefroren
            self.dhw.note_cold_draw()
        day = now.date().isoformat()
        for key, val in (
            ("dhw_draw_k", res["draw_k"]),
            ("dhw_loss_kwh", res["loss_kwh"]),
            ("dhw_charge_kwh", res["charge_kwh"]),
            ("dhw_loss_strategy_kwh", sh["loss_kwh"]),
            ("dhw_cold_draws", sh["cold_draw"]),
        ):
            if val:
                self.store.add_daily(day, key, val)
        if self.dhw.kwh_per_kelvin() is not None:
            # Ladeenergie nur, solange auch Verluste berechnet werden (Nenner der Ersparnis) –
            # auch 0 schreiben: markiert den Tag als so gezählt
            self.store.add_daily(day, "dhw_charge_cmp_kwh", res["charge_kwh"])

    def save(self) -> None:
        self.store.put("model.dhw", self.dhw.to_dict())
        self.store.put("model.house", self.house.to_dict())
        for climate, m in self.rooms.items():
            self.store.put(f"model.room.{climate}", m.to_dict())
        for p, learner in self.persons.items():
            self.store.put(f"model.person.{p}", learner.to_dict())
        self.store.put("model.room_ext", self.room_ext)
        self.store.put("last_room_step", self._last_room_step)
        self.store.put("last_hour", self._last_hour)

    # ------------------------------------------------------------------ Auswertung
    def summary(self) -> dict:
        now_ts = int(time.time())
        now = local(now_ts)
        t_out = self.outdoor_now()
        _, rooms, _ = self.snapshot()
        temps = [r[1] for r in rooms if r[1] is not None]
        t_in = sum(temps) / len(temps) if temps else None

        day_start = midnight(now)
        events = [
            (r["ts"], r["state"])
            for r in self.store.query(
                "SELECT ts, state FROM burner_events WHERE ts >= ? ORDER BY ts", (day_start - 86400,)
            )
        ]
        cycling = analysis.cycling_stats(events, day_start, now_ts + 1)
        today = now.date().isoformat()
        counters = self.counter_days(today).get(today)
        if counters:  # Zähler der Therme sind genauer als Ein/Aus-Ereignisse
            cycling.update(counters)
        if "avg_run_min" not in (counters or {}):  # ohne Heizlaufzeit-Zähler: Läufe ohne Warmwasser
            avg, _ = analysis.heat_runs(self.store, day_start, now_ts + 1)
            cycling["avg_run_min"] = None if avg is None else round(avg, 1)
        live = self.store.daily(today).get(today, {})
        cycling["blocked_min"] = round(live.get("blocked_min", 0.0), 1)
        cycling["waiting_min"] = round(live.get("waiting_min", 0.0), 1)
        cycling["mode_wait_min"] = round(live.get("mode_wait_min", 0.0), 1)
        cycling["enable_delay_min"] = round(live.get("enable_delay_min", 0.0), 1)
        cycling["enable_delay_count"] = int(live.get("enable_delay_count", 0))
        cycling["mode_switch_count"] = int(live.get("mode_switch_count", 0))
        cycling["overrun_min"] = round(live.get("overrun_min", 0.0), 1)
        cycling["overrun_kwh"] = round(live.get("overrun_kwh", 0.0), 2)
        starts, runtime = num(self._boiler("heat_starts")), num(self._boiler("heat_runtime"))
        cycling["lifetime_run_per_start_min"] = (
            round(runtime / starts, 1) if starts and runtime else None
        )

        hourly = [dict(r) for r in self.store.query("SELECT * FROM hourly ORDER BY ts")]
        nom = self.nominal_kw()
        room_checks = {c: guards.check_room(m) for c, m in self.rooms.items()}
        check_rooms = {c: m for c, m in self.rooms.items()
                       if room_checks[c]["ok"] and not m.floor and c not in self.cfg.floor_heating_rooms}
        room_hourly: dict[int, dict[str, float]] = {}
        if check_rooms:
            for r in self.store.query(
                "SELECT ts - ts % 3600 AS h, room, AVG(temp) AS t FROM room_samples "
                "WHERE temp IS NOT NULL GROUP BY h, room"
            ):
                if r["room"] in check_rooms:
                    room_hourly.setdefault(r["h"], {})[r["room"]] = r["t"]
        savings = analysis.estimate_savings(
            hourly, self.house, self.eff.away_temp, self.eff.night_temp, self.eff.is_night,
            p_max_kw=(nom or 18.0) * 0.95, rooms=check_rooms, room_hourly=room_hourly,
            max_flow=self.cfg.max_flow_temp,
        )
        house_check = guards.check_house(self.house)
        names_by_climate = {r.climate: r.name for r in self.found.rooms}
        gas_total = sum((h["gas_kwh"] or 0) + (h["gas_spike_kwh"] or 0) for h in hourly)
        span_days = savings.hours / 24  # nur Stunden, in denen sich Heizgas bestimmen ließ
        year_days = savings.span_hours / 24  # Hochrechnung: Urlaub zählt mit (dort wird nichts gespart)
        price, fee = self.cfg.gas_price_eur_per_kwh, self.cfg.gas_base_fee_eur_per_year
        last_year = [h for h in hourly if h["ts"] >= now_ts - 365 * 86400]
        year_kwh = sum((h["gas_kwh"] or 0) + (h["gas_spike_kwh"] or 0) for h in last_year)
        year_span = (now_ts - last_year[0]["ts"]) / 86400 if last_year else 0
        costs = {
            "kwh": round(year_kwh, 0),
            "days": round(year_span, 1),
            "eur": round(year_kwh * price + fee * year_span / 365, 0),
            # Jahreswert nur mit vollen 12 Monaten – Hochrechnen aus Teilen verzerrt (Sommer/Winter)
            "eur_per_year": round(year_kwh * price + fee, 0) if year_span >= 360 else None,
            "full_year": year_span >= 360,
        }

        persons = [{**p.eta(now), "status": p.status(now)["text"], "long_away": p.long_away}
                   for p in self.persons.values()]
        vacation = self.on_vacation(now_ts)
        if vacation:  # Rückkehr: schnelle GPS-Annäherung vor geplantem Ende vor Datum
            arrival = self.vacation.arrival(now, list(self.persons.values()))
        else:
            arrival = next_arrival(list(self.persons.values()), now)
        plan = None
        if arrival and arrival["eta"] and t_out is not None and house_check["ok"]:
            room_inputs, skipped = self.plan_rooms(rooms, room_checks)
            # Außentemperatur zur Ankunft: Vorhersagepunkt in der Nähe, sonst die aktuelle (seit 0.36.1 sichtbar)
            point = self.forecast_point_at(arrival["eta"])
            t_used = t_out if point is None else point[1]
            plan = analysis.preheat_plan(
                room_inputs, self.house, arrival["eta"], now, t_used, self.cfg.max_flow_temp,
                dhw=self.dhw_for_arrival(arrival["eta"], now),
            )
            if plan is not None:  # worauf der Plan sich verlässt
                plan = {**plan, "arrival_source": arrival.get("source"), "skipped_warm": skipped,
                        "arrival_confidence": arrival.get("confidence"), "arrival_person": arrival.get("person"),
                        "t_out_used": t_used, "t_out_source": "aktuell" if point is None else "vorhersage",
                        "t_out_at": None if point is None else point[0].astimezone().isoformat()}
        self.last_plan = plan
        ext = self.ext_summary(now_ts)

        flow_needed = None
        if t_out is not None:
            needs = [
                self.rooms[c].required_flow(target, t_out)
                for c, temp, target, _, _ in rooms
                if target is not None and c in self.rooms and room_checks[c]["ok"]
            ]
            if needs:
                flow_needed = round(min(max(max(needs), 25), self.cfg.max_flow_temp), 1)

        ready = readiness.assess(self, now_ts)
        return {
            "now": now.isoformat(),
            "mode": self.cfg.mode,
            "t_out": t_out,
            "t_in": None if t_in is None else round(t_in, 2),
            "house": self.house.summary(self.cfg.design_outdoor_temp, t_out, t_in),
            "rooms": [
                {**self.rooms[c].summary(t_out if t_out is not None else 5.0, self.cfg.max_flow_temp),
                 "temp": temp, "target": target, "valve": valve, "humidity": hum,
                 "check": room_checks[c], **ext[c]}
                for c, temp, target, valve, hum in rooms if c in self.rooms
            ],
            "cycling": cycling,
            "costs": costs,
            "dhw": self.dhw_summary(),
            "vacation": {
                "active": vacation,
                "until": (self.vacation.end(now).isoformat() if vacation and self.vacation.end(now) else None),
                "until_exact": bool(vacation and self.vacation.trip(now) and self.vacation.trip(now).end_exact),
                "ranges": [[t.start.isoformat(), t.end.isoformat(), t.end_exact] for t in self.vacation.trips],
                "returned": sorted(self.vacation.returned),
            },
            "advice": self.tado_advice(),
            "ignored": self.ignored(),
            "flow_study": self.flow_study(),
            "schedule": {"learned": self.schedule_learned, "effective": self.eff.as_dict(),
                         # bei Steuerung heatpilot wäre Lernen aus Tado zirkulär: der Wochenplan gilt
                         "learning": self.cfg.learn_schedule and not self.cfg.is_managing()},
            "sleep": self.sleep_summary(),  # Nachtzeiten aus dem Stromverbrauch (0.36)
            # seit 0.36.1: Wettervorhersage (Kachel) und „Bereit für die Steuerung?“ (Stufe und ein Satz)
            "forecast": self.forecast_summary(now_ts),
            "readiness": {k: ready[k] for k in ("level", "headline")},
            "control_mode": self.cfg.control_mode,
            "limits": {
                "away_temp": self.eff.away_temp,      # damit rechnet die Ersparnis (gelernt, nie darunter)
                "night_temp": self.eff.night_temp,
                "away_floor": self.cfg.away_temp,     # feste Untergrenzen aus den Einstellungen
                "night_floor": self.cfg.night_temp,
                "dhw_min_temp": self.cfg.dhw_min_temp,
                "max_flow_temp": self.cfg.max_flow_temp,
            },
            "guards": {
                "house": house_check,
                "dhw": guards.check_dhw(self.dhw),
                "rooms_ok": sum(1 for r in room_checks.values() if r["ok"]),
                "rooms_total": len(room_checks),
            },
            "savings": {
                "hours": savings.hours,
                "cold_hours": savings.cold_hours,
                "rooms_checked": len(check_rooms),
                "rooms_total": len(self.rooms),
                # plausible Fußbodenräume werden nicht je Raum simuliert (zu träge für Stundenschritte)
                "rooms_ok_floor": sum(1 for c, m in self.rooms.items() if room_checks[c]["ok"] and c not in check_rooms),
                "room_hours": savings.room_hours,
                "room_cold": [{"room": c, "name": names_by_climate.get(c, c), "hours": n}
                              for c, n in (savings.room_cold or {}).items() if n],
                "lead_by_rooms": savings.lead_by_rooms,
                "lead_by_house": savings.lead_by_house,
                "reliable": house_check["ok"],
                "actual_kwh": savings.actual_kwh,
                "saved_kwh": savings.saved_kwh,
                "saved_pct": savings.saved_pct,
                "saved_eur": round(savings.saved_kwh * self.cfg.gas_price_eur_per_kwh, 2),
                # Jahreswert nur mit vollem Jahr, sonst bleibt der Prozentwert die Aussage
                "year_kwh": round(savings.saved_kwh * 365 / year_days, 0) if year_days >= 360 else None,
                "year_eur": round(savings.saved_kwh * 365 / year_days * self.cfg.gas_price_eur_per_kwh, 0)
                if year_days >= 360 else None,
                "dhw_separated": savings.dhw_separated,
                "gas_total_kwh": round(gas_total, 1),
                "span_days": round(span_days, 1),
                "daily": savings.daily[-60:],
            },
            "persons": [{**p, "eta": p["eta"].isoformat() if p["eta"] else None} for p in persons],
            "arrival": None if not arrival else {**arrival, "eta": arrival["eta"].isoformat()},
            "plan": None if not plan else {
                **plan,
                "best": None if not plan.get("best") else {
                    **plan["best"],
                    "start": plan["best"]["start"].isoformat(),
                    "steps": [{**st, "start": st["start"].isoformat(), "end": st["end"].isoformat()}
                              for st in plan["best"].get("steps", [])],
                },
                "options": [{**o, "start": o["start"].isoformat()} for o in plan.get("options", [])],
            },
            "flow_recommendation": flow_needed,
            "flow_current_setpoint": num(self._boiler("sel_flow_temp")),
            "flow_calculated": num(self._thermo("hc_calc_flow")),
            "min_period": num(self._boiler("min_period")),
            "hyst_on": num(self._boiler("hyst_on")),
            "hyst_off": num(self._boiler("hyst_off")),
            "heating_off": flag(self._boiler("heating_off")),
            "heat_enabled": self.heat_enabled(),
            "hc_mode": self._thermo("hc_mode"),
            "hc_seltemp": num(self._thermo("hc_seltemp")),
            "remote_temp": num(self._thermo("hc_remotetemp")),
            "pump_mod": num(self._boiler("pump_mod")),
            "demand_pct": num(self._val(self.found.demand_sensor)),
            "setpoint_demand": self._demand,
            "emsesp": bool(self.ems),
            "discovered": self.found.as_dict(private=False),
        }

    def plan_rooms(self, rooms: list[tuple], room_checks: dict) -> tuple[list[tuple], list[str]]:
        """Räume für den Vorheizplan: plausible Modelle mit Ist und Soll – ein Raum mit laufender
        Fremdwärme (Kamin) gilt als schon warm und wird nicht eingeplant (zweiter Wert: seine Namen)."""
        names = {r.climate: r.name for r in self.found.rooms}
        inputs, skipped = [], []
        for climate, temp, target, _, _ in rooms:
            if climate not in self.rooms or temp is None or target is None or not room_checks[climate]["ok"]:
                continue
            if climate in self.room_ext:
                skipped.append(names.get(climate, climate))
                continue
            inputs.append((self.rooms[climate], temp, target))
        return inputs, skipped

    def house_signature(self, days: int = 365) -> list[dict]:
        """Tagesmittel: Gasleistung gegen Temperaturdifferenz (Energiesignatur).

        Einzelne Stunden streuen stark (Kessel taktet, Warmwasser lädt) – Tagesmittel zeigen,
        ob die gelernte Linie passt. Stunden mit Warmwasserladung (bekannt) zählen nicht.
        """
        since = int(time.time()) - days * 86400
        by_day: dict[str, list[tuple[float, float]]] = {}
        for r in self.store.query(
            "SELECT ts, t_in, t_out, gas_kwh, dhw_frac, dhw_energy_frac FROM hourly WHERE ts >= ? "
            "AND gas_kwh IS NOT NULL AND t_in IS NOT NULL AND t_out IS NOT NULL", (since,)
        ):
            if r["t_out"] > 15 or (r["dhw_frac"] or 0) > 0:  # wie beim Lernen
                continue
            by_day.setdefault(local(r["ts"]).date().isoformat(), []).append(
                (r["t_in"] - r["t_out"], self.heating_gas(r)))
        out = []
        for day, vals in sorted(by_day.items()):
            if len(vals) >= 12:
                out.append({"day": day, "dt": round(sum(v[0] for v in vals) / len(vals), 2),
                            "kw": round(sum(v[1] for v in vals) / len(vals), 3), "hours": len(vals)})
        return out

    def dhw_points(self, days: int = 14, now_ts: int | None = None) -> list[dict]:
        """Gemessenes Auskühlen des Speichers (ohne Ladung, ohne Zapfung), je 1 K gemittelt.

        Dieselbe Auswahl wie beim Lernen (DrawWatch, Nachlauf nach der Ladung) – so lässt sich
        prüfen, ob die gelernte Kurve passt.
        """
        now_ts = now_ts or int(time.time())
        rows = self.store.query(
            "SELECT ts, dhw_temp, dhw FROM samples WHERE ts >= ? AND dhw_temp IS NOT NULL ORDER BY ts",
            (now_ts - days * 86400,))
        bins: dict[int, list[float]] = {}
        watch = DrawWatch(self.dhw.sensor_step)  # dieselbe Fühlerstufe wie beim Lernen
        last_charge = None  # Ende der letzten Ladung (nach dem Nachlauf)
        tail = tail_ts = None  # Nachlauf nach dem Ladesignal: [(Stunden seit Ladeende, Temperatur)], Ladeende
        for a, b in zip(rows, rows[1:]):
            dt_h = (b["ts"] - a["ts"]) / 3600
            if not 0 < dt_h <= 0.5:
                watch.reset()
                continue
            if b["dhw"]:
                watch.reset()
                tail, tail_ts = [(0.0, b["dhw_temp"])], b["ts"]
                continue
            if tail is not None:
                tail.append(((b["ts"] - tail_ts) / 3600, b["dhw_temp"]))
                if tail[-1][0] <= AFTER_CHARGE_H + 1e-6 and tail_rising(tail):
                    continue  # Nachlauf zählt noch zur Ladung
                tail, last_charge = None, a["ts"]
            since = 99.0 if last_charge is None else (b["ts"] - last_charge) / 3600
            seg = watch.step(local(b["ts"]), dt_h, a["dhw_temp"], b["dhw_temp"], self.dhw.cooling_kh, since)["segment"]
            if seg:
                t0, hours, t1 = seg
                bins.setdefault(round((t0 + t1) / 2), []).append((t0 - t1) / hours)
        return [{"t": t, "kh": round(sum(v) / len(v), 3), "n": len(v)}
                for t, v in sorted(bins.items()) if len(v) >= 3]

    def dhw_summary(self) -> dict:
        t_now = num(self._boiler("dhw_temp"))
        rows = self.store.query(
            "SELECT key, SUM(value) AS v, COUNT(DISTINCT day) AS days FROM daily "
            "WHERE key LIKE 'dhw_%' GROUP BY key"
        )
        tot = {r["key"]: r["v"] for r in rows}
        days = next((r["days"] for r in rows if r["key"] == "dhw_loss_kwh"), 0)  # Zeitraum der Verluste
        loss, loss_s = tot.get("dhw_loss_kwh", 0.0), tot.get("dhw_loss_strategy_kwh", 0.0)
        charge = self.dhw_charge_compared()
        today = local(time.time()).date().isoformat()
        return {
            **self.dhw.summary(t_now),
            "temp": t_now,
            "set": num(self._boiler("dhw_seltemp")),
            "recommendation": self.dhw_rec,
            "today": {k: round(v, 2) for k, v in self.store.daily(today).get(today, {}).items()
                      if k.startswith("dhw_")},
            "period_days": days,
            "charge_kwh": round(charge, 1),
            "charge_total_kwh": round(tot.get("dhw_charge_kwh", 0.0), 1),
            "loss_kwh": round(loss, 1),
            "loss_strategy_kwh": round(loss_s, 1),
            "saved_kwh": round(loss - loss_s, 1),
            "saved_pct": round(100 * (loss - loss_s) / charge, 1) if charge > 1 else None,
            "saved_eur": round((loss - loss_s) * self.cfg.gas_price_eur_per_kwh, 2),
            "cold_draws": int(tot.get("dhw_cold_draws", 0)),
            "points": self.dhw_points(),
            "curve_kh": {t: round(self.dhw.cooling_kh(t), 3) for t in range(30, 71)},
            "model_ok": guards.check_dhw(self.dhw)["ok"],
            "kwh_per_k_avg": None if self.dhw.kwh_per_kelvin() is None else round(self.dhw.kwh_per_kelvin(), 3),
            "next_charge": self.dhw_next_charge(t_now),
            "disinfect": self.disinfect_schedule(),
        }

    def dhw_charge_compared(self) -> float:
        """Ladeenergie über denselben Zeitraum wie die Verluste (Nenner der Warmwasser-Ersparnis).

        Tage mit ``dhw_charge_cmp_kwh`` (zählt erst ab dem Schritt, in dem die Ladekosten gelernt
        sind) nehmen diesen Wert; ältere Tage ohne ihn zählen nur, wenn an ihnen Verluste berechnet wurden.
        """
        rows = self.store.query("SELECT day, key, SUM(value) AS v FROM daily WHERE key IN "
                                "('dhw_charge_kwh', 'dhw_charge_cmp_kwh', 'dhw_loss_kwh') GROUP BY day, key")
        by_day: dict[str, dict] = {}
        for r in rows:
            by_day.setdefault(r["day"], {})[r["key"]] = r["v"]
        total = 0.0
        for v in by_day.values():
            if "dhw_charge_cmp_kwh" in v:
                total += v["dhw_charge_cmp_kwh"]
            elif "dhw_loss_kwh" in v:
                total += v.get("dhw_charge_kwh", 0.0)
        return total

    def dhw_set_profile(self) -> list[float | None] | None:
        """Warmwasser-Soll-Zeitprogramm des Reglers, gelernt aus den Messwerten (je 5 min nach Wanduhr).

        Je Slot der häufigste Wert der letzten 14 Tage, nur wenn ≥ 70 % der Tage ihn zeigen –
        sonst None (dann gilt das aktuelle Soll). dhw_switchtime von EMS-ESP enthält nur einen
        Eintrag und passt nicht zu den gemessenen Umschaltzeiten, deshalb nicht verwendet.
        """
        cached = self.__dict__.get("_dhw_set_prof")
        if cached and time.time() - cached[0] < DHW_SET_CACHE_S:
            return cached[1]
        rows = self.store.query("SELECT ts, dhw_set FROM samples WHERE ts >= ? AND dhw_set IS NOT NULL",
                                (int(time.time()) - DHW_SET_DAYS * 86400,))
        per_day: dict[tuple[str, int], float] = {}
        for r in rows:
            w = local(r["ts"])
            per_day[(w.date().isoformat(), self._set_slot(w))] = round(r["dhw_set"] * 2) / 2
        counts: dict[int, Counter] = {}
        for (_, slot), v in per_day.items():
            counts.setdefault(slot, Counter())[v] += 1
        prof: list[float | None] = [None] * (24 * 60 // DHW_SET_SLOT_MIN)
        for slot, c in counts.items():
            v, n = c.most_common(1)[0]
            total = sum(c.values())
            if total >= DHW_SET_MIN_DAYS and n >= DHW_SET_SHARE * total:
                prof[slot] = v
        prof = prof if any(v is not None for v in prof) else None
        self._dhw_set_prof = (time.time(), prof)
        return prof

    @staticmethod
    def _set_slot(when: datetime) -> int:
        w = when.astimezone() if when.tzinfo else when  # Wanduhr, auch über die Zeitumstellung
        return (w.hour * 60 + w.minute) // DHW_SET_SLOT_MIN

    def dhw_next_charge(self, t_now: float | None) -> dict | None:
        """Nächste Speicherladung aus Soll und Einschalt-Hysterese des Kessels (z. B. 55 − 10 = 45 °C).

        Mit gelerntem Soll-Zeitprogramm gilt je Uhrzeit dessen Grenze (nachts z. B. 45 − 10 = 35 °C);
        springt das Soll morgens über den Speicher, ist das die Ladung.
        """
        set_t, hyst = num(self._boiler("dhw_seltemp")), num(self._boiler("dhw_hyst_on"))
        if t_now is None or set_t is None or hyst is None:
            return None
        hyst = abs(hyst)
        trigger = set_t - hyst
        if flag(self._boiler("dhw_active")) or t_now <= trigger:
            return {"trigger": trigger, "eta": None, "now": True}
        now = local(time.time())
        prof = self.dhw_set_profile()
        v_now = None if prof is None else prof[self._set_slot(now)]
        if v_now is not None and abs(v_now - set_t) > 0.5:
            prof = None  # Programm passt nicht zum heutigen Soll (umgestellt?): wie bisher fest rechnen

        def trig(when: datetime) -> float:
            v = prof[self._set_slot(when)] if prof else None
            return (set_t if v is None else v) - hyst

        eta = self.dhw.next_charge(now, t_now, trig if prof else trigger)
        out = {"trigger": trigger, "eta": eta.isoformat() if eta else None,
               "hours": None if eta is None else round((eta - now).total_seconds() / 3600, 1), "now": False}
        if eta is not None and prof:
            out["trigger_at_eta"] = trig(eta)
            out["by_schedule"] = trig(eta) > trig(eta - timedelta(minutes=DHW_SET_SLOT_MIN)) + 0.1
        return out

    def disinfect_schedule(self) -> dict | None:
        """Legionellenschaltung laut Regler (Tag/Uhrzeit) – diese Ladungen lernt das Modell nicht."""
        day, hour = self._thermo("disinfect_day"), num(self._thermo("disinfect_hour"))
        if day is None and hour is None:
            return None
        return {"day": day, "hour": None if hour is None else int(hour),
                "special_charges": self.dhw.special_charges}

    def dhw_for_arrival(self, arrival: datetime, now: datetime) -> dict | None:
        """Wie lange muss der Speicher vor der Ankunft laden? (teilt sich die Therme mit dem Heizen)"""
        t_now = num(self._boiler("dhw_temp"))
        if t_now is None:
            return None
        target = min(self.cfg.dhw_comfort_temp + self.dhw.comfort_margin, self.cfg.dhw_max_temp)
        hours_to = max((arrival - now).total_seconds() / 3600, 0.0)
        # bis kurz vor Ankunft kühlt er weiter aus (Ladung ist erst gegen Ende geplant)
        t_start = self.dhw.cool(t_now, max(hours_to - 2.0, 0.0))
        if t_start >= target - 1.0:
            return None
        if guards.check_dhw(self.dhw)["ok"]:
            hours = self.dhw.charge_hours(t_start, target)
        else:  # Modell noch unsicher: vorsichtig mit 40 K/h rechnen
            hours = max(target - t_start, 0.0) / 40.0
        return {"hours": round(hours, 2), "from": round(t_start, 1), "to": round(target, 1)}

    def counter_days(self, since_day: str) -> dict[str, dict]:
        """Starts und Laufzeit je Tag aus den Zählern der Therme (Differenz Max-Min)."""
        rows = self.store.query(
            "SELECT date(ts, 'unixepoch', 'localtime') AS day, "
            "MAX(burn_starts) - MIN(burn_starts) AS starts, "
            "MAX(heat_starts) - MIN(heat_starts) AS heat_starts, "
            "MAX(heat_min) - MIN(heat_min) AS heat_min "
            "FROM samples WHERE burn_starts IS NOT NULL AND ts >= ? GROUP BY day",
            (int(datetime.fromisoformat(since_day).timestamp()),),
        )
        out = {}
        for r in rows:
            d = {"starts": int(r["starts"]), "source": "zaehler"}
            if r["heat_starts"] is not None:
                d["heat_starts"] = int(r["heat_starts"])
                if r["heat_starts"] and r["heat_min"] is not None:
                    d["avg_run_min"] = round(r["heat_min"] / r["heat_starts"], 1)
            out[r["day"]] = d
        return out

    def history(self, days: int = 30) -> dict:
        since = int(time.time()) - days * 86400
        hourly = [dict(r) for r in self.store.query("SELECT * FROM hourly WHERE ts >= ? ORDER BY ts", (since,))]
        snaps = [
            {"ts": r["ts"], "kind": r["kind"], "key": r["key"], "data": json.loads(r["data"])}
            for r in self.store.query("SELECT * FROM snapshots ORDER BY ts")
        ]
        daily_cycles = []
        today = local(time.time())
        for d in range(min(days, 30)):
            day = today - timedelta(days=d)
            s, e = midnight(day), midnight(day + timedelta(days=1))
            ev = [(r["ts"], r["state"]) for r in self.store.query(
                "SELECT ts, state FROM burner_events WHERE ts >= ? AND ts < ? ORDER BY ts", (s - 86400, e))]
            st = analysis.cycling_stats(ev, s, e)
            daily_cycles.append({"day": day.date().isoformat(), **st})
        first = daily_cycles[-1]["day"] if daily_cycles else local(time.time()).date().isoformat()
        counters = self.counter_days(first)
        live = self.store.daily(first)
        for d in daily_cycles:
            cnt = counters.get(d["day"], {})
            d.update(cnt)
            if "avg_run_min" not in cnt:  # ohne Heizlaufzeit-Zähler: nur Läufe ohne Warmwasser
                day = datetime.fromisoformat(d["day"])
                avg, _ = analysis.heat_runs(self.store, int(day.timestamp()), int((day + timedelta(days=1)).timestamp()))
                d["avg_run_min"] = None if avg is None else round(avg, 1)
            for k, v in live.get(d["day"], {}).items():
                d[k] = round(v, 2)
        return {"hourly": hourly, "snapshots": snaps, "cycles": list(reversed(daily_cycles))}

    # ------------------------------------------------------------------ Vorhersage
    def forecast_points(self) -> list[tuple[datetime, float]]:
        """Stündliche Vorhersage als (Zeitpunkt mit Zone, Außentemperatur), zeitlich sortiert.

        Punkte ohne lesbare Zeit oder Temperatur fallen weg. Zeiten ohne Zone gelten als Ortszeit
        (HA liefert ISO mit Offset; seit 0.36.1 defensiv, vorher brach der Vergleich ab).
        """
        out = []
        for f in self.forecast or []:
            if not isinstance(f, dict):
                continue
            when, temp = parse_time(f.get("datetime")), num(f.get("temperature"))
            if when is not None and temp is not None:
                out.append((when, temp))
        return sorted(out, key=lambda p: p[0])

    def forecast_point_at(self, when: datetime) -> tuple[datetime, float] | None:
        """Der Vorhersagepunkt am nächsten zu ``when`` – nur innerhalb ``FORECAST_MAX_GAP_MIN``, sonst None.

        Für die Anzeige, welcher Zeitpunkt genommen wurde (Vorheizplan: „Vorhersage für 17:00“).
        Ohne Wetter-Entität None – eine alte Vorhersage zählt dann nicht mehr (wie bei der Kachel).
        """
        if not self.found.weather:
            return None
        when = aware(when)
        best = min(self.forecast_points(), key=lambda p: abs((p[0] - when).total_seconds()), default=None)
        if best is None or abs((best[0] - when).total_seconds()) > FORECAST_MAX_GAP_MIN * 60:
            return None
        return best

    def outdoor_forecast_at(self, when: datetime) -> float | None:
        """Außentemperatur laut Vorhersage zum Zeitpunkt ``when`` – None ohne Punkt in der Nähe (seit 0.36.1)."""
        point = self.forecast_point_at(when)
        return point[1] if point else None

    def forecast_summary(self, now_ts: float) -> dict | None:
        """Kachel „Vorhersage“ (seit 0.36.1): Alter, Reichweite, nächste 12 h und Trend.

        None ohne Wetter-Entität oder ohne Punkte. ``trend_k`` = Temperatur in 12 h − jetzt (je nächster
        Punkt, auf 0,5 K gerundet); fehlt einer der beiden Punkte, None.
        """
        if not self.found.weather:
            return None
        points = self.forecast_points()
        if not points:
            return None
        now = local(now_ts)
        ahead = now + timedelta(hours=FORECAST_AHEAD_H)
        # der Punkt der laufenden Stunde zählt mit (HA beginnt meist zur vollen Stunde)
        window = [p for p in points if now - timedelta(minutes=30) <= p[0] <= ahead]
        next12 = None
        if window:
            low = min(window, key=lambda p: p[1])
            next12 = {"min": low[1], "min_at": low[0].astimezone().isoformat(), "max": max(p[1] for p in window)}
        cur, later = self.forecast_point_at(now), self.forecast_point_at(ahead)
        return {
            "age_min": None if self.forecast_ts is None else max(0, round((now_ts - self.forecast_ts) / 60)),
            "points": len(points),
            "hours": round(max((points[-1][0] - now).total_seconds() / 3600, 0.0), 1),
            "next12": next12,
            "trend_k": (round((later[1] - cur[1]) * 2) / 2 or 0.0) if cur and later else None,  # ohne −0,0
        }
