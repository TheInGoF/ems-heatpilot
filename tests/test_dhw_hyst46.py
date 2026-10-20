"""0.46: Warmwasser-Ziel = tiefste gehaltene Speichertemperatur, Soll = Ziel + Einschalthysterese des Kessels.

Nachts, bei niemandem zu Hause und im Urlaub darf der Speicher bis zum Minimum fallen; wer zu Hause und wach ist,
bekommt Komfort. Empfehlung, Vorschau, Weg W und der Schatten der Ersparnis nutzen dasselbe Ziel.
Seit 0.46.2 ist Komfort (+ Reserve) bzw. Minimum + Bedarf selbst das Soll; nur das Minimum bekommt die Hysterese
dazu (neue Fälle: ``test_dhw_v462.py``).
"""

from datetime import datetime, timedelta
from pathlib import Path

from heatpilot import dhw_control
from heatpilot import dhw_target as dt
from heatpilot import preview as pv
from heatpilot.config import Config
from heatpilot.dhw import DhwModel

from test_control34 import SUN, at, run, ts_of
from test_dhw_control38 import make
from test_preview37 import by_hour, dhw_model, house

ROOT = Path(__file__).resolve().parents[1]
MON_NOON = datetime(2026, 10, 19, 12, 0)  # Montag; Nacht laut Standardplan So 21:00 → Mo 04:30


def _cfg(dhw_min=30.0, comfort=50.0, dhw_max=60.0) -> Config:
    cfg = Config()
    cfg.dhw_min_temp, cfg.dhw_comfort_temp, cfg.dhw_max_temp = dhw_min, comfort, dhw_max
    return cfg


# ------------------------------------------------------------------ Soll = Ziel + Hysterese
def test_setpoint_is_hold_plus_hysteresis():
    cfg, m = _cfg(), DhwModel()
    night = datetime(2026, 10, 19, 1, 0)
    for hyst, target in ((-10.0, 40), (-5.0, 35), (5.0, 35)):  # Vorzeichen egal (EMS-ESP meldet −10)
        rec = m.recommend(night, True, None, cfg, hyst=hyst)
        assert rec["hold"] == 30.0 and rec["target"] == target and not rec["hyst_assumed"], hyst
        assert rec["reason"] == "Nacht – Speicher darf bis 30 °C fallen"
    rec = m.recommend(night, True, None, cfg)  # Kessel meldet keine Hysterese: 5 K angenommen
    assert rec["target"] == 35 and rec["hyst"] == 5.0 and rec["hyst_assumed"]
    assert "Hysterese 5 K angenommen" in rec["reason"]
    assert dt.hysteresis("-10") == (10.0, False) and dt.hysteresis(None) == (dt.HYST_ASSUMED_K, True)


def test_setpoint_rounds_up_and_stops_at_the_maximum():
    cfg, m = _cfg(), DhwModel()
    m.comfort_margin = 1.3
    rec = m.recommend(MON_NOON, True, None, cfg, hyst=-5)
    assert rec["target"] == 52 and rec["hold"] == 47.0  # seit 0.46.2: 51,3 ist das Soll, aufgerundet 52
    cfg.dhw_comfort_temp = 58.0
    rec = m.recommend(MON_NOON, True, None, cfg, hyst=-5)
    assert rec["target"] == 60 and rec["hold"] == 55.0 and not rec["margin_bound"]  # am Maximum: Reserve hebt nichts
    assert dt.setpoint(50.0, 4.5, cfg) == 55 and dt.setpoint(50.0, 5.0, cfg) == 55
    assert dt.capped(51.3, cfg) == 52 and dt.capped(52.0, cfg) == 52 and dt.capped(61.0, cfg) == 60


# ------------------------------------------------------------------ Zustände
def test_night_drops_to_minimum_and_heats_up_before_waking():
    # seit 0.46.2: Laderate 20 K/h, Aufheizen 30 → 50 °C (Komfort ist das Soll) 1 h, + 0,5 h Puffer
    cfg, m = _cfg(), DhwModel()
    assert m.recommend(datetime(2026, 10, 19, 2, 30), True, None, cfg, hyst=-5)["hold"] == 30.0  # 2 h vor Ende
    rec = m.recommend(datetime(2026, 10, 19, 3, 0), True, None, cfg, hyst=-5)  # 1,5 h vor dem Nachtende
    assert rec["hold"] == 45.0 and rec["target"] == 50
    assert rec["reason"] == "vor dem Aufstehen aufheizen – Speicher 45–50 °C"
    assert m.recommend(datetime(2026, 10, 19, 2, 30), None, None, cfg, hyst=-5)["hold"] == 30.0  # unbekannt: auch
    # Nachtplan der Einstellungen gilt (hier 23:00–07:00 an allen Tagen)
    cfg.night_plan = [{"start": "23:00", "end": "07:00"} for _ in range(7)]
    assert m.recommend(datetime(2026, 10, 19, 22, 30), True, None, cfg, hyst=-5)["target"] == 50
    assert m.recommend(datetime(2026, 10, 19, 23, 30), True, None, cfg, hyst=-5)["hold"] == 30.0
    assert m.recommend(datetime(2026, 10, 20, 5, 0), True, None, cfg, hyst=-5)["hold"] == 30.0
    assert m.recommend(datetime(2026, 10, 20, 5, 30), True, None, cfg, hyst=-5)["target"] == 50


def test_nobody_home_and_vacation_drop_to_minimum_until_shortly_before_arrival():
    cfg, m = _cfg(), DhwModel()
    rec = m.recommend(MON_NOON, False, MON_NOON + timedelta(hours=6), cfg, hyst=-5)
    assert rec["hold"] == 30.0 and rec["reason"] == "niemand zu Hause – Speicher darf bis 30 °C fallen"
    assert not rec["margin_bound"]
    rec = m.recommend(MON_NOON, False, None, cfg, hyst=-5, vacation=True)
    assert rec["hold"] == 30.0 and rec["reason"].startswith("Urlaub – Speicher darf bis 30 °C fallen")
    # seit 0.46.2: Aufheizdauer 1 h + 0,5 h Puffer – 2 h vor der Ankunft darf er noch fallen, 1,5 h vorher heizt er
    assert m.recommend(MON_NOON, False, MON_NOON + timedelta(hours=2), cfg, hyst=-5)["hold"] == 30.0
    rec = m.recommend(MON_NOON, False, MON_NOON + timedelta(hours=1.5), cfg, hyst=-5)
    assert rec["target"] == 50 and rec["reason"] == "vor der Ankunft aufheizen – Speicher 45–50 °C"
    # Ankunft unbekannt (Gewohnheit lernt noch): das heutige Soll bleibt
    rec = m.recommend(MON_NOON, False, None, cfg, hyst=-5, unknown_from=MON_NOON, current_set=48.0)
    assert rec["target"] == 48 and rec["hold"] == 43.0 and "Ankunft unbekannt" in rec["reason"]


def test_someone_home_and_awake_keeps_comfort_not_minimum_plus_reserve():
    cfg, m = _cfg(), DhwModel()
    m.comfort_margin = 3.0
    rec = m.recommend(MON_NOON, True, None, cfg, hyst=-5)  # keine Zapfung erwartet
    # seit 0.46.2: Komfort + Reserve ist das Soll (53), der Speicher schwankt 48–53 °C
    assert rec["hold"] == 48.0 and rec["target"] == 53 and rec["margin_bound"] and not rec["expects_draw"]
    assert rec["reason"] == "jemand zu Hause – Speicher 48–53 °C"
    assert m.recommend(MON_NOON, None, None, cfg, hyst=-5)["target"] == 53  # Anwesenheit unbekannt: auch


def test_big_draw_lifts_to_minimum_plus_need():
    cfg, m = _cfg(dhw_max=70.0), DhwModel()
    monday_7 = datetime(2026, 10, 19, 7, 0)
    m.big_draws = [[(monday_7 - timedelta(days=7 * w)).isoformat(timespec="minutes"), 30.0, False] for w in (1, 2)]
    rec = m.recommend(monday_7 - timedelta(minutes=30), True, None, cfg, hyst=-5)
    assert rec["target"] == 60 and rec["hold"] == 55.0  # Minimum 30 + 30 K, über Komfort (seit 0.46.2 das Soll)
    assert rec["reason"].startswith("große Zapfung üblich (≈30 K)") and not rec["margin_bound"]


def test_hint_only_for_hysteresis_above_five_kelvin():
    assert "Hysterese am Kessel 10 K" in dt.hyst_hint(10.0, False)
    assert dt.hyst_hint(5.0, False) is None and dt.hyst_hint(5.0, True) is None


# ------------------------------------------------------------------ derselbe Weg überall
def test_preview_control_and_shadow_use_the_same_target(tmp_path):
    cfg = Config.from_raw({"control_mode": "heatpilot"})
    eng, _, _ = house(tmp_path / "p", cfg=cfg)
    eng.dhw = dhw_model()
    eng.ems.setdefault("boiler", {})["dhw_hyston"] = -10.0
    h = by_hour(pv.preview(eng, ts_of(SUN, 10)))
    rec = eng.dhw.recommend(at(SUN, 12), None, None, eng.cfg, hyst=-10.0)
    # seit 0.46.2: Komfort 50 ist das Soll (Speicher 40–50 °C); nur das Minimum bekommt die Hysterese dazu
    assert h["12:00"]["dhw"] == rec["target"] == 50 and rec["hold"] == 40.0
    assert h["21:00"]["dhw"] == 50 and h["21:00"]["dhw_reason"].startswith("Nacht")  # Minimum 40 + 10

    eng, ha, dc = make(tmp_path / "w")
    eng.dhw = dhw_model()
    eng.ems = {"boiler": {"dhw_hyston": -10.0}}
    t = ts_of(SUN, 12)
    eng.dhw_recommend(t, {"home_count": 1, "dhw_set": 45.0})
    assert eng.dhw_rec["target"] == 50 and eng.dhw_rec["hold"] == 40.0  # seit 0.46.2: Komfort ist das Soll
    assert dhw_control.wish(eng.dhw_rec["target"], eng.cfg) == 50
    dc.plan(t, {}, now=t)
    run(dc.apply(now=t))
    assert ha.writes()[-1]["service_data"] == {"value": 50}  # Weg W schreibt dasselbe Soll

    # Schatten der Ersparnis: lädt wie der Kessel erst unter 40 °C, dann bis 50 °C
    eng.dhw.kwh_per_k, eng.dhw.kwh_per_k_n = {"kalt": 0.2, "warm": None}, {"kalt": 10, "warm": 0}
    eng.dhw_shadow.t_sim = 45.0
    for i in range(2):
        eng.dhw_step(t + i * 300, {"dhw_temp": 49.0, "dhw": 0, "vacation": 0, "home_count": 1})
    assert 44.0 < eng.dhw_shadow.t_sim < 45.0  # im Band 40–50 °C: lädt nicht
    eng.dhw_shadow.t_sim = 39.0
    eng.dhw_step(t + 600, {"dhw_temp": 49.0, "dhw": 0, "vacation": 0, "home_count": 1})
    assert eng.dhw_shadow.t_sim == 50.0
    eng.dhw_step(t + 900, {"dhw_temp": 49.0, "dhw": 0, "vacation": 0, "home_count": 1})
    assert 49.0 < eng.dhw_shadow.t_sim < 50.0  # am Soll: kühlt aus, lädt nicht gleich wieder


def test_ui_card_and_files():
    html = (ROOT / "ems_heatpilot" / "heatpilot" / "static" / "index.html").read_text(encoding="utf-8")
    # seit 0.46.2 wie der Grundtext: Bereich bzw. „darf bis … fallen“, die Hysterese nur, wenn angenommen
    assert "Speicher bleibt über" not in html
    assert "`Speicher darf bis ${fmt(rec.hold)} °C fallen` : `Speicher ${fmt(rec.hold)}–${fmt(rec.target, 0)} °C`" in html
    assert 'rec.hyst_assumed ? ` (Hysterese ${fmt(rec.hyst)} K angenommen)` : ""' in html
    assert "darf bis" in html  # seit 0.46.3 Zeitstrahl statt Graf mit Legende
    assert 'id="dhw-hyst-note"' in html and "w.hyst_hint" in html
    for name in ("dhw_target.py", "dhw.py", "engine_dhw.py", "dhw_watch.py", "preview.py"):
        text = (ROOT / "ems_heatpilot" / "heatpilot" / name).read_text(encoding="utf-8")
        assert text.startswith('"""') and len(text.splitlines()) <= 600, name
