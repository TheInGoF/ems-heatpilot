"""Nachtzeiten und Temperaturen aus Tado lernen – die Einstellungen sind nur Startwerte.

Gelernt wird aus dem, was Tado tatsächlich tut: das mittlere Soll aller Räume
(Median) über die letzten ``WINDOW_DAYS`` Tage, ohne Urlaub.

- **Nacht:** abends sinkt das Soll um mindestens ``DROP_K`` unter das Niveau der zwei Stunden
  davor (während jemand zu Hause ist; heizt Tado nachmittags wirklich, höchstens unter das
  Tagesniveau), morgens steigt es wieder zum Großteil zurück. Median über alle Nächte.
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
MIN_AWAY_H = 20.0
OFF_TEMP = 5.0                # Tado „aus“ = Frostschutz
DROP_K = 1.0
HOLD_MIN = 60                 # so lange muss die Absenkung mindestens halten
MAX_SHIFT_H = 3.0
MAX_RAISE_K = 3.0
EVENING = (18 * 60, 27 * 60)  # 18:00 bis 03:00 (Minuten ab Mitternacht des Abends)
MORNING = (3 * 60, 11 * 60)
DAY_MIN_PTS = 12              # so viele Nachmittagswerte (12–18 Uhr, jemand da), bevor das Tagesniveau gilt
DAY_REAL_C = OFF_TEMP + 5     # Tagesniveau darüber = Tado heizt nachmittags wirklich (nicht „aus“)


def _local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _hhmm(minutes: float) -> str:
    m = int(5 * round(minutes / 5)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def house_setpoints(store, since: int, skip: set[str] = frozenset(),
                    only: set[str] | None = None) -> dict[int, float]:
    """Median-Soll aller Räume je Zeitpunkt (nur wenn mindestens die Hälfte der Räume meldet).

    Steht ein Tado-Raum auf „aus“ (kein Soll, aber Raumtemperatur vorhanden), gilt der
    Frostschutz von Tado (5 °C) – „aus“ ist dann die Absenkung.
    """
    by_ts: dict[int, list[float]] = defaultdict(list)
    rooms: set[str] = set()
    for r in store.query(
        "SELECT ts, room, target, temp FROM room_samples WHERE ts >= ? AND (target IS NOT NULL OR temp IS NOT NULL)",
        (since,)
    ):
        if r["room"] in skip or (only is not None and r["room"] not in only):
            continue
        rooms.add(r["room"])
        by_ts[r["ts"]].append(r["target"] if r["target"] is not None else OFF_TEMP)
    need = max(1, len(rooms) // 2)
    return {ts: statistics.median(v) for ts, v in by_ts.items() if len(v) >= need}


def _reference(pts: list[tuple[int, int, float]], i: int, day_vals: list[float], home: dict) -> float | None:
    """Soll vor der möglichen Absenkung: Median der 2 h davor, solange jemand zu Hause ist.

    Bezug ist der Abend selbst, nicht der Nachmittag – steht Tado nachmittags auf „aus“ und
    heizt erst ab 18 Uhr, ist das Absenken danach trotzdem die Nacht. Heizt Tado nachmittags
    aber wirklich, ist der Bezug höchstens das Tagesniveau: Ein Abendkomfort über dem Tag
    (22 °C statt 20 °C) ist keine Absenkung, wenn es danach auf 20 °C zurückgeht, und morgens
    muss das Soll nur zum Tagesniveau zurück, nicht zum Abendkomfort.
    """
    m = pts[i][1]
    before = [v for ts, mm, v in pts[:i] if mm >= m - 120 and home.get(ts, True)]
    ref = statistics.median(before) if len(before) >= 6 else None
    if ref is None:
        evening = [v for ts, mm, v in pts[:i] if home.get(ts, True)]
        cands = [statistics.median(x) for x in (day_vals, evening) if len(x) >= 6]
        ref = max(cands) if cands else None
    day = statistics.median(day_vals) if len(day_vals) >= DAY_MIN_PTS else None
    if ref is not None and day is not None and day > DAY_REAL_C:
        ref = min(ref, day)
    return ref


def learn(store, now_ts: int, skip_rooms: set[str] = frozenset(), rooms: set[str] | None = None,
          persons: list[str] | None = None, since_ts: int | None = None) -> dict:
    """``since_ts``: frühestens ab dann (zuletzt hat HeatPilot selbst gesteuert – das wäre zirkulär)."""
    since = max(now_ts - WINDOW_DAYS * 86400, int(since_ts or 0))
    sp = house_setpoints(store, since, skip_rooms, rooms)
    home = store.home_by_ts(since, persons)
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
        if m >= EVENING[0] - 120:  # zwei Stunden davor als Bezug fürs Absenken
            evenings[d.date().isoformat()].append((ts, m, v))
        elif m < MORNING[1]:
            key = (d - timedelta(days=1)).date().isoformat()
            evenings[key].append((ts, m + 24 * 60, v))

    starts, ends, night_vals = [], [], []
    for day, pts in evenings.items():
        pts.sort()
        start_i = ref = None
        for i, (ts, m, v) in enumerate(pts):
            if not EVENING[0] <= m < EVENING[1] or not home.get(ts, True):
                continue
            ref = _reference(pts, i, days.get(day, []), home)
            if ref is None or v > ref - DROP_K:
                continue
            hold = [p for p in pts[i:] if p[1] < m + HOLD_MIN]
            if hold and all(p[2] <= ref - DROP_K for p in hold) and hold[-1][1] >= m + HOLD_MIN - 10:
                start_i = i
                break
        if start_i is None:
            continue
        start = pts[start_i][1]
        night_med = statistics.median(v for _, m, v in pts[start_i:] if m < MORNING[0] + 24 * 60)
        # morgens: zurück, sobald das Soll den Großteil des Wegs zum Bezug (Abend bzw. Tagesniveau) geschafft hat
        rise = night_med + 0.75 * (max(ref, night_med + DROP_K) - night_med)
        end = None
        for ts, m, v in pts[start_i:]:
            if MORNING[0] + 24 * 60 <= m < MORNING[1] + 24 * 60 and v >= rise:
                end = m
                break
        if end is None:
            continue
        starts.append(start)
        ends.append(end - 24 * 60)
        night_vals.extend(v for _, m, v in pts if start <= m < end)

    away = [v for ts, v in series if not home.get(ts, True) and 8 <= _local(ts).hour < 18]
    ts_all = [ts for ts, _ in series]
    gaps = [b - a for a, b in zip(ts_all, ts_all[1:]) if 0 < b - a <= 900]
    step_h = (statistics.median(gaps) if gaps else 300) / 3600
    away_h = len(away) * step_h
    out = {"nights": len(starts), "away_hours": round(away_h, 1), "window_days": WINDOW_DAYS,
           "night_start": None, "night_end": None, "night_temp": None, "away_temp": None}
    if starts:
        out["night_start"] = _hhmm(statistics.median(starts))
        out["night_end"] = _hhmm(statistics.median(ends))
        out["night_temp"] = round(statistics.median(night_vals) * 2) / 2
    if away_h >= MIN_AWAY_H:
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
    plan: list[dict] | None = None  # Wochenplan Mo..So (Steuerung heatpilot), sonst None

    def is_night(self, t: time) -> bool:
        if self.night_start <= self.night_end:
            return self.night_start <= t < self.night_end
        return t >= self.night_start or t < self.night_end

    def as_dict(self) -> dict:
        return {"night_start": self.night_start.strftime("%H:%M"), "night_end": self.night_end.strftime("%H:%M"),
                "night_temp": self.night_temp, "away_temp": self.away_temp, "source": self.source,
                "plan": self.plan}


def _shift_ok(learned: str, configured: time) -> bool:
    h, m = map(int, learned.split(":"))
    diff = abs(h * 60 + m - _minutes(configured))
    return min(diff, 24 * 60 - diff) <= MAX_SHIFT_H * 60


def _plan_median(cfg, key: str) -> time:
    """Mo–Fr-Mittel (Median) des Wochenplans als feste Uhrzeit für ältere Aufrufer (Ersparnis)."""
    mins = []
    for d in range(5):
        start, end = cfg.night_times(d)
        m = _minutes(start if key == "start" else end)
        # gelernter Beginn nach Mitternacht (0.36) zählt als später Abend, nicht als Morgen
        mins.append(m + 24 * 60 if key == "start" and m < 12 * 60 else m)
    m = int(statistics.median(mins)) % (24 * 60)
    return time(m // 60, m % 60)


def effective(cfg, learned: dict | None) -> Effective:
    src = {"night_start": "eingestellt", "night_end": "eingestellt", "night_temp": "eingestellt",
           "away_temp": "eingestellt"}
    if getattr(cfg, "control_mode", "beobachten") == "heatpilot":
        # HeatPilot schreibt die Zeiten selbst – aus Tado lernen wäre zirkulär: der Wochenplan gilt
        src = {k: "wochenplan" for k in src}
        # seit 0.36: wirksame Zeiten je Tag mit Herkunft je Hälfte (eingestellt | gelernt (Strom))
        plan = [{"day": day, **cfg.night_entry(i)} for i, day in enumerate(("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"))]
        return Effective(_plan_median(cfg, "start"), _plan_median(cfg, "end"), cfg.night_temp, cfg.away_temp,
                         src, plan)
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
