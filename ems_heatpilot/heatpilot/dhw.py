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
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
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
SPECIAL_COVER_H = 12.0     # so lange danach ist eine große Zapfung durch die Sonderladung gedeckt
HIT_K = 1.0               # ab so viel Temperaturabfall in einer Stunde zählt sie als „mit Zapfung“
HIT_THRESHOLD = 0.25      # in jeder 4. Woche gezapft: Warmwasser bereithalten
AFTER_CHARGE_H = 0.5   # so lange nach einer Ladung zieht der Fühler noch nach – nicht als Auskühlen lernen
MAX_RISE_K = 0.3       # steigt die Temperatur ohne Ladung stärker, wird geheizt (Ladesignal fehlt o. ä.)
# Nachlauf: Das Ladesignal fällt, wenn der Brenner schon aus ist – der Speicher steigt danach noch
# 1,5–3,5 K (Restwärme, Ladepumpe). Die Ladung endet erst, wenn er nicht mehr steigt.
TAIL_WIN_H = 5 / 60    # Anstieg über dieses Fenster prüfen (bei längerer Abtastung: der letzte Schritt) –
TAIL_RISE_K = 0.2      # mind. so viel (zwei Fühlerstufen) …
TAIL_RISE_KH = 2.0     # … oder so schnell; je Schritt geprüft wäre bei 60 s jede flache Stufe das Ende
# Zapfungen über ein Zeitfenster: Überschuss-Abfall gegenüber dem erwarteten Auskühlen aufsummiert
DRAW_WIN_H = 20 / 60   # Fensterlänge – unabhängig von der Abtastrate (60–900 s); der älteste Schritt
#                        zählt anteilig, wenn er über das Fenster hinausragt (sonst 30 min bei 900 s)
DRAW_WIN_K = 0.8       # ab so viel Überschuss im Fenster: Zapfung (ein Schritt > 1 K reicht immer)
DRAW_OPEN_K = 0.3      # ab so viel gilt das Fenster als offen – solange kein Auskühlen lernen
# Gröbere Speicherfühler (0,5- oder 1-K-Stufen): eine einzelne Stufe läge über DRAW_OPEN_K, kein
# ruhiger Abschnitt käme je zustande und das Auskühlen würde als 0 gelernt. Darum wird die Fühler-
# stufe aus den Messwerten geschätzt (kleinster häufiger Betrag von d_t ≠ 0) und hebt die Schwellen:
# offen ab max(DRAW_OPEN_K, Stufe + 0,1), Zapfung ab max(DRAW_WIN_K, 1,5 × Stufe). Bei 0,1 K ändert
# sich nichts.
SENSOR_STEP = 0.1          # Standard-Fühlerstufe
SENSOR_STEP_N = 300        # so viele Schritte mit Änderung bilden das gleitende Fenster
SENSOR_STEP_MIN_N = 30     # vorher bleibt der Standard bzw. der gespeicherte Wert
SENSOR_STEP_SHARE = 0.1    # Stufe = kleinster Betrag, der mindestens so oft vorkommt (keine Ausreißer)
DRAW_QUIET_H = 10 / 60  # fällt der Speicher so lange …
DRAW_GROW_K = 0.25     # … um weniger als das zusätzlich: Zapfung vorbei
DRAW_MAX_H = 1.0       # länger zählt nichts mehr zur selben Zapfung
SEG_EXCESS_KH = 1.0    # ruhiger Abschnitt darf höchstens so viel schneller fallen als das Modell
COLD_HOLD_H = 0.5      # Zapfschritte mit weniger Abstand sind ein Ereignis (Reserve, Komfortzähler)
# Zapfung während der Ladung: Abfall über mind. CHARGE_DRAW_H zusammenfassen (wie ein 5-min-Schritt),
# erst dann die Schwelle – sonst wäre sie bei 60 s fünfmal unempfindlicher als bei 300 s
CHARGE_DRAW_H = 5 / 60
CHARGE_DRAW_K = 1.0


def cooling_sample(d_t: float, since_charge_h: float) -> bool:
    """Ist dieser Schritt reines Auskühlen? (keine Zapfung geprüft vorher)"""
    return since_charge_h >= AFTER_CHARGE_H and -2.0 < d_t <= MAX_RISE_K


def sensor_step_of(diffs: list[float], default: float = SENSOR_STEP) -> float:
    """Fühlerstufe aus den Beträgen von d_t ≠ 0: der kleinste, der häufig vorkommt.

    Ein 0,5-K-Fühler liefert nur Vielfache von 0,5; bei 0,1 K ist 0,1 der häufigste kleine Schritt.
    Seltene kleine Beträge (Übertragungsfehler) zählen nicht. Zu wenige Werte: ``default``.
    """
    if len(diffs) < SENSOR_STEP_MIN_N:
        return default
    need = max(3, SENSOR_STEP_SHARE * len(diffs))
    return min((v for v, n in Counter(diffs).items() if n >= need), default=default)


class DrawWatch:
    """Zapfungen über ein Zeitfenster erkennen und ruhige Abschnitte fürs Auskühlen sammeln.

    Gemeinsam fürs Lernen (DhwModel) und die Anzeige (engine.dhw_points). Gezählt wird der
    Abfall über das erwartete Auskühlen hinaus, summiert über die letzten DRAW_WIN_H: So hängt
    die Erkennung nicht von der Abtastrate ab, und die 0,1-K-Stufen des Fühlers heben sich auf.
    ``sensor_step``: gelernte Fühlerstufe – gröbere Fühler heben die Schwellen (s. SENSOR_STEP).
    """

    def __init__(self, sensor_step: float = SENSOR_STEP) -> None:
        self.win: list[list] = []      # [Zeitpunkt, Stunden, Überschuss-Abfall K]
        self.ev: dict | None = None    # laufende Zapfung
        self.seg: list | None = None   # ruhiger Abschnitt [Start-T, Stunden, End-T]
        self.sensor_step = sensor_step

    def reset(self) -> None:
        """Ladung oder Lücke: nichts darüber hinweg verbinden."""
        self.win, self.ev, self.seg = [], None, None

    @property
    def open_k(self) -> float:
        return max(DRAW_OPEN_K, self.sensor_step + 0.1)

    @property
    def win_k(self) -> float:
        """Zapfung ab anderthalb Fühlerstufen im Fenster: eine Stufe ist noch Auskühlen, zwei sind eine
        Zapfung – auch wenn das Auskühlen im Fenster sie etwas kleiner macht. Bei 0,1 K: DRAW_WIN_K."""
        return max(DRAW_WIN_K, 1.5 * self.sensor_step)

    @property
    def seg_excess_kh(self) -> float:
        """Ein grober Fühler macht aus einem ruhigen Abschnitt je nach Phasenlage eine Stufe mehr."""
        return SEG_EXCESS_KH + max(self.sensor_step - SENSOR_STEP, 0.0) / SEGMENT_H

    def net(self) -> float:
        """Überschuss im Fenster; der älteste Schritt zählt nur mit dem Anteil, der noch hineinragt."""
        if not self.win:
            return 0.0
        rest = sum(x[1] for x in self.win[1:])
        w0 = min((DRAW_WIN_H - rest) / self.win[0][1], 1.0) if self.win[0][1] > 0 else 1.0
        return w0 * self.win[0][2] + sum(x[2] for x in self.win[1:])

    @property
    def open(self) -> bool:
        """Fällt der Speicher gerade auffällig? Dann noch kein Auskühlen lernen."""
        return self.ev is not None or self.net() >= self.open_k

    def step(self, when: datetime, dt_h: float, t_prev: float, t_cur: float, cooling_kh, since_charge_h: float) -> dict:
        """Ein Schritt ohne Ladung. Gibt {'draw_k', 'start', 'segment'} zurück.

        ``start``: Zeitpunkt, ab dem die Zapfung lief (rückwirkend dort buchen);
        ``segment``: fertiger ruhiger Abschnitt (Start-T, Stunden, End-T) zum Lernen.
        """
        out = {"draw_k": 0.0, "start": None, "segment": None}
        d_t = t_cur - t_prev
        excess = -cooling_kh(t_prev) * dt_h - d_t
        if self.ev is not None:  # laufende Zapfung: jeden weiteren Abfall dazubuchen
            ev = self.ev
            ev["net"] += excess
            ev["h"] += dt_h
            if ev["net"] > ev["peak"]:
                out["draw_k"], out["start"] = ev["net"] - ev["peak"], ev["start"]
                ev["peak"] = ev["net"]
            # Zuwachs über die letzten DRAW_QUIET_H (über die Zeit statt je Schritt: abtastratenunabhängig)
            ev["hist"].append((ev["h"], ev["peak"]))
            while len(ev["hist"]) > 1 and ev["hist"][1][0] <= ev["h"] - DRAW_QUIET_H + 1e-6:
                ev["hist"].pop(0)
            ref_h, ref_peak = ev["hist"][0]
            quiet = ref_h <= ev["h"] - DRAW_QUIET_H + 1e-6 and ev["peak"] - ref_peak < DRAW_GROW_K
            if quiet or ev["h"] >= DRAW_MAX_H - 1e-6:
                self.ev = None
            return out
        self.win.append([when, dt_h, excess])
        while len(self.win) > 1 and sum(x[1] for x in self.win[1:]) >= DRAW_WIN_H - 1e-6:
            self.win.pop(0)
        net = self.net()
        if net >= self.win_k:
            # Beginn: der späteste Schritt, ab dem (fast) der ganze Abfall liegt – Rauschen davor zählt
            # nicht (ungewichtet: der Beginn ist ein Zeitpunkt, kein Anteil)
            i = max(j for j in range(len(self.win)) if sum(x[2] for x in self.win[j:]) >= 0.9 * net)
            out["draw_k"], out["start"] = net, self.win[i][0]
            self.ev = {"start": self.win[i][0], "net": 0.0, "peak": 0.0, "h": 0.0, "hist": [(0.0, 0.0)]}
            self.win, self.seg = [], None
            return out
        if not cooling_sample(d_t, since_charge_h):
            self.seg = None
            return out
        # Über einen ruhigen Abschnitt mitteln: einzelne 1–5-min-Schritte sind bei 0,1 K
        # Fühlerauflösung zu verrauscht. Geprüft wird der Abschnitt als Ganzes, nicht jede Stufe.
        if self.open:
            # kleine Zapfung unter der Schwelle: Abschnitt verwerfen, nicht nur aufschieben –
            # sonst landet sie danach doch im Auskühlen. Neuer Abschnitt, wenn das Fenster zu ist.
            self.seg = None
            return out
        if self.seg is None:
            self.seg = [t_prev, 0.0, t_prev]
        self.seg[1] += dt_h
        self.seg[2] = t_cur
        if self.seg[1] >= SEGMENT_H - 1e-6:
            t0, hours, t1 = self.seg
            if (t0 - t1) / hours <= cooling_kh((t0 + t1) / 2) + self.seg_excess_kh:
                out["segment"] = (t0, hours, t1)
            self.seg = [t_cur, 0.0, t_cur]
        return out


def tail_rising(hist: list[tuple[float, float]]) -> bool:
    """Steigt der Speicher nach dem Ladesignal noch (Nachlauf)?

    ``hist``: (Stunden seit dem Ladesignal, Temperatur) – erster Punkt das Ladeende, letzter jetzt.
    Geprüft wird der Anstieg über die letzten TAIL_WIN_H (bei längerer Abtastung: der letzte
    Schritt), nicht jeder Schritt für sich – bei 60 s beendete sonst jede flache Fühlerstufe den
    Nachlauf, und die Ladekosten hingen von der Abtastrate ab. Solange das Fenster seit dem Ladeende
    noch nicht voll ist, genügt, dass es nicht fällt. Fällt der Speicher um TAIL_RISE_K oder mehr
    (im Schritt oder seit dem Ladeende), ist er vorbei (Zapfung).
    """
    h, t = hist[-1]
    if t - hist[-2][1] <= -TAIL_RISE_K + 1e-6:
        return False
    ref = next((p for p in reversed(hist[:-1]) if p[0] <= h - TAIL_WIN_H + 1e-6), hist[0])
    rise, win_h = t - ref[1], h - ref[0]
    if win_h < TAIL_WIN_H - 1e-6:
        return rise > -TAIL_RISE_K + 1e-6
    return rise >= TAIL_RISE_K - 1e-6 or (rise > 0 and rise / win_h >= TAIL_RISE_KH)


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
        """Wiederkehrende große Zapfung (mind. 2× am selben Wochentag ±1 h) im Zeitfenster: übliche Größe.

        Folgt sie üblicherweise auf eine Sonderladung (Wanne nach der Legionellenschaltung), ist sie
        dadurch gedeckt und zählt nicht – dafür muss die Strategie nicht vorher höher laden.
        """
        sizes = []
        for h in range(int(hours) + 1):
            t = _wall(now + timedelta(hours=h))
            same = [b[1] for b in self.big_draws if not (len(b) > 2 and b[2])
                    and (d := datetime.fromisoformat(b[0])).weekday() == t.weekday() and abs(d.hour - t.hour) <= 1]
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
    ) -> dict:
        """Ein Abtastschritt. Gibt {'draw_k', 'loss_kwh', 'charge_kwh'} zurück.

        ``learn_usage=False`` (Urlaub): Zapfprofil und Reserve bleiben eingefroren,
        die Speicher-Physik (Auskühlen, Ladekosten) lernt weiter.
        ``special`` (Legionellenschaltung, Einmalladung): Ladung läuft mit, aber weder
        Ladekosten noch Zapfprofil dieser Stunde werden gelernt.
        ``burner_kw_prev``: Brennerleistung am vorigen Messpunkt – dann zählt die mittlere
        Leistung über den Schritt statt der am Schrittende.
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
    ) -> dict:
        """Empfohlene Speichertemperatur jetzt (nur Empfehlung, wird nicht gesetzt).

        ``unknown_from``: ab dann ist offen, ob jemand heimkommt (Gewohnheit dort noch nicht
        verlässlich gelernt). Liegt das im Horizont, bleibt das heutige Soll ``current_set`` –
        Minimum nur, wenn eine Ankunft sicher weit weg ist bzw. alle lange weg sind.
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
        big = self.big_draw_expected(now, horizon)
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


@dataclass
class DhwShadow:
    """Spielt die Strategie parallel zur Wirklichkeit durch (gleiche Zapfungen).

    Ersparnis = Bereitschaftsverlust (echt) - Bereitschaftsverlust (Strategie).
    Zapfungen bei zu kaltem Speicher werden als Komfortverletzung gezählt – je Ereignis
    einmal (Schritte mit weniger als COLD_HOLD_H Abstand gehören zusammen), kleine
    Zapfungen unter HIT_K nicht.
    """

    t_sim: float | None = None
    _ev: dict | None = None  # laufendes Zapfereignis: Schatten vorher, Summe, Ruhezeit, gezählt
    _ride: bool = False      # Schatten steht noch auf der Wärme einer gespiegelten Sonderladung
    _ride_h: float = 0.0     # Stunden seit deren Ende: länger als SPECIAL_COVER_H deckt sie nichts

    def step(self, model: DhwModel, t_actual: float | None, target: float, draw_k: float,
             dt_h: float, comfort: float, *, dhw_min: float | None = None,
             margin_bound: bool | None = None, dhw_max: float | None = None,
             mirror: float | None = None) -> dict:
        """``cold_draw``: Ereignis war zu kalt (Kennzahl). ``raise_margin``: zu kalt, und mehr Reserve
        hätte das Ziel davor angehoben (``margin_bound``) – nur dann lohnt sie. Das gilt auch für
        „keine Zapfung erwartet“ mit jemandem zu Hause: Dort ist Minimum + Reserve der Boden, und die
        Reserve ist für unregelmäßige Haushalte die einzige Korrektur. Ohne Angabe wie bisher. Beides
        gilt so, wie es vor dem Ereignis stand. Ist der Fehlbetrag größer als die höchste Reserve oder
        die Zapfung größer als dhw_max − dhw_min, hilft keine Reserve: nur Kennzahl.

        ``mirror``: Speichertemperatur, solange der Regler eine Sonderladung fährt (Legionellen-
        schaltung). Die kann keine Strategie verhindern – der Schatten bekommt sie genauso, sonst
        spart er Verluste ein, die es nicht gibt. Eine Zapfung, die er nur dank dieser Wärme bedient
        (Wanne nach der Legionellenschaltung: beginnt warm, zieht durch ihre Größe unter das Minimum),
        zählt weder als zu kalt noch für die Reserve – höchstens SPECIAL_COVER_H lang, und nicht, wenn
        der Schatten sie schon unter dem Komfortwert bedient.
        """
        out = {"loss_kwh": 0.0, "cold_draw": 0, "raise_margin": 0}
        if t_actual is None:
            return out
        if self.t_sim is None:
            self.t_sim = t_actual
        if mirror is not None and mirror > self.t_sim:
            self.t_sim, self._ride, self._ride_h = mirror, True, 0.0
        elif self._ride:
            self._ride_h += dt_h
            self._ride = self._ride_h <= SPECIAL_COVER_H + 1e-6
        ev = self._ev
        if ev is not None:
            ev["quiet"] += dt_h
            if ev["quiet"] > COLD_HOLD_H + 1e-6:
                ev = self._ev = None
        if draw_k > 0:
            if ev is None:
                ev = self._ev = {"t0": self.t_sim, "k": 0.0, "quiet": 0.0, "counted": False,
                                 "bound": margin_bound is None or bool(margin_bound),
                                 "special": self._ride and self.t_sim >= comfort - 1e-6}
            ev["k"] += draw_k
            ev["quiet"] = 0.0
            under = dhw_min is not None and ev["t0"] - ev["k"] < dhw_min
            short = ev["t0"] < comfort or under
            if not ev["counted"] and ev["k"] >= HIT_K and short and not ev["special"]:
                ev["counted"] = True
                out["cold_draw"] = 1
                fixable = ev["t0"] < comfort or (dhw_min - (ev["t0"] - ev["k"]) <= DHW_MARGIN_MAX + 1e-6)
                if dhw_max is not None:
                    fixable = fixable and ev["t0"] < dhw_max - 1e-6
                    if dhw_min is not None and ev["k"] > dhw_max - dhw_min:
                        fixable = False  # kein Ziel bis zum Maximum reicht dafür
                out["raise_margin"] = int(ev["bound"] and fixable)
            self.t_sim -= draw_k
        cooled = self.t_sim - model.cooling_kh(self.t_sim) * dt_h
        # Strategie lädt nach, sobald sie unter ihr Ziel fällt (Energie wird gleich verbucht);
        # ab da ist es wieder ihr eigener Stand, nicht mehr der der Sonderladung
        if cooled < target:
            self.t_sim, self._ride = target, False
        else:
            self.t_sim = cooled
        c = model.kwh_per_kelvin()
        if c is not None:
            out["loss_kwh"] = c * model.cooling_kh(self.t_sim) * dt_h
        return out
