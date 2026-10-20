"""0.28: Warmwasser – Nennleistung aus den Einstellungen, Sonderladungen, Zapfung während der Ladung, Trefferquote."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from heatpilot.config import Config
from heatpilot.dhw import DhwModel
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store


def _charge(m, t0, start, rise_per_step=3.0, steps=6, dhw_set=55.0, draw_first=False):
    t = start
    for i in range(steps):
        nxt = t - 10.0 if (draw_first and i == 0) else t + rise_per_step
        m.step(t0 + timedelta(minutes=5 * i), 5 / 60, t, nxt, True, 12.0, dhw_set=dhw_set)
        t = nxt
    m.step(t0 + timedelta(minutes=5 * steps), 5 / 60, t, t, False, None, dhw_set=dhw_set)


def test_nominal_power_from_settings_is_used_for_charges():
    cfg = Config()
    cfg.boiler_nominal_kw = 18.0
    eng = Engine(cfg, Store(Path(tempfile.mkdtemp()) / "n.db"), Discovered())  # Kessel liefert keine Nennleistung
    t0 = int(datetime(2026, 10, 5, 4, 0).timestamp())
    temps = [44.0, 44.0, 47.5, 51.0, 54.5, 56.0, 56.0, 55.9, 55.9]  # 6 min ohne Anstieg: Nachlauf vorbei (0.32c)
    for i, temp in enumerate(temps):
        on = 2 <= i <= 5
        s = {"dhw_temp": temp, "burner_on": int(on), "burner_pow": 60.0 if on else 0.0, "flow": 70.0 if on else 40.0,
             "dhw": None}
        eng.infer_dhw(t0 + i * 120, s)
        eng.dhw_step(t0 + i * 120, s)
    assert eng.dhw.kwh_per_k_n["kalt"] == 1 and eng.dhw.kwh_per_k["kalt"] is not None
    assert eng.dhw_rec is not None  # Empfehlung bzw. Grund gibt es ab dem ersten Messpunkt


def test_legionella_charge_is_recognised_by_target():
    m = DhwModel()
    _charge(m, datetime(2026, 10, 4, 13, 0), 56.0, rise_per_step=3.4)  # 56 -> 76 °C bei Soll 55
    assert m.special_charges == 1 and m.charges == 0 and m.kwh_per_k_n["warm"] == 0


def test_charge_with_draw_counts_but_teaches_no_cost():
    m = DhwModel()
    _charge(m, datetime(2026, 10, 11, 18, 45), 45.0, rise_per_step=4.0, draw_first=True)  # Wanne beim Ladestart
    assert m.charges == 1 and m.kwh_per_k_n["kalt"] + m.kwh_per_k_n["warm"] == 0


def test_cost_average_weighted_by_measurements():
    m = DhwModel()
    m.kwh_per_k = {"kalt": 0.2, "warm": 0.4}
    m.kwh_per_k_n = {"kalt": 20, "warm": 1}
    assert abs(m.kwh_per_kelvin() - (0.2 * 5 + 0.4 * 1) / 6) < 1e-9


def test_regular_but_rare_use_keeps_water_ready():
    m = DhwModel()
    cfg = Config()
    m.hit_daily[18] = 0.3  # in knapp jeder dritten Woche wird um 18 Uhr gezapft (Mittelwert dabei klein)
    rec = m.recommend(datetime(2026, 10, 18, 17, 30), True, None, cfg)
    assert rec["target"] >= cfg.dhw_comfort_temp and "oft gezapft" in rec["reason"]


def test_comfort_margin_lifts_comfort_not_the_minimum():
    """Umgekehrt seit 0.46: Die Reserve hebt den Komfort (wach, zu Hause) – nachts gilt das Minimum."""
    m = DhwModel()
    cfg = Config()
    m.comfort_margin = 4.0
    assert m.recommend(datetime(2026, 10, 14, 1, 0), True, None, cfg)["hold"] == cfg.dhw_min_temp
    # seit 0.46.2: Komfort + Reserve ist das Soll (nicht mehr die gehaltene Untergrenze)
    assert m.recommend(datetime(2026, 10, 14, 12, 0), True, None, cfg)["target"] == cfg.dhw_comfort_temp + 4
