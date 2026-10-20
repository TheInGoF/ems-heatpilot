"""Regelschleife „HeatPilot steuert“ (seit 0.34): Wunschzustand je Raum und Schreiben der Solltemperaturen.

Nur bei Schalter ``control_mode = heatpilot`` und nur für Räume mit „macht mit“ (``Room.managed``).
Der Wunschzustand ist eine reine Funktion ``desired(...)`` (testbar) mit den Phasen
``komfort``, ``nacht``, ``abwesend``, ``vorheizen``, ``urlaub``:

1. Urlaub → Abwesenheitstemperatur; rechtzeitig vor der Rückkehr vorheizen auf Komfort.
2. Nacht laut Wochenplan → Nachttemperatur; vor dem Nachtende so früh vorheizen, wie der Raum
   laut Modell braucht (gedeckelt auf ``preheat_max_h``, mindestens 15 min; seit 0.46.5 sanft, ``preheat_hours``,
   und ein begonnenes Vorheizen bleibt bis zum Ziel, ``preheating``). Seit 0.37 mit der
   Außentemperatur zum Zielzeitpunkt (Nachtende, Ankunft, Rückkehr) laut Vorhersage, sonst der aktuellen.
3. Niemand zu Hause (sicher) → Abwesenheitstemperatur; erwartete Ankunft innerhalb der Aufheizzeit →
   vorheizen. Tracker unbekannt oder Ankunft offen (Gewohnheit lernt noch) → Komfort.
4. Sonst Komfort.

Geschrieben wird nur bei einer Änderung des Wunsch-Sollwerts (Phasenwechsel), nicht jeden Takt.
Hat jemand von Hand gedreht (Soll ≠ zuletzt geschrieben, auch „aus“), bleibt das bis zum nächsten
Phasenwechsel – dann schreibt HeatPilot den Wunschzustand auch, wenn er sich dabei nicht ändert
(vorheizen → komfort). Pause hält alle Schreibvorgänge an; ein Boost, der in der Pause abläuft,
endet mit dem Soll von vorher. Fehler: nach ``RETRY_MIN`` erneut, ab ``MAX_FAILS`` Hinweis – seit 0.38 auch als
Meldung über die Benachrichtigungsziele und im Problem-Sensor (``notify``), einmal je Ereignis; der erste Erfolg
danach meldet „wieder in Ordnung“.
Seit 0.40 legt die Ventilführung (``valve_control``, eigener Schalter „Ventile führen“, Standard aus) höchstens einen
Schritt auf das Wunsch-Soll (früh drosseln, gemeinsam anfordern): ``plan()`` fragt sie je Raum nach dem Soll
(``Guide.plan``) – Phasenwechsel, Hand, Boost und Pause gehen vor –, geschrieben wird es auf demselben Weg, mit
denselben Fehlerregeln; erst ein erfolgreicher Schreibvorgang macht ihren Schritt gültig (``Guide.wrote``). Damit ein
eigener Schritt nie als Hand zählt: Steht am Thermostat, was HeatPilot zuletzt schreiben wollte (``tried``, auch nach
einem Teilfehler in einer Gruppe oder einem Neuaufbau während des Aufrufs) bzw. der laufende Schritt, gilt das als
geschrieben; meldet es bis zu ``GRACE_TICKS`` Takte nach dem Schreiben noch den Wert von vorher, ist das Geschriebene
noch nicht angekommen (``_observe``). Verwirft die Führung einen Schritt wegen Hand (auch „aus“), gilt das Wunschsoll
als zuletzt geschrieben – die Hand bleibt dann wie oben bis zum Phasenwechsel. In der Pause geht nur das Zurück der
Führung hinaus; vor einer Pause, „Beobachten“ oder „macht mit“ aus nimmt ``release`` laufende Schritte einmal zurück.
Sollwerte gelten ab 0,1 K als verschieden (``_same``, mit Toleranz für Gleitkommareste).
``plan()`` läuft im Takt der Engine (nach ``dhw_step``) und sammelt die Schreibvorgänge,
``apply()`` führt sie danach aus (asynchron, nur über ``Control``). ``stop()`` (main.build beim
Neuaufbau) hält ein noch laufendes ``apply()`` an, damit die alte Schleife nicht weiterschreibt.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from . import guards, valve_control
from .control import TEMP_MAX, TEMP_MIN
from .boiler_status import ACTIONS as BOILER_ACTIONS
from .boiler_status import LOG_NAME as BOILER_LOG_NAME
from .dhw_control import ACTIONS as DHW_ACTIONS
from .dhw_control import LOG_NAME as DHW_LOG_NAME
from .disinfect_status import ACTIONS as DISINFECT_ACTIONS
from .disinfect_status import LOG_NAME as DISINFECT_LOG_NAME
from .presence import arrival_unknown, next_arrival

log = logging.getLogger(__name__)

PHASES = ("komfort", "nacht", "abwesend", "vorheizen", "urlaub")
PREHEAT_MIN_H = 0.25      # so früh mindestens umschalten
PREHEAT_DEFAULT_H = 1.0   # Raummodell noch nicht plausibel: pauschal so lange vorheizen
REACH_GAP_K = 0.3         # Gleichgewicht so weit unter dem Ziel: „erreicht ≈ …“ anzeigen
RETRY_MIN = 15            # nach einem Schreibfehler so lange warten
MAX_FAILS = 3             # ab so vielen Fehlschlägen in Folge Hinweis in der Oberfläche
PAUSE_MAX_H = 48
LOG_SHOW = 20             # Protokollzeilen im Heizen-Reiter
KV_STATE = "control.state"
KV_PAUSE = "control.pause_until"
NOTIFY_KEY = "room_write_failed:"  # + climate: Schreibfehler eines Raums ab MAX_FAILS (notify, seit 0.38)
SAME_K = 0.1 - 1e-6       # Sollwerte gleich, wenn näher beieinander (kleinster Schritt 0,1 K, Gleitkommareste)
GRACE_TICKS = 2           # so viele Takte nach dem Schreiben darf das Thermostat noch den Wert von vorher melden


def _same(a: float | None, b: float | None) -> bool:
    """Gleiches Soll? Unter 0,1 K Unterschied, auf 0,01 gerundet – 20,9 − 20,8 = 0,0999… zählt als ein Schritt."""
    return a is not None and b is not None and abs(round(a, 2) - round(b, 2)) < SAME_K


def _arriving(st: dict, current: float | None) -> bool:
    """Meldet das Thermostat noch den Wert von vor dem letzten Schreibvorgang (``before``, auch „aus“)? Dann ist das
    Geschriebene noch nicht angekommen – kein Hand-Eingriff."""
    before = st.get("before")
    return not _same(current, st.get("last_written")) and (
        _same(current, before) or (current is None and before is None))


@dataclass
class Presence:
    """Anwesenheit für den Wunschzustand – alles, was ``desired`` über Menschen wissen muss."""

    home_count: int | None = None          # bekannte Anwesende; None = Tracker unbekannt / keine Personen
    vacation: bool = False
    arrival: datetime | None = None        # erwartete Ankunft (Gewohnheit/GPS bzw. Urlaubsende)
    arrival_unknown: datetime | None = None  # ab wann offen ist, ob jemand heimkommt


class Desired(NamedTuple):
    setpoint: float
    phase: str
    reason: str
    until: datetime | None  # geplanter nächster Wechsel (None: keiner absehbar)


def preheat_hours(model, temp: float | None, target: float, max_flow: float, t_out: float, cap: float) -> float:
    """Vorlauf fürs Vorheizen in Stunden: aus dem Raummodell, gedeckelt; unerreichbar → Deckel.

    Seit 0.46.5 sanft: so früh, dass der niedrigste Vorlauf ab ``PREHEAT_FLOW_FROM`` reicht, der innerhalb des
    Deckels ans Ziel kommt – statt kurz mit dem höchsten Vorlauf (morgens 60 °C, danach wieder 35 °C). Die Vorschau
    findet denselben Vorlauf (``preview._preheat_flow``). ``model`` None (nicht plausibel) oder Raumtemperatur
    unbekannt → ``PREHEAT_DEFAULT_H``.
    """
    cap = max(cap, PREHEAT_MIN_H)
    if model is None or temp is None:
        return min(PREHEAT_DEFAULT_H, cap)
    for flow in range(int(PREHEAT_FLOW_FROM), int(max_flow) + 1):
        hours = model.time_to_reach(temp, target, float(flow), t_out)
        if hours is not None and hours <= cap:
            return max(hours, PREHEAT_MIN_H)
    return cap  # Ziel auch mit maximalem Vorlauf nicht im Deckel erreichbar: bis zum Deckel vorheizen


PREHEAT_FLOW_FROM = 35.0  # kleinster Vorlauf beim Vorheizen (wie ``preview.FLOW_MIN``)


def reach_c(model, target: float, max_flow: float, t_out: float) -> float | None:
    """Was der Raum bei maximalem Vorlauf höchstens erreicht, wenn das unter dem Ziel liegt (sonst None)."""
    if model is None:
        return None
    eq = model.equilibrium(max_flow, t_out)
    return round(eq * 2) / 2 if eq < target - REACH_GAP_K else None


def _clamp(value: float, comfort: float, cfg) -> float:
    """Nie über Komfort, nie unter dem niedrigsten eingestellten Wert, immer 5–25 °C, 0,5-K-Raster."""
    lo = min(cfg.away_temp, cfg.night_temp, comfort)
    v = max(min(value, comfort), lo)
    return round(max(TEMP_MIN, min(TEMP_MAX, v)) * 2) / 2


def desired(room, now: datetime, cfg, presence: Presence, model=None, temp: float | None = None,
            t_out: float | None = None, t_out_at: Callable[[datetime], float | None] | None = None,
            preheating: datetime | None = None) -> Desired:
    """Wunschzustand eines Raums (``discovery.Room``) – reine Funktion, siehe Modulbeschreibung.

    ``t_out_at(Zeitpunkt)`` (seit 0.37, z. B. ``Engine.outdoor_forecast_at``): Außentemperatur zum Ziel des
    Vorheizens (Nachtende, Ankunft, Rückkehr) laut Vorhersage; liefert sie None oder fehlt sie, gilt ``t_out``.
    ``preheating`` (seit 0.46.5): Ziel eines schon begonnenen Vorheizens – für dieses Ziel bleibt es dabei, auch wenn
    der Raum schneller warm wird als geplant (sonst pendelte die Phase zwischen Vorheizen und Nacht bzw. Abwesenheit).
    """
    comfort, away, night = float(room.comfort), cfg.away_temp, cfg.night_temp
    t_out = 5.0 if t_out is None else t_out

    def lead(target: float, at: datetime) -> timedelta:
        if preheating is not None and abs(at.timestamp() - preheating.timestamp()) < 1:
            return max(at - now, timedelta(0))  # läuft schon: bleibt bis zum Ziel
        t_at = t_out_at(at) if t_out_at is not None else None
        return timedelta(hours=preheat_hours(model, temp, target, cfg.max_flow_temp,
                                             t_out if t_at is None else t_at, cfg.preheat_max_h))

    # 1. Urlaub
    if presence.vacation:
        eta = presence.arrival
        if eta is not None and now >= eta - lead(comfort, eta):
            return Desired(_clamp(comfort, comfort, cfg), "vorheizen", f"Rückkehr aus dem Urlaub {eta:%H:%M}", eta)
        return Desired(_clamp(away, comfort, cfg), "urlaub", "Urlaub",
                       None if eta is None else eta - lead(comfort, eta))

    # Tagesziel (ohne Nacht): 3. niemand zu Hause, 4. sonst Komfort
    if presence.home_count == 0:
        eta, unknown = presence.arrival, presence.arrival_unknown
        if unknown is not None and unknown <= now + lead(comfort, unknown):
            day = (comfort, "komfort", "Ankunft offen – Gewohnheit lernt noch", unknown)
        elif eta is not None and now >= eta - lead(comfort, eta):
            day = (comfort, "vorheizen", f"Ankunft erwartet {eta:%H:%M}", eta)
        elif eta is not None:
            day = (away, "abwesend", f"niemand zu Hause, Ankunft {eta:%H:%M}", eta - lead(comfort, eta))
        else:
            day = (away, "abwesend", "niemand zu Hause", None)
    elif presence.home_count is None:
        day = (comfort, "komfort", "Anwesenheit unbekannt", None)
    else:
        day = (comfort, "komfort", "jemand zu Hause", None)
    day_sp, day_phase, day_reason, day_until = day

    # 2. Nacht laut Wochenplan – Vorheizen nur für ein Komfortziel, nicht für die Abwesenheitstemperatur
    if cfg.is_night_at(now):
        end = cfg.night_end_at(now)
        if day_phase in ("komfort", "vorheizen") and day_sp > night:
            start = end - lead(day_sp, end)
            if now >= start:
                hours = (end - start).total_seconds() / 3600
                return Desired(_clamp(day_sp, comfort, cfg), "vorheizen",
                               f"Nachtende {end:%H:%M}, Raum braucht {hours:.1f} h", end)
            return Desired(_clamp(night, comfort, cfg), "nacht", f"Nacht bis {end:%H:%M}", start)
        return Desired(_clamp(night, comfort, cfg), "nacht", f"Nacht bis {end:%H:%M}",
                       min(end, day_until) if day_until else end)
    nxt = cfg.night_start_after(now)
    until = min(day_until, nxt) if day_until else nxt
    return Desired(_clamp(day_sp, comfort, cfg), day_phase, day_reason, until)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class ControlLoop:
    """Hält den Schreibzustand je Raum (zuletzt geschrieben, Phase, Fehler) und die Pause – überlebt Neustarts (kv)."""

    def __init__(self, eng, control) -> None:
        self.eng = eng
        self.control = control
        self.store = eng.store
        self.state: dict[str, dict] = {c: s for c, s in (self.store.get(KV_STATE) or {}).items()
                                       if c in {r.climate for r in eng.found.rooms}}
        self.pause_until: float | None = self.store.get(KV_PAUSE)
        self.rooms: dict[str, dict] = {}    # Anzeige je Raum (letzter plan())
        self.pending: list[tuple[str, float, str]] = []
        self.props: dict[str, dict] = {}    # 0.40: Vorschläge der Ventilführung zu den vorgemerkten Schreibvorgängen
        self.guide = valve_control.Guide(eng)  # Ventile führen (0.40, eigener Schalter)
        self.restores: list[tuple[str, float | None]] = []
        self.planned_ts: int | None = None
        self.stopped = False  # durch eine neue Schleife ersetzt (main.build): nichts mehr schreiben

    def stop(self) -> None:
        """Schleife außer Dienst stellen – ein noch laufendes ``apply()`` bricht vor dem nächsten Schreiben ab."""
        self.stopped = True

    # ------------------------------------------------------------------ Eingänge
    def presence(self, now: datetime, sample: dict) -> Presence:
        """Wie ``Engine.dhw_recommend``: Urlaub → Rückkehr, niemand da → Ankunft/offen."""
        home = sample.get("home_count")
        persons = list(self.eng.persons.values())
        p = Presence(home_count=home, vacation=bool(sample.get("vacation")))
        if p.vacation:
            ret = self.eng.vacation.arrival(now, persons)
            p.arrival = ret["eta"] if ret else None
        elif home == 0:
            nxt = next_arrival(persons, now)
            p.arrival = nxt["eta"] if nxt else None
            p.arrival_unknown = arrival_unknown(persons, now)
        return p

    def paused(self, ts: float | None = None) -> bool:
        ts = ts or time.time()
        return bool(self.pause_until and ts < self.pause_until)

    def pause(self, hours: float) -> float | None:
        """Schreibvorgänge für ``hours`` Stunden anhalten; 0 = weiter."""
        hours = float(hours)
        if not 0 <= hours <= PAUSE_MAX_H:
            raise ValueError(f"Pause zwischen 0 und {PAUSE_MAX_H} h")
        self.pause_until = time.time() + hours * 3600 if hours > 0 else None
        self.store.put(KV_PAUSE, self.pause_until)
        log.info("Steuerung %s", f"pausiert bis {datetime.fromtimestamp(self.pause_until):%d.%m. %H:%M}"
                 if self.pause_until else "läuft weiter")
        return self.pause_until

    # ------------------------------------------------------------------ Takt
    def plan(self, ts: int, sample: dict, rooms: list[tuple]) -> None:
        """Wunschzustände bestimmen und Schreibvorgänge vormerken (schreibt selbst nichts). Seit 0.40 legt die
        Ventilführung (``valve_control``, eigener Schalter) höchstens einen Schritt auf das Wunsch-Soll."""
        eng = self.eng
        self.planned_ts = ts
        self.pending, self.restores, self.rooms, self.props = [], [], {}, {}
        managing, paused = eng.cfg.is_managing(), self.paused(ts)
        targets = {r.climate: self.control.target(r.climate) for r in eng.found.rooms}
        boosts = self.control.active(targets, ts)
        desireds = self._desireds(ts, sample, rooms, managing, paused, boosts)
        expired = self._restores(ts, desireds, managing, paused)
        if not managing:
            if self.state:  # Schalter zurück auf beobachten: Schreibzustand vergessen
                self.state = {}
                self.store.put(KV_STATE, {})
            self.guide.clear()
            self._settle_problems()
            return
        managed = [r.climate for r in eng.found.rooms if r.managed]
        lags = {c for c in managed if self._observe(c, targets.get(c))}
        ctx = {c: self._context(c, desireds[c], targets.get(c), boosts, expired, c in lags) for c in managed}
        wants = self.guide.plan(ts, sample, rooms, ctx)
        for c in self.guide.dropped:  # Schritt wegen Hand (auch „aus“) verworfen: Bezug ist wieder das Wunschsoll
            st = self.state.setdefault(c, {})
            st["last_written"] = ctx[c]["base"]
            if not ctx[c]["boost"]:  # Hand: was HeatPilot schreiben wollte, ist nicht mehr „eigen“ (ein Boost endet
                st.pop("tried", None)  # in der Pause mit dem Soll von vorher – der Schritt bleibt dann erkennbar)
        for room in eng.found.rooms:
            if not room.managed:
                self.state.pop(room.climate, None)
                continue
            self._decide(room.climate, desireds[room.climate], targets.get(room.climate), boosts, expired, paused,
                         ts, wants.get(room.climate), room.climate in lags)
        self.state = {c: s for c, s in self.state.items() if c in self.rooms}
        self.store.put(KV_STATE, self.state)
        self._settle_problems()

    def _desireds(self, ts: int, sample: dict, rooms: list[tuple], managing: bool, paused: bool,
                  boosts: dict) -> dict[str, Desired]:
        """Wunschzustand je Raum und die Anzeige dazu (``self.rooms``)."""
        from .engine import local  # spät: engine importiert dieses Modul

        eng, cfg = self.eng, self.eng.cfg
        now, readings = local(ts), {r[0]: r for r in rooms}
        presence, t_out = self.presence(now, sample), sample.get("t_out")
        t_out_at = eng.outdoor_forecast_at  # 0.37: Vorheizdauer mit der Außentemperatur zum Zielzeitpunkt
        desireds: dict[str, Desired] = {}
        for room in eng.found.rooms:
            reading = readings.get(room.climate)
            temp = reading[1] if reading else None
            model = eng.rooms.get(room.climate)
            ok = model is not None and guards.check_room(model)["ok"]
            held = self.state.get(room.climate, {}).get("preheat")  # 0.46.5: begonnenes Vorheizen bleibt
            d = desired(room, now, cfg, presence, model if ok else None, temp, t_out, t_out_at,
                        local(held) if held else None)
            desireds[room.climate] = d
            self.rooms[room.climate] = {
                "managed": bool(room.managed), "phase": d.phase if managing else None,
                "desired": d.setpoint if managing else None, "reason": d.reason if managing else None,
                "next_change": _iso(d.until) if managing else None,
                "next_phase": (desired(room, d.until + timedelta(minutes=1), cfg, presence, model if ok else None,
                                       temp, t_out, t_out_at).phase if managing and d.until else None),
                "reach_c": reach_c(model if ok else None, d.setpoint, cfg.max_flow_temp,
                                   5.0 if t_out is None else t_out),
                "manual": False, "manual_until": None, "boost": room.climate in boosts,
                "last_written": None, "errors": 0, "retry_at": None, "paused": paused and managing,
                "valve": None, "valve_guided": False,  # 0.40: laufender Eingriff der Ventilführung, geführt?
            }
        return desireds

    def _restores(self, ts: int, desireds: dict, managing: bool, paused: bool) -> set[str]:
        """Abgelaufene lokale Boosts: Wunschzustand zurück (heatpilot, Raum macht mit, keine Pause), sonst Soll von
        vorher. Gibt die Räume zurück."""
        expired = {c for c, _ in self.control.expired_boosts(ts)}
        for climate in expired:
            room = self.eng.found.room_of(climate)
            back = (desireds[climate].setpoint if managing and not paused and room is not None and room.managed
                    else None)
            self.restores.append((climate, back))
        return expired

    def _observe(self, c: str, current: float | None) -> bool:
        """Eigenes am Thermostat erkennen, bevor Hand zählt (0.40). Meldet es bis zu ``GRACE_TICKS`` Takte nach einem
        Schreibvorgang noch den Wert von vorher, ist das Geschriebene noch nicht angekommen (True: keine Hand, nichts
        Neues). Steht dort, was HeatPilot zuletzt schreiben wollte (``tried`` – auch nach einem Teilfehler in einer
        Gruppe oder einem Neuaufbau während des Aufrufs) bzw. der laufende Schritt der Führung, gilt es als
        geschrieben."""
        st = self.state.get(c)
        if not st:
            return False
        grace = int(st.pop("grace", 0) or 0)
        if grace > 0 and _arriving(st, current):
            st["grace"] = grace - 1
            return True
        active = self.guide.st["active"].get(c)
        if current is not None and not _same(current, st.get("last_written")) and (
                _same(current, st.get("tried")) or (active is not None and _same(current, active["sp"]))):
            st["last_written"] = current
        return False

    def _context(self, c: str, d: Desired, current: float | None, boosts: dict, expired: set,
                 lag: bool = False) -> dict:
        """Was die Ventilführung über einen Raum wissen muss: Wunsch-Soll, Phase, Phasenwechsel, Hand (gedreht, „aus“,
        Boost) und ob das Thermostat auf dem zuletzt Geschriebenen steht (``lag``: Geschriebenes noch nicht da)."""
        st = self.state.get(c, {})
        last = st.get("last_written")
        reset = st.get("phase") != d.phase
        touched = not lag and last is not None and not _same(current, last)
        boosted = c in boosts or c in expired or bool(st.get("boosted"))
        return {"base": d.setpoint, "phase": d.phase, "reset": reset, "hands": boosted or (touched and not reset),
                "boost": boosted, "settled": not lag and _same(current, last)}

    def _decide(self, c: str, d: Desired, current: float | None, boosts: dict, expired: set, paused: bool,
                ts: int, want=None, lag: bool = False) -> None:
        """Schreiben oder nicht – nur bei einer Änderung des Wunsch-Solls (bzw. des geführten Solls), Hand bis zum
        Phasenwechsel, Boost, Pause (außer dem Zurück der Ventilführung), Wartezeit nach Fehlern."""
        from .engine import local

        st, info = self.state.setdefault(c, {}), self.rooms[c]
        last = st.get("last_written")
        phase_changed = st.get("phase") != d.phase
        manual = not lag and last is not None and current is not None and not _same(current, last) and c not in boosts
        if manual and _same(current, d.setpoint):  # von Hand genau auf den Wunsch gedreht
            st["last_written"], last, manual = current, current, False
        sp, reason, prop, write = d.setpoint, f"{d.phase}: {d.reason}", None, False
        if c in boosts:
            st["boosted"] = True
        elif c in expired:
            st.pop("boosted", None)  # Wiederherstellung läuft über end_boost (restores), nicht doppelt
        elif st.pop("boosted", False):
            write = not _same(current, d.setpoint)  # nach dem Boost zurück zum Wunsch (auch nach „aus“)
        elif phase_changed and not _same(current, d.setpoint):
            write = True  # Phasenwechsel beendet Handänderung/„aus“, auch wenn das Wunsch-Soll gleich bleibt
        else:
            if want is not None:  # Ventilführung (0.40): geführtes Soll, Grund nur bei einer Änderung
                sp, reason, prop = want.sp, want.reason or reason, want.prop
            if last is None or not _same(sp, last):
                write = not manual or phase_changed
        if manual and phase_changed:
            manual = False
        st["phase"] = d.phase
        if d.phase == "vorheizen" and d.until is not None:
            st["preheat"] = round(d.until.timestamp())  # Ziel des laufenden Vorheizens (0.46.5, ``desired``)
        else:
            st.pop("preheat", None)
        retry_at = st.get("retry_at")
        back = prop is not None and prop["action"] == "zurück"  # Zurück der Ventilführung: auch in der Pause (0.40)
        if write and ((retry_at and ts < retry_at) or (paused and not back)):
            write = False
        if write:
            self.pending.append((c, sp, reason))
            st["tried"], st["before"] = sp, current  # eigen, auch wenn der Aufruf nur teilweise oder spät ankommt
            if prop is not None:
                self.props[c] = prop
        info.update({"manual": manual, "manual_until": _iso(d.until) if manual else None,
                     "last_written": st.get("last_written"), "errors": int(st.get("fails", 0)),
                     "retry_at": None if not retry_at or ts >= retry_at else _iso(local(retry_at)),
                     "valve": self.guide.room_info(c), "valve_guided": c in self.guide.guided})

    def guided_rooms(self) -> set[str]:
        """Räume, die die Ventilführung gerade führt – der Trockenlauf (``valve_dry.step``) rechnet sie nicht mit."""
        return set(self.guide.guided)

    # ------------------------------------------------------------------ Meldungen (0.38)
    def _notifier(self):
        return getattr(self.eng, "notifier", None)

    def _fail_text(self, climate: str, fails: int) -> str:
        room = self.eng.found.room_of(climate)
        return f"{room.name if room else climate}: {fails} Schreibfehler in Folge – Thermostat prüfen"

    def _settle_problems(self) -> None:
        """Offene Schreibfehler-Meldungen abgleichen: Räume ohne ``MAX_FAILS`` Fehler (entfernt, macht nicht mehr mit,
        Schalter beobachten) still schließen."""
        n = self._notifier()
        if n is not None:
            keep = {NOTIFY_KEY + c: self._fail_text(c, int(s.get("fails", 0)))
                    for c, s in self.state.items() if int(s.get("fails", 0)) >= MAX_FAILS}
            n.settle(NOTIFY_KEY, keep)

    def _report(self, climate: str, fails: int) -> None:
        """Nach einem Schreibvorgang: ab ``MAX_FAILS`` Fehlern in Folge melden, nach Erfolg „wieder in Ordnung“."""
        n = self._notifier()
        if n is None:
            return
        if fails >= MAX_FAILS:
            n.problem(NOTIFY_KEY + climate, self._fail_text(climate, fails))
        elif fails == 0:
            room = self.eng.found.room_of(climate)
            n.solved(NOTIFY_KEY + climate, f"{room.name if room else climate}: Thermostat nimmt das Soll wieder an")

    async def apply(self) -> None:
        """Vorgemerkte Schreibvorgänge ausführen – nur über Control (erlaubte Dienste, gewählte Räume). Ein Schritt der
        Ventilführung (0.40) geht in der Schrittweite des Thermostats hinaus, nur solange ihr Schalter wirkt; erst der
        Erfolg macht ihn gültig (``Guide.wrote``)."""
        pending, restores, props = self.pending, self.restores, self.props
        self.pending, self.restores, self.props = [], [], {}
        now = self.planned_ts or time.time()  # dieselbe Uhr wie plan()
        for climate, setpoint, reason in pending:
            if self.stopped or not self.eng.cfg.is_managing():  # inzwischen ersetzt bzw. Schalter beobachten
                log.info("HeatPilot: Schreibvorgänge verworfen – Schleife ersetzt oder Steuerung aus")
                break
            st, info = self.state.setdefault(climate, {}), self.rooms.get(climate, {})
            prop = props.get(climate)
            if prop is not None and prop["action"] != "zurück" and not self.eng.cfg.guides_valves():
                continue  # Ventile führen inzwischen aus: keinen Schritt mehr schreiben
            try:
                await self.control.apply(climate, setpoint, reason, *(() if prop is None else (prop["step"],)))
                st["last_written"], st["fails"], st["grace"] = setpoint, 0, GRACE_TICKS
                st.pop("retry_at", None)
                if not self.stopped:  # sofort – ersetzt (main.build), füllt die alte Schleife den kv nicht wieder
                    self.store.put(KV_STATE, self.state)  # (die neue erkennt den Schritt an ``tried`` aus plan())
                self.guide.wrote(climate, now, prop, save=not self.stopped)
                info.update({"last_written": setpoint, "errors": 0, "retry_at": None,
                             "valve": self.guide.room_info(climate)})
                log.info("HeatPilot: %s → %.1f °C (%s)", climate, setpoint, reason)
                self._report(climate, 0)
            except Exception as err:  # noqa: BLE001 – loggen, später erneut
                st["fails"] = int(st.get("fails", 0)) + 1
                st["retry_at"] = now + RETRY_MIN * 60
                info.update({"errors": st["fails"], "retry_at": _iso(datetime.fromtimestamp(st["retry_at"]).astimezone())})
                log.warning("HeatPilot: %s nicht auf %.1f °C gesetzt (%s) – Versuch %d, erneut in %d min",
                            climate, setpoint, err, st["fails"], RETRY_MIN)
                self._report(climate, st["fails"])
        for climate, setpoint in restores:
            if self.stopped:
                break  # die neue Schleife übernimmt den abgelaufenen Boost (steht noch in kv)
            try:
                wrote = await self.control.end_boost(climate, setpoint)
                if wrote and setpoint is not None and climate in self.state:
                    self.state[climate]["last_written"] = setpoint
                    self.rooms.get(climate, {})["last_written"] = setpoint
            except Exception as err:  # noqa: BLE001
                log.warning("Boost %s: Wiederherstellen fehlgeschlagen (%s)", climate, err)
        if (pending or restores) and not self.stopped:
            self.store.put(KV_STATE, self.state)

    async def release(self, keep: set[str], why: str) -> int:
        """Ventile führen (0.40): laufende Schritte der Räume außerhalb von ``keep`` einmal zurück aufs Wunschsoll – vor
        einer Pause (alle Räume) und bevor eine neue Schleife einen Raum nicht mehr führen darf („Beobachten“, „macht
        mit“ aus, Raum entfernt; ``main.build``). Geschrieben wird noch unter den Einstellungen, unter denen der Schritt
        entstand (HeatPilot steuert, Raum macht mit), über denselben Weg – und nur, wenn der Schritt noch am Thermostat
        steht (sonst gilt die Hand, auch „aus“). Gibt die Zahl der Schreibvorgänge zurück."""
        if self.stopped or not self.eng.cfg.is_managing():
            return 0
        now, done = int(time.time()), 0
        for c, want in self.guide.give_back(set(self.guide.st["active"]) - set(keep), why, now).items():
            room, st = self.eng.found.room_of(c), self.state.setdefault(c, {})
            current, step_sp = self.control.target(c), self.guide.st["active"][c]["sp"]
            arriving = bool(st.get("grace")) and _arriving(st, current)  # eben geschrieben, noch nicht gemeldet
            if room is None or not room.managed or not (_same(current, step_sp) or arriving):
                continue
            try:
                await self.control.apply(c, want.sp, want.reason, want.prop["step"])
            except Exception as err:  # noqa: BLE001 – der Schritt bleibt stehen, höchstens einer neben dem Wunschsoll
                log.warning("HeatPilot: %s nicht zurück auf %.1f °C gesetzt (%s)", c, want.sp, err)
                continue
            st.update(last_written=want.sp, tried=want.sp, before=current, grace=GRACE_TICKS)
            self.guide.wrote(c, now, want.prop)
            done += 1
            log.info("HeatPilot: %s → %.1f °C (%s)", c, want.sp, want.reason)
        if done:
            self.store.put(KV_STATE, self.state)
        return done

    # ------------------------------------------------------------------ Anzeige
    def desired_setpoints(self) -> dict[str, float]:
        """Wunsch-Soll je Raum mit „macht mit“ (für den Knopf „Zeitplan“ bei Steuerung heatpilot)."""
        if not self.eng.cfg.is_managing():
            return {}
        return {c: r["desired"] for c, r in self.rooms.items() if r["managed"] and r["desired"] is not None}

    def warnings(self) -> list[str]:
        """Hinweise im Reiter „Heizen“: Schreibfehler in Folge, dazu (0.36.3) verworfene Boosts auf den Heizkreis
        von EMS-ESP, die noch am Regler stehen (``Control.notes``)."""
        names = {r.climate: r.name for r in self.eng.found.rooms}
        notes = getattr(self.control, "notes", None)
        return [f"{names.get(c, c)}: {s['fails']} Schreibfehler in Folge – Thermostat prüfen"
                for c, s in self.state.items() if int(s.get("fails", 0)) >= MAX_FAILS] + (notes() if notes else [])

    def as_dict(self, now_ts: int | None = None) -> dict:
        """Status für ``GET /api/heat`` (Schlüssel ``control``)."""
        from .engine import local

        now_ts = now_ts or int(time.time())
        cfg = self.eng.cfg
        managing = cfg.is_managing()
        paused = self.paused(now_ts)
        names = {r.climate: r.name for r in self.eng.found.rooms}
        # nur Wechsel, die noch kommen (0.43): ein vergangener (der Takt hat ihn noch nicht neu berechnet) steht als
        # Phase schon im Text – „nächste Änderung 03:30“ um 03:35 wäre falsch
        managed = [r for r in self.rooms.values() if r["managed"] and r["next_change"]
                   and datetime.fromisoformat(r["next_change"]).timestamp() > now_ts]
        nxt = min(managed, key=lambda r: r["next_change"]) if managed else None
        text = "Beobachten"
        now = local(now_ts)
        if managing:
            text = "HeatPilot pausiert" if paused else "HeatPilot steuert"
            if cfg.is_night_at(now):
                text += f" · Nacht bis {cfg.night_end_at(now):%H:%M}"
            if nxt and nxt["next_phase"]:
                text += f" · nächste Änderung {datetime.fromisoformat(nxt['next_change']):%H:%M} {nxt['next_phase'].capitalize()}"
        return {
            "mode": cfg.control_mode,
            "active": managing and not paused,
            "paused_until": _iso(local(self.pause_until)) if paused else None,
            "pause_left_min": max(1, round((self.pause_until - now_ts) / 60)) if paused else None,
            "pause_max_h": PAUSE_MAX_H,
            "next_change": nxt["next_change"] if nxt else None,
            "next_phase": nxt["next_phase"] if nxt else None,
            "status_text": text,
            "managed_rooms": sum(1 for r in self.eng.found.rooms if r.managed),
            "warnings": self.warnings(),
            "hint": ("Zurück zu Tado nur über den Schalter in den Einstellungen; danach in der Tado-App den "
                     "Zeitplan fortsetzen. Fällt HeatPilot aus, bleibt das zuletzt geschriebene Soll stehen."
                     if managing else None),
            # seit 0.38 auch die Zeilen des Warmwasser-Solls (Weg W): kind „dhw“, Name „Warmwasser-Soll“; seit 0.41
            # die des Kessels (Weg B, Vorlauf-Soll und Heizbetrieb): kind „boiler“, Name „Kessel“; seit 0.42 die der
            # Desinfektion: kind „dhw“, Name „Desinfektion“
            "log": [{**row, "time": local(row["ts"]).isoformat(),
                     **({"kind": "dhw", "name": DHW_LOG_NAME} if row["action"] in DHW_ACTIONS else
                        {"kind": "dhw", "name": DISINFECT_LOG_NAME} if row["action"] in DISINFECT_ACTIONS else
                        {"kind": "boiler", "name": BOILER_LOG_NAME} if row["action"] in BOILER_ACTIONS else
                        {"kind": "room",
                         "name": names.get(row["room"]) or names.get(self._key_of(row["room"]), row["room"])})}
                    for row in self.store.control_log(LOG_SHOW)],
            "rooms": self.rooms,
        }

    def _key_of(self, climate: str) -> str:
        room = self.eng.found.room_of(climate)
        return room.climate if room else climate
