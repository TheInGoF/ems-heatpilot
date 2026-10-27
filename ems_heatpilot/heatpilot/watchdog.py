"""Wächter in Home Assistant (Weg B, seit 0.41): Heartbeat-Sensor, Erkennung der Wächter-Automation, Anleitung.

Fällt HeatPilot aus, bleibt der letzte Vorlauf am Kessel stehen; ohne Wärmeabnahme fällt der Kessel zurück. Einen
festen Vorlauf stellt nur der Drehschalter am Kessel ein, nicht Home Assistant – deshalb meldet der Wächter seit 0.59
nur noch: eine Automation, die du selbst anlegst (HeatPilot schlägt sie als fertiges YAML vor, ``automation_yaml``):
„Wenn ``sensor.heatpilot_heartbeat`` seit 60 min unverändert ist und HeatPilot den Kessel geführt hat (Attribut
``kessel`` ist „heatpilot“) → Meldung aufs Handy“. Die Notfallanleitung (Drehschalter auf Festwert) steht in der Doku.

* ``publish`` – HeatPilot setzt ``sensor.heatpilot_heartbeat`` (Zeitstempel) jede Minute (``main.live_loop``).
* ``scan`` – liest die Automationen (Zustände ``automation.*`` und je Automation ihre Konfiguration über die
  WebSocket-Abfrage ``automation/config`` – nur lesen) und sucht die, die den Heartbeat-Sensor als Auslöser haben;
  alle 10 min, solange „Kessel über HeatPilot“ nicht aus ist. Dazu, ob sie das Vorlauf-Soll setzt und die Bedingung
  auf das Attribut ``kessel`` hat.
* ``status`` – erkannt, ausgeschaltet, nicht erkannt oder unbekannt – für „Bereit für die Steuerung?“ (nicht erkannt
  ist eine Einschränkung, kein Hindernis).
* ``guide`` – die Anleitung Schritt für Schritt mit den Werten zum Abtippen (Knopf „Anleitung Wächter“), dazu das
  YAML der Automation mit den gewählten Entitäten (``automation_yaml``).
"""

from __future__ import annotations

import json
import logging
import time

from . import __version__
from .values import local

log = logging.getLogger(__name__)

HEARTBEAT = "sensor.heatpilot_heartbeat"
EVERY_S = 60           # Heartbeat so oft
SCAN_EVERY_S = 600     # Automationen so oft lesen …
SCAN_MAX_S = 60        # … höchstens so lange
STALE_MIN = 60         # die Automation greift, wenn der Heartbeat so lange unverändert ist
NAME = "HeatPilot-Wächter"
MODE_ATTR, MODE_VALUE = "kessel", "heatpilot"  # Bedingung: HeatPilot hat den Kessel geführt
TEXT = (f"HeatPilot antwortet seit {STALE_MIN} min nicht – Kessel prüfen; wenn es kalt wird, Drehschalter am Kessel "
        "auf Festwert stellen (Notfallanleitung in der Doku)")


async def publish(ha, now: float, mode: str) -> None:
    """Heartbeat setzen: Zeitstempel als Zustand (Geräteklasse timestamp), dazu der Schalter."""
    await ha.set_state(HEARTBEAT, local(now).isoformat(timespec="seconds"), {
        "friendly_name": "HeatPilot Heartbeat", "device_class": "timestamp", "icon": "mdi:heart-pulse",
        "kessel": mode, "version": __version__,
        "hinweis": f"Ändert sich jede Minute. Seit {STALE_MIN} min unverändert = HeatPilot läuft nicht.",
    })


def _triggers(config: dict) -> object:
    return config.get("triggers", config.get("trigger")) or []


def _conditions(config: dict) -> object:
    return config.get("conditions", config.get("condition")) or []


async def scan(ha, states: dict) -> dict:
    """Automationen mit dem Heartbeat als Auslöser (nur lesen). {ts, found: [{entity_id, name, on, checks_mode}],
    error, checked}; eine Automation, deren Konfiguration nicht lesbar ist, zählt als nicht gefunden."""
    found, errors = [], []
    for eid, st in sorted((states or {}).items()):
        if not eid.startswith("automation."):
            continue
        try:
            config = (await ha.automation_config(eid) or {}).get("config") or {}
        except Exception as err:  # noqa: BLE001 – eine nicht lesbare Automation hält die anderen nicht auf
            errors.append(f"{eid}: {err or type(err).__name__}")
            continue
        if HEARTBEAT not in json.dumps(_triggers(config), ensure_ascii=False, default=str):
            continue
        conds = json.dumps(_conditions(config), ensure_ascii=False, default=str)
        found.append({"entity_id": eid, "name": (st.get("attributes") or {}).get("friendly_name") or eid,
                      "on": st.get("state") == "on", "checks_mode": MODE_ATTR in conds and MODE_VALUE in conds})
    autos = sum(1 for e in states or {} if e.startswith("automation."))
    error = None if not errors or len(errors) < autos else f"Automationen nicht lesbar ({errors[0]})"
    return {"ts": int(time.time()), "found": found, "error": error, "checked": autos}


def status(eng, mode: str) -> dict:
    """Stand des Wächters für Anzeige und Bereitschaft: {state, text, automations, time} – state „erkannt“,
    „aus“ (Automation ausgeschaltet), „fehlt“, „unbekannt“ (noch nicht gelesen bzw. nicht lesbar)."""
    w = getattr(eng, "watchdog", None) or {}
    found = w.get("found") or []
    base = {"automations": found, "time": local(w["ts"]).isoformat() if w.get("ts") else None, "entity": HEARTBEAT}
    if mode == "aus" and not w:
        return {**base, "state": "unbekannt",
                "text": "Wächter wird geprüft, sobald „Kessel über HeatPilot“ nicht aus ist"}
    if not w or w.get("error"):
        why = w.get("error") or "Automationen noch nicht gelesen"
        return {**base, "state": "unbekannt", "text": f"Wächter unbekannt ({why})"}
    on = [a for a in found if a["on"]]
    if on:
        a = on[0]
        miss = ""
        if a.get("checks_mode") is False:
            miss = f" – Bedingung „{MODE_ATTR} ist {MODE_VALUE}“ fehlt (Anleitung Wächter)"
        return {**base, "state": "erkannt", "text": f"Wächter erkannt: {a['name']}{miss}"}
    if found:
        return {**base, "state": "aus", "text": f"Wächter {found[0]['name']} ist ausgeschaltet – in Home Assistant "
                                                "einschalten"}
    return {**base, "state": "fehlt", "text": "Wächter nicht erkannt – keine Automation mit sensor.heatpilot_heartbeat "
                                              "als Auslöser (Anleitung Wächter)"}


def _notify_yaml(targets, text: str) -> list[str]:
    """Aktionen für die Meldung: Handy-App-Dienst direkt, notify-Entität über ``notify.send_message``, ohne Ziel eine
    Meldung in Home Assistant (kommt immer an)."""
    if not targets:
        return ["  - action: persistent_notification.create", "    data:", f'      title: "{NAME}"', f'      message: "{text}"']
    out = []
    for t in targets:
        if t.startswith("notify.mobile_app_") or "." not in t:
            out += [f"  - action: {t}", "    data:", f'      title: "{NAME}"', f'      message: "{text}"']
        else:
            out += ["  - action: notify.send_message", "    target:", f"      entity_id: {t}", "    data:", f'      message: "{text}"']
    return out


def automation_yaml(targets) -> str:
    """Die Wächter-Automation als YAML (Einstellungen › Automationen › Neu › ⋮ › „In YAML bearbeiten“, einfügen)."""
    lines = [
        f"alias: {NAME}",
        f'description: "Meldet, wenn HeatPilot {STALE_MIN} min nicht antwortet"',
        "mode: single",
        "triggers:",
        "  - trigger: state",
        f"    entity_id: {HEARTBEAT}",
        f'    for: "01:00:00"',
        "conditions:",
        "  - condition: state",
        f"    entity_id: {HEARTBEAT}",
        f"    attribute: {MODE_ATTR}",
        f"    state: {MODE_VALUE}",
        "actions:",
    ]
    return "\n".join(lines + _notify_yaml(list(targets or ()), TEXT)) + "\n"


def guide(targets=()) -> dict:
    """Anleitung für die Wächter-Automation in der Oberfläche von Home Assistant, dazu als YAML."""
    notify = ", ".join(targets) if targets else "dein Handy (notify.mobile_app_…)"
    condition = f"Attribut „{MODE_ATTR}“ von {HEARTBEAT} ist „{MODE_VALUE}“"
    steps = [
        "In Home Assistant „Einstellungen“ › „Automationen & Szenen“ › „Automation erstellen“ › „Neue Automation "
        "erstellen“.",
        f"„Auslöser hinzufügen“ › „Entität“ › „Zustand“: Entität „HeatPilot Heartbeat“ ({HEARTBEAT}), „Von“ und „Nach“ "
        f"leer lassen, „Für“ 01:00:00 ({STALE_MIN} min).",
        f"„Bedingung hinzufügen“ › „Entität“ › „Zustand“: Entität „HeatPilot Heartbeat“, Attribut „{MODE_ATTR}“, Zustand "
        f"„{MODE_VALUE}“ – der Wächter meldet nur, solange HeatPilot den Kessel führt (nicht nach dem Zurückschalten).",
        f"„Aktion hinzufügen“ › „Benachrichtigungen“ › {notify}: Nachricht „{TEXT}“.",
        f"Speichern, Name „{NAME}“. HeatPilot erkennt die Automation am Auslöser (bis zu 10 min später).",
    ]
    return {"title": "Anleitung Wächter", "name": NAME, "steps": steps, "yaml": automation_yaml(targets),
            "values": {"trigger_entity": HEARTBEAT, "for": "01:00:00", "condition": condition, "message": TEXT},
            "note": ("Nach einem Neustart von Home Assistant fehlt der Heartbeat, bis HeatPilot ihn wieder setzt "
                     "(höchstens 1 min) – läuft HeatPilot dann nicht, meldet der Wächter nicht. Prüfen: HeatPilot "
                     "anhalten und nach einer Stunde nachsehen, ob die Meldung kam.")}
