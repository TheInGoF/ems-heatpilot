"""0.49.1: Kessel ohne Regler, der „Heizbetrieb aus“ nicht annimmt – steht er auf seinem Ruhewert (Vorlauf-Soll 7 °C),
heizt er ohnehin nicht: kein Schalten, kein Fehlschlag, keine Pause. Alte „fremd → 7 °C“ verschwinden."""
import time

from heatpilot import boiler_rule as br
from heatpilot.boiler_watch import forget_rest_drops

from test_boiler41 import FLOW, HEAT, T0, actions, calls, house
from test_control34 import climate, run

T = 1_800_000_000


def test_no_heating_off_while_the_boiler_rests():
    off_since = T - br.HEAT_OFF_S
    assert br.decide_heating(False, True, T, None, off_since=off_since).write                     # wie bisher
    d = br.decide_heating(False, True, T, None, off_since=off_since, resting=True)
    assert not d.write and "Ruhewert" in d.why
    assert br.decide_heating(False, True, T, None, protect="Grenze unter 35 °C", resting=True).write  # Schutz geht vor


def _run(eng, ha, bc, start, end, cold, last=None, drop_s=270):
    """Minuten ``start`` bis ``end`` im 30-s-Takt. Kessel ohne Regler: verwirft das Vorlauf-Soll ``drop_s`` nach dem
    letzten Schreiben (``last``, zeigt dann 7 °C) und nimmt „Heizbetrieb aus“ nicht an. ``cold(Minute)``: Raum braucht
    Wärme. Gibt den Zeitpunkt des letzten Vorlauf-Schreibens zurück."""
    for k in range(start * 60, end * 60, 30):
        t = T0 + k
        if cold(k // 60):
            climate(eng, "climate.wz", 20.0, 21.0, hvac_action="heating")
        else:
            climate(eng, "climate.wz", 21.8, 21.0, hvac_action="idle")
        if last is not None and t - last >= drop_s:
            eng.states[FLOW]["state"] = "7"
        eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 40.0}}, time.time()
        n = len(calls(ha))
        bc.plan(t)
        run(bc.apply(t))
        for c in calls(ha)[n:]:
            if c[2] == FLOW:
                eng.states[FLOW]["state"], last = str(c[3]["value"]), t
            elif c[2] == HEAT and c[1] == "turn_on":
                eng.states[HEAT]["state"] = "on"
    return last


def test_boiler_that_ignores_heating_off_is_not_paused(tmp_path):
    """Fall aus dem Haus: kein Raum braucht Wärme, der Kessel fällt auf 7 °C und lässt den Heizbetrieb an."""
    eng, ha, bc = house(tmp_path, boiler_keepalive=True, boiler_idle="aus")
    cold = lambda m: m < 40 or m >= 80  # noqa: E731
    last = _run(eng, ha, bc, 0, 75, cold)
    assert eng.states[FLOW]["state"] == "7" and "Ruhewert" in bc.view["decision"]["heating"]
    _run(eng, ha, bc, 75, 90, cold, last)
    acts = actions(eng)
    assert "heating_off" not in acts and "boiler_check_failed" not in acts and not bc.paused()
    assert eng.states[FLOW]["state"] == "50"  # wieder Bedarf: sofort neues Vorlauf-Soll, Heizbetrieb war ja an


def test_off_check_tolerates_a_resting_boiler(tmp_path):
    """„Aus“ geschrieben, solange der Kessel noch heizte – bis zur Prüfung ist er auf 7 °C gefallen: kein Fehlschlag."""
    eng, ha, bc = house(tmp_path, boiler_keepalive=True, boiler_idle="aus")
    bc.state["heating"] = {"check": {"ts": T0, "value": False, "due": T0 + 60, "until": T0 + 300}}
    eng.states[FLOW]["state"], eng.states[HEAT]["state"] = "7", "on"
    bc.verify(T0 + 60)
    assert not bc.state.get("fails") and not bc.state["heating"].get("check")
    bc.state["heating"] = {"check": {"ts": T0, "value": False, "due": T0 + 60, "until": T0 + 300}}
    eng.states[FLOW]["state"] = "46"  # heizt noch und bleibt an: echter Fehlschlag
    bc.verify(T0 + 60)
    assert bc.state.get("fails") == 1


def test_old_rest_drops_are_forgotten():
    st = {"foreign_last": {"ts": 100, "what": "flow", "from": 42.0, "to": 7.0}, "foreign": [50, 100]}
    forget_rest_drops(st)
    assert "foreign_last" not in st and st["foreign"] == []
    real = {"foreign_last": {"ts": 100, "what": "flow", "from": 42.0, "to": 55.0}, "foreign": [100]}
    forget_rest_drops(real)
    assert real["foreign_last"]["to"] == 55.0 and real["foreign"] == [100]
