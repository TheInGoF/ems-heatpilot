import json

from heatpilot.config import Config, save_settings, validate


def test_validate_cleans_and_rejects():
    clean, errors = validate({
        "gas_price_eur_per_kwh": "0,14",
        "outdoor_sensors": ["sensor.a", "", "sensor.a", "sensor.b"],
        "night_start": "7:05",
        "rooms": [{"climate": "climate.bad", "name": "Bad", "heating_power": "", "temperature": "sensor.bad_t"}],
        "vacations": ["2026-12-20 - 2027-01-03 17:30"],
        "overrides": ["nom_power=number.boiler_nominal_Power", ""],
        "unbekannt": 1,
    })
    assert errors == []
    assert clean["gas_price_eur_per_kwh"] == 0.14
    assert clean["outdoor_sensors"] == ["sensor.a", "sensor.b"]
    assert clean["night_start"] == "07:05"
    assert clean["rooms"] == [{"name": "Bad", "climate": "climate.bad", "temperature": "sensor.bad_t"}]
    assert "unbekannt" not in clean

    _, errors = validate({"max_flow_temp": 120, "gas_sensor": "kein entity", "night_end": "25:00",
                          "vacations": ["nächste Woche"], "rooms": [{"climate": ""}]})
    assert len(errors) == 5
    _, errors = validate({"dhw_min_temp": 55, "dhw_comfort_temp": 50, "dhw_max_temp": 60})
    assert errors and "Warmwasser" in errors[0]


def test_settings_from_ui_override_addon_options(tmp_path):
    (tmp_path / "options.json").write_text(json.dumps({"gas_sensor": "sensor.alt", "away_temp": 19}))
    assert Config.load(tmp_path / "options.json").gas_sensor == "sensor.alt"
    clean, _ = validate({"gas_sensor": "sensor.neu", "night_start": "23:00"})
    save_settings(clean, tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.gas_sensor == "sensor.neu" and cfg.away_temp == 19
    assert cfg.night_start.hour == 23
    # Speichern ergänzt, statt Früheres zu löschen
    save_settings(validate({"away_temp": 18})[0], tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.gas_sensor == "sensor.neu" and cfg.away_temp == 18
    raw = cfg.to_raw()
    assert raw["night_start"] == "23:00" and raw["emsesp_host"] == ""
    assert validate(raw)[1] == []  # was die Oberfläche bekommt, kann sie unverändert speichern


def test_fresh_install_without_yaml_options(tmp_path):
    """Add-on ohne YAML-Optionen: HA schreibt {} – alles kommt aus der Oberfläche."""
    (tmp_path / "options.json").write_text("{}")
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.rooms == [] and cfg.gas_sensor is None and cfg.away_temp == 19.0
    save_settings(validate({"gas_sensor": "sensor.gas", "rooms": [{"climate": "climate.bad"}]})[0], tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.gas_sensor == "sensor.gas" and cfg.rooms[0]["climate"] == "climate.bad"
