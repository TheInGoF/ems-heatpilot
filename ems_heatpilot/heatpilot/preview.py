"""Vorschau (Trockenlauf, seit 0.37): was HeatPilot in den nächsten 24 h am Kessel setzen würde – nur Anzeige.

Es wird nichts geschrieben, EMS-ESP bleibt nur lesend. ``preview`` rechnet in 5-min-Schritten bis zum Ende der
24. vollen Stunde (ab der laufenden):

* **Außentemperatur:** Vorhersagepunkt (±90 min wie ``Engine.forecast_point_at``), an den Außenfühler
  angeglichen: jetzt gilt der Fühler (gegen ihn sind die Raummodelle gelernt, mit ihm rechnet die Steuerung), der
  Versatz Fühler − Vorhersage klingt über ``FORECAST_FADE_H`` ab. Ohne Vorhersagepunkt der Fühler.
* **Anwesenheit** (``household``): jetzt laut Home Assistant; wer da ist, bleibt bis zur nächsten gewohnten
  Abfahrt; wer weg ist, bis zur erwarteten Ankunft (GPS bzw. Gewohnheit, im Urlaub die Rückkehr), danach wieder
  nach Gewohnheit.
* **Raum-Soll:** Räume mit „macht mit“ bei „HeatPilot steuert“ bekommen den Wunschzustand der Steuerung
  (``controlloop.desired`` mit Vorhersage); die übrigen das, was Tado voraussichtlich will – Nacht und
  Abwesenheit aus ``schedule.effective``, tagsüber das heutige Soll (nachts bzw. bei Abwesenheit abgefragt: das
  gemessene Tages-Soll der letzten 48 h, ``_day_setpoints``).
* **Raumtemperatur** läuft nach dem Raummodell mit (heizen mit dem Vorlauf, auskühlen ohne Wärme): daraus die
  Vorheizdauer und ob ein Raum gerade Wärme braucht. Räume, die nicht für den Vorlauf zählen, hält ihr
  Thermostat am Soll, solange der Heizbetrieb nicht sicher aus ist; ohne Raummodell bleibt der Messwert.
* **Vorlauf-Soll** = höchster nötiger Vorlauf der Räume mit plausiblem Modell ohne Fußbodenheizung
  (``RoomModel.required_flow``), 25 °C bis ``max_flow_temp``, auf 1 K gerundet. Heizt ein Raum vor, gilt der
  niedrigste Vorlauf, mit dem er ab dem Beginn laut Steuerung rechtzeitig warm wird (``_preheat_flow``; seit 0.46.5
  beginnt ``desired`` sanft so früh, dass genau dieser niedrigste reicht; ein begonnenes Vorheizen bleibt bis zum
  Ziel, ``held`` – jetzt aus dem Zustand der Steuerung), heizt er sonst auf, +5 K. Heizbetrieb aus nur, wenn kein
  Raum Wärme braucht – auch keiner, der nicht für den Vorlauf zählt (``_other_need``).
* **Fußboden-Grenze** (0.39.1, ``floor_limit``): Das Vorlauf-Soll liegt nie über dem höchsten Vorlauf der
  Fußbodenheizungen – direkt am Kessel (bzw. Anschluss unbekannt) und seit 0.46.5 auch mit RTL-Ventil immer (es
  öffnet, sobald der Rücklauf kalt ist), Mischer nie. Ob ein RTL-Raum Wärme anfordert (``_floor_want``), sagt nur
  noch der Grund: jetzt nach den Messwerten wie „Bereit für die Steuerung?“ (``floor_limit.demand``), danach in den
  Schritten, in denen er laut Vorschau Wärme braucht; ohne Raummodell die von jetzt, solange das Soll wie jetzt ist;
  hat ein Gerät des Raums keinen Zustand (nicht verfügbar), bleibt sie offen – die Grenze gilt (sicher) ohnehin.
  Auch beim Vorheizen und Aufheizen (``_capped``, nach allen anderen Regeln). Liegt die Grenze unter 35 °C, gilt sie,
  sobald ein Raum Wärme braucht. Der Grund sagt dann „… – begrenzt durch Fußboden …“.
* **Warmwasser-Soll** aus ``DhwModel.recommend`` mit derselben Anwesenheit, dem Desinfektionstermin und (seit 0.46) der
  Einschalthysterese des Kessels (Soll = gehaltene Speichertemperatur + Hysterese, ``dhw_target``), auf ganze
  Grad wie Weg W (``dhw_control.wish``: halbe Grad aufwärts, Minimum…Maximum).
* **Schreibvorgänge, die es gäbe** (``count_writes``): nur bei Änderung ab 1 K bzw. an/aus, 5 min Haltezeit. Seit 0.38
  gilt beim Warmwasser dieselbe Regel wie in Weg W (die Schwelle auch für die ungerundete Empfehlung, ``dhw_exact``): Bei „Trockenlauf“ bzw. „HeatPilot setzt“ beginnt die Zählung beim
  Wert von Weg W (zuletzt „geschrieben“ bzw. Kessel-Soll) mit der laufenden Haltezeit; bei „HeatPilot setzt“ heißen
  die Warmwasser-Schreibvorgänge „schreibt“ (``dhw_writes``), sonst „würde schreiben“. Seit 0.41 (Weg B) ebenso
  Vorlauf und Heizbetrieb (``flow_writes``): bei „Kessel über HeatPilot“ trocken bzw. heatpilot beginnt die Zählung
  beim Wert des Schritts (zuletzt „geschrieben“ bzw. am Kessel), „schreibt“ nur bei heatpilot ohne Pause und Sperre.
  Live rechnet ``boiler_flow`` dazu Abnahme, Zuschlag und Takt-Boost – die Vorschau zeigt den Plan aus dem Bedarf.

Je volle Stunde gilt der Wert, der den größten Teil der Stunde gilt; ``steps`` enthält jede Änderung genau.
Gerechnet wird bei Abruf (``cached``, höchstens alle 5 min) und stündlich fürs Protokoll (``preview_log``).
"""

from __future__ import annotations

import bisect
import math
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import boiler_rule, dhw_control, dhw_disinfect, floor_limit, guards, heatup
from .config import Config
from .controlloop import Presence, desired
from .engine_summary import FORECAST_MAX_GAP_MIN
from .presence_plan import household
from .values import flag, local, num

STEP_MIN = 5           # Rechenschritt
HOURS = 24             # so viele volle Stunden ab der laufenden
FLOW_MIN = 35.0        # Vorlauf-Soll nie darunter; nur Halten darunter: Heizbetrieb aus (Ingo, 0.39; 0.46.5 s. _flow)
FLOW_STEP_K = 1.0      # Schreibschwelle Vorlauf (Planungspapier: ab 1 K)
DHW_STEP_K = dhw_control.STEP_K        # Schreibschwelle Warmwasser – dieselbe wie Weg W (0.38)
HOLD_MIN = dhw_control.HOLD_S // 60    # Haltezeit nach jedem Schreiben (5 min) – dieselbe wie Weg W
HEATUP_GAP_K = 0.3     # Regelgenauigkeit des Thermostats: so weit unter dem Soll gilt ein Raum als am Soll …
HOLD_BAND_K = 0.3      # … und so weit darüber auch; darüber kühlt er ab, darunter heizt er auf
FORECAST_FADE_H = 6.0  # Versatz Außenfühler − Vorhersage (jetzt) klingt über so viele Stunden ab
DAY_SP_H = 48          # Tages-Soll nicht gesteuerter Räume, nachts abgefragt: gemessen in so vielen Stunden
CACHE_S = 300
NOTE = "Beobachten – HeatPilot schreibt nichts an den Kessel"
NOTE_BOILER = ("Kessel über HeatPilot schreibt Vorlauf und Heizbetrieb – die Vorschau zeigt den Plan aus dem Bedarf, "
               "Abnahme-Untergrenze, Zuschlag und Takt-Boost rechnet HeatPilot live dazu")
NO_MODELS = "Noch kein Raummodell plausibel (ohne Fußbodenheizung) – ohne Raummodelle kein Vorlauf-Soll"
NO_T_OUT = "Keine Außentemperatur – ohne sie kein Vorlauf-Soll"


def hour_start(ts: float) -> int:
    """Beginn der laufenden vollen Stunde nach Wanduhr (auch in Zonen mit halben Stunden Versatz)."""
    return int(local(ts).replace(minute=0, second=0, microsecond=0).timestamp())


def _de(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


# ------------------------------------------------------------------ Schreibvorgänge
def count_writes(series: list[tuple[datetime, float | bool | None]], start, step_k: float | None,
                 hold_min: float = HOLD_MIN, since: datetime | None = None) -> list[tuple[datetime, object, object]]:
    """Schreibvorgänge nach den abgesprochenen Regeln: (Zeitpunkt, neuer Wert, vorher).

    Geschrieben wird nur bei Änderung – Zahlen ab ``step_k`` Unterschied zum zuletzt geschriebenen Wert, an/aus
    (``step_k`` None) bei jedem Wechsel –, danach gilt ``hold_min`` Haltezeit: Was sich darin ändert, geht erst
    danach hinaus, und nur, wenn es dann noch anders ist. ``start``: Wert, der heute am Kessel steht (None =
    unbekannt, dann zählt der erste Wert). Werte None (z. B. Vorlauf bei Heizbetrieb aus) werden übersprungen.
    ``since`` (0.38): Zeitpunkt des letzten echten Schreibens – dessen Haltezeit gilt auch am Anfang. Ein dritter
    Wert je Eintrag (0.38, Warmwasser) ist der ungerundete: Die Schwelle gilt auch für ihn (wie ``dhw_rule.decide``).
    """
    out, last, last_t = [], start, since
    for t, v, *exact in series:
        if v is None:
            continue
        e = exact[0] if exact and exact[0] is not None else v
        if last is not None and ((abs(v - last) < step_k - 1e-9 or abs(e - last) < step_k - 1e-9)
                                 if step_k is not None else v == last):
            continue
        if last_t is not None and t < last_t + timedelta(minutes=hold_min):
            continue
        out.append((t, v, last))
        last, last_t = v, t
    return out


# ------------------------------------------------------------------ Räume
class _Nights:
    """Die Einstellungen mit gemerkten Nachtzeiten je Tag (nur lesen): ``desired`` fragt den Wochenplan in der
    Vorschau tausendfach – gerechnet wird mit denselben Funktionen wie in ``Config``."""

    def __init__(self, cfg) -> None:
        self._cfg, self._memo = cfg, {}

    def __getattr__(self, name: str):
        return getattr(self._cfg, name)

    def night_span(self, day: datetime) -> tuple[datetime, datetime]:
        key = (day.date(), day.tzinfo is not None)
        if key not in self._memo:
            self._memo[key] = Config.night_span(self._cfg, day)
        return self._memo[key]

    def _spans_around(self, dt: datetime) -> list[tuple[datetime, datetime]]:
        return Config._spans_around(self, dt)

    def is_night_at(self, dt: datetime) -> bool:
        return Config.is_night_at(self, dt)

    def night_end_at(self, dt: datetime) -> datetime:
        return Config.night_end_at(self, dt)

    def night_start_after(self, dt: datetime) -> datetime:
        return Config.night_start_after(self, dt)


@dataclass
class _Room:
    name: str
    room: object              # discovery.Room
    model: object | None      # plausibles Raummodell, sonst None
    heats: bool               # zählt für den Vorlauf (plausibles Modell, keine Fußbodenheizung)
    managed: bool             # HeatPilot steuert den Raum (Wunschzustand), sonst Tado
    temp: float | None        # mitlaufende Raumtemperatur
    day_sp: float | None      # Tado-Soll tagsüber (nicht gesteuerte Räume); None = Thermostat aus


class _Run:
    """Ein Durchlauf der Vorschau: Eingänge einmal lesen, dann Schritt für Schritt rechnen."""

    def __init__(self, eng, now_ts: int) -> None:
        self.eng, self.cfg = eng, _Nights(eng.cfg)
        self.now = local(now_ts)
        self.h0 = hour_start(now_ts)
        self.end_ts = self.h0 + HOURS * 3600
        self.start_ts = now_ts - now_ts % (STEP_MIN * 60)
        self.sample, readings, persons = eng.snapshot()
        self.t_out_now = self.sample.get("t_out")
        points = eng.forecast_points() if eng.found.weather else []
        self.pt_ts, self.pt_val = [p[0].timestamp() for p in points], [p[1] for p in points]
        f_now = self.t_out_at(self.now)
        self.offset = 0.0 if self.t_out_now is None or f_now is None else self.t_out_now - f_now
        self.dhw_check = guards.check_dhw(eng.dhw)
        self.dhw_last = num(self.sample.get("dhw_set"))
        self.dhw_hyst = eng._boiler("dhw_hyst_on")  # Einschalthysterese wie im Takt (0.46)
        self.dis_h = eng.disinfect_ready_h()
        self.hh = household(eng, self.now, local(self.end_ts), persons)
        self.skipped: list[dict] = []
        self.rooms = self._rooms(readings)
        self.preheat: dict = {}
        st = getattr(getattr(eng, "control_loop", None), "state", None)  # 0.46.5: laufendes Vorheizen (Ziel) je Raum
        self.held = {c: local(x["preheat"]) for c, x in st.items() if x.get("preheat")} if isinstance(st, dict) else {}
        # Fußboden-Grenze (0.39.1): Räume, Anforderung jetzt aus den Messwerten, Räume mit einem Gerät ohne Zustand
        self.floors = floor_limit.floor_rooms(self.cfg, eng.found.rooms)
        seen = floor_limit.read(self.floors, eng.states)
        self.floor_now = {f.climate: floor_limit.demand(f, seen) for f in self.floors}
        self.floor_blind = {f.climate for f in self.floors if floor_limit.blind(f, seen)}
        self.floor_sp0: dict = {}  # Soll jetzt (erster Schritt) je Fußboden-Raum

    def _rooms(self, readings: list[tuple]) -> list[_Room]:
        eng, cfg = self.eng, self.cfg
        by = {r[0]: r for r in readings}
        pres = self.hh.at(self.now)
        day_now = not pres.vacation and pres.home_count != 0 and not eng.eff.is_night(self.now.time())
        measured = {} if day_now else self._day_setpoints()
        out = []
        for room in eng.found.rooms:
            model = eng.rooms.get(room.climate)
            ok = model is not None and guards.check_room(model)["ok"]
            floor = (model is not None and model.floor) or room.climate in cfg.floor_heating_rooms
            temp, target = (by[room.climate][1], by[room.climate][2]) if room.climate in by else (None, None)
            if not ok:
                self.skipped.append({"name": room.name, "why": "Raummodell noch nicht plausibel"})
            elif floor:
                self.skipped.append({"name": room.name, "why": "Fußbodenheizung – zählt nicht für den Vorlauf"})
            day_sp = target if day_now else measured.get(room.climate, float(room.comfort))
            out.append(_Room(room.name, room, model if ok else None, ok and not floor,
                             cfg.is_managing() and room.managed and not room.ems, temp, day_sp))
        return out

    def _day_setpoints(self) -> dict[str, float | None]:
        """Tages-Soll je Raum, gemessen in den letzten ``DAY_SP_H`` Stunden – nur tagsüber, ohne Urlaub und ohne
        Messpunkte, in denen sicher niemand zu Hause war: der Median der Sollwerte, None („aus“), wenn der Raum
        dabei überwiegend aus war. Räume ohne solche Messpunkte fehlen (dann gilt ihr Komfortwert)."""
        rows = self.eng.store.query(
            "SELECT r.ts, r.room, r.target, s.home_count, s.vacation FROM room_samples r "
            "LEFT JOIN samples s ON s.ts = r.ts WHERE r.ts >= ? AND r.ts <= ?",
            (int(self.now.timestamp()) - DAY_SP_H * 3600, int(self.now.timestamp())))
        night: dict[int, bool] = {}
        by: dict[str, list] = {}
        for r in rows:
            if r["ts"] not in night:
                night[r["ts"]] = self.eng.eff.is_night(local(r["ts"]).time())
            if not (r["vacation"] or r["home_count"] == 0 or night[r["ts"]]):
                by.setdefault(r["room"], []).append(r["target"])
        out = {}
        for room, targets in by.items():
            on = [t for t in targets if t is not None]
            out[room] = float(statistics.median(on)) if len(on) * 2 >= len(targets) else None
        return out

    # -------------------------------------------------------------- Eingänge je Zeitpunkt
    def t_out_at(self, when: datetime) -> float | None:
        """Vorhersagepunkt am nächsten zu ``when`` – nur innerhalb ±90 min (wie ``Engine.outdoor_forecast_at``)."""
        if not self.pt_ts:
            return None
        ts = when.timestamp()
        i = bisect.bisect_left(self.pt_ts, ts)
        j = min((k for k in (i - 1, i) if 0 <= k < len(self.pt_ts)), key=lambda k: abs(self.pt_ts[k] - ts))
        return self.pt_val[j] if abs(self.pt_ts[j] - ts) <= FORECAST_MAX_GAP_MIN * 60 else None

    def _t_out(self, t: datetime) -> tuple[float | None, str | None]:
        """(Außentemperatur, Herkunft) zum Zeitpunkt ``t``: Vorhersagepunkt plus Versatz Fühler − Vorhersage von
        jetzt, abklingend über ``FORECAST_FADE_H`` („angeglichen“); ohne Vorhersagepunkt der Fühler („aktuell“)."""
        v = self.t_out_at(t)
        if v is None:
            return self.t_out_now, None if self.t_out_now is None else "aktuell"
        shift = self.offset * min(max(1 - (t - self.now).total_seconds() / 3600 / FORECAST_FADE_H, 0.0), 1.0)
        return (v, "vorhersage") if abs(shift) < 0.05 else (round(v + shift, 2), "angeglichen")

    def _setpoint(self, r: _Room, t: datetime, pres: Presence, t_out: float) -> tuple[float | None, str, datetime | None]:
        """(Soll, Phase, Ziel des Vorheizens) eines Raums zum Zeitpunkt ``t``."""
        if r.managed:
            c = r.room.climate
            d = desired(r.room, t, self.cfg, pres, r.model, r.temp, t_out, self.t_out_at, self.held.get(c))
            self.held[c] = d.until if d.phase == "vorheizen" else None
            return d.setpoint, d.phase, d.until if d.phase == "vorheizen" else None
        sp, eff = r.day_sp, self.eng.eff
        if sp is None:
            return None, "aus", None
        if pres.vacation:
            return min(sp, eff.away_temp), "urlaub", None
        if eff.is_night(t.time()):
            return min(sp, eff.night_temp), "nacht", None
        if pres.home_count == 0:
            return min(sp, eff.away_temp), "abwesend", None
        return sp, "komfort", None

    def _need(self, r: _Room, sp: float | None, phase: str, until: datetime | None, t: datetime,
              t_out: float) -> tuple[float | None, str] | None:
        """(nötiger Vorlauf, Grund) eines Raums jetzt – None: braucht keine Wärme. Räume, die nicht für den
        Vorlauf zählen, brauchen Wärme ohne eigenen Vorlauf (``_other_need``)."""
        if sp is None:
            return None
        if not r.heats:
            return self._other_need(r, sp, t_out)
        m = r.model
        if m.b * (sp - t_out) - m.c <= 0:
            return None  # hält das Soll ohne Heizung (Fremdwärme reicht)
        if r.temp is not None and r.temp > sp + HOLD_BAND_K:
            return None  # kühlt erst aufs Soll herunter
        hold = m.required_flow(sp, t_out)
        if r.temp is None or r.temp >= sp - HEATUP_GAP_K:
            return hold, f"hält {_de(sp)} °C"
        if phase == "vorheizen" and until is not None:
            return max(hold, self._preheat_flow(r, sp, until, t)), f"heizt vor bis {until:%H:%M}"
        flow = heatup.heatup_flow(m, r.temp, sp, t_out, self.cfg.max_flow_temp)  # 0.50.4: statt fest +5 K
        return flow, f"heizt auf {_de(sp)} °C ({flow:.0f} °C, ca. {heatup.HEATUP_H:.0f} h)"

    def _other_need(self, r: _Room, sp: float, t_out: float) -> tuple[None, str] | None:
        """Braucht ein Raum Wärme, der nicht für den Vorlauf zählt? Den Vorlauf bestimmt er nicht, aber solange
        er Wärme braucht, bleibt der Heizbetrieb an. Mit plausiblem Modell (Fußbodenheizung) wie die übrigen
        Räume: Halten braucht Wärme, außer Fremdwärme reicht; ohne Modell nach dem Messwert – unter dem Soll
        (Regelgenauigkeit) oder Temperatur unbekannt."""
        m, kind = r.model, "Fußbodenheizung" if r.model is not None else "ohne Raummodell"
        if r.temp is None:
            return None, f"ohne Raumtemperatur ({kind}) – Heizbetrieb bleibt an"
        if m is not None:
            if m.b * (sp - t_out) - m.c <= 0 or r.temp > sp + HOLD_BAND_K:
                return None
            if r.temp >= sp - HEATUP_GAP_K:
                return None, f"hält {_de(sp)} °C ({kind}) – Heizbetrieb bleibt an"
        elif r.temp >= sp - HEATUP_GAP_K:
            return None
        return None, f"unter Soll ({kind}) – Heizbetrieb bleibt an"

    def _preheat_flow(self, r: _Room, sp: float, until: datetime, t: datetime) -> float:
        """Vorlauf fürs Vorheizen: der niedrigste (1 K genau), mit dem der Raum ab ``t`` bis ``until`` sein Soll
        erreicht – reicht auch der maximale nicht, der maximale. Den Beginn ``t`` bestimmt die Steuerung (``desired``,
        seit 0.46.5 sanft: so früh, dass der niedrigste Vorlauf ab 35 °C reicht, der innerhalb von „Vorheizen höchstens“
        ans Ziel kommt – es ist derselbe). Einmal je Vorheizen gerechnet, dann fest. Die Fußboden-Grenze (0.39.1) gilt danach
        in ``_capped`` – dieselbe Zahl, als suchte es nur bis zur Grenze, und die Stunde sagt „begrenzt durch …“."""
        key = (r.room.climate, until)
        if key not in self.preheat:
            hours = (until - t).total_seconds() / 3600
            t_at = self._t_out(until)[0]
            t_at = 5.0 if t_at is None else t_at
            top = self.cfg.max_flow_temp
            low = max(math.ceil(r.model.required_flow(sp, t_at)), math.ceil(FLOW_MIN))
            self.preheat[key] = next(
                (float(f) for f in range(low, int(top) + 1)
                 if (d := r.model.time_to_reach(r.temp, sp, f, t_at)) is not None and d <= hours), float(top))
        return self.preheat[key]

    # -------------------------------------------------------------- Schritt
    def step(self, t: datetime) -> dict:
        t_out, src = self._t_out(t)
        pres = self.hh.at(t)
        sps, phases, needs, wants = {}, {}, [], {}
        first = t.timestamp() <= self.start_ts
        for r in self.rooms:
            sp, phase, until = self._setpoint(r, t, pres, 5.0 if t_out is None else t_out)
            sps[r.name], phases[r.name] = sp, phase
            need = self._need(r, sp, phase, until, t, t_out) if t_out is not None else None
            wants[r.room.climate] = self._floor_want(r, need is not None, sp, first)
            if need is not None:
                needs.append((need[0], r.name, need[1]))
        cap, cap_why = floor_limit.cap_of(self.floors, wants)
        flow, heating, why, limited = self._capped(needs, self._flow(needs, t_out), cap, cap_why)
        idle = heating is False and self.cfg.boiler_idle == "pumpe"  # 0.46.5: kein Bedarf – nur Pumpe (Weg B)
        why = why.replace("Heizbetrieb aus", boiler_rule.IDLE_TEXT) if idle else why
        dhw, dhw_why, dhw_exact, dhw_hold = self._dhw(t, pres)
        night = self.cfg.is_night_at(t) if self.cfg.is_managing() else self.eng.eff.is_night(t.time())
        return {"ts": int(t.timestamp()), "t": t, "t_out": t_out, "src": src, "flow": flow, "heating": heating,
                "why": why, "dhw": dhw, "dhw_why": dhw_why, "sps": sps, "phases": phases, "night": night,
                "home": pres.home_count, "vacation": pres.vacation,
                "dhw_exact": dhw_exact, "dhw_hold": dhw_hold, "cap": cap, "limited": limited, "idle": idle}

    def _floor_want(self, r: _Room, needs_heat: bool, sp: float | None, first: bool) -> bool | None:
        """Fordert ein Fußboden-Raum in diesem Schritt Wärme an (seit 0.46.5 nur für den Grund, ``floor_limit.why`` – die
        RTL-Grenze gilt immer)? None = offen. Jetzt (erster Schritt) wie „Bereit für die Steuerung?“ aus den Messwerten (0,1 K unter
        dem Soll, Ventil, hvac_action). Danach: braucht er laut Vorschau Wärme, ja; hat ein Gerät keinen Zustand, offen;
        ohne Raummodell (die Temperatur läuft nicht mit) die Anforderung von jetzt, solange das Soll wie jetzt ist."""
        c = r.room.climate
        if c not in self.floor_now:
            return needs_heat
        if first:
            self.floor_sp0[c] = sp
            return self.floor_now[c]
        if needs_heat:
            return True
        if c in self.floor_blind:
            return None
        if r.model is None and c in self.floor_sp0 and self.floor_sp0[c] == sp:
            return self.floor_now[c]
        return False

    def _flow(self, needs: list[tuple], t_out: float | None) -> tuple[int | None, bool | None, str]:
        if not any(r.heats for r in self.rooms):
            return None, None, NO_MODELS
        if t_out is None:
            return None, None, NO_T_OUT
        if not needs:
            return None, False, "kein Raum braucht Wärme – Heizbetrieb aus"
        flows = [n for n in needs if n[0] is not None]
        others = [n for n in needs if n[0] is None]    # Räume ohne eigenen Vorlauf (ohne Modell, Fußboden)
        warming = [n for n in flows if n[2].startswith("heizt")]  # 0.46.5: heizt auf/vor (unter Soll − 0,3 K)
        if flows and max(flows)[0] < FLOW_MIN and not others and not warming:
            # nur Halten unter der Untergrenze lohnt nicht: aus, bis ein Raum mindestens FLOW_MIN braucht oder aufheizt
            return None, False, f"nötiger Vorlauf unter {FLOW_MIN:.0f} °C ({max(flows)[1]}) – Heizbetrieb aus"
        if flows and max(flows)[0] >= FLOW_MIN:
            value, name, why = max(flows)
        else:  # 35 °C ist die Untergrenze, wenn geheizt wird (aufheizen, vorheizen, Raum ohne eigenen Vorlauf)
            value, name, why = (FLOW_MIN, *(max(warming) if warming else others[0])[1:])
        return round(min(max(value, FLOW_MIN), self.cfg.max_flow_temp)), True, f"{name} {why}"

    @staticmethod
    def _capped(needs: list[tuple], res: tuple, cap: float | None, cap_why: str | None) -> tuple:
        """Fußboden-Grenze (0.39.1) auf (Vorlauf, Heizbetrieb, Grund) anwenden – (…, begrenzt?). Über der Grenze gilt
        die Grenze („begrenzt durch …“). Liegt sie unter ``FLOW_MIN``, käme der Vorlauf nie auf die Untergrenze: Dann
        gilt die Grenze, sobald ein Raum Wärme braucht (statt „Heizbetrieb aus“)."""
        flow, heating, why = res
        if cap is None or heating is None:
            return flow, heating, why, False
        top = math.floor(cap + 1e-9)
        if heating and flow > top:
            return top, True, f"{why} – begrenzt durch {cap_why}", True
        if heating is False and needs and top < FLOW_MIN:
            _, name, reason = max(needs, key=lambda n: -math.inf if n[0] is None else n[0])
            return top, True, f"{name} {reason} – begrenzt durch {cap_why}", True
        return flow, heating, why, False

    def _dhw(self, t: datetime, pres: Presence) -> tuple[int | None, str, float | None, float | None]:
        """(Warmwasser-Soll auf ganze Grad wie Weg W, Grund, Empfehlung ungerundet – für die Schwelle, gehaltene
        Speichertemperatur ``hold`` der Empfehlung – 0.46.1, nur zur Anzeige)."""
        if not self.dhw_check["ok"]:
            return None, "lernt noch: " + "; ".join(self.dhw_check["reasons"]), None, None
        if pres.vacation:
            someone, arrival, unknown = False, pres.arrival, None
        else:
            hc = pres.home_count
            someone = None if hc is None else hc > 0
            arrival, unknown = (pres.arrival, pres.arrival_unknown) if hc == 0 else (None, None)
        rec = self.eng.dhw.recommend(t, someone, arrival, self.cfg, unknown_from=unknown,
                                     current_set=self.dhw_last, disinfect_h=self.dis_h, hyst=self.dhw_hyst,
                                     vacation=pres.vacation)
        self.dhw_last = rec["target"]
        # dieselbe Rundung und Schwelle wie Weg W (0.38)
        return (dhw_control.wish(rec["target"], self.cfg), rec["reason"], dhw_control.unrounded(rec["target"], self.cfg),
                rec.get("hold"))

    def advance(self, row: dict, dt_h: float) -> None:
        """Raumtemperaturen einen Schritt weiter: unter dem Soll heizen mit dem Vorlauf, am Soll hält das Thermostat
        es (auf die Regelgenauigkeit genau), ohne Heizbetrieb oder darüber kühlen sie aus. Räume, die nicht für
        den Vorlauf zählen, hält ihr Thermostat am Soll (darunter: aufs Soll), solange der Heizbetrieb nicht
        sicher aus ist – ihr Aufheizen lässt sich ohne Vorlauf nicht rechnen. Ohne Raummodell bleibt der Messwert."""
        t_out = row["t_out"]
        if t_out is None:
            return
        for r in self.rooms:
            if r.temp is None or r.model is None:
                continue
            sp = row["sps"][r.name]
            cooled = r.model.cool_down(r.temp, t_out, dt_h)
            if not r.heats:
                r.temp = max(cooled, sp) if sp is not None and row["heating"] is not False else cooled
                continue
            held = row["heating"] and sp is not None and r.temp >= sp - HEATUP_GAP_K  # Thermostat hält das Soll
            if row["heating"] and sp is not None and not held:
                r.temp = min(r.temp + r.model.rate(r.temp, row["flow"], t_out) * dt_h, sp)
            else:
                r.temp = max(cooled, sp) if held else cooled

    def run(self) -> list[dict]:
        rows = []
        for ts in range(self.start_ts, self.end_ts, STEP_MIN * 60):
            row = self.step(local(ts))
            rows.append(row)
            self.advance(row, STEP_MIN / 60)
        return rows

    def start_state(self) -> dict:
        """Was heute am Kessel steht: Vorlauf-Soll (Regler), Warmwasser-Soll, Heizbetrieb (Betriebsart, Heizen aus)."""
        day, off = self.sample.get("hc_day"), flag(self.sample.get("heating_off"))
        heating = None if day is None and off is None else bool(day != 0 and off != 1)
        return {"flow": num(self.sample.get("sel_flow")), "dhw": num(self.sample.get("dhw_set")), "heating": heating}


# ------------------------------------------------------------------ Ergebnis
def _dominant(values: list):
    """Der Wert, der am längsten gilt (gleich lang: der frühere)."""
    return Counter(values).most_common(1)[0][0] if values else None


def _hour(h_ts: int, rows: list[dict], writes: list[dict], dis: list[datetime], dis_info: dict) -> dict:
    sel = [r for r in rows if h_ts <= r["ts"] < h_ts + 3600]
    heating = _dominant([r["heating"] for r in sel])
    flow = _dominant([r["flow"] for r in sel if r["heating"]]) if heating else None
    lead = next((r for r in sel if r["heating"] == heating and r["flow"] == flow), sel[0])
    t_outs = [r["t_out"] for r in sel if r["t_out"] is not None]
    dhw = _dominant([r["dhw"] for r in sel])
    names = sel[0]["sps"].keys()
    at = [d for d in dis if h_ts <= d.timestamp() < h_ts + 3600]
    return {
        "t": local(h_ts).isoformat(),
        "t_out": round(sum(t_outs) / len(t_outs), 1) if t_outs else None,
        "t_out_src": _dominant([r["src"] for r in sel]),
        "flow": flow, "flow_max": max((r["flow"] for r in sel if r["heating"]), default=None),
        "heating": heating, "flow_reason": lead["why"], "idle": bool(heating is False and lead.get("idle")),
        # Fußboden-Grenze (0.39.1): die der Stunde und ob sie den Vorlauf gesenkt hat
        "flow_cap": _dominant([r["cap"] for r in sel]), "flow_limited": bool(heating and lead["limited"]),
        "dhw": dhw, "dhw_reason": next(r["dhw_why"] for r in sel if r["dhw"] == dhw),
        "dhw_hold": next(r["dhw_hold"] for r in sel if r["dhw"] == dhw),  # 0.46.1: Untergrenze (Soll − Hysterese)
        "rooms": {n: _dominant([r["sps"][n] for r in sel]) for n in names},
        "phases": {n: _dominant([r["phases"][n] for r in sel]) for n in names},
        "night": _dominant([r["night"] for r in sel]), "home": _dominant([r["home"] for r in sel]),
        "vacation": _dominant([r["vacation"] for r in sel]),
        "disinfect": {**dis_info, "at": at[0].isoformat()} if at else None,
        "writes": [w for w in writes if h_ts <= w["ts"] < h_ts + 3600],
    }


def _steps(rows: list[dict]) -> list[dict]:
    """Jede Änderung von Vorlauf, Heizbetrieb oder Warmwasser genau (für einen Stufengrafen)."""
    out, prev = [], None
    for r in rows:
        key = (r["flow"] if r["heating"] else None, r["heating"], r["dhw"], r["idle"])
        if key != prev:
            # 0.46.1, nur Anzeige (Übersicht, nächste 12 h): Gründe und gehaltene Speichertemperatur beim Wechsel
            out.append({"t": r["t"].isoformat(), "flow": key[0], "heating": key[1], "dhw": key[2], "idle": r["idle"],
                        "flow_why": r["why"], "dhw_why": r["dhw_why"], "dhw_hold": r["dhw_hold"]})
            prev = key
    return out


def _written_after(writes: dict[str, list], start: dict, until_ts: int) -> dict:
    """Was nach den Schreibvorgängen bis ``until_ts`` am Kessel stünde (Startwert für die nächste Stunde)."""
    out = dict(start)
    for kind, items in writes.items():
        for t, v, _ in items:
            if t.timestamp() < until_ts:
                out[kind] = v
    return out


def preview(eng, now_ts: float | None = None, written: dict | None = None) -> dict:
    """Vorschau der nächsten 24 h (siehe Modulbeschreibung). ``written``: Werte, die HeatPilot zuletzt geschrieben
    hätte (Protokoll, für die Schreibzählung); sonst gilt, was heute am Kessel steht."""
    now_ts = int(time.time() if now_ts is None else now_ts)
    run = _Run(eng, now_ts)
    rows = run.run()
    start = {**run.start_state(), **(written or {})}
    boiler = _boiler_rule(eng)
    start.update({k: v for k, v in (boiler["ref"] or {}).items() if v is not None})  # Weg B (0.41): wie im Schritt
    dhw = _dhw_rule(eng)
    if dhw["ref"] is not None:  # Weg W (0.38): Startwert und Haltezeit wie im Takt
        start["dhw"] = dhw["ref"][0]
    raw = {
        "flow": count_writes([(r["t"], r["flow"] if r["heating"] else boiler_rule.PUMP_FLOW if r["idle"] else None)
                              for r in rows], start["flow"], FLOW_STEP_K),  # 0.46.5: nur Pumpe – an, 20 °C
        "heating": count_writes([(r["t"], True if r["idle"] else r["heating"]) for r in rows], start["heating"], None),
        "dhw": count_writes([(r["t"], r["dhw"], r["dhw_exact"]) for r in rows], start["dhw"], DHW_STEP_K,
                            since=dhw["since"]),
    }
    writes = sorted(({"ts": int(t.timestamp()), "t": t.isoformat(), "what": kind, "value": v, "from": prev}
                     for kind, items in raw.items() for t, v, prev in items), key=lambda w: (w["ts"], w["what"]))
    cfg = eng.cfg
    dis = cfg.disinfections(local(run.h0), local(run.end_ts - 1))
    dis_info = {"temp": eng.disinfect_temp()[0], "counts": dhw_disinfect.plan_counts(eng.dhw, cfg, run.now)}
    return {
        "now": run.now.isoformat(), "step_min": STEP_MIN, "dry_run": not boiler["writes"],
        "note": NOTE_BOILER if boiler["writes"] else NOTE,
        "control_mode": cfg.control_mode,
        "hours": [_hour(h, rows, writes, dis, dis_info) for h in range(run.h0, run.end_ts, 3600)],
        "steps": _steps(rows),
        "writes_24h": {k: len(v) for k, v in raw.items()},
        "start": start,
        "written_next": _written_after(raw, start, run.h0 + 3600),
        "rules": {"flow_k": FLOW_STEP_K, "dhw_k": DHW_STEP_K, "hold_min": HOLD_MIN},
        "flow_note": rows[0]["why"] if rows and rows[0]["heating"] is None else None,
        "dhw_note": rows[0]["dhw_why"] if rows and rows[0]["dhw"] is None else None,
        "rooms_used": [r.name for r in run.rooms if r.heats],
        "rooms_skipped": run.skipped,
        "floor": floor_limit.describe(run.floors),  # Fußboden-Räume mit Anschluss und Grenze (0.39.1)
        "forecast": bool(run.pt_ts),
        # Versatz Außenfühler − Vorhersage jetzt (K): so weit ist die Vorhersage angeglichen, klingt über fade_h ab
        "forecast_offset_k": round(run.offset, 1) if run.pt_ts and run.t_out_now is not None else None,
        "forecast_fade_h": FORECAST_FADE_H,
        # Weg W (0.38): Schalter des Warmwasser-Solls und wie seine Schreibvorgänge heißen
        "dhw_mode": dhw["mode"], "dhw_writes": dhw["label"], "dhw_paused": dhw["paused"],
        # Weg B (0.41): Schalter „Kessel über HeatPilot“ und wie Vorlauf- und Heizbetrieb-Schreibvorgänge heißen
        "boiler_mode": boiler["mode"], "flow_writes": boiler["label"], "boiler_blocked": boiler["blocked"],
        "boiler_paused": boiler["paused"],
    }


def _boiler_rule(eng) -> dict:
    """Weg B (0.41) für die Vorschau: Schalter, Startwerte wie im Schritt (``BoilerControl.reference``) und die
    Bezeichnung „schreibt“ (heatpilot, nicht pausiert, nicht gesperrt) bzw. „würde schreiben“."""
    ctl = getattr(eng, "boiler_control", None)
    mode = getattr(eng.cfg, "boiler_control", "aus")
    blocked = ctl.blocker() if ctl is not None and mode == "heatpilot" else None
    paused = bool(ctl is not None and mode == "heatpilot" and ctl.paused())
    writes = mode == "heatpilot" and ctl is not None and not paused and not blocked
    return {"mode": mode, "ref": ctl.reference() if ctl is not None else None, "writes": writes, "paused": paused,
            "blocked": blocked, "label": "schreibt" if writes else "würde schreiben"}


def _dhw_rule(eng) -> dict:
    """Weg W für die Vorschau: Schalter, Startwert (Wert, letzter Schreibzeitpunkt) nach derselben Regel wie
    ``dhw_control`` und die Bezeichnung „schreibt“ (HeatPilot setzt, nicht pausiert) bzw. „würde schreiben“."""
    ctl = getattr(eng, "dhw_control", None)
    mode = getattr(eng.cfg, "dhw_control", "aus")
    ref = ctl.reference() if ctl is not None else None
    paused = bool(ctl is not None and ctl.paused())
    since = None if ref is None or ref[1] is None else local(ref[1])
    writes = mode == "heatpilot" and not paused and ctl is not None and bool(ctl.entity_id)
    return {"mode": mode, "ref": ref, "since": since, "paused": paused and mode == "heatpilot",
            "label": "schreibt" if writes else "würde schreiben"}


def cached(eng, now_ts: float | None = None, max_age_s: float = CACHE_S) -> dict:
    """Vorschau für die Oberfläche: höchstens alle ``max_age_s`` neu gerechnet (und zur vollen Stunde)."""
    now = time.time() if now_ts is None else now_ts
    hit = eng.__dict__.get("_preview")
    if hit is None or now - hit[0] > max_age_s or now < hit[0] or hour_start(now) != hour_start(hit[0]):
        eng.__dict__["_preview"] = (now, preview(eng, now))
    return eng.__dict__["_preview"][1]
