"""Ventilmodell je Raum (seit 0.39): wie das Thermostat sein Ventil abhängig von Soll − Raumtemperatur stellt.

Grundlage für „Thermostate als Stellglied“ (Planungspapier): Der eingebaute Regler des Thermostats wird später über
fein verschobene Sollwerte geführt – dafür muss HeatPilot wissen, wie das Ventil reagiert. Gelernt wird nur aus den
eigenen Messwerten (``room_samples``: Raumtemperatur, Soll, Ventil in %), ohne Gerätewerte von Herstellern; das
Aufbereiten steht in ``valve_learn``, die Vorhersage in ``valve_predict``. Diese Datei hält das Gelernte und bewertet
es, sie schreibt nichts.

* **Kennlinie im Beharren:** Ventil je Regelabweichung e = Soll − Raum in 0,1-K-Klassen (−1,0 … +2,0 K), als
  Häufigkeiten mit Vergessen (Halbwertszeit ``HALF_LIFE_D``); je Klasse der Median, über die Klassen monoton
  geglättet. Daraus Öffnungsbeginn ``e0`` (> 5 %), halb offen ``e50``, ganz offen ``e100`` (≥ 90 %), Totband.
* **Sprungantwort** auf Soll-Sprünge ab 0,5 K: Totzeit (bis 5 Prozentpunkte Änderung) und Zeit bis 63 % der neuen
  Stellung, Median der letzten Sprünge, getrennt für auf und zu; dazu die Messpause (lang: Zeiten nur grob).
* **PI mit Sättigung:** v = clamp(Kp·e + I, 0, 100), I = clamp(I + Ki·(e − e_i)·Δt, 0, 100) (Anti-Windup), e um die
  Totzeit verzögert, e_i ein Nullpunkt (Totband des Reglers); kleinste Quadrate auf 30-min-Abschnitten ohne Sättigung,
  das Messrauschen der Raumtemperatur herausgerechnet (sonst wäre Kp zu klein). Ist die Nachstellzeit Kp/Ki länger als
  ``TN_P_MIN``, gilt das Thermostat als **P-Regler**: Die Kennlinie ist dann eine Eigenschaft des Geräts und die
  Vorhersage folgt ihr; mit Integrator hängt sie von der Last ab und wird nur gezeigt, nicht verwendet.
* **Gütemaß:** mittlerer absoluter Fehler der vorhergesagten Ventilstellung (ein Messschritt und 30 min) über die
  letzten 14 Tage, in Prozentpunkten – und zum Vergleich der Fehler von „Ventil bleibt, wie es ist“.
* **Nur an/aus** (kein Ventilsensor, Ventil aus ``hvac_action``; oder die Werte sind nur 0 und 100): statt der
  Kennlinie die Schaltschwellen – ein ab ``e_on``, aus ab ``e_off`` – und wie lange es nach einem Soll-Sprung dauert.
* **Art gewechselt** (seit 0.45.1): Meldet ein Raum mit stetigem %-Modell nur noch an/aus (Ventilsensor gelöscht),
  bleibt das Modell – es ruht (``no_pct``: nicht belastbar, vergisst nichts) und lernt mit %-Werten weiter.
* **Belastbar**, wenn ≥ 8 Sprünge (≥ 4 je Richtung, Reaktionszeiten ohne große Streuung), ≥ 48 h Beharrung, der
  30-min-Fehler ≤ 20 Prozentpunkte und deutlich kleiner als bei „Ventil bleibt“; beim P-Regler wandert die Kennlinie
  nicht. Sonst „lernt“ mit Grund.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from . import valve_predict as vp
from .valve_predict import OPEN_PCT, SAT_HI, SAT_LO, clamp  # noqa: F401 – für valve_learn/valve_dry hier zu holen
from .valve_text import de_num as _n
from .valve_text import text, whole_min
from .values import local

VERSION = 1                 # Stand des Ventilmodells; ein anderer gespeicherter Stand wird aus der Historie neu gelernt
E_MIN, E_STEP, N_CLASSES = -1.0, 0.1, 31   # Klassen der Regelabweichung −1,0 … +2,0 K
V_STEP, N_BINS = 5.0, 21    # Ventil in 5-%-Stufen 0 … 100
HALF_PCT = 50.0
FULL_PCT = 90.0             # ab hier „ganz offen“
JUMP_K = 0.5                # Soll-Sprung ab so viel Kelvin
MOVE_PCT = 5.0              # Totzeit: bis sich das Ventil um so viele Prozentpunkte bewegt
RESP_MIN_PCT = 10.0         # ein Sprung zählt nur, wenn das Ventil sich mindestens so weit bewegt
RESP_KEEP = 30              # so viele Sprünge je Richtung merken
SWITCH_KEEP = 50            # so viele Schaltpunkte je Richtung merken
HALF_LIFE_D = 30.0          # Kennlinie, PI, Rauschen und Zählung der Ventilwerte vergessen mit dieser Halbwertszeit
CLASS_MIN_H = 0.5           # Klasse zählt erst mit so vielen Stunden
PI_MIN_N = 30               # so viele 30-min-Abschnitte ohne Sättigung, bevor Kp/Ki gelten
I_ZERO_MAX = 0.5            # Nullpunkt des Integrators höchstens so weit vom Soll (K)
TN_P_MIN = 180.0            # Nachstellzeit Kp/Ki (min) mindestens so lang: wie ein P-Regler (Kennlinie gilt)
NOISE_MIN_N = 50            # so viele Rausch-Stichproben, bevor das Rauschen herausgerechnet wird
SIGNAL_MIN = 0.25           # höchstens so viel der Streuung von Δe darf Rauschen sein, sonst ist Kp „unsicher“
SWITCH_MIN = 3              # so viele Schaltpunkte je Richtung für die Schwellen
ERR_DAYS = 14               # Gütemaß über so viele Tage
ERR_MIN_N = 50              # so viele 30-min-Vergleiche, bevor das Gütemaß zählt
RELIABLE_JUMPS = 8
RELIABLE_DIR_JUMPS = 4      # … davon mindestens so viele je Richtung
RELIABLE_SPREAD = 1.0       # Reaktionszeiten je Richtung: (Q3 − Q1) / Median höchstens
RELIABLE_STEADY_H = 48.0
RELIABLE_ERR = 20.0         # Prozentpunkte
RELIABLE_VS_KEEP = 0.7      # 30-min-Fehler höchstens so viel von dem bei „Ventil bleibt, wie es ist“
E50_DAYS, E50_MIN_N, E50_SPREAD_K = 14, 3, 0.3  # P-Regler: „halb offen“ wandert in 14 Tagen höchstens so weit
SERIES = {"P": 5, "PI": 7}  # Stelle der Fehlersumme je Vorhersager in einer Gütemaß-Zelle (danach die Zahl)
BINARY_SHARE = 0.02         # weniger Zwischenwerte (5…95 %) als dieser Anteil: Ventil kennt nur an/aus …
BINARY_MIN_N = 500          # … nach so vielen Messwerten (nur solche, die etwas sagen: Heizbetrieb, Ventil im Spiel)
NO_PCT_TEXT = "nur an/aus gemeldet – das %-Modell bleibt und lernt erst mit %-Werten weiter"  # 0.45.1


def e_class(e: float) -> int:
    """Klasse der Regelabweichung; außerhalb −1 … +2 K die Randklasse (Ventil dort ohnehin zu bzw. offen)."""
    return min(max(round((e - E_MIN) / E_STEP), 0), N_CLASSES - 1)


def class_e(i: int) -> float:
    return round(E_MIN + i * E_STEP, 1)


def isotonic(points: list[tuple[float, float, float]]) -> list[tuple[float, float]]:
    """Monoton steigende Glättung (Pool Adjacent Violators): [(x, y, Gewicht)] nach x sortiert → [(x, y)]."""
    blocks: list[list[float]] = []  # [Summe y·w, Summe w, Anzahl]
    for _, y, w in points:
        blocks.append([y * w, w, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            b = blocks.pop()
            blocks[-1] = [blocks[-1][0] + b[0], blocks[-1][1] + b[1], blocks[-1][2] + b[2]]
    out, i = [], 0
    for s, w, n in blocks:
        for _ in range(int(n)):
            out.append((points[i][0], s / w))
            i += 1
    return out


def crossing(curve: list[tuple[float, float]], thr: float, strict: bool = False) -> float | None:
    """Regelabweichung, bei der die Kennlinie ``thr`` erreicht (linear zwischen den Klassen); None: nie bzw. schon
    in der ersten Klasse (dann liegt der Punkt außerhalb des Bereichs)."""
    for i, (x, y) in enumerate(curve):
        if y > thr if strict else y >= thr:
            if i == 0:
                return None
            x0, y0 = curve[i - 1]
            return round(x0 + (thr - y0) / (y - y0) * (x - x0), 2) if y != y0 else x
    return None


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Kleines lineares Gleichungssystem (Gauß mit Pivotsuche); None, wenn es (fast) singulär ist."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    scale = max(abs(m[i][i]) for i in range(n)) or 1.0
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-10 * scale:
            return None
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _spread(values: list[float]) -> dict | None:
    """Streuung: Quartile und (Q3 − Q1) / Median – ab ``RELIABLE_DIR_JUMPS`` Werten."""
    if len(values) < RELIABLE_DIR_JUMPS:
        return None
    q1, med, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"q1": round(q1, 1), "q3": round(q3, 1), "ratio": round((q3 - q1) / med, 2) if med > 0 else None}


def _day_no(day: str) -> int:
    return datetime.fromisoformat(day).toordinal()


@dataclass
class ValveModel:
    """Gelerntes Ventilverhalten eines logischen Raums (Gruppen: Mittel der Ventile). ``binary_dev``: kein Gerät
    des Raums hat einen Ventilsensor – das Ventil ist aus ``hvac_action`` nur 0 oder 100 %."""

    binary_dev: bool = False
    hist: list = field(default_factory=lambda: [[0.0] * N_BINS for _ in range(N_CLASSES)])  # Stunden je Klasse/Stufe
    steady_h: float = 0.0
    jumps: dict = field(default_factory=lambda: {"up": [], "down": []})  # [[Totzeit, t63, Messpause], …] in Minuten
    switches: dict = field(default_factory=lambda: {"on": [], "off": []})  # Regelabweichung beim Schalten
    pi: list = field(default_factory=lambda: [0.0] * 10)  # Summen der Regression (siehe add_pi), n – mit Vergessen
    noise: list = field(default_factory=lambda: [0.0, 0.0])  # Σ geschätztes |Rauschen| der Raumtemperatur, n
    err: dict = field(default_factory=dict)  # Tag → [Σ|Fehler| 1 Schritt, n, 30 min, n, Σ|Ventil bleibt|, P, n, PI, n]
    e50_days: dict = field(default_factory=dict)  # Tag → „halb offen“ an diesem Tag (wandert die Kennlinie?)
    skipped_h: dict = field(default_factory=lambda: {"fenster": 0.0, "fremdwaerme": 0.0, "heizung_aus": 0.0})
    valve_n: float = 0.0                                 # Messwerte mit Ventil, die etwas sagen …
    inter_n: float = 0.0                                 # … davon zwischen 5 und 95 %
    seen_kind: str | None = None                         # Art, unter der Sprünge und Gütemaß gesammelt sind
    learned_until: int | None = None                     # bis hier sind die Messwerte gelernt
    decay_ts: int | None = None
    version: int = VERSION
    no_pct: bool = field(default=False, compare=False)   # 0.45.1: %-Modell, der Raum meldet nur an/aus (ruht)

    # ------------------------------------------------------------------ Eigenschaften
    @property
    def kind(self) -> str:
        """„binär“ (nur an/aus) oder „stetig“."""
        if self.binary_dev or (self.valve_n >= BINARY_MIN_N and self.inter_n < BINARY_SHARE * self.valve_n):
            return "binär"
        return "stetig"

    def curve(self) -> list[dict]:
        """Kennlinie: je Klasse mit genug Daten der Median des Ventils, monoton geglättet."""
        pts = []
        for i, bins in enumerate(self.hist):
            w = sum(bins)
            if w < CLASS_MIN_H:
                continue
            half, cum = w / 2, 0.0
            for b, wb in enumerate(bins):
                if wb > 0 and cum + wb >= half:
                    med = clamp(b * V_STEP - V_STEP / 2 + V_STEP * (half - cum) / wb)
                    break
                cum += wb
            pts.append((class_e(i), med, w))
        smooth = isotonic(pts)
        return [{"e": x, "valve": round(y, 1), "hours": round(p[2], 1)} for (x, y), p in zip(smooth, pts)]

    def points(self) -> dict:
        """Öffnungsbeginn, halb und ganz offen (K unter Soll; negativ = über Soll) und das Totband."""
        c = [(p["e"], p["valve"]) for p in self.curve()]
        e0 = crossing(c, OPEN_PCT, strict=True)
        return {"e0": e0, "e50": crossing(c, HALF_PCT), "e100": crossing(c, FULL_PCT),
                "deadband_k": None if e0 is None else round(max(e0, 0.0), 2)}

    def response(self) -> dict:
        """Je Richtung: Zahl der Sprünge, Mediane von Totzeit, 63-%-Zeit und Messpause (min), Streuung; „grob“, wenn
        die Messpause länger als die halbe 63-%-Zeit ist (die Zeiten liegen dann eher zu lang)."""
        out = {}
        for d in ("up", "down"):
            js = self.jumps[d]
            t63 = _median([j[1] for j in js])
            step = _median([j[2] for j in js if len(j) > 2])
            out[d] = {"n": len(js), "dead_min": _median([j[0] for j in js]), "t63_min": t63,
                      "spread": _spread([j[1] for j in js]), "step_min": step,
                      "coarse": step is not None and t63 is not None and step > t63 / 2}
        return out

    def dead_s(self) -> float:
        """Totzeit für das PI-Modell (Median aller Sprünge, s); ohne Sprünge 0."""
        all_dead = [j[0] for d in ("up", "down") for j in self.jumps[d]]
        return 60.0 * statistics.median(all_dead) if all_dead else 0.0

    def taus(self) -> tuple[float, float]:
        """Zeitkonstanten des Stellmotors (min), öffnen und schließen: 63-%-Zeit − Totzeit; ohne Sprünge 0."""
        r = self.response()
        out = []
        for d in ("up", "down"):
            dead, t63 = r[d]["dead_min"], r[d]["t63_min"]
            out.append(0.0 if dead is None or t63 is None or self.kind == "binär" else max(t63 - dead, 0.0))
        return out[0], out[1]

    def noise_k(self) -> float | None:
        """Geschätztes Messrauschen der Raumtemperatur (Standardabweichung, K); None mit zu wenig Stichproben."""
        s, n = self.noise
        return None if n < NOISE_MIN_N else round(s / n * 1.2533, 3)  # mittlerer |Fehler| · √(π/2)

    def _regression(self) -> tuple[list, list, float]:
        """(Matrix, rechte Seite, Signalanteil von Δe) – das Messrauschen ist aus Σ Δe² herausgerechnet: Rauschen am
        Anfang und Ende eines Abschnitts macht Δe größer, ohne dass das Ventil folgt (Kp sonst zu klein); höchstens bis
        auf ``SIGNAL_MIN``, darunter gilt Kp als unsicher."""
        s11, s12, s13, s22, s23, s33, y1, y2, y3, n = self.pi
        sigma, share = self.noise_k(), 1.0
        if sigma and s11 > 0:
            share = max(1.0 - 2 * sigma**2 * n / s11, SIGNAL_MIN)
            s11 *= share
        return [[s11, s12, s13], [s12, s22, s23], [s13, s23, s33]], [y1, y2, y3], share

    def gains(self) -> tuple[float | None, float | None, float]:
        """(Kp in %/K, Ki in %/(K·min), Nullpunkt des Integrators in K) aus den kleinsten Quadraten; Kp, Ki ≥ 0.

        Gelernt auf 30-min-Abschnitten ohne Sättigung: Δv = Kp·Δe + Ki·∫e dt − Ki·e_i·∫dt – über 30 min statt eines
        Messschritts, und das Rauschen der Raumtemperatur herausgerechnet (``_regression``). Der Nullpunkt ``e_i``
        (das Totband eines PI-Reglers) ist ein Achsabschnitt der Regression. None, solange zu wenig gesehen."""
        if self.pi[9] < PI_MIN_N or self.pi[0] <= 0 or self.pi[3] <= 0:
            return None, None, 0.0
        S, y, _ = self._regression()
        best = None
        for idx in ((0, 1, 2), (0, 1), (1, 2), (0,), (1,)):  # ∫dt nur mit Integrator
            th = _solve([[S[i][j] for j in idx] for i in idx], [y[i] for i in idx])
            if th is None:
                continue
            full = [0.0, 0.0, 0.0]
            for i, v in zip(idx, th):
                full[i] = v
            if full[0] < 0 or full[1] < 0:
                continue
            cost = sum(full[i] * S[i][j] * full[j] for i in range(3) for j in range(3)) - 2 * sum(
                full[i] * y[i] for i in range(3))
            if best is None or cost < best[0] - 1e-9:
                best = (cost, full)
        if best is None:
            return None, None, 0.0
        kp, ki, c = best[1]
        e_i = min(max(-c / ki, -I_ZERO_MAX), I_ZERO_MAX) if ki > 1e-6 else 0.0
        return kp, ki, e_i

    def regler(self, now_day: str | None = None) -> str | None:
        """„an/aus“, „P“ (Vorhersage entlang der Kennlinie – sie gehört dann zum Gerät) oder „PI“ (mit Integrator –
        die Kennlinie hängt von der Last ab); None, solange Kp/Ki fehlen. Gewählt wird, was über 30 min besser
        vorhergesagt hat (beide werden immer mitgerechnet); bis beide genug Vergleiche haben, nach der Nachstellzeit."""
        if self.kind == "binär":
            return "an/aus"
        kp, ki, _ = self.gains()
        if kp is None:
            return None
        if len(self.curve()) < 2:
            return "PI"
        (ep, np_), (ei, ni) = self.series("P", now_day), self.series("PI", now_day)
        if np_ >= ERR_MIN_N and ni >= ERR_MIN_N:
            return "P" if ep <= ei else "PI"
        return "P" if ki * TN_P_MIN <= kp else "PI"

    def switch_e(self) -> tuple[float | None, float | None]:
        """(e_on, e_off) eines Ventils, das nur an/aus kennt – Mediane der Schaltpunkte."""
        on, off = self.switches["on"], self.switches["off"]
        if len(on) < SWITCH_MIN or len(off) < SWITCH_MIN:
            return None, None
        e_on, e_off = statistics.median(on), statistics.median(off)
        if e_on < e_off:  # widersprüchlich (selten): eine Schwelle in der Mitte
            e_on = e_off = (e_on + e_off) / 2
        return round(e_on, 2), round(e_off, 2)

    def open_at(self) -> float | None:
        """Ab dieser Regelabweichung öffnet bzw. schaltet das Ventil laut Kennlinie bzw. Schaltpunkten."""
        return self.switch_e()[0] if self.kind == "binär" else self.points()["e0"]

    # ------------------------------------------------------------------ Vorhersage (valve_predict)
    def candidates(self) -> dict:
        """Vorhersager zur Wahl (``valve_predict``): „an/aus“ mit den Schaltpunkten; stetig „P“ entlang der Kennlinie
        und „PI“ mit Integrator, jeweils mit dem Stellmotor – sobald Kp/Ki gelernt sind."""
        if self.kind == "binär":
            e_on, e_off = self.switch_e()
            return {} if e_on is None else {"an/aus": vp.Binary(e_on, e_off)}
        kp, ki, e_i = self.gains()
        if kp is None:
            return {}
        up, down = self.taus()
        out = {"PI": vp.PI(kp, ki, e_i, up, down)}
        pts = [(p["e"], p["valve"]) for p in self.curve()]
        if len(pts) >= 2:
            out["P"] = vp.Curve(pts, up, down)
        return out

    def predictor(self):
        """Der gewählte Vorhersager (``regler``); None, solange das Modell nichts vorhersagen kann."""
        return self.candidates().get(self.regler())

    def step(self, v_prev: float, e_prev: float, e_new: float, dt_min: float) -> float | None:
        """Ventilstellung nach ``dt_min`` Minuten, wenn die Regelabweichung von ``e_prev`` auf ``e_new`` geht."""
        p = self.predictor()
        return None if p is None else p.step(p.start(v_prev, e_prev), e_new, dt_min)

    def predict_path(self, v_now: float, e_now: float, minutes: float, e_end: float = 0.0) -> float | None:
        """Ventil nach ``minutes``, wenn die Regelabweichung gleichmäßig von ``e_now`` auf ``e_end`` geht
        (1-min-Schritte, mit Totzeit); None ohne Vorhersage."""
        p = self.predictor()
        if p is None:
            return None
        n = max(round(minutes), 1)
        es = [e_now + (e_end - e_now) * k / n for k in range(n + 1)]
        return round(vp.path(p, v_now, es, self.dead_s() / 60)[-1], 1)

    # ------------------------------------------------------------------ Lernen (aufbereitet von valve_learn)
    def decay(self, ts: int) -> None:
        """Kennlinie, Beharrungsstunden, PI-Summen, Rauschen und Zählung vergessen bis ``ts`` (``HALF_LIFE_D``)."""
        if self.decay_ts is not None and ts > self.decay_ts:
            f = 0.5 ** ((ts - self.decay_ts) / (HALF_LIFE_D * 86400))
            self.hist = [[w * f for w in bins] for bins in self.hist]
            self.steady_h *= f
            self.pi = [v * f for v in self.pi]
            self.noise = [v * f for v in self.noise]
            self.valve_n *= f
            self.inter_n *= f
        self.decay_ts = ts if self.decay_ts is None else max(self.decay_ts, ts)

    def note_kind(self) -> None:
        """Hat die Art gewechselt (Daten zeigen jetzt an/aus bzw. stetig), gehören Sprünge, Schaltpunkte und Gütemaß
        zur alten Art – neu sammeln (an/aus misst die Schaltzeit, nicht die 63-%-Zeit)."""
        k = self.kind
        if self.seen_kind is not None and self.seen_kind != k:
            self.jumps = {"up": [], "down": []}
            self.switches = {"on": [], "off": []}
            self.err = {}
        self.seen_kind = k

    def add_steady(self, e: float, valve: float, hours: float) -> None:
        self.hist[e_class(e)][round(clamp(valve) / V_STEP)] += hours
        self.steady_h += hours

    def count_valve(self, valve: float) -> None:
        self.valve_n += 1
        self.inter_n += OPEN_PCT < valve < 100 - OPEN_PCT

    def add_noise(self, abs_dev: float) -> None:
        self.noise[0] += abs_dev
        self.noise[1] += 1

    def add_jump(self, direction: str, dead_min: float, t63_min: float, step_min: float) -> None:
        entry = [round(dead_min, 2), round(t63_min, 2), round(step_min, 1)]
        self.jumps[direction] = (self.jumps[direction] + [entry])[-RESP_KEEP:]

    def add_switch(self, on: bool, e: float) -> None:
        key = "on" if on else "off"
        self.switches[key] = (self.switches[key] + [round(e, 3)])[-SWITCH_KEEP:]

    def add_pi(self, de: float, e_dt: float, dt: float, dv: float) -> None:
        """Ein 30-min-Abschnitt ohne Sättigung: Δe (K), ∫e dt (K·min), ∫dt (min), Δv (Prozentpunkte)."""
        for i, v in enumerate((de * de, de * e_dt, de * dt, e_dt * e_dt, e_dt * dt, dt * dt,
                               de * dv, e_dt * dv, dt * dv, 1.0)):
            self.pi[i] += v

    def add_err(self, day: str, horizon: int, err: float | None, keep: float | None = None,
                by: dict | None = None) -> None:
        """Ein Vergleich: Fehler des gewählten Vorhersagers (1 Schritt bzw. 30 min), bei 30 min dazu „Ventil bleibt“
        (``keep``) und die Fehler je Vorhersager (``by``: „P“, „PI“) – daraus wählt ``regler``."""
        cell = self.err.setdefault(day, [0.0, 0, 0.0, 0, 0.0, 0.0, 0, 0.0, 0])
        cell.extend([0.0] * (9 - len(cell)))
        k = 0 if horizon == 1 else 2
        if err is not None:
            cell[k] += abs(err)
            cell[k + 1] += 1
        if keep is not None:
            cell[4] += abs(keep)
        for name, e in (by or {}).items():
            if name in SERIES:
                cell[SERIES[name]] += abs(e)
                cell[SERIES[name] + 1] += 1

    def prune_err(self, newest_day: str) -> None:
        last = _day_no(newest_day)
        self.err = {d: v for d, v in self.err.items() if last - _day_no(d) < ERR_DAYS}

    def note_e50(self, day: str) -> None:
        """„Halb offen“ des Tages merken (die letzten ``E50_DAYS`` Tage)."""
        e50 = self.points()["e50"]
        if e50 is not None:
            self.e50_days[day] = e50
        last = _day_no(day)
        self.e50_days = {d: v for d, v in self.e50_days.items() if last - _day_no(d) < E50_DAYS}

    def _cells(self, now_day: str | None) -> list:
        if not self.err:
            return []
        last = _day_no(now_day or max(self.err))
        return [v for d, v in self.err.items() if 0 <= last - _day_no(d) < ERR_DAYS]

    def series(self, name: str, now_day: str | None = None) -> tuple[float | None, int]:
        """(Fehler über 30 min, Zahl) eines Vorhersagers („P“, „PI“) über die letzten ``ERR_DAYS`` Tage."""
        k = SERIES[name]
        cells = [c for c in self._cells(now_day) if len(c) > k]
        s, n = sum(c[k] for c in cells), sum(c[k + 1] for c in cells)
        return (round(s / n, 1) if n else None), int(n)

    def error(self, horizon: int = 30, now_day: str | None = None) -> tuple[float | None, int]:
        """(mittlerer absoluter Fehler in Prozentpunkten, Zahl der Vergleiche) über die letzten ``ERR_DAYS`` Tage – über
        30 min der des Vorhersagers, der jetzt gilt (beim stetigen Ventil „P“ oder „PI“)."""
        reg = self.regler(now_day) if horizon != 1 and self.kind != "binär" else None
        if reg in SERIES:
            return self.series(reg, now_day)
        k = 0 if horizon == 1 else 2
        cells = self._cells(now_day)
        s, n = sum(c[k] for c in cells), sum(c[k + 1] for c in cells)
        return (round(s / n, 1) if n else None), int(n)

    def keep_error(self, now_day: str | None = None) -> float | None:
        """Fehler über 30 min von „Ventil bleibt, wie es ist“ (Prozentpunkte) – der Vergleich für das Gütemaß."""
        cells = [c for c in self._cells(now_day) if len(c) > 4]
        n = sum(c[3] for c in cells)
        return round(sum(c[4] for c in cells) / n, 1) if n else None

    # ------------------------------------------------------------------ Bewertung
    def _jump_reasons(self) -> list[str]:
        r, out = self.response(), []
        n = r["up"]["n"] + r["down"]["n"]
        if n < RELIABLE_JUMPS:
            return [f"erst {n} von {RELIABLE_JUMPS} Soll-Sprüngen"]
        for d, word in (("up", "nach oben"), ("down", "nach unten")):
            sp = r[d]["spread"]
            if r[d]["n"] < RELIABLE_DIR_JUMPS:
                out.append(f"erst {r[d]['n']} Soll-Sprünge {word} (mindestens {RELIABLE_DIR_JUMPS} je Richtung)")
            elif self.kind != "binär" and sp and sp["ratio"] is not None and sp["ratio"] > RELIABLE_SPREAD:
                out.append(f"Reaktion {word} streut stark ({whole_min(sp['q1'])}–{whole_min(sp['q3'])} min)")
        return out

    def reliable(self, now_day: str | None = None) -> tuple[bool, list[str]]:
        """Belastbar? Mit Gründen, warum (noch) nicht. Die Streuung der Reaktion zählt nur bei stetigen Ventilen (sie
        gibt dort den Stellmotor der Vorhersage); bei an/aus hängt die Zeit bis zum Schalten vom Raum ab."""
        reasons = self._jump_reasons()
        if self.steady_h < RELIABLE_STEADY_H:
            reasons.append(f"erst {self.steady_h:.0f} von {RELIABLE_STEADY_H:.0f} h Beharrung")
        err, n30 = self.error(30, now_day)
        keep = self.keep_error(now_day)
        if n30 < ERR_MIN_N or err is None:
            reasons.append("Vorhersage noch nicht geprüft")
        elif err > RELIABLE_ERR:
            reasons.append(f"Vorhersagefehler über 30 min {err:.0f} Prozentpunkte, höchstens {RELIABLE_ERR:.0f}")
        elif keep is not None and err > RELIABLE_VS_KEEP * keep:
            reasons.append(f"Vorhersage kaum besser als „Ventil bleibt, wie es ist“ ({_n(err)} statt {_n(keep)} "
                           f"Prozentpunkte über 30 min)")
        reg = self.regler(now_day)
        if reg == "PI" and self._regression()[2] <= SIGNAL_MIN:  # die Vorhersage mit Integrator braucht Kp
            reasons.append(f"Messrauschen ≈ {_n(self.noise_k() or 0, 2)} K überdeckt die Verstärkung Kp")
        if reg == "P":
            vals = list(self.e50_days.values())
            if len(vals) >= E50_MIN_N and max(vals) - min(vals) > E50_SPREAD_K:
                reasons.append(f"Kennlinie wandert (halb offen zwischen {_n(min(vals))} und {_n(max(vals))} K in "
                               f"{E50_DAYS} Tagen)")
        return not reasons, reasons

    def usable(self, now_day: str | None = None, catching_up: bool = False,
               group: dict | None = None) -> tuple[bool, list[str]]:
        """Belastbar für diesen Raum (``reliable``), dazu: keine gemischte Gruppe, nicht beim Nachlernen aus der
        Historie – dasselbe Urteil für die Anzeige (``summary``) und die Ventilführung (``valve_control``, 0.40)."""
        ok, reasons = self.reliable(now_day)
        if self.no_pct:  # 0.45.1: nichts zum Prüfen – nicht führen, das Modell bleibt
            ok, reasons = False, [NO_PCT_TEXT, *reasons]
        if group and group.get("mixed"):
            ok, reasons = False, [*reasons, "Gruppe mit stetigen und an/aus-Ventilen – das Mittel beschreibt keines"]
        if catching_up:
            ok, reasons = False, ["lernt aus der gespeicherten Historie nach", *reasons]
        return ok, reasons

    def summary(self, step_k: float, step_source: str, now_ts: int | None = None, catching_up: bool = False,
                group: dict | None = None) -> dict:
        """Anzeige je Raum (Einstellungen → Räume, Raum-Dialog): Kennlinie, Punkte, Sprungantwort, Güte, Text.
        ``group``: {"n": Ventile, "mixed": stetige und an/aus gemischt} bei Gruppen."""
        day = None if now_ts is None else local(now_ts).date().isoformat()
        ok, reasons = self.usable(day, catching_up, group)
        kp, ki, e_i = self.gains()
        _, _, share = self._regression() if self.pi[0] > 0 else (None, None, 1.0)
        e_on, e_off = self.switch_e()
        err1, n1 = self.error(1, day)
        err30, n30 = self.error(30, day)
        resp, reg = self.response(), self.regler(day)
        out = {
            "kind": self.kind, "regler": reg, "load_dependent": reg == "PI",
            "state": "ruht" if self.no_pct else "belastbar" if ok else "lernt", "reliable": ok, "reasons": reasons,
            "no_pct": self.no_pct,
            "curve": self.curve(), **self.points(), "response": resp,
            "jumps": resp["up"]["n"] + resp["down"]["n"], "steady_h": round(self.steady_h, 1),
            "pi": None if kp is None else {
                "kp": round(kp, 1), "ki": round(ki, 3), "e_i": round(e_i, 2),
                "tn_min": round(kp / ki) if ki > 1e-6 else None, "noise_k": self.noise_k(),
                "kp_uncertain": share <= SIGNAL_MIN},
            "switch": None if self.kind != "binär" else {  # Schaltpunkte nur für Ventile, die nur an/aus kennen
                "e_on": e_on, "e_off": e_off, "n_on": len(self.switches["on"]), "n_off": len(self.switches["off"])},
            "error": {"step_pp": err1, "step_n": n1, "pp_30": err30, "n_30": n30, "days": ERR_DAYS,
                      "keep_pp_30": self.keep_error(day)},
            "skipped_h": {k: round(v, 1) for k, v in self.skipped_h.items()},
            "step_k": step_k, "step_source": step_source, "catching_up": catching_up, "group": group,
            "learned_until": None if self.learned_until is None else local(self.learned_until).isoformat(),
            "rules": {"jumps": RELIABLE_JUMPS, "dir_jumps": RELIABLE_DIR_JUMPS, "steady_h": RELIABLE_STEADY_H,
                      "err_pp": RELIABLE_ERR, "vs_keep": RELIABLE_VS_KEEP, "tn_p_min": TN_P_MIN, "jump_k": JUMP_K},
        }
        out["text"] = text(out)
        return out

    def to_dict(self) -> dict:
        return {"version": self.version, "binary_dev": self.binary_dev,
                "hist": [[round(w, 4) for w in bins] for bins in self.hist], "steady_h": round(self.steady_h, 3),
                "jumps": self.jumps, "switches": self.switches, "pi": [round(v, 6) for v in self.pi],
                "noise": [round(v, 4) for v in self.noise], "err": self.err, "e50_days": self.e50_days,
                "skipped_h": {k: round(v, 2) for k, v in self.skipped_h.items()},
                "valve_n": round(self.valve_n, 3), "inter_n": round(self.inter_n, 3), "seen_kind": self.seen_kind,
                "learned_until": self.learned_until, "decay_ts": self.decay_ts}

    @classmethod
    def from_dict(cls, d: dict | None, binary_dev: bool = False) -> ValveModel:
        """Gespeichertes Modell; fehlt es oder passt der Stand (``VERSION``) nicht, ein neues – es lernt dann aus der
        gespeicherten Historie nach. Seit 0.45.1 bleibt es, wenn sich die Geräteart ändert (dasselbe Ventil): Ein
        stetiges %-Modell bleibt stetig, solange der Raum nur an/aus meldet (``no_pct``: ruht, lernt erst mit
        %-Werten weiter); an/aus → % lernt stetig weiter. Neu nur mit „Ventil neu lernen“ bzw. „anderes Thermostat“."""
        if not d or d.get("version") != VERSION:
            return cls(binary_dev=binary_dev)
        m = cls(binary_dev=bool(d.get("binary_dev")))
        hist = d.get("hist") or []
        if len(hist) == N_CLASSES and all(len(b) == N_BINS for b in hist):
            m.hist = [list(b) for b in hist]
        m.steady_h = float(d.get("steady_h", 0.0))
        m.jumps = {k: list((d.get("jumps") or {}).get(k, [])) for k in ("up", "down")}
        m.switches = {k: list((d.get("switches") or {}).get(k, [])) for k in ("on", "off")}
        if len(d.get("pi") or []) == len(m.pi):
            m.pi = list(d["pi"])
        if len(d.get("noise") or []) == 2:
            m.noise = list(d["noise"])
        m.err = dict(d.get("err") or {})
        m.e50_days = dict(d.get("e50_days") or {})
        m.skipped_h.update(d.get("skipped_h") or {})
        m.valve_n, m.inter_n = float(d.get("valve_n", 0.0)), float(d.get("inter_n", 0.0))
        m.seen_kind, m.learned_until, m.decay_ts = d.get("seen_kind"), d.get("learned_until"), d.get("decay_ts")
        if m.binary_dev != binary_dev:
            m.binary_dev = binary_dev and m.kind == "binär"
        m.no_pct = binary_dev and not m.binary_dev
        return m
