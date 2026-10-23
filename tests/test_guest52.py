"""0.52: Gast-Ansicht – der Reiter „Heizen“ fürs Lovelace-Dashboard, ohne Einstellungen.

Sicherheitsregeln (jede hat einen Test): Gäste (direkt über den Port, ohne ``X-Ingress-Path``) erreichen nur die
Startseite und die Heizen-Endpunkte; Einstellungen, Diagnose und Export sind 403 – auch die Pause. Über Ingress
bleibt alles wie bisher. Ohne Supervisor (Tests, Entwicklung) ist der Schutz aus."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from heatpilot import web_guest
from heatpilot.web import make_app

def test_guest_sees_heat_but_not_settings(tmp_path):
    async def go():
        from test_control34 import make_app as build

        app, _, _ = build(tmp_path)
        web_app = make_app(app)
        web_app["guarded"] = True  # wie im Add-on (SUPERVISOR_TOKEN gesetzt)
        client = TestClient(TestServer(web_app))
        await client.start_server()
        ingress = {"X-Ingress-Path": "/api/hassio_ingress/abc"}
        try:
            assert (await client.get("/api/heat")).status == 200           # Gast: Heizen ja
            assert (await client.get("/", allow_redirects=False)).status == 200
            assert (await client.get("/api/summary")).status == 200
            for path in ("/api/settings", "/api/entities", "/api/live", "/api/diagnostics", "/api/export",
                         "/api/boiler", "/api/preview"):
                r = await client.get(path)
                assert r.status == 403, path
                assert "Gast-Ansicht" in (await r.json())["error"]
            assert (await client.post("/api/settings", json={})).status == 403
            assert (await client.post("/api/heat/pause", json={"hours": 2})).status == 403  # Pause nie für Gäste
            r = await client.post("/api/heat/boost", json={"rooms": [], "temp": 24, "minutes": 30})
            assert r.status != 403                                          # Bedienen erlaubt (Prüfung macht Control)
            assert (await client.get("/api/settings", headers=ingress)).status == 200  # Ingress: alles wie bisher
            j = await (await client.get("/api/heat")).json()
            assert j.get("guest") is True
            j = await (await client.get("/api/heat", headers=ingress)).json()
            assert j.get("guest") is False
        finally:
            await client.close()

    asyncio.run(go())


def test_guard_is_off_without_supervisor(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    async def go():
        from test_control34 import make_app as build

        app, _, _ = build(tmp_path)
        web_app = make_app(app)
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            assert (await client.get("/api/settings")).status == 200  # Entwicklung/Tests: kein Schutz
        finally:
            await client.close()

    asyncio.run(go())


def test_allowlists_are_minimal():
    assert set(web_guest.ALLOWED_POST) == {"/api/heat/boost", "/api/heat/schedule", "/api/heat/temp",
                                           "/api/heat/mode"}
    assert "/api/heat/pause" not in web_guest.ALLOWED_POST and "/api/settings" not in web_guest.ALLOWED_GET


def test_ui_hides_tabs_for_guests():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot" / "static"
            / "index.html").read_text(encoding="utf-8")
    assert "guestMode(j.guest)" in html and "function guestMode" in html
    assert 'GUEST ? "" : act("pause"' in html
