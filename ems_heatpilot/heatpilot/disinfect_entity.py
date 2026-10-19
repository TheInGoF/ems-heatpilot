"""Schalter der Desinfektion (seit 0.42): Vorschlag, Wahl und Prüfung – welchen ``switch`` HeatPilot zum
Desinfektionstermin schalten darf.

Ohne Regler hat der Kessel für die Legionellenschaltung keinen Timer, nur den Schalter „WWK Desinfizieren“ (EMS-ESP
``wwdisinfecting``, ab 3.6 ``dhw/disinfecting``; in Home Assistant z. B. ``switch.boiler_wwdisinfecting``). Die Rolle
dazu ist „dhw_disinfecting“ (``discovery_roles``, seit 0.25 gelesen); die Einstellung ``disinfect_entity`` wählt die
Entität, leer = erkannt (``target``).

Strenge Prüfung (``problem``, ein Hindernis – ``disinfect_control`` schaltet dann nicht, ``Control`` weist den Aufruf
ab): ``switch``, verfügbar und an bzw. aus; kein Heizkreis, keine andere erkannte Rolle, kein anderer bekannter
Kesselschalter – nie die Einmalladung, die Zirkulation, der Heizbetrieb oder der Warmwasser-Betrieb (``wrong_role``);
und der Name bzw. Anzeigename muss nach der Desinfektion aussehen (``NAME_HINT``). Anders als beim Warmwasser-Soll ist
ein unpassender Name hier keine Einschränkung, sondern ein Hindernis: Geschaltet wird nur ein Schalter, der erkennbar
die Desinfektion ist. ``check_call`` ist die letzte Prüfung in ``Control`` vor ``switch.turn_on``/``turn_off``.

Dazu die Einstellung (``FIELDS``, ``clean_config`` – ``config`` bindet sie ein). Reine Funktionen, schreibt nichts.
"""

from __future__ import annotations

import re

from . import entity_guard as eg
from .boiler_rule import SWITCH_ENTITY

ROLE = "dhw_disinfecting"
WHAT = "die Desinfektion"
EMS_KEY = "wwdisinfecting"   # Kesselsteuerung von EMS-ESP (``emsesp.BOILER_CONTROLS``, stündlich gelesen)
NAME_HINT = re.compile(r"disinfect|desinf|legionell", re.IGNORECASE)
# weitere bekannte Kesselschalter (EMS-ESP-Namen) ohne eigene Rolle – nie die Desinfektion
OTHER_SWITCH = {"wwcirc": "Zirkulation", "dhw_circ": "Zirkulation", "wwcircpump": "Zirkulationspumpe",
                "dhw_circpump": "Zirkulationspumpe", "wwcircmode": "Zirkulation", "dhw_circmode": "Zirkulation",
                "wwactivated": "Warmwasser-Betrieb", "dhw_activated": "Warmwasser-Betrieb",
                "chimneysweeper": "Schornsteinfeger", "wwchargeoptimization": "Ladungsoptimierung",
                "dhw_chargeoptimization": "Ladungsoptimierung"}
NOT_DISINFECT = eg.other_names(ROLE, OTHER_SWITCH)
# Einstellung (``config.FIELDS``): die Entität des Schalters, leer = erkannt
FIELDS: dict[str, tuple] = {"disinfect_entity": ("entity", "switch")}


def clean_config(cfg) -> None:
    """Gespeicherte Einstellung lesen (``Config.from_raw``): leer bzw. keine switch-Entität gilt als „erkannt“."""
    value = getattr(cfg, "disinfect_entity", None)
    if not value or not SWITCH_ENTITY.match(str(value)):
        cfg.disinfect_entity = None


# ------------------------------------------------------------------ Vorschlag und Wahl
def suggest(found, states: dict) -> str | None:
    """Vorschlag aus der Erkennung: die Rolle, wenn sie ein ``switch`` ist, sonst ein ``switch`` mit einem der
    EMS-ESP-Namen (``switch.boiler_wwdisinfecting``, ``switch.ems_esp_boiler_dhw_disinfecting`` …)."""
    return eg.suggest(found, states, ROLE, "switch")


def target(cfg, found, states: dict) -> tuple[str | None, str | None]:
    """(Entität, Herkunft): eingestellt (``disinfect_entity``), sonst erkannt; nur ``switch``."""
    chosen = getattr(cfg, "disinfect_entity", None)
    if chosen:
        return (chosen, "eingestellt") if chosen.startswith("switch.") else (None, None)
    hit = suggest(found, states)
    return (hit, "erkannt") if hit else (None, None)


# ------------------------------------------------------------------ Prüfung
def wrong_role(entity: str, found=None) -> str | None:
    """Gehört die Entität sichtbar zu etwas anderem (Heizkreis, andere Rolle, anderer Kesselschalter)? Der Grund."""
    return eg.wrong_role(entity, found, ROLE, NOT_DISINFECT, WHAT)


def name_problem(entity: str, state: dict | None = None, found=None) -> str | None:
    """Falsche Rolle oder ein Name ohne Hinweis auf die Desinfektion (Entität und Anzeigename) – der Grund."""
    wrong = wrong_role(entity, found)
    if wrong:
        return wrong
    if not eg.name_hint(entity, state, NAME_HINT):
        return (f"Name von {entity} deutet nicht auf die Desinfektion – HeatPilot schaltet nur den Schalter "
                "„Desinfizieren“ (EMS-ESP wwdisinfecting)")
    return None


def problem(entity: str | None, state: dict | None, found=None) -> str | None:
    """Warum die Entität nicht als Schalter der Desinfektion taugt – None = taugt (siehe Modulbeschreibung)."""
    if not entity:
        return ("keine Entität für die Desinfektion gefunden – unter Einstellungen → Entitäten wählen (Schalter "
                "„Desinfizieren“)")
    if not entity.startswith("switch."):
        return f"{entity} ist keine switch-Entität"
    why = name_problem(entity, state, found) or eg.state_problem(entity, state)
    if why is None and state.get("state") not in ("on", "off"):
        return f"{entity} meldet weder an noch aus ({state.get('state')})"
    return why


def picker(entity: str, state: dict | None, found=None) -> dict:
    """Auswahl in der Oberfläche (``api/entities`` → ``disinfect``): ``fits`` = switch, Name passt, keine andere
    Rolle; ``hint`` = dasselbe (es gibt keine erlaubten Entitäten ohne passenden Namen)."""
    fits = entity.startswith("switch.") and name_problem(entity, state, found) is None
    return {"fits": fits, "hint": fits}


def setting_problem(value: str) -> str | None:
    """Beim Speichern (``config.validate``): Entität, die sichtbar zu etwas anderem gehört. Der Name wird erst mit dem
    Zustand geprüft (Anzeigename) – ein unpassender ist dann ein Hindernis."""
    return wrong_role(value)


def ems_blocked(eng) -> str | None:
    """Hindernis, wenn EMS-ESP die Desinfektion nicht schalten lässt (``wwdisinfecting`` nicht schreibbar/gefunden)."""
    return eg.ems_write_blocked(eng, EMS_KEY, WHAT)


def check_call(entity_id: str, chosen: str | None, data: dict, state: dict | None, locked: str | None) -> None:
    """Letzte Prüfung in ``Control`` vor ``switch.turn_on``/``turn_off``: nur die eine Entität, ohne Daten, sie muss
    taugen, und nichts sperrt (``locked``: Termin, Warmwasser-Soll „HeatPilot setzt“, kein Regler am Bus, keine
    Pause – ``disinfect_rule.lock``). Sonst ``PermissionError``."""
    if not chosen or entity_id != chosen or data:
        raise PermissionError(f"switch auf {entity_id} ist gesperrt – nur der Schalter der Desinfektion zum Termin")
    why = problem(entity_id, state)
    if why:
        raise PermissionError(f"{why} – gesperrt")
    if locked:
        raise PermissionError(f"Desinfektion auf {entity_id} gesperrt: {locked}")
