"""Entitäten automatisch finden: EMS-ESP-Therme, Tado-Räume, Personen, Wetter, Gas.

Jede Zuordnung lässt sich in den Add-on-Optionen über ``overrides``
(``rolle=entity_id``) überschreiben.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Rolle -> EMS-ESP-Namen. Je Rolle zuerst der Kurzname (so heißen die Werte in
# der EMS-ESP-API und bei "Entity-ID-Format: Kurzname"), dann der lange Name
# (Standard-Entity-IDs in HA, z. B. sensor.boiler_flame_current).
BOILER_ROLES: dict[str, tuple[str, ...]] = {
    "flow_temp": ("curflowtemp", "current_flow_temperature"),
    "sel_flow_temp": ("selflowtemp", "selected_flow_temperature"),
    "return_temp": ("rettemp", "return_temperature"),
    "burner_power": ("curburnpow", "burner_current_power"),
    "burner_on": ("burngas", "gas"),
    "burner_starts": ("burnstarts", "burner_starts"),
    "heating_pump": ("heatingpump", "heating_pump"),
    "heating_active": ("heatingactive", "heating_active"),
    "dhw_active": ("dhw_charging", "wwcharging", "dhw_active"),
    "dhw_seltemp": ("dhw_seltemp", "dhw_selected_temperature"),
    "dhw_temp": ("dhw_curtemp", "dhw_current_intern_temperature"),
    "boiler_outdoor": ("outdoortemp", "outside_temperature"),
    "min_period": ("burnminperiod", "burner_min_period"),
    "hyst_on": ("boilhyston", "hysteresis_on_temperature"),
    "hyst_off": ("boilhystoff", "hysteresis_off_temperature"),
    "min_power": ("burnminpower", "burner_min_power"),
    "max_power": ("burnmaxpower", "burner_max_power"),
    "nom_power": ("nompower", "nominal_power", "nominal_Power"),
    "heating_off": ("heatingoff", "forceheatingoff", "force_heating_off"),
    "boiler_temp": ("boiltemp", "actual_boiler_temperature"),
    "pump_mod": ("heatingpumpmod", "heating_pump_modulation"),
    "flame_current": ("flamecurr", "flame_current"),
    "burner_runtime": ("burnworkmin", "total_burner_operating_time"),
    "heat_starts": ("heatstarts", "burner_starts_heating"),
    "heat_runtime": ("heatworkmin", "total_heat_operating_time"),
    "nrg_total": ("nrgtotal", "total_energy"),
    "nrg_heat": ("nrgheat", "energy_heating"),
}

# Regler (ES73/RC35 …), Heizkreis 1
THERMOSTAT_ROLES: dict[str, tuple[str, ...]] = {
    "hc_seltemp": ("hc1_seltemp", "hc1_selected_room_temperature"),
    "hc_calc_flow": ("hc1_targetflowtemp", "hc1_calcflowtemp", "hc1_target_flow_temperature"),
    "hc_mode": ("hc1_mode", "hc1_operating_mode"),
    "hc_remotetemp": ("hc1_remotetemp", "hc1_room_temperature_from_remote"),
    "damped_outdoor": ("dampedoutdoortemp", "damped_outdoor_temperature"),
}

# Beschriftungen für die Oberfläche (Zuordnungen)
ROLE_LABELS: dict[str, str] = {
    "flow_temp": "Vorlauftemperatur (Ist)",
    "sel_flow_temp": "Vorlauftemperatur (Soll am Kessel)",
    "return_temp": "Rücklauftemperatur",
    "burner_power": "Brennerleistung (%)",
    "burner_on": "Brenner an (Gas)",
    "burner_starts": "Brennerstarts (Zähler)",
    "heating_pump": "Heizungspumpe",
    "heating_active": "Heizen aktiv",
    "dhw_active": "Warmwasser lädt",
    "dhw_seltemp": "Warmwasser-Soll",
    "dhw_temp": "Speichertemperatur",
    "boiler_outdoor": "Außenfühler am Kessel",
    "min_period": "Antipendelzeit",
    "hyst_on": "Einschaltdifferenz",
    "hyst_off": "Ausschaltdifferenz",
    "min_power": "Minimale Brennerleistung",
    "max_power": "Maximale Brennerleistung",
    "nom_power": "Nennleistung (kW)",
    "heating_off": "Heizen abschalten",
    "boiler_temp": "Kesseltemperatur",
    "pump_mod": "Pumpenmodulation",
    "flame_current": "Flammenstrom",
    "burner_runtime": "Brennerlaufzeit (Zähler)",
    "heat_starts": "Brennerstarts Heizen (Zähler)",
    "heat_runtime": "Heizlaufzeit (Zähler)",
    "nrg_total": "Energie gesamt (EMS-ESP)",
    "nrg_heat": "Energie Heizen (EMS-ESP)",
    "hc_seltemp": "Regler: Raum-Soll",
    "hc_calc_flow": "Regler: berechnete Vorlauftemperatur",
    "hc_mode": "Regler: Betriebsart (Tag/Nacht)",
    "hc_remotetemp": "Regler: Raumtemperatur (Remote)",
    "damped_outdoor": "Regler: gedämpfte Außentemperatur",
}

# Betriebsart, in der der Heizkreis heizt (EMS-ESP liefert je nach Sprache)
HEAT_MODES = {"tag", "day", "comfort", "heat", "heizen", "an", "on"}


@dataclass
class Room:
    name: str
    climate: str
    heating_power: str | None = None  # Tado: Ventilöffnung / Heizleistung in %
    humidity: str | None = None
    temp_sensor: str | None = None  # für die Langzeitstatistik (climate hat keine)


@dataclass
class Discovered:
    boiler: dict[str, str] = field(default_factory=dict)
    thermostat: dict[str, str] = field(default_factory=dict)
    rooms: list[Room] = field(default_factory=list)
    persons: list[str] = field(default_factory=list)
    outdoor: list[str] = field(default_factory=list)
    weather: str | None = None
    gas: str | None = None
    gas_unit: str | None = None
    demand_sensor: str | None = None  # z. B. sensor.heizungsanforderung (Ventile in %)
    home_zone: tuple[float, float] | None = None

    def all_sensor_ids(self) -> list[str]:
        ids = list(self.boiler.values()) + list(self.thermostat.values()) + list(self.outdoor)
        for r in self.rooms:
            ids += [x for x in (r.heating_power, r.humidity, r.temp_sensor) if x]
        if self.gas:
            ids.append(self.gas)
        if self.demand_sensor:
            ids.append(self.demand_sensor)
        return sorted(set(ids))

    def as_dict(self) -> dict:
        return {
            "boiler": self.boiler,
            "thermostat": self.thermostat,
            "rooms": [r.__dict__ for r in self.rooms],
            "persons": self.persons,
            "outdoor": self.outdoor,
            "weather": self.weather,
            "gas": self.gas,
            "gas_unit": self.gas_unit,
            "demand_sensor": self.demand_sensor,
            "home_zone": self.home_zone,
        }


def _find_boiler(entity_ids: list[str], keys: tuple[str, ...], device: str = "boiler") -> str | None:
    for key in keys:
        # optionales Präfix vor dem Gerät, z. B. number.ems_esp_thermostat_hc1_remotetemp
        pattern = re.compile(
            rf"^(sensor|binary_sensor|number|switch|select)\.(.*_)?{device}_{re.escape(key)}$",
            re.IGNORECASE,
        )
        hits = sorted(e for e in entity_ids if pattern.match(e))
        if hits:
            return hits[0]
    return None


def _discover_tado(d: Discovered, ids: list[str], by_id: dict, registry: list[dict], reg: dict) -> None:
    """Tado-Räume: climate-Entitäten der Tado-Integration, Zusatzsensoren über das Gerät."""
    tado_climates = [
        e for e in ids
        if e.startswith("climate.") and reg.get(e, {}).get("platform") == "tado"
    ]
    if not tado_climates:
        tado_climates = [
            e for e in ids
            if e.startswith("climate.")
            and "current_temperature" in by_id[e].get("attributes", {})
            and "boiler" not in e and "thermostat" not in e
        ]
    for climate in sorted(tado_climates):
        device = reg.get(climate, {}).get("device_id")
        siblings = [
            r["entity_id"] for r in registry
            if device and r.get("device_id") == device and r["entity_id"].startswith("sensor.")
        ]
        name = by_id[climate]["attributes"].get("friendly_name", climate)
        room = Room(name=name, climate=climate)
        for s in siblings:
            attrs = by_id.get(s, {}).get("attributes", {})
            if s.endswith("_heating") or (attrs.get("unit_of_measurement") == "%" and "heating" in s):
                room.heating_power = s
            elif attrs.get("device_class") == "humidity":
                room.humidity = s
            elif attrs.get("device_class") == "temperature":
                room.temp_sensor = s
        d.rooms.append(room)


def discover(states: list[dict], registry: list[dict], cfg) -> Discovered:
    by_id = {s["entity_id"]: s for s in states}
    ids = list(by_id)
    reg = {r["entity_id"]: r for r in registry}
    d = Discovered()

    for role, keys in BOILER_ROLES.items():
        hit = _find_boiler(ids, keys)
        if hit:
            d.boiler[role] = hit

    for role, keys in THERMOSTAT_ROLES.items():
        hit = _find_boiler(ids, keys, "thermostat")
        if hit:
            d.thermostat[role] = hit

    if cfg.rooms:
        for r in cfg.rooms:
            name = r.get("name") or by_id.get(r["climate"], {}).get("attributes", {}).get(
                "friendly_name", r["climate"]
            )
            d.rooms.append(
                Room(
                    name=name,
                    climate=r["climate"],
                    heating_power=r.get("heating_power"),
                    humidity=r.get("humidity"),
                    temp_sensor=r.get("temperature"),
                )
            )
    else:
        _discover_tado(d, ids, by_id, registry, reg)

    d.persons = sorted(e for e in ids if e.startswith("person."))

    d.outdoor = [e for e in cfg.outdoor_sensors if e in by_id]
    if not d.outdoor and "boiler_outdoor" in d.boiler:
        d.outdoor = [d.boiler["boiler_outdoor"]]

    if cfg.weather_entity:
        d.weather = cfg.weather_entity
    else:
        weathers = sorted(e for e in ids if e.startswith("weather."))
        d.weather = weathers[0] if weathers else None

    if cfg.gas_sensor and cfg.gas_sensor in by_id:
        d.gas = cfg.gas_sensor
    else:
        if cfg.gas_sensor:
            log.warning("Gaszähler %s nicht gefunden – suche automatisch", cfg.gas_sensor)
        gas = sorted(
            e for e in ids
            if e.startswith("sensor.")
            and by_id[e].get("attributes", {}).get("device_class") == "gas"
            and by_id[e].get("attributes", {}).get("state_class") in ("total", "total_increasing")
        )
        d.gas = gas[0] if gas else None
    if not d.gas:
        # Notlösung: von EMS-ESP geschätzte Energie (Brennerleistung × Nennleistung)
        d.gas = d.boiler.get("nrg_total") or d.boiler.get("nrg_heat")
        d.gas_unit = "kWh" if d.gas else None
    elif d.gas in by_id:
        d.gas_unit = by_id[d.gas].get("attributes", {}).get("unit_of_measurement")

    if cfg.demand_sensor and cfg.demand_sensor in by_id:
        d.demand_sensor = cfg.demand_sensor

    zone = by_id.get("zone.home", {}).get("attributes", {})
    if "latitude" in zone:
        d.home_zone = (zone["latitude"], zone["longitude"])

    # Overrides aus den Optionen
    for role, entity in cfg.overrides.items():
        if role in BOILER_ROLES:
            d.boiler[role] = entity
        elif role in THERMOSTAT_ROLES:
            d.thermostat[role] = entity
        elif role == "gas":
            d.gas = entity
        elif role == "weather":
            d.weather = entity

    log.info("Gefunden: %s", d.as_dict())
    return d
