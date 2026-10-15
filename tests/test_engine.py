from datetime import datetime

from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room, discover
from heatpilot.engine import Engine
from heatpilot.emsesp import flatten
from heatpilot.store import Store

T0 = datetime(2026, 10, 15, 22, 0).timestamp()


def make_engine(tmp_path):
    found = Discovered(
        boiler={
            "burner_on": "binary_sensor.boiler_burngas",
            "burner_power": "sensor.boiler_curburnpow",
            "nom_power": "number.boiler_nompower",
            "min_period": "number.boiler_burnminperiod",
            "dhw_active": "binary_sensor.boiler_dhw_charging",
        },
        rooms=[Room("Wohnzimmer", "climate.wz"), Room("Bad", "climate.bad")],
    )
    eng = Engine(Config(), Store(tmp_path / "t.db"), found)
    for eid, st in {
        "number.boiler_nompower": "18",
        "number.boiler_burnminperiod": "25",
        "binary_sensor.boiler_dhw_charging": "off",
        "sensor.boiler_curburnpow": "30",
    }.items():
        eng.states[eid] = {"state": st, "attributes": {}}
    return eng


def room(eng, eid, temp, target):
    eng.states[eid] = {"state": "heat", "attributes": {"current_temperature": temp, "temperature": target}}


def burner(eng, on):
    eng.states["binary_sensor.boiler_burngas"] = {"state": "on" if on else "off", "attributes": {}}


def test_overrun_after_setpoint_drop_and_lockout_wait(tmp_path):
    eng = make_engine(tmp_path)
    room(eng, "climate.wz", 20.5, 21.0)
    room(eng, "climate.bad", 21.0, 21.0)
    burner(eng, True)
    eng.update_live(T0)
    assert eng.setpoint_demand() is True

    # 22:00 Tado senkt nachts ab – Ventile schließen erst 10 min später
    room(eng, "climate.wz", 20.6, 18.0)
    room(eng, "climate.bad", 21.0, 18.0)
    eng.update_live(T0 + 1)
    eng.update_live(T0 + 1 + 600)
    burner(eng, False)
    eng.update_live(T0 + 601)

    # Morgens: Soll steigt, aber die Taktsperre (25 min) läuft noch 5 min
    room(eng, "climate.wz", 18.4, 21.0)
    eng.update_live(T0 + 601 + 20 * 60)
    eng.update_live(T0 + 601 + 25 * 60)
    eng.update_live(T0 + 601 + 27 * 60)
    eng.store.commit()

    day = datetime.fromtimestamp(T0).date().isoformat()
    d = eng.store.daily(day)[day]
    assert abs(d["overrun_min"] - 10.0) < 0.1
    assert abs(d["overrun_kwh"] - 0.3 * 18 * 10 / 60) < 0.01
    assert abs(d["blocked_min"] - 5.0) < 0.1
    assert abs(d["waiting_min"] - 7.0) < 0.1


def test_setpoint_demand_hysteresis(tmp_path):
    eng = make_engine(tmp_path)
    room(eng, "climate.wz", 20.9, 21.0)
    room(eng, "climate.bad", 21.0, 21.0)
    assert eng.setpoint_demand() is None  # im Band, noch kein Zustand
    room(eng, "climate.wz", 20.7, 21.0)
    assert eng.setpoint_demand() is True
    room(eng, "climate.wz", 20.9, 21.0)
    assert eng.setpoint_demand() is True  # Hysterese hält
    room(eng, "climate.wz", 21.0, 21.0)
    assert eng.setpoint_demand() is False
    eng.states["climate.wz"] = {"state": "off", "attributes": {"current_temperature": 15}}
    room(eng, "climate.bad", 21.0, 21.0)
    assert eng.setpoint_demand() is False


def test_emsesp_values_take_priority(tmp_path):
    eng = make_engine(tmp_path)
    burner(eng, False)
    eng.set_ems(flatten({"burngas": "on", "dhw": {"charging": "off"}, "burnminperiod": 25}))
    assert eng.burner_on() == 0  # set_ems erwartet Geräte-Ebene
    eng.set_ems({"boiler": flatten({"burngas": "on", "dhw": {"charging": "off"}})})
    assert eng.burner_on() == 1
    assert flatten({"hc1": {"seltemp": 21}})["hc1_seltemp"] == 21


def test_configured_rooms_and_outdoor_sensors():
    cfg = Config()
    cfg.outdoor_sensors = ["sensor.a", "sensor.b"]
    cfg.rooms = [{"name": "Büro", "climate": "climate.buero", "heating_power": "sensor.buero_heating"}]
    states = [
        {"entity_id": "sensor.a", "state": "3", "attributes": {}},
        {"entity_id": "sensor.b", "state": "1", "attributes": {}},
        {"entity_id": "climate.buero", "state": "heat", "attributes": {"current_temperature": 20}},
        {"entity_id": "climate.tado_other", "state": "heat", "attributes": {"current_temperature": 20}},
        {"entity_id": "sensor.boiler_nrgtotal", "state": "6994", "attributes": {}},
    ]
    d = discover(states, [], cfg)
    assert [r.name for r in d.rooms] == ["Büro"]
    assert d.rooms[0].heating_power == "sensor.buero_heating"
    assert d.outdoor == ["sensor.a", "sensor.b"]
    assert d.gas == "sensor.boiler_nrgtotal" and d.gas_unit == "kWh"
    assert abs(cfg.gas_kwh_per_m3 - 11.2 * 0.95) < 1e-9
