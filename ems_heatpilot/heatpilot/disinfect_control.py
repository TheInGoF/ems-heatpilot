"""Desinfektion auslösen (seit 0.42): Ablauf zum Termin – einschalten, prüfen, beobachten, ausschalten.

Wann HeatPilot schalten darf, steht in ``disinfect_rule`` (Termin, Warmwasser-Soll „HeatPilot setzt“, kein Regler am
Bus, keine Pause von Weg W; Schalter tauglich – ``disinfect_entity``). Geschrieben wird nur über
``Control.set_disinfect`` (``switch.turn_on``/``turn_off`` auf die eine Entität); EMS-ESP bleibt nur lesend.

Je Schritt (30-s-Takt von ``main.disinfect_step``: ``plan``, danach ``apply``):

1. **Fällig** (``disinfect_rule.due``): Termin begonnen, höchstens 12 h her, noch nicht erledigt. Macht ein Regler am
   Bus die Desinfektion (frisch gemeldet), ist der Termin damit erledigt (nichts geschaltet). Im Trockenlauf steht
   „würde Desinfektion starten“ im Protokoll. Bei „HeatPilot setzt“ wird eingeschaltet – nur ohne Hindernis; sonst
   wartet der Termin (Grund in der Anzeige) bis 12 h nach dem Beginn. Ein Termin gilt mit dem Einschalten als erledigt,
   auch wenn es scheitert: höchstens einmal ausgelöst, auch nach einem Neustart (Zustand in kv
   ``disinfect_control.state``). Lief binnen 24 h schon eine (Termin verlegt), wird er übersprungen (``too_soon``).
2. **Verpasst** (``disinfect_rule.missed``): Lief HeatPilot länger als 12 h nach dem Termin nicht bzw. war es so lange
   gesperrt: Meldung „Desinfektion … verpasst“, dann gilt der nächste Termin.
3. **Prüfen** 60 s nach dem Einschalten: Schalter an bzw. EMS-ESP meldet Desinfektion oder Sonderladung → läuft. Fällt
   der Schalter zurück: Fehlschlag. Unbekannt: bis 5 min weiter prüfen. Lief der Aufruf in einen Zeitüberlauf (Ausgang
   unklar), entscheidet ebenfalls diese Prüfung.
4. **Beobachten** (erst nach der Bestätigung durch den Kessel): Erreicht der Speicher die Desinfektionstemperatur − 3 K,
   ist sie gelungen; sonst spätestens nach Ladedauer laut Modell + 60 min ein Fehlschlag mit Meldung (``K_FAIL``,
   Problem-Sensor). Lag der Speicher schon beim Einschalten darüber, zählt erst die volle Desinfektionstemperatur oder
   dass der Kessel den Schalter selbst zurücksetzt. Der nächste Erfolg meldet „wieder in Ordnung“.
5. **Ausschalten**: Steht der Schalter danach noch an, schaltet HeatPilot ihn einmal aus und prüft nach 60 s; bleibt er
   an, kommt eine Meldung. Nach einem Erfolg wartet HeatPilot damit 15 min (Nachlauf, meist setzt der Kessel den
   Schalter mit der vollen Temperatur selbst zurück; war der Speicher schon beim Einschalten heiß, bis zur Frist des
   Ablaufs), nach einem Fehlschlag nicht. Sperrt etwas nur kurz (Regler unbekannt, EMS-ESP antwortet nicht, Pause von
   Weg W, Schalter unbekannt, Fehler beim Aufruf), versucht HeatPilot es weiter – erst nach 30 min kommt die Meldung
   „nicht ausgeschaltet“.

Solange ein Ablauf läuft (``active``), lassen Weg W (Speicher-Soll) und Weg B (Vorlauf-Soll) den Kessel in Ruhe –
``dhw_control.special_running`` fragt das. Wird der Schalter von Weg W umgestellt, bricht der Ablauf ab (der Kessel
beendet die Desinfektion selbst). Ein gescheiterter Termin zählt im Warmwasser-Modell nicht mehr (``triggered``). Die
Anzeige steht in ``disinfect_status``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from . import disinfect_rule as rule
from .config import WEEKDAYS
from .dhw_rule import EMS_FRESH_S, _c
from .discovery_roles import BOILER_ROLES
from .disinfect_status import (ACT_ABORT, ACT_DONE, ACT_DRY, ACT_FAIL, ACT_MISSED, ACT_RUN, ACT_SKIP,  # noqa: F401
                               ACT_SWITCH, ACTIONS, LOG_ROOM, DisinfectStatus)
from .values import flag, num

log = logging.getLogger(__name__)

KV = "disinfect_control.state"
# Schlüssel der Meldungen (notify.Notifier) – Anfang „disinfect_“ (``settle``)
K_FAIL, K_MISSED, K_SWITCH = "disinfect_failed", "disinfect_missed", "disinfect_switch"


def _when(dt: datetime) -> str:
    """„So 26.10. 17:30“ – für den nächsten Termin in einer Meldung."""
    return f"{WEEKDAYS[dt.weekday()]} {dt.day}.{dt.month}. {dt:%H:%M}"


def _err(err: Exception) -> str:
    """Fehlertext für Meldung und Protokoll – nie leer (ein Zeitüberlauf hat oft keinen Text)."""
    text = str(err).strip()
    if text:
        return text
    return "Zeitüberlauf" if isinstance(err, (asyncio.TimeoutError, TimeoutError)) else type(err).__name__



class DisinfectControl(DisinfectStatus):
    """Zustand des Auslösens (erledigte Termine, laufender Ablauf, letztes Ergebnis) und der Schritt dazu."""

    def __init__(self, eng, control=None, notifier=None, entity: tuple = (None, None),
                 carry: DisinfectControl | None = None) -> None:
        """``entity``: (Entität, Herkunft) aus ``disinfect_entity.target`` – dieselbe bekommt ``Control``. ``carry``:
        Instanz vor einem Neuaufbau, ihr Zustand geht über."""
        self.eng, self.control, self.notifier = eng, control, notifier
        self.store = eng.store
        self.entity_id, self.entity_source = entity
        self.state: dict = carry.state if carry is not None else dict(self.store.get(KV) or {})
        self.pending: tuple[str, str] | None = None
        self.planned_ts: int | None = None
        self.stopped = False
        self._saved: str | None = None

    def stop(self) -> None:
        """Außer Dienst (main.build): ein vorgemerkter Schaltvorgang wird verworfen."""
        self.stopped = True

    @property
    def mode(self) -> str:
        return rule.mode(self.eng.cfg)

    def active(self) -> bool:
        """Läuft ein Ablauf (eingeschaltet bis wieder aus)? Dann fassen Weg W und Weg B den Kessel nicht an."""
        return bool(self.state.get("run"))

    # -------------------------------------------------------------- Kessel lesen
    def switch_on(self) -> bool | None:
        """Schalter laut Home Assistant: an, aus oder unbekannt."""
        st = (self.eng.states.get(self.entity_id) or {}).get("state") if self.entity_id else None
        f = flag(st)
        return None if f is None else bool(f)

    def _fresh(self) -> dict:
        """Kesselwerte direkt von EMS-ESP – nur frisch (höchstens 90 s alt), sonst leer."""
        ts = getattr(self.eng, "ems_ts", None)
        return (self.eng.ems.get("boiler") or {}) if ts is not None and time.time() - ts <= EMS_FRESH_S else {}

    def boiler_says(self) -> bool | None:
        """EMS-ESP direkt (nur frisch): meldet der Kessel Desinfektion oder Sonderladung? None = unbekannt."""
        boiler = self._fresh()
        vals = [flag(boiler[k]) for r in ("dhw_disinfecting", "dhw_onetime") for k in BOILER_ROLES[r] if k in boiler]
        vals = [v for v in vals if v is not None]
        return None if not vals else any(vals)

    def tank(self) -> float | None:
        """Speichertemperatur jetzt: EMS-ESP direkt (frisch), sonst die Entität in Home Assistant."""
        boiler = self._fresh()
        raw = next((boiler[k] for k in BOILER_ROLES["dhw_temp"] if k in boiler), None)
        if raw is None:
            raw = self.eng._val((getattr(self.eng.found, "boiler", None) or {}).get("dhw_temp"))
        v = num(raw)
        return v if v is not None and 0.0 < v < 100.0 else None

    # -------------------------------------------------------------- Schritt
    def plan(self, now: float | None = None) -> None:
        """Im 30-s-Takt: Termin fällig oder verpasst, laufender Ablauf. Schaltet selbst nichts – ``apply`` tut es."""
        now = time.time() if now is None else now
        ts, st = int(now), self.state
        self.pending, self.planned_ts = None, ts
        self._arm(ts)
        self.eng.cfg.disinfect_triggered = self.triggered(ts)
        if st.get("run"):
            self._follow(ts, st["run"])
        elif self.mode != "aus":
            self._missed(ts)
            self._due(ts)
        else:
            st.pop("waiting", None)
        self._settle(ts)
        self._save()

    def triggered(self, ts: float) -> bool:
        """Zählt der Termin im Warmwasser-Modell (``cfg.disinfect_triggered``)? HeatPilot löst ihn aus und nichts sperrt
        – aber nicht, solange das Modell noch mit der Wärme eines gescheiterten Termins rechnen würde
        (``disinfect_rule.failed_recently``): Dann rechnet es bis dahin ohne sie."""
        if self.mode != "heatpilot" or not rule.triggers(self.eng, self.entity_id):
            return False
        last = self.state.get("last") or {}
        return last.get("ok") is not False or not rule.failed_recently(last, ts, self.eng.disinfect_ready_h())

    def _arm(self, ts: int) -> None:
        """Termin, Uhrzeit oder Schalter geändert (auch: erster Start mit 0.42): Termine zählen erst ab jetzt. Lag
        ``armed`` in der Zukunft (die Uhr ging beim Scharfschalten vor), gilt jetzt – sonst zählte bis dahin keiner."""
        key = rule.arm_key(self.eng.cfg)
        armed = self.state.get("armed")
        if not armed or armed.get("key") != key:
            self.state["armed"] = {"ts": ts, "key": key}
            self.state.pop("waiting", None)
        elif (armed.get("ts") or 0) > ts:
            log.warning("Desinfektion: Uhr ging beim Scharfschalten vor (%s) – Termine zählen ab jetzt", armed["ts"])
            armed["ts"] = ts

    def _done(self) -> list[str]:
        return list(self.state.get("done") or [])

    def _mark(self, due_key: str) -> None:
        self.state["done"] = (self._done() + [due_key])[-rule.KEEP:]

    def _due(self, ts: int) -> None:
        """Fälliger Termin: Regler macht es, Trockenlauf protokolliert, „HeatPilot setzt“ schaltet ein (vormerken)."""
        st = self.state
        t = rule.due(self.eng.cfg, ts, st["armed"]["ts"], self._done())
        if t is None:
            st.pop("waiting", None)
            return
        k, lab = rule.key(t), rule.label(t)
        if rule.regulator_does_it(self.eng):
            self._mark(k)
            st["last"] = {"due": k, "label": lab, "ts": ts, "end": ts, "ok": None, "result": "regler",
                          "text": "macht der Regler (Regler am Bus)"}
            return
        if self._skip(ts, t):
            return
        temp = rule.reach(self.eng)[0]
        if self.mode == "trocken":
            self._mark(k)
            st["last"] = {"due": k, "label": lab, "ts": ts, "end": ts, "ok": None, "result": "trocken",
                          "text": "Trockenlauf – nur im Protokoll"}
            self._log(ts, ACT_DRY, None, f"Trockenlauf: würde Desinfektion {lab} starten ({_c(temp)} °C, Schalter "
                                         f"{self.entity_id or 'fehlt'})")
            return
        why = rule.blocker(self.eng, self.entity_id)
        if why:
            w = st.get("waiting") if (st.get("waiting") or {}).get("due") == k else None
            st["waiting"] = {"due": k, "label": lab, "why": why, "since": (w or {}).get("since", ts)}
            return
        st.pop("waiting", None)
        self.pending = ("on", k)

    def _missed(self, ts: int) -> None:
        """Termin, der länger als 12 h her ist und nicht erledigt: verpasst – Meldung (nur „HeatPilot setzt“, ohne
        Regler am Bus), dann gilt der nächste."""
        st, cfg = self.state, self.eng.cfg
        t = rule.missed(cfg, ts, st["armed"]["ts"], self._done())
        if t is None:
            return
        k, lab = rule.key(t), rule.label(t)
        if self._skip(ts, t):
            return
        self._mark(k)
        wait = st.pop("waiting", None) if (st.get("waiting") or {}).get("due") == k else None
        if self.mode != "heatpilot" or rule.regulator_does_it(self.eng):
            return
        why = wait["why"] if wait else "HeatPilot lief zum Termin nicht"
        nxt = rule.next_after(cfg, ts)
        text = f"Desinfektion {lab} verpasst – {why}" + (f"; nächster Termin {_when(nxt)}" if nxt else "")
        st["last"] = {"due": k, "label": lab, "ts": ts, "end": ts, "ok": False, "result": "verpasst", "text": why}
        self._log(ts, ACT_MISSED, None, text)
        self._notify(K_MISSED, text, ts)

    def _skip(self, ts: int, t: datetime) -> bool:
        """Lief binnen 24 h schon eine Desinfektion (``disinfect_rule.too_soon`` – Termin während bzw. kurz nach dem
        Ablauf verlegt)? Dann ist dieser Termin erledigt, ohne zu schalten – mit einer Zeile im Protokoll."""
        st = self.state
        if self.mode != "heatpilot" or not rule.too_soon(t, st.get("on"), st.get("last")):
            return False
        k, lab = rule.key(t), rule.label(t)
        self._mark(k)
        if (st.get("waiting") or {}).get("due") == k:
            st.pop("waiting", None)
        before = rule.label(datetime.fromisoformat(st["on"]["due"])) if st["on"].get("due") else "eben"
        self._log(ts, ACT_SKIP, None, f"Termin {lab} übersprungen – die Desinfektion {before} lief vor weniger als "
                                      f"{rule.GAP_S // 3600} h")
        return True

    # -------------------------------------------------------------- laufender Ablauf
    def _follow(self, ts: int, run: dict) -> None:
        """Laufender Ablauf: prüfen, beobachten, Nachlauf, ausschalten. Schalter von Weg W umgestellt: abbrechen."""
        phase = run.get("phase")
        if self.mode != "heatpilot":
            if phase in ("check", "running"):
                self._finish(ts, run, None, "abgebrochen – Warmwasser-Soll nicht mehr „HeatPilot setzt“ bzw. kein "
                                            "Termin; der Kessel beendet die Desinfektion selbst", switch_off=False)
            else:
                self._close(ts, run, None, "nicht ausgeschaltet – Schalter umgestellt, der Kessel beendet sie selbst")
            return
        t = self.tank()
        if t is not None and phase in ("check", "running"):
            run["peak"] = max(run.get("peak") or t, t)
        if phase == "check":
            self._check_on(ts, run, t)
        elif phase == "running":
            self._watch(ts, run, t)
        elif phase == "after":
            self._after(ts, run)
        elif phase == "off":
            self._off(ts, run)  # Ausschalten noch offen (kurz gesperrt, Fehler im letzten Schritt, Neustart)
        elif phase == "off_check":
            self._check_off(ts, run)

    def _check_on(self, ts: int, run: dict, t: float | None) -> None:
        """60 s nach dem Einschalten: läuft sie? Nur die Bestätigung durch den Kessel zählt (Schalter an bzw. EMS-ESP
        meldet sie) – ein schon heißer Speicher allein ist keine Desinfektion. Unbekannt: bis 5 min weiter prüfen."""
        chk = run["check"]
        if ts < chk["due"]:
            return
        sw, ems = self.switch_on(), self.boiler_says()
        if sw or ems:
            run["phase"], run["confirmed"] = "running", ts
            how = "Schalter an" if sw else "EMS-ESP meldet die Desinfektion"
            if run.get("unclear"):
                how += f"; das Einschalten meldete „{run['unclear']}“"
            self._log(ts, ACT_RUN, None, f"Desinfektion {run['label']} läuft ({how}) – Ziel {_c(rule.need(run))} °C "
                                         f"im Speicher")
            self._watch(ts, run, t)
            return
        if sw is None and ems is None and ts < chk["until"]:
            return
        off = sw is False or ems is False
        if run.get("unclear"):  # Zeitüberlauf beim Einschalten – die Prüfung zeigt: nicht angekommen
            why = f"nicht gestartet ({run['unclear']}) – " + (
                "Schalter aus" if off else "Zustand des Schalters unbekannt")
        else:
            why = ("Schalter fällt sofort zurück – der Kessel nimmt die Desinfektion nicht an" if off
                   else "Zustand des Schalters unbekannt – nicht bestätigt")
        self._finish(ts, run, False, why)

    def _watch(self, ts: int, run: dict, t: float | None) -> None:
        """Beobachten (der Kessel hat bestätigt): Speicher bei der Erfolgsschwelle (``disinfect_rule.need``) →
        gelungen; war er schon beim Einschalten heiß, auch, wenn der Kessel den Schalter selbst zurücksetzt (Programm
        fertig); nach Ladedauer + 60 min → Fehlschlag."""
        need = rule.need(run)
        if t is not None and t >= need:
            self._finish(ts, run, True, f"{_c(t)} °C erreicht (Ziel {_c(need)} °C)")
        elif rule.hot(run) and t is not None and t >= run["goal"] and self.switch_on() is False:
            self._finish(ts, run, True, f"der Kessel hat sie beendet – Speicher {_c(t)} °C (war schon beim Einschalten "
                                        f"{_c(run['from'])} °C)")
        elif ts >= run["deadline"]:
            peak = run.get("peak")
            self._finish(ts, run, False, f"Speicher erreichte nur {_c(peak)} °C, nötig {_c(need)} °C"
                         if peak is not None else "Speichertemperatur unbekannt – Erfolg nicht feststellbar")

    def _finish(self, ts: int, run: dict, ok: bool | None, text: str, switch_off: bool = True) -> None:
        """Ergebnis merken, protokollieren, melden. Steht der Schalter noch an: nach einem Erfolg darf der Kessel die
        Desinfektion ``AFTER_S`` lang selbst beenden (Nachlauf), sonst gleich ausschalten."""
        st, lab = self.state, run["label"]
        st["last"] = {"due": run["due"], "label": lab, "ts": run["ts"], "end": ts, "ok": ok, "text": text,
                      "result": "gelungen" if ok else "abgebrochen" if ok is None else "fehlgeschlagen",
                      "peak": run.get("peak"), "temp": run.get("temp"), "goal": run.get("goal")}
        peak = None if run.get("peak") is None else round(run["peak"], 1)
        if ok:
            self._log(ts, ACT_DONE, peak, f"Desinfektion {lab} gelungen – {text}")
            if self.notifier is not None:
                for key in (K_FAIL, K_MISSED):
                    self.notifier.solved(key, f"Desinfektion wieder in Ordnung – {lab} gelaufen ({text})", ts)
        elif ok is False:
            self._log(ts, ACT_FAIL, peak, f"Desinfektion {lab} nicht gelaufen – {text}")
            self._notify(K_FAIL, f"Desinfektion {lab} nicht gelaufen – {text}", ts)
        else:
            self._log(ts, ACT_ABORT, peak, f"Desinfektion {lab} {text}")
        if not switch_off or self.switch_on() is False:
            st.pop("run", None)
        elif ok:  # Nachlauf – war der Speicher schon beim Einschalten heiß, bis zur Frist des Ablaufs
            until = ts + rule.AFTER_S
            run["phase"], run["after_until"] = "after", max(until, run.get("deadline") or 0) if rule.hot(run) else until
        else:
            self._to_off(ts, run)

    def _after(self, ts: int, run: dict) -> None:
        """Nachlauf nach dem Erfolg: Setzt der Kessel den Schalter selbst zurück, ist es erledigt; sonst ausschalten
        (auch bei unbekanntem Schalter – ``_off`` wartet dann auf ihn)."""
        if self.switch_on() is False:
            self._close(ts, run, None, "Schalter aus (vom Kessel)")
        elif ts >= run["after_until"]:
            self._to_off(ts, run)

    def _to_off(self, ts: int, run: dict) -> None:
        """Ausschalten beginnt: ab jetzt höchstens ``OFF_WAIT_S`` lang versuchen."""
        run["phase"], run["off"] = "off", {"since": ts}
        self._off(ts, run)

    def _off(self, ts: int, run: dict) -> None:
        """Ausschalten offen: Schalter schon aus → fertig. Sperrt etwas (``disinfect_rule.lock``: Regler am Bus bzw.
        unbekannt, EMS-ESP antwortet nicht, Pause von Weg W) oder ist der Schalter unbekannt, je Schritt neu versuchen;
        nach einem gescheiterten Aufruf frühestens nach ``CHECK_S``. Erst nach ``OFF_WAIT_S`` Meldung."""
        off = run.setdefault("off", {"since": ts})
        sw = self.switch_on()
        if sw is False:
            self._close(ts, run, None, "Schalter aus")
            return
        why = rule.lock(self.eng) or (None if sw else "Zustand des Schalters unbekannt")
        if why is None and ts >= (off.get("next") or 0):
            self.pending = ("off", run["due"])
            return
        if why:
            off["why"] = why
        if ts - off["since"] >= rule.OFF_WAIT_S:
            self._close(ts, run, f"Schalter nicht ausgeschaltet ({off.get('why') or 'unbekannt'})")

    def _check_off(self, ts: int, run: dict) -> None:
        """60 s nach dem Ausschalten: ist der Schalter aus? Unbekannt: bis 5 min weiter prüfen."""
        chk = run["check"]
        if ts < chk["due"]:
            return
        sw = self.switch_on()
        if sw is None and ts < chk["until"]:
            return
        if sw is False:
            self._close(ts, run, None, "Schalter aus")
        else:
            self._close(ts, run, "Schalter steht nach dem Ausschalten noch an" if sw
                        else "Schalter nach dem Ausschalten unbekannt")

    def _close(self, ts: int, run: dict, problem: str | None, note: str | None = None) -> None:
        """Ablauf beenden. ``problem``: der Schalter blieb an bzw. durfte nicht ausgeschaltet werden – Meldung."""
        st = self.state
        last = st.get("last")
        if last and last.get("due") == run["due"]:
            last["switch"] = problem or note
        st.pop("run", None)
        if problem:
            text = f"Desinfektion {run['label']}: {problem} – bitte in Home Assistant prüfen"
            self._log(ts, ACT_SWITCH, None, text)
            self._notify(K_SWITCH, text, ts)

    # -------------------------------------------------------------- Schalten
    async def apply(self, now: float | None = None) -> bool:
        """Vorgemerktes schalten – nur über ``Control.set_disinfect``, nur bei „HeatPilot setzt“, ohne Hindernis, nicht
        nach einem Neuaufbau. True, wenn geschaltet wurde."""
        pending, self.pending = self.pending, None
        if pending is None:
            return False
        action, due_key = pending
        if self.stopped or self.control is None or self.mode != "heatpilot":
            log.info("Desinfektion: Schaltvorgang verworfen – Schalter, Termin oder Neuaufbau")
            return False
        ts = self.planned_ts or int(time.time())
        if action == "on":
            return await self._switch_on(ts, due_key, now)
        return await self._switch_off(ts, now)

    async def _switch_on(self, ts: int, due_key: str, now: float | None) -> bool:
        """Einschalten. Klar abgelehnt (gesperrt, Fehler von Home Assistant): Fehlschlag mit Meldung. Ausgang unklar
        (Zeitüberlauf, Verbindung getrennt – ``Control.maybe_arrived``): der Ablauf beginnt trotzdem, die Prüfung nach
        60 s entscheidet – sonst stünde ein doch angenommener Schalter unbeobachtet an."""
        why = rule.blocker(self.eng, self.entity_id)
        if why:
            log.info("Desinfektion: Einschalten verworfen – %s", why)
            return False
        lab = rule.label(datetime.fromisoformat(due_key))
        temp, goal = rule.reach(self.eng)
        self._mark(due_key)  # höchstens einmal – auch wenn das Schalten scheitert oder HeatPilot gleich neu startet
        self._save()
        run = {"due": due_key, "label": lab, "ts": ts, "phase": "check", "temp": temp, "goal": goal,
               "deadline": rule.deadline(self.eng, ts), "from": self.tank(), "peak": self.tank()}
        sent = True
        try:
            await self.control.set_disinfect(True, f"Desinfektion {lab} ({_c(temp)} °C)")
        except Exception as err:  # noqa: BLE001 – Fehlschlag mit Meldung, kein neuer Versuch vor dem nächsten Termin
            if not self._unclear(err):
                self._finish(ts, run, False, f"nicht gestartet ({_err(err)})", switch_off=False)
                self._save()
                return False
            sent, run["unclear"] = False, _err(err)
            log.warning("Desinfektion %s: Einschalten unklar (%s) – die Prüfung nach %d s entscheidet", lab,
                        run["unclear"], rule.CHECK_S)
        done = time.time() if now is None else now
        run["check"] = {"due": done + rule.CHECK_S, "until": done + rule.CHECK_MAX_S}
        self.state["run"], self.state["on"] = run, {"ts": ts, "due": due_key}
        if sent:
            log.info("Desinfektion %s eingeschaltet (%s °C), Prüfung in %d s", lab, _c(temp), rule.CHECK_S)
        self._save()
        return sent

    def _unclear(self, err: Exception) -> bool:
        """Kann der Aufruf trotz Fehler angekommen sein? Eine Sperre (``PermissionError``) nie."""
        if isinstance(err, PermissionError):
            return False
        probe = getattr(self.control, "maybe_arrived", None)
        return bool(probe(err)) if probe is not None else isinstance(err, (asyncio.TimeoutError, TimeoutError))

    async def _switch_off(self, ts: int, now: float | None) -> bool:
        """Ausschalten (``_off`` hat es vorgemerkt). Scheitert es oder sperrt etwas gerade, bleibt es offen und
        ``_off`` versucht es weiter – nach ``OFF_WAIT_S`` mit Meldung."""
        run = self.state.get("run")
        if not run or run.get("phase") != "off":
            return False
        off = run.setdefault("off", {"since": ts})
        try:
            why = rule.lock(self.eng)
            if why:
                raise PermissionError(why)
            await self.control.set_disinfect(False, f"Desinfektion {run['label']} vorbei – Schalter aus")
        except Exception as err:  # noqa: BLE001 – offen lassen, im nächsten Schritt (bzw. nach 60 s) erneut
            off["why"], off["next"] = _err(err), ts + rule.CHECK_S
            if ts - off["since"] >= rule.OFF_WAIT_S:
                self._close(ts, run, f"Schalter nicht ausgeschaltet ({off['why']})")
            else:
                log.info("Desinfektion %s: Ausschalten gescheitert (%s) – neuer Versuch", run["label"], off["why"])
            self._save()
            return False
        done = time.time() if now is None else now
        run["phase"], run["check"] = "off_check", {"due": done + rule.CHECK_S, "until": done + rule.CHECK_MAX_S}
        run.pop("off", None)
        self._save()
        return True

    # -------------------------------------------------------------- Meldungen, Protokoll, Zustand
    def _settle(self, ts: int) -> None:
        """Ohne „HeatPilot setzt“ alle Meldungen still schließen; Schalter wieder aus → „wieder in Ordnung“."""
        n = self.notifier
        if n is None:
            return
        if self.mode != "heatpilot":
            n.settle("disinfect_", {}, ts)
        elif K_SWITCH in n.open_keys() and not self.active() and self.switch_on() is False:
            n.solved(K_SWITCH, "Desinfektion: Schalter wieder aus", ts)

    def _notify(self, key: str, text: str, ts: int) -> None:
        """Jedes Ereignis einmal melden – ein noch offenes vom letzten Termin vorher still schließen."""
        if self.notifier is not None:
            self.notifier.solved(key, ts=ts, quiet=True)
            self.notifier.problem(key, text, ts)

    def _log(self, ts: int, action: str, value, reason: str) -> None:
        self.store.add_control_log(int(ts), self.entity_id or LOG_ROOM, action, value, reason)

    def _save(self) -> None:
        """Zustand speichern – nur bei Änderung (der Schritt läuft alle 30 s)."""
        text = json.dumps(self.state, sort_keys=True, default=str)
        if text != self._saved:
            self.store.put(KV, self.state)
            self._saved = text
