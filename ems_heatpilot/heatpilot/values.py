"""Werte lesen: Zahl und Ein/Aus aus Zuständen, Zeitpunkte in Ortszeit.

Kleine Helfer, die die Engine, ihre Teile (``engine_*.py``) und einige Auswertungen gemeinsam
brauchen. Sie wandeln nur Werte um und merken sich nichts. Bisherige Importe aus ``heatpilot.engine``
(``num``, ``flag``, ``local`` …) gelten weiter – engine reicht die Namen durch.
"""

from __future__ import annotations

from datetime import datetime

ON_STATES = {"on", "true", "1", "heat", "heating",
             "an", "ein", "ja", "wahr", "aktiv"}  # EMS-ESP mit deutscher Sprache: „an“/„aus“


def num(value) -> float | None:
    if isinstance(value, str):
        value = value.strip().replace(",", ".")  # z. B. EMS-ESP-Zähler "4321,57"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN raus


def flag(value) -> int | None:
    if value is None or value in ("unknown", "unavailable"):
        return None
    if isinstance(value, bool):
        return int(value)
    if num(value) is not None:
        return int(num(value) > 0)
    return int(str(value).lower() in ON_STATES)


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def aware(dt: datetime) -> datetime:
    """Zeitpunkt mit Zeitzone – ohne Zone gilt er als Ortszeit."""
    return dt if dt.tzinfo is not None else dt.astimezone()


def parse_time(value) -> datetime | None:
    """ISO-Zeit (auch mit „Z“) oder datetime als Zeitpunkt mit Zone; ohne Zone Ortszeit, unlesbar None."""
    if isinstance(value, datetime):
        return aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def midnight(day: datetime) -> int:
    """Beginn dieses Kalendertags (Ortszeit) – auch am Tag der Zeitumstellung richtig (23/25 h).

    Nicht ``replace(hour=0)`` auf einem Zeitpunkt mit fester Zeitzone: Nach der Umstellung
    läge Mitternacht sonst eine Stunde daneben.
    """
    return int(datetime.combine(day.date(), datetime.min.time()).timestamp())
