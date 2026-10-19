"""Entitäten automatisch finden: EMS-ESP-Therme, Tado-Räume, Personen, Wetter, Gas.

Jede Zuordnung lässt sich in den Add-on-Optionen über ``overrides``
(``rolle=entity_id``) überschreiben.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# Rollen -> EMS-ESP-Namen und ihre Beschriftungen (seit 0.36.3 in ``discovery_roles``) – hier durchgereicht,
# weil Module und Tests sie aus ``heatpilot.discovery`` holen
from .discovery_roles import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES  # noqa: F401
from .ems_lock import EMS_CLIMATE, LOCK_TEXT, is_ems_climate

log = logging.getLogger(__name__)

# Integrationen, deren climate-Entitäten Räume sind (Tado-Cloud, HomeKit-Bridge von Tado u. a.)
ROOM_PLATFORMS = ("tado", "homekit_controller")
# EMS-ESP-Heizkreis als climate (climate.thermostat_hc1, climate.ems_esp_thermostat_hc1): kein Raum – gilt nur
# für fremde Plattformen; eine Tado-Zone „Bad Thermostat“ oder ein HomeKit-Ventil „Wohnzimmer Thermostat“ bleibt Raum.
# Die Schreibsperre (0.36.3) erkennt ihn am Gerät, siehe ems_lock
_EMS_CLIMATE = EMS_CLIMATE
# Wandthermostat (Raumregler) am Modell- oder Gerätenamen: Tado „Smart Thermostat“ (ohne „Radiator“),
# HomeKit-Modelle ST01 (Smart Thermostat), RU01/RU02 (Wireless Temperature Sensor als Raumregler)
_WALL_MODELS = re.compile(r"^(ST|RU)\d{2}", re.IGNORECASE)
# Bestandteile von Gerätenamen, die keinen Raum beschreiben (für die Kurzform „Gerätename, gekürzt“)
_NAME_NOISE = re.compile(r"\b(tado°?|smart|radiator|wireless|temperature|sensor|thermostat|valve|[A-Z]{2}\d{6,})\b",
                         re.IGNORECASE)

# Betriebsart, in der der Heizkreis heizt (EMS-ESP liefert je nach Sprache)
HEAT_MODES = {"tag", "day", "comfort", "heat", "heizen", "an", "on"}


@dataclass
class Room:
    """Ein logischer Raum. ``climate`` ist sein Schlüssel (Raumregler, sonst erstes Gerät der Gruppe);
    unter ihm laufen Messwerte und Modell. Weitere Geräte derselben Gruppe sind ``members``
    (Mitläufer): ihre Messwerte fließen ins Mittel, sie werden nicht als eigener Raum gelernt."""

    name: str
    climate: str
    heating_power: str | None = None  # Tado: Ventilöffnung / Heizleistung in %
    humidity: str | None = None
    temp_sensor: str | None = None  # für die Langzeitstatistik (climate hat keine)
    # seit 0.34: Steuerung
    group: str | None = None          # Raumname der Gruppe (Einstellungen), None = eigener Raum
    controller: bool = False          # Schlüssel ist ein Wandthermostat: nur er wird gemessen und beschrieben
    managed: bool = False             # „macht mit“ – nur dann schreibt HeatPilot
    comfort: float = 21.0             # Komforttemperatur (°C)
    platform: str | None = None       # Integration des Schlüssels laut Registry (tado, homekit_controller …)
    # Mitläufer: [{"climate", "heating_power", "humidity", "temp_sensor", "platform", "preset", "preset_max"}]
    members: list[dict] = field(default_factory=list)
    # seit 0.35: Hydraulik – Voreinstellung am Ventilunterteil des Schlüssels (None = unbekannt) und Zahl der
    # Stufen (None = unbekannt, 0 = kein Voreinstellventil); HA-Bereich und Gerät des Schlüssels
    preset: int | None = None
    preset_max: int | None = None
    area: str | None = None
    device_name: str | None = None
    device_model: str | None = None
    # seit 0.36.3: Heizkreis von EMS-ESP (ein Gerät des Raums gehört zu EMS-ESP) – nie „macht mit“,
    # Control weist jeden Schreibvorgang darauf hart ab (EMS-ESP nur lesen)
    ems: bool = False

    @property
    def climates(self) -> list[str]:
        """Alle climate-Entitäten des Raums (Schlüssel zuerst)."""
        return [self.climate, *(m["climate"] for m in self.members)]

    @property
    def devices(self) -> list[dict]:
        """Schlüssel und Mitläufer in einheitlicher Form."""
        head = {"climate": self.climate, "heating_power": self.heating_power, "humidity": self.humidity,
                "temp_sensor": self.temp_sensor, "platform": self.platform,
                "preset": self.preset, "preset_max": self.preset_max}
        return [head, *self.members]

    @property
    def write_targets(self) -> list[str]:
        """Wohin HeatPilot schreibt: nur der Raumregler, sonst alle Ventile des Raums."""
        return [self.climate] if self.controller else self.climates


@dataclass
class Registry:
    """Entitäten-, Geräte- und Bereichsregistry von HA (seit 0.35). Geräte/Bereiche dürfen fehlen (alte HA)."""

    entities: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    areas: list[dict] = field(default_factory=list)

    @classmethod
    def of(cls, value) -> "Registry":
        """Auch eine nackte Entitätenliste (ältere Aufrufer, Tests) oder ein dict annehmen."""
        if isinstance(value, Registry):
            return value
        if isinstance(value, dict):
            return cls(list(value.get("entities") or []), list(value.get("devices") or []),
                       list(value.get("areas") or []))
        return cls(list(value or []))

    def lookup(self) -> "RegistryIndex":
        return RegistryIndex(self)


class RegistryIndex:
    """Schnelle Nachschlagetabellen: Entität -> Eintrag, Gerät -> Eintrag, Gerät -> Entitäten, Bereich -> Name."""

    def __init__(self, reg: Registry) -> None:
        self.entity = {r["entity_id"]: r for r in reg.entities if r.get("entity_id")}
        self.device = {d["id"]: d for d in reg.devices if d.get("id")}
        self.area_name = {a["area_id"]: a.get("name") or a["area_id"] for a in reg.areas if a.get("area_id")}
        self.by_device: dict[str, list[str]] = defaultdict(list)
        for r in reg.entities:
            if r.get("device_id") and r.get("entity_id"):
                self.by_device[r["device_id"]].append(r["entity_id"])

    def area_of(self, entity_id: str) -> str | None:
        """Bereichsname: an der Entität gesetzt, sonst der des Geräts."""
        e = self.entity.get(entity_id, {})
        area_id = e.get("area_id") or self.device.get(e.get("device_id") or "", {}).get("area_id")
        return self.area_name.get(area_id, area_id) if area_id else None

    def device_of(self, entity_id: str) -> dict:
        return self.device.get(self.entity.get(entity_id, {}).get("device_id") or "", {})

    def siblings(self, entity_id: str, domain: str = "sensor.") -> list[str]:
        """Entitäten desselben Geräts (ohne die Entität selbst)."""
        device_id = self.entity.get(entity_id, {}).get("device_id")
        return [e for e in self.by_device.get(device_id or "", []) if e != entity_id and e.startswith(domain)]


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
    # seit 0.36: Leistungssensoren des Hauses (Summe, ggf. je Phase) und Verbraucher, die nicht zählen –
    # nur aus den Einstellungen, keine Automatik; power_units: Einheit je Sensor (W/kW) zur Zeit der
    # Erkennung, damit auch die Historie (ohne Attribute) richtig umgerechnet wird
    power: list[str] = field(default_factory=list)
    power_exclude: list[str] = field(default_factory=list)
    power_units: dict[str, str] = field(default_factory=dict)
    home_zone: tuple[float, float] | None = None
    # nur bei ``all_rooms=True`` (Vorschläge): die climate-Entitäten, die die Erkennung im Betrieb ohne
    # Raum-Einstellungen als Räume nähme – die Oberfläche hakt nur diese vorab an (sonst dasselbe Ventil
    # doppelt über Tado-Cloud und HomeKit, Klimageräte fremder Plattformen als Raum)
    auto_rooms: list[str] = field(default_factory=list)

    def all_sensor_ids(self) -> list[str]:
        ids = list(self.boiler.values()) + list(self.thermostat.values()) + list(self.outdoor)
        for r in self.rooms:
            for d in r.devices:
                ids += [x for x in (d.get("heating_power"), d.get("humidity"), d.get("temp_sensor")) if x]
        if self.gas:
            ids.append(self.gas)
        if self.demand_sensor:
            ids.append(self.demand_sensor)
        ids += self.power + self.power_exclude
        return sorted(set(ids))

    def climate_ids(self) -> list[str]:
        """Alle climate-Entitäten aller Räume (Schlüssel und Mitläufer)."""
        return [c for r in self.rooms for c in r.climates]

    def room_of(self, climate: str) -> "Room | None":
        return next((r for r in self.rooms if climate in r.climates), None)

    def as_dict(self, private: bool = True) -> dict:
        """``private=False``: ohne Standort (für Log und Diagnose-Datei)."""
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
            "power": self.power,
            "power_exclude": self.power_exclude,
            "power_units": self.power_units,
            "home_zone": self.home_zone if private else (self.home_zone is not None),
            "auto_rooms": self.auto_rooms,
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


# Heizleistung/Ventilöffnung eines Tado-Raums – Entitätsnamen hängen von der Sprache ab,
# in der die Integration eingerichtet wurde (z. B. sensor.kuche_heizleistung)
HEATING_WORDS = ("heating", "heizleistung", "heizung", "heizen", "ventil", "valve", "verwarming", "chauffage")


def _is_heating_sensor(entity_id: str, attrs: dict, reg_entry: dict) -> bool:
    # sprachunabhängig: Übersetzungsschlüssel bzw. eindeutige ID der Tado-Integration
    if reg_entry.get("translation_key") == "heating" or str(reg_entry.get("unique_id", "")).endswith(" heating"):
        return True
    if attrs.get("unit_of_measurement") != "%":
        return entity_id.endswith("_heating")
    name = (entity_id + " " + str(attrs.get("friendly_name", ""))).lower()
    return any(w in name for w in HEATING_WORDS) and "feucht" not in name and "humid" not in name


def short_name(name: str) -> str:
    """Gerätename ohne Hersteller/Modellwörter/Seriennummer („tado Smart Radiator Thermostat VA03…“ -> „“)."""
    out = re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", name or "")).strip(" -–·")
    return out


def device_sensors(climate: str, idx: RegistryIndex, by_id: dict) -> dict:
    """Sensoren am Gerät einer climate-Entität: Heizleistung/Ventil, Feuchte, Temperatur.

    Gibt {"heating_power", "humidity", "temp_sensor", "siblings"}; ``siblings`` = alle Sensoren des Geräts
    als [{"id", "kind", "name", "unit"}] mit kind temperature/humidity/heating_power/other.
    Die Tado-Sonderfälle bleiben: Übersetzungsschlüssel ``heating`` bzw. unique_id, sprachabhängige
    Namen, einziger %-Sensor (außer Feuchte/Batterie) = Heizleistung.
    """
    out: dict = {"heating_power": None, "humidity": None, "temp_sensor": None, "siblings": []}
    percent = []
    for sid in sorted(idx.siblings(climate)):
        attrs = by_id.get(sid, {}).get("attributes", {})
        kind = "other"
        if _is_heating_sensor(sid, attrs, idx.entity.get(sid, {})):
            kind = "heating_power"
            out["heating_power"] = out["heating_power"] or sid
        elif attrs.get("device_class") == "humidity":
            kind = "humidity"
            out["humidity"] = out["humidity"] or sid
        elif attrs.get("device_class") == "temperature":
            kind = "temperature"
            out["temp_sensor"] = out["temp_sensor"] or sid
        elif attrs.get("unit_of_measurement") == "%" and attrs.get("device_class") in (None, "power_factor"):
            percent.append(sid)
        out["siblings"].append({"id": sid, "kind": kind, "name": attrs.get("friendly_name", sid),
                                "unit": attrs.get("unit_of_measurement")})
    if out["heating_power"] is None and len(percent) == 1:
        out["heating_power"] = percent[0]  # einziger %-Sensor am Thermostat (außer Feuchte) = Heizleistung
        for sib in out["siblings"]:
            if sib["id"] == percent[0]:
                sib["kind"] = "heating_power"
    return out


def is_wall_thermostat(climate: str, idx: RegistryIndex, by_id: dict) -> bool:
    """Vorschlag „Raumregler“: Wandthermostat laut Modell/Name (Tado Smart Thermostat, HomeKit ST01/RU0x)."""
    dev = idx.device_of(climate)
    model = str(dev.get("model") or "")
    names = " ".join(str(x) for x in (dev.get("name_by_user"), dev.get("name"), model,
                                      by_id.get(climate, {}).get("attributes", {}).get("friendly_name")) if x)
    if _WALL_MODELS.match(model.strip()):
        return True
    low = names.lower()
    return "smart thermostat" in low and "radiator" not in low


def climate_info(climate: str, idx: RegistryIndex, by_id: dict) -> dict:
    """Bereich, Gerät, Sensoren und Raumregler-Vorschlag einer climate-Entität (für Vorschläge und Oberfläche).

    ``ems`` (0.36.3): Heizkreis von EMS-ESP (``ems_lock``) – die Oberfläche sperrt „macht mit“, HeatPilot schreibt
    nie darauf.
    """
    dev = idx.device_of(climate)
    return {
        "area": idx.area_of(climate),
        "device_name": dev.get("name_by_user") or dev.get("name") or None,
        "device_model": dev.get("model") or None,
        "controller": is_wall_thermostat(climate, idx, by_id),
        "ems": is_ems_climate(climate, idx),
        **device_sensors(climate, idx, by_id),
    }


def room_candidates(ids: list[str], by_id: dict, idx: RegistryIndex) -> list[str]:
    """climate-Entitäten, die Räume sein können: Tado/HomeKit immer (nie über den Namen verworfen), sonst jede
    mit Gerät außer dem EMS-Heizkreis."""
    out = []
    for e in sorted(ids):
        if not e.startswith("climate."):
            continue
        platform = idx.entity.get(e, {}).get("platform")
        if platform in ROOM_PLATFORMS or (idx.device_of(e) and not _EMS_CLIMATE.match(e)
                                          and not is_ems_climate(e, idx)):
            out.append(e)
    return out


def _operating(candidates: list[str], idx: RegistryIndex) -> list[str]:
    """Räume im Betrieb ohne Raum-Einstellungen: nur die Tado-Cloud-Zonen, wenn es welche gibt (dieselben
    Geräte sonst doppelt über HomeKit), sonst nur Tado/HomeKit, sonst alle Kandidaten."""
    platform = {e: idx.entity.get(e, {}).get("platform") for e in candidates}
    for keep in (("tado",), ROOM_PLATFORMS):
        sel = [e for e in candidates if platform[e] in keep]
        if sel:
            return sel
    return candidates


def _unique_names(rooms: list[Room], by_id: dict) -> None:
    """Gleicher Name (zwei Ventile im Bereich „Wohnzimmer“): Rest des HA-Namens anhängen („Wohnzimmer VA2“),
    notfalls der HA-Name oder die Entität – in Übersicht, Grafen und Empfehlungen bleiben die Räume unterscheidbar."""
    def friendly(r: Room) -> str:
        return by_id.get(r.climate, {}).get("attributes", {}).get("friendly_name") or r.climate

    for variant in (lambda r: f"{r.name} {friendly(r).split()[-1]}", friendly, lambda r: r.climate):
        dup = {n for n, k in Counter(r.name for r in rooms).items() if k > 1}
        if not dup:
            return
        for r in rooms:
            if r.name in dup:
                r.name = variant(r)


def _discover_rooms(d: Discovered, ids: list[str], by_id: dict, idx: RegistryIndex, all_rooms: bool) -> None:
    """Räume ohne eigene Einstellungen: Tado-Räume, Zusatzsensoren und Bereich über das Gerät.

    ``all_rooms=False`` (laufender Betrieb ohne Raum-Einstellungen): nur die Tado-Cloud-Räume, wenn es welche
    gibt (sonst doppelte Räume, wenn dieselben Geräte auch über HomeKit da sind), sonst Tado/HomeKit, sonst
    alle (``_operating``); gleiche Namen werden unterscheidbar gemacht.
    ``all_rooms=True`` (Vorschläge für die Einstellungen): jede climate-Entität mit Gerät, Name und Gruppe
    aus dem HA-Bereich, Raumregler-Vorschlag für Wandthermostate; ``auto_rooms`` nennt die Betriebsauswahl.
    """
    candidates = room_candidates(ids, by_id, idx)
    if not candidates:
        candidates = [
            e for e in sorted(ids)
            if e.startswith("climate.")
            and "current_temperature" in by_id[e].get("attributes", {})
            and "boiler" not in e and "thermostat" not in e and not is_ems_climate(e, idx)
        ]
    if all_rooms:
        d.auto_rooms = _operating(candidates, idx)
    else:
        candidates = _operating(candidates, idx)
    for climate in candidates:
        info = climate_info(climate, idx, by_id)
        friendly = by_id[climate]["attributes"].get("friendly_name", climate)
        name = info["area"] or short_name(info["device_name"] or "") or short_name(friendly) or friendly
        if not all_rooms and idx.entity.get(climate, {}).get("platform") == "tado":
            name = friendly  # Tado-Cloud: der Zonenname ist der Raumname
        d.rooms.append(Room(
            name=name, climate=climate, heating_power=info["heating_power"], humidity=info["humidity"],
            temp_sensor=info["temp_sensor"], group=info["area"] if all_rooms else None,
            controller=info["controller"] if all_rooms else False,
            platform=idx.entity.get(climate, {}).get("platform"),
            area=info["area"], device_name=info["device_name"], device_model=info["device_model"],
            ems=info["ems"],
        ))
    if not all_rooms:
        _unique_names(d.rooms, by_id)


def _preset(r: dict, key: str) -> int | None:
    """Voreinstellung/Stufen aus einem Raum-Eintrag (0.35): fehlt = None; preset 0 = unbekannt = None."""
    v = r.get(key)
    if v in (None, ""):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if key == "preset" and v <= 0:
        return None
    return v if v >= 0 else None


def group_rooms(entries: list[dict], by_id: dict, reg: dict, info: dict[str, dict] | None = None) -> list[Room]:
    """Raum-Einträge der Einstellungen zu logischen Räumen zusammenfassen (seit 0.34).

    Gleicher ``group``-Name = derselbe Raum. Schlüssel ist das Gerät mit ``controller`` (Raumregler),
    sonst das erste der Gruppe; die übrigen sind Mitläufer. „macht mit“ gilt, sobald ein Gerät der
    Gruppe es hat; die Komforttemperatur und die Voreinstellung (0.35) kommen vom Schlüssel, Mitläufer
    behalten ihre eigene Voreinstellung. Einträge ohne ``group``: eigener Raum. ``info``: Bereich/Gerät je
    climate (``climate_info``), optional.

    Seit 0.36.3: Gehört ein Gerät der Gruppe zu EMS-ESP (``info[…]["ems"]``), ist der Raum ``ems`` und macht nie
    mit – auch wenn „macht mit“ gespeichert ist (Warnung im Log). HeatPilot schreibt nicht an den Kessel.
    """
    info = info or {}

    def device(r: dict) -> dict:
        return {"climate": r["climate"], "heating_power": r.get("heating_power") or None,
                "humidity": r.get("humidity") or None, "temp_sensor": r.get("temperature") or None,
                "platform": reg.get(r["climate"], {}).get("platform"),
                "preset": _preset(r, "preset"), "preset_max": _preset(r, "preset_max")}

    def friendly(climate: str) -> str:
        return by_id.get(climate, {}).get("attributes", {}).get("friendly_name", climate)

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for i, r in enumerate(entries):
        key = r.get("group") or f"\0{i}"  # ohne Gruppe: eigener Raum
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    rooms = []
    for key in order:
        entries_ = groups[key]
        head = next((r for r in entries_ if r.get("controller")), entries_[0])
        others = [r for r in entries_ if r is not head]
        name = (head.get("group") or head.get("name") or friendly(head["climate"])).strip()
        comfort = head.get("comfort")
        try:
            comfort = float(comfort) if comfort is not None else 21.0
        except (TypeError, ValueError):
            comfort = 21.0
        ems = any(info.get(r["climate"], {}).get("ems") for r in entries_)
        managed = any(bool(r.get("managed")) for r in entries_)
        if ems and managed:
            log.warning("%s macht nicht mit: %s", name, LOCK_TEXT)
        rooms.append(Room(
            name=name, climate=head["climate"], heating_power=head.get("heating_power") or None,
            humidity=head.get("humidity") or None, temp_sensor=head.get("temperature") or None,
            group=head.get("group") or None, controller=bool(head.get("controller")),
            managed=managed and not ems, comfort=comfort, ems=ems,
            platform=reg.get(head["climate"], {}).get("platform"),
            members=[device(r) for r in others],
            preset=_preset(head, "preset"), preset_max=_preset(head, "preset_max"),
            area=info.get(head["climate"], {}).get("area"),
            device_name=info.get(head["climate"], {}).get("device_name"),
            device_model=info.get(head["climate"], {}).get("device_model"),
        ))
    return rooms


def person_kind(state: dict) -> str:
    """``person``: HA-Benutzer mit Gerät · ``device``: Gerät, aber kein HA-Benutzer (z. B. Fahrzeug,
    das als Person angelegt ist, weil es ein Bild und Abwesenheiten hat) · ``dummy``: ohne Gerät."""
    a = state.get("attributes", {})
    if not a.get("device_trackers"):
        return "dummy"
    return "person" if a.get("user_id") else "device"


def discover(states: list[dict], registry, cfg, all_rooms: bool = False) -> Discovered:
    """``registry``: ``Registry`` (Entitäten, Geräte, Bereiche) oder nur die Entitätenliste.
    ``all_rooms``: Vorschläge für die Einstellungen – jede climate-Entität mit Gerät (siehe ``_discover_rooms``)."""
    by_id = {s["entity_id"]: s for s in states}
    ids = list(by_id)
    idx = Registry.of(registry).lookup()
    reg = idx.entity
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
        info = {r["climate"]: climate_info(r["climate"], idx, by_id) for r in cfg.rooms if r.get("climate")}
        d.rooms = group_rooms(cfg.rooms, by_id, reg, info)
    else:
        _discover_rooms(d, ids, by_id, idx, all_rooms)

    all_persons = sorted(e for e in ids if e.startswith("person."))
    if cfg.persons:
        d.persons = [p for p in cfg.persons if p in by_id]
    else:
        # automatisch: echte Menschen = HA-Benutzer mit Gerät; Fahrzeuge (Gerät ohne Benutzer)
        # und Dummies nicht. Gibt es keine Benutzer-Zuordnung, alle mit Gerät.
        kinds = {p: person_kind(by_id.get(p, {})) for p in all_persons}
        d.persons = ([p for p in all_persons if kinds[p] == "person"]
                     or [p for p in all_persons if kinds[p] != "dummy"] or all_persons)

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

    # Schlafenszeit aus dem Stromverbrauch (0.36): nur gewählte Sensoren, die es in HA gibt
    d.power = [e for e in cfg.power_sensors if e in by_id]
    d.power_exclude = [e for e in cfg.power_exclude if e in by_id and e not in d.power]
    missing = [e for e in [*cfg.power_sensors, *cfg.power_exclude] if e not in by_id]
    if missing:
        log.warning("Leistungssensoren nicht gefunden: %s – zählen nicht", ", ".join(missing))
    for e in d.power + d.power_exclude:
        unit = by_id[e].get("attributes", {}).get("unit_of_measurement")
        if unit:
            d.power_units[e] = str(unit)

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

    log.info("Gefunden: %s", d.as_dict(private=False))
    return d
