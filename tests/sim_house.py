"""Künstliches Haus für Tests: zwei Wärmespeicher (Luft/Heizkörper schnell, Wände langsam).

Damit prüfen die Tests, dass HeatPilot ein *beliebiges* Haus richtig lernt – nicht nur eines,
an dessen Daten die Parameter zufällig passen. Gas = zugeführte Wärme (Wirkungsgrad 1).

    C_a · dT_a/dt = Q_heiz − (T_a − T_m)·G_am − (T_a − T_out)·G_ao
    C_m · dT_m/dt = (T_a − T_m)·G_am − (T_m − T_out)·G_mo + Q_fremd

Gemessen wird T_a (Raumluft). Wärmeverlust im Beharrungszustand: H = G_ao + G_am·G_mo/(G_am + G_mo).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime


@dataclass
class House:
    g_ao: float = 0.05      # kW/K Luft → außen (Lüftung, Fenster)
    g_am: float = 0.6       # kW/K Luft ↔ Wände
    g_mo: float = 0.1       # kW/K Wände → außen
    c_a: float = 1.0        # kWh/K Luft, Heizkörper, Möbel
    c_m: float = 12.0       # kWh/K Wände
    free_kw: float = 0.6    # Fremdwärme (auf die Wände)
    p_max_kw: float = 12.0
    p_min_kw: float = 3.0
    day: tuple = (5, 22)    # geheizt 5–22 Uhr, nachts aus (wie Tado „aus“)
    set_c: float = 21.0
    t_out_mean: float = 3.0
    t_out_swing: float = 4.0
    season_k: float = 0.0   # Jahresgang: Tagesmittel = t_out_mean − season_k·cos(Jahr), Minimum Mitte Januar
    t_out_noise: float = 3.0  # Streuung des Tagesmittels von Tag zu Tag (gleichverteilt ±)

    @property
    def h_kw_per_k(self) -> float:
        return self.g_ao + self.g_am * self.g_mo / (self.g_am + self.g_mo)

    @property
    def offset_kw(self) -> float:
        """Achsabschnitt wie im Hausmodell: Fremdwärme, wie sie an der Luft ankommt (negativ)."""
        return -self.free_kw * self.g_am / (self.g_am + self.g_mo)

    def time_constants(self) -> tuple[float, float]:
        """(schnell, langsam) in Stunden – Eigenwerte des Zwei-Speicher-Systems."""
        a11 = -(self.g_am + self.g_ao) / self.c_a
        a12 = self.g_am / self.c_a
        a21 = self.g_am / self.c_m
        a22 = -(self.g_am + self.g_mo) / self.c_m
        tr, det = a11 + a22, a11 * a22 - a12 * a21
        disc = math.sqrt(tr * tr / 4 - det)
        l1, l2 = tr / 2 - disc, tr / 2 + disc
        return -1 / l1, -1 / l2

    def simulate(self, days: int, start: datetime, seed: int = 1, dhw=None, dhw_known: bool = True) -> list[tuple]:
        """Stundenzeilen wie im Speicher: (ts, t_out, t_in, gas_kwh, dhw_frac, home, vacation, spike, dhw_energy).

        ``dhw(hour, weekday)``: Warmwasser-Gas dieser Stunde (kWh). ``dhw_known=False``: wie die
        Langzeitstatistik vor HeatPilot – ob Warmwasser geladen wurde, ist unbekannt.
        """
        rng = random.Random(seed)
        t0 = int(start.timestamp())
        t_out_day = self.t_out_mean
        ta = tm = self.set_c
        on = False
        rows = []
        steps = 12  # 5-min-Schritte
        dt = 1 / steps
        for h in range(days * 24):
            ts = t0 + h * 3600
            hour = datetime.fromtimestamp(ts).hour
            if hour == 0:
                doy = datetime.fromtimestamp(ts).timetuple().tm_yday
                season = self.season_k * math.cos(2 * math.pi * (doy - 15) / 365)
                t_out_day = self.t_out_mean - season + rng.uniform(-self.t_out_noise, self.t_out_noise)
            gas = t_out_sum = t_in_sum = 0.0
            heat_on = self.day[0] <= hour < self.day[1]
            for s in range(steps):
                frac = (h + s * dt) % 24
                t_out = t_out_day + self.t_out_swing * math.sin(2 * math.pi * (frac - 9) / 24)
                # Zweipunktregler mit Mindestleistung: der Kessel taktet wie in echt
                if heat_on and ta < self.set_c - 0.2:
                    on = True
                elif not heat_on or ta > self.set_c + 0.2:
                    on = False
                q = self.p_max_kw if on else 0.0
                dta = (q - (ta - tm) * self.g_am - (ta - t_out) * self.g_ao) / self.c_a
                dtm = ((ta - tm) * self.g_am - (tm - t_out) * self.g_mo + self.free_kw) / self.c_m
                ta += dta * dt
                tm += dtm * dt
                gas += q * dt
                t_out_sum += t_out
                t_in_sum += ta
            dhw_kwh = dhw(hour, datetime.fromtimestamp(ts).weekday()) if dhw else 0.0
            frac = (dhw_kwh / (gas + dhw_kwh) if gas + dhw_kwh > 0 else 0.0) if dhw_known else None
            rows.append((ts, round(t_out_sum / steps, 2), round(t_in_sum / steps + rng.gauss(0, 0.03), 2),
                         round(gas + dhw_kwh, 3), frac, 1.0, 0.0, None, frac))
        return rows
