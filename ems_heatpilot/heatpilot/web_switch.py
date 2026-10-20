"""Abfragen zum Thermostatwechsel (seit 0.44, ``room_switch``): ``GET/POST api/rooms/switch`` und
``POST api/rooms/valve_relearn``.

* ``GET api/rooms/switch`` – nur lesen: offene Wechsel, die Wahlen, die Räume mit fester Kennung und „Ventil lernt ab“,
  das Protokoll (``room_switch.overview``).
* ``POST api/rooms/switch {room_id, choice}`` – ``choice``: ``same`` (gleiches Thermostat, alles zieht um), ``other``
  (anderes Thermostat, das Ventil lernt neu) oder ``new`` (nichts zieht um). 400 mit Grund, wenn es nicht geht, 409
  während eines Neuaufbaus, 503 beim Start.
* ``POST api/rooms/valve_relearn {room_id}`` – „Ventil neu lernen“ (die Oberfläche fragt vorher nach).

Schreibt nichts an Home Assistant oder den Kessel.
"""

from __future__ import annotations

from typing import Callable

from . import room_switch
from .discovery import Registry
from .room_adopt import AdoptBusy, AdoptError


def routes(app, json_response: Callable) -> list[tuple[str, str, Callable]]:
    """(Methode, Pfad, Handler) für ``web.make_app``; ``app``: main.App."""

    async def body_of(req) -> dict | None:
        try:
            body = await req.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    async def run(req, call) -> object:
        if app.eng is None:
            return json_response({"ok": False, "error": f"HeatPilot startet noch ({app.status})"}, status=503)
        body = await body_of(req)
        if body is None:
            return json_response({"ok": False, "error": "Ungültige Daten"}, status=400)
        try:
            res = await call(body)
        except AdoptBusy as err:
            return json_response({"ok": False, "error": str(err)}, status=409)
        except AdoptError as err:
            return json_response({"ok": False, "error": str(err)}, status=400)
        return json_response({"ok": True, **res})

    async def get(_req):
        eng = app.eng
        if eng is None:
            return json_response({"starting": True, "status": app.status})
        try:
            idx = Registry.of(await app.registry()).lookup()
        except Exception:  # noqa: BLE001 – ohne Registry: ohne Herkunft
            idx = Registry().lookup()
        return json_response(room_switch.overview(eng, idx))

    async def post(req):
        return await run(req, lambda b: room_switch.resolve(app, str(b.get("room_id") or "").strip(),
                                                            str(b.get("choice") or "").strip()))

    async def valve(req):
        return await run(req, lambda b: room_switch.relearn_valve(app, str(b.get("room_id") or "").strip()))

    return [("GET", "/api/rooms/switch", get), ("POST", "/api/rooms/switch", post),
            ("POST", "/api/rooms/valve_relearn", valve)]
