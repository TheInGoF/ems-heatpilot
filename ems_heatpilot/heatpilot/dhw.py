"""Warmwasser lernen: Auskühlen, Ladekosten, Zapfprofil – und daraus die sparsamste
Speichertemperatur, die trotzdem für die nächste Nutzung reicht.

Gelernt wird aus den 5-Minuten-Werten von Speichertemperatur, Ladestatus und
Brennerleistung:

* Auskühlen (ohne Ladung, ohne Zapfung):  dT/dt = a + b * T   (b < 0)
  -> Umgebungstemperatur T_amb = -a/b, Bereitschaftsverlust wächst mit T.
* Ladung: Gas-kWh je Kelvin, getrennt nach Start unter/über 47 °C
  (Brennwert: kälterer Speicher = kältere Rücklauf = besserer Wirkungsgrad),
  dazu die Ladegeschwindigkeit in K/h.
* Zapfungen: Temperaturabfall deutlich schneller als das Auskühlen, summiert über ein
  Zeitfenster von 20 min (eine Dusche fällt am Fühler über mehrere Schritte um je 0,4–0,8 K).
  Pro Wochenstunde (7 x 24) wird der typische Abfall gelernt.

Physik vorweg: Wasser von 40 auf 60 °C zu heizen kostet so viel wie zweimal
10 K – teuer ist das *Warmhalten* (Verlust wächst mit der Temperatur), das
schlechtere Kondensieren beim Laden aus warmem Speicher und jeder Brennerstart.

Erkennen am Fühler (Zapfungen, Nachlauf, Fühlerstufe) und der Schatten der Strategie stehen in
``dhw_watch``. Die Namen davon, die Module und Tests aus ``heatpilot.dhw`` holen, reicht dieses Modul
durch; die übrigen Konstanten der Erkennung gibt es nur in ``dhw_watch``. Den Desinfektionstermin aus den
Einstellungen (seit 0.36.3: einplanen, vorschlagen) rechnet ``dhw_disinfect``.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .dhw_disinfect import covers, is_disinfection, planned_for, remember
from .dhw_watch import (
    AFTER_CHARGE_H, HIT_K, SENSOR_STEP, SENSOR_STEP_N, SPECIAL_COVER_H, DrawWatch, sensor_step_of, tail_rising,
)
from .guards import DHW_MARGIN_DECAY, DHW_MARGIN_MAX, DHW_MARGIN_STEP
from .rls import RLS

# Durchgereicht, weil Tests diese Namen aus heatpilot.dhw holen
from .dhw_watch import DRAW_OPEN_K, DRAW_WIN_K, SEG_EXCESS_KH, TAIL_WIN_H, DhwShadow  # noqa: F401

SLOTS = 7 * 24
LOW_START_LIMIT = 47.0
BIG_DRAW_K = 6.0       # ab so viel Abfall in einer Stunde: große Zapfung (z. B. Badewanne)
BIG_KEEP_DAYS = 60
BIG_WARMUP_DAYS = 14
SPECIAL_ABOVE_SET_K = 8.0  # so weit über dem Soll geladen = Legionellenschaltung
HIT_THRESHOLD = 0.25      # in jeder 4. Woche gezapft: Warmwasser bereithalten
# Zapfung während der Ladung: Abfall über mind. CHARGE_DRAW_H zusammenfassen (wie ein 5-min-Schritt),
# erst dann die Schwelle – sonst wäre sie bei 60 s fünfmal unempfindlicher als bei 300 s
CHARGE_DRAW_H = 5 / 60
CHARGE_DRAW_K = 1.0


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
    # Fühlerstufe des Speicherfühlers, aus den Messwerten geschätzt (s. SENSOR_STEP)
    sensor_step: float = SENSOR_STEP
    # laufende Zustände
    _dt_hist: list[float] = field(default_factory=list)  # Beträge von d_t ≠ 0, gleitendes Fenster
    _charge: dict | None = None
    _slot: int | None = None
    _slot_draw: float = 0.0
    _slot_valid: bool = False
    _since_charge_h: float = 99.0  # Stunden seit Ladeende: kurz danach zieht der Fühler noch nach
    _slot_special: bool = False  # in dieser Stunde lief eine Sonderladung – Zapfprofil nicht lernen
    _slot_start: datetime | None = None
    # vorige Stunde: wird erst eine Stunde später gelernt, damit eine Zapfung über die volle
    # Stunde hinweg noch rückwirkend in ihre Startstunde gebucht werden kann
    _pend: dict | None = None
    _watch: DrawWatch = field(default_factory=DrawWatch)  # Zapf-Fenster und ruhiger Abschnitt
    # Zapfprofil, als der Haushalt leer wurde: wird daraus „alle lange weg“, zurück auf diesen Stand
    _backup: dict | None = None
    _last_special: datetime | None = None  # Ende der letzten Sonderladung: große Zapfungen danach sind gedeckt
    # seit 0.36.3: Ende der zuletzt erkannten Desinfektionen (ISO, ohne Einmalladungen) – bestätigen den Termin
    disinfects: list = field(default_factory=list)

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

    @property
    def special_running(self) -> bool:
        """Läuft gerade eine Sonderladung (Legionellen, Einmalladung, weit über das Soll) samt Nachlauf? (0.38)"""
        return bool(self._charge and self._charge.get("special"))

    @property
    def last_special(self) -> datetime | None:
        """Ende der zuletzt erkannten Sonderladung (Legionellenschaltung, Einmalladung) – None, wenn noch keine."""
        return self._last_special

    def big_draw_expected(self, now: datetime, hours: float, planned: list[datetime] | None = None) -> float | None:
        """Wiederkehrende große Zapfung (mind. 2× am selben Wochentag ±1 h) im Zeitfenster: übliche Größe.

        Folgt sie üblicherweise auf eine Sonderladung (Wanne nach der Legionellenschaltung), ist sie
        dadurch gedeckt und zählt nicht – dafür muss die Strategie nicht vorher höher laden. Seit 0.36.3
        ebenso nach einem geplanten Desinfektionstermin (``planned``, siehe ``dhw_disinfect.covers``).
        """
        sizes = []
        for h in range(int(hours) + 1):
            t = _wall(now + timedelta(hours=h))
            same = [b[1] for b in self.big_draws if not (len(b) > 2 and b[2])
                    and (d := datetime.fromisoformat(b[0])).weekday() == t.weekday() and abs(d.hour - t.hour) <= 1
                    and not (planned and covers(planned, t.replace(minute=0, second=0, microsecond=0)
                                                + timedelta(hours=d.hour - t.hour)))]
            if len(same) >= 2:
                sizes.append(statistics.median(same))
        return max(sizes) if sizes else None

    def expected_draw(self, now: datetime, hours: float) -> float:
        """Erwarteter Temperaturabfall durch Zapfungen in den nächsten Stunden (K)."""
        return sum(self.slot_draw(now + timedelta(hours=h)) for h in range(int(hours) + 1))

    def next_charge(self, now: datetime, t_now: float, trigger, max_h: float = 48) -> datetime | None:
        """Wann der Kessel wieder nachlädt: Auskühlen plus erwartete Zapfungen bis zur Einschaltgrenze.

        ``trigger``: Einschaltgrenze als Zahl oder als Funktion der Uhrzeit (Soll-Zeitprogramm).
        Springt das Soll morgens hoch und liegt der Speicher darunter, ist die Sprungzeit die Ladung.
        """
        fixed = not callable(trigger)
        t, step = t_now, 0.25 if fixed else 5 / 60  # mit Zeitprogramm auf 5 min genau
        for i in range(1, round(max_h / step) + 1):
            when = now + timedelta(hours=i * step)
            t -= (self.cooling_kh(t) + self.slot_draw(when)) * step
            if t <= (trigger if fixed else trigger(when)):
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
        *,
        burner_kw_prev: float | None = None,
        disinfect: bool = False,
        disinfect_temp: float | None = None,
    ) -> dict:
        """Ein Abtastschritt. Gibt {'draw_k', 'loss_kwh', 'charge_kwh'} zurück.

        ``learn_usage=False`` (Urlaub): Zapfprofil und Reserve bleiben eingefroren,
        die Speicher-Physik (Auskühlen, Ladekosten) lernt weiter.
        ``special`` (Legionellenschaltung, Einmalladung): Ladung läuft mit, aber weder
        Ladekosten noch Zapfprofil dieser Stunde werden gelernt.
        ``burner_kw_prev``: Brennerleistung am vorigen Messpunkt – dann zählt die mittlere
        Leistung über den Schritt statt der am Schrittende. ``disinfect`` (Kessel meldet die Desinfektion)
        und ``disinfect_temp``: ob eine Sonderladung eine Desinfektion war (``dhw_disinfect.is_disinfection``).
        """
        out = {"draw_k": 0.0, "loss_kwh": 0.0, "charge_kwh": 0.0, "special": False}
        self._roll_slot(when, learn_usage)
        if t_prev is None or t_cur is None or dt_h <= 0 or dt_h > 0.5:
            self._slot_valid = False
            self._watch.reset()  # Lücke: Zapf-Fenster und Abschnitt nicht darüber hinweg ziehen
            if self._charge is not None:
                self._charge["acc"] = None
            return out
        self._slot_valid = learn_usage
        if special:
            self._slot_special = True
        mean = burner_kw_prev is not None and burner_kw is not None
        power = (burner_kw + burner_kw_prev) / 2 if mean else burner_kw
        d_t = t_cur - t_prev
        self._learn_sensor_step(d_t)

        if charging:
            self._watch.reset()
            if self._charge is None:
                # Starttemperatur = nach der Zapfung, vor der ersten Ladewärme (Zapfung unten abgezogen)
                self._charge = {"start": t_prev, "max": t_cur, "kwh": 0.0, "h": 0.0, "special": False,
                                "set": dhw_set, "drawn": False, "tail_h": 0.0, "peak_h": 0.0, "tail": None,
                                "acc": None}
                if mean:
                    # Rampe ab 0 (Gegenstück zum Abschluss am Ladeende): Was der Brenner am vorigen
                    # Messpunkt leistete, war Heizung – sonst zählt im Winter halbe Heizleistung zur Ladung
                    power = burner_kw / 2
            c = self._charge
            # Zapfung während/zu Beginn der Ladung (Dusche löst die Ladung ja oft aus):
            # Temperatur fällt trotz Brenner -> Abfall plus geschätzte Ladewärme ist die Zapfung
            acc = c.get("acc") or {"h": 0.0, "d_t": 0.0, "heat_k": 0.0, "cool_k": 0.0, "when": when}
            cpk = self.kwh_per_kelvin()
            acc["h"] += dt_h
            acc["d_t"] += d_t
            acc["heat_k"] += power * dt_h / cpk if power and cpk else 0.0
            acc["cool_k"] += self.cooling_kh(t_prev) * dt_h
            c["acc"] = acc
            if acc["h"] >= CHARGE_DRAW_H - 1e-6:
                out["draw_k"] = self._charge_draw(c)
            # Sonderladung auch ohne Kesselschalter: weit über das Soll geladen (Legionellenschaltung)
            c["special"] = c.get("special") or special or (c["set"] is not None and t_cur > c["set"] + SPECIAL_ABOVE_SET_K)
            out["special"] = c["special"]
            c["disinfect"], c["dis_temp"] = c.get("disinfect") or disinfect, disinfect_temp
            c["max"] = max(c["max"], t_cur)
            c["h"] += dt_h + c["tail_h"]  # Ladung kam im Nachlauf zurück: läuft einfach weiter
            c["tail_h"], c["peak_h"], c["tail"] = 0.0, 0.0, None
            c["kw"] = burner_kw if mean else None
            if power is not None:
                c["kwh"] += power * dt_h
                out["charge_kwh"] = power * dt_h
            return out

        if self._charge is not None:
            c = self._charge
            out["draw_k"] = self._charge_draw(c)  # angefangener Abschnitt am Ladeende
            rose, c["max"] = t_cur > c["max"], max(c["max"], t_cur)
            c["special"] = c.get("special") or special
            out["special"] = c["special"]
            if c.get("kw"):
                # Trapez zu Ende rechnen: zum Ladeende fällt die Leistung auf null. Was der Brenner
                # jetzt leistet, gehört schon dem Heizkreis und zählt nicht mehr.
                out["charge_kwh"] = c["kw"] / 2 * dt_h
                c["kwh"] += out["charge_kwh"]
                c["kw"] = None
            if c["tail"] is None:
                c["tail"] = [(0.0, t_prev)]
            c["tail"].append((c["tail_h"] + dt_h, t_cur))
            if c["tail_h"] + dt_h <= AFTER_CHARGE_H + 1e-6 and tail_rising(c["tail"]):
                # Nachlauf: Anstieg zählt noch zur Ladung – ohne Brennerenergie, ohne Auskühl-Lernen;
                # zur Ladedauer nur bis zum Gipfel (flache Schritte danach nicht)
                c["tail_h"] += dt_h
                if rose:
                    c["peak_h"] = c["tail_h"]
                out["loss_kwh"] = self._loss_kwh(t_prev, dt_h)
                return out
            self._finish_charge(when)
            self._since_charge_h = 0.0
        self._since_charge_h += dt_h

        res = self._watch.step(when, dt_h, t_prev, t_cur, self.cooling_kh, self._since_charge_h)
        if res["draw_k"] > 0:
            out["draw_k"] += res["draw_k"]
            self._book_draw(res["start"], res["draw_k"])
        if res["segment"]:
            t0, hours, t1 = res["segment"]
            self.loss.update([1.0, (t0 + t1) / 2], (t1 - t0) / hours)
        out["loss_kwh"] = self._loss_kwh(t_prev, dt_h)
        return out

    def _loss_kwh(self, t: float, dt_h: float) -> float:
        c = self.kwh_per_kelvin()
        return 0.0 if c is None else c * self.cooling_kh(t) * dt_h

    def _learn_sensor_step(self, d_t: float) -> None:
        """Fühlerstufe aus dem gleitenden Fenster der Änderungen schätzen (Zapf-Fenster folgt ihr)."""
        step = round(abs(d_t), 2)
        if step >= 0.005:
            self._dt_hist.append(step)
            del self._dt_hist[:-SENSOR_STEP_N]
            self.sensor_step = sensor_step_of(self._dt_hist, self.sensor_step)
        self._watch.sensor_step = self.sensor_step

    def _charge_draw(self, c: dict) -> float:
        """Zusammengefassten Ladeabschnitt prüfen: fiel der Speicher trotz Brenner, ist das die Zapfung."""
        acc, c["acc"] = c.get("acc"), None
        if acc is None or acc["d_t"] >= -CHARGE_DRAW_K:
            return 0.0
        k = -acc["d_t"] + acc["heat_k"] - acc["cool_k"]
        if k <= 0:
            return 0.0
        self._book_draw(acc["when"], k)
        c["start"] -= k  # gezapfte Wärme muss zusätzlich geladen werden
        c["drawn"] = True
        return k

    def _book_draw(self, start: datetime, k: float) -> None:
        """Zapfung in ihre Startstunde buchen – auch rückwirkend, solange die noch nicht gelernt ist."""
        slot = slot_of(start)
        if self._pend is not None and slot == self._pend["slot"] and slot != self._slot:
            self._pend["draw"] += k
        else:
            self._slot_draw += k

    def _finish_charge(self, when: datetime | None = None) -> None:
        c, self._charge = self._charge, None
        rise = c["max"] - c["start"]
        if rise < 2.0 or c["h"] <= 0:
            return
        if c.get("special") or (c.get("set") is not None and c["max"] > c["set"] + SPECIAL_ABOVE_SET_K):
            self.special_charges += 1
            self._last_special = when
            if when is not None and is_disinfection(bool(c.get("disinfect")), c["max"], c.get("dis_temp")):
                self.disinfects = remember(self.disinfects, when)  # keine Einmalladung: bestätigt den Termin
            return
        self.charges += 1
        if c.get("drawn"):
            return  # während der Ladung gezapft: Anstieg nur geschätzt – keine Ladekosten daraus lernen
        # Ladegeschwindigkeit bis zum Gipfel: der Anstieg enthält den Nachlauf, also auch dessen Zeit
        # (sonst plant der Vorheizplan die Ladung zu kurz). Ladekosten weiter mit dem vollen Anstieg.
        self.charge_rate_kh += 0.2 * (rise / (c["h"] + c.get("peak_h", 0.0)) - self.charge_rate_kh)
        if c["kwh"] > 0:
            key = "kalt" if c["start"] < LOW_START_LIMIT else "warm"
            per_k = c["kwh"] / rise
            old = self.kwh_per_k[key]
            self.kwh_per_k[key] = per_k if old is None else old + 0.2 * (per_k - old)
            self.kwh_per_k_n[key] += 1

    def _roll_slot(self, when: datetime, learn_usage: bool = True) -> None:
        slot = slot_of(when)
        if slot == self._slot:
            return
        if self._slot is not None:
            self._learn_slot(self._pend, when)  # vorletzte Stunde ist jetzt endgültig
            self._pend = {"slot": self._slot, "draw": self._slot_draw, "valid": self._slot_valid,
                          "special": self._slot_special, "start": self._slot_start}
            if learn_usage:
                self.comfort_margin = max(self.comfort_margin - DHW_MARGIN_DECAY / 24, 0.0)
        self._slot, self._slot_draw, self._slot_valid, self._slot_special = slot, 0.0, False, False
        self._slot_start = when.replace(minute=0, second=0, microsecond=0)

    def _learn_slot(self, p: dict | None, when: datetime) -> None:
        """Zapfprofil einer abgeschlossenen Stunde lernen."""
        if p is None or not p["valid"] or p["special"]:
            return
        s, hour, draw = p["slot"], p["slot"] % 24, p["draw"]
        # Ausreißer gegen das Übliche dieser Stunde: groß und deutlich über dem Profil –
        # erst wenn die Stunde ~2 Wochen gelernt ist (vorher ist noch nichts „üblich“)
        cap = max(3.0, 3 * self.daily[hour] + 1.0)
        seen = sum(self.profile_n[d * 24 + hour] for d in range(7))
        if seen >= BIG_WARMUP_DAYS and draw >= BIG_DRAW_K and draw > cap:
            # nach einer Sonderladung (Wanne nach der Legionellenschaltung): deren Wärme wird bewusst
            # genutzt – die Strategie muss dafür nicht vorher höher laden
            covered = self._last_special is not None and \
                0 <= (p["start"] + timedelta(hours=1) - self._last_special).total_seconds() <= SPECIAL_COVER_H * 3600
            self.big_draws.append([p["start"].isoformat(timespec="minutes"), round(draw, 1), covered])
            limit = (when - timedelta(days=BIG_KEEP_DAYS)).isoformat(timespec="minutes")
            self.big_draws = [b for b in self.big_draws if b[0] >= limit][-50:]
            draw = cap  # zählt fürs Profil nur gedeckelt
        self.profile[s] += 0.3 * (draw - self.profile[s])
        self.profile_n[s] += 1
        self.daily[hour] += 0.15 * (draw - self.daily[hour])
        hit = 1.0 if p["draw"] >= HIT_K else 0.0  # vor dem Deckeln: große Zapfung zählt voll
        self.hit[s] += 0.3 * (hit - self.hit[s])
        self.hit_daily[hour] += 0.15 * (hit - self.hit_daily[hour])

    def note_household(self, home_count: int | None, away_long: bool) -> None:
        """Bis „alle lange weg“ erkannt ist, vergehen Stunden bis Tage – die würden sonst als
        „keine Zapfung“ gelernt. Darum beim Leerwerden sichern, bei ``away_long`` zurückspielen,
        bei Heimkehr verwerfen. Gebunden nur an das Flag, nicht an eine feste Dauer."""
        if home_count is None:
            return
        if home_count > 0:
            self._backup = None
        elif away_long:
            if self._backup is not None:
                b, self._backup = self._backup, None
                for k in _BACKUP_KEYS:
                    setattr(self, k, list(b[k]) if isinstance(b[k], list) else b[k])
                # die Stunden bis zum Weggehen (beim Sichern noch offen) jetzt lernen, alles danach nicht
                for p in (b["pend"], b["cur"]):
                    if p is not None and p.get("start") is not None:
                        self._learn_slot(p, p["start"] + timedelta(hours=2))
                self._pend, self._slot_valid = None, False
        elif self._backup is None:
            self._backup = {k: (list(v) if isinstance(v := getattr(self, k), list) else v) for k in _BACKUP_KEYS}
            self._backup["pend"] = None if self._pend is None else dict(self._pend)
            self._backup["cur"] = None if self._slot is None else {
                "slot": self._slot, "draw": self._slot_draw, "valid": self._slot_valid,
                "special": self._slot_special, "start": self._slot_start}

    def note_cold_draw(self) -> None:
        self.comfort_margin = min(self.comfort_margin + DHW_MARGIN_STEP, DHW_MARGIN_MAX)

    # ------------------------------------------------------------------ Strategie
    def recommend(
        self,
        now: datetime,
        someone_home: bool | None,
        next_arrival: datetime | None,
        cfg,
        unknown_from: datetime | None = None,
        current_set: float | None = None,
        disinfect_h: float | None = None,
    ) -> dict:
        """Empfohlene Speichertemperatur jetzt (nur Empfehlung, wird nicht gesetzt).

        ``unknown_from``: ab dann ist offen, ob jemand heimkommt (Gewohnheit dort noch nicht
        verlässlich gelernt). Liegt das im Horizont, bleibt das heutige Soll ``current_set`` –
        Minimum nur, wenn eine Ankunft sicher weit weg ist bzw. alle lange weg sind.
        ``disinfect_h``: Ladedauer der Desinfektion zum Termin (``dhw_disinfect.ready_hours``).
        """
        comfort = min(cfg.dhw_comfort_temp + self.comfort_margin, cfg.dhw_max_temp)
        reheat_h = (comfort - cfg.dhw_min_temp) / max(self.charge_rate_kh, 5.0)
        horizon = reheat_h + 1.0
        if someone_home is False:
            if next_arrival is None or (next_arrival - now).total_seconds() / 3600 > horizon:
                if unknown_from is not None and (unknown_from - now).total_seconds() / 3600 <= horizon:
                    keep = current_set if current_set is not None else cfg.dhw_comfort_temp
                    return {"target": round(keep), "reason": "Ankunft unbekannt (Gewohnheit lernt noch) – Soll bleibt",
                            "expects_draw": False, "margin_bound": False}
                return {"target": cfg.dhw_min_temp, "reason": "niemand zu Hause", "expects_draw": False,
                        "margin_bound": False}
        draw = self.expected_draw(now, horizon)
        chance = max(self.hit_at(slot // 24, slot % 24)
                     for slot in (slot_of(now + timedelta(hours=h)) for h in range(int(horizon) + 1)))
        # geplanter Desinfektionstermin (0.36.3): große Zapfungen danach deckt dessen Wärme
        big = self.big_draw_expected(now, horizon, planned_for(self, cfg, now, horizon, disinfect_h))
        if draw >= 1.0 or chance >= HIT_THRESHOLD or big:
            # Komfort zu Beginn der Nutzung; bei viel Verbrauch so hoch, dass er danach
            # nicht unter das Minimum fällt
            need = max(draw, big or 0.0)
            target = min(max(comfort, cfg.dhw_min_temp + need), cfg.dhw_max_temp)
            why = (f"große Zapfung üblich (≈{big:.0f} K)" if big else
                   f"Zapfung erwartet (≈{draw:.0f} K)" if draw >= 1.0 else
                   f"hier wird oft gezapft (in {chance * 100:.0f} % der Fälle)")
            # margin_bound: mehr Reserve hebt dieses Ziel an (kommt aus Komfort + Reserve, unter dem
            # Maximum) – nicht, wenn Minimum + Bedarf es bestimmt oder es schon am Maximum liegt
            bound = comfort >= cfg.dhw_min_temp + need and target < cfg.dhw_max_temp
            return {"target": round(target), "reason": why, "expects_draw": True, "margin_bound": bound}
        # Selbstkorrektur nach zu kalten Zapfungen gilt auch für das Minimum
        floor = min(cfg.dhw_min_temp + self.comfort_margin, comfort) if someone_home is not False else cfg.dhw_min_temp
        return {"target": round(floor), "reason": "keine Zapfung erwartet", "expects_draw": False,
                "margin_bound": someone_home is not False and floor < cfg.dhw_max_temp}

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
            "sensor_step": self.sensor_step,
            "backup": None if self._backup is None else {
                **self._backup, "pend": _pend_out(self._backup["pend"]), "cur": _pend_out(self._backup["cur"])},
            "last_special": None if self._last_special is None else self._last_special.isoformat(),
            "disinfects": self.disinfects,
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
            sensor_step=d.get("sensor_step", SENSOR_STEP),
            disinfects=list(d.get("disinfects") or []),
        )
        m._watch.sensor_step = m.sensor_step
        b = d.get("backup")
        if b and all(k in b for k in _BACKUP_KEYS):
            m._backup = {**b, "pend": _pend_in(b.get("pend")), "cur": _pend_in(b.get("cur"))}
        if d.get("last_special"):
            m._last_special = datetime.fromisoformat(d["last_special"])
        return m


# big_draws gehört dazu: Die Stunde vor dem Leerwerden wird beim Zurückspielen noch einmal gelernt –
# ohne Sicherung stünde eine große Zapfung darin zweimal und gälte als wiederkehrend
_BACKUP_KEYS = ("profile", "profile_n", "daily", "hit", "hit_daily", "comfort_margin", "big_draws")


def _pend_out(p: dict | None) -> dict | None:
    """Offene Stunde speicherbar machen (Zeitpunkt als Text)."""
    if p is None:
        return None
    return {**p, "start": None if p.get("start") is None else p["start"].isoformat()}


def _pend_in(p: dict | None) -> dict | None:
    if p is None:
        return None
    return {**p, "start": None if p.get("start") is None else datetime.fromisoformat(p["start"])}
