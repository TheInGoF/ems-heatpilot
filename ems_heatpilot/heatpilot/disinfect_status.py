"""Anzeige des Auslösens der Desinfektion (seit 0.42): wer sie macht, Stand, letztes Ergebnis – nur lesen.

``info`` sagt für Einstellungen → Warmwasser, Übersicht und „Bereit für die Steuerung?“, wer die Desinfektion zum Termin
auslöst – „Desinfektion: löst HeatPilot aus (So 17:30)“, „macht der Regler“ (Regler am Bus), „Trockenlauf“ oder „aus“ –,
ob gerade etwas sperrt, ob ein Ablauf läuft und wie die letzte ausging. ``ready`` macht daraus Einschränkungen und
Hinweise für den Block „Warmwasser über HeatPilot“ (ändert die Stufe der Raumsteuerung nicht). ``DisinfectStatus`` ist
das Mixin für ``disinfect_control.DisinfectControl`` (Protokollzeilen, ``as_dict`` für ``api/dhw``). Hier stehen auch
die Aktionen der Protokollzeilen (``control_log``; die Zeilen von ``Control`` heißen ``disinfect_on``/``_off``).

Uhrzeiten gehen als ISO-Felder hinaus (der Browser setzt sie in seiner Zeitzone ein); Texte tragen nur den Termin, wie
er eingestellt ist („So 17:30“).
"""

from __future__ import annotations

import time

from . import disinfect_entity as de
from . import disinfect_rule as rule
from .dhw_disinfect import NOT_YET
from .dhw_rule import _c, _iso
from .regulator import regulator
from .values import local

# Protokoll (control_log): Schalten über Control und die Zeilen des Ablaufs
ACT_ON, ACT_OFF = "disinfect_on", "disinfect_off"
ACT_DRY, ACT_RUN, ACT_DONE, ACT_FAIL, ACT_MISSED, ACT_ABORT, ACT_SWITCH, ACT_SKIP = (
    "disinfect_dry", "disinfect_running", "disinfect_done", "disinfect_failed", "disinfect_missed",
    "disinfect_aborted", "disinfect_switch", "disinfect_skipped")
ACTIONS = (ACT_ON, ACT_OFF, ACT_DRY, ACT_RUN, ACT_DONE, ACT_FAIL, ACT_MISSED, ACT_ABORT, ACT_SWITCH, ACT_SKIP)
LOG_ROOM, LOG_NAME = "disinfect", "Desinfektion"

# Hinweis je Stand (Einstellungen → Warmwasser; die Kachel der Übersicht zeigt den Teil vor „ – “)
HINT_HEATPILOT = ("HeatPilot löst sie aus – zum Termin schaltet es den Schalter „Desinfizieren“ und prüft, ob der "
                  "Speicher die Desinfektionstemperatur erreicht")
HINT_REGULATOR = ("Der Regler macht sie – er ist am Bus und desinfiziert selbst, seinen Termin hier eintragen; "
                  "HeatPilot löst sie erst aus, wenn kein Regler mehr am Bus ist")
HINT_DRY = "Beobachten – zum Termin schreibt HeatPilot nur ins Protokoll, dass es die Desinfektion starten würde"
NOBODY = ("Desinfektionstermin eingetragen, aber niemand löst ihn aus – kein Regler am Bus; mit „Warmwasser-Soll: "
          "HeatPilot setzt“ löst HeatPilot die Desinfektion aus")
RESULTS = {"gelungen": "gelungen", "fehlgeschlagen": "nicht gelaufen", "verpasst": "verpasst",
           "abgebrochen": "abgebrochen", "regler": "macht der Regler", "trocken": "Beobachten"}


def _who(cfg, reg: dict) -> str:
    """Wer löst aus: „regler“ (Regler am Bus, frisch gemeldet), „heatpilot“, „trocken“ oder „aus“. Gilt der Regler nur
    noch kurz als vorhanden (``hold``, eben abgebaut?), sperrt er nur – der Termin wartet (``blocked``)."""
    if reg["present"] is True and not reg.get("hold"):
        return "regler"
    return rule.mode(cfg)


def _texts(cfg, who: str, label: str | None) -> tuple[str, str, str | None]:
    """(Zeile, Hinweis, Grund für „aus“)."""
    if who == "regler":
        return "Desinfektion: macht der Regler", HINT_REGULATOR, None
    if who == "heatpilot":
        return f"Desinfektion: löst HeatPilot aus ({label})", HINT_HEATPILOT, None
    if who == "trocken":
        return f"Desinfektion: Beobachten – würde {label} auslösen", HINT_DRY, None
    why = ("kein Termin eingestellt" if not rule.planned(cfg)
           else "HeatPilot löst sie nur bei „Warmwasser-Soll: HeatPilot setzt“ ohne Regler am Bus aus")
    return "Desinfektion: aus", NOT_YET, why


def _run(ctl, now_ts: int) -> dict | None:
    """Laufender Ablauf für die Anzeige; ``off_wait``: Ausschalten offen und gerade gesperrt (Grund, seit wann)."""
    run = (ctl.state.get("run") if ctl is not None else None) or None
    if not run:
        return None
    off = run.get("off") if run.get("phase") == "off" else None
    wait = {"why": off["why"], "since": _iso(off.get("since"))} if off and off.get("why") else None
    text = f"läuft seit dem Termin {run['label']} – Speicher {_c(ctl.tank())} °C, Ziel {_c(rule.need(run))} °C"
    return {"label": run["label"], "phase": run.get("phase"), "since": _iso(run["ts"]), "goal": rule.need(run),
            "temp": run.get("temp"), "peak": run.get("peak"), "tank": ctl.tank(), "deadline": _iso(run.get("deadline")),
            "off_wait": wait, "text": text + (f"; Ausschalten wartet – {wait['why']}" if wait else "")}


def _last(st: dict) -> dict | None:
    last = st.get("last")
    if not last:
        return None
    return {**last, "time": _iso(last.get("ts")), "end_time": _iso(last.get("end")),
            "result_text": RESULTS.get(last.get("result"), last.get("result"))}


def info(eng, ctl=None, now_ts: float | None = None) -> dict:
    """Wer löst die Desinfektion aus, was sperrt, was läuft, wie ging die letzte aus (siehe Modulbeschreibung)."""
    cfg = eng.cfg
    ctl = ctl if ctl is not None else getattr(eng, "disinfect_control", None)
    now_ts = int(time.time() if now_ts is None else now_ts)
    entity, source = ((ctl.entity_id, ctl.entity_source) if ctl is not None
                      else de.target(cfg, eng.found, eng.states))
    state = eng.states.get(entity) if entity else None
    nxt = cfg.next_disinfection(local(now_ts))
    label = rule.label(nxt) if nxt else None
    reg = regulator(eng)
    present, who = reg["present"], _who(cfg, reg)
    text, hint, why_off = _texts(cfg, who, label)
    blocked = rule.blocker(eng, entity) if who == "heatpilot" else None
    st = ctl.state if ctl is not None else {}
    wait = st.get("waiting")
    detected = (getattr(eng.dhw, "disinfects", None) or [None])[-1]
    failed = (who == "heatpilot" and (st.get("last") or {}).get("ok") is False
              and rule.failed_recently(st["last"], now_ts, eng.disinfect_ready_h()))
    return {
        "state": who, "text": text, "hint": hint, "reason": why_off, "label": label,
        "next": nxt.isoformat() if nxt else None, "triggers": who == "heatpilot" and blocked is None,
        "blocked": blocked, "regulator": present,
        "entity": entity, "entity_source": source,
        "entity_name": ((state or {}).get("attributes") or {}).get("friendly_name") or entity,
        "entity_problem": de.problem(entity, state, eng.found) if entity else None,
        "switch": (state or {}).get("state"),
        "waiting": None if not wait else {**wait, "since": _iso(wait.get("since"))},
        "run": _run(ctl, now_ts), "last": _last(st), "detected": detected,
        # gescheiterter Termin: seine Wärme zählt im Warmwasser-Modell nicht (``DisinfectControl.triggered``)
        "uncounted": (f"Desinfektion {st['last']['label']} {RESULTS.get(st['last'].get('result'), 'nicht gelaufen')} – "
                      "ihre Wärme zählt im Warmwasser-Modell nicht") if failed else None,
        "rules": {"check_s": rule.CHECK_S, "reach_k": rule.REACH_K, "extra_min": rule.EXTRA_S // 60,
                  "catchup_h": rule.CATCHUP_S // 3600, "after_min": rule.AFTER_S // 60,
                  "off_wait_min": rule.OFF_WAIT_S // 60, "gap_h": rule.GAP_S // 3600},
    }


def ready(eng, now_ts: int) -> tuple[list[str], list[str], dict]:
    """Für „Bereit für die Steuerung?“, Block „Warmwasser über HeatPilot“: (Einschränkungen, Hinweise, ``info``)."""
    x = info(eng, now_ts=now_ts)
    limits, notes = [], [x["text"] + (f" – {x['reason']}" if x["reason"] and rule.planned(eng.cfg) else "")]
    if x["state"] == "heatpilot" and x["blocked"]:  # ob ein Regler sie macht, ist unbekannt: nur Hinweis
        (limits if x["regulator"] is False else notes).append(
            f"Desinfektion: HeatPilot kann sie zurzeit nicht auslösen – {x['blocked']}")
    if x["state"] == "aus" and rule.planned(eng.cfg) and x["regulator"] is False:
        limits.append(NOBODY)
    last = x["last"]
    if last and last.get("ok") is False:
        limits.append(f"Letzte Desinfektion ({last['label']}) {last['result_text']} – {last['text']}")
    elif last and last.get("ok"):
        notes.append(f"Letzte Desinfektion ({last['label']}) gelungen – {last['text']}")
    if x["run"]:
        notes.append(f"Desinfektion {x['run']['text']}")
    return limits, notes, x


class DisinfectStatus:
    """Anzeige-Teil von ``DisinfectControl`` – braucht dessen ``state``, ``store``, ``eng``."""

    def log_rows(self, limit: int = 10) -> list[dict]:
        """Die letzten Zeilen der Desinfektion im Protokoll, neueste zuerst."""
        marks = ", ".join("?" * len(ACTIONS))
        rows = self.store.query(f"SELECT ts, room, action, value, reason FROM control_log WHERE action IN ({marks}) "
                                "ORDER BY ts DESC, rowid DESC LIMIT ?", (*ACTIONS, int(limit)))
        return [{**dict(r), "time": local(r["ts"]).isoformat(), "kind": "dhw", "name": LOG_NAME} for r in rows]

    def as_dict(self, now_ts: int | None = None) -> dict:
        """Stand des Auslösens mit Protokoll (``disinfect`` in ``GET api/dhw``)."""
        return {**info(self.eng, self, now_ts), "log": self.log_rows()}
