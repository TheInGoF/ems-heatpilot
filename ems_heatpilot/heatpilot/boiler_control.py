"""Weg B (seit 0.41): Kessel über HeatPilot – Vorlauf-Soll live und Heizbetrieb an/aus, nur ohne Regler am Bus.

Schalter ``boiler_control`` (Standard „aus“), getrennt von Raumsteuerung (``control_mode``) und Warmwasser
(``dhw_control``):

* ``aus`` – nichts; HeatPilot rechnet den Kessel nicht,
* ``trocken`` – rechnet live (alle 30 s) und protokolliert „würde schreiben“ (Vorlauf-Soll, Heizbetrieb) mit Grund,
  schreibt nichts – geht immer, auch mit Regler am Bus (Vergleich mit dem, was der Regler setzt),
* ``heatpilot`` – schreibt über Home Assistant: ``number.set_value`` auf die eine Entität des Vorlauf-Solls und
  ``switch.turn_on``/``turn_off`` auf die eine Entität des Heizbetriebs (``Control.set_flow``/``set_heating``: nur
  diese, ganzzahlig, 35 °C…``max_flow_temp`` und Fußboden-Grenze – alles andere hart abgewiesen). **Solange ein Regler
  am Bus ist** (oder es unbekannt ist), wird nicht geschrieben: Er überschreibt alle 60 s, was am Kessel gesetzt wird
  (``boiler_entity.regulator``). EMS-ESP bleibt nur lesend. Seit 0.48 läuft im selben Schritt die Antipendelzeit mit
  (``period_control``, nur mit „Antipendelzeit anpassen“; Standard aus – dann fasst HeatPilot sie nie an).

Ablauf je Schritt (``plan`` im 30-s-Takt der Live-Werte, danach ``apply``; ``main.boiler_step``):

1. fällige Prüfung (``verify``): 60 s nach dem Schreiben zurücklesen – Entität in Home Assistant bzw. EMS-ESP direkt
   (frisch, höchstens 90 s alt; widersprechen sich beide, ist der Wert unklar, geprüft wird bis 5 min danach weiter).
   Nicht übernommen: Fehlschlag (Protokoll, Hinweis, Meldung, Problem-Sensor); nach 3 Fehlschlägen in Folge Pause bis
   „wieder einschalten“. Während einer Warmwasser-Ladung wartet die Prüfung des Vorlaufs, bis sie vorbei ist.
2. Ziel rechnen (``boiler_flow.compute``: Bedarf, Abnahme, Takt-Boost, Fußboden-Grenze, Obergrenze, Grenzen der
   Entität). Ist es länger als ``BLIND_S`` nicht rechenbar (Außentemperatur fehlt, kein Raummodell), gilt der
   Rückfall-Vorlauf mit Heizbetrieb an (``_blind``) – mit Hinweis und Meldung; danach geht es normal weiter.
3. Fremde Änderungen erkennen (nur „heatpilot“, nicht gesperrt, ``boiler_watch``): ändert sich ein Wert ohne
   HeatPilot, Hinweis und Meldung („Regler noch dran? Automation?“); 2 fremde Änderungen in 1 h → Pause. Ein Eingriff
   des Wächters in Home Assistant wird erkannt und gemeldet, aber nicht gezählt.
4. Entscheiden (``boiler_rule``): Vorlauf-Soll nur ab 1 K Änderung, höchstens ein Schreiben je 60 s, anheben sofort,
   senken erst nach 10 min niedrigerem Ziel, nicht während einer Warmwasser-Ladung; Heizbetrieb mit 5 min Haltezeit,
   aus erst nach 15 min ohne Bedarf, an nur mit bekanntem Vorlauf-Soll bis zur Grenze. **Schutz:** Steht das
   Vorlauf-Soll am Kessel über der Grenze (Fußboden, höchster Vorlauf, Entität – in einer der beiden Quellen), wird
   sofort gesenkt bzw. – ohne Bedarf – der Heizbetrieb ausgeschaltet. Bei „heatpilot“ vormerken – nur ohne Hindernis
   (``blocker``: dieselben Hindernisse wie in „Bereit für die Steuerung?“). Weist ``Control`` einen Schreibvorgang ab,
   steht das als Hinweis da und wird nach 15 min gemeldet.

**Zurückschalten** auf aus/trocken: einmal den Rückfall-Vorlauf (``boiler_fallback_flow``, Standard 45 °C, höchstens
die strengste Fußboden-Grenze) und Heizbetrieb an schreiben – noch über die alte Schreibstelle (``fallback``, von
``main.App.build`` aufgerufen), damit der Kessel nicht auf einem Nacht-Wert stehen bleibt; Hinweis, den Regler wieder
anzuschließen bzw. den Kessel selbst zu stellen. Fällt HeatPilot aus, bleibt der letzte Vorlauf stehen – dafür der
Wächter in Home Assistant (``watchdog``). Der Zustand überlebt Neustarts (kv ``boiler_control.state``); die Anzeige
steht in ``boiler_status``, Überwachung und Meldungen in ``boiler_watch``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time

from . import boiler_entity as be
from . import boiler_flow, plausibility
from . import boiler_rule as br
from .boiler_rule import CHECK_MAX_S, CHECK_S, EMS_FRESH_S, MAX_FAILS, Decision, _c, decide_flow, decide_heating, shown
from .boiler_status import (ACT_DRY, ACT_FAIL, ACT_FALLBACK, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME,  # noqa: F401
                            ACTIONS, LOG_ROOM, BoilerStatus)
from .boiler_status import fallback as fallback_flow
from .boiler_watch import (BLOCKED_NOTE_S, K_BLIND, K_BLOCKED, K_FAIL, K_FOREIGN, K_PAUSED,  # noqa: F401
                           K_REJECTED, K_WATCHDOG, BoilerWatch, same)
from .dhw_control import special_running
from .period_control import PeriodControl
from .discovery_roles import BOILER_ROLES
from .values import flag, num

log = logging.getLogger(__name__)

KV = "boiler_control.state"
WHATS = ("flow", "heating")
ROLES = {"flow": "sel_flow_temp", "heating": "heating_activated"}
_same = same  # wie bisher hier zu holen


def _value(what: str, raw):
    """Zustand als Wert: Vorlauf-Soll als plausible Zahl, Heizbetrieb als True/False (None = unbekannt)."""
    if what == "flow":
        return plausibility.in_range("sel_flow", num(raw))
    f = flag(raw)
    return None if f is None else bool(f)


class BoilerControl(BoilerStatus, BoilerWatch):
    """Zustand von Weg B (je Wert: letztes Schreiben, Prüfung, zuletzt gesehen, Trockenlauf; Fehlschläge, Pause,
    fremde Änderungen, Takt-Boost) und der Schritt dazu."""

    def __init__(self, eng, control=None, notifier=None, entities: dict | None = None,
                 carry: BoilerControl | None = None) -> None:
        """``entities``: {"flow": (Entität, Herkunft), "heating": (…)} aus ``boiler_entity.flow_target``/
        ``heat_target`` – dieselben bekommt ``Control``. ``carry``: Instanz vor einem Neuaufbau (Zustand geht über)."""
        self.eng, self.control, self.notifier = eng, control, notifier
        self.store = eng.store
        ents = entities or {}
        self.entities = {w: (ents.get(w) or (None, None))[0] for w in WHATS}
        self.sources = {w: (ents.get(w) or (None, None))[1] for w in WHATS}
        self.state: dict = carry.state if carry is not None else dict(self.store.get(KV) or {})
        self.pending: list[tuple[str, object, str]] = []
        self.planned_ts: int | None = None
        self.view: dict = {}
        self.stopped = False
        self._saved: str | None = None
        self.period = PeriodControl(self, ents.get("period"), carry.period if carry is not None else None)  # 0.48

    def stop(self) -> None:
        """Außer Dienst (main.build): vorgemerkte Schreibvorgänge werden verworfen."""
        self.stopped = True

    @property
    def mode(self) -> str:
        return self.eng.cfg.boiler_control

    def paused(self) -> bool:
        return bool(self.state.get("paused"))

    def _ch(self, what: str) -> dict:
        return self.state.setdefault(what, {})

    # -------------------------------------------------------------- Kessel-Werte
    def readings(self, what: str) -> dict:
        """Wert am Kessel aus beiden Quellen: Entität in HA (``ha``) und EMS-ESP direkt (``ems``, nur frisch);
        ``unclear``: beide bekannt, aber verschieden."""
        eid = self.entities[what]
        ha = _value(what, (self.eng.states.get(eid) or {}).get("state")) if eid else None
        ts = getattr(self.eng, "ems_ts", None)
        boiler = (self.eng.ems.get("boiler") or {}) if ts is not None and time.time() - ts <= EMS_FRESH_S else {}
        ems = next((v for v in (_value(what, boiler.get(k)) for k in BOILER_ROLES[ROLES[what]]) if v is not None), None)
        return {"ha": ha, "ems": ems, "unclear": ha is not None and ems is not None and not _same(what, ha, ems)}

    def current(self, what: str, r: dict | None = None):
        """Wert jetzt: Entität in HA, sonst EMS-ESP (frisch); widersprechen sie sich, None (nichts schreiben)."""
        r = r or self.readings(what)
        if r["unclear"]:
            return None
        return r["ha"] if r["ha"] is not None else r["ems"]

    def _shown(self, what: str, seen, r: dict) -> str:
        if seen is not None:
            return f"Kessel zeigt {shown(what, seen)}"
        if r["unclear"]:
            return f"unklar: Home Assistant zeigt {shown(what, r['ha'])}, EMS-ESP {shown(what, r['ems'])}"
        return f"{br.LABELS[what]} unbekannt"

    def charging(self) -> bool:
        """Warmwasser lädt bzw. Sonderladung läuft – das Vorlauf-Soll am Kessel gehört gerade der Ladung."""
        sample = {"dhw_special": self.eng.dhw_special(), "dhw_disinfecting": flag(self.eng._boiler("dhw_disinfecting"))}
        return flag(self.eng._boiler("dhw_active")) == 1 or special_running(self.eng, sample)

    def bounds(self) -> tuple[float | None, float | None]:
        """(min, max) der Entität des Vorlauf-Solls laut Home Assistant – Grenzen fürs Ziel (``boiler_flow``)."""
        eid = self.entities["flow"]
        return be.bounds(self.eng.states.get(eid) if eid else None)

    def blocker(self, now: float | None = None) -> str | None:
        """Warum jetzt nicht geschrieben werden darf (Regler am Bus, Entitäten, EMS-ESP, „Heizen abschalten“) – None =
        darf. Zählt nie als Fehlschlag."""
        problems = be.write_problems(self.eng, self.entities["flow"], self.entities["heating"], now)
        return problems[0] if problems else None

    # -------------------------------------------------------------- Schritt
    def plan(self, now: float | None = None) -> None:
        """Im 30-s-Takt: Prüfung, Ziel, fremde Änderungen, Entscheidung. Schreibt selbst nichts – bei „heatpilot“
        werden die Schreibvorgänge für ``apply`` vorgemerkt, im Trockenlauf nur protokolliert."""
        now = time.time() if now is None else now
        ts, mode, st = int(now), self.mode, self.state
        self.pending, self.planned_ts = [], ts
        self._switch(mode, ts)
        if mode == "aus":
            self.view = {"ts": ts}
            self._settle(ts)
            self._save()
            self.period.plan(ts, None, now)
            return
        self.verify(now)
        target, st["boost"] = boiler_flow.compute(self.eng, ts, st.get("boost"), self.bounds())
        target = self._blind(ts, target)
        target = self._no_switch(target)
        cur = {w: self.current(w) for w in WHATS}
        blocked = self.blocker(now) if mode == "heatpilot" else None
        if mode == "heatpilot":
            self._watch(ts, cur, target.charging, blocked)
            self._resolve(ts, cur)
            self._blocked_note(ts, blocked)
            self._rejected_note(ts)
        dec = self._decide(mode, ts, target, cur, blocked)
        heat_first = dec["heating"].write and dec["heating"].value is False  # aus vor dem Vorlauf
        for what in ("heating", "flow") if heat_first else ("flow", "heating"):
            d = dec[what]
            if d.write and what == "flow":
                self._ch("flow").pop("down", None)  # nach einem Schreiben beginnt das 10-min-Fenster neu
            if d.write and mode == "trocken":
                self._ch(what)["dry"] = {"ts": ts, "value": d.value, "from": self._reference(mode, what, cur[what])[0]}
                self._log(ts, ACT_DRY, d.value if what == "flow" else int(d.value),
                          f"Beobachten: würde {br.LABELS[what]} {shown(what, d.value)} schreiben ({target.reason})")
            elif d.write and mode == "heatpilot":
                self.pending.append((what, d.value, f"{d.why} · {target.reason}" if "Schutz" in d.why
                                     else target.reason))
        self.view = {"ts": ts, "target": target, "current": cur, "blocked": blocked,
                     "decision": {w: dec[w].why for w in WHATS}, "until": {w: dec[w].until for w in WHATS},
                     "write": {w: dec[w].write for w in WHATS}}
        st["last_plan"] = ts
        self._settle(ts)
        self._save()
        self.period.plan(ts, target, now)  # 0.48: Antipendelzeit mit demselben Ziel und derselben Sperre

    def _decide(self, mode: str, ts: int, target, cur: dict, blocked: str | None) -> dict[str, Decision]:
        """Vorlauf-Soll und Heizbetrieb nach den Regeln (``boiler_rule``) – mit dem Schutz über der Grenze."""
        paused = mode == "heatpilot" and self.paused()
        ref = {w: self._reference(mode, w, cur[w]) for w in WHATS}
        checking = {w: mode == "heatpilot" and bool(self._ch(w).get("check")) for w in WHATS}
        seen = self._flow_values(mode, ref["flow"][0])
        over = self._over(mode, seen, target.hi)
        flow = self._flow_decision(ts, target, ref["flow"], paused, checking["flow"], over)
        heat = self._heat_decision(ts, target, ref, paused, checking["heating"], over, flow, bool(seen))
        out = {"flow": flow, "heating": heat}
        if blocked:
            out = {w: Decision(False, d.value, blocked) if d.write else d for w, d in out.items()}
        return out

    def _flow_values(self, mode: str, ref_flow) -> list[float]:
        """Was der Kessel als Vorlauf-Soll zeigt: bei „heatpilot“ Home Assistant und EMS-ESP (frisch) – auch beide,
        wenn sie sich widersprechen –, im Trockenlauf der Bezugswert."""
        if mode == "heatpilot":
            r = self.readings("flow")
            return [v for v in (r["ha"], r["ems"]) if v is not None]
        return [ref_flow] if ref_flow is not None else []

    def _over(self, mode: str, values: list[float], hi: int | None) -> float | None:
        """Vorlauf-Soll am Kessel über der Grenze ``hi`` (mehr als 0,5 K)? Der höchste Wert aus ``values`` (auch einer
        von beiden bei unklarem Wert). None, wenn nicht – oder wenn ein Wert bis zur Grenze schon geschrieben ist und
        seine Prüfung läuft."""
        if hi is None:
            return None
        chk = self._ch("flow").get("check") if mode == "heatpilot" else None
        if chk and chk["value"] <= hi:
            return None
        top = max(values, default=None)
        return top if top is not None and top > hi + br.CHECK_TOL_K else None

    def _flow_decision(self, ts: int, target, ref: tuple, paused: bool, checking: bool, over) -> Decision:
        """Vorlauf-Soll: anheben sofort, senken erst nach ``FLOW_DOWN_S`` niedrigerem Ziel (auf den höchsten Wert in
        dieser Zeit, höchstens die Grenze), über der Grenze sofort (Schutz)."""
        if not target.heating:
            return Decision(False, None, target.reason if target.heating is None else "Heizbetrieb aus – Vorlauf-Soll "
                                                                                     "bleibt, wie es ist")
        current, last_ts = ref
        fch = self._ch("flow")
        if target.flow is not None and current is not None and target.flow <= current - br.STEP_K + 1e-9:
            down = fch.get("down") or {"since": ts, "peak": target.flow}
            down["peak"] = max(down["peak"], target.flow)
            fch["down"] = down
        else:
            fch.pop("down", None)
        down = fch.get("down")
        value = target.flow if over is not None or not down else min(down["peak"], target.hi or down["peak"])
        return decide_flow(value, current, ts, last_ts, target.charging, paused, checking,
                           (down or {}).get("since"), over)

    def _no_switch(self, target):
        """Lässt sich der Heizbetrieb nicht schalten (nur Rückmeldung des Kessels, 0.46.7), wird aus „Heizbetrieb aus“
        „nur Pumpe“: Heizbetrieb bleibt, Vorlauf-Soll ``PUMP_FLOW`` – der Brenner springt nicht an."""
        if target.heating is not False or be.heat_usable(self.eng, self.entities["heating"]):
            return target
        pump = br.idle_flow(self.eng.cfg, self.bounds()) or br.PUMP_FLOW
        return dataclasses.replace(target, heating=True, flow=pump, base=pump, idle=True,
                                   reason=f"{target.reason} – Heizbetrieb nur Rückmeldung: {br.IDLE_TEXT}")

    def _heat_decision(self, ts: int, target, ref: dict, paused: bool, checking: bool, over, flow,
                       known: bool) -> Decision:
        """Heizbetrieb: aus erst nach ``HEAT_OFF_S`` ohne Bedarf, als Schutz sofort (Vorlauf über der Grenze ohne
        Bedarf, Fußboden-Grenze unter 35 °C); an nur, wenn das Vorlauf-Soll bekannt ist (``known``) und höchstens bei
        der Grenze liegt – oder es im selben Schritt geschrieben wird."""
        hch, want = self._ch("heating"), target.heating
        if not be.heat_usable(self.eng, self.entities["heating"]):  # 0.46.7: nur Rückmeldung – nie schalten
            return Decision(False, None, "Heizbetrieb ist nur eine Rückmeldung des Kessels – HeatPilot schaltet ihn nicht")
        if want is False:
            hch.setdefault("off_since", ts)
        else:
            hch.pop("off_since", None)
        protect = None
        if over is not None and not want and not target.charging:
            want, protect = False, f"Vorlauf-Soll am Kessel {_c(over)} °C über der Grenze {target.hi} °C"
        elif target.cap_off:
            protect = "Grenze unter 35 °C"
        unsafe = None
        if want and not flow.write:
            unsafe = (f"Vorlauf-Soll am Kessel {_c(over)} °C über der Grenze {target.hi} °C" if over is not None
                      else "Vorlauf-Soll am Kessel unbekannt" if not known else None)
        return decide_heating(want, ref["heating"][0], ts, ref["heating"][1], paused, checking, hch.get("off_since"),
                              unsafe, protect)

    def _blind(self, ts: int, target):
        """Kein Vorlauf-Soll rechenbar (Außentemperatur fehlt, kein Raummodell …): nach ``BLIND_S`` gilt der
        Rückfall-Vorlauf mit Heizbetrieb an – nach denselben Regeln (Grenzen, Schwelle, Warmwasser-Ladung, Prüfung) –
        mit Hinweis und Meldung. Ist das Ziel wieder rechenbar, geht es normal weiter."""
        st = self.state
        if target.heating is not None:
            if st.pop("blind", None) is not None and self.notifier is not None:
                self.notifier.solved(K_BLIND, "Kessel: Vorlauf-Soll wieder rechenbar – HeatPilot führt wieder", ts)
            return target
        blind = st.setdefault("blind", {"since": ts})
        blind["why"] = target.reason
        if ts - blind["since"] < br.BLIND_S:
            return target
        fb = fallback_flow(self.eng)
        if fb is None or target.hi is None or fb > target.hi:
            return dataclasses.replace(target, reason=f"{target.reason} – Rückfall-Vorlauf läge unter 35 °C bzw. über "
                                                      "der Grenze, HeatPilot schreibt nichts")
        why = f"kein Vorlauf-Soll rechenbar seit {(ts - blind['since']) // 60} min – Rückfall-Vorlauf {fb} °C"
        if self.mode == "heatpilot":
            self._notify(K_BLIND, f"Kessel: kein Vorlauf-Soll rechenbar ({target.reason}) – Kessel auf Rückfallwert "
                                  f"{fb} °C und Heizbetrieb an", ts)
        return dataclasses.replace(target, heating=True, flow=fb, reason=f"{why} ({target.reason})", base=fb)

    def _reference(self, mode: str, what: str, current) -> tuple:
        """Womit verglichen wird: bei „heatpilot“ der Wert am Kessel und das letzte Schreiben; im Trockenlauf der
        zuletzt „geschriebene“ Wert – sonst schriebe er jeden Schritt dasselbe ins Protokoll."""
        ch = self._ch(what)
        if mode == "trocken":
            dry = ch.get("dry")
            return (dry["value"], dry["ts"]) if dry else (current, None)
        return current, (ch.get("last") or {}).get("ts")

    def reference(self) -> dict | None:
        """Für die Vorschau (dieselbe Regel): Startwerte {flow, heating} bei trocken/heatpilot, sonst None."""
        mode = self.mode
        return None if mode == "aus" else {w: self._reference(mode, w, self.current(w))[0] for w in WHATS}

    def _switch(self, mode: str, ts: int) -> None:
        """Schalter umgestellt: weg von „heatpilot“ Prüfungen verwerfen und den Stand für den Hinweis merken (den
        Rückfallwert schreibt ``fallback`` vorher über die alte Schreibstelle); Trockenlauf neu beginnen; bei
        „heatpilot“ Fehlschläge und fremde Werte ab jetzt zählen. Der Takt-Boost beginnt immer neu."""
        st = self.state
        prev = st.get("mode")
        if prev == mode:
            return
        if prev == "heatpilot":
            st["stopped"] = {"ts": ts, **{w: (self._ch(w).get("last") or {}).get("value") for w in WHATS}}
            for w in WHATS:
                self._ch(w).pop("check", None)
        for w in WHATS:
            for key in ("down", "off_since"):
                self._ch(w).pop(key, None)
            if mode == "trocken":
                self._ch(w).pop("dry", None)
            if mode == "heatpilot":
                self._ch(w).pop("seen", None)
        if mode == "heatpilot":
            st.pop("stopped", None)
            st.pop("blocked_since", None)
            st["fails"], st["since"] = 0, ts
        st["boost"] = None
        for key in ("blind", "rejected"):
            st.pop(key, None)
        st["mode"] = mode
        if prev is not None:
            log.info("Kessel über HeatPilot: Schalter %s → %s", prev, mode)

    # -------------------------------------------------------------- Schreiben
    async def apply(self, now: float | None = None) -> int:
        """Vorgemerktes schreiben (Vorlauf-Soll, Heizbetrieb, seit 0.48 Antipendelzeit) – Zahl der Schreibvorgänge."""
        return await self._apply(now) + await self.period.apply(now)

    async def _apply(self, now: float | None = None) -> int:
        """Vorlauf-Soll und Heizbetrieb – nur über ``Control``, nur bei „heatpilot“, nicht pausiert, ohne Hindernis,
        nicht nach einem Neuaufbau. Merkt je Wert die Prüfung nach 60 s vor."""
        pending, self.pending = self.pending, []
        if not pending:
            return 0
        if self.stopped or self.mode != "heatpilot" or self.paused() or self.control is None:
            log.info("Kessel über HeatPilot: Schreibvorgang verworfen – Schalter, Pause oder Neuaufbau")
            return 0
        problem = self.blocker(now)
        if problem:
            log.info("Kessel über HeatPilot: Schreibvorgang verworfen – %s", problem)
            return 0
        n = 0
        for what, value, why in pending:
            if not await self._write(what, value, why, now):
                break  # ein Fehler: nicht weiter (z. B. Heizbetrieb an ohne neues Vorlauf-Soll)
            n += 1
        self._save()
        return n

    async def _write(self, what: str, value, why: str, now: float | None) -> bool:
        ts = self.planned_ts or int(time.time())
        before = self.current(what)
        ch = self._ch(what)
        try:
            if what == "flow":
                await self.control.set_flow(value, f"{why} (vorher {shown(what, before)})")
            else:
                await self.control.set_heating(bool(value), why)
        except PermissionError as err:  # Schutzregel (z. B. Fußboden-Grenze inzwischen tiefer): kein Fehlschlag
            self._rejected(ts, what, value, err)
            return False
        except Exception as err:  # noqa: BLE001 – zählt als Fehlschlag, nächster Versuch im nächsten Schritt
            ch["last"] = {"ts": ts, "value": value, "from": before, "reason": why}
            self._failed(ts, what, value, f"nicht geschrieben ({err})")
            return False
        ch["last"] = {"ts": ts, "value": value, "from": before, "reason": why}
        if (self.state.get("rejected") or {}).get("what") == what:
            self.state.pop("rejected")
        done = time.time() if now is None else now
        ch["check"] = {"ts": ts, "value": value, "due": done + CHECK_S, "until": done + CHECK_MAX_S}
        log.info("Kessel über HeatPilot: %s → %s (%s), Prüfung in %d s", br.LABELS[what], shown(what, value), why,
                 CHECK_S)
        return True

    async def fallback(self, value: int | None, now: float | None = None) -> dict:
        """Zurückschalten (heatpilot → aus/trocken, ``main.App.build`` mit der alten Schreibstelle): einmal den
        Rückfall-Vorlauf ``value`` und Heizbetrieb an schreiben – nur ohne Hindernis (ein Regler am Bus regelt ohnehin).
        Ergebnis im Zustand (``fallback``) und im Protokoll."""
        ts = int(time.time() if now is None else now)
        res: dict = {"ts": ts, "value": value, "ok": False}
        problem = self.blocker(now)
        if self.control is None:
            res["text"] = "nichts zurückgeschrieben – keine Verbindung"
        elif value is None:
            res["text"] = "nichts zurückgeschrieben – der Rückfall-Vorlauf läge unter 35 °C (Fußboden-Grenze)"
        elif problem:
            res["text"] = f"nichts zurückgeschrieben – {problem}"
        else:
            try:
                await self.control.set_flow(value, "Zurückschalten: Rückfall-Vorlauf")
                await self.control.set_heating(True, "Zurückschalten: Heizbetrieb an")
                res.update(ok=True, text=f"Vorlauf-Soll {value} °C und Heizbetrieb an geschrieben")
            except Exception as err:  # noqa: BLE001 – nur melden, der Neuaufbau geht weiter
                res["text"] = f"Rückfallwert nicht geschrieben ({err})"
        self.state["fallback"] = res
        self.pending = []
        self._log(ts, ACT_FALLBACK, value, f"Kessel über HeatPilot zurückgeschaltet: {res['text']} – {br.HINT_BACK}")
        log.info("Kessel über HeatPilot zurückgeschaltet: %s", res["text"])
        self._save()
        return res

    # -------------------------------------------------------------- Prüfen
    def verify(self, now: float | None = None) -> bool:
        """Prüfung 60 s nach dem Schreiben: Zeigt der Kessel den Wert (Vorlauf ±0,5 K, Heizbetrieb gleich)? Unbekannt
        oder unklar: bis 5 min danach weiter prüfen. Während einer Warmwasser-Ladung wartet die Prüfung des Vorlaufs.
        True, wenn geprüft wurde."""
        now, st, done = time.time() if now is None else now, self.state, False
        for what in WHATS:
            chk = self._ch(what).get("check")
            if not chk or now < chk["due"]:
                continue
            if self.mode != "heatpilot":
                self._ch(what).pop("check")
                continue
            if what == "flow" and self.charging():
                chk["until"] = max(chk["until"], now + CHECK_MAX_S)
                continue
            r = self.readings(what)
            seen = self.current(what, r)
            if seen is None and now < chk["until"]:
                continue
            self._ch(what).pop("check")
            ok = _same(what, seen, chk["value"])
            st["last_check"] = {"ts": int(now), "what": what, "value": chk["value"], "seen": seen, "ok": ok,
                                "shown": self._shown(what, seen, r)}
            self._ch(what)["seen"] = seen
            if ok:
                self._accepted(int(now), what, chk["value"])
            else:
                self._failed(int(now), what, chk["value"], f"nicht übernommen ({st['last_check']['shown']})")
            done = True
        if done:
            self._save()
        return done

    def _accepted(self, ts: int, what: str, value, late: bool = False) -> None:
        self.state["fails"] = 0
        log.info("Kessel über HeatPilot: %s %s %sübernommen", br.LABELS[what], shown(what, value),
                 "verspätet " if late else "")
        if self.notifier is not None:
            self.notifier.solved(K_FAIL, f"Kessel wieder in Ordnung – {br.LABELS[what]} {shown(what, value)} "
                                         "übernommen", ts)

    def _failed(self, ts: int, what: str, value, text: str) -> None:
        """Fehlschlag: Protokoll, Hinweis, Meldung (einmal je Ereignis); ab ``MAX_FAILS`` in Folge pausieren."""
        st = self.state
        st["fails"] = fails = int(st.get("fails", 0)) + 1
        msg = f"{br.LABELS[what]} {shown(what, value)} {text}"
        self._log(ts, ACT_FAIL, value if what == "flow" else int(bool(value)), f"{msg} – Fehlschlag {fails} von "
                                                                                f"{MAX_FAILS}")
        log.warning("Kessel über HeatPilot: %s – Fehlschlag %d von %d", msg, fails, MAX_FAILS)
        self._notify(K_FAIL, f"Kessel: {msg}", ts)
        if fails >= MAX_FAILS:
            self._pause(ts, f"{MAX_FAILS} Fehlschläge in Folge",
                        f"Kessel: {msg} – {MAX_FAILS}. Fehlschlag in Folge, Kessel über HeatPilot pausiert. Bitte "
                        "prüfen und in HeatPilot wieder einschalten")

    def _resolve(self, ts: int, cur: dict) -> None:
        """Fehlschlag erledigt, ohne neu zu schreiben: Zeigt der Kessel den zuletzt geschriebenen Wert doch (spät
        übernommen, EMS-ESP neu gestartet), ist es ein Erfolg – auch während einer Pause."""
        if not int(self.state.get("fails", 0)):
            return
        for what in WHATS:
            ch = self._ch(what)
            last = (ch.get("last") or {}).get("value")
            if not ch.get("check") and last is not None and _same(what, cur[what], last):
                self._accepted(ts, what, last, late=True)
                return

    def _log(self, ts: int, action: str, value, reason: str) -> None:
        self.store.add_control_log(int(ts), self.entities["flow"] or LOG_ROOM, action, value, reason)

    def _save(self) -> None:
        """Zustand speichern – nur bei Änderung (der Schritt läuft alle 30 s)."""
        text = json.dumps(self.state, sort_keys=True, default=str)
        if text != self._saved:
            self.store.put(KV, self.state)
            self._saved = text
