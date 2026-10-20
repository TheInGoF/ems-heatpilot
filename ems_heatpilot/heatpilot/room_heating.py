"""Heizleistung vom alten Thermostat (seit 0.45): Ventil in % für Räume ohne eigenen Ventilsensor.

Nach dem Wechsel Tado-Cloud → HomeKit hat das neue Gerät keinen Sensor „Heizleistung“ mehr – das Ventil kennt HeatPilot
dann nur als 0/100 % aus ``hvac_action``. Läuft die alte Integration weiter, meldet ihr Sensor am alten Gerät die
Heizleistung der Zone weiter. ``find`` sucht ihn je Raum, der an keinem Gerät einen Ventil-/Heizleistungssensor hat:

* ``adopted`` – am Gerät eines alten Thermostats des Raums (``rooms.adopted`` „from“, ``rooms.aliases`` „from“) laut
  Registry, mit der Erkennung der Geschwister-Sensoren (``discovery.device_sensors``);
* ``alias`` – am Gerät des alten Temperatursensors (Alias) des Raums;
* ``area`` / ``name`` – sonst genau ein Tado-Heizleistungssensor (Übersetzungsschlüssel bzw. unique_id „heating“) im
  gleichen Bereich bzw. mit dem Namen des Raums oder eines alten Thermostats.

Ein Sensor, den ein Raum schon nutzt oder der an einem aktuellen Thermostat hängt, zählt nicht; passt einer zu zwei
Räumen, bekommt ihn keiner. ``attach`` setzt den Fund zur Laufzeit wie einen Geschwister-Sensor ein
(``Room.heating_power`` des Schlüssels) – die Einstellungen bleiben unverändert, ein eingetragener Sensor hat immer
Vorrang. Die Oberfläche zeigt den Fund mit Herkunft im Feld „Ventil / Heizleistung“ (``Discovered.valve_found``), der
Nutzer übernimmt ihn mit Speichern.

``stale``: Ein Ventilsensor, der seit ``STALE_S`` keinen neuen Wert gemeldet hat und dem das Thermostat widerspricht
(heizt bei 0 %, ruht bei mehr als 0 %), gilt wie ein stiller Sensor als Ersatzwert (``engine_sample``).

Liest nur (Zustände, Registry, kv); schreibt nichts an Home Assistant und ändert keine Einstellungen.
"""

from __future__ import annotations

import logging

from . import room_adopt, room_alias
from .discovery import device_sensors
from .values import parse_time

log = logging.getLogger(__name__)

HOW_TEXT = {"adopted": "vom alten Thermostat", "alias": "vom Gerät des alten Temperatursensors",
            "area": "aus dem gleichen Bereich", "name": "mit passendem Namen"}
# Wörter in Namen von Heizleistungssensoren, die keinen Raum beschreiben (zusätzlich zu ``room_adopt.NOISE``)
HEAT_WORDS = {"heizleistung", "leistung", "power", "heizen", "verwarming", "chauffage", "percentage", "prozent"}
STALE_S = 6 * 3600          # so lange ohne neuen Wert (und im Widerspruch zum Thermostat) = veraltet
ACTIONS = ("heating", "idle", "off")


def hnorm(text) -> str:
    """Vergleichsform eines Heizleistungssensors bzw. Raumnamens („sensor.kuche_heating“ → „kuche“)."""
    t = str(text or "")
    if t.startswith("sensor."):
        t = t[len("sensor."):]
    return " ".join(w for w in room_adopt.norm(t.replace("_", " ")).split() if w not in HEAT_WORDS)


def _attrs(states: dict, eid: str) -> dict:
    return (states.get(eid) or {}).get("attributes") or {}


def _unit_ok(sid: str, states: dict) -> bool:
    """In % (oder ohne bekannte Einheit, etwa solange der Sensor nicht verfügbar ist)."""
    return _attrs(states, sid).get("unit_of_measurement") in (None, "%")


def is_tado_heating(sid: str, idx) -> bool:
    """Heizleistung der Tado-Integration: Übersetzungsschlüssel bzw. unique_id „heating“ (sprachunabhängig)."""
    e = idx.entity.get(sid, {})
    return e.get("translation_key") == "heating" or str(e.get("unique_id", "")).endswith(" heating")


def on_device(entity: str, idx, states: dict) -> str | None:
    """Heizleistung am Gerät von ``entity`` (altes Thermostat oder alter Temperatursensor) laut Registry."""
    if idx is None or entity not in idx.entity:
        return None
    sid = device_sensors(entity, idx, states)["heating_power"]
    return sid if sid and _unit_ok(sid, states) else None


def old_sources(store, room) -> list[tuple[str, str]]:
    """Alte Geräte eines Raums [(Entität, wie)]: übernommene alte Schlüssel (zuletzt übernommen zuerst), ``from`` des
    Alias, der Alias-Sensor selbst."""
    out = [(old, "adopted") for old in reversed(room_alias.adopted_from(store, room.climate))]
    alias = room_alias.get(store).get(room.climate) or {}
    if alias.get("from"):
        out.append((alias["from"], "adopted"))
    if alias.get("stat"):
        out.append((alias["stat"], "alias"))
    seen: set[str] = set()
    return [(e, how) for e, how in out if not (e in seen or seen.add(e))]


def labels(store, states: dict, room) -> set[str]:
    """Namen des Raums (Anzeigename, Raum, Bereich) und seiner alten Thermostate in Vergleichsform."""
    out = {room.name, room.group or "", room.area or ""}
    for old, how in old_sources(store, room):
        if how == "adopted":
            out |= room_alias.old_labels(store, states, old)
    return {hnorm(x) for x in out} - {""}


def taken(found, idx, states: dict) -> set[str]:
    """Sensoren, die kein Fund sein können: eingetragene Ventilsensoren und Heizleistung an aktuellen Thermostaten."""
    ids = {d.get("heating_power") for r in found.rooms for d in r.devices}
    if idx is not None:
        ids |= {device_sensors(c, idx, states)["heating_power"] for c in found.climate_ids() if c in idx.entity}
    return {i for i in ids if i}


def by_place(store, room, idx, states: dict, exclude: set[str]) -> tuple[str, str] | None:
    """Genau ein Tado-Heizleistungssensor im Bereich des Raums, sonst genau einer mit passendem Namen."""
    cands = [s for s in idx.entity if s.startswith("sensor.") and s not in exclude and is_tado_heating(s, idx)
             and _unit_ok(s, states)]
    if room.area:
        hits = [s for s in cands if idx.area_of(s) == room.area]
        if len(hits) == 1:
            return hits[0], "area"
    want = labels(store, states, room)
    hits = [s for s in cands if {hnorm(s), hnorm(_attrs(states, s).get("friendly_name"))} & want]
    return (hits[0], "name") if len(hits) == 1 else None


def _entry(store, states: dict, sid: str, frm: str | None, how: str) -> dict:
    name = _attrs(states, sid).get("friendly_name") or sid
    from_name = None
    if frm:
        from_name = room_adopt.name_of(store, states, frm) if frm.startswith("climate.") else \
            (_attrs(states, frm).get("friendly_name") or frm)
    return {"sensor": sid, "name": name, "from": frm, "from_name": from_name, "how": how, "how_text": HOW_TEXT[how]}


def from_old(store, found, room, idx, states: dict, exclude: set[str]) -> dict | None:
    """Heizleistung am Gerät eines alten Thermostats bzw. des Alias (``old_sources``) oder None. Ein altes Thermostat,
    das wieder zu einem anderen Raum gehört, zählt nicht."""
    current = set(found.climate_ids()) - set(room.climates)
    for ent, how in old_sources(store, room):
        sid = None if ent in current else on_device(ent, idx, states)
        if sid and sid not in exclude:
            return _entry(store, states, sid, ent, how)
    return None


def _unique(hits: dict[str, dict]) -> dict[str, dict]:
    sensors = [h["sensor"] for h in hits.values()]
    return {k: h for k, h in hits.items() if sensors.count(h["sensor"]) == 1}  # einer für zwei Räume: keiner


def find(store, found, idx, states: dict) -> dict[str, dict]:
    """Je Raum ohne Ventil-/Heizleistungssensor an allen Geräten der Fund: {Raumschlüssel: {sensor, name, from,
    from_name, how, how_text}}. Erst die alten Geräte aller Räume, dann – ohne deren Funde – Bereich und Name. Ohne
    Registry nichts."""
    if idx is None:
        return {}
    exclude = taken(found, idx, states)
    rooms = [r for r in found.rooms if not any(d.get("heating_power") for d in r.devices)]
    hits = _unique({r.climate: h for r in rooms if (h := from_old(store, found, r, idx, states, exclude))})
    exclude |= {h["sensor"] for h in hits.values()}
    place = {}
    for r in rooms:
        hit = None if r.climate in hits else by_place(store, r, idx, states, exclude)
        if hit:
            place[r.climate] = _entry(store, states, hit[0], None, hit[1])
    return {**hits, **_unique(place)}


def attach(store, found, idx, states: dict) -> dict[str, dict]:
    """Funde zur Laufzeit einsetzen (``Room.heating_power`` des Schlüssels) und in ``found.valve_found`` merken. Ein
    Fehler hält den Aufbau nicht auf."""
    try:
        hits = find(store, found, idx, states)
    except Exception:  # noqa: BLE001
        log.exception("Heizleistung alter Thermostate nicht gesucht")
        return {}
    for key, hit in hits.items():
        found.room_of(key).heating_power = hit["sensor"]
        log.info("Raum %s: Heizleistung %s %s genutzt (%s) – in den Einstellungen übernehmen", key, hit["sensor"],
                 hit["how_text"], hit["from"] or "–")
    found.valve_found = hits
    return hits


def stale(state: dict | None, valve: float, action, now: float) -> bool:
    """Ventilsensor veraltet: seit ``STALE_S`` kein neuer Wert (``last_reported``/``last_updated``/``last_changed``)
    und das Thermostat widerspricht ihm (heizt bei 0 %, ruht bei mehr als 0 %). Ohne Zeit oder ``hvac_action``: nein."""
    if not state or action not in ACTIONS:
        return False
    times = [parse_time(state.get(k)) for k in ("last_reported", "last_updated", "last_changed")]
    seen = max((t.timestamp() for t in times if t is not None), default=None)
    if seen is None or now - seen < STALE_S:
        return False
    return (action == "heating") != (valve > 0)
