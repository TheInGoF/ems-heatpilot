"""Urlaubsmodus: Zeiträume, in denen Gewohnheiten nicht gelernt werden.

Quellen (eine reicht):
* Optionen ``vacations``: Liste von Zeiträumen, z. B.
  ``"2026-12-20 - 2027-01-03"`` oder ``"2026-12-20 08:00 - 2027-01-03 17:30"``.
* Option ``vacation_entity``: z. B. ``input_boolean.urlaub`` – an = Urlaub.

Rückkehr – das Live-Signal schlägt den Plan:
1. **GPS:** Erst wenn jemand *sicher und ununterbrochen* heimkommt (mind. 30 min
   lückenlos näher, ≥ 15 km und 10 % der Strecke, im Schnitt ≥ 20 km/h – siehe
   ``PersonLearner.steady_return``), gilt dessen berechnete Ankunft – auch 1–2 Tage
   vor oder nach dem geplanten Ende. Kehrtwende oder lange ohne Fortschritt hebt
   das wieder auf.
2. **Geplantes Ende mit Uhrzeit:** genau diese Zeit.
3. **Nur Datum:** dieser Tag zur Standard-Uhrzeit (``vacation_arrival_time``,
   Vorgabe 18:00) – an dem Tag verfeinert GPS.

Ende des Urlaubsmodus: sobald jemand 10 Minuten zu Hause ist. Ist niemand da,
bleibt er nach dem geplanten Ende noch bis zu ``RETURN_GRACE_DAYS`` aktiv.

Während des Urlaubs pausieren Gewohnheiten (Anwesenheitsmuster, Zapfprofil,
Raummodelle); Physik (Wärmeverlust, Speicher) lernt weiter, die GPS-Entfernung
wird weiter verfolgt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

log = logging.getLogger(__name__)

_PART = r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?"
_RANGE = re.compile(rf"^\s*{_PART}\s*(?:-|–|bis)\s*{_PART}\s*$")

RETURN_GRACE_DAYS = 3
RETURN_HOME_MIN = 10


@dataclass
class Trip:
    start: datetime
    end: datetime
    end_exact: bool  # Uhrzeit angegeben (sonst nur Datum + Standard-Uhrzeit)

    @property
    def key(self) -> str:
        return self.start.isoformat()


def parse_ranges(items: list[str], default_arrival: time = time(18, 0)) -> list[Trip]:
    out = []
    for item in items:
        m = _RANGE.match(item)
        if not m:
            log.warning("Urlaub '%s' nicht verstanden – Format: JJJJ-MM-TT [HH:MM] - JJJJ-MM-TT [HH:MM]", item)
            continue
        d1, t1, d2, t2 = m.groups()
        start = datetime.combine(datetime.fromisoformat(d1).date(), time.fromisoformat(t1 or "00:00"))
        end = datetime.combine(datetime.fromisoformat(d2).date(),
                               time.fromisoformat(t2) if t2 else default_arrival)
        if end > start:
            out.append(Trip(start.astimezone(), end.astimezone(), t2 is not None))
    return out


class Vacation:
    def __init__(self, ranges: list[str], entity: str | None, default_arrival: time = time(18, 0)) -> None:
        self.trips = parse_ranges(ranges, default_arrival)
        self.entity = entity
        self.returned: set[str] = set()  # Reisen, von denen schon jemand zurück ist
        self._home_since: datetime | None = None

    @property
    def ranges(self) -> list[tuple[datetime, datetime]]:
        return [(t.start, t.end) for t in self.trips]

    def trip(self, when: datetime) -> Trip | None:
        """Laufende Reise (inkl. Nachlauf, falls niemand pünktlich zurück ist)."""
        for t in self.trips:
            if t.start <= when < t.end + timedelta(days=RETURN_GRACE_DAYS) and t.key not in self.returned:
                return t
        return None

    def active(self, when: datetime, entity_state: str | None = None) -> bool:
        if entity_state is not None and str(entity_state).lower() in ("on", "true", "1"):
            return "entity" not in self.returned
        return self.trip(when) is not None

    def end(self, when: datetime) -> datetime | None:
        t = self.trip(when)
        return t.end if t and t.end > when else None

    def note_presence(self, when: datetime, anyone_home: bool, entity_state: str | None = None) -> bool:
        """Rückkehr erkennen: jemand ≥ 10 min zu Hause -> Urlaub vorbei. True bei Rückkehr."""
        if not self.active(when, entity_state):
            self._home_since = None
            if entity_state is not None and str(entity_state).lower() not in ("on", "true", "1"):
                self.returned.discard("entity")  # Schalter aus: beim nächsten Einschalten neu
            return False
        if not anyone_home:
            self._home_since = None
            return False
        self._home_since = self._home_since or when
        if when - self._home_since < timedelta(minutes=RETURN_HOME_MIN):
            return False
        t = self.trip(when)
        if t:
            self.returned.add(t.key)
        if entity_state is not None and str(entity_state).lower() in ("on", "true", "1"):
            self.returned.add("entity")
        self._home_since = None
        log.info("Rückkehr aus dem Urlaub erkannt (%s)", when.isoformat())
        return True

    def arrival(self, when: datetime, persons: list) -> dict | None:
        """Erwartete Rückkehr: schnelle GPS-Annäherung schlägt den Plan."""
        best = None
        for p in persons:
            ret = p.steady_return(when)
            if ret and ret["eta"] - when <= timedelta(hours=24):
                if best is None or ret["eta"] < best["eta"]:
                    best = {"person": p.person, "home": False, "dist_km": ret["dist_km"],
                            "speed_kmh": ret["speed_kmh"], "eta": ret["eta"], "confidence": 0.9,
                            "source": "gps", "since": ret["since"]}
        if best:
            return best
        t = self.trip(when)
        if t and t.end > when:
            return {"person": "Urlaub", "eta": t.end, "home": False, "dist_km": None, "speed_kmh": None,
                    "confidence": 0.9 if t.end_exact else 0.5,
                    "source": "urlaub" if t.end_exact else "urlaub_datum"}
        return None
