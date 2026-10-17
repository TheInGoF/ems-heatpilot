"""0.26: Raummodelle – Pumpe liefert Wärme, kein Kovarianz-Aufblähen, Sonne nie negativ, Leitplanken."""

import tempfile
from datetime import datetime
from pathlib import Path

from heatpilot import advice, guards
from heatpilot.analysis import estimate_savings
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.models import HouseModel, RoomModel
from heatpilot.rls import RLS
from heatpilot.store import Store


def test_idle_inputs_do_not_wind_up():
    r = RLS(n=2, lam=0.99, delta=1.0)
    for _ in range(200):
        r.update([0.0, 1.0], 0.0, idle=[0])
    assert r.P[0][0] <= 1.0 + 1e-9  # ohne gerichtetes Vergessen wüchse es auf ~e^2


def test_pump_without_burner_heats_the_room():
    st = Store(Path(tempfile.mkdtemp()) / "p.db")
    eng = Engine(Config(), st, Discovered(rooms=[Room("Flur", "climate.flur")]))
    t0 = 1_800_000_000 - 1_800_000_000 % 1800
    for k in range(7):  # Brenner aus, Pumpe läuft, Vorlauf 35 °C, Ventil offen
        ts = t0 + k * 300
        st.add_sample(ts, {"flow": 35.0, "t_out": 5.0, "burner_on": 0, "heating": 0, "pump_mod": 99, "dhw": 0})
        st.add_room_samples([(ts, "climate.flur", 19.0 + 0.05 * k, 21.0, 100.0, None)])
    st.commit()
    before = eng.rooms["climate.flur"].excitation
    eng.learn_rooms(t0, t0 + 1800)
    assert eng.rooms["climate.flur"].excitation > before + 1  # Wärmeeingang gezählt


def test_sun_never_negative():
    m = RoomModel("r")
    for k in range(300):  # Sonne aus Westen fällt mit Abkühlung zusammen – darf nicht „kühlen“
        m.learn(21.0, 20.9, 0.0, 5.0, 0.5, solar=(0.0, 0.0, 1.0 if k % 2 else 0.0))
    assert min(m.rls.theta[3:6]) >= 0.0


def test_guard_rejects_implausible_room():
    m = RoomModel("r")
    m.rls.theta, m.rls.updates, m.excitation = [0.24, 0.0076, -0.05], 500, 0.4
    reasons = " ".join(guards.check_room(m)["reasons"])
    assert "Heizwirkung/Auskühlen" in reasons and "zu wenig Heizbetrieb" in reasons and "unter außen" in reasons


def test_unreachable_ignores_dhw_flow(tmp_path):
    rooms = [Room("Flur", "climate.flur")]
    eng = Engine(Config(), Store(tmp_path / "u.db"), Discovered(rooms=rooms, persons=["person.a"]))
    t0 = int(datetime(2026, 10, 5).timestamp())
    for i in range(7 * 288):
        ts = t0 + i * 300
        d = datetime.fromtimestamp(ts)
        evening = 18 <= d.hour < 22
        dhw = evening and d.minute < 10
        eng.store.add_person_samples([(ts, "person.a", 1, 0.0)])
        eng.store.add_sample(ts, {"flow": 85.0 if dhw else 40.0, "dhw": int(dhw)})
        eng.store.add_room_samples([(ts, "climate.flur", 18.5 if evening else 21.0, 21.0, 100.0 if evening else 30.0, None)])
    eng.store.commit()
    items = [a for a in advice.build(eng, t0 + 7 * 86400) if a["kind"] == "nicht_erreichbar"]
    assert items and "am Limit" not in items[0]["text"]


def test_no_savings_from_hours_without_heating():
    house = HouseModel()
    house.rls.theta, house.rls.updates = [0.15, 4.0, -0.9], 1000
    start = int(datetime(2026, 11, 2).timestamp())
    hourly = []
    for i in range(7 * 24):
        ts = start + i * 3600
        d = datetime.fromtimestamp(ts)
        night = d.hour >= 22 or d.hour < 6
        # nachts heizt die Anlage ohnehin nicht, das Haus kühlt nur langsam
        hourly.append({"ts": ts, "t_in": 20.5 if night else 21.0, "t_out": 8.0, "gas_kwh": 0.0 if night else 1.2,
                       "home_frac": 1.0, "dhw_frac": 0.0, "vacation_frac": 0.0})
    is_night = lambda t: t.hour >= 22 or t.hour < 6  # noqa: E731
    res = estimate_savings(hourly, house, 18.0, 18.0, is_night, 18.0)
    assert res.saved_kwh >= 0
    # Absenken auf 18 °C in Nächten ohne Heizbetrieb spart fast nichts – das Haus kühlt kaum
    assert res.saved_kwh < 0.15 * res.actual_kwh
