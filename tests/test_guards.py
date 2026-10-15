"""Leitplanken: Das System darf sparen, aber nicht übertreiben."""

from datetime import datetime

from heatpilot import guards
from heatpilot.analysis import estimate_savings
from heatpilot.config import Config
from heatpilot.dhw import DhwModel
from heatpilot.models import HouseModel


def house(h_kw=0.2, c_kwh=3.0, updates=500):
    m = HouseModel()
    m.rls.theta = [h_kw, c_kwh, 0.2]
    m.rls.updates = updates
    return m


def week_hourly(t_in=21.0, t_out=2.0):
    start = int(datetime(2026, 10, 12, 0, 0).timestamp())
    rows = []
    for h in range(24 * 7):
        when = datetime.fromtimestamp(start + h * 3600)
        away = when.weekday() < 5 and 8 <= when.hour < 17
        rows.append({"ts": start + h * 3600, "t_in": t_in, "t_out": t_out,
                     "gas_kwh": 0.2 * (t_in - t_out) + 0.2, "home_frac": 0.0 if away else 1.0})
    return rows


def test_implausible_or_young_models_may_not_recommend():
    assert guards.check_house(house())["ok"]
    bad = guards.check_house(house(h_kw=0.0))
    assert not bad["ok"] and any("Wärmeverlust" in r for r in bad["reasons"])
    base_heavy = HouseModel()
    base_heavy.rls.theta, base_heavy.rls.updates = [0.036, 4.0, 2.8], 500
    assert not guards.check_house(base_heavy)["ok"]  # Verbrauch nur über Grundlast erklärt
    young = guards.check_house(house(updates=10))
    assert not young["ok"]
    assert not guards.check_dhw(DhwModel())["ok"]  # frisches Modell: erst lernen


def test_savings_need_realistic_preheat_and_count_cold_hours():
    cfg = Config()
    rows = week_hourly()
    ok = estimate_savings(rows, house(), cfg.away_temp, cfg.night_temp, cfg.is_night, p_max_kw=17)
    assert ok.cold_hours == 0 and ok.saved_kwh > 0
    # Träges Haus + schwache Therme: rechtzeitig wieder warm geht nur mit langem Vorlauf.
    # Die Strategie verzichtet dann lieber auf Absenkung, statt Kälte zu riskieren.
    weak = estimate_savings(rows, house(c_kwh=30), cfg.away_temp, cfg.night_temp, cfg.is_night,
                            p_max_kw=4.5)
    assert weak.cold_hours == 0
    assert weak.saved_kwh < ok.saved_kwh * 0.2


def test_strategy_never_goes_below_floors():
    """'Gar nicht heizen' ist keine erlaubte Strategie: Absenkung endet bei away/night."""
    cfg = Config()
    rows = week_hourly()
    res = estimate_savings(rows, house(c_kwh=0.5), cfg.away_temp, cfg.night_temp, cfg.is_night)
    max_possible = 0.2 * sum(21.0 - min(cfg.away_temp, cfg.night_temp) for _ in rows)
    assert res.saved_kwh < max_possible
    assert res.saved_pct < 15  # 2 K Absenkung tagsüber/3 K nachts – kein Wunderwert


def test_dhw_reserve_grows_after_cold_draw_but_is_capped_and_decays():
    m = DhwModel()
    for _ in range(20):
        m.note_cold_draw()
    assert m.comfort_margin == guards.DHW_MARGIN_MAX
    cfg = Config()
    rec = m.recommend(datetime(2026, 11, 2, 12, 0), False, None, cfg)
    assert rec["target"] == cfg.dhw_min_temp  # Reserve gilt nur für erwartete Nutzung
    when = datetime(2026, 11, 2, 0, 0)
    from datetime import timedelta
    for hour in range(24 * 10 + 1):  # 10 Tage (240 Stundenwechsel) ohne Problem
        m.step(when + timedelta(hours=hour), 1 / 12, 45.0, 45.0, False, None)
    assert m.comfort_margin <= guards.DHW_MARGIN_MAX - 10 * guards.DHW_MARGIN_DECAY + 0.01


def test_dhw_recommendation_stays_within_bounds():
    m = DhwModel()
    m.daily = [30.0] * 24  # absurd hoher Verbrauch erwartet
    cfg = Config()
    rec = m.recommend(datetime(2026, 11, 2, 6, 0), True, None, cfg)
    assert cfg.dhw_min_temp <= rec["target"] <= cfg.dhw_max_temp
