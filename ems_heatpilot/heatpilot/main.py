"""Einstiegspunkt des Add-ons."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from aiohttp import web

from . import __version__
from .backfill import run_backfill
from .control import Control
from .controlloop import ControlLoop
from .config import DATA_DIR, SETTINGS_NAME, Config, backup, restore_settings, save_settings, validate
from .dhw_control import DhwControl, target_entity
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


class App:
    """Hält Verbindung, Datenbank und die laufende Engine – Einstellungen ohne Neustart übernehmen."""

    def __init__(self) -> None:
        self._status = "verbinde mit Home Assistant"
        self.cfg = Config.load()
        self.ha = HAClient()
        self.store = Store(DATA_DIR / "heatpilot.db")
        self.eng: Engine | None = None
        self.control: Control | None = None
        self.pending: dict[str, dict | None] = {}
        self._ems_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._registry: tuple[float, Registry] | None = None
        self._reset = False
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

    async def build(self, backfill: bool = True) -> None:
        """(Neu) aufbauen: Konfiguration laden, Entitäten finden, Engine starten."""
        async with self._lock:
            self.cfg = Config.load()
            states = await self.ha.get_states()
            found = discover(states, await self.registry(), self.cfg)
            old = self.eng
            if self._reset:
                since = (datetime.now() - timedelta(days=self.cfg.backfill_days + 1)).date().isoformat()
                self.store.reset_learning(since)
                self._reset = False
                backfill = True
                log.info("Lernstand wird aus den gespeicherten und den HA-Daten neu berechnet")
            elif old is not None:
                old.save()  # Lernstand sichern, die neue Engine lädt ihn
            eng = Engine(self.cfg, self.store, found)
            if backfill:
                self.status = "übernehme Historie"
                await run_backfill(self.ha, eng)
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
            self.eng = eng
            self._build_control(eng, found, old)
            self._restart_emsesp()
            self.status = "läuft"

    def _build_control(self, eng: Engine, found, old: Engine | None) -> None:
        """Schreibstellen neu aufbauen: Control (Gruppen, HA-Zustände; seit 0.38 die eine Entität des
        Warmwasser-Solls – nur mit Schalter „heatpilot“ – und die Benachrichtigungsziele), Regelschleife, Weg W und
        Meldungen. Stand von Weg W und Meldungen geht von der alten Engine über; die alten Schleifen schreiben nichts
        mehr."""
        cfg = self.cfg
        entity = target_entity(cfg, found, eng.states)
        self.control = Control(self.ha, self.store, found.rooms, states=eng.states,
                               dhw_entity=entity[0] if cfg.writes_dhw() else None,
                               dhw_limits=(cfg.dhw_min_temp, cfg.dhw_max_temp), notify_targets=cfg.notify_targets)
        eng.notifier = Notifier(self.store, self.control, cfg.notify_targets,
                                carry=old.notifier if old is not None else None)
        eng.control_loop = ControlLoop(eng, self.control)
        eng.dhw_control = DhwControl(eng, self.control, eng.notifier, entity,
                                     carry=old.dhw_control if old is not None else None)
        if old is not None and old.control_loop is not None:
            old.control_loop.stop()  # ein noch laufendes apply() der alten Schleife schreibt nichts mehr
        if old is not None and old.dhw_control is not None:
            old.dhw_control.stop()

    def _restart_emsesp(self) -> None:
        if self._ems_task:
            self._ems_task.cancel()
            self._ems_task = None
        if self.cfg.emsesp_host:
            self._ems_task = asyncio.create_task(poll_emsesp(EmsEsp(self.cfg.emsesp_host), self))

    async def apply_settings(self, raw: dict) -> list[str]:
        clean, errors = validate(raw)
        if errors:
            return errors
        save_settings(clean)
        log.info("Einstellungen gespeichert: %s", sorted(clean))
        before = list(self.eng.found.persons) if self.eng else None
        rooms_before, power_before = self._room_inputs(), self._power_inputs()
        await self.build(backfill=False)
        rooms_after = self._room_inputs()
        changed = sorted(c for c, v in rooms_after.items() if rooms_before is not None and rooms_before.get(c) != v)
        reload = False
        if changed:
            # Sensoren eines Raums geändert oder Raum neu: dessen Werte gibt es bisher nicht in den
            # gespeicherten Daten – Historie aus Home Assistant neu holen und neu lernen
            log.info("Räume mit neuen Sensoren/neu gewählt: %s – Historie wird neu eingelesen", ", ".join(changed))
            reload = True
        if power_before is not None and power_before != self._power_inputs():
            # Leistungssensoren (0.36) geändert: Hausleistung der Vergangenheit neu holen, Nächte neu rechnen
            log.info("Leistungssensoren geändert – Historie wird neu eingelesen")
            self.store.clear_power()  # Werte der alten Sensoren nicht als Nächte zählen
            reload = True
        if reload:
            asyncio.create_task(self.reload_history())
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
        """Historie neu einlesen und alles neu lernen (z. B. nach Wahl eines anderen Gaszählers)."""
        self._reset = True
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
    """Alle 30 s die Live-Werte merken (Reiter „Live“) und eine fällige Prüfung des Warmwasser-Solls (0.38)."""
    while True:
        try:
            if app.eng is not None:
                app.eng.record_live()
        except Exception:  # noqa: BLE001
            log.exception("Live-Werte nicht erfasst")
        try:
            await check_dhw(app)
        except Exception:  # noqa: BLE001
            log.exception("Prüfung des Warmwasser-Solls fehlgeschlagen")
        await asyncio.sleep(LIVE_STEP_S)


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
    Fehler dabei werden nur geloggt.
    """
    controls_at: float | None = None
    try:
        while True:
            try:
                values = await ems.read()
                if values and app.eng is not None:
                    app.eng.set_ems(values)
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
