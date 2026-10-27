"""Seite „Entitäten“ (seit 0.39.1): welche Entitäten HeatPilot liest bzw. beschreibt – geordnet nach Thema.

Je Rolle steht hier, wofür HeatPilot sie braucht (ein Satz), ob sie nötig ist, welche Entitäten zu ihr passen
(Domäne, Einheit, Geräteklasse – für die gefilterte Auswahl) und wo die Wahl gespeichert wird. ``build`` setzt daneben,
was gerade gilt: gewählt, automatisch erkannt, direkt von EMS-ESP gelesen oder fehlt – mit Zustand, Einheit, Bereich
und ob die Entität verfügbar ist. Gruppen: Kessel und Regler (EMS-ESP-Rollen aus ``discovery_roles``), Warmwasser über
HeatPilot (seit 0.42 mit dem Schalter der Desinfektion), Kessel über HeatPilot (seit 0.41: Vorlauf-Soll und
Heizbetrieb, Weg B), Außen & Wetter, Gas & Bedarf, Strom, Räume, Personen & Urlaub, Benachrichtigungen.

Nur lesen (``GET api/entity_map``). Gespeichert wird über die bestehenden Einstellungen (``POST api/settings``), es gibt
keine zweite Speicherstelle: Kessel- und Regler-Rollen als ``overrides`` (``rolle=entity_id``, leer = Erkennung), alles
andere in den vorhandenen Feldern, Raum-Sensoren am Raum-Eintrag (Räume-Tabelle). Personen nur mit Name und Zustand,
keine Koordinaten.

„Nötig“ heißt: ohne den Wert fehlt HeatPilot etwas ohne Ersatz – im Code geprüft:

* Vorlauf (Ist): Heizkurve, Wärmemodell und Vorschau; Ersatz nur EMS-ESP direkt.
* Brenner an oder Brennerleistung: eines genügt (Brenner an = Leistung über 0).
* Warmwasser lädt oder Speichertemperatur: eines genügt (eine Ladung erkennt HeatPilot auch am Temperaturanstieg und
  hält sie aus dem Heizmodell heraus); ohne Speichertemperatur gibt es kein Warmwasser-Modell.
* Außentemperatur: Außenfühler, sonst der Außenfühler am Kessel (als Entität in Home Assistant), sonst das Wetter.
* je Raum das Thermostat; die Warmwasser-Soll-Entität nur bei „HeatPilot setzt“, der Schalter der Desinfektion nur bei
  „HeatPilot setzt“ mit Desinfektionstermin; Vorlauf-Soll und Heizbetrieb nur bei „Kessel über HeatPilot: heatpilot“.

Der Rücklauf ist optional: Er dient nur der Vorlauf-Auswertung und der Live-Anzeige. Liest EMS-ESP einen Kessel- oder
Regler-Wert direkt (REST), fehlt er nicht – auch ohne Entität in Home Assistant. Ausnahme ist der Außenfühler am
Kessel: Als Außentemperatur dient nur eine Entität (``discovery``, ``engine_sample.outdoor_now``); den Wert direkt von
EMS-ESP zeigt die Seite nur an und sagt das dazu (``EMS_ONLY_SHOWN``). Ein Raum-Sensor, den es in Home Assistant nicht
mehr gibt (``sensor_gone``, seit 0.45.1), ist kein Problem: „gibt es nicht mehr“ (``gone``) – das Feld darf leer
bleiben.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import sensor_gone
from .boiler_entity import suggest_flow, suggest_heat
from .boiler_period import suggest as suggest_period
from .disinfect_entity import suggest as suggest_disinfect
from .dhw_entity import suggest_entity
from .discovery import device_sensors
from .discovery_roles import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES

SOURCE_SET, SOURCE_AUTO, SOURCE_EMS, SOURCE_MISSING = "gewählt", "automatisch", "EMS-ESP", "fehlt"
# Zustand einer Zeile: was fehlt (nötig), nicht verfügbar, nicht gefunden (optional, Erkennung fand nichts), nicht
# gewählt (optional ohne Erkennung) – der Filter „nur fehlende“ zeigt die ersten drei
ISSUE_MISSING, ISSUE_OFF, ISSUE_NOT_FOUND, ISSUE_NOT_SET = "fehlt", "nicht verfügbar", "nicht gefunden", "nicht gewählt"
ISSUE_GONE = "gibt es nicht mehr"  # 0.45.1: Raum-Sensor weder in den Zuständen noch in der Registry – kein Problem
ISSUE_REMOVED = "Regler abgebaut"  # 0.52.2: Werte des Reglers fehlen zu Recht – kein Problem
PROBLEMS = (ISSUE_MISSING, ISSUE_OFF, ISSUE_NOT_FOUND)
OFF_STATES = ("unavailable", "unknown")

GROUPS: dict[str, tuple[str, str]] = {
    "kessel": ("Kessel (EMS-ESP)", "Werte des Kessels über Home Assistant bzw. direkt von EMS-ESP – meist findet "
                                   "HeatPilot sie selbst."),
    "regler": ("Regler (EMS-ESP)", "Werte des Reglers, Heizkreis 1 – alle optional: Vergleich, Heizkurve, Termine."),
    "warmwasser": ("Warmwasser über HeatPilot", "Die Entitäten, auf die HeatPilot fürs Warmwasser schreiben darf "
                                                ": das Warmwasser-Soll und – zum Desinfektionstermin – der "
                                                "Schalter „Desinfizieren“."),
    "kessel_hp": ("Kessel über HeatPilot", "Die Entitäten, auf die HeatPilot am Kessel schreiben darf: "
                                           "Vorlauf-Soll und Heizbetrieb, seit 0.48 die Antipendelzeit."),
    "aussen": ("Außen & Wetter", "Außentemperatur und Vorhersage."),
    "gas": ("Gas & Anforderung", "Verbrauch und Anforderung."),
    "strom": ("Strom & Aktivität", "Hausleistung und Aktivität (Lampen, Bewegungsmelder …) für die Nachtzeiten."),
    "raeume": ("Räume", "Thermostate und Sensoren je Raum – geändert wird unter Einstellungen → Räume."),
    "personen": ("Personen & Urlaub", "Wer für die Anwesenheit zählt, und ein Urlaubs-Schalter."),
    "benachrichtigungen": ("Benachrichtigungen", "Ziele wählen, speichern, dann mit der Testnachricht prüfen."),
}

# Art des Werts -> (Domänen, Einheiten, Geräteklassen) für die Auswahl. Einheit "" = ohne Einheit; passt, wenn Domäne
# stimmt und – falls Einheiten oder Klassen verlangt sind – die Einheit oder die Geräteklasse
KINDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "temp": (("sensor", "number"), ("°C",), ("temperature",)),
    "delta": (("sensor", "number"), ("°C", "K"), ("temperature",)),
    "pct": (("sensor", "number"), ("%",), ()),
    "flag": (("binary_sensor", "switch", "sensor"), ("",), ()),
    "count": (("sensor", "counter"), ("",), ()),
    "minutes": (("sensor", "number"), ("min",), ("duration",)),
    "kw": (("sensor", "number"), ("kW",), ("power",)),
    "kwh": (("sensor",), ("kWh",), ("energy",)),
    "bar": (("sensor", "number"), ("bar",), ("pressure",)),
    "ua": (("sensor",), ("µA", "μA", "uA"), ("current",)),
    "text": (("sensor",), ("",), ()),
    "choice": (("select", "sensor"), ("",), ()),
    "any": (("sensor", "number", "select"), (), ()),
    "dhw": (("number",), ("°C",), ()),
    "flow_set": (("number",), ("°C",), ()),
    "heat_switch": (("switch",), (), ()),
    "period_set": (("number",), ("min", "minutes"), ()),  # 0.48: Antipendelzeit
    "disinfect_switch": (("switch",), (), ()),
    "weather": (("weather",), (), ()),
    "gas": (("sensor",), ("m³", "m3", "kWh"), ("gas", "energy")),
    "demand": (("sensor",), ("%",), ()),
    "power": (("sensor",), ("W", "kW"), ("power",)),
    "activity": (("light", "switch", "binary_sensor", "input_boolean", "media_player"), (), ()),  # 0.63
    "person": (("person",), (), ()),
    "toggle": (("input_boolean", "switch", "binary_sensor"), (), ()),
    "notify": (("notify",), (), ()),
    "climate": (("climate",), (), ()),
    "humidity": (("sensor",), ("%",), ("humidity",)),
}
# Auswahl in der Oberfläche mit eigener Regel (PICKERS): Warmwasser-Soll nur, was laut dhw_entity taugt; Vorlauf-Soll
# und Heizbetrieb (0.41) nur, was laut boiler_entity taugt (``api/entities`` → ``flow`` bzw. ``heating``); der Schalter
# der Desinfektion (0.42) nur, was laut disinfect_entity taugt (``disinfect``)
PICKER = {"dhw": "dhw", "flow_set": "flow", "heat_switch": "heating", "disinfect_switch": "disinfect",
          "period_set": "period"}  # 0.48: Antipendelzeit (boiler_period.picker)

# EMS-ESP-Rolle -> (Art, wofür HeatPilot sie braucht)
ROLES: dict[str, tuple[str, str]] = {
    "flow_temp": ("temp", "Daraus lernt HeatPilot Heizkurve und Wärmemodell; ohne ihn keine Vorschau."),
    "sel_flow_temp": ("temp", "Vorlauf-Soll am Kessel – Vergleich mit der Vorschau, Vorschlag für „Kessel über "
                              "HeatPilot“."),
    "return_temp": ("temp", "Nur für die Spreizung in der Vorlauf-Auswertung und die Live-Anzeige."),
    "burner_power": ("pct", "Wärmemenge je Takt; ersetzt „Brenner an“ (Leistung über 0)."),
    "burner_on": ("flag", "Laufzeiten, Starts und Taktsperre; ersetzbar durch die Brennerleistung."),
    "burner_starts": ("count", "Takten zählen – Wochenbericht und Ventile (Beobachten)."),
    "heating_pump": ("flag", "Trennt Heizbetrieb und Warmwasser; ohne sie gilt „Brenner an ohne Warmwasser“."),
    "heating_active": ("flag", "Nur mitgelesen (Diagnose)."),
    "dhw_active": ("flag", "Ladungen erkennen und aus dem Heizmodell heraushalten; ersetzbar durch die "
                           "Speichertemperatur."),
    "dhw_seltemp": ("temp", "Warmwasser-Soll des Kessels – Strategie, Vergleich mit der Empfehlung, Warmwasser über HeatPilot."),
    "dhw_temp": ("temp", "Warmwasser-Modell und Empfehlung; erkennt auch Ladungen am Temperaturanstieg."),
    "boiler_outdoor": ("temp", "Außentemperatur, wenn kein eigener Außenfühler gewählt ist – nur als Entität in Home "
                               "Assistant."),
    "min_period": ("minutes", "Taktsperre in Live und in der Kesselauswertung."),
    "hyst_on": ("delta", "Anzeige in der Kesselauswertung."),
    "hyst_off": ("delta", "Anzeige in der Kesselauswertung."),
    "min_power": ("pct", "Fürs Zertifikat, wenn die kleinste Leistung nicht eingetragen ist."),
    "max_power": ("pct", "Nur mitgelesen (Diagnose)."),
    "nom_power": ("kw", "Rechnet Prozent in kW um (Zertifikat, Live), wenn die Nennleistung nicht eingetragen ist."),
    "heating_off": ("flag", "Heizbetrieb aus erkennen; ist es an, schreibt „Kessel über HeatPilot“ nicht."),
    "heating_activated": ("flag", "Heizbetrieb an/aus – Vorschlag für „Kessel über HeatPilot“ und Prüfung."),
    "boiler_temp": ("temp", "Live-Anzeige und Plausibilität."),
    "pump_mod": ("pct", "Läuft die Pumpe? – Live und Lernen."),
    "flame_current": ("ua", "Nur aufgezeichnet (Diagnose)."),
    "burner_runtime": ("minutes", "Nur mitgelesen (Diagnose)."),
    "heat_starts": ("count", "Takten beim Heizen – Kesselauswertung und Zertifikat."),
    "heat_runtime": ("minutes", "Starts je Laufstunde in der Kesselauswertung."),
    "nrg_total": ("kwh", "Ersatz, wenn kein Gaszähler da ist (Energie-Schätzung)."),
    "nrg_heat": ("kwh", "Zweiter Ersatz für den Gaszähler."),
    "dhw_starts": ("count", "Warmwasser-Ladungen sicher erkennen."),
    "dhw_disinfecting": ("flag", "Solche Ladungen lernt das Modell nicht; bestätigt den Termin; Vorschlag für "
                                 "„Desinfektion auslösen“."),
    "dhw_disinfect_temp": ("temp", "Desinfektionstemperatur – sonst gilt der eingestellte Wert."),
    "dhw_onetime": ("flag", "Einmalladungen lernt das Modell nicht."),
    "dhw_hyst_on": ("delta", "Wann der Speicher wieder lädt – Warmwasser-Modell."),
    "pump_delay": ("minutes", "Live-Anzeige."),
    "sys_press": ("bar", "Warnung bei zu wenig Anlagendruck."),
    "service_code": ("text", "Kesselstatus in Live."),
    "last_code": ("text", "Hinweis in der Diagnose."),
    "maint_msg": ("text", "Hinweis in der Diagnose."),
    "maint_date": ("text", "Hinweis in der Diagnose."),
    "hc_seltemp": ("temp", "Raum-Soll des Reglers – aufgezeichnet und angezeigt."),
    "hc_calc_flow": ("temp", "Vorlauf des Reglers – Vergleich „Plan gegen Regler“ in der Vorschau."),
    "hc_mode": ("choice", "Heizfreigabe des Reglers (Tag = heizen) erkennen."),
    "hc_remotetemp": ("temp", "Raumtemperatur der Fernbedienung – aufgezeichnet."),
    "damped_outdoor": ("temp", "Nur mitgelesen (Diagnose)."),
    "hc_designtemp": ("temp", "Heizkurve des Reglers – Vergleich in der Kesselauswertung."),
    "hc_offsettemp": ("delta", "Heizkurve des Reglers – Vergleich in der Kesselauswertung."),
    "hc_minexttemp": ("temp", "Heizkurve des Reglers – Vergleich in der Kesselauswertung."),
    "building": ("choice", "Nur mitgelesen (Diagnose)."),
    "disinfect_day": ("choice", "Desinfektionstermin des Reglers."),
    "disinfect_hour": ("any", "Desinfektionstermin des Reglers."),
}
# Rollen, deren Wert direkt von EMS-ESP nur angezeigt wird (HeatPilot liest sie nur als Entität) -> Hinweis dazu
EMS_ONLY_SHOWN: dict[str, str] = {
    "boiler_outdoor": "Nur Anzeige: Der Wert direkt von EMS-ESP dient nicht als Außentemperatur – dafür hier eine "
                      "Entität wählen, einen Außenfühler oder das Wetter (Außen & Wetter).",
}
# Nötige Rollen -> Rollen, die sie ersetzen (eine davon genügt)
NEED: dict[str, tuple[str, ...]] = {"flow_temp": (), "burner_on": ("burner_power",), "burner_power": ("burner_on",),
                                    "dhw_active": ("dhw_temp",), "dhw_temp": ("dhw_active",)}

# Feld der Einstellungen -> (Gruppe, Beschriftung, Art, mehrere, Erkennung, Zweck)
FIELD_ENTRIES: dict[str, tuple[str, str, str, bool, bool, str]] = {
    "dhw_set_entity": ("warmwasser", "Warmwasser-Soll (Entität)", "dhw", False, True,
                       "Nur hierhin schreibt HeatPilot bei „HeatPilot setzt“; leer = Vorschlag aus der Erkennung."),
    "disinfect_entity": ("warmwasser", "Desinfektion auslösen (Schalter)", "disinfect_switch", False, True,
                         "Nur diesen Schalter schaltet HeatPilot zum Desinfektionstermin (switch „Desinfizieren“, "
                         "wwdisinfecting) – bei „HeatPilot setzt“ ohne Regler am Bus; leer = Vorschlag aus der "
                         "Erkennung."),
    "flow_set_entity": ("kessel_hp", "Vorlauf-Soll (Entität)", "flow_set", False, True,
                        "Nur hierhin schreibt HeatPilot das Vorlauf-Soll bei „Kessel über HeatPilot“ (number, °C); leer "
                        "= Vorschlag aus der Erkennung."),
    "heating_switch_entity": ("kessel_hp", "Heizbetrieb (Entität)", "heat_switch", False, True,
                              "Nur diesen Schalter schaltet HeatPilot bei „Kessel über HeatPilot“ (switch, Heizbetrieb "
                              "aktiviert); leer = Vorschlag aus der Erkennung."),
    "period_set_entity": ("kessel_hp", "Antipendelzeit (Entität)", "period_set", False, True,
                          "Nur hierhin schreibt HeatPilot die Antipendelzeit bei „Antipendelzeit anpassen“ (number, "
                          "Minuten, EMS-ESP burnminperiod); leer = Vorschlag aus der Erkennung."),
    "outdoor_sensors": ("aussen", "Außenfühler", "temp", True, True,
                        "Außentemperatur für Modelle und Vorschau – bei mehreren zählt das Minimum; leer = Außenfühler "
                        "am Kessel aus Home Assistant."),
    "weather_entity": ("aussen", "Wetter", "weather", False, True,
                       "Vorhersage für Vorheizen und Vorschau, Bewölkung für die Sonnenanteile; ohne Außenfühler auch "
                       "die Außentemperatur."),
    "gas_sensor": ("gas", "Gaszähler", "gas", False, True,
                   "Zählerstand in m³ oder kWh – Wärmebedarf und Kosten; ohne Zähler die Energie-Schätzung von "
                   "EMS-ESP."),
    "demand_sensor": ("gas", "Heizungsanforderung", "demand", False, False,
                      "Sensor einer bestehenden Automation (z. B. Ventile in %) – wird nur aufgezeichnet."),
    "power_sensors": ("strom", "Leistungssensoren (Haus, ggf. je Phase)", "power", True, False,
                      "Summe = Hausleistung (W oder kW, bei drei Phasen alle drei) – daraus lernt HeatPilot die "
                      "Nachtzeiten."),
    "power_exclude": ("strom", "Nicht zählen (Waschmaschine, Wallbox …)", "power", True, False,
                      "Verbraucher, die auch nachts laufen – werden von der Hausleistung abgezogen."),
    "activity_sensors": ("strom", "Aktivität (Lampen, Bewegungsmelder …)", "activity", True, False,
                         "Sensor-Eimer: jede Entität, die an ist, zählt als „wach“ – daraus lernt HeatPilot mit der "
                         "Hausleistung Schlafens- und Aufwachzeit. Ein kurzes Licht nachts (Toilette) weckt nicht."),
    "persons": ("personen", "Personen", "person", True, True,
                "Ohne Häkchen zählen automatisch alle Personen mit Home-Assistant-Benutzer und Gerät."),
    "vacation_entity": ("personen", "Urlaubs-Schalter", "toggle", False, False,
                        "An = Urlaub – zusätzlich zu den eingetragenen Urlauben."),
    "notify_targets": ("benachrichtigungen", "Benachrichtigungsziele", "notify", True, False,
                       "notify-Entitäten oder -Dienste; ohne Ziel keine Meldungen."),
}
# Raum-Eintrag: Feld -> (Beschriftung, Art, nötig, Zweck)
ROOM_FIELDS: dict[str, tuple[str, str, bool, str]] = {
    "climate": ("Thermostat", "climate", True, "Raumtemperatur, Soll und Ventil; nur hierhin schreibt HeatPilot bei "
                                               "„macht mit“."),
    "temperature": ("Temperatur-Sensor", "temp", False, "Eigener Sensor für die Langzeitstatistik."),
    "humidity": ("Feuchte", "humidity", False, "Luftfeuchte des Raums."),
    "heating_power": ("Ventil / Heizleistung", "pct", False,
                      "Ventilöffnung in % – Ventil-Lernen und Wärmebedarf; sonst aus hvac_action."),
}
_ROOM_SENSOR = {"temperature": "temp_sensor", "humidity": "humidity", "heating_power": "heating_power"}


def parse_overrides(value) -> dict[str, str]:
    """Gespeicherte Zuordnungen ``rolle=entity_id`` lesen (``Config.from_raw``); Unlesbares – leer, ohne „=“, keine
    Liste, kein Text – überspringen: alte bzw. von Hand geänderte Einstellungen laden immer."""
    items = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
    out = {}
    for item in items:
        role, sep, ent = item.partition("=") if isinstance(item, str) else ("", "", "")
        if sep and role.strip() and ent.strip():
            out[role.strip()] = ent.strip()
    return out


def migrate_overrides(cfg) -> None:
    """Alte Zuordnungen ``gas=…``/``weather=…`` (frühere Add-on-Optionen) in die Felder Gaszähler und Wetter – dort
    zeigt sie die Seite „Entitäten“. Die Zuordnung hatte Vorrang vor dem Feld, deshalb ersetzt sie es."""
    for role, key in (("gas", "gas_sensor"), ("weather", "weather_entity")):
        entity = (cfg.overrides or {}).pop(role, None)
        if entity:
            setattr(cfg, key, entity)


def filter_of(kind: str) -> dict:
    domains, units, classes = KINDS[kind]
    out = {"domains": list(domains), "units": list(units), "device_classes": list(classes)}
    return {**out, "picker": PICKER[kind]} if kind in PICKER else out


def fits(entity_id: str, unit, device_class, filt: dict) -> bool:
    """Passt die Entität zur Rolle? Dieselbe Regel nutzt die Auswahl in der Oberfläche (``emFits``)."""
    domains, units, classes = filt.get("domains") or [], filt.get("units") or [], filt.get("device_classes") or []
    if domains and entity_id.split(".", 1)[0] not in domains:
        return False
    if not units and not classes:
        return True
    return str(unit or "") in units or (device_class is not None and device_class in classes)


@dataclass
class _Ctx:
    cfg: object
    found: object
    auto: dict
    states: dict
    idx: object | None
    ems: dict
    services: dict | None


def _item(c: _Ctx, eid: str, filt: dict, person: bool = False) -> dict:
    """Zustand einer Entität; Personen nur mit Name und Zustand (keine Attribute, keine Koordinaten)."""
    st = c.states.get(eid)
    a = (st or {}).get("attributes") or {}
    out = {"entity_id": eid, "friendly_name": a.get("friendly_name") or eid, "exists": st is not None,
           "state": None if st is None else st.get("state"),
           "available": st is not None and st.get("state") not in OFF_STATES}
    if person:
        return out
    unit = a.get("unit_of_measurement")
    return {**out, "unit": unit, "area": c.idx.area_of(eid) if c.idx is not None else None,
            "fits": None if st is None else fits(eid, unit, a.get("device_class"), filt)}


def _notify_item(c: _Ctx, eid: str) -> dict:
    """Ziel als Entität (Zustand „unknown“ ist normal) oder als Dienst; Dienste unbekannt: verfügbar offen."""
    st = c.states.get(eid)
    if st is not None:
        name = (st.get("attributes") or {}).get("friendly_name") or eid
        return {"entity_id": eid, "friendly_name": name, "exists": True, "state": st.get("state"),
                "available": st.get("state") != "unavailable", "kind": "entity"}
    if c.services is None:
        return {"entity_id": eid, "friendly_name": eid, "exists": None, "state": None, "available": None, "kind": None}
    ok = eid.split(".", 1)[1] in ((c.services or {}).get("notify") or {})
    return {"entity_id": eid, "friendly_name": eid.split(".", 1)[1].replace("_", " "), "exists": ok, "state": None,
            "available": ok, "kind": "service" if ok else None}


def _base(key, group, label, kind, purpose, setting, *, multi=False, auto=True, required=False, alt=()) -> dict:
    return {"key": key, "group": group, "label": label, "purpose": purpose, "required": required, "alt": list(alt),
            "multi": multi, "auto": auto, "filter": filter_of(kind), "setting": setting, "fallback": None,
            "ems_key": None}


def _fill(e: dict, c: _Ctx, chosen, detected, ems_value=None) -> dict:
    """current (gewählt, sonst erkannt), source und Zustand; ohne beides der Wert von EMS-ESP direkt, falls da."""
    blank = {"state": None, "unit": None, "available": None, "friendly_name": None, "area": None, "exists": None,
             "fits": None}
    if e["multi"]:
        chosen = [x for x in chosen or [] if x]
        detected = [x for x in detected or [] if x] if e["auto"] else []
        current = chosen or detected
        if e["key"] == "notify_targets":
            items = [_notify_item(c, x) for x in current]
        else:
            items = [_item(c, x, e["filter"], person=e["key"] == "persons") for x in current]
        e.update(blank, chosen=chosen, detected=detected, current=current, items=items,
                 source=SOURCE_SET if chosen else SOURCE_AUTO if current else SOURCE_MISSING,
                 available=all(i["available"] is not False for i in items) if items else None)
        return e
    chosen = chosen or ""
    detected = (detected or None) if e["auto"] else None
    current = chosen or detected
    e.update(blank, chosen=chosen, detected=detected, current=current or None)
    if current:
        e.update({k: v for k, v in _item(c, current, e["filter"]).items() if k != "entity_id"},
                 source=SOURCE_SET if chosen else SOURCE_AUTO)
    elif ems_value is not None:
        e.update(source=SOURCE_EMS, state=ems_value, available=True)
    else:
        e["source"] = SOURCE_MISSING
    return e


def _detected(c: _Ctx, device: str, role: str):
    """Was die Erkennung ohne Zuordnung fände (Vorschläge aus ``App.suggestions``); fehlen sie, das Gefundene, solange
    keine Zuordnung es verdeckt."""
    if device in c.auto:
        return (c.auto.get(device) or {}).get(role)
    return None if role in c.cfg.overrides else (getattr(c.found, device, None) or {}).get(role)


def _ems_entries(c: _Ctx) -> list[dict]:
    """Kessel- und Regler-Rollen, nötige zuerst; gespeichert als ``overrides`` (rolle=entity_id)."""
    out = []
    for group, device, roles in (("kessel", "boiler", BOILER_ROLES), ("regler", "thermostat", THERMOSTAT_ROLES)):
        rest = (c.ems or {}).get(device) or {}
        for role in sorted(roles, key=lambda r: r not in NEED):
            kind, purpose = ROLES[role]
            e = _base(role, group, ROLE_LABELS.get(role, role), kind, purpose,
                      {"kind": "override", "name": "overrides", "role": role},
                      required=role in NEED, alt=NEED.get(role, ()))
            key = next((k for k in roles[role] if k in rest), None)
            _fill(e, c, c.cfg.overrides.get(role), _detected(c, device, role), rest.get(key) if key else None)
            e["ems_key"] = f"{device}/{key}" if key else None
            if e["source"] == SOURCE_EMS and role in EMS_ONLY_SHOWN:
                e["fallback"] = EMS_ONLY_SHOWN[role]
            out.append(e)
    return out


def _field_values(c: _Ctx) -> dict:
    """Feld -> (gewählt, erkannt). Erkannt aus den Vorschlägen (Erkennung ohne eigene Vorgaben), sonst das Gefundene."""
    cfg, f, a = c.cfg, c.found, c.auto

    def auto(key, own, found_value):
        return a[key] if key in a else (None if own else found_value)

    return {
        "dhw_set_entity": (cfg.dhw_set_entity, suggest_entity(f, c.states)),
        "disinfect_entity": (cfg.disinfect_entity, suggest_disinfect(f, c.states)),
        "flow_set_entity": (cfg.flow_set_entity, suggest_flow(f, c.states)),
        "heating_switch_entity": (cfg.heating_switch_entity, suggest_heat(f, c.states)),
        "period_set_entity": (cfg.period_set_entity, suggest_period(f, c.states)),  # 0.48
        "outdoor_sensors": (cfg.outdoor_sensors, auto("outdoor", cfg.outdoor_sensors, f.outdoor)),
        "weather_entity": (cfg.weather_entity, auto("weather", cfg.weather_entity, f.weather)),
        "gas_sensor": (cfg.gas_sensor, auto("gas", cfg.gas_sensor, f.gas)),
        "demand_sensor": (cfg.demand_sensor, None), "power_sensors": (cfg.power_sensors, None),
        "power_exclude": (cfg.power_exclude, None), "activity_sensors": (cfg.activity_sensors, None),
        "persons": (cfg.persons, auto("persons", cfg.persons, f.persons)),
        "vacation_entity": (cfg.vacation_entity, None), "notify_targets": (cfg.notify_targets, None),
    }


def _field_entries(c: _Ctx) -> list[dict]:
    """Warmwasser-Soll, Außen & Wetter, Gas & Bedarf, Strom, Personen & Urlaub, Benachrichtigungen."""
    values, out = _field_values(c), []
    for key, (group, label, kind, multi, auto, purpose) in FIELD_ENTRIES.items():
        required = key == "outdoor_sensors" or (key == "dhw_set_entity" and c.cfg.dhw_control == "heatpilot") or (
            key in ("flow_set_entity", "heating_switch_entity") and c.cfg.boiler_control == "heatpilot") or (
            key == "period_set_entity" and c.cfg.boiler_control == "heatpilot" and c.cfg.boiler_period) or (
            key == "disinfect_entity" and c.cfg.dhw_control == "heatpilot" and bool(c.cfg.dhw_disinfect_day))
        e = _base(key, group, label, kind, purpose, {"kind": "field", "name": key}, multi=multi, auto=auto,
                  required=required, alt=("weather_entity",) if key == "outdoor_sensors" else ())
        _fill(e, c, *values[key])
        out.append(e)
    gas = next(e for e in out if e["key"] == "gas_sensor")
    nrg = {(c.auto.get("boiler") or {}).get(r) for r in ("nrg_total", "nrg_heat")} | {
        (getattr(c.found, "boiler", None) or {}).get(r) for r in ("nrg_total", "nrg_heat")}
    if gas["current"] and gas["current"] in nrg - {None}:
        gas["fallback"] = "Ersatz: Energie-Schätzung von EMS-ESP – kein Gaszähler gefunden"
    elif gas["chosen"] and not gas["exists"] and c.found.gas and c.found.gas != gas["chosen"]:
        gas["fallback"] = f"nicht in Home Assistant – HeatPilot nutzt stattdessen {c.found.gas}"
    return out


def _room_rows(c: _Ctx) -> list[tuple[int | None, str, dict]]:
    """(Index in den Einstellungen, Anzeigename, Raum-Eintrag); ohne eigene Räume die erkannten Geräte."""
    if c.cfg.rooms:
        return [(i, (r.get("group") or r.get("name") or "").strip() or r["climate"], r)
                for i, r in enumerate(c.cfg.rooms)]
    return [(None, r.name, {"climate": d["climate"], "temperature": d.get("temp_sensor"), "humidity": d.get("humidity"),
                            "heating_power": d.get("heating_power")}) for r in c.found.rooms for d in r.devices]


def _room_entries(c: _Ctx) -> list[dict]:
    """Je Raum-Eintrag Thermostat und Sensoren. Leere Sensorfelder nutzt HeatPilot nicht – der Sensor am Gerät steht
    dann nur als Vorschlag dabei (die Räume-Tabelle belegt ihn vor)."""
    out, own = [], bool(c.cfg.rooms)
    for i, room, r in _room_rows(c):
        sib = device_sensors(r["climate"], c.idx, c.states) if own and c.idx is not None else {}
        for field, (label, kind, required, purpose) in ROOM_FIELDS.items():
            e = _base(f"rooms.{r['climate']}.{field}", "raeume", label, kind, purpose,
                      {"kind": "room", "index": i, "field": field}, auto=not own, required=required)
            value = r.get(field) or None
            _fill(e, c, value if own else None, None if own else value)
            e.update(room=room, room_climate=r["climate"],
                     suggested=sib.get(_ROOM_SENSOR.get(field, "")) if not value and field != "climate" else None,
                     gone=value if value and field != "climate" and not sensor_gone.exists(value, c.states, c.idx)
                     else None)
            out.append(e)
    return out


def _needs(entries: list[dict], removed: bool = False) -> None:
    """Ersatz (``covered_by``), ``missing`` (nötig, fehlt ohne Ersatz) und ``issue`` je Eintrag. ``removed``: der Regler
    ist abgebaut – seine Werte fehlen zu Recht (``ISSUE_REMOVED``, kein Problem)."""
    by_key = {e["key"]: e for e in entries}
    for e in entries:
        e["removed"] = removed and e["group"] == "regler" and not e["required"]
        if e["removed"]:
            e.update(covered_by=None, missing=False, issue=ISSUE_REMOVED, problem=False)
            continue
        alt = [by_key[k] for k in e["alt"] if k in by_key and by_key[k]["source"] != SOURCE_MISSING]
        e["covered_by"] = alt[0]["label"] if alt and e["source"] == SOURCE_MISSING else None
        e["missing"] = bool(e["required"] and e["source"] == SOURCE_MISSING and not alt)
        if e["missing"]:
            e["issue"] = ISSUE_MISSING
        elif e.get("gone"):
            e["issue"] = ISSUE_GONE
        elif e["available"] is False and not e["ems_key"]:
            e["issue"] = ISSUE_OFF
        elif e["source"] == SOURCE_MISSING and not e["covered_by"]:
            e["issue"] = ISSUE_NOT_FOUND if e["auto"] and e["group"] != "raeume" else ISSUE_NOT_SET
        else:
            e["issue"] = None
        e["problem"] = e["issue"] in PROBLEMS


def counts(entries: list[dict]) -> dict:
    """Zähler für die Überschrift einer Gruppe: belegt von gesamt, nötige fehlen, nicht verfügbar, nicht gefunden."""
    return {"total": len(entries), "found": sum(e["source"] != SOURCE_MISSING for e in entries),
            "required": sum(e["required"] for e in entries), "missing": sum(e["missing"] for e in entries),
            "unavailable": sum(e["issue"] == ISSUE_OFF for e in entries),
            "not_found": sum(e["issue"] == ISSUE_NOT_FOUND for e in entries),
            "problems": sum(e["problem"] for e in entries), "auto": any(e["auto"] for e in entries)}


def regulator_removed(cfg, ems: dict | None) -> bool:
    """Regler abgebaut (0.52.2): vom Nutzer bestätigt, oder EMS-ESP antwortet und meldet kein Gerät „thermostat“."""
    return bool(getattr(cfg, "regulator_removed", False) or (ems and ems.get("boiler") and not ems.get("thermostat")))


def build(cfg, found, auto: dict | None, states: dict, idx=None, ems: dict | None = None,
          services: dict | None = None) -> dict:
    """Alle Rollen in Gruppen mit dem, was gilt. ``auto``: Erkennung ohne eigene Vorgaben (``Discovered.as_dict``),
    ``idx``: ``RegistryIndex`` (Bereiche), ``ems``: Werte von EMS-ESP direkt, ``services``: Dienste von Home
    Assistant (für Benachrichtigungsziele; None = unbekannt)."""
    c = _Ctx(cfg, found, dict(auto or {}), states or {}, idx, ems or {}, services)
    entries = _ems_entries(c) + _field_entries(c) + _room_entries(c)
    _needs(entries, regulator_removed(cfg, c.ems))
    groups = [{"key": key, "title": title, "text": text, "entries": [e for e in entries if e["group"] == key]}
              for key, (title, text) in GROUPS.items()]
    for g in groups:
        g["counts"] = counts(g["entries"])
    extra = [{"role": r, "entity": x} for r, x in (cfg.overrides or {}).items()
             if r not in BOILER_ROLES and r not in THERMOSTAT_ROLES]
    return {"groups": groups, "counts": counts(entries), "extra_overrides": extra,
            "sources": [SOURCE_SET, SOURCE_AUTO, SOURCE_EMS, SOURCE_MISSING], "issues": list(PROBLEMS)}
