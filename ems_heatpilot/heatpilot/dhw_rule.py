"""Regel von Weg W (seit 0.38): Grenzwerte und reine Funktionen für das Warmwasser-Soll – ohne Zustand, ohne Schreiben.

Hier steht, *wann* HeatPilot das Warmwasser-Soll schreiben würde; ``dhw_control`` führt es aus, die Vorschau
(``preview``) rechnet mit denselben Funktionen und Grenzwerten:

* ``wish`` – Wunsch aus der Warmwasser-Empfehlung: auf 1 K gerundet (halbe Grad aufwärts), Minimum…Maximum,
* ``decide`` – nur bei Änderung ab 1 K zum Kessel-Soll (beim gerundeten Wunsch und bei der ungerundeten Empfehlung,
  ``unrounded`` – 52,4 ↔ 52,6 °C schreibt nicht abwechselnd 52 und 53), nicht in der Haltezeit von 5 min nach dem
  letzten Schreiben, nicht während einer Sonderladung, einer Pause oder einer offenen Prüfung.

Die übrigen Grenzwerte (Prüfung nach 60 s, Pause nach 3 Fehlschlägen, 2 fremden Änderungen in 1 h oder 24
Schreibvorgängen in 24 h) und die Texthelfer stehen ebenfalls hier, damit Ablauf und Anzeige dieselben nutzen.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from .values import local

STEP_K = 1.0              # nur bei Änderung ab so viel schreiben
HOLD_S = 300              # Haltezeit nach jedem Schreiben
CHECK_S = 60              # so lange nach dem Schreiben prüfen
CHECK_MAX_S = 300         # Kessel-Soll unbekannt bzw. unklar: so lange nach dem Schreiben weiter prüfen
CHECK_TOL_K = 0.5         # der Kessel muss den Wert so genau zeigen
EMS_FRESH_S = 90          # Werte direkt von EMS-ESP gelten so lange (Abfrage alle 30 s), danach nur die Entität
MAX_FAILS = 3             # Fehlschläge in Folge, dann Pause
FOREIGN_MAX = 2           # fremde Änderungen …
FOREIGN_WINDOW_S = 3600   # … in dieser Zeit, dann Pause
DAY_MAX = 24              # höchstens so viele Schreibvorgänge …
DAY_S = 24 * 3600         # … in dieser Zeit, dann Pause (die Empfehlung schwankt)
HINT_S = 24 * 3600        # Hinweis „HA-Automation abschalten“ bzw. „wieder einschalten“ so lange
HINT_AUTOMATION = "HA-Automation für das Speicher-Soll abschalten – zwei Schreiber gleichzeitig darf es nicht geben"
HINT_FAILSAFE = "Fällt HeatPilot aus, bleibt das zuletzt geschriebene Soll stehen – deshalb nie unter dem Minimum"
UNKNOWN = "Kessel-Soll unbekannt – nichts geschrieben"


# ------------------------------------------------------------------ Texthelfer
def _c(value) -> str:
    """Temperatur für Texte: ganze Grad ohne, sonst eine Nachkommastelle mit Komma."""
    if value is None:
        return "?"
    v = round(float(value), 1)
    return f"{v:.0f}" if v == int(v) else f"{v:.1f}".replace(".", ",")


def _hm(ts: float) -> str:
    """Uhrzeit des Add-ons – nur für Protokoll, Log und ``status_text``; die Oberfläche bekommt ISO-Zeiten."""
    return f"{local(ts):%H:%M}"


def _iso(ts: float | None) -> str | None:
    return None if ts is None else local(ts).isoformat()


def _ago(seconds: float) -> str:
    """Zeitabstand für Texte ohne Uhrzeit (gleich in jeder Zeitzone): „vor 5 min“, „vor 3 h“."""
    m = max(0, int(seconds // 60))
    return "gerade eben" if m < 1 else f"vor {m} min" if m < 90 else f"vor {round(m / 60)} h"


# ------------------------------------------------------------------ Regel
def limits(cfg) -> tuple[int, int]:
    """Ganze Grad zwischen Minimum und Maximum der Einstellungen (Warmwasser)."""
    return math.ceil(cfg.dhw_min_temp - 1e-9), math.floor(cfg.dhw_max_temp + 1e-9)


def wish(target, cfg) -> int | None:
    """Wunsch aus der Empfehlung: auf 1 K gerundet (halbe Grad aufwärts), auf Minimum…Maximum geklemmt."""
    if target is None:
        return None
    lo, hi = limits(cfg)
    if lo > hi:
        return None
    return min(max(math.floor(float(target) + 0.5), lo), hi)


def unrounded(target, cfg) -> float | None:
    """Empfehlung ungerundet, auf Minimum…Maximum geklemmt – für die Schwelle in ``decide``."""
    if target is None:
        return None
    lo, hi = limits(cfg)
    return None if lo > hi else min(max(float(target), lo), hi)


class Decision(NamedTuple):
    write: bool
    value: int | None
    why: str                   # Grund fürs Schreiben bzw. warum nicht
    until: int | None = None   # Ende der Haltezeit (für die Anzeige als Uhrzeit im Browser)


def decide(value: int | None, current: float | None, ts: float, last_ts: float | None = None,
           special: bool = False, paused: bool = False, checking: bool = False,
           exact: float | None = None) -> Decision:
    """Schreiben? Nur bei Änderung ab ``STEP_K`` zum Kessel-Soll ``current`` – beim gerundeten Wunsch ``value`` und,
    wenn bekannt, bei der ungerundeten Empfehlung ``exact`` –, nicht in der Haltezeit nach ``last_ts``, nicht während
    einer Sonderladung, einer Pause oder einer offenen Prüfung."""
    if value is None:
        return Decision(False, None, "keine Empfehlung (Warmwasser-Modell lernt noch)")
    if paused:
        return Decision(False, value, "pausiert")
    if special:
        return Decision(False, value, "Sonderladung bzw. Desinfektion läuft – geschrieben wird danach")
    if current is None:
        return Decision(False, value, UNKNOWN)
    if abs(value - current) < STEP_K - 1e-9 or (exact is not None and abs(exact - current) < STEP_K - 1e-9):
        return Decision(False, value, f"Kessel-Soll {_c(current)} °C passt")
    if checking:
        return Decision(False, value, "Prüfung des letzten Schreibens läuft")
    if last_ts is not None and ts < last_ts + HOLD_S:
        return Decision(False, value, f"Haltezeit bis {_hm(last_ts + HOLD_S)}", until=int(last_ts + HOLD_S))
    return Decision(True, value, f"{_c(current)} → {value} °C")
