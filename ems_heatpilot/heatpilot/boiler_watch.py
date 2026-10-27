"""Weg B (seit 0.41): fremde Schreiber, Eingriff des Wächters, Pause, Sperre, Abweisungen – und die Meldungen dazu.

``BoilerWatch`` ist ein Mixin für ``boiler_control.BoilerControl`` (wie ``boiler_status`` für die Anzeige): Es braucht
dessen ``state``, ``notifier``, ``eng``, ``paused``, ``_ch``, ``_log``, ``_save`` und schreibt selbst nichts am Kessel.

* ``_watch`` – fremden Schreiber erkennen: Ein Wert ist anders als zuletzt gesehen, ohne dass HeatPilot schrieb.
  Änderungen in **einem** Schritt (Vorlauf-Soll und Heizbetrieb zugleich) zählen als **ein** Ereignis; 2 Ereignisse
  in 1 h → Pause bis „wieder einschalten“.
* ``_watchdog_hit`` – nach einer Pause von HeatPilot ab ``WATCHDOG_GAP_S`` (der Wächter in Home Assistant greift nach
  60 min ohne Heartbeat): Steht der Kessel jetzt auf genau dem Rückfall-Vorlauf mit Heizbetrieb an, war es der Wächter –
  gemeldet, aber nicht als fremder Schreiber gezählt (keine Pause); HeatPilot führt danach wieder.
* ``_rejected`` – ``Control`` weist einen Schreibvorgang ab (Schutzregel, z. B. Grenze der Entität): Hinweis, nach
  ``REJECT_NOTE_S`` Meldung; erledigt, sobald 5 min nichts mehr abgewiesen wurde.
* ``_blocked_note`` – nach dem ersten Schreiben 15 min gesperrt (EMS-ESP antwortet nicht, Regler wieder dran): Meldung.
* ``_settle`` – Meldungen abgleichen (Schalter nicht „heatpilot“, Pause aufgehoben, seit 1 h nichts Fremdes).
"""

from __future__ import annotations

import logging
import time

from . import boiler_rule as br
from . import watchdog
from .boiler_rule import CHECK_TOL_K, FOREIGN_MAX, FOREIGN_WINDOW_S, HINT_S, PUMP_FLOW, shown
from .boiler_status import ACT_FOREIGN, ACT_PAUSE, ACT_RESUME, fallback

log = logging.getLogger(__name__)

# Schlüssel der Meldungen (notify.Notifier)
K_FAIL, K_PAUSED, K_FOREIGN, K_BLOCKED = "boiler_write_failed", "boiler_paused", "boiler_foreign", "boiler_blocked"
K_REJECTED, K_BLIND, K_WATCHDOG = "boiler_rejected", "boiler_blind", "boiler_watchdog"
BLOCKED_NOTE_S = 900   # gesperrt, nachdem HeatPilot schon geschrieben hat: nach so langer Zeit melden
REJECT_NOTE_S = 900    # Control weist ab: nach so langer Zeit melden …
REJECT_CLEAR_S = 300   # … erledigt, wenn so lange nichts mehr abgewiesen wurde
WATCHDOG_GAP_S = watchdog.STALE_MIN * 60 - 300  # so lange lief HeatPilot nicht: der Wächter kann eingegriffen haben


def same(what: str, a, b) -> bool:
    """Gleicher Wert am Kessel: Vorlauf ±0,5 K, Heizbetrieb gleich; unbekannt ist nie gleich. Seit 0.47.4 gelten zwei
    Vorlauf-Soll bis ``PUMP_FLOW`` als gleich: Der Kessel meldet „kein Wärmebedarf“ selbst als eigenen Wert (z. B. 7 °C
    statt der geschriebenen 20 °C) – das ist kein anderer Schreiber."""
    if a is None or b is None:
        return False
    if what == "flow" and float(a) <= PUMP_FLOW + CHECK_TOL_K and float(b) <= PUMP_FLOW + CHECK_TOL_K:
        return True
    return abs(float(a) - float(b)) <= CHECK_TOL_K if what == "flow" else bool(a) == bool(b)


def forget_rest_drops(st: dict) -> None:
    """Fremde Änderungen aus der Zeit vor 0.48.1 vergessen (seit 0.49.1): Damals zählte der Rückfall des Kessels auf
    seinen Ruhewert (Vorlauf-Soll 7 °C) noch als fremder Schreiber – heute nicht mehr, der Hinweis bliebe sonst 24 h
    stehen. Erkannt an der letzten fremden Änderung; mit ihr gehen die Zeitpunkte bis dahin."""
    last = st.get("foreign_last")
    if last and last.get("what") == "flow" and last.get("to") is not None and float(last["to"]) <= PUMP_FLOW + CHECK_TOL_K:
        st.pop("foreign_last")
        st["foreign"] = [t for t in st.get("foreign") or [] if t > last["ts"]]


class BoilerWatch:
    """Überwachungs-Teil von ``BoilerControl`` (siehe Modulbeschreibung)."""

    # -------------------------------------------------------------- fremde Schreiber, Wächter
    def _watch(self, ts: int, cur: dict, charging: bool, blocked: str | None) -> None:
        """Fremden Schreiber erkennen: ein Wert anders als zuletzt gesehen, ohne dass HeatPilot schrieb. Zurück auf
        den eigenen Wert ist nicht fremd; gesperrt (Regler am Bus …) zählt nichts, der Bezug beginnt danach neu."""
        changes = []
        for what in ("flow", "heating"):
            ch = self._ch(what)
            seen = ch.get("seen")
            if blocked:
                ch.pop("seen", None)
                continue
            if cur[what] is not None:
                ch["seen"] = cur[what]
            if (cur[what] is None or seen is None or ch.get("check") or self.paused()
                    or charging or same(what, cur[what], seen)  # 0.57: Ladung schaltet auch den Heizbetrieb
                    or (what == "flow" and float(cur[what]) <= PUMP_FLOW + CHECK_TOL_K)):  # 0.48.1: verworfen
                continue
            last = ch.get("last")
            if last and same(what, cur[what], last["value"]):
                continue
            changes.append((what, seen, cur[what]))
        if changes and not self._watchdog_hit(ts, changes, cur):
            self._foreign(ts, changes)

    def _watchdog_hit(self, ts: int, changes: list[tuple], cur: dict) -> bool:
        """War es der Wächter in Home Assistant? HeatPilot lief mindestens ``WATCHDOG_GAP_S`` nicht, der Kessel steht
        jetzt auf genau dem Rückfall-Vorlauf mit Heizbetrieb an, und nur das hat sich geändert. Dann melden statt
        zählen – kein fremder Schreiber, keine Pause."""
        st = self.state
        gap = ts - int(st.get("last_plan") or ts)
        fb = fallback(self.eng)
        if gap < WATCHDOG_GAP_S or fb is None or not (same("flow", cur["flow"], fb) and cur["heating"]):
            return False
        if any(what == "heating" and not value for what, _, value in changes):
            return False
        st["watchdog_hit"] = {"ts": ts, "flow": cur["flow"], "gap_min": gap // 60}
        text = (f"Der Wächter in Home Assistant hat eingegriffen: Vorlauf-Soll {shown('flow', cur['flow'])}, Heizbetrieb "
                f"an – HeatPilot lief {gap // 60} min nicht")
        self._log(ts, ACT_FOREIGN, cur["flow"], f"{text}; kein fremder Schreiber, HeatPilot führt den Kessel wieder")
        log.warning("Kessel über HeatPilot: %s", text)
        self._notify(K_WATCHDOG, f"Kessel: {text}. HeatPilot führt den Kessel wieder – bitte im Protokoll des Add-ons "
                                 "nachsehen, warum es ausfiel", ts)
        return True

    def _foreign(self, ts: int, changes: list[tuple]) -> None:
        """Fremde Änderungen eines Schritts: ein Ereignis (auch wenn Vorlauf-Soll und Heizbetrieb zugleich), je Wert
        eine Zeile im Protokoll, eine Meldung; ab ``FOREIGN_MAX`` Ereignissen in 1 h Pause."""
        st = self.state
        st["foreign"] = [t for t in st.get("foreign") or [] if t > ts - HINT_S] + [ts]
        what, seen, now_value = changes[0]
        st["foreign_last"] = {"ts": ts, "what": what, "from": seen, "to": now_value}
        texts = []
        for what, seen, now_value in changes:
            change = f"{br.LABELS[what]} von außen geändert: {shown(what, seen)} → {shown(what, now_value)}"
            texts.append(change)
            self._log(ts, ACT_FOREIGN, now_value if what == "flow" else int(bool(now_value)),
                      f"{change} – anderer Schreiber aktiv")
        self._notify(K_FOREIGN, f"Kessel: {'; '.join(texts)} – Regler noch am Bus oder eine Automation?", ts)
        recent = [t for t in st["foreign"] if t > ts - FOREIGN_WINDOW_S and t > st.get("resumed", 0)]
        if len(recent) >= FOREIGN_MAX:
            self._pause(ts, f"{FOREIGN_MAX} fremde Änderungen in 1 h",
                        f"Kessel: {len(recent)}× in 1 h von außen geändert – Kessel über HeatPilot pausiert, damit "
                        "nicht gegeneinander geschrieben wird. Regler noch am Bus oder eine Automation? Danach in "
                        "HeatPilot wieder einschalten")

    # -------------------------------------------------------------- Pause
    def _pause(self, ts: int, reason: str, text: str) -> None:
        if self.state.get("paused"):
            return
        self.state["paused"] = {"ts": ts, "reason": reason}
        self.pending = []
        self._log(ts, ACT_PAUSE, None, f"Kessel über HeatPilot pausiert: {reason}")
        self._notify(K_PAUSED, text, ts)

    def resume(self, ts: int | None = None) -> bool:
        """„Wieder einschalten“ – nur bei bestehender Pause (sonst False, nichts geändert): Pause aufheben,
        Fehlschläge zurück, fremde Änderungen ab jetzt neu zählen. True, wenn pausiert war."""
        st = self.state
        was = st.get("paused")
        if not was:
            return False
        ts = int(time.time()) if ts is None else int(ts)
        st.pop("paused")
        st["fails"], st["resumed"] = 0, ts
        for what in ("flow", "heating"):
            self._ch(what).pop("seen", None)
        self._log(ts, ACT_RESUME, None, f"Kessel über HeatPilot wieder eingeschaltet (pausiert wegen: {was['reason']})")
        if self.notifier is not None:
            for key in (K_PAUSED, K_FOREIGN):
                self.notifier.solved(key, ts=ts, quiet=True)
        self._save()
        return True

    # -------------------------------------------------------------- gesperrt, abgewiesen
    def _blocked_note(self, ts: int, blocked: str | None) -> None:
        """Hat HeatPilot schon geschrieben und ist das Schreiben seit 15 min gesperrt (EMS-ESP antwortet nicht, Regler
        wieder angeschlossen …), wird es gemeldet – der Kessel behält solange den letzten Wert."""
        st, n = self.state, self.notifier
        if not blocked:
            if st.pop("blocked_since", None) is not None and n is not None:
                n.solved(K_BLOCKED, "Kessel über HeatPilot schreibt wieder", ts)
            return
        if not any((self._ch(w).get("last") or {}).get("ts", 0) >= st.get("since", 0) for w in ("flow", "heating")):
            return  # in diesem Einschalten noch nichts geschrieben (z. B. Regler noch dran): nur Hinweis
        since = st.setdefault("blocked_since", ts)
        if ts - since >= BLOCKED_NOTE_S:
            kept = (self._ch("flow").get("last") or {}).get("value")
            self._notify(K_BLOCKED, f"Kessel über HeatPilot schreibt seit {BLOCKED_NOTE_S // 60} min nicht: {blocked}"
                                    f" – der Kessel behält {f'{kept} °C' if kept is not None else 'den letzten Wert'}",
                         ts)

    def _rejected(self, ts: int, what: str, value, err: Exception) -> None:
        """``Control`` hat den Schreibvorgang abgewiesen (Schutzregel): kein Fehlschlag, aber Hinweis – der Kessel
        behält seinen Wert. Bleibt es dabei, kommt nach ``REJECT_NOTE_S`` eine Meldung."""
        rej = self.state.get("rejected")
        first = rej is None or rej.get("what") != what
        since = ts if first else rej["ts"]
        self.state["rejected"] = {"ts": since, "last": ts, "what": what, "value": value, "text": str(err)}
        (log.warning if first else log.debug)("Kessel über HeatPilot: %s %s abgewiesen – %s", br.LABELS[what],
                                              shown(what, value), err)

    def _rejected_note(self, ts: int) -> None:
        """Abweisung erledigt (seit ``REJECT_CLEAR_S`` keine mehr) bzw. seit ``REJECT_NOTE_S`` anhaltend: melden."""
        rej = self.state.get("rejected")
        if rej and ts - rej["last"] > REJECT_CLEAR_S:
            self.state.pop("rejected")
            rej = None
        if not rej:  # auch nach einem gelungenen Schreiben (``_write`` räumt ``rejected`` ab)
            if self.notifier is not None:
                self.notifier.solved(K_REJECTED, "Kessel: HeatPilot schreibt wieder", ts)
        elif ts - rej["ts"] >= REJECT_NOTE_S:
            self._notify(K_REJECTED, f"Kessel: {br.LABELS[rej['what']]} {shown(rej['what'], rej['value'])} wird seit "
                                     f"{REJECT_NOTE_S // 60} min abgewiesen ({rej['text']}) – bitte prüfen", ts)

    # -------------------------------------------------------------- Meldungen
    def _settle(self, ts: int) -> None:
        """Meldungen abgleichen: ohne „heatpilot“ alle still schließen, Pause aufgehoben → still, seit 1 h keine
        fremde Änderung → „wieder in Ordnung“; der Eingriff des Wächters nach 1 h still."""
        n = self.notifier
        if n is None:
            return
        if self.mode != "heatpilot":
            n.settle("boiler_", {}, ts)
            return
        if not self.paused():
            n.solved(K_PAUSED, ts=ts, quiet=True)
        if not any(t > ts - FOREIGN_WINDOW_S for t in self.state.get("foreign") or []):
            n.solved(K_FOREIGN, "Kessel: seit 1 h keine fremden Änderungen mehr", ts)
        hit = self.state.get("watchdog_hit")
        if hit and ts - hit["ts"] >= FOREIGN_WINDOW_S:
            n.solved(K_WATCHDOG, ts=ts, quiet=True)

    def _notify(self, key: str, text: str, ts: int) -> None:
        if self.notifier is not None:
            self.notifier.problem(key, text, ts)
