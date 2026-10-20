"""Prüfung einer Schreib-Entität am Kessel (seit 0.41 gemeinsam für Weg W und B): Vorschlag, falsche Rolle, Hindernis.

HeatPilot schreibt am Kessel nur auf Entitäten, die genau nach dem aussehen, was geschrieben wird – das Warmwasser-Soll
(``dhw_entity``, Weg W), das Vorlauf-Soll und den Heizbetrieb (``boiler_entity``, Weg B), seit 0.42 den Schalter der
Desinfektion (``disinfect_entity``). Was dafür bei allen gleich gilt, steht hier und wird mit der Rolle und einer
Beschriftung („das Warmwasser-Soll“) aufgerufen:

* ``suggest`` – Vorschlag aus der Erkennung: die Rolle, wenn sie in der richtigen Domäne liegt, sonst eine Entität mit
  einem der EMS-ESP-Namen der Rolle (``number.boiler_selflowtemp``, ``switch.ems_esp_boiler_heatingactivated`` …),
* ``wrong_role`` – gehört die Entität sichtbar zu etwas anderem: Heizkreis im Namen (``…_hc1_…``), andere erkannte
  Rolle oder Name einer anderen Rolle bzw. eines anderen bekannten Kesselwerts,
* ``state_problem`` – fehlt in Home Assistant oder ist nicht verfügbar; ``unit_problem`` – keine Temperatur in °C,
* ``ems_control``/``ems_write_blocked`` – lässt EMS-ESP die Kesselsteuerung schreiben (stündlich gelesen, nur lesen)?

Reine Funktionen, schreibt nichts.
"""

from __future__ import annotations

import re

from .discovery_roles import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES

UNITS = ("°C", "°c", "C")
# Heizkreis im Namen (number.thermostat_hc1_designtemp, …_hc2_…): nie ein Wert des Kessels
HEATING_CIRCUIT = re.compile(r"(^|_)hc\d+(_|$)")
OFF_STATES = (None, "unavailable", "unknown")


def other_names(role: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Name → Beschriftung aller anderen Rollen (Kessel, Regler) und der weiteren Kesselwerte ``extra``."""
    out: dict[str, str] = {}
    for roles in (BOILER_ROLES, THERMOSTAT_ROLES):
        for r, names in roles.items():
            if r != role:
                for name in names:
                    out.setdefault(name.lower(), ROLE_LABELS.get(r, r))
    return {**out, **(extra or {})}


def suggest(found, states: dict, role: str, domain: str) -> str | None:
    """Vorschlag aus der Erkennung: die Rolle, wenn sie in ``domain`` liegt, sonst eine Entität dieser Domäne mit einem
    der EMS-ESP-Namen der Rolle (mit optionalem Präfix vor ``boiler_``)."""
    hit = (getattr(found, "boiler", None) or {}).get(role)
    if hit and hit.startswith(domain + "."):
        return hit
    names = "|".join(re.escape(k) for k in BOILER_ROLES[role])
    pattern = re.compile(rf"^{re.escape(domain)}\.(.*_)?boiler_({names})$", re.IGNORECASE)
    hits = sorted(e for e in states or {} if pattern.match(e))
    return hits[0] if hits else None


def wrong_role(entity: str, found, role: str, names: dict[str, str], what: str) -> str | None:
    """Gehört die Entität sichtbar zu etwas anderem als ``what`` („das Vorlauf-Soll“)? Heizkreis im Namen, andere
    erkannte Rolle (``found``) oder Name einer anderen Rolle bzw. eines anderen Kesselwerts (``names``) – der Grund,
    sonst None."""
    obj = entity.split(".", 1)[-1].lower()
    if HEATING_CIRCUIT.search(obj):
        return f"{entity} gehört zu einem Heizkreis (z. B. Heizkurve des Reglers) – nicht {what}"
    for group in ("boiler", "thermostat"):
        for r, eid in (getattr(found, group, None) or {}).items():
            if eid == entity and r != role:
                return f"{entity} ist laut Erkennung „{ROLE_LABELS.get(r, r)}“ – nicht {what}"
    hit = next((n for n in names if obj == n or obj.endswith("_" + n)), None)
    if hit:
        return f"{entity} ist ein anderer Kesselwert („{names[hit]}“) – nicht {what}"
    return None


def state_problem(entity: str, state: dict | None) -> str | None:
    """Fehlt die Entität in Home Assistant oder ist sie nicht verfügbar?"""
    if not state:
        return f"{entity} fehlt in Home Assistant"
    if state.get("state") in OFF_STATES:
        return f"{entity} ist nicht verfügbar"
    return None


def unit_problem(entity: str, state: dict | None) -> str | None:
    """Nur eine Temperatur in °C: ohne Einheit nicht, mit anderer Einheit nicht."""
    unit = ((state or {}).get("attributes") or {}).get("unit_of_measurement")
    if unit is None or not str(unit).strip():
        return f"{entity} meldet keine Einheit – erlaubt ist nur eine Temperatur in °C"
    if str(unit).strip() not in UNITS:
        return f"{entity} ist keine Temperatur (Einheit {unit})"
    return None


def name_hint(entity: str, state: dict | None, pattern: re.Pattern) -> bool:
    """Deutet der Name der Entität oder ihr Anzeigename auf ``pattern`` (z. B. Warmwasser)?"""
    name = str(((state or {}).get("attributes") or {}).get("friendly_name") or "")
    return bool(pattern.search(entity.split(".", 1)[-1]) or pattern.search(name))


# ------------------------------------------------------------------ schreibbar laut Kesselsteuerungen
def ems_control(eng, key: str) -> dict:
    """Kesselsteuerung ``key`` laut EMS-ESP (stündlich gelesen, nur lesen): {polled, found, writeable, value}.
    ``writeable`` False = nicht schreibbar oder nicht gefunden, None = unbekannt (EMS-ESP nicht eingerichtet bzw. noch
    nicht abgefragt – ``ems_controls_ts`` setzt main nur mit eingerichtetem EMS-ESP)."""
    polled = getattr(eng, "ems_controls_ts", None) is not None
    c = ((getattr(eng, "ems_controls", None) or {}).get(key) or {}) if polled else {}
    found = bool(c.get("found"))
    writeable = c.get("writeable") if found else (False if polled else None)
    return {"polled": polled, "found": found, "writeable": writeable, "value": c.get("value") if found else None}


def ems_write_blocked(eng, key: str, what: str) -> str | None:
    """Hindernis, wenn EMS-ESP ``what`` („das Vorlauf-Soll“) ausdrücklich nicht schreiben lässt. Seit 0.46.10 sperrt
    „nicht gefunden“ nicht mehr: Manche EMS-ESP-Stände beantworten die Abfrage einzelner Werte nicht, obwohl es sie
    gibt – geschrieben wird ohnehin über Home Assistant, und die Prüfung nach dem Schreiben bemerkt einen Fehlschlag."""
    c = ems_control(eng, key)
    if not c["found"] or c["writeable"] is not False:
        return None
    return f"EMS-ESP lässt {what} nicht schreiben ({key} nicht schreibbar)"
