"""Herzstück: Zustände sammeln, Modelle lernen, Auswertungen berechnen.

Beobachten ist der Kern: Das Add-on liest, lernt und veröffentlicht eigene ``sensor.heatpilot_*``-Sensoren und
eine Weboberfläche. Geschrieben wird nur über ``control.Control`` – das Raum-Soll der Thermostate (Steuerung, 0.34)
und, nur mit eigenem Schalter, das Warmwasser-Soll des Kessels (Weg W, 0.38, ``dhw_control``).

Diese Datei ist der Kern: ``Engine`` baut die Modelle auf, nimmt Zustände an, treibt den Takt und
speichert. Die Arbeit steckt nach Thema in Mixins – Messpunkt (``engine_sample``), Kessel live
(``engine_boiler``), Warmwasser (``engine_dhw``), Lernen (``engine_learn``) und Auswertung
(``engine_summary``); die Wertehelfer stehen in ``values``. Seit 0.37 merkt sich der Takt stündlich den Plan der
Vorschau (``preview_log``, Trockenlauf – nur Anzeige). Seit 0.38 prüft der Takt nach ``dhw_step`` das Warmwasser-Soll
(``dhw_control.plan`` – merkt einen Schreibvorgang nur vor, geschrieben wird danach in ``main``). Seit 0.39 lernt der
Takt das Ventilverhalten je Raum (``valve_model``/``valve_learn``) und protokolliert im Trockenlauf, was HeatPilot an
den Thermostaten verstellen würde (``valve_dry`` – schreibt nichts). Seit 0.40 führt die Regelschleife mit eigenem
Schalter die Ventile wirklich (``valve_control``); diese Räume lässt der Trockenlauf aus. Seit 0.41 führt
``boiler_control`` (Weg B, eigener Schalter) Vorlauf-Soll und Heizbetrieb des Kessels – im 30-s-Takt von ``main``,
nicht im Messtakt. Seit 0.42 löst ``disinfect_control`` (mit Weg W „HeatPilot setzt“, ohne Regler am Bus) die
Desinfektion zum Termin aus – ebenfalls im 30-s-Takt.
"""

from __future__ import annotations

import logging
import time

from . import keep_data, live_log, plausibility, preview_log, regulator, schedule, sensor_gone, sleep, valve_dry, valve_learn
from .config import Config
from .dhw import DhwModel
from .dhw_watch import DhwShadow
from .discovery import BOILER_ROLES, THERMOSTAT_ROLES, Discovered
from .engine_boiler import BoilerMixin
from .engine_dhw import DhwMixin
from .engine_learn import LearnMixin
from .engine_sample import SampleMixin
from .engine_summary import SummaryMixin
from .live import LiveBuffer
from .models import ROOM_STEP_H, HouseModel, RoomModel
from .presence import PersonLearner, household_long_away
from .store import Store
from .vacation import Vacation
from .valve_model import ValveModel
from .values import local

# Durchgereicht sind genau die Namen, die Module und Tests aus heatpilot.engine holen: die Wertehelfer
# und einzelne Konstanten der Mixins. Alle übrigen Konstanten stehen beim Thema (engine_*.py) und sind nur
# dort importierbar. analysis, guards und next_arrival ersetzen Tests hier per monkeypatch – engine_summary
# und engine_dhw rufen next_arrival deshalb über dieses Modul auf. Die Uhr dagegen bindet jedes Teilmodul
# selbst (import time): Wer sie für die ganze Engine anhalten will, ersetzt time.time.
from . import analysis, guards  # noqa: F401
from .engine_learn import EXT_DAY_SKIP_H, KV_MANAGED_UNTIL  # noqa: F401
from .engine_sample import PERSON_UNKNOWN  # noqa: F401
from .engine_summary import FORECAST_MAX_GAP_MIN  # noqa: F401
from .presence import next_arrival  # noqa: F401
from .values import ON_STATES, aware, flag, midnight, num, parse_time  # noqa: F401

log = logging.getLogger(__name__)

STALE_S = 12 * 3600


class Engine(SampleMixin, BoilerMixin, DhwMixin, LearnMixin, SummaryMixin):
    """Zustände, Modelle und Takt des Add-ons; die Themen stecken in den Mixins (siehe Moduldocstring)."""

    def __init__(self, cfg: Config, store: Store, found: Discovered) -> None:
        self.cfg = cfg
        self.store = store
        self.found = found
        self.states: dict[str, dict] = {}
        self.ems: dict[str, dict] = {}  # direkt von EMS-ESP gelesen (optional)
        # Zeit der letzten Antwort von EMS-ESP (0.38): Weg W nimmt EMS-ESP-Werte nur frisch (dhw_control.readings)
        self.ems_ts: float | None = None
        # Regler am Bus (Weg B, 0.41): zuletzt gesehen, Folge „kein Gerät thermostat“ – regulator.note/regulator
        self.regulator_watch: dict = regulator.load(store)
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
        # Ventilmodelle (0.39): neu oder anderer Modellstand → lernt im Takt aus der gespeicherten Historie nach.
        # Nicht für den Heizkreis von EMS-ESP – er ist kein Thermostat mit Ventil
        self.valves: dict[str, ValveModel] = {
            r.climate: ValveModel.from_dict(store.get(f"model.valve.{r.climate}"), valve_learn.binary_device(r))
            for r in found.rooms if not r.ems}
        # 0.45.1: beim Aufbau entfernte Sensoren (``sensor_gone``)
        self._dropped = {e for f in getattr(found, "sensors_gone", {}).values() for e in f.values()}
        self.vacation = Vacation(cfg.vacations, cfg.vacation_entity, cfg.vacation_arrival_time)
        self.vacation.returned = set(store.get("vacation_returned", []))
        self.persons: dict[str, PersonLearner] = {}
        for p in found.persons:
            saved = store.get(f"model.person.{p}")
            self.persons[p] = PersonLearner.from_dict(saved) if saved else PersonLearner(p)
        saved = store.get("model.dhw")
        self.dhw = DhwModel.from_dict(saved) if saved else DhwModel()
        if not store.get("dhw.margin_reset_0462"):  # 0.46.2: Reserve stammt aus der alten, zu kalten Strategie
            self.dhw.comfort_margin = 0.0
            if self.dhw._backup is not None:  # auch nicht über „alle lange weg“ (Zapfprofil-Sicherung) zurück
                self.dhw._backup["comfort_margin"] = 0.0
            if saved:  # gleich speichern: sonst käme sie nach einem Neustart vor dem nächsten Speichern zurück
                store.put("model.dhw", self.dhw.to_dict())
            store.put("dhw.margin_reset_0462", True)
        self.dhw_shadow = DhwShadow()
        # Zeiten/Temperaturen: Einstellungen sind Startwerte, gelernt wird aus Tado
        self.schedule_learned: dict | None = store.get("schedule.learned")
        # Schlafenszeit aus dem Stromverbrauch (0.36): gespeicherte Nächte → Wochenplan, damit die
        # Steuerung vom ersten Takt an die richtigen Zeiten hat; wirkt nur mit night_plan_auto
        self._sleep_ts = 0.0
        self.sleep_nights: dict = (store.get(sleep.KV_NIGHTS) or {}) if (sleepy := found.power or found.activity) else {}
        self._sleep = sleep.summary(self.sleep_nights, int(time.time()), cfg, found.power, found.power_exclude, found.activity)
        cfg.night_plan_learned = sleep.learned_plan(self._sleep["plan"]) if sleepy else None
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
        self.dhw_control = None   # dhw_control.DhwControl, von main gesetzt (Weg W seit 0.38)
        self.notifier = None      # notify.Notifier, von main gesetzt (Meldungen, Problem-Sensor seit 0.38)
        self.boiler_control = None  # boiler_control.BoilerControl, von main gesetzt (Weg B seit 0.41)
        self.valve_pid = None  # valve_pid_control.ValvePidControl, von main gesetzt (ventilgeführt seit 0.51)
        self.disinfect_control = None  # disinfect_control.DisinfectControl, von main gesetzt (Desinfektion seit 0.42)
        self.watchdog: dict = {}    # Wächter-Automationen in HA (watchdog.scan, main alle 10 min) – nur gelesen
        self.heartbeat_ts: float | None = None  # zuletzt gesetzter sensor.heatpilot_heartbeat (0.41)
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
        if self._last_room_step is not None and (fresh_rooms := fresh_rooms | self.migrate_rooms()):  # 0.53
            self.relearn_rooms(fresh_rooms)

    # ------------------------------------------------------------------ Zustände
    def on_state(self, entity_id: str, state: dict | None) -> None:
        if state is None:
            self.states.pop(entity_id, None)
            self.__dict__.setdefault("_gone", set()).add(entity_id)  # 0.45.1: entfernt – ein Sensor zählt nicht
            return
        self.__dict__.get("_gone", set()).discard(entity_id)
        dropped = self.__dict__.get("_dropped")
        if dropped and entity_id in dropped:  # 0.45.1: beim Aufbau als gelöscht entfernt, meldet sich doch
            dropped.discard(entity_id)
            sensor_gone.back(self.found, entity_id)
        self.states[entity_id] = state
        if entity_id in self._live_entities:
            self.update_live()

    def set_ems(self, values: dict[str, dict], status: dict[str, str] | None = None) -> None:
        """Neue Werte von EMS-ESP (main.poll_emsesp, nur bei einer Antwort) – mit Zeitstempel ``ems_ts``. ``status``
        (seit 0.41, ``EmsEsp.status``): wie die Abfrage je Gerät ausging – für „Regler am Bus“ (``regulator.note``)."""
        self.ems = values
        self.ems_ts = time.time()
        regulator.note(self, status, self.ems_ts)
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

    # ------------------------------------------------------------------ Urlaub
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
        self.store.add_valve_subst(ts, sorted(self.__dict__.get("valve_subst", ())))  # Ventil-Ersatzwerte (0.39)
        self.store.add_person_samples((ts, *p) for p in persons)
        self.dhw_step(ts, sample)
        if self.dhw_control is not None:  # Weg W (0.38): Warmwasser-Soll prüfen, ggf. vormerken – schreibt nichts
            try:
                self.dhw_control.plan(ts, sample, now=now_ts)
            except Exception:  # noqa: BLE001 – Lernen und Raumsteuerung laufen weiter, es wird nichts geschrieben
                self.dhw_control.pending = None
                log.exception("Warmwasser-Soll: Takt fehlgeschlagen")
        self.update_sleep(now_ts)  # Nachtzeiten aus dem Strom (0.36) – vor dem Wunschzustand, stündlich
        for m in self.rooms.values():  # 0.54: Auskühlen mit dem Wind von jetzt (Steuerung, Vorschau, Kessel)
            m.wind_now = sample.get("wind") or 0.0
        self.wind_now = sample.get("wind")  # 0.55: für Übersicht → Jetzt
        if self.control_loop is not None:  # Steuerung (0.34): Wunschzustände, Schreibvorgänge vormerken
            self.control_loop.plan(ts, sample, rooms)
        self.store.commit()
        keep_data.daily(self.store, self.cfg.keep_days, now_ts)  # 0.44: Aufbewahrung, einmal am Tag

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
        try:  # Trockenlauf (0.37): Plan der begonnenen Stunde merken, Ist-Werte nachtragen – schreibt nichts an den Kessel
            preview_log.record(self, now_ts)
        except Exception:  # noqa: BLE001 – das Protokoll darf Lernen und Steuerung nie aufhalten
            log.exception("Trockenlauf-Protokoll fehlgeschlagen")
        self.valve_step(ts, sample, rooms)  # Ventile (0.39): lernen und Trockenlauf – schreibt nichts
        self.save()

    def valve_step(self, ts: int, sample: dict, rooms: list[tuple]) -> None:
        """Ventile (0.39): Modelle bis zum letzten Raumschritt lernen (die Historie in Abschnitten, ``learn_valves``),
        dann den Trockenlauf protokollieren (``valve_dry``). Schreibt nichts an Thermostate oder Kessel – das tut nur
        die Regelschleife mit „Ventile führen“ (0.40, ``valve_control``); ein Fehler hält Lernen und Steuerung nicht
        auf."""
        try:
            self.learn_valves()
        except Exception:  # noqa: BLE001
            log.exception("Ventilmodelle: Lernen fehlgeschlagen")
        try:  # Räume, die HeatPilot gerade wirklich führt (Ventile führen, 0.40), rechnet der Trockenlauf nicht mit
            loop = self.control_loop
            valve_dry.step(self, ts, sample, rooms, loop.guided_rooms() if loop is not None else ())
        except Exception:  # noqa: BLE001
            log.exception("Ventil-Trockenlauf fehlgeschlagen")

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
        ts = now_ts or int(time.time())
        self.live.add(ts, sample, rooms)
        live_log.record(self.store, ts, sample, rooms, self.persons)  # 0.49.2: 48 h als CSV; Personen seit 0.68

    # ------------------------------------------------------------------ Speichern
    def save(self) -> None:
        self.store.put("model.dhw", self.dhw.to_dict())
        self.store.put("model.house", self.house.to_dict())
        busy = self.__dict__.get("_room_busy") or set()  # lernt gerade neu (0.42.1): kein halbes Modell speichern
        for climate, m in self.rooms.items():
            if climate not in busy:
                self.store.put(f"model.room.{climate}", m.to_dict())
        for climate, v in self.valves.items():
            self.store.put(f"model.valve.{climate}", v.to_dict())
        for p, learner in self.persons.items():
            self.store.put(f"model.person.{p}", learner.to_dict())
        self.store.put("model.room_ext", self.room_ext)
        self.store.put("last_room_step", self._last_room_step)
        self.store.put("last_hour", self._last_hour)
