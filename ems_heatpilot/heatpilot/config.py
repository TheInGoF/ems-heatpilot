"""Einstellungen: Vorgaben ← Weboberfläche (``/data/settings.json``).

Eingerichtet wird ausschließlich über die HeatPilot-Seite (Reiter Einstellungen);
das Add-on hat keine YAML-Optionen. Eine ``options.json`` älterer Versionen wird
noch gelesen, damit dort gesetzte Werte bei einem Update nicht verloren gehen.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("HEATPILOT_DATA", "/data"))
SETTINGS_NAME = "settings.json"

_ENTITY = re.compile(r"^[a-z_]+\.[A-Za-z0-9_]+$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")

# Feld -> (Typ, min, max). Typen: int, float, str, entity, entities, time, strings, rooms, overrides
FIELDS: dict[str, tuple] = {
    "sample_interval_s": ("int", 60, 900),
    "backfill_days": ("int", 0, 365),
    "statistics_backfill_days": ("int", 0, 1095),
    "outdoor_sensors": ("entities",),
    "rooms": ("rooms",),
    "gas_sensor": ("entity",),
    "demand_sensor": ("entity",),
    "weather_entity": ("entity",),
    "emsesp_host": ("str",),
    "gas_calorific_value": ("float", 8, 13),
    "gas_z_number": ("float", 0.8, 1.1),
    "gas_price_eur_per_kwh": ("float", 0, 1),
    "gas_base_fee_eur_per_year": ("float", 0, 2000),
    "dhw_min_temp": ("float", 30, 60),
    "dhw_comfort_temp": ("float", 35, 65),
    "dhw_max_temp": ("float", 40, 70),
    "design_outdoor_temp": ("float", -25, 0),
    "living_area_m2": ("float", 0, 2000),
    "boiler_nominal_kw": ("float", 0, 100),
    "boiler_min_power_pct": ("float", 0, 100),
    "max_flow_temp": ("int", 30, 90),
    "away_temp": ("float", 10, 24),
    "night_temp": ("float", 10, 24),
    "night_start": ("time",),
    "night_end": ("time",),
    "learn_schedule": ("bool",),
    "floor_heating_rooms": ("entities",),
    "vacations": ("strings",),
    "vacation_entity": ("entity",),
    "vacation_arrival_time": ("time",),
    "overrides": ("overrides",),
    "debug_logging": ("bool",),
}


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def validate(raw: dict) -> tuple[dict, list[str]]:
    """Eingaben aus der Oberfläche prüfen und bereinigen. Gibt (sauber, Fehler) zurück."""
    from .vacation import _RANGE  # spätes Import, vermeidet Zyklus

    clean: dict = {}
    errors: list[str] = []
    for key, value in raw.items():
        if key not in FIELDS:
            continue
        kind = FIELDS[key][0]
        try:
            if kind in ("int", "float"):
                lo, hi = FIELDS[key][1:]
                v = int(value) if kind == "int" else float(str(value).replace(",", "."))
                if not lo <= v <= hi:
                    raise ValueError(f"muss zwischen {lo} und {hi} liegen")
                clean[key] = v
            elif kind == "str":
                clean[key] = str(value or "").strip()
            elif kind == "bool":
                clean[key] = value is True or str(value).lower() in ("true", "1", "on", "ja")
            elif kind == "entity":
                v = str(value or "").strip()
                if v and not _ENTITY.match(v):
                    raise ValueError("keine gültige Entität (z. B. sensor.name)")
                clean[key] = v
            elif kind == "entities":
                items = [str(v).strip() for v in (value or []) if str(v).strip()]
                bad = [v for v in items if not _ENTITY.match(v)]
                if bad:
                    raise ValueError(f"ungültig: {', '.join(bad)}")
                clean[key] = list(dict.fromkeys(items))
            elif kind == "time":
                m = _TIME.match(str(value).strip())
                if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                    raise ValueError("Uhrzeit als HH:MM")
                clean[key] = f"{int(m.group(1)):02d}:{m.group(2)}"
            elif kind == "strings":
                items = [str(v).strip() for v in (value or []) if str(v).strip()]
                if key == "vacations":
                    bad = [v for v in items if not _RANGE.match(v)]
                    if bad:
                        raise ValueError(f"Format JJJJ-MM-TT [HH:MM] - JJJJ-MM-TT [HH:MM]: {', '.join(bad)}")
                clean[key] = items
            elif kind == "rooms":
                rooms = []
                for r in value or []:
                    climate = str(r.get("climate", "")).strip()
                    if not _ENTITY.match(climate):
                        raise ValueError(f"Raum ohne gültige climate-Entität: {climate or '(leer)'}")
                    room = {"name": str(r.get("name") or "").strip(), "climate": climate}
                    for opt in ("temperature", "heating_power", "humidity"):
                        v = str(r.get(opt) or "").strip()
                        if v:
                            if not _ENTITY.match(v):
                                raise ValueError(f"{climate}: {opt} ungültig")
                            room[opt] = v
                    rooms.append(room)
                clean[key] = rooms
            elif kind == "overrides":
                items = []
                for item in value or []:
                    item = str(item).strip()
                    if not item:
                        continue
                    role, _, ent = item.partition("=")
                    if not role.strip() or not _ENTITY.match(ent.strip()):
                        raise ValueError(f"Format rolle=entity_id: {item}")
                    items.append(f"{role.strip()}={ent.strip()}")
                clean[key] = items
        except (TypeError, ValueError) as err:
            errors.append(f"{key}: {err}")
    if not errors:
        lo = clean.get("dhw_min_temp", raw.get("dhw_min_temp"))
        mid = clean.get("dhw_comfort_temp", raw.get("dhw_comfort_temp"))
        hi = clean.get("dhw_max_temp", raw.get("dhw_max_temp"))
        if None not in (lo, mid, hi) and not (float(lo) <= float(mid) <= float(hi)):
            errors.append("Warmwasser: Minimum ≤ Komfort ≤ Maximum")
    return clean, errors


@dataclass
class Config:
    mode: str = "observe"
    sample_interval_s: int = 300
    backfill_days: int = 30
    statistics_backfill_days: int = 365
    outdoor_sensors: list[str] = field(default_factory=list)
    # Räume: [{name, climate, temperature?, heating_power?, humidity?}], leer = Tado automatisch
    rooms: list[dict] = field(default_factory=list)
    gas_sensor: str | None = None
    # Heizungsanforderung der bestehenden Automation (nur aufzeichnen)
    demand_sensor: str | None = None
    weather_entity: str | None = None
    emsesp_host: str | None = None
    # Gasabrechnung: kWh = m³ × Brennwert × Zustandszahl (Werte von der Gasrechnung)
    gas_calorific_value: float = 11.2
    gas_z_number: float = 0.95
    gas_price_eur_per_kwh: float = 0.12
    gas_base_fee_eur_per_year: float = 150.0
    # Warmwasser-Strategie (nur Empfehlung): nie unter min, für Nutzung comfort
    dhw_min_temp: float = 40.0
    dhw_comfort_temp: float = 50.0
    dhw_max_temp: float = 60.0
    design_outdoor_temp: float = -12.0
    # fürs Zertifikat; 0 = unbekannt bzw. aus EMS-ESP (nompower, burnminpower)
    living_area_m2: float = 0.0
    boiler_nominal_kw: float = 0.0
    boiler_min_power_pct: float = 0.0
    max_flow_temp: int = 75
    away_temp: float = 19.0
    night_temp: float = 18.0
    night_start: time = time(22, 30)
    night_end: time = time(6, 0)
    # Nachtzeiten/-temperatur und Abwesenheit aus Tado weiterlernen (Einstellungen = Startwerte)
    learn_schedule: bool = True
    # Räume mit Fußbodenheizung ohne Stellventil (z. B. festes RTL), climate-Entitäten
    floor_heating_rooms: list[str] = field(default_factory=list)
    # Urlaub: ["2026-12-20 - 2027-01-03", "2027-02-01 08:00 - 2027-02-05 17:30"]
    vacations: list[str] = field(default_factory=list)
    vacation_entity: str | None = None  # z. B. input_boolean.urlaub
    vacation_arrival_time: time = time(18, 0)  # Rückkehr, wenn nur ein Datum angegeben ist
    # "rolle=entity_id", z. B. "flow_temp=sensor.boiler_curflowtemp"
    overrides: dict[str, str] = field(default_factory=dict)
    debug_logging: bool = False  # ausführliches Log (jeder Takt) für die Fehlersuche

    @classmethod
    def load(cls, path: Path | None = None, settings_path: Path | None = None) -> "Config":
        path = path or DATA_DIR / "options.json"
        settings_path = settings_path or path.parent / SETTINGS_NAME
        raw: dict = {}
        if path.exists():
            raw.update(json.loads(path.read_text()))
        if settings_path.exists():  # Weboberfläche hat Vorrang
            raw.update(json.loads(settings_path.read_text()))
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: dict) -> "Config":
        cfg = cls()
        for key, value in raw.items():
            if key in ("night_start", "night_end", "vacation_arrival_time"):
                value = _parse_time(value)
            elif key == "overrides":
                value = dict(item.split("=", 1) for item in value if "=" in item)
                value = {k.strip(): v.strip() for k, v in value.items()}
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        for key in ("gas_sensor", "demand_sensor", "weather_entity", "emsesp_host", "vacation_entity"):
            if getattr(cfg, key) == "":
                setattr(cfg, key, None)
        cfg.rooms = [r for r in cfg.rooms if r.get("climate")]
        return cfg

    def to_raw(self) -> dict:
        """Für die Oberfläche: JSON-fähig, im selben Format wie gespeichert."""
        out = {}
        for key in FIELDS:
            v = getattr(self, key)
            if isinstance(v, time):
                v = v.strftime("%H:%M")
            elif key == "overrides":
                v = [f"{k}={e}" for k, e in v.items()]
            elif v is None:
                v = ""
            out[key] = v
        return out

    @property
    def gas_kwh_per_m3(self) -> float:
        return self.gas_calorific_value * self.gas_z_number

    def is_night(self, t: time) -> bool:
        if self.night_start <= self.night_end:
            return self.night_start <= t < self.night_end
        return t >= self.night_start or t < self.night_end


def save_settings(clean: dict, data_dir: Path | None = None) -> Path:
    """Geprüfte Einstellungen speichern (atomar)."""
    path = (data_dir or DATA_DIR) / SETTINGS_NAME
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update(clean)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return path
