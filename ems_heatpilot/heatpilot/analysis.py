"""Auswertungen: Takten, Ersparnis-Schätzung, Vorheizplan."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import HouseModel, RoomModel

# Brennwert-Wirkungsgrad sinkt mit der Vorlauftemperatur (Richtwert ~0,4 %/K
# über 40 °C). Wird in Phase 2 aus dem Gasverbrauch gelernt.
EFF_PENALTY_PER_K = 0.004


def cycling_stats(events: list[tuple[int, int]], day_start: int, day_end: int) -> dict:
    """Brennerstarts, mittlere Lauf- und Pausenzeit aus Ein/Aus-Ereignissen."""
    starts = 0
    runs: list[float] = []
    pauses: list[float] = []
    last_on: int | None = None
    last_off: int | None = None
    prev_state: int | None = None
    for ts, state in events:
        if state == prev_state:
            continue
        if state == 1:
            if day_start <= ts < day_end:
                starts += 1
                if last_off is not None:
                    pauses.append((ts - last_off) / 60)
            last_on = ts
        else:
            if last_on is not None and day_start <= ts < day_end:
                runs.append((ts - last_on) / 60)
            last_off = ts
        prev_state = state
    return {
        "starts": starts,
        "avg_run_min": round(sum(runs) / len(runs), 1) if runs else None,
        "avg_pause_min": round(sum(pauses) / len(pauses), 1) if pauses else None,
        "short_runs": sum(1 for r in runs if r < 10),
    }


@dataclass
class SavingsResult:
    hours: int
    actual_kwh: float
    saved_kwh: float
    saved_pct: float | None
    daily: list[dict]


def estimate_savings(
    hourly: list[dict],
    house: HouseModel,
    away_temp: float,
    night_temp: float,
    is_night,
) -> SavingsResult:
    """Was hätte die Strategie (Absenken bei Abwesenheit/nachts) gespart?

    Energiebilanz: Verbrauch ~ H * Σ (T_innen - T_außen). Die Strategie lässt
    das Haus mit seiner gelernten Trägheit (tau = C/H) auskühlen und heizt
    rechtzeitig zur Ankunft wieder auf; das Wiederaufheizen ist über C
    enthalten. Ersparnis = H * Σ (T_ist - T_strategie).
    """
    h = house.h_kw_per_k
    tau = house.tau_h or 30.0
    t_sim: float | None = None
    saved = 0.0
    actual = 0.0
    hours = 0
    daily: dict[str, dict] = {}
    for row in hourly:
        t_in, t_out = row.get("t_in"), row.get("t_out")
        if t_in is None or t_out is None:
            t_sim = None
            continue
        when = datetime.fromtimestamp(row["ts"]).astimezone()
        home = row.get("home_frac")
        occupied = home is None or home > 0
        target = t_in
        if is_night(when.time()):
            target = min(t_in, night_temp)
        elif not occupied:
            target = min(t_in, away_temp)
        if t_sim is None:
            t_sim = t_in
        if t_sim > target:
            cooled = t_out + (t_sim - t_out) * math.exp(-1.0 / tau)
            t_sim = max(cooled, target)
        else:
            t_sim = target
        diff = h * (t_in - t_sim)
        saved += diff
        gas = row.get("gas_kwh") or 0.0
        actual += gas
        hours += 1
        day = when.date().isoformat()
        d = daily.setdefault(day, {"day": day, "actual_kwh": 0.0, "saved_kwh": 0.0})
        d["actual_kwh"] += gas
        d["saved_kwh"] += diff
    for d in daily.values():
        d["actual_kwh"] = round(d["actual_kwh"], 2)
        d["saved_kwh"] = round(d["saved_kwh"], 2)
    pct = round(100 * saved / actual, 1) if actual > 1 else None
    return SavingsResult(hours, round(actual, 1), round(saved, 1), pct, list(daily.values()))


def preheat_plan(
    rooms: list[tuple[RoomModel, float, float]],
    house: HouseModel,
    arrival: datetime,
    now: datetime,
    t_out: float,
    max_flow: float,
    min_flow: float = 35.0,
) -> dict | None:
    """Wann mit welcher Vorlauftemperatur starten, damit es bei Ankunft warm ist?

    ``rooms``: (Modell, aktuelle Temperatur, Solltemperatur bei Ankunft).
    Für jede Vorlauftemperatur wird die nötige Heizdauer des langsamsten Raums
    berechnet und der Gasbedarf geschätzt; gewählt wird der sparsamste Plan.
    """
    if not rooms:
        return None
    hours_left = max((arrival - now).total_seconds() / 3600, 0.0)
    options = []
    flow = min_flow
    while flow <= max_flow + 0.1:
        worst = 0.0
        rises = []
        feasible = True
        for model, t_now, target in rooms:
            t_at_start = model.cool_down(t_now, t_out, max(hours_left - 1, 0))
            d = model.time_to_reach(t_at_start, target, flow, t_out)
            if d is None:
                feasible = False
                break
            worst = max(worst, d)
            rises.append(max(target - t_at_start, 0.0))
        if feasible:
            t_in_avg = sum(t for _, _, t in rooms) / len(rooms) - 1.0
            heat = house.h_kw_per_k * max(t_in_avg - t_out, 0) * worst
            heat += house.c_kwh_per_k * sum(rises) / len(rises)
            eff = 1.0 + EFF_PENALTY_PER_K * max(flow - 40, 0)
            options.append(
                {
                    "flow": flow,
                    "duration_h": round(worst, 2),
                    "start": arrival - timedelta(hours=worst),
                    "est_kwh": round(heat * eff, 2),
                    "too_late": arrival - timedelta(hours=worst) < now,
                }
            )
        flow += 5
    if not options:
        return {"feasible": False, "options": []}
    in_time = [o for o in options if not o["too_late"]]
    if in_time:
        best = min(in_time, key=lambda o: (o["est_kwh"], o["flow"]))
    else:  # schon zu spät: sofort mit dem schnellsten Plan los
        best = {**min(options, key=lambda o: o["duration_h"]), "start": now}
    return {"feasible": True, "best": best, "options": options}
