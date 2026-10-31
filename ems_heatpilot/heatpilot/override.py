"""Override „nur jetzt“ (seit 0.62, Konzeptpapier Abschnitt 7): Nacht, Tag, Anwesend oder Abwesend für das ganze Haus.

Gedacht für Ausnahmen – nachts um 3 Uhr zur Arbeit, spät heimkommen, früh ins Bett, samstags früh raus. **Tag**
(seit 0.71) beendet die Nacht: Räume in Nacht oder Vorheizen bekommen, was tagsüber gälte – Komfort, wenn jemand zu
Hause ist, sonst die Abwesenheitstemperatur. Es gibt nur diese eine Art:
Der Override setzt das Gelernte vorübergehend außer Kraft und endet von selbst (``apply``):

* am **nächsten Schaltpunkt** – dem frühesten geplanten Wechsel der Räume beim Setzen (``until``, aus dem Plan ohne
  Override), höchstens nach ``MAX_H``;
* sobald der Plan **von selbst** dorthin kommt (alle Räume ohne Override schon in dieser Phase);
* wenn das System erkennt, dass die Lage vorbei ist: bei „Abwesend“ kommt jemand heim, bei „Anwesend“ sind alle weg.

Danach gilt wieder der normale Plan. Er wirkt nur bei „HeatPilot steuert“ und nur auf Räume mit „macht mit“ (der
Regelkreis schreibt nur dort); ein Boost geht vor. Die Knöpfe lehren nichts (seit 0.71): Jeder Override steht mit
Beginn und Ende im kv ``LOG`` (``periods``), und die Nachterkennung (``sleep.learn``) zählt eine Nacht mit Override
nicht. Gesetzt über ``heat_actions`` (Aktion ``override``, Seite und Karte), Stand im kv ``control.override`` und als
``sensor.heatpilot_modus`` für die Karte (``sensor``).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)

KV = "control.override"
LOG, LOG_DAYS = "control.override.log", 60  # 0.71: [Beginn, Ende|None, Modus] – Nächte mit Override lernt nichts
MODES = {"nacht": ("nacht", "Nacht"), "tag": ("komfort", "Tag"), "anwesend": ("komfort", "Anwesend"),
         "abwesend": ("abwesend", "Abwesend")}
SAME = {"nacht": ("nacht",), "tag": ("komfort", "vorheizen", "abwesend", "urlaub"), "anwesend": ("komfort", "vorheizen"),
        "abwesend": ("abwesend", "urlaub")}
MAX_H = 24
SENSOR = "sensor.heatpilot_modus"


def get(store) -> dict | None:
    return store.get(KV) or None


def _log(store, ts: int, mode: str | None) -> None:
    """Laufenden Eintrag schließen, bei ``mode`` einen neuen beginnen; älter als ``LOG_DAYS`` fällt weg."""
    log_ = [e for e in store.get(LOG) or [] if (e[1] or ts) >= ts - LOG_DAYS * 86400]
    if log_ and log_[-1][1] is None:
        log_[-1][1] = ts
    if mode:
        log_.append([ts, None, mode])
    store.put(LOG, log_)


def periods(store, now: int | None = None) -> list[tuple[int, int]]:
    """Zeiträume mit Override (Beginn, Ende; ein laufender bis ``now``) – damit die Knöpfe nichts lehren."""
    now = int(now or time.time())
    return [(int(a), int(b if b is not None else now)) for a, b, _ in store.get(LOG) or []]


def set_mode(store, mode: str, ts: float | None = None) -> dict | None:
    """Override setzen (``mode`` aus ``MODES``) oder mit „aus“ beenden. ``until`` und die Zahl der Anwesenden beim
    Setzen (``home``) legt der nächste Takt fest."""
    if mode in ("aus", "", None):
        store.put(KV, None)
        _log(store, int(ts or time.time()), None)
        log.info("Override beendet (von Hand)")
        return None
    if mode not in MODES:
        raise ValueError(f"Override „{mode}“ unbekannt – nacht, tag, anwesend, abwesend oder aus")
    ov = {"mode": mode, "since": int(ts or time.time()), "until": None, "home": None}
    store.put(KV, ov)
    _log(store, ov["since"], mode)
    log.info("Override „nur jetzt“: %s", MODES[mode][1])
    return ov


def _end(store, why: str, ts: int | None = None) -> None:
    store.put(KV, None)
    _log(store, int(ts or time.time()), None)
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
        _end(loop.store, why, ts)
        return first
    phase, name = MODES[mode]
    until, cfg, out = datetime.fromtimestamp(ov["until"]).astimezone(), loop.eng.cfg, {}
    for room in rooms:
        comfort, nat = float(room.comfort), first.get(room.climate)
        if mode == "tag" and nat is not None and nat.phase not in ("nacht", "vorheizen"):
            continue  # Tag: nur die Nacht endet – was tagsüber ohnehin gilt, bleibt
        away = mode == "abwesend" or (mode == "tag" and presence.home_count == 0)
        sp = cfg.night_temp if mode == "nacht" else cfg.away_temp if away else comfort
        out[room.climate] = Desired(_clamp(sp, comfort, cfg), "abwesend" if away else phase, f"nur jetzt: {name}", until)
    return {**first, **out}


def sensor(store) -> tuple[str, str, dict]:
    """``sensor.heatpilot_modus`` für die Karte: Zustand nacht/anwesend/abwesend bzw. „auto“, Ende als Zeitstempel."""
    ov = get(store) or {}
    return SENSOR, ov.get("mode") or "auto", {
        "friendly_name": "HeatPilot Modus", "icon": "mdi:home-clock",
        "until": ov.get("until"), "since": ov.get("since"), "modes": ["auto", *MODES]}
