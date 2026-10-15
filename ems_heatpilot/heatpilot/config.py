"""Add-on-Optionen aus /data/options.json laden."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("HEATPILOT_DATA", "/data"))


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


@dataclass
class Config:
    mode: str = "observe"
    sample_interval_s: int = 300
    backfill_days: int = 30
    statistics_backfill_days: int = 365
    outdoor_sensors: list[str] = field(default_factory=lambda: ["sensor.aussen_temperatur"])
    # Räume: [{name, climate, temperature?, heating_power?, humidity?}], leer = Tado automatisch
    rooms: list[dict] = field(default_factory=list)
    gas_sensor: str | None = "sensor.analogsensor_21"
    # Heizungsanforderung der bestehenden Automation (nur aufzeichnen)
    demand_sensor: str | None = "sensor.heizungsanforderung"
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
    max_flow_temp: int = 75
    away_temp: float = 19.0
    night_temp: float = 18.0
    night_start: time = time(22, 30)
    night_end: time = time(6, 0)
    # Räume mit Fußbodenheizung ohne Stellventil (z. B. festes RTL), climate-Entitäten
    floor_heating_rooms: list[str] = field(default_factory=list)
    # Urlaub: ["2026-12-20 - 2027-01-03", "2027-02-01 08:00 - 2027-02-05 17:30"]
    vacations: list[str] = field(default_factory=list)
    vacation_entity: str | None = None  # z. B. input_boolean.urlaub
    # "rolle=entity_id", z. B. "flow_temp=sensor.boiler_curflowtemp"
    overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DATA_DIR / "options.json"
        raw: dict = {}
        if path.exists():
            raw = json.loads(path.read_text())
        cfg = cls()
        for key, value in raw.items():
            if key in ("night_start", "night_end"):
                value = _parse_time(value)
            elif key == "overrides":
                value = dict(
                    item.split("=", 1) for item in value if "=" in item
                )
                value = {k.strip(): v.strip() for k, v in value.items()}
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        for key in ("gas_sensor", "demand_sensor", "weather_entity", "emsesp_host", "vacation_entity"):
            if getattr(cfg, key) == "":
                setattr(cfg, key, None)
        cfg.rooms = [r for r in cfg.rooms if r.get("climate")]
        return cfg

    @property
    def gas_kwh_per_m3(self) -> float:
        return self.gas_calorific_value * self.gas_z_number

    def is_night(self, t: time) -> bool:
        if self.night_start <= self.night_end:
            return self.night_start <= t < self.night_end
        return t >= self.night_start or t < self.night_end
