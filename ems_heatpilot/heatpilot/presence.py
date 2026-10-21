"""Anwesenheit lernen und Ankunft vorhersagen – für beliebig viele Personen.

Zwei Quellen, beide aus den ``person.*``-Entitäten von Home Assistant:

1. GPS live: Aus Entfernung zum Zuhause und deren Änderung berechnen wir
   selbst, ob sich jemand nähert und wann er ankommt. Keine extra Sensoren.
   Die typische Heimfahrt-Geschwindigkeit wird pro Person gelernt.
2. Gewohnheit: Pro Person ein Wochenraster (7 Tage x 96 Viertelstunden) mit
   der gelernten Wahrscheinlichkeit, zu Hause zu sein. Lange Abwesenheit (Reise
   ohne eingetragenen Urlaub) wird erkannt und nicht als Gewohnheit gelernt –
   außer sie wiederholt sich regelmäßig (Wochenpendler, Wochenendfahrt).
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
MOVE_KMH = 3.0           # darunter gilt man als „steht“ (GPS-Schwankung)
SPEED_WINDOW_S = 900     # Annäherungstempo: Regression über so viele Sekunden
# GPS-Ankunft nur bei echter Annäherung: seit Beginn so viel näher (deutlich über der GPS-Schwankung)
# oder mindestens so schnell im Verhältnis zum gelernten Heimfahrt-Tempo
APPROACH_MIN_KM = 4 * JITTER_KM
APPROACH_MIN_FRAC = 0.5

# Kontrolle der Vorhersage: was wurde so lange vor der tatsächlichen Ankunft erwartet?
CHECK_BEFORE_MIN = (60, 30, 15)
MIN_AWAY_MIN = 30        # kürzere Abwesenheit (GPS-Flattern, Mülltonne) zählt nicht als Heimkehr
KEEP_ARRIVALS = 30
PRED_EVERY_S = 300       # Vorhersage höchstens alle 5 min festhalten
SHORT_TRIP_MIN = 60      # kürzer weg UND …
SHORT_TRIP_KM = 1.0      # … nicht weiter weg (Spaziergang): zählt nicht in der Vorhersage-Güte

# Gewohnheit: Startwert 0,5 heißt „unbekannt“, nicht „zu Hause“
HABIT_MIN_P = 0.5        # Feld zählt als „kommt heim“ nur strikt darüber …
HABIT_MIN_VISITS = 2     # … und wenn es so oft gelernt wurde …
# … und erst, wenn es sich so weit vom Tiefpunkt seit dem Verlassen abhebt (seit 0.48.6): Ist jemand weg zu einer Zeit,
# in der er laut Gewohnheit genauso oft zu Hause ist wie danach (z. B. jeden zweiten Mittwoch den ganzen Tag, 0,51),
# sagt die Gewohnheit nichts über eine Heimkehr – sonst rollte die Ankunft mit der Uhr mit (immer „gleich“ nach der
# Fahrzeit) und das Haus heizte vor, obwohl niemand da ist
HABIT_RISE = 0.1
HABIT_LATE_MIN = 60      # so lange nach der erwarteten Heimkehr nicht da (und nicht auf dem Heimweg): Block verpasst
VISITS_MAX = 99
EARLIEST_MIN_KM = 1.0    # weiter weg: frühestens nach der Fahrzeit (Entfernung / gelerntes Tempo)

# Lange Abwesenheit (Reise ohne eingetragenen Urlaub): Raster beim Verlassen sichern,
# danach auf die Sicherung zurück und bis zur Heimkehr nicht mehr lernen
LONG_AWAY_H = 72         # länger weg (Wochenenden in der Nähe bleiben gelernt)
LONG_AWAY_FAR_H = 24     # oder so lange weg …
LONG_AWAY_FAR_KM = 300.0  # … und dabei weiter weg als das
# Regelmäßige lange Abwesenheit (Wochenpendler, Wochenendfahrt) ist Gewohnheit, keine Reise:
# eine frühere begann so viele Tage vorher zur selben Wanduhrzeit (± Toleranz) …
REGULAR_EVERY_D = (7, 14)
REGULAR_TOL_MIN = 90
REGULAR_EXTRA_H = 12     # … und die jetzige dauert höchstens so viel länger, sonst doch Reise
KEEP_AWAY_TRIPS = 8      # gemerkte Abwesenheiten über LONG_AWAY_FAR_H je Person (Abfahrt, Dauer)

# Heimfahrt-Tempo lernen
STILL_RESET_MIN = 10     # so lange stehen: Annäherung beginnt neu (Ampel nicht)
LEARN_MIN_KMH = 3.0      # beobachtetes Tempo außerhalb: unplausibel, verworfen (nicht begrenzt)
LEARN_MAX_KMH = 200.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def slot_of(ts: datetime) -> int:
    return ts.weekday() * SLOTS_PER_DAY + (ts.hour * 60 + ts.minute) // SLOT_MIN


def _rise(p: float, low: float | None) -> bool:
    """Feld „kommt heim“: über 0,5 und (bei bekanntem Tiefpunkt) mindestens ``HABIT_RISE`` darüber – auf 6 Stellen
    gerundet, damit 0,561 − 0,461 als 0,1 zählt."""
    return p > HABIT_MIN_P and (low is None or round(p - low, 6) >= HABIT_RISE)


def short_trip(a: dict) -> bool:
    """Kurzer Abstecher (Spaziergang, Mülltonne): Zeile bleibt, zählt aber nicht in der Güte."""
    if not a.get("left") or a.get("max_km") is None:
        return False  # ohne GPS oder Startzeit nicht entscheidbar: zählt mit
    mins = (datetime.fromisoformat(a["ts"]) - datetime.fromisoformat(a["left"])).total_seconds() / 60
    return mins < SHORT_TRIP_MIN and a["max_km"] < SHORT_TRIP_KM


def _arrivals(rows) -> list[dict]:
    """Heimkehr-Protokoll: je Zeitpunkt eine Zeile (die zuletzt geschriebene), zeitlich geordnet, die letzten
    ``KEEP_ARRIVALS`` – eine Neuberechnung spielt behaltene Personenmodelle noch einmal ab (seit 0.48.6 nicht doppelt)."""
    by_ts = {datetime.fromisoformat(a["ts"]): a for a in rows}
    return [by_ts[t] for t in sorted(by_ts)][-KEEP_ARRIVALS:]


def _trips(trips) -> list[tuple[datetime, float]]:
    """Lange Abwesenheiten (Abfahrt, Stunden): je Abfahrt eine, zeitlich geordnet, die letzten ``KEEP_AWAY_TRIPS``."""
    by_start = {start: (start, hours) for start, hours in trips}
    return [by_start[t] for t in sorted(by_start)][-KEEP_AWAY_TRIPS:]


@dataclass
class PersonLearner:
    person: str
    week: list[float] = field(default_factory=lambda: [0.5] * (7 * SLOTS_PER_DAY))
    visits: list[int] = field(default_factory=lambda: [0] * (7 * SLOTS_PER_DAY))  # Lernschritte je Feld
    alpha: float = 0.1
    approach_speed_kmh: float = 35.0
    arrivals_learned: int = 0
    track: deque = field(default_factory=lambda: deque(maxlen=60))
    home: bool | None = None
    dist_km: float | None = None
    _approach_start: tuple[datetime, float] | None = None
    _last_slot_learned: int | None = None
    _return: dict | None = None  # bestätigte Heimfahrt
    # Prüfung der eigenen Vorhersage: (Zeitpunkt, erwartete Ankunft, Quelle) solange unterwegs
    _preds: deque = field(default_factory=lambda: deque(maxlen=400))
    _left_at: datetime | None = None
    _max_dist: float | None = None
    arrivals: list = field(default_factory=list)
    # lange Abwesenheit: seit wann weg, größte Entfernung, Sicherung des Rasters beim Verlassen
    long_away: bool = False
    _away_since: datetime | None = None
    _away_km: float | None = None
    _backup: tuple[list[float], list[int]] | None = None
    away_regular: bool = False  # laufende lange Abwesenheit wiederholt sich: lernt normal weiter
    away_trips: list = field(default_factory=list)  # [(Abfahrt, Stunden)] der letzten langen Abwesenheiten
    _still_since: datetime | None = None

    def observe(self, ts: datetime, home: bool, dist_km: float | None, learn: bool = True) -> None:
        """Position verfolgen; ``learn=False`` (Urlaub): Gewohnheiten nicht anpassen.

        Lange weg (``long_away``): Raster zurück auf den Stand beim Verlassen, bis zur Heimkehr
        kein Lernen – das Heimfahrt-Tempo lernt bei der Heimkehr trotzdem. Regelmäßige lange
        Abwesenheit (``away_regular``) lernt normal weiter.
        """
        self._track_away(ts, home, dist_km)
        slot = slot_of(ts)
        if learn and not self.long_away and slot != self._last_slot_learned:
            self.week[slot] += self.alpha * ((1.0 if home else 0.0) - self.week[slot])
            self.visits[slot] = min(self.visits[slot] + 1, VISITS_MAX)
            self._last_slot_learned = slot

        if dist_km is not None:
            self.track.append((ts, dist_km))
            self.dist_km = dist_km

        speed = self.approach_speed_now(ts)
        if not home and speed is not None:
            if abs(speed) <= MOVE_KMH:  # steht / GPS-Schwankung: nach einer Weile keine laufende Annäherung
                self._still_since = self._still_since or ts
                if (ts - self._still_since).total_seconds() >= STILL_RESET_MIN * 60:
                    self._approach_start = None
            else:
                self._still_since = None
            if speed < -MOVE_KMH:
                self._approach_start = None
            elif speed > MOVE_KMH and self._approach_start is None and dist_km is not None and dist_km > 1.0:
                self._approach_start = (ts, dist_km)

        if home and self.home is False:
            self._log_arrival(ts)
        if home is False and self.home is not False:
            self._left_at = ts if self.home is True else None  # Start unbekannt nach Neustart
            self._max_dist = None
            self._preds.clear()

        if home and self.home is False and self._approach_start is not None:
            start_ts, start_dist = self._approach_start
            hours = (ts - start_ts).total_seconds() / 3600
            observed = start_dist / hours if hours > 0 else 0.0
            if learn and 0.05 < hours < 4 and LEARN_MIN_KMH <= observed <= LEARN_MAX_KMH:
                self.approach_speed_kmh += 0.2 * (observed - self.approach_speed_kmh)
                self.arrivals_learned += 1
            self._approach_start = None
        if home:
            self._approach_start = None
            self._still_since = None
            self._return = None
            self.track.clear()
        self.home = home
        if not home:
            if dist_km is not None:
                self._max_dist = dist_km if self._max_dist is None else max(self._max_dist, dist_km)
            last = self._preds[-1][0] if self._preds else None
            e = self.eta(ts) if last is None or (ts - last).total_seconds() >= PRED_EVERY_S else {"eta": None}
            if e["eta"] is not None:
                self._preds.append((ts, e["eta"], e["source"]))

    def _track_away(self, ts: datetime, home: bool, dist_km: float | None) -> None:
        """Lange Abwesenheit erkennen (je Person, live und bei der Neuberechnung gleich)."""
        if home:
            if self._away_since is not None:
                hours = (ts - self._away_since).total_seconds() / 3600
                if hours >= LONG_AWAY_FAR_H:  # für die Erkennung regelmäßiger Abwesenheit merken
                    self.away_trips = _trips([*self.away_trips, (self._away_since, round(hours, 2))])
            self.long_away, self._away_since, self._away_km, self._backup = False, None, None, None
            self.away_regular = False
            return
        if self._away_since is None:  # gerade los (oder erster Wert nach Neuberechnung): Raster sichern
            self._away_since = ts
            self._backup = (list(self.week), list(self.visits))
        if dist_km is not None:
            self._away_km = dist_km if self._away_km is None else max(self._away_km, dist_km)
        if self.long_away:
            return
        hours = (ts - self._away_since).total_seconds() / 3600
        if hours > LONG_AWAY_H or (hours > LONG_AWAY_FAR_H and (self._away_km or 0.0) > LONG_AWAY_FAR_KM):
            limit = self._regular_limit_h(self._away_since)
            self.away_regular = limit is not None and hours <= limit
            if self.away_regular:
                return  # wie vor 1–2 Wochen: Gewohnheit, Sicherung bleibt für den Fall „doch länger“
            self.long_away = True
            if self._backup is not None:  # seitdem nur „weg“ gelernt: Zurücksetzen ist exakt
                self.week, self.visits = list(self._backup[0]), list(self._backup[1])
            self._backup = None

    def _regular_limit_h(self, since: datetime) -> float | None:
        """Begann vor 7 oder 14 Tagen (Wanduhr ± Toleranz) eine lange Abwesenheit?

        Dann: bis zu wie vielen Stunden die jetzige als regelmäßig gilt, sonst None.
        """
        wall = since.replace(tzinfo=None)
        hours = [h for start, h in self.away_trips
                 if any(abs((wall - start.replace(tzinfo=None)).total_seconds() / 60 - d * 1440) <= REGULAR_TOL_MIN
                        for d in REGULAR_EVERY_D)]
        return max(hours) + REGULAR_EXTRA_H if hours else None

    def _log_arrival(self, ts: datetime) -> None:
        """Heimkehr festhalten – mit dem, was HeatPilot 60/30/15 min vorher erwartet hatte."""
        if self._left_at is not None and (ts - self._left_at).total_seconds() < MIN_AWAY_MIN * 60:
            return
        checks = []
        for before in CHECK_BEFORE_MIN:
            at = ts - timedelta(minutes=before)
            cands = [p for p in self._preds if at - timedelta(minutes=20) <= p[0] <= at]
            if cands:
                t, eta, source = cands[-1]
                checks.append({"before_min": before, "eta": eta.isoformat(), "source": source,
                               "error_min": round((eta - ts).total_seconds() / 60)})
        self.arrivals = _arrivals([*self.arrivals, {
            "ts": ts.isoformat(),
            "left": self._left_at.isoformat() if self._left_at else None,
            "max_km": None if self._max_dist is None else round(self._max_dist, 1),
            "checks": checks,
        }])
        self._preds.clear()

    def status(self, now: datetime) -> dict:
        """Wie HeatPilot die Lage gerade deutet – zum Nachprüfen in der Oberfläche."""
        st = self._status(now)
        if self.long_away and not self.home:
            st["long_away"] = True
            st["since"] = st.get("since") or (self._away_since.isoformat() if self._away_since else None)
            st["text"] += " · lange weg – Gewohnheit lernt bis zur Heimkehr nicht"
        elif self.away_regular and not self.home:
            st["text"] += " · regelmäßig lange weg – Gewohnheit lernt weiter"
        return st

    def _status(self, now: datetime) -> dict:
        if self.home:
            return {"state": "zuhause", "text": "zu Hause"}
        if self.home is None:
            return {"state": "unbekannt", "text": "noch kein Wert"}
        ret = self.steady_return(now)
        if ret:
            return {"state": "heimfahrt", "since": ret["since"].isoformat(), "eta": ret["eta"].isoformat(),
                    "speed_kmh": ret["speed_kmh"],
                    "text": f"Heimfahrt erkannt seit {ret['since']:%H:%M} – im Schnitt {ret['speed_kmh']:.0f} km/h näher"}
        speed = self.approach_speed_now(now)
        if self.dist_km is None:
            return {"state": "unterwegs", "text": "unterwegs – keine Entfernung (kein GPS)"}
        if speed is None:
            return {"state": "unterwegs", "text": "unterwegs – zu wenige Messpunkte für eine Richtung"}
        if speed > MOVE_KMH:
            return {"state": "naehert", "speed_kmh": round(speed, 1),
                    "text": f"kommt näher ({speed:.0f} km/h) – noch keine sichere Heimfahrt"}
        if speed < -MOVE_KMH:
            return {"state": "entfernt", "speed_kmh": round(speed, 1), "text": f"entfernt sich ({-speed:.0f} km/h)"}
        return {"state": "steht", "speed_kmh": round(speed, 1), "text": "steht / bleibt in der Gegend"}

    def habit_hours(self) -> list[list[float]]:
        """Gelernte Wahrscheinlichkeit „zu Hause“ je Wochentag und Stunde (7 × 24, 0…1)."""
        per_h = SLOTS_PER_DAY // 24
        return [[round(sum(self.week[d * SLOTS_PER_DAY + h * per_h + i] for i in range(per_h)) / per_h, 3)
                 for h in range(24)] for d in range(7)]

    def arrival_quality(self) -> dict:
        """Mittlerer Fehler der Vorhersage je Vorlauf und Quelle (Minuten, positiv = zu spät erwartet).

        ``{60: {"gps": {...}, "gewohnheit": {...}}, …}`` – kurze Abstecher zählen nicht.
        """
        out: dict = {}
        for before in CHECK_BEFORE_MIN:
            by_src: dict[str, list[int]] = {}
            for a in self.arrivals:
                if short_trip(a):
                    continue
                for c in a["checks"]:
                    if c["before_min"] == before:
                        by_src.setdefault(c.get("source") or "?", []).append(c["error_min"])
            if by_src:
                out[before] = {src: {"n": len(errs), "mean_abs_min": round(sum(abs(e) for e in errs) / len(errs)),
                                     "within_15": round(sum(1 for e in errs if abs(e) <= 15) / len(errs), 2)}
                               for src, errs in by_src.items()}
        return out

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
        pts = [(t, d) for t, d in self.track if (now - t).total_seconds() <= SPEED_WINDOW_S]
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

    def _approaching(self, speed: float) -> bool:
        """Echte Annäherung statt GPS-Schwankung: schnell genug oder seit Beginn deutlich näher.

        Ein einzelner GPS-Sprung zählt nicht: die Annäherung beginnt erst am gesprungenen Punkt.
        """
        if speed >= APPROACH_MIN_FRAC * self.approach_speed_kmh:
            return True
        start = self._approach_start
        return start is not None and self.dist_km is not None and start[1] - self.dist_km >= APPROACH_MIN_KM

    def _habit_slots(self, now: datetime, horizon_h: int, earliest: datetime | None):
        """Kommende Felder (Zeitpunkt nach Wanduhr, Index), frühestens ``earliest``."""
        start = slot_of(now)
        for i in range(1, horizon_h * 60 // SLOT_MIN + 1):
            minutes = i * SLOT_MIN - (now.minute % SLOT_MIN)
            # Gewohnheit gilt nach Wanduhr: über die Zeitumstellung hinweg nicht in absoluter Zeit rechnen
            wall = now.replace(tzinfo=None) + timedelta(minutes=minutes)
            at = wall.astimezone() if now.tzinfo else wall
            if earliest is None or at >= earliest:
                yield at, (start + i) % len(self.week)

    def habit_reliable(self, slot: int) -> bool:
        return self.visits[slot] >= HABIT_MIN_VISITS

    def _habit_low(self, now: datetime, since: datetime | None) -> float | None:
        """Tiefpunkt der Gewohnheit „zu Hause“ von ``since`` (Beginn der Abwesenheit; None: nur das laufende Feld) bis
        jetzt – nach dem Raster vom Verlassen (``_backup``), denn die laufende Abwesenheit hat diese Felder seitdem schon
        gesenkt. Eine erwartete Heimkehr (Anstieg um ``HABIT_RISE``), die ``HABIT_LATE_MIN`` vorbei ist, hebt den
        Tiefpunkt auf ihr Feld: Die Person kam nicht, der Rest dieses Blocks ist keine Ankunft mehr. None: kein
        verlässlich gelerntes Feld."""
        week, visits = self._backup if self._backup is not None else (self.week, self.visits)
        start = since if since is not None and since < now else now
        # Wanduhr wie ``_habit_slots``: Minuten vom Beginn des ersten Felds bis jetzt; länger weg: die letzte Woche
        span = (now.replace(tzinfo=None) - start.replace(tzinfo=None)).total_seconds() / 60 + start.minute % SLOT_MIN
        first, steps, low = slot_of(start), int(span // SLOT_MIN), None
        for k in range(max(steps - len(week) + 1, 0), steps + 1):
            slot = (first + k) % len(week)
            if visits[slot] < HABIT_MIN_VISITS:
                continue
            p = week[slot]
            if low is None or p < low:
                low = p
            elif _rise(p, low) and span - k * SLOT_MIN >= HABIT_LATE_MIN:
                low = p
        return low

    def next_home_by_habit(self, now: datetime, horizon_h: int = 24, earliest: datetime | None = None,
                           since: datetime | None = None) -> tuple[datetime, float] | None:
        """Erste erwartete Heimkehr: das erste verlässlich gelernte Feld „zu Hause“, das sich um ``HABIT_RISE`` vom
        Tiefpunkt seit ``since`` (Beginn der Abwesenheit, sonst jetzt) abhebt (``_habit_low``) – unbesuchte Felder
        (0,5) liefern keine Ankunft, ebenso wenig ein Zuhause, das nicht häufiger ist als die Zeit, in der die Person
        schon weg ist. Felder vor ``earliest`` zählen für den Tiefpunkt, liefern aber keine Ankunft."""
        low = self._habit_low(now, since)
        for at, slot in self._habit_slots(now, horizon_h, None):
            if not self.habit_reliable(slot):
                continue
            p = self.week[slot]
            if _rise(p, low):
                if earliest is None or at >= earliest:
                    return at, p
            elif low is None or p < low:
                low = p
        return None

    def next_away_by_habit(self, now: datetime, horizon_h: int = 24) -> datetime | None:
        """Erwartete Abfahrt (seit 0.37, Vorschau): der nächste Wechsel „zu Hause“ → „weg“ der Gewohnheit – das
        erste verlässlich gelernte Feld „weg“, vor dem ab jetzt (laufendes Feld eingeschlossen) mindestens ein
        verlässliches Feld „zu Hause“ liegt. Wer gegen die Gewohnheit zu Hause ist (Homeoffice, Feiertag), bleibt
        so bis zur nächsten gewohnten Abfahrt. Reine Rechnung auf dem Raster, ohne GPS-Zustand."""
        cur = slot_of(now)
        seen_home = self.week[cur] > HABIT_MIN_P and self.habit_reliable(cur)
        for at, slot in self._habit_slots(now, horizon_h, None):
            if not self.habit_reliable(slot):
                continue
            if self.week[slot] > HABIT_MIN_P:
                seen_home = True
            elif self.week[slot] < HABIT_MIN_P and seen_home:
                return at
        return None

    def unlearned_slots(self, now: datetime, until: datetime, earliest: datetime | None = None) -> list[datetime]:
        """Beginne der noch nicht verlässlich gelernten Felder bis ``until`` (seit 0.37, Vorschau) – dort wäre die
        Ankunft offen (wie ``unknown_from``, aber für eine geplante Abwesenheit und ohne GPS-Zustand)."""
        hours = max(int((until - now).total_seconds() // 3600) + 1, 1)
        return [at for at, slot in self._habit_slots(now, hours, earliest)
                if at < until and not self.habit_reliable(slot)]

    def earliest_arrival(self, now: datetime) -> datetime | None:
        """Frühestmögliche Ankunft aus der Entfernung – nur Untergrenze für die Gewohnheit."""
        if self.dist_km is None or self.dist_km <= EARLIEST_MIN_KM:
            return None
        return now + timedelta(hours=self.dist_km / max(self.approach_speed_kmh, 5.0))

    def unknown_from(self, now: datetime, horizon_h: int = 24) -> datetime | None:
        """Ab wann die Ankunft offen ist: erstes noch nicht verlässlich gelerntes Feld (sonst None).

        None auch, wenn die Person zu Hause, lange weg oder per GPS auf dem Heimweg ist.
        """
        if self.home or self.long_away:
            return None
        if self.eta(now)["source"] == "gps":
            return None
        for at, slot in self._habit_slots(now, horizon_h, self.earliest_arrival(now)):
            if not self.habit_reliable(slot):
                return at
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
        ret = self.steady_return(now)
        if speed is not None and speed > MOVE_KMH and self.dist_km and self._approaching(speed):
            vl = self.approach_speed_kmh
            v = 0.5 * max(speed, vl) + 0.5 * vl  # Momenttempo (Stau, Anfahrt) darf nur verfrühen, nie verspäten
            eta = now + timedelta(hours=self.dist_km / max(v, 5))
            if ret and ret["eta"] > now:  # bestätigte Heimfahrt: deren Schnitt mitnehmen
                eta = min(eta, ret["eta"])
            info["eta"], info["source"], info["confidence"] = eta, "gps", 0.8
            return info
        if ret:  # bestätigte Heimfahrt, gerade kurz Rast/Stau: deren Ankunft gilt weiter
            info["eta"], info["source"], info["confidence"] = ret["eta"], "gps", 0.7
            return info
        if self.long_away:
            return info  # lange weg: die Gewohnheit sagt über die Rückkehr nichts
        habit = self.next_home_by_habit(now, earliest=self.earliest_arrival(now), since=self._away_since)
        if habit:
            info["eta"], info["confidence"] = habit[0], round(habit[1], 2)
            info["source"] = "gewohnheit"
        return info

    def to_dict(self) -> dict:
        d = {
            "person": self.person,
            "week": self.week,
            "visits": self.visits,
            "approach_speed_kmh": self.approach_speed_kmh,
            "arrivals_learned": self.arrivals_learned,
            "arrivals": self.arrivals,
        }
        if self.away_trips:
            d["away_trips"] = [[t.isoformat(), h] for t, h in self.away_trips]
        if self._away_since is not None:  # laufende Abwesenheit übersteht einen Neustart
            d["away"] = {"since": self._away_since.isoformat(), "max_km": self._away_km,
                         "long": self.long_away, "regular": self.away_regular,
                         "backup": None if self._backup is None else list(self._backup)}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PersonLearner":
        n = len(d["week"])
        p = cls(
            person=d["person"],
            week=d["week"],
            # alte Stände ohne Zähler gelten als gelernt
            visits=list(d.get("visits") or [HABIT_MIN_VISITS] * n),
            approach_speed_kmh=d.get("approach_speed_kmh", 35.0),
            arrivals_learned=d.get("arrivals_learned", 0),
            arrivals=_arrivals(d.get("arrivals", [])),
            away_trips=_trips((datetime.fromisoformat(t), h) for t, h in d.get("away_trips", [])),
        )
        away = d.get("away")
        if away and away.get("since"):
            p._away_since = datetime.fromisoformat(away["since"])
            p._away_km = away.get("max_km")
            p.long_away = bool(away.get("long"))
            p.away_regular = bool(away.get("regular"))
            b = away.get("backup")
            p._backup = (list(b[0]), list(b[1])) if b else None
        return p


def next_arrival(learners: list[PersonLearner], now: datetime) -> dict | None:
    """Früheste erwartete Ankunft, wenn gerade niemand zu Hause ist."""
    if any(p.home for p in learners):
        return None
    etas = [p.eta(now) for p in learners]
    etas = [e for e in etas if e["eta"] is not None]
    if not etas:
        return None
    return min(etas, key=lambda e: e["eta"])


def arrival_unknown(learners: list[PersonLearner], now: datetime) -> datetime | None:
    """Ab wann offen ist, ob jemand heimkommt (Gewohnheit lernt dort noch) – None: niemand offen."""
    if any(p.home for p in learners):
        return None
    times = [t for t in (p.unknown_from(now) for p in learners) if t is not None]
    return min(times, default=None)


def household_long_away(learners: list[PersonLearner]) -> bool:
    """Alle Personen lange weg: Zapfprofil und Raummodelle lernen nicht (eigenes Flag, kein Urlaub).

    Regelmäßige lange Abwesenheit (Pendeln) zählt nicht – sie gehört zum Alltag des Hauses.
    """
    return bool(learners) and all(p.long_away and p.home is False for p in learners)
