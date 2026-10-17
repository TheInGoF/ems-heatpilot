"""0.29: Anzeigen der Übersicht passen zum Modell (UI-Prüfung)."""

from datetime import datetime, timedelta

from heatpilot import certificate, flowstudy, weekly
from heatpilot.analysis import estimate_savings
from heatpilot.config import Config
from heatpilot.models import RoomModel
from heatpilot.presence import PersonLearner

from test_certificate import NOW, year_engine
from test_flowstudy import NOW as FS_NOW, fill
from test_guards import house, week_hourly
from test_weekly import _walk


def test_savings_percent_relative_to_heating_gas():
    cfg = Config()
    rows = week_hourly()
    base = estimate_savings(rows, house(), cfg.away_temp, cfg.night_temp, cfg.is_night, p_max_kw=17)
    with_dhw = [{**r, "dhw_energy_frac": 0.25} for r in rows]
    res = estimate_savings(with_dhw, house(), cfg.away_temp, cfg.night_temp, cfg.is_night, p_max_kw=17)
    assert res.actual_kwh == round(base.actual_kwh * 0.75, 1)
    assert res.saved_pct > base.saved_pct
    # Stunden ohne Warmwasser-Angabe (Langzeitstatistik) zählen nicht, sobald es die Angabe gibt
    mixed = [{**r, "dhw_frac": 0.0} if i % 2 else r for i, r in enumerate(rows)]
    part = estimate_savings(mixed, house(), cfg.away_temp, cfg.night_temp, cfg.is_night, p_max_kw=17)
    assert part.hours == len(rows) // 2


def test_gas_free_hours_let_the_setback_catch_up():
    """Hält Fremdwärme das echte Haus ohne Gas warm, wärmt sie das abgesenkte genauso."""
    start = int(datetime(2026, 10, 12, 0, 0).timestamp())
    rows = []
    for h in range(72):
        gas = 0.2 * 16 + 0.2 if h < 24 else 0.0  # erster Tag geheizt, dann gasfrei warm
        rows.append({"ts": start + h * 3600, "t_in": 21.0, "t_out": 5.0, "gas_kwh": gas, "home_frac": 0.0})
    res = estimate_savings(rows, house(), 16.0, 16.0, lambda t: True, p_max_kw=17)
    day = {d["day"]: d["saved_kwh"] for d in res.daily}
    d2, d3 = (day[(datetime.fromtimestamp(start) + timedelta(days=k)).date().isoformat()] for k in (1, 2))
    assert 0 < d3 < d2 * 0.5


def test_floor_room_reaches_target_like_required_flow():
    m = RoomModel("Bad", floor=True)
    m.rls.theta = [0.02, 0.03, 0.05, 0.0, 0.0, 0.0, 0.0]
    assert round(m.required_flow(21.0, 5.0)) == 37
    assert m.equilibrium(45.0, 5.0) == 25.0  # Fußboden heizt im Dauerbetrieb mit
    hours = m.time_to_reach(19.0, 21.0, 45.0, 5.0)
    plain = RoomModel("Wz")
    plain.rls.theta = [0.05, 0.05, 0.0, 0.0, 0.0, 0.0]
    assert hours is not None and hours > plain.time_to_reach(19.0, 21.0, 45.0, 5.0)


def test_certificate_cycling_cost_not_extrapolated(tmp_path):
    data = certificate.collect(year_engine(tmp_path, days=40), NOW)
    ce = data["boiler"]["cycling_extra"]
    assert ce["full_year"] is False and ce["days"] == 40
    v = {x["area"]: x for x in data["verdicts"]}
    assert "in den bisher gemessenen 40 Tagen" in v["Kesselgröße"]["text"]
    assert "im Jahr" not in v["Kesselgröße"]["text"]
    assert data["boiler"]["starts_heat"] is True
    assert v["Taktverhalten"]["text"].startswith("Im Schnitt 24 Heizstarts am Tag")


def test_week_starts_findings_in_starts_per_day(tmp_path):
    m = {"grid": [[1.0] * 24 for _ in range(7)], "unit": "je h", "per_day": [24.0, 24, 24, 24, 24, 30, 24]}
    text = " ".join(weekly.findings("starts", m))
    assert "30 Starts je Tag" in text and "je h je Tag" not in text


def test_flow_study_subtracts_free_heat(tmp_path):
    store = fill(tmp_path, gas_low=0.20, gas_high=0.18)
    plain = flowstudy.study(store, FS_NOW)
    adj = flowstudy.study(store, FS_NOW, offset_kw=-1.0)
    c0 = plain["bands"][0]["cells"][0]["gas_per_kh"]
    c1 = adj["bands"][0]["cells"][0]["gas_per_kh"]
    assert round(c1 - c0, 3) == round(1.0 / 19.0, 3)
    assert "Vorläufig" in plain["verdict"]["text"] and "Vorläufig" not in adj["verdict"]["text"]


def test_eta_keeps_gps_during_short_rest():
    p = PersonLearner("person.a")
    t = datetime(2026, 1, 5, 7, 0)
    t = _walk(p, t, True, 0.0, 30)
    t = _walk(p, t, False, lambda i: min(i, 40.0), 60)
    t = _walk(p, t, False, 40.0, 300) + timedelta(minutes=25)       # Funkloch beim Losfahren
    t = _walk(p, t, False, lambda i: max(40.0 - i * 0.6, 10.0), 50)  # 36 km/h heimwärts
    assert p.status(t)["state"] == "heimfahrt"
    t = _walk(p, t, False, 10.0, 20)                                # kurze Rast
    assert p.status(t)["state"] == "heimfahrt"
    assert (p.approach_speed_now(t) or 0) <= 3  # GPS-Tempo allein gäbe keine Ankunft mehr
    eta = p.eta(t)
    assert eta["source"] == "gps" and eta["eta"] > t



def test_flow_study_skips_hours_below_heating_limit(tmp_path):
    store = fill(tmp_path, gas_low=0.20, gas_high=0.18)  # alle Stunden: 19 K innen–außen
    assert flowstudy.study(store, FS_NOW, offset_kw=-1.0, heating_limit_k=10.0)["hours"] > 0
    assert flowstudy.study(store, FS_NOW, offset_kw=-1.0, heating_limit_k=18.0)["hours"] == 0


def test_certificate_cycling_ignores_hours_without_dhw_info(tmp_path):
    eng = year_engine(tmp_path, days=40)
    full = certificate.collect(eng, NOW)["boiler"]["cycling_extra"]
    # ein Jahr Langzeitstatistik davor: ohne Warmwasser-Angabe, zählt nicht als Heizgas
    old = [(NOW - 400 * 86400 + i * 3600, 5.0, 20.5, 2.0, None, None) for i in range(300 * 24)]
    eng.store.add_hourly(old)
    eng.store.commit()
    again = certificate.collect(eng, NOW)["boiler"]["cycling_extra"]
    assert again["kwh_lo"] == full["kwh_lo"] and again["full_year"] is False


def test_savings_year_projection_counts_vacation_days():
    cfg = Config()
    rows = week_hourly()
    rows = [{**r, "vacation_frac": 1.0} if i >= len(rows) - 48 else r for i, r in enumerate(rows)]
    res = estimate_savings(rows, house(), cfg.away_temp, cfg.night_temp, cfg.is_night, p_max_kw=17)
    assert res.hours == len(rows) - 48 and res.span_hours == len(rows)
    assert res.dhw_separated is False


def test_preheat_plan_names_unreachable_room():
    from heatpilot.analysis import preheat_plan
    weak = RoomModel("Bad")
    weak.rls.theta = [0.005, 0.1, 0.0, 0.0, 0.0, 0.0]  # Heizkörper viel zu klein
    now = datetime(2026, 1, 5, 12, 0)
    plan = preheat_plan([(weak, 17.0, 21.0)], house(), now + timedelta(hours=3), now, -5.0, 75.0)
    assert plan["feasible"] is False and plan["unreachable"] == "Bad"
