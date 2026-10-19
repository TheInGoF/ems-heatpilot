"""Ventil vorhersagen (seit 0.39): wie sich das Ventil eines Thermostats in den nächsten Minuten bewegt.

Aus dem gelernten Ventilmodell (``valve_model``) baut ``ValveModel.predictor`` einen der drei Vorhersager; alle haben
denselben Ablauf: ``start(Ventil, Regelabweichung)`` gibt den Zustand, ``step(Zustand, e, Δt in min)`` rechnet einen
Schritt weiter und gibt das Ventil zurück. e ist dabei schon um die Totzeit verzögert (``Delay``).

* **an/aus** (``Binary``): ein ab ``e_on``, aus ab ``e_off``, dazwischen bleibt es (Hysterese).
* **P-Regler** (``Curve``, Nachstellzeit lang oder kein Integrator): Ziel ist die Kennlinie im Beharren bei e – sie
  ist bei einem P-Regler eine Eigenschaft des Geräts (mit Totband und Sättigung) –, das Ventil folgt mit dem
  Stellmotor (Zeitkonstante aus den Sprungantworten, auf und zu getrennt).
* **PI-Regler** (``PI``): v = Kp·e + I, I = I + Ki·(e − e_i)·Δt, beides auf 0…100 % begrenzt (Anti-Windup), das Ventil
  folgt mit dem Stellmotor. Den Integrator kennt niemand – er wird aus der Stellung geschätzt: zu → abgebaut (0),
  ganz offen → der kleinste Wert, der dazu passt, sonst Ventil − Kp·e.

Schreibt nichts, rechnet nur.
"""

from __future__ import annotations

import math
from collections import deque

OPEN_PCT = 5.0              # darüber gilt das Ventil als offen (Öffnungsbeginn, Wärmeanforderung)
SAT_LO, SAT_HI = 1.0, 99.0  # Ventil dazwischen: nicht gesättigt


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return lo if v < lo else min(v, hi)


class Binary:
    """Nur an/aus: Schaltpunkte mit Hysterese."""

    kind = "an/aus"

    def __init__(self, e_on: float, e_off: float) -> None:
        self.e_on, self.e_off = e_on, e_off

    def start(self, v: float, e: float) -> list:
        return [100.0 if v > OPEN_PCT else 0.0]

    def step(self, s: list, e: float, dt: float) -> float:
        s[0] = 100.0 if e >= self.e_on else 0.0 if e <= self.e_off else s[0]
        return s[0]


class _Lag:
    """Stellmotor: das Ventil folgt der Stellgröße mit einer Zeitkonstante (min), öffnen und schließen getrennt."""

    def __init__(self, tau_up: float, tau_down: float) -> None:
        self.tau_up, self.tau_down = max(tau_up, 0.0), max(tau_down, 0.0)

    def follow(self, v: float, u: float, dt: float) -> float:
        tau = self.tau_up if u > v else self.tau_down
        return u if tau <= 0 else u + (v - u) * math.exp(-dt / tau)


class Curve(_Lag):
    """P-Regler: Ziel ist die Kennlinie im Beharren (linear zwischen den Klassen, außerhalb die Randwerte)."""

    kind = "P"

    def __init__(self, points: list[tuple[float, float]], tau_up: float, tau_down: float) -> None:
        super().__init__(tau_up, tau_down)
        self.pts = sorted(points)

    def at(self, e: float) -> float:
        pts = self.pts
        if e <= pts[0][0]:
            return pts[0][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if e <= x1:
                return y0 + (y1 - y0) * (e - x0) / (x1 - x0) if x1 > x0 else y1
        return pts[-1][1]

    def start(self, v: float, e: float) -> list:
        return [v]

    def step(self, s: list, e: float, dt: float) -> float:
        s[0] = self.follow(s[0], self.at(e), dt)
        return s[0]


class PI(_Lag):
    """PI-Regler mit Sättigung und Anti-Windup; Zustand [Ventil, Integrator]."""

    kind = "PI"

    def __init__(self, kp: float, ki: float, e_i: float, tau_up: float, tau_down: float) -> None:
        super().__init__(tau_up, tau_down)
        self.kp, self.ki, self.e_i = kp, ki, e_i

    def start(self, v: float, e: float) -> list:
        if v <= SAT_LO:
            i = 0.0  # zu: der Integrator ist abgebaut – Ventil − Kp·e hieße „öffnet gleich“, sobald e steigt
        elif v >= SAT_HI:
            i = clamp(100.0 - self.kp * e)  # ganz offen: der kleinste Integrator, der dazu passt
        else:
            i = clamp(v - self.kp * e)
        return [v, i]

    def step(self, s: list, e: float, dt: float) -> float:
        s[1] = clamp(s[1] + self.ki * (e - self.e_i) * dt)
        s[0] = self.follow(s[0], clamp(self.kp * e + s[1]), dt)
        return s[0]


class Delay:
    """Totzeit in ganzen Minuten: ``push(e)`` gibt die Regelabweichung von vor ``dead_min`` Minuten zurück (am Anfang
    die erste)."""

    def __init__(self, dead_min: float, e0: float) -> None:
        n = max(round(dead_min), 0)
        self.buf = deque([e0] * n, maxlen=n) if n else None

    def push(self, e: float) -> float:
        if self.buf is None:
            return e
        old = self.buf[0]
        self.buf.append(e)
        return old


def path(pred, v0: float, es: list[float], dead_min: float) -> list[float]:
    """Ventil je Minute, wenn die (unverzögerte) Regelabweichung minütlich ``es`` ist; ``es[0]`` ist jetzt."""
    delay = Delay(dead_min, es[0])
    s, out = pred.start(v0, es[0]), []
    for e in es[1:]:
        out.append(pred.step(s, delay.push(e), 1.0))
    return out
