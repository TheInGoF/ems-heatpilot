"""Nicht auf ein Haus zugeschnitten: das Hausmodell findet bei sehr verschiedenen Häusern die wahren Werte.

Künstliche Häuser mit zwei Wärmespeichern (siehe sim_house.py): Altbau (schnell, hoher Verlust),
Neubau (träge, kleiner Verlust), Fußbodenheizung (langsame Wärmeabgabe), schwere Wände, jeweils mit
Nachtabschaltung oder durchgehend geheizt – über eine Heizsaison ab Oktober und mit Start im
Dezember bei stark wechselndem Wetter (Tagesmittel ±6 K).
"""

from datetime import datetime

import pytest

from heatpilot.models import HouseModel
from sim_house import House

SEASON = dict(t_out_mean=8.0, season_k=8.0)
HOUSES = {
    "altbau": House(g_ao=0.08, g_am=0.8, g_mo=0.2, c_a=1.2, c_m=8.0, free_kw=0.8, p_max_kw=18, **SEASON),
    "neubau": House(g_ao=0.03, g_am=0.5, g_mo=0.05, c_a=0.8, c_m=14.0, free_kw=0.5, p_max_kw=8, **SEASON),
    "fussboden": House(g_ao=0.04, g_am=0.25, g_mo=0.08, c_a=2.5, c_m=10.0, free_kw=0.6, p_max_kw=10, **SEASON),
    "altbau_durchgeheizt": House(g_ao=0.08, g_am=0.8, g_mo=0.2, c_a=1.2, c_m=8.0, free_kw=0.8, p_max_kw=18,
                                 day=(0, 24), **SEASON),
    "neubau_durchgeheizt": House(g_ao=0.03, g_am=0.5, g_mo=0.05, c_a=0.8, c_m=14.0, free_kw=0.5, p_max_kw=8,
                                 day=(0, 24), **SEASON),
    "schwer": House(g_ao=0.03, g_am=0.8, g_mo=0.06, c_a=1.0, c_m=25.0, free_kw=0.6, p_max_kw=10, **SEASON),
}


def learn_days(m: HouseModel, rows: list[tuple]) -> None:
    by: dict[str, list[tuple]] = {}
    for r in rows:
        by.setdefault(datetime.fromtimestamp(r[0]).date().isoformat(), []).append(r)
    for day, v in sorted(by.items()):
        if len(v) >= 20:
            m.learn_day(day, sum(r[2] - r[1] for r in v) / len(v), sum(r[1] for r in v) / len(v),
                        sum(r[3] for r in v) / len(v))


@pytest.mark.parametrize("name", list(HOUSES))
def test_heat_loss_found_for_any_house(name):
    house = HOUSES[name]
    m = HouseModel()
    learn_days(m, house.simulate(200, datetime(2026, 10, 1)))
    assert m.daily_valid
    assert abs(m.h_kw_per_k / house.h_kw_per_k - 1) < 0.10, (m.h_kw_per_k, house.h_kw_per_k)
    assert abs(m.offset_kw - house.offset_kw) < 0.3
    assert abs(m.demand_kw(20, -12) / (house.h_kw_per_k * 32 + house.offset_kw) - 1) < 0.10


@pytest.mark.parametrize("name", ["neubau", "fussboden", "schwer", "altbau"])
def test_winter_start_with_changeable_weather(name):
    """Start am 1. Dezember, 90 Tage, Tagesmittel ±6 K: mit nur einem Vortag lag H bei trägen Häusern 20–25 % zu tief."""
    house = House(**{**HOUSES[name].__dict__, "t_out_noise": 6.0})
    m = HouseModel()
    learn_days(m, house.simulate(90, datetime(2026, 12, 1)))
    assert m.daily_valid
    assert abs(m.h_kw_per_k / house.h_kw_per_k - 1) < 0.05, (m.h_kw_per_k, house.h_kw_per_k)
    assert abs(m.offset_kw - house.offset_kw) < 0.2


def test_too_little_weather_variety_keeps_hourly_balance():
    """Zwei Wochen fast gleiches Wetter: H aus Tagen wäre Zufall – dann gilt weiter das Stundenmodell."""
    house = House(t_out_mean=5.0, t_out_swing=1.0)
    m = HouseModel()
    learn_days(m, house.simulate(40, datetime(2026, 11, 1)))
    assert not m.daily_valid and m.h_kw_per_k == max(m.rls.theta[0], 0.0)


def test_days_must_follow_each_other():
    m = HouseModel()
    m.learn_day("2026-11-01", 15.0, 5.0, 2.0)
    assert m.learn_day("2026-11-02", 15.5, 5.0, 2.1) is None  # noch kein Tag für das Wochenmittel
    assert m.learn_day("2026-11-04", 16.0, 4.0, 2.2) is None  # Lücke: Vortag fehlt
    assert m.learn_day("2026-11-05", 17.0, 3.0, 2.4) is not None  # Vortag da, Wochenmittel aus 1./2.
    assert m.learn_day("2026-11-05", 17.0, 3.0, 2.4) is None  # derselbe Tag zählt nicht doppelt
    assert m.learn_day("2026-11-06", 12.0, 8.0, 0.3, learn=False) is None  # Urlaub: nur Zustand
    assert [h[0] for h in m.daily_hist] == ["2026-11-01", "2026-11-02", "2026-11-04", "2026-11-05", "2026-11-06"]
    back = HouseModel.from_dict(m.to_dict())
    assert back.daily.theta == m.daily.theta and back.daily_hist == m.daily_hist
    old = m.to_dict()
    old["daily"] = {**old["daily"], "n": 3, "theta": [0.1, 0.05, 0.0], "P": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
    assert HouseModel.from_dict(old).daily.updates == 0  # Tagesmodell von vor 0.32: neu lernen


@pytest.mark.parametrize("name", ["altbau", "neubau_durchgeheizt"])
def test_engine_pipeline_finds_heat_loss(tmp_path, name):
    """Über die Engine (Stundenwerte → Tage) wie im Betrieb und beim Neulernen."""
    from heatpilot.config import Config
    from heatpilot.discovery import Discovered
    from heatpilot.engine import Engine
    from heatpilot.store import Store
    house = HOUSES[name]
    eng = Engine(Config(), Store(tmp_path / "e.db"), Discovered())
    rows = house.simulate(200, datetime(2026, 10, 1))
    eng.store.add_hourly(rows)
    eng.store.commit()
    for r in rows:
        eng.learn_house_hour(r[0] + 3600)
    m = eng.house
    assert m.daily_valid and abs(m.h_kw_per_k / house.h_kw_per_k - 1) < 0.10
    assert m.balance_plausible
    # durchgehend geheizt: nie gasfrei – das Auskühlen ist nicht gemessen, Trägheit dann aus der Bilanz
    assert (m.tau_cool_h is None) == (house.day == (0, 24))


def test_room_starts_with_this_houses_cooling_time():
    """Kein Pauschalwert: ein ungelernter Raum übernimmt die gemessene Auskühlzeit des eigenen Hauses."""
    from heatpilot.models import RoomModel
    r = RoomModel("Wz")
    r.adopt_house_tau(120.0)
    assert abs(r.b - 1 / 120) < 1e-9
    r.learn(20.0, 19.9, 0.0, 5.0)
    r.adopt_house_tau(30.0)  # schon gelernt: bleibt
    assert abs(r.tau_h - 1 / r.rls.theta[1]) < 1e-9 and r.tau_h > 60
