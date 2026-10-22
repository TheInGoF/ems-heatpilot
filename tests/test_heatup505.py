"""0.50.5: Vorheizen lernt je Raum (Geschwindigkeit, Abstand unter dem Ziel); ohne offenes Ventil sofort senken."""

from datetime import datetime, timezone

from heatpilot import heatup

T = 1_800_000_000
END = datetime.fromtimestamp(T + 3600, timezone.utc)


def _night(st, temps, sp=20.5):
    """Vorheizen ab T bis END (alle 5 min ein Wert), danach Komfort bis 30 min nach dem Ende, dann Abschluss."""
    for i, t in enumerate(temps):
        ts = T + i * 300
        heatup.track(st, "vorheizen" if ts < T + 3600 else "komfort", sp if ts < T + 3600 else 21.0, END, t, ts, False)
    heatup.track(st, "komfort", 21.0, None, temps[-1], T + 3600 + heatup.COAST_S, False)


def test_fast_room_with_overshoot_learns_speed_and_margin():
    st = {}
    assert heatup.tune(st) == (0.5, 1.0)
    _night(st, [19.0, 19.6, 20.2, 20.6, 21.0, 21.4, 21.7, 21.8, 21.8, 21.7, 21.6, 21.5, 21.4, 21.3, 21.2, 21.1, 21.0, 21.0, 21.0])
    m, s = heatup.tune(st)
    assert s > 1.2   # in 15 statt 60 min am abgesenkten Soll: schneller als gedacht → später beginnen
    assert m > 0.6   # Spitze 21,8 statt 21,0: mehr Abstand
    assert "pre" not in st


def test_slow_room_that_does_not_arrive_gets_slower_and_vacation_is_ignored():
    st = {}
    _night(st, [19.0] * 19)
    assert heatup.tune(st)[1] < 1.0 and heatup.tune(st)[0] == 0.5
    st2 = {}
    heatup.track(st2, "vorheizen", 20.5, END, 19.0, T, True)  # Urlaubsende: sanft, nicht lernen
    assert st2 == {}


def test_no_open_valve_lowers_at_once(tmp_path):
    from test_boiler41 import FLOW, T0, calls, house, shows, step
    from test_control34 import climate

    eng, ha, bc = house(tmp_path)
    step(bc, T0)
    shows(eng, flow=46)
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="idle")  # warm, Ventil zu
    step(bc, T0 + 90)
    assert [c[3]["value"] for c in calls(ha) if c[2] == FLOW] == [46, 41]
