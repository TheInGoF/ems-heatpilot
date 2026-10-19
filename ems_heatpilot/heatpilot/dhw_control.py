"""Weg W (seit 0.38): HeatPilot setzt das Warmwasser-Soll des Kessels – der einzige Schreibweg zum Kessel.

Schalter ``dhw_control`` (Einstellungen → Warmwasser), unabhängig von der Raumsteuerung (``control_mode``):

* ``aus`` (Standard) – wie bisher: nur Empfehlung und Vorschau,
* ``trocken`` – rechnet im Takt und protokolliert „würde schreiben X °C (Grund)“ (``control_log``, Aktion
  ``dry_run``), schreibt nichts,
* ``heatpilot`` – schreibt über Home Assistant ``number.set_value`` auf die Entität des Speicher-Solls
  (``Control.set_dhw``: nur diese Entität, ganzzahlig, Minimum…Maximum – alles andere hart abgewiesen). Welche
  Entität taugt, prüft ``dhw_entity`` (number mit °C, kein Heizkreis, keine andere Rolle).

Die Regel (Planungspapier, Weg W) steht in den reinen Funktionen ``wish`` und ``decide`` (``dhw_rule``) – die Vorschau
nutzt sie auch:

1. Wunsch = Warmwasser-Empfehlung (dieselbe wie in Übersicht und Vorschau), auf 1 K gerundet, Minimum…Maximum.
2. Geschrieben wird nur bei Änderung: |Wunsch − Kessel-Soll| ≥ 1 K, kein Wiederholen gleicher Werte. Die Schwelle
   gilt auch für die ungerundete Empfehlung – 52,4 ↔ 52,6 °C schreibt nicht abwechselnd 52 und 53.
3. 5 min Haltezeit nach jedem Schreiben, kein Raster: 15:03 geschrieben → frühestens 15:08 neu entschieden.
4. Läuft eine Sonderladung oder Desinfektion, wird nicht geschrieben, bis sie vorbei ist.
5. 60 s nach dem Schreiben prüfen, ob der Kessel den Wert zeigt (±0,5 K) – gemerkt und im nächsten passenden Takt
   geprüft (30-s-Takt der Live-Werte bzw. Messtakt), ohne zu warten. Kessel-Soll = die beschriebene Entität in HA;
   EMS-ESP direkt nur mit frischen Werten (höchstens 90 s alt). Widersprechen sich beide, ist das Soll unklar: nichts
   schreiben, nichts als fremd zählen. Ist es bei der Prüfung unbekannt oder unklar (EMS-ESP startet neu), wird bis
   5 min nach dem Schreiben weiter geprüft. Zeigt der Kessel einen anderen Wert: Fehlschlag – Protokoll, Hinweis,
   Meldung; nächster Versuch frühestens nach der Haltezeit; nach 3 Fehlschlägen in Folge pausiert Weg W bis „wieder
   einschalten“. Zeigt der Kessel den Wert später doch (auch in der Pause), ist der Fehlschlag erledigt: „wieder in
   Ordnung“.
6. Ändert sich das Kessel-Soll ohne HeatPilot (z. B. die alte HA-Automation), ist ein fremder Schreiber aktiv:
   Hinweis und Meldung; nach 2 fremden Änderungen in 1 h pausiert Weg W – nicht gegeneinander schreiben.
7. Keine Zählsperre (seit 0.39): bei 1 K Änderung wird nach jeder Haltezeit geschrieben, egal wie oft am Tag.
8. Fällt HeatPilot aus, bleibt das letzte Soll stehen – deshalb nie unter dem Minimum. Beim Zurückschalten auf
   aus/trocken wird nichts zurückgeschrieben (Hinweis: die HA-Automation ggf. wieder einschalten).

Ablauf: ``plan`` im Takt (``Engine.tick`` nach ``dhw_step``) prüft, erkennt Fremdes und merkt einen Schreibvorgang
vor – nur, wenn nichts dagegen spricht (``blocker``: dieselben Hindernisse wie in „Bereit für die Steuerung?“);
``apply`` (main, nach dem Takt) schreibt; ``verify`` prüft. Der Zustand überlebt Neustarts (kv
``dhw_control.state``), Meldungen gehen über ``notify.Notifier``, die Bereitschaft zeigt ``readiness.dhw``. Die
Anzeige (Statuszeile, Hinweise, ``as_dict``) steht in ``dhw_status`` – Uhrzeiten gehen als ISO-Felder hinaus, der
Browser setzt sie in seiner Zeitzone ein.
"""

from __future__ import annotations

import logging
import time

from . import plausibility
from .dhw_entity import entity_problem, suggest_entity, target_entity, write_blocked  # noqa: F401 – auch für Aufrufer
from .dhw_rule import (CHECK_MAX_S, CHECK_S, CHECK_TOL_K, COUNT_WINDOW_S, EMS_FRESH_S, FOREIGN_MAX,  # noqa: F401
                       FOREIGN_WINDOW_S, HINT_AUTOMATION, HINT_FAILSAFE, HINT_S, HOLD_S, MAX_FAILS, STEP_K, UNKNOWN,
                       Decision, _c, decide, limits, unrounded, wish)
from .dhw_status import (ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME, ACT_WRITE, ACTIONS,  # noqa: F401
                         LOG_NAME, LOG_ROOM, DhwStatus)
from .discovery_roles import BOILER_ROLES
from .values import flag, num

log = logging.getLogger(__name__)

KV = "dhw_control.state"
# Schlüssel der Meldungen (notify.Notifier)
K_FAIL, K_PAUSED, K_FOREIGN = "dhw_write_failed", "dhw_paused", "dhw_foreign"


def _plausible(value) -> float | None:
    """Kessel-Soll als Zahl, nur plausibel (ein Platzhalter wie 3276,7 zählt nicht)."""
    return plausibility.in_range("dhw_set", num(value))


# ------------------------------------------------------------------ Ablauf
class DhwControl(DhwStatus):
    """Zustand von Weg W (letztes Schreiben, Prüfung, Fehlschläge, Pause, fremde Änderungen) und der Takt dazu."""

    def __init__(self, eng, control=None, notifier=None, entity: tuple = (None, None),
                 carry: DhwControl | None = None) -> None:
        """``entity``: (Entität, Herkunft) aus ``target_entity`` – dieselbe bekommt ``Control``. ``carry``: Instanz
        vor einem Neuaufbau, ihr Zustand geht über (dasselbe Objekt, auch für ein noch laufendes ``apply``)."""
        self.eng, self.control, self.notifier = eng, control, notifier
        self.store = eng.store
        self.entity_id, self.entity_source = entity
        self.state: dict = carry.state if carry is not None else dict(self.store.get(KV) or {})
        self.pending: tuple[int, str] | None = None
        self.planned_ts: int | None = None
        self.view: dict = {}
        self.stopped = False

    def stop(self) -> None:
        """Außer Dienst (main.build): ein noch vorgemerkter Schreibvorgang wird verworfen."""
        self.stopped = True

    @property
    def mode(self) -> str:
        return self.eng.cfg.dhw_control

    def paused(self) -> bool:
        return bool(self.state.get("paused"))

    # -------------------------------------------------------------- Kessel-Soll
    def readings(self) -> dict:
        """Kessel-Soll aus beiden Quellen: Zustand der beschriebenen Entität in HA (``ha``) und EMS-ESP direkt
        (``ems`` – nur frisch, höchstens ``EMS_FRESH_S`` alt: antwortet EMS-ESP nicht mehr, bliebe sein letzter Wert
        sonst für immer stehen). ``unclear``: beide bekannt, aber mehr als 0,5 K verschieden."""
        ha = _plausible((self.eng.states.get(self.entity_id) or {}).get("state")) if self.entity_id else None
        ts = getattr(self.eng, "ems_ts", None)
        boiler = (self.eng.ems.get("boiler") or {}) if ts is not None and time.time() - ts <= EMS_FRESH_S else {}
        ems = next((v for v in (_plausible(boiler.get(k)) for k in BOILER_ROLES["dhw_seltemp"]) if v is not None), None)
        return {"ha": ha, "ems": ems, "unclear": ha is not None and ems is not None and abs(ha - ems) > CHECK_TOL_K}

    def current(self, r: dict | None = None) -> float | None:
        """Kessel-Soll jetzt: die beschriebene Entität in HA, sonst EMS-ESP (frisch), sonst die erkannte Rolle in HA.
        Widersprechen sich Entität und EMS-ESP, ist es unklar: None – nichts schreiben, nichts als fremd zählen."""
        r = r or self.readings()
        if r["unclear"]:
            return None
        if r["ha"] is not None or r["ems"] is not None:
            return r["ha"] if r["ha"] is not None else r["ems"]
        role = (getattr(self.eng.found, "boiler", None) or {}).get("dhw_seltemp")
        return _plausible(self.eng._val(role)) if role and role != self.entity_id else None

    def _shown(self, seen: float | None, r: dict) -> str:
        """Was der Kessel zeigt – für Meldungen und Hinweise."""
        if seen is not None:
            return f"Kessel zeigt {_c(seen)} °C"
        if r["unclear"]:
            return f"Kessel-Soll unklar: Home Assistant zeigt {_c(r['ha'])} °C, EMS-ESP {_c(r['ems'])} °C"
        return "Kessel-Soll unbekannt"

    def special(self, sample: dict) -> bool:
        """Sonderladung oder Desinfektion läuft: Kesselschalter im Messpunkt oder in HA (falls EMS-ESP nicht mehr
        antwortet und der Messpunkt dessen letzten Stand zeigt) oder vom Modell erkannt (samt Nachlauf)."""
        roles = getattr(self.eng.found, "boiler", None) or {}
        in_ha = any(flag(self.eng._val(roles.get(r))) == 1 for r in ("dhw_disinfecting", "dhw_onetime"))
        return bool(sample.get("dhw_special") or sample.get("dhw_disinfecting") or in_ha
                    or getattr(self.eng.dhw, "special_running", False))

    def blocker(self) -> str | None:
        """Warum jetzt nicht geschrieben werden darf – dieselben Hindernisse wie in „Bereit für die Steuerung?“:
        Entität fehlt, taugt nicht (``dhw_entity``) oder ist nicht verfügbar; EMS-ESP lässt das Warmwasser-Soll nicht
        schreiben. None = darf. Zählt nie als Fehlschlag."""
        state = self.eng.states.get(self.entity_id) if self.entity_id else None
        return entity_problem(self.entity_id, state, self.eng.found) or write_blocked(self.eng)

    # -------------------------------------------------------------- Takt
    def plan(self, ts: int, sample: dict, now: float | None = None) -> None:
        """Im Takt: fällige Prüfung, fremde Änderungen, erledigte Fehlschläge, Entscheidung. Schreibt selbst nichts –
        bei „heatpilot“ wird der Schreibvorgang für ``apply`` vorgemerkt, im Trockenlauf nur protokolliert."""
        mode, st = self.mode, self.state
        self.pending, self.planned_ts = None, ts
        self._switch(mode, ts)
        self.verify(time.time() if now is None else now)
        r = self.readings()
        current = self.current(r)
        rec = self.eng.dhw_rec or {}
        cfg = self.eng.cfg
        value, why = wish(rec.get("target"), cfg), rec.get("reason") or "keine Empfehlung"
        special = self.special(sample)
        if mode == "heatpilot":
            self._watch(ts, current, special)
            self._resolve(ts, current, value)
        ref, last_ts = self._reference(mode, current)
        d = decide(value, ref, ts, last_ts, special, paused=mode == "heatpilot" and self.paused(),
                   checking=bool(st.get("check")), exact=unrounded(rec.get("target"), cfg))
        if d.why == UNKNOWN and r["unclear"]:
            d = Decision(False, value, f"{self._shown(None, r)} – nichts geschrieben")
        if d.write and mode == "heatpilot":
            d = self._guard(ts, d)
        if d.write and mode == "trocken":
            st["dry"] = {"ts": ts, "value": d.value, "from": ref}
            self._log(ts, ACT_DRY, d.value, f"Trockenlauf: würde {d.value} °C schreiben ({why})")
        elif d.write and mode == "heatpilot":
            self.pending = (d.value, why)
        self.view = {"ts": ts, "wish": value, "reason": why, "current": current, "special": special,
                     "decision": d.why, "until": d.until, "write": d.write and mode != "aus"}
        self._settle(ts)
        self._save()

    def _guard(self, ts: int, d: Decision) -> Decision:
        """Vor dem Vormerken (nur „heatpilot“): Spricht ein Hindernis dagegen (``blocker``), wird nichts vorgemerkt –
        kein Fehlschlag. Eine Zählsperre gibt es nicht (Ingo): Ändert sich die Empfehlung um 1 K, wird nach der
        Haltezeit geschrieben – sonst bliebe der Speicher z. B. auf 60 °C stehen, nur weil viel los war."""
        problem = self.blocker()
        if problem:
            return Decision(False, d.value, problem)
        return d

    def _writes(self, ts: int) -> list[int]:
        """Schreibvorgänge der letzten 24 h seit dem letzten „wieder einschalten“ – nur für die Anzeige."""
        since = max(ts - COUNT_WINDOW_S, int(self.state.get("resumed", 0)))
        return [t for t in self.state.get("writes") or [] if t > since]

    def _reference(self, mode: str, current: float | None) -> tuple[float | None, int | None]:
        """Womit verglichen wird: bei „heatpilot“ das Kessel-Soll und das letzte Schreiben (Haltezeit); im Trockenlauf
        der zuletzt „geschriebene“ Wert – sonst schriebe er jeden Takt dasselbe ins Protokoll."""
        if mode == "trocken":
            dry = self.state.get("dry")
            return (dry["value"], dry["ts"]) if dry else (current, None)
        return current, (self.state.get("last") or {}).get("ts")

    def reference(self) -> tuple[float | None, int | None] | None:
        """Für die Vorschau (dieselbe Regel): (Startwert, letzter Schreibzeitpunkt) bei trocken/heatpilot, sonst None."""
        mode = self.mode
        return None if mode == "aus" else self._reference(mode, self.current())

    def _switch(self, mode: str, ts: int) -> None:
        """Schalter umgestellt: weg von „heatpilot“ nichts zurückschreiben (Hinweis), Trockenlauf neu beginnen,
        bei „heatpilot“ fremde Änderungen ab jetzt zählen."""
        st = self.state
        prev = st.get("mode")
        if prev == mode:
            return
        if prev == "heatpilot":
            st.pop("check", None)
            st["stopped"] = {"ts": ts, "value": (st.get("last") or {}).get("value")}
        if mode == "trocken":
            st.pop("dry", None)
        if mode == "heatpilot":  # Fehlschläge neu zählen; eine Pause hebt nur „wieder einschalten“ auf
            for key in ("seen", "stopped", "fail_settled"):
                st.pop(key, None)
            st["fails"] = 0
        st["mode"] = mode
        if prev is not None:
            log.info("Warmwasser-Soll: Schalter %s → %s", prev, mode)

    async def apply(self, now: float | None = None) -> bool:
        """Vorgemerktes Warmwasser-Soll schreiben – nur über ``Control.set_dhw``, nur bei „heatpilot“, nicht pausiert,
        ohne Hindernis, nicht nach einem Neuaufbau. Merkt die Prüfung nach 60 s vor. True, wenn geschrieben wurde."""
        pending, self.pending = self.pending, None
        if pending is None:
            return False
        if self.stopped or self.mode != "heatpilot" or self.paused() or self.control is None:
            log.info("Warmwasser-Soll: Schreibvorgang verworfen – Schalter, Pause oder Neuaufbau")
            return False
        problem = self.blocker()
        if problem:
            log.info("Warmwasser-Soll: Schreibvorgang verworfen – %s", problem)
            return False
        value, why = pending
        ts = self.planned_ts or int(time.time())
        before = self.current()
        st = self.state
        st["last"] = {"ts": ts, "value": value, "from": before, "reason": why}
        st["writes"] = self._writes(ts) + [ts]  # auch ein Versuch, auf den HA mit Fehler antwortet
        try:
            await self.control.set_dhw(value, f"{why} (vorher {_c(before)} °C)")
        except Exception as err:  # noqa: BLE001 – zählt als Fehlschlag, nächster Versuch nach der Haltezeit
            self._failed(ts, value, f"nicht geschrieben ({err})")
            self._save()
            return False
        done = time.time() if now is None else now
        st["check"] = {"ts": ts, "value": value, "due": done + CHECK_S, "until": done + CHECK_MAX_S}
        log.info("Warmwasser-Soll → %d °C (%s), Prüfung in %d s", value, why, CHECK_S)
        self._save()
        return True

    def verify(self, now: float | None = None) -> bool:
        """Prüfung 60 s nach dem Schreiben: Zeigt der Kessel den Wert (±0,5 K)? Blockiert nichts – fällig im nächsten
        passenden Takt. Ist das Kessel-Soll unbekannt oder unklar, wird bis ``CHECK_MAX_S`` nach dem Schreiben weiter
        geprüft, erst dann zählt es als Fehlschlag. True, wenn geprüft wurde (dann ggf. Meldungen senden)."""
        st, now = self.state, time.time() if now is None else now
        chk = st.get("check")
        if not chk or now < chk["due"]:
            return False
        if self.mode != "heatpilot":
            st.pop("check")
            self._save()
            return False
        r = self.readings()
        seen = self.current(r)
        if seen is None and now < chk.get("until", chk["due"] - CHECK_S + CHECK_MAX_S):
            return False
        st.pop("check")
        ok = seen is not None and abs(seen - chk["value"]) <= CHECK_TOL_K
        shown = self._shown(seen, r)
        st["last_check"] = {"ts": int(now), "value": chk["value"], "seen": seen, "ok": ok, "shown": shown}
        st["seen"] = seen
        if ok:
            self._accepted(int(now), chk["value"])
        else:
            self._failed(int(now), chk["value"], f"nicht übernommen ({shown})")
        self._save()
        return True

    def _accepted(self, ts: int, value: int, late: bool = False) -> None:
        self.state["fails"] = 0
        self.state.pop("fail_settled", None)
        log.info("Warmwasser-Soll %d °C %sübernommen", value, "verspätet " if late else "")
        if self.notifier is not None:
            self.notifier.solved(K_FAIL, f"Warmwasser-Soll wieder in Ordnung – {value} °C übernommen", ts)

    def _failed(self, ts: int, value: int, what: str) -> None:
        """Fehlschlag: Protokoll, Hinweis, Meldung (einmal je Ereignis); ab ``MAX_FAILS`` in Folge pausieren."""
        st = self.state
        st["fails"] = fails = int(st.get("fails", 0)) + 1
        st.pop("fail_settled", None)
        text = f"Warmwasser-Soll {value} °C {what}"
        self._log(ts, ACT_FAIL, value, f"{text} – Fehlschlag {fails} von {MAX_FAILS}")
        log.warning("%s – Fehlschlag %d von %d", text, fails, MAX_FAILS)
        if fails < MAX_FAILS:
            self._notify(K_FAIL, f"{text} – {fails + 1}. Versuch in {HOLD_S // 60} min", ts)
            return
        self._notify(K_FAIL, text, ts)
        self._pause(ts, f"{MAX_FAILS} Fehlschläge in Folge",
                    f"{text} – {MAX_FAILS}. Fehlschlag in Folge, Warmwasser über HeatPilot pausiert. Bitte prüfen "
                    "und in HeatPilot wieder einschalten")

    def _resolve(self, ts: int, current: float | None, value: int | None) -> None:
        """Fehlschlag erledigt, ohne neu zu schreiben – auch während einer Pause. Zeigt der Kessel den zuletzt
        geschriebenen Wert (spät übernommen, nach einem Neustart von EMS-ESP), ist es ein Erfolg. Zeigt er schon den
        Wunsch, braucht es keinen neuen Versuch: Problem und Problem-Sensor gehen aus, die Zahl der Fehlschläge bleibt
        (scheitert der nächste Versuch, zählt er weiter bis zur Pause)."""
        st, n = self.state, self.notifier
        fails = int(st.get("fails", 0))
        if st.get("check") or current is None or not (fails or (n is not None and K_FAIL in n.open_keys())):
            return
        last = (st.get("last") or {}).get("value")
        if last is not None and abs(current - last) <= CHECK_TOL_K:
            self._accepted(ts, last, late=True)
        elif value is not None and abs(current - value) < STEP_K - 1e-9 and not st.get("fail_settled"):
            st["fail_settled"] = ts
            if n is not None:
                n.solved(K_FAIL, f"Warmwasser-Soll: kein neuer Versuch nötig – Kessel zeigt {_c(current)} °C, passt "
                                 "zur Empfehlung", ts)

    def _watch(self, ts: int, current: float | None, special: bool) -> None:
        """Fremden Schreiber erkennen: Kessel-Soll anders als zuletzt gesehen, ohne dass HeatPilot schrieb. Zurück auf
        den eigenen Wert ist nicht fremd (spät übernommen – das erledigt ``_resolve``)."""
        st = self.state
        seen = st.get("seen")
        if current is not None:
            st["seen"] = current
        if current is None or seen is None or st.get("check") or special or self.paused():
            return
        if abs(current - seen) < CHECK_TOL_K:
            return
        last = st.get("last")
        if last and abs(current - last["value"]) <= CHECK_TOL_K:
            return
        st["foreign"] = [t for t in st.get("foreign") or [] if t > ts - HINT_S] + [ts]
        st["foreign_last"] = {"ts": ts, "from": seen, "to": current}
        self._log(ts, ACT_FOREIGN, current, f"Kessel-Soll von außen geändert: {_c(seen)} → {_c(current)} °C – "
                                            "anderer Schreiber aktiv")
        self._notify(K_FOREIGN, f"Warmwasser-Soll von außen geändert ({_c(seen)} → {_c(current)} °C) – anderer "
                                "Schreiber aktiv, HA-Automation abschalten", ts)
        recent = [t for t in st["foreign"] if t > ts - FOREIGN_WINDOW_S and t > st.get("resumed", 0)]
        if len(recent) >= FOREIGN_MAX:
            self._pause(ts, f"{FOREIGN_MAX} fremde Änderungen in 1 h",
                        f"Warmwasser-Soll {len(recent)}× in 1 h von außen geändert – Warmwasser über HeatPilot "
                        "pausiert, damit nicht gegeneinander geschrieben wird. HA-Automation abschalten, dann in "
                        "HeatPilot wieder einschalten")

    def _pause(self, ts: int, reason: str, text: str) -> None:
        if self.state.get("paused"):
            return
        self.state["paused"] = {"ts": ts, "reason": reason}
        self.pending = None
        self._log(ts, ACT_PAUSE, None, f"Warmwasser über HeatPilot pausiert: {reason}")
        self._notify(K_PAUSED, text, ts)

    def resume(self, ts: int | None = None) -> bool:
        """„Wieder einschalten“ – nur bei bestehender Pause: Pause aufheben, Fehlschläge zurück, fremde Änderungen und
        Schreibvorgänge ab jetzt neu zählen. Ohne Pause ändert sich nichts (False) – sonst hebelte ein wiederholter
        Aufruf die Pause nach 3 Fehlschlägen aus. Eine offene Schreibfehler-Meldung bleibt offen – der erste Erfolg
        meldet „wieder in Ordnung“. True, wenn pausiert war."""
        st = self.state
        was = st.get("paused")
        if not was:
            return False
        ts = int(time.time()) if ts is None else int(ts)
        st.pop("paused")
        st["fails"], st["resumed"] = 0, ts
        for key in ("seen", "fail_settled"):  # Kessel-Soll ab jetzt neuer Bezug
            st.pop(key, None)
        self._log(ts, ACT_RESUME, None, f"Warmwasser über HeatPilot wieder eingeschaltet (pausiert wegen: "
                                        f"{was['reason']})")
        if self.notifier is not None:
            for key in (K_PAUSED, K_FOREIGN):
                self.notifier.solved(key, ts=ts, quiet=True)
        self._save()
        return True

    def _settle(self, ts: int) -> None:
        """Meldungen abgleichen: ohne „heatpilot“ alle still schließen, Pause aufgehoben → still, seit 1 h keine
        fremde Änderung → „wieder in Ordnung“."""
        n = self.notifier
        if n is None:
            return
        if self.mode != "heatpilot":
            n.settle("dhw_", {}, ts)
            return
        if not self.paused():
            n.solved(K_PAUSED, ts=ts, quiet=True)
        if not any(t > ts - FOREIGN_WINDOW_S for t in self.state.get("foreign") or []):
            n.solved(K_FOREIGN, "Warmwasser-Soll: seit 1 h keine fremden Änderungen mehr", ts)

    def _notify(self, key: str, text: str, ts: int) -> None:
        if self.notifier is not None:
            self.notifier.problem(key, text, ts)

    def _log(self, ts: int, action: str, value, reason: str) -> None:
        self.store.add_control_log(int(ts), self.entity_id or LOG_ROOM, action, value, reason)

    def _save(self) -> None:
        self.store.put(KV, self.state)
