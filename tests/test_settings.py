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


def test_settings_backup_and_restore(tmp_path):
    from heatpilot.config import BACKUP_NAME, restore_settings, save_settings

    data, backup_dir = tmp_path / "data", tmp_path / "config"
    data.mkdir(); backup_dir.mkdir()
    import heatpilot.config as c
    old = c.BACKUP_DIR
    c.BACKUP_DIR = backup_dir
    try:
        save_settings({"living_area_m2": 85.0}, data_dir=data)
    finally:
        c.BACKUP_DIR = old
    assert (backup_dir / BACKUP_NAME).exists()
    (data / "settings.json").unlink()  # /data verloren, z. B. nach Neuinstallation
    assert restore_settings(data, backup_dir)
    assert '"living_area_m2": 85.0' in (data / "settings.json").read_text()
    assert not restore_settings(data, backup_dir)  # vorhanden: nichts überschreiben


def test_settings_api_roundtrip_control_fields(tmp_path):
    """0.34: Schalter, Nachtplan, Vorheizdeckel und Raumfelder gehen über die Oberfläche hin und zurück."""
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot import schedule
    from heatpilot.web import make_app as web_app
    from test_control34 import make_app

    app, _ha, _loop = make_app(tmp_path / "eng")
    (tmp_path / "options.json").write_text("{}")

    async def apply_settings(raw):  # wie main.App.apply_settings, ohne Neuaufbau der Engine
        clean, errors = validate(raw)
        if errors:
            return errors
        save_settings(clean, tmp_path)
        cfg = Config.load(tmp_path / "options.json")
        app.cfg = app.eng.cfg = cfg
        app.eng.eff = schedule.effective(cfg, app.eng.schedule_learned)
        return []

    app.apply_settings = apply_settings
    plan = [{"start": "21:00", "end": "4:30"}] * 7
    plan[0] = {"start": "20:30", "end": "04:30"}

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            body = {"control_mode": "heatpilot", "preheat_max_h": "2,5", "night_plan": plan, "night_temp": 17.5,
                    "rooms": [{"climate": "climate.wz", "name": "Wz", "group": "Wohnzimmer", "controller": True,
                               "managed": True, "comfort": "20,5", "heating_power": "", "temperature": "", "humidity": ""},
                              {"climate": "climate.wz_v1", "name": "", "group": "Wohnzimmer", "controller": False,
                               "managed": False, "comfort": ""}]}
            r = await client.post("/api/settings", json=body)
            assert r.status == 200 and (await r.json())["ok"]
            j = await (await client.get("/api/settings")).json()
            s = j["settings"]
            assert s["control_mode"] == "heatpilot" and s["preheat_max_h"] == 2.5 and s["night_temp"] == 17.5
            assert len(s["night_plan"]) == 7 and s["night_plan"][0] == {"start": "20:30", "end": "04:30"}
            assert s["night_plan"][1] == {"start": "21:00", "end": "04:30"}  # 4:30 -> 04:30
            assert s["rooms"][0] == {"name": "Wz", "climate": "climate.wz", "group": "Wohnzimmer", "controller": True,
                                     "managed": True, "comfort": 20.5}
            assert s["rooms"][1] == {"name": "", "climate": "climate.wz_v1", "group": "Wohnzimmer", "controller": False,
                                     "managed": False}  # leerer Komfort wird nicht gespeichert (= 21)
            assert validate(s)[1] == []  # was die Oberfläche bekommt, kann sie unverändert speichern
            eff = j["schedule"]["effective"]
            assert eff["plan"][0] == {"day": "Mo", "start": "20:30", "end": "04:30"} and eff["source"]["night_start"] == "wochenplan"
            assert j["control"]["limits"] == {"temp_min": 5.0, "temp_max": 25.0} and j["climate_targets"]["climate.wz"] == 19.0
            # Fehler kommen als 400 mit Klartext, nichts wird übernommen
            for bad, text in (({"rooms": [{"climate": "climate.wz", "comfort": "30"}]}, "Komfort muss zwischen 5 und 25"),
                              ({"rooms": [{"climate": "climate.wz", "comfort": "warm"}]}, "Komforttemperatur keine Zahl"),
                              ({"night_plan": [{"start": "10:00", "end": "04:30"}] + plan[1:]}, "Mo: Beginn der Nacht liegt am Abend"),
                              ({"night_plan": plan[:6]}, "7 Einträge"),
                              ({"control_mode": "auto"}, "control_mode: erlaubt"),
                              ({"preheat_max_h": 9}, "preheat_max_h")):
                r = await client.post("/api/settings", json=bad)
                assert r.status == 400, bad
                errs = (await r.json())["errors"]
                assert any(text in e for e in errs), (text, errs)
            s = (await (await client.get("/api/settings")).json())["settings"]
            assert s["control_mode"] == "heatpilot" and s["rooms"][0]["comfort"] == 20.5
            # zurück auf Beobachten: Plan nicht mehr maßgeblich, Räume behalten ihre Felder
            r = await client.post("/api/settings", json={"control_mode": "beobachten"})
            assert r.status == 200
            j = await (await client.get("/api/settings")).json()
            assert j["settings"]["control_mode"] == "beobachten" and j["schedule"]["effective"]["plan"] is None
            assert j["settings"]["rooms"][0]["managed"] is True
        finally:
            await client.close()

    asyncio.run(go())
