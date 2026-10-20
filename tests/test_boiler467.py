"""0.46.7: Ist „Heizbetrieb aktiviert“ beim Kessel nur eine Rückmeldung (EMS-ESP: heatingactivated nicht gefunden bzw.
nicht schreibbar), sperrt das bei „Pumpe läuft weiter“ nicht mehr – HeatPilot führt den Kessel allein über das
Vorlauf-Soll und schaltet den Heizbetrieb nie. Bei „Heizbetrieb ganz aus“ sperrt es wie bisher."""
import time

from heatpilot import boiler_entity as be
from heatpilot import boiler_rule as br
from test_boiler41 import T0, house, step


def _feedback_only(eng):
    eng.ems_controls = {"selflowtemp": {"found": True, "writeable": True, "value": 30}}  # heatingactivated fehlt
    eng.ems_controls_ts = time.time()


def test_feedback_only_heating_does_not_block_in_pump_mode(tmp_path):
    eng, ha, bc = house(tmp_path)
    _feedback_only(eng)
    assert not be.heat_usable(eng, bc.entities["heating"])
    assert be.write_problems(eng, bc.entities["flow"], bc.entities["heating"]) == []
    step(bc, T0)
    services = [c for c in ha.calls if isinstance(c, tuple)] if hasattr(ha, "calls") else []
    assert not any("switch" in str(c) for c in services)  # Heizbetrieb nie geschaltet


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
