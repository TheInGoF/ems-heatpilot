"""Einstiegspunkt des Add-ons."""

from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import web

from . import __version__
from .backfill import run_backfill
from .config import DATA_DIR, Config
from .discovery import discover
from .engine import Engine
from .ha_client import HAClient
from .publisher import publish
from .store import Store
from .web import make_app

log = logging.getLogger("heatpilot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.load()
    log.info("EMS HeatPilot %s startet (Modus: %s)", __version__, cfg.mode)

    engine_ref: dict = {"status": "verbinde mit Home Assistant"}
    runner = web.AppRunner(make_app(engine_ref))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8099).start()

    pending: dict[str, dict | None] = {}

    def on_state(entity_id, state):
        eng = engine_ref.get("engine")
        if eng is not None:
            eng.on_state(entity_id, state)
        else:
            pending[entity_id] = state

    ha = HAClient()
    await ha.start(on_state)
    states = await ha.get_states()
    registry = await ha.entity_registry()
    found = discover(states, registry, cfg)

    store = Store(DATA_DIR / "heatpilot.db")
    eng = Engine(cfg, store, found)
    engine_ref["status"] = "übernehme Historie"
    await run_backfill(ha, eng)
    for s in await ha.get_states():
        eng.on_state(s["entity_id"], s)
    for entity_id, s in pending.items():
        eng.on_state(entity_id, s)
    engine_ref["engine"] = eng
    engine_ref["status"] = "läuft"

    last_forecast = 0.0
    while True:
        try:
            if found.weather and time.time() - last_forecast > 3600:
                eng.forecast = await ha.weather_forecast(found.weather)
                last_forecast = time.time()
        except Exception as err:  # noqa: BLE001
            log.warning("Wettervorhersage nicht verfügbar: %s", err)
        try:
            eng.tick()
            await publish(ha, eng.summary())
        except Exception:  # noqa: BLE001
            log.exception("Fehler im Takt")
        await asyncio.sleep(cfg.sample_interval_s - time.time() % cfg.sample_interval_s + 1)


if __name__ == "__main__":
    asyncio.run(main())
