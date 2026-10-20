"""0.32: Legionellenwärme bewusst nutzen – die Sonderladung des Reglers trifft Wirklichkeit und
Strategie gleichermaßen; eine große Zapfung danach (Wanne am Sonntagabend) ist dadurch gedeckt."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from heatpilot.config import Config
from heatpilot.dhw import DhwModel, DhwShadow
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

STEP = 5 / 60
MONDAY = datetime(2026, 1, 5)


def _cfg() -> Config:
    cfg = Config()
    cfg.dhw_min_temp, cfg.dhw_comfort_temp, cfg.dhw_max_temp = 30.0, 50.0, 60.0
    return cfg


def _sim(weeks: int, legionella: bool = True, flag: bool = True):
    """Dusche täglich 7 Uhr (12 K), Wanne sonntags 18 Uhr (45 K), Kessel lädt unter 45 auf 55 °C.
    ``legionella``: sonntags 13 Uhr lädt der Regler auf 70 °C (``flag``: mit Kesselschalter, sonst
    nur am Anstieg weit über das Soll erkennbar). Übergabe an den Schatten wie in engine.dhw_step."""
    cfg = _cfg()
    m, sh = DhwModel(), DhwShadow()
    for d in range(7):
        m.profile_n[d * 24 + 18] = 2  # Stunde 18 gilt als eingelernt: große Zapfungen zählen
    t, charging, goal, rec = 55.0, False, 55.0, None
    log = {"cold_sunday": 0, "cold_other": 0, "sunday": []}
    for i in range(weeks * 7 * 288):
        now = MONDAY + timedelta(minutes=5 * i)
        t_prev = t
        t -= 0.02 * (t - 18.0) * STEP
        sunday = now.weekday() == 6
        if now.hour == 7 and now.minute < 15:
            t -= 4.0
        if sunday and now.hour == 18 and now.minute < 20:
            t -= 11.25
        if legionella and sunday and now.hour == 13 and now.minute == 0:
            charging, goal = True, 70.0
        elif not charging and t < 45:
            charging, goal = True, 55.0
        heated = charging
        if charging:
            t += 63.5 * STEP
            if t >= goal:
                charging = False
        res = m.step(now, STEP, round(t_prev, 1), round(t, 1), heated, 10.8 if heated else None,
                     special=flag and heated and goal == 70.0, dhw_set=55.0,
                     burner_kw_prev=10.8 if heated else 0.0)
        before = rec or {}
        rec = m.recommend(now, True, None, cfg)
        out = sh.step(m, round(t, 1), rec["hold"], res["draw_k"], STEP, cfg.dhw_comfort_temp,
                      dhw_min=cfg.dhw_min_temp,
                      margin_bound=bool(before.get("margin_bound")), dhw_max=cfg.dhw_max_temp,
                      mirror=round(t, 1) if res["special"] else None, top=rec["target"])
        if out["raise_margin"] and m.charges >= 3:
            m.note_cold_draw()
        if out["cold_draw"]:
            log["cold_sunday" if sunday else "cold_other"] += 1
        if sunday and now.minute == 0 and 12 <= now.hour <= 19:
            log["sunday"].append((now.hour, round(t, 1), round(sh.t_sim, 1), rec["target"], rec["reason"]))
    return m, sh, cfg, log


def test_special_charge_reaches_the_shadow_too():
    m, sh, cfg, log = _sim(3)
    last = {h: row for h, *row in log["sunday"][-8:]}
    assert last[14][1] >= last[14][0] - 1.0  # nach der Legionellenladung steht der Schatten genauso hoch
    assert last[17][1] >= last[17][0] - 1.0  # und kühlt mit ihr aus – gleiche Verluste, keine Scheinersparnis
    assert abs(last[18][1] - last[18][0]) <= 1.5  # die Wanne trifft beide gleich (danach lädt jeder auf sein Ziel)
    assert log["cold_sunday"] == 0 and m.comfort_margin == 0.0  # Wanne nach der Sonderladung: nicht zu kalt


def test_without_special_charge_the_bath_counts_as_cold():
    m, sh, cfg, log = _sim(3, legionella=False)
    assert log["cold_sunday"] >= 2  # Gegenprobe: ohne Legionellenladung ist die Wanne für die Strategie zu kalt


@pytest.mark.parametrize("flag", [True, False])
def test_bath_after_special_charge_is_covered_and_not_recommended_for(flag):
    m, sh, cfg, log = _sim(3, flag=flag)
    assert m.special_charges >= 2
    assert len(m.big_draws) >= 2 and all(b[2] for b in m.big_draws), m.big_draws
    sunday_17 = MONDAY + timedelta(days=6, hours=17)
    assert m.big_draw_expected(sunday_17, 2.0) is None
    rec = m.recommend(sunday_17, True, None, cfg)
    assert rec["hold"] <= cfg.dhw_comfort_temp + m.comfort_margin + 1 and not rec["reason"].startswith("große Zapfung")
    # So 12 Uhr, vor der Legionellenladung: seit 0.46 Komfort (jemand zu Hause und wach), nicht höher für die Wanne
    rec = m.recommend(MONDAY + timedelta(days=6, hours=12), True, None, cfg)
    assert cfg.dhw_comfort_temp <= rec["hold"] <= cfg.dhw_comfort_temp + m.comfort_margin + 1


def test_uncovered_bath_still_lifts_the_target():
    m, sh, cfg, log = _sim(3, legionella=False)
    assert len(m.big_draws) >= 2 and not any(b[2] for b in m.big_draws)
    sunday_17 = MONDAY + timedelta(days=6, hours=17)
    # während der Wanne springt der Kessel an – der Teil während der Ladung wird nur geschätzt
    assert m.big_draw_expected(sunday_17, 2.0) >= 30.0
    assert m.recommend(sunday_17, True, None, cfg)["target"] == cfg.dhw_max_temp


def test_old_big_draws_and_restart():
    m = DhwModel()
    m.big_draws = [["2026-01-04T18:00", 45.0], ["2026-01-11T18:00", 44.0]]  # Stand vor 0.32: ohne Deckung
    sunday_17 = MONDAY + timedelta(days=6, hours=17)
    assert m.big_draw_expected(sunday_17, 2.0) == 44.5
    m.big_draws.append(["2026-01-18T18:00", 43.0, True])
    m._last_special = MONDAY + timedelta(days=13, hours=13, minutes=20)
    again = DhwModel.from_dict(m.to_dict())
    assert again.big_draws == m.big_draws and again._last_special == m._last_special
    assert again.big_draw_expected(sunday_17, 2.0) == 44.5  # die gedeckte zählt nicht mit
    assert DhwModel.from_dict({**m.to_dict(), "last_special": None})._last_special is None


def test_engine_mirrors_the_special_charge():
    cfg = Config()
    cfg.boiler_nominal_kw = 18.0
    eng = Engine(cfg, Store(Path(tempfile.mkdtemp()) / "t.db"), Discovered())
    t0 = int(datetime(2026, 1, 11, 12, 0).timestamp())
    t = 55.0
    for i in range(60):
        ts = t0 + i * 300
        charging = 12 <= i < 30  # 13:00–14:30 Legionellenladung auf 70 °C
        if charging and t < 70:
            t = min(t + 1.0, 70.0)
        else:
            t -= 0.05
        eng.dhw_step(ts, {"dhw_temp": round(t, 1), "dhw": int(charging), "dhw_special": int(charging),
                          "burner_pow": 60 if charging else 0, "dhw_set": 55.0, "home_count": 1})
    assert eng.dhw_shadow.t_sim is not None and eng.dhw_shadow.t_sim >= t - 1.0
    assert eng.dhw_shadow.t_sim > 60.0  # ohne Spiegelung stünde der Schatten beim Soll 55
