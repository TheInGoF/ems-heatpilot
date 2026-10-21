"""Niemand zu Hause (seit 0.49, wie Tado): Soll nach Entfernung der nächsten Person, Tagesziel bei Abwesenheit.

* ``away_setpoint`` – je 10 km Entfernung der nächsten Person ``away_distance_pct`` % unter Komfort, nie unter der
  Abwesenheitstemperatur (21 °C und 10 %: in 10 km 18,9 °C). Ohne Entfernung (kein GPS) oder bei 0 % gleich die
  Abwesenheitstemperatur.
* ``nearest_km`` – Entfernung der nächsten Person mit GPS, erst nach ``AWAY_KM_STEP`` Änderung neu: sonst ließe die
  GPS-Schwankung an einer Stufe das Soll hin und her springen.
* ``nobody_home`` – Tagesziel für ``controlloop.desired``: Ankunft offen (Gewohnheit lernt noch) → Komfort; erwartete
  Ankunft innerhalb der Aufheizzeit → vorheizen, so früh, dass der Raum zur Ankunft Komfort − ``arrival_margin_k``
  erreicht (ein kaum ausgekühlter Raum heizt erst kurz vorher, nur ein stark ausgekühlter früh); sonst das Soll nach
  Entfernung.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

AWAY_KM_STEP = 2.0  # Entfernung fürs Abwesenheits-Soll erst nach so viel Änderung neu (GPS-Schwankung)


def away_setpoint(comfort: float, cfg, km: float | None) -> float:
    """Soll, wenn niemand zu Hause ist – siehe Modulbeschreibung (ungerundet; ``controlloop._clamp`` rundet)."""
    if km is None or cfg.away_distance_pct <= 0:
        return cfg.away_temp
    return max(comfort * (1 - cfg.away_distance_pct / 100 * km / 10), cfg.away_temp)


def nearest_km(persons, held: float | None) -> float | None:
    """Entfernung der nächsten Person, die weg ist und eine Entfernung hat; ``held`` (die bisherige) bleibt, solange sich
    die neue um weniger als ``AWAY_KM_STEP`` unterscheidet. None: niemand mit Entfernung."""
    km = min((p.dist_km for p in persons if p.home is False and p.dist_km is not None), default=None)
    return held if km is not None and held is not None and abs(km - held) < AWAY_KM_STEP else km


def nobody_home(comfort: float, cfg, presence, now: datetime,
                lead: Callable[[float, datetime], timedelta]) -> tuple[float, str, str, datetime | None]:
    """Tagesziel (Soll, Phase, Grund, nächster Wechsel), wenn niemand zu Hause ist – siehe Modulbeschreibung.
    ``lead(Ziel, Zeitpunkt)``: Aufheizzeit des Raums bis zum Ziel (``controlloop.desired``)."""
    eta, unknown, km = presence.arrival, presence.arrival_unknown, presence.away_km
    away, arrive = away_setpoint(comfort, cfg, km), comfort - cfg.arrival_margin_k
    near = "" if km is None or away <= cfg.away_temp else f", nächste Person {km:.0f} km"
    if unknown is not None and unknown <= now + lead(arrive, unknown):
        return comfort, "komfort", "Ankunft offen – Gewohnheit lernt noch", unknown
    if eta is not None and now >= eta - lead(arrive, eta):
        return comfort, "vorheizen", f"Ankunft erwartet {eta:%H:%M}", eta
    if eta is not None:
        return away, "abwesend", f"niemand zu Hause{near}, Ankunft {eta:%H:%M}", eta - lead(arrive, eta)
    return away, "abwesend", f"niemand zu Hause{near}", None
