"""Sensoren, die es in Home Assistant nicht mehr gibt (seit 0.45.1): zählen als nicht vorhanden.

Nach dem Löschen einer alten Integration (Tado-Cloud) bzw. alter Thermostate stehen ihre Sensoren womöglich noch in den
Einstellungen eines Raums (Ventil / Heizleistung, Feuchte, Temperatur-Sensor). Gibt es einen weder in den Zuständen noch
in der Entitätenregistry, ist er gelöscht: ``drop`` nimmt Ventil- und Feuchtesensor beim Aufbau aus dem Raum – wie ein
leeres Feld, also kein „Sensor still, Ersatzwert“ auf Dauer und kein Fehler – und merkt sie in
``Discovered.sensors_gone``; die Einstellungen bleiben unverändert. Ein gelöschter Temperatur-Sensor speist nur die
Langzeitstatistik (live gilt die Temperatur des Thermostats): Er bleibt am Raum – seine Statistik kann in Home Assistant
weiter lesbar sein, und kein anderer Raum soll sie als alten Sensor bekommen –, zählt aber nirgends als Fehler.
(Verschwindet ein Ventilsensor im laufenden Betrieb, zählt er ab dem Entfernen ebenso nicht; meldet sich ein entfernter
doch, setzt ``back`` ihn wieder ein – ``Engine.on_state``.)
``for_settings`` gibt der Oberfläche je Thermostat die Felder mit gelöschtem Sensor („gibt es in
Home Assistant nicht mehr – kann leer bleiben“) und bietet die gefundene Heizleistung vom alten Thermostat
(``room_heating``) nur an, solange es sie gibt. Was HeatPilot von diesen Sensoren schon gespeichert hat, bleibt.

Ist die Registry unbekannt bzw. leer (Fehler beim Lesen), gilt jeder Sensor als vorhanden – lieber ein stiller Sensor
als ein fälschlich entfernter. Liest nur (Zustände, Registry); schreibt nichts an Home Assistant.
"""

from __future__ import annotations

import logging

from .discovery import Registry

log = logging.getLogger(__name__)

# Feld am Gerät (``Room.devices``) → Feld in den Einstellungen; ``DROP``: diese nimmt ``drop`` aus dem Raum
FIELDS = {"heating_power": "heating_power", "humidity": "humidity", "temp_sensor": "temperature"}
DROP = ("heating_power", "humidity")


def exists(eid: str | None, states: dict, idx) -> bool:
    """Gibt es die Entität (Zustand oder Registry)? Ohne bekannte Registry: ja."""
    if not eid or eid in states or idx is None or not idx.entity:
        return True
    return eid in idx.entity


def drop(found, states: dict, idx) -> dict[str, dict[str, str]]:
    """Gelöschte Ventil- und Feuchtesensoren aus den Räumen nehmen (nur zur Laufzeit) – {Thermostat: {Feld der
    Einstellungen: Entität}}."""
    gone: dict[str, dict[str, str]] = {}
    for r in found.rooms:
        for i, d in enumerate(r.devices):
            for dev_key in DROP:
                eid, cfg_key = d.get(dev_key), FIELDS[dev_key]
                if not eid or exists(eid, states, idx):
                    continue
                gone.setdefault(d["climate"], {})[cfg_key] = eid
                if i == 0:
                    setattr(r, dev_key, None)
                else:
                    d[dev_key] = None  # Mitläufer: ``devices`` gibt ihre eigenen Einträge
                log.info("Raum %s: %s gibt es in Home Assistant nicht mehr – zählt nicht (Einstellungen bleiben, "
                         "Gespeichertes bleibt)", r.name, eid)
    found.sensors_gone = gone
    return gone


def back(found, eid: str) -> bool:
    """Ein entfernter Sensor meldet sich doch (etwa später geladen, ohne Registry-Eintrag): zurück an sein Gerät."""
    dev_key = {v: k for k, v in FIELDS.items()}
    for climate, fields in list(found.sensors_gone.items()):
        for cfg_key, x in list(fields.items()):
            if x != eid:
                continue
            room = found.room_of(climate)
            dev = None if room is None else next((d for d in room.members if d["climate"] == climate), None)
            if dev is not None:
                dev[dev_key[cfg_key]] = eid
            elif room is not None:
                setattr(room, dev_key[cfg_key], eid)
            del fields[cfg_key]
            if not fields:
                del found.sensors_gone[climate]
            log.info("%s meldet sich wieder – zählt wieder", eid)
            return True
    return False


async def for_settings(app) -> dict:
    """Für ``GET api/settings``: gefundene Heizleistung nur, solange es sie gibt (``valve_found``), und je Thermostat
    die Felder mit gelöschtem Sensor (``sensors_gone``)."""
    try:
        idx = Registry.of(await app.registry()).lookup()
    except Exception as err:  # noqa: BLE001 – ohne Registry: alles gilt als vorhanden
        log.debug("Registry nicht lesbar: %s", err)
        idx = None
    states = app.eng.states
    gone: dict[str, dict[str, str]] = {}
    for r in app.cfg.rooms or []:
        for key in FIELDS.values():
            eid = r.get(key) if isinstance(r, dict) else None
            if eid and not exists(eid, states, idx):
                gone.setdefault(r["climate"], {})[key] = eid
    found = getattr(app.eng.found, "valve_found", None) or {}
    return {"valve_found": {k: v for k, v in found.items() if exists(v.get("sensor"), states, idx)},
            "sensors_gone": gone}
