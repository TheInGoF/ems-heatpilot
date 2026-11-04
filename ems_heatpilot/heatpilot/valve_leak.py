"""Ventil schließt nicht (seit 0.70): Meldung, wenn ein Raum bei geschlossenem Ventil deutlich schneller warm wird als die
anderen Räume mit geschlossenem Ventil, sobald die Heizung läuft.

Mancher Ventilkörper schließt mit dem Thermostat nicht ganz (Adapter, fehlende Unterlegscheibe, falsch kalibrierter
Hub) – das Thermostat meldet 0 %, der Heizkörper bleibt lauwarm. Das lässt sich nur am Heizkörper beheben; HeatPilot
meldet es deshalb, statt es wegzuregeln. Erkennung aus den eigenen Messwerten der letzten ``DAYS`` Tage
(``room_samples`` mit ``samples`` am selben Zeitpunkt).

Seit 0.75.1 **gegen die Nachbarn** statt gegen „Heizung aus“: Läuft die Heizung, werden auch die übrigen Räume warm, die
Bewohner sind wach, es wird gekocht – „ohne Heizung“ sind vor allem Nächte. So sah bis 0.75 fast jeder Raum undicht aus
(30.10.–02.11.: +0,1 bis +0,45 K/h, gemeldet Küche und Flur). Jetzt zählt je Messschritt mit laufender Heizung
(Heizbetrieb, kein Warmwasser, Vorlauf mindestens ``HOT_K`` über dem Raum) nur, wie viel schneller der Raum warm wird als
der Median der anderen Räume, deren Ventil zugleich zu ist (bis ``CLOSED_PCT``) – Nachbarwärme, Wetter und Anwesenheit
heben sich dabei auf (``excess``). Undicht ist ein Raum, wenn das an mindestens ``MIN_DAYS`` Tagen (je mindestens
``DAY_MIN_H`` Stunden gesehen) im Mittel ``LEAK_KH`` oder mehr sind – ein einzelner Back- oder Sonnentag reicht nicht
(``finding``). Räume mit Fußbodenheizung zählen nicht, auch nicht als Vergleich (das RTL-Ventil am Heizkreis sagt nichts
über das Thermostat).

``check`` läuft im Takt (``main.run``) höchstens einmal je ``EVERY_S``; offene Meldungen ohne Befund schließen still.
"""

from __future__ import annotations

import statistics
import time

from . import floor_limit
from .values import local

DAYS = 7
CLOSED_PCT = 2.0
HOT_K = 15.0
LEAK_KH = 0.5     # 0.75.1: Überschuss gegen die anderen geschlossenen Räume (bis 0.75: 0,3 K/h gegen „Heizung aus“)
MIN_DAYS = 3      # … an so vielen Tagen
DAY_MIN_H = 0.5   # … je mindestens so lange gesehen
GAP_S = 900       # größere Lücken zwischen zwei Messpunkten zählen nicht
EVERY_S = 3600
KEY = "valve_leak:"


def excess(store, since: int, skip=()) -> dict[str, dict[str, tuple[float, float]]]:
    """Je Raum und Tag (Ortszeit, ISO-Datum): (Überschuss in K/h gegen den Median der anderen Räume mit geschlossenem
    Ventil, Stunden) – nur Schritte mit laufender Heizung und mindestens zwei geschlossenen Räumen. ``skip``: Räume, die
    weder zählen noch vergleichen (Fußbodenheizung)."""
    rows = store.query("SELECT r.ts, r.room, r.temp, r.valve, s.heating, s.dhw, s.flow FROM room_samples r "
                       "JOIN samples s ON s.ts = r.ts WHERE r.ts >= ? ORDER BY r.ts", (since,))
    steps: list[tuple] = []  # (ts, {Raum: (Ist, Ventil)}, heizt)
    for r in rows:
        if not steps or steps[-1][0] != r["ts"]:
            steps.append((r["ts"], {}, bool(r["heating"]) and not r["dhw"] and r["flow"] is not None, r["flow"]))
        if r["room"] not in skip:
            steps[-1][1][r["room"]] = (r["temp"], r["valve"])
    acc: dict[str, dict[str, list[float]]] = {}
    for a, b in zip(steps, steps[1:]):
        dt = b[0] - a[0]
        if dt <= 0 or dt > GAP_S or not a[2]:
            continue
        rates = {}
        for room, (t0, v0) in a[1].items():
            t1, v1 = b[1].get(room, (None, None))
            if None in (t0, t1, v0, v1) or max(v0, v1) > CLOSED_PCT or a[3] < t0 + HOT_K:
                continue
            rates[room] = (t1 - t0) * 3600 / dt
        if len(rates) < 2:
            continue
        day = local(a[0]).date().isoformat()
        for room, rate in rates.items():
            k = acc.setdefault(room, {}).setdefault(day, [0.0, 0.0])
            k[0] += (rate - statistics.median(x for r, x in rates.items() if r != room)) * dt / 3600
            k[1] += dt / 3600
    return {room: {d: (x / h, h) for d, (x, h) in days.items() if h > 0} for room, days in acc.items()}


def finding(days: dict[str, tuple[float, float]]) -> str | None:
    """Text des Befunds bzw. None (dicht oder zu wenig gesehen) – ``days`` wie ein Eintrag von ``excess``."""
    seen = [x for x, h in days.values() if h >= DAY_MIN_H]
    hits = [x for x in seen if x >= LEAK_KH]
    if len(hits) < MIN_DAYS:
        return None
    mean = f"{sum(hits) / len(hits):+.1f}".replace(".", ",")
    return (f"Ventil schließt wohl nicht ganz – bei 0 % wird der Raum an {len(hits)} von {len(seen)} Tagen schneller warm "
            f"als die anderen Räume mit geschlossenem Ventil, sobald die Heizung läuft (im Mittel {mean} K/h). "
            "Ventiladapter prüfen, Thermostat neu kalibrieren, ggf. Unterlegscheibe")


def check(eng, now: float | None = None) -> dict[str, str]:
    """Alle Räume prüfen (höchstens einmal je ``EVERY_S``), Meldungen öffnen bzw. still schließen. Gibt die Befunde
    {climate: Text} zurück."""
    now = time.time() if now is None else now
    if eng.notifier is None or now - eng.__dict__.get("_leak_ts", 0) < EVERY_S:
        return {}
    eng._leak_ts = now
    floors = {f.climate for f in floor_limit.floor_rooms(eng.cfg, eng.found.rooms)}
    by_room, found = excess(eng.store, int(now) - DAYS * 86400, floors), {}
    for room in eng.found.rooms:
        if room.climate in floors:
            continue
        text = finding(by_room.get(room.climate, {}))
        if text:
            found[KEY + room.climate] = f"{room.name}: {text}"
    for key, text in found.items():
        eng.notifier.problem(key, text, now)
    eng.notifier.settle(KEY, found, now)
    return {k[len(KEY):]: t for k, t in found.items()}
