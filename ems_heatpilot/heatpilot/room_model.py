"""Raummodell (pro Tado-Raum), gelernt alle 30 Minuten – seit 0.53 mit Ventilkennlinie und Heizkörper-Exponent.

    dT/dt = a * Heizkörper + b * (T_außen - T_raum) + c  (+ f * Fußboden, verzögert)

    Heizkörper = max(T_vorlauf - T_raum, 0)^n * Ventilöffnung^p * Heizbetrieb
    a  wie stark der Heizkörper den Raum erwärmt   [K/h je K^n]
    n, p  Form des Heizkörpers, je Raum aus Kandidaten gewählt (``FORMS``)
    b  wie schnell der Raum auskühlt (1/b = Zeitkonstante in h)
    c  Fremdwärme (Personen, Nachbarräume) [K/h]
    + g_Ost/Süd/West * Einstrahlung: Sonnenwärme durch die Fenster je Himmelsrichtung
    + w * Wind * (T_außen - T_raum): Wind kühlt zusätzlich aus (seit 0.54, je Raum gelernt, nie negativ)

Das Auskühlen ist damit ``b + w · Wind`` (``b`` schließt den Wind von jetzt ein, ``wind_now`` in m/s, von der Engine
je Takt gesetzt) – bei Sturm kühlt ein zugiger Raum schneller aus, ein geschützter lernt „kaum Wind“.

Bis 0.52 war der Heizkörper linear: (Vorlauf − Raum) · Ventil. An einem Export (7 Tage, 6 Räume, Vorhersage über
1 h) war das Modell beim Heizen durchweg zu langsam, bei mehr als 50 °C Vorlauf um rund 1 K/h. Zwei Ursachen ließen
sich belegen:

* **Ventilkennlinie:** Die gemeldete Heizleistung des Thermostats ist nicht der Durchfluss – schon eine kleine
  Öffnung bringt viel Wärme (die Leistung eines Heizkörpers wächst stark unterproportional mit dem Durchfluss, wie
  ``balance.POWER_EXP``). Mit Ventil^0,5 (p) sank der Fehler beim Heizen um 20 %.
* **Vorlauf:** Linear im Vorlauf war es bei hohem Vorlauf zu langsam und bei 35–40 °C eher zu schnell. Ein steilerer
  Exponent n senkte den Fehler um weitere 7 % (bei > 50 °C fast ohne Versatz). Der beste lag über dem Lehrbuchwert
  1,3 (Thermostat am Heizkörper, Hydraulik).

Darum laufen alle Kombinationen aus n (``FLOW_EXPS``) und p (``VALVE_EXPS``) mit, je eine mit eigenem RLS, und die
mit dem kleinsten Vorhersagefehler in Schritten mit Heizbetrieb gilt (wie die schnelle Auskühlphase im Hausmodell).
Bis genug Heizbetrieb gesehen ist – und solange keine andere um 2 % besser ist –, gilt die erste: linear wie bis
0.52. Ein Ventil, das wirklich linear wirkt (an einem künstlichen Raum geprüft), bleibt so unverzerrt.

Nicht übernommen, weil ohne messbaren Gewinn: Totzeit bzw. Speichermasse des Heizkörpers, ein eigener Term für den
Messort am Heizkörper, die Mitteltemperatur aus Vor- und Rücklauf.

Modelle von vor 0.53 werden beim Laden je Form umgerechnet (``a`` bei 20 K und halber Öffnung gleich, die lineare
Form bleibt, wie sie war) und, wo es eine
Historie gibt, mit diesen Werten als Start aus den gespeicherten Daten neu gelernt (``LearnMixin.migrate_rooms``).
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

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
# a gilt bei FLOW_REF_K (für steilere Exponenten umgerechnet, siehe ``_prior_rls``).
ROOM_PRIOR = {"a": (0.02, 0.05), "f": (0.01, 0.02), "b": (0.018, 0.03), "c": (0.0, 0.3), "sun": (0.0, 1.0),
              "w": (0.0, 0.003)}  # 0.54: Wind – Auskühlen je m/s (1/h); 10 m/s × 0,002 ≈ +100 % bei b = 0,02
# Fremdwärme-Erkennung (Kamin, Ofen, Gäste): Sonne und Fußboden gelten erst als gelernt, wenn das
# Modell so viele Schritte hat und die Unsicherheit der gerade angeregten Parameter auf die Hälfte
# des Startwerts gesunken ist – sonst verwürfe ein junges Modell seine eigene Lernchance als Ereignis
EXT_LEARNED_STEPS = 200
EXT_LEARNED_P_FRAC = 0.25   # P-Diagonale (Unsicherheit²) höchstens so viel vom Startwert

# 0.53: Ventilkennlinie und Heizkörper-Exponent (siehe Modulbeschreibung)
FLOW_EXPS = (1.0, 1.3, 1.6, 2.0)  # Kandidaten für n
VALVE_EXPS = (1.0, 0.5)           # Kandidaten für p (Wärme ∝ Öffnung^p)
FORMS = tuple((n, p) for p in VALVE_EXPS for n in FLOW_EXPS)  # je Form ein RLS
FORM_DEFAULT = 0           # (1, 1): linear wie bis 0.52, bis eine andere Form messbar besser ist
FLOW_REF_K = 20.0          # Bezug für die Heizwirkung in K/h je K (``a_ref``): 20 K über Raum, Ventil offen
SEL_MIN_STEPS = 48         # so viele (abklingende) Schritte mit Heizbetrieb, bevor ein Kandidat gewählt wird
SEL_DECAY = 0.995          # Fehlersumme für die Wahl (~200 Heizschritte Gedächtnis)
SEL_SWITCH = 0.98          # ein anderer Kandidat gilt erst, wenn er den gültigen um 2 % schlägt
MIGRATE_VALVE = 0.5        # Umrechnen von vor 0.53: gleiche Wirkung bei FLOW_REF_K und halber Öffnung
FORM = 2                   # Modellform im gespeicherten Stand (fehlt = bis 0.52, linear)


def drive(warm: float, valve: float, n: float = 1.0, p: float = 1.0) -> float:
    """Heizkörper-Eingang: ``warm`` (Vorlauf − Raum, K, mit Heizbetrieb) ^n · Öffnung (0…1) ^p."""
    if warm <= 0 or valve <= 0:
        return 0.0
    return warm ** n * valve ** p


def _migrated(old: RLS, n: float, p: float) -> RLS:
    """RLS von vor 0.53 für die Form (n, p): a (und seine Unsicherheit) so skaliert, dass die Wirkung bei
    FLOW_REF_K und MIGRATE_VALVE gleich bleibt – alles andere wie gelernt; (1, 1) bleibt unverändert."""
    r = RLS.from_dict(copy.deepcopy(old.to_dict()))  # to_dict reicht die Listen selbst weiter
    s = FLOW_REF_K * MIGRATE_VALVE / drive(FLOW_REF_K, MIGRATE_VALVE, n, p)
    r.theta[0] *= s
    for j in range(r.n):
        r.P[0][j] *= s
        r.P[j][0] *= s
    return r


@dataclass
class RoomModel:
    """Raummodell. Mit ``floor=True`` zusätzlich Fußbodenheizung ohne Stellventil.

    Fußbodenheizung (z. B. mit fest eingestelltem RTL-Ventil) heizt immer, wenn
    warmes Heizwasser fließt – unabhängig von Tado – und wirkt über den Estrich
    stark verzögert. Ihr Eingang wird deshalb mit ``floor_lag_h`` geglättet (linear):

        dT/dt = a * Heizkörper + f * Fußboden(verzögert) + b * (T_außen - T) + c

    Je Form (n, p) ein RLS (``rlss``); ``rls`` ist das gültige (``form_index``).
    """

    room: str
    floor: bool = False
    floor_lag_h: float = 3.0
    floor_u: float = 0.0  # geglätteter Fußboden-Eingang (K über Raumtemperatur)
    rlss: list | None = None
    # wie viel Heizbetrieb das Modell gesehen hat (K·h, linear wie bis 0.52, klingt langsam ab)
    excitation: float = 0.0
    form_index: int = FORM_DEFAULT
    sel_sse: list = field(default_factory=lambda: [0.0] * len(FORMS))
    sel_w: list = field(default_factory=lambda: [0.0] * len(FORMS))
    migrated: bool = False  # aus einem Modell von vor 0.53 umgerechnet (wird nicht gespeichert)
    wind_now: float = 0.0   # 0.54: Wind jetzt in m/s – von der Engine je Takt gesetzt (wird nicht gespeichert)

    def __post_init__(self) -> None:
        if self.rlss is None:
            self.rlss = [self._prior_rls(n) for n, _ in FORMS]
        for r in self.rlss:
            r.gate = ROOM_GATE

    def _prior_rls(self, n: float) -> RLS:
        keys = ["a"] + (["f"] if self.floor else []) + ["b", "c"] + ["sun"] * 3 + ["w"]
        s = FLOW_REF_K ** (1.0 - n)  # gleiche Wirkung bei FLOW_REF_K
        theta = [ROOM_PRIOR[k][0] * (s if k == "a" else 1.0) for k in keys]
        sd = [ROOM_PRIOR[k][1] * (s if k == "a" else 1.0) for k in keys]
        P = [[sd[i] ** 2 if i == j else 0.0 for j in range(len(keys))] for i in range(len(keys))]
        return RLS(n=len(keys), lam=ROOM_LAM, theta=theta, P=P, min_sigma=0.03)

    @property
    def rls(self) -> RLS:
        return self.rlss[self.form_index]

    @property
    def n(self) -> float:
        """Gültiger Heizkörper-Exponent (Vorlauf − Raum)."""
        return FORMS[self.form_index][0]

    @property
    def p(self) -> float:
        """Gültige Ventilkennlinie (Wärme ∝ Öffnung^p)."""
        return FORMS[self.form_index][1]

    def adopt_house_tau(self, tau_h: float | None) -> None:
        """Startwert fürs Auskühlen aus der gemessenen Trägheit des eigenen Hauses (nur vor dem ersten Lernen)."""
        for r in self.rlss:
            if tau_h and r.updates == 0:
                r.theta[2 if self.floor else 1] = min(max(1.0 / tau_h, 1 / 250), 1 / 15)

    @property
    def _base_n(self) -> int:
        return 4 if self.floor else 3

    @property
    def solar(self) -> tuple[float, float, float]:
        """Sonnenwärme Ost/Süd/West in K/h bei voller Sonne (nie negativ)."""
        th = self.rls.theta[self._base_n:self._base_n + 3]
        return tuple(round(max(v, 0.0), 3) for v in th) if len(th) == 3 else (0.0, 0.0, 0.0)

    @property
    def wind_gain(self) -> float:
        """Zusätzliches Auskühlen je m/s Wind (1/h, nie negativ) – 0, solange nichts gelernt ist."""
        th = self.rls.theta
        return max(th[self._base_n + 3], 0.0) if len(th) > self._base_n + 3 else 0.0

    @property
    def a(self) -> float:
        return max(self.rls.theta[0], 1e-4 * FLOW_REF_K ** (1.0 - self.n))

    @property
    def a_ref(self) -> float:
        """Heizwirkung in K/h je K bei FLOW_REF_K über Raum und offenem Ventil – vergleichbar über alle Exponenten."""
        return self.a * FLOW_REF_K ** (self.n - 1.0)

    @property
    def f(self) -> float:
        """Wirkung der Fußbodenheizung (0 ohne Fußbodenheizung)."""
        return max(self.rls.theta[1], 0.0) if self.floor else 0.0

    @property
    def b(self) -> float:
        """Auskühlen jetzt (1/h): gelernte Grundrate plus Wind (``wind_gain`` · ``wind_now``, seit 0.54)."""
        return max(self.rls.theta[2 if self.floor else 1], 1e-3) + self.wind_gain * max(self.wind_now, 0.0)

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

    def heat_input(self, parts: list[tuple[float, float]], form: tuple[float, float] | None = None) -> float:
        """Mittlerer Heizkörper-Eingang eines Schritts aus (Vorlauf − Raum mit Heizbetrieb, Öffnung 0…1) je Messpunkt."""
        n, p = FORMS[self.form_index] if form is None else form
        return sum(drive(w, v, n, p) for w, v in parts) / len(parts) if parts else 0.0

    def heating(self, flow: float, t_room: float, valve: float = 1.0) -> float:
        """Temperaturanstieg in K/h, den der Heizkörper allein bringt."""
        return self.a * drive(flow - t_room, valve, self.n, self.p)

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
        parts: list[tuple[float, float]] | None = None,
        wind: float | None = None,
    ) -> float:
        """Lernschritt aller Formen. ``heat_input``: linear (Vorlauf − Raum) · Öffnung wie bis 0.52 – zählt für
        den gesehenen Heizbetrieb; ``parts``: die Messpunkte dazu (Vorlauf − Raum, Öffnung 0…1), ohne gilt
        ``heat_input`` bei offenem Ventil. ``update=False`` führt nur den Estrich-Speicher nach (z. B. im Urlaub)."""
        t_mean = (t_start + t_end) / 2
        if self.floor:
            self.floor_u += (1 - math.exp(-dt_h / self.floor_lag_h)) * (floor_input - self.floor_u)
        if not update:
            return 0.0
        parts = parts if parts is not None else [(heat_input, 1.0)]
        rest = ([self.floor_u * dt_h] if self.floor else []) + [(t_out - t_mean) * dt_h, dt_h]
        rest += [v * dt_h for v in (solar or (0.0, 0.0, 0.0))] + [(wind or 0.0) * (t_out - t_mean) * dt_h]
        heated, err = heat_input * dt_h > 1e-6, 0.0
        for i, (form, r) in enumerate(zip(FORMS, self.rlss)):
            x = [self.heat_input(parts, form) * dt_h] + rest
            idle = [k for k, v in enumerate(x) if abs(v) < 1e-6]
            if self.floor and abs(x[1]) < 0.05 * dt_h:
                idle.append(1)
            e = r.update(x, t_end - t_start, idle=idle)
            if heated:  # Fehler vor dem Lernen, gedeckelt wie im RLS (ein Ausreißer entscheidet nicht die Wahl)
                sigma = max(r.rmse or 0.0, r.min_sigma)
                self.sel_sse[i] = SEL_DECAY * self.sel_sse[i] + min(e * e, (4 * sigma) ** 2)
                self.sel_w[i] = SEL_DECAY * self.sel_w[i] + 1.0
            if i == self.form_index:
                err = e
            self._sun_not_negative(r)
        self.excitation = EXCITATION_DECAY * self.excitation + heat_input * dt_h
        if heated:
            self._choose()
        return err

    def _choose(self) -> None:
        """Gültige Form wählen: kleinster mittlerer Fehler, sobald alle genug Heizbetrieb gesehen haben."""
        if min(self.sel_w) < SEL_MIN_STEPS:
            return
        mse = [s / w for s, w in zip(self.sel_sse, self.sel_w)]
        best = min(range(len(mse)), key=lambda i: mse[i])
        if best != self.form_index and mse[best] < SEL_SWITCH * mse[self.form_index]:
            self.form_index = best

    def _sun_not_negative(self, rls: RLS | None = None) -> None:
        """Sonne kann nicht kühlen und Wind nicht wärmen: negative Werte auf 0 projizieren (P-gewichtet).

        Die zusammenhängenden Parameter (Fremdwärme, Auskühlen, andere Richtungen) werden
        dabei optimal nachgeführt; P bleibt, damit der Wert später wieder wachsen kann.
        """
        for r in [rls] if rls is not None else self.rlss:
            th, P = r.theta, r.P
            for i in range(self._base_n, self._base_n + 4):
                if i < len(th) and th[i] < 0 and P[i][i] > 1e-12:
                    ti = th[i]
                    r.theta = th = [th[j] - P[j][i] / P[i][i] * ti for j in range(len(th))]
                    th[i] = 0.0

    def explained_rate(self, heat_input: float, t_out: float, t_mean: float,
                       solar: tuple[float, float, float] | None = None) -> float:
        """Temperaturänderung in K/h, die das Modell aus Heizkörper, Auskühlen, Fremdwärme und Sonne erklärt.

        ``heat_input`` in der gültigen Form (``heat_input(parts)``). Was darüber hinaus steigt, ist unerklärt –
        Kamin, Ofen, viele Gäste.
        """
        sun = sum(g * s for g, s in zip(self.solar, solar or (0.0, 0.0, 0.0)))
        return self.a * heat_input + self.b * (t_out - t_mean) + self.c_eff + sun

    def inputs_learned(self, solar: tuple[float, float, float] | None = None, floor: bool = False) -> bool:
        """Sind Sonne (angeregte Richtungen) bzw. Fußboden schon gelernt? Siehe EXT_LEARNED_*."""
        if self.rls.updates < EXT_LEARNED_STEPS:
            return False
        idx = [self._base_n + i for i, s in enumerate(solar or ()) if s >= 0.1]
        if floor and self.floor:
            idx.append(1)
        for i in idx:
            prior = ROOM_PRIOR["f" if i == 1 and self.floor else "sun"][1] ** 2
            if i >= len(self.rls.P) or self.rls.P[i][i] > EXT_LEARNED_P_FRAC * prior:
                return False
        return True

    def rate(self, t_room: float, flow: float, t_out: float, valve: float = 1.0) -> float:
        """Temperaturänderung in K/h bei gegebener Vorlauftemperatur."""
        return self.heating(flow, t_room, valve) + self.b * (t_out - t_room) + self.c_eff

    def equilibrium(self, flow: float, t_out: float, valve: float = 1.0) -> float:
        """Endtemperatur bei Dauerbetrieb – die Fußbodenheizung heizt dann mit (wie in required_flow)."""
        f, b, c = self.f, self.b, self.c
        if self.n == 1.0:
            a = self.a * drive(1.0, valve, 1.0, self.p) + f
            return (a * flow + b * t_out + c) / (a + b)
        t = (f * flow + b * t_out + c) / (f + b)  # ohne Heizkörper
        if t >= flow:
            return t
        g, n = self.a * drive(1.0, valve, 1.0, self.p), self.n
        for _ in range(30):  # Newton: die Wärmebilanz fällt mit T und ist konvex – von links ohne Überschießen
            warm = max(flow - t, 0.0)
            bal = g * warm ** n + f * warm + b * (t_out - t) + c
            if bal < 1e-9:
                break
            t += bal / (g * n * warm ** (n - 1) + f + b)
        return min(t, flow)

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
                u += keep * (max(flow - t, 0.0) - u)
                t += (self.heating(flow, t, valve) + self.f * u + self.b * (t_out - t) + self.c) * step
                if t >= target:
                    return round(i * step, 2)
            return float(FLOOR_MAX_H)  # erreichbar, aber sehr langsam
        if self.n == 1.0:
            k = self.a * drive(1.0, valve, 1.0, self.p) + self.b
            return -math.log((t_inf - target) / (t_inf - t_now)) / k
        # Zeit = ∫ dT / Anstieg(T) von jetzt bis zum Ziel (Simpson, der Anstieg bleibt bis zum Ziel positiv)
        steps = 32
        h = (target - t_now) / steps
        w = [1 if i in (0, steps) else 4 if i % 2 else 2 for i in range(steps + 1)]
        return sum(wi / self.rate(t_now + i * h, flow, t_out, valve) for i, wi in enumerate(w)) * h / 3

    def required_flow(self, target: float, t_out: float, valve: float = 1.0) -> float:
        """Vorlauftemperatur, die die Zieltemperatur dauerhaft hält (Fußboden heizt mit)."""
        need = max(self.b * (target - t_out) - self.c, 0.0)
        g, f, n = max(self.a * drive(1.0, valve, 1.0, self.p), 1e-12), self.f, self.n
        if n == 1.0 or f <= 0:
            return target + (need / (g + f) if n == 1.0 else (need / g) ** (1.0 / n))
        lo, hi = 0.0, 1.0
        while g * hi ** n + f * hi < need and hi < 1e4:
            hi *= 2
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if g * mid ** n + f * mid < need else (lo, mid)
        return target + (lo + hi) / 2

    def valve_for(self, need: float, flow: float, t_room: float) -> float | None:
        """Öffnung (0…1, kann darüber liegen), mit der der Heizkörper ``need`` K/h bringt – None ohne Vorlauf über Raum."""
        warm = flow - t_room
        if warm <= 0:
            return None
        return (max(need, 0.0) / (self.a * warm ** self.n)) ** (1.0 / self.p)

    def cool_down(self, t_now: float, t_out: float, hours: float) -> float:
        """Raumtemperatur nach ``hours`` Stunden ohne Heizung (nie unter die Außentemperatur)."""
        t_inf = max(t_out + self.c / self.b, t_out)
        return t_inf + (t_now - t_inf) * math.exp(-self.b * hours)

    def seeded(self) -> "RoomModel":
        """Neues Modell mit den Werten dieses Modells als Startwerte (Unsicherheit wie am Anfang) – zum Neulernen
        aus der Historie nach dem Umrechnen (0.53), damit dieselben Messpunkte nicht doppelt zählen."""
        m = RoomModel(self.room, floor=self.floor, floor_lag_h=self.floor_lag_h, floor_u=self.floor_u)
        for new, old in zip(m.rlss, self.rlss):
            new.theta = list(old.theta)
        return m

    def summary(self, t_out: float, max_flow: float) -> dict:
        return {
            "room": self.room,
            "floor": self.floor,
            "floor_gain": round(self.f, 4) if self.floor else None,
            "updates": self.rls.updates,
            "rmse_k": self.rls.rmse,
            "tau_h": round(self.tau_h, 1),
            "heat_gain": round(self.a_ref, 4),
            "flow_exp": self.n,
            "valve_exp": self.p,
            "free_gain_k_h": round(self.c, 3),
            "solar_gain": self.solar,
            "wind_gain": round(self.wind_gain, 4),  # 0.54: Auskühlen je m/s Wind
            "hours_plus_2k_max_flow": self.time_to_reach(19.0, 21.0, max_flow, t_out),
            "flow_to_hold_21": round(self.required_flow(21.0, t_out), 1),
        }

    def to_dict(self) -> dict:
        return {
            "form": FORM,
            "room": self.room,
            "floor": self.floor,
            "floor_lag_h": self.floor_lag_h,
            "floor_u": self.floor_u,
            "excitation": self.excitation,
            "rls": self.rlss[FORM_DEFAULT].to_dict(),  # lineare Form wie bis 0.52 (Leser des alten Formats)
            "rlss": [r.to_dict() for r in self.rlss],
            "form_index": self.form_index,
            "sel_sse": self.sel_sse,
            "sel_w": self.sel_w,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RoomModel":
        floor = d.get("floor", False)
        kw = dict(room=d["room"], floor=floor, floor_lag_h=d.get("floor_lag_h", 3.0), floor_u=d.get("floor_u", 0.0),
                  excitation=d.get("excitation", 0.0))
        if d.get("form") == FORM and len(d.get("rlss") or []) == len(FORMS):
            m = cls(**kw, rlss=[RLS.from_dict(r) for r in d["rlss"]],
                    form_index=min(int(d.get("form_index", FORM_DEFAULT)), len(FORMS) - 1),
                    sel_sse=list(d.get("sel_sse") or [0.0] * len(FORMS)),
                    sel_w=list(d.get("sel_w") or [0.0] * len(FORMS)))
        else:  # bis 0.52: linear – je Kandidat umrechnen, Neulernen aus der Historie übernimmt die Engine
            old = RLS.from_dict(d["rls"])
            if old.n == (4 if floor else 3):
                old.extend(3)  # Modell von vor 0.20: Sonnen-Parameter ergänzen
            m = cls(**kw, rlss=[_migrated(old, n, p) for n, p in FORMS], migrated=True)
        for r in m.rlss:  # bis 0.53 ohne Wind: Parameter ergänzen (Startwert 0), Gelerntes bleibt
            if r.n == m._base_n + 3:
                r.extend(1, ROOM_PRIOR["w"][1] ** 2)
        m._sun_not_negative()
        return m
