"""Rekursive kleinste Quadrate (RLS) mit Vergessensfaktor.

Das ist der Lernkern: Jede neue Messung verschiebt die Parameter ein Stück,
alte Messungen verlieren mit dem Faktor ``lam`` exponentiell an Gewicht.
Damit passt sich das Modell laufend an (Jahreszeit, Sonne, neue Fenster ...).
Bewusst ohne numpy, die Matrizen sind winzig (3-4 Parameter).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class RLS:
    n: int
    lam: float = 0.998
    delta: float = 100.0
    theta: list[float] = field(default_factory=list)
    P: list[list[float]] = field(default_factory=list)
    updates: int = 0
    # gleitender Fehler der Vorhersage *vor* dem Update (ehrliches Gütemaß)
    err_sq_ewma: float | None = None

    def __post_init__(self) -> None:
        if not self.theta:
            self.theta = [0.0] * self.n
        if not self.P:
            self.P = [
                [self.delta if i == j else 0.0 for j in range(self.n)]
                for i in range(self.n)
            ]

    def predict(self, x: list[float]) -> float:
        return sum(t * xi for t, xi in zip(self.theta, x))

    def update(self, x: list[float], y: float) -> float:
        """Lernt aus einem Messpunkt, gibt den Vorhersagefehler zurück."""
        n = self.n
        Px = [sum(self.P[i][j] * x[j] for j in range(n)) for i in range(n)]
        denom = self.lam + sum(x[i] * Px[i] for i in range(n))
        if denom <= 1e-12 or not math.isfinite(denom):
            return 0.0
        k = [v / denom for v in Px]
        err = y - self.predict(x)
        self.theta = [self.theta[i] + k[i] * err for i in range(n)]
        xP = [sum(x[i] * self.P[i][j] for i in range(n)) for j in range(n)]
        self.P = [
            [(self.P[i][j] - k[i] * xP[j]) / self.lam for j in range(n)]
            for i in range(n)
        ]
        # Kovarianz begrenzen, sonst explodiert sie bei wenig Anregung
        for i in range(n):
            if self.P[i][i] > 1e6:
                scale = 1e6 / self.P[i][i]
                self.P[i] = [v * scale for v in self.P[i]]
        self.updates += 1
        a = 0.02
        self.err_sq_ewma = (
            err * err
            if self.err_sq_ewma is None
            else (1 - a) * self.err_sq_ewma + a * err * err
        )
        return err

    @property
    def rmse(self) -> float | None:
        return None if self.err_sq_ewma is None else math.sqrt(self.err_sq_ewma)

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "lam": self.lam,
            "theta": self.theta,
            "P": self.P,
            "updates": self.updates,
            "err_sq_ewma": self.err_sq_ewma,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RLS":
        return cls(
            n=d["n"],
            lam=d["lam"],
            theta=d["theta"],
            P=d["P"],
            updates=d.get("updates", 0),
            err_sq_ewma=d.get("err_sq_ewma"),
        )
