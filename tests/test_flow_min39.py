"""Vorlauf mindestens 35 °C, darunter Heizbetrieb aus (Wunsch Ingo, 0.39).

„35 °C sollten beim Vorlauf die Untergrenze sein, wenn geheizt wird. Darunter braucht er gar nicht mehr versuchen
zu heizen.“ Geprüft an der Entscheidung der Vorschau (preview._Run._flow) mit erfundenen Bedarfen – ohne Testhaus.
"""

from types import SimpleNamespace

from heatpilot import preview as pv


def _run(max_flow=75):
    run = pv._Run.__new__(pv._Run)
    run.rooms = [SimpleNamespace(heats=True)]
    run.cfg = SimpleNamespace(max_flow_temp=max_flow)
    return run


def test_floor_is_35():
    assert pv.FLOW_MIN == 35.0


def test_needed_below_35_means_heating_off():
    flow, heating, why = _run()._flow([(28.4, "Wz", "hält 21 °C"), (31.0, "Küche", "hält 21 °C")], 5.0)
    assert flow is None and heating is False
    assert why == "nötiger Vorlauf unter 35 °C (Küche) – Heizbetrieb aus"


def test_needed_35_or_more_is_used_as_is():
    assert _run()._flow([(38.2, "Wz", "hält 21 °C"), (31.0, "Küche", "x")], 5.0)[:2] == (38, True)
    assert _run()._flow([(35.0, "Wz", "hält 21 °C")], 5.0)[:2] == (35, True)


def test_room_without_own_flow_keeps_heating_on_at_35():
    # Raum ohne plausibles Modell bzw. Fußboden unter Soll: Heizbetrieb bleibt an, mit der Untergrenze
    flow, heating, why = _run()._flow([(28.0, "Wz", "hält 21 °C"), (None, "Bad", "unter Soll – Heizbetrieb bleibt an")], 5.0)
    assert (flow, heating) == (35, True) and why.startswith("Bad")
    assert _run()._flow([(None, "Bad", "unter Soll")], 5.0)[:2] == (35, True)


def test_max_flow_still_caps():
    assert _run(max_flow=50)._flow([(62.0, "Wz", "heizt auf")], 5.0)[:2] == (50, True)
