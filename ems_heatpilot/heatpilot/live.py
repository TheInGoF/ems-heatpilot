"""Live-Ansicht: was Heizung und Haus gerade tun – und in den letzten Stunden getan haben.

Alle 30 s merkt sich HeatPilot die wichtigsten Werte im Speicher (12 h). Ältere
Abschnitte – etwa nach einem Neustart – kommen aus der Datenbank (alle 5 min).
"""

from __future__ import annotations

import time
from collections import deque

STEP_S = 30
KEEP_H = 12
KEYS = ("t_out", "flow", "sel_flow", "calc_flow", "ret", "boiler_temp", "burner_pow", "burner_on",
        "heating", "dhw", "dhw_temp", "dhw_set", "pump_mod", "flame", "hc_day")


class LiveBuffer:
    def __init__(self, hours: int = KEEP_H, step_s: int = STEP_S) -> None:
        self.points: deque = deque(maxlen=hours * 3600 // step_s + 10)

    def add(self, ts: int, sample: dict, rooms: list[tuple]) -> None:
        self.points.append((
            ts,
            {k: sample.get(k) for k in KEYS},
            {r[0]: (r[1], r[2], r[3]) for r in rooms},
        ))

    def since(self, ts: int) -> list[tuple]:
        return [p for p in self.points if p[0] >= ts]


def _mean(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def series(eng, hours: float = 12, now_ts: int | None = None) -> dict:
    """Zeitreihen der letzten ``hours`` Stunden: Datenbank (5 min) + Live-Puffer (30 s)."""
    now_ts = now_ts or int(time.time())
    start = now_ts - int(hours * 3600)
    ring = eng.live.since(start)
    ring_start = ring[0][0] if ring else now_ts + 1
    names = {r.climate: r.name for r in eng.found.rooms}

    out = {k: [] for k in ("ts",) + KEYS + ("t_in",)}
    rooms = {c: {"name": n, "ts": [], "temp": [], "target": [], "valve": []} for c, n in names.items()}

    db_rooms: dict[int, dict] = {}
    for r in eng.store.query(
        "SELECT ts, room, temp, target, valve FROM room_samples WHERE ts >= ? AND ts < ? ORDER BY ts",
        (start, ring_start),
    ):
        db_rooms.setdefault(r["ts"], {})[r["room"]] = (r["temp"], r["target"], r["valve"])
    for r in eng.store.query("SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (start, ring_start)):
        row = dict(r)
        _append(out, rooms, r["ts"], {k: row.get(k) for k in KEYS}, db_rooms.get(r["ts"], {}))
    for ts, sample, rmap in ring:
        _append(out, rooms, ts, sample, rmap)
    return {"series": out, "rooms": [{"climate": c, **v} for c, v in rooms.items()], "step_s": STEP_S}


def _append(out: dict, rooms: dict, ts: int, sample: dict, rmap: dict) -> None:
    out["ts"].append(ts)
    for k in KEYS:
        out[k].append(sample.get(k))
    out["t_in"].append(_mean(v[0] for v in rmap.values()))
    for climate, r in rooms.items():
        temp, target, valve = rmap.get(climate, (None, None, None))
        r["ts"].append(ts)
        r["temp"].append(temp)
        r["target"].append(target)
        r["valve"].append(valve)


def now(eng) -> dict:
    """Aktueller Zustand für die Anzeigen."""
    sample, rooms, persons = eng.snapshot()
    names = {r.climate: r.name for r in eng.found.rooms}
    prev = eng._live_prev or {}
    period = sample.get("min_period")
    lock_left = None
    if period and eng._last_off_ts is not None and not sample.get("burner_on"):
        left = period * 60 - (time.time() - eng._last_off_ts)
        lock_left = round(left / 60) if left > 0 else None
    from .engine import num  # spät: engine importiert dieses Modul

    nom = eng.cfg.boiler_nominal_kw or num(eng._boiler("nom_power"))
    power = sample.get("burner_pow")
    return {
        "ts": int(time.time()),
        **{k: sample.get(k) for k in KEYS},
        "t_in": _mean(r[1] for r in rooms),
        "power_kw": None if power is None or not nom else round(power / 100 * nom, 1),
        "hc_mode": eng._thermo("hc_mode"),
        "setpoint_demand": eng.setpoint_demand(),
        "lock_left_min": lock_left,
        "blocked": bool(prev.get("blocked")),
        "overrun": bool(prev.get("overrun")),
        "home": sample.get("home_count"),
        "persons": len(persons),
        "source": "EMS-ESP direkt" if eng.ems.get("boiler") else "Home Assistant",
        "rooms": [{"climate": c, "name": names.get(c, c), "temp": t, "target": tg, "valve": v, "humidity": h}
                  for c, t, tg, v, h in rooms],
    }
