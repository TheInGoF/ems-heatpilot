"""0.57: Eine Warmwasser-Ladung schaltet am Kessel auch den Heizbetrieb an – das ist keine fremde Änderung und kein
Fehlschlag (Fall 27.10.: Ladung 03:05 bei „Heizbetrieb aus“ → fremde Änderungen → Pause → morgens keine Heizung).
Und das 30-s-Protokoll zeigt, was geschrieben wurde (Spalte ``hp_geschrieben``)."""
import time

from heatpilot import live_log
from test_boiler41 import FLOW, HEAT, T0, actions, calls
from test_boiler493 import DHW, _house
from test_control34 import climate, run


def _steps(eng, ha, bc, minutes, start=0, charge=None):
    for k in range(start * 60, (start + minutes) * 60, 30):
        t, m = T0 + k, k / 60
        climate(eng, "climate.wz", 21.5, 21.0, hvac_action="idle")  # kein Bedarf: Heizbetrieb aus
        on = charge is not None and charge[0] <= m < charge[1]
        eng.states[DHW]["state"] = "on" if on else "off"
        if on:  # der Kessel lädt: Heizbetrieb an, eigenes Lade-Soll – laut HA und EMS-ESP mal so, mal so
            eng.states[HEAT]["state"] = "on" if int(m * 2) % 2 == 0 else "off"
            eng.states[FLOW]["state"] = "75"
        eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 40.0}}, time.time()
        n = len(calls(ha))
        bc.plan(t)
        run(bc.apply(t))
        for c in calls(ha)[n:]:
            if c[2] == HEAT:
                eng.states[HEAT]["state"] = "on" if c[1] == "turn_on" else "off"
            elif c[2] == FLOW:
                eng.states[FLOW]["state"] = str(c[3]["value"])
        if charge is not None and m >= charge[1] and eng.states[FLOW]["state"] == "75":
            eng.states[FLOW]["state"], eng.states[HEAT]["state"] = "7", "off"  # nach der Ladung: Ruhe


def test_charge_switching_heating_on_is_no_foreign_change_and_no_pause(tmp_path):
    eng, ha, bc = _house(tmp_path)
    _steps(eng, ha, bc, 40)  # Heizbetrieb aus geschrieben
    assert eng.states[HEAT]["state"] == "off"
    _steps(eng, ha, bc, 30, start=40, charge=(45, 57))
    acts = actions(eng)
    assert "boiler_foreign" not in acts and "boiler_check_failed" not in acts and not bc.paused()


def test_csv_shows_what_was_written(tmp_path):
    eng, ha, bc = _house(tmp_path)
    for k in range(0, 300, 30):
        live_log.record(eng.store, T0 + k, {}, [])
    eng.store.add_control_log(T0 + 61, "boiler", "heat_off", None, "kein Raum braucht Wärme")
    text = live_log.csv_text(eng, now_ts=T0 + 300)
    head, *rows = text.splitlines()
    col = head.split(",").index("hp_geschrieben")
    hit = [r for r in rows if "heat_off" in r]
    assert len(hit) == 1 and hit[0].split(",")[1] == str(T0 + 90) and "kein Raum braucht Wärme" in hit[0]
    assert col > head.split(",").index("hp_vorlauf_entscheidung")
