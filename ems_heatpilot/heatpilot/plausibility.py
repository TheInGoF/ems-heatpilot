"""Plausibilität: was das Modell verwirrt, wird ignoriert.

Fehler passieren überall – in Tado, in EMS-ESP und in Home Assistant selbst. Drei
Schichten halten sie vom Lernen fern:

1. **Messwerte:** außerhalb physikalischer Grenzen (z. B. EMS-Platzhalter wie
   3276,7 °C) oder als einzelne Spitze (Raum springt in 5 min um mehr als 4 K und
   gleich wieder zurück) -> verworfen.
2. **Gaszähler je Stunde:**
   - *Nachtrag/Sprung:* mehr Gas, als der Brenner in dieser Stunde verbrennen
     konnte (Brennerlaufzeit × Leistung), oder – ohne Brennerdaten – ein Vielfaches
     der letzten Woche. Typisch: der Zählerstand wird von Hand nachgezogen, weil
     der Reed-Kontakt Umdrehungen verpasst hat. Die Stunde wird nicht gelernt; der
     Nachtrag zählt aber in Verbrauch und Kosten mit, denn das Gas wurde ja verbrannt.
   - *Verpasste Zählung:* der Brenner lief kräftig, der Zähler zählte (fast) nichts.
   - *Rückwärts:* Zählerstand sinkt (Korrektur nach unten, Tausch) -> ignoriert.
3. **Lernen (RLS):** Ein Messpunkt, dessen Abweichung weit über dem üblichen
   Fehler liegt, wird gekappt (zählt nur begrenzt) oder ganz verworfen – siehe
   ``rls.RLS``.
"""

from __future__ import annotations

import statistics

# Bereiche, außerhalb derer ein Wert ein Fehler ist
RANGES: dict[str, tuple[float, float]] = {
    "t_out": (-45.0, 50.0),
    "flow": (-10.0, 110.0), "sel_flow": (0.0, 110.0), "ret": (-10.0, 110.0),
    "boiler_temp": (-10.0, 110.0), "calc_flow": (0.0, 110.0), "hc_seltemp": (0.0, 40.0),
    "dhw_temp": (0.0, 95.0), "dhw_set": (0.0, 95.0), "remote_temp": (0.0, 40.0),
    "burner_pow": (0.0, 100.0), "pump_mod": (0.0, 100.0), "flame": (0.0, 200.0),
    "demand_pct": (0.0, 100.0), "min_period": (0.0, 240.0), "syspress": (0.0, 6.0),
    "room_temp": (0.0, 40.0), "room_target": (4.0, 30.0), "valve": (0.0, 100.0), "humidity": (0.0, 100.0),
}

ROOM_SPIKE_K = 4.0          # Sprung in einem Abtastschritt, der als Einzelspitze gilt
ROOM_SPIKE_WINDOW_S = 900
OUTDOOR_OUTLIER_K = 8.0     # Fühler so weit unter dem Median der anderen -> ignoriert

GAS_MARGIN = 1.3            # Spielraum über der aus Brennerlaufzeit möglichen Gasmenge
GAS_SLACK_KWH = 1.0
GAS_MISSED_MIN_KWH = 2.0    # ab so viel erwartetem Gas fällt eine fehlende Zählung auf
GAS_MISSED_SHARE = 0.2
DEFAULT_NOMINAL_KW = 30.0   # Nennleistung unbekannt: großzügig annehmen
GAS_SERIES_MIN_SHARE = 0.6  # ohne Brennerdaten: erst ab 60 % der Nennleistung in einer Stunde ein Sprung


def in_range(key: str, value: float | None) -> float | None:
    if value is None:
        return None
    lo, hi = RANGES.get(key, (float("-inf"), float("inf")))
    return value if lo <= value <= hi else None


class SpikeFilter:
    """Einzelne Ausreißer einer Zeitreihe verwerfen; bleibt der neue Wert, gilt er."""

    def __init__(self, max_jump: float = ROOM_SPIKE_K, window_s: int = ROOM_SPIKE_WINDOW_S) -> None:
        self.max_jump, self.window_s = max_jump, window_s
        self.last: dict[str, tuple[int, float]] = {}
        self.pending: dict[str, float] = {}

    def __call__(self, key: str, ts: int, value: float | None) -> float | None:
        if value is None:
            return None
        prev = self.last.get(key)
        if prev and ts - prev[0] <= self.window_s and abs(value - prev[1]) > self.max_jump:
            pend = self.pending.get(key)
            if pend is None or abs(value - pend) > 1.0:
                self.pending[key] = value
                return None  # erst einmal verwerfen – bestätigt der nächste Wert, gilt er
        self.pending.pop(key, None)
        self.last[key] = (ts, value)
        return value


def robust_outdoor(values: list[float]) -> list[float]:
    """Ab drei Fühlern: einer, der weit unter den anderen liegt, ist defekt (nicht kalt)."""
    if len(values) < 3:
        return values
    med = statistics.median(values)
    return [v for v in values if v >= med - OUTDOOR_OUTLIER_K]


def gas_hour(gas_kwh: float | None, rows: list, nominal_kw: float | None,
             recent: list[float] | None = None) -> tuple[str, float | None]:
    """Prüft den Gasverbrauch einer Stunde. Gibt (Zustand, kWh fürs Lernen).

    Zustand: ``ok``, ``spike`` (Nachtrag/Sprung), ``missed`` (Zähler zählte nicht),
    ``none`` (kein Wert). ``rows``: Abtastpunkte der Stunde mit burner_on/burner_pow.
    ``recent``: Stundenwerte der Vorwoche (Rückfall ohne Brennerdaten).
    """
    if gas_kwh is None:
        return "none", None
    if gas_kwh < 0:
        return "negative", None  # Zählerstand gesunken (Tausch, Korrektur) – nicht lernen, nicht zählen
    nom = nominal_kw or DEFAULT_NOMINAL_KW
    burner = [r for r in rows if r["burner_on"] is not None]
    if len(burner) >= 6:
        on = [r for r in burner if r["burner_on"]]
        frac = len(on) / len(burner)
        pows = [r["burner_pow"] for r in on if r["burner_pow"] is not None]
        load = (sum(pows) / len(pows) / 100) if pows else 1.0
        expected_max = frac * nom * GAS_MARGIN + GAS_SLACK_KWH
        if gas_kwh > expected_max:
            return "spike", None
        expected = frac * nom * (load if pows else 0.3)
        if expected >= GAS_MISSED_MIN_KWH and gas_kwh < GAS_MISSED_SHARE * expected:
            return "missed", None
        return "ok", gas_kwh
    # ohne Brennerdaten: physikalische Obergrenze und Vergleich mit der Vorwoche
    if gas_kwh > nom * GAS_MARGIN:
        return "spike", None
    ref = [v for v in (recent or []) if v is not None]
    if len(ref) >= 48:
        p95 = sorted(ref)[int(0.95 * (len(ref) - 1))]
        # Untergrenze nach Nennleistung: Legionellen- und Aufheizstunden nach ruhigen Tagen sind echt
        if gas_kwh > max(3 * p95, p95 + 6.0, 4.0, GAS_SERIES_MIN_SHARE * nom):
            return "spike", None
    return "ok", gas_kwh


def gas_series(hours: dict[int, float], nominal_kw: float | None) -> tuple[dict[int, float | None], dict[int, float]]:
    """Ganze Stundenreihe ohne Brennerdaten prüfen (Langzeitstatistik). Gibt (fürs Lernen, Sprünge)."""
    clean: dict[int, float | None] = {}
    spikes: dict[int, float] = {}
    window: list[float] = []
    for hour in sorted(hours):
        status, value = gas_hour(hours[hour], [], nominal_kw, window[-168:])
        clean[hour] = value
        if status == "spike":
            spikes[hour] = hours[hour]
        elif value is not None:
            window.append(value)
    return clean, spikes
