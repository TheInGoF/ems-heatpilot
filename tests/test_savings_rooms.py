"""Ersparnis nur bei gleichem Komfort – auch je Raum, nicht nur fürs ganze Haus."""

from datetime import datetime, time

from heatpilot import analysis
from heatpilot.models import HouseModel, RoomModel

START = int(datetime(2026, 11, 2, 0, 0).timestamp())  # Montag


def setup(days=7):
    house = HouseModel()
    house.rls.theta, house.rls.updates = [0.16, 2.7, 0.05], 1000
    hourly, room_hourly = [], {}
    for i in range(days * 24):
        ts = START + i * 3600
        d = datetime.fromtimestamp(ts)
        home = 0.0 if d.weekday() < 5 and 8 <= d.hour < 17 else 1.0
        hourly.append({"ts": ts, "t_in": 21.0, "t_out": 0.0, "gas_kwh": 3.4, "home_frac": home,
                       "dhw_frac": 0.0, "vacation_frac": 0.0})
        room_hourly[ts] = {"climate.stark": 21.0, "climate.schwach": 21.0}
    return house, hourly, room_hourly


def room(a):
    m = RoomModel("r")
    m.rls.theta, m.rls.updates, m.excitation = [a, 0.05, 0.0], 500, 100.0
    return m


def night(t: time) -> bool:
    return t >= time(22, 0) or t < time(6, 0)


def run(rooms, room_hourly, hourly, house):
    return analysis.estimate_savings(hourly, house, 17.0, 18.0, night, 18.0 * 0.95,
                                     rooms=rooms, room_hourly=room_hourly, max_flow=75.0)


def test_slow_room_makes_preheat_start_earlier():
    house, hourly, rh = setup()
    only_house = run({}, rh, hourly, house)
    strong = run({"climate.stark": room(0.06)}, rh, hourly, house)
    weak = run({"climate.stark": room(0.06), "climate.schwach": room(0.012)}, rh, hourly, house)
    # das große Haus-Modell mit 18-kW-Kessel heizt rechnerisch sofort auf – die Räume nicht
    assert weak.saved_kwh < strong.saved_kwh <= only_house.saved_kwh
    assert weak.lead_by_rooms > 0
    assert weak.room_hours > 0 and sum(weak.room_cold.values()) <= 2  # Komfort gehalten


def test_room_that_cannot_recover_prevents_setback():
    house, hourly, rh = setup()
    hopeless = run({"climate.schwach": room(0.003)}, rh, hourly, house)  # erreicht 21 °C nie wieder
    assert hopeless.saved_kwh < 1.0
    assert sum(hopeless.room_cold.values()) == 0
