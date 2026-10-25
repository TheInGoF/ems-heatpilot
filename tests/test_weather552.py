"""0.55.2: Wetter · 24 Stunden in der Übersicht – api/weather liefert je Stunde Temperatur, Wolken, Regen, Wind (m/s)
und UV aus der Vorhersage; fehlende Felder bleiben None, nur die nächsten 24 Stunden."""

from datetime import datetime, timedelta, timezone

from heatpilot import web_weather

T0 = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)


def _fc(n=30):
    return [{"datetime": (T0 + timedelta(hours=i)).isoformat(), "temperature": 7 + i * 0.1, "cloud_coverage": 50,
             "precipitation": 0.2, "precipitation_probability": 40, "wind_speed": 36, "wind_gust_speed": 72,
             "uv_index": 0.5, "condition": "rainy"} for i in range(n)]


def test_next_24_hours_with_wind_in_ms():
    hs = web_weather.hours(_fc(), {"wind_speed_unit": "km/h"}, (T0 + timedelta(minutes=20)).timestamp())
    assert len(hs) == 24 and hs[0]["t"].startswith("2026-10-25T05:00")  # die laufende Stunde zählt mit
    assert hs[0]["wind"] == 10.0 and hs[0]["gust"] == 20.0 and hs[0]["cloud"] == 50 and hs[0]["uv"] == 0.5


def test_missing_fields_stay_none_and_bad_points_drop():
    fc = [{"datetime": T0.isoformat(), "temperature": 5}, {"datetime": "kaputt"}, "kein dict"]
    hs = web_weather.hours(fc, {}, T0.timestamp())
    assert len(hs) == 1 and hs[0]["temp"] == 5 and hs[0]["rain"] is None and hs[0]["wind"] is None


def test_ui_card_rows_and_axis():
    from pathlib import Path
    page = (Path(__file__).parents[1] / "ems_heatpilot/heatpilot/static/index.html").read_text()
    assert '<div class="card" id="ov-wx" hidden>' in page and 'getJSON("api/weather")' in page
    for k in ('["temp", "Temperatur"', '["rain", "Regen"', '["cloud", "Wolken"', '["uv", "Sonne (UV)"', '["wind", "Wind"'):
        assert k in page
    assert "height: 38px" in page  # etwa 1 cm je Graf


def test_weather_now_like_a_weather_card():  # 0.56.1
    st = {"state": "rainy", "attributes": {"temperature": 7.4, "apparent_temperature": 3, "humidity": 89,
                                           "pressure": 987, "pressure_unit": "hPa", "wind_speed": 32,
                                           "wind_speed_unit": "km/h", "wind_gust_speed": 56.7, "wind_bearing": 182,
                                           "uv_index": 0, "cloud_coverage": 100}}
    n = web_weather.current(st)
    assert n["cond"] == "rainy" and n["temp"] == 7.4 and n["feels"] == 3 and n["humidity"] == 89
    assert n["wind"] == 8.89 and n["gust"] == 15.75 and n["bearing"] == 182 and n["pressure_unit"] == "hPa"
    assert web_weather.current(None) is None


def test_ui_weather_now_header():  # 0.56.1
    from pathlib import Path
    page = (Path(__file__).parents[1] / "ems_heatpilot/heatpilot/static/index.html").read_text()
    assert 'id="ov-wx-now"' in page and "renderWeatherNow(j.now, j.hours || [])" in page
    assert 'rainy: "Regen"' in page and 'sunny: "Sonnig"' in page and "function wxIcon(c)" in page
