"""Schlafenszeit aus dem Hausstromverbrauch lernen (seit 0.36).

Abends fällt der Verbrauch des Hauses auf das Nachtniveau (Standby) – Schlafenszeit; morgens
steigt er wieder (Kaffeemaschine, Licht, Bad) – Aufstehen. Gemessen wird die Summe der gewählten
Leistungssensoren (``power_w``) abzüglich der Verbraucher, die nicht zählen (``power_ex_w``:
Waschmaschine, Spülmaschine, Wallbox). Daraus entsteht ein Wochenplan je Wochentag, der mit
``night_plan_auto`` den eingestellten Wochenplan ersetzt, wo er belastbar ist – sonst nur Vorschlag.

Je Nacht (Fenster 18:00 bis 12:00 des Folgetags, Ortszeit, auch über die Zeitumstellung):

1. Netto-Leistung je Messpunkt, in 10-min-Bins der Median (robust gegen Spitzen).
   Fehlen über ``MAX_MISSING`` der Bins → „keine Daten“.
2. Urlaub oder alle lange weg irgendwo im Fenster, oder um 22:00 niemand zu Hause (Personen vorhanden,
   aber keiner da) → „nicht zu Hause“. Anwesenheit unbekannt schließt nicht aus.
3. Nachtgrund = 10. Perzentil der Bins 00:00–04:00, Abendniveau = Median 19:00–21:00 (sonst 18:00–22:00).
   Unterschied unter ``MIN_CONTRAST_W`` → „zu wenig Unterschied“ (Haus ohne Abendverbrauch oder der
   Sensor misst nur einen Teil). Schwelle = Grund + max(``THR_MIN_W``, 35 % des Unterschieds), mindestens
   aber knapp über dem 90. Perzentil der Nachtbins (Kühlschrank-/Gefrierschranklauf: 15 min je 45 min sind
   in 10-min-Bins nicht wegzumitteln – läge die Schwelle darunter, zählte jeder Lauf als „wach“), gedeckelt
   bei ``THR_MAX_SHARE`` des Unterschieds, damit sie nie an den Abend heranreicht.
4. Schlafbeginn: Beginn des ersten Laufs von 30 min unter der Schwelle (19:00–02:30), nach dem bis 03:00
   mindestens 60 % der Bins unter der Schwelle bleiben – sonst ist noch jemand wach, weitersuchen.
5. Aufstehen: erster Bin ab 03:30 bzw. 4 h nach dem Schlafbeginn (bis 11:00), der über der Schwelle
   liegt und mit dem 2 von 3 Bins (er und die zwei folgenden) darüber liegen – ein kurzer Einbruch nach dem
   Aufstehen schadet nicht. Ohne Aufstehen zählt die Nacht nur mit Beginn.

Wochenplan: Beginn aus den Nächten, deren **Abend** der Wochentag ist, Ende aus denen, deren **Morgen**
er ist – wie der Wochenplan der Einstellungen (Nacht Fr→Sa: „Fr ab“, „Sa bis“). Median der letzten
``WEEKS`` Wochen, auf 5 min gerundet; belastbar ab ``MIN_NIGHTS`` Nächten mit Spannweite (Q3−Q1) bis
``MAX_SPREAD_MIN`` und plausibler Lage (Beginn 19:00–01:30, Ende 03:30–09:30).

Zustand in kv ``KV_NIGHTS`` = {Datum des Abends: Nacht}. Gerechnet wird eine Nacht genau einmal, sobald
ihr Fenster vorbei ist; Nächte älter als ``KEEP_DAYS`` fallen heraus (``store.reset_learning`` löscht
alles, nach dem Neueinlesen wird neu gerechnet).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from .config import WEEKDAYS

KV_NIGHTS = "sleep.nights"
KEEP_DAYS = 56
WEEKS = 6
BIN_MIN = 10
MAX_HOLD_S = 900                     # ein Messwert füllt höchstens so lange (längstes Abtastintervall)
WINDOW = (18 * 60, 36 * 60)          # Minuten ab Mitternacht des Abends: 18:00 bis 12:00 des Folgetags
MAX_MISSING = 0.20
BASE_SPAN = (24 * 60, 28 * 60)       # Nachtgrund aus 00:00–04:00
EVE_SPAN = (19 * 60, 21 * 60)        # Abendniveau aus 19:00–21:00 …
EVE_SPAN_WIDE = (18 * 60, 22 * 60)   # … sonst 18:00–22:00
MIN_CONTRAST_W = 60.0
THR_MIN_W = 40.0
THR_SHARE = 0.35
NIGHT_BAND_P = 0.90                  # Nachtbins bis zu diesem Perzentil (Kühlschrank an) bleiben unter der Schwelle …
NIGHT_BAND_W = 20.0                  # … mit so viel Luft
THR_MAX_SHARE = 0.60                 # Schwelle höchstens so weit vom Grund zum Abend
START_SPAN = (19 * 60, 26 * 60 + 30)  # Schlafbeginn 19:00–02:30
START_RUN = 3                        # so viele Bins (30 min) unter der Schwelle
QUIET_UNTIL = 27 * 60                # danach bis 03:00 …
QUIET_SHARE = 0.60                   # … so viele Bins unter der Schwelle, sonst ist noch jemand wach
END_EARLIEST = 27 * 60 + 30          # Aufstehen frühestens 03:30 …
END_AFTER_START_MIN = 4 * 60         # … und 4 h nach dem Schlafbeginn
END_LATEST = 35 * 60                 # bis 11:00
END_ABOVE = 2                        # von 3 Bins über der Schwelle
HOME_CHECK = (21 * 60 + 50, 22 * 60 + 10)  # „um 22:00“ niemand zu Hause
MIN_NIGHTS = 3
MAX_SPREAD_MIN = 90
START_PLAUSIBLE = (19 * 60, 25 * 60 + 30)   # 19:00–01:30 (Minuten ab Mitternacht des Abends)
END_PLAUSIBLE = (3 * 60 + 30, 9 * 60 + 30)  # 03:30–09:30 (Minuten am Morgen)
ROUND_MIN = 5

OK, NO_DATA, NOT_HOME, LOW_CONTRAST, NO_START = ("ok", "keine Daten", "nicht zu Hause",
                                                 "zu wenig Unterschied", "kein Schlafbeginn")


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _at(day: date, minutes: int) -> int:
    """Zeitpunkt der Wanduhrzeit ``minutes`` ab Mitternacht von ``day`` (über 1440 = Folgetag) – Ortszeit."""
    d = day + timedelta(days=minutes // 1440)
    m = minutes % 1440
    return int(datetime.combine(d, time(m // 60, m % 60)).timestamp())


def _minute_of(ts: int, day: date) -> int:
    """Wanduhr-Minuten ab Mitternacht des Abends ``day`` (am Folgetag über 1440)."""
    d = _local(ts)
    return (d.date() - day).days * 1440 + d.hour * 60 + d.minute


def _hhmm(minutes: float) -> str:
    m = int(ROUND_MIN * round(minutes / ROUND_MIN)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _percentile(values: list[float], p: float) -> float:
    vals = sorted(values)
    pos = p * (len(vals) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def night_bins(rows, day: date) -> dict[int, float]:
    """Netto-Leistung (W) je 10-min-Bin als Median; Schlüssel = Bin-Beginn in Minuten ab Mitternacht des Abends.

    Ohne Hausleistung (``power_w`` None) zählt der Messpunkt nicht; Ausschlüsse werden abgezogen, nie unter 0.
    """
    by_bin: dict[int, list[float]] = defaultdict(list)
    # Ein Messwert gilt bis zum nächsten Messpunkt: bei langem Abtastintervall (bis 15 min) füllt er
    # auch die folgenden Bins, sonst fehlte jeder dritte und keine Nacht wäre vollständig
    stamps = sorted(r["ts"] for r in rows)
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    hold_min = min(max(statistics.median(gaps) if gaps else 60, 60), MAX_HOLD_S) / 60
    for r in rows:
        if r["power_w"] is None:
            continue
        m = _minute_of(r["ts"], day)
        value = max(r["power_w"] - (r["power_ex_w"] or 0.0), 0.0)
        b = m - m % BIN_MIN
        while b < m + hold_min:
            if WINDOW[0] <= b < WINDOW[1]:
                by_bin[b].append(value)
            b += BIN_MIN
    return {m: statistics.median(v) for m, v in by_bin.items()}


def learn_night(rows, day: date) -> dict:
    """Eine Nacht (Abend ``day``) aus den Messpunkten ihres Fensters – siehe Modulbeschreibung.

    Gibt {start_min, end_min, base_w, eve_w, thr_w, valid, reason}; start_min zählt ab Mitternacht des
    Abends (kann über 1440 liegen), end_min am Folgetag.
    """
    out: dict = {"start_min": None, "end_min": None, "base_w": None, "eve_w": None, "thr_w": None,
                 "valid": False, "reason": NO_DATA}
    bins = night_bins(rows, day)
    if len(bins) < (1 - MAX_MISSING) * (WINDOW[1] - WINDOW[0]) // BIN_MIN:
        return out
    home_22 = [r["home_count"] for r in rows
               if r["home_count"] is not None and HOME_CHECK[0] <= _minute_of(r["ts"], day) <= HOME_CHECK[1]]
    if any(r["vacation"] or r["away_long"] for r in rows) or (home_22 and max(home_22) == 0):
        out["reason"] = NOT_HOME
        return out
    base_vals = [v for m, v in bins.items() if BASE_SPAN[0] <= m < BASE_SPAN[1]]
    eve_vals = ([v for m, v in bins.items() if EVE_SPAN[0] <= m < EVE_SPAN[1]]
                or [v for m, v in bins.items() if EVE_SPAN_WIDE[0] <= m < EVE_SPAN_WIDE[1]])
    if not base_vals or not eve_vals:
        return out
    base, eve = _percentile(base_vals, 0.10), statistics.median(eve_vals)
    out.update(base_w=round(base, 1), eve_w=round(eve, 1))
    if eve - base < MIN_CONTRAST_W:
        out["reason"] = LOW_CONTRAST
        return out
    thr = base + max(THR_MIN_W, THR_SHARE * (eve - base))
    thr = min(max(thr, _percentile(base_vals, NIGHT_BAND_P) + NIGHT_BAND_W), base + THR_MAX_SHARE * (eve - base))
    out["thr_w"] = round(thr, 1)

    start = None
    for m in range(START_SPAN[0], START_SPAN[1] + 1, BIN_MIN):
        run = [bins.get(m + i * BIN_MIN) for i in range(START_RUN)]
        if any(v is None or v >= thr for v in run):
            continue
        later = [v for mm, v in bins.items() if m <= mm < QUIET_UNTIL]
        if later and sum(v < thr for v in later) / len(later) >= QUIET_SHARE:
            start = m
            break
    if start is None:
        out["reason"] = NO_START
        return out
    out.update(start_min=start, valid=True, reason=OK)

    for m in range(max(END_EARLIEST, start + END_AFTER_START_MIN), END_LATEST + 1, BIN_MIN):
        trio = [bins.get(m + i * BIN_MIN) for i in range(3)]
        if trio[0] is not None and trio[0] > thr and sum(v is not None and v > thr for v in trio) >= END_ABOVE:
            out["end_min"] = m - 1440
            break
    return out


def learn(store, now_ts: int) -> dict[str, dict]:
    """Fertige, noch nicht gespeicherte Nächte rechnen, alte entfernen; gibt alle gespeicherten zurück.

    Nächte vor dem ersten Messpunkt mit Hausleistung gibt es nicht (sie würden sonst als „keine Daten“
    zählen). Eine Abfrage je offener Nacht – im Betrieb eine am Tag.
    """
    nights: dict[str, dict] = store.get(KV_NIGHTS) or {}
    today = _local(now_ts).date()
    oldest = today - timedelta(days=KEEP_DAYS)
    changed = False
    for key in [k for k in nights if date.fromisoformat(k) < oldest]:
        del nights[key]
        changed = True
    since = store.get("power.since")  # 0.44: ältere Hausleistung gehört zu früher gewählten Sensoren
    first = store.query("SELECT MIN(ts) AS t FROM samples WHERE power_w IS NOT NULL AND ts >= ?",
                        (int(since) if isinstance(since, (int, float)) else 0,))[0]["t"]
    if first is not None:
        # erste Nacht, deren Fenster ganz nach dem ersten Messwert liegt – angebrochene Nächte nicht zählen
        day = _local(first).date() - timedelta(days=1)
        while _at(day, WINDOW[0]) < first:
            day += timedelta(days=1)
        day = max(oldest, day)
        while _at(day, WINDOW[1]) <= now_ts:
            key = day.isoformat()
            if key not in nights:
                rows = store.query(
                    "SELECT ts, power_w, power_ex_w, vacation, away_long, home_count FROM samples "
                    "WHERE ts >= ? AND ts < ?", (_at(day, WINDOW[0]), _at(day, WINDOW[1])))
                if any(r["power_w"] is not None for r in rows):
                    nights[key] = learn_night(rows, day)
                    changed = True
            day += timedelta(days=1)
    if changed:
        store.put(KV_NIGHTS, nights)
    return nights


def _half(values: list[float], plausible: tuple[int, int]) -> tuple[str | None, int, int | None, bool]:
    """(Uhrzeit, Zahl der Nächte, Spannweite Q3−Q1 in min, belastbar) einer Planhälfte."""
    if not values:
        return None, 0, None, False
    med = statistics.median(values)
    spread = None
    if len(values) >= 2:
        q = statistics.quantiles(values, n=4, method="inclusive")
        spread = int(round(q[2] - q[0]))
    reliable = (len(values) >= MIN_NIGHTS and spread is not None and spread <= MAX_SPREAD_MIN
                and plausible[0] <= med <= plausible[1])
    return _hhmm(med), len(values), spread, reliable


def plan(nights: dict[str, dict], now_ts: int, weeks: int = WEEKS) -> list[dict]:
    """Wochenplan Mo..So aus den gültigen Nächten der letzten ``weeks`` Wochen – Beginn nach Abend, Ende nach Morgen."""
    since = _local(now_ts).date() - timedelta(days=7 * weeks)
    starts: list[list[float]] = [[] for _ in range(7)]
    ends: list[list[float]] = [[] for _ in range(7)]
    for key, n in nights.items():
        d = date.fromisoformat(key)
        if d < since or not n.get("valid"):
            continue
        if n.get("start_min") is not None:
            starts[d.weekday()].append(n["start_min"])
        if n.get("end_min") is not None:
            ends[(d + timedelta(days=1)).weekday()].append(n["end_min"])
    out = []
    for i, day in enumerate(WEEKDAYS):
        s, e = _half(starts[i], START_PLAUSIBLE), _half(ends[i], END_PLAUSIBLE)
        out.append({"day": day, "start": s[0], "end": e[0], "n_start": s[1], "n_end": e[1],
                    "spread_start_min": s[2], "spread_end_min": e[2],
                    "reliable_start": s[3], "reliable_end": e[3]})
    return out


def learned_plan(plan_days: list[dict]) -> list[dict]:
    """Für ``Config.night_plan_learned``: je Tag {start, end}, None, wo die Hälfte nicht belastbar ist."""
    return [{"start": d["start"] if d["reliable_start"] else None,
             "end": d["end"] if d["reliable_end"] else None} for d in plan_days]


def summary(nights: dict[str, dict], now_ts: int, cfg, sensors: list[str], exclude: list[str]) -> dict:
    """Zusammenfassung für Oberfläche und API – ohne Sensoren harmlos (Plan mit None, nichts aktiv)."""
    days = plan(nights, now_ts) if sensors else plan({}, now_ts)
    since = _local(now_ts).date() - timedelta(days=7 * WEEKS)
    recent = [n for key, n in nights.items() if sensors and date.fromisoformat(key) >= since]
    invalid = Counter(n.get("reason") or NO_DATA for n in recent if not n.get("valid"))
    reliable = any(d["reliable_start"] or d["reliable_end"] for d in days)
    return {
        "active": bool(cfg.night_plan_auto and reliable),
        "auto": bool(cfg.night_plan_auto),
        "sensors": list(sensors),
        "exclude": list(exclude),
        "plan": days,
        "nights": sum(1 for n in recent if n.get("valid")),
        "invalid": dict(invalid),
        "weeks": WEEKS,
    }
