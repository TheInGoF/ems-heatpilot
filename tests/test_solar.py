"""Sonnenwärme je Raum: Südraum lernt Süd, Nordraum lernt „kaum Sonne“."""

import random
from datetime import datetime, timezone

from heatpilot import solar
from heatpilot.models import ROOM_STEP_H, RoomModel

LAT, LON = 53.55, 10.0


def test_sun_position_is_plausible():
    noon = datetime(2026, 6, 21, 11, 20, tzinfo=timezone.utc).timestamp()
    elev, az = solar.sun_position(noon, LAT, LON)
    assert 59 < elev < 61 and 175 < az < 185
    morning = datetime(2026, 6, 21, 5, 0, tzinfo=timezone.utc).timestamp()
    e, s, w = solar.facade_inputs(morning, LAT, LON, 0)
    assert e > 0.3 and s == 0 and w == 0
    night = datetime(2026, 12, 21, 22, 0, tzinfo=timezone.utc).timestamp()
    assert solar.facade_inputs(night, LAT, LON, 0) == (0.0, 0.0, 0.0)


def simulate(gains):
    """Raum ohne Heizung, Sonne heizt mit ``gains`` (Ost, Süd, West) K/h bei voller Sonne."""
    rnd = random.Random(1)
    m = RoomModel("r")
    t, t0 = 20.0, datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
    for i in range(30 * 48):
        ts = t0 + i * ROOM_STEP_H * 3600
        cloud = rnd.choice([0, 20, 60, 100])
        sun = solar.facade_inputs(ts, LAT, LON, cloud)
        t_out = 5.0
        rate = 0.05 * (t_out - t) + 0.6 + sum(g * x for g, x in zip(gains, sun))
        t_new = t + rate * ROOM_STEP_H + rnd.gauss(0, 0.02)
        m.learn(t, t_new, 0.0, t_out, ROOM_STEP_H, solar=sun)
        t = t_new
    return m


def test_south_room_learns_south_and_north_room_learns_nothing():
    south = simulate((0.0, 1.5, 0.0)).solar
    assert south[1] > 1.0 and south[0] < 0.4 and south[2] < 0.4
    assert "Süd" in solar.describe(south)
    north = simulate((0.0, 0.0, 0.0)).solar
    assert max(north) < 0.15 and solar.describe(north) == "kaum Sonne"


def test_old_room_model_is_extended_not_reset():
    from heatpilot.rls import RLS
    old = RLS(n=3, theta=[0.03, 0.05, 0.1], P=[[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)])
    d = {"room": "r", "floor": False, "rls": old.to_dict()}  # Stand von vor 0.20 (und vor 0.53: ohne „form“)
    m = RoomModel.from_dict(d)
    # Sonne ergänzt, alles wie gelernt (gültig ist die lineare Form wie bis 0.52)
    assert m.rls.n == 6 and m.rls.theta[:3] == [0.03, 0.05, 0.1] and m.solar == (0.0, 0.0, 0.0) and m.migrated
