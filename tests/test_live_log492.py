"""0.49.2: 30-s-Werte 48 h in der Datenbank, als CSV herunterladbar."""

import asyncio
import csv
import io
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from heatpilot import live_log
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.store import Store

NOW = 1_790_000_000


def _eng(tmp_path):
    found = Discovered(rooms=[Room("Wohnzimmer", "climate.wz"), Room("Bad", "climate.bad")])
    return Engine(Config(), Store(tmp_path / "l.db"), found)


def test_points_are_kept_48_hours_and_survive_a_restart(tmp_path):
    eng = _eng(tmp_path)
    for i in range(0, 50 * 120):  # 50 h alle 30 s
        live_log.record(eng.store, NOW - 50 * 3600 + i * 30, {"flow": 50.0}, [])
    again = Store(tmp_path / "l.db")  # neu geöffnet wie nach einem Neustart
    rows = again.live_rows(0)
    assert rows[0][0] >= NOW - 48 * 3600 - 30 and len(rows) <= 48 * 120 + 1
    assert rows[-1][1]["s"]["flow"] == 50.0


def test_csv_has_boiler_values_and_named_rooms(tmp_path):
    eng = _eng(tmp_path)
    for i in range(3):
        sample = {"flow": 45.5 + i, "ret": 39.25, "burner_pow": 32.0, "burner_on": True, "pump_mod": 100.0,
                  "burn_starts": 347853 + i, "dhw": None}
        rooms = [("climate.wz", 20.1, 21.0, 100.0, 48.0), ("climate.other", 19.0, 19.5, 0.0)]
        live_log.record(eng.store, NOW - 60 + i * 30, sample, rooms)
    text = live_log.csv_text(eng, 1, NOW)
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    assert head[:4] == ["zeit", "ts", *live_log.COLS[:2]] and "burn_starts" in head
    assert head[-8:] == ["Wohnzimmer ist", "Wohnzimmer soll", "Wohnzimmer ventil", "Wohnzimmer feuchte",
                         "climate.other ist", "climate.other soll", "climate.other ventil", "climate.other feuchte"]
    assert "Bad ist" not in head  # nichts gemeldet: keine leeren Spalten
    assert len(rows) == 4
    first = dict(zip(head, rows[1]))
    assert first["flow"] == "45.5" and first["ret"] == "39.25" and first["burner_on"] == "1" and first["dhw"] == ""
    assert first["Wohnzimmer ventil"] == "100.0" and first["climate.other feuchte"] == ""
    assert live_log.csv_text(eng, 1, NOW + 7200).count("\n") == 1  # nur der Kopf


def test_engine_records_every_live_tick(tmp_path):
    eng = _eng(tmp_path)
    eng.record_live(NOW)
    eng.record_live(NOW + 30)
    assert [ts for ts, _ in eng.store.live_rows(0)] == [NOW, NOW + 30]
    assert len(eng.live.points) == 2


def test_download_route(tmp_path):
    from heatpilot.web import make_app

    eng = _eng(tmp_path)
    live_log.record(eng.store, NOW, {"flow": 40.0}, [])
    app = SimpleNamespace(eng=eng)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            r = await client.get("/api/live/export?hours=48")
            body = await r.text()
            bad = await client.get("/api/live/export?hours=x")
            return r.status, r.headers.get("Content-Disposition", ""), r.content_type, body, bad.status
        finally:
            await client.close()

    status, disp, ctype, body, bad = asyncio.run(go())
    assert status == 200 and ctype == "text/csv" and 'filename="heatpilot-30s-' in disp
    assert body.startswith("zeit,ts,") and bad == 200
