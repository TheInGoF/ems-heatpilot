"""Live-Ansicht: Datenbank (5 min) und Live-Puffer (30 s) zu einer Zeitreihe."""

from heatpilot import live
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.store import Store

NOW = 1_790_000_000


def test_series_merges_database_and_buffer(tmp_path):
    found = Discovered(rooms=[Room("Wz", "climate.wz"), Room("Bad", "climate.bad")])
    eng = Engine(Config(), Store(tmp_path / "l.db"), found)
    for i in range(12 * 12):  # 12 h alle 5 min in der Datenbank
        ts = NOW - 12 * 3600 + i * 300
        eng.store.add_sample(ts, {"flow": 50.0, "burner_on": i % 2, "t_out": 3.0})
        eng.store.add_room_samples([(ts, "climate.wz", 20.0, 21.0, 40.0, None),
                                    (ts, "climate.bad", 22.0, 22.0, 0.0, None)])
    eng.store.commit()
    for i in range(120):  # letzte Stunde alle 30 s im Speicher
        ts = NOW - 3600 + i * 30
        eng.live.add(ts, {"flow": 60.0, "burner_on": 1}, [("climate.wz", 21.0, 21.0, 10.0, None)])
    data = live.series(eng, 12, NOW)
    s = data["series"]
    assert len(s["ts"]) == 11 * 12 + 120  # DB nur bis zum Beginn des Puffers
    assert s["ts"] == sorted(s["ts"]) and s["ts"][0] == NOW - 12 * 3600
    assert s["flow"][0] == 50.0 and s["flow"][-1] == 60.0
    assert s["t_in"][0] == 21.0 and s["t_in"][-1] == 21.0  # Mittel Wz 20 / Bad 22, später nur Wz
    wz = next(r for r in data["rooms"] if r["climate"] == "climate.wz")
    assert wz["valve"][-1] == 10.0 and len(wz["ts"]) == len(s["ts"])
    bad = next(r for r in data["rooms"] if r["climate"] == "climate.bad")
    assert bad["temp"][-1] is None  # im Puffer nicht gemeldet: Lücke statt erfundener Wert


def test_buffer_keeps_twelve_hours():
    buf = live.LiveBuffer()
    for i in range(3000):
        buf.add(i * 30, {}, [])
    assert len(buf.points) == buf.points.maxlen and buf.points[-1][0] == 2999 * 30
    assert buf.points[0][0] <= 2999 * 30 - 12 * 3600
