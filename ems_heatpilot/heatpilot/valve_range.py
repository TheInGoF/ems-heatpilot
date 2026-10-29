"""Wirksamer Bereich des Ventils (seit 0.70): ab welcher Öffnung ein ventilgeführter Raum nicht schneller warm wird.

Thermostatventile geben den größten Teil des Durchflusses oft schon im ersten Teil des Hubs frei – darüber bringt mehr
Öffnung kaum noch etwas. Der PID weiß das nicht: Er zieht bis 100 % auf und regelt danach zu spät zurück. Gelernt wird
aus den eigenen Messwerten der letzten ``DAYS`` Tage (``room_samples`` mit ``samples``), nur wenn der Raum Wärme will
(unter Soll − ``WANT_K``), die Heizung läuft (Heizbetrieb, kein Warmwasser, Vorlauf ``HOT_K`` über dem Raum) und das
Ventil ruhig steht: je Öffnungsklasse (``BINS``) der Anstieg in K/h je K Vorlauf über Raum. ``full_at`` ist die
kleinste Klasse, ab der alle Klassen bis 100 % mindestens ``FULL_FRAC`` des Anstiegs bei voll offen bringen.

Verwendet wird das nur als weicher Deckel für den PID (``balance_auto``, Zustand ``limit``) – nie als Grund für mehr
Vorlauf: Wird ein Raum am Deckel nicht warm, ruht die Grenze, bis er es ist (``balance_auto.RELEASE_S``). Unter
``PLAUSIBLE_PCT`` ist eher das Ventil falsch kalibriert als die Physik so – dann gilt keine Grenze, sondern eine Meldung.
"""

from __future__ import annotations

DAYS = 14
BINS = (20, 40, 60, 80, 100)
WANT_K = 0.2
HOT_K = 15.0
STEADY_PCT = 5.0
MIN_H = 1.0          # so viele Stunden je Klasse, damit sie zählt
FULL_FRAC = 0.9
PLAUSIBLE_PCT = 60
GAP_S = 900
KEY = "valve_range:"


def _bin(valve: float) -> int:
    return next(b for b in BINS if valve <= b)


def rates(store, climate: str, since: int) -> dict[int, tuple[float, float]]:
    """Je Öffnungsklasse (Anstieg in K/h je K Vorlauf über Raum, Stunden) – nur Klassen mit Daten."""
    rows = store.query("SELECT r.ts, r.temp, r.target, r.valve, s.heating, s.dhw, s.flow FROM room_samples r "
                       "JOIN samples s ON s.ts = r.ts WHERE r.room = ? AND r.ts >= ? ORDER BY r.ts", (climate, since))
    acc: dict[int, list[float]] = {}
    for a, b in zip(rows, rows[1:]):
        dt = b["ts"] - a["ts"]
        if dt <= 0 or dt > GAP_S or None in (a["temp"], b["temp"], a["target"], a["valve"], b["valve"], a["flow"]):
            continue
        warm = a["flow"] - a["temp"]
        if (a["temp"] >= a["target"] - WANT_K or not a["heating"] or a["dhw"] or warm < HOT_K
                or a["valve"] <= 2.0 or abs(b["valve"] - a["valve"]) > STEADY_PCT):
            continue
        k = acc.setdefault(_bin(a["valve"]), [0.0, 0.0])
        k[0] += (b["temp"] - a["temp"]) / warm
        k[1] += dt / 3600
    return {b: (d / h, h) for b, (d, h) in acc.items() if h > 0}


def full_at(r: dict[int, tuple[float, float]]) -> int | None:
    """Kleinste Öffnung, ab der es nicht mehr schneller geht – None, solange voll offen zu wenig gesehen ist."""
    top = r.get(100)
    if not top or top[1] < MIN_H or top[0] <= 0:
        return None
    best = 100
    for b in reversed(BINS[:-1]):
        got = r.get(b)
        if not got or got[1] < MIN_H or got[0] < FULL_FRAC * top[0]:
            break
        best = b
    return best


def update(bal: dict, notifier, rooms, store, now: int) -> None:
    """Grenzen der ventilgeführten Räume neu lernen (``bal["limit"]``), unplausibel kleine melden statt verwenden."""
    limits, found = bal.setdefault("limit", {}), {}
    for room in rooms:
        pct = full_at(rates(store, room.climate, now - DAYS * 86400))
        if pct is None or pct >= 100:
            limits.pop(room.climate, None)
        elif pct < PLAUSIBLE_PCT:
            limits.pop(room.climate, None)
            found[KEY + room.climate] = (f"{room.name}: Ventil wirkt schon ab {pct} % voll – eher falsch kalibriert als "
                                         "echt. Thermostat neu kalibrieren bzw. Ventil prüfen; HeatPilot begrenzt nicht")
        else:
            limits[room.climate] = float(pct)
    if notifier is not None:
        for key, text in found.items():
            notifier.problem(key, text, now)
        notifier.settle(KEY, found, now)
