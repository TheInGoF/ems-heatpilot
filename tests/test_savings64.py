"""0.64: Ersparnis gegenüber festem Zeitplan (Stunden je Woche) und gegenüber früher – Heizgas je Gradtag (20/15),
Vergleichszeitraum wählbar (Konzeptpapier, Abschnitt 8)."""

from datetime import date, datetime, timedelta

import pytest

from heatpilot import savings_compare as sc
from heatpilot.config import Config
from heatpilot.store import Store

from test_sleep36 import berlin  # noqa: F401


def fill(store, start: date, days: int, t_out: float, kwh_h: float, home=1.0):
    t0 = int(datetime.combine(start, datetime.min.time()).timestamp())
    # Reihenfolge von HOURLY_COLS: ts, t_out, t_in, gas_kwh, dhw_frac, home_frac, vacation_frac
    store.add_hourly([(t0 + h * 3600, t_out, 20.0, kwh_h, 0.0, home, 0.0) for h in range(days * 24)])
    store.commit()


def test_history_per_degree_day(tmp_path, berlin):  # noqa: F811
    store = Store(tmp_path / "s.db")
    now = datetime(2026, 11, 30, 12)
    fill(store, date(2025, 11, 1), 30, 5.0, 2.0)            # Vorjahr: 48 kWh/Tag bei 15 Kd → 3,2 kWh/Kd
    fill(store, now.date() - timedelta(days=30), 30, 0.0, 2.4)  # jetzt: 57,6 kWh/Tag bei 20 Kd → 2,88 kWh/Kd
    out = sc.compare(store, int(now.timestamp()))
    assert out["ref"] is None and "Vergleichszeitraum wählen" in out["note"]
    assert sc.set_ref(store, "2025-11-01", "2025-11-30") == {"from": "2025-11-01", "to": "2025-11-30"}
    out = sc.compare(store, int(now.timestamp()))
    assert out["ref"]["kwh_per_kd"] == 3.2 and out["recent"]["kwh_per_kd"] == 2.88 and out["saved_pct"] == 10.0
    with pytest.raises(ValueError):
        sc.set_ref(store, "2025-12-01", "2025-11-01")
    assert sc.set_ref(store, "", "") is None and sc.compare(store, int(now.timestamp()))["ref"] is None


def test_time_saving_nights_and_absence(tmp_path, berlin):  # noqa: F811
    store = Store(tmp_path / "s.db")
    now = datetime(2026, 11, 30, 0)
    fill(store, now.date() - timedelta(days=7), 7, 5.0, 1.0, home=0.0)  # eine Woche niemand da
    cfg = Config()  # Wochenplan 21:00–04:30 (Mo–Fr), gelernt: werktags erst 05:30 auf
    cfg.night_plan_learned = [{"start": None, "end": "05:30"}] * 7
    t = sc.time_saving(store, cfg, int(now.timestamp()))
    assert t["learned_used"] is False and t["night_h_week"] == 0.0 and t["night_h_possible"] > 0
    assert t["away_h_week"] > 100  # alles außerhalb der festen Nacht
    cfg.night_plan_auto = True
    t2 = sc.time_saving(store, cfg, int(now.timestamp()))
    assert t2["learned_used"] and t2["night_h_week"] == t["night_h_possible"]
    assert t2["total_h_week"] == round(t2["night_h_week"] + t2["away_h_week"], 1)
