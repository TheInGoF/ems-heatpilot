"""Raum-Eintrag der Einstellungen (seit 0.41 eigene Datei, vorher in ``config``): einen Raum aus der Oberfläche prüfen.

``clean_room`` prüft und bereinigt einen Eintrag der Räume-Tabelle (``config.validate``): climate-Entität, Sensoren,
Gruppe, Raumregler, „macht mit“, Komforttemperatur, Voreinstellung am Ventilunterteil (0.35) und – über
``floor_limit.clean_entry`` – die Fußbodenheizung (0.39.1). Fehler als ValueError mit der Entität. Die Grenzwerte
dazu (Komfort, Stufen der Voreinstellung) stehen hier; ``config`` reicht sie unter den bisherigen Namen durch.
"""

from __future__ import annotations

import re

from . import floor_limit

ENTITY = re.compile(r"^[a-z_]+\.[A-Za-z0-9_]+$")
COMFORT_MIN, COMFORT_MAX, COMFORT_DEFAULT = 5.0, 25.0, 21.0
# Hydraulik (0.35): Voreinstellung am Ventilunterteil – höchstens so viele Stufen
PRESET_MAX = 12


def _comfort(r: dict, climate: str) -> float:
    try:
        comfort = round(float(str(r["comfort"]).replace(",", ".")) * 2) / 2
    except ValueError:
        raise ValueError(f"{climate}: Komforttemperatur keine Zahl") from None
    if not COMFORT_MIN <= comfort <= COMFORT_MAX:
        raise ValueError(f"{climate}: Komfort muss zwischen {COMFORT_MIN:g} und {COMFORT_MAX:g} liegen")
    return comfort


def _presets(r: dict, climate: str) -> dict[str, int]:
    """Hydraulik (0.35): preset = aktuelle Voreinstellung, preset_max = Zahl der Stufen. Leer = unbekannt (Schlüssel
    fehlt); preset_max 0 = kein Voreinstellventil (Rücklaufverschraubung)."""
    steps: dict[str, int] = {}
    for key, label in (("preset_max", "Stufen"), ("preset", "Voreinstellung")):
        if r.get(key) in (None, ""):
            continue
        try:
            v = int(str(r[key]).strip())
        except ValueError:
            raise ValueError(f"{climate}: {label} keine ganze Zahl") from None
        if v < 0 or v > PRESET_MAX:
            raise ValueError(f"{climate}: {label} muss zwischen 0 und {PRESET_MAX} liegen")
        steps[key] = v
    if steps.get("preset"):
        if "preset_max" not in steps:
            raise ValueError(f"{climate}: Voreinstellung {steps['preset']} braucht die Zahl der Stufen")
        if steps["preset"] > steps["preset_max"]:
            raise ValueError(f"{climate}: Voreinstellung {steps['preset']} liegt über den {steps['preset_max']} Stufen")
    out = {"preset_max": steps["preset_max"]} if "preset_max" in steps else {}
    if steps.get("preset"):
        out["preset"] = steps["preset"]
    return out


def clean_room(r: dict) -> dict:
    """Ein Raum-Eintrag aus der Oberfläche; neue Felder (0.34) nur, wenn sie gesetzt sind. Seit 0.39.1 dazu die
    Fußbodenheizung: Anschluss ``floor_type`` und höchster Vorlauf ``floor_max_flow`` (``floor_limit.clean_entry``)."""
    climate = str(r.get("climate", "")).strip()
    if not ENTITY.match(climate):
        raise ValueError(f"Raum ohne gültige climate-Entität: {climate or '(leer)'}")
    room = {"name": str(r.get("name") or "").strip(), "climate": climate}
    for opt in ("temperature", "heating_power", "humidity"):
        v = str(r.get(opt) or "").strip()
        if v:
            if not ENTITY.match(v):
                raise ValueError(f"{climate}: {opt} ungültig")
            room[opt] = v
    group = str(r.get("group") or "").strip()
    if group:
        room["group"] = group
    for key in ("controller", "managed"):
        if key in r:
            room[key] = r[key] is True or str(r[key]).lower() in ("true", "1", "on", "ja")
    if r.get("comfort") not in (None, ""):
        room["comfort"] = _comfort(r, climate)
    room.update(_presets(r, climate))
    room.update(floor_limit.clean_entry(r, climate))
    return room
