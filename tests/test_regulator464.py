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


def test_user_confirmed_removal_gives_go_unless_emsesp_reports_values():
    """0.46.9: „Regler ist abgebaut“ angehakt → frei ohne Warten; nur echte Werte des Reglers von EMS-ESP sperren."""
    from heatpilot.config import Config
    eng = _eng({}, {})
    eng.cfg = Config(emsesp_host="ems.local", regulator_removed=True)
    eng.ems, eng.ems_ts, eng.regulator_watch = {"boiler": {"curflowtemp": 40}}, NOW, {"last": "fehler", "ts": NOW}
    r = regulator.regulator(eng, NOW)
    assert r["present"] is False and "bestätigt" in r["text"]
    eng.ems["thermostat"] = {"hc1_seltemp": 21.0}
    assert regulator.regulator(eng, NOW)["present"] is True
    eng.cfg.regulator_removed, eng.ems = False, {"boiler": {"curflowtemp": 40}}
    assert regulator.regulator(eng, NOW)["present"] is None  # ohne Bestätigung: wie bisher prüfen
