"""0.49.3: Warmwasser-Ladung ohne Regler – der Kessel setzt sein Soll selbst (z. B. 75 °C), oft bevor „lädt“ gemeldet
wird und noch kurz danach. Das ist kein Fehlschlag, kein Schutz-Schreiben und kein fremder Schreiber."""
import time

from test_boiler41 import FLOW, T0, actions, calls, house
from test_control34 import climate, run

DHW, TANK, TSET = "binary_sensor.dhw", "sensor.dhw_temp", "number.dhw_set"


def _house(tmp_path, **kw):
    eng, ha, bc = house(tmp_path, boiler_keepalive=True, boiler_idle="aus", **kw)
    eng.found.boiler.update({"dhw_active": DHW, "dhw_temp": TANK, "dhw_seltemp": TSET})
    eng.states.update({DHW: {"state": "off", "attributes": {}}, TANK: {"state": "45.0", "attributes": {}},
                       TSET: {"state": "50", "attributes": {}}})
    return eng, ha, bc


def _run(eng, ha, bc, minutes, charge=(10, 10.5, 20, 21)):
    """Kessel-Soll 75 °C von ``charge[0]`` bis ``charge[3]`` (Minuten), „lädt“ nur von ``charge[1]`` bis ``charge[2]``."""
    start, on, off, back = charge
    written, during = None, []
    for k in range(0, minutes * 60, 30):
        t, m = T0 + k, k / 60
        climate(eng, "climate.wz", 20.0, 21.0, hvac_action="heating")
        eng.states[DHW]["state"] = "on" if on <= m < off else "off"
        if start <= m < back:
            eng.states[FLOW]["state"] = "75"
        elif m >= back and written is not None:
            eng.states[FLOW]["state"] = str(written)
        eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 40.0}}, time.time()
        n = len(calls(ha))
        bc.plan(t)
        run(bc.apply(t))
        for c in calls(ha)[n:]:
            if c[2] == FLOW:
                written = c[3]["value"]
                if start <= m < back:
                    during.append((m, written))
                else:
                    eng.states[FLOW]["state"] = str(written)
    return during


def test_charge_set_by_the_boiler_is_no_failure_and_no_protection(tmp_path):
    eng, ha, bc = _house(tmp_path, max_flow_temp=65)  # 75 °C liegt über dem höchsten Vorlauf
    during = _run(eng, ha, bc, 30)
    acts = actions(eng)
    assert during == []  # kein Schutz-Schreiben, kein Erneuern während der Ladung
    assert "boiler_check_failed" not in acts and "boiler_foreign" not in acts and not bc.paused()
    assert acts.count("flow_set") > 10  # vorher und danach normal erneuert


def test_charge_below_the_highest_flow_is_recognised_by_the_jump(tmp_path):
    eng, ha, bc = _house(tmp_path)  # höchster Vorlauf 75: erkannt am Sprung über das eigene Soll, Speicher unter Soll
    assert _run(eng, ha, bc, 30) == []
    assert "boiler_check_failed" not in actions(eng)


def test_three_minutes_after_the_charge_it_writes_again(tmp_path):
    eng, ha, bc = _house(tmp_path)
    _run(eng, ha, bc, 30, charge=(10, 10.5, 20, 23.5))  # Kessel zeigt sein Lade-Soll noch 3,5 min nach „lädt“
    assert bc.state["dhw_ts"] == T0 + 19 * 60 + 30
    assert "boiler_check_failed" not in actions(eng)


def test_only_the_own_setpoint_is_renewed(tmp_path):
    """Ein Lade-Soll, das nach dem Fenster noch steht, wird nicht als „gehaltenes Soll“ erneuert."""
    eng, ha, bc = _house(tmp_path)
    eng.states[TANK]["state"] = "50.0"  # Speicher voll: kein Lade-Soll mehr erkennbar
    bc.state["flow"] = {"last": {"ts": T0 - 120, "value": 46, "from": 46}}
    eng.states[FLOW]["state"] = "70"
    climate(eng, "climate.wz", 20.0, 21.0, hvac_action="heating")
    eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 40.0}}, time.time()
    bc.plan(T0)
    assert not (bc.view["write"]["flow"] and bc.pending and bc.pending[0][1] == 70)


def test_a_foreign_writer_is_still_noticed_when_the_tank_is_full(tmp_path):
    eng, ha, bc = _house(tmp_path)
    eng.states[TANK]["state"] = "51.0"
    _run(eng, ha, bc, 3, charge=(99, 99, 99, 99))
    eng.states[FLOW]["state"] = "60"
    eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 40.0}}, time.time()
    assert not bc.charging(T0 + 300)  # Speicher voll: 60 °C ist kein Lade-Soll
    bc.plan(T0 + 300)
    acts = actions(eng)
    assert "boiler_foreign" in acts or "boiler_check_failed" in acts  # bemerkt (während einer Prüfung als Fehlschlag)
