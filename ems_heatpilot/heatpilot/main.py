"""Einstiegspunkt des Add-ons.

Seit 0.41 läuft im 30-s-Takt der Live-Werte auch Weg B (``boiler_step``: Kessel über HeatPilot rechnen, prüfen,
schreiben), der Heartbeat ``sensor.heatpilot_heartbeat`` (jede Minute) und das Lesen der Wächter-Automationen
(alle 10 min, nur lesen). Beim Zurückschalten von „heatpilot“ setzt die alte Schreibstelle die Antipendelzeit
zurück (``App._boiler_back``). Seit 0.42 läuft im selben Takt das Auslösen der Desinfektion zum Termin
(``disinfect_step``: nur mit Warmwasser-Soll „HeatPilot setzt“, ohne Regler am Bus). Seit 0.42.1 übernimmt
``App.adopt_room`` auf Rückfrage Gelerntes von alten Raum-Entitäten (``room_adopt``) – ohne Neueinlesen der Historie –
und merkt sich deren alten Temperatursensor (``room_alias``). ``App.restore_house`` stellt Hausdaten wieder her
(Langzeitstatistik mit den Aliassen, nur zusammenführend), ``App.relearn`` lernt aus den gespeicherten Daten neu
(``house_restore``). Ein Raumwechsel in den Einstellungen holt nur die Historie der neuen Räume nach und lernt dann
aus den gespeicherten Daten neu (``App.update_rooms``) – kein Neueinlesen von allem mehr. Diese Abläufe stehen in
``app_jobs``; ``App.build(relearn=True)`` baut dafür die neue Engine. Bricht ein Neustart das Neulernen ab, setzt
``App.build`` es beim Start fort; „Historie neu einlesen“ behält Warmwasser- und Personenmodelle (``_learning_plan``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from aiohttp import web

from . import __version__
from . import boiler_period, disinfect_entity, disinfect_rule, floor_limit, house_restore, watchdog
from . import card_bridge, room_ids, room_switch  # 0.44: feste Raumkennung, Thermostatwechsel; 0.56: Karten
from . import live_log  # 0.50: Entscheidung von Weg B ins 30-s-Protokoll
from . import room_heating, sensor_gone, valve_learn  # 0.45: Heizleistung vom alten Thermostat; 0.45.1
from .app_jobs import JobsMixin
from .backfill import run_backfill
from . import room_lead
from .boiler_control import BoilerControl
from .valve_pid_control import ValvePidControl
from .boiler_entity import flow_target, heat_target, regulator_block, same_entity
from .control import Control
from .controlloop import ControlLoop
from .config import DATA_DIR, SETTINGS_NAME, Config, backup, restore_settings, save_settings, validate
from .dhw_control import DhwControl, target_entity
from .disinfect_control import DisinfectControl
from .diagnostics import set_debug, setup_logging
from .discovery import Registry, discover
from .emsesp import EmsEsp
from .engine import Engine
from .ha_client import HAClient
from .live import STEP_S as LIVE_STEP_S
from .notify import Notifier
from .publisher import publish, publish_problem
from .store import Store
from .web import make_app

log = logging.getLogger("heatpilot")
CONTROLS_EVERY_S = 3600  # Kesselsteuerungen (0.36.1) beim ersten Kontakt und danach stündlich prüfen

DATA_VERSION = 13  # 3: Warmwasser-Auskühlen · 4: große Zapfungen · 5: Ladeerkennung · 6 (0.20): Sonne
#                   7 (0.26): Hausmodell mit Fremdwärme, Auskühlzeit, mittiger Ableitung, Heizperiode
#                   8 (0.27): Raummodelle – Pumpe liefert Wärme, gerichtetes Vergessen, Sonne ≥ 0
#                   9 (0.28): Warmwasser – Nennleistung aus Einstellungen, Sonderladungen, Trefferquote
#                  10 (0.30): Raummodelle – Startunsicherheit je Parameter, langsameres Vergessen,
#                             Ausreißer-Schwelle mit Vorhersage-Unsicherheit
#                  11 (0.31): Hausmodell – Auskühlen in zwei Phasen, Warmwasser aus Altdaten herausgerechnet
#                  12 (0.32): Warmwasser – Ladung mit Nachlauf, Zapfungen über Zeitfenster, Reserve je Ereignis,
#                             mittlere Brennerleistung, Ersparnis über denselben Zeitraum;
#                             Anwesenheit – lange Abwesenheit nicht als Gewohnheit, Lernschritte je Feld,
#                             Heimfahrt-Tempo nur aus durchgehender Annäherung;
#                             Hausmodell aus Tageswerten, Auskühlen mit Übertemperatur aus der Bilanz,
#                             schnelle Phase je Anlage, Raum-Startwerte aus dem Haus; Zeitplan-Empfehlungen
#                  13 (0.33): Fremdwärme-Ereignisse je Raum (Kamin, Ofen) – nicht gelernt, Stundenwerte ext_frac
#                  0.39 bleibt bei 13: Die Ventilmodelle sind neu und haben einen eigenen Stand (valve_model.VERSION).
#                  Fehlt ein Modell oder passt sein Stand nicht, lernt es im Takt aus den gespeicherten Messwerten
#                  (room_samples, bis 60 Tage) in Abschnitten nach – Raum-, Haus- und alle übrigen Modelle bleiben,
#                  wie sie sind, und es wird nichts aus Home Assistant neu geholt. „Lernstand neu berechnen“ verwirft
#                  auch sie.


class App(JobsMixin):
    """Hält Verbindung, Datenbank und die laufende Engine – Einstellungen ohne Neustart übernehmen. Übernehmen,
    Wiederherstellen und Neulernen (0.42.1) stehen in ``app_jobs.JobsMixin``."""

    def __init__(self) -> None:
        self._status = "verbinde mit Home Assistant"
        self.cfg = Config.load()
        self.ha = HAClient()
        self.store = Store(DATA_DIR / "heatpilot.db")
        self.eng: Engine | None = None
        self.control: Control | None = None
        self.pending: dict[str, dict | None] = {}
        self.tasks: set = set()  # 0.56: laufende Befehle der Karten (card_bridge.handle)
        self._ems_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._registry: tuple[float, Registry] | None = None
        self._reset = False
        self.job = None  # 0.42.1: laufendes bzw. letztes Wiederherstellen/Neulernen (app_jobs)
        # neuer Datenstand (Ausreißer-Schutz 0.14, Warmwasser 0.17.1): älteren Lernstand einmal neu berechnen
        if self.store.get("data_version", 0) < DATA_VERSION:
            self._reset = bool(self.store.get("backfill_done"))
            self.store.put("data_version", DATA_VERSION)

    @property
    def status(self) -> str:
        if self.eng is None and self.ha.last_error and not self.ha.connected.is_set():
            return f"keine Verbindung zu Home Assistant – {self.ha.last_error}"
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        self._status = value

    @property
    def configured(self) -> bool:
        return (DATA_DIR / SETTINGS_NAME).exists()

    def on_state(self, entity_id: str, state: dict | None) -> None:
        if self.eng is not None:
            self.eng.on_state(entity_id, state)
            if entity_id == card_bridge.COMMAND and card_bridge.parse((state or {}).get("state")):  # 0.56: Karte
                task = asyncio.get_running_loop().create_task(card_bridge.handle(self, state["state"]))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        else:
            self.pending[entity_id] = state

    async def registry(self) -> Registry:
        """Entitäten, Geräte und Bereiche von HA (Cache 60 s). Fehlen Geräte/Bereiche (alte HA, Fehler):
        leer – HeatPilot arbeitet dann wie bisher nur mit der Entitätenregistry."""
        if self._registry is None or time.time() - self._registry[0] > 60:
            entities = await self.ha.entity_registry()
            parts = []
            for name, call in (("Geräte", self.ha.device_registry), ("Bereiche", self.ha.area_registry)):
                try:
                    parts.append(await call() or [])
                except Exception as err:  # noqa: BLE001 – ohne Registry weiterarbeiten
                    log.warning("%s-Registry nicht lesbar (%s) – Räume ohne Bereich/Gerät", name, err)
                    parts.append([])
            self._registry = (time.time(), Registry(entities, *parts))
        return self._registry[1]

    async def build(self, backfill: bool = True, relearn: bool = False) -> None:
        """(Neu) aufbauen: Konfiguration laden, Entitäten finden, Engine starten. ``relearn`` (0.42.1): die neue Engine
        lernt aus den gespeicherten Daten neu (``house_restore``), ohne etwas aus Home Assistant neu zu holen."""
        async with self._lock:
            await self._build(backfill, relearn)

    async def _build(self, backfill: bool, relearn: bool = False) -> None:
        """``build`` unter der schon gehaltenen Sperre."""
        self.cfg = Config.load()
        states = await self.ha.get_states()
        reg = await self.registry()
        found = discover(states, reg, self.cfg)
        renames = room_switch.prepare(self, found, reg)  # 0.44: feste Kennung, Wechsel erkennen
        # 0.45.1: gelöschte Sensoren zählen nicht; 0.45: Räume ohne Ventilsensor nutzen die Heizleistung ihres alten
        # Thermostats, solange es sie gibt (Einstellungen bleiben)
        idx, by_id = Registry.of(reg).lookup(), {s["entity_id"]: s for s in states}
        sensor_gone.drop(found, by_id, idx)
        room_heating.attach(self.store, found, idx, by_id)
        old = self.eng
        backfill, relearn, since, kept = self._learning_plan(found, backfill, relearn, old)
        eng = Engine(self.cfg, self.store, found)
        if backfill:
            self.status = "übernehme Historie"
            await run_backfill(self.ha, eng)
            house_restore.put_back(eng, kept)
        elif relearn:
            self.status = "lerne aus den gespeicherten Daten neu"
            await house_restore.relearn(eng, since)
        for s in await self.ha.get_states():
            eng.on_state(s["entity_id"], s)
        for entity_id, s in self.pending.items():
            eng.on_state(entity_id, s)
        self.pending.clear()
        if old is not None:
            if old.found.weather == eng.found.weather:  # Vorhersage nur von derselben Wetter-Entität
                eng.forecast, eng.forecast_ts = old.forecast, old.forecast_ts
            eng.live = old.live
            if old.cfg.emsesp_host == eng.cfg.emsesp_host:  # Kesselsteuerungen bis zur nächsten Abfrage
                eng.ems_controls, eng.ems_controls_ts = old.ems_controls, old.ems_controls_ts
                eng.ems, eng.ems_ts = old.ems, old.ems_ts  # letzte Werte mit ihrer Zeit (Weg B: Regler am Bus)
                eng.regulator_watch = old.regulator_watch  # Regler zuletzt gesehen, Folge „kein Gerät“ (0.41)
            await self._release_valves(old, found)
            await self._boiler_back(old)
        self.eng = eng
        await room_switch.follow(eng, renames)  # HA hat nur umbenannt: Daten ziehen ohne Rückfrage um
        await room_switch.resume(eng)  # Neulernen nach einem Wechsel, das ein Neustart unterbrach
        self._build_control(eng, found, old)
        self._restart_emsesp()
        self.status = "läuft"

    def _learning_plan(self, found, backfill: bool, relearn: bool, old: Engine | None) -> tuple:
        """Was die neue Engine lernt: alles neu aus den gespeicherten und den HA-Daten (neuer Datenstand bzw. „Historie
        neu einlesen“ – dann bleiben Warmwasser- und Personenmodelle, 0.42.1), aus den gespeicherten Daten neu
        (``relearn`` oder ein durch einen Neustart unterbrochenes Neulernen, ``house_restore.pending``) oder weiter wie
        bisher. Gibt (backfill, relearn, since, behaltene Modelle) zurück."""
        if self._reset:
            kept = house_restore.keep_models(self.store) if self._reset == "keep" else {}
            since_day = (datetime.now() - timedelta(days=self.cfg.backfill_days + 1)).date().isoformat()
            self.store.reset_learning(since_day)
            self.store.put(house_restore.PENDING, None)  # der Neuaufbau lernt ohnehin alles neu
            self._reset = False
            log.info("Lernstand wird aus den gespeicherten und den HA-Daten neu berechnet")
            return True, False, None, kept
        if relearn or house_restore.pending(self.store):
            if not relearn:
                log.info("Neu lernen wurde unterbrochen (Neustart) – HeatPilot lernt jetzt aus den gespeicherten "
                         "Daten neu")
            # 0.45.1: ein %-Ventilmodell ohne %-Werte (ruht) bleibt – es ließe sich nicht neu lernen
            return False, True, house_restore.prepare(self.store, [r.climate for r in found.rooms],
                                                      valve_learn.resting(self.store, found.rooms)), {}
        if old is not None:
            old.save()  # Lernstand sichern, die neue Engine lädt ihn
        return backfill, False, None, {}

    async def _release_valves(self, old: Engine, found) -> None:
        """Ventile führen (0.40): Führt die neue Schleife einen Raum nicht mehr („Beobachten“, „macht mit“ aus, Raum
        entfernt), nimmt die alte ihren laufenden Schritt vorher einmal zurück aufs Wunschsoll – noch unter den alten
        Einstellungen, unter denen er geschrieben wurde (``ControlLoop.release``). Ein Fehler hält den Aufbau nicht
        auf."""
        loop = old.control_loop
        if loop is None:
            return
        managing = self.cfg.is_managing()
        keep = {r.climate for r in found.rooms if r.managed} if managing else set()
        try:
            await loop.release(keep, "macht nicht mehr mit" if managing else "Steuerung „Beobachten“")
        except Exception:  # noqa: BLE001
            log.exception("Ventile führen: Zurückschreiben vor dem Neuaufbau fehlgeschlagen")

    async def _boiler_back(self, old: Engine) -> None:
        """Weg B (0.41): Antipendelzeit nicht mehr geführt – einmal den Grundwert zurück, noch über die alte
        Schreibstelle (0.48, nie ein Fehler). Einen Rückfall-Vorlauf schreibt HeatPilot seit 0.59 nicht mehr."""
        ctl = old.boiler_control
        if ctl is not None:
            await ctl.period.back(self.cfg)

    def _build_control(self, eng: Engine, found, old: Engine | None) -> None:
        """Schreibstellen neu aufbauen: Control (Gruppen, HA-Zustände; seit 0.38 die eine Entität des
        Warmwasser-Solls – nur mit Schalter „heatpilot“ – und die Benachrichtigungsziele), Regelschleife, Weg W, Weg B,
        Desinfektion (0.42: der Schalter nur mit Termin und „HeatPilot setzt“) und Meldungen. Stand von Weg W, Weg B,
        Desinfektion und Meldungen geht von der alten Engine über; die alten Schleifen schreiben nichts mehr."""
        cfg = self.cfg
        entity = target_entity(cfg, found, eng.states)
        boiler = {"flow": flow_target(cfg, found, eng.states), "heating": heat_target(cfg, found, eng.states),
                  "period": boiler_period.target(cfg, found, eng.states)}  # 0.48: Antipendelzeit
        writes = {"flow": boiler["flow"][0], "heating": boiler["heating"][0], "max": cfg.max_flow_temp,
                  "period": boiler["period"][0] if cfg.boiler_period else None,
                  "floors": floor_limit.floor_rooms(cfg, found.rooms),
                  "pump": cfg.boiler_idle == "pumpe"} if cfg.writes_boiler() else None
        dis = disinfect_entity.target(cfg, found, eng.states)
        self.control = Control(self.ha, self.store, found.rooms, states=eng.states,
                               dhw_entity=entity[0] if cfg.writes_dhw() else None,
                               dhw_limits=(cfg.dhw_min_temp, cfg.dhw_max_temp), notify_targets=cfg.notify_targets,
                               boiler=writes, boiler_lock=lambda: regulator_block(eng),
                               disinfect=dis[0] if disinfect_rule.writes(cfg) else None,
                               disinfect_lock=lambda: disinfect_rule.lock(eng),
                               valves=room_lead.valve_rooms(found.rooms))  # 0.51: ventilgeführte Räume
        eng.notifier = Notifier(self.store, self.control, cfg.notify_targets,
                                carry=old.notifier if old is not None else None)
        eng.control_loop = ControlLoop(eng, self.control)
        eng.dhw_control = DhwControl(eng, self.control, eng.notifier, entity,
                                     carry=old.dhw_control if old is not None else None)
        eng.boiler_control = BoilerControl(eng, self.control, eng.notifier, boiler,
                                           carry=old.boiler_control if old is not None else None)
        eng.disinfect_control = DisinfectControl(eng, self.control, eng.notifier, dis,
                                                 carry=getattr(old, "disinfect_control", None))
        eng.valve_pid = ValvePidControl(eng, self.control, eng.notifier,
                                        carry=getattr(old, "valve_pid", None))  # 0.51: ventilgeführte Räume
        # zählt der Termin im Warmwasser-Modell (HeatPilot löst ihn aus, der letzte ist nicht eben gescheitert)? Gleich,
        # nicht erst im nächsten 30-s-Schritt
        cfg.disinfect_triggered = eng.disinfect_control.triggered(time.time())
        if old is not None:
            eng.watchdog, eng.heartbeat_ts = old.watchdog, old.heartbeat_ts
        if old is not None and old.control_loop is not None:
            old.control_loop.stop()  # ein noch laufendes apply() der alten Schleife schreibt nichts mehr
        if old is not None and old.dhw_control is not None:
            old.dhw_control.stop()
        if old is not None and old.boiler_control is not None:
            old.boiler_control.stop()
        if old is not None and getattr(old, "valve_pid", None) is not None:
            old.valve_pid.stop()
        if old is not None and getattr(old, "disinfect_control", None) is not None:
            old.disinfect_control.stop()

    def _restart_emsesp(self) -> None:
        if self._ems_task:
            self._ems_task.cancel()
            self._ems_task = None
        if self.cfg.emsesp_host:
            self._ems_task = asyncio.create_task(poll_emsesp(EmsEsp(self.cfg.emsesp_host), self))

    async def apply_settings(self, raw: dict) -> list[str]:
        clean, errors = validate(raw)
        if not errors and self.eng is not None and {"flow_set_entity", "dhw_set_entity"} & set(clean):
            cfg = Config.from_raw({**self.cfg.to_raw(), **clean})  # Weg B (0.41): auch gegen die erkannten Entitäten
            errors = [p for p in (same_entity(cfg, self.eng.found, self.eng.states),) if p]
        if errors:
            return errors
        if "rooms" in clean:  # 0.44: feste Raumkennung, bisheriges Schlüsselgerät bleibt vorn
            clean["rooms"] = room_ids.assign(clean["rooms"], self.eng.found.rooms if self.eng else None)
        save_settings(clean)
        log.info("Einstellungen gespeichert: %s", sorted(clean))
        before = list(self.eng.found.persons) if self.eng else None
        rooms_before, power_before = self._room_inputs(), self._power_inputs()
        await self.build(backfill=False)
        rooms_after = self._room_inputs()
        changed = sorted(c for c, v in rooms_after.items() if rooms_before is not None and rooms_before.get(c) != v)
        reload = False
        if power_before is not None and power_before != self._power_inputs():
            # Leistungssensoren (0.36) geändert: Hausleistung der Vergangenheit neu holen, Nächte neu rechnen
            log.info("Leistungssensoren geändert – Lücken werden aus Home Assistant gefüllt")
            # Werte der alten Sensoren nicht als Nächte zählen – gelöscht nur, was HA neu füllt, ältere bleiben
            self.store.clear_power(int(time.time()) - self.cfg.backfill_days * 86400)
            reload = True
        if reload:
            asyncio.create_task(self.reload_history())
        elif changed:
            # Sensoren eines Raums geändert oder Raum neu: seit 0.42.1 nur die Historie dieser Räume nachholen (nichts
            # überschreiben, keine Langzeitstatistik) und aus den gespeicherten Daten neu lernen
            log.info("Räume mit neuen Sensoren/neu gewählt: %s – ihre Historie wird nachgeholt", ", ".join(changed))
            self.start_job("rooms", set(changed))
        if before is not None and sorted(before) != sorted(self.eng.found.persons):
            self.store.recount_home(self.eng.found.persons)
            log.info("Personen geändert (%s) – Anwesenheit in den gespeicherten Daten neu gezählt",
                     ", ".join(self.eng.found.persons) or "keine")
        set_debug(self.cfg.debug_logging)
        return []

    def _room_inputs(self) -> dict[str, tuple] | None:
        if self.eng is None:
            return None
        return {r.climate: (r.heating_power, r.temp_sensor, tuple(sorted(d["climate"] for d in r.members)))
                for r in self.eng.found.rooms}

    def _power_inputs(self) -> tuple[tuple, tuple] | None:
        if self.eng is None:
            return None
        return tuple(self.eng.found.power), tuple(self.eng.found.power_exclude)

    async def reload_history(self) -> None:
        """Lücken aus Home Assistant füllen (bis 0.43 „Historie neu einlesen“) und alles neu lernen (z. B. nach Wahl
        eines anderen Gaszählers) – seit 0.44 gewinnen eigene Werte, HA ergänzt nur (``backfill``); Warmwasser- und
        Personenmodelle bleiben (``house_restore.keep_models``)."""
        self._reset = "keep"
        await self.build(backfill=True)

    async def refresh_rooms(self) -> None:
        """Nach einer Bedienung: Thermostate sofort neu lesen (nicht erst beim nächsten Ereignis)."""
        if self.eng is None:
            return
        rooms = set(self.eng.found.climate_ids())
        for s in await self.ha.get_states():
            if s["entity_id"] in rooms:
                self.eng.on_state(s["entity_id"], s)

    async def suggestions(self) -> dict:
        """Was die automatische Erkennung ohne eigene Vorgaben finden würde."""
        found = discover(await self.ha.get_states(), await self.registry(), Config.from_raw({}), all_rooms=True)
        return found.as_dict(private=False)


async def main() -> None:
    restored = restore_settings()
    log_file = setup_logging(Config.load().debug_logging)
    app = App()
    log.info("EMS HeatPilot %s startet (Modus: %s, Log: %s)", __version__, app.cfg.mode, log_file)
    if restored:
        log.warning("Einstellungen fehlten in /data – aus der Sicherung im Add-on-Konfigurationsordner zurückgeholt")
    elif (DATA_DIR / SETTINGS_NAME).exists():
        backup((DATA_DIR / SETTINGS_NAME).read_text())  # Sicherung auch für ältere Installationen anlegen

    runner = web.AppRunner(make_app(app))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8099).start()

    try:
        await run(app)
    finally:
        await app.ha.close()
        await runner.cleanup()


async def live_loop(app: App) -> None:
    """Alle 30 s die Live-Werte merken (Reiter „Live“), eine fällige Prüfung des Warmwasser-Solls (0.38), Weg B,
    Heartbeat und Wächter (0.41), Desinfektion (0.42) – jedes für sich, ein Fehler hält die anderen nicht auf."""
    while True:
        try:
            if app.eng is not None:
                app.eng.record_live()
        except Exception:  # noqa: BLE001
            log.exception("Live-Werte nicht erfasst")
        for name, step in (("Prüfung des Warmwasser-Solls", check_dhw), ("Kessel über HeatPilot", boiler_step),
                           ("Ventilgeführte Räume", valve_pid_step),
                           ("Desinfektion", disinfect_step), ("Heartbeat", heartbeat),
                           ("Wächter lesen", scan_watchdog), ("Karten-Sensoren", card_bridge.step)):
            try:
                await step(app)
            except Exception:  # noqa: BLE001
                log.exception("%s fehlgeschlagen", name)
        await asyncio.sleep(LIVE_STEP_S)


async def boiler_step(app: App, now: float | None = None) -> int:
    """Weg B (0.41): Kessel über HeatPilot im 30-s-Takt – rechnen, prüfen, vormerken (``plan``), schreiben
    (``apply``), danach Meldungen und Problem-Sensor. Gibt die Zahl der Schreibvorgänge zurück."""
    eng = app.eng
    ctl = getattr(eng, "boiler_control", None) if eng is not None else None
    if ctl is None:
        return 0
    try:
        ctl.plan(now)
    except Exception:  # noqa: BLE001 – Lernen und Raumsteuerung laufen weiter, es wird nichts geschrieben
        ctl.pending = []
        log.exception("Kessel über HeatPilot: Schritt fehlgeschlagen – nichts geschrieben")
    try:
        live_log.note_boiler(eng.store, ctl.view)  # 0.50: Entscheidung in die 30-s-Zeile dieses Takts
    except Exception:  # noqa: BLE001 – nur das Protokoll
        log.exception("Kessel über HeatPilot: Entscheidung nicht ins 30-s-Protokoll geschrieben")
    n = await ctl.apply(now)
    await send_notes(app, eng)
    return n


async def valve_pid_step(app: App, now: float | None = None) -> int:
    """Ventilgeführte Räume (0.51) im 30-s-Takt: PID rechnen, vormerken (``plan``), schreiben (``apply``)."""
    eng = app.eng
    ctl = getattr(eng, "valve_pid", None) if eng is not None else None
    if ctl is None or not ctl.rooms():
        return 0
    try:
        ctl.plan(now)
    except Exception:  # noqa: BLE001 – die übrigen Schritte laufen weiter, es wird nichts geschrieben
        ctl.pending = []
        log.exception("Ventilgeführte Räume: Schritt fehlgeschlagen – nichts geschrieben")
    return await ctl.apply(now)


async def disinfect_step(app: App, now: float | None = None) -> bool:
    """Desinfektion auslösen (0.42) im 30-s-Takt: Termin fällig, prüfen, beobachten (``plan``), schalten (``apply``),
    danach Meldungen und Problem-Sensor. True, wenn geschaltet wurde."""
    eng = app.eng
    ctl = getattr(eng, "disinfect_control", None) if eng is not None else None
    if ctl is None:
        return False
    try:
        ctl.plan(now)
    except Exception:  # noqa: BLE001 – Lernen, Raumsteuerung und Kessel laufen weiter, es wird nichts geschaltet
        ctl.pending = None
        log.exception("Desinfektion: Schritt fehlgeschlagen – nichts geschaltet")
    done = await ctl.apply(now)
    await send_notes(app, eng)
    return done


async def heartbeat(app: App, now: float | None = None) -> bool:
    """``sensor.heatpilot_heartbeat`` jede Minute setzen (0.41) – der Wächter in HA sieht daran, dass es läuft."""
    eng, now = app.eng, time.time() if now is None else now
    if eng is None or (eng.heartbeat_ts is not None and now - eng.heartbeat_ts < watchdog.EVERY_S - 1):
        return False
    await watchdog.publish(app.ha, now, eng.cfg.boiler_control)
    eng.heartbeat_ts = now
    return True


async def scan_watchdog(app: App, now: float | None = None) -> bool:
    """Wächter-Automationen lesen (0.41, nur lesen) – alle 10 min, solange „Kessel über HeatPilot“ nicht aus ist."""
    eng, now = app.eng, time.time() if now is None else now
    if eng is None or eng.cfg.boiler_control == "aus":
        return False
    if eng.watchdog and now - eng.watchdog.get("ts", 0) < watchdog.SCAN_EVERY_S:
        return False
    try:  # viele Automationen bzw. HA langsam: höchstens eine Minute, der Live-Takt wartet sonst
        eng.watchdog = await asyncio.wait_for(watchdog.scan(app.ha, eng.states), timeout=watchdog.SCAN_MAX_S)
    except asyncio.TimeoutError:
        eng.watchdog = {"found": [], "error": "Automationen nicht lesbar (Zeitüberlauf)", "checked": 0}
    eng.watchdog["ts"] = int(now)
    return True


async def check_dhw(app: App, now: float | None = None) -> bool:
    """Weg W (0.38): 60 s nach dem Schreiben prüfen, ob der Kessel das Warmwasser-Soll zeigt – im 30-s-Takt, ohne den
    Messtakt aufzuhalten. Danach Meldungen senden und den Problem-Sensor setzen. True, wenn geprüft wurde."""
    eng = app.eng
    if eng is None or eng.dhw_control is None or not eng.dhw_control.verify(time.time() if now is None else now):
        return False
    await send_notes(app, eng)
    return True


async def send_notes(app: App, eng: Engine) -> None:
    """Vorgemerkte Meldungen schicken und den Problem-Sensor setzen (0.38) – Fehler nur loggen."""
    if eng.notifier is None:
        return
    await eng.notifier.flush()
    try:
        await publish_problem(app.ha, eng.notifier.problem_state())
    except Exception as err:  # noqa: BLE001
        log.warning("Problem-Sensor nicht gesetzt: %s", err)


async def run(app: App) -> None:
    await app.ha.start(app.on_state)
    await app.build(backfill=True)
    app._live_task = asyncio.create_task(live_loop(app))

    forecast_of = None  # Wetter-Entität des letzten Abrufs
    while True:
        eng = app.eng
        try:
            # stündlich – und sofort nach einem Wechsel der Wetter-Entität (build() verwirft dann die alte)
            weather = eng.found.weather
            if weather and (weather != forecast_of or eng.forecast_ts is None
                            or time.time() - eng.forecast_ts > 3600):
                eng.forecast = await app.ha.weather_forecast(weather)
                eng.forecast_ts, forecast_of = time.time(), weather  # Stand der Vorhersage (Kachel, seit 0.36.1)
        except Exception as err:  # noqa: BLE001
            log.warning("Wettervorhersage nicht verfügbar: %s", err)
        try:
            started = time.monotonic()
            eng.tick()
            if eng.control_loop is not None:  # Steuerung (0.34): vorgemerkte Schreibvorgänge ausführen
                await eng.control_loop.apply()
            if eng.dhw_control is not None:  # Weg W (0.38): vorgemerktes Warmwasser-Soll schreiben
                await eng.dhw_control.apply()
            await send_notes(app, eng)  # Meldungen (0.38), Problem-Sensor
            summary = eng.summary()
            eng.note_changes(summary)
            await publish(app.ha, summary)
            log.debug("Takt fertig in %.2f s", time.monotonic() - started)
        except Exception:  # noqa: BLE001
            log.exception("Fehler im Takt")
        interval = app.cfg.sample_interval_s
        await asyncio.sleep(interval - time.time() % interval + 1)


async def poll_emsesp(ems: EmsEsp, app: App, interval_s: int = 30, controls_every_s: int = CONTROLS_EVERY_S) -> None:
    """Alle Werte direkt von EMS-ESP lesen (nur lesen, kein Token nötig).

    Seit 0.36.1 außerdem die Kesselsteuerungen (``EmsEsp.controls``, ebenfalls nur GET) für
    „Bereit für die Steuerung?“: sobald EMS-ESP antwortet, danach stündlich, in ``eng.ems_controls``.
    Fehler dabei werden nur geloggt. Seit 0.41 geht mit den Werten der Ausgang je Gerät mit (``EmsEsp.status``) –
    eine gescheiterte Abfrage des Reglers ist „unbekannt“, nicht „kein Regler“ (``regulator``).
    """
    controls_at: float | None = None
    try:
        while True:
            try:
                values = await ems.read()
                if values and app.eng is not None:
                    app.eng.set_ems(values, getattr(ems, "status", None))  # je Gerät: Antwort, fehlt, Fehler
                    app.eng.store.commit()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("EMS-ESP-Abfrage fehlgeschlagen")
            if ems.ok and app.eng is not None and (
                    controls_at is None or time.monotonic() - controls_at >= controls_every_s):
                controls_at = time.monotonic()
                try:
                    controls = await ems.controls()
                    app.eng.ems_controls, app.eng.ems_controls_ts = controls, time.time()
                    found = [c for c in controls.values() if c.get("found")]
                    log.info("EMS-ESP: Kesselsteuerungen %d von %d gefunden, %d schreibbar (nur Anzeige)",
                             len(found), len(controls), sum(1 for c in found if c.get("writeable")))
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 – nur Anzeige: loggen, in einer Stunde erneut
                    log.warning("EMS-ESP-Kesselsteuerungen nicht lesbar: %s", err)
            await asyncio.sleep(interval_s)
    finally:
        await ems.close()


if __name__ == "__main__":
    asyncio.run(main())
