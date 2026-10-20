"""0.32: Anwesenheit – lange Abwesenheit, verlässliche Gewohnheit, GPS-Ankunft, Heimfahrt-Tempo."""

from datetime import datetime, timedelta

from heatpilot import presence
from heatpilot.config import Config
from heatpilot.dhw import DhwModel
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.presence import (HABIT_MIN_VISITS, SLOTS_PER_DAY, PersonLearner, arrival_unknown,
                                household_long_away, next_arrival, slot_of)
from heatpilot.store import Store

N = 7 * SLOTS_PER_DAY
MONDAY = datetime(2026, 10, 5, 0, 0)  # Montag


def _walk(p, t, home, dist, minutes, step=2):
    for i in range(0, minutes, step):
        d = dist(i) if callable(dist) else dist
        p.observe(t + timedelta(minutes=i), home, d)
    return t + timedelta(minutes=minutes)


def _commuter(weeks=3):
    """Werktags 8–17 Uhr weg (30 km), sonst zu Hause – im 15-min-Raster gelernt."""
    p = PersonLearner("person.a")
    for k in range(weeks * N):
        ts = MONDAY + timedelta(minutes=15 * k)
        away = ts.weekday() < 5 and 8 <= ts.hour < 17
        p.observe(ts, not away, 30.0 if away else 0.0)
    return p, MONDAY + timedelta(weeks=weeks)


# ------------------------------------------------------------------ lange Abwesenheit
def test_five_days_away_leaves_habit_unchanged_then_learns_normally():
    p, t = _commuter()
    p.observe(t, True, 0.0)  # Montag 0:00 zu Hause
    week0, visits0 = list(p.week), list(p.visits)
    t = _walk(p, t + timedelta(minutes=15), False, 400.0, 5 * 24 * 60, step=15)  # 5 Tage Reise
    assert p.long_away
    assert p.week == week0 and p.visits == visits0  # Raster wie beim Verlassen
    p.observe(t, True, 0.0)  # zurück
    assert not p.long_away
    s = slot_of(t)
    assert p.week[s] > week0[s] and p.visits[s] == visits0[s] + 1  # lernt wieder normal
    t = _walk(p, t + timedelta(minutes=15), False, 30.0, 60, step=15)
    assert p.week[slot_of(t - timedelta(minutes=15))] < week0[slot_of(t - timedelta(minutes=15))]


def test_weekend_away_is_still_learned():
    p, t = _commuter()
    fri = t + timedelta(days=4, hours=17)  # Freitag 17:00
    _walk(p, t, True, 0.0, 4 * 24 * 60 + 17 * 60, step=15)
    before = list(p.week)
    back = _walk(p, fri, False, 120.0, 51 * 60, step=15)  # bis Sonntag 20:00 weg (51 h, 120 km)
    assert not p.long_away
    sat_noon = slot_of(fri + timedelta(hours=19))
    assert p.week[sat_noon] < before[sat_noon]  # das Wochenende weg ist gelernt
    p.observe(back, True, 0.0)
    assert not p.long_away


def test_far_away_counts_as_long_after_one_day():
    p, t = _commuter()
    p.observe(t, True, 0.0)
    week0 = list(p.week)
    t = _walk(p, t + timedelta(minutes=15), False, 20.0, 20 * 60, step=15)
    t = _walk(p, t, False, 450.0, 6 * 60, step=15)  # 26 h weg, davon zuletzt 450 km
    assert p.long_away and p.week == week0


def test_household_long_away_flag_only_if_everyone_is_long_away(tmp_path):
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a", "person.b"])
    eng = Engine(Config(), Store(tmp_path / "l.db"), found)
    start = int(datetime(2026, 10, 13, 8, 0).timestamp())
    for p in eng.persons.values():  # beide seit 4 Tagen weg
        p._away_since = datetime.fromtimestamp(start).astimezone() - timedelta(days=4)
    eng.states["person.a"] = {"state": "not_home", "attributes": {}}
    eng.states["person.b"] = {"state": "home", "attributes": {}}
    eng.tick(start)
    assert eng.store.query("SELECT away_long FROM samples")[-1]["away_long"] == 0  # B ist da
    assert eng.persons["person.a"].long_away
    seen = []
    orig = eng.dhw.step
    eng.dhw.step = lambda *a, **kw: seen.append(kw["learn_usage"]) or orig(*a, **kw)
    eng.states["person.b"] = {"state": "not_home", "attributes": {}}
    eng.persons["person.b"]._away_since = datetime.fromtimestamp(start).astimezone() - timedelta(days=4)
    eng.tick(start + 300)
    row = eng.store.query("SELECT away_long, vacation FROM samples ORDER BY ts")[-1]
    assert row["away_long"] == 1 and row["vacation"] == 0  # eigenes Flag, kein Urlaub
    assert not eng.on_vacation(start + 300)
    assert seen[-1] is False  # Zapfprofil lernt nicht
    assert household_long_away(list(eng.persons.values()))


def test_rooms_do_not_learn_while_everyone_is_long_away(tmp_path):
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "r.db"), found)
    start = 1_790_000_000 - 1_790_000_000 % 3600
    for i in range(12):
        ts = start + i * 300
        eng.store.add_sample(ts, {"flow": 40.0, "t_out": 5.0, "heating": 1, "pump_mod": 50.0, "away_long": 1})
        eng.store.add_room_samples([(ts, "climate.wz", 20.0 + 0.01 * i, 21.0, 50.0, None)])
    eng.store.commit()
    calls = []
    model = eng.rooms["climate.wz"]
    orig = model.learn
    model.learn = lambda *a, **kw: calls.append(kw["update"]) or orig(*a, **kw)
    eng.learn_rooms(start, start + 3600)
    assert calls == [False]


# ------------------------------------------------------------------ Gewohnheit
def test_unvisited_slots_give_no_arrival():
    p = PersonLearner("person.a")
    now = datetime(2026, 10, 24, 20, 7)
    p.observe(now, False, None)
    assert p.next_home_by_habit(now) is None
    assert p.eta(now)["eta"] is None
    assert arrival_unknown([p], now) is not None  # offen, nicht „sicher weg“


def test_habit_needs_two_visits():
    p = PersonLearner("person.a")
    s = slot_of(MONDAY + timedelta(hours=18))
    p.week[s], p.visits[s] = 0.55, 1
    assert p.next_home_by_habit(MONDAY + timedelta(hours=17)) is None
    p.visits[s] = HABIT_MIN_VISITS
    eta, conf = p.next_home_by_habit(MONDAY + timedelta(hours=17))
    assert eta.hour == 18 and conf == 0.55


def test_1500_km_away_no_habit_arrival_in_15_min():
    p, t = _commuter()
    now = t + timedelta(hours=16, minutes=50)  # Montag 16:50, Gewohnheit: 17:00 zu Hause
    p.observe(now - timedelta(minutes=10), False, 30.0)
    assert p.eta(now)["source"] == "gewohnheit"
    p.observe(now, False, 1500.0)
    p.track.clear()  # kein Tempo – nur Entfernung
    p.track.append((now, 1500.0))
    e = p.eta(now)
    assert e["eta"] is None or (e["eta"] - now) > timedelta(hours=24)
    assert next_arrival([p], now) is None


def test_distance_is_earliest_arrival_for_habit():
    p = PersonLearner("person.a")
    p.visits = [5] * N
    p.week = [0.1] * N
    now = MONDAY + timedelta(hours=16)
    for h in (16.25, 17.25):
        p.week[slot_of(MONDAY + timedelta(hours=h))] = 0.9
    p.home, p.dist_km, p.approach_speed_kmh = False, 40.0, 40.0  # frühestens 17:00
    e = p.eta(now)
    assert e["source"] == "gewohnheit" and (e["eta"].hour, e["eta"].minute) == (17, 15)
    p.dist_km = 0.5  # in der Nähe: keine Untergrenze
    assert (p.eta(now)["eta"].hour, p.eta(now)["eta"].minute) == (16, 15)


def test_old_state_counts_as_learned_and_new_fields_roundtrip():
    old = {"person": "person.a", "week": [0.5] * N, "approach_speed_kmh": 40.0}
    p = PersonLearner.from_dict(old)
    assert p.visits == [HABIT_MIN_VISITS] * N and not p.long_away
    p.observe(datetime(2026, 10, 5, 8, 0), True, 0.0)
    _walk(p, datetime(2026, 10, 5, 8, 15), False, 500.0, 30 * 60, step=15)
    assert p.long_away
    q = PersonLearner.from_dict(p.to_dict())
    assert q.long_away and q.visits == p.visits and q._away_since == p._away_since
    q.observe(datetime(2026, 10, 7, 8, 0), True, 0.0)
    assert not q.long_away and "away" not in q.to_dict()


# ------------------------------------------------------------------ GPS-Ankunft
def test_slow_start_of_drive_does_not_delay_eta():
    p = PersonLearner("person.a", approach_speed_kmh=45.0)
    t = datetime(2026, 10, 8, 15, 0)
    t = _walk(p, t, False, 44.0, 30)
    t = _walk(p, t, False, lambda i: 44.0 - i * 10 / 60, 20)  # Stau: 10 km/h, schon > 2 km näher (0.32: echte Annäherung)
    e = p.eta(t)
    assert e["source"] == "gps"
    mins = (e["eta"] - t).total_seconds() / 60
    assert mins <= p.dist_km / 45.0 * 60 + 1  # nicht später als mit dem gelernten Tempo


def test_confirmed_return_pulls_eta_forward():
    p = PersonLearner("person.a")  # gelernt 35 km/h, gefahren wird 100 km/h
    t = datetime(2026, 10, 18, 12, 0)
    for i in range(10):
        p.observe(t + timedelta(minutes=5 * i), False, 300 - 8.33 * i)
    now = t + timedelta(minutes=45)
    ret = p.steady_return(now)
    assert ret is not None
    e = p.eta(now)
    assert e["source"] == "gps" and abs((e["eta"] - ret["eta"]).total_seconds()) < 60


# ------------------------------------------------------------------ Heimfahrt-Tempo
def test_gps_jump_standing_then_drive_learns_about_44_kmh():
    p = PersonLearner("person.a")
    t = datetime(2026, 10, 14, 12, 0)
    t = _walk(p, t, True, 0.0, 10)
    t = _walk(p, t, False, lambda i: min(i, 44.0), 60)        # zur Arbeit
    t = _walk(p, t, False, 44.0, 60)
    t = _walk(p, t, False, 43.0, 150)                         # GPS springt 1 km, dann 2,5 h Stehen
    t = _walk(p, t, False, lambda i: max(43.0 - i * 44 / 60, 0.3), 60)  # 1 h Fahrt
    p.observe(t, True, 0.0)
    assert p.arrivals_learned == 1
    observed = 35.0 + (p.approach_speed_kmh - 35.0) / 0.2
    assert 38 <= observed <= 50, observed


def test_implausible_speed_is_discarded_not_clamped():
    p = PersonLearner("person.a")
    t = datetime(2026, 10, 14, 12, 0)
    t = _walk(p, t, True, 0.0, 10)
    t = _walk(p, t, False, lambda i: min(i * 2, 300.0), 200)
    t = _walk(p, t, False, lambda i: 300.0 - i * 5, 20)       # Sprung: 300 km/h „näher“
    p.observe(t, True, 0.0)
    assert p.arrivals_learned == 0 and p.approach_speed_kmh == 35.0


# ------------------------------------------------------------------ Kennzahl
def test_quality_by_source_and_short_trips_excluded():
    p = PersonLearner("person.a")
    p.arrivals = [
        {"ts": "2026-10-17T08:05:00", "left": "2026-10-16T18:00:00", "max_km": 37.9,
         "checks": [{"before_min": 60, "source": "gewohnheit", "error_min": 655},
                    {"before_min": 30, "source": "gps", "error_min": 10}]},
        {"ts": "2026-10-16T17:00:00", "left": "2026-10-16T07:00:00", "max_km": 44.0,
         "checks": [{"before_min": 60, "source": "gps", "error_min": -14}]},
        {"ts": "2026-10-17T17:40:00", "left": "2026-10-17T17:10:00", "max_km": 0.6,  # Spaziergang
         "checks": [{"before_min": 15, "source": "gewohnheit", "error_min": 80}]},
    ]
    q = p.arrival_quality()
    assert q[60]["gps"] == {"n": 1, "mean_abs_min": 14, "within_15": 1.0}
    assert q[60]["gewohnheit"]["mean_abs_min"] == 655
    assert 15 not in q  # Abstecher zählt nicht
    assert len(p.arrivals) == 3 and presence.short_trip(p.arrivals[2])


# ------------------------------------------------------------------ Warmwasser
def test_dhw_keeps_setpoint_when_arrival_unknown():
    cfg = Config()
    m = DhwModel()
    now = datetime(2026, 10, 18, 12, 0)
    r = m.recommend(now, False, None, cfg, unknown_from=now + timedelta(minutes=15), current_set=55.0)
    assert r["target"] == 55 and "Ankunft unbekannt" in r["reason"]
    r = m.recommend(now, False, None, cfg, unknown_from=None, current_set=55.0)
    assert r["hold"] == cfg.dhw_min_temp and r["reason"].startswith("niemand zu Hause")


def test_engine_dhw_unknown_for_fresh_habit_but_minimum_when_long_away(tmp_path):
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "w.db"), found)
    eng.dhw.charge_rate_kh = 20.0
    ts = int(datetime(2026, 10, 13, 8, 0).timestamp())
    eng.persons["person.a"].observe(datetime.fromtimestamp(ts).astimezone(), False, None)
    import heatpilot.engine as engmod
    orig = engmod.guards.check_dhw
    engmod.guards.check_dhw = lambda m: {"ok": True, "reasons": []}
    try:
        eng.dhw_recommend(ts, {"home_count": 0, "dhw_set": 55.0})
        assert "Ankunft unbekannt" in eng.dhw_rec["reason"] and eng.dhw_rec["target"] == 55
        eng.persons["person.a"].long_away = True
        eng.dhw_recommend(ts, {"home_count": 0, "dhw_set": 55.0})
        assert eng.dhw_rec["hold"] == eng.cfg.dhw_min_temp
    finally:
        engmod.guards.check_dhw = orig


# ------------------------------------------------------------------ Export, Vorheizplan
def test_export_presence_in_5_min_grid(tmp_path):
    from types import SimpleNamespace

    from heatpilot import diagnostics

    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"], home_zone=(53.55, 10.0))
    eng = Engine(Config(), Store(tmp_path / "e.db"), found)
    t0 = 1_790_000_000 - 1_790_000_000 % 900
    eng.store.add_person_samples([(t0 + i * 120, "person.a", 0, 10.0 + i) for i in range(15)])
    eng.store.commit()
    app = SimpleNamespace(eng=eng, cfg=Config())
    data = diagnostics.export(app, days=10000)
    a = data["anwesenheit"]
    ts = [r[a["spalten"].index("ts")] for r in a["zeilen"]]
    assert len(ts) == 6 and all(b - a_ <= 360 for a_, b in zip(ts, ts[1:]))  # 5-min-Raster, nicht 15
    assert "anwesenheit" in data["hinweis"] and "53.55" not in str(data["anwesenheit"])
    est = diagnostics.export_estimate(app, 10000)
    assert est >= 60_000 + 6 * 40


def test_preheat_plan_carries_arrival_source_and_confidence(tmp_path, monkeypatch):
    import heatpilot.engine as engmod

    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"], outdoor=["sensor.out"])
    eng = Engine(Config(), Store(tmp_path / "p.db"), found)
    eng.states["sensor.out"] = {"state": "5.0", "attributes": {}}
    p = eng.persons["person.a"]
    p.home, p.visits = False, [5] * N
    now = engmod.local(int(datetime.now().timestamp()))
    p.week[(slot_of(now) + 4) % N] = 0.8
    monkeypatch.setattr(engmod.guards, "check_house", lambda h: {"ok": True, "reasons": []})
    monkeypatch.setattr(engmod.analysis, "preheat_plan", lambda *a, **kw: {"feasible": False, "options": []})
    plan = eng.summary()["plan"]
    assert plan["arrival_source"] == "gewohnheit" and plan["arrival_confidence"] == 0.8
