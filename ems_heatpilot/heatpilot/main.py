"""Einstiegspunkt des Add-ons."""

from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import web

from . import __version__
from .backfill import run_backfill
from .config import DATA_DIR, SETTINGS_NAME, Config, save_settings, validate
from .diagnostics import set_debug, setup_logging
from .discovery import discover
from .emsesp import EmsEsp
from .engine import Engine
from .ha_client import HAClient
from .publisher import publish
from .store import Store
from .web import make_app

log = logging.getLogger("heatpilot")


class App:
    """Hält Verbindung, Datenbank und die laufende Engine – Einstellungen ohne Neustart übernehmen."""

    def __init__(self) -> None:
        self._status = "verbinde mit Home Assistant"
        self.cfg = Config.load()
        self.ha = HAClient()
        self.store = Store(DATA_DIR / "heatpilot.db")
        self.eng: Engine | None = None
        self.pending: dict[str, dict | None] = {}
        self._ems_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._registry: tuple[float, list[dict]] | None = None

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

    async def registry(self) -> list[dict]:
        if self._registry is None or time.time() - self._registry[0] > 60:
            self._registry = (time.time(), await self.ha.entity_registry())
        return self._registry[1]

    async def build(self, backfill: bool = True) -> None:
        """(Neu) aufbauen: Konfiguration laden, Entitäten finden, Engine starten."""
        async with self._lock:
            self.cfg = Config.load()
            states = await self.ha.get_states()
            found = discover(states, await self.registry(), self.cfg)
            old = self.eng
            if old is not None:
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
                eng.forecast = old.forecast
            self.eng = eng
            self._restart_emsesp()
            self.status = "läuft"

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
        await self.build(backfill=False)
        set_debug(self.cfg.debug_logging)
        return []

    async def reload_history(self) -> None:
        """Historie neu einlesen (z. B. nach Wahl eines anderen Gaszählers)."""
        self.store.put("backfill_done", False)
        await self.build(backfill=True)

    async def suggestions(self) -> dict:
        """Was die automatische Erkennung ohne eigene Vorgaben finden würde."""
        found = discover(await self.ha.get_states(), await self.registry(), Config.from_raw({}))
        return found.as_dict(private=False)


async def main() -> None:
    log_file = setup_logging(Config.load().debug_logging)
    app = App()
    log.info("EMS HeatPilot %s startet (Modus: %s, Log: %s)", __version__, app.cfg.mode, log_file)

    runner = web.AppRunner(make_app(app))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8099).start()

    try:
        await run(app)
    finally:
        await app.ha.close()
        await runner.cleanup()


async def run(app: App) -> None:
    await app.ha.start(app.on_state)
    await app.build(backfill=True)

    last_forecast = 0.0
    while True:
        eng = app.eng
        try:
            if eng.found.weather and time.time() - last_forecast > 3600:
                eng.forecast = await app.ha.weather_forecast(eng.found.weather)
                last_forecast = time.time()
        except Exception as err:  # noqa: BLE001
            log.warning("Wettervorhersage nicht verfügbar: %s", err)
        try:
            started = time.monotonic()
            eng.tick()
            summary = eng.summary()
            eng.note_changes(summary)
            await publish(app.ha, summary)
            log.debug("Takt fertig in %.2f s", time.monotonic() - started)
        except Exception:  # noqa: BLE001
            log.exception("Fehler im Takt")
        interval = app.cfg.sample_interval_s
        await asyncio.sleep(interval - time.time() % interval + 1)


async def poll_emsesp(ems: EmsEsp, app: App, interval_s: int = 30) -> None:
    """Alle Werte direkt von EMS-ESP lesen (nur lesen, kein Token nötig)."""
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
            await asyncio.sleep(interval_s)
    finally:
        await ems.close()


if __name__ == "__main__":
    asyncio.run(main())
