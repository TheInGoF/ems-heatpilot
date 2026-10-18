"""0.35 Räume zuordnen: HA-Bereiche, Geräte und Gerätesensoren – Vorschläge, Oberfläche, Speichern."""

import asyncio
import json
from types import SimpleNamespace

from heatpilot import advice
from heatpilot.config import Config, save_settings, validate
from heatpilot.discovery import Registry, climate_info, discover, short_name


def homekit_valve(serial, area_id=None, model="SRT01", name="tado Smart Radiator Thermostat", humidity=True):
    """Eine HomeKit-Entität der Tado-Bridge mit Gerät, Temperatur- und Feuchtesensor am Gerät."""
    slug = f"tado_smart_radiator_thermostat_{serial.lower()}"
    dev = f"dev_{serial}"
    states = [
        {"entity_id": f"climate.{slug}", "state": "heat",
         "attributes": {"friendly_name": f"{name} {serial}", "current_temperature": 20.0, "temperature": 21.0}},
        {"entity_id": f"sensor.{slug}_current_temperature", "state": "20.1",
         "attributes": {"friendly_name": f"{name} {serial} Current Temperature", "unit_of_measurement": "°C",
                        "device_class": "temperature"}},
        {"entity_id": f"sensor.{slug}_battery", "state": "90",
         "attributes": {"unit_of_measurement": "%", "device_class": "battery"}},
    ]
    if humidity:
        states.append({"entity_id": f"sensor.{slug}_current_humidity", "state": "45",
                       "attributes": {"unit_of_measurement": "%", "device_class": "humidity"}})
    entities = [{"entity_id": s["entity_id"], "platform": "homekit_controller", "device_id": dev, "area_id": None}
                for s in states]
    device = {"id": dev, "name": f"{name} {serial}", "name_by_user": None, "model": model,
              "manufacturer": "tado", "area_id": area_id}
    return states, entities, device


def house():
    """Wohnzimmer mit Ventil, Bad mit Wandthermostat (ST01), Flur-Ventil ohne Bereich; EMS-Heizkreis als climate."""
    s1, e1, d1 = homekit_valve("VA0302920704", "wohnzimmer")
    s2, e2, d2 = homekit_valve("RU0123456789", "bad", model="ST01", name="tado Smart Thermostat", humidity=True)
    s3, e3, d3 = homekit_valve("VA0399999999", None)
    states = s1 + s2 + s3 + [
        {"entity_id": "climate.thermostat_hc1", "state": "heat",
         "attributes": {"friendly_name": "Thermostat hc1", "current_temperature": 21.0}},
        {"entity_id": "sensor.boiler_curflowtemp", "state": "45", "attributes": {}},
    ]
    entities = e1 + e2 + e3 + [{"entity_id": "climate.thermostat_hc1", "platform": "mqtt", "device_id": "dev_ems"}]
    devices = [d1, d2, d3, {"id": "dev_ems", "name": "EMS-ESP Thermostat", "model": "RC310", "area_id": "keller"}]
    areas = [{"area_id": "wohnzimmer", "name": "Wohnzimmer"}, {"area_id": "bad", "name": "Bad"},
             {"area_id": "keller", "name": "Keller"}]
    return states, Registry(entities, devices, areas)


WZ = "climate.tado_smart_radiator_thermostat_va0302920704"
BAD = "climate.tado_smart_radiator_thermostat_ru0123456789"
FLUR = "climate.tado_smart_radiator_thermostat_va0399999999"


def test_suggestions_use_area_device_sensors_and_wall_thermostat():
    states, reg = house()
    found = discover(states, reg, Config(), all_rooms=True)
    rooms = {r.climate: r for r in found.rooms}
    assert set(rooms) == {WZ, BAD, FLUR}  # der EMS-Heizkreis ist kein Raum
    wz = rooms[WZ]
    assert wz.name == "Wohnzimmer" and wz.group == "Wohnzimmer" and wz.area == "Wohnzimmer"
    assert wz.device_name == "tado Smart Radiator Thermostat VA0302920704" and wz.device_model == "SRT01"
    assert wz.temp_sensor == f"sensor.{WZ[8:]}_current_temperature"
    assert wz.humidity == f"sensor.{WZ[8:]}_current_humidity" and wz.heating_power is None  # Batterie ist keine Heizleistung
    assert wz.controller is False and wz.platform == "homekit_controller"
    assert rooms[BAD].controller is True and rooms[BAD].name == "Bad"  # Wandthermostat: Vorschlag Raumregler
    flur = rooms[FLUR]
    assert flur.area is None and flur.group is None and flur.controller is False
    assert flur.name == "tado Smart Radiator Thermostat VA0399999999"  # ohne Bereich: Gerätename (Kurzform wäre leer)
    # Vorschläge sind JSON-fähig und enthalten die neuen Felder
    d = json.loads(json.dumps(found.as_dict(private=False)))
    assert {"area", "device_name", "device_model", "preset", "preset_max"} <= set(d["rooms"][0])


def test_wall_thermostat_by_name_without_model_code():
    states, reg = house()
    reg.devices[1]["model"] = "Thermostat"  # kein ST01 – aber „Smart Thermostat“ im Namen, ohne „Radiator“
    idx = reg.lookup()
    by_id = {s["entity_id"]: s for s in states}
    assert climate_info(BAD, idx, by_id)["controller"] is True
    assert climate_info(WZ, idx, by_id)["controller"] is False


def test_entity_without_device_or_registry_works_as_before():
    states, _ = house()
    plain = [{"entity_id": "climate.kueche", "state": "heat",
              "attributes": {"friendly_name": "Küche", "current_temperature": 20.0}}]
    # nackte Entitätenliste (alte Aufrufer): kein Fehler, Räume wie bisher über den Namen
    found = discover(plain, [{"entity_id": "climate.kueche", "platform": "tado", "device_id": None}], Config())
    assert [r.climate for r in found.rooms] == ["climate.kueche"] and found.rooms[0].area is None
    # Registry fehlt ganz
    found = discover(plain, [], Config(), all_rooms=True)
    assert [r.name for r in found.rooms] == ["Küche"]
    # Registry ohne Geräte/Bereiche (alte HA) – HomeKit-Ventile werden trotzdem gefunden
    found = discover(states, Registry([{"entity_id": WZ, "platform": "homekit_controller"}], [], []), Config())
    assert [r.climate for r in found.rooms] == [WZ] and found.rooms[0].temp_sensor is None


def test_auto_mode_prefers_tado_cloud_but_finds_homekit_alone():
    states, reg = house()
    # ohne Tado-Cloud: die HomeKit-Ventile sind die Räume (vorher wegen „thermostat“ im Namen übersehen)
    auto = discover(states, reg, Config())
    assert {r.climate for r in auto.rooms} == {WZ, BAD, FLUR}
    assert all(r.group is None and r.controller is False for r in auto.rooms)  # Vorschläge nur in den Einstellungen
    # mit Tado-Cloud: nur die Cloud-Zonen (dieselben Geräte sonst doppelt), Zonenname = Raumname
    states2 = states + [{"entity_id": "climate.wohnzimmer", "state": "heat",
                         "attributes": {"friendly_name": "Wohnzimmer", "current_temperature": 20.0}}]
    reg2 = Registry(reg.entities + [{"entity_id": "climate.wohnzimmer", "platform": "tado", "device_id": "dev_zone"}],
                    reg.devices + [{"id": "dev_zone", "name": "Wohnzimmer", "model": "HEATING", "area_id": "wohnzimmer"}],
                    reg.areas)
    auto = discover(states2, reg2, Config())
    assert [r.climate for r in auto.rooms] == ["climate.wohnzimmer"] and auto.rooms[0].area == "Wohnzimmer"
    both = discover(states2, reg2, Config(), all_rooms=True)
    assert {r.climate for r in both.rooms} == {WZ, BAD, FLUR, "climate.wohnzimmer"}


def test_configured_rooms_carry_presets_area_and_members():
    states, reg = house()
    cfg = Config.from_raw({"rooms": [
        {"climate": WZ, "name": "Wz", "group": "Wohnzimmer", "preset": 5, "preset_max": 6},
        {"climate": FLUR, "group": "Wohnzimmer", "preset": 3, "preset_max": 6},
        {"climate": BAD, "name": "Bad", "controller": True, "preset_max": 0},
    ]})
    found = discover(states, reg, cfg)
    wz, bad = found.rooms
    assert wz.climate == WZ and wz.preset == 5 and wz.preset_max == 6 and wz.area == "Wohnzimmer"
    assert wz.device_model == "SRT01" and wz.members[0]["preset"] == 3 and wz.members[0]["preset_max"] == 6
    assert wz.devices[0]["preset"] == 5 and wz.devices[1]["climate"] == FLUR
    assert bad.preset is None and bad.preset_max == 0 and bad.controller is True
    # Konfiguration ohne Angaben: unbekannt
    found = discover(states, reg, Config.from_raw({"rooms": [{"climate": WZ}]}))
    assert found.rooms[0].preset is None and found.rooms[0].preset_max is None


def test_short_name_strips_vendor_and_serial():
    assert short_name("tado Smart Radiator Thermostat VA0302920704") == ""
    assert short_name("Heizkörper Küche Thermostat") == "Heizkörper Küche"
    assert short_name("") == ""


def test_app_registry_survives_missing_device_and_area_registry():
    from heatpilot.main import App

    calls = []

    async def entity_registry():
        return [{"entity_id": "climate.a", "platform": "tado"}]

    async def device_registry():
        calls.append("devices")
        raise RuntimeError("Unknown command")  # alte HA

    async def area_registry():
        return [{"area_id": "wz", "name": "Wohnzimmer"}]

    app = App.__new__(App)
    app._registry = None
    app.ha = SimpleNamespace(entity_registry=entity_registry, device_registry=device_registry,
                             area_registry=area_registry)
    reg = asyncio.run(app.registry())
    assert isinstance(reg, Registry) and reg.devices == [] and reg.areas[0]["name"] == "Wohnzimmer"
    assert reg.entities[0]["entity_id"] == "climate.a" and calls == ["devices"]
    assert asyncio.run(app.registry()) is reg  # Cache


def web_app_with_rooms(tmp_path):
    """Test-App (test_control34.make_app) mit HomeKit-Haus in den HA-Zuständen und voller Registry."""
    from test_control34 import make_app

    states, reg = house()
    app, ha, loop = make_app(tmp_path / "eng")
    for s in states:
        app.eng.states[s["entity_id"]] = s
    # Räume der Engine: das Wohnzimmer-Ventil mit Voreinstellung, das Modell des Raums ist plausibel
    cfg = Config.from_raw({"rooms": [{"climate": WZ, "name": "Wohnzimmer", "preset": 5, "preset_max": 6},
                                     {"climate": BAD, "name": "Bad", "preset_max": 6}]})
    app.eng.found = discover(states, reg, cfg)
    app.cfg = app.eng.cfg = cfg

    async def registry():
        return reg

    app.registry = registry
    (tmp_path / "options.json").write_text("{}")

    async def apply_settings(raw):  # wie main.App.apply_settings, ohne Neuaufbau der Engine
        clean, errors = validate(raw)
        if errors:
            return errors
        save_settings(clean, tmp_path)
        new_cfg = Config.load(tmp_path / "options.json")
        app.cfg = app.eng.cfg = new_cfg
        app.eng.found = discover(states, reg, new_cfg)
        return []

    app.apply_settings = apply_settings
    return app


def test_api_entities_and_settings_track_preset_changes(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot.web import make_app as web_app

    app = web_app_with_rooms(tmp_path)

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            ents = {e["id"]: e for e in await (await client.get("api/entities")).json()}
            wz = ents[WZ]
            assert wz["area"] == "Wohnzimmer" and wz["device_name"].endswith("VA0302920704")
            assert wz["device_model"] == "SRT01" and wz["controller"] is False and wz["platform"] == "homekit_controller"
            kinds = {s["kind"]: s["id"] for s in wz["siblings"]}
            assert kinds["temperature"].endswith("_current_temperature") and kinds["humidity"].endswith("_current_humidity")
            assert kinds["other"].endswith("_battery") and all({"id", "kind", "name", "unit"} <= set(s) for s in wz["siblings"])
            assert ents[BAD]["controller"] is True and ents[FLUR]["area"] is None
            assert ents[ents[WZ]["siblings"][0]["id"]]["area"] == "Wohnzimmer"  # Bereich auch für Sensoren
            assert "siblings" not in ents["sensor.boiler_curflowtemp"]
            # Speichern: erstes Eintragen (unbekannt -> 5) ist keine Änderung; 5 -> 6 wird gemerkt
            body = {"rooms": [{"climate": WZ, "name": "Wohnzimmer", "preset": 5, "preset_max": 6},
                              {"climate": BAD, "name": "Bad", "preset": 4, "preset_max": 6}]}
            r = await client.post("/api/settings", json=body)
            j = await r.json()
            assert r.status == 200 and j["ok"] and j["preset_changes"] == []
            assert j["rooms"] == [{"name": "Wohnzimmer", "climate": WZ, "controller": False, "members": []},
                                  {"name": "Bad", "climate": BAD, "controller": False, "members": []}]
            body["rooms"][0]["preset"] = 6
            j = await (await client.post("/api/settings", json=body)).json()
            assert len(j["preset_changes"]) == 1
            ch = j["preset_changes"][0]
            assert ch["room"] == WZ and ch["from"] == 5 and ch["to"] == 6 and "ts" in ch and "flow_before" in ch
            kv = app.eng.store.get(advice.PRESET_CHANGES_KEY)
            assert kv == [ch]
            s = (await (await client.get("/api/settings")).json())
            assert s["balance"]["changes"] == [ch] and s["settings"]["rooms"][0]["preset"] == 6
            # Gruppe: Schlüssel ist der Raumregler, das Ventil Mitläufer
            body = {"rooms": [{"climate": WZ, "group": "Bad", "preset": 6, "preset_max": 6},
                              {"climate": BAD, "group": "Bad", "controller": True, "preset": 4, "preset_max": 6}]}
            j = await (await client.post("/api/settings", json=body)).json()
            assert j["rooms"] == [{"name": "Bad", "climate": BAD, "controller": True, "members": [WZ]}]
            assert j["preset_changes"] == []
        finally:
            await client.close()

    asyncio.run(go())
