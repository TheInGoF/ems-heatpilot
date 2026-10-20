"""0.46.4: Ein abgebauter Regler blockiert nicht mehr – beschreibbare Eingaben (z. B. number.…_remotetemp, das eine
Automation bzw. der EMS-ESP-Fernfühler schreibt) zählen nicht als Lebenszeichen, und eine leere Antwort von EMS-ESP
für den Regler heißt „meldet nichts“ (nicht „unbekannt“)."""
from datetime import datetime, timezone
from types import SimpleNamespace

from heatpilot import regulator

NOW = 1_800_000_000.0


def _eng(states, roles):
    return SimpleNamespace(found=SimpleNamespace(thermostat=roles), states=states)


def _st(value, ts):
    return {"state": value, "last_updated": datetime.fromtimestamp(ts, timezone.utc).isoformat()}


def test_written_remote_temperature_is_no_sign_of_life():
    eng = _eng({"number.ems_esp_thermostat_hc1_remotetemp": _st("19.5", NOW - 30)},
               {"hc_remotetemp": "number.ems_esp_thermostat_hc1_remotetemp"})
    assert regulator._fresh_ha(eng, NOW) is None


def test_fresh_regulator_sensor_still_counts():
    eng = _eng({"sensor.thermostat_hc1_seltemp": _st("21", NOW - 30),
                "number.thermostat_hc1_offset": _st("0", NOW - 30)},
               {"hc_seltemp": "sensor.thermostat_hc1_seltemp", "hc_offsettemp": "number.thermostat_hc1_offset"})
    assert regulator._fresh_ha(eng, NOW) == "sensor.thermostat_hc1_seltemp"
    eng.states["sensor.thermostat_hc1_seltemp"] = _st("unavailable", NOW - 30)
    assert regulator._fresh_ha(eng, NOW) is None
