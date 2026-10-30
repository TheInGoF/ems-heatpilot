"""0.71: Stufe-3-Räume (Ventil direkt geführt) – nie eine Betriebsart oder ein Soll ans Thermostat (das beendet dessen
Ventilsteuerung); die Knöpfe wirken auf HeatPilots eigene Betriebsarten: Aus (Frostschutz), Manuell, Boost, Zeitplan."""

from types import SimpleNamespace

import pytest

from heatpilot import heat_actions, valve_modes
from heatpilot.control import ControlError
from heatpilot.discovery import Room

from test_control import run
from test_valve_pid51 import T0, vhouse, vroom, vstep


def _app(eng, vp):
    return SimpleNamespace(eng=eng, control=vp.control, cfg=eng.cfg)


def _climate_calls(ha):
    return [c for c in ha.calls if c[0] in ("climate", "tado")]


def test_thermostat_of_a_stage3_room_gets_no_climate_call(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    with pytest.raises(PermissionError, match="Stufe 3"):
        run(vp.control._call("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "auto"}))
    assert _climate_calls(ha) == []


def test_off_keeps_frost_protection_and_schedule_returns_to_the_plan(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path, temp=20.0)
    app = _app(eng, vp)
    vstep(eng, loop, vp, T0)
    n = len(_climate_calls(ha))
    res = run(heat_actions.run(app, "mode", {"room": "climate.wz", "mode": "off"}))
    assert res["done"] == ["climate.wz"] and len(_climate_calls(ha)) == n  # nichts ans Thermostat
    vstep(eng, loop, vp, T0 + 600)
    st = vp.state["rooms"]["climate.wz"]
    assert st["want"] == 0 and st["why"].startswith("Aus (Frostschutz)")  # 20 °C über 7 °C: zu
    run(heat_actions.run(app, "schedule", {"rooms": ["climate.wz"]}))
    assert "own" not in st


def test_temp_holds_until_the_next_phase_and_boost_ends_by_itself(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    app = _app(eng, vp)
    vstep(eng, loop, vp, T0)
    run(heat_actions.run(app, "temp", {"room": "climate.wz", "temp": 23}))
    st = vp.state["rooms"]["climate.wz"]
    assert st["own"]["mode"] == "manuell" and st["own"]["sp"] == 23.0
    assert valve_modes.apply(st, 21.0, st["own"]["phase"], T0 + 60) == (23.0, "Manuell 23 °C")
    assert valve_modes.apply(st, 18.0, "nacht", T0 + 60) == (18.0, None) and "own" not in st  # Phasenwechsel
    run(heat_actions.run(app, "boost", {"rooms": ["climate.wz"], "temp": 24, "minutes": 30}))
    assert valve_modes.apply(st, 21.0, "komfort", int(st["own"]["since"]) + 60)[0] == 24.0
    assert valve_modes.apply(st, 21.0, "komfort", int(st["own"]["since"]) + 1800) == (21.0, None)


def test_mixed_rooms_split_between_own_modes_and_the_thermostat(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path, rooms=[vroom(), Room("Bad", "climate.bad", managed=True, comfort=21.0,
                                                                   platform="homekit_controller")])
    eng.states["climate.bad"] = {"state": "heat", "attributes": {"temperature": 20, "hvac_modes": ["off", "heat"]}}
    app = _app(eng, vp)
    res = run(heat_actions.run(app, "mode", {"rooms": ["climate.wz", "climate.bad"], "mode": "off"}))
    assert sorted(res["done"]) == ["climate.bad", "climate.wz"]
    assert [c[2] for c in _climate_calls(ha)] == ["climate.bad"]  # nur das normale Thermostat


def test_own_modes_need_heatpilot_in_control(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    eng.cfg.control_mode = "beobachten"
    with pytest.raises(ControlError, match="HeatPilot steuert"):
        run(heat_actions.run(_app(eng, vp), "mode", {"room": "climate.wz", "mode": "off"}))


# ------------------------------------------------------------------ Tado bleibt auf manuell (0.71)
def test_device_automation_is_never_left_standing(tmp_path):
    """Tado springt von selbst in seine Automatik zurück; stand dort zufällig dasselbe Soll, blieb es bisher dabei."""
    from test_control34 import MON, climate, make, step, ts_of

    eng, ha, _, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 17.0)
    step(eng, loop, ts_of(MON, 12))
    want = ha.calls[-1][3]["temperature"]
    climate(eng, "climate.wz", 20.0, want, mode="auto")  # Tado-Automatik mit demselben Soll
    n = len(ha.calls)
    step(eng, loop, ts_of(MON, 12) + 60)
    assert ("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "heat"}) in ha.calls[n:]


def test_boost_and_schedule_of_led_tado_rooms_avoid_the_tado_automation(tmp_path):
    from test_control34 import make

    tado = Room("Wz", "climate.wz", managed=True, comfort=21.0, platform="tado")
    eng, ha, ctl, loop = make(tmp_path, rooms=[tado])
    eng.states["climate.wz"] = {"state": "heat", "attributes": {"temperature": 20, "hvac_modes": ["off", "heat", "auto"]}}
    run(ctl.boost(["climate.wz"], 23, 30, own={"climate.wz"}))
    assert not [c for c in ha.calls if c[:2] == ("tado", "set_climate_timer")]
    assert ("climate", "set_temperature", "climate.wz", {"temperature": 23.0}) in ha.calls
    run(ctl.schedule(["climate.wz"], {"climate.wz": 21.0}))
    assert ("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "auto"}) not in ha.calls
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": 21.0})
    run(ctl.schedule(["climate.wz"]))  # nicht geführt (beobachten): wie bisher zurück in den Tado-Zeitplan
    assert ha.calls[-1] == ("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "auto"})
