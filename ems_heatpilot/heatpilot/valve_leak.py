"""Ventil schließt nicht (seit 0.70): Meldung, wenn ein Raum bei geschlossenem Ventil warm wird, sobald die Heizung läuft.

Mancher Ventilkörper schließt mit dem Thermostat nicht ganz (Adapter, fehlende Unterlegscheibe, falsch kalibrierter
Hub) – das Thermostat meldet 0 %, der Heizkörper bleibt lauwarm. Das lässt sich nur am Heizkörper beheben; HeatPilot
meldet es deshalb, statt es wegzuregeln. Erkennung aus den eigenen Messwerten der letzten ``DAYS`` Tage
(``room_samples`` mit ``samples`` am selben Zeitpunkt): je Raum der Temperaturverlauf bei Ventil bis ``CLOSED_PCT``,
getrennt danach, ob die Heizung läuft (Heizbetrieb, kein Warmwasser, Vorlauf mindestens ``HOT_K`` über dem Raum) oder
nicht. Wird der Raum mit laufender Heizung um ``LEAK_KH`` je Stunde schneller warm als ohne – je mindestens ``MIN_H``
Stunden –, ist es das Ventil: Sonne und Nachbarwärme kommen auch ohne Heizung. Räume mit Fußbodenheizung zählen nicht
(das RTL-Ventil am Heizkreis sagt nichts über das Thermostat).

``check`` läuft im Takt (``main.run``) höchstens einmal je ``EVERY_S``; offene Meldungen ohne Befund schließen still.
"""

from __future__ import annotations

import time

from . import floor_limit

DAYS = 7
CLOSED_PCT = 2.0
HOT_K = 15.0
LEAK_KH = 0.3
MIN_H = 2.0
GAP_S = 900       # größere Lücken zwischen zwei Messpunkten zählen nicht
EVERY_S = 3600
KEY = "valve_leak:"


def rates(store, climate: str, since: int) -> dict:
    """Temperaturänderung in K/h bei geschlossenem Ventil – {hot: (K/h, h), cold: (K/h, h)}, None ohne Daten."""
    rows = store.query("SELECT r.ts, r.temp, r.valve, s.heating, s.dhw, s.flow FROM room_samples r "
                       "JOIN samples s ON s.ts = r.ts WHERE r.room = ? AND r.ts >= ? ORDER BY r.ts", (climate, since))
    acc = {"hot": [0.0, 0.0], "cold": [0.0, 0.0]}
    for a, b in zip(rows, rows[1:]):
        dt = b["ts"] - a["ts"]
        if (dt <= 0 or dt > GAP_S or None in (a["temp"], b["temp"], a["valve"], b["valve"])
                or max(a["valve"], b["valve"]) > CLOSED_PCT):
            continue
        hot = bool(a["heating"]) and not a["dhw"] and a["flow"] is not None and a["flow"] >= a["temp"] + HOT_K
        k = acc["hot" if hot else "cold"]
        k[0] += b["temp"] - a["temp"]
        k[1] += dt / 3600
    return {k: (d / h, h) if h > 0 else None for k, (d, h) in acc.items()}


def finding(r: dict) -> str | None:
    """Text des Befunds bzw. None (dicht oder zu wenig gesehen)."""
    hot, cold = r.get("hot"), r.get("cold")
    if not hot or not cold or min(hot[1], cold[1]) < MIN_H or hot[0] - cold[0] < LEAK_KH or hot[0] <= 0:
        return None
    return (f"Ventil schließt wohl nicht ganz – bei 0 % wird der Raum warm, sobald die Heizung läuft "
            f"({hot[0]:+.1f} K/h, ohne Heizung {cold[0]:+.1f} K/h). Ventiladapter prüfen, Thermostat neu kalibrieren, "
            "ggf. Unterlegscheibe")


def check(eng, now: float | None = None) -> dict[str, str]:
    """Alle Räume prüfen (höchstens einmal je ``EVERY_S``), Meldungen öffnen bzw. still schließen. Gibt die Befunde
    {climate: Text} zurück."""
    now = time.time() if now is None else now
    if eng.notifier is None or now - eng.__dict__.get("_leak_ts", 0) < EVERY_S:
        return {}
    eng._leak_ts = now
    floors = {f.climate for f in floor_limit.floor_rooms(eng.cfg, eng.found.rooms)}
    since, found = int(now) - DAYS * 86400, {}
    for room in eng.found.rooms:
        if room.climate in floors:
            continue
        text = finding(rates(eng.store, room.climate, since))
        if text:
            found[KEY + room.climate] = f"{room.name}: {text}"
    for key, text in found.items():
        eng.notifier.problem(key, text, now)
    eng.notifier.settle(KEY, found, now)
    return {k[len(KEY):]: t for k, t in found.items()}
