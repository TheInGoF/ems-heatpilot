"""Künstliche Thermostate für Tests: ein Raum mit Heizkörper und ein Ventilregler mit bekannten Eigenschaften.

Damit prüfen die Tests, dass HeatPilot das Ventilverhalten eines *beliebigen* Thermostats lernt – stetig (PI mit
Totband, Totzeit, Stellmotor, Sättigung 0–100 %) oder nur an/aus (Schaltschwellen) –, nicht eines bestimmten Geräts.

    Raum:   dT/dt = a · (Vorlauf − T) · Ventil + b · (außen − T) + Fenster + Fremdwärme      [K/h]
    Regler: e = Soll − T, im Totband 0, um die Totzeit verzögert; u = Kp·e + I, I += Ki·e·Δt (0…100, Anti-Windup);
            das Ventil folgt u mit dem Stellmotor (Zeitkonstante öffnen/schließen). An/aus: ein ab e_on, aus ab e_off.

``simulate`` rechnet in 1-min-Schritten und gibt die Messpunkte im Takt ``step_s`` zurück (mit Messrauschen auf der
Raumtemperatur) und dazu die Wahrheit je Minute.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Thermostat:
    kp: float = 100.0          # %/K
    ki: float = 0.0            # %/(K·min)
    deadband: float = 0.1      # K
    dead_min: float = 4.0      # Totzeit
    tau_up_min: float = 6.0    # Stellmotor öffnen
    tau_down_min: float = 10.0  # Stellmotor schließen
    binary: bool = False
    e_on: float = 0.3          # an/aus: ein ab so viel unter Soll …
    e_off: float = -0.1        # … aus ab so viel über Soll (negativ)


@dataclass
class Room:
    a: float = 0.03            # 1/h je K Übertemperatur bei offenem Ventil
    b: float = 0.02            # Auskühlen 1/h
    flow: float = 50.0
    t_out: float = 5.0
    t0: float = 20.0


@dataclass
class Scenario:
    days: int = 6
    step_s: int = 300
    noise_k: float = 0.03
    seed: int = 1
    day: tuple = (6, 22)       # Tages-Soll in diesen Stunden, sonst Nacht
    day_sp: float = 21.0
    night_sp: float = 18.0
    extra_jumps: int = 3       # zusätzliche Soll-Sprünge je Tag (±0,5 / ±1 K für 1–2 h, zu zufälliger Minute)
    windows: list = field(default_factory=list)   # [(Minute ab Start, Dauer min)]: Fenster offen, Ventil zu
    ext: list = field(default_factory=list)       # [(Minute ab Start, Dauer min, K/h)]: Fremdwärme
    heating_off: list = field(default_factory=list)  # [(Minute ab Start, Dauer min)]: kein Heizwasser


def _in(spans, minute) -> tuple | None:
    return next((s for s in spans if s[0] <= minute < s[0] + s[1]), None)


def schedule(sc: Scenario, start_ts: int) -> list[float]:
    """Soll je Minute: Tag/Nacht plus zufällige Zusatzsprünge (Zeitplan, Boost, Hand)."""
    rng = random.Random(sc.seed)
    n = sc.days * 1440
    sp = []
    for m in range(n):
        h = ((start_ts // 60 + m) // 60) % 24
        sp.append(sc.day_sp if sc.day[0] <= h < sc.day[1] else sc.night_sp)
    for d in range(sc.days):
        for _ in range(sc.extra_jumps):
            m0 = d * 1440 + rng.randrange(sc.day[0] * 60 + 30, sc.day[1] * 60 - 150)
            delta = rng.choice((-1.0, -0.5, 0.5, 1.0))
            for m in range(m0, min(m0 + rng.randrange(60, 120), n)):
                sp[m] = sp[m] + delta
    return sp


def simulate(th: Thermostat, room: Room, sc: Scenario, start_ts: int) -> tuple[list[tuple], list[tuple]]:
    """(Messpunkte [(ts, temp, Soll, Ventil)], Wahrheit je Minute [(ts, temp, Soll, Ventil)])."""
    rng = random.Random(sc.seed + 1000)
    sps = schedule(sc, start_ts)
    t, v, i_term, on = room.t0, 0.0, 0.0, False
    hist: list[float] = []
    rows, truth = [], []
    dt_h = 1 / 60
    for m, sp in enumerate(sps):
        ts = start_ts + m * 60
        win = _in(sc.windows, m)
        e = sp - t
        hist.append(e)
        delay = round(th.dead_min)
        ed = hist[-1 - delay] if len(hist) > delay else hist[0]
        if th.binary:
            if ed >= th.e_on:
                on = True
            elif ed <= th.e_off:
                on = False
            v = 100.0 if on else 0.0
        else:
            edb = 0.0 if abs(ed) <= th.deadband else ed - math.copysign(th.deadband, ed)
            i_term = min(max(i_term + th.ki * edb, 0.0), 100.0)
            u = min(max(th.kp * edb + i_term, 0.0), 100.0)
            tau = th.tau_up_min if u > v else th.tau_down_min
            v += (u - v) * (1 - math.exp(-1 / tau))
        valve = 0.0 if win else v  # Fenster offen: das Thermostat schließt
        heat = 0.0 if _in(sc.heating_off, m) else room.a * (room.flow - t) * valve / 100
        rate = heat + room.b * (room.t_out - t)
        if win:
            rate += 3.0 * (room.t_out - t) / 16  # kräftig auskühlen (≈ 3 K/h)
        ext = _in(sc.ext, m)
        if ext:
            rate += ext[2]
        t += rate * dt_h
        truth.append((ts, t, sp, valve))
        if (ts - start_ts) % sc.step_s == 0:
            rows.append((ts, round(t + rng.gauss(0, sc.noise_k), 2), sp, round(valve, 1)))
    return rows, truth


def store_rows(store, climate: str, rows: list[tuple], heat_on: bool | None = True) -> None:
    """Messpunkte wie HeatPilot sie speichert (room_samples, dazu samples mit Betriebsart Tag)."""
    store.add_room_samples((ts, climate, temp, sp, valve, None) for ts, temp, sp, valve in rows)
    if heat_on is not None:
        for ts, *_ in rows:
            store.add_sample(ts, {"hc_day": 1 if heat_on else 0, "heating_off": 0})
    store.commit()
