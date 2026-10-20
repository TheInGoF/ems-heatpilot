"""0.46.7: Ist „Heizbetrieb aktiviert“ beim Kessel nur eine Rückmeldung (EMS-ESP: heatingactivated nicht gefunden bzw.
nicht schreibbar), sperrt das bei „Pumpe läuft weiter“ nicht mehr – HeatPilot führt den Kessel allein über das
Vorlauf-Soll und schaltet den Heizbetrieb nie. Bei „Heizbetrieb ganz aus“ sperrt es wie bisher."""
import time

from heatpilot import boiler_entity as be
from heatpilot import boiler_rule as br
from test_boiler41 import T0, calls, house, step


def _feedback_only(eng):
    # heatingactivated nur Rückmeldung (EMS-ESP: nicht schreibbar); seit 0.46.10 sperrt „nicht gefunden“ nicht mehr
    eng.ems_controls = {"selflowtemp": {"found": True, "writeable": True, "value": 30},
                        "heatingactivated": {"found": True, "writeable": False, "value": True}}
    eng.ems_controls_ts = time.time()


def test_feedback_only_heating_does_not_block_in_pump_mode(tmp_path):
    eng, ha, bc = house(tmp_path)
    _feedback_only(eng)
    assert not be.heat_usable(eng, bc.entities["heating"])
    assert be.write_problems(eng, bc.entities["flow"], bc.entities["heating"]) == []
    step(bc, T0)
    done = calls(ha)
    assert done and all(c[0] == "number" for c in done)  # Vorlauf-Soll geschrieben, Heizbetrieb nie geschaltet


def test_without_need_target_becomes_pump_not_off(tmp_path):
    eng, ha, bc = house(tmp_path)
    _feedback_only(eng)
    from heatpilot.boiler_flow import Target
    off = Target(False, None, "Fußboden-Grenze unter 35 °C")
    got = bc._no_switch(off)
    assert (got.heating, got.flow, got.idle) == (True, br.PUMP_FLOW, True) and "nur Rückmeldung" in got.reason


def test_full_off_mode_still_needs_the_switch(tmp_path):
    eng, ha, bc = house(tmp_path, boiler_idle="aus")
    _feedback_only(eng)
    problems = be.write_problems(eng, bc.entities["flow"], bc.entities["heating"])
    assert any("heatingactivated" in p for p in problems)


def test_boost_end_without_setpoint_uses_schedule_and_off_only_without_any(tmp_path):
    """0.46.8: Ende eines Boosts ohne Soll von vorher (HomeKit meldet ein ruhendes Tado als „aus“) → Soll laut Zeitplan,
    nie „auto“; „aus“ nur, wenn es weder Soll noch Zeitplan gibt."""
    import asyncio
    eng, ha, bc = house(tmp_path)
    ctl = bc.control if hasattr(bc, "control") else bc.ctl
    room = next(iter(ctl.groups))
    ctl.boosts[room] = {"temp": 23.0, "end": 0, "local": True, "before": None, "before_mode": "off",
                        "targets": ctl.groups[room].write_targets}
    ctl.target = lambda r: 23.0
    assert asyncio.run(ctl.end_boost(room, None, fallback=20.5))
    done = [(p["service"], p.get("service_data")) for p in ha.payloads if p["domain"] == "climate"]
    assert done[-1] == ("set_temperature", {"temperature": 20.5})
    assert not any(srv == "set_hvac_mode" and data.get("hvac_mode") in ("auto", "off") for srv, data in done)


def test_not_found_controls_do_not_block(tmp_path):
    """0.46.10: EMS-ESP beantwortet die Abfrage einzelner Werte nicht („nicht gefunden“), obwohl es sie gibt – das
    sperrt nicht; nur „ausdrücklich nicht schreibbar“ sperrt."""
    eng, ha, bc = house(tmp_path)
    eng.ems_controls, eng.ems_controls_ts = {}, time.time()  # nichts gefunden
    assert be.write_problems(eng, bc.entities["flow"], bc.entities["heating"]) == []
    eng.ems_controls = {"selflowtemp": {"found": True, "writeable": False}}
    assert any("selflowtemp nicht schreibbar" in p for p in be.write_problems(eng, bc.entities["flow"], None))


def test_open_valve_gets_heat_even_if_models_say_no_need(tmp_path):
    """0.47.2: Fordert ein Thermostat jetzt Wärme an (Ventil offen), liefert der Kessel mindestens 35 °C – auch wenn die
    Modelle (z. B. Nacht laut Plan, Raum am Soll) keinen Bedarf sehen."""
    from heatpilot.boiler_flow import compute
    from test_boiler41 import climate, slow_room
    eng, ha, bc = house(tmp_path)
    eng.rooms["climate.wz"] = slow_room(a=0.02, b=0.01)                 # braucht nur 29 °C – rechnerisch kein Bedarf
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="idle")
    assert compute(eng, T0)[0].idle                                     # Ventil zu: nur Pumpe
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="heating")       # Thermostat öffnet
    t = compute(eng, T0)[0]
    assert t.heating is True and not t.idle and t.flow >= br.FLOW_MIN and "Ventil offen" in t.reason
