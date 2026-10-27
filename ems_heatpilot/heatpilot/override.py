"""Override „nur jetzt“ (seit 0.62, Konzeptpapier Abschnitt 7): Nacht, Anwesend oder Abwesend für das ganze Haus.

Gedacht für Ausnahmen – nachts um 3 Uhr zur Arbeit, spät heimkommen, früh ins Bett. Es gibt nur diese eine Art:
Der Override setzt das Gelernte vorübergehend außer Kraft und endet von selbst (``apply``):

* am **nächsten Schaltpunkt** – dem frühesten geplanten Wechsel der Räume beim Setzen (``until``, aus dem Plan ohne
  Override), höchstens nach ``MAX_H``;
* sobald der Plan **von selbst** dorthin kommt (alle Räume ohne Override schon in dieser Phase);
* wenn das System erkennt, dass die Lage vorbei ist: bei „Abwesend“ kommt jemand heim, bei „Anwesend“ sind alle weg.

Danach gilt wieder der normale Plan. Er wirkt nur bei „HeatPilot steuert“ und nur auf Räume mit „macht mit“ (der
Regelkreis schreibt nur dort); ein Boost geht vor. Gelernt wird weiter wie sonst – Overrides sind selten und fallen
kaum ins Gewicht. Gesetzt über ``heat_actions`` (Aktion ``override``, Seite und Karte), Stand im kv
``control.override`` und als ``sensor.heatpilot_modus`` für die Karte (``sensor``).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)

KV = "control.override"
MODES = {"nacht": ("nacht", "Nacht"), "anwesend": ("komfort", "Anwesend"), "abwesend": ("abwesend", "Abwesend")}
SAME = {"nacht": ("nacht",), "anwesend": ("komfort", "vorheizen"), "abwesend": ("abwesend", "urlaub")}
MAX_H = 24
SENSOR = "sensor.heatpilot_modus"


def get(store) -> dict | None:
    return store.get(KV) or None


def set_mode(store, mode: str, ts: float | None = None) -> dict | None:
    """Override setzen (``mode`` aus ``MODES``) oder mit „aus“ beenden. ``until`` und die Zahl der Anwesenden beim
    Setzen (``home``) legt der nächste Takt fest."""
    if mode in ("aus", "", None):
        store.put(KV, None)
        log.info("Override beendet (von Hand)")
        return None
    if mode not in MODES:
        raise ValueError(f"Override „{mode}“ unbekannt – nacht, anwesend, abwesend oder aus")
    ov = {"mode": mode, "since": int(ts or time.time()), "until": None, "home": None}
    store.put(KV, ov)
    log.info("Override „nur jetzt“: %s", MODES[mode][1])
    return ov


def _end(store, why: str) -> None:
    store.put(KV, None)
    log.info("Override beendet: %s", why)


def apply(loop, ts: int, first: dict, presence, rooms) -> dict:
    """Die Wunschzustände ``first`` (ohne Override) für den laufenden Override ersetzen – oder ihn beenden."""
    from .controlloop import Desired, _clamp  # spät: controlloop importiert dieses Modul

    ov = get(loop.store)
    if not ov:
        return first
    mode, natural = ov["mode"], [d for d in first.values()]
    if ov.get("until") is None:  # erster Takt: der nächste Schaltpunkt des normalen Plans
        nxt = [d.until.timestamp() for d in natural if d.until is not None]
        ov["until"] = int(min(nxt)) if nxt else ov["since"] + MAX_H * 3600
        ov["home"] = presence.home_count
        loop.store.put(KV, ov)
    home = presence.home_count
    why = ("Schaltpunkt erreicht" if ts >= min(ov["until"], ov["since"] + MAX_H * 3600)
           else "der Plan ist von selbst so weit" if natural and all(d.phase in SAME[mode] for d in natural)
           else "jemand ist heimgekommen" if mode == "abwesend" and home and home > (ov.get("home") or 0)
           else "alle sind weg" if mode == "anwesend" and home == 0 and (ov.get("home") or 0) > 0 else None)
    if why:
        _end(loop.store, why)
        return first
    phase, name = MODES[mode]
    until, cfg, out = datetime.fromtimestamp(ov["until"]).astimezone(), loop.eng.cfg, {}
    for room in rooms:
        comfort = float(room.comfort)
        sp = {"nacht": cfg.night_temp, "anwesend": comfort, "abwesend": cfg.away_temp}[mode]
        out[room.climate] = Desired(_clamp(sp, comfort, cfg), phase, f"nur jetzt: {name}", until)
    return {**first, **out}


def sensor(store) -> tuple[str, str, dict]:
    """``sensor.heatpilot_modus`` für die Karte: Zustand nacht/anwesend/abwesend bzw. „auto“, Ende als Zeitstempel."""
    ov = get(store) or {}
    return SENSOR, ov.get("mode") or "auto", {
        "friendly_name": "HeatPilot Modus", "icon": "mdi:home-clock",
        "until": ov.get("until"), "since": ov.get("since"), "modes": ["auto", *MODES]}
