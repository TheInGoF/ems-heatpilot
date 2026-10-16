"""Lernende Wärmemodelle für Räume und das ganze Haus.

Raummodell (pro Tado-Raum), gelernt alle 30 Minuten:

    dT/dt = a * Heizleistung + b * (T_außen - T_raum) + c

    Heizleistung = max(T_vorlauf - T_raum, 0) * Ventilöffnung * Heizbetrieb
    a  wie stark der Heizkörper den Raum erwärmt   [1/h]
    b  wie schnell der Raum auskühlt (1/b = Zeitkonstante in h)
    c  Fremdwärme (Sonne, Personen, Nachbarräume) [K/h]

Hausmodell, gelernt jede Stunde aus dem Gasverbrauch:

    Gasleistung = H * (T_innen - T_außen) + C * dT_innen/dt + Grundlast

    H  Wärmeverlust des Hauses [kW/K]  -> Heizlast bei jeder Außentemperatur
    C  Wärmespeicher des Hauses [kWh/K] -> wie träge das Haus ist
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .rls import RLS

ROOM_STEP_H = 0.5


@dataclass
class RoomModel:
    """Raummodell. Mit ``floor=True`` zusätzlich Fußbodenheizung ohne Stellventil.

    Fußbodenheizung (z. B. mit fest eingestelltem RTL-Ventil) heizt immer, wenn
    warmes Heizwasser fließt – unabhängig von Tado – und wirkt über den Estrich
    stark verzögert. Ihr Eingang wird deshalb mit ``floor_lag_h`` geglättet:

        dT/dt = a * Heizkörper + f * Fußboden(verzögert) + b * (T_außen - T) + c
    """

    room: str
    floor: bool = False
    floor_lag_h: float = 3.0
    floor_u: float = 0.0  # geglätteter Fußboden-Eingang (K über Raumtemperatur)
    rls: RLS | None = None

    def __post_init__(self) -> None:
        if self.rls is None:
            theta = [0.02, 0.01, 0.05, 0.0] if self.floor else [0.02, 0.05, 0.0]
            self.rls = RLS(n=len(theta), lam=0.998, delta=1.0, theta=theta, min_sigma=0.03)

    @property
    def a(self) -> float:
        return max(self.rls.theta[0], 1e-4)

    @property
    def f(self) -> float:
        """Wirkung der Fußbodenheizung (0 ohne Fußbodenheizung)."""
        return max(self.rls.theta[1], 0.0) if self.floor else 0.0

    @property
    def b(self) -> float:
        return max(self.rls.theta[2 if self.floor else 1], 1e-3)

    @property
    def c(self) -> float:
        return self.rls.theta[3 if self.floor else 2]

    @property
    def c_eff(self) -> float:
        """Fremdwärme plus aktuelle (träge) Fußbodenwärme – kurzfristig quasi konstant."""
        return self.c + self.f * self.floor_u

    @property
    def tau_h(self) -> float:
        return 1.0 / self.b

    def learn(
        self,
        t_start: float,
        t_end: float,
        heat_input: float,
        t_out: float,
        dt_h: float = ROOM_STEP_H,
        floor_input: float = 0.0,
        update: bool = True,
    ) -> float:
        """Lernschritt. ``update=False`` führt nur den Estrich-Speicher nach (z. B. im Urlaub)."""
        t_mean = (t_start + t_end) / 2
        if self.floor:
            self.floor_u += (1 - math.exp(-dt_h / self.floor_lag_h)) * (floor_input - self.floor_u)
            x = [heat_input * dt_h, self.floor_u * dt_h, (t_out - t_mean) * dt_h, dt_h]
        else:
            x = [heat_input * dt_h, (t_out - t_mean) * dt_h, dt_h]
        if not update:
            return 0.0
        return self.rls.update(x, t_end - t_start)

    def rate(self, t_room: float, flow: float, t_out: float, valve: float = 1.0) -> float:
        """Temperaturänderung in K/h bei gegebener Vorlauftemperatur."""
        u = max(flow - t_room, 0.0) * valve
        return self.a * u + self.b * (t_out - t_room) + self.c_eff

    def equilibrium(self, flow: float, t_out: float, valve: float = 1.0) -> float:
        """Endtemperatur bei Dauerbetrieb (Fußboden als konstant angenommen – kurzfristig)."""
        a = self.a * valve
        return (a * flow + self.b * t_out + self.c_eff) / (a + self.b)

    def time_to_reach(
        self, t_now: float, target: float, flow: float, t_out: float, valve: float = 1.0
    ) -> float | None:
        """Stunden bis zum Ziel, None wenn es mit diesem Vorlauf nicht erreichbar ist."""
        if t_now >= target:
            return 0.0
        t_inf = self.equilibrium(flow, t_out, valve)
        if t_inf <= target + 0.05:
            return None
        k = self.a * valve + self.b
        return -math.log((t_inf - target) / (t_inf - t_now)) / k

    def required_flow(self, target: float, t_out: float, valve: float = 1.0) -> float:
        """Vorlauftemperatur, die die Zieltemperatur dauerhaft hält (Fußboden heizt mit)."""
        need = self.b * (target - t_out) - self.c
        return target + max(need, 0.0) / (self.a * valve + self.f)

    def cool_down(self, t_now: float, t_out: float, hours: float) -> float:
        """Raumtemperatur nach ``hours`` Stunden ohne Heizung."""
        t_inf = t_out + self.c / self.b
        return t_inf + (t_now - t_inf) * math.exp(-self.b * hours)

    def summary(self, t_out: float, max_flow: float) -> dict:
        return {
            "room": self.room,
            "floor": self.floor,
            "floor_gain": round(self.f, 4) if self.floor else None,
            "updates": self.rls.updates,
            "rmse_k": self.rls.rmse,
            "tau_h": round(self.tau_h, 1),
            "heat_gain": round(self.a, 4),
            "free_gain_k_h": round(self.c, 3),
            "hours_plus_2k_max_flow": self.time_to_reach(19.0, 21.0, max_flow, t_out),
            "flow_to_hold_21": round(self.required_flow(21.0, t_out), 1),
        }

    def to_dict(self) -> dict:
        return {
            "room": self.room,
            "floor": self.floor,
            "floor_lag_h": self.floor_lag_h,
            "floor_u": self.floor_u,
            "rls": self.rls.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RoomModel":
        return cls(
            room=d["room"],
            floor=d.get("floor", False),
            floor_lag_h=d.get("floor_lag_h", 3.0),
            floor_u=d.get("floor_u", 0.0),
            rls=RLS.from_dict(d["rls"]),
        )


@dataclass
class HouseModel:
    rls: RLS = field(
        default_factory=lambda: RLS(n=3, lam=0.999, delta=10.0, theta=[0.2, 2.0, 0.3], min_sigma=0.2)
    )
    # nur in der Heizperiode lernen, sonst verzerrt der Sommer das Bild
    max_learn_outdoor: float = 15.0

    @property
    def h_kw_per_k(self) -> float:
        return max(self.rls.theta[0], 0.0)

    @property
    def c_kwh_per_k(self) -> float:
        return max(self.rls.theta[1], 0.0)

    @property
    def base_kw(self) -> float:
        return max(self.rls.theta[2], 0.0)

    @property
    def tau_h(self) -> float | None:
        if self.h_kw_per_k <= 1e-3:
            return None
        return self.c_kwh_per_k / self.h_kw_per_k

    def learn(self, gas_kw: float, t_in: float, t_out: float, dt_in_per_h: float) -> float | None:
        if t_out > self.max_learn_outdoor or gas_kw < 0:
            return None
        return self.rls.update([t_in - t_out, dt_in_per_h, 1.0], gas_kw)

    def demand_kw(self, t_in: float, t_out: float) -> float:
        """Benötigte Gasleistung, um t_in bei t_out zu halten."""
        return max(self.h_kw_per_k * (t_in - t_out), 0.0) + self.base_kw

    def summary(self, design_outdoor: float, t_out_now: float | None, t_in_now: float | None) -> dict:
        now = None
        if t_out_now is not None and t_in_now is not None:
            now = round(self.demand_kw(t_in_now, t_out_now), 2)
        return {
            "updates": self.rls.updates,
            "rmse_kw": self.rls.rmse,
            "heat_loss_w_per_k": round(self.h_kw_per_k * 1000, 0),
            "storage_kwh_per_k": round(self.c_kwh_per_k, 2),
            "base_kw": round(self.base_kw, 2),
            "tau_h": None if self.tau_h is None else round(self.tau_h, 1),
            "design_load_kw": round(self.demand_kw(20.0, design_outdoor), 2),
            "demand_now_kw": now,
        }

    def to_dict(self) -> dict:
        return {"rls": self.rls.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "HouseModel":
        return cls(rls=RLS.from_dict(d["rls"]))
