"""Desinfektion auslösen (seit 0.42): zum Termin einschalten, danach nur noch den Speicher beobachten.

Wann HeatPilot schalten darf, steht in ``disinfect_rule`` (Termin, Warmwasser-Soll „HeatPilot setzt“, kein Regler am
Bus, keine Pause von Weg W; Schalter tauglich – ``disinfect_entity``). Geschrieben wird nur über
``Control.start_disinfect`` (``switch.turn_on`` auf die eine Entität); EMS-ESP bleibt nur lesend. Der Schalter ist ein
**Taster** (seit 0.74.1): Er rastet ein, der Kessel heizt und setzt ihn am Ende seines Programms selbst zurück –
HeatPilot schaltet nur ein, prüft den Schalter nicht und schaltet ihn nie aus.

Je Schritt (30-s-Takt von ``main.disinfect_step``: ``plan``, danach ``apply``):

1. **Fällig** (``disinfect_rule.due``): Termin begonnen, höchstens 12 h her, noch nicht erledigt. Macht ein Regler am
   Bus die Desinfektion (frisch gemeldet), ist der Termin damit erledigt (nichts geschaltet). Im Trockenlauf steht
   „würde Desinfektion starten“ im Protokoll. Bei „HeatPilot setzt“ wird eingeschaltet – nur ohne Hindernis; sonst
   wartet der Termin (Grund in der Anzeige) bis 12 h nach dem Beginn. Ein Termin gilt mit dem Einschalten als erledigt,
   auch wenn es scheitert: höchstens einmal ausgelöst, auch nach einem Neustart (Zustand in kv
   ``disinfect_control.state``). Lief binnen 24 h schon eine (Termin verlegt), wird er übersprungen (``too_soon``).
2. **Verpasst** (``disinfect_rule.missed``): Lief HeatPilot länger als 12 h nach dem Termin nicht bzw. war es so lange
   gesperrt: Meldung „Desinfektion … verpasst“, dann gilt der nächste Termin.
3. **Beobachten** vom Einschalten an: Erreicht der Speicher die Desinfektionstemperatur − 3 K, ist sie gelungen (lag
   er schon beim Einschalten darüber, erst die volle Temperatur); sonst nach Ladedauer laut Modell + 60 min ein
   Fehlschlag mit Meldung (``K_FAIL``, Problem-Sensor). Der nächste Erfolg meldet „wieder in Ordnung“. Scheitert schon
   das Einschalten (gesperrt, Fehler von Home Assistant), ist das der Fehlschlag; ist sein Ausgang unklar
   (Zeitüberlauf), entscheidet ebenso der Speicher.

Solange ein Ablauf läuft (``active``: vom Einschalten bis zum Ergebnis), lassen Weg W (Speicher-Soll) und Weg B
(Vorlauf-Soll) den Kessel in Ruhe – ``dhw_control.special_running`` fragt das, danach den Schalter selbst, solange der
Kessel desinfiziert. Wird Weg W umgestellt, bricht der Ablauf ab (der Kessel beendet die Desinfektion selbst). Ein
gescheiterter Termin zählt im Warmwasser-Modell nicht mehr (``triggered``). Die Anzeige steht in ``disinfect_status``.
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
from .disinfect_status import ACT_ABORT, ACT_DONE, ACT_DRY, ACT_FAIL, ACT_MISSED, ACT_SKIP, LOG_ROOM, DisinfectStatus
from .values import num

log = logging.getLogger(__name__)

KV = "disinfect_control.state"
# Schlüssel der Meldungen (notify.Notifier) – Anfang „disinfect_“ (``settle`` schließt alle anderen still)
K_FAIL, K_MISSED = "disinfect_failed", "disinfect_missed"


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
        self.pending: str | None = None  # Kennung des Termins, der eingeschaltet werden soll
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
        """Läuft ein Ablauf (eingeschaltet bis zum Ergebnis)? Dann fassen Weg W und Weg B den Kessel nicht an."""
        return bool(self.state.get("run"))

    # -------------------------------------------------------------- Kessel lesen
    def _fresh(self) -> dict:
        """Kesselwerte direkt von EMS-ESP – nur frisch (höchstens 90 s alt), sonst leer."""
        ts = getattr(self.eng, "ems_ts", None)
        return (self.eng.ems.get("boiler") or {}) if ts is not None and time.time() - ts <= EMS_FRESH_S else {}

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
                          "text": "Beobachten – nur im Protokoll"}
            self._log(ts, ACT_DRY, None, f"Beobachten: würde Desinfektion {lab} starten ({_c(temp)} °C, Schalter "
                                         f"{self.entity_id or 'fehlt'})")
            return
        why = rule.blocker(self.eng, self.entity_id)
        if why:
            w = st.get("waiting") if (st.get("waiting") or {}).get("due") == k else None
            st["waiting"] = {"due": k, "label": lab, "why": why, "since": (w or {}).get("since", ts)}
            return
        st.pop("waiting", None)
        self.pending = k

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
        """Laufender Ablauf: den Speicher beobachten (siehe Modulbeschreibung). Weg W umgestellt: abbrechen."""
        if (self.state.get("last") or {}).get("due") == run["due"]:  # Ergebnis steht schon fest (Ablauf von vor
            self.state.pop("run", None)                              # 0.74.1 im Nachlauf bzw. beim Ausschalten)
            return
        if self.mode != "heatpilot":
            self._finish(ts, run, None, "abgebrochen – Warmwasser-Soll nicht mehr „HeatPilot setzt“ bzw. kein Termin; "
                                        "der Kessel beendet die Desinfektion selbst")
            return
        t = self.tank()
        if t is not None:
            run["peak"] = max(run.get("peak") or t, t)
        need = rule.need(run)
        if t is not None and t >= need:
            self._finish(ts, run, True, f"{_c(t)} °C erreicht (Ziel {_c(need)} °C)")
        elif ts >= run["deadline"]:
            peak = run.get("peak")
            why = (f"Speicher erreichte nur {_c(peak)} °C, nötig {_c(need)} °C" if peak is not None
                   else "Speichertemperatur unbekannt – Erfolg nicht feststellbar")
            self._finish(ts, run, False, why + (f" (das Einschalten meldete „{run['unclear']}“)"
                                                if run.get("unclear") else ""))

    def _finish(self, ts: int, run: dict, ok: bool | None, text: str) -> None:
        """Ergebnis merken, protokollieren, melden – damit ist der Ablauf vorbei (den Schalter setzt der Kessel zurück)."""
        st, lab = self.state, run["label"]
        st["last"] = {"due": run["due"], "label": lab, "ts": run["ts"], "end": ts, "ok": ok, "text": text,
                      "result": "gelungen" if ok else "abgebrochen" if ok is None else "fehlgeschlagen",
                      "peak": run.get("peak"), "temp": run.get("temp"), "goal": run.get("goal")}
        st.pop("run", None)
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

    # -------------------------------------------------------------- Schalten
    async def apply(self) -> bool:
        """Vorgemerktes Einschalten – nur über ``Control.start_disinfect``, nur bei „HeatPilot setzt“, ohne Hindernis,
        nicht nach einem Neuaufbau. True, wenn eingeschaltet wurde."""
        due_key, self.pending = self.pending, None
        if due_key is None:
            return False
        if self.stopped or self.control is None or self.mode != "heatpilot":
            log.info("Desinfektion: Einschalten verworfen – Schalter, Termin oder Neuaufbau")
            return False
        return await self._switch_on(self.planned_ts or int(time.time()), due_key)

    async def _switch_on(self, ts: int, due_key: str) -> bool:
        """Einschalten. Klar abgelehnt (gesperrt, Fehler von Home Assistant): Fehlschlag mit Meldung. Ausgang unklar
        (Zeitüberlauf, Verbindung getrennt – ``Control.maybe_arrived``): der Ablauf beginnt trotzdem, der Speicher
        entscheidet."""
        why = rule.blocker(self.eng, self.entity_id)
        if why:
            log.info("Desinfektion: Einschalten verworfen – %s", why)
            return False
        lab = rule.label(datetime.fromisoformat(due_key))
        temp, goal = rule.reach(self.eng)
        self._mark(due_key)  # höchstens einmal – auch wenn das Schalten scheitert oder HeatPilot gleich neu startet
        self._save()
        run = {"due": due_key, "label": lab, "ts": ts, "temp": temp, "goal": goal,
               "deadline": rule.deadline(self.eng, ts), "from": self.tank(), "peak": self.tank()}
        sent = True
        try:
            await self.control.start_disinfect(f"Desinfektion {lab} ({_c(temp)} °C)")
        except Exception as err:  # noqa: BLE001 – Fehlschlag mit Meldung, kein neuer Versuch vor dem nächsten Termin
            if not self._unclear(err):
                self._finish(ts, run, False, f"nicht gestartet ({_err(err)})")
                self._save()
                return False
            sent, run["unclear"] = False, _err(err)
            log.warning("Desinfektion %s: Einschalten unklar (%s) – der Speicher entscheidet", lab, run["unclear"])
        self.state["run"], self.state["on"] = run, {"ts": ts, "due": due_key}
        if sent:
            log.info("Desinfektion %s eingeschaltet (%s °C) – der Kessel beendet sie selbst", lab, _c(temp))
        self._save()
        return sent

    def _unclear(self, err: Exception) -> bool:
        """Kann der Aufruf trotz Fehler angekommen sein? Eine Sperre (``PermissionError``) nie."""
        if isinstance(err, PermissionError):
            return False
        probe = getattr(self.control, "maybe_arrived", None)
        return bool(probe(err)) if probe is not None else isinstance(err, (asyncio.TimeoutError, TimeoutError))

    # -------------------------------------------------------------- Meldungen, Protokoll, Zustand
    def _settle(self, ts: int) -> None:
        """Offene Meldungen abgleichen: bei „HeatPilot setzt“ bleiben nur „nicht gelaufen“ und „verpasst“ – alle
        anderen schließen still (etwa „Schalter steht noch an“ von vor 0.74.1); sonst alle."""
        n = self.notifier
        if n is None:
            return
        keep = {k: text for k, text in n.open_keys("disinfect_").items() if k in (K_FAIL, K_MISSED)}
        n.settle("disinfect_", keep if self.mode == "heatpilot" else {}, ts)

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
