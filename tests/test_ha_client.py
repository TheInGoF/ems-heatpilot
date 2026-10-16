"""Anmeldung bei Home Assistant: Schlüssel finden, abgelehnte Anmeldung ohne Absturz."""

import asyncio
import logging

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from heatpilot.ha_client import HAClient, find_token


def make_server(good_token):
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        msg = await ws.receive_json()
        if msg.get("access_token") != good_token:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        async for m in ws:
            if m.type == WSMsgType.TEXT:
                data = m.json()
                await ws.send_json({"id": data["id"], "type": "result", "success": True, "result": None})
        return ws

    app = web.Application()
    app.router.add_get("/websocket", ws_handler)
    return TestServer(app)


async def connect(token, caplog=None):
    server = make_server("richtig")
    await server.start_server()
    ha = HAClient(base_url=str(server.make_url("")).rstrip("/"), token=token)
    task = asyncio.create_task(ha.start(lambda *_: None))
    for _ in range(100):
        if task.done() or ha.last_error:
            break
        await asyncio.sleep(0.02)
    info = ha.info()
    done = task.done()
    task.cancel()
    await ha.close()
    await server.close()
    return info, done


def test_rejected_token_does_not_crash_and_is_reported(caplog):
    caplog.set_level(logging.DEBUG, logger="heatpilot.ha_client")
    info, done = asyncio.run(connect("falsch"))
    assert not done  # start() wartet weiter, kein TimeoutError
    assert not info["connected"]
    assert info["last_error"] == "Anmeldung bei Home Assistant fehlgeschlagen: Invalid access"
    assert info["token_present"] and info["token_length"] == 6
    text = caplog.text
    assert "falsch" not in text  # Schlüssel selbst nie im Log
    assert "Schlüssel vorhanden: ja, 6 Zeichen" in text and "neu starten" in text


def test_good_token_connects():
    info, done = asyncio.run(connect("richtig"))
    assert done and info["connected"] and info["last_error"] == ""


def test_token_from_env_or_s6_file(tmp_path):
    f = tmp_path / "SUPERVISOR_TOKEN"
    f.write_text("aus-datei\n")
    assert find_token({"SUPERVISOR_TOKEN": "aus-env"}, (str(f),)) == ("aus-env", "SUPERVISOR_TOKEN")
    assert find_token({"HASSIO_TOKEN": "alt"}, (str(f),)) == ("alt", "HASSIO_TOKEN")
    assert find_token({"SUPERVISOR_TOKEN": " "}, (str(tmp_path / "fehlt"), str(f))) == ("aus-datei", str(f))
    assert find_token({}, (str(tmp_path / "fehlt"),)) == ("", "")
