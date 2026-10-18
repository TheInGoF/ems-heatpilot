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
import statistics
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
SPECIAL_ABOVE_SET_K = 8.0  # so weit über dem Soll geladen = Legionellenschaltung
HIT_K = 1.0               # ab so viel Temperaturabfall in einer Stunde zählt sie als „mit Zapfung“
HIT_THRESHOLD = 0.25      # in jeder 4. Woche gezapft: Warmwasser bereithalten
AFTER_CHARGE_H = 0.5   # so lange nach einer Ladung zieht der Fühler noch nach – nicht als Auskühlen lernen
MAX_RISE_K = 0.3       # steigt die Temperatur ohne Ladung stärker, wird geheizt (Ladesignal fehlt o. ä.)


def cooling_sample(d_t: float, since_charge_h: float) -> bool:
    """Ist dieser Schritt reines Auskühlen? (keine Zapfung geprüft vorher)"""
    return since_charge_h >= AFTER_CHARGE_H and -2.0 < d_t <= MAX_RISE_K


def _wall(dt: datetime) -> datetime:
    """Ortszeit dieses Zeitpunkts – nach Rechnen über die Zeitumstellung hinweg stimmt sonst die Stunde nicht."""
    return dt.astimezone() if dt.tzinfo else dt


def slot_of(ts: datetime) -> int:
    ts = _wall(ts)
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
    # Legionellenschaltung/Einmalladung: geladen, aber nicht gelernt
    special_charges: int = 0
    # Wie oft in dieser Stunde überhaupt gezapft wird (Anteil, nicht Menge) – seltene, aber
    # regelmäßige Nutzung (Wanne am Sonntag) verschwindet sonst im Mittelwert
    hit: list[float] = field(default_factory=lambda: [0.0] * SLOTS)
    hit_daily: list[float] = field(default_factory=lambda: [0.0] * 24)
    # laufende Zustände
    _charge: dict | None = None
    _slot: int | None = None
    _slot_draw: float = 0.0
    _slot_valid: bool = False
    _since_charge_h: float = 99.0  # Stunden seit Ladeende: kurz danach zieht der Fühler noch nach
    _slot_special: bool = False  # in dieser Stunde lief eine Sonderladung – Zapfprofil nicht lernen
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
        # nach Zahl der Messungen gewichtet (höchstens 5 je Topf) – ein Topf mit 2 Messungen
        # zählt nicht so viel wie einer mit 20
        pairs = [(v, min(self.kwh_per_k_n.get(k, 1), 5)) for k, v in self.kwh_per_k.items() if v is not None]
        w = sum(n for _, n in pairs)
        return sum(v * n for v, n in pairs) / w if w else None

    def loss_w(self, t: float) -> float | None:
        """Bereitschaftsverlust als Gasleistung in W (was das Warmhalten kostet)."""
        c = self.kwh_per_kelvin()
        return None if c is None else c * self.cooling_kh(t) * 1000

    def slot_draw(self, when: datetime) -> float:
        """Erwarteter Abfall (K) in dieser Stunde: Tagesprofil, mit genug Wochen das Wochenprofil."""
        when = _wall(when)
        return self.slot_draw_at(when.weekday(), when.hour)

    def weekly_weight(self, weekday: int, hour: int) -> float:
        """Wie stark der Wochentag zählt: wächst mit jeder gelernten Woche, höchstens 70 %."""
        return min(self.profile_n[weekday * 24 + hour] / 8, 0.7)

    def slot_draw_at(self, weekday: int, hour: int) -> float:
        w = self.weekly_weight(weekday, hour)
        return (1 - w) * self.daily[hour] + w * self.profile[weekday * 24 + hour]

    def hit_at(self, weekday: int, hour: int) -> float:
        w = self.weekly_weight(weekday, hour)
        return (1 - w) * self.hit_daily[hour] + w * self.hit[weekday * 24 + hour]

    def big_draw_expected(self, now: datetime, hours: float) -> float | None:
        """Wiederkehrende große Zapfung (mind. 2× am selben Wochentag ±1 h) im Zeitfenster: übliche Größe."""
        sizes = []
        for h in range(int(hours) + 1):
            t = _wall(now + timedelta(hours=h))
            same = [k for ts, k in self.big_draws
                    if (d := datetime.fromisoformat(ts)).weekday() == t.weekday() and abs(d.hour - t.hour) <= 1]
            if len(same) >= 2:
                sizes.append(statistics.median(same))
        return max(sizes) if sizes else None

    def expected_draw(self, now: datetime, hours: float) -> float:
        """Erwarteter Temperaturabfall durch Zapfungen in den nächsten Stunden (K)."""
        return sum(self.slot_draw(now + timedelta(hours=h)) for h in range(int(hours) + 1))

    def next_charge(self, now: datetime, t_now: float, trigger: float, max_h: float = 48) -> datetime | None:
        """Wann der Kessel wieder nachlädt: Auskühlen plus erwartete Zapfungen bis zur Einschaltgrenze."""
        t, step = t_now, 0.25
        for i in range(1, int(max_h / step) + 1):
            when = now + timedelta(hours=i * step)
            t -= (self.cooling_kh(t) + self.slot_draw(when)) * step
            if t <= trigger:
                return when
        return None

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
        special: bool = False,
        dhw_set: float | None = None,
    ) -> dict:
        """Ein Abtastschritt. Gibt {'draw_k', 'loss_kwh', 'charge_kwh'} zurück.

        ``learn_usage=False`` (Urlaub): Zapfprofil und Reserve bleiben eingefroren,
        die Speicher-Physik (Auskühlen, Ladekosten) lernt weiter.
        ``special`` (Legionellenschaltung, Einmalladung): Ladung läuft mit, aber weder
        Ladekosten noch Zapfprofil dieser Stunde werden gelernt.
        """
        out = {"draw_k": 0.0, "loss_kwh": 0.0, "charge_kwh": 0.0}
        self._roll_slot(when, learn_usage)
        if t_prev is None or t_cur is None or dt_h <= 0 or dt_h > 0.5:
            self._slot_valid = False
            return out
        self._slot_valid = learn_usage
        if special:
            self._slot_special = True

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
                self._charge = {"start": t_prev - out["draw_k"], "max": t_cur, "kwh": 0.0, "h": 0.0,
                                "special": False, "set": dhw_set, "drawn": False}
            elif out["draw_k"]:
                self._charge["start"] -= out["draw_k"]  # gezapfte Wärme muss zusätzlich geladen werden
            c = self._charge
            c["special"] = c.get("special") or special
            c["drawn"] = c.get("drawn") or out["draw_k"] > 0
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
        # Sonderladung auch ohne Kesselschalter: weit über das Soll geladen (Legionellenschaltung)
        if c.get("special") or (c.get("set") is not None and c["max"] > c["set"] + SPECIAL_ABOVE_SET_K):
            self.special_charges += 1
            return
        self.charges += 1
        if c.get("drawn"):
            return  # während der Ladung gezapft: Anstieg nur geschätzt – keine Ladekosten daraus lernen
        self.charge_rate_kh += 0.2 * (rise / c["h"] - self.charge_rate_kh)
        if c["kwh"] > 0:
            key = "kalt" if c["start"] < LOW_START_LIMIT else "warm"
            per_k = c["kwh"] / rise
            old = self.kwh_per_k[key]
            self.kwh_per_k[key] = per_k if old is None else old + 0.2 * (per_k - old)
            self.kwh_per_k_n[key] += 1

    def _roll_slot(self, when: datetime, learn_usage: bool = True) -> None:
        slot = slot_of(when)
        if self._slot is not None and slot != self._slot and self._slot_valid and not self._slot_special:
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
            hit = 1.0 if self._slot_draw >= HIT_K else 0.0  # vor dem Deckeln: große Zapfung zählt voll
            self.hit[self._slot] += 0.3 * (hit - self.hit[self._slot])
            self.hit_daily[hour] += 0.15 * (hit - self.hit_daily[hour])
        if slot != self._slot:
            if self._slot is not None and learn_usage:
                self.comfort_margin = max(self.comfort_margin - DHW_MARGIN_DECAY / 24, 0.0)
            self._slot, self._slot_draw, self._slot_valid, self._slot_special = slot, 0.0, False, False

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
        chance = max(self.hit_at(slot // 24, slot % 24)
                     for slot in (slot_of(now + timedelta(hours=h)) for h in range(int(horizon) + 1)))
        big = self.big_draw_expected(now, horizon)
        if draw >= 1.0 or chance >= HIT_THRESHOLD or big:
            # Komfort zu Beginn der Nutzung; bei viel Verbrauch so hoch, dass er danach
            # nicht unter das Minimum fällt
            need = max(draw, big or 0.0)
            target = min(max(comfort, cfg.dhw_min_temp + need), cfg.dhw_max_temp)
            why = (f"große Zapfung üblich (≈{big:.0f} K)" if big else
                   f"Zapfung erwartet (≈{draw:.0f} K)" if draw >= 1.0 else
                   f"hier wird oft gezapft (in {chance * 100:.0f} % der Fälle)")
            return {"target": round(target), "reason": why}
        # Selbstkorrektur nach zu kalten Zapfungen gilt auch für das Minimum
        floor = min(cfg.dhw_min_temp + self.comfort_margin, comfort) if someone_home is not False else cfg.dhw_min_temp
        return {"target": round(floor), "reason": "keine Zapfung erwartet"}

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
            # Vorhersage je Wochentag (Mo=0) und Stunde, so wie HeatPilot sie zum Planen nutzt
            "profile_week": [[round(self.slot_draw_at(d, h), 2) for h in range(24)] for d in range(7)],
            "week_weight": [round(sum(self.weekly_weight(d, h) for h in range(24)) / 24, 2) for d in range(7)],
            "big_draws": self.big_draws,
            "comfort_margin": round(self.comfort_margin, 1),
            "special_charges": self.special_charges,
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
            "special_charges": self.special_charges,
            "hit": self.hit,
            "hit_daily": self.hit_daily,
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
            special_charges=d.get("special_charges", 0),
            hit=d.get("hit", [0.0] * SLOTS),
            hit_daily=d.get("hit_daily", [0.0] * 24),
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
