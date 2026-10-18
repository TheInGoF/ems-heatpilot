"""Lernende Wärmemodelle für Räume und das ganze Haus.

Raummodell (pro Tado-Raum), gelernt alle 30 Minuten:

    dT/dt = a * Heizleistung + b * (T_außen - T_raum) + c

    Heizleistung = max(T_vorlauf - T_raum, 0) * Ventilöffnung * Heizbetrieb
    a  wie stark der Heizkörper den Raum erwärmt   [1/h]
    b  wie schnell der Raum auskühlt (1/b = Zeitkonstante in h)
    c  Fremdwärme (Personen, Nachbarräume) [K/h]
    + g_Ost/Süd/West * Einstrahlung: Sonnenwärme durch die Fenster je Himmelsrichtung

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

ROOM_STEP_H = 0.5
FLOOR_MAX_H = 48   # länger rechnet der Fußboden-Aufheizlauf nicht
# Raum-Lernen: Gedächtnis ~6 Wochen – die Auskühlrate ist eine Gebäudegröße und im Herbst
# (kleiner Abstand innen–außen) nur langsam von der Fremdwärme zu trennen
ROOM_LAM = 0.9995
ROOM_GATE = 6.0          # Ausreißer-Kappung (× erwarteter Fehler, siehe rls.py)
EXCITATION_DECAY = 0.998  # „gesehener Heizbetrieb“ klingt unabhängig vom Lern-Gedächtnis ab
# Startwerte und Startunsicherheit je Parameter (Wurzel der P-Diagonale, wie das RLS sie führt –
# relativ zum Messfehler): Heizwirkung a, Fußboden f, Auskühlen b (≈ 1/55 h wie ein typisches
# Haus), Fremdwärme c, Sonne Ost/Süd/West. Mit gleicher Unsicherheit für alles verschob ein
# einziger Schritt a um das Zehnfache; an echten Daten nachgerechnet (Export 10/2026).
ROOM_PRIOR = {"a": (0.02, 0.05), "f": (0.01, 0.02), "b": (0.018, 0.03), "c": (0.0, 0.3), "sun": (0.0, 1.0)}


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
    # wie viel Heizbetrieb das Modell gesehen hat (K·h, klingt langsam ab)
    excitation: float = 0.0

    def __post_init__(self) -> None:
        if self.rls is None:
            keys = ["a"] + (["f"] if self.floor else []) + ["b", "c"] + ["sun"] * 3
            theta = [ROOM_PRIOR[k][0] for k in keys]
            P = [[ROOM_PRIOR[k][1] ** 2 if i == j else 0.0 for j in range(len(keys))] for i, k in enumerate(keys)]
            self.rls = RLS(n=len(keys), lam=ROOM_LAM, theta=theta, P=P, min_sigma=0.03)
        elif self.rls.n == self._base_n:
            self.rls.extend(3)  # Modell von vor 0.20: Sonnen-Parameter ergänzen
        self.rls.gate = ROOM_GATE

    def adopt_house_tau(self, tau_h: float | None) -> None:
        """Startwert fürs Auskühlen aus der gemessenen Trägheit des eigenen Hauses (nur vor dem ersten Lernen)."""
        if tau_h and self.rls.updates == 0:
            self.rls.theta[2 if self.floor else 1] = min(max(1.0 / tau_h, 1 / 250), 1 / 15)

    @property
    def _base_n(self) -> int:
        return 4 if self.floor else 3

    @property
    def solar(self) -> tuple[float, float, float]:
        """Sonnenwärme Ost/Süd/West in K/h bei voller Sonne (nie negativ)."""
        th = self.rls.theta[self._base_n:self._base_n + 3]
        return tuple(round(max(v, 0.0), 3) for v in th) if len(th) == 3 else (0.0, 0.0, 0.0)

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
        solar: tuple[float, float, float] | None = None,
    ) -> float:
        """Lernschritt. ``update=False`` führt nur den Estrich-Speicher nach (z. B. im Urlaub)."""
        t_mean = (t_start + t_end) / 2
        if self.floor:
            self.floor_u += (1 - math.exp(-dt_h / self.floor_lag_h)) * (floor_input - self.floor_u)
            x = [heat_input * dt_h, self.floor_u * dt_h, (t_out - t_mean) * dt_h, dt_h]
        else:
            x = [heat_input * dt_h, (t_out - t_mean) * dt_h, dt_h]
        x += [v * dt_h for v in (solar or (0.0, 0.0, 0.0))]
        if not update:
            return 0.0
        idle = [i for i, v in enumerate(x) if abs(v) < 1e-6]
        if self.floor and abs(x[1]) < 0.05 * dt_h:
            idle.append(1)
        err = self.rls.update(x, t_end - t_start, idle=idle)
        self.excitation = EXCITATION_DECAY * self.excitation + x[0]
        self._sun_not_negative()
        return err

    def _sun_not_negative(self) -> None:
        """Sonne kann nicht kühlen: negative Sonnenwerte auf 0 projizieren (P-gewichtet).

        Die zusammenhängenden Parameter (Fremdwärme, Auskühlen, andere Richtungen) werden
        dabei optimal nachgeführt; P bleibt, damit der Wert später wieder wachsen kann.
        """
        th, P = self.rls.theta, self.rls.P
        for i in range(self._base_n, self._base_n + 3):
            if i < len(th) and th[i] < 0 and P[i][i] > 1e-12:
                ti = th[i]
                self.rls.theta = th = [th[j] - P[j][i] / P[i][i] * ti for j in range(len(th))]
                th[i] = 0.0

    def rate(self, t_room: float, flow: float, t_out: float, valve: float = 1.0) -> float:
        """Temperaturänderung in K/h bei gegebener Vorlauftemperatur."""
        u = max(flow - t_room, 0.0) * valve
        return self.a * u + self.b * (t_out - t_room) + self.c_eff

    def equilibrium(self, flow: float, t_out: float, valve: float = 1.0) -> float:
        """Endtemperatur bei Dauerbetrieb – die Fußbodenheizung heizt dann mit (wie in required_flow)."""
        a = self.a * valve + self.f
        return (a * flow + self.b * t_out + self.c) / (a + self.b)

    def time_to_reach(
        self, t_now: float, target: float, flow: float, t_out: float, valve: float = 1.0
    ) -> float | None:
        """Stunden bis zum Ziel, None wenn es mit diesem Vorlauf nicht erreichbar ist."""
        if t_now >= target:
            return 0.0
        t_inf = self.equilibrium(flow, t_out, valve)
        if t_inf <= target + 0.05:
            return None
        if self.floor:
            # Estrich wärmt verzögert: in 6-min-Schritten nachrechnen, ab dem heutigen Estrich-Zustand
            step, t, u = 0.1, t_now, self.floor_u
            keep = 1 - math.exp(-step / self.floor_lag_h)
            for i in range(1, FLOOR_MAX_H * 10 + 1):
                warm = max(flow - t, 0.0)
                u += keep * (warm - u)
                t += (self.a * valve * warm + self.f * u + self.b * (t_out - t) + self.c) * step
                if t >= target:
                    return round(i * step, 2)
            return float(FLOOR_MAX_H)  # erreichbar, aber sehr langsam
        k = self.a * valve + self.b
        return -math.log((t_inf - target) / (t_inf - t_now)) / k

    def required_flow(self, target: float, t_out: float, valve: float = 1.0) -> float:
        """Vorlauftemperatur, die die Zieltemperatur dauerhaft hält (Fußboden heizt mit)."""
        need = self.b * (target - t_out) - self.c
        return target + max(need, 0.0) / (self.a * valve + self.f)

    def cool_down(self, t_now: float, t_out: float, hours: float) -> float:
        """Raumtemperatur nach ``hours`` Stunden ohne Heizung (nie unter die Außentemperatur)."""
        t_inf = max(t_out + self.c / self.b, t_out)
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
            "solar_gain": self.solar,
            "hours_plus_2k_max_flow": self.time_to_reach(19.0, 21.0, max_flow, t_out),
            "flow_to_hold_21": round(self.required_flow(21.0, t_out), 1),
        }

    def to_dict(self) -> dict:
        return {
            "room": self.room,
            "floor": self.floor,
            "floor_lag_h": self.floor_lag_h,
            "floor_u": self.floor_u,
            "excitation": self.excitation,
            "rls": self.rls.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RoomModel":
        m = cls(
            room=d["room"],
            floor=d.get("floor", False),
            floor_lag_h=d.get("floor_lag_h", 3.0),
            floor_u=d.get("floor_u", 0.0),
            rls=RLS.from_dict(d["rls"]),
            excitation=d.get("excitation", 0.0),
        )
        m._sun_not_negative()
        return m


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
