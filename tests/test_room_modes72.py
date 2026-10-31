"""0.72: eigenes Climate je geführtem Raum (``room_modes``, für Ventilräume seit 0.71) – die Knöpfe wirken auf HeatPilots
Betriebsarten Zeitplan, Manuell, Aus (Frostschutz), Boost; ans Thermostat geht keine Betriebsart: Tado bekommt nur
Temperaturen und bleibt auf manuell, ein Ventil (Stufe 3, Shelly) nur die Öffnung."""

from types import SimpleNamespace

import pytest

from heatpilot import heat_actions, room_modes
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
    assert loop.rooms["climate.wz"]["desired"] == room_modes.FROST_C
    run(heat_actions.run(app, "schedule", {"rooms": ["climate.wz"]}))
    assert "own" not in loop.state["climate.wz"]


def test_temp_holds_until_the_next_phase_and_boost_ends_by_itself(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    app = _app(eng, vp)
    vstep(eng, loop, vp, T0)
    run(heat_actions.run(app, "temp", {"room": "climate.wz", "temp": 23}))
    st = loop.state["climate.wz"]
    assert st["own"]["mode"] == "manuell" and st["own"]["sp"] == 23.0
    assert room_modes.apply(st, 21.0, st["own"]["phase"], T0 + 60) == (23.0, "Manuell 23 °C")
    assert room_modes.apply(st, 18.0, "nacht", T0 + 60) == (18.0, None) and "own" not in st  # Phasenwechsel
    run(heat_actions.run(app, "boost", {"rooms": ["climate.wz"], "temp": 24, "minutes": 30}))
    assert room_modes.apply(st, 21.0, "komfort", int(st["own"]["since"]) + 60)[0] == 24.0
    assert room_modes.apply(st, 21.0, "komfort", int(st["own"]["since"]) + 1800) == (21.0, None)


def test_mixed_rooms_split_between_own_modes_and_the_thermostat(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path, rooms=[vroom(), Room("Bad", "climate.bad", managed=False, comfort=21.0,
                                                                   platform="homekit_controller")])  # macht nicht mit
    eng.states["climate.bad"] = {"state": "heat", "attributes": {"temperature": 20, "hvac_modes": ["off", "heat"]}}
    app = _app(eng, vp)
    res = run(heat_actions.run(app, "mode", {"rooms": ["climate.wz", "climate.bad"], "mode": "off"}))
    assert sorted(res["done"]) == ["climate.bad", "climate.wz"]
    assert [c[2] for c in _climate_calls(ha)] == ["climate.bad"]  # nur das Thermostat des nicht geführten Raums


def test_without_heatpilot_in_control_the_buttons_go_to_the_thermostat(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    eng.cfg.control_mode = "beobachten"
    assert room_modes.own_rooms(_app(eng, vp)) == set()


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


def test_tado_room_buttons_set_own_modes_and_only_temperatures_go_out(tmp_path):
    """Tado: Aus = 7 °C, Boost und Manuell als Temperatur, Zeitplan = HeatPilots Plan – nie Tados Automatik oder Timer."""
    from test_control34 import MON, make, step, ts_of

    tado = Room("Wz", "climate.wz", managed=True, comfort=21.0, platform="tado")
    eng, ha, ctl, loop = make(tmp_path, rooms=[tado])
    eng.states["climate.wz"] = {"state": "heat", "attributes": {"temperature": 17, "current_temperature": 20,
                                                                "hvac_modes": ["off", "heat", "auto"]}}
    app = SimpleNamespace(eng=eng, control=ctl)
    t = ts_of(MON, 12)
    step(eng, loop, t)
    plan = ha.calls[-1][3]["temperature"]
    for i, (action, body, want) in enumerate([("mode", {"mode": "off"}, room_modes.FROST_C),
                                               ("boost", {"temp": 24, "minutes": 30}, 24.0),
                                               ("mode", {"mode": "auto"}, plan), ("temp", {"temp": 22.5}, 22.5)]):
        run(heat_actions.run(app, action, {**body, "room": "climate.wz", "rooms": ["climate.wz"]}))
        step(eng, loop, t + 60 * (i + 1))
        assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": want}), action
    assert not [c for c in ha.calls if c[:2] == ("tado", "set_climate_timer") or c[3].get("hvac_mode") in ("auto", "off")]


def test_button_shows_at_once_not_only_after_the_next_tick(tmp_path):
    """Der Knopf „Manuell“ blieb in der App bis zum nächsten Takt ungefärbt – die Anzeige kam erst mit dem Plan."""
    from test_control34 import MON, climate, make, step, ts_of

    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 17.0)
    step(eng, loop, ts_of(MON, 12))
    plan = loop.rooms["climate.wz"]["desired"]
    app = SimpleNamespace(eng=eng, control=ctl)
    run(heat_actions.run(app, "temp", {"room": "climate.wz", "temp": 23.5}))
    assert loop.rooms["climate.wz"]["own"]["mode"] == "manuell" and loop.rooms["climate.wz"]["desired"] == 23.5
    run(heat_actions.run(app, "mode", {"room": "climate.wz", "mode": "off"}))
    assert loop.rooms["climate.wz"]["desired"] == room_modes.FROST_C
    run(heat_actions.run(app, "schedule", {"rooms": ["climate.wz"]}))
    assert loop.rooms["climate.wz"]["own"] is None and loop.rooms["climate.wz"]["desired"] == plan


def test_schedule_ends_an_old_control_boost_so_heatpilot_takes_over(tmp_path):
    """Ein Boost von vor 0.73 (Tado-Timer bzw. lokal in ``Control``) hielt die Regelschleife an – „Zeitplan“ räumte nur
    das eigene Climate auf, der Raum blieb im Boost."""
    from test_control34 import MON, climate, make, step, ts_of

    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 17.0)
    t = ts_of(MON, 12)
    step(eng, loop, t)
    plan = ha.calls[-1][3]["temperature"]
    eng.cfg.control_mode = "beobachten"
    run(ctl.boost(["climate.wz"], 24, 120))  # alter Boost über Control
    eng.cfg.control_mode = "heatpilot"
    climate(eng, "climate.wz", 20.0, 24.0)
    step(eng, loop, t + 60)
    assert ha.calls[-1][3] == {"temperature": 24.0}  # Boost läuft, HeatPilot hält sich raus
    run(heat_actions.run(SimpleNamespace(eng=eng, control=ctl), "schedule", {"rooms": ["climate.wz"]}))
    assert "climate.wz" not in ctl.boosts
    step(eng, loop, t + 120)
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": plan})


def test_own_boost_shows_in_the_boost_sensor_and_the_room(tmp_path):
    from heatpilot import card_bridge
    from test_control34 import MON, climate, make, step, ts_of

    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 17.0)
    step(eng, loop, ts_of(MON, 12))
    run(heat_actions.run(SimpleNamespace(eng=eng, control=ctl), "boost", {"rooms": ["climate.wz"], "temp": 24, "minutes": 60}))
    _, n, attrs = card_bridge.boost(eng, ctl, None)
    assert n == 1 and attrs["rooms"][0]["temp"] == 24.0 and 55 <= attrs["rooms"][0]["left_min"] <= 60
    room = next(a for _e, _s, a in card_bridge.rooms(eng, ctl) if a["climate"] == "climate.wz")
    assert room["boost"]["temp"] == 24.0


def test_schedule_after_own_boost_clears_every_boost_display(tmp_path):
    from heatpilot import card_bridge
    from test_control34 import MON, climate, make, step, ts_of

    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 17.0)
    step(eng, loop, ts_of(MON, 12))
    app = SimpleNamespace(eng=eng, control=ctl)
    run(heat_actions.run(app, "boost", {"rooms": ["climate.wz"], "temp": 24, "minutes": 60}))
    run(heat_actions.run(app, "schedule", {"rooms": ["climate.wz"]}))
    assert card_bridge.boost(eng, ctl, None)[1] == 0
    room = next(a for _e, _s, a in card_bridge.rooms(eng, ctl) if a["climate"] == "climate.wz")
    assert room["boost"] is None and room["own"] is None
