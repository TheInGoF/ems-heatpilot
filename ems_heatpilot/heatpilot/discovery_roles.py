"""EMS-ESP-Rollen (seit 0.36.3 eigene Datei): welche EMS-ESP-Werte HeatPilot unter welcher Rolle liest.

``BOILER_ROLES`` (Kessel) und ``THERMOSTAT_ROLES`` (Regler, Heizkreis 1) ordnen jeder Rolle die Namen zu, unter
denen EMS-ESP den Wert meldet – in der API und als Entität in Home Assistant. ``ROLE_LABELS`` beschriftet die
Rollen für die Oberfläche (Seite „Entitäten“, Diagnose). Nur Tabellen, keine Logik; ``discovery`` sucht damit die
Entitäten und reicht die Namen weiter.
"""

from __future__ import annotations

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
    "dhw_active": ("dhw_charging", "wwcharging", "dhw_active", "dhw_chargepump", "wwchargepump", "dhw_charge_pump",
                   "dhw_3wayvalve"),
    # Speicher-Soll; ältere EMS-ESP (vor 3.6) melden es als „wwseltemp“ (0.38, Weg W)
    "dhw_seltemp": ("dhw_seltemp", "dhw_selected_temperature", "wwseltemp"),
    "dhw_temp": ("dhw_curtemp", "dhw_current_intern_temperature"),
    "boiler_outdoor": ("outdoortemp", "outside_temperature"),
    "min_period": ("burnminperiod", "burner_min_period"),
    "hyst_on": ("boilhyston", "hysteresis_on_temperature"),
    "hyst_off": ("boilhystoff", "hysteresis_off_temperature"),
    "min_power": ("burnminpower", "burner_min_power"),
    "max_power": ("burnmaxpower", "burner_max_power"),
    "nom_power": ("nompower", "nominal_power", "nominal_Power"),
    "heating_off": ("heatingoff", "forceheatingoff", "force_heating_off"),
    # Heizbetrieb an/aus (0.41, Weg B): darauf schaltet HeatPilot mit „Kessel über HeatPilot“
    "heating_activated": ("heatingactivated", "heating_activated"),
    "boiler_temp": ("boiltemp", "actual_boiler_temperature"),
    "pump_mod": ("heatingpumpmod", "heating_pump_modulation"),
    "flame_current": ("flamecurr", "flame_current"),
    "burner_runtime": ("burnworkmin", "total_burner_operating_time"),
    "heat_starts": ("heatstarts", "burner_starts_heating"),
    "heat_runtime": ("heatworkmin", "total_heat_operating_time"),
    "nrg_total": ("nrgtotal", "total_energy"),
    "nrg_heat": ("nrgheat", "energy_heating"),
    # seit 0.25: Warmwasser-Details, Anlage, Wartung
    "dhw_starts": ("dhw_starts", "wwstarts"),
    "dhw_disinfecting": ("dhw_disinfecting", "wwdisinfecting"),
    # Desinfektionstemperatur des Kessels (0.36.3) – neuere EMS-ESP unter „dhw“, ältere als ww…
    "dhw_disinfect_temp": ("dhw_disinfectiontemp", "wwdisinfectiontemp", "dhw_disinfection_temperature"),
    "dhw_onetime": ("dhw_onetime", "wwonetime", "dhw_one_time_charging"),
    "dhw_hyst_on": ("dhw_hyston", "wwhyston", "dhw_hysteresis_on"),
    "pump_delay": ("pumpdelay", "pump_delay"),
    "sys_press": ("syspress", "system_pressure"),
    "service_code": ("servicecode", "service_code"),
    "last_code": ("lastcode", "last_error_code"),
    "maint_msg": ("maintenancemessage", "maintenance_message"),
    "maint_date": ("maintenancedate", "next_maintenance_date"),
}

# Regler (ES73/RC35 …), Heizkreis 1
THERMOSTAT_ROLES: dict[str, tuple[str, ...]] = {
    "hc_seltemp": ("hc1_seltemp", "hc1_selected_room_temperature"),
    "hc_calc_flow": ("hc1_targetflowtemp", "hc1_calcflowtemp", "hc1_target_flow_temperature"),
    "hc_mode": ("hc1_mode", "hc1_operating_mode"),
    "hc_remotetemp": ("hc1_remotetemp", "hc1_room_temperature_from_remote"),
    "damped_outdoor": ("dampedoutdoortemp", "damped_outdoor_temperature"),
    # Heizkurve des Reglers (seit 0.25)
    "hc_designtemp": ("hc1_designtemp", "hc1_design_temperature"),
    "hc_offsettemp": ("hc1_offsettemp", "hc1_offset_temperature"),
    "hc_minexttemp": ("minexttemp", "minimal_external_temperature"),
    "building": ("building", "building_type"),
    "disinfect_day": ("dhw_disinfectday", "wwdisinfectday", "dhw_disinfection_day"),
    "disinfect_hour": ("dhw_disinfecthour", "wwdisinfecthour", "dhw_disinfection_hour"),
}

# Beschriftungen für die Oberfläche (Seite „Entitäten“, Diagnose)
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
    "heating_activated": "Heizbetrieb aktiviert",
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
    "dhw_starts": "Warmwasser-Ladungen (Zähler)",
    "dhw_disinfecting": "Legionellenschaltung läuft",
    "dhw_disinfect_temp": "Desinfektion: Temperatur (Kessel)",
    "dhw_onetime": "Warmwasser-Einmalladung",
    "dhw_hyst_on": "Warmwasser: Nachladen ab (K unter Soll)",
    "pump_delay": "Pumpennachlauf (min)",
    "sys_press": "Anlagendruck (bar)",
    "service_code": "Kesselstatus (Servicecode)",
    "last_code": "Letzter Fehler",
    "maint_msg": "Wartungsmeldung",
    "maint_date": "Wartungstermin",
    "hc_designtemp": "Regler: Auslegungstemperatur (Heizkurve)",
    "hc_offsettemp": "Regler: Offset Heizkurve",
    "hc_minexttemp": "Regler: tiefste Außentemperatur (Auslegung)",
    "building": "Regler: Gebäudeart",
    "disinfect_day": "Legionellenschaltung: Tag",
    "disinfect_hour": "Legionellenschaltung: Uhrzeit",
}
