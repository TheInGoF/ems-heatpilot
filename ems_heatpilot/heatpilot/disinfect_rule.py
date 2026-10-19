"""Desinfektion auslösen (seit 0.42): Regeln und Grenzwerte – wann HeatPilot den Schalter „Desinfizieren“ schaltet.

Ohne Regler am Bus hat der Kessel für die Legionellenschaltung keinen Timer, nur den Schalter ``wwdisinfecting``.
HeatPilot schaltet ihn zum eingestellten Termin (seit 0.36.3 in den Einstellungen) – aber nur, wenn alles zutrifft
(``lock``): ein Termin ist eingestellt, das Warmwasser-Soll steht auf „HeatPilot setzt“ (Weg W), **kein Regler am Bus**
(dieselbe Erkennung wie Weg B, ``regulator`` – mit Regler macht er die Desinfektion) und Weg W ist nicht pausiert.
Dazu muss der Schalter taugen (``disinfect_entity``) und EMS-ESP ihn schalten lassen (``blocker``). Im Trockenlauf von
Weg W steht zum Termin nur „würde Desinfektion starten“ im Protokoll.

Termine (Ortszeit, auch über die Zeitumstellung – ``Config.disinfections``):

* ``due`` – der letzte Termin, dessen Beginn höchstens ``CATCHUP_S`` (12 h) zurückliegt: fällig. Lief HeatPilot zum
  Termin nicht (oder war es gesperrt), wird er in dieser Zeit nachgeholt.
* ``missed`` – der letzte Termin davor; ist er nicht erledigt, war er verpasst (Meldung, dann gilt der nächste).
* Termine vor ``armed`` zählen nicht (Termin oder Schalter eben geändert, erster Start mit 0.42) – sonst löste HeatPilot
  gleich nach dem Eintragen einen vergangenen Termin aus.
* ``key`` – Kennung eines Termins (Beginn als ISO-Zeit): ein Termin wird höchstens einmal ausgelöst.
* ``too_soon`` – höchstens eine Desinfektion je ``GAP_S`` (24 h): Wird der Termin während oder kurz nach einem Ablauf
  auf später am selben Tag gelegt, gilt der neue Termin erst in der nächsten Woche (außer der Ablauf schlug fehl).

Ablauf (``disinfect_control``): einschalten; nach ``CHECK_S`` prüfen (Schalter an bzw. EMS-ESP meldet die
Desinfektion; unbekannt: bis ``CHECK_MAX_S`` weiter prüfen); beobachten, bis der Speicher die Desinfektionstemperatur
− ``REACH_K`` erreicht (Erfolg, nur nachdem der Kessel sie bestätigt hat) – spätestens Ladedauer laut Modell +
``EXTRA_S``, sonst Fehlschlag; danach den Schalter ausschalten, wenn er noch an ist (einmal, mit Prüfung) – nach einem
Erfolg erst nach dem Nachlauf ``AFTER_S``: Meist setzt der Kessel ihn selbst zurück, sobald der Speicher die volle
Temperatur hat. Sperrt dabei etwas nur kurz (``lock``, Schalter unbekannt, Fehler beim Aufruf), versucht HeatPilot es
bis ``OFF_WAIT_S`` lang weiter, dann kommt eine Meldung. Reine Funktionen, schreibt nichts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import disinfect_entity as de
from .config import WEEKDAYS
from .dhw_watch import SPECIAL_COVER_H
from .regulator import regulator
from .values import local

CHECK_S = 60              # so lange nach dem Einschalten (bzw. Ausschalten) prüfen
CHECK_MAX_S = 300         # Zustand unbekannt: so lange weiter prüfen, dann Fehlschlag
REACH_K = 3.0             # Erfolg: Speicher mindestens Desinfektionstemperatur − so viel
EXTRA_S = 3600            # spätestens Ladedauer laut Modell + so lange, sonst Fehlschlag
AFTER_S = 900             # Nachlauf: so lange darf der Kessel nach dem Erfolg den Schalter selbst zurücksetzen
OFF_WAIT_S = 1800         # Ausschalten kurz gesperrt bzw. Schalter unbekannt: so lange weiter versuchen, dann Meldung
CATCHUP_S = 12 * 3600     # verpasster Termin (HeatPilot aus, gesperrt): so lange nach dem Beginn nachholen
GAP_S = 24 * 3600         # höchstens eine Desinfektion in so langer Zeit (Termin während eines Ablaufs verlegt)
KEEP = 8                  # so viele erledigte Termine merken
LOOKBACK_DAYS = 8         # verpasste Termine so weit zurück suchen (einer je Woche)


def planned(cfg) -> bool:
    return cfg.disinfect_weekday() is not None


def mode(cfg) -> str:
    """„aus“ (kein Termin bzw. Warmwasser-Soll aus), „trocken“ oder „heatpilot“ – folgt dem Schalter von Weg W."""
    return cfg.dhw_control if planned(cfg) and cfg.dhw_control in ("trocken", "heatpilot") else "aus"


def writes(cfg) -> bool:
    """Bekommt ``Control`` den Schalter der Desinfektion? Nur mit Termin und „HeatPilot setzt“ (``main``)."""
    return mode(cfg) == "heatpilot"


def label(dt: datetime) -> str:
    """„So 17:30“ – der Termin, wie er eingestellt ist (Wanduhr)."""
    return f"{WEEKDAYS[dt.weekday()]} {dt:%H:%M}"


def key(dt: datetime) -> str:
    """Kennung eines Termins: Beginn als ISO-Zeit in Minuten (mit Zeitzone, auch über die Umstellung eindeutig)."""
    return dt.isoformat(timespec="minutes")


def arm_key(cfg) -> str:
    """Ändert sich das (Termin, Uhrzeit, Schalter), beginnt die Zählung neu (``armed``)."""
    return f"{cfg.dhw_disinfect_day} {cfg.dhw_disinfect_time:%H:%M} {mode(cfg)}"


def due(cfg, now_ts: float, armed_ts: float, done: list[str]) -> datetime | None:
    """Fälliger Termin: Beginn höchstens ``CATCHUP_S`` her, nicht vor ``armed_ts``, noch nicht erledigt."""
    hits = cfg.disinfections(local(now_ts - CATCHUP_S), local(now_ts))
    last = hits[-1] if hits else None
    if last is None or last.timestamp() < armed_ts or key(last) in done:
        return None
    return last


def missed(cfg, now_ts: float, armed_ts: float, done: list[str]) -> datetime | None:
    """Letzter Termin vor dem Fenster von ``due``, der nicht erledigt ist (und nicht vor ``armed_ts`` lag)."""
    hits = cfg.disinfections(local(now_ts - LOOKBACK_DAYS * 86400), local(now_ts - CATCHUP_S - 1))
    last = hits[-1] if hits else None
    if last is None or last.timestamp() < armed_ts or key(last) in done:
        return None
    return last


def too_soon(start: datetime, on: dict | None, last: dict | None) -> bool:
    """Liegt der Termin ``start`` weniger als ``GAP_S`` neben dem letzten Einschalten (``on``: {ts, due})? Dann lief die
    Desinfektion eben schon (Termin während bzw. kurz nach dem Ablauf verlegt). Schlug dieser Ablauf fehl (``last``
    zum selben Termin mit ok False), gilt der neue Termin – das ist dann ein neuer Versuch."""
    if not on or on.get("ts") is None or abs(start.timestamp() - float(on["ts"])) >= GAP_S:
        return False
    return not (last and last.get("due") == on.get("due") and last.get("ok") is False)


def failed_recently(last: dict | None, now_ts: float, ready_h: float) -> bool:
    """Ist der letzte Termin gescheitert (nicht gelaufen, verpasst) und rechnete das Warmwasser-Modell noch mit seiner
    Wärme (bis Ladedauer + ``SPECIAL_COVER_H`` + 1 h nach dem Beginn, wie ``dhw_disinfect.planned_for``)?"""
    if not last or last.get("ok") is not False or not last.get("due"):
        return False
    try:
        start = datetime.fromisoformat(last["due"]).timestamp()
    except (TypeError, ValueError):
        return False
    return now_ts < start + (ready_h + SPECIAL_COVER_H + 1) * 3600


def next_after(cfg, now_ts: float) -> datetime | None:
    """Nächster Termin nach ``now_ts`` (für „nächster Termin …“)."""
    hits = cfg.disinfections(local(now_ts + 1), local(now_ts) + timedelta(days=LOOKBACK_DAYS))
    return hits[0] if hits else None


# ------------------------------------------------------------------ darf HeatPilot schalten?
def lock(eng) -> str | None:
    """Warum HeatPilot die Desinfektion jetzt nicht schalten darf (Termin, Warmwasser-Soll „HeatPilot setzt“, Regler am
    Bus bzw. unbekannt, Pause von Weg W) – None = darf. Auch ``Control`` fragt das vor jedem Aufruf."""
    cfg = eng.cfg
    if not planned(cfg):
        return "kein Desinfektionstermin eingestellt"
    if cfg.dhw_control != "heatpilot":
        return "Warmwasser-Soll steht nicht auf „HeatPilot setzt“"
    r = regulator(eng)
    if r["present"] is not False:
        return r["text"]
    ctl = getattr(eng, "dhw_control", None)
    if ctl is not None and ctl.paused():
        return "Warmwasser über HeatPilot ist pausiert – erst „wieder einschalten“"
    return None


def blocker(eng, entity: str | None) -> str | None:
    """``lock`` und dazu: Schalter taugt (``disinfect_entity.problem``), EMS-ESP lässt ihn schalten."""
    return (lock(eng) or de.problem(entity, eng.states.get(entity) if entity else None, eng.found)
            or de.ems_blocked(eng))


def triggers(eng, entity: str | None) -> bool:
    """Löst HeatPilot den Termin aus (Weg W „HeatPilot setzt“, nichts sperrt)? Dann zählt er im Warmwasser-Modell ohne
    Bestätigung durch einen Regler (``dhw_disinfect.plan_counts``, Planungspapier)."""
    return blocker(eng, entity) is None


def regulator_does_it(eng) -> bool:
    """Ein Regler am Bus macht die Desinfektion selbst (wie bis 0.41) – HeatPilot schaltet dann nichts. Nur mit frischem
    Nachweis (EMS-ESP meldet das Gerät thermostat gerade bzw. frische Regler-Werte in Home Assistant): Gilt er nur noch
    ``regulator.HOLD_S`` lang als vorhanden (eben abgebaut?), ist es unbekannt – der Termin wartet, wird nachgeholt."""
    r = regulator(eng)
    return r["present"] is True and not r.get("hold")


def deadline(eng, start_ts: float) -> int:
    """Spätestens dann muss der Speicher heiß sein: Ladedauer laut Modell (``Engine.disinfect_ready_h``) + 60 min."""
    return int(start_ts + eng.disinfect_ready_h() * 3600 + EXTRA_S)


def hot(run: dict) -> bool:
    """Lag der Speicher schon beim Einschalten (``from``) bei bzw. über der Erfolgsschwelle (``goal``)?"""
    return run.get("from") is not None and run.get("goal") is not None and run["from"] >= run["goal"]


def need(run: dict) -> float:
    """Erfolgsschwelle eines Ablaufs: Desinfektionstemperatur − ``REACH_K``; lag der Speicher schon beim Einschalten
    darüber (``hot``), die volle Desinfektionstemperatur – sonst gälte sie gleich nach der Bestätigung als gelungen."""
    return run["temp"] if hot(run) and run.get("temp") is not None else run["goal"]


def reach(eng) -> tuple[float, float]:
    """(Desinfektionstemperatur, Erfolgsschwelle = Temperatur − ``REACH_K``)."""
    temp = float(eng.disinfect_temp()[0])
    return temp, temp - REACH_K
