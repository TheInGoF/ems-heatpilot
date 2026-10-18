"""0.32 Nachbesserung Warmwasser: Reserve nur, wenn sie das Ziel anhebt; Zapfprofil bei langer
Abwesenheit; Zapfung während der Ladung abtastratenunabhängig; Ladegeschwindigkeit mit Nachlauf;
ruhige Abschnitte mit offenem Zapf-Fenster verwerfen."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from heatpilot.config import Config
from heatpilot.dhw import TAIL_WIN_H, DhwModel, DhwShadow, DrawWatch
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

STEP = 5 / 60


def _engine() -> Engine:
    cfg = Config()
    cfg.boiler_nominal_kw = 18.0
    return Engine(cfg, Store(Path(tempfile.mkdtemp()) / "t.db"), Discovered())


def _cfg(dhw_min=30.0, comfort=50.0, dhw_max=60.0) -> Config:
    cfg = Config()
    cfg.dhw_min_temp, cfg.dhw_comfort_temp, cfg.dhw_max_temp = dhw_min, comfort, dhw_max
    return cfg


# ---------------------------------------------------------------------------- 1. Reserve nur, wenn sie hilft
def _house_with_bath(weeks: int):
    """Tägliche Dusche 7 Uhr (12 K), Bad sonntags 18 Uhr (45 K); Kessel lädt unter 45 °C auf 55 °C.
    Übergabe an den Schatten wie in engine.dhw_step: Stand der Empfehlung vor dem Schritt."""
    cfg = _cfg()
    m, sh = DhwModel(), DhwShadow()
    dt_h = STEP
    t, charging, rec, start = 55.0, False, None, datetime(2026, 1, 5)
    margins = []
    for i in range(weeks * 7 * 288):
        now = start + timedelta(minutes=5 * i)
        t_prev = t
        t -= 0.02 * (t - 18.0) * dt_h
        if now.hour == 7 and now.minute < 15:
            t -= 4.0
        if now.weekday() == 6 and now.hour == 18 and now.minute < 20:
            t -= 11.25
        if not charging and t < 45:
            charging = True
        heated = charging
        if charging:
            t += 63.5 * dt_h
            if t >= 55:
                charging = False
        res = m.step(now, dt_h, round(t_prev, 1), round(t, 1), heated, 10.8 if heated else None,
                     burner_kw_prev=10.8 if heated else 0.0)
        before = rec or {}
        rec = m.recommend(now, True, None, cfg)
        out = sh.step(m, round(t, 1), rec["target"], res["draw_k"], dt_h, cfg.dhw_comfort_temp,
                      dhw_min=cfg.dhw_min_temp,
                      margin_bound=bool(before.get("margin_bound")), dhw_max=cfg.dhw_max_temp)
        if out["raise_margin"] and m.charges >= 3:
            m.note_cold_draw()
        if now.weekday() == 6 and now.hour == 23 and now.minute == 0:
            margins.append(m.comfort_margin)
    return m, cfg, start + timedelta(weeks=weeks, hours=3), margins


def test_weekly_bath_at_the_cap_does_not_ratchet_the_reserve():
    m, cfg, monday_3am, margins = _house_with_bath(30)
    assert len(m.big_draws) >= 2  # das Bad ist als wiederkehrend bekannt
    assert max(margins[6:]) <= 2.0, margins
    assert m.recommend(monday_3am, True, None, cfg)["target"] == cfg.dhw_min_temp


def test_recommend_says_whether_more_reserve_lifts_the_target():
    cfg = _cfg()
    m = DhwModel()
    m.daily[7] = 5.0  # Dusche: Komfort bestimmt das Ziel
    rec = m.recommend(datetime(2026, 10, 19, 6, 30), True, None, cfg)
    assert rec["expects_draw"] and rec["margin_bound"]
    m.daily[7] = 40.0  # Minimum + Bedarf über dem Maximum: gedeckelt
    rec = m.recommend(datetime(2026, 10, 19, 6, 30), True, None, cfg)
    assert rec["target"] == 60 and not rec["margin_bound"]
    m.daily[7], m.comfort_margin = 5.0, 10.0  # Komfort + Reserve = Maximum
    assert not m.recommend(datetime(2026, 10, 19, 6, 30), True, None, cfg)["margin_bound"]
    assert not m.recommend(datetime(2026, 10, 19, 6, 30), False, None, cfg)["margin_bound"]


@pytest.mark.parametrize("t0,k,bound,dhw_max,raises", [
    (50.0, 22.0, True, 60.0, 1),   # 2 K unter dem Minimum, Ziel aus Komfort: mehr Reserve hilft
    (50.0, 22.0, False, 60.0, 0),  # Ziel kam aus Minimum + Bedarf: Reserve ändert nichts
    (60.0, 32.0, True, 60.0, 0),   # schon am Maximum
    (55.0, 31.0, True, 60.0, 0),   # größer als Maximum − Minimum: keine Reserve reicht
    (50.0, 29.5, True, 60.0, 1),   # Fehlbetrag 9,5 K: mit der höchsten Reserve (10 K) noch deckbar
    (50.0, 31.5, True, 70.0, 0),   # Fehlbetrag 11,5 K: mehr, als die Reserve je hergibt
])
def test_cold_event_raises_reserve_only_if_reserve_could_help(t0, k, bound, dhw_max, raises):
    m = DhwModel()
    m.loss.theta = [0.8, -0.04]
    sh = DhwShadow(t_sim=t0)
    res = sh.step(m, t0, 30.0, k, STEP, 50.0, dhw_min=30.0, margin_bound=bound,
                  dhw_max=dhw_max)
    assert res["cold_draw"] == 1
    assert res["raise_margin"] == raises


def test_reserve_state_is_taken_from_before_the_event():
    """Die Wanne über zwei Schritte: zählt, was vor dem ersten Schritt galt."""
    m = DhwModel()
    sh = DhwShadow(t_sim=50.0)
    first = sh.step(m, 50.0, 50.0, 0.5, STEP, 50.0, dhw_min=30.0, margin_bound=False,
                    dhw_max=60.0)
    second = sh.step(m, 50.0, 50.0, 21.0, STEP, 50.0, dhw_min=30.0, margin_bound=True,
                     dhw_max=60.0)
    assert first["cold_draw"] == 0 and second["cold_draw"] == 1 and second["raise_margin"] == 0


def test_engine_hands_margin_bound_to_the_shadow():
    def run(daily: float, start: float, drop: float) -> float:
        eng = _engine()
        eng.cfg.dhw_min_temp, eng.cfg.dhw_comfort_temp, eng.cfg.dhw_max_temp = 30.0, 50.0, 70.0
        eng.dhw.kwh_per_k, eng.dhw.kwh_per_k_n = {"kalt": 0.2, "warm": None}, {"kalt": 10, "warm": 0}
        eng.dhw.charges, eng.dhw.loss.updates = 10, 500
        eng.dhw.daily = [daily] * 24  # Strategie erwartet immer eine Zapfung dieser Größe
        cold, step = [], eng.dhw_shadow.step

        def counting(*a, **k):
            out = step(*a, **k)
            cold.append(out["cold_draw"])
            return out

        eng.dhw_shadow.step = counting
        t0 = int(datetime(2026, 10, 12, 7, 0).timestamp())
        for i, temp in enumerate([start, start, start - drop, start - drop]):
            eng.dhw_step(t0 + i * 300, {"dhw_temp": temp, "dhw": 0, "vacation": 0, "home_count": 1})
        assert sum(cold) == 1  # in beiden Fällen zu kalt gezählt
        return eng.dhw.comfort_margin

    assert run(1.5, 50.0, 22.0) > 1.5  # Ziel 50 °C aus Komfort: 2 K unter dem Minimum, Reserve steigt
    # Ziel 65 °C aus Minimum + Bedarf (3 Stunden je 11,7 K): auch zu kalt, aber mehr Reserve hebt es nicht
    assert run(35.0 / 3, 65.0, 37.0) == 0.0


# ---------------------------------------------------------------------------- 2. Zapfprofil bei langer Abwesenheit
def _trip(away_long_after_h: float | None, weeks: int = 3, days: int = 4, roundtrip_json: bool = False):
    """Ein-Personen-Haushalt, Dusche 7 Uhr; Reise ab Montag 08:00. ``away_long`` kommt nach
    ``away_long_after_h`` (wie von presence.household_long_away), None = nie."""
    m = DhwModel()
    start = datetime(2026, 9, 7)
    trip0 = start + timedelta(weeks=weeks, hours=8)
    trip1 = trip0 + timedelta(days=days)
    t, charging, snap = 55.0, False, None
    now = start
    while now < trip1 + timedelta(hours=1):
        now += timedelta(minutes=5)
        away = trip0 <= now < trip1
        long_away = away and away_long_after_h is not None and (now - trip0) >= timedelta(hours=away_long_after_h)
        t_prev = t
        t -= 0.02 * (t - 18) * STEP
        if not away and now.hour == 7 and now.minute in (0, 5, 10):
            t -= 4
        if not charging and t < 45:
            charging = True
        if charging:
            t += 60 * STEP
            if t >= 55:
                charging = False
        m.note_household(0 if away else 1, long_away)  # wie engine.dhw_step: vor dem Lernschritt
        m.step(now, STEP, round(t_prev, 1), round(t, 1), charging, 10.8 if charging else None,
               learn_usage=not long_away, burner_kw_prev=10.8 if charging else 0.0)
        if now == trip0 + timedelta(hours=1, minutes=5):  # Montag 07 Uhr ist jetzt gelernt, sonst nichts
            snap = (list(m.daily), list(m.hit_daily), list(m.profile))
        if roundtrip_json and now == trip0 + timedelta(hours=30):  # Neustart mitten in der Reise
            m = DhwModel.from_dict(json.loads(json.dumps(m.to_dict())))
        if now == trip1 - timedelta(minutes=5):
            before_return = (list(m.daily), list(m.hit_daily), list(m.profile))
    return snap, before_return, m


@pytest.mark.parametrize("after_h", [72.0, 24.0, 6.0])
def test_long_absence_does_not_teach_empty_hours_before_it_was_detected(after_h):
    snap, back, _ = _trip(after_h)
    assert back[0] == pytest.approx(snap[0])  # Tagesprofil wie vor der Reise
    assert back[1] == pytest.approx(snap[1])  # Trefferquote ebenso
    assert back[2] == pytest.approx(snap[2])  # Wochenprofil ebenso


def test_backup_survives_a_restart():
    snap, back, m = _trip(72.0, roundtrip_json=True)
    assert back[0] == pytest.approx(snap[0]) and back[2] == pytest.approx(snap[2])
    assert m._backup is None  # nach der Heimkehr verworfen


def test_short_absence_learns_as_before():
    """Ohne „lange weg“ (kurzer Ausflug, oder lange Abwesenheit, die regelmäßig ist): normal weiterlernen."""
    snap, back, m = _trip(None, days=2)
    assert back[0][7] < snap[0][7]  # der Ausflugsmorgen zählt als „keine Dusche“
    assert m._backup is None


def test_engine_passes_household_to_dhw_model():
    eng = _engine()
    t0 = int(datetime(2026, 10, 12, 9, 0).timestamp())
    eng.dhw.daily[7] = 6.0
    eng.dhw_step(t0, {"dhw_temp": 50.0, "dhw": 0, "home_count": 0, "away_long": 0})
    assert eng.dhw._backup is not None and eng.dhw._backup["daily"][7] == 6.0
    eng.dhw.daily[7] = 3.0  # leere Stunden gelernt …
    eng.dhw_step(t0 + 300, {"dhw_temp": 49.9, "dhw": 0, "home_count": 0, "away_long": 1})
    assert eng.dhw.daily[7] == 6.0 and eng.dhw._backup is None  # … und zurückgenommen
    eng.dhw_step(t0 + 600, {"dhw_temp": 49.8, "dhw": 0, "home_count": 1, "away_long": 0})
    assert eng.dhw._backup is None


# ---------------------------------------------------------------------------- 3. Zapfung während der Ladung
def _charge_with_shower(step_s: int):
    """Ladung 45 → 57 °C, im zweiten 5-min-Abschnitt fällt der Speicher trotz Brenner um 2 K."""
    m = DhwModel()
    m.loss.theta = [0.8, -0.04]
    m.kwh_per_k, m.kwh_per_k_n = {"kalt": 0.2, "warm": None}, {"kalt": 5, "warm": 0}
    coarse = [45.0, 48.0, 46.0, 49.0, 52.0, 55.0, 57.0]
    n = 300 // step_s
    temps = [coarse[0]] + [round(a + (b - a) * j / n, 2) for a, b in zip(coarse, coarse[1:]) for j in range(1, n + 1)]
    t0, dt_h, total = datetime(2026, 10, 11, 18, 40), step_s / 3600, 0.0
    for i in range(1, len(temps)):
        total += m.step(t0 + timedelta(seconds=step_s * i), dt_h, temps[i - 1], temps[i], True, 12.0,
                        burner_kw_prev=12.0)["draw_k"]
    drawn = m._charge["drawn"]
    end = t0 + timedelta(seconds=step_s * len(temps))
    total += m.step(end, dt_h, temps[-1], temps[-1], False, None)["draw_k"]
    return total, drawn, m


@pytest.mark.parametrize("step_s", [60, 100, 150])
def test_draw_during_charge_does_not_depend_on_sample_rate(step_s):
    ref, ref_drawn, ref_m = _charge_with_shower(300)
    total, drawn, m = _charge_with_shower(step_s)
    assert ref_drawn and drawn
    assert total == pytest.approx(ref, abs=0.05)
    assert m.kwh_per_k_n == ref_m.kwh_per_k_n  # gezapft: in beiden Fällen keine Ladekosten gelernt
    assert m._slot_draw == pytest.approx(ref_m._slot_draw, abs=0.05)


def test_short_drop_at_charge_end_still_counts():
    """Ladesignal fällt mitten im 5-min-Abschnitt: der angefangene Abschnitt wird trotzdem geprüft."""
    m = DhwModel()
    m.kwh_per_k, m.kwh_per_k_n = {"kalt": 0.2, "warm": None}, {"kalt": 5, "warm": 0}
    t0, dt_h = datetime(2026, 10, 11, 18, 40), 1 / 60
    temps = [45.0, 46.0, 47.0, 48.0, 49.0, 50.0, 49.2, 48.4]
    for i in range(1, len(temps)):
        m.step(t0 + timedelta(minutes=i), dt_h, temps[i - 1], temps[i], True, 12.0, burner_kw_prev=12.0)
    res = m.step(t0 + timedelta(minutes=len(temps)), dt_h, temps[-1], temps[-1], False, None)
    assert res["draw_k"] > 1.6


# ---------------------------------------------------------------------------- 4. Ladegeschwindigkeit mit Nachlauf
@pytest.mark.parametrize("step_s", [60, 300])
def test_charge_rate_is_rise_over_time_to_peak(step_s):
    """Brenner 15 min: 45 → 54 °C, danach Nachlauf +2 K in 10 min – bis zum Gipfel 25 min für 11 K."""
    m = DhwModel()
    dt_h, now = step_s / 3600, datetime(2026, 10, 15, 8, 40)
    t, rate0 = 45.0, m.charge_rate_kh
    for _ in range(15 * 60 // step_s):
        now += timedelta(seconds=step_s)
        m.step(now, dt_h, t, t + 9.0 * step_s / 900, True, 12.0, burner_kw_prev=12.0)
        t += 9.0 * step_s / 900
    for _ in range(10 * 60 // step_s):
        now += timedelta(seconds=step_s)
        m.step(now, dt_h, t, t + 2.0 * step_s / 600, False, 0.0, burner_kw_prev=0.0)
        t += 2.0 * step_s / 600
    # flache Schritte: der Nachlauf endet, sobald es über TAIL_WIN_H nicht mehr gestiegen ist (0.32c) –
    # sie zählen nicht zur Ladedauer
    for _ in range(max(1, round(TAIL_WIN_H * 3600 / step_s))):
        now += timedelta(seconds=step_s)
        m.step(now, dt_h, t, t, False, 0.0, burner_kw_prev=0.0)
    assert m.charges == 1
    assert m.charge_rate_kh == pytest.approx(rate0 + 0.2 * (11.0 / (25 / 60) - rate0), rel=0.02)
    # Kosten: voller Anstieg; Energie als Trapez – Rampe ab 0 im ersten Schritt (der Brenner lief
    # davor für die Heizung, 0.32c) und auf 0 im ersten Schritt nach dem Signal
    assert m.kwh_per_k["kalt"] == pytest.approx(12.0 * 0.25 / 11.0, rel=0.02)


# ---------------------------------------------------------------------------- 5. Abschnitte mit offenem Fenster
def _watch_run(step_s: int, true_kh: float, small_draw_k: float, hours: float = 8.0):
    """Ruhiges Auskühlen (Modell = Wahrheit), jede 2. Stunde eine Kleinstzapfung unter der Zapfschwelle."""
    w, dt_h = DrawWatch(), step_s / 3600
    exact, shown, now = 55.0, 55.0, datetime(2026, 10, 12, 0, 0)
    segs, draw_times = [], []
    for i in range(int(hours * 3600 / step_s)):
        now += timedelta(seconds=step_s)
        exact -= true_kh * dt_h
        minute = (i * step_s // 60) % 120
        if 60 <= minute < 70:  # 10 min Händewaschen
            exact -= small_draw_k * dt_h * 6
            draw_times.append(now)
        new = round(exact, 1)
        res = w.step(now, dt_h, shown, new, lambda _t: true_kh, 99.0)
        if res["segment"]:
            segs.append((now, res["segment"]))
        shown = new
    return segs, draw_times


@pytest.mark.parametrize("step_s", [60, 300])
def test_segment_with_open_window_is_dropped_not_postponed(step_s):
    segs, draws = _watch_run(step_s, 0.6, 0.5)
    assert segs  # Auskühlen wird weiter gelernt
    for end, (_, hours, _) in segs:
        begin = end - timedelta(hours=hours)
        assert not any(begin < d <= end for d in draws), (begin, end)
    # gelernte Steigungen ohne Kleinstzapfung: nahe am wahren Auskühlen
    for _, (t0, hours, t1) in segs:
        assert (t0 - t1) / hours < 0.6 + 0.45


@pytest.mark.parametrize("step_s", [60, 300])
def test_faster_cooling_than_start_model_is_still_learned(step_s):
    """Keine einseitige Auswahl: kühlt der Speicher schneller als das Startmodell, lernt er es trotzdem."""
    m = DhwModel()  # Start: 50 °C ≈ 1,2 K/h
    true_a, true_b = 1.6, -0.06  # 55 °C: 1,7 K/h, 45 °C: 1,1 K/h
    dt_h, exact, shown, now = step_s / 3600, 58.0, 58.0, datetime(2026, 10, 12, 0, 0)
    for _ in range(int(48 * 3600 / step_s)):
        now += timedelta(seconds=step_s)
        exact -= -(true_a + true_b * exact) * dt_h
        if exact < 42.0:
            exact = 58.0  # Ladung (ohne Signal hier: Lücke)
            m.step(now, dt_h, None, None, False, None)
            shown = round(exact, 1)
            continue
        new = round(exact, 1)
        m.step(now, dt_h, shown, new, False, None)
        shown = new
    assert m.loss.updates >= 30
    assert abs(m.cooling_kh(50.0) - 1.4) < 0.2


def test_dhw_points_drop_segments_with_small_draws():
    eng = _engine()
    true = DhwModel()  # Auskühlen wie das (Start-)Modell der Engine
    t0 = int(datetime(2026, 10, 1, 0, 0).timestamp())
    ts, exact = t0, 55.0
    for _ in range(12):  # alle 6 h nachladen, je Zyklus 0,6 K Händewaschen über 15 min
        for i in range(72):
            ts += 300
            flag = 0
            if i < 2:
                exact, flag = 55.0, 1
            else:
                exact -= true.cooling_kh(exact) * STEP
                if 30 <= i < 33:
                    exact -= 0.2
            eng.store.add_sample(ts, {"dhw_temp": round(exact, 1), "dhw": flag})
    pts = eng.dhw_points(days=5, now_ts=ts)
    # höchstens ein Schritt unter der Fenster-Schwelle darf noch hineinrutschen (< DRAW_OPEN_K / SEGMENT_H)
    assert pts and all(p["kh"] <= true.cooling_kh(p["t"]) + 0.45 for p in pts), pts
