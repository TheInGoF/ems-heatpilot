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
(``picker``) bietet nur passende Entitäten an, die mit Warmwasser im Namen zuerst. Die Prüfungen, die auch für
Weg B gelten, stehen seit 0.41 in ``entity_guard``.
"""

from __future__ import annotations

import re

from . import entity_guard as eg
from .entity_guard import HEATING_CIRCUIT, UNITS  # noqa: F401 – wie bisher hier zu holen

ROLE = "dhw_seltemp"
WHAT = "das Warmwasser-Soll"
# Hinweis auf Warmwasser im Namen der Entität oder im Anzeigenamen
DHW_HINT = re.compile(r"dhw|ww|warm|wasser|water|speicher|tank|cylinder|sanit", re.IGNORECASE)
# weitere bekannte Kesselwerte (EMS-ESP-Namen) mit °C, für die es keine Rolle gibt
OTHER_VALUES = {"heatingtemp": "Heiztemperatur (Obergrenze)", "heating_temperature": "Heiztemperatur (Obergrenze)"}
NOT_DHW = eg.other_names(ROLE, OTHER_VALUES)


# ------------------------------------------------------------------ Vorschlag und Wahl
def suggest_entity(found, states: dict) -> str | None:
    """Vorschlag aus der Erkennung: die Rolle „Warmwasser-Soll“, wenn sie eine ``number``-Entität ist, sonst eine
    ``number``-Entität mit einem der EMS-ESP-Namen (``number.boiler_dhw_seltemp``, ``…_wwseltemp`` …)."""
    return eg.suggest(found, states, ROLE, "number")


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
    return eg.wrong_role(entity, found, ROLE, NOT_DHW, WHAT)


def entity_problem(entity: str | None, state: dict | None, found=None) -> str | None:
    """Warum die Entität nicht taugt (Hindernis) – None = taugt: number, keine andere Rolle, verfügbar, Einheit °C."""
    if not entity:
        return "keine Entität für das Warmwasser-Soll gefunden – in den Einstellungen wählen"
    if not entity.startswith("number."):
        return f"{entity} ist keine number-Entität"
    return wrong_role(entity, found) or eg.state_problem(entity, state) or eg.unit_problem(entity, state)


def entity_doubt(entity: str | None, state: dict | None) -> str | None:
    """Einschränkung: Weder die Entität noch ihr Anzeigename deuten auf Warmwasser – erlaubt (andere Kessel,
    umbenannte Entitäten), aber bitte prüfen. None = Name passt."""
    if not entity or eg.name_hint(entity, state, DHW_HINT):
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
    {polled, found, writeable, value} – siehe ``entity_guard.ems_control``."""
    return eg.ems_control(eng, "wwseltemp")


def write_blocked(eng) -> str | None:
    """Hindernis, wenn EMS-ESP das Warmwasser-Soll nicht schreiben lässt (nicht schreibbar bzw. nicht gefunden)."""
    return eg.ems_write_blocked(eng, "wwseltemp", WHAT)


def _num(value) -> float | None:
    try:
        return None if isinstance(value, bool) else float(value)
    except (TypeError, ValueError):
        return None


def check_call(entity_id: str, dhw_entity: str | None, limits, data: dict, state: dict) -> None:
    """Warmwasser-Soll (Weg W): nur die eine eingestellte ``number``-Entität, nur ``value``, ganzzahlig,
    Minimum ≤ Wert ≤ Maximum (Einstellungen und, wenn HA sie meldet, die Grenzen der Entität); die Entität muss
    taugen (``dhw_entity.entity_problem``: verfügbar, Einheit °C, kein Heizkreis, kein anderer Kesselwert).
    Alles andere hart abgewiesen."""
    if not dhw_entity or entity_id != dhw_entity or not limits:
        raise PermissionError(f"number.set_value auf {entity_id} ist gesperrt – nur das eingestellte "
                  "Warmwasser-Soll bei „HeatPilot setzt“")
    value = data.get("value")
    lo, hi = limits
    if set(data) != {"value"} or isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise PermissionError(f"Warmwasser-Soll {value!r} gesperrt – nur ganze Grad von {lo:g} bis {hi:g} °C")
    problem = entity_problem(entity_id, state)
    if problem:
        raise PermissionError(f"{problem} – gesperrt")
    attrs = state.get("attributes") or {}
    e_lo, e_hi = _num(attrs.get("min")), _num(attrs.get("max"))
    if (e_lo is not None and value < e_lo) or (e_hi is not None and value > e_hi):
        raise PermissionError(f"Warmwasser-Soll {value} °C außerhalb der Grenzen von {entity_id} "
                  f"({e_lo:g}…{e_hi:g} °C)" if e_lo is not None and e_hi is not None
                  else f"Warmwasser-Soll {value} °C außerhalb der Grenzen von {entity_id}")
