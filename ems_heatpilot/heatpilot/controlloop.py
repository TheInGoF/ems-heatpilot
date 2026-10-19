"""Regelschleife „HeatPilot steuert“ (seit 0.34): Wunschzustand je Raum und Schreiben der Solltemperaturen.

Nur bei Schalter ``control_mode = heatpilot`` und nur für Räume mit „macht mit“ (``Room.managed``).
Der Wunschzustand ist eine reine Funktion ``desired(...)`` (testbar) mit den Phasen
``komfort``, ``nacht``, ``abwesend``, ``vorheizen``, ``urlaub``:

1. Urlaub → Abwesenheitstemperatur; rechtzeitig vor der Rückkehr vorheizen auf Komfort.
2. Nacht laut Wochenplan → Nachttemperatur; vor dem Nachtende so früh vorheizen, wie der Raum
   laut Modell braucht (gedeckelt auf ``preheat_max_h``, mindestens 15 min).
3. Niemand zu Hause (sicher) → Abwesenheitstemperatur; erwartete Ankunft innerhalb der Aufheizzeit →
   vorheizen. Tracker unbekannt oder Ankunft offen (Gewohnheit lernt noch) → Komfort.
4. Sonst Komfort.

Geschrieben wird nur bei einer Änderung des Wunsch-Sollwerts (Phasenwechsel), nicht jeden Takt.
Hat jemand von Hand gedreht (Soll ≠ zuletzt geschrieben, auch „aus“), bleibt das bis zum nächsten
Phasenwechsel – dann schreibt HeatPilot den Wunschzustand auch, wenn er sich dabei nicht ändert
(vorheizen → komfort). Pause hält alle Schreibvorgänge an; ein Boost, der in der Pause abläuft,
endet mit dem Soll von vorher. Fehler: nach ``RETRY_MIN`` erneut, ab ``MAX_FAILS`` Hinweis.
``plan()`` läuft im Takt der Engine (nach ``dhw_step``) und sammelt die Schreibvorgänge,
``apply()`` führt sie danach aus (asynchron, nur über ``Control``). ``stop()`` (main.build beim
Neuaufbau) hält ein noch laufendes ``apply()`` an, damit die alte Schleife nicht weiterschreibt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from . import guards
from .control import TEMP_MAX, TEMP_MIN
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

    ``model`` None (nicht plausibel) oder Raumtemperatur unbekannt → ``PREHEAT_DEFAULT_H``.
    """
    cap = max(cap, PREHEAT_MIN_H)
    if model is None or temp is None:
        return min(PREHEAT_DEFAULT_H, cap)
    hours = model.time_to_reach(temp, target, max_flow, t_out)
    if hours is None:
        return cap  # Ziel mit maximalem Vorlauf nicht erreichbar: akzeptieren, nur bis zum Deckel vorheizen
    return min(max(hours, PREHEAT_MIN_H), cap)


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
            t_out: float | None = None) -> Desired:
    """Wunschzustand eines Raums (``discovery.Room``) – reine Funktion, siehe Modulbeschreibung."""
    comfort, away, night = float(room.comfort), cfg.away_temp, cfg.night_temp
    t_out = 5.0 if t_out is None else t_out

    def lead(target: float) -> timedelta:
        return timedelta(hours=preheat_hours(model, temp, target, cfg.max_flow_temp, t_out, cfg.preheat_max_h))

    # 1. Urlaub
    if presence.vacation:
        eta = presence.arrival
        if eta is not None and now >= eta - lead(comfort):
            return Desired(_clamp(comfort, comfort, cfg), "vorheizen", f"Rückkehr aus dem Urlaub {eta:%H:%M}", eta)
        return Desired(_clamp(away, comfort, cfg), "urlaub", "Urlaub",
                       None if eta is None else eta - lead(comfort))

    # Tagesziel (ohne Nacht): 3. niemand zu Hause, 4. sonst Komfort
    if presence.home_count == 0:
        eta, unknown = presence.arrival, presence.arrival_unknown
        if unknown is not None and unknown <= now + lead(comfort):
            day = (comfort, "komfort", "Ankunft offen – Gewohnheit lernt noch", unknown)
        elif eta is not None and now >= eta - lead(comfort):
            day = (comfort, "vorheizen", f"Ankunft erwartet {eta:%H:%M}", eta)
        elif eta is not None:
            day = (away, "abwesend", f"niemand zu Hause, Ankunft {eta:%H:%M}", eta - lead(comfort))
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
            start = end - lead(day_sp)
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
        """Wunschzustände bestimmen und Schreibvorgänge vormerken (schreibt selbst nichts)."""
        from .engine import local  # spät: engine importiert dieses Modul

        eng, cfg = self.eng, self.eng.cfg
        now = local(ts)
        self.planned_ts = ts
        self.pending, self.restores, self.rooms = [], [], {}
        readings = {r[0]: r for r in rooms}
        managing = cfg.is_managing()
        presence = self.presence(now, sample)
        t_out = sample.get("t_out")
        paused = self.paused(ts)
        targets = {r.climate: self.control.target(r.climate) for r in eng.found.rooms}
        boosts = self.control.active(targets, ts)

        desireds: dict[str, Desired] = {}
        for room in eng.found.rooms:
            reading = readings.get(room.climate)
            temp = reading[1] if reading else None
            model = eng.rooms.get(room.climate)
            ok = model is not None and guards.check_room(model)["ok"]
            d = desired(room, now, cfg, presence, model if ok else None, temp, t_out)
            desireds[room.climate] = d
            self.rooms[room.climate] = {
                "managed": bool(room.managed), "phase": d.phase if managing else None,
                "desired": d.setpoint if managing else None, "reason": d.reason if managing else None,
                "next_change": _iso(d.until) if managing else None,
                "next_phase": (desired(room, d.until + timedelta(minutes=1), cfg, presence, model if ok else None,
                                       temp, t_out).phase if managing and d.until else None),
                "reach_c": reach_c(model if ok else None, d.setpoint, cfg.max_flow_temp,
                                   5.0 if t_out is None else t_out),
                "manual": False, "manual_until": None, "boost": room.climate in boosts,
                "last_written": None, "errors": 0, "retry_at": None, "paused": paused and managing,
            }

        # abgelaufene lokale Boosts: Wunschzustand zurück (heatpilot, Raum macht mit, keine Pause),
        # sonst Soll von vorher
        expired = {c for c, _ in self.control.expired_boosts(ts)}
        for climate in expired:
            room = eng.found.room_of(climate)
            back = (desireds[climate].setpoint if managing and not paused and room is not None and room.managed
                    else None)
            self.restores.append((climate, back))

        if not managing:
            if self.state:  # Schalter zurück auf beobachten: Schreibzustand vergessen
                self.state = {}
                self.store.put(KV_STATE, {})
            return

        for room in eng.found.rooms:
            if not room.managed:
                self.state.pop(room.climate, None)
                continue
            c, d, info = room.climate, desireds[room.climate], self.rooms[room.climate]
            st = self.state.setdefault(c, {})
            current = targets.get(c)
            last = st.get("last_written")
            phase_changed = st.get("phase") != d.phase
            manual = last is not None and current is not None and abs(current - last) >= 0.1 and c not in boosts
            if manual and abs(current - d.setpoint) < 0.1:  # von Hand genau auf den Wunsch gedreht
                st["last_written"], last, manual = current, current, False
            write = False
            if c in boosts:
                st["boosted"] = True
            elif c in expired:
                st.pop("boosted", None)  # Wiederherstellung läuft über end_boost (restores), nicht doppelt
            elif st.pop("boosted", False):
                write = current is None or abs(current - d.setpoint) >= 0.1  # nach dem Boost zurück zum Wunsch
            elif phase_changed and (current is None or abs(current - d.setpoint) >= 0.1):
                write = True  # Phasenwechsel beendet Handänderung/„aus“, auch wenn das Wunsch-Soll gleich bleibt
            elif last is None or abs(d.setpoint - last) >= 0.1:
                write = not manual or phase_changed
            if manual and phase_changed:
                manual = False
            st["phase"] = d.phase
            retry_at = st.get("retry_at")
            if write and retry_at and ts < retry_at:
                write = False
            if write and paused:
                write = False
            if write:
                self.pending.append((c, d.setpoint, f"{d.phase}: {d.reason}"))
            info.update({"manual": manual, "manual_until": _iso(d.until) if manual else None,
                         "last_written": st.get("last_written"), "errors": int(st.get("fails", 0)),
                         "retry_at": None if not retry_at or ts >= retry_at else _iso(local(retry_at))})
        self.state = {c: s for c, s in self.state.items() if c in self.rooms}
        self.store.put(KV_STATE, self.state)

    async def apply(self) -> None:
        """Vorgemerkte Schreibvorgänge ausführen – nur über Control (erlaubte Dienste, gewählte Räume)."""
        pending, restores = self.pending, self.restores
        self.pending, self.restores = [], []
        now = self.planned_ts or time.time()  # dieselbe Uhr wie plan()
        for climate, setpoint, reason in pending:
            if self.stopped or not self.eng.cfg.is_managing():  # inzwischen ersetzt bzw. Schalter beobachten
                log.info("HeatPilot: Schreibvorgänge verworfen – Schleife ersetzt oder Steuerung aus")
                break
            st, info = self.state.setdefault(climate, {}), self.rooms.get(climate, {})
            try:
                await self.control.apply(climate, setpoint, reason)
                st["last_written"], st["fails"] = setpoint, 0
                st.pop("retry_at", None)
                info.update({"last_written": setpoint, "errors": 0, "retry_at": None})
                log.info("HeatPilot: %s → %.1f °C (%s)", climate, setpoint, reason)
            except Exception as err:  # noqa: BLE001 – loggen, später erneut
                st["fails"] = int(st.get("fails", 0)) + 1
                st["retry_at"] = now + RETRY_MIN * 60
                info.update({"errors": st["fails"], "retry_at": _iso(datetime.fromtimestamp(st["retry_at"]).astimezone())})
                log.warning("HeatPilot: %s nicht auf %.1f °C gesetzt (%s) – Versuch %d, erneut in %d min",
                            climate, setpoint, err, st["fails"], RETRY_MIN)
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
        managed = [r for r in self.rooms.values() if r["managed"] and r["next_change"]]
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
            "log": [{**row, "time": local(row["ts"]).isoformat(),
                     "name": names.get(row["room"]) or names.get(self._key_of(row["room"]), row["room"])}
                    for row in self.store.control_log(LOG_SHOW)],
            "rooms": self.rooms,
        }

    def _key_of(self, climate: str) -> str:
        room = self.eng.found.room_of(climate)
        return room.climate if room else climate
