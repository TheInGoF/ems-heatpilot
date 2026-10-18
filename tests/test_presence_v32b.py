"""0.32: Anwesenheit nachgebessert – regelmäßige lange Abwesenheit, unbekannter Tracker,
Neuberechnung aus eigenen Messwerten, GPS-Schwankung ist keine Heimfahrt."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from heatpilot import backfill
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine, local
from heatpilot.presence import SLOTS_PER_DAY, PersonLearner, household_long_away, next_arrival, slot_of
from heatpilot.store import Store

N = 7 * SLOTS_PER_DAY
MONDAY = datetime(2026, 8, 3, 0, 0)  # Montag


def _run(p, start, weeks, away, dist, step_min=15):
    """``weeks`` Wochen im Raster abtasten; ``away(ts)`` sagt, ob die Person weg ist."""
    for k in range(int(weeks * 7 * 24 * 60 // step_min)):
        ts = start + timedelta(minutes=step_min * k)
        a = away(ts)
        p.observe(ts, not a, dist if a else 0.0)
    return start + timedelta(weeks=weeks)


def _week_commuter(ts):
    wd, h = ts.weekday(), ts.hour
    return (wd == 0 and h >= 6) or wd in (1, 2, 3) or (wd == 4 and h < 18)


# ------------------------------------------------------------------ regelmäßige lange Abwesenheit
def test_weekly_commuter_is_learned_and_habit_gives_friday_arrival():
    p = PersonLearner("person.a")
    t = _run(p, MONDAY, 7, _week_commuter, 100.0)
    fri = t + timedelta(days=4, hours=15)  # Woche 8, Freitag 15:00
    _run(p, t, (4 * 24 + 15) / (7 * 24), _week_commuter, 100.0)
    assert not p.long_away and p.away_regular
    assert not household_long_away([p])
    e = p.eta(fri)
    assert e["source"] == "gewohnheit" and (e["eta"].weekday(), e["eta"].hour, e["eta"].minute) == (4, 18, 0)
    assert next_arrival([p], fri)["eta"] == e["eta"]
    wed = slot_of(MONDAY + timedelta(days=2, hours=12))
    assert p.week[wed] < 0.4 and p.visits[wed] >= 5  # die Woche weg ist gelernt


def test_one_off_trip_leaves_grid_unchanged():
    p = PersonLearner("person.a")
    t = _run(p, MONDAY, 3, lambda ts: ts.weekday() < 5 and 8 <= ts.hour < 17, 30.0)
    t += timedelta(days=2, hours=10)  # Mittwoch 10:00 los
    p.observe(t - timedelta(minutes=15), True, 0.0)
    week0, visits0 = list(p.week), list(p.visits)
    for k in range(5 * 96):
        p.observe(t + timedelta(minutes=15 * k), False, 200.0)
    assert p.long_away and not p.away_regular
    assert p.week == week0 and p.visits == visits0
    p.observe(t + timedelta(days=5), True, 0.0)
    assert p.away_trips[-1][1] == pytest.approx(120.0, abs=0.5)


def test_regular_absence_that_gets_much_longer_becomes_a_trip():
    p = PersonLearner("person.a")
    t = _run(p, MONDAY, 3, _week_commuter, 100.0)
    start = t + timedelta(hours=6)  # Montag 06:00 wie immer los – diesmal 10 Tage
    _run(p, t, 6 / (7 * 24), lambda ts: False, 0.0)
    week0, visits0 = list(p.week), list(p.visits)
    ts = start
    while ts < start + timedelta(hours=100):
        p.observe(ts, False, 100.0)
        ts += timedelta(minutes=15)
    assert p.away_regular and not p.long_away and p.visits != visits0  # lernt wie jede Woche
    while ts < start + timedelta(days=10):
        p.observe(ts, False, 100.0)
        ts += timedelta(minutes=15)
    assert p.long_away and not p.away_regular
    assert p.week == week0 and p.visits == visits0  # zurück auf den Stand bei Abfahrt


def test_far_weekend_trip_every_two_weeks_is_learned_from_the_second():
    p = PersonLearner("person.a")
    start = MONDAY + timedelta(days=4, hours=17)  # Freitag 17:00
    p.observe(start - timedelta(minutes=15), True, 0.0)
    saturday = slot_of(start + timedelta(hours=19))  # Samstag 12:00

    def trip(begin):
        ts = begin
        while ts < begin + timedelta(hours=51):  # bis Sonntag 20:00, 400 km
            p.observe(ts, False, 400.0)
            ts += timedelta(minutes=15)
        return ts

    v0 = p.visits[saturday]
    back = trip(start)
    assert p.long_away and p.visits[saturday] == v0  # erste Fahrt: wie eine Reise
    nxt = start + timedelta(weeks=2)
    while back < nxt:
        p.observe(back, True, 0.0)
        back += timedelta(minutes=15)
    v1, w1 = p.visits[saturday], p.week[saturday]
    trip(nxt)
    assert not p.long_away and p.away_regular
    assert p.visits[saturday] == v1 + 1 and p.week[saturday] < w1  # zweite Fahrt: gelernt
    q = PersonLearner.from_dict(p.to_dict())
    assert q.away_regular and q.away_trips == p.away_trips


# ------------------------------------------------------------------ Tracker unbekannt
def test_tracker_unknown_for_four_days_is_not_long_away(tmp_path):
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a", "person.b"])
    eng = Engine(Config(), Store(tmp_path / "u.db"), found)
    ts = int(datetime(2026, 10, 5, 8, 0).timestamp())
    eng.states["person.a"] = {"state": "home", "attributes": {}}
    eng.states["person.b"] = {"state": "not_home", "attributes": {}}
    eng.persons["person.b"]._away_since = local(ts) - timedelta(days=5)  # B ist auf Reise
    eng.observe_persons(ts, eng.snapshot()[2], False)
    visits0 = list(eng.persons["person.a"].visits)
    for state in ("unknown", "unavailable"):
        eng.states["person.a"] = {"state": state, "attributes": {}}
        for _ in range(2 * 96):
            ts += 900
            sample, _, persons = eng.snapshot()
            assert {p: h for p, h, _ in persons}["person.a"] is None
            assert sample["home_count"] is None  # B weg, A unbekannt: offen, ob jemand da ist
            assert eng.observe_persons(ts, persons, False) is False  # A unbekannt: nicht „alle lange weg“
    a = eng.persons["person.a"]
    assert not a.long_away and a.home is True and a.visits == visits0  # nichts gelernt
    assert eng.persons["person.b"].long_away
    del eng.states["person.a"]  # fehlt ganz: ebenso
    assert eng.observe_persons(ts + 900, eng.snapshot()[2], False) is False
    eng.states["person.a"] = {"state": "unknown", "attributes": {}}
    eng.store.add_person_samples((ts, *p) for p in eng.snapshot()[2])
    rows = {r["person"]: r["home"] for r in eng.store.query("SELECT person, home FROM person_samples")}
    assert rows == {"person.a": None, "person.b": 0}  # leer gespeichert, nicht „weg“


# ------------------------------------------------------------------ Neuberechnung
STEP = 900


def _home(ts: int) -> bool:
    d = local(ts)
    return not (d.weekday() < 5 and 8 <= d.hour < 17)


class _FakeHA:
    """HA-Recorder mit nur ``days`` Tagen Personen-Historie (Zustand zum Abfragebeginn inklusive)."""

    def __init__(self, end_ts: int, days: int, fail: bool = False):
        self.first, self.end, self.fail = end_ts - days * 86400, end_ts, fail

    async def history(self, ids, s, e, attributes=False):
        if self.fail:
            raise RuntimeError("HA nicht erreichbar")
        if "person.a" not in ids:
            return {}
        t0, t1 = max(int(s.timestamp()), self.first), min(int(e.timestamp()), self.end)
        out, prev = [], None
        for ts in range(t0 - t0 % STEP, t1, STEP):
            st = "home" if _home(ts) else "not_home"
            if ts >= t0 and st != prev:
                out.append({"s": st, "a": {}, "lu": ts})
                prev = st
        return {"person.a": out}

    async def statistics(self, *a, **kw):
        raise RuntimeError("HA nicht erreichbar")


def _recalc_engine(tmp_path, end_ts):
    cfg = Config()
    cfg.sample_interval_s, cfg.backfill_days, cfg.statistics_backfill_days = STEP, 10, 0
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"])
    store = Store(tmp_path / "n.db")
    own = range(end_ts - 30 * 86400, end_ts, STEP)  # 30 Tage eigene Messwerte
    store.add_person_samples((ts, "person.a", int(_home(ts)), None) for ts in own)
    store.commit()
    ref = PersonLearner("person.a")  # so hatte HeatPilot live gelernt
    for ts in own:
        ref.observe(local(ts), _home(ts), None)
    return Engine(cfg, store, found), ref


@pytest.mark.parametrize("fail", [False, True])
def test_recalc_learns_persons_from_own_samples_before_ha_history(tmp_path, monkeypatch, fail):
    end_ts = int(datetime(2026, 10, 16, 12, 0).timestamp())
    monkeypatch.setattr(backfill, "time", SimpleNamespace(time=lambda: end_ts))
    eng, ref = _recalc_engine(tmp_path, end_ts)
    asyncio.run(backfill.run_backfill(_FakeHA(end_ts, 10, fail), eng))
    p = eng.persons["person.a"]
    assert p.visits == ref.visits  # ganz nachgelernt, nichts doppelt
    assert p.week == pytest.approx(ref.week)
    assert max(p.visits) >= 4
    n = eng.store.query("SELECT COUNT(*) AS n FROM person_samples")[0]["n"]
    assert n == 30 * 86400 // STEP


# ------------------------------------------------------------------ GPS-Schwankung
JITTER = [43.4, 42.9, 43.0, 44.4, 42.3, 42.8, 44.4, 43.35, 43.4, 42.2, 43.9, 42.7, 44.4, 42.4, 43.6, 42.6]


@pytest.mark.parametrize("step_s", [60, 300, 900])
def test_gps_jitter_at_work_gives_no_arrival_within_one_hour(step_s):
    p = PersonLearner("person.a", approach_speed_kmh=45.0)
    t = datetime(2026, 10, 13, 8, 0)
    p.observe(t, True, 0.0)
    speeds = []
    for k in range(1, 8 * 3600 // step_s):  # 8 h am Arbeitsplatz, GPS springt alle 15 min
        now = t + timedelta(seconds=k * step_s)
        p.observe(now, False, JITTER[(k * step_s // 900) % len(JITTER)])
        e = p.eta(now)
        if e["speed_kmh"] is not None:
            speeds.append(e["speed_kmh"])
        assert e["eta"] is None or e["eta"] - now > timedelta(hours=1), (now, e)
    assert any(3 < v <= 9 for v in speeds)  # die Schwankung sieht wie langsame Annäherung aus


def test_real_drive_still_gives_gps_arrival():
    p = PersonLearner("person.a", approach_speed_kmh=45.0)
    t = datetime(2026, 10, 13, 16, 0)
    p.observe(t, True, 0.0)
    for k in range(1, 30):
        p.observe(t + timedelta(minutes=k), False, 43.4)
    now = t + timedelta(minutes=30)
    for k in range(6):  # 6 min mit 50 km/h
        p.observe(now + timedelta(minutes=k), False, 43.4 - 5 / 6 * k)
    e = p.eta(now + timedelta(minutes=5))
    assert e["source"] == "gps" and timedelta(minutes=40) < e["eta"] - now < timedelta(minutes=60)
