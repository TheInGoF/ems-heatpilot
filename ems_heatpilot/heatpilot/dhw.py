"""Warmwasser lernen: Auskühlen, Ladekosten, Zapfprofil – und daraus die sparsamste
Speichertemperatur, die trotzdem für die nächste Nutzung reicht.

Gelernt wird aus den 5-Minuten-Werten von Speichertemperatur, Ladestatus und
Brennerleistung:

* Auskühlen (ohne Ladung, ohne Zapfung):  dT/dt = a + b * T   (b < 0)
  -> Umgebungstemperatur T_amb = -a/b, Bereitschaftsverlust wächst mit T.
* Ladung: Gas-kWh je Kelvin, getrennt nach Start unter/über 47 °C
  (Brennwert: kälterer Speicher = kältere Rücklauf = besserer Wirkungsgrad),
  dazu die Ladegeschwindigkeit in K/h.
* Zapfungen: Temperaturabfall deutlich schneller als das Auskühlen.
  Pro Wochenstunde (7 x 24) wird der typische Abfall gelernt.

Physik vorweg: Wasser von 40 auf 60 °C zu heizen kostet so viel wie zweimal
10 K – teuer ist das *Warmhalten* (Verlust wächst mit der Temperatur), das
schlechtere Kondensieren beim Laden aus warmem Speicher und jeder Brennerstart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .guards import DHW_MARGIN_DECAY, DHW_MARGIN_MAX, DHW_MARGIN_STEP
from .rls import RLS

SLOTS = 7 * 24
LOW_START_LIMIT = 47.0
BIG_DRAW_K = 6.0       # ab so viel Abfall in einer Stunde: große Zapfung (z. B. Badewanne)
BIG_KEEP_DAYS = 60
BIG_WARMUP_DAYS = 14
SEGMENT_H = 0.5       # Auskühlen über so lange ruhige Abschnitte lernen
AFTER_CHARGE_H = 0.5   # so lange nach einer Ladung zieht der Fühler noch nach – nicht als Auskühlen lernen
MAX_RISE_K = 0.3       # steigt die Temperatur ohne Ladung stärker, wird geheizt (Ladesignal fehlt o. ä.)


def cooling_sample(d_t: float, since_charge_h: float) -> bool:
    """Ist dieser Schritt reines Auskühlen? (keine Zapfung geprüft vorher)"""
    return since_charge_h >= AFTER_CHARGE_H and -2.0 < d_t <= MAX_RISE_K


def slot_of(ts: datetime) -> int:
    return ts.weekday() * 24 + ts.hour


@dataclass
class DhwModel:
    loss: RLS = field(default_factory=lambda: RLS(n=2, lam=0.999, delta=1.0, theta=[0.8, -0.04], min_sigma=0.05))
    charge_rate_kh: float = 20.0
    charges: int = 0
    kwh_per_k: dict = field(default_factory=lambda: {"kalt": None, "warm": None})
    kwh_per_k_n: dict = field(default_factory=lambda: {"kalt": 0, "warm": 0})
    profile: list[float] = field(default_factory=lambda: [0.0] * SLOTS)  # je Wochenstunde
    profile_n: list[int] = field(default_factory=lambda: [0] * SLOTS)
    daily: list[float] = field(default_factory=lambda: [0.0] * 24)  # je Tagesstunde, lernt täglich
    # Selbstkorrektur: zusätzliche Reserve nach zu kalten Zapfungen (gedeckelt, baut sich ab)
    comfort_margin: float = 0.0
    # seltene große Zapfungen (Wanne o. ä.) – getrennt, damit sie das übliche Profil nicht überdecken
    big_draws: list = field(default_factory=list)
    # laufende Zustände
    _charge: dict | None = None
    _slot: int | None = None
    _slot_draw: float = 0.0
    _slot_valid: bool = False
    _since_charge_h: float = 99.0  # Stunden seit Ladeende: kurz danach zieht der Fühler noch nach
    _seg: list | None = None  # ruhiger Abschnitt [Start-T, Stunden, End-T] – Auskühlen über ≥ 30 min lernen

    # ------------------------------------------------------------------ Modell
    @property
    def ambient(self) -> float:
        a, b = self.loss.theta
        return -a / b if b < -1e-4 else 20.0

    def cooling_kh(self, t: float) -> float:
        """Auskühlen in K/h (positiv) bei Speichertemperatur t."""
        a, b = self.loss.theta
        return max(-(a + b * t), 0.0)

    def cool(self, t: float, hours: float) -> float:
        """Speichertemperatur nach ``hours`` Stunden ohne Ladung und Zapfung."""
        a, b = self.loss.theta
        if b >= -1e-4:
            return t
        amb = -a / b
        return amb + (t - amb) * math.exp(b * hours)

    def charge_hours(self, t_from: float, t_to: float) -> float:
        """Ladedauer in Stunden (gelernte Ladegeschwindigkeit)."""
        return max(t_to - t_from, 0.0) / max(self.charge_rate_kh, 5.0)

    def kwh_per_kelvin(self, start_temp: float | None = None) -> float | None:
        if start_temp is not None:
            v = self.kwh_per_k["kalt" if start_temp < LOW_START_LIMIT else "warm"]
            if v is not None:
                return v
        vals = [v for v in self.kwh_per_k.values() if v is not None]
        return sum(vals) / len(vals) if vals else None

    def loss_w(self, t: float) -> float | None:
        """Bereitschaftsverlust als Gasleistung in W (was das Warmhalten kostet)."""
        c = self.kwh_per_kelvin()
        return None if c is None else c * self.cooling_kh(t) * 1000

    def slot_draw(self, when: datetime) -> float:
        """Erwarteter Abfall (K) in dieser Stunde: Tagesprofil, mit genug Wochen das Wochenprofil."""
        slot = slot_of(when)
        weekly_weight = min(self.profile_n[slot] / 8, 0.7)  # nach ~8 Wochen 70 % Wochentag
        return (1 - weekly_weight) * self.daily[when.hour] + weekly_weight * self.profile[slot]

    def expected_draw(self, now: datetime, hours: float) -> float:
        """Erwarteter Temperaturabfall durch Zapfungen in den nächsten Stunden (K)."""
        return sum(self.slot_draw(now + timedelta(hours=h)) for h in range(int(hours) + 1))

    # ------------------------------------------------------------------ Lernen
    def step(
        self,
        when: datetime,
        dt_h: float,
        t_prev: float | None,
        t_cur: float | None,
        charging: bool,
        burner_kw: float | None,
        learn_usage: bool = True,
    ) -> dict:
        """Ein Abtastschritt. Gibt {'draw_k', 'loss_kwh', 'charge_kwh'} zurück.

        ``learn_usage=False`` (Urlaub): Zapfprofil und Reserve bleiben eingefroren,
        die Speicher-Physik (Auskühlen, Ladekosten) lernt weiter.
        """
        out = {"draw_k": 0.0, "loss_kwh": 0.0, "charge_kwh": 0.0}
        self._roll_slot(when, learn_usage)
        if t_prev is None or t_cur is None or dt_h <= 0 or dt_h > 0.5:
            self._slot_valid = False
            return out
        self._slot_valid = learn_usage

        if charging:
            # Zapfung während/zu Beginn der Ladung (Dusche löst die Ladung ja oft aus):
            # Temperatur fällt trotz Brenner -> Abfall plus geschätzte Ladewärme ist die Zapfung
            d_t = t_cur - t_prev
            if d_t < -1.0:
                c = self.kwh_per_kelvin()
                heat_k = burner_kw * dt_h / c if burner_kw and c else 0.0
                out["draw_k"] = -d_t + heat_k - self.cooling_kh(t_prev) * dt_h
                self._slot_draw += out["draw_k"]
            if self._charge is None:
                # Starttemperatur = nach der Zapfung, vor der ersten Ladewärme
                self._charge = {"start": t_prev - out["draw_k"], "max": t_cur, "kwh": 0.0, "h": 0.0}
            elif out["draw_k"]:
                self._charge["start"] -= out["draw_k"]  # gezapfte Wärme muss zusätzlich geladen werden
            c = self._charge
            c["max"] = max(c["max"], t_cur)
            c["h"] += dt_h
            if burner_kw is not None:
                c["kwh"] += burner_kw * dt_h
                out["charge_kwh"] = burner_kw * dt_h
            return out

        if self._charge is not None:
            self._finish_charge()
            self._since_charge_h = 0.0
        self._since_charge_h += dt_h

        d_t = t_cur - t_prev
        expected = -self.cooling_kh(t_prev) * dt_h
        if d_t < expected - 1.0:  # deutlich schneller als Auskühlen -> Zapfung
            out["draw_k"] = expected - d_t
            self._slot_draw += out["draw_k"]
            self._seg = None
        elif cooling_sample(d_t, self._since_charge_h):
            # Über einen ruhigen Abschnitt mitteln: einzelne 1–5-min-Schritte sind bei 0,1 K
            # Fühlerauflösung zu verrauscht
            if self._seg is None:
                self._seg = [t_prev, 0.0, t_prev]
            self._seg[1] += dt_h
            self._seg[2] = t_cur
            if self._seg[1] >= SEGMENT_H - 1e-6:
                t0, hours, t1 = self._seg
                self.loss.update([1.0, (t0 + t1) / 2], (t1 - t0) / hours)
                self._seg = [t_cur, 0.0, t_cur]
        else:
            self._seg = None
        c = self.kwh_per_kelvin()
        if c is not None:
            out["loss_kwh"] = c * self.cooling_kh(t_prev) * dt_h
        return out

    def _finish_charge(self) -> None:
        c, self._charge = self._charge, None
        rise = c["max"] - c["start"]
        if rise < 2.0 or c["h"] <= 0:
            return
        self.charges += 1
        self.charge_rate_kh += 0.2 * (rise / c["h"] - self.charge_rate_kh)
        if c["kwh"] > 0:
            key = "kalt" if c["start"] < LOW_START_LIMIT else "warm"
            per_k = c["kwh"] / rise
            old = self.kwh_per_k[key]
            self.kwh_per_k[key] = per_k if old is None else old + 0.2 * (per_k - old)
            self.kwh_per_k_n[key] += 1

    def _roll_slot(self, when: datetime, learn_usage: bool = True) -> None:
        slot = slot_of(when)
        if self._slot is not None and slot != self._slot and self._slot_valid:
            hour = self._slot % 24
            draw = self._slot_draw
            # Ausreißer gegen das Übliche dieser Stunde: groß und deutlich über dem Profil –
            # erst wenn die Stunde ~2 Wochen gelernt ist (vorher ist noch nichts „üblich“)
            cap = max(3.0, 3 * self.daily[hour] + 1.0)
            seen = sum(self.profile_n[d * 24 + hour] for d in range(7))
            if seen >= BIG_WARMUP_DAYS and draw >= BIG_DRAW_K and draw > cap:
                ended = (when - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                self.big_draws.append([ended.isoformat(timespec="minutes"), round(draw, 1)])
                limit = (when - timedelta(days=BIG_KEEP_DAYS)).isoformat(timespec="minutes")
                self.big_draws = [b for b in self.big_draws if b[0] >= limit][-50:]
                draw = cap  # zählt fürs Profil nur gedeckelt
            self.profile[self._slot] += 0.3 * (draw - self.profile[self._slot])
            self.profile_n[self._slot] += 1
            self.daily[hour] += 0.15 * (draw - self.daily[hour])
        if slot != self._slot:
            if self._slot is not None and learn_usage:
                self.comfort_margin = max(self.comfort_margin - DHW_MARGIN_DECAY / 24, 0.0)
            self._slot, self._slot_draw, self._slot_valid = slot, 0.0, False

    def note_cold_draw(self) -> None:
        self.comfort_margin = min(self.comfort_margin + DHW_MARGIN_STEP, DHW_MARGIN_MAX)

    # ------------------------------------------------------------------ Strategie
    def recommend(
        self,
        now: datetime,
        someone_home: bool | None,
        next_arrival: datetime | None,
        cfg,
    ) -> dict:
        """Empfohlene Speichertemperatur jetzt (nur Empfehlung, wird nicht gesetzt)."""
        comfort = min(cfg.dhw_comfort_temp + self.comfort_margin, cfg.dhw_max_temp)
        reheat_h = (comfort - cfg.dhw_min_temp) / max(self.charge_rate_kh, 5.0)
        horizon = reheat_h + 1.0
        if someone_home is False:
            if next_arrival is None or (next_arrival - now).total_seconds() / 3600 > horizon:
                return {"target": cfg.dhw_min_temp, "reason": "niemand zu Hause"}
        draw = self.expected_draw(now, horizon)
        if draw >= 1.0:
            # Komfort zu Beginn der Nutzung; bei viel Verbrauch so hoch, dass er danach
            # nicht unter das Minimum fällt
            target = min(max(comfort, cfg.dhw_min_temp + draw), cfg.dhw_max_temp)
            return {"target": round(target), "reason": f"Zapfung erwartet (≈{draw:.0f} K)"}
        return {"target": cfg.dhw_min_temp, "reason": "keine Zapfung erwartet"}

    def summary(self, t_now: float | None) -> dict:
        hours = self.daily
        return {
            "ambient": round(self.ambient, 1),
            "cooling_kh_at_55": round(self.cooling_kh(55.0), 2),
            "loss_w_now": None if t_now is None or self.loss_w(t_now) is None else round(self.loss_w(t_now)),
            "loss_w_at": {t: (None if self.loss_w(t) is None else round(self.loss_w(t))) for t in (40, 45, 50, 55, 60)},
            "kwh_per_k": {k: (None if v is None else round(v, 3)) for k, v in self.kwh_per_k.items()},
            "kwh_per_k_n": self.kwh_per_k_n,
            "charge_rate_kh": round(self.charge_rate_kh, 1),
            "charges": self.charges,
            "loss_updates": self.loss.updates,
            "profile_hours": [round(h, 2) for h in hours],
            "big_draws": self.big_draws,
            "comfort_margin": round(self.comfort_margin, 1),
        }

    # ------------------------------------------------------------------ Speichern
    def to_dict(self) -> dict:
        return {
            "loss": self.loss.to_dict(),
            "charge_rate_kh": self.charge_rate_kh,
            "charges": self.charges,
            "kwh_per_k": self.kwh_per_k,
            "kwh_per_k_n": self.kwh_per_k_n,
            "profile": self.profile,
            "profile_n": self.profile_n,
            "daily": self.daily,
            "comfort_margin": self.comfort_margin,
            "big_draws": self.big_draws,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DhwModel":
        m = cls(
            loss=RLS.from_dict(d["loss"]),
            charge_rate_kh=d["charge_rate_kh"],
            charges=d["charges"],
            kwh_per_k=d["kwh_per_k"],
            kwh_per_k_n=d["kwh_per_k_n"],
            profile=d["profile"],
            profile_n=d["profile_n"],
            daily=d.get("daily", [0.0] * 24),
            comfort_margin=d.get("comfort_margin", 0.0),
            big_draws=d.get("big_draws", []),
        )
        return m


@dataclass
class DhwShadow:
    """Spielt die Strategie parallel zur Wirklichkeit durch (gleiche Zapfungen).

    Ersparnis = Bereitschaftsverlust (echt) - Bereitschaftsverlust (Strategie).
    Zapfungen bei zu kaltem Speicher werden als Komfortverletzung gezählt.
    """

    t_sim: float | None = None

    def step(self, model: DhwModel, t_actual: float | None, target: float, draw_k: float,
             dt_h: float, comfort: float) -> dict:
        out = {"loss_kwh": 0.0, "cold_draw": 0}
        if t_actual is None:
            return out
        if self.t_sim is None:
            self.t_sim = t_actual
        if draw_k > 0:
            if self.t_sim < comfort:
                out["cold_draw"] = 1
            self.t_sim -= draw_k
        cooled = self.t_sim - model.cooling_kh(self.t_sim) * dt_h
        # Strategie lädt nach, sobald sie unter ihr Ziel fällt (Energie wird gleich verbucht)
        self.t_sim = max(cooled, target) if cooled < target else cooled
        c = model.kwh_per_kelvin()
        if c is not None:
            out["loss_kwh"] = c * model.cooling_kh(self.t_sim) * dt_h
        return out
