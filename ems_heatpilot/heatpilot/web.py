"""Weboberfläche (über Ingress direkt in der HA-Seitenleiste)."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

STATIC = Path(__file__).parent / "static"


def make_app(engine_ref: dict) -> web.Application:
    app = web.Application()

    async def index(_req):
        return web.FileResponse(STATIC / "index.html")

    async def summary(_req):
        eng = engine_ref.get("engine")
        if eng is None:
            return web.json_response({"starting": True, "status": engine_ref.get("status")})
        return web.json_response(eng.summary())

    async def history(req):
        eng = engine_ref.get("engine")
        if eng is None:
            return web.json_response({"starting": True})
        days = int(req.query.get("days", "30"))
        return web.json_response(eng.history(min(max(days, 1), 400)))

    app.router.add_get("/", index)
    app.router.add_get("/api/summary", summary)
    app.router.add_get("/api/history", history)
    return app
