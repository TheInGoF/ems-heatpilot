"""0.25: Zusatzdaten aus EMS-ESP – Sonderladungen, Ladezähler, nächste Ladung, Heizkurve, Anlage."""

from datetime import datetime, timedelta

from heatpilot.dhw import DhwModel
from heatpilot.flowstudy import curve_hint


def _charge(m: DhwModel, t0: datetime, start: float, special: bool) -> datetime:
    t = start
    for i in range(6):  # 30 min laden, +3 K je 5 min
        m.step(t0 + timedelta(minutes=5 * i), 5 / 60, t, t + 3, True, 12.0, special=special)
        t += 3
    m.step(t0 + timedelta(minutes=30), 5 / 60, t, t, False, None, special=special)
    return t0 + timedelta(minutes=35)


def test_special_charge_is_not_learned():
    m = DhwModel()
    _charge(m, datetime(2026, 1, 4, 13, 0), 50, special=True)
    assert m.charges == 0 and m.special_charges == 1 and m.kwh_per_k["kalt"] is None and m.kwh_per_k["warm"] is None
    _charge(m, datetime(2026, 1, 5, 7, 0), 45, special=False)
    assert m.charges == 1 and m.kwh_per_k["kalt"] is not None


def test_special_hour_does_not_teach_draw_profile():
    m = DhwModel()
    t0 = datetime(2026, 1, 4, 13, 0)
    m.step(t0, 5 / 60, 50, 50, False, None)
    m.step(t0 + timedelta(minutes=5), 5 / 60, 50, 40, False, None, special=True)  # Abfall während Sonderladung
    m.step(t0 + timedelta(minutes=10), 5 / 60, 40, 40, False, None)
    m.step(t0 + timedelta(hours=1, minutes=1), 5 / 60, 40, 40, False, None)  # Stunde rollt
    assert m.profile[6 * 24 + 13] == 0.0 and m.daily[13] == 0.0


def test_next_charge_reaches_trigger():
    m = DhwModel()
    now = datetime(2026, 1, 5, 12, 0)
    eta = m.next_charge(now, 50.0, 45.0)
    assert eta is not None and now < eta < now + timedelta(hours=48)
    assert m.next_charge(now, 50.0, 10.0, max_h=4) is None


def test_curve_hint_translates_flow_to_design_temp():
    c = curve_hint(60, 21, None, 0)
    assert c["min_ext_known"] is False and 40 < c["flow_now"] < 50
    assert 66 <= c["design_new"] <= 70  # +5 K Vorlauf bei 5 °C außen ≈ +8 K Auslegung
    assert curve_hint(None, 21, -10, 0) is None


def test_dhw_counter_marks_charge_start():
    from types import SimpleNamespace

    from heatpilot.engine import Engine

    eng = SimpleNamespace(_dhw_prev=None, dhw_flag_seen=False, dhw_inferred=0,
                          _boiler=lambda role: None)
    s1 = {"dhw": None, "dhw_temp": 50.0, "burner_on": 0, "dhw_starts": 100}
    Engine.infer_dhw(eng, 0, s1)
    s2 = {"dhw": None, "dhw_temp": 50.0, "burner_on": 1, "dhw_starts": 101}
    Engine.infer_dhw(eng, 120, s2)
    assert s1["dhw"] == 0 and s2["dhw"] == 1 and eng.dhw_counted == 1
