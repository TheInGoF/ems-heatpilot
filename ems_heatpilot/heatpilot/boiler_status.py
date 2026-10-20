"""Anzeige von Weg B (seit 0.41): Statuszeile, Hinweise, Protokoll, Status und Bereitschaft – nur lesen.

``BoilerStatus`` ist ein Mixin für ``boiler_control.BoilerControl`` (wie ``dhw_status`` für Weg W): Es liest den Zustand
und baut daraus, was Heizen, Übersicht und Einstellungen zeigen (``as_dict`` für ``GET api/boiler`` und ``boiler`` in
``api/heat``) – Statuszeile „Kessel: HeatPilot · Vorlauf 42 °C (Wz hält 21 °C) · Boost +4 K“ bzw. Trockenlauf,
gesperrt (Regler am Bus), pausiert. ``ready`` ist der Block „Kessel über HeatPilot“ in „Bereit für die Steuerung?“:
Entitäten gefunden und tauglich, schreibbar laut EMS-ESP, Regler am Bus (sperrt), Wächter, letzte Prüfung, Pause,
fremder Schreiber. Hier stehen auch die Aktionen der Kessel-Zeilen im Protokoll (``control_log``).

Uhrzeiten gehen als ISO-Felder hinaus (der Browser setzt sie in seiner Zeitzone ein), Texte für die Oberfläche tragen
keine Uhrzeit des Add-ons.
"""

from __future__ import annotations

import time

from . import boiler_entity as be
from . import boiler_rule as br
from . import entity_guard as eg
from . import floor_limit, watchdog
from .boiler_rule import (CHECK_MAX_S, CHECK_S, CHECK_TOL_K, FOREIGN_MAX, FOREIGN_WINDOW_S, HINT_S, MAX_FAILS,
                          _ago, _iso, shown)
from .values import local, num

# Protokoll (control_log): Schreibvorgänge von Control (flow_set, heating_on/off) und die Zeilen des Ablaufs
ACT_FLOW, ACT_ON, ACT_OFF = "flow_set", "heating_on", "heating_off"
ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME, ACT_FALLBACK = (
    "boiler_dry", "boiler_check_failed", "boiler_foreign", "boiler_paused", "boiler_resumed", "boiler_fallback")
ACTIONS = (ACT_FLOW, ACT_ON, ACT_OFF, ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME, ACT_FALLBACK)
LOG_ROOM, LOG_NAME = "boiler", "Kessel"


def worst_cap(eng) -> float | None:
    """Strengste Fußboden-Grenze (gilt ohne HeatPilot immer – für Rückfallwert und Wächter)."""
    return floor_limit.cap_of(floor_limit.floor_rooms(eng.cfg, eng.found.rooms), {})[0]


def _flow_entity(eng) -> str | None:
    ctl = getattr(eng, "boiler_control", None)
    return ctl.entities["flow"] if ctl is not None else be.flow_target(eng.cfg, eng.found, eng.states)[0]


def fallback(eng) -> int | None:
    """Rückfall-Vorlauf, wie HeatPilot ihn beim Zurückschalten (und ohne rechenbares Ziel) schreibt und der Wächter ihn
    setzen soll – höchstens die strengste Fußboden-Grenze und der Höchstwert der Entität."""
    eid = _flow_entity(eng)
    return br.fallback_value(eng.cfg, worst_cap(eng), be.bounds(eng.states.get(eid) if eid else None))


def _times(d: dict | None) -> dict | None:
    return None if not d else {**d, "time": _iso(d["ts"])}


def _first(reason: str) -> str:
    return (reason or "").split(" · ")[0]


class BoilerStatus:
    """Anzeige-Teil von ``BoilerControl`` – braucht dessen ``state``, ``view``, ``mode``, ``entities``, ``current``."""

    def log_rows(self, limit: int = 10) -> list[dict]:
        """Die letzten Kessel-Zeilen des Protokolls, neueste zuerst."""
        marks = ", ".join("?" * len(ACTIONS))
        rows = self.store.query(f"SELECT ts, room, action, value, reason FROM control_log WHERE action IN ({marks}) "
                                "ORDER BY ts DESC, rowid DESC LIMIT ?", (*ACTIONS, int(limit)))
        return [{**dict(r), "time": local(r["ts"]).isoformat(), "kind": "boiler", "name": LOG_NAME} for r in rows]

    def warnings(self, now_ts: int) -> list[str]:
        """Hinweise: Pause, Fehlschlag, fremder Schreiber, gesperrt, Zurückschalten, Wächter – ohne Uhrzeit."""
        st, mode, out = self.state, self.mode, []
        lc, paused, fails = st.get("last_check"), st.get("paused"), int(st.get("fails", 0))
        if mode == "heatpilot" and paused:
            out.append(f"Kessel über HeatPilot pausiert ({paused['reason']}) – prüfen und „wieder einschalten“")
        elif mode == "heatpilot" and fails and lc and not lc.get("ok"):
            out.append(f"{br.LABELS[lc['what']]} {shown(lc['what'], lc['value'])} nicht übernommen ({lc.get('shown')}) "
                       f"– Fehlschlag {fails} von {MAX_FAILS}")
        if mode == "heatpilot" and self.view.get("blocked") and not paused:
            out.append(f"HeatPilot schreibt den Kessel nicht: {self.view['blocked']}")
        out += self._extra_warnings(now_ts)
        last = st.get("foreign_last")
        if mode == "heatpilot" and last and now_ts - last["ts"] < HINT_S:
            what = last["what"]
            out.append(f"Anderer Schreiber am Kessel (zuletzt {br.LABELS[what]} {shown(what, last['from'])} → "
                       f"{shown(what, last['to'])}, {_ago(now_ts - last['ts'])}) – Regler noch am Bus oder eine "
                       "Automation?")
        stop = st.get("stopped")
        if mode != "heatpilot" and stop and now_ts - stop["ts"] < HINT_S:
            fb = (st.get("fallback") or {}).get("text") or "nichts zurückgeschrieben"
            out.append(f"HeatPilot schreibt den Kessel nicht mehr ({fb}) – {br.HINT_BACK}")
        if mode != "aus":
            w = watchdog.status(self.eng, mode)
            if w["state"] in ("fehlt", "aus"):
                out.append(w["text"])
        return out

    def _extra_warnings(self, now_ts: int) -> list[str]:
        """Hinweise seit der Prüfung von 0.41: abgewiesen (Control), kein Ziel rechenbar, Eingriff des Wächters."""
        st, out = self.state, []
        rej = st.get("rejected")
        if self.mode == "heatpilot" and rej:
            out.append(f"HeatPilot darf {br.LABELS[rej['what']]} {shown(rej['what'], rej['value'])} nicht schreiben "
                       f"({rej['text']}, zuerst {_ago(now_ts - rej['ts'])})")
        blind = st.get("blind")
        if blind:
            fb = fallback(self.eng)
            late = now_ts - blind["since"] >= br.BLIND_S
            dry = " (Beobachten: nur im Protokoll)" if self.mode != "heatpilot" else ""
            out.append(f"Kein Vorlauf-Soll rechenbar ({blind.get('why')}) – " + (
                (f"Kessel auf Rückfall-Vorlauf {fb} °C und Heizbetrieb an{dry}" if fb is not None else
                 "Rückfall-Vorlauf läge unter 35 °C, HeatPilot schreibt nichts") if late else
                f"nach {br.BLIND_S // 60} min stellt HeatPilot den Rückfall-Vorlauf ein{dry}"))
        hit = st.get("watchdog_hit")
        if self.mode == "heatpilot" and hit and now_ts - hit["ts"] < HINT_S:
            out.append(f"Der Wächter in Home Assistant hat {_ago(now_ts - hit['ts'])} eingegriffen (HeatPilot lief "
                       f"{hit['gap_min']} min nicht) – HeatPilot führt den Kessel wieder")
        return out

    def _status(self) -> str:
        """Statuszeile (ohne Uhrzeiten)."""
        mode, st, v = self.mode, self.state, self.view
        if mode == "aus":
            return "Kessel: aus – Regler bzw. Kessel regeln selbst"
        if mode == "heatpilot" and self.paused():
            return f"Kessel: pausiert – {st['paused']['reason']}"
        if mode == "heatpilot" and v.get("blocked"):
            return f"Kessel: gesperrt – {v['blocked']}"
        t = v.get("target")
        lead, dry = ("Kessel: HeatPilot", "") if mode == "heatpilot" else ("Kessel: Beobachten", "würde ")
        if t is None:
            return f"{lead} – noch nicht gerechnet"
        if t.heating is None:
            return f"{lead} · kein Vorlauf-Soll – {t.reason}"
        if not t.heating:
            return f"{lead} · {dry}Heizbetrieb aus{' schalten' if dry else ''} ({_first(t.reason)})"
        if getattr(t, "idle", False):  # 0.46.5: kein Bedarf – Heizbetrieb bleibt an, nur die Pumpe läuft
            return f"{lead} · kein Bedarf – " + (f"würde Vorlauf-Soll {t.flow} °C setzen, Pumpe läuft weiter" if dry
                                                 else f"Pumpe läuft, Vorlauf-Soll {t.flow} °C")
        parts = [f"{lead} · {dry}Vorlauf {t.flow} °C{' setzen' if dry else ''} ({_first(t.reason)})"]
        if t.load is not None and t.load > (t.base or 0):
            parts.append(f"Abnahme mind. {t.load} °C")
        if t.boost_k:
            parts.append(f"Boost +{t.boost_k} K")
        if t.limited and t.cap is not None:
            parts.append(f"begrenzt auf {br._c(t.cap)} °C (Fußboden)")
        if t.charging:
            parts.append("Warmwasser lädt – Vorlauf danach")
        return " · ".join(parts)

    def _target(self) -> dict | None:
        t = self.view.get("target")
        if t is None:
            return None
        return {"heating": t.heating, "flow": t.flow, "reason": t.reason, "base": t.base, "load": t.load,
                "load_text": t.load_text, "boost_k": t.boost_k, "boost_wanted": t.boost_wanted,
                "waiting": list(t.waiting), "cycling": t.cycling, "cap": t.cap, "cap_why": t.cap_why,
                "limited": t.limited, "charging": t.charging, "t_out": t.t_out, "idle": getattr(t, "idle", False)}

    def _entity(self, what: str) -> dict:
        eid = self.entities[what]
        name = ((self.eng.states.get(eid) or {}).get("attributes") or {}).get("friendly_name") if eid else None
        return {"entity": eid, "source": self.sources[what], "name": name or eid, "current": self.current(what)}

    def as_dict(self, now_ts: int | None = None) -> dict:
        """Status für Heizen, Übersicht und Einstellungen (``GET api/boiler``, ``boiler`` in ``api/heat``)."""
        now_ts = int(time.time()) if now_ts is None else int(now_ts)
        st, mode, v = self.state, self.mode, self.view
        paused, lc, fl, boost = st.get("paused"), st.get("last_check"), st.get("foreign_last"), st.get("boost") or {}
        foreign = st.get("foreign") or []
        lo, hi = br.flow_limits(self.eng.cfg, None, be.bounds(self.eng.states.get(self.entities["flow"] or "")))
        flow_ent, heat_ent = self.entities["flow"], self.entities["heating"]
        fb = fallback(self.eng)
        return {
            "mode": mode, "modes": list(br.BOILER_MODES), "writes": mode == "heatpilot",
            "active": mode == "heatpilot" and not paused and not v.get("blocked") and bool(flow_ent and heat_ent),
            "blocked": v.get("blocked") if mode == "heatpilot" else None,
            "regulator": be.regulator(self.eng),
            "entities": {w: self._entity(w) for w in ("flow", "heating")},
            "target": self._target(),
            "decision": v.get("decision"), "decision_until": {w: _iso(t) for w, t in (v.get("until") or {}).items()},
            "boost": {"k": int(boost.get("k") or 0), "changed": _iso(boost.get("ts")), "wanted": bool(boost.get("on"))},
            "last_write": {w: _times(self._ch(w).get("last")) for w in ("flow", "heating")},
            "check_due": {w: _iso((self._ch(w).get("check") or {}).get("due")) for w in ("flow", "heating")},
            "last_check": _times(lc), "fails": int(st.get("fails", 0)), "paused": bool(paused),
            "paused_since": _iso(paused["ts"]) if paused else None, "pause_reason": (paused or {}).get("reason"),
            "foreign_last": _times(fl), "foreign_1h": sum(1 for t in foreign if t > now_ts - FOREIGN_WINDOW_S),
            "foreign_24h": sum(1 for t in foreign if t > now_ts - HINT_S),
            "dry_last": {w: _times(self._ch(w).get("dry")) for w in ("flow", "heating")},
            "fallback": {"setting": self.eng.cfg.boiler_fallback_flow, "value": fb, "last": _times(st.get("fallback"))},
            "rejected": _times(st.get("rejected")), "blind": _times(st.get("blind") and {
                **st["blind"], "ts": st["blind"]["since"]}), "watchdog_hit": _times(st.get("watchdog_hit")),
            "watchdog": {**watchdog.status(self.eng, mode),
                         "guide": watchdog.guide(flow_ent, heat_ent, fb, self.eng.cfg.notify_targets, worst_cap(self.eng))},
            "heartbeat": {"entity": watchdog.HEARTBEAT, "last": _iso(getattr(self.eng, "heartbeat_ts", None))},
            "status_text": self._status(), "warnings": self.warnings(now_ts),
            "notes": [br.HINT_FAILSAFE, "Die Antipendelzeit des Kessels bleibt, wie sie ist"],
            "rules": {"flow_min": lo, "flow_max": hi, "step_k": br.STEP_K, "flow_gap_s": br.FLOW_GAP_S,
                      "pump_flow": br.PUMP_FLOW, "idle": self.eng.cfg.boiler_idle,  # 0.46.5: kein Bedarf
                      "heat_hold_min": br.HEAT_HOLD_S // 60, "heat_off_min": br.HEAT_OFF_S // 60,
                      "flow_down_min": br.FLOW_DOWN_S // 60, "blind_min": br.BLIND_S // 60,
                      "check_s": CHECK_S, "check_max_s": CHECK_MAX_S,
                      "tolerance_k": CHECK_TOL_K, "max_fails": MAX_FAILS, "foreign_max": FOREIGN_MAX,
                      "foreign_window_min": FOREIGN_WINDOW_S // 60, "boost_step_k": br.BOOST_STEP_K,
                      "boost_max_k": br.BOOST_MAX_K, "boost_step_min": br.BOOST_STEP_S // 60},
            "log": self.log_rows(),
        }


# ------------------------------------------------------------------ Bereit für die Steuerung?
def _entity_info(eng, what: str, entity: str | None, source: str | None) -> tuple[dict, list[str], list[str]]:
    """Angaben einer Entität (Name, Wert, Grenzen, schreibbar laut EMS-ESP), Hindernisse, Einschränkungen."""
    state = eng.states.get(entity) if entity else None
    attrs = (state or {}).get("attributes") or {}
    problem = (be.flow_problem if what == "flow" else be.heat_problem)(entity, state, eng.found)
    doubt = (be.flow_doubt if what == "flow" else be.heat_doubt)(entity, state) if problem is None else None
    key = be.EMS_FLOW if what == "flow" else be.EMS_HEAT
    ems = eg.ems_control(eng, key)
    info = {"entity": entity, "source": source, "name": attrs.get("friendly_name") or entity, "ok": problem is None,
            "problem": problem, "doubt": doubt, "state": (state or {}).get("state"),
            "unit": attrs.get("unit_of_measurement"),
            "min": num(attrs.get("min")), "max": num(attrs.get("max")), "ems_key": key, "writeable": ems["writeable"],
            "writeable_polled": ems["polled"]}
    blocked = eg.ems_write_blocked(eng, key, be.FLOW_WHAT if what == "flow" else be.HEAT_WHAT)
    return info, [p for p in (problem, blocked) if p], [doubt] if doubt else []


def ready(eng, now_ts: int) -> dict:
    """Block „Kessel über HeatPilot“ (Weg B) – nur Anzeige, ändert die Stufe der Raumsteuerung nicht."""
    cfg, ctl = eng.cfg, getattr(eng, "boiler_control", None)
    if ctl is not None:
        ents = {w: (ctl.entities[w], ctl.sources[w]) for w in ("flow", "heating")}
    else:
        ents = {"flow": be.flow_target(cfg, eng.found, eng.states),
                "heating": be.heat_target(cfg, eng.found, eng.states)}
    st = ctl.state if ctl is not None else {}
    flow, b1, l1 = _entity_info(eng, "flow", *ents["flow"])
    heat, b2, l2 = _entity_info(eng, "heating", *ents["heating"])
    reg = be.regulator(eng)
    blockers = ([] if reg["present"] is False else [reg["text"]]) + b1 + b2
    off, twin = be.heating_off(eng), be.same_entity(cfg, eng.found, eng.states)
    blockers += [p for p in (off, twin) if p]
    limits, notes = l1 + l2, []
    paused, lc = st.get("paused"), st.get("last_check")
    if paused:
        blockers.append(f"Pausiert ({paused['reason']}) – prüfen und „wieder einschalten“")
    if lc and not lc.get("ok"):
        limits.append(f"Letzte Prüfung: {br.LABELS[lc['what']]} {shown(lc['what'], lc['value'])} nicht übernommen")
    foreign = [t for t in st.get("foreign") or [] if t > now_ts - HINT_S]
    if foreign:
        limits.append(f"Fremde Änderungen am Kessel in den letzten 24 h: {len(foreign)} – Regler noch am Bus oder eine "
                      "Automation?")
    w = watchdog.status(eng, cfg.boiler_control)
    if w["state"] != "erkannt":
        limits.append(w["text"])
    from .boiler_flow import min_power_kw  # spät: boiler_flow nutzt die Vorschau
    kw, why = min_power_kw(eng)
    if kw is None:
        limits.append(why)
    for ent in (flow, heat):
        if ent["writeable"] is None and ent["ok"]:
            notes.append(f"Ob EMS-ESP {ent['ems_key']} schreiben lässt, ist unbekannt – die Prüfung nach 60 s zeigt es")
    floor = floor_limit.status(cfg, eng.found.rooms, eng.states)
    notes += floor["notes"][:1]
    fb = fallback(eng)
    notes.append(f"Rückfall-Vorlauf {fb} °C (Zurückschalten, Wächter)" if fb is not None else
                 "Rückfall-Vorlauf läge unter 35 °C (Fußboden-Grenze) – beim Zurückschalten schreibt HeatPilot nichts")
    notes.append(br.HINT_FAILSAFE)
    mode, writes = cfg.boiler_control, cfg.writes_boiler()
    level = "nicht bereit" if blockers else ("eingeschränkt" if limits else "bereit")
    if blockers:
        headline = f"Nicht bereit – {blockers[0]}."
    elif limits:
        headline = ("Läuft – eingeschränkt: " if writes else "Eingeschränkt bereit – ") + limits[0] + "."
    else:
        headline = (f"{'Läuft' if writes else 'Bereit'} – {flow['name']} und {heat['name']} schreibbar, kein Regler "
                    "am Bus.")
    fl = st.get("foreign_last")
    return {"level": level, "headline": headline, "mode": mode, "writes": writes, "flow": flow, "heating": heat,
            "regulator": reg, "heating_off": off, "watchdog": w, "fallback": fb,
            "last_check": _times(lc), "paused": bool(paused), "pause_reason": (paused or {}).get("reason"),
            "paused_since": _iso(paused["ts"]) if paused else None, "foreign_last": _times(fl),
            "foreign_24h": len(foreign), "min_power_kw": None if kw is None else round(kw, 1),
            "blockers": blockers, "limits": limits, "notes": notes}
