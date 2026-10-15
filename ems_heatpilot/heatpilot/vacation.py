"""Urlaubsmodus: Zeiträume, in denen Gewohnheiten nicht gelernt werden.

Quellen (eine reicht):
* Optionen ``vacations``: Liste von Zeiträumen, z. B.
  ``"2026-12-20 - 2027-01-03"`` oder ``"2026-12-20 08:00 - 2027-01-03 17:30"``.
  Ohne Uhrzeit beginnt der Urlaub um 00:00 und endet um 18:00 (Rückkehr am Abend).
* Option ``vacation_entity``: z. B. ``input_boolean.urlaub`` – an = Urlaub.

Während des Urlaubs:
* Anwesenheitsgewohnheiten, Warmwasser-Zapfprofil und Raummodelle lernen nicht –
  nach dem Urlaub geht es mit dem Gelernten weiter wie vorher.
* Das Hausmodell (Wärmeverlust) lernt weiter – Physik ändert sich im Urlaub nicht.
* Die Ersparnis-Rechnung lässt Urlaubsstunden aus.
* Ist das Ende bekannt, gilt es als Ankunft: rechtzeitig vorheizen und Warmwasser laden.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, time

log = logging.getLogger(__name__)

_PART = r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?"
_RANGE = re.compile(rf"^\s*{_PART}\s*(?:-|–|bis)\s*{_PART}\s*$")


def parse_ranges(items: list[str]) -> list[tuple[datetime, datetime]]:
    out = []
    for item in items:
        m = _RANGE.match(item)
        if not m:
            log.warning("Urlaub '%s' nicht verstanden – Format: JJJJ-MM-TT [HH:MM] - JJJJ-MM-TT [HH:MM]", item)
            continue
        d1, t1, d2, t2 = m.groups()
        start = datetime.combine(datetime.fromisoformat(d1).date(), time.fromisoformat(t1 or "00:00"))
        end = datetime.combine(datetime.fromisoformat(d2).date(), time.fromisoformat(t2 or "18:00"))
        if end > start:
            out.append((start.astimezone(), end.astimezone()))
    return out


class Vacation:
    def __init__(self, ranges: list[str], entity: str | None) -> None:
        self.ranges = parse_ranges(ranges)
        self.entity = entity

    def active(self, when: datetime, entity_state: str | None = None) -> bool:
        if entity_state is not None and str(entity_state).lower() in ("on", "true", "1"):
            return True
        return any(s <= when < e for s, e in self.ranges)

    def end(self, when: datetime) -> datetime | None:
        """Ende des laufenden Urlaubs aus den Zeiträumen (für die Entität unbekannt)."""
        for s, e in self.ranges:
            if s <= when < e:
                return e
        return None
