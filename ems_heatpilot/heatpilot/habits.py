"""Gelernte Gewohnheiten auf einen Blick (seit 0.67): je Wochentag und Stunde schlafend, wach oder abwesend.

Zusammengesetzt aus dem, was HeatPilot schon lernt – nichts Neues wird gerechnet:

* **schlafend** – die Nacht laut gelerntem Wochenplan (``sleep``: Hausleistung und Sensor-Eimer), wo er für den Tag
  belastbar ist, sonst der eingestellte Wochenplan (``source`` je Tag: gelernt | eingestellt);
* **abwesend** – außerhalb der Nacht, wenn laut gelernter Gewohnheit der Personen (``presence.habit_hours``)
  wahrscheinlich niemand zu Hause ist (Wahrscheinlichkeit „jemand da“ unter ``AWAY_P``);
* **wach** – sonst.

Dazu je Tag die Sicherheitsangabe der Aufwachzeit (``sleep.confidence``). ``GET api/habits`` für Übersicht und
Einstellungen.
"""

from __future__ import annotations

from .config import WEEKDAYS

AWAY_P = 0.3


def _min(hhmm: str | None) -> int | None:
    return None if not hhmm else int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _nights(cfg, plan_days: list[dict]) -> list[dict]:
    """Je Wochentag {start, end, source}: Beginn am Abend dieses Tages, Ende am Morgen dieses Tages."""
    out = []
    for i, d in enumerate(plan_days or [{}] * 7):
        fixed = cfg.night_plan[i]
        start = d.get("start") if d.get("reliable_start") else None
        end = d.get("end") if d.get("reliable_end") else None
        out.append({"start": start or fixed["start"], "end": end or fixed["end"],
                    "source": "gelernt" if (start or end) else "eingestellt", "sure": d.get("sure_end")})
    return out


def _home_prob(persons) -> list[list[float]] | None:
    """Wahrscheinlichkeit „mindestens eine Person zu Hause“ je Wochentag und Stunde – None ohne Personen."""
    hours = [p.habit_hours() for p in persons]
    if not hours:
        return None
    out = []
    for d in range(7):
        row = []
        for h in range(24):
            away = 1.0
            for ph in hours:
                away *= 1.0 - ph[d][h]
            row.append(round(1.0 - away, 3))
        out.append(row)
    return out


def week(cfg, plan_days: list[dict], persons) -> dict:
    """{days: [{day, hours: [schlafend|wach|abwesend × 24], source, sure, bed, wake}], persons: bool}."""
    nights, home = _nights(cfg, plan_days), _home_prob(list(persons))
    days = []
    for i, day in enumerate(WEEKDAYS):
        bed = _min(nights[i]["start"])               # Abend dieses Tages
        wake = _min(nights[i]["end"])                # Morgen dieses Tages
        hours = []
        for h in range(24):
            m = h * 60 + 30
            if (wake is not None and m < wake) or (bed is not None and m >= bed):
                hours.append("schlafend")
            elif home is not None and home[i][h] < AWAY_P:
                hours.append("abwesend")
            else:
                hours.append("wach")
        days.append({"day": day, "hours": hours, "source": nights[i]["source"], "sure": nights[i]["sure"],
                     "bed": nights[i]["start"], "wake": nights[i]["end"]})
    return {"days": days, "persons": home is not None}


def routes(app, json_response) -> list[tuple]:
    async def get(_req):
        eng = app.eng
        if eng is None:
            return json_response({"starting": True})
        sl = eng.sleep_summary() or {}
        return json_response({**week(eng.cfg, sl.get("plan") or [], eng.persons.values()),
                              "sensors": len(sl.get("sensors") or []) + len(sl.get("activity") or []),
                              "nights": sl.get("nights", 0)})

    return [("GET", "/api/habits", get)]
