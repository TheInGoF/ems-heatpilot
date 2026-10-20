"""Prüfung 0.36 – Nachtzeiten aus dem Stromverbrauch: Funde der gegnerischen Prüfung als Regressionstests.

Behoben: gelernter Beginn nach Mitternacht (Nacht als Zeitspanne), Abtastintervall über 10 min
(Messwert gilt bis zum nächsten), Altwerte früherer Sensoren und angebrochene erste Nacht.
"""

import json
import sqlite3
import time
from datetime import date, datetime, time as dtime, timedelta
from types import SimpleNamespace

import pytest

from heatpilot import diagnostics, engine as engine_mod, schedule, sleep
from heatpilot.config import Config, validate
from heatpilot.controlloop import desired
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.store import Store

from test_control34 import HOME, at, wz
from test_sleep36 import BED, NOW, WAKE, fill, house_w

MON = (2026, 10, 12)


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# ------------------------------------------------------------------ 1./3. Gelernter Beginn nach Mitternacht
def test_f1_unit_learned_start_after_midnight_breaks_is_night_at():
    """Der Entwurf nennt Beginn 19:00–01:30 plausibel. Ein gelernter Beginn nach Mitternacht (z. B. 00:30)
    landet über night_entry in night_times – aber is_night_at/night_end_at setzen einen Abendbeginn voraus
    (_clean_night_plan erzwingt für eingestellte Werte „ab 12:00“). Folge: der ganze Tag ist Nacht."""
    cfg = Config(control_mode="heatpilot", night_plan_auto=True)
    cfg.night_plan_learned = [{"start": "00:30", "end": None}] + [{"start": None, "end": None}] * 6
    assert cfg.night_entry(0)["start"] == "00:30"
    # Montag 14:00 darf keine Nacht sein
    assert cfg.is_night_at(at(MON, 14)) is False, "Mo 14:00 gilt als Nacht"
    assert desired(wz(), at(MON, 14), cfg, HOME).phase == "komfort"


def test_f1_end_to_end_plan_marks_after_midnight_start_reliable_and_day_becomes_night(tmp_path):
    """Haus geht jeden Abend um 00:30 ins Bett (Entwurf: plausibel bis 01:30) → Plan belastbar „00:30“ →
    Engine setzt night_plan_learned → mit Häkchen ist am Nachmittag Nacht (Räume auf Nachttemperatur)."""
    bed = {k: 24 * 60 + 30 for k in BED}
    wake = {k: 6 * 60 + 30 for k in WAKE}
    store = fill(Store(tmp_path / "m.db"), days=28, power=lambda d: house_w(d, bed=bed, wake=wake))
    now = int(datetime(2026, 9, 28, 13, 0).timestamp())
    nights = sleep.learn(store, now)
    n = nights["2026-09-08"]
    assert n["valid"] and n["start_min"] == 24 * 60 + 30 and n["end_min"] == 6 * 60 + 30
    plan = sleep.plan(nights, now)
    assert plan[0]["start"] == "00:30" and plan[0]["reliable_start"], plan[0]
    cfg = Config(control_mode="heatpilot", power_sensors=["sensor.l1"], night_plan_auto=True)
    eng = Engine(cfg, store, Discovered(rooms=[Room("Wz", "climate.wz")], power=["sensor.l1"]))
    eng.update_sleep(now)
    assert cfg.night_plan_learned[0] == {"start": "00:30", "end": "06:30"}
    assert eng.sleep_summary()["active"]
    noon = datetime(2026, 9, 28, 14, 0).astimezone()  # Montag 14:00
    assert cfg.is_night_at(noon) is False, f"14:00 ist Nacht – night_entry={cfg.night_entry(0)}"
    assert desired(wz(), noon, cfg, HOME).phase != "nacht"


def test_f1_which_learned_starts_break_the_day_model():
    """Übersicht: jeder gelernte Beginn zwischen 00:00 und 01:30 macht den ganzen Tag zur Nacht."""
    broken = []
    for start_min in range(23 * 60, 25 * 60 + 31, 30):
        cfg = Config(control_mode="heatpilot", night_plan_auto=True)
        s = f"{(start_min % 1440) // 60:02d}:{start_min % 60:02d}"
        cfg.night_plan_learned = [{"start": s, "end": None}] * 7
        if cfg.is_night_at(at(MON, 14)):
            broken.append(s)
    assert broken == [], f"ganzer Tag Nacht bei gelerntem Beginn {broken}"


# ------------------------------------------------------------------ 3. gemischte Quellen über Mitternacht (erwartet grün)
def test_mixed_sources_across_midnight_consistent():
    cfg = Config(control_mode="heatpilot", night_plan_auto=True)
    # Mo: Beginn gelernt 23:00, Ende eingestellt 04:30; Di: Beginn eingestellt 21:00, Ende gelernt 05:30
    cfg.night_plan_learned = [{"start": "23:00", "end": None}, {"start": None, "end": "05:30"}] + [None] * 5
    tue = (2026, 10, 13)
    assert not cfg.is_night_at(at(MON, 22, 30))
    assert cfg.night_start_after(at(MON, 22, 30)) == at(MON, 23)
    assert cfg.is_night_at(at(MON, 23, 30)) and cfg.night_end_at(at(MON, 23, 30)) == at(tue, 5, 30)
    assert cfg.is_night_at(at(tue, 0, 30)) and cfg.night_end_at(at(tue, 0, 30)) == at(tue, 5, 30)
    assert not cfg.is_night_at(at(tue, 5, 45))
    d = desired(wz(), at(MON, 23, 30), cfg, HOME)
    assert d.phase == "nacht" and d.until == at(tue, 4, 30)  # 1 h Vorheizen vor dem gelernten Ende
    assert desired(wz(), at(tue, 5, 45), cfg, HOME).phase == "komfort"
    # Schalter aus → alles eingestellt
    cfg.night_plan_auto = False
    assert cfg.is_night_at(at(MON, 22, 30)) and cfg.night_end_at(at(MON, 23, 30)) == at(tue, 4, 30)
    # Beobachten: Plan None, Laufzeitfeld wirkt nur über night_times (wird dort nicht genutzt)
    cfg2 = Config(night_plan_auto=True)
    cfg2.night_plan_learned = [{"start": "23:00", "end": "05:30"}] * 7
    assert schedule.effective(cfg2, None).plan is None
    assert schedule.effective(cfg2, None).night_start == dtime(22, 30)


# ------------------------------------------------------------------ 1. DST Frühjahr (letzter So im März)
def test_spring_forward_night_no_crash_no_shift(tmp_path, berlin):
    # 2026-03-29 (So) 02:00 → 03:00; Nacht Sa→So: Bett 23:00, auf 07:30
    store = fill(Store(tmp_path / "dst.db"), start=datetime(2026, 3, 26, 18, 0), days=5)
    nights = sleep.learn(store, int(datetime(2026, 3, 31, 13, 0).timestamp()))
    assert len(nights) == 5 and all(n["valid"] for n in nights.values()), nights
    sa = nights["2026-03-28"]
    assert sa["start_min"] == 23 * 60 and sa["end_min"] == 7 * 60 + 30, sa
    so = nights["2026-03-29"]
    assert so["start_min"] == 22 * 60 and so["end_min"] == 5 * 60, so
    plan = sleep.plan(nights, int(datetime(2026, 3, 31, 13, 0).timestamp()))
    assert plan[5]["start"] == "23:00" and plan[6]["end"] == "07:30" and plan[6]["start"] == "22:00"


def test_learn_window_boundary_and_removal(tmp_path):
    """Nacht erst nach 12:00 des Folgetags; zweimal learn → gleiche kv; Verfall; reset_learning + Neuaufbau."""
    store = fill(Store(tmp_path / "w.db"), days=6)
    before = int(datetime(2026, 9, 4, 11, 59).timestamp())
    at_noon = int(datetime(2026, 9, 4, 12, 0).timestamp())
    assert "2026-09-03" not in sleep.learn(store, before)
    assert "2026-09-03" in sleep.learn(store, at_noon)
    kv1 = json.dumps(store.get("sleep.nights"), sort_keys=True)
    sleep.learn(store, at_noon + 1800)
    assert json.dumps(store.get("sleep.nights"), sort_keys=True) == kv1
    store.reset_learning("2026-01-01")
    assert store.get("sleep.nights") is None
    again = sleep.learn(store, at_noon)
    assert json.dumps(again, sort_keys=True) == kv1


# ------------------------------------------------------------------ 4. Messwerte: Abtastintervall > 10 min
@pytest.mark.parametrize("step", [600, 720, 900])
def test_f2_sample_interval_longer_than_bin(tmp_path, step):
    """sample_interval_s ist 60–900 s einstellbar. Bins sind 10 min; fehlende Bins zählen als Lücke.
    Mit 900 s (erlaubt) gibt es nur 72 von 108 Bins → jede Nacht „keine Daten“, die Funktion lernt nie."""
    store = fill(Store(tmp_path / f"s{step}.db"), days=10, step=step)
    nights = sleep.learn(store, int(datetime(2026, 9, 9, 13, 0).timestamp()))
    reasons = {n["reason"] for n in nights.values()}
    assert reasons == {"ok"}, f"step={step}: {reasons}"


# ------------------------------------------------------------------ 4. alte Datenbank ohne power-Spalten
def test_old_database_without_power_columns(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE samples(ts INTEGER PRIMARY KEY, t_out REAL, flow REAL, sel_flow REAL, ret REAL,
            burner_pow REAL, burner_on INTEGER, heating INTEGER, dhw INTEGER, gas REAL, min_period REAL,
            home_count INTEGER, vacation REAL, away_long REAL);
        CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT);
    """)
    t0 = int(datetime(2026, 9, 1, 18, 0).timestamp())
    db.executemany("INSERT INTO samples(ts, t_out, vacation, away_long) VALUES(?, ?, 0, 0)",
                   [(t0 + i * 300, 10.0) for i in range(300)])
    db.commit(); db.close()
    store = Store(path)
    cols = {r["name"] for r in store.db.execute("PRAGMA table_info(samples)")}
    assert {"power_w", "power_ex_w"} <= cols
    assert sleep.learn(store, int(datetime(2026, 9, 10, 13, 0).timestamp())) == {}
    cfg = Config(power_sensors=["sensor.l1"])
    eng = Engine(cfg, store, Discovered(power=["sensor.l1"], power_units={"sensor.l1": "W"}))
    eng.states["sensor.l1"] = {"state": "250", "attributes": {}}
    eng.tick(int(datetime(2026, 9, 10, 13, 0).timestamp()))
    assert store.query("SELECT power_w FROM samples WHERE power_w IS NOT NULL")[0]["power_w"] == 250.0
    assert cfg.night_plan_learned == [{"start": None, "end": None}] * 7


# ------------------------------------------------------------------ 4. Messwerte: Einheit, Phase, Ausschluss, Bereiche
def test_snapshot_power_edge_cases(tmp_path):
    found = Discovered(power=["sensor.l1", "sensor.l2"], power_exclude=["sensor.wm"], power_units={"sensor.l1": "kW"})
    eng = Engine(Config(), Store(tmp_path / "p.db"), found)
    st = lambda v, unit=None: {"state": v, "attributes": ({"unit_of_measurement": unit} if unit else {})}
    eng.states.update({"sensor.l1": st("0.5"), "sensor.l2": st("100", "W"), "sensor.wm": st("unavailable", "W")})
    s = eng.snapshot()[0]
    assert s["power_w"] == 600.0 and s["power_ex_w"] == 0.0
    eng.states["sensor.wm"] = st("-3", "W")  # negativer Ausschluss: unplausibel → 0, nicht abgezogen
    s = eng.snapshot()[0]
    assert s["power_ex_w"] is None and "power_ex_w" in eng._rejected
    eng.states["sensor.l2"] = st("unknown", "W")
    assert eng.snapshot()[0]["power_w"] is None
    eng.states["sensor.l2"] = st("200000", "W")  # über 100 kW: verworfen
    assert eng.snapshot()[0]["power_w"] is None and "power_w" in eng._rejected
    eng.states["sensor.l2"] = st("1,5", "kW")  # Komma-Zahl
    assert eng.snapshot()[0]["power_w"] == 2000.0


# ------------------------------------------------------------------ 5. Export/Diagnose ohne Koordinaten, mit Nächten
def test_export_and_bundle_with_sleep_without_coordinates(tmp_path):
    store = fill(Store(tmp_path / "x.db"), days=10)
    now = int(datetime(2026, 9, 9, 13, 0).timestamp())
    cfg = Config(control_mode="heatpilot", power_sensors=["sensor.l1"], night_plan_auto=True)
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"], home_zone=(53.55, 10.0),
                       power=["sensor.l1"], power_units={"sensor.l1": "W"})
    eng = Engine(cfg, store, found)
    eng.states["person.a"] = {"state": "home", "attributes": {"latitude": 53.9, "longitude": 10.4, "friendly_name": "A"}}
    eng.states["sensor.l1"] = {"state": "300", "attributes": {"unit_of_measurement": "W"}}
    eng.update_sleep(now)
    assert store.get("sleep.nights")
    connected = SimpleNamespace(is_set=lambda: True)
    app = SimpleNamespace(eng=eng, cfg=cfg, status="läuft", configured=True,
                          ha=SimpleNamespace(connected=connected, info=lambda: {"connected": True, "token_present": True,
                                                                                "last_error": ""}))
    for data in (diagnostics.bundle(app), diagnostics.export(app, days=60)):
        text = json.dumps(data, default=str)
        assert "latitude" not in text and "53.9" not in text and "10.4" not in text and "53.55" not in text
        assert "night_plan_learned" not in text
    b = diagnostics.bundle(app, hours=24 * 400)
    assert "sleep.nights" in b["kv"] and b["summary"]["sleep"]["sensors"] == ["sensor.l1"]
    assert "power_w" in b["samples"][0]
    assert b["settings"]["power_sensors"] == ["sensor.l1"]


# ------------------------------------------------------------------ 5. web-Payloads
def test_web_payloads_engine_none_and_with_sensors(tmp_path):
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot.web import make_app as web_app

    class App(SimpleNamespace):
        pass

    store = fill(Store(tmp_path / "web.db"), days=28)
    now = int(datetime(2026, 9, 28, 13, 0).timestamp())
    cfg = Config(control_mode="heatpilot", power_sensors=["sensor.l1", "sensor.fehlt"], night_plan_auto=True)
    found = Discovered(rooms=[Room("Wz", "climate.wz")], power=["sensor.l1"], power_units={"sensor.l1": "W"})
    eng = Engine(cfg, store, found)
    eng.update_sleep(now)

    async def registry():
        return []

    async def suggestions():
        return {}

    app = App(eng=None, cfg=cfg, status="startet", configured=True, registry=registry, suggestions=suggestions)

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            for path in ("/api/settings", "/api/summary"):
                j = await (await client.get(path)).json()
                assert j.get("starting") is True, (path, j)
            app.eng = eng
            j = await (await client.get("/api/settings")).json()
            assert j["sleep"]["sensors"] == ["sensor.l1"] and j["sleep"]["active"] is True
            assert j["settings"]["power_sensors"] == ["sensor.l1", "sensor.fehlt"]
            assert "night_plan_learned" not in j["settings"]
            assert j["discovered"]["power_units"] == {"sensor.l1": "W"}
            assert j["schedule"]["effective"]["plan"][0]["source_start"] == "gelernt (Strom)"
            assert validate(j["settings"])[1] == []
            j = await (await client.get("/api/summary")).json()
            assert j["sleep"]["nights"] == 28 and j["schedule"]["effective"]["plan"][5]["start"] == "23:00"
        finally:
            await client.close()

    asyncio.run(go())


# ------------------------------------------------------------------ Historie: alte power_w-Werte aus früherer Sensorwahl
def test_f3_stale_power_values_outside_backfill_window_count_as_nights(tmp_path):
    """Nach einem Sensorwechsel liest reload_history nur backfill_days (30) neu ein; ältere Messpunkte behalten
    power_w des alten Sensors. learn() beginnt bei MIN(ts) mit power_w → Nächte aus Altdaten des falschen
    Sensors zählen (hier: 'zu wenig Unterschied' in der Zeile 'ausgelassen', obwohl es die Nächte vor der
    Einrichtung gar nicht gab)."""
    store = Store(tmp_path / "stale.db")
    start = datetime(2026, 8, 17, 18, 0)
    now = int(datetime(2026, 9, 28, 13, 0).timestamp())
    # 42 Tage: zuerst war ein falscher Sensor gewählt (konstant 5 W, z. B. eine Lampe)
    fill(store, start=start, days=42, power=lambda d: 5.0)
    # Sensorwechsel: apply_settings verwirft die Hausleistung (clear_power), reload_history → reset_learning +
    # neu einlesen (seit 0.44 ohne Löschen: HA füllt nur Lücken – die verworfene Hausleistung ist eine)
    store.clear_power()
    store.reset_learning("2026-01-01")
    t_bf = now - 30 * 86400
    fill(store, start=datetime.fromtimestamp(t_bf), days=30)
    nights = sleep.learn(store, now)
    s = sleep.summary(nights, now, Config(power_sensors=["sensor.l1"]), ["sensor.l1"], [])
    assert s["invalid"] == {}, f"Altdaten des früheren Sensors zählen: {s['invalid']}"


def test_f3b_partial_first_night_after_setup_counted_as_no_data(tmp_path):
    """Sensoren um 23:00 eingerichtet: die angebrochene erste Nacht wird als „keine Daten“ gespeichert und
    sechs Wochen lang in „ausgelassen: 1× keine Daten“ gezählt – obwohl vorher gar nicht gemessen wurde."""
    store = fill(Store(tmp_path / "late.db"), start=datetime(2026, 9, 1, 23, 0), days=8)
    now = int(datetime(2026, 9, 9, 13, 0).timestamp())
    nights = sleep.learn(store, now)
    s = sleep.summary(nights, now, Config(power_sensors=["sensor.l1"]), ["sensor.l1"], [])
    assert s["invalid"] == {}, s["invalid"]


def test_controlloop_status_with_learned_times(tmp_path):
    """ControlLoop.as_dict: Statuszeile „Nacht bis …“ folgt dem gelernten Ende (erwartet grün)."""
    from test_control34 import make, step

    eng, ha, ctl, loop = make(tmp_path)
    cfg = eng.cfg
    cfg.night_plan_auto = True
    cfg.night_plan_learned = [{"start": "23:00", "end": "05:30"}] * 7
    ts = int(at(MON, 23, 30).timestamp())
    step(eng, loop, ts)
    d = loop.as_dict(ts)
    assert "Nacht bis 05:30" in d["status_text"], d["status_text"]
    cfg.night_plan_auto = False
    assert "Nacht bis 04:30" in loop.as_dict(ts)["status_text"]
