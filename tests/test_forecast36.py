"""0.36.1 Wettervorhersage sichtbar: Punkt nur in der Nähe, Kachel „Vorhersage“, Vorheizplan sagt, womit er rechnet.

Keine Modell- oder Steuerungsänderung – die Steuerung nutzt die Vorhersage erst in 0.37.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

import heatpilot.engine as engmod
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import FORECAST_MAX_GAP_MIN, Engine, parse_time
from heatpilot.store import Store

NOW = int(datetime(2026, 10, 19, 14, 0).timestamp())  # volle Stunde, Ortszeit


def make(tmp_path, weather="weather.home", db="f.db", **kw):
    found = Discovered(rooms=[Room("Wz", "climate.wz")], outdoor=["sensor.out"], weather=weather, **kw)
    eng = Engine(Config(), Store(tmp_path / db), found)
    eng.states["sensor.out"] = {"state": "8.0", "attributes": {}}
    return eng


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def hourly(start_ts: int, temps: list[float]) -> list[dict]:
    """Stündliche Vorhersage wie ``weather.get_forecasts`` (ISO mit Offset)."""
    return [{"datetime": iso(start_ts + i * 3600), "temperature": t, "condition": "cloudy"} for i, t in enumerate(temps)]


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# ------------------------------------------------------------------ Punkt am Zeitpunkt
def test_point_only_within_90_minutes(tmp_path):
    eng = make(tmp_path)
    eng.forecast = hourly(NOW, [5.0, 4.0, 3.0])  # 14:00, 15:00, 16:00
    when = datetime.fromtimestamp(NOW + 3600 + 20 * 60).astimezone()  # 15:20 → Punkt 15:00
    point = eng.forecast_point_at(when)
    assert point[1] == 4.0 and point[0] == datetime.fromtimestamp(NOW + 3600).astimezone()
    assert eng.outdoor_forecast_at(when) == 4.0
    # die Vorhersage endet um 16:00: um 17:30 gilt der Punkt noch (90 min), um 17:31 nicht mehr
    edge = datetime.fromtimestamp(NOW + 2 * 3600 + FORECAST_MAX_GAP_MIN * 60).astimezone()
    assert eng.outdoor_forecast_at(edge) == 3.0
    assert eng.outdoor_forecast_at(edge + timedelta(minutes=1)) is None
    # Ankunft in drei Tagen bekommt nicht den letzten Punkt (vor 0.36.1 schon)
    assert eng.outdoor_forecast_at(datetime.fromtimestamp(NOW + 3 * 86400).astimezone()) is None
    assert eng.forecast_point_at(datetime.fromtimestamp(NOW - 3 * 3600).astimezone()) is None
    eng.forecast = []
    assert eng.forecast_point_at(when) is None and eng.outdoor_forecast_at(when) is None


def test_bad_points_skipped_and_naive_times_are_local(tmp_path, berlin):
    eng = make(tmp_path)
    eng.forecast = [
        {"datetime": "2026-10-19T14:00:00", "temperature": 6.0},          # ohne Zone: Ortszeit (Berlin, MESZ)
        {"datetime": "2026-10-19T13:00:00Z", "temperature": 7.0},         # „Z“ = UTC = 15:00 Berlin
        {"datetime": "kaputt", "temperature": 1.0},
        {"datetime": "2026-10-19T16:00:00+02:00", "temperature": None},  # ohne Temperatur
        {"temperature": 2.0},
        "kein Punkt",
    ]
    assert [(p[0].isoformat(), p[1]) for p in eng.forecast_points()] == [
        ("2026-10-19T14:00:00+02:00", 6.0), ("2026-10-19T13:00:00+00:00", 7.0)]
    utc = datetime(2026, 10, 19, 12, 5, tzinfo=timezone.utc)   # 14:05 Berlin
    assert eng.outdoor_forecast_at(utc) == 6.0
    assert eng.outdoor_forecast_at(datetime(2026, 10, 19, 15, 10)) == 7.0  # Frage ohne Zone: auch Ortszeit
    assert parse_time("") is None and parse_time(None) is None and parse_time(17) is None


# ------------------------------------------------------------------ Kachel „Vorhersage“
def test_forecast_summary_next12_trend_age(tmp_path):
    eng = make(tmp_path)
    # 13:00 (vorbei) bis morgen 15:00; Minimum −2,2 °C um 23:00, in 12 h (02:00) 0,8 °C, jetzt 7,4 °C
    temps = [9.0] + [abs(k - 9) - 2.2 + (0.6 if k == 0 else 0.0) for k in range(26)]
    eng.forecast = hourly(NOW - 3600, temps)
    eng.forecast_ts = NOW - 20 * 60
    f = eng.forecast_summary(NOW)
    assert f["age_min"] == 20 and f["points"] == 27 and f["hours"] == 25.0
    n = f["next12"]
    assert n["min"] == pytest.approx(-2.2) and n["min_at"] == iso(NOW + 9 * 3600)
    assert n["max"] == pytest.approx(7.4)  # der Punkt 13:00 (9 °C) liegt vor dem Fenster
    assert f["trend_k"] == -6.5  # 0,8 − 7,4 = −6,6 → auf 0,5 gerundet
    # nur Punkte in der Vergangenheit: Reichweite 0, kein Trend, keine nächsten 12 h
    eng.forecast = hourly(NOW - 6 * 3600, [3.0, 3.0])
    old = eng.forecast_summary(NOW)
    assert old["hours"] == 0.0 and old["next12"] is None and old["trend_k"] is None and old["points"] == 2
    # gleich bleibend: Trend 0 (nicht −0)
    eng.forecast = hourly(NOW, [4.1] * 13)
    assert str(eng.forecast_summary(NOW)["trend_k"]) == "0.0"


def test_forecast_none_without_entity_or_points(tmp_path):
    eng = make(tmp_path, weather=None)
    eng.forecast = hourly(NOW, [5.0])
    assert eng.forecast_summary(NOW) is None
    eng = make(tmp_path, db="b.db")
    assert eng.forecast_summary(NOW) is None  # Wetter-Entität, aber (noch) keine Punkte
    eng.forecast = [{"datetime": "kaputt", "temperature": 3.0}]
    assert eng.forecast_summary(NOW) is None
    s = eng.summary()
    assert s["forecast"] is None and s["discovered"]["weather"] == "weather.home"


# ------------------------------------------------------------------ Vorheizplan
def _plan(eng, monkeypatch):
    """Vorheizplan zur Ankunft in 2 h – was preheat_plan als Außentemperatur bekommt, wird mitgeschrieben."""
    got = {}

    def fake_plan(rooms, house, eta, now, t_out, max_flow, dhw=None):
        got["t_out"] = t_out
        return {"feasible": False, "options": []}

    monkeypatch.setattr(engmod.guards, "check_house", lambda h: {"ok": True, "reasons": []})
    monkeypatch.setattr(engmod, "next_arrival",
                        lambda persons, now: {"eta": now + timedelta(hours=2), "source": "gewohnheit"})
    monkeypatch.setattr(engmod.analysis, "preheat_plan", fake_plan)
    return eng.summary()["plan"], got


def test_plan_tells_which_outdoor_temperature(tmp_path, monkeypatch):
    eng = make(tmp_path)
    now = time.time()
    eng.forecast = [{"datetime": iso(now + 2 * 3600 + 20 * 60), "temperature": -3.5},   # Ankunft + 20 min
                    {"datetime": iso(now + 5 * 3600), "temperature": -6.0}]
    eng.forecast_ts = now
    plan, got = _plan(eng, monkeypatch)
    assert plan["t_out_source"] == "vorhersage" and plan["t_out_used"] == -3.5 and got["t_out"] == -3.5
    assert datetime.fromisoformat(plan["t_out_at"]) == datetime.fromtimestamp(now + 2 * 3600 + 20 * 60).astimezone()
    # Vorhersagepunkt zu weit weg (Ankunft + 2 h): aktuelle Außentemperatur
    eng.forecast = [{"datetime": iso(now + 4 * 3600), "temperature": -6.0}]
    plan, got = _plan(eng, monkeypatch)
    assert plan["t_out_source"] == "aktuell" and plan["t_out_used"] == 8.0 and plan["t_out_at"] is None
    assert got["t_out"] == 8.0


def test_no_points_plan_uses_current_and_forecast_none(tmp_path, monkeypatch):
    eng = make(tmp_path)
    plan, got = _plan(eng, monkeypatch)
    assert plan["t_out_source"] == "aktuell" and plan["t_out_used"] == 8.0 and got["t_out"] == 8.0
    assert eng.summary()["forecast"] is None


def test_summary_forecast_section_is_json_clean(tmp_path):
    import json

    from heatpilot.web import clean

    eng = make(tmp_path)
    now = time.time()
    eng.forecast = hourly(int(now), [5.0 - k * 0.5 for k in range(24)])
    eng.forecast_ts = now - 61
    f = eng.summary()["forecast"]
    assert f["age_min"] == 1 and f["points"] == 24 and f["next12"]["max"] == 5.0 and f["trend_k"] == -6.0
    assert json.dumps(clean(f), allow_nan=False)
