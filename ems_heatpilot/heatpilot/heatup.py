"""Aufheizen und Vorheizen aus den Raummodellen (seit 0.50.4) – statt eines festen Zuschlags von +5 K.

* ``heatup_flow`` – Vorlauf zum Aufheizen eines Raums: der niedrigste ab Halten (mindestens 35 °C), mit dem er laut
  Raummodell in ``HEATUP_H`` sein Soll erreicht; schafft es auch der höchste nicht, der höchste. Gilt live
  („heizt auf“ in der Vorschau und für den Kessel) und fürs tägliche Vorheizen.
* ``shared_flow`` – ein gemeinsamer Vorlauf fürs tägliche Vorheizen aller Räume, die zum selben Ziel aufheizen: der
  höchste ``heatup_flow`` dieser Räume (der langsamste Raum schafft es in ``HEATUP_H``), mindestens so viel, dass die
  Räume zusammen die Mindestleistung des Kessels abnehmen (``boiler_rule.load_flow``, sonst taktet er).
* ``preheat_hours`` – wie lange ein Raum bei diesem Vorlauf braucht: Jeder Raum beginnt so spät, dass er zum Ziel
  fertig ist (ein Raum, der 0,2 h braucht, 12 min vorher). Reicht der Vorlauf im Deckel ``preheat_max_h`` nicht,
  höher bis zum höchsten. Vor dem Ende eines Urlaubs sanft (``soft``): ab 35 °C, so früh wie nötig (0.46.5).
"""

from __future__ import annotations

import math

PREHEAT_MIN_H = 0.25      # so früh mindestens umschalten
PREHEAT_DEFAULT_H = 1.0   # Raummodell noch nicht plausibel: pauschal so lange vorheizen
PREHEAT_FLOW_FROM = 35.0  # kleinster Vorlauf beim Vorheizen (wie ``preview.FLOW_MIN``)
HEATUP_H = 1.0            # Aufheizen: Soll in etwa so vielen Stunden


def heatup_flow(model, temp: float, target: float, t_out: float, max_flow: float, hours: float = HEATUP_H) -> float:
    """Niedrigster ganzer Vorlauf ab Halten (mindestens 35 °C), mit dem der Raum in ``hours`` sein Soll erreicht."""
    lo = max(PREHEAT_FLOW_FROM, math.ceil(model.required_flow(target, t_out) - 1e-9))
    for flow in range(int(min(lo, max_flow)), int(max_flow) + 1):
        h = model.time_to_reach(temp, target, float(flow), t_out)
        if h is not None and h <= hours:
            return float(flow)
    return float(max_flow)


def shared_flow(rooms: list[tuple], max_flow: float, min_kw: float | None = None,
                kw_per_kh: float | None = None) -> float | None:
    """Gemeinsamer Vorlauf fürs tägliche Vorheizen – ``rooms``: (Raummodell, Ist, Ziel, Außentemperatur) der Räume,
    die aufheizen müssen. None ohne solche Räume."""
    if not rooms:
        return None
    flow = max(heatup_flow(m, t, sp, out, max_flow) for m, t, sp, out in rooms)
    if min_kw and kw_per_kh:
        from .boiler_rule import load_flow
        load = load_flow(min_kw, kw_per_kh, [(m.a, t, m.n) for m, t, _, _ in rooms], int(flow), int(max_flow))
        flow = float(load) if load is not None else flow
    return flow


def preheat_hours(model, temp: float | None, target: float, max_flow: float, t_out: float, cap: float,
                  soft: bool = False, flow: float | None = None) -> float:
    """Vorheizdauer in Stunden (siehe Modulbeschreibung): bei ``flow`` (sonst ``heatup_flow``, sanft ab 35 °C), reicht
    das im Deckel nicht, höher; unerreichbar → Deckel. ``model`` None bzw. Raumtemperatur unbekannt →
    ``PREHEAT_DEFAULT_H``. Die Vorschau findet denselben Vorlauf (``preview._preheat_flow``)."""
    cap = max(cap, PREHEAT_MIN_H)
    if model is None or temp is None:
        return min(PREHEAT_DEFAULT_H, cap)
    start = PREHEAT_FLOW_FROM if soft else (flow or heatup_flow(model, temp, target, t_out, max_flow))
    for f in range(int(min(start, max_flow)), int(max_flow) + 1):
        hours = model.time_to_reach(temp, target, float(f), t_out)
        if hours is not None and hours <= cap:
            return max(hours, PREHEAT_MIN_H)
    return cap  # Ziel auch mit maximalem Vorlauf nicht im Deckel erreichbar: bis zum Deckel vorheizen


# ------------------------------------------------------------------ 0.50.5: aus jedem Vorheizen lernen
MARGIN_DEFAULT = 0.5       # Vorheizen: Thermostat zuerst so viel unter dem Ziel – die Restwärme bringt den Rest
MARGIN_MAX = 1.5
SPEED_MIN, SPEED_MAX = 0.5, 3.0  # gelernte Aufheizgeschwindigkeit gegenüber dem Raummodell
LEARN = 0.3                # so viel zählt jedes neue Vorheizen
COAST_S = 1800             # nach dem Ende so lange noch die Spitze (Restwärme) mitnehmen


def tune(st: dict) -> tuple[float, float]:
    """(Abstand unter dem Ziel, Geschwindigkeit) eines Raums – gelernt, sonst die Startwerte."""
    t = st.get("tune") or {}
    return float(t.get("margin", MARGIN_DEFAULT)), float(t.get("speed", 1.0))


def track(st: dict, phase: str, setpoint: float, until, temp: float | None, ts: int, vacation: bool) -> None:
    """Tägliches Vorheizen beobachten (Beginn, geplantes Ende, wann das abgesenkte Soll erreicht war, höchste
    Temperatur bis ``COAST_S`` danach) und danach lernen: Geschwindigkeit = geplante / tatsächliche Dauer (nicht
    erreicht: langsamer), Abstand so, dass die Spitze aufs Ziel fällt (Überschwingen → mehr Abstand)."""
    pre = st.get("pre")
    if phase == "vorheizen" and not vacation and until is not None and temp is not None:
        if pre is None:
            m = tune(st)[0]
            pre = st["pre"] = {"start": ts, "end": int(until.timestamp()), "sp": setpoint, "m": m, "peak": temp,
                               "reached": None}
        pre["peak"] = max(pre["peak"], temp)
        if pre["reached"] is None and temp >= pre["sp"] - 0.05:
            pre["reached"] = ts
        return
    if pre is None:
        return
    if temp is not None and 0 <= ts - pre["end"] < COAST_S and phase != "nacht":
        pre["peak"] = max(pre["peak"], temp)
        return
    st.pop("pre")
    m, s = tune(st)
    planned = max(pre["end"] - pre["start"], 60)
    seen = s * planned / max(pre["reached"] - pre["start"], 60) if pre["reached"] else s * 0.8
    over = pre["peak"] - (pre["sp"] + pre["m"]) if pre["reached"] else 0.0
    st["tune"] = {"speed": round(min(max((1 - LEARN) * s + LEARN * seen, SPEED_MIN), SPEED_MAX), 3),
                  "margin": round(min(max(m + LEARN * over, 0.0), MARGIN_MAX), 2)}


def engine_shared_flow(eng, readings: dict, t_out: float | None) -> float | None:
    """Gemeinsamer Vorlauf fürs tägliche Vorheizen (``shared_flow``) der Räume, die mitmachen, ein plausibles
    Modell haben und mehr als 0,3 K unter Komfort liegen – mit der Mindestleistung des Kessels (``controlloop``)."""
    from . import guards
    from .boiler_flow import min_power_kw

    out, rooms = 5.0 if t_out is None else t_out, []
    for r in eng.found.rooms:
        m, temp = eng.rooms.get(r.climate), (readings.get(r.climate) or (None, None))[1]
        if r.managed and m is not None and temp is not None and temp < r.comfort - 0.3 and guards.check_room(m)["ok"]:
            rooms.append((m, temp, float(r.comfort), out))
    need = sum(max(m.b * (t - out) - m.c, 0.0) for m, t, _, _ in rooms)
    house = (eng.house.demand_kw(sum(x[1] for x in rooms) / len(rooms), out)
             if rooms and need > 0 and guards.check_house(eng.house)["ok"] else 0.0)
    return shared_flow(rooms, eng.cfg.max_flow_temp, min_power_kw(eng)[0], house / need if house > 0 else None)


# ------------------------------------------------------------------ 0.56.9: gemeinsam anfangen
JOIN_H = 0.75  # wer zum selben Ziel spätestens so bald selbst anfinge, fängt mit dem ersten Raum an


def start_together(desireds: dict, want, now) -> dict:
    """Vorheizen gemeinsam beginnen: Heizt ein Raum schon vor (Ziel ``until``), fangen die Räume, die zum selben Ziel
    innerhalb von ``JOIN_H`` selbst anfingen, gleich mit an – der Kessel heizt schon, und nur offene Thermostate holen
    die Wärme ab (Fall 25.10.: Vorlauf 55 °C ab 17:25, die Räume erst ab 18:30, alle zu kalt bei der Ankunft).
    ``want(climate, Zeitpunkt)``: Wunschzustand des Raums zu einem Zeitpunkt (``controlloop.desired``)."""
    from datetime import timedelta

    goals = {d.until for d in desireds.values() if d.phase == "vorheizen" and d.until is not None}
    if not goals:
        return desireds
    out = dict(desireds)
    for c, d in desireds.items():
        if d.phase not in ("abwesend", "nacht") or d.until is None or d.until - now > timedelta(hours=JOIN_H):
            continue
        later = want(c, d.until + timedelta(minutes=1))  # so sähe der Raum zu seinem eigenen Beginn aus
        if later.phase == "vorheizen" and later.until in goals:
            out[c] = later._replace(reason=f"{later.reason} – gemeinsam mit den anderen Räumen")
    return out
