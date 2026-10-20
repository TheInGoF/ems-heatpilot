"""Entitäten von Weg B (seit 0.41): Vorlauf-Soll und Heizbetrieb – Vorschlag, Wahl, Prüfung – und ob HeatPilot am
Kessel schreiben darf.

Geschrieben wird nur auf zwei Entitäten, beide über Home Assistant (wie Weg W, ``dhw_entity``):

* **Vorlauf-Soll** – eine ``number``-Entität mit °C, die nach dem Vorlauf-Soll des Kessels aussieht (Rolle
  „Vorlauftemperatur (Soll am Kessel)“, EMS-ESP ``selflowtemp``); Einstellung ``flow_set_entity``, leer = erkannt,
* **Heizbetrieb** – eine ``switch``-Entität (Rolle „Heizbetrieb aktiviert“, EMS-ESP ``heatingactivated``);
  Einstellung ``heating_switch_entity``, leer = erkannt.

Strenge Prüfung (``flow_problem``/``heat_problem``, Hindernis): richtige Domäne, verfügbar, beim Vorlauf °C, kein
Heizkreis (``…_hc1_…``), keine andere erkannte Rolle und kein anderer bekannter Kesselwert – nie die Heiztemperatur
(Obergrenze), nie „Heizen abschalten“ (umgekehrte Bedeutung), nie Warmwasser-Schalter. Ein Name ohne passenden Hinweis
ist erlaubt (andere Kessel), steht aber als Einschränkung da (``*_doubt``).

Ob HeatPilot gerade schreiben darf (``write_problems``): **kein Regler am Bus** (``regulator``, eigene Datei: EMS-ESP
antwortet auf das Gerät ``thermostat`` oder es gibt frische Regler-Werte in Home Assistant – der Regler überschreibt
alle 60 s; ohne EMS-ESP, ohne frische Antwort oder solange nicht sicher ist, dass er fehlt, ist es unbekannt und
ebenfalls gesperrt), beide Entitäten tauglich, Vorlauf-Soll und Warmwasser-Soll nicht dieselbe Entität
(``same_entity``), EMS-ESP lässt ``selflowtemp``/``heatingactivated`` schreiben, „Heizen abschalten“ ist nicht an.
Dazu die Grenzen, die Home Assistant für die Entität des Vorlauf-Solls meldet (``bounds``). Nur lesen, schreibt nichts.
"""

from __future__ import annotations

import re

from . import dhw_entity, disinfect_entity
from . import entity_guard as eg
from .boiler_rule import FLOW_MIN
from .regulator import regulator, regulator_block  # noqa: F401 – wie bisher auch hier zu holen
from .values import flag, num

FLOW_ROLE, HEAT_ROLE = "sel_flow_temp", "heating_activated"
FLOW_WHAT, HEAT_WHAT = "das Vorlauf-Soll", "der Heizbetrieb"
FLOW_HINT = re.compile(r"flow|vorlauf", re.IGNORECASE)
HEAT_HINT = re.compile(r"heat|heiz", re.IGNORECASE)
# weitere bekannte Kesselwerte (EMS-ESP-Namen) ohne Rolle, die nie das Vorlauf-Soll bzw. der Heizbetrieb sind
OTHER_FLOW = {"heatingtemp": "Heiztemperatur (Obergrenze)", "heating_temperature": "Heiztemperatur (Obergrenze)",
              "wwflowtempoffset": "Warmwasser: Anhebung Vorlauf", "dhw_flowtempoffset": "Warmwasser: Anhebung Vorlauf",
              "selburnpow": "Brennerleistung (Soll)", "boil2hyston": "Einschaltdifferenz"}
OTHER_SWITCH = {"wwactivated": "Warmwasser-Betrieb", "dhw_activated": "Warmwasser-Betrieb",
                "wwcirc": "Zirkulation", "dhw_circ": "Zirkulation", "wwcircpump": "Zirkulationspumpe",
                "dhw_circpump": "Zirkulationspumpe", "chimneysweeper": "Schornsteinfeger",
                "wwchargeoptimization": "Ladungsoptimierung", "dhw_chargeoptimization": "Ladungsoptimierung"}
NOT_FLOW = eg.other_names(FLOW_ROLE, OTHER_FLOW)
NOT_HEAT = eg.other_names(HEAT_ROLE, OTHER_SWITCH)
EMS_FLOW, EMS_HEAT = "selflowtemp", "heatingactivated"   # Kesselsteuerungen von EMS-ESP (stündlich gelesen)
HEATING_OFF_TEXT = ("„Heizen abschalten“ ist an – EMS-ESP hält die Heizung aus und überschreibt das Vorlauf-Soll; "
                    "erst in EMS-ESP bzw. Home Assistant ausschalten (HeatPilot schaltet das nicht)")


# ------------------------------------------------------------------ Vorschlag und Wahl
def suggest_flow(found, states: dict) -> str | None:
    return eg.suggest(found, states, FLOW_ROLE, "number")


def suggest_heat(found, states: dict) -> str | None:
    return eg.suggest(found, states, HEAT_ROLE, "switch")


def _target(chosen: str | None, domain: str, hit: str | None) -> tuple[str | None, str | None]:
    if chosen:
        return (chosen, "eingestellt") if chosen.startswith(domain + ".") else (None, None)
    return (hit, "erkannt") if hit else (None, None)


def flow_target(cfg, found, states: dict) -> tuple[str | None, str | None]:
    """(Entität, Herkunft) des Vorlauf-Solls: eingestellt (``flow_set_entity``), sonst erkannt; nur ``number``."""
    return _target(cfg.flow_set_entity, "number", suggest_flow(found, states))


def heat_target(cfg, found, states: dict) -> tuple[str | None, str | None]:
    """(Entität, Herkunft) des Heizbetriebs: eingestellt (``heating_switch_entity``), sonst erkannt; nur ``switch``."""
    return _target(cfg.heating_switch_entity, "switch", suggest_heat(found, states))


# ------------------------------------------------------------------ Prüfung
def flow_wrong(entity: str, found=None) -> str | None:
    return eg.wrong_role(entity, found, FLOW_ROLE, NOT_FLOW, FLOW_WHAT)


def heat_wrong(entity: str, found=None) -> str | None:
    return eg.wrong_role(entity, found, HEAT_ROLE, NOT_HEAT, HEAT_WHAT)


def flow_problem(entity: str | None, state: dict | None, found=None) -> str | None:
    """Warum die Entität nicht als Vorlauf-Soll taugt – None = taugt: number, keine andere Rolle, verfügbar, °C."""
    if not entity:
        return "keine Entität für das Vorlauf-Soll gefunden – in den Einstellungen wählen"
    if not entity.startswith("number."):
        return f"{entity} ist keine number-Entität"
    return flow_wrong(entity, found) or eg.state_problem(entity, state) or eg.unit_problem(entity, state)


def heat_problem(entity: str | None, state: dict | None, found=None) -> str | None:
    """Warum die Entität nicht als Heizbetrieb taugt – None = taugt: switch, keine andere Rolle, an oder aus."""
    if not entity:
        return "keine Entität für den Heizbetrieb gefunden – in den Einstellungen wählen"
    if not entity.startswith("switch."):
        return f"{entity} ist keine switch-Entität"
    problem = heat_wrong(entity, found) or eg.state_problem(entity, state)
    if problem is None and state.get("state") not in ("on", "off"):
        return f"{entity} meldet weder an noch aus ({state.get('state')})"
    return problem


def flow_doubt(entity: str | None, state: dict | None) -> str | None:
    if not entity or eg.name_hint(entity, state, FLOW_HINT):
        return None
    return f"Name von {entity} deutet nicht auf ein Vorlauf-Soll – prüfen, ob es wirklich das Vorlauf-Soll ist"


def heat_doubt(entity: str | None, state: dict | None) -> str | None:
    if not entity or eg.name_hint(entity, state, HEAT_HINT):
        return None
    return f"Name von {entity} deutet nicht auf den Heizbetrieb – prüfen, ob er wirklich den Heizbetrieb schaltet"


def flow_picker(entity: str, state: dict | None, found=None) -> dict:
    """Auswahl in der Oberfläche (``api/entities`` → ``flow``): ``fits`` = number mit °C, keine andere Rolle;
    ``hint`` = Name deutet auf ein Vorlauf-Soll."""
    unit = str(((state or {}).get("attributes") or {}).get("unit_of_measurement") or "").strip()
    fits = entity.startswith("number.") and unit in eg.UNITS and flow_wrong(entity, found) is None
    return {"fits": fits, "hint": flow_doubt(entity, state) is None}


def heat_picker(entity: str, state: dict | None, found=None) -> dict:
    """Auswahl in der Oberfläche (``api/entities`` → ``heating``): ``fits`` = switch, keine andere Rolle."""
    fits = entity.startswith("switch.") and heat_wrong(entity, found) is None
    return {"fits": fits, "hint": heat_doubt(entity, state) is None}


def bounds(state: dict | None) -> tuple[float | None, float | None]:
    """(min, max), die Home Assistant für die Entität meldet – None, wo nichts gemeldet wird."""
    attrs = (state or {}).get("attributes") or {}
    return num(attrs.get("min")), num(attrs.get("max"))


def same_entity(cfg, found, states: dict) -> str | None:
    """Vorlauf-Soll und Warmwasser-Soll auf derselben Entität (eingestellt oder erkannt): beide Schreibwege gesperrt –
    sonst prüfte ``Control`` das Warmwasser-Soll mit den Grenzen des Vorlaufs (oder umgekehrt)."""
    flow, dhw = flow_target(cfg, found, states)[0], dhw_entity.target_entity(cfg, found, states)[0]
    if flow and flow == dhw:
        return (f"Vorlauf-Soll und Warmwasser-Soll sind dieselbe Entität ({flow}) – beide Schreibwege gesperrt; unter "
                "Einstellungen → Kessel zwei verschiedene wählen")
    return None


def setting_problem(key: str, value: str) -> str | None:
    """Beim Speichern (``config.validate``): Entität, die sichtbar zu etwas anderem gehört – sie lässt sich nicht
    speichern (Warmwasser-Soll, Vorlauf-Soll, Heizbetrieb, seit 0.42 der Schalter der Desinfektion)."""
    check = {"dhw_set_entity": dhw_entity.wrong_role, "flow_set_entity": flow_wrong,
             "heating_switch_entity": heat_wrong, "disinfect_entity": disinfect_entity.setting_problem}.get(key)
    return check(value) if check else None


# ------------------------------------------------------------------ darf geschrieben werden?
def heating_off(eng) -> str | None:
    """„Heizen abschalten“ ist an (EMS-ESP ``heatingoff``): EMS-ESP hält die Heizung aus – nicht schreiben."""
    return HEATING_OFF_TEXT if flag(eng._boiler("heating_off")) == 1 else None


def heat_needed(cfg) -> bool:
    """Braucht HeatPilot den Schalter Heizbetrieb? Nur bei „Heizbetrieb ganz aus“ – bei „Pumpe läuft weiter“ (Standard)
    bleibt der Heizbetrieb an und HeatPilot führt den Kessel allein über das Vorlauf-Soll (0.46.7)."""
    return getattr(cfg, "boiler_idle", "pumpe") == "aus"


def heat_usable(eng, heat: str | None) -> bool:
    """Lässt sich der Heizbetrieb schalten (Entität taugt, EMS-ESP lässt schreiben)? Bei manchen Kesseln ist
    ``heatingactivated`` nur eine Rückmeldung – dann schaltet HeatPilot ihn nie (0.46.7)."""
    return (heat_problem(heat, eng.states.get(heat) if heat else None, eng.found) is None
            and eg.ems_write_blocked(eng, EMS_HEAT, HEAT_WHAT) is None)


def write_problems(eng, flow: str | None, heat: str | None, now: float | None = None) -> list[str]:
    """Alle Hindernisse fürs Schreiben (dieselben wie in „Bereit für die Steuerung?“): Regler am Bus, Entitäten (auch
    dieselbe wie das Warmwasser-Soll), schreibbar laut EMS-ESP, „Heizen abschalten“, Obergrenze unter 35 °C. Leer =
    darf."""
    r = regulator(eng, now)
    out = [] if r["present"] is False else [r["text"]]
    need_heat = heat_needed(eng.cfg)  # 0.46.7: der Schalter sperrt nur, wenn er gebraucht wird
    out += [p for p in (flow_problem(flow, eng.states.get(flow) if flow else None, eng.found),
                        heat_problem(heat, eng.states.get(heat) if heat else None, eng.found) if need_heat else None,
                        same_entity(eng.cfg, eng.found, eng.states),
                        eg.ems_write_blocked(eng, EMS_FLOW, FLOW_WHAT),
                        eg.ems_write_blocked(eng, EMS_HEAT, HEAT_WHAT) if need_heat else None,
                        heating_off(eng)) if p]
    if eng.cfg.max_flow_temp < FLOW_MIN:
        out.append(f"Höchster Vorlauf {eng.cfg.max_flow_temp} °C liegt unter {FLOW_MIN} °C – kein Vorlauf-Soll möglich")
    return out
