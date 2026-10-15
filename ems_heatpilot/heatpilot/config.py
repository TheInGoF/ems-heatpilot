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
    outdoor_sensors: list[str] = field(default_factory=list)
    gas_sensor: str | None = None
    weather_entity: str | None = None
    gas_kwh_per_m3: float = 10.3
    gas_price_eur_per_kwh: float = 0.12
    design_outdoor_temp: float = -12.0
    max_flow_temp: int = 75
    away_temp: float = 19.0
    night_temp: float = 18.0
    night_start: time = time(22, 30)
    night_end: time = time(6, 0)
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
        if cfg.gas_sensor == "":
            cfg.gas_sensor = None
        if cfg.weather_entity == "":
            cfg.weather_entity = None
        return cfg

    def is_night(self, t: time) -> bool:
        if self.night_start <= self.night_end:
            return self.night_start <= t < self.night_end
        return t >= self.night_start or t < self.night_end
