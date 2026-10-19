"""Anwesenheit der nächsten Stunden (seit 0.37, für die Vorschau): wer wann zu Hause sein wird.

Die Steuerung fragt die Anwesenheit nur für jetzt (``ControlLoop.presence``). Die Vorschau braucht sie für jeden
Zeitpunkt der nächsten 24 h und rechnet sie hier aus dem, was schon gelernt ist – ohne etwas zu lernen oder zu ändern:

* Wer zu Hause ist, bleibt bis zur nächsten gewohnten Abfahrt (Wechsel „zu Hause“ → „weg“ der Gewohnheit,
  ``PersonLearner.next_away_by_habit``) – wer gegen die Gewohnheit da ist (Homeoffice, Feiertag), also bis morgen.
* Wer weg ist, kommt zur erwarteten Ankunft (GPS bzw. Gewohnheit wie ``PersonLearner.eta``) – lange weg ohne GPS gar
  nicht; danach gilt wieder die Gewohnheit (``next_home_by_habit``).
* Wo die Gewohnheit noch lernt, ist die Ankunft offen (wie ``arrival_unknown``) – die Steuerung bleibt dann bei Komfort.
* Urlaub: laufend bis zur erwarteten Rückkehr (``Vacation.arrival``), geplante Reisen nach ihrem Zeitraum.
* Wer jetzt zu Hause ist, sagt der aktuelle Zustand in Home Assistant (wie im Messpunkt der Steuerung): Tracker
  ohne Wert („unavailable“) heißt Anwesenheit unbekannt, nicht der zuletzt beobachtete Zustand.

``Household.at(t)`` liefert die Anwesenheit in der Form von ``controlloop.Presence``.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime

from .controlloop import Presence

HORIZON_H = 25         # so weit sucht die Gewohnheit nach dem nächsten Wechsel
MAX_CHANGES = 24       # Wechsel je Person im Zeitraum höchstens (Schutz vor Endlosschleifen)


def _first(hit):
    return hit[0] if hit else None


def timeline(p, now: datetime, end: datetime, eta: datetime | None, gps: bool,
             home: bool | None = None) -> tuple[list[tuple[datetime, bool | None]], list[datetime]]:
    """Wechselpunkte einer Person ``(ab, zu Hause?)`` von ``now`` bis ``end`` und die Felder, an denen ihre Ankunft
    offen ist (Gewohnheit lernt dort noch). ``eta``/``gps``: erwartete Ankunft der laufenden Abwesenheit und ob
    GPS sie kennt (dann ist nichts offen – wie ``PersonLearner.unknown_from``). ``home``: Zustand jetzt; None = der
    zuletzt beobachtete (``p.home``)."""
    home = p.home if home is None else home
    if home is None:
        return [(now, None)], []
    home, t, current = bool(home), now, not home
    points: list[tuple[datetime, bool | None]] = [(now, home)]
    unknown: list[datetime] = []
    while t < end and len(points) <= MAX_CHANGES:
        at = t.replace(second=0, microsecond=0)  # Felder beginnen zur vollen Viertelstunde, nicht 08:45:23
        if home:
            nxt = p.next_away_by_habit(at, HORIZON_H)
        else:
            nxt = eta if current else _first(p.next_home_by_habit(at, HORIZON_H))
            if not p.long_away and not (current and gps):
                earliest = p.earliest_arrival(now) if current else None
                unknown += p.unlearned_slots(at, min(nxt or end, end), earliest)
            current = False
        if nxt is None or nxt >= end or nxt <= t:
            break
        home = not home
        points.append((nxt, home))
        t = nxt
    return points, sorted(unknown)


def _state(points: list[tuple[datetime, bool | None]], t: datetime) -> bool | None:
    state = points[0][1]
    for at, s in points:
        if at > t:
            break
        state = s
    return state


@dataclass
class Household:
    """Anwesenheit im Vorschau-Zeitraum: Wechselpunkte je Person, Zeiten offener Ankunft, Urlaub (ab, Rückkehr)."""

    points: list[list[tuple[datetime, bool | None]]] = field(default_factory=list)
    unknown: list[list[datetime]] = field(default_factory=list)
    trips: list[tuple[datetime, datetime | None]] = field(default_factory=list)

    def at(self, t: datetime) -> Presence:
        """Anwesenheit zum Zeitpunkt ``t`` in der Form, die ``controlloop.desired`` erwartet."""
        for start, back in self.trips:
            if start <= t and (back is None or t < back):
                return Presence(home_count=0, vacation=True, arrival=back)
        if not self.points:
            return Presence()  # keine Personen: Anwesenheit unbekannt (wie die Steuerung)
        states = [_state(p, t) for p in self.points]
        home = sum(1 for s in states if s)
        if home or any(s is None for s in states):
            return Presence(home_count=home or None)
        arrivals = [next((at for at, s in p if at > t and s), None) for p in self.points]
        unknown = [u[i] for u in self.unknown if (i := bisect.bisect_left(u, t)) < len(u)]
        return Presence(home_count=0, arrival=min((a for a in arrivals if a), default=None),
                        arrival_unknown=min(unknown, default=None))


def household(eng, now: datetime, end: datetime, states: list[tuple] | None = None) -> Household:
    """Anwesenheit für die Vorschau aus Gewohnheit, GPS und Urlaub (siehe Modulbeschreibung). ``states``: die
    Personen aus ``Engine.snapshot`` (Entität, zu Hause 1/0/None, Entfernung) – fehlt eine darin oder ist ihr Zustand
    None, ist ihre Anwesenheit unbekannt; ohne ``states`` gilt der zuletzt beobachtete Zustand."""
    hh = Household()
    persons = list(eng.persons.values())
    now_home = None if states is None else {e: h for e, h, _ in states}
    on_vac, back = eng.on_vacation(now.timestamp()), None
    if on_vac:
        ret = eng.vacation.arrival(now, persons)
        back = ret["eta"] if ret else None
        hh.trips.append((now, back))
    for trip in eng.vacation.trips:  # geplante Reisen, die im Zeitraum beginnen
        if now < trip.start < end and trip.key not in eng.vacation.returned:
            hh.trips.append((trip.start, trip.end))
    for entity, p in eng.persons.items():
        home = p.home if now_home is None else now_home.get(entity)
        if home is None:  # Tracker ohne Wert: Anwesenheit unbekannt (wie ``home_count`` None im Messpunkt)
            hh.points.append([(now, None)])
            hh.unknown.append([])
            continue
        if on_vac and not home:
            eta, gps = back, True  # im Urlaub zählt die Rückkehr, die Gewohnheit sagt nichts
        else:
            e = p.eta(now) if not home else {"eta": None, "source": None}
            eta, gps = e["eta"], e["source"] == "gps"
        pts, unknown = timeline(p, now, end, eta, gps, bool(home))
        hh.points.append(pts)
        hh.unknown.append(unknown)
    return hh
