import math
import random
from datetime import datetime, timedelta

from heatpilot.analysis import estimate_savings
from heatpilot.config import Config
from heatpilot.dhw import DhwModel
from heatpilot.engine import Engine
from heatpilot.discovery import Discovered, Room
from heatpilot.models import HouseModel, RoomModel
from heatpilot.store import Store
from heatpilot.vacation import Vacation, parse_ranges


def simulate_bath(model: RoomModel, days=21, seed=4):
    """Bad: Fußbodenheizung (festes RTL, 3 h träge) heizt immer, Heizkörper nur als Boost."""
    rng = random.Random(seed)
    t, slab = 21.0, 0.0
    a, f, b, c = 0.05, 0.03, 0.04, 0.05
    for step in range(days * 48):
        hour = (step / 2) % 24
        t_out = 3 + 4 * math.sin(2 * math.pi * (hour - 9) / 24)
        heating = 1.0 if rng.random() < 0.6 else 0.0       # Brenner/Pumpe läuft
        flow = 45.0 if heating else 22.0
        boost = 1.0 if hour in (6.0, 6.5, 19.0, 19.5) else 0.0  # Heizkörper beim Duschen
        t0 = t
        for _ in range(30):
            dt = 1 / 60
            warm = max(flow - t, 0) * heating
            slab += (1 - math.exp(-dt / 3.0)) * (warm - slab)
            t += (a * warm * boost + f * slab + b * (t_out - t) + c) * dt
        warm_mean = max(flow - (t0 + t) / 2, 0) * heating
        model.learn(round(t0, 1), round(t, 1), warm_mean * boost, t_out, floor_input=warm_mean)
    return model


def test_floor_heating_room_is_learned_better_with_floor_input():
    floor = simulate_bath(RoomModel("Bad", floor=True))
    plain = simulate_bath(RoomModel("Bad (ohne Fußboden)"))
    assert floor.f > 0.01
    assert floor.rls.rmse < plain.rls.rmse
    # Fußboden heizt mit: dauerhaft nötiger Vorlauf liegt niedriger als nur mit Heizkörper
    radiator_only = 21 + (floor.b * 21 - floor.c) / floor.a
    assert floor.required_flow(21, 0) < radiator_only
    d = RoomModel.from_dict(floor.to_dict())
    assert d.floor and d.f == floor.f and d.floor_u == floor.floor_u


def test_vacation_ranges_parse_and_end_is_arrival():
    v = Vacation(["2026-12-20 - 2027-01-03", "2027-02-01 08:00 – 2027-02-05 17:30", "Quatsch"], None)
    assert len(v.ranges) == 2
    inside = datetime(2026, 12, 24, 12, 0).astimezone()
    assert v.active(inside) and v.end(inside).hour == 18
    assert not v.trips[0].end_exact and v.trips[1].end_exact
    # niemand zurück: Urlaub bleibt nach Plan-Ende aktiv (bis zu 3 Tage), dann vorbei
    assert v.active(datetime(2027, 1, 4, 12, 0).astimezone())
    assert not v.active(datetime(2027, 1, 7, 12, 0).astimezone())
    assert v.active(datetime(2027, 1, 10).astimezone(), "on")  # Schalter


def test_vacation_freezes_habits_but_not_physics(tmp_path):
    cfg = Config()
    cfg.vacations = ["2026-10-12 - 2026-10-18"]
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"])
    eng = Engine(cfg, Store(tmp_path / "v.db"), found)
    eng.states["person.a"] = {"state": "not_home", "attributes": {}}
    week_before = list(eng.persons["person.a"].week)
    start = int(datetime(2026, 10, 13, 8, 0).timestamp())
    for i in range(48):
        eng.tick(start + i * 300)
    assert eng.persons["person.a"].week == week_before  # Gewohnheiten unverändert
    rows = eng.store.query("SELECT vacation FROM samples")
    assert rows and all(r["vacation"] == 1 for r in rows)
    s = eng.summary()  # Laufzeit: summary darf im Urlaub nicht scheitern
    assert s["vacation"]["active"] is False or s["vacation"]["until"] is not None


def test_dhw_usage_profile_frozen_during_vacation():
    m = DhwModel()
    when = datetime(2026, 11, 2, 6, 0)
    for i in range(36):  # 3 h, um 7 Uhr eine "Zapfung"
        t_prev, t_cur = 50.0, (35.0 if i == 12 else 50.0)
        m.step(when + timedelta(minutes=5 * i), 5 / 60, t_prev, t_cur, False, None, learn_usage=False)
    assert max(m.daily) == 0.0


def test_savings_skip_vacation_hours():
    house = HouseModel()
    house.rls.theta = [0.2, 3.0, 0.2]
    cfg = Config()
    start = int(datetime(2026, 10, 12, 0, 0).timestamp())
    rows = [{"ts": start + h * 3600, "t_in": 21.0, "t_out": 2.0, "gas_kwh": 4.0,
             "home_frac": 0.0, "vacation_frac": 1.0} for h in range(48)]
    res = estimate_savings(rows, house, cfg.away_temp, cfg.night_temp, cfg.is_night)
    assert res.hours == 0 and res.saved_kwh == 0


def _bath_like_room():
    m = RoomModel("Wz")
    m.rls.theta = [0.05, 0.04, 0.0]  # Heizkörper, Auskühlen, Fremdwärme
    m.rls.updates = 500
    return m


def test_return_from_vacation_charges_dhw_first_then_heats_never_both():
    """Rückkehr: Warmwasser 30 -> 55 °C dauert 30 min, Heizen ~1 h – nacheinander planen."""
    from heatpilot.analysis import PLAN_BUFFER_MIN, preheat_plan

    house = HouseModel()
    house.rls.theta = [0.2, 3.0, 0.2]
    now = datetime(2027, 1, 3, 12, 0).astimezone()
    arrival = now + timedelta(hours=6)
    dhw = {"hours": 0.5, "from": 30.0, "to": 55.0}
    plan = preheat_plan([(_bath_like_room(), 17.0, 21.0)], house, arrival, now, 2.0, 75, dhw=dhw)
    best = plan["best"]
    steps = best["steps"]
    assert [s["what"] for s in steps] == ["Warmwasser", "Heizen"]
    assert steps[0]["end"] == steps[1]["start"]  # direkt nacheinander, nie gleichzeitig
    assert steps[1]["end"] == arrival  # pünktlich warm
    lead_h = (arrival - best["start"]).total_seconds() / 3600
    assert abs(lead_h - (best["duration_h"] + 0.5 + PLAN_BUFFER_MIN / 60)) < 0.01
    without = preheat_plan([(_bath_like_room(), 17.0, 21.0)], house, arrival, now, 2.0, 75)
    assert best["start"] < without["best"]["start"]  # mit Warmwasser früher los


def test_too_late_starts_now_and_says_how_late():
    from heatpilot.analysis import preheat_plan

    house = HouseModel()
    house.rls.theta = [0.2, 3.0, 0.2]
    now = datetime(2027, 1, 3, 17, 30).astimezone()
    arrival = now + timedelta(minutes=45)
    plan = preheat_plan([(_bath_like_room(), 17.0, 21.0)], house, arrival, now, 2.0, 75,
                        dhw={"hours": 0.5, "from": 30.0, "to": 55.0})
    best = plan["best"]
    assert best["start"] == now and best["late_min"] > 0
    assert best["steps"][0]["what"] == "Warmwasser"


def test_dhw_cooling_and_charge_time():
    m = DhwModel()
    m.loss.theta = [0.72, -0.04]  # Umgebung 18 °C
    m.charge_rate_kh = 50.0
    assert abs(m.cool(55.0, 24) - (18 + 37 * math.exp(-0.04 * 24))) < 0.01
    assert abs(m.charge_hours(30.0, 55.0) - 0.5) < 1e-9


def _trip_engine(tmp_path, ranges):
    cfg = Config()
    cfg.vacations = ranges
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a", "person.b"],
                       home_zone=(53.55, 10.0))
    return Engine(cfg, Store(tmp_path / "trip.db"), found)


def _away(eng, person, lat, ts):
    eng.states[person] = {"state": "not_home", "attributes": {"latitude": lat, "longitude": 10.0}}


def test_early_return_by_gps_beats_planned_end(tmp_path):
    """Plan: Rückkehr in 2 Tagen. Person A fährt aber heute schon mit 100 km/h Richtung Heimat."""
    eng = _trip_engine(tmp_path, ["2026-10-10 - 2026-10-20 15:00"])
    t0 = int(datetime(2026, 10, 18, 12, 0).timestamp())
    lat0 = 53.55 + 250 / 111  # 250 km nördlich
    _away(eng, "person.b", lat0, t0)
    for i in range(8):  # 35 min Autobahn: 100 km/h
        _away(eng, "person.a", lat0 - (100 * i / 12) / 111, t0)
        eng.tick(t0 + i * 300)
    now = datetime.fromtimestamp(t0 + 7 * 300).astimezone()
    arrival = eng.vacation.arrival(now, list(eng.persons.values()))
    assert arrival["source"] == "gps" and arrival["person"] == "person.a"
    hours = (arrival["eta"] - now).total_seconds() / 3600
    assert 1.8 < hours < 2.2  # noch ~192 km bei 100 km/h
    assert eng.on_vacation(t0 + 7 * 300)  # unterwegs: Urlaub noch aktiv, Gewohnheiten pausiert


def test_slow_movement_at_holiday_place_is_ignored(tmp_path):
    eng = _trip_engine(tmp_path, ["2026-10-10 - 2026-10-20"])
    t0 = int(datetime(2026, 10, 15, 12, 0).timestamp())
    lat0 = 53.55 + 600 / 111
    _away(eng, "person.b", lat0, t0)
    for i in range(6):  # Spaziergang 4 km/h Richtung Heimat
        _away(eng, "person.a", lat0 - (4 * i / 12) / 111, t0)
        eng.tick(t0 + i * 300)
    arrival = eng.vacation.arrival(datetime.fromtimestamp(t0 + 5 * 300).astimezone(),
                                   list(eng.persons.values()))
    assert arrival["source"] == "urlaub_datum"  # nur Datum -> Standard-Uhrzeit
    assert arrival["eta"].hour == 18 and arrival["eta"].day == 20


def test_being_home_ends_vacation_and_resumes_learning(tmp_path):
    eng = _trip_engine(tmp_path, ["2026-10-10 - 2026-10-20"])
    t0 = int(datetime(2026, 10, 18, 17, 0).timestamp())
    for p in ("person.a", "person.b"):
        eng.states[p] = {"state": "home", "attributes": {"latitude": 53.55, "longitude": 10.0}}
    for i in range(4):  # 15 min zu Hause
        eng.tick(t0 + i * 300)
    assert not eng.on_vacation(t0 + 4 * 300)
    assert eng.store.get("vacation_returned")  # bleibt über Neustarts erhalten
    eng2 = Engine(eng.cfg, eng.store, eng.found)
    assert not eng2.on_vacation(t0 + 4 * 300)


def _drive(learner, start, kms):
    """Entfernungen (km) im 5-Minuten-Takt in den Tracker einspielen."""
    for i, d in enumerate(kms):
        learner.observe(start + timedelta(minutes=5 * i), False, d, learn=False)
    return start + timedelta(minutes=5 * (len(kms) - 1))


def test_return_needs_uninterrupted_approach():
    from heatpilot.presence import PersonLearner

    start = datetime(2026, 10, 18, 12, 0).astimezone()
    # 20 min schnell in Richtung Heimat: noch nicht sicher
    p = PersonLearner("person.a")
    now = _drive(p, start, [300 - 8 * i for i in range(5)])
    assert p.steady_return(now) is None
    # Zickzack: 100 km/h hin und her – nie ununterbrochen näher
    p = PersonLearner("person.a")
    now = _drive(p, start, [300, 292, 298, 290, 296, 288, 294, 286, 292, 284])
    assert p.steady_return(now) is None
    # 35 min durchgehend mit 100 km/h näher: bestätigt
    p = PersonLearner("person.a")
    now = _drive(p, start, [300 - 8.33 * i for i in range(8)])
    ret = p.steady_return(now)
    assert ret is not None and 90 < ret["speed_kmh"] < 110


def test_confirmed_return_survives_rest_stop_but_not_turning_back():
    from heatpilot.presence import PersonLearner

    start = datetime(2026, 10, 18, 12, 0).astimezone()
    p = PersonLearner("person.a")
    kms = [300 - 8.33 * i for i in range(8)]
    now = _drive(p, start, kms)
    eta_before = p.steady_return(now)["eta"]
    # 30 min Rast: bleibt bestätigt, Ankunft verschiebt sich nach hinten
    rest = [kms[-1]] * 6
    now = _drive(p, now + timedelta(minutes=5), rest)
    ret = p.steady_return(now)
    assert ret is not None and ret["eta"] > eta_before
    # Kehrtwende: 10 km wieder weiter weg -> aufgehoben, Plan gilt wieder
    now = _drive(p, now + timedelta(minutes=5), [kms[-1] + 5, kms[-1] + 10])
    assert p.steady_return(now) is None
