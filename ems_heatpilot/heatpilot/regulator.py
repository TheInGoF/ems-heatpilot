"""Regler am Bus (Weg B, seit 0.41): Hängt ein Regler (Gerät ``thermostat``) am EMS-Bus? – nur lesen.

Solange ein Regler am Bus ist, schreibt HeatPilot den Kessel nicht: Er überschreibt alle 60 s, was am Kessel gesetzt
wird. Seit der Prüfung von 0.41 gilt dabei: **Eine fehlende Antwort ist nicht „kein Regler“.**

* ``EmsEsp.read`` merkt sich je Gerät, wie die Abfrage ausging (``emsesp.OK``, ``ABSENT`` – EMS-ESP meldet
  ausdrücklich kein solches Gerät –, ``FAILED`` – Zeitüberlauf, Fehler, leer).
* ``note`` (von ``Engine.set_ems`` bei jeder Antwort von EMS-ESP) führt Buch in ``eng.regulator_watch``: wann der
  Regler zuletzt gesehen wurde (EMS-ESP liefert Werte des Geräts ``thermostat`` oder es gibt frische Regler-Werte in
  Home Assistant) und seit wann EMS-ESP in Folge ausdrücklich „kein Gerät thermostat“ meldet. Eine Lücke zwischen den
  Antworten oder ein Fehler beginnt die Folge neu. Seit 0.46.5 zählt eine gescheiterte Abfrage des Reglers, wenn der
  Kessel in derselben Abfrage antwortet, wie „kein Gerät“ (``quiet``: EMS-ESP erreichbar, der Regler antwortet nicht –
  so meldet EMS-ESP einen abgebauten Regler oft). Der Stand überlebt Neustarts (kv ``regulator.watch``).
* ``regulator`` – vorhanden (sperrt), unbekannt (sperrt) oder nicht vorhanden. „Nicht vorhanden“ gilt nur, wenn
  EMS-ESP gerade antwortet, mindestens ``ABSENT_N`` Abfragen in Folge über mindestens ``ABSENT_S`` ausdrücklich kein
  Gerät ``thermostat`` meldet und der Regler seit ``HOLD_S`` nicht mehr gesehen wurde – ein Neustart von EMS-ESP
  (der Kessel wird oft vor dem Regler erkannt) oder eine einzelne gescheiterte Abfrage öffnet die Sperre nicht.
* ``regulator_block`` – für ``Control`` (letzte Sperre vor dem Aufruf).
"""

from __future__ import annotations

import time

from . import entity_guard as eg
from .boiler_rule import EMS_FRESH_S, REGULATOR_FRESH_S, REGULATOR_TEXT, _ago
from .emsesp import ABSENT, FAILED, OK
from .values import parse_time

KV = "regulator.watch"
ABSENT_N = 3          # so viele Abfragen in Folge „kein Gerät thermostat“ …
ABSENT_S = 300        # … über mindestens so lange
HOLD_S = 1800         # ein gesehener Regler gilt so lange als vorhanden
SAVE_EVERY_S = 300    # „zuletzt gesehen“ höchstens so oft speichern (die Abfrage läuft alle 30 s)


def load(store) -> dict:
    """Gespeicherter Stand (Neustart): zuletzt gesehen, letzte Antwort, Folge „nicht vorhanden“."""
    raw = store.get(KV) if store is not None else None
    return dict(raw) if isinstance(raw, dict) else {}


# Eingaben, die Home Assistant selbst schreibt (Automation, EMS-ESP-Fernfühler) – kein Lebenszeichen des Reglers
# (0.46.4: number.…_remotetemp hielt einen abgebauten Regler „am Bus“)
WRITABLE = ("number.", "select.", "switch.", "button.", "input_number.", "input_select.", "input_boolean.")
INPUT_ROLES = ("hc_remotetemp",)


def _fresh_ha(eng, now: float) -> str | None:
    """Eine Entität des Reglers (Heizkreis) in Home Assistant, deren Wert in den letzten ``REGULATOR_FRESH_S`` kam –
    nur, was der Regler selbst meldet (Sensoren), keine beschreibbaren Eingaben."""
    for role, eid in sorted((getattr(eng.found, "thermostat", None) or {}).items()):
        st = eng.states.get(eid) or {}
        if (not role.startswith("hc_") or role in INPUT_ROLES or eid.startswith(WRITABLE)
                or st.get("state") in eg.OFF_STATES):
            continue
        seen = parse_time(st.get("last_reported") or st.get("last_updated"))
        if seen is not None and now - seen.timestamp() <= REGULATOR_FRESH_S:
            return eid
    return None


def note(eng, status: dict | None, ts: float) -> None:
    """Antwort von EMS-ESP verbuchen (``Engine.set_ems``). ``status``: Ausgang je Gerät aus ``EmsEsp.status``; fehlt
    er (ältere Aufrufer), zählt nur „Werte des Reglers da“ – sonst ist es unbekannt, nie „nicht vorhanden“."""
    w = eng.regulator_watch
    old = dict(w)
    got = (status or {}).get("thermostat")
    if got is None:
        got = OK if (eng.ems or {}).get("thermostat") else FAILED
    quiet = got == FAILED and (status or {}).get("boiler") == OK  # 0.46.5: Kessel antwortet, nur der Regler nicht
    if quiet:
        got = ABSENT
    w["quiet"] = quiet
    if got == OK or _fresh_ha(eng, ts):
        w["seen"] = ts
    if got == ABSENT:
        gap = w.get("ts") is None or ts - w["ts"] > EMS_FRESH_S or w.get("last") != ABSENT
        w["absent_since"], w["absent_n"] = (ts, 1) if gap else (w.get("absent_since", ts), w.get("absent_n", 0) + 1)
    else:
        w.pop("absent_since", None)
        w.pop("absent_n", None)
    w["last"], w["ts"] = got, ts
    if (old.get("last") != got or old.get("quiet") != quiet
            or (w.get("seen") or 0) - (old.get("seen") or 0) >= SAVE_EVERY_S):
        eng.store.put(KV, w)


def _unknown(text: str) -> dict:
    return {"present": None, "source": None, "text": text}


def regulator(eng, now: float | None = None) -> dict:
    """Ist ein Regler am Bus? {present: True | False | None (unbekannt), text, source}. Solange er da ist (oder es
    unbekannt ist), schreibt HeatPilot nicht – der Regler überschreibt alle 60 s, was am Kessel gesetzt wird. ``hold``
    (seit 0.42): vorhanden nur, weil er vor weniger als ``HOLD_S`` gesehen wurde – für die Desinfektion kein Nachweis,
    dass er sie macht (``disinfect_rule.regulator_does_it``)."""
    now = time.time() if now is None else now
    ts = getattr(eng, "ems_ts", None)
    fresh = ts is not None and now - ts <= EMS_FRESH_S
    if fresh and (eng.ems or {}).get("thermostat"):
        return {"present": True, "source": "ems",
                "text": f"EMS-ESP meldet einen Regler (Gerät thermostat) – {REGULATOR_TEXT}"}
    if getattr(eng.cfg, "regulator_removed", False):  # 0.46.9: Nutzer sagt „Regler abgebaut“ – kein Warten
        return {"present": False, "source": "user", "text": "Kein Regler am Bus (von dir bestätigt: Regler abgebaut)"}
    eid = _fresh_ha(eng, now)
    if eid:
        return {"present": True, "source": "ha",
                "text": f"Frische Regler-Werte in Home Assistant ({eid}) – {REGULATOR_TEXT}"}
    if not eng.cfg.emsesp_host:
        return _unknown("Ob ein Regler am Bus ist, lässt sich ohne EMS-ESP nicht prüfen – EMS-ESP-Adresse eintragen "
                        "(Einstellungen → Grundlagen); bis dahin schreibt HeatPilot nicht")
    if not fresh:
        return _unknown("EMS-ESP antwortet gerade nicht – ob ein Regler am Bus ist, ist unbekannt; HeatPilot schreibt "
                        "erst wieder, wenn EMS-ESP antwortet")
    w = getattr(eng, "regulator_watch", None) or {}
    seen = w.get("seen")
    if seen is not None and now - seen < HOLD_S:  # nur noch gehalten: kein frischer Nachweis (``hold``)
        return {"present": True, "source": "ems", "hold": True,
                "text": f"Regler zuletzt {_ago(now - seen)} gemeldet – HeatPilot schreibt erst, wenn er "
                        f"{HOLD_S // 60} min lang nicht mehr gemeldet wird. {REGULATOR_TEXT}"}
    if w.get("last") != ABSENT:
        return _unknown("EMS-ESP beantwortet die Abfrage des Reglers (Gerät thermostat) gerade nicht – ob ein Regler am "
                        "Bus ist, ist unbekannt; HeatPilot schreibt nicht")
    n, since = int(w.get("absent_n") or 0), w.get("absent_since")
    what = ("der Regler (Gerät thermostat) antwortet EMS-ESP nicht, der Kessel schon" if w.get("quiet")
            else "EMS-ESP meldet kein Gerät thermostat")
    if n < ABSENT_N or since is None or now - since < ABSENT_S:
        return _unknown(f"{what[0].upper()}{what[1:]} – HeatPilot prüft noch ({min(n, ABSENT_N)} von {ABSENT_N} "
                        f"Abfragen, mindestens {ABSENT_S // 60} min), bevor es schreibt")
    return {"present": False, "source": "ems", "text": f"Kein Regler am Bus ({what})"}


def regulator_block(eng, now: float | None = None) -> str | None:
    """Für ``Control`` (letzte Sperre vor dem Aufruf): der Grund, solange ein Regler am Bus bzw. es unbekannt ist."""
    r = regulator(eng, now)
    return None if r["present"] is False else r["text"]
