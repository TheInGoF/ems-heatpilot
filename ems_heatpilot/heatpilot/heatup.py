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
        load = load_flow(min_kw, kw_per_kh, [(m.a, t) for m, t, _, _ in rooms], int(flow), int(max_flow))
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
