"""Entität des Warmwasser-Solls für Weg W (seit 0.38): Vorschlag, Wahl und Prüfung – auf welche ``number``-Entität
HeatPilot schreiben darf.

Geschrieben wird nur auf eine Entität, die nach dem Speicher-Soll aussieht. Hart abgewiesen (``entity_problem``:
Hindernis in „Bereit für die Steuerung?“, ``DhwControl.plan`` merkt nichts vor, ``Control`` weist den Aufruf ab):

* keine ``number``-Entität, fehlt in Home Assistant oder ist nicht verfügbar,
* keine Temperatur: Einheit fehlt oder ist nicht °C,
* gehört sichtbar zu etwas anderem (``wrong_role``): zu einem Heizkreis (``…_hc1_…``, z. B. die Heizkurve des
  Reglers), zu einer anderen erkannten Rolle (Vorlauf-Soll, Hysterese, Desinfektionstemperatur …) oder zu einem
  anderen bekannten Kesselwert von EMS-ESP (Heiztemperatur).

Ein Name ohne Hinweis auf Warmwasser (dhw, ww, warm, Wasser, Speicher …, auch im Anzeigenamen) ist erlaubt – andere
Kessel und umbenannte Entitäten heißen anders –, steht aber als Einschränkung da (``entity_doubt``: „prüfen“).
Dazu kommt, ob EMS-ESP das Warmwasser-Soll schreiben lässt (``boiler_control``: Kesselsteuerung ``wwseltemp``,
stündlich gelesen); „nicht schreibbar“ ist ebenfalls ein Hindernis (``write_blocked``). Die Auswahl in der Oberfläche
(``picker``) bietet nur passende Entitäten an, die mit Warmwasser im Namen zuerst.
"""

from __future__ import annotations

import re

from .discovery_roles import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES

ROLE = "dhw_seltemp"
UNITS = ("°C", "°c", "C")
# Heizkreis im Namen (number.thermostat_hc1_designtemp, …_hc2_…): nie das Warmwasser-Soll
HEATING_CIRCUIT = re.compile(r"(^|_)hc\d+(_|$)")
# Hinweis auf Warmwasser im Namen der Entität oder im Anzeigenamen
DHW_HINT = re.compile(r"dhw|ww|warm|wasser|water|speicher|tank|cylinder|sanit", re.IGNORECASE)
# weitere bekannte Kesselwerte (EMS-ESP-Namen) mit °C, für die es keine Rolle gibt
OTHER_VALUES = {"heatingtemp": "Heiztemperatur (Obergrenze)", "heating_temperature": "Heiztemperatur (Obergrenze)"}


def _other_names() -> dict[str, str]:
    """Name → Beschriftung aller anderen Rollen (Kessel, Regler) und weiteren Kesselwerte."""
    out: dict[str, str] = {}
    for roles in (BOILER_ROLES, THERMOSTAT_ROLES):
        for role, names in roles.items():
            if role != ROLE:
                for name in names:
                    out.setdefault(name.lower(), ROLE_LABELS.get(role, role))
    return {**out, **OTHER_VALUES}


NOT_DHW = _other_names()


# ------------------------------------------------------------------ Vorschlag und Wahl
def suggest_entity(found, states: dict) -> str | None:
    """Vorschlag aus der Erkennung: die Rolle „Warmwasser-Soll“, wenn sie eine ``number``-Entität ist, sonst eine
    ``number``-Entität mit einem der EMS-ESP-Namen (``number.boiler_dhw_seltemp``, ``…_wwseltemp`` …)."""
    role = (getattr(found, "boiler", None) or {}).get(ROLE)
    if role and role.startswith("number."):
        return role
    names = "|".join(re.escape(k) for k in BOILER_ROLES[ROLE])
    pattern = re.compile(rf"^number\.(.*_)?boiler_({names})$", re.IGNORECASE)
    hits = sorted(e for e in states or {} if pattern.match(e))
    return hits[0] if hits else None


def target_entity(cfg, found, states: dict) -> tuple[str | None, str | None]:
    """(Entität, Herkunft): eingestellt (``dhw_set_entity``), sonst erkannt; nur ``number``."""
    if cfg.dhw_set_entity:
        return (cfg.dhw_set_entity, "eingestellt") if cfg.dhw_set_entity.startswith("number.") else (None, None)
    hit = suggest_entity(found, states)
    return (hit, "erkannt") if hit else (None, None)


# ------------------------------------------------------------------ Prüfung
def wrong_role(entity: str, found=None) -> str | None:
    """Gehört die Entität sichtbar zu etwas anderem als dem Warmwasser-Soll? Heizkreis im Namen, andere erkannte Rolle
    (``found``) oder Name einer anderen Rolle bzw. eines anderen Kesselwerts – dann der Grund, sonst None."""
    obj = entity.split(".", 1)[-1].lower()
    if HEATING_CIRCUIT.search(obj):
        return f"{entity} gehört zu einem Heizkreis (z. B. Heizkurve des Reglers) – nicht das Warmwasser-Soll"
    for group in ("boiler", "thermostat"):
        for role, eid in (getattr(found, group, None) or {}).items():
            if eid == entity and role != ROLE:
                return f"{entity} ist laut Erkennung „{ROLE_LABELS.get(role, role)}“ – nicht das Warmwasser-Soll"
    hit = next((n for n in NOT_DHW if obj == n or obj.endswith("_" + n)), None)
    if hit:
        return f"{entity} ist ein anderer Kesselwert („{NOT_DHW[hit]}“) – nicht das Warmwasser-Soll"
    return None


def entity_problem(entity: str | None, state: dict | None, found=None) -> str | None:
    """Warum die Entität nicht taugt (Hindernis) – None = taugt: number, keine andere Rolle, verfügbar, Einheit °C."""
    if not entity:
        return "keine Entität für das Warmwasser-Soll gefunden – in den Einstellungen wählen"
    if not entity.startswith("number."):
        return f"{entity} ist keine number-Entität"
    wrong = wrong_role(entity, found)
    if wrong:
        return wrong
    if not state:
        return f"{entity} fehlt in Home Assistant"
    if state.get("state") in (None, "unavailable", "unknown"):
        return f"{entity} ist nicht verfügbar"
    unit = (state.get("attributes") or {}).get("unit_of_measurement")
    if unit is None or not str(unit).strip():
        return f"{entity} meldet keine Einheit – erlaubt ist nur eine Temperatur in °C"
    if str(unit).strip() not in UNITS:
        return f"{entity} ist keine Temperatur (Einheit {unit})"
    return None


def entity_doubt(entity: str | None, state: dict | None) -> str | None:
    """Einschränkung: Weder die Entität noch ihr Anzeigename deuten auf Warmwasser – erlaubt (andere Kessel,
    umbenannte Entitäten), aber bitte prüfen. None = Name passt."""
    if not entity:
        return None
    name = str(((state or {}).get("attributes") or {}).get("friendly_name") or "")
    if DHW_HINT.search(entity.split(".", 1)[-1]) or DHW_HINT.search(name):
        return None
    return f"Name von {entity} deutet nicht auf ein Warmwasser-Soll – prüfen, ob es wirklich das Speicher-Soll ist"


def picker(entity: str, state: dict | None, found=None) -> dict:
    """Für die Auswahl in der Oberfläche (``api/entities``): ``fits`` = number mit °C und keine andere Rolle,
    ``hint`` = Name deutet auf Warmwasser (steht weiter oben)."""
    unit = str(((state or {}).get("attributes") or {}).get("unit_of_measurement") or "").strip()
    fits = entity.startswith("number.") and unit in UNITS and wrong_role(entity, found) is None
    return {"fits": fits, "hint": entity_doubt(entity, state) is None}


# ------------------------------------------------------------------ Schreibbar laut Kesselsteuerungen
def boiler_control(eng) -> dict:
    """Warmwasser-Soll laut den Kesselsteuerungen von EMS-ESP (``wwseltemp``, stündlich gelesen, nur lesen):
    {polled, found, writeable, value}. ``writeable`` False = nicht schreibbar oder nicht gefunden, None = unbekannt
    (EMS-ESP nicht eingerichtet bzw. noch nicht abgefragt – ``ems_controls_ts`` setzt main nur mit eingerichtetem
    EMS-ESP und übernimmt es beim Neuaufbau nur bei gleicher Adresse)."""
    polled = getattr(eng, "ems_controls_ts", None) is not None
    c = ((getattr(eng, "ems_controls", None) or {}).get("wwseltemp") or {}) if polled else {}
    found = bool(c.get("found"))
    writeable = c.get("writeable") if found else (False if polled else None)
    return {"polled": polled, "found": found, "writeable": writeable, "value": c.get("value") if found else None}


def write_blocked(eng) -> str | None:
    """Hindernis, wenn EMS-ESP das Warmwasser-Soll nicht schreiben lässt (nicht schreibbar bzw. nicht gefunden)."""
    c = boiler_control(eng)
    if c["writeable"] is not False:
        return None
    return ("EMS-ESP lässt das Warmwasser-Soll nicht schreiben (wwseltemp "
            + ("nicht schreibbar)" if c["found"] else "nicht gefunden)"))
