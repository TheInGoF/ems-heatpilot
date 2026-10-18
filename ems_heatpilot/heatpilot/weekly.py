"""Die Woche im Blick: wann im Haus was passiert – Montag bis Sonntag, je Stunde.

Alles aus den gespeicherten Stunden der letzten Wochen, je Wochentag und Stunde gemittelt
(7 × 24 Felder). Urlaubsstunden zählen nicht, Zählersprünge sind schon herausgerechnet.

* **Gas gesamt / Heizen / Warmwasser** (kW): was im Mittel verbraucht wird. Warmwasser =
  Gas in der Zeit, in der der Speicher lädt.
* **Gas wetterbereinigt** (W je Kelvin innen–außen): Heizgas ohne den Einfluss kalter
  oder milder Wochen – zeigt, ob ein Wochentag wirklich mehr braucht oder nur kälter war.
  Je Feld Heizgas im Verhältnis zum Bedarf laut Hausmodell (Summe zu Summe), mal Wärmeverlust:
  gleich H, wenn genau nach Modell durchgeheizt wird; Absenkung macht es kleiner. Ohne
  Hausmodell Heizgas je Grad-Stunde (Σ Gas / Σ ΔT).
* **Abweichung vom Hausmodell** (kW): gemessen minus vom Modell erwartet. Das Hausmodell
  kennt keinen Wochentag; ein festes Muster hier (z. B. Sonntag mittags weniger, weil gekocht
  wird) erklärt es noch nicht.
* **Brennerstarts** (nur Stunden mit aufgezeichneten Brennerdaten), **zu Hause** (Anteil der
  Zeit), **Tado-Soll** (Median der eingeschalteten Räume, nur wirklich eingestellte Werte),
  **Tado aus** (Anteil der Räume auf „aus“) und **Ventile** (Mittel, auch je Raum).
* Gas, Heizen und Warmwasser beruhen auf denselben Stunden: Stunden aus der Langzeitstatistik
  ohne Warmwasser-Angabe zählen nicht (außer die Anlage hat gar kein Warmwasser-Signal).
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
    "off": ("Tado aus", "%"),
    "valve": ("Ventile", "%"),
}
SUM_METRICS = ("gas", "heat", "dhw", "model", "starts")  # je Tag aufsummiert sinnvoll


def _cell(ts: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts).astimezone()
    return d.weekday(), d.hour


def _grid(values: dict[tuple[int, int], list[float]], digits: int, agg=None) -> tuple[list, list]:
    grid, counts = [], []
    for d in range(7):
        row, nrow = [], []
        for h in range(24):
            v = values.get((d, h), [])
            x = (agg(sorted(v)) if agg else sum(v) / len(v)) if len(v) >= MIN_N else None
            row.append(None if x is None else round(x, digits))
            nrow.append(len(v))
        grid.append(row)
        counts.append(nrow)
    return grid, counts


def _ratio_grid(sums: dict[tuple[int, int], list[float]], scale: float) -> tuple[list, list]:
    """Je Feld Σ Zähler / Σ Nenner · scale (Gas wetterbereinigt) – gasfreie Stunden zählen mit 0 Gas mit."""
    grid, counts = [], []
    for d in range(7):
        row, nrow = [], []
        for h in range(24):
            num, den, n = sums.get((d, h), (0.0, 0.0, 0))
            row.append(round(scale * num / den) if n >= MIN_N and den > 1e-6 else None)
            nrow.append(n)
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
    # ohne jedes Warmwasser-Signal gilt alles Gas als Heizgas (wie bei der Ersparnis)
    track = any(r["dhw_energy_frac"] is not None or r["dhw_frac"] is not None for r in hourly.values())
    use_model = house is not None and house_ok
    gas_k_sum: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0])  # Σ Gas, Σ Bedarf/ΔT, n
    measured: set[int] = set()
    for ts, r in hourly.items():
        if ts in vacation:
            continue
        c = _cell(ts)
        if r["home_frac"] is not None:
            v["home"][c].append(r["home_frac"] * 100)
        gas = r["gas_kwh"]
        if gas is None:
            continue
        # Warmwasser-Anteil nach Energie (Brennerleistung), ältere Stunden nach Zeit; ohne Angabe
        # (Langzeitstatistik) lässt sich Heizen/Warmwasser nicht trennen – dann zählt die Stunde nicht
        frac = r["dhw_energy_frac"] if r["dhw_energy_frac"] is not None else r["dhw_frac"]
        if frac is None:
            if track:
                continue
            frac = 0.0
        measured.add(ts)
        frac = min(max(frac, 0.0), 1.0)
        v["gas"][c].append(gas)
        v["heat"][c].append(gas * (1 - frac))
        v["dhw"][c].append(gas * frac)
        if r["t_in"] is None or r["t_out"] is None:
            continue
        dt = r["t_in"] - r["t_out"]
        if dt >= 3.0 and r["t_out"] <= 15:
            acc = gas_k_sum[c]
            acc[0] += gas * (1 - frac)
            # Bedarf laut Modell (ohne Speicher) in kW, ohne Modell die Gradstunden in K
            acc[1] += max(house.h_kw_per_k * dt + house.offset_kw, 0.0) if use_model else dt
            acc[2] += 1
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
    # nur Stunden mit aufgezeichneten Brennerdaten – Lücken und Stunden aus der Langzeitstatistik
    # zählen sonst als „0 Starts“. Ab Beginn der Recorder-Historie, frühestens ab dem ersten
    # Brennerereignis: history_start ist das angeforderte Fenster (z. B. 30 Tage), der HA-Recorder
    # hält oft nur 10 Tage
    hs = store.get("history_start")
    first = store.query("SELECT MIN(ts) AS t FROM burner_events")[0]["t"]
    if hs is not None and first is not None:
        hs = max(hs, first)
    for ts in hourly:
        if ts in vacation or (ts < hs - hs % 3600 if hs is not None else ts not in measured):
            continue
        v["starts"][_cell(ts)].append(starts.get(ts, 0))

    # Soll nur aus eingeschalteten Räumen (Mittel aus „aus“ = 5 °C und 21 °C ergäbe nie eingestellte Werte);
    # wie oft Tado aus ist, steht getrennt
    per_hour_target: dict[int, list[float]] = defaultdict(list)
    per_hour_off: dict[int, list[int]] = defaultdict(list)
    per_hour_valve: dict[int, list[float]] = defaultdict(list)
    for r in store.query(
        "SELECT ts - ts % 3600 AS h, room, AVG(target) AS target, AVG(valve) AS valve, COUNT(temp) AS n, "
        "SUM(CASE WHEN target IS NULL THEN 1 ELSE 0 END) AS n_off, COUNT(*) AS n_all "
        "FROM room_samples WHERE ts >= ? GROUP BY h, room", (since,)
    ):
        if rooms is not None and r["room"] not in rooms:
            continue
        if r["h"] in vacation:
            continue
        if r["n"]:  # „aus“ nur zählen, wenn der Raum überhaupt gemeldet hat
            off = 2 * r["n_off"] >= r["n_all"]
            per_hour_off[r["h"]].append(int(off))
            if not off and r["target"] is not None and r["target"] > OFF_TEMP:
                per_hour_target[r["h"]].append(r["target"])
        if r["valve"] is not None and (room is None or r["room"] == room):
            per_hour_valve[r["h"]].append(r["valve"])
    for h, vals in per_hour_target.items():
        v["target"][_cell(h)].append(statistics.median_low(sorted(vals)))
    for h, vals in per_hour_off.items():
        v["off"][_cell(h)].append(100 * sum(vals) / len(vals))
    for h, vals in per_hour_valve.items():
        v["valve"][_cell(h)].append(sum(vals) / len(vals))
    special = {"gas_k": _ratio_grid(gas_k_sum, house.h_kw_per_k * 1000 if use_model else 1000.0),
               "target": _grid(v["target"], 1, statistics.median_low)}

    out = {"days": days, "since": since, "hours": len(hourly) - len(vacation), "metrics": {}}
    for key, (label, unit) in METRICS.items():
        grid, counts = special.get(key) or _grid(v[key], 0 if key in ("home", "off") else 2)
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
