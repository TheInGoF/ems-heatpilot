"""Anzeige von Weg W (seit 0.38): Statuszeile, Hinweise, Protokoll und Status für die Oberfläche – nur lesen.

``DhwStatus`` ist ein Mixin für ``dhw_control.DhwControl`` (wie die ``engine_*``-Mixins der Engine): Es liest den
Zustand von Weg W und baut daraus, was Heizen, Übersicht und Einstellungen zeigen (``as_dict`` für ``GET api/dhw``,
``dhw`` in ``api/heat``). Hier stehen auch die Aktionen der Warmwasser-Zeilen im Protokoll (``control_log``).

Uhrzeiten: Texte für die Oberfläche tragen keine Uhrzeit des Add-ons – sonst stünde dieselbe Pause mit zwei
Uhrzeiten da, wenn Browser und Add-on in verschiedenen Zeitzonen laufen. Zeiten gehen als ISO-Felder hinaus
(``paused_since``, ``decision_until`` …), die Statuszeile als Vorlage mit Platzhaltern (``status``), der fremde
Schreiber als Abstand („vor 3 h“). ``status_text`` mit Uhrzeiten des Add-ons bleibt für Diagnose und API.
"""

from __future__ import annotations

import logging
import time

from .config import DHW_MODES
from .dhw_rule import (CHECK_MAX_S, CHECK_S, CHECK_TOL_K, DAY_MAX, FOREIGN_MAX, FOREIGN_WINDOW_S, HINT_AUTOMATION,
                       HINT_FAILSAFE, HINT_S, HOLD_S, MAX_FAILS, STEP_K, _ago, _c, _iso, limits)
from .values import local

log = logging.getLogger(__name__)

# Protokoll (control_log): Aktionen der Warmwasser-Zeilen; Zeile ohne Entität unter LOG_ROOM
ACT_WRITE, ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME = (
    "set_value", "dry_run", "check_failed", "foreign", "paused", "resumed")
ACTIONS = (ACT_WRITE, ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME)
LOG_ROOM, LOG_NAME = "dhw", "Warmwasser-Soll"


def _fill(text: str, times: dict[str, str]) -> str:
    """Platzhalter der Statuszeile mit der Uhrzeit des Add-ons füllen (ISO-Zeit, Stunde:Minute)."""
    for key, iso in times.items():
        text = text.replace("{" + key + "}", iso[11:16])
    return text


class DhwStatus:
    """Anzeige-Teil von ``DhwControl`` – braucht dessen ``state``, ``view``, ``mode``, ``readings``/``current``."""

    def log_rows(self, limit: int = 10) -> list[dict]:
        """Die letzten Warmwasser-Zeilen des Protokolls, neueste zuerst."""
        marks = ", ".join("?" * len(ACTIONS))
        rows = self.store.query(f"SELECT ts, room, action, value, reason FROM control_log WHERE action IN ({marks}) "
                                "ORDER BY ts DESC, rowid DESC LIMIT ?", (*ACTIONS, int(limit)))
        return [{**dict(r), "time": local(r["ts"]).isoformat(), "kind": "dhw", "name": LOG_NAME} for r in rows]

    def next_change(self, now_ts: int) -> dict | None:
        """Nächster Schreibvorgang laut Vorschau (dieselbe Regel) – None bei „aus“, Pause oder ohne Vorschau."""
        if self.mode == "aus" or self.paused():
            return None
        try:
            from . import preview  # spät: preview nutzt dhw_control
            p = preview.cached(self.eng, now_ts)
        except Exception as err:  # noqa: BLE001 – nur Anzeige
            log.debug("Vorschau für das Warmwasser-Soll nicht verfügbar: %s", err)
            return None
        w = next((w for h in p.get("hours") or [] for w in h.get("writes") or []
                  if w.get("what") == "dhw" and w.get("ts", 0) > now_ts), None)
        return {"time": w["t"], "value": w["value"]} if w else None

    def warnings(self, now_ts: int) -> list[str]:
        """Hinweise für Heizen, Übersicht und Einstellungen: Fehlschlag, Pause, fremder Schreiber, Zurückschalten –
        ohne Uhrzeit (die Pause zeigt die Oberfläche mit ``paused_since``, der fremde Schreiber steht als Abstand)."""
        st, mode, out = self.state, self.mode, []
        chk, paused, fails = st.get("last_check"), st.get("paused"), int(st.get("fails", 0))
        if mode == "heatpilot" and paused:
            out.append(f"Warmwasser über HeatPilot pausiert ({paused['reason']}) – prüfen und „wieder einschalten“")
        elif mode == "heatpilot" and fails and chk and not chk.get("ok"):
            seen = chk.get("shown") or (f"Kessel zeigt {_c(chk['seen'])} °C" if chk.get("seen") is not None
                                        else "Kessel-Soll unbekannt")
            then = ("Kessel-Soll passt inzwischen zur Empfehlung, kein neuer Versuch nötig" if st.get("fail_settled")
                    else "nächster Versuch nach der Haltezeit")
            out.append(f"Warmwasser-Soll {chk['value']} °C nicht übernommen ({seen}) – Fehlschlag {fails} von "
                       f"{MAX_FAILS}, {then}")
        last = st.get("foreign_last")
        if mode == "heatpilot" and last and now_ts - last["ts"] < HINT_S:
            out.append(f"Anderer Schreiber aktiv (zuletzt {_c(last['from'])} → {_c(last['to'])} °C, "
                       f"{_ago(now_ts - last['ts'])}) – {HINT_AUTOMATION}")
        stop = st.get("stopped")
        if mode != "heatpilot" and stop and now_ts - stop["ts"] < HINT_S:
            kept = f"{stop['value']} °C" if stop.get("value") is not None else "das letzte Soll"
            out.append(f"HeatPilot schreibt das Warmwasser-Soll nicht mehr – der Kessel behält {kept}; die "
                       "HA-Automation ggf. wieder einschalten")
        return out

    def _status(self, nxt: dict | None = None) -> tuple[str, dict[str, str]]:
        """Statuszeile als Vorlage mit Platzhaltern für Uhrzeiten (``{since}``, ``{next}``) und deren ISO-Zeiten – die
        Oberfläche setzt sie in der Zeitzone des Browsers ein, ``status_text`` in der des Add-ons."""
        mode, st, times = self.mode, self.state, {}
        if mode == "aus":
            return "Warmwasser-Soll: aus – HeatPilot empfiehlt nur", times
        if mode == "heatpilot" and not self.entity_id:
            return "Warmwasser-Soll: keine Entität – in den Einstellungen wählen", times
        if mode == "heatpilot" and self.paused():
            return f"Warmwasser über HeatPilot pausiert – {st['paused']['reason']}", times
        r = self.readings()
        cur = self.current(r)
        if mode == "trocken":
            dry = st.get("dry")
            times.update({"since": _iso(dry["ts"])} if dry else {})
            parts = ["Trockenlauf", f"würde {dry['value']} °C seit {{since}}" if dry else f"Kessel-Soll {_c(cur)} °C"]
        else:
            last = st.get("last")
            parts = ["HeatPilot setzt"]
            if last and cur is not None and abs(cur - last["value"]) <= CHECK_TOL_K:
                times["since"] = _iso(last["ts"])
                parts.append(f"{last['value']} °C seit {{since}}")
            elif cur is not None:
                parts.append(f"Kessel zeigt {_c(cur)} °C")
            elif r["unclear"]:
                parts.append("Kessel-Soll unklar")
        if self.view.get("special"):
            parts.append("Sonderladung läuft – danach")
        if nxt:
            times["next"] = nxt["time"]
            parts.append(f"nächste Änderung {{next}} → {nxt['value']} °C")
        return " · ".join(parts), times

    def status_text(self, now_ts: int, nxt: dict | None = None) -> str:
        """Statuszeile: „HeatPilot setzt · 50 °C seit 15:03 · nächste Änderung 17:30 → 55 °C“ bzw. Trockenlauf/pausiert
        – mit Uhrzeiten des Add-ons (Diagnose, API); die Oberfläche nutzt die Vorlage aus ``as_dict``."""
        return _fill(*self._status(nxt))

    def as_dict(self, now_ts: int | None = None) -> dict:
        """Status für Heizen, Übersicht und Einstellungen (``GET api/dhw``, ``dhw`` in ``api/heat``). Uhrzeiten als
        ISO-Felder; ``status`` ist die Statuszeile als Vorlage dafür."""
        now_ts = int(time.time()) if now_ts is None else int(now_ts)
        st, mode, v = self.state, self.mode, self.view
        last, chk, lc, paused = st.get("last"), st.get("check"), st.get("last_check"), st.get("paused")
        hold = last["ts"] + HOLD_S if last and mode == "heatpilot" and now_ts < last["ts"] + HOLD_S else None
        foreign, fl = st.get("foreign") or [], st.get("foreign_last")
        lo, hi = limits(self.eng.cfg)
        nxt = self.next_change(now_ts)
        tpl, times = self._status(nxt)
        name = ((self.eng.states.get(self.entity_id) or {}).get("attributes") or {}).get("friendly_name")
        return {
            "mode": mode, "modes": list(DHW_MODES),
            "active": mode == "heatpilot" and not paused and bool(self.entity_id),
            "writes": mode == "heatpilot",
            "entity": self.entity_id, "entity_source": self.entity_source, "entity_name": name or self.entity_id,
            "current": self.current(), "wish": v.get("wish"), "reason": v.get("reason"),
            "decision": v.get("decision"), "decision_until": _iso(v.get("until")), "special": bool(v.get("special")),
            "last_write": None if not last else {**last, "time": _iso(last["ts"])},
            "hold_until": _iso(hold), "check_due": _iso(chk["due"]) if chk else None,
            "last_check": None if not lc else {**lc, "time": _iso(lc["ts"])},
            "fails": int(st.get("fails", 0)), "paused": bool(paused),
            "paused_since": _iso(paused["ts"]) if paused else None, "pause_reason": (paused or {}).get("reason"),
            "foreign_last": None if not fl else {**fl, "time": _iso(fl["ts"])},
            "foreign_1h": sum(1 for t in foreign if t > now_ts - FOREIGN_WINDOW_S),
            "foreign_24h": sum(1 for t in foreign if t > now_ts - HINT_S),
            "writes_24h": len(self._writes(now_ts)),
            "dry_last": None if not st.get("dry") else {**st["dry"], "time": _iso(st["dry"]["ts"])},
            "next_change": nxt, "status_text": _fill(tpl, times), "status": {"text": tpl, "times": times},
            "warnings": self.warnings(now_ts), "notes": [HINT_AUTOMATION, HINT_FAILSAFE],
            "rules": {"min": lo, "max": hi, "step_k": STEP_K, "hold_min": HOLD_S // 60, "check_s": CHECK_S,
                      "check_max_s": CHECK_MAX_S, "tolerance_k": CHECK_TOL_K, "max_fails": MAX_FAILS,
                      "foreign_max": FOREIGN_MAX, "foreign_window_min": FOREIGN_WINDOW_S // 60, "day_max": DAY_MAX},
            "log": self.log_rows(),
        }
