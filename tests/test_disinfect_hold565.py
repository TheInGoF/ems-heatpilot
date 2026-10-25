"""0.56.5: Während einer Desinfektion schreibt Weg B nichts – auch nicht, wenn die Meldung beim Halten der Temperatur
zwischendurch wegfällt. Erst wenn der Brenner einmal aus war, gehört das Vorlauf-Soll wieder HeatPilot."""
import time

from test_boiler41 import FLOW, T0, actions, calls
from test_boiler493 import _house
from test_control34 import climate, run

DIS, BURN = "binary_sensor.disinfecting", "binary_sensor.burner"


def _setup(tmp_path):
    eng, ha, bc = _house(tmp_path)
    eng.found.boiler.update({"dhw_disinfecting": DIS, "burner_on": BURN})
    eng.states.update({DIS: {"state": "off", "attributes": {}}, BURN: {"state": "on", "attributes": {}},
                       "sensor.dhw_temp": {"state": "62.0", "attributes": {}}})  # Speicher über dem Soll (Desinfektion)
    return eng, ha, bc


def _run(eng, ha, bc, minutes, dis=(10, 12), heat=(10, 40)):
    """Desinfektion gemeldet nur ``dis``, der Kessel heizt aber mit 75 °C und Brenner an während ``heat``."""
    during, written = [], None
    for k in range(0, minutes * 60, 30):
        t, m = T0 + k, k / 60
        climate(eng, "climate.wz", 20.0, 21.0, hvac_action="heating")
        eng.states[DIS]["state"] = "on" if dis[0] <= m < dis[1] else "off"
        eng.states[BURN]["state"] = "on" if m < heat[1] else "off"
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


def test_no_write_until_the_burner_was_off_once(tmp_path):
    eng, ha, bc = _setup(tmp_path)
    assert _run(eng, ha, bc, 50) == []  # Meldung weg ab Minute 12, Brenner bis 40 an: nichts geschrieben
    acts = actions(eng)
    assert "boiler_check_failed" not in acts and not bc.paused()
    assert "special_ts" not in bc.state  # Brenner war aus: wieder frei
    assert acts.count("flow_set") > 3  # vorher und danach normal


def test_hold_ends_after_four_hours_without_burner_signal(tmp_path):
    eng, ha, bc = _setup(tmp_path)
    bc.state["special_ts"] = T0
    eng.states[BURN]["state"] = "on"
    assert bc._special_hold(T0 + 3600) and not bc._special_hold(T0 + 4 * 3600 + 1)


def test_short_press_between_two_samples_counts(tmp_path):  # 0.56.6: Desinfektion ist ein Taster
    eng, ha, bc = _setup(tmp_path)
    _run(eng, ha, bc, 5, dis=(99, 99), heat=(99, 99))  # vorher normal
    eng.on_state(DIS, {"state": "on", "attributes": {}})   # kurz gedrückt …
    eng.on_state(DIS, {"state": "off", "attributes": {}})  # … und gleich wieder aus, bevor HeatPilot abfragt
    assert bc.charging(time.time()) and "special_ts" in bc.state
    eng.states[FLOW]["state"] = "75"
    n = len(calls(ha))
    bc.plan(time.time())
    run(bc.apply(time.time()))
    assert not [c for c in calls(ha)[n:] if c[2] == FLOW]  # Brenner an: nichts geschrieben
    eng.states[BURN]["state"] = "off"
    eng.special_pressed_ts = 0
    later = time.time() + 200  # nach dem kurzen Nachlauf (DHW_AFTER_S)
    assert not bc.charging(later) and "special_ts" not in bc.state  # Brenner einmal aus: wieder frei
