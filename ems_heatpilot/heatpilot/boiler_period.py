"""Antipendelzeit führen (seit 0.48): Regel, Entität und die Schreibprüfung für ``Control`` – ohne Zustand.

Die Antipendelzeit (Taktsperre, EMS-ESP ``burnminperiod``, in Home Assistant z. B. ``number.boiler_burner_min_period``,
0–120 min) hält den Brenner nach dem Abschalten so lange aus. Fest auf 25 min blockiert sie Starts auch dann, wenn
gerade genug Wärme abgenommen wird; zu kurz lässt sie den Kessel takten. Einstellung „Antipendelzeit anpassen“
(``boiler_period``, Standard aus): geschrieben wird nur bei „Kessel über HeatPilot“ = „heatpilot“, bei „Beobachten“
steht nur im Protokoll, was HeatPilot schreiben würde (Ablauf in ``period_control``).

Regel ``want`` – aus dem Ziel des Schritts (``boiler_flow.Target``):

* **am kürzesten** ``WAIT_MIN`` = 5 min, wenn Räume auf Wärme warten (``Target.waiting``) und der Kessel nicht mehr
  als ``WAIT_MAX_STARTS_H``-mal je Stunde startet – seit 0.74.2 nur, wenn die Abnahme reicht oder das Soll eines
  wartenden Raums eben angehoben wurde (``quick``: heimkommen, hochdrehen, Nachtende; ``raised``, ``RAISE_S`` lang).
  Dann auch ohne Haltezeit (``decide``, ``urgent``). Sonst lief der Kessel morgens bei einem einzigen offenen Raum alle
  5 min für 2 min an (02.11.: 5 Starts in 30 min),
* **kurz** ``SHORT_MIN`` = 10 min, wenn die Abnahme beim Ziel-Vorlauf reicht (die Räume mit offenem Ventil nehmen die
  Mindestleistung des Kessels ab dem Vorlauf ``Target.load`` ab, ``boiler_flow._load``) und der Kessel nicht taktet:
  Dann kann der Brenner auf kleinster Stufe durchlaufen; geht er doch einmal aus, soll ein neuer Start nicht 25 min
  warten. 10 min lassen auch im ungünstigsten Fall höchstens 6 Starts je Stunde zu – und taktet er trotzdem
  (``valve_dry.cycling``: ab 3 Starts in 1 h mit Läufen unter 10 min), gilt sofort wieder lang,
* **lang** = der **Grundwert**: der Wert am Kessel beim Einschalten der Funktion, mindestens ``LONG_MIN`` = 25 min (der
  bisher feste Wert), wenn die Abnahme nicht reicht oder unbekannt ist (Mindestleistung, Hausmodell) oder der Kessel
  taktet – dann sind lange Pausen gewollt; **noch länger** (seit 0.74.2) ``EXTRA_MIN`` = 45 min, wenn die Läufe der
  letzten Stunde im Mittel unter ``SHORT_RUN_MIN`` blieben (die Räume nehmen kaum Wärme ab – 02.11.: 0,6 kW bei
  5,8 kW kleinster Leistung) und kein Raum am Soll hängt (``Target.stalled``),
* **unverändert** ohne Wärmebedarf („nur Pumpe“, Heizbetrieb aus), ohne rechenbares Vorlauf-Soll und während einer
  Warmwasser-Ladung (der Kessel gehört dann dem Speicher).

Alle Werte in ganzen Minuten, hart ``LO_MIN``…``HI_MIN`` (5–60). ``decide``: nur bei Änderung ab ``STEP_MIN`` = 5 min
(kleinere Unterschiede ändern am Takten nichts), höchstens ein Schreiben je ``HOLD_S`` = 15 min (kein Pendeln; der
Kessel braucht die Sperre ohnehin erst beim nächsten Abschalten), nicht während einer Prüfung oder Pause. Die Konstanten
sind hier gesammelt und änderbar.

Entität (``target``/``problem``): Einstellung ``period_set_entity`` (leer = erkannt, Rolle „Antipendelzeit“), nur
``number``, verfügbar, Einheit Minuten, keine andere Rolle. ``PeriodWrite`` ist der Teil von ``Control``: nur diese eine
Entität, nur ``number.set_value`` mit ``value`` als ganzer Zahl in den Grenzen, gesperrt wie das Vorlauf-Soll.
"""

from __future__ import annotations

import re

from . import entity_guard as eg
from .boiler_rule import Decision, _hm
from .values import num

SHORT_MIN = 10           # Abnahme reicht: kurz (höchstens 6 Starts je Stunde, auch wenn der Brenner gleich ausgeht)
LONG_MIN = 25            # Grundwert mindestens so viel – der bisher feste Wert
WAIT_MAX_STARTS_H = 6    # 0.48.2: warten Räume auf Wärme, kurz – außer bei so vielen Starts je Stunde
WAIT_MIN = 5             # 0.57.1: … und zwar so kurz (der Kessel zählt ab dem Schreiben neu – Fall 26.10.: 10 min Warten)
EXTRA_MIN = 45           # 0.74.2: Läufe nur kurz (die Räume nehmen kaum ab) – noch längere Sperre, halb so viele Starts
SHORT_RUN_MIN = 5        # … unter so vielen Minuten je Lauf im Mittel der letzten Stunde
RAISE_K, RAISE_S = 0.5, 1800  # 0.74.2: Soll so weit angehoben (heimkommen, hochdrehen) – so lange schnell reagieren
LO_MIN, HI_MIN = 5, 60   # hart: HeatPilot schreibt nie außerhalb (Control weist ab)
STEP_MIN = 5             # nur bei Änderung ab so viel Minuten
HOLD_S = 900             # höchstens ein Schreiben je so viele Sekunden
ROLE, WHAT, LABEL = "min_period", "die Antipendelzeit", "Antipendelzeit"
UNITS = ("min", "minutes", "minute", "minuten")
NAME_HINT = re.compile(r"period|pendel|takt", re.IGNORECASE)
NOT_PERIOD = eg.other_names(ROLE)

# Protokoll (control_log): Schreiben (Control), Beobachten, Fehlschlag, fremde Änderung, Pause, Grundwert zurück
ACT_SET, ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_BACK = (
    "boiler_period", "boiler_period_dry", "boiler_period_failed", "boiler_period_foreign", "boiler_period_paused",
    "boiler_period_back")
ACTIONS = (ACT_SET, ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_BACK)


def minutes(value) -> int | None:
    """Antipendelzeit als ganze Minuten (plausibel 0–240) – None, wenn unbekannt."""
    v = num(value)
    return round(v) if v is not None and 0 <= v <= 240 else None


def base_of(seen: int | None) -> int:
    """Grundwert aus dem Wert am Kessel beim Einschalten: mindestens ``LONG_MIN``, höchstens ``HI_MIN``."""
    return min(max(seen if seen is not None else LONG_MIN, LONG_MIN), HI_MIN)


# ------------------------------------------------------------------ Regel
def raised(seen: dict, targets: dict, since: dict, ts: int) -> dict:
    """Räume, deren Soll in den letzten ``RAISE_S`` um mindestens ``RAISE_K`` stieg: {Name: Zeitpunkt}. ``seen`` (Soll
    je Raum beim letzten Schritt) wird auf ``targets`` nachgeführt; ein Raum ohne Soll („aus“) zählt nicht."""
    for name, sp in targets.items():
        if sp is None:
            continue
        if seen.get(name) is not None and sp >= seen[name] + RAISE_K:
            since[name] = ts
        seen[name] = sp
    return {n: t for n, t in since.items() if ts - t < RAISE_S}


def quick(target, raised_now: dict) -> list[str]:
    """Wartende Räume mit eben angehobenem Soll – dort soll der Kessel schnell anspringen (0.74.2)."""
    return [n for n in (getattr(target, "waiting", None) or []) if n in raised_now]


def want(target, base: int, fast: list | tuple = ()) -> tuple[int | None, str]:
    """Antipendelzeit jetzt: (Minuten, Grund) – (None, Grund) = nichts ändern (siehe Modulbeschreibung). ``fast``:
    wartende Räume mit eben angehobenem Soll (``quick``)."""
    if target is None:
        return None, "noch nicht gerechnet"
    if target.charging:
        return None, "Warmwasser lädt – Antipendelzeit bleibt"
    if target.heating is None:
        return None, "kein Vorlauf-Soll rechenbar – Antipendelzeit bleibt"
    if not target.heating or getattr(target, "idle", False):
        return None, "kein Wärmebedarf – Antipendelzeit bleibt"
    cyc = target.cycling or {}
    waiting = list(getattr(target, "waiting", None) or [])
    load_ok = target.load is not None and target.flow is not None and target.flow >= target.load
    if (fast or (waiting and load_ok)) and (cyc.get("starts_h") or 0) < WAIT_MAX_STARTS_H:  # Wärme geht vor – 0.74.2:
        if fast:                                               # nur, wenn sie abgenommen wird oder ein Soll stieg
            return WAIT_MIN, f"{', '.join(fast)}: Soll angehoben – kürzeste Sperre"
        return WAIT_MIN, f"{', '.join(waiting)} warten auf Wärme – kürzeste Sperre"
    text = (target.load_text or "").replace("Abnahme: ", "")
    if load_ok and not cyc.get("cycling"):
        return SHORT_MIN, f"Abnahme reicht: {text}"
    run = cyc.get("run_min")
    if (cyc.get("starts_h") or 0) >= 1 and run is not None and run < SHORT_RUN_MIN and not getattr(target, "stalled", None):
        mins = f"{run:.1f}".replace(".", ",")
        return max(base, EXTRA_MIN), f"Läufe im Mittel nur {mins} min – die Räume nehmen kaum Wärme ab: längere Sperre"
    if cyc.get("cycling"):
        return base, f"Kessel taktet ({cyc.get('starts_h', 0)} Starts in 1 h) – lange Sperre"
    return base, f"Abnahme reicht nicht – {text}" if text else "Abnahme reicht nicht"


def decide(value: int | None, current: int | None, ts: float, last_ts: float | None = None,
           paused: bool = False, checking: bool = False, urgent: bool = False) -> Decision:
    """Schreiben? Nur bei Änderung ab ``STEP_MIN`` zum Wert am Kessel ``current``, höchstens einmal je ``HOLD_S``
    nach ``last_ts`` (``urgent`` – ein Soll stieg eben – darf kürzer werden, ohne zu warten), nicht während einer Pause
    oder einer offenen Prüfung."""
    if value is None:
        return Decision(False, None, "nichts ändern")
    if paused:
        return Decision(False, value, "pausiert")
    if current is None:
        return Decision(False, value, "Antipendelzeit am Kessel unbekannt – nichts geschrieben")
    if abs(value - current) < STEP_MIN:
        return Decision(False, value, f"Kessel hat {current} min – Änderung unter {STEP_MIN} min")
    if checking:
        return Decision(False, value, "wartet auf die Prüfung des letzten Schreibens")
    if last_ts is not None and ts - last_ts < HOLD_S and not (urgent and value < current):
        until = int(last_ts + HOLD_S)
        return Decision(False, value, f"Haltezeit {HOLD_S // 60} min bis {_hm(until)}", until)
    return Decision(True, value, f"Antipendelzeit {current} → {value} min")


# ------------------------------------------------------------------ Entität
def suggest(found, states: dict) -> str | None:
    return eg.suggest(found, states, ROLE, "number")


def target(cfg, found, states: dict) -> tuple[str | None, str | None]:
    """(Entität, Herkunft): eingestellt (``period_set_entity``), sonst erkannt; nur ``number``."""
    chosen = getattr(cfg, "period_set_entity", None)
    if chosen:
        return (chosen, "eingestellt") if chosen.startswith("number.") else (None, None)
    hit = suggest(found, states)
    return (hit, "erkannt") if hit else (None, None)


def wrong(entity: str, found=None) -> str | None:
    return eg.wrong_role(entity, found, ROLE, NOT_PERIOD, WHAT)


def problem(entity: str | None, state: dict | None, found=None) -> str | None:
    """Warum die Entität nicht als Antipendelzeit taugt – None = taugt: number, keine andere Rolle, verfügbar, min."""
    if not entity:
        return "keine Entität für die Antipendelzeit gefunden – in den Einstellungen wählen"
    if not entity.startswith("number."):
        return f"{entity} ist keine number-Entität"
    p = wrong(entity, found) or eg.state_problem(entity, state)
    unit = str(((state or {}).get("attributes") or {}).get("unit_of_measurement") or "").strip()
    return p or (None if unit.lower() in UNITS else f"{entity} ist keine Zeit in Minuten (Einheit {unit or 'fehlt'})")


def picker(entity: str, state: dict | None, found=None) -> dict:
    """Auswahl in der Oberfläche (``api/entities`` → ``period``): ``fits`` = number in Minuten, keine andere Rolle."""
    unit = str(((state or {}).get("attributes") or {}).get("unit_of_measurement") or "").strip().lower()
    fits = entity.startswith("number.") and unit in UNITS and wrong(entity, found) is None
    return {"fits": fits, "hint": eg.name_hint(entity, state, NAME_HINT)}


def entity_for(boiler: dict | None, dhw: str | None) -> str | None:
    """Die Entität, die ``Control`` beschreiben darf: nur ``number``, nie das Vorlauf- oder Warmwasser-Soll."""
    b = boiler or {}
    e = b.get("period")
    return e if str(e or "").startswith("number.") and e not in (b.get("flow"), dhw) else None


class PeriodWrite:
    """Teil von ``Control`` (0.48): ``number.set_value`` auf die eine Entität der Antipendelzeit – sonst nichts."""

    period_entity: str | None = None

    def _check_period(self, entity_id: str, data: dict) -> None:
        """Nur ``value``, ganze Minuten ``LO_MIN``…``HI_MIN`` und in den Grenzen der Entität; sie muss taugen
        (``problem``); gesperrt wie das Vorlauf-Soll (Regler am Bus, ``_boiler_guard``)."""
        value, state = data.get("value"), self._state(entity_id)
        whole = isinstance(value, int) and not isinstance(value, bool)
        if set(data) != {"value"} or not whole or not LO_MIN <= value <= HI_MIN:
            raise PermissionError(f"Antipendelzeit {value!r} gesperrt – nur ganze Minuten von {LO_MIN} bis {HI_MIN}")
        if p := problem(entity_id, state):
            raise PermissionError(f"{p} – gesperrt")
        attrs = state.get("attributes") or {}
        lo, hi = num(attrs.get("min")), num(attrs.get("max"))
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise PermissionError(f"Antipendelzeit {value} min außerhalb der Grenzen von {entity_id}")
        self._boiler_guard(entity_id, LABEL)

    async def set_period(self, value: int, reason: str) -> None:
        """Antipendelzeit schreiben (0.48) – Prüfung in ``_check_period``."""
        if not self.period_entity:
            raise PermissionError("Antipendelzeit: Schreiben gesperrt (nicht „Antipendelzeit anpassen“ bei „Kessel "
                                  "über HeatPilot“ oder keine Entität)")
        await self._call("number", "set_value", self.period_entity, {"value": value}, reason, action=ACT_SET)
