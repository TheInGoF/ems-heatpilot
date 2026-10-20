"""Vorlauf-Soll live (Weg B, seit 0.41): was HeatPilot jetzt am Kessel setzen würde – Bedarf, Abnahme, Boost, Grenzen.

Bei jedem Schritt (30 s) neu gerechnet, ohne eigenen Zustand – nur der Takt-Boost läuft über ``boiler_control`` weiter
(``compute`` bekommt seinen Stand und gibt den neuen zurück). In dieser Reihenfolge (Planungspapier, festgelegt):

1. **Bedarf** – dieselbe Rechnung wie die Vorschau (``preview._Run``, ein Schritt genau jetzt): nötiger Vorlauf des
   anspruchsvollsten Raums aus den Raummodellen, Vorheizen, Aufheizzuschlag, Fußboden-Grenze; Untergrenze 35 °C –
   halten alle Räume nur und braucht keiner so viel, ist der Heizbetrieb aus (heizt einer auf oder vor: an mit 35 °C,
   seit 0.46.5). Nacht und Abwesenheit folgen aus den Raum-Sollwerten der
   Steuerung (braucht kein Raum Wärme, ist der Heizbetrieb aus). Seit 0.46.5 bleibt er dann standardmäßig an und das
   Vorlauf-Soll geht auf ``boiler_rule.PUMP_FLOW`` (``idle``: nur Pumpe, ``boiler_rule.idle_flow``); bei der Einstellung
   „Heizbetrieb ganz aus“ wie bisher.
2. **Mindest-Vorlauf aus der Abnahme** (``_load``) – Kessel-Mindestleistung (Einstellung bzw. EMS-ESP, Prozent ×
   Nennleistung) gegen die Abgabe der gerade anfordernden Räume (Ventil offen): je Raum Heizwirkung × (Vorlauf −
   Raumtemperatur) aus dem Raummodell, in kW umgerechnet mit dem Hausmodell (Wärmebedarf des Hauses jetzt verteilt auf
   den Bedarf der Räume mit Raummodell – Näherung: Räume gleicher Wärmespeicherung). Liegt der nötige Wert über dem
   aus 1., gilt er; reicht auch der höchste nicht, gilt nichts (lange Pausen, keine Sonderlogik).
3. **Takt-Boost** (``boiler_rule.boost_step``) – taktet der Kessel stark (mindestens 3 Starts in der letzten Stunde,
   Läufe im Mittel unter 10 min, ``valve_dry.cycling``) und wartet ein Raum unter Soll mit offenem Ventil: +2 K je
   Schritt bis +10 K über den Wert aus 1./2.; zurück in 2-K-Schritten, sobald der Raum warm ist (kein Raum mehr
   ``BOOST_WARM_K`` unter Soll mit offenem Ventil) bzw. 10 min, nachdem kein Raum mehr wartet oder das Takten aufhört.
   Wer Wärme braucht, bekommt sie sofort. Die Antipendelzeit des Kessels bleibt, wie sie ist.
4. **Fußboden-Grenze hart** – ``floor_limit.flow_cap`` mit den Messwerten von jetzt, auch beim Boost und Vorheizen;
   liegt sie unter 35 °C, ist der Heizbetrieb aus (darunter schreibt HeatPilot keinen Vorlauf).
5. **Obergrenze** ``max_flow_temp`` und die Grenzen, die Home Assistant für die Entität meldet (``bounds``); das
   Ergebnis in ganzen Grad. ``Target.hi`` ist die Obergrenze jetzt – darüber schreibt ``boiler_control`` als Schutz
   sofort zurück.

Läuft eine Warmwasser-Ladung oder Sonderladung (``charging``), fasst HeatPilot das Vorlauf-Soll nicht an – der Kessel
lädt den Speicher über das 3-Wege-Ventil. Schreibt nichts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import boiler_rule as br
from . import boiler_power, floor_limit, guards, preview, valve_dry
from .dhw_control import special_running


@dataclass
class Target:
    """Ergebnis eines Schritts: Heizbetrieb (None = kein Vorlauf-Soll rechenbar), Vorlauf-Soll und die Teile davon."""

    heating: bool | None
    flow: int | None
    reason: str
    base: int | None = None          # aus dem Bedarf (1.)
    load: int | None = None          # Mindest-Vorlauf aus der Abnahme (2.), None = gilt nicht
    load_text: str | None = None
    boost_k: int = 0                 # Takt-Boost (3.), wirksam
    boost_wanted: bool = False
    waiting: list = field(default_factory=list)   # Räume, die unter Soll mit offenem Ventil warten
    cycling: dict | None = None      # Brennerstarts und Laufzeit der letzten Stunde
    cap: float | None = None         # Fußboden-Grenze jetzt (4.)
    cap_why: str | None = None
    limited: bool = False            # Grenze (4./5.) hat den Wert gesenkt
    charging: bool = False           # Warmwasser lädt – Vorlauf-Soll nicht anfassen
    t_out: float | None = None
    hi: int | None = None            # höchstes Vorlauf-Soll jetzt (Fußboden-Grenze, max_flow_temp, Entität)
    cap_off: bool = False            # Heizbetrieb aus, weil die Grenze unter 35 °C liegt (Schutz: sofort)
    idle: bool = False               # kein Wärmebedarf: Heizbetrieb an, Vorlauf-Soll „nur Pumpe“ (0.46.5)


def _readings(eng) -> dict[str, tuple]:
    return {r.climate: x for r in eng.found.rooms if not r.ems and (x := eng.room_reading(r)) is not None}


def _waiting(eng, readings: dict, gap: float = br.BOOST_GAP_K) -> list[str]:
    """Räume unter Soll (mehr als ``gap``) mit offenem Ventil – sie warten auf Wärme."""
    out = []
    for room in eng.found.rooms:
        _, temp, target, valve, _ = readings.get(room.climate, (None,) * 5)
        if None not in (temp, target, valve) and temp < target - gap and valve > br.BOOST_VALVE_PCT:
            out.append(room.name)
    return out


def min_power_kw(eng) -> tuple[float | None, str | None]:
    """Mindestleistung des Kessels in kW (Einstellung bzw. EMS-ESP: Prozent × Nennleistung) – oder warum unbekannt
    (``boiler_power.missing``: „Abnahme-Regel aus: …“, seit 0.42 mit dem Vorschlag aus dem Bereich der maximalen
    Brennerleistung, der nie automatisch gilt)."""
    pct, nom = boiler_power.pct(eng)[0], eng.nominal_kw()
    if not pct or not nom:
        return None, boiler_power.missing(eng)
    return pct / 100.0 * nom, None


def _load(eng, run, readings: dict, t_out: float, lo: int, hi: int) -> tuple[int | None, str | None]:
    """Mindest-Vorlauf aus der Abnahme (siehe Modulbeschreibung): (Vorlauf, Text) – (None, Grund), wenn nichts gilt."""
    min_kw, why = min_power_kw(eng)
    if min_kw is None:
        return None, why
    if not guards.check_house(eng.house)["ok"]:
        return None, "Abnahme: Hausmodell lernt noch – ohne Mindest-Vorlauf aus der Abnahme"
    rooms = [(r, readings[r.room.climate][1]) for r in run.rooms
             if r.heats and r.room.climate in readings and readings[r.room.climate][1] is not None]
    need = sum(max(r.model.b * (t - t_out) - r.model.c, 0.0) for r, t in rooms)  # K/h, Räume mit Raummodell
    house_kw = eng.house.demand_kw(sum(t for _, t in rooms) / len(rooms), t_out) if rooms else 0.0
    if need <= 0 or house_kw <= 0:
        return None, "Abnahme: gerade kein Wärmebedarf laut Haus- und Raummodellen"
    open_rooms = [(r.model.a, t) for r, t in rooms if (readings[r.room.climate][3] or 0) > br.BOOST_VALVE_PCT]
    if not open_rooms:
        return None, "Abnahme: kein Raum mit offenem Ventil"
    value = br.load_flow(min_kw, house_kw / need, open_rooms, lo, hi)
    n = f"{len(open_rooms)} Raum nimmt" if len(open_rooms) == 1 else f"{len(open_rooms)} Räume nehmen"
    if value is None:
        return None, (f"Abnahme: {n} {br._c(round(min_kw, 1))} kW auch bei {hi} °C nicht ab – lange Pausen, "
                      "der Kessel taktet über seine eigene Sperre")
    return value, f"Abnahme: {n} die Mindestleistung {br._c(round(min_kw, 1))} kW ab {value} °C ab"


def _off(heating, reason: str, **kw) -> Target:
    return Target(heating, None, reason, **kw)


def compute(eng, now_ts: int, boost: dict | None = None, bounds: tuple | None = None) -> tuple[Target, dict]:
    """Ziel jetzt (siehe Modulbeschreibung) und der neue Stand des Takt-Boosts. ``bounds``: (min, max) der Entität des
    Vorlauf-Solls laut Home Assistant (``boiler_entity.bounds``)."""
    run = preview._Run(eng, now_ts)
    run.start_ts = now_ts          # erster Schritt genau jetzt (Fußboden-Anforderung aus den Messwerten)
    row = run.step(run.now)
    readings = _readings(eng)
    charging = bool(run.sample.get("dhw")) or special_running(eng, run.sample)
    cap, cap_why = floor_limit.flow_cap(run.floors, floor_limit.read(run.floors, eng.states))
    lo, hi = br.flow_limits(eng.cfg, cap, bounds)
    common = {"cap": cap, "cap_why": cap_why, "charging": charging, "t_out": row["t_out"], "hi": hi}
    reset = br.boost_step(boost, False, now_ts, 0)
    if row["heating"] is None:
        return _off(None, row["why"], **common), reset
    if row["heating"] is False:
        pump = br.idle_flow(eng.cfg, bounds) if row.get("idle") else None
        if pump is not None:  # 0.46.5: kein Bedarf – Heizbetrieb bleibt an, nur die Pumpe läuft
            return Target(True, pump, row["why"], base=pump, idle=True, **common), reset
        return _off(False, row["why"].replace(br.IDLE_TEXT, "Heizbetrieb aus"), **common), reset
    if lo > hi:
        what = f"Fußboden-Grenze {br._c(cap)} °C" if cap is not None and cap < br.FLOW_MIN else "Obergrenze"
        return _off(False, f"{row['why']} – {what} liegt unter {lo} °C: HeatPilot schreibt keinen Vorlauf "
                           "darunter, Heizbetrieb aus", limited=True, cap_off=True, **common), reset
    base = min(max(int(row["flow"]), lo), hi)
    load, load_text = _load(eng, run, readings, row["t_out"], lo, hi)
    value, reason = base, row["why"]
    if load is not None and load > value:
        value, reason = load, f"{reason} · {load_text}"
    cyc = valve_dry.cycling(eng.store, now_ts)
    waiting = _waiting(eng, readings)
    wanted = bool(cyc["cycling"] and waiting)
    settled = not _waiting(eng, readings, br.BOOST_WARM_K)  # warm: zurück sofort, sonst erst 10 min nach dem Wunsch
    new_boost = br.boost_step(boost, wanted, now_ts, hi - value, settled)
    k = min(int(new_boost["k"]), hi - value)
    flow = int(math.floor(min(value + k, hi)))
    by_cap = cap is not None and hi == math.floor(cap + 1e-9)
    # gesenkt: über der Grenze – oder der Boost will mehr, als bis zur Grenze geht
    limited = (max(int(row["flow"]), base, load or 0) + k > hi or bool(row.get("limited"))
               or (wanted and flow >= hi and k < br.BOOST_MAX_K))
    if k:
        reason = f"{reason} · Boost +{k} K"
    if limited and by_cap and flow >= hi and "begrenzt" not in reason:
        reason = f"{reason} – begrenzt durch {cap_why}"
    elif limited and flow >= hi and "begrenzt" not in reason and bounds and bounds[1] is not None \
            and hi == math.floor(bounds[1] + 1e-9):
        reason = f"{reason} – begrenzt durch den Höchstwert der Entität ({hi} °C)"
    return Target(True, flow, reason, base=base, load=load, load_text=load_text, boost_k=k, boost_wanted=wanted,
                  waiting=waiting, cycling=cyc, limited=limited, **common), new_boost
