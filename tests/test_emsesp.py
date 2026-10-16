"""EMS-ESP: Kessel und Regler immer, weitere Geräte nur, wenn sie antworten (und seltener)."""

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from heatpilot.emsesp import EXTRA_DEVICES, EmsEsp


def test_reads_extra_devices_that_answer():
    calls = []

    async def handler(req):
        dev = req.match_info["dev"]
        calls.append(dev)
        data = {"boiler": {"curflowtemp": 45.1, "dhw": {"curtemp": 51.0}},
                "thermostat": {"hc1": {"seltemp": 21.0}},
                "mixer": {"hc2": {"flowtemp": 33.0}}}.get(dev)
        if data is None:
            return web.json_response({"message": "unknown device"}, status=400)
        return web.json_response(data)

    async def run():
        app = web.Application()
        app.router.add_get("/api/{dev}", handler)
        server = TestServer(app)
        await server.start_server()
        ems = EmsEsp(str(server.make_url("")).rstrip("/"))
        first = await ems.read()
        n_first = len(calls)
        for _ in range(3):
            later = await ems.read()
        await ems.close()
        await server.close()
        return first, later, n_first, ems.extra

    first, later, n_first, extra = asyncio.run(run())
    assert first["boiler"] == {"curflowtemp": 45.1, "dhw_curtemp": 51.0}
    assert first["mixer"] == {"hc2_flowtemp": 33.0} and extra == ["mixer"]
    assert n_first == 2 + len(EXTRA_DEVICES)
    assert "mixer" in later  # zwischen den selteneren Abfragen bleibt der letzte Stand
