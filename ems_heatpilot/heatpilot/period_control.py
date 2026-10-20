"""Antipendelzeit führen (seit 0.48): Zustand und Ablauf – Grundwert, Schreiben, Prüfung, fremde Änderung, Zurück.

Läuft im 30-s-Schritt von Weg B mit (``BoilerControl.plan``/``apply``), mit dessen Ziel (``boiler_flow.Target``) und
dessen Sperre; die Regel steht in ``boiler_period``. Aktiv nur mit „Antipendelzeit anpassen“ (``boiler_period``):
bei „Kessel über HeatPilot“ = „heatpilot“ wird geschrieben, bei „trocken“ (Beobachten) nur protokolliert, was HeatPilot
schreiben würde (dieselben Regeln, Bezug ist der zuletzt „geschriebene“ Wert).

* **Grundwert** – beim Einschalten (Schalter oder Betriebsart neu) der Wert am Kessel, mindestens 25 min
  (``boiler_period.base_of``); solange er unbekannt ist, geschieht nichts.
* **Schreiben** über ``Control.set_period`` – nur ohne Hindernis: dieselben wie beim Kessel (``BoilerControl.blocker``:
  Regler am Bus, „Heizen abschalten“, Entitäten …), Kessel pausiert, Entität der Antipendelzeit taugt nicht.
* **Prüfung** ``CHECK_S`` nach dem Schreiben (unbekannt: bis ``CHECK_MAX_S``): übernommen bzw. nicht übernommen
  (Protokoll, Meldung); nach ``MAX_FAILS`` Fehlschlägen in Folge pausiert die Funktion (nur sie).
* **Fremde Änderung** – anders als zuletzt gesehen, ohne dass HeatPilot schrieb (wie ``boiler_watch``): Protokoll,
  Meldung, der neue Wert gilt als Grundwert (mindestens 25 min), die Haltezeit beginnt neu; ``FOREIGN_MAX`` in 1 h →
  Pause. Eine Pause endet, wenn „Antipendelzeit anpassen“ aus- und wieder eingeschaltet wird.
* **Zurück** (``back``, von ``main.App._boiler_back`` mit der alten Schreibstelle): wird die Funktion bzw. „Kessel über
  HeatPilot“ ausgeschaltet und hatte HeatPilot geschrieben, einmal den Grundwert zurück – nur ohne Hindernis.

Fällt HeatPilot aus, bleibt der zuletzt geschriebene Wert stehen (nie unter 5 min). Zustand in kv
``boiler_period.state`` (überlebt Neustarts und Neuaufbau).
"""

from __future__ import annotations

import json
import logging
import time

from . import boiler_period as bp
from .boiler_rule import CHECK_MAX_S, CHECK_S, EMS_FRESH_S, FOREIGN_MAX, FOREIGN_WINDOW_S, MAX_FAILS, _iso
from .boiler_status import _times
from .discovery_roles import BOILER_ROLES

log = logging.getLogger(__name__)

KV = "boiler_period.state"
K_FAIL, K_FOREIGN, K_PAUSED = "boiler_period_failed", "boiler_period_foreign", "boiler_period_paused"
RESET = ("check", "dry", "seen", "base", "orig", "fails", "paused", "foreign", "rejected", "hold")


class PeriodControl:
    """Antipendelzeit neben Weg B: ``owner`` ist die ``BoilerControl`` (Schreibstelle, Meldungen, Sperre)."""

    def __init__(self, owner, entity: tuple | None = None, carry: PeriodControl | None = None) -> None:
        self.owner, self.eng, self.store = owner, owner.eng, owner.store
        self.entity, self.source = entity or (None, None)
        self.state: dict = carry.state if carry is not None else dict(self.store.get(KV) or {})
        self.pending: tuple[int, str] | None = None
        self.view: dict = {}
        self._saved: str | None = None

    def mode(self, cfg=None) -> str:
        """„heatpilot“ (schreibt), „trocken“ (protokolliert) oder „aus“ – nur mit „Antipendelzeit anpassen“."""
        cfg = cfg or self.eng.cfg
        return cfg.boiler_control if getattr(cfg, "boiler_period", False) is True else "aus"

    def current(self) -> int | None:
        """Wert am Kessel: Entität in HA, sonst EMS-ESP (frisch); widersprechen sie sich, None (nichts schreiben)."""
        ha = bp.minutes((self.eng.states.get(self.entity) or {}).get("state")) if self.entity else None
        ts = getattr(self.eng, "ems_ts", None)
        boiler = (self.eng.ems.get("boiler") or {}) if ts is not None and time.time() - ts <= EMS_FRESH_S else {}
        ems = next((v for v in (bp.minutes(boiler.get(k)) for k in BOILER_ROLES[bp.ROLE]) if v is not None), None)
        if ha is not None and ems is not None and ha != ems:
            return None
        return ha if ha is not None else ems

    def blocker(self, now: float | None = None) -> str | None:
        """Hindernis fürs Schreiben – dieselben wie beim Kessel, dazu Pause des Kessels und die eigene Entität."""
        o = self.owner
        why = o.blocker(now) or ("Kessel über HeatPilot pausiert" if o.paused() else None)
        return why or bp.problem(self.entity, self.eng.states.get(self.entity or ""), self.eng.found)

    # -------------------------------------------------------------- Schritt
    def plan(self, ts: int, target, now: float | None = None) -> None:
        """Im Schritt von Weg B: Prüfung, Grundwert, fremde Änderung, Entscheidung; vormerken bzw. protokollieren."""
        st, mode = self.state, self.mode()
        self.pending = None
        self._switch(mode, ts)
        if mode == "aus":
            self.view = {"ts": ts}
            return self._finish(ts, mode)
        blocked = self.blocker(now) if mode == "heatpilot" else None
        if mode == "heatpilot":
            self.verify(ts)
        cur = self.current()
        if st.get("base") is None and cur is not None:
            st["base"], st["orig"] = bp.base_of(cur), cur
        if mode == "heatpilot":
            self._watch(ts, cur, blocked)
        value, reason = (bp.want(target, st["base"]) if st.get("base") is not None
                         else (None, "Grundwert noch unbekannt – Antipendelzeit am Kessel nicht lesbar"))
        ref, last_ts = self._reference(mode, cur)
        dec = bp.decide(value, ref, ts, last_ts, paused=bool(st.get("paused")), checking=bool(st.get("check")))
        if dec.write and mode == "trocken":
            st["dry"] = {"ts": ts, "value": value, "from": ref}
            self._log(ts, bp.ACT_DRY, value, f"Beobachten: würde Antipendelzeit {value} min schreiben ({reason})")
        elif dec.write and not blocked:
            self.pending = (value, reason)
        self.view = {"ts": ts, "value": value, "reason": reason, "current": cur, "blocked": blocked,
                     "decision": dec.why, "until": dec.until, "write": dec.write}
        self._finish(ts, mode)

    def _reference(self, mode: str, cur) -> tuple:
        """Womit verglichen wird: bei „heatpilot“ der Wert am Kessel und das letzte Schreiben (bzw. die neu begonnene
        Haltezeit nach einer fremden Änderung), im Trockenlauf der zuletzt „geschriebene“ Wert."""
        st = self.state
        if mode == "trocken":
            dry = st.get("dry")
            return (dry["value"], dry["ts"]) if dry else (cur, None)
        times = [t for t in ((st.get("last") or {}).get("ts"), st.get("hold")) if t is not None]
        return cur, max(times) if times else None

    def _switch(self, mode: str, ts: int) -> None:
        """Ein-, aus- oder umgeschaltet: Grundwert, Prüfung, Pause und Zählungen beginnen neu (das letzte Schreiben
        bleibt für die Haltezeit)."""
        st = self.state
        prev = st.get("mode")
        if prev == mode:
            return
        for key in RESET:
            st.pop(key, None)
        st["mode"], st["since"] = mode, ts
        if prev is not None:
            log.info("Antipendelzeit: %s → %s", prev, mode)

    def _finish(self, ts: int, mode: str) -> None:
        """Meldungen abgleichen und speichern: ohne „heatpilot“ still schließen, sonst Pause/fremd erledigt."""
        n = self.owner.notifier
        if n is not None and mode != "heatpilot":
            n.settle("boiler_period_", {}, ts)
        elif n is not None:
            if not self.state.get("paused"):
                n.solved(K_PAUSED, ts=ts, quiet=True)
            if not any(t > ts - FOREIGN_WINDOW_S for t in self.state.get("foreign") or []):
                n.solved(K_FOREIGN, "Kessel: Antipendelzeit seit 1 h nicht mehr von außen geändert", ts)
        self._save()

    # -------------------------------------------------------------- Prüfen, fremd, Pause
    def verify(self, ts: int) -> None:
        """Prüfung ``CHECK_S`` nach dem Schreiben: Zeigt der Kessel den Wert? Unbekannt: bis ``CHECK_MAX_S`` weiter."""
        st = self.state
        chk = st.get("check")
        if not chk or ts < chk["due"]:
            return
        seen = self.current()
        if seen is None and ts < chk["until"]:
            return
        st.pop("check")
        ok = seen == chk["value"]
        st["last_check"] = {"ts": ts, "value": chk["value"], "seen": seen, "ok": ok}
        if seen is not None:
            st["seen"] = seen
        if ok:
            st["fails"] = 0
            if self.owner.notifier is not None:
                self.owner.notifier.solved(K_FAIL, f"Kessel: Antipendelzeit {seen} min übernommen", ts)
        else:
            self._failed(ts, chk["value"], f"nicht übernommen (Kessel zeigt {'?' if seen is None else seen} min)")

    def _failed(self, ts: int, value: int, text: str) -> None:
        st = self.state
        st["fails"] = fails = int(st.get("fails", 0)) + 1
        msg = f"Antipendelzeit {value} min {text}"
        self._log(ts, bp.ACT_FAIL, value, f"{msg} – Fehlschlag {fails} von {MAX_FAILS}")
        log.warning("Antipendelzeit: %s – Fehlschlag %d von %d", msg, fails, MAX_FAILS)
        self._notify(K_FAIL, f"Kessel: {msg}", ts)
        if fails >= MAX_FAILS:
            self._pause(ts, f"{MAX_FAILS} Fehlschläge in Folge")

    def _watch(self, ts: int, cur: int | None, blocked: str | None) -> None:
        """Fremde Änderung: anders als zuletzt gesehen, ohne dass HeatPilot schrieb (zurück auf den eigenen Wert ist
        nicht fremd); gesperrt zählt nichts, der Bezug beginnt danach neu."""
        st = self.state
        seen = st.get("seen")
        if blocked:
            st.pop("seen", None)
            return
        if cur is not None:
            st["seen"] = cur
        if cur is None or seen is None or cur == seen or st.get("check") or st.get("paused"):
            return
        if cur == (st.get("last") or {}).get("value"):
            return
        st["foreign"] = [t for t in st.get("foreign") or [] if t > ts - FOREIGN_WINDOW_S] + [ts]
        st["foreign_last"] = {"ts": ts, "from": seen, "to": cur}
        st["base"], st["hold"] = bp.base_of(cur), ts
        text = f"Antipendelzeit von außen geändert: {seen} → {cur} min"
        self._log(ts, bp.ACT_FOREIGN, cur, f"{text} – Grundwert jetzt {st['base']} min, Haltezeit beginnt neu")
        self._notify(K_FOREIGN, f"Kessel: {text} – eine Automation oder der Regler? HeatPilot nimmt {st['base']} min "
                                "als Grundwert", ts)
        if len(st["foreign"]) >= FOREIGN_MAX:
            self._pause(ts, f"{FOREIGN_MAX} fremde Änderungen in 1 h")

    def _pause(self, ts: int, reason: str) -> None:
        if self.state.get("paused"):
            return
        self.state["paused"], self.pending = {"ts": ts, "reason": reason}, None
        self._log(ts, bp.ACT_PAUSE, None, f"Antipendelzeit pausiert: {reason}")
        self._notify(K_PAUSED, f"Kessel: Antipendelzeit pausiert ({reason}) – bitte prüfen, dann „Antipendelzeit "
                               "anpassen“ aus- und wieder einschalten", ts)

    # -------------------------------------------------------------- Schreiben
    async def apply(self, now: float | None = None) -> int:
        """Vorgemerktes schreiben – nur „heatpilot“, nicht pausiert, ohne Hindernis, nicht nach einem Neuaufbau.
        Merkt die Prüfung vor. Gibt die Zahl der Schreibvorgänge zurück (0 oder 1)."""
        pending, self.pending = self.pending, None
        o, st = self.owner, self.state
        if not pending or o.stopped or self.mode() != "heatpilot" or st.get("paused") or o.control is None:
            return 0
        if problem := self.blocker(now):
            log.info("Antipendelzeit: Schreibvorgang verworfen – %s", problem)
            return 0
        (value, reason), ts, before = pending, o.planned_ts or int(time.time()), self.current()
        last = {"ts": ts, "value": value, "from": before, "reason": reason}
        try:
            await o.control.set_period(value, f"{reason} (vorher {before} min)")
        except PermissionError as err:  # Schutzregel in Control: kein Fehlschlag, nur Hinweis
            st["rejected"] = {"ts": ts, "value": value, "text": str(err)}
            log.warning("Antipendelzeit %s min abgewiesen – %s", value, err)
            return 0
        except Exception as err:  # noqa: BLE001 – zählt als Fehlschlag, nächster Versuch nach der Haltezeit
            st["last"] = last
            self._failed(ts, value, f"nicht geschrieben ({err})")
            self._save()
            return 0
        st["last"] = last
        st.pop("rejected", None)
        done = time.time() if now is None else now
        st["check"] = {"ts": ts, "value": value, "due": done + CHECK_S, "until": done + CHECK_MAX_S}
        log.info("Antipendelzeit → %s min (%s), Prüfung in %d s", value, reason, CHECK_S)
        self._save()
        return 1

    async def back(self, cfg, now: float | None = None) -> dict | None:
        """Nicht mehr geführt (Schalter aus bzw. „Kessel über HeatPilot“ nicht mehr „heatpilot“, ``cfg`` = neue
        Einstellungen): einmal den Grundwert zurück, wenn HeatPilot seit dem Einschalten geschrieben hat. Nie Fehler –
        der Neuaufbau geht immer weiter."""
        try:
            return await self._back(cfg, now)
        except Exception:
            log.exception("Antipendelzeit: Grundwert beim Ausschalten nicht zurückgeschrieben")
            return None

    async def _back(self, cfg, now: float | None) -> dict | None:
        st = self.state
        last, base = st.get("last") or {}, st.get("base")
        if self.mode() != "heatpilot" or self.mode(cfg) == "heatpilot" or base is None \
                or last.get("ts", -1) < st.get("since", 0):
            return None
        ts, cur = int(time.time() if now is None else now), self.current()
        res: dict = {"ts": ts, "value": base, "ok": False}
        problem = self.owner.blocker(now) or bp.problem(self.entity, self.eng.states.get(self.entity or ""),
                                                        self.eng.found)  # Pause des Kessels hält das nicht auf
        if cur == base:
            res.update(ok=True, text=f"Kessel hat schon den Grundwert {base} min")
        elif self.owner.control is None or problem:
            res["text"] = f"nichts zurückgeschrieben – {problem or 'keine Verbindung'}"
        else:
            try:
                await self.owner.control.set_period(base, "Antipendelzeit nicht mehr geführt: Grundwert zurück")
                res.update(ok=True, text=f"Grundwert {base} min zurückgeschrieben")
            except Exception as err:  # noqa: BLE001 – nur melden, der Neuaufbau geht weiter
                res["text"] = f"Grundwert nicht zurückgeschrieben ({err})"
        st["back"], self.pending = res, None
        self._log(ts, bp.ACT_BACK, base, f"Antipendelzeit nicht mehr geführt: {res['text']}")
        self._save()
        return res

    # -------------------------------------------------------------- Anzeige
    def line(self) -> str | None:
        """Zeile für Status und Übersicht – None, solange die Funktion aus ist."""
        mode, st, v = self.mode(), self.state, self.view
        if mode == "aus":
            return None
        if mode == "heatpilot" and st.get("paused"):
            return f"Antipendelzeit: pausiert – {st['paused']['reason']}"
        if mode == "heatpilot" and v.get("blocked"):
            return f"Antipendelzeit: gesperrt – {v['blocked']}"
        cur, value, reason = v.get("current"), v.get("value"), v.get("reason") or "noch nicht gerechnet"
        if value is None:
            return f"Antipendelzeit: bleibt {'?' if cur is None else cur} min ({reason})"
        lead, tail = ("würde " if mode == "trocken" else ""), ""
        if mode == "heatpilot" and cur is not None and cur != value:
            tail = f" · Kessel hat noch {cur} min – " + ("wird jetzt geschrieben" if v.get("write") else v["decision"])
        return f"Antipendelzeit: {lead}{value} min ({reason}){tail}"

    def note(self) -> str:
        """Hinweis in der Statusbox des Kessels (früher fest: „bleibt, wie sie ist“)."""
        if self.mode() == "aus":
            return "Die Antipendelzeit des Kessels bleibt, wie sie ist"
        return (f"Antipendelzeit: {bp.SHORT_MIN} min, wenn die Abnahme reicht, sonst der Grundwert "
                f"(mindestens {bp.LONG_MIN} min)")

    def as_dict(self) -> dict:
        """Status für Einstellungen → Kessel und Übersicht (``period`` in ``api/boiler``)."""
        st, v, cfg = self.state, self.view, self.eng.cfg
        state = self.eng.states.get(self.entity or "")
        return {
            "on": getattr(cfg, "boiler_period", False) is True, "mode": self.mode(), "entity": self.entity,
            "source": self.source, "name": ((state or {}).get("attributes") or {}).get("friendly_name") or self.entity,
            "current": self.current(), "problem": bp.problem(self.entity, state, self.eng.found),
            "base": st.get("base"), "want": v.get("value"), "reason": v.get("reason"), "decision": v.get("decision"),
            "until": _iso(v.get("until")), "blocked": v.get("blocked"), "paused": st.get("paused"),
            "fails": int(st.get("fails", 0)), "last_write": _times(st.get("last")),
            "check_due": _iso((st.get("check") or {}).get("due")), "last_check": _times(st.get("last_check")),
            "foreign_last": _times(st.get("foreign_last")), "back": _times(st.get("back")),
            "rejected": _times(st.get("rejected")), "dry_last": _times(st.get("dry")), "status_text": self.line(),
            "rules": {"short_min": bp.SHORT_MIN, "long_min": bp.LONG_MIN, "lo_min": bp.LO_MIN, "hi_min": bp.HI_MIN,
                      "step_min": bp.STEP_MIN, "hold_min": bp.HOLD_S // 60, "check_s": CHECK_S},
        }

    # -------------------------------------------------------------- Helfer
    def _log(self, ts: int, action: str, value, reason: str) -> None:
        self.store.add_control_log(int(ts), self.entity or "boiler", action, value, reason)

    def _notify(self, key: str, text: str, ts: int) -> None:
        if self.owner.notifier is not None:
            self.owner.notifier.problem(key, text, ts)

    def _save(self) -> None:
        text = json.dumps(self.state, sort_keys=True, default=str)
        if text != self._saved:
            self.store.put(KV, self.state)
            self._saved = text
