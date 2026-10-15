"""Anwesenheit lernen und Ankunft vorhersagen – für beliebig viele Personen.

Zwei Quellen, beide aus den ``person.*``-Entitäten von Home Assistant:

1. GPS live: Aus Entfernung zum Zuhause und deren Änderung berechnen wir
   selbst, ob sich jemand nähert und wann er ankommt. Keine extra Sensoren.
   Die typische Heimfahrt-Geschwindigkeit wird pro Person gelernt.
2. Gewohnheit: Pro Person ein Wochenraster (7 Tage x 96 Viertelstunden) mit
   der gelernten Wahrscheinlichkeit, zu Hause zu sein.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SLOTS_PER_DAY = 96
SLOT_MIN = 15

# Sichere Heimfahrt (z. B. Urlaubsrückkehr): erst bestätigen, wenn ununterbrochen näher
STEADY_MIN = 30          # mindestens so lange ohne Unterbrechung näher
STEADY_DROP_KM = 15.0    # dabei mindestens so viel geschafft (und 10 % der Strecke)
STEADY_SPEED_KMH = 20.0  # im Schnitt mindestens so schnell
JITTER_KM = 0.5          # GPS-Schwankung, die nicht als "weiter weg" zählt
REVERSAL_KM = 3.0        # danach wieder so viel weiter weg = Kehrtwende, Abbruch
STALL_MIN = 45           # so lange ohne Fortschritt (Rast) = Abbruch
MAX_GAP_MIN = 20         # größere Lücken in den GPS-Daten unterbrechen die Serie


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def slot_of(ts: datetime) -> int:
    return ts.weekday() * SLOTS_PER_DAY + (ts.hour * 60 + ts.minute) // SLOT_MIN


@dataclass
class PersonLearner:
    person: str
    week: list[float] = field(default_factory=lambda: [0.5] * (7 * SLOTS_PER_DAY))
    alpha: float = 0.1
    approach_speed_kmh: float = 35.0
    arrivals_learned: int = 0
    track: deque = field(default_factory=lambda: deque(maxlen=60))
    home: bool | None = None
    dist_km: float | None = None
    _approach_start: tuple[datetime, float] | None = None
    _last_slot_learned: int | None = None
    _return: dict | None = None  # bestätigte Heimfahrt

    def observe(self, ts: datetime, home: bool, dist_km: float | None, learn: bool = True) -> None:
        """Position verfolgen; ``learn=False`` (Urlaub): Gewohnheiten nicht anpassen."""
        slot = slot_of(ts)
        if learn and slot != self._last_slot_learned:
            self.week[slot] += self.alpha * ((1.0 if home else 0.0) - self.week[slot])
            self._last_slot_learned = slot

        if dist_km is not None:
            self.track.append((ts, dist_km))
            self.dist_km = dist_km

        speed = self.approach_speed_now(ts)
        if not home and speed is not None and speed > 3 and self._approach_start is None:
            if dist_km is not None and dist_km > 1.0:
                self._approach_start = (ts, dist_km)
        if not home and speed is not None and speed < -3:
            self._approach_start = None

        if home and self.home is False and self._approach_start is not None:
            start_ts, start_dist = self._approach_start
            hours = (ts - start_ts).total_seconds() / 3600
            if learn and 0.05 < hours < 4:
                observed = start_dist / hours
                self.approach_speed_kmh += 0.2 * (observed - self.approach_speed_kmh)
                self.arrivals_learned += 1
            self._approach_start = None
        if home:
            self._approach_start = None
            self._return = None
            self.track.clear()
        self.home = home

    def steady_return(self, now: datetime) -> dict | None:
        """Sichere Heimfahrt erkennen: ununterbrochen näher, schnell genug, weit genug.

        Bestätigt nur, wenn die letzten Messpunkte lückenlos immer näher kommen
        (bis auf GPS-Schwankung) – mindestens STEADY_MIN Minuten, STEADY_DROP_KM und
        10 % der Strecke, im Schnitt ≥ STEADY_SPEED_KMH. Danach bleibt die Bestätigung
        bei Rast oder kleinem Umweg bestehen und fällt erst bei Kehrtwende
        (> REVERSAL_KM weiter weg) oder STALL_MIN ohne Fortschritt.
        Ankunft = Restentfernung / Durchschnittstempo der Heimfahrt.
        """
        pts = [p for p in self.track if p[0] <= now]
        if self.home or not pts:
            self._return = None
            return None
        t_last, d_last = pts[-1]

        i = len(pts) - 1
        while i > 0:
            (t0, d0), (t1, d1) = pts[i - 1], pts[i]
            if (t1 - t0).total_seconds() > MAX_GAP_MIN * 60 or d1 > d0 + JITTER_KM:
                break
            i -= 1
        seg = pts[i:]
        dur_h = (seg[-1][0] - seg[0][0]).total_seconds() / 3600
        drop = seg[0][1] - seg[-1][1]
        speed = drop / dur_h if dur_h > 0 else 0.0
        if (dur_h * 60 >= STEADY_MIN and drop >= max(STEADY_DROP_KM, 0.1 * seg[0][1])
                and speed >= STEADY_SPEED_KMH):
            if self._return is None:
                self._return = {"since": seg[0][0], "min_dist": d_last, "progress_at": t_last}
            self._return["speed"] = speed

        r = self._return
        if r is None:
            return None
        if d_last < r["min_dist"] - JITTER_KM:
            r["min_dist"], r["progress_at"] = d_last, t_last
        if d_last > r["min_dist"] + REVERSAL_KM or (now - r["progress_at"]).total_seconds() > STALL_MIN * 60:
            self._return = None  # Kehrtwende oder zu lange kein Fortschritt
            return None
        return {
            "since": r["since"],
            "dist_km": round(d_last, 1),
            "speed_kmh": round(r["speed"], 1),
            "eta": now + timedelta(hours=d_last / max(r["speed"], 5.0)),
        }

    def approach_speed_now(self, now: datetime) -> float | None:
        """Annäherungsgeschwindigkeit km/h (positiv = kommt näher)."""
        pts = [(t, d) for t, d in self.track if (now - t).total_seconds() <= 900]
        if len(pts) < 2:
            return None
        t0 = pts[0][0]
        xs = [(t - t0).total_seconds() / 3600 for t, _ in pts]
        ys = [d for _, d in pts]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        var = sum((x - mx) ** 2 for x in xs)
        if var < 1e-6:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
        return -slope

    def next_home_by_habit(self, now: datetime, horizon_h: int = 24) -> tuple[datetime, float] | None:
        start = slot_of(now)
        for i in range(1, horizon_h * 60 // SLOT_MIN + 1):
            p = self.week[(start + i) % len(self.week)]
            if p >= 0.5:
                minutes = i * SLOT_MIN - (now.minute % SLOT_MIN)
                return now + timedelta(minutes=minutes), p
        return None

    def eta(self, now: datetime) -> dict:
        info = {
            "person": self.person,
            "home": self.home,
            "dist_km": None if self.dist_km is None else round(self.dist_km, 1),
            "speed_kmh": None,
            "eta": None,
            "source": None,
            "confidence": None,
        }
        if self.home:
            info["eta"] = now
            info["source"] = "zuhause"
            return info
        speed = self.approach_speed_now(now)
        if speed is not None:
            info["speed_kmh"] = round(speed, 1)
        if speed is not None and speed > 3 and self.dist_km:
            v = 0.5 * speed + 0.5 * self.approach_speed_kmh
            info["eta"] = now + timedelta(hours=self.dist_km / max(v, 5))
            info["source"] = "gps"
            info["confidence"] = 0.8
            return info
        habit = self.next_home_by_habit(now)
        if habit:
            info["eta"], info["confidence"] = habit[0], round(habit[1], 2)
            info["source"] = "gewohnheit"
        return info

    def to_dict(self) -> dict:
        return {
            "person": self.person,
            "week": self.week,
            "approach_speed_kmh": self.approach_speed_kmh,
            "arrivals_learned": self.arrivals_learned,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PersonLearner":
        return cls(
            person=d["person"],
            week=d["week"],
            approach_speed_kmh=d.get("approach_speed_kmh", 35.0),
            arrivals_learned=d.get("arrivals_learned", 0),
        )


def next_arrival(learners: list[PersonLearner], now: datetime) -> dict | None:
    """Früheste erwartete Ankunft, wenn gerade niemand zu Hause ist."""
    if any(p.home for p in learners):
        return None
    etas = [p.eta(now) for p in learners]
    etas = [e for e in etas if e["eta"] is not None]
    if not etas:
        return None
    return min(etas, key=lambda e: e["eta"])
