"""0.32 Gegenprüfung Warmwasser: Sicherung mit big_draws, Nachlauf über ein Zeitfenster, Zapf-Fenster
auch bei 900 s 20 min, Sonderwärme entschuldigt nur warm begonnene Zapfungen, Reserve auch aus dem
Boden „keine Zapfung erwartet“, Fühlerstufe aus den Messwerten, Ladebeginn als Rampe ab 0."""

import math
import random
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from heatpilot.config import Config
from heatpilot.dhw import (
    DRAW_OPEN_K, DRAW_WIN_K, HIT_K, SEG_EXCESS_KH, SPECIAL_COVER_H, TAIL_WIN_H,
    DhwModel, DhwShadow, sensor_step_of, tail_rising,
)
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

STEP = 5 / 60


def _cfg(dhw_min=30.0, comfort=50.0, dhw_max=60.0) -> Config:
    cfg = Config()
    cfg.dhw_min_temp, cfg.dhw_comfort_temp, cfg.dhw_max_temp = dhw_min, comfort, dhw_max
    return cfg


def _model() -> DhwModel:
    m = DhwModel()
    m.loss.theta = [0.8, -0.04]  # 50 °C: 1,2 K/h
    return m


# ---------------------------------------------------------------------------- logik:1 big_draws in der Sicherung
def test_big_draw_before_leaving_is_not_doubled_by_the_backup():
    """Sonntag 09:00 Wanne, 10:20 fahren alle ab, nach 72 h „alle lange weg“: Die Stunde 9 wird live
    gelernt und beim Zurückspielen noch einmal – big_draws muss dabei mit zurückgesetzt werden, sonst
    gilt die eine Wanne als wiederkehrend und die Strategie empfiehlt jeden Sonntag höher zu laden."""
    m = _model()
    now, t = datetime(2026, 9, 6, 0, 0).astimezone(), 50.0  # Sonntag

    def adv(extra=0.0, home=1, away_long=False):
        nonlocal now, t
        m.note_household(home, away_long)
        now += timedelta(seconds=300)
        new = t - m.cooling_kh(t) * STEP - extra
        if new < 40:  # nachladen
            m.step(now, STEP, t, 50.0, True, 12.0, dhw_set=55.0, learn_usage=not away_long)
            t = 50.0
            return
        m.step(now, STEP, t, new, False, None, dhw_set=55.0, learn_usage=not away_long)
        t = new

    while now < datetime(2026, 9, 27, 9, 0).astimezone():
        adv()
    for _ in range(6):  # 09:00–09:30 Wanne 12 K
        adv(2.0)
    while now < datetime(2026, 9, 27, 10, 20).astimezone():
        adv()
    departure = now
    while now < departure + timedelta(hours=72):
        adv(home=0)
    assert len(m.big_draws) == 1
    while now < departure + timedelta(hours=100):
        adv(home=0, away_long=True)
    assert len(m.big_draws) == 1, m.big_draws
    sunday = datetime(2026, 10, 4, 7, 30).astimezone()
    assert m.big_draw_expected(sunday, 3) is None
    assert not m.recommend(sunday, True, None, Config())["reason"].startswith("große Zapfung")


def test_backup_with_big_draws_survives_a_restart():
    m = DhwModel()
    m.big_draws = [["2026-09-27T09:00", 10.0, False]]
    m.note_household(0, False)
    again = DhwModel.from_dict(m.to_dict())
    assert again._backup is not None and again._backup["big_draws"] == m.big_draws
    assert DhwModel.from_dict({**m.to_dict(), "backup": {"profile": []}})._backup is None  # alte Sicherung ohne Schlüssel


# ---------------------------------------------------------------------------- logik:2 Nachlauf über ein Zeitfenster
def _charge_with_slow_tail(step_s: int, tail_k: float = 2.0, tau_min: float = 15.0):
    """Ladung 45 → 54 °C mit 3 kWh (15 min), danach Nachlauf tail_k·(1 − e^(−t/τ)) bei 0,1-K-Stufen."""
    m = _model()
    now, dt_h, shown = datetime(2026, 10, 12, 10, 0), step_s / 3600, 45.0
    n = int(900 / step_s)
    for i in range(n):
        now += timedelta(seconds=step_s)
        new = round(45.0 + 9.0 * (i + 1) / n, 1)
        m.step(now, dt_h, shown, new, True, 12.0, dhw_set=55.0)
        shown = new
    for i in range(1, int(5400 / step_s)):
        now += timedelta(seconds=step_s)
        minutes = i * step_s / 60
        new = round(54.0 + tail_k * (1 - math.exp(-minutes / tau_min)) - 0.01 * minutes, 1)
        m.step(now, dt_h, shown, new, False, None, dhw_set=55.0)
        shown = new
    assert m.charges == 1
    return m.kwh_per_k["kalt"], m.charge_rate_kh


def test_charge_cost_with_slow_tail_does_not_depend_on_sample_rate():
    """Bei 60 s war jede flache Fühlerstufe das Ende des Nachlaufs: +10 % Ladekosten gegenüber 300 s."""
    runs = {s: _charge_with_slow_tail(s) for s in (60, 300, 900)}
    costs = [c for c, _ in runs.values()]
    assert max(costs) / min(costs) - 1 <= 0.05, runs
    # das langsame Ende des Nachlaufs (unter ≈ 2 K/h) zählt bei keiner Abtastrate mehr mit
    ideal = 3.0 / (9.0 + 2.0 * (1 - math.exp(-30 / 15)))  # Nachlauf bis AFTER_CHARGE_H (30 min)
    for c in costs:
        assert c == pytest.approx(ideal, rel=0.1)


def test_tail_rising_checks_the_window_not_the_step():
    hist = [(0.0, 54.0)] + [(i / 60, 54.0 + 0.1 * ((i + 1) // 2)) for i in range(1, 11)]  # 60 s: +0,1 / 0 / +0,1 …
    assert tail_rising(hist)  # 0,5 K in 10 min, auch wenn der letzte Schritt flach ist
    flat = hist + [(hist[-1][0] + i / 60, hist[-1][1]) for i in range(1, 6)]
    assert not tail_rising(flat)  # 5 min nichts mehr: vorbei
    assert not tail_rising(hist + [(hist[-1][0] + 1 / 60, hist[-1][1] - 0.2)])  # fällt: Zapfung
    assert tail_rising([(0.0, 54.0), (STEP, 54.2)]) and not tail_rising([(0.0, 54.0), (STEP, 54.1)])  # 300 s wie bisher


def test_flat_steps_at_the_end_do_not_count_for_the_charge_rate():
    m = _model()
    now, t = datetime(2026, 10, 15, 8, 40), 45.0
    for _ in range(15):  # 15 min Ladung, 9 K
        now += timedelta(minutes=1)
        m.step(now, 1 / 60, t, t + 0.6, True, 12.0)
        t += 0.6
    for i in range(10):  # Nachlauf +1 K in 10 min, jede zweite Minute eine Stufe
        now += timedelta(minutes=1)
        rise = 0.2 if i % 2 else 0.0
        m.step(now, 1 / 60, t, t + rise, False, None)
        t += rise
    for _ in range(6):  # flach: Nachlauf läuft noch TAIL_WIN_H weiter, zählt aber nicht zur Ladedauer
        now += timedelta(minutes=1)
        m.step(now, 1 / 60, t, t, False, None)
    assert m.charges == 1
    assert m.charge_rate_kh == pytest.approx(20.0 + 0.2 * (10.0 / (25 / 60) - 20.0), rel=0.02)


def test_dhw_points_with_minute_samples_and_slow_tail():
    cfg = Config()
    cfg.boiler_nominal_kw = 18.0
    eng = Engine(cfg, Store(Path(tempfile.mkdtemp()) / "t.db"), Discovered())
    true = DhwModel()
    t0 = int(datetime(2026, 10, 1, 0, 0).timestamp())
    ts, exact = t0, 55.0
    for _ in range(8):  # alle 6 h: Ladung, langsamer Nachlauf mit flachen Minuten, Auskühlen
        for i in range(360):
            ts += 60
            flag = 0
            if i < 10:
                exact, flag = 53.0 + 0.2 * i, 1
            elif i < 30:
                exact += 0.1 if i % 2 else 0.0  # +1 K in 20 min, jede zweite Minute flach
            else:
                exact -= true.cooling_kh(exact) / 60
            eng.store.add_sample(ts, {"dhw_temp": round(exact, 1), "dhw": flag})
    pts = eng.dhw_points(days=5, now_ts=ts)
    assert pts and all(p["kh"] <= true.cooling_kh(p["t"]) + 0.3 and p["kh"] >= 0 for p in pts), pts


# ---------------------------------------------------------------------------- logik:3 Zapf-Fenster bei 900 s
def _slow_drop(step_s: int, drop_k: float, minutes: int) -> float:
    m = _model()
    now, dt_h, exact, shown, total = datetime(2026, 10, 12, 10, 0), step_s / 3600, 50.0, 50.0, 0.0

    def adv(extra: float):
        nonlocal now, exact, shown, total
        now += timedelta(seconds=step_s)
        exact -= m.cooling_kh(exact) * dt_h + extra
        new = round(exact, 1)
        total += m.step(now, dt_h, shown, new, False, None)["draw_k"]
        shown = new

    for _ in range(int(3600 / step_s)):
        adv(0.0)
    k = int(minutes * 60 / step_s)
    for _ in range(k):
        adv(drop_k / k)
    for _ in range(int(3600 / step_s)):
        adv(0.0)
    return round(total, 2)


@pytest.mark.parametrize("drop_k,minutes", [(2.0, 60), (0.9, 30)])
def test_slow_excess_below_the_window_threshold_at_300_and_900_s(drop_k, minutes):
    """Vorher behielt das Fenster bei 900 s zwei Schritte (30 min): 2 K/h galten nur dort als Zapfung."""
    assert _slow_drop(300, drop_k, minutes) == 0.0
    assert _slow_drop(900, drop_k, minutes) == 0.0


def test_real_shower_is_still_found_at_300_and_900_s():
    assert _slow_drop(300, 1.6, 20) >= HIT_K
    assert _slow_drop(900, 1.6, 20) >= HIT_K


# ---------------------------------------------------------------------------- logik:4 / daten:4 Sonderwärme
def _shadow_after_special(hours_after: float, target: float = 40.0) -> DhwShadow:
    """Sonderladung auf 65 °C gespiegelt, danach ``hours_after`` ohne Zapfung mit Ziel ``target``."""
    m = _model()
    sh = DhwShadow(t_sim=40.0)
    for t in (45.0, 50.0, 55.0, 60.0, 65.0):
        sh.step(m, t, target, 0.0, STEP, 50.0, dhw_min=30.0, margin_bound=False, dhw_max=60.0, mirror=t)
    for _ in range(int(hours_after * 12)):
        sh.step(m, 60.0, target, 0.0, STEP, 50.0, dhw_min=30.0, margin_bound=False, dhw_max=60.0)
    return sh


def test_special_heat_excuses_only_draws_that_start_warm():
    sh = _shadow_after_special(2.0)
    assert sh._ride and sh.t_sim >= 50.0
    res = sh.step(_model(), 60.0, 40.0, 36.0, STEP, 50.0, dhw_min=30.0, margin_bound=True, dhw_max=60.0)
    assert res["cold_draw"] == 0  # Wanne nach der Legionellenschaltung: warm begonnen, nur durch Größe unter Minimum
    sh = _shadow_after_special(10.5)
    assert sh._ride and 40.0 < sh.t_sim < 50.0  # fährt noch auf der Sonderwärme, aber schon unter Komfort
    res = sh.step(_model(), 52.0, 50.0, 6.0, STEP, 50.0, dhw_min=30.0, margin_bound=True, dhw_max=60.0)
    assert res["cold_draw"] == 1 and res["raise_margin"] == 1  # zählt wie ohne Spiegelung


def test_ride_on_special_heat_ends_after_special_cover_h():
    sh = _shadow_after_special(SPECIAL_COVER_H + 1.0, target=30.0)
    assert not sh._ride
    sh = _shadow_after_special(SPECIAL_COVER_H - 1.0, target=30.0)
    assert sh._ride


# ---------------------------------------------------------------------------- daten:1 Reserve aus dem Boden
def test_unexpected_cold_draw_raises_reserve_if_the_floor_is_bound():
    m = _model()
    res = DhwShadow(t_sim=36.0).step(m, 36.0, 36.0, 2.0, STEP, 50.0, dhw_min=30.0, margin_bound=True)
    assert res["cold_draw"] == 1 and res["raise_margin"] == 1
    res = DhwShadow(t_sim=36.0).step(m, 36.0, 36.0, 2.0, STEP, 50.0, dhw_min=30.0, margin_bound=False)
    assert res["cold_draw"] == 1 and res["raise_margin"] == 0  # niemand zu Hause: Boden ist das Minimum


def test_floor_reports_margin_bound_only_with_someone_home():
    m, cfg = DhwModel(), _cfg()
    at = datetime(2026, 10, 19, 3, 0)
    assert m.recommend(at, True, None, cfg)["margin_bound"]
    assert m.recommend(at, None, None, cfg)["margin_bound"]
    assert not m.recommend(at, False, None, cfg)["margin_bound"]


def _irregular_household(weeks: int, seed: int = 1):
    """Eine Dusche täglich (10 K) zu wechselnder Uhrzeit 07:30–21:30: Keine Stunde erreicht die
    Trefferquote, die Strategie sagt immer „keine Zapfung erwartet“ – die Reserve ist die einzige
    Korrektur. Übergabe an den Schatten wie in engine.dhw_step."""
    rng = random.Random(seed)
    cfg = _cfg()
    m, sh = DhwModel(), DhwShadow()
    t, charging, rec, start = 55.0, False, None, datetime(2026, 1, 5)
    cold, shower = 0, None
    for i in range(weeks * 7 * 288):
        now = start + timedelta(minutes=5 * i)
        if now.hour == 0 and now.minute == 0:
            shower = now + timedelta(minutes=5 * rng.randint(90, 258))
        t_prev = t
        t -= 0.02 * (t - 18.0) * STEP
        if shower is not None and shower <= now < shower + timedelta(minutes=15):
            t -= 10.0 / 3
        if not charging and t < 45:
            charging = True
        heated = charging
        if charging:
            t += 63.5 * STEP
            if t >= 55:
                charging = False
        res = m.step(now, STEP, round(t_prev, 1), round(t, 1), heated, 10.8 if heated else None,
                     burner_kw_prev=10.8 if heated else 0.0)
        before = rec or {}
        rec = m.recommend(now, True, None, cfg)
        out = sh.step(m, round(t, 1), rec["target"], res["draw_k"], STEP, cfg.dhw_comfort_temp,
                      dhw_min=cfg.dhw_min_temp, margin_bound=bool(before.get("margin_bound")), dhw_max=cfg.dhw_max_temp)
        if out["raise_margin"] and m.charges >= 3:
            m.note_cold_draw()
        cold += out["cold_draw"]
    return m, cfg, cold


def test_irregular_household_gets_a_reserve_above_the_minimum():
    m, cfg, cold = _irregular_household(4)
    assert max(m.hit_daily) < 0.25  # keine Stunde „üblich“: das Profil hilft hier nicht
    assert m.comfort_margin >= 4.0
    rec = m.recommend(datetime(2026, 2, 2, 3, 0), True, None, cfg)
    assert rec["reason"] == "keine Zapfung erwartet" and rec["target"] > cfg.dhw_min_temp


# ---------------------------------------------------------------------------- daten:2 Fühlerstufe
def test_sensor_step_is_the_smallest_frequent_change():
    assert sensor_step_of([0.5] * 20 + [1.0] * 15) == 0.5
    assert sensor_step_of([1.0] * 30 + [2.0] * 5 + [0.1] * 2) == 1.0  # zwei 0,1-Ausreißer zählen nicht
    assert sensor_step_of([0.1] * 25 + [0.2] * 10 + [3.0] * 3) == 0.1
    assert sensor_step_of([0.5] * 10) == 0.1  # zu wenig: Standard


def _coarse_tank(q: float, weeks: int = 3, step_s: int = 300):
    """Speicher mit wahrem Auskühlen 0,32 K/h bei 55 °C, Fühler mit Stufe ``q``; Dusche 7 Uhr (2,5 K),
    Abwasch 19 Uhr (1,5 K); Kessel lädt unter 45 °C auf 55 °C."""
    m = DhwModel()  # Startmodell 1,4 K/h bei 55 °C – muss auf 0,32 herunterlernen
    k = 0.32 / 35
    dt_h, t, charging = step_s / 3600, 55.0, False
    now = datetime(2026, 1, 5)
    shown = round(t / q) * q
    found = 0
    for _ in range(int(weeks * 7 * 24 * 3600 / step_s)):
        now += timedelta(seconds=step_s)
        t -= k * (t - 20.0) * dt_h
        if now.hour == 7 and now.minute < 10:
            t -= 2.5 * dt_h * 6
        if now.hour == 19 and now.minute < 5:
            t -= 1.5 * dt_h * 12
        if not charging and t < 45:
            charging = True
        heated = charging
        if charging:
            t += 60.0 * dt_h
            if t >= 55:
                charging = False
        new = round(round(t / q) * q, 2)
        res = m.step(now, dt_h, shown, new, heated, 12.0 if heated else None, burner_kw_prev=12.0 if heated else 0.0)
        if res["draw_k"] >= HIT_K and now.hour == 7:
            found += 1
        shown = new
    return m, found


@pytest.mark.parametrize("q", [0.5, 1.0])
def test_coarse_sensor_steps_still_learn_the_cooling(q):
    m, found = _coarse_tank(q)
    assert m.sensor_step == q
    assert m.cooling_kh(55.0) == pytest.approx(0.32, rel=0.2), m.cooling_kh(55.0)
    assert found >= 16  # die Dusche wird weiter erkannt (21 Tage; an 4 davon löst sie gleich die Ladung aus)
    assert m.daily[7] >= 1.2 and m.hit_daily[7] >= 0.5


def test_fine_sensor_keeps_the_thresholds():
    m, found = _coarse_tank(0.1)
    assert m.sensor_step == 0.1
    assert m._watch.open_k == DRAW_OPEN_K and m._watch.win_k == DRAW_WIN_K and m._watch.seg_excess_kh == SEG_EXCESS_KH
    assert m.cooling_kh(55.0) == pytest.approx(0.32, rel=0.2)
    assert found >= 18


def test_sensor_step_is_saved_and_used_by_dhw_points():
    m = DhwModel()
    m.sensor_step = 0.5
    again = DhwModel.from_dict(m.to_dict())
    assert again.sensor_step == 0.5 and again._watch.open_k == 0.6 and again._watch.win_k == 0.8
    again.sensor_step = again._watch.sensor_step = 1.0
    assert again._watch.open_k == 1.1 and again._watch.win_k == 1.5
    assert DhwModel.from_dict({k: v for k, v in m.to_dict().items() if k != "sensor_step"}).sensor_step == 0.1


# ---------------------------------------------------------------------------- daten:3 Ladebeginn als Rampe
def test_first_charge_step_ramps_from_zero_even_if_the_burner_was_heating():
    m = _model()
    t0 = datetime(2026, 10, 15, 8, 40)
    res = m.step(t0, STEP, 45.0, 48.0, True, 12.0, burner_kw_prev=8.0)  # davor 8 kW Heizbetrieb
    assert res["charge_kwh"] == pytest.approx(6.0 * STEP)
    res = m.step(t0 + timedelta(minutes=5), STEP, 48.0, 51.0, True, 12.0, burner_kw_prev=12.0)
    assert res["charge_kwh"] == pytest.approx(12.0 * STEP)  # danach wie bisher das Mittel


def test_draw_at_charge_start_uses_the_ramp_too():
    m = _model()
    m.kwh_per_k["kalt"], m.kwh_per_k_n["kalt"] = 0.2, 5
    t0 = datetime(2026, 10, 11, 18, 40)
    heating = m.step(t0, STEP, 70.0, 40.0, True, 18.0, burner_kw_prev=8.0)["draw_k"]
    m = _model()
    m.kwh_per_k["kalt"], m.kwh_per_k_n["kalt"] = 0.2, 5
    idle = m.step(t0, STEP, 70.0, 40.0, True, 18.0, burner_kw_prev=0.0)["draw_k"]
    assert heating == pytest.approx(idle)


def test_charge_cost_does_not_depend_on_heating_before_the_charge():
    def cost(prev_kw: float) -> float:
        m = _model()
        t0, t = datetime(2026, 10, 15, 8, 40), 45.0
        for i in range(3):
            m.step(t0 + timedelta(minutes=5 * i), STEP, t, t + 3.0, True, 12.0, burner_kw_prev=prev_kw if i == 0 else 12.0)
            t += 3.0
        m.step(t0 + timedelta(minutes=15), STEP, t, t, False, prev_kw, burner_kw_prev=12.0)
        return m.kwh_per_k["kalt"]

    assert cost(8.0) == pytest.approx(cost(0.0))
    assert cost(0.0) == pytest.approx(3 * 12.0 * STEP / 9.0)
