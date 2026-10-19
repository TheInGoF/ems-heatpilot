"""Sperre gegen den Heizkreis von EMS-ESP (seit 0.36.3): Er ist nie ein Raum, auf den HeatPilot schreibt.

EMS-ESP meldet den Heizkreis des Reglers als climate-Entität (``climate.thermostat_hc1``). Wählt jemand sie als
Raum mit „macht mit“, ginge ``climate.set_temperature`` über Home Assistant an den Regler – das widerspricht
„EMS-ESP nur lesen“ (am Kessel schreibt HeatPilot seit 0.38 nur das Warmwasser-Soll, siehe ``dhw_control``).
Hier steht, woran HeatPilot diesen Heizkreis erkennt; ``discovery`` setzt damit ``Room.ems`` (der Raum macht nie
mit), ``control`` weist jeden Schreibvorgang darauf hart ab, ``readiness`` nennt ihn „nicht dabei“.

Erkannt wird am Gerät laut Geräteregistry, nicht am Namen: Tado-Zonen und HomeKit-Ventile heißen oft auch
„…_thermostat“ und bleiben beschreibbar. Nur wenn Home Assistant kein Gerät kennt, zählt der Name – und nur bei
MQTT, worüber EMS-ESP seine Entitäten anmeldet.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .discovery import RegistryIndex

# EMS-ESP-Heizkreis am Namen (climate.thermostat_hc1, climate.ems_esp_thermostat_hc1, climate.boiler) – für die
# Raumvorschläge (``discovery``) und, ohne Gerät, für die Sperre
EMS_CLIMATE = re.compile(r"^climate\.(.*_)?(boiler|thermostat)(_hc\d+)?$", re.IGNORECASE)
# Gerät von EMS-ESP: „EMS-ESP“ in Hersteller, Name oder Modell, oder Hersteller „proddy“ (Entwickler von EMS-ESP,
# so steht es bei älteren Versionen am Gateway) – Groß/klein egal
EMS_DEVICE_TEXT = "ems-esp"
EMS_MANUFACTURER = "proddy"
EMS_PLATFORM = "mqtt"
# Hinweis für Oberfläche, Protokoll und Bereitschaft
LOCK_TEXT = "Heizkreis von EMS-ESP – HeatPilot schreibt nicht an den Heizkreis"


def ems_device(dev: dict | None) -> bool:
    """Ist dieser Eintrag der Geräteregistry ein Gerät von EMS-ESP?"""
    if not dev:
        return False
    texts = " ".join(str(dev.get(k) or "") for k in ("manufacturer", "name", "model")).lower()
    return EMS_DEVICE_TEXT in texts or str(dev.get("manufacturer") or "").strip().lower() == EMS_MANUFACTURER


def is_ems_climate(climate: str, idx: RegistryIndex) -> bool:
    """Ist diese climate-Entität der Heizkreis von EMS-ESP?

    Mit Gerät: das Gerät selbst oder das Gateway, über das es angebunden ist (EMS-ESP meldet Kessel und Regler
    als eigene Geräte „über“ EMS-ESP). Ohne Gerät: Plattform MQTT und Name wie ``EMS_CLIMATE``.
    """
    dev = idx.device_of(climate)
    if dev:
        return ems_device(dev) or ems_device(idx.device.get(dev.get("via_device_id") or ""))
    platform = idx.entity.get(climate, {}).get("platform")
    return platform == EMS_PLATFORM and bool(EMS_CLIMATE.match(climate))
