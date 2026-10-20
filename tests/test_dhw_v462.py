"""0.46.2: Die Komforttemperatur der Einstellungen ist das Warmwasser-Soll (Speicher Komfort − Hysterese bis
Komfort); nur das Minimum bekommt die Hysterese dazu, eine große Zapfung Minimum + Bedarf ohne Hysterese.
Aufheizdauer = (Komfort − Minimum) / Laderate + 0,5 h; die alte Reserve wird einmal zurückgesetzt."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from heatpilot import dhw_target as dt
from heatpilot.config import Config
from heatpilot.dhw import DhwModel, DhwShadow
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

ROOT = Path(__file__).resolve().parents[1]
MON_NOON = datetime(2026, 10, 19, 12, 0)  # Montag; Nacht laut Standardplan So 21:00 → Mo 04:30
NIGHT_END = datetime(2026, 10, 19, 4, 30)


def _cfg(dhw_min=40.0, comfort=50.0, dhw_max=60.0) -> Config:
    cfg = Config()
    cfg.dhw_min_temp, cfg.dhw_comfort_temp, cfg.dhw_max_temp = dhw_min, comfort, dhw_max
    return cfg


# ------------------------------------------------------------------ Soll
def test_comfort_is_the_setpoint_and_the_store_swings_below_it():
    cfg, m = _cfg(), DhwModel()
    rec = m.recommend(MON_NOON, True, None, cfg, hyst=-5)
    assert rec["target"] == 50 and rec["hold"] == 45.0 and rec["margin_bound"]
    assert rec["reason"] == "jemand zu Hause – Speicher 45–50 °C"
    rec = m.recommend(MON_NOON, True, None, cfg)  # Kessel meldet keine Hysterese: 5 K angenommen, Soll gleich
    assert rec["target"] == 50 and rec["reason"] == "jemand zu Hause – Speicher 45–50 °C (Hysterese 5 K angenommen)"
    assert m.recommend(MON_NOON, True, None, cfg, hyst=-10)["target"] == 50  # größere Hysterese: Soll bleibt


def test_night_away_and_vacation_drop_to_minimum_plus_hysteresis():
    cfg, m = _cfg(), DhwModel()
    for rec in (m.recommend(datetime(2026, 10, 19, 1, 0), True, None, cfg, hyst=-5),
                m.recommend(MON_NOON, False, MON_NOON + timedelta(hours=8), cfg, hyst=-5),
                m.recommend(MON_NOON, False, None, cfg, hyst=-5, vacation=True)):
        assert rec["target"] == 45 and rec["hold"] == 40.0 and "Speicher darf bis 40 °C fallen" in rec["reason"]
    assert m.recommend(datetime(2026, 10, 19, 1, 0), True, None, cfg, hyst=-10)["target"] == 50  # Minimum + 10


def test_big_draw_is_minimum_plus_need_without_hysteresis():
    cfg, m = _cfg(dhw_max=70.0), DhwModel()
    monday_7 = datetime(2026, 10, 19, 7, 0)
    m.big_draws = [[(monday_7 - timedelta(days=7 * w)).isoformat(timespec="minutes"), 25.0, False] for w in (1, 2)]
    rec = m.recommend(monday_7 - timedelta(minutes=30), True, None, cfg, hyst=-5)
    assert rec["target"] == 65 and rec["hold"] == 60.0  # 40 + 25, nicht 70
    assert rec["reason"].startswith("große Zapfung üblich (≈25 K)") and not rec["margin_bound"]
    m.big_draws = [[b[0], 8.0, False] for b in m.big_draws]  # kleiner als Komfort − Minimum: Komfort bleibt
    assert m.recommend(monday_7 - timedelta(minutes=30), True, None, cfg, hyst=-5)["target"] == 50


def test_draw_seen_one_hour_ahead_even_when_charging_fast():
    cfg, m = _cfg(), DhwModel()
    m.charge_rate_kh = 60.0  # Aufheizdauer 10 K / 60 K/h + 0,5 h < 1 h
    m.daily[7] = 15.0  # Dusche um 7 Uhr
    rec = m.recommend(datetime(2026, 10, 19, 6, 30), True, None, cfg, hyst=-5)
    assert rec["expects_draw"] and rec["target"] == 55  # Minimum 40 + 15 K, schon eine halbe Stunde vorher


# ------------------------------------------------------------------ Aufheizdauer
def test_heating_starts_comfort_minus_minimum_over_rate_plus_half_an_hour_before_night_end():
    for dhw_min, rate in ((40.0, 20.0), (30.0, 20.0), (30.0, 40.0)):
        cfg, m = _cfg(dhw_min=dhw_min), DhwModel()
        m.charge_rate_kh = rate
        lead = timedelta(hours=(50.0 - dhw_min) / rate + dt.BUFFER_H)
        assert dt.BUFFER_H == 0.5
        early = m.recommend(NIGHT_END - lead - timedelta(minutes=5), True, None, cfg, hyst=-5)
        start = m.recommend(NIGHT_END - lead, True, None, cfg, hyst=-5)
        assert early["hold"] == dhw_min and early["reason"].startswith("Nacht"), (dhw_min, rate)
        assert start["target"] == 50 and start["reason"].startswith("vor dem Aufstehen aufheizen"), (dhw_min, rate)
    # dasselbe vor der Ankunft (Minimum 40, 20 K/h: 1 h vorher)
    cfg, m = _cfg(), DhwModel()
    assert m.recommend(MON_NOON, False, MON_NOON + timedelta(minutes=65), cfg, hyst=-5)["hold"] == 40.0
    assert m.recommend(MON_NOON, False, MON_NOON + timedelta(hours=1), cfg, hyst=-5)["target"] == 50


def test_heating_lead_uses_comfort_plus_reserve_capped():
    cfg, m = _cfg(), DhwModel()
    m.comfort_margin = 4.0  # Soll 54: (54 − 40) / 20 + 0,5 = 1,2 h
    assert m.recommend(NIGHT_END - timedelta(minutes=72), True, None, cfg, hyst=-5)["target"] == 54
    assert m.recommend(NIGHT_END - timedelta(minutes=77), True, None, cfg, hyst=-5)["hold"] == 40.0


# ------------------------------------------------------------------ Reserve, Schatten, Ankunft
def _engine(path: Path) -> Engine:
    cfg = Config()
    cfg.boiler_nominal_kw = 18.0
    return Engine(cfg, Store(path), Discovered())


def test_reserve_from_the_old_strategy_is_reset_once(tmp_path):
    db = tmp_path / "t.db"
    old = DhwModel()
    old.comfort_margin = 6.0
    old.note_household(0, False)  # alle gerade weg: Zapfprofil samt Reserve gesichert
    assert old._backup["comfort_margin"] == 6.0
    Store(db).put("model.dhw", old.to_dict())
    eng = _engine(db)
    assert eng.dhw.comfort_margin == 0.0 and eng.dhw._backup["comfort_margin"] == 0.0
    assert eng.store.get("dhw.margin_reset_0462") is True
    assert eng.store.get("model.dhw")["comfort_margin"] == 0.0  # gleich gespeichert, nicht erst beim nächsten Takt
    eng.dhw.note_cold_draw()  # neu gelernte Reserve bleibt beim nächsten Start
    eng.save()
    assert _engine(db).dhw.comfort_margin == 2.0


def test_shadow_draw_inside_the_band_is_not_cold_and_does_not_raise_the_reserve():
    m = DhwModel()
    m.loss.theta = [0.8, -0.04]
    for t0, cold in ((46.0, 0), (44.0, 1)):
        out = DhwShadow(t_sim=t0).step(m, t0, 45.0, 3.0, 5 / 60, 50.0, dhw_min=40.0, margin_bound=True,
                                       dhw_max=60.0, top=50)
        assert out["cold_draw"] == cold and out["raise_margin"] == cold, t0
    # ohne ``top`` (ältere Aufrufer) wie bisher: unter Komfort ist zu kalt
    assert DhwShadow(t_sim=46.0).step(m, 46.0, 45.0, 3.0, 5 / 60, 50.0)["cold_draw"] == 1


def test_shadow_credits_the_boiler_reloading_during_a_draw():
    m = DhwModel()
    m.loss.theta = [0.8, -0.04]
    m.charge_rate_kh = 60.0

    def shower(k, t0=50.0, dt_h=5 / 60):
        sh = DhwShadow(t_sim=t0)
        out = sh.step(m, t0, 45.0, k, dt_h, 50.0, dhw_min=40.0, margin_bound=True, dhw_max=60.0, top=50)
        return out["cold_draw"], round(sh._ev["low"], 2)

    # 12 K in 5 min: nach ≈2,1 min unter 45 °C, danach lädt der Kessel ≈2,9 K nach – tiefster Stand ≈40,9 °C
    assert shower(12.0) == (0, 40.92)
    assert shower(16.0)[0] == 1  # mehr, als er in der Zeit nachlädt: zu kalt
    assert shower(12.0, dt_h=15 / 60) == (0, 40.92)  # längerer Messschritt: nicht mehr als 5 min Nachladen
    sh = DhwShadow(t_sim=50.0)  # bleibt über 45 °C: nichts angerechnet
    sh.step(m, 50.0, 45.0, 4.0, 5 / 60, 50.0, dhw_min=40.0, top=50)
    assert sh._ev["low"] == 46.0
    # Wanne über drei Schritte: lädt ab dem Unterschreiten von 50 °C durchgehend nach (5 K je 5 min)
    sh = DhwShadow(t_sim=55.0)
    for _ in range(3):
        out = sh.step(m, 55.0, 50.0, 8.0, 5 / 60, 50.0, dhw_min=30.0, top=55)
        assert out["cold_draw"] == 0
    assert round(sh._ev["low"], 3) == 42.875  # ohne Nachladen 31 °C


def test_morning_shower_with_minimum_check_only_counts_what_the_boiler_cannot_cover(monkeypatch):
    """Simulation aus test_dhw (Dusche 15 K in einem 5-min-Schritt, Laderate ≈51 K/h, Hysterese 5 K), mit der
    Minimum-Prüfung wie in der Engine, verglichen über die letzten 14 Tage: Ohne Nachladen zogen 15 Zapfungen
    (14 Morgenduschen, ein Abwasch) unter 40 °C, bis 33,7 °C; mit Nachladen bleiben 5 Morgenduschen, die der
    Kessel in den 5 min nicht ausgleicht (tiefster Stand 37,8–39,4 °C)."""
    import test_dhw

    steps, events, step = [0], [], DhwShadow.step

    def with_minimum(self, *a, **kw):
        out = step(self, *a, dhw_min=40.0, **kw)
        steps[0] += 1
        if out["cold_draw"] and steps[0] > 14 * 288:
            events.append(self._ev["low"])
        return out

    monkeypatch.setattr(DhwShadow, "step", with_minimum)
    test_dhw.simulate(set_t=60.0, hyst=5.0)
    assert len(events) <= 5 and min(events) >= 37.5, events  # höchstens 2,5 K unter dem Minimum


def test_recommendation_says_whether_the_store_may_drop():
    cfg, m = _cfg(), DhwModel()
    assert m.recommend(datetime(2026, 10, 19, 1, 0), True, None, cfg, hyst=-5)["drop"] is True
    assert m.recommend(MON_NOON, True, None, cfg, hyst=-5)["drop"] is False


def test_charge_before_arrival_goes_to_comfort_not_comfort_plus_hysteresis(tmp_path):
    eng = _engine(tmp_path / "t.db")
    eng.ems = {"boiler": {"dhw_curtemp": 35.0, "dhw_hyston": -5.0}}
    now = datetime(2026, 10, 19, 16, 0)
    out = eng.dhw_for_arrival(now + timedelta(hours=1), now)
    assert out["to"] == 50.0 and out["from"] == 35.0


def test_version_changelog_docs():
    from heatpilot import __version__
    assert __version__ == "0.46.2"
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert log.split("\n## ")[1].startswith("0.46.2\n")
    head = log.split("\n## 0.46.2\n")[1].split("\n## ")[0]
    for t in ("Komforttemperatur ist das Soll", "Minimum + Hysterese", "0,5 h", "dhw.margin_reset_0462",
              "DRAW_SPREAD_H", "Speicher darf bis hier fallen", "Zertifikat", "tests/test_dhw_v462.py"):
        assert t in head, t
    docs = (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
    assert "Die Komforttemperatur der Einstellungen ist das Soll" in docs and "+ 0,5 h Puffer" in docs
