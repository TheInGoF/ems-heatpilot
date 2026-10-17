"""Auswertungen: Takten, Ersparnis-Schätzung, Vorheizplan."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import HouseModel, RoomModel

GAS_FREE_KWH = 0.05  # Stunde ohne Heizbetrieb (wie beim Lernen der Auskühlzeit)

# Brennwert-Wirkungsgrad sinkt mit der Vorlauftemperatur (Richtwert ~0,4 %/K
# über 40 °C). Wird in Phase 2 aus dem Gasverbrauch gelernt.
EFF_PENALTY_PER_K = 0.004
# Sicherheitspuffer für den Vorheizplan (Minuten), damit es nicht knapp wird
PLAN_BUFFER_MIN = 10


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


RUN_MAX_MIN = 180           # längere „Läufe“ sind Aufzeichnungslücken


def heat_runs(store, since: int, until: int | None = None) -> tuple[float | None, int]:
    """Mittlere Laufzeit je Heizstart aus den Brenner-Ereignissen (ohne Warmwasserladungen).

    Für Kessel ohne Heizlaufzeit-Zähler. Ein Lauf zählt als Warmwasser, wenn währenddessen
    ein Messwert „Warmwasser lädt“ zeigt. Gezählt werden Läufe, die im Zeitraum enden.
    """
    until = until or 2**62
    events = store.query("SELECT ts, state FROM burner_events WHERE ts >= ? AND ts < ? ORDER BY ts",
                         (since - RUN_MAX_MIN * 60, until))
    dhw_ts = [r["ts"] for r in store.query("SELECT ts FROM samples WHERE ts >= ? AND ts < ? AND dhw = 1",
                                           (since - RUN_MAX_MIN * 60, until + 60))]
    runs, start = [], None
    for e in events:
        if e["state"] == 1 and start is None:
            start = e["ts"]
        elif e["state"] == 0 and start is not None:
            minutes = (e["ts"] - start) / 60
            if (e["ts"] >= since and 0.2 <= minutes <= RUN_MAX_MIN
                    and not any(start - 60 <= t <= e["ts"] + 60 for t in dhw_ts)):
                runs.append(minutes)
            start = None
    return (sum(runs) / len(runs) if runs else None), len(runs)


@dataclass
class SavingsResult:
    hours: int
    actual_kwh: float
    saved_kwh: float
    saved_pct: float | None
    daily: list[dict]
    cold_hours: int = 0
    # Prüfung je Raum (nur Stunden mit Raumdaten und Räume mit plausiblem Modell)
    room_hours: int = 0
    room_cold: dict | None = None
    lead_by_rooms: int = 0     # Absenkungen, die wegen eines langsamen Raums früher endeten
    lead_by_house: int = 0
    span_hours: int = 0        # ausgewertete Stunden plus Urlaub (Nenner der Jahres-Hochrechnung)
    dhw_separated: bool = True  # False: kein Warmwasser-Signal, alles Gas gilt als Heizgas


def estimate_savings(
    hourly: list[dict],
    house: HouseModel,
    away_temp: float,
    night_temp: float,
    is_night,
    p_max_kw: float = 18.0,
    rooms: dict[str, RoomModel] | None = None,
    room_hourly: dict[int, dict[str, float]] | None = None,
    max_flow: float = 75.0,
) -> SavingsResult:
    """Was hätte die Strategie (Absenken bei Abwesenheit/nachts) gespart – bei gleichem Komfort?

    Leitplanken gegen Schönrechnen:
    * Die Strategie darf nie unter ``away_temp``/``night_temp`` und nie über die
      tatsächliche Raumtemperatur gehen.
    * Aufheizen geht nur so schnell, wie die Therme kann:
      dT/h = (P_max - H * (T - T_außen)) / C. Deshalb muss sie rechtzeitig vor der
      Ankunft starten (Vorlauf aus dem gelernten Modell).
    * Jede Stunde mit Anwesenheit, in der es trotzdem mehr als 0,5 K kälter
      gewesen wäre, zählt als ``cold_hours`` – Ersparnis ohne Komfort zählt nicht.
    * **Je Raum** (wo Raumdaten und ein plausibles Raummodell vorliegen): Jeder Raum
      kühlt und heizt nach seinem eigenen Modell – aufheizen höchstens mit vollem
      Ventil und maximalem Vorlauf. Das Vorheizen startet so früh, wie der
      langsamste Raum braucht; was trotzdem zu kalt wäre, zählt je Raum.
    """
    h = house.h_kw_per_k
    c = house.c_kwh_per_k
    tau = house.tau_h or 30.0
    excess = house.cool_excess_k  # ohne Heizung bleibt das Haus so viel wärmer als außen (Fremdwärme)
    rows = []
    for row in hourly:
        when = datetime.fromtimestamp(row["ts"]).astimezone()
        home = row.get("home_frac")
        rows.append((row, when, home is None or home > 0, is_night(when.time())))

    def lead_hours(t_from: float, t_to: float, t_out: float) -> int:
        if c <= 0 or t_to <= t_from:
            return 0
        net = p_max_kw - h * (t_to - t_out)
        if net <= 0.1:
            return 24
        return min(int(math.ceil((t_to - t_from) * c / net)), 24)

    rooms = rooms or {}
    room_hourly = room_hourly or {}
    r_sim: dict[str, float | None] = {r: None for r in rooms}
    room_cold = {r: 0 for r in rooms}
    room_hours = by_rooms = by_house = 0

    def room_lead(t_out: float, actual: dict[str, float], dhw: float, floor_t: float) -> int:
        """Stunden, die der langsamste Raum nach dieser Absenk-Stunde zum Aufheizen bräuchte."""
        lead = 0
        for r, model in rooms.items():
            t_now, want = r_sim.get(r), actual.get(r)
            if t_now is None or want is None:
                continue
            t_after = max(model.cool_down(t_now, t_out, 1.0), min(floor_t, t_now))
            if t_after >= want - 0.05:
                continue
            hrs = model.time_to_reach(t_after, want, max_flow, t_out)
            if hrs is None:
                return 24  # schafft es nie: dann lieber gar nicht absenken
            lead = max(lead, int(math.ceil(hrs / max(1.0 - dhw, 0.3))))
        return min(lead, 24)

    def step_room(model: RoomModel, t: float, target: float, t_out: float, heat_h: float) -> float:
        if t > target:
            return max(model.cool_down(t, t_out, 1.0), target)
        if t < target:
            for _ in range(6):  # 10-min-Schritte, Heizen nur außerhalb der Warmwasserladung
                t += (model.rate(t, max_flow, t_out) * heat_h + model.rate(t, t, t_out) * (1 - heat_h)) / 6
                if t >= target:
                    return target
        return t

    track_dhw = any(r.get("dhw_energy_frac") is not None or r.get("dhw_frac") is not None for r in hourly)
    t_sim: float | None = None
    saved = actual = 0.0
    hours = cold = vac_hours = 0
    daily: dict[str, dict] = {}
    for i, (row, when, occupied, night) in enumerate(rows):
        t_in, t_out = row.get("t_in"), row.get("t_out")
        if t_in is None or t_out is None or (row.get("vacation_frac") or 0) > 0:
            t_sim = None  # Urlaubsstunden zählen nicht zur Ersparnis (aber im Jahr: dort spart man nichts)
            if t_in is not None and t_out is not None:
                vac_hours += 1
            continue
        if t_sim is None:
            t_sim = t_in
        target = t_in
        if night:
            target = min(t_in, night_temp)
        elif not occupied:
            target = min(t_in, away_temp)
        ra = room_hourly.get(row["ts"], {})
        for r in rooms:
            if ra.get(r) is None:
                r_sim[r] = None
            elif r_sim[r] is None:
                r_sim[r] = ra[r]
        dhw = min(row.get("dhw_frac") or 0.0, 1.0)
        if target < t_in:
            # rechtzeitig vorheizen: Komfort wird in den nächsten L Stunden gebraucht?
            lead_h = lead_hours(t_sim, t_in, t_out)
            lead_r = room_lead(t_out, ra, dhw, night_temp if night else away_temp)
            if lead_r >= 24:
                target = t_in  # ein Raum käme nicht mehr hoch: hier nicht absenken
                by_rooms += 1
            for j, (_, _, occ_j, night_j) in enumerate(rows[i + 1 : i + 1 + max(lead_h, lead_r)]):
                if target >= t_in:
                    break
                if occ_j and not night_j:
                    target = t_in
                    if j >= lead_h:
                        by_rooms += 1
                    else:
                        by_house += 1
                    break
        setback = target < t_in
        checked = False
        for r, model in rooms.items():
            if r_sim[r] is None:
                continue
            want = ra[r]
            r_target = min(want, night_temp if night else away_temp) if setback else want
            r_sim[r] = step_room(model, r_sim[r], r_target, t_out, 1.0 - dhw)
            if occupied and not night:
                checked = True
                if r_sim[r] < want - 0.5:
                    room_cold[r] += 1
        room_hours += checked
        def heat_up(t: float) -> float:
            # während Warmwasserladung bekommt der Heizkreis nichts (3-Wege-Ventil)
            p_heat = p_max_kw * (1.0 - min(row.get("dhw_frac") or 0.0, 1.0))
            rise = (p_heat - h * (t - t_out)) / c if c > 0 else 99.0
            return min(target, t + max(rise, 0.0))

        prev = rows[i - 1][0] if i > 0 else None
        prev_in = prev.get("t_in") if prev and prev["ts"] == row["ts"] - 3600 else None
        gas = row.get("gas_kwh")
        gas_free = gas is not None and gas <= GAS_FREE_KWH and not row.get("dhw_frac")
        if gas_free and prev_in is not None and prev_in - t_out - excess >= 0.5:
            # Die Heizung war auch in Wirklichkeit aus: die Absenkung wird nicht tiefer, der Rückstand
            # klingt ab – so wie das Haus tatsächlich abkühlt, aber mindestens mit der Trägheit
            # (hält Sonne das echte Haus warm, wärmt sie das abgesenkte genauso)
            ratio = min(max((t_in - t_out - excess) / (prev_in - t_out - excess), 0.0), 1.0, math.exp(-1.0 / tau))
            t_sim = t_in - max(prev_in - t_sim, 0.0) * ratio
            if t_sim < target:
                t_sim = heat_up(t_sim)
        elif t_sim > target:
            t_eq = t_out + excess
            cooled = t_eq + (t_sim - t_eq) * math.exp(-1.0 / tau)
            t_sim = max(min(cooled, t_sim), target)  # Auskühlen hebt nie an
        elif t_sim < target:
            t_sim = heat_up(t_sim)
        t_sim = min(t_sim, t_in)  # nie wärmer als in Wirklichkeit – keine negative Ersparnis
        if occupied and not night and t_sim < t_in - 0.5:
            cold += 1
        # Bezug ist das Heizgas: Warmwasser-Anteil nach Brennerleistung, ältere Stunden nach Zeit.
        # Ohne Angabe (Langzeitstatistik) lässt sich Heizen nicht vom Warmwasser trennen – zählt nicht.
        # Ganz ohne Warmwasser-Sensor zählt alles Gas als Heizgas.
        frac = row.get("dhw_energy_frac")
        if frac is None:
            frac = row.get("dhw_frac")
        if frac is None:
            if track_dhw:
                continue
            frac = 0.0
        gas = (row.get("gas_kwh") or 0.0) * (1.0 - min(max(frac, 0.0), 1.0))
        diff = h * (t_in - t_sim)
        saved += diff
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
    return SavingsResult(hours, round(actual, 1), round(saved, 1), pct, list(daily.values()), cold,
                         room_hours, {r: n for r, n in room_cold.items()} if rooms else None, by_rooms, by_house,
                         hours + vac_hours, track_dhw)


def preheat_plan(
    rooms: list[tuple[RoomModel, float, float]],
    house: HouseModel,
    arrival: datetime,
    now: datetime,
    t_out: float,
    max_flow: float,
    min_flow: float = 35.0,
    dhw: dict | None = None,
) -> dict | None:
    """Wann mit welcher Vorlauftemperatur starten, damit es bei Ankunft warm ist?

    ``rooms``: (Modell, aktuelle Temperatur, Solltemperatur bei Ankunft).
    Für jede Vorlauftemperatur wird die nötige Heizdauer des langsamsten Raums
    berechnet und der Gasbedarf geschätzt; gewählt wird der sparsamste Plan.

    ``dhw``: {"hours", "from", "to"} – Warmwasser muss auch geladen werden. Speicher
    und Heizkreis teilen sich die Therme (3-Wege-Ventil), also nacheinander:
    **erst Warmwasser, dann Heizen** – so kühlen die Räume während der Ladung nicht
    wieder aus, der volle Speicher verliert in der Heizphase nur wenig, und der
    Brenner läuft beim Umschalten ohne neuen Start durch.
    """
    dhw_h = (dhw or {}).get("hours", 0.0)
    buffer_h = PLAN_BUFFER_MIN / 60
    if not rooms:
        return None
    hours_left = max((arrival - now).total_seconds() / 3600, 0.0)
    options = []
    blocker = None  # Raum, der auch mit dem höchsten Vorlauf nicht warm wird
    flow = min_flow
    while flow <= max_flow + 0.1:
        worst = 0.0
        rises = []
        feasible = True
        for model, t_now, target in rooms:
            t_at_start = model.cool_down(t_now, t_out, max(hours_left - 1, 0))
            d = model.time_to_reach(t_at_start, target, flow, t_out)
            if d is None:
                feasible, blocker = False, model.room
                break
            worst = max(worst, d)
            rises.append(max(target - t_at_start, 0.0))
        if feasible:
            t_in_avg = sum(t for _, _, t in rooms) / len(rooms) - 1.0
            heat = house.demand_kw(t_in_avg, t_out) * worst  # mit Fremdwärme (Achsabschnitt)
            heat += house.c_kwh_per_k * sum(rises) / len(rises)
            eff = 1.0 + EFF_PENALTY_PER_K * max(flow - 40, 0)
            total = worst + dhw_h + buffer_h
            options.append(
                {
                    "flow": flow,
                    "duration_h": round(worst, 2),
                    "total_h": round(total, 2),
                    "start": arrival - timedelta(hours=total),
                    "est_kwh": round(heat * eff, 2),
                    "too_late": arrival - timedelta(hours=total) < now,
                }
            )
        flow += 5
    if not options:
        return {"feasible": False, "options": [], "unreachable": blocker}
    in_time = [o for o in options if not o["too_late"]]
    if in_time:
        best = dict(min(in_time, key=lambda o: (o["est_kwh"], o["flow"])))
        late_min = 0
    else:  # schon zu spät: sofort mit dem schnellsten Plan los
        fastest = min(options, key=lambda o: o["total_h"])
        late_min = round((now - fastest["start"]).total_seconds() / 60)
        best = {**fastest, "start": now}
    best["late_min"] = late_min
    best["steps"] = _sequence(best["start"], dhw, best["duration_h"], best["flow"], buffer_h,
                              None if late_min else arrival)
    return {"feasible": True, "best": best, "options": options}


def _sequence(start: datetime, dhw: dict | None, heat_h: float, flow: float, buffer_h: float,
              arrival: datetime | None = None) -> list[dict]:
    """Zeitablauf: erst Warmwasser laden, direkt danach Räume heizen (nie gleichzeitig)."""
    steps = []
    t = start
    if dhw and dhw.get("hours", 0) > 0:
        end = t + timedelta(hours=dhw["hours"])
        steps.append({"what": "Warmwasser", "start": t, "end": end,
                      "from": dhw.get("from"), "to": dhw.get("to")})
        t = end
    end = arrival or t + timedelta(hours=heat_h + buffer_h)  # pünktlich zur Ankunft fertig
    steps.append({"what": "Heizen", "start": t, "end": end, "flow": flow})
    return steps
