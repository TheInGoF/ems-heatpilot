import math
import random
from datetime import datetime, timedelta

from heatpilot.analysis import cycling_stats, estimate_savings, preheat_plan
from heatpilot.config import Config
from heatpilot.models import HouseModel, RoomModel
from heatpilot.presence import PersonLearner, next_arrival
from heatpilot.rls import RLS


def test_rls_recovers_linear_parameters():
    rng = random.Random(1)
    rls = RLS(n=3, lam=1.0)
    for _ in range(500):
        x = [rng.uniform(-5, 5), rng.uniform(0, 3), 1.0]
        rls.update(x, 2.0 * x[0] - 0.5 * x[1] + 0.3 + rng.gauss(0, 0.01))
    assert abs(rls.theta[0] - 2.0) < 0.01
    assert abs(rls.theta[1] + 0.5) < 0.01
    assert abs(rls.theta[2] - 0.3) < 0.01


def simulate_room(a=0.05, b=0.04, c=0.1, days=14, seed=2):
    """Synthetischer Raum mit Heizzyklen und Tado-typischer 0,1-K-Auflösung."""
    rng = random.Random(seed)
    model = RoomModel("Test")
    t = 19.0
    for step in range(days * 48):
        hour = (step / 2) % 24
        t_out = 3 + 4 * math.sin(2 * math.pi * (hour - 9) / 24)
        flow = 55.0 if 6 <= hour < 22 and rng.random() < 0.7 else 20.0
        valve = 1.0 if t < 21 else 0.3
        t0 = t
        for _ in range(30):  # minütliche Physik
            dt = 1 / 60
            u = max(flow - t, 0) * valve
            t += (a * u + b * (t_out - t) + c) * dt
        model.learn(round(t0, 1), round(t, 1), max(flow - (t0 + t) / 2, 0) * valve, t_out)
    return model


def test_room_model_learns_physics():
    m = simulate_room()
    assert abs(m.b - 0.04) < 0.015, m.rls.theta
    assert abs(m.a - 0.05) < 0.01, m.rls.theta
    assert m.rls.rmse < 0.15
    # Aufheizen von 18 auf 21 °C: höherer Vorlauf muss schneller sein
    slow = m.time_to_reach(18, 21, 45, 0)
    fast = m.time_to_reach(18, 21, 70, 0)
    assert fast is not None and slow is not None and fast < slow


def test_house_model_learns_heat_loss():
    rng = random.Random(3)
    house = HouseModel()
    t_in = 20.5
    for _ in range(24 * 60):
        t_out = rng.uniform(-8, 12)
        d_in = rng.uniform(-0.3, 0.3)
        gas = 0.18 * (t_in - t_out) + 2.5 * d_in + 0.25 + rng.gauss(0, 0.2)
        house.learn(gas, t_in, t_out, d_in)
    assert abs(house.h_kw_per_k - 0.18) < 0.01
    assert abs(house.c_kwh_per_k - 2.5) < 0.3
    s = house.summary(-12, 0, 20)
    assert 6.0 < s["design_load_kw"] < 6.6


def test_house_ignores_summer():
    house = HouseModel()
    assert house.learn(0.3, 22, 25, 0) is None
    assert house.rls.updates == 0


def test_presence_gps_eta_and_learning():
    p = PersonLearner("person.a")
    start = datetime(2026, 10, 15, 17, 0).astimezone()
    p.observe(start - timedelta(minutes=5), False, 30.0)
    for i in range(10):  # 60 km/h Richtung Zuhause
        p.observe(start + timedelta(minutes=i), False, 30.0 - i)
    info = p.eta(start + timedelta(minutes=9))
    assert info["source"] == "gps"
    mins = (info["eta"] - (start + timedelta(minutes=9))).total_seconds() / 60
    assert 15 < mins < 60
    for i in range(10, 31):
        p.observe(start + timedelta(minutes=i), i == 30, max(30.0 - i, 0.05))
    assert p.arrivals_learned == 1
    assert p.approach_speed_kmh > 35


def test_presence_habit_and_multiple_people():
    a, b = PersonLearner("person.a"), PersonLearner("person.b")
    monday = datetime(2026, 10, 12, 0, 0).astimezone()
    for week in range(8):
        for slot in range(96 * 7):
            ts = monday + timedelta(weeks=week, minutes=15 * slot)
            weekday_work = ts.weekday() < 5 and 8 <= ts.hour < 17
            a.observe(ts, not weekday_work, None)
            b.observe(ts, not (ts.weekday() < 5 and 7 <= ts.hour < 18), None)
    now = monday + timedelta(weeks=8, hours=12)  # Montag Mittag, beide weg
    a.home = b.home = False
    arr = next_arrival([a, b], now)
    assert arr["person"] == "person.a"
    assert arr["eta"].hour == 17 and arr["source"] == "gewohnheit"


def test_cycling_stats():
    base = 1_000_000
    ev = [(base, 1), (base + 300, 0), (base + 900, 1), (base + 3900, 0)]
    s = cycling_stats(ev, base - 10, base + 10_000)
    assert s["starts"] == 2 and s["short_runs"] == 1
    assert s["avg_pause_min"] == 10.0


def test_savings_positive_when_away():
    house = HouseModel()
    house.rls.theta = [0.2, 3.0, 0.2]
    cfg = Config()
    start = int(datetime(2026, 10, 12, 0, 0).astimezone().timestamp())
    hourly = []
    for h in range(24 * 7):
        when = datetime.fromtimestamp(start + h * 3600)
        away = when.weekday() < 5 and 8 <= when.hour < 17
        hourly.append({"ts": start + h * 3600, "t_in": 21.0, "t_out": 2.0,
                       "gas_kwh": 0.2 * 19 + 0.2, "home_frac": 0.0 if away else 1.0})
    res = estimate_savings(hourly, house, cfg.away_temp, cfg.night_temp, cfg.is_night)
    assert res.saved_kwh > 0 and 3 < res.saved_pct < 25


def test_preheat_plan_prefers_feasible_cheapest():
    m = simulate_room()
    house = HouseModel()
    house.rls.theta = [0.2, 3.0, 0.2]
    now = datetime(2026, 10, 15, 15, 0).astimezone()
    plan = preheat_plan([(m, 18.5, 21.0)], house, now + timedelta(hours=3), now, 2.0, 75)
    assert plan["feasible"]
    assert plan["best"]["start"] <= now + timedelta(hours=3)
    assert all(o["duration_h"] > 0 for o in plan["options"])

