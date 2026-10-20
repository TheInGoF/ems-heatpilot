"""0.32: Warmwasser – Ladung mit Nachlauf, Zapfungen über ein Zeitfenster, Reserve je Ereignis,
Soll-Zeitprogramm für die nächste Ladung, Ersparnis über denselben Zeitraum, mittlere Brennerleistung."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from heatpilot import engine as engine_mod
from heatpilot.config import Config
from heatpilot.dhw import DhwModel, DhwShadow
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

STEP = 5 / 60


def _engine() -> Engine:
    cfg = Config()
    cfg.boiler_nominal_kw = 18.0
    return Engine(cfg, Store(Path(tempfile.mkdtemp()) / "t.db"), Discovered())


def _model(cool_a=0.8, cool_b=-0.04) -> DhwModel:
    m = DhwModel()
    m.loss.theta = [cool_a, cool_b]  # 50 °C: 1,2 K/h
    return m


def _cool(m: DhwModel, t0: datetime, t: float, minutes: int, step_s: int, exact: list | None = None):
    """Ruhiges Auskühlen mit 0,1-K-Fühlerauflösung; gibt (Zeit, gerundete Temperatur, exakte) zurück."""
    dt_h = step_s / 3600
    exact = [t] if exact is None else exact
    shown, draws = round(exact[0], 1), 0.0
    now = t0
    for _ in range(int(minutes * 60 / step_s)):
        now += timedelta(seconds=step_s)
        exact[0] -= m.cooling_kh(exact[0]) * dt_h
        new = round(exact[0], 1)
        draws += m.step(now, dt_h, shown, new, False, None)["draw_k"]
        shown = new
    return now, shown, draws


# ---------------------------------------------------------------------------- 1. Ladung mit Nachlauf
def _charge_with_tail(m: DhwModel, t0: datetime, tail=(1.0, 0.8, 0.2), heating_kw=6.0, **kw):
    """Ladung 45 → 54 °C (3 Schritte, 12 kW), danach steigt der Speicher ohne Ladesignal weiter."""
    temps = [45.0, 48.0, 51.0, 54.0]
    for i in range(1, 4):
        m.step(t0 + timedelta(minutes=5 * i), STEP, temps[i - 1], temps[i], True, 12.0, dhw_set=55.0, **kw)
    t, now = 54.0, t0 + timedelta(minutes=15)
    for rise in tail:  # Nachlauf – der Brenner läuft hier für den Heizkreis
        now += timedelta(minutes=5)
        m.step(now, STEP, t, t + rise, False, heating_kw, dhw_set=55.0)
        t += rise
    now += timedelta(minutes=5)
    m.step(now, STEP, t, t, False, heating_kw, dhw_set=55.0)  # kein Anstieg mehr: Ladung fertig
    return t


def test_charge_cost_counts_the_rise_after_the_charge_signal():
    m = _model()
    peak = _charge_with_tail(m, datetime(2026, 10, 15, 8, 40))
    kwh = 3 * 12.0 * STEP  # nur die Ladeschritte, nicht der Heizkreis im Nachlauf
    assert m.kwh_per_k_n["kalt"] == 1
    assert m.kwh_per_k["kalt"] == pytest.approx(kwh / (peak - 45.0))  # voller Anstieg 45 → 56 °C
    # Ladegeschwindigkeit bis zum Gipfel: 3 Ladeschritte + 3 Schritte Nachlauf (0.32b)
    assert m.charge_rate_kh == pytest.approx(20.0 + 0.2 * ((peak - 45.0) / 0.5 - 20.0))


def test_no_cooling_learned_and_no_burner_energy_during_tail():
    m = _model()
    before = m.loss.updates
    t0 = datetime(2026, 10, 15, 8, 40)
    temps = [45.0, 48.0, 51.0, 54.0]
    for i in range(1, 4):
        m.step(t0 + timedelta(minutes=5 * i), STEP, temps[i - 1], temps[i], True, 12.0)
    res = m.step(t0 + timedelta(minutes=20), STEP, 54.0, 55.0, False, 6.0)
    assert res["charge_kwh"] == 0.0 and m._charge is not None  # Nachlauf, Ladung noch offen
    assert m.loss.updates == before


def test_draw_in_tail_closes_charge_and_is_booked():
    m = _model()
    t0 = datetime(2026, 10, 15, 8, 40)
    temps = [45.0, 48.0, 51.0, 54.0]
    for i in range(1, 4):
        m.step(t0 + timedelta(minutes=5 * i), STEP, temps[i - 1], temps[i], True, 12.0)
    m.step(t0 + timedelta(minutes=20), STEP, 54.0, 55.5, False, None)  # Nachlauf
    res = m.step(t0 + timedelta(minutes=25), STEP, 55.5, 50.0, False, None)  # Dusche
    assert m._charge is None and m.charges == 1
    assert res["draw_k"] > 5.0
    assert m.kwh_per_k["kalt"] == pytest.approx(3 * 12.0 * STEP / (55.5 - 45.0))


def test_charge_resumed_in_tail_is_one_charge():
    m = _model()
    t0 = datetime(2026, 10, 15, 8, 40)
    m.step(t0, STEP, 45.0, 48.0, True, 12.0)
    m.step(t0 + timedelta(minutes=5), STEP, 48.0, 49.0, False, None)   # Signal kurz weg, steigt noch
    m.step(t0 + timedelta(minutes=10), STEP, 49.0, 52.0, True, 12.0)   # lädt weiter
    m.step(t0 + timedelta(minutes=15), STEP, 52.0, 52.0, False, None)
    assert m.charges == 1
    assert m.kwh_per_k["kalt"] == pytest.approx(2 * 12.0 * STEP / 7.0)


def test_tail_ends_after_after_charge_h():
    m = _model()
    t0 = datetime(2026, 10, 15, 8, 40)
    m.step(t0, STEP, 45.0, 50.0, True, 12.0)
    t = 50.0
    for i in range(1, 9):  # steigt 40 min weiter (Signal fehlt): nach 30 min ist Schluss
        m.step(t0 + timedelta(minutes=5 * i), STEP, t, t + 0.5, False, None)
        t += 0.5
        if i == 6:
            assert m._charge is not None
    assert m._charge is None and m.charges == 1


# ---------------------------------------------------------------------------- 6. mittlere Brennerleistung
def test_mean_burner_power_for_draw_and_charge_energy():
    m = _model()
    m.kwh_per_k["kalt"], m.kwh_per_k_n["kalt"] = 0.2, 5
    t0 = datetime(2026, 10, 11, 18, 40)
    # Brenner startet im selben Schritt, in dem die Wanne den Speicher abkühlt: nur die halbe Leistung
    res = m.step(t0, STEP, 70.0, 40.0, True, 18.0, burner_kw_prev=0.0)
    heat_k = 9.0 * STEP / 0.2
    assert res["draw_k"] == pytest.approx(30.0 + heat_k - m.cooling_kh(70.0) * STEP)
    assert res["charge_kwh"] == pytest.approx(9.0 * STEP)


def test_mean_power_closes_trapezoid_at_charge_end():
    """Beide Enden 0 %: Trapez mit Abschluss = Rechteck am Schrittende (Energie vollständig)."""
    powers = [10.0, 7.0, 6.0]
    m = _model()
    t0 = datetime(2026, 10, 15, 8, 40)
    prev, t, total = 0.0, 45.0, 0.0
    for i, p in enumerate(powers):
        total += m.step(t0 + timedelta(minutes=5 * i), STEP, t, t + 3, True, p, burner_kw_prev=prev)["charge_kwh"]
        prev, t = p, t + 3
    # nach dem Signal: Brenner läuft für den Heizkreis (5 kW) – zählt nicht
    total += m.step(t0 + timedelta(minutes=15), STEP, t, t + 1.0, False, 5.0, burner_kw_prev=prev)["charge_kwh"]
    total += m.step(t0 + timedelta(minutes=20), STEP, t + 1, t + 1, False, 5.0, burner_kw_prev=5.0)["charge_kwh"]
    assert total == pytest.approx(sum(powers) * STEP)
    assert m.kwh_per_k["kalt"] == pytest.approx(sum(powers) * STEP / 10.0)


def test_engine_passes_previous_burner_power():
    eng = _engine()
    t0 = int(datetime(2026, 10, 5, 4, 0).timestamp())
    temps = [44.0, 44.0, 47.5, 51.0, 54.5, 56.0, 56.0, 55.9]
    for i, temp in enumerate(temps):
        on = 2 <= i <= 4
        s = {"dhw_temp": temp, "burner_on": int(on), "burner_pow": 60.0 if on else 0.0,
             "flow": 70.0 if on else 40.0, "dhw": 1 if on else 0}
        eng.dhw_step(t0 + i * 300, s)
    kwh = 3 * 0.6 * 18.0 * STEP  # Trapez 0 → 60 → 60 → 60 → 0 % = 3 volle Schritte
    assert eng.dhw.kwh_per_k["kalt"] == pytest.approx(kwh / (56.0 - 44.0))


# ---------------------------------------------------------------------------- 2. Zapfungen über ein Zeitfenster
def _gradual(m: DhwModel, t0: datetime, t: float, step_s: int, drops: list[float]):
    now, total = t0, 0.0
    for d in drops:
        now += timedelta(seconds=step_s)
        total += m.step(now, step_s / 3600, t, round(t - d, 1), False, None)["draw_k"]
        t = round(t - d, 1)
    return now, t, total


@pytest.mark.parametrize("step_s,drops", [(300, [0.5] * 4), (120, [0.2] * 10)])
def test_gradual_shower_is_a_draw_in_the_profile(step_s, drops):
    m = _model(0.24, -0.015)  # langsames Auskühlen ≈ 0,53 K/h
    t0 = datetime(2026, 10, 12, 5, 0)
    now, t, _ = _cool(m, t0 - timedelta(hours=1), 52.0, 60, step_s)
    now, t, total = _gradual(m, now, t, step_s, drops)
    now, t, rest = _cool(m, now, t, 30, step_s, exact=[t])
    assert 1.6 <= total + rest <= 2.3
    hour = m.daily[5]
    for _ in range(2):  # Stunde wird eine Stunde später endgültig gelernt
        now, t, _ = _cool(m, now, t, 60, step_s, exact=[t])
    assert m.daily[5] > hour and m.hit_daily[5] > 0  # ≥ HIT_K: zählt als Stunde mit Zapfung


@pytest.mark.parametrize("step_s", [60, 120, 300, 900])
def test_quiet_cooling_with_quantisation_is_no_draw(step_s):
    m = _model()
    _, _, draws = _cool(m, datetime(2026, 10, 12, 0, 0), 58.0, 12 * 60, step_s)
    assert draws == 0.0
    assert m.loss.updates >= 20  # und gelernt wird trotzdem (keine einseitige Auswahl)
    assert abs(m.cooling_kh(50.0) - 1.2) < 0.15


def test_draw_crossing_the_hour_is_booked_to_its_start_hour():
    m = _model(0.24, -0.015)
    t0 = datetime(2026, 10, 11, 11, 0)
    now, t, _ = _cool(m, t0, 52.0, 50, 300)  # bis 11:50
    now, t, _ = _gradual(m, now, t, 300, [0.5, 0.5, 0.5, 0.5])  # 11:55 … 12:10
    for _ in range(3):
        now, t, _ = _cool(m, now, t, 60, 300, exact=[t])
    assert m.daily[11] > 0.1 and m.daily[12] < 0.05


def test_step_drop_still_counts_immediately():
    m = _model()
    t0 = datetime(2026, 10, 11, 7, 0)
    _cool(m, t0 - timedelta(hours=1), 55.0, 60, 300)
    res = m.step(t0 + timedelta(minutes=5), STEP, 54.0, 40.0, False, None)
    assert res["draw_k"] == pytest.approx(14.0 - m.cooling_kh(54.0) * STEP, abs=0.2)


def test_segment_with_small_draw_is_not_learned_but_noise_steps_are():
    m = _model()
    t0 = datetime(2026, 10, 12, 0, 0)
    now, t, _ = _cool(m, t0, 55.0, 60, 300)
    before = m.loss.updates
    # 30 min mit 0,6 K Kleinstzapfung (Händewaschen): unter der Zapfschwelle, aber kein Auskühlen
    for d in (0.1, 0.2, 0.4, 0.2, 0.1, 0.1):
        now += timedelta(minutes=5)
        m.step(now, STEP, t, round(t - d, 1), False, None)
        t = round(t - d, 1)
    assert m.loss.updates == before
    # einzelne −0,2-K-Stufen im ruhigen Auskühlen verwerfen den Abschnitt nicht
    for d in (0.2, 0.0, 0.1, 0.2, 0.0, 0.1):
        now += timedelta(minutes=5)
        m.step(now, STEP, t, round(t - d, 1), False, None)
        t = round(t - d, 1)
    assert m.loss.updates == before + 1


def test_dhw_points_use_the_same_draw_window():
    eng = _engine()
    true = DhwModel()  # Auskühlen wie das (Start-)Modell der Engine
    t0 = int(datetime(2026, 10, 1, 0, 0).timestamp())
    ts, exact, shown = t0, 55.0, 55.0
    for _ in range(12):  # alle 6 h nachladen, je Zyklus eine allmähliche Dusche
        for i in range(72):
            ts += 300
            flag = 0
            if i < 2:
                exact, flag = 55.0, 1
            else:
                exact -= true.cooling_kh(exact) * STEP
                if 30 <= i < 34:
                    exact -= 0.5
            shown = round(exact, 1)
            eng.store.add_sample(ts, {"dhw_temp": shown, "dhw": flag})
    pts = eng.dhw_points(days=5, now_ts=ts)
    assert pts and all(p["kh"] <= true.cooling_kh(p["t"]) + 0.3 for p in pts), pts


# ---------------------------------------------------------------------------- 3. Reserve je Ereignis
def test_bath_over_two_steps_is_one_cold_event():
    m = _model()
    sh = DhwShadow(t_sim=40.0)
    out = [sh.step(m, 40.0, 40.0, k, STEP, 50.0, dhw_min=30.0) for k in (30.0, 8.0)]
    assert [o["cold_draw"] for o in out] == [1, 0]
    assert [o["raise_margin"] for o in out] == [1, 0]
    for _ in range(7):  # 35 min später: neues Ereignis
        sh.step(m, 40.0, 40.0, 0.0, STEP, 50.0, dhw_min=30.0)
    assert sh.step(m, 40.0, 40.0, 3.0, STEP, 50.0, dhw_min=30.0)["cold_draw"] == 1


def test_unbound_or_small_draw_does_not_raise_reserve():
    m = _model()
    sh = DhwShadow(t_sim=36.0)
    res = sh.step(m, 36.0, 36.0, 2.0, STEP, 50.0, dhw_min=30.0, margin_bound=False)
    assert res["cold_draw"] == 1 and res["raise_margin"] == 0  # zählt als Kennzahl, Reserve bleibt
    sh = DhwShadow(t_sim=36.0)
    res = sh.step(m, 36.0, 36.0, 2.0, STEP, 50.0, dhw_min=30.0, margin_bound=True)
    assert res["raise_margin"] == 1  # unerwartet, aber mehr Reserve hätte das Ziel angehoben (0.32c)
    sh = DhwShadow(t_sim=36.0)
    res = sh.step(m, 36.0, 36.0, 0.5, STEP, 50.0, dhw_min=30.0)
    assert res["cold_draw"] == 0  # Händewaschen < HIT_K
    sh = DhwShadow(t_sim=52.0)
    res = sh.step(m, 52.0, 52.0, 25.0, STEP, 50.0, dhw_min=30.0)
    assert res["cold_draw"] == 1  # warm genug gestartet, aber die Wanne zieht unter das Minimum


def test_recommend_says_whether_a_draw_is_expected():
    m = DhwModel()
    cfg = Config()
    m.hit_daily[18] = 0.3
    assert m.recommend(datetime(2026, 10, 18, 17, 30), True, None, cfg)["expects_draw"] is True
    assert m.recommend(datetime(2026, 10, 18, 3, 0), True, None, cfg)["expects_draw"] is False


def _cold_draw_through_engine(vacation: int, expect: bool, home: int = 1) -> Engine:
    eng = _engine()
    eng.dhw.kwh_per_k, eng.dhw.kwh_per_k_n = {"kalt": 0.2, "warm": None}, {"kalt": 10, "warm": 0}
    eng.dhw.charges, eng.dhw.loss.updates = 10, 500
    if expect:
        eng.dhw.hit_daily = [0.5] * 24  # Strategie erwartet immer eine Zapfung
    t0 = int(datetime(2026, 10, 12, 7, 0).timestamp())
    eng.dhw_shadow.t_sim = 35.0  # Schatten kalt
    for i, temp in enumerate([45.0, 45.0, 25.0, 25.0]):  # 20 K: auch mit 5 K Hysterese im Schatten zu kalt (0.46)
        eng.dhw_step(t0 + i * 300, {"dhw_temp": temp, "dhw": 0, "vacation": vacation, "home_count": home})
    return eng


def test_reserve_rises_when_it_lifts_the_target_and_never_on_vacation():
    assert _cold_draw_through_engine(0, expect=True).dhw.comfort_margin > 1.5
    # keine Zapfung erwartet, jemand zu Hause und wach: seit 0.46 Komfort + Reserve – die Reserve hebt es an
    assert _cold_draw_through_engine(0, expect=False).dhw.comfort_margin > 1.5
    assert _cold_draw_through_engine(0, expect=False, home=0).dhw.comfort_margin == 0.0  # niemand da
    assert _cold_draw_through_engine(1, expect=True).dhw.comfort_margin == 0.0


# ---------------------------------------------------------------------------- 4. Nächste Ladung mit Zeitprogramm
def _night_setback(when: datetime) -> float:
    minutes = when.hour * 60 + when.minute
    return (55.0 if 4 * 60 + 35 <= minutes < 21 * 60 + 5 else 45.0) - 10.0


def test_next_charge_follows_setpoint_schedule():
    m = DhwModel()
    m.loss.theta = [0.24, -0.015]  # ≈ 0,5 K/h
    eta = m.next_charge(datetime(2026, 10, 16, 22, 0), 47.0, _night_setback)
    assert eta is not None and (eta.day, eta.hour, eta.minute) == (17, 4, 35)  # Sollsprung lädt sofort
    eta = m.next_charge(datetime(2026, 10, 16, 4, 30), 46.0, _night_setback)
    assert eta is not None and (eta - datetime(2026, 10, 16, 4, 35)) > timedelta(minutes=30)  # 46 > 45: keine Ladung
    assert m.next_charge(datetime(2026, 1, 5, 12, 0), 50.0, 45.0) is not None  # Zahl wie bisher


def test_engine_learns_setpoint_schedule_and_uses_it(monkeypatch):
    eng = _engine()
    start = datetime(2026, 10, 3, 0, 0)
    for i in range(14 * 288):
        when = start + timedelta(minutes=5 * i)
        set_t = _night_setback(when) + 10.0
        if i == 1000:
            set_t = 40.0  # einzelner Ausreißer
        eng.store.add_sample(int(when.timestamp()), {"dhw_set": set_t, "dhw_temp": 50.0})
    now = datetime(2026, 10, 16, 22, 0)
    monkeypatch.setattr(engine_mod.time, "time", lambda: now.timestamp())
    prof = eng.dhw_set_profile()
    assert prof[4 * 12 + 7] == 55.0 and prof[4 * 12 + 6] == 45.0 and prof[21 * 12] == 55.0 and prof[21 * 12 + 1] == 45.0
    assert 40.0 not in prof
    eng.dhw.loss.theta = [0.24, -0.015]
    eng.ems = {"boiler": {"dhw_seltemp": 45.0, "dhw_hyston": -10.0, "dhw_curtemp": 47.0}}
    nc = eng.dhw_next_charge(47.0)
    assert nc["trigger"] == 35.0 and nc["by_schedule"] and nc["trigger_at_eta"] == 45.0
    eta = datetime.fromisoformat(nc["eta"])
    assert (eta.hour, eta.minute) == (4, 35)


# ---------------------------------------------------------------------------- 5. Ersparnis über denselben Zeitraum
def test_savings_use_the_same_period_for_charge_and_losses():
    eng = _engine()
    add = eng.store.add_daily
    add("2026-09-20", "dhw_charge_kwh", 5.3)   # Legionellenladung, Ladekosten noch nicht gelernt
    add("2026-09-27", "dhw_charge_kwh", 5.2)
    for day in ("2026-10-03", "2026-10-04"):    # alte Tage: Verluste ohne Vergleichsschlüssel
        add(day, "dhw_charge_kwh", 2.0)
        add(day, "dhw_loss_kwh", 3.0)
        add(day, "dhw_loss_strategy_kwh", 2.0)
    add("2026-10-05", "dhw_charge_kwh", 4.0)    # neu: c erst mittags gelernt
    add("2026-10-05", "dhw_charge_cmp_kwh", 1.0)
    add("2026-10-05", "dhw_loss_kwh", 1.0)
    add("2026-10-05", "dhw_loss_strategy_kwh", 0.5)
    s = eng.dhw_summary()
    assert s["period_days"] == 3
    assert s["charge_kwh"] == pytest.approx(5.0) and s["charge_total_kwh"] == pytest.approx(18.5)
    assert s["saved_pct"] == pytest.approx(100 * 2.5 / 5.0)


def test_engine_writes_comparable_charge_only_with_known_cost():
    eng = _engine()
    t0 = int(datetime(2026, 10, 5, 4, 0).timestamp())
    for i, (temp, on) in enumerate([(44.0, 0), (47.0, 1), (50.0, 1), (50.0, 0)]):
        eng.dhw_step(t0 + i * 300, {"dhw_temp": temp, "burner_pow": 60.0 if on else 0.0, "dhw": on})
    day = eng.store.daily("2026-10-05").get("2026-10-05", {})
    # erste Ladung: c erst an ihrem Ende bekannt – nur der Abschluss-Schritt zählt mit
    assert day["dhw_charge_cmp_kwh"] < 0.5 * day["dhw_charge_kwh"]
    assert eng.dhw.kwh_per_kelvin() is not None


# ---------------------------------------------------------------------------- 7. Datenstand
def test_data_version_bumped_for_relearning():
    from heatpilot import main

    assert main.DATA_VERSION >= 12
