"""Lernende Wärmemodelle für Räume und das ganze Haus.

Raummodell (pro Tado-Raum, seit 0.53 in ``room_model``, hier weitergereicht): Aufheizen über den Heizkörper
(Vorlauf − Raum)^n · Ventil^0,5, Auskühlen, Fremdwärme, Sonne – siehe dort.

Hausmodell, gelernt jede Stunde aus dem Gasverbrauch:

    Gasleistung = H * (T_innen - T_außen) + C * dT_innen/dt + Grundlast

    H  Wärmeverlust des Hauses [kW/K]  -> Heizlast bei jeder Außentemperatur
    C  Wärmespeicher des Hauses [kWh/K] -> wie träge das Haus ist
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .rls import RLS
from .room_model import (  # noqa: F401 – weitergereicht, die Aufrufer importieren sie von hier
    EXCITATION_DECAY, EXT_LEARNED_P_FRAC, EXT_LEARNED_STEPS, FLOOR_MAX_H, ROOM_GATE, ROOM_LAM, ROOM_PRIOR, ROOM_STEP_H,
    RoomModel,
)

COOL_MIN_UPDATES = 30   # so viele gasfreie Nachtstunden, bevor die gemessene Auskühlzeit gilt
# Auskühlen in zwei Phasen: kurz nach dem Abschalten geben Luft und Heizkörper ihre Wärme schnell
# ab, danach kühlen die Wände langsam aus. Wie schnell die erste Phase ist, hängt von der Anlage
# ab (Heizkörper, Fußboden, Bauweise) – darum laufen mehrere Kandidaten mit, und der mit dem
# kleinsten Vorhersagefehler gilt. 1,5 h ist nur der Startwert, bis genug Stunden gelernt sind.
FAST_TAU_CANDIDATES = (0.5, 1.0, 1.5, 2.5, 4.0)
FAST_TAU_DEFAULT = 2      # Index von 1,5 h
COOL_SSE_DECAY = 0.995    # Fehlersumme für die Auswahl (~200 Stunden Gedächtnis)


def fast_decay(hours_off: float, tau_h: float = FAST_TAU_CANDIDATES[FAST_TAU_DEFAULT]) -> float:
    """Anteil der schnellen Auskühlphase in der Stunde ``hours_off`` nach dem Abschalten (Stundenmitte)."""
    return math.exp(-max(hours_off - 0.5, 0.0) / tau_h)


def _cool_rls() -> RLS:
    return RLS(n=2, lam=0.999, delta=1.0, theta=[0.03, 0.0], min_sigma=0.03)


# Wärmeverlust aus Tageswerten: Stunden zeigen vor allem, wie Luft und Heizkörper reagieren – die
# Wände nehmen Wärme über Tage auf und geben sie wieder ab. Tagesmittel mit dem Vortag und dem
# Mittel der Tage −2 bis −7 als weiteren Eingängen fangen das auf (H = Summe der drei
# Koeffizienten). Nur der Vortag reichte nicht: Bei Winterstart mit stark wechselndem Wetter
# (Tagesmittel ±6 K) lag H für träge Häuser 20–25 % zu niedrig, mit der Wochenwirkung sind es an
# künstlichen Häusern (Altbau bis Neubau, Fußboden, schwer, durchgeheizt, Oktober- und
# Dezemberstart) wenige Prozent. Das Stundenmodell lag bei trägen Häusern um bis zu 75 % daneben.
DAILY_LAM = 0.995          # Gedächtnis ~200 Tage – eine Heizsaison
DAILY_MIN_DAYS = 21        # so viele Heiztage, bevor die Tageswerte gelten …
DAILY_MIN_STD_K = 2.0      # … und so viel Streuung des Unterschieds innen–außen (sonst ist H nicht bestimmt)
DAILY_MIN_HOURS = 20       # Tag zählt nur mit so vielen Stunden
DAILY_HIST_DAYS = 7        # so viele Vortage als Zustand (Vortag + Mittel der Tage −2 bis −7)
DAILY_N = 4                # Eingänge: ΔT heute, ΔT Vortag, Mittel ΔT Tage −2…−7, Achsabschnitt


def _daily_rls() -> RLS:
    # Achsabschnitt mit großer Startunsicherheit: Im Winter (ΔT ≈ 20 K, wenig Streuung) ist er von H
    # kaum zu trennen, ein enger Startwert 0 zog ihn Richtung 0 und H bei Dezemberstart 5–10 % nach unten
    P = [[1.0 if i == j else 0.0 for j in range(DAILY_N)] for i in range(DAILY_N)]
    P[-1][-1] = 100.0
    return RLS(n=DAILY_N, lam=DAILY_LAM, theta=[0.1, 0.03, 0.02, 0.0], P=P, min_sigma=0.05)


@dataclass
class HouseModel:
    """Gasbilanz des Hauses: Gas = H·(innen − außen) + C·dT_innen/dt + Achsabschnitt.

    Der Achsabschnitt darf negativ sein: Fremdwärme (Sonne, Personen, Geräte) deckt einen
    Teil der Verluste, unterhalb der Heizgrenze braucht das Haus kein Gas. Positiv wäre er
    eine Grundlast. Die Trägheit kommt aus einem eigenen Auskühl-Modell (gasfreie Nächte):
    C/H aus der Bilanz beschreibt den Speicher der Heizkörper und Raumluft, nicht wie
    langsam die Wände auskühlen.
    """

    rls: RLS = field(
        default_factory=lambda: RLS(n=3, lam=0.999, delta=10.0, theta=[0.2, 2.0, 0.3], min_sigma=0.2)
    )
    # Tageswerte: Gas = H₀·ΔT(Tag) + H₁·ΔT(Vortag) + H₂·Mittel ΔT(Tage −2…−7) + Achsabschnitt,
    # dazu die letzten Tage [Datum, ΔT] (Lücken bleiben Lücken) und die Streuung von ΔT
    daily: RLS = field(default_factory=_daily_rls)
    daily_hist: list = field(default_factory=list)   # [[ISO-Datum, ΔT], …], höchstens DAILY_HIST_DAYS
    daily_dt_stats: list = field(default_factory=lambda: [0.0, 0.0, 0.0])  # Gewicht, Σ, Σ² (abklingend)
    # Auskühlen ohne Heizung: dT/h = −k·(innen − außen − e) − A·e^(−t/τ_schnell), t = Stunden seit dem
    # letzten Gas; e = Übertemperatur durch Fremdwärme, aus der Gasbilanz (Heizgrenze) – frei mitgeschätzt
    # kamen bei trägen Häusern unrealistische Werte heraus. τ = 1/k, schneller Abfall A·τ_schnell.
    # Ein Modell je Kandidat für τ_schnell, dazu deren Fehlersummen.
    cools: list = field(default_factory=lambda: [_cool_rls() for _ in FAST_TAU_CANDIDATES])
    cool_sse: list = field(default_factory=lambda: [0.0 for _ in FAST_TAU_CANDIDATES])
    cool_w: list = field(default_factory=lambda: [0.0 for _ in FAST_TAU_CANDIDATES])  # Gewicht dazu
    # nur in der Heizperiode lernen, sonst verzerrt der Sommer das Bild
    max_learn_outdoor: float = 15.0

    @property
    def balance_plausible(self) -> bool:
        """Gasbilanz in den Grenzen der Leitplanken (guards.LIMITS house_*)."""
        from .guards import LIMITS
        if self.rls.updates < LIMITS["house_min_updates"] and not self.daily_valid:
            return False
        lo, hi = LIMITS["house_h_w_per_k"]
        olo, ohi = LIMITS["house_offset_kw"]
        return lo <= self.h_kw_per_k * 1000 <= hi and olo <= self.offset_kw <= ohi

    @property
    def daily_valid(self) -> bool:
        """Gelten die Tageswerte? Genug Heiztage mit genug Unterschied in der Witterung."""
        w, s, s2 = self.daily_dt_stats
        if self.daily.updates < DAILY_MIN_DAYS or w <= 0:
            return False
        std = math.sqrt(max(s2 / w - (s / w) ** 2, 0.0))
        return std >= DAILY_MIN_STD_K and self.daily_h > 1e-3

    @property
    def daily_h(self) -> float:
        """Wärmeverlust aus den Tageswerten: Summe der Koeffizienten (Beharrung = alle Tage gleich)."""
        return sum(self.daily.theta[:-1])

    @property
    def h_kw_per_k(self) -> float:
        if self.daily_valid:
            return max(self.daily_h, 0.0)
        return max(self.rls.theta[0], 0.0)

    @property
    def c_kwh_per_k(self) -> float:
        return max(self.rls.theta[1], 0.0)

    @property
    def offset_kw(self) -> float:
        """Achsabschnitt der Gasbilanz (negativ = Fremdwärme, positiv = Grundlast)."""
        return self.daily.theta[-1] if self.daily_valid else self.rls.theta[2]

    @property
    def base_kw(self) -> float:
        return max(self.offset_kw, 0.0)

    @property
    def free_heat_kw(self) -> float:
        return max(-self.offset_kw, 0.0)

    @property
    def heating_limit_k(self) -> float | None:
        """Ab so viel Kelvin innen–außen braucht das Haus Gas (Heizgrenze)."""
        if self.h_kw_per_k <= 1e-3:
            return None
        return -self.offset_kw / self.h_kw_per_k

    @property
    def cool_index(self) -> int:
        """Bester Kandidat für die schnelle Phase (kleinster mittlerer Fehler), vorher der Startwert."""
        ok = [i for i, w in enumerate(self.cool_w) if w >= COOL_MIN_UPDATES * 0.9]
        if not ok:
            return FAST_TAU_DEFAULT
        return min(ok, key=lambda i: (self.cool_sse[i] / self.cool_w[i], i != FAST_TAU_DEFAULT))

    @property
    def cool(self) -> RLS:
        return self.cools[self.cool_index]

    @property
    def fast_tau_h(self) -> float:
        return FAST_TAU_CANDIDATES[self.cool_index]

    @property
    def tau_cool_h(self) -> float | None:
        k = self.cool.theta[0]
        if self.cool.updates < COOL_MIN_UPDATES or k <= 1e-3:
            return None
        return 1.0 / k

    @property
    def cool_excess_k(self) -> float:
        """Wie viel wärmer als außen das Haus ohne Heizung bliebe (Fremdwärme), höchstens 10 K.

        Aus der Gasbilanz: bis zur Heizgrenze deckt die Fremdwärme die Verluste.
        """
        h = self.h_kw_per_k
        return min(self.free_heat_kw / h, 10.0) if h > 1e-3 else 0.0

    @property
    def cool_fast_k(self) -> float:
        """Zusätzlicher schneller Abfall nach dem Abschalten (K, Luft und Heizkörper), nie negativ."""
        if self.tau_cool_h is None:
            return 0.0
        return min(max(self.cool.theta[1], 0.0) * self.fast_tau_h, 5.0)

    def fast_drop(self, hours_off: float) -> float:
        """Schneller Abfall (K) in der Stunde ``hours_off`` → ``hours_off + 1`` nach dem Abschalten."""
        a = self.cool_fast_k
        if a <= 0:
            return 0.0
        tf = self.fast_tau_h
        return a * (math.exp(-hours_off / tf) - math.exp(-(hours_off + 1) / tf))

    @property
    def tau_balance_h(self) -> float | None:
        if self.h_kw_per_k <= 1e-3:
            return None
        return self.c_kwh_per_k / self.h_kw_per_k

    @property
    def tau_h(self) -> float | None:
        """Trägheit (Auskühlen): gemessen in gasfreien Nächten, sonst aus der Gasbilanz."""
        return self.tau_cool_h if self.tau_cool_h is not None else self.tau_balance_h

    def learn_day(self, day: str, dt_mean: float, t_out_mean: float, gas_kw_mean: float,
                  learn: bool = True) -> float | None:
        """Ein vollständiger Tag (Mittelwerte): lernt mit dem Vortag und der Woche davor als Eingängen.

        ``day`` als ISO-Datum. Der Vortag muss vorliegen, von den Tagen −2 bis −7 mindestens einer
        (Lücken werden übersprungen, das Mittel bildet sich aus den vorhandenen Tagen). Ein schon
        gemerkter Tag wird nicht noch einmal gelernt. ``learn=False`` merkt nur den Zustand
        (z. B. Urlaubstag: der Unterschied innen–außen zählt für die Folgetage, das Gas nicht).
        """
        d = date.fromisoformat(day)
        if self.daily_hist and date.fromisoformat(self.daily_hist[-1][0]) >= d:
            return None
        age = {(d - date.fromisoformat(h[0])).days: h[1] for h in self.daily_hist}
        self.daily_hist = [h for h in self.daily_hist if 0 < (d - date.fromisoformat(h[0])).days < DAILY_HIST_DAYS]
        self.daily_hist.append([day, dt_mean])
        week = [age[k] for k in range(2, DAILY_HIST_DAYS + 1) if k in age]
        if not learn or t_out_mean > self.max_learn_outdoor or gas_kw_mean < 0 or 1 not in age or not week:
            return None
        w, s, s2 = (v * DAILY_LAM for v in self.daily_dt_stats)
        self.daily_dt_stats = [w + 1.0, s + dt_mean, s2 + dt_mean * dt_mean]
        return self.daily.update([dt_mean, age[1], sum(week) / len(week), 1.0], gas_kw_mean)

    def learn(self, gas_kw: float, t_in: float, t_out: float, dt_in_per_h: float) -> float | None:
        if t_out > self.max_learn_outdoor or gas_kw < 0:
            return None
        return self.rls.update([t_in - t_out, dt_in_per_h, 1.0], gas_kw)

    def learn_cooling(self, t_in: float, t_out: float, dt_in_per_h: float, hours_off: float = 99.0) -> float | None:
        """Eine gasfreie Stunde: wie schnell kühlt das Haus bei diesem Unterschied aus?

        ``hours_off``: Stunden seit der letzten Stunde mit Gas (für die schnelle Phase).
        """
        if t_out > self.max_learn_outdoor or t_in - t_out < 3 or abs(dt_in_per_h) > 1.0:
            return None
        if not self.balance_plausible:
            return None  # Übertemperatur kommt aus der Gasbilanz – erst lernen, wenn sie stimmt
        err = None
        excess = self.cool_excess_k
        for i, (tf, r) in enumerate(zip(FAST_TAU_CANDIDATES, self.cools)):
            e = r.update([-(t_in - t_out - excess), -fast_decay(hours_off, tf)], dt_in_per_h)
            # Fehler vor dem Lernen, gedeckelt wie im RLS (ein Ausreißer entscheidet nicht die Wahl)
            sigma = max(r.rmse or 0.0, r.min_sigma)
            self.cool_sse[i] = COOL_SSE_DECAY * self.cool_sse[i] + min(e * e, (4 * sigma) ** 2)
            self.cool_w[i] = COOL_SSE_DECAY * self.cool_w[i] + 1.0
            if i == self.cool_index:
                err = e
        return err

    def demand_kw(self, t_in: float, t_out: float) -> float:
        """Benötigte Gasleistung, um t_in bei t_out zu halten (Fremdwärme abgezogen)."""
        return max(self.h_kw_per_k * (t_in - t_out) + self.offset_kw, 0.0)

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
            "offset_kw": round(self.offset_kw, 2),
            "free_heat_kw": round(self.free_heat_kw, 2),
            "heating_limit_k": None if self.heating_limit_k is None else round(self.heating_limit_k, 1),
            "tau_h": None if self.tau_h is None else round(self.tau_h, 1),
            "tau_source": "abkuehlung" if self.tau_cool_h is not None else "bilanz",
            "balance_source": "tage" if self.daily_valid else "stunden",
            "daily_days": self.daily.updates,
            "cool_updates": self.cool.updates,
            "cool_fast_k": round(self.cool_fast_k, 2),
            "cool_fast_tau_h": self.fast_tau_h,
            "design_load_kw": round(self.demand_kw(20.0, design_outdoor), 2),
            "demand_now_kw": now,
        }

    def to_dict(self) -> dict:
        return {"rls": self.rls.to_dict(), "cools": [r.to_dict() for r in self.cools],
                "cool_sse": self.cool_sse, "cool_w": self.cool_w,
                "daily": self.daily.to_dict(), "daily_hist": self.daily_hist, "daily_dt_stats": self.daily_dt_stats}

    @classmethod
    def from_dict(cls, d: dict) -> "HouseModel":
        m = cls(rls=RLS.from_dict(d["rls"]))
        cools = d.get("cools") or []
        if len(cools) == len(FAST_TAU_CANDIDATES) and all(c["n"] == 2 for c in cools):
            m.cools = [RLS.from_dict(c) for c in cools]
            m.cool_sse = list(d.get("cool_sse") or m.cool_sse)
            m.cool_w = list(d.get("cool_w") or m.cool_w)
        # ältere Auskühl-Modelle (mit frei geschätzter Übertemperatur) passen nicht – neu lernen
        # Tagesmodelle von vor 0.32 (nur ein Vortag) passen nicht – neu lernen
        if d.get("daily") and d["daily"].get("n") == DAILY_N:
            m.daily = RLS.from_dict(d["daily"])
            m.daily_hist = list(d.get("daily_hist") or [])
            m.daily_dt_stats = list(d.get("daily_dt_stats") or m.daily_dt_stats)
        return m
