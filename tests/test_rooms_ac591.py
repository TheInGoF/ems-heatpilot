"""0.59.1: Klimaanlagen (hvac_modes mit cool, dry, fan_only oder heat_cool) werden nie als Raum vorgeschlagen –
auch nicht von Tado oder HomeKit; Heizkörper-Thermostate bleiben Räume."""

from heatpilot.config import Config
from heatpilot.discovery import discover, is_air_conditioner


def climate(eid, modes, platform="tado"):
    st = {"entity_id": eid, "state": "heat", "attributes": {"friendly_name": eid.split(".")[1].title(),
                                                           "current_temperature": 20.0, "hvac_modes": modes}}
    return st, {"entity_id": eid, "platform": platform, "device_id": "dev_" + eid}


def test_air_conditioners_are_never_rooms():
    rows = [climate("climate.wohnzimmer", ["off", "heat", "auto"]),
            climate("climate.buero_klima", ["off", "auto", "cool", "dry", "fan_only", "heat"]),
            climate("climate.schlafzimmer", ["heat", "off"], "homekit_controller"),
            climate("climate.dach_split", ["off", "heat_cool"], "homekit_controller")]
    states, registry = [r[0] for r in rows], [r[1] for r in rows]
    d = discover(states, registry, Config(), all_rooms=True)
    assert sorted(r.climate for r in d.rooms) == ["climate.schlafzimmer", "climate.wohnzimmer"]
    assert d.auto_rooms == ["climate.wohnzimmer"]  # Betrieb: nur Tado-Cloud, die Tado-Klimaanlage nicht
    assert is_air_conditioner(rows[1][0]) and not is_air_conditioner(rows[0][0]) and not is_air_conditioner({})
