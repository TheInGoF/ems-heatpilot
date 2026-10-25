"""Wetter der nächsten 24 Stunden für Übersicht → Jetzt (seit 0.55.2): ``GET api/weather``.

Aus der stündlichen Vorhersage, die HeatPilot ohnehin abruft (``eng.forecast``, ``weather.get_forecasts``): je Stunde
Temperatur, gefühlt, Wolken, Regen (mm und Wahrscheinlichkeit), Wind und Böen in m/s (Einheit der Wetter-Entität,
umgerechnet wie beim Lernen), UV-Index und Zustand. Fehlende Felder bleiben None – jede Integration liefert andere.
Nur lesen, nur Anzeige.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Callable

from .engine_sample import wind_ms
from .values import local, num, parse_time

HOURS = 24


def hours(forecast, attrs: dict, now_ts: float) -> list[dict]:
    """Die nächsten ``HOURS`` Stunden ab der laufenden, zeitlich sortiert."""
    now, unit = local(now_ts), attrs.get("wind_speed_unit")
    start, end = now - timedelta(minutes=59), now + timedelta(hours=HOURS)
    out = []
    for f in forecast or []:
        when = parse_time(f.get("datetime")) if isinstance(f, dict) else None
        if when is None or not start <= when < end:
            continue
        out.append({"t": when.isoformat(), "temp": num(f.get("temperature")), "feels": num(f.get("apparent_temperature")),
                    "cloud": num(f.get("cloud_coverage")), "rain": num(f.get("precipitation")),
                    "rain_prob": num(f.get("precipitation_probability")), "wind": wind_ms(f.get("wind_speed"), unit),
                    "gust": wind_ms(f.get("wind_gust_speed"), unit), "bearing": num(f.get("wind_bearing")),
                    "uv": num(f.get("uv_index")), "cond": f.get("condition")})
    return sorted(out, key=lambda h: h["t"])[:HOURS]


def routes(app, json_response: Callable) -> list[tuple[str, str, Callable]]:
    """(Methode, Pfad, Handler) für ``web.make_app``."""

    async def weather(_req):
        eng = app.eng
        if eng is None:
            return json_response({"starting": True})
        ent = eng.found.weather
        attrs = (eng.states.get(ent) or {}).get("attributes", {}) if ent else {}
        return json_response({"entity": ent, "rain_unit": attrs.get("precipitation_unit") or "mm",
                              "hours": hours(eng.forecast, attrs, time.time()) if ent else []})

    return [("GET", "/api/weather", weather)]
