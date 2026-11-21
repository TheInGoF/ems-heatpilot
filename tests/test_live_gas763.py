"""0.76.3: Kachel „Gaszähler“ in Live – Stand, Einheit des Sensors, heute verbraucht (m³ und kWh)."""

import types
from datetime import datetime

from heatpilot import live
from heatpilot.config import Config
from heatpilot.store import Store


def eng(tmp_path, unit="m³"):
    e = types.SimpleNamespace(store=Store(tmp_path / "hp.db"), cfg=Config(), found=types.SimpleNamespace(gas="sensor.gas"))
    e.states = {"sensor.gas": {"state": "6101.47", "attributes": {"unit_of_measurement": unit}}}
    return e


def test_gas_today(tmp_path):
    e = eng(tmp_path)
    midnight = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    e.store.add_sample(midnight - 300, {"gas": 6097.0})
    e.store.add_sample(midnight + 300, {"gas": 6098.26})
    out = live.gas_now(e, 6101.47)
    assert out["gas"] == 6101.47 and out["gas_unit"] == "m³" and out["gas_today"] == 3.21
    assert out["gas_today_kwh"] == round(3.21 * e.cfg.gas_kwh_per_m3, 1)


def test_gas_kwh_sensor_and_missing(tmp_path):
    e = eng(tmp_path, unit="kWh")
    assert live.gas_now(e, None) == {"gas": None, "gas_unit": "kWh", "gas_today": None, "gas_today_kwh": None}
    assert live.gas_now(e, 100.0)["gas_today"] is None  # heute noch kein Messwert
