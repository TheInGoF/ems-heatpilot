"""Wochenraster Mo–So × Stunde und Anwesenheits-Kontrolle."""

from datetime import datetime, timedelta

from heatpilot import weekly
from heatpilot.models import HouseModel
from heatpilot.presence import PersonLearner
from heatpilot.store import Store


def _monday_ts() -> int:
    d = datetime(2026, 1, 5, 0, 0).astimezone()  # Montag
    return int(d.timestamp())


def test_weekday_peak_and_weather_normalised(tmp_path):
    store = Store(tmp_path / "w.db")
    start = _monday_ts()
    rows = []
    for i in range(28 * 24):
        ts = start + i * 3600
        d = datetime.fromtimestamp(ts).astimezone()
        gas = 1.0 + (1.5 if d.weekday() == 6 and d.hour == 18 else 0.0)  # Sonntagabend mehr
        vac = 1.0 if i < 24 else 0.0  # erster Tag Urlaub – zählt nicht
        rows.append((ts, 5.0, 21.0, 99.0 if vac else gas, 0.0, 1.0, vac))
    store.add_hourly(rows)
    out = weekly.collect(store, start + 28 * 86400, 30, HouseModel())
    gas = out["metrics"]["gas"]
    assert gas["grid"][6][18] == 2.5
    assert gas["grid"][0][0] == 1.0  # Urlaubsstunde (99 kWh) nicht mitgezählt
    assert gas["n"][0][0] == 3
    assert "So 18 Uhr" in gas["findings"][0]
    assert gas["per_day"][6] == 25.5
    assert out["metrics"]["gas_k"]["grid"][6][18] == round(2500 / 16)
    assert out["metrics"]["home"]["grid"][2][12] == 100


def test_starts_count_only_existing_hours(tmp_path):
    store = Store(tmp_path / "s.db")
    start = _monday_ts()
    store.add_hourly([(start + i * 3600, 5.0, 21.0, 1.0, 0.0, 1.0, 0.0) for i in range(14 * 24)])
    for week in range(2):
        t = start + week * 7 * 86400 + 6 * 3600  # Montag 06 Uhr: zwei Starts
        for k in range(2):
            store.add_burner_event(t + k * 1200, True)
            store.add_burner_event(t + k * 1200 + 600, False)
    out = weekly.collect(store, start + 14 * 86400, 14)
    st = out["metrics"]["starts"]
    assert st["grid"][0][6] == 2
    assert st["grid"][0][7] == 0


def _walk(p: PersonLearner, t: datetime, home: bool, dist, minutes: int, step: int = 2):
    for i in range(0, minutes, step):
        d = dist(i) if callable(dist) else dist
        p.observe(t + timedelta(minutes=i), home, d)
    return t + timedelta(minutes=minutes)


def test_arrival_logged_with_predictions():
    p = PersonLearner("person.a")
    t = datetime(2026, 1, 5, 7, 0)
    t = _walk(p, t, True, 0.0, 30)
    t = _walk(p, t, False, lambda i: min(i, 30.0), 60)       # wegfahren
    t = _walk(p, t, False, 30.0, 300)                         # bei der Arbeit
    t = _walk(p, t, False, lambda i: max(30.0 - i * 0.6, 0.5), 50)  # 36 km/h nach Hause
    assert p.status(t)["state"] in ("naehert", "heimfahrt")
    p.observe(t, True, 0.0)
    assert len(p.arrivals) == 1
    a = p.arrivals[0]
    assert a["max_km"] == 30.0
    checks = {c["before_min"]: c for c in a["checks"]}
    assert checks[15]["source"] == "gps"
    assert abs(checks[15]["error_min"]) <= 10
    assert p.arrival_quality()[15]["n"] == 1
    assert p.status(t)["state"] == "zuhause"
    d = PersonLearner.from_dict(p.to_dict())
    assert d.arrivals == p.arrivals


def test_short_absence_is_not_an_arrival():
    p = PersonLearner("person.a")
    t = datetime(2026, 1, 5, 7, 0)
    t = _walk(p, t, True, 0.0, 10)
    t = _walk(p, t, False, 0.2, 10)
    p.observe(t, True, 0.0)
    assert p.arrivals == []


def test_habit_hours_shape():
    p = PersonLearner("person.a")
    h = p.habit_hours()
    assert len(h) == 7 and all(len(r) == 24 for r in h) and h[0][0] == 0.5
