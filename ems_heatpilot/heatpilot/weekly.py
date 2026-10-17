"""Die Woche im Blick: wann im Haus was passiert – Montag bis Sonntag, je Stunde.

Alles aus den gespeicherten Stunden der letzten Wochen, je Wochentag und Stunde gemittelt
(7 × 24 Felder). Urlaubsstunden zählen nicht, Zählersprünge sind schon herausgerechnet.

* **Gas gesamt / Heizen / Warmwasser** (kW): was im Mittel verbraucht wird. Warmwasser =
  Gas in der Zeit, in der der Speicher lädt.
* **Gas wetterbereinigt** (W je Kelvin innen–außen): Heizgas ohne den Einfluss kalter
  oder milder Wochen – zeigt, ob ein Wochentag wirklich mehr braucht oder nur kälter war.
  Mit plausiblem Hausmodell wird der Achsabschnitt herausgerechnet (die Fremdwärme also wieder
  hinzugezählt, der Wert entspricht dem Wärmeverlust) und nur Stunden deutlich unter der
  Heizgrenze zählen – sonst sähen kalte Tage teurer aus.
* **Abweichung vom Hausmodell** (kW): gemessen minus vom Modell erwartet. Das Hausmodell
  kennt keinen Wochentag; ein festes Muster hier (z. B. Sonntag mittags weniger, weil gekocht
  wird) erklärt es noch nicht.
* **Brennerstarts**, **zu Hause** (Anteil der Zeit), **Tado-Soll** (Median der Räume, „aus“ =
  5 °C) und **Ventile** (Mittel, auch je Raum).
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from datetime import datetime

from .schedule import OFF_TEMP

WINDOW_DAYS = 56
MIN_N = 2           # so viele Stunden je Feld, bevor es einen Wert zeigt
DAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
DAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
METRICS = {
    "gas": ("Gas gesamt", "kW"),
    "heat": ("Gas fürs Heizen", "kW"),
    "dhw": ("Gas für Warmwasser", "kW"),
    "gas_k": ("Gas wetterbereinigt", "W/K"),
    "model": ("Abweichung vom Hausmodell", "kW"),
    "starts": ("Brennerstarts", "je h"),
    "home": ("Jemand zu Hause", "%"),
    "target": ("Tado-Soll (Median)", "°C"),
    "valve": ("Ventile", "%"),
}
SUM_METRICS = ("gas", "heat", "dhw", "model", "starts")  # je Tag aufsummiert sinnvoll


def _cell(ts: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts).astimezone()
    return d.weekday(), d.hour


def _grid(values: dict[tuple[int, int], list[float]], digits: int) -> tuple[list, list]:
    grid, counts = [], []
    for d in range(7):
        row, nrow = [], []
        for h in range(24):
            v = values.get((d, h), [])
            row.append(round(sum(v) / len(v), digits) if len(v) >= MIN_N else None)
            nrow.append(len(v))
        grid.append(row)
        counts.append(nrow)
    return grid, counts


def collect(store, now_ts: int | None = None, days: int = WINDOW_DAYS, house=None,
            rooms: list[str] | None = None, room: str | None = None, house_ok: bool = False) -> dict:
    """Alle Wochenraster auf einmal (für das gewählte Zeitfenster)."""
    now_ts = now_ts or int(time.time())
    since = now_ts - days * 86400
    v: dict[str, dict] = {k: defaultdict(list) for k in METRICS}

    hourly = {r["ts"]: r for r in store.query(
        "SELECT ts, t_out, t_in, gas_kwh, dhw_frac, dhw_energy_frac, home_frac, vacation_frac FROM hourly "
        "WHERE ts >= ? ORDER BY ts",
        (since,))}
    vacation = {ts for ts, r in hourly.items() if (r["vacation_frac"] or 0) > 0.5}
    offset = house.offset_kw if house is not None and house_ok else 0.0
    min_dt = max(house.heating_limit_k or 0.0, 0.0) + 2 if house is not None and house_ok else 3.0
    for ts, r in hourly.items():
        if ts in vacation:
            continue
        c = _cell(ts)
        if r["home_frac"] is not None:
            v["home"][c].append(r["home_frac"] * 100)
        gas = r["gas_kwh"]
        if gas is None:
            continue
        v["gas"][c].append(gas)
        # Warmwasser-Anteil nach Energie (Brennerleistung), ältere Stunden nach Zeit; ohne Angabe
        # (Langzeitstatistik) lässt sich Heizen/Warmwasser nicht trennen
        frac = r["dhw_energy_frac"] if r["dhw_energy_frac"] is not None else r["dhw_frac"]
        if frac is None:
            continue
        frac = min(max(frac, 0.0), 1.0)
        v["heat"][c].append(gas * (1 - frac))
        v["dhw"][c].append(gas * frac)
        if r["t_in"] is None or r["t_out"] is None:
            continue
        dt = r["t_in"] - r["t_out"]
        if dt >= max(min_dt, 3.0) and r["t_out"] <= 15:
            v["gas_k"][c].append((gas * (1 - frac) - offset) * 1000 / dt)
        before, after = hourly.get(ts - 3600), hourly.get(ts + 3600)
        if (house is not None and r["t_out"] <= house.max_learn_outdoor and before and after
                and before["t_in"] is not None and after["t_in"] is not None):
            d_in = (after["t_in"] - before["t_in"]) / 2  # wie beim Lernen: mittige Ableitung
            expected = house.h_kw_per_k * dt + house.c_kwh_per_k * d_in + house.offset_kw
            v["model"][c].append(gas * (1 - frac) - max(expected, 0.0))

    starts: dict[int, int] = defaultdict(int)
    prev = None
    for r in store.query("SELECT ts, state FROM burner_events WHERE ts >= ? ORDER BY ts", (since,)):
        if r["state"] == 1 and prev != 1:
            starts[r["ts"] - r["ts"] % 3600] += 1
        prev = r["state"]
    for ts in hourly:  # nur Stunden, die es gibt – sonst zählen Lücken als „0 Starts“
        if ts not in vacation and hourly[ts]["gas_kwh"] is not None:
            v["starts"][_cell(ts)].append(starts.get(ts, 0))

    per_hour_target: dict[int, list[float]] = defaultdict(list)
    per_hour_valve: dict[int, list[float]] = defaultdict(list)
    for r in store.query(
        "SELECT ts - ts % 3600 AS h, room, AVG(COALESCE(target, ?)) AS target, AVG(valve) AS valve, "
        "COUNT(temp) AS n FROM room_samples WHERE ts >= ? GROUP BY h, room", (OFF_TEMP, since)
    ):
        if rooms is not None and r["room"] not in rooms:
            continue
        if r["h"] in vacation:
            continue
        if r["n"]:  # „aus“ nur zählen, wenn der Raum überhaupt gemeldet hat
            per_hour_target[r["h"]].append(r["target"])
        if r["valve"] is not None and (room is None or r["room"] == room):
            per_hour_valve[r["h"]].append(r["valve"])
    for h, vals in per_hour_target.items():
        v["target"][_cell(h)].append(statistics.median(vals))
    for h, vals in per_hour_valve.items():
        v["valve"][_cell(h)].append(sum(vals) / len(vals))

    out = {"days": days, "since": since, "hours": len(hourly) - len(vacation), "metrics": {}}
    for key, (label, unit) in METRICS.items():
        grid, counts = _grid(v[key], 0 if key in ("home", "gas_k") else 1 if key == "target" else 2)
        if not any(x is not None for row in grid for x in row):
            continue
        m = {"label": label, "unit": unit, "grid": grid, "n": counts}
        if key in SUM_METRICS:
            m["per_day"] = [round(sum(x for x in row if x is not None), 1) if all(x is not None for x in row) else None
                            for row in grid]
        else:
            m["per_day"] = [_mean(row) for row in grid]
        m["findings"] = findings(key, m)
        out["metrics"][key] = m
    return out


def _mean(row):
    vals = [x for x in row if x is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _fmt(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def findings(key: str, m: dict) -> list[str]:
    """Kurze Klartext-Aussagen zum Raster – regelbasiert, ohne Interpretation darüber hinaus."""
    grid, unit, per_day = m["grid"], m["unit"], m["per_day"]
    cells = [(x, d, h) for d, row in enumerate(grid) for h, x in enumerate(row) if x is not None]
    if not cells:
        return []
    out = []
    digits = 0 if unit in ("%", "W/K") else 1
    if key in ("gas", "heat", "dhw", "gas_k", "starts", "valve"):
        top = sorted(cells, reverse=True)[:3]
        out.append("Am höchsten: " + " · ".join(f"{DAYS[d]} {h:02d} Uhr ({_fmt(x, digits)} {unit})" for x, d, h in top))
    if key == "model":
        big = sorted(cells, key=lambda c: abs(c[0]), reverse=True)[:3]
        out.append("Größte Abweichung: " + " · ".join(f"{DAYS[d]} {h:02d} Uhr ({'+' if x >= 0 else ''}{_fmt(x, 2)} kW)"
                                                     for x, d, h in big)
                   + " – plus: mehr verbraucht als erwartet, minus: weniger (z. B. Kochen, Sonne, viele Leute).")
    days = [(x, d) for d, x in enumerate(per_day) if x is not None]
    if len(days) >= 5 and key in ("gas", "heat", "dhw", "gas_k", "starts"):
        mean = sum(x for x, _ in days) / len(days)
        hi = max(days)
        if mean > 0:
            suffix = " je Tag" if key in SUM_METRICS else ""
            u = "kWh" if key in ("gas", "heat", "dhw") else "Starts" if key == "starts" else unit
            out.append(f"Tag mit dem meisten: {DAY_NAMES[hi[1]]} ({_fmt(hi[0], 0 if key == 'starts' else digits)} {u}{suffix}, "
                       f"{(hi[0] / mean - 1) * 100:+.0f} % gegenüber dem Mittel)")
        wk = [x for x, d in days if d < 5]
        we = [x for x, d in days if d >= 5]
        if wk and we and sum(wk) > 0:
            diff = (sum(we) / len(we)) / (sum(wk) / len(wk)) - 1
            out.append("Wochenende gegenüber Werktagen: " + (
                "kein Unterschied" if abs(diff) < 0.005 else f"{diff * 100:+.0f} %"))
    if key == "home":
        low = sorted(cells)[:3]
        out.append("Am seltensten jemand da: " + " · ".join(f"{DAYS[d]} {h:02d} Uhr ({x:.0f} %)" for x, d, h in low))
    return out
