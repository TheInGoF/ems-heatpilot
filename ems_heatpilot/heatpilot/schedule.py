"""Nachtzeiten und Temperaturen aus Tado lernen – die Einstellungen sind nur Startwerte.

Gelernt wird aus dem, was Tado tatsächlich tut: das mittlere Soll aller Räume
(Median) über die letzten ``WINDOW_DAYS`` Tage, ohne Urlaub.

- **Nacht:** abends sinkt das Soll um mindestens ``DROP_K`` unter das Tagesniveau
  (während jemand zu Hause ist), morgens steigt es wieder. Median über alle Nächte.
- **Nachttemperatur:** Median-Soll in der gelernten Nacht.
- **Abwesenheit:** Median-Soll tagsüber, wenn niemand zu Hause ist (Geofencing).

Leitplanken: Zeiten erst ab ``MIN_NIGHTS`` erkannten Nächten und höchstens
``MAX_SHIFT_H`` vom eingestellten Wert entfernt. Temperaturen nie unter dem
eingestellten Wert – Komfort bleibt eine feste Grenze – und höchstens
``MAX_RAISE_K`` darüber.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta

WINDOW_DAYS = 28
MIN_NIGHTS = 7
MIN_AWAY_SAMPLES = 20 * 12    # 20 Stunden à 5 min
DROP_K = 1.0
HOLD_MIN = 60                 # so lange muss die Absenkung mindestens halten
MAX_SHIFT_H = 3.0
MAX_RAISE_K = 3.0
EVENING = (18 * 60, 27 * 60)  # 18:00 bis 03:00 (Minuten ab Mitternacht des Abends)
MORNING = (3 * 60, 11 * 60)


def _local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _hhmm(minutes: float) -> str:
    m = int(5 * round(minutes / 5)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def house_setpoints(store, since: int, skip: set[str] = frozenset()) -> dict[int, float]:
    """Median-Soll aller Räume je Zeitpunkt (nur wenn mindestens die Hälfte der Räume meldet)."""
    by_ts: dict[int, list[float]] = defaultdict(list)
    rooms: set[str] = set()
    for r in store.query(
        "SELECT ts, room, target FROM room_samples WHERE ts >= ? AND target IS NOT NULL", (since,)
    ):
        if r["room"] in skip:
            continue
        rooms.add(r["room"])
        by_ts[r["ts"]].append(r["target"])
    need = max(1, len(rooms) // 2)
    return {ts: statistics.median(v) for ts, v in by_ts.items() if len(v) >= need}


def learn(store, now_ts: int, skip_rooms: set[str] = frozenset()) -> dict:
    since = now_ts - WINDOW_DAYS * 86400
    sp = house_setpoints(store, since, skip_rooms)
    home: dict[int, bool] = {r["ts"]: bool(r["h"]) for r in store.query(
        "SELECT ts, MAX(home) AS h FROM person_samples WHERE ts >= ? GROUP BY ts", (since,))}
    vacation = {r["ts"] for r in store.query(
        "SELECT ts FROM samples WHERE ts >= ? AND vacation = 1", (since,))}
    series = sorted((ts, v) for ts, v in sp.items() if ts not in vacation)

    # nach Abend gruppieren: Minuten ab Mitternacht des Abends (nach Mitternacht > 1440)
    evenings: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
    days: dict[str, list[float]] = defaultdict(list)
    for ts, v in series:
        d = _local(ts)
        m = d.hour * 60 + d.minute
        if 12 * 60 <= m < 18 * 60 and home.get(ts, True):
            days[d.date().isoformat()].append(v)
        if m >= EVENING[0]:
            evenings[d.date().isoformat()].append((ts, m, v))
        elif m < MORNING[1]:
            key = (d - timedelta(days=1)).date().isoformat()
            evenings[key].append((ts, m + 24 * 60, v))

    starts, ends, night_vals = [], [], []
    for day, pts in evenings.items():
        if len(days.get(day, [])) < 12:
            continue
        level = statistics.median(days[day])
        pts.sort()
        start_i = None
        for i, (ts, m, v) in enumerate(pts):
            if not EVENING[0] <= m < EVENING[1] or v > level - DROP_K or not home.get(ts, True):
                continue
            hold = [p for p in pts[i:] if p[1] < m + HOLD_MIN]
            if hold and all(p[2] <= level - DROP_K for p in hold) and hold[-1][1] >= m + HOLD_MIN - 10:
                start_i = i
                break
        if start_i is None:
            continue
        end = None
        for ts, m, v in pts[start_i:]:
            if MORNING[0] + 24 * 60 <= m < MORNING[1] + 24 * 60 and v >= level - 0.5:
                end = m
                break
        if end is None:
            continue
        start = pts[start_i][1]
        starts.append(start)
        ends.append(end - 24 * 60)
        night_vals.extend(v for _, m, v in pts if start <= m < end)

    away = [v for ts, v in series if not home.get(ts, True) and 8 <= _local(ts).hour < 18]
    out = {"nights": len(starts), "away_samples": len(away), "window_days": WINDOW_DAYS,
           "night_start": None, "night_end": None, "night_temp": None, "away_temp": None}
    if starts:
        out["night_start"] = _hhmm(statistics.median(starts))
        out["night_end"] = _hhmm(statistics.median(ends))
        out["night_temp"] = round(statistics.median(night_vals) * 2) / 2
    if len(away) >= MIN_AWAY_SAMPLES:
        out["away_temp"] = round(statistics.median(away) * 2) / 2
    return out


@dataclass
class Effective:
    """Was HeatPilot tatsächlich verwendet – mit Herkunft je Wert."""

    night_start: time
    night_end: time
    night_temp: float
    away_temp: float
    source: dict

    def is_night(self, t: time) -> bool:
        if self.night_start <= self.night_end:
            return self.night_start <= t < self.night_end
        return t >= self.night_start or t < self.night_end

    def as_dict(self) -> dict:
        return {"night_start": self.night_start.strftime("%H:%M"), "night_end": self.night_end.strftime("%H:%M"),
                "night_temp": self.night_temp, "away_temp": self.away_temp, "source": self.source}


def _shift_ok(learned: str, configured: time) -> bool:
    h, m = map(int, learned.split(":"))
    diff = abs(h * 60 + m - _minutes(configured))
    return min(diff, 24 * 60 - diff) <= MAX_SHIFT_H * 60


def effective(cfg, learned: dict | None) -> Effective:
    src = {"night_start": "eingestellt", "night_end": "eingestellt", "night_temp": "eingestellt",
           "away_temp": "eingestellt"}
    eff = Effective(cfg.night_start, cfg.night_end, cfg.night_temp, cfg.away_temp, src)
    if not cfg.learn_schedule or not learned:
        return eff
    if learned.get("nights", 0) >= MIN_NIGHTS:
        for key in ("night_start", "night_end"):
            value = learned.get(key)
            if value and _shift_ok(value, getattr(cfg, key)):
                h, m = map(int, value.split(":"))
                setattr(eff, key, time(h, m))
                src[key] = "gelernt"
        if learned.get("night_temp") is not None:
            eff.night_temp = min(max(learned["night_temp"], cfg.night_temp), cfg.night_temp + MAX_RAISE_K)
            src["night_temp"] = "gelernt" if eff.night_temp != cfg.night_temp else "eingestellt (Untergrenze)"
    if learned.get("away_temp") is not None:
        eff.away_temp = min(max(learned["away_temp"], cfg.away_temp), cfg.away_temp + MAX_RAISE_K)
        src["away_temp"] = "gelernt" if eff.away_temp != cfg.away_temp else "eingestellt (Untergrenze)"
    return eff
