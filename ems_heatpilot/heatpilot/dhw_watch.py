"""Warmwasser am Fühler beobachten: Zapfungen, Nachlauf, Fühlerstufe – und der Schatten der Strategie.

``DrawWatch`` erkennt Zapfungen über ein Zeitfenster und sammelt ruhige Abschnitte fürs Auskühlen,
``tail_rising`` erkennt den Nachlauf nach dem Ladesignal, ``sensor_step_of`` die Stufe des
Speicherfühlers. ``DhwShadow`` spielt die Spar-Strategie parallel zur Wirklichkeit durch (Ersparnis,
zu kalte Zapfungen). Gelernt wird hier nichts – das macht ``DhwModel`` in ``dhw.py`` mit diesen Bausteinen.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .guards import DHW_MARGIN_MAX

if TYPE_CHECKING:
    from .dhw import DhwModel

SEGMENT_H = 0.5       # Auskühlen über so lange ruhige Abschnitte lernen
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
DRAW_QUIET_H = 10 / 60  # fällt der Speicher so lange …
DRAW_GROW_K = 0.25     # … um weniger als das zusätzlich: Zapfung vorbei
DRAW_MAX_H = 1.0       # länger zählt nichts mehr zur selben Zapfung
SEG_EXCESS_KH = 1.0    # ruhiger Abschnitt darf höchstens so viel schneller fallen als das Modell
# Gröbere Speicherfühler (0,5- oder 1-K-Stufen): eine einzelne Stufe läge über DRAW_OPEN_K, kein
# ruhiger Abschnitt käme je zustande und das Auskühlen würde als 0 gelernt. Darum wird die Fühler-
# stufe aus den Messwerten geschätzt (kleinster häufiger Betrag von d_t ≠ 0) und hebt die Schwellen:
# offen ab max(DRAW_OPEN_K, Stufe + 0,1), Zapfung ab max(DRAW_WIN_K, 1,5 × Stufe). Bei 0,1 K ändert
# sich nichts.
SENSOR_STEP = 0.1          # Standard-Fühlerstufe
SENSOR_STEP_N = 300        # so viele Schritte mit Änderung bilden das gleitende Fenster
SENSOR_STEP_MIN_N = 30     # vorher bleibt der Standard bzw. der gespeicherte Wert
SENSOR_STEP_SHARE = 0.1    # Stufe = kleinster Betrag, der mindestens so oft vorkommt (keine Ausreißer)
# Zapfereignisse – gelten für das Zapfprofil (DhwModel) und den Schatten gleich
HIT_K = 1.0               # ab so viel Temperaturabfall in einer Stunde zählt sie als „mit Zapfung“
SPECIAL_COVER_H = 12.0     # so lange danach ist eine große Zapfung durch die Sonderladung gedeckt
COLD_HOLD_H = 0.5      # Zapfschritte mit weniger Abstand sind ein Ereignis (Reserve, Komfortzähler)
# Schatten (0.46.2): Die Zapfung eines Messschritts gilt als gleichmäßig über den Schritt verteilt, höchstens so
# lange – nur so lange lädt der Kessel im Schatten schon während des Zapfens nach. Eine Dauer innerhalb des
# Schritts misst HeatPilot nicht; 5 min ist kurz (eine Dusche dauert meist länger), längere Zapfungen ergeben
# sich aus mehreren Schritten
DRAW_SPREAD_H = 5 / 60


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
             mirror: float | None = None, top: float | None = None) -> dict:
        """``target``: darunter lädt die Strategie nach – seit 0.46 bis ``top`` (Soll; ``target`` = Soll − Hysterese,
        wie der Kessel), ohne ``top`` bis ``target``. ``cold_draw``: Ereignis war zu kalt (Kennzahl). ``raise_margin``:
        zu kalt, und mehr Reserve hätte das Ziel davor angehoben (``margin_bound``) – nur dann lohnt sie (seit 0.46
        immer, wenn jemand zu Hause und wach ist: dort gilt Komfort + Reserve). Ohne Angabe wie bisher. Beides
        gilt so, wie es vor dem Ereignis stand. Ist der Fehlbetrag größer als die höchste Reserve oder
        die Zapfung größer als dhw_max − dhw_min, hilft keine Reserve: nur Kennzahl.

        ``mirror``: Speichertemperatur, solange der Regler eine Sonderladung fährt (Legionellen-
        schaltung). Die kann keine Strategie verhindern – der Schatten bekommt sie genauso, sonst
        spart er Verluste ein, die es nicht gibt. Eine Zapfung, die er nur dank dieser Wärme bedient
        (Wanne nach der Legionellenschaltung: beginnt warm, zieht durch ihre Größe unter das Minimum),
        zählt weder als zu kalt noch für die Reserve – höchstens SPECIAL_COVER_H lang, und nicht, wenn
        der Schatten sie schon unter dem Komfortwert bedient. Seit 0.36.3 kommt ``mirror`` auch einmal nach der
        Ladedauer eines bestätigten Desinfektionstermins (Desinfektionstemperatur, ``dhw_disinfect.shadow_mirror``):
        Die Strategie desinfiziert dann selbst – danach gilt dasselbe wie nach der Sonderladung des Reglers.

        Seit 0.46.2 ist der Komfortwert das Soll (Speicher zwischen Komfort − Hysterese und Komfort): Mit ``top``
        heißt „zu kalt“ unter Komfort − Hysterese (``top − target``), sonst zählte jede Zapfung im üblichen
        Band und die Reserve wüchse um die Hysterese nach. Ebenfalls seit 0.46.2 zählt für „unter das Minimum“
        der tiefste Stand im Ereignis, mit dem Nachladen des Kessels: Fällt der Speicher während der Zapfung
        unter ``target``, lädt er mit der gelernten Laderate für die restliche Zapfdauer nach (``_event_step``).
        """
        out = {"loss_kwh": 0.0, "cold_draw": 0, "raise_margin": 0}
        if t_actual is None:
            return out
        if top is not None:
            comfort -= max(top - target, 0.0)
        if self.t_sim is None:
            self.t_sim = t_actual
        if mirror is not None and mirror > self.t_sim:
            self.t_sim, self._ride, self._ride_h = mirror, True, 0.0
        elif self._ride:
            self._ride_h += dt_h
            self._ride = self._ride_h <= SPECIAL_COVER_H + 1e-6
        ev = self._ev
        rate, full = max(model.charge_rate_kh, 5.0), max(target, top if top is not None else target)
        if ev is not None:
            ev["quiet"] += dt_h
            if ev["quiet"] > COLD_HOLD_H + 1e-6:
                ev = self._ev = None
            elif draw_k <= 0:
                _event_step(ev, 0.0, dt_h, target, rate, full)  # zwischen den Zapfschritten lädt er weiter
        if draw_k > 0:
            if ev is None:
                ev = self._ev = {"t0": self.t_sim, "k": 0.0, "quiet": 0.0, "counted": False,
                                 "bound": margin_bound is None or bool(margin_bound),
                                 "special": self._ride and self.t_sim >= comfort - 1e-6,
                                 "t": self.t_sim, "low": self.t_sim, "on": False}
            ev["k"] += draw_k
            ev["quiet"] = 0.0
            _event_step(ev, draw_k, dt_h, target, rate, full)
            under = dhw_min is not None and ev["low"] < dhw_min
            short = ev["t0"] < comfort or under
            if not ev["counted"] and ev["k"] >= HIT_K and short and not ev["special"]:
                ev["counted"] = True
                out["cold_draw"] = 1
                fixable = ev["t0"] < comfort or (dhw_min - ev["low"] <= DHW_MARGIN_MAX + 1e-6)
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
            self.t_sim, self._ride = max(target, top if top is not None else target), False
        else:
            self.t_sim = cooled
        c = model.kwh_per_kelvin()
        if c is not None:
            out["loss_kwh"] = c * model.cooling_kh(self.t_sim) * dt_h
        return out


def _event_step(ev: dict, k: float, dt_h: float, hold: float, rate: float, full: float) -> None:
    """Speicher im Zapfereignis (``ev``: ``t``, ``low``, ``on``) einen Messschritt weiter.

    Die Zapfung ``k`` läuft gleichmäßig über min(Schritt, ``DRAW_SPREAD_H``), der Rest des Schritts ohne Zapfung.
    Fällt der Speicher unter ``hold`` (Soll − Hysterese), lädt der Kessel wie der echte – auch während des
    Zapfens – mit ``rate`` (gelernte Laderate, K/h) bis ``full`` (Soll). ``low``: tiefster Stand im Ereignis.
    Auskühlen in diesen Minuten bleibt außen vor (wie bisher beim Vergleich mit dem Minimum).
    """
    t = ev["t"]
    d = min(dt_h, DRAW_SPREAD_H) if k > 0 else 0.0
    if d > 0:
        r = k / d
        start = 0.0 if ev["on"] else max(t - hold, 0.0) / r  # ab da lädt der Kessel mit
        if start < d:
            ev["on"] = True
            end = t - k + rate * (d - start)
            ev["low"] = min(ev["low"], t - r * start, end)  # beim Einschalten bzw. am Zapfende
            t = min(end, full)
        else:
            t -= k
            ev["low"] = min(ev["low"], t)
    if ev["on"]:
        t = min(t + rate * (dt_h - d), full)
        ev["on"] = t < full - 1e-6
    ev["t"] = t
