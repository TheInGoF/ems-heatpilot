"""0.56.5: Während einer Desinfektion schreibt Weg B nichts – auch nicht, wenn die Meldung zwischendurch wegfällt. Erst
wenn das Vorlauf-Soll des Kessels wieder niedrig ist (0.56.8), gehört es wieder HeatPilot."""
import time

from test_boiler41 import FLOW, T0, actions, calls
from test_boiler493 import _house
from test_control34 import climate, run

DIS = "binary_sensor.disinfecting"


def _setup(tmp_path):
    eng, ha, bc = _house(tmp_path)
    eng.found.boiler.update({"dhw_disinfecting": DIS})
    eng.states.update({DIS: {"state": "off", "attributes": {}},
                       "sensor.dhw_temp": {"state": "62.0", "attributes": {}}})  # Speicher über dem Soll (Desinfektion)
    return eng, ha, bc


def _run(eng, ha, bc, minutes, dis=(10, 12), heat=(10, 40)):
    """Desinfektion gemeldet nur ``dis``, der Kessel heizt aber mit seinem Soll 75 °C während ``heat``."""
    during, written = [], None
    for k in range(0, minutes * 60, 30):
        t, m = T0 + k, k / 60
        climate(eng, "climate.wz", 20.0, 21.0, hvac_action="heating")
        eng.states[DIS]["state"] = "on" if dis[0] <= m < dis[1] else "off"
        if heat[0] <= m < heat[1]:
            eng.states[FLOW]["state"] = "75"
        elif m >= heat[1] and written is not None:
            eng.states[FLOW]["state"] = str(written)  # Kessel zeigt danach wieder das zuletzt geschriebene Soll
        eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 60.0}}, time.time()
        n = len(calls(ha))
        bc.plan(t)
        run(bc.apply(t))
        for c in calls(ha)[n:]:
            if c[2] == FLOW:
                written = c[3]["value"]
                if heat[0] <= m < heat[1]:
                    during.append((m, c[3]["value"]))
                else:
                    eng.states[FLOW]["state"] = str(c[3]["value"])
    return during


def test_no_write_until_the_boiler_setpoint_is_low_again(tmp_path):
    eng, ha, bc = _setup(tmp_path)
    assert _run(eng, ha, bc, 50) == []  # Meldung weg ab Minute 12, Kessel-Soll bis 40 hoch: nichts geschrieben
    acts = actions(eng)
    assert "boiler_check_failed" not in acts and not bc.paused()
    assert "special_ts" not in bc.state  # Kessel-Soll wieder niedrig: wieder frei
    assert acts.count("flow_set") > 3  # vorher und danach normal


def test_hold_ends_after_four_hours_at_the_latest(tmp_path):
    eng, ha, bc = _setup(tmp_path)
    bc.state["special_ts"] = T0
    eng.states[FLOW]["state"] = "80"  # Kessel-Soll bleibt hoch
    assert bc._special_hold(T0 + 3600) and not bc._special_hold(T0 + 4 * 3600 + 1)
