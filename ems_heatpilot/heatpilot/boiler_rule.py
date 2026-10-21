"""Regel von Weg B (seit 0.41): Grenzwerte und reine Funktionen für Vorlauf-Soll und Heizbetrieb – ohne Zustand.

Hier steht, *wann* HeatPilot am Kessel schreiben würde; ``boiler_control`` führt es aus, ``boiler_flow`` rechnet den
Wunsch. Die Regeln (Planungspapier, „Regeln für den Vorlauf“, festgelegt von Ingo):

* ``decide_flow`` – Vorlauf-Soll nur bei Änderung ab 1 K zum Kessel-Soll, höchstens ein Schreiben je 60 s (der
  Kessel braucht die Prüfung), nicht während einer Warmwasser-Ladung, einer Pause oder einer offenen Prüfung.
  **Anheben sofort** (wer Wärme braucht, bekommt sie sofort), **senken erst, wenn das Ziel 10 min lang niedriger
  bleibt** (kein Pendeln an einer Schwelle). **Schutz:** Liegt das Vorlauf-Soll am Kessel über der Grenze (Fußboden,
  höchster Vorlauf, Entität), wird sofort gesenkt – ohne 60-s-Abstand und ohne auf eine offene Prüfung zu warten,
* ``decide_heating`` – Heizbetrieb an/aus mit 5 min Haltezeit nach jedem Schalten (nicht hin und her); aus erst nach
  15 min ohne Bedarf; an nur, wenn das Vorlauf-Soll am Kessel bekannt ist und höchstens bei der Grenze liegt (bzw. im
  selben Schritt geschrieben wird); aus sofort als Schutz (Vorlauf über der Grenze, Fußboden-Grenze unter 35 °C),
* ``boost_step`` – Takt-Boost: in 2-K-Schritten bis +10 K (der erste sofort, dann je 10 min), zurück in 2-K-Schritten,
  sobald der Raum warm ist (der erste Schritt zurück sofort) bzw. 10 min, nachdem kein Raum mehr wartet oder das
  Takten aufgehört hat (kein Pendeln an der Schwelle),
* ``load_flow`` – Mindest-Vorlauf aus der Abnahme: kleinster Vorlauf, bei dem die anfordernden Räume die
  Mindestleistung des Kessels abnehmen; reicht auch der höchste nicht, gilt nichts (lange Pausen, keine Sonderlogik),
* ``flow_limits``/``fallback_value`` – 35 °C bis ``max_flow_temp`` bzw. Fußboden-Grenze und die Grenzen der Entität,
  ganze Grad; der Rückfallwert beim Zurückschalten, wenn kein Vorlauf-Soll rechenbar ist, und für den Wächter,
* ``idle_flow`` (seit 0.46.5) – braucht kein Raum Wärme, bleibt der Heizbetrieb an und das Vorlauf-Soll geht auf
  ``PUMP_FLOW`` (der Brenner springt nicht an, die Heizungspumpe läuft weiter); Einstellung „Bei keinem Wärmebedarf“
  (``boiler_idle``): „Pumpe läuft weiter“ (Standard) bzw. „Heizbetrieb ganz aus“ (wie bis 0.46.4, z. B. im Sommer).

Dazu die Einstellungen von Weg B (``FIELDS``, ``clean_config`` – ``config`` bindet sie ein) und die Texthelfer. Die
Grenzwerte für Prüfung, Fehlschläge und fremde Schreiber sind dieselben wie in Weg W (``dhw_rule``).
"""

from __future__ import annotations

import math
import re

from .dhw_rule import (CHECK_MAX_S, CHECK_S, CHECK_TOL_K, EMS_FRESH_S, FOREIGN_MAX, FOREIGN_WINDOW_S,  # noqa: F401
                       HINT_S, MAX_FAILS, Decision, _ago, _c, _hm, _iso)

BOILER_MODES = ("aus", "trocken", "heatpilot")
IDLE_MODES = ("pumpe", "aus")  # Bei keinem Wärmebedarf (0.46.5): Pumpe läuft weiter (Standard) | Heizbetrieb ganz aus
FLOW_MIN = 35             # Vorlauf-Soll nie darunter, wenn geheizt wird (Ingo); sonst „nur Pumpe“ bzw. Heizbetrieb aus
PUMP_FLOW = 20            # kein Bedarf, Pumpe läuft: so tief, dass der Brenner nicht anspringt (0.46.5)
IDLE_TEXT = f"Pumpe läuft, Vorlauf-Soll {PUMP_FLOW} °C"  # statt „Heizbetrieb aus“ (Vorschau, Status)
STEP_K = 1.0              # Vorlauf-Soll nur bei Änderung ab so viel
FLOW_GAP_S = 60           # höchstens ein Vorlauf-Schreiben je so viele Sekunden
KEEPALIVE_S = 60          # 0.48.1: Soll so oft erneuern – ein Kessel ohne Regler verwirft es sonst (zeigt dann 7 °C)
HEAT_HOLD_S = 300         # Heizbetrieb an/aus: Haltezeit nach jedem Schalten
HEAT_OFF_S = 900          # Heizbetrieb aus erst nach so langer Zeit ohne Bedarf (kein Pendeln an der Schwelle)
FLOW_DOWN_S = 600         # Vorlauf-Soll senken erst, wenn das Ziel so lange niedriger bleibt (anheben sofort)
BLIND_S = 900             # kein Vorlauf-Soll rechenbar: nach so langer Zeit der Rückfall-Vorlauf
BOOST_STEP_K = 2          # Takt-Boost: Schritt …
BOOST_MAX_K = 10          # … bis höchstens so viel über dem Wert aus Bedarf und Abnahme …
BOOST_STEP_S = 600        # … ein Schritt je so viele Sekunden (der erste sofort)
BOOST_GAP_K = 0.2         # ein Raum wartet: so weit unter seinem Soll …
BOOST_VALVE_PCT = 5.0     # … mit dem Ventil weiter offen
BOOST_WARM_K = 0.1        # Boost zurück sofort, sobald kein Raum mehr so weit unter Soll mit offenem Ventil wartet
FALLBACK_DEFAULT, FALLBACK_MIN, FALLBACK_MAX = 45, 35, 75  # Rückfall-Vorlauf (Zurückschalten, Wächter)
REGULATOR_FRESH_S = 600   # Werte des Reglers in Home Assistant gelten so lange als frisch („Regler am Bus“)

# Einstellungen von Weg B (``config.FIELDS``): Schalter, die zwei Entitäten (leer = erkannt), Rückfall-Vorlauf
FIELDS: dict[str, tuple] = {
    "boiler_control": ("choice", BOILER_MODES),
    "flow_set_entity": ("entity", "number"),
    "heating_switch_entity": ("entity", "switch"),
    "boiler_fallback_flow": ("int", FALLBACK_MIN, FALLBACK_MAX),
    "boiler_idle": ("choice", IDLE_MODES),
    "boiler_keepalive": ("bool",),  # 0.48.1: Vorlauf-Soll jede Minute erneuern (Kessel ohne Regler verwirft es sonst)
    "regulator_removed": ("bool",),  # 0.46.9: „Regler ist abgebaut“ – vom Nutzer bestätigt, Prüfung entfällt
    "boiler_period": ("bool",),      # 0.48: „Antipendelzeit anpassen“ (boiler_period, period_control), Standard aus
    "period_set_entity": ("entity", "number"),  # 0.48: Entität der Antipendelzeit, leer = erkannt
}
SWITCH_ENTITY = re.compile(r"^switch\.[a-z0-9_]+$")
NUMBER_ENTITY = re.compile(r"^number\.[a-z0-9_]+$")

LABELS = {"flow": "Vorlauf-Soll", "heating": "Heizbetrieb"}
REGULATOR_TEXT = "Regler am Bus – er überschreibt alle 60 s. Erst „Beobachten“ prüfen, dann Regler abbauen"
HINT_FAILSAFE = ("Fällt HeatPilot aus, bleibt der letzte Vorlauf stehen – dafür der Wächter in Home Assistant "
                 "(Anleitung Wächter)")
HINT_BACK = "Regler wieder anschließen bzw. den Kessel selbst stellen"


def clean_config(cfg) -> None:
    """Gespeicherte Einstellungen lesen (``Config.from_raw``): Unbekanntes gilt als „aus“, falsche Entitäten als leer,
    ein unlesbarer Rückfall-Vorlauf als Standard – alte und von Hand geänderte Einstellungen laden immer."""
    if cfg.boiler_control not in BOILER_MODES:
        cfg.boiler_control = "aus"
    for key, pattern in (("flow_set_entity", NUMBER_ENTITY), ("heating_switch_entity", SWITCH_ENTITY),
                         ("period_set_entity", NUMBER_ENTITY)):
        if not getattr(cfg, key, None) or not pattern.match(str(getattr(cfg, key))):
            setattr(cfg, key, None)
    cfg.boiler_period = getattr(cfg, "boiler_period", False) is True  # nur ein echtes „an“ (0.48), sonst aus
    v = cfg.boiler_fallback_flow
    ok = isinstance(v, int) and not isinstance(v, bool) and FALLBACK_MIN <= v <= FALLBACK_MAX
    cfg.boiler_fallback_flow = v if ok else FALLBACK_DEFAULT
    if getattr(cfg, "boiler_idle", None) not in IDLE_MODES:  # alte Einstellungen ohne die Wahl: Standard
        cfg.boiler_idle = IDLE_MODES[0]


# ------------------------------------------------------------------ Texthelfer
def on_off(value) -> str:
    return "?" if value is None else ("an" if value else "aus")


def shown(what: str, value) -> str:
    """Wert für Texte: „42 °C“ (Vorlauf) bzw. „an“/„aus“ (Heizbetrieb)."""
    return f"{_c(value)} °C" if what == "flow" else on_off(value)


# ------------------------------------------------------------------ Grenzen
def flow_limits(cfg, cap: float | None = None, bounds: tuple | None = None) -> tuple[int, int]:
    """Ganze Grad, die HeatPilot schreiben darf: 35 °C bis ``max_flow_temp``, darüber hinaus höchstens die
    Fußboden-Grenze ``cap`` und innerhalb der Grenzen der Entität ``bounds`` (min, max – None, wo Home Assistant
    nichts meldet). Leer (lo > hi), wenn die Grenze unter 35 °C liegt."""
    lo, hi = FLOW_MIN, int(math.floor(cfg.max_flow_temp + 1e-9))
    if cap is not None:
        hi = min(hi, int(math.floor(cap + 1e-9)))
    e_lo, e_hi = bounds or (None, None)
    if e_lo is not None:
        lo = max(lo, int(math.ceil(e_lo - 1e-9)))
    if e_hi is not None:
        hi = min(hi, int(math.floor(e_hi + 1e-9)))
    return lo, hi


def fallback_value(cfg, worst_cap: float | None, bounds: tuple | None = None) -> int | None:
    """Rückfall-Vorlauf (Zurückschalten, kein Ziel rechenbar, Wächter): ``boiler_fallback_flow``, höchstens
    ``max_flow_temp``, die strengste Fußboden-Grenze (sie gilt dann ohne HeatPilot immer) und der Höchstwert der
    Entität. None, wenn das unter 35 °C (bzw. dem Mindestwert der Entität) fiele."""
    lo, hi = flow_limits(cfg, worst_cap, bounds)
    value = min(int(cfg.boiler_fallback_flow), hi)
    return value if value >= lo else None


def idle_flow(cfg, bounds: tuple | None = None) -> int | None:
    """Vorlauf-Soll „nur Pumpe“ ohne Wärmebedarf (0.46.5): ``PUMP_FLOW``, mindestens der Mindestwert der Entität –
    None bei „Heizbetrieb ganz aus“ oder wenn das 35 °C erreichte (dann heizte der Kessel: Heizbetrieb aus wie bisher)."""
    if getattr(cfg, "boiler_idle", IDLE_MODES[0]) != "pumpe":
        return None
    e_lo, e_hi = bounds or (None, None)
    value = max(PUMP_FLOW, math.ceil(e_lo - 1e-9)) if e_lo is not None else PUMP_FLOW
    return value if value < FLOW_MIN and (e_hi is None or value <= e_hi) else None


# ------------------------------------------------------------------ Entscheidungen
def decide_flow(value: int | None, current: float | None, ts: float, last_ts: float | None = None,
                charging: bool = False, paused: bool = False, checking: bool = False,
                down_since: float | None = None, over: float | None = None, keepalive: bool = False) -> Decision:
    """Vorlauf-Soll schreiben? Nur bei Änderung ab ``STEP_K`` zum Kessel-Soll ``current``, höchstens einmal je
    ``FLOW_GAP_S`` nach ``last_ts``, nicht während einer Warmwasser-Ladung, einer Pause oder einer offenen Prüfung.
    Senken erst, wenn das Ziel seit ``down_since`` mindestens ``FLOW_DOWN_S`` niedriger ist (None = gilt nicht, z. B.
    für reine Regeltests: sofort). ``over``: Kesselwert über der Grenze (auch nur in einer Quelle bei unklarem Wert) –
    Schutz: sofort senken, ohne Abstand und offene Prüfung; nur eine Warmwasser-Ladung bzw. Pause hält es auf."""
    if value is None:
        return Decision(False, None, "kein Vorlauf-Soll")
    if paused:
        return Decision(False, value, "pausiert")
    if charging:
        return Decision(False, value, "Warmwasser lädt – Vorlauf-Soll bleibt, bis die Ladung vorbei ist")
    if over is not None:
        return Decision(True, value, f"Schutz: Kessel zeigt {_c(over)} °C, über der Grenze → {value} °C")
    if current is None:
        return Decision(False, value, "Vorlauf-Soll am Kessel unbekannt – nichts geschrieben")
    if keepalive and value >= FLOW_MIN and current <= PUMP_FLOW + 0.5:  # 0.48.1: Kessel hat das Soll verworfen
        return Decision(True, value, f"Kessel hat das Soll verworfen ({_c(current)} °C) → {value} °C")
    if abs(value - current) < STEP_K - 1e-9:
        if keepalive and value >= FLOW_MIN and not checking and (last_ts is None or ts >= last_ts + KEEPALIVE_S):
            return Decision(True, value, f"erneuert: {value} °C (der Kessel verwirft das Soll sonst)")  # 0.48.1
        return Decision(False, value, f"Vorlauf-Soll {_c(current)} °C passt")
    if checking:
        return Decision(False, value, "Prüfung des letzten Schreibens läuft")
    if last_ts is not None and ts < last_ts + FLOW_GAP_S:
        return Decision(False, value, f"höchstens ein Vorlauf-Schreiben je {FLOW_GAP_S} s",
                        until=int(last_ts + FLOW_GAP_S))
    if value < current and down_since is not None and ts < down_since + FLOW_DOWN_S:
        hold = f"senkt auf {value} °C erst, wenn das Ziel {FLOW_DOWN_S // 60} min niedriger bleibt"
        if keepalive and current >= FLOW_MIN and (last_ts is None or ts >= last_ts + KEEPALIVE_S):  # 0.48.6: das
            return Decision(True, round(current), f"erneuert: {_c(current)} °C, {hold}",  # gehaltene Soll
                            until=int(down_since + FLOW_DOWN_S))  # erneuern, sonst fällt der Kessel darin auf 7 °C
        return Decision(False, value, hold, until=int(down_since + FLOW_DOWN_S))
    return Decision(True, value, f"{_c(current)} → {value} °C")


def decide_heating(want: bool | None, current: bool | None, ts: float, last_ts: float | None = None,
                   paused: bool = False, checking: bool = False, off_since: float | None = None,
                   unsafe: str | None = None, protect: str | None = None, resting: bool = False) -> Decision:
    """Heizbetrieb schalten? Nur bei Wechsel, nicht in der Haltezeit von 5 min nach dem letzten Schalten, nicht in
    einer Pause oder während einer offenen Prüfung. Aus erst, wenn seit ``off_since`` ``HEAT_OFF_S`` lang kein Bedarf
    war (None = gilt nicht: sofort). An nicht, solange ``unsafe`` (Vorlauf-Soll am Kessel unklar bzw. über der Grenze).
    ``protect``: aus als Schutz – sofort, ohne Haltezeit und offene Prüfung. ``resting`` (seit 0.49.1): Der Kessel steht
    schon auf seinem Ruhewert (Vorlauf-Soll bis ``PUMP_FLOW``, z. B. 7 °C) und heizt nicht – aus braucht es dann nicht
    (mancher Kessel ohne Regler nimmt „aus“ gar nicht an)."""
    if want is None:
        return Decision(False, None, "Heizbetrieb offen – kein Vorlauf-Soll")
    if paused:
        return Decision(False, want, "pausiert")
    if current is None:
        return Decision(False, want, "Heizbetrieb am Kessel unbekannt – nichts geschaltet")
    if bool(current) == bool(want):
        return Decision(False, want, f"Heizbetrieb {on_off(want)} passt")
    if want and unsafe:
        return Decision(False, want, f"{unsafe} – Heizbetrieb bleibt aus")
    if not want and protect:
        return Decision(True, want, f"Schutz: {protect} – Heizbetrieb an → aus")
    if not want and resting:
        return Decision(False, want, "Kessel steht auf seinem Ruhewert und heizt nicht – Heizbetrieb bleibt an")
    if checking:
        return Decision(False, want, "Prüfung des letzten Schaltens läuft")
    if last_ts is not None and ts < last_ts + HEAT_HOLD_S:
        return Decision(False, want, f"Haltezeit bis {_hm(last_ts + HEAT_HOLD_S)}", until=int(last_ts + HEAT_HOLD_S))
    if not want and off_since is not None and ts < off_since + HEAT_OFF_S:
        return Decision(False, want, f"Heizbetrieb aus erst nach {HEAT_OFF_S // 60} min ohne Bedarf",
                        until=int(off_since + HEAT_OFF_S))
    return Decision(True, want, f"Heizbetrieb {on_off(current)} → {on_off(want)}")


# ------------------------------------------------------------------ Takt-Boost
def boost_step(boost: dict | None, wanted: bool, ts: int, headroom: int, settled: bool = True) -> dict:
    """Nächster Stand des Takt-Boosts {k, ts, on, want}: ``wanted`` = Kessel taktet und ein Raum wartet. Hoch in
    ``BOOST_STEP_K``-Schritten bis ``BOOST_MAX_K`` (der erste sofort, dann je ``BOOST_STEP_S``), höchstens bis
    ``headroom`` (Abstand zur Obergrenze bzw. Fußboden-Grenze); zurück in denselben Schritten – der erste sofort,
    sobald ``wanted`` endet und der Raum warm ist (``settled``), sonst erst ``BOOST_STEP_S`` nach dem letzten Wunsch
    (``want``): Schwankt der Raum an der Schwelle, pendelt der Boost nicht. ``ts`` ist die letzte Änderung."""
    b = dict(boost or {"k": 0, "ts": None, "on": False})
    k, last, was = int(b.get("k") or 0), b.get("ts"), bool(b.get("on"))
    want_ts = ts if wanted else b.get("want")
    due = last is None or ts >= last + BOOST_STEP_S
    calm = settled or want_ts is None or ts >= want_ts + BOOST_STEP_S
    headroom = max(int(headroom), 0)
    if wanted and k < min(BOOST_MAX_K, headroom) and (k == 0 or due):
        k, last = min(k + BOOST_STEP_K, BOOST_MAX_K, headroom), ts
    elif not wanted and k > 0 and calm and ((was and settled) or due):
        k, last = max(k - BOOST_STEP_K, 0), ts
    if k > headroom:
        k, last = headroom, ts
    return {**b, "k": k, "ts": last, "on": bool(wanted), "want": want_ts}


# ------------------------------------------------------------------ Mindest-Vorlauf aus der Abnahme
def load_flow(min_kw: float, kw_per_kh: float, rooms: list[tuple[float, float]], lo: int, hi: int) -> int | None:
    """Kleinster ganzer Vorlauf V in lo…hi, bei dem die anfordernden Räume (je Raum: Heizwirkung a in 1/h, Raum-
    temperatur) mindestens ``min_kw`` abnehmen: Σ kw_per_kh · a · (V − T) ≥ min_kw (Ventil offen). None, wenn auch
    ``hi`` nicht reicht – dann taktet der Kessel über seine eigene Sperre, keine Sonderlogik."""
    if min_kw <= 0 or kw_per_kh <= 0 or not rooms or lo > hi:
        return None

    def power(v: float) -> float:
        return kw_per_kh * sum(a * max(v - t, 0.0) for a, t in rooms)

    return next((v for v in range(lo, hi + 1) if power(v) >= min_kw - 1e-9), None)
