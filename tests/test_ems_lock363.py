"""0.36.3: Sperre gegen den Heizkreis von EMS-ESP als Raum – erkannt am Gerät, nicht am Namen.

Wählt jemand den Heizkreis von EMS-ESP (``climate.thermostat_hc1``) als Raum mit „macht mit“, ginge ein Soll
über Home Assistant an den Regler. Er macht deshalb nie mit, Control weist jeden Schreibvorgang darauf hart ab,
„Bereit für die Steuerung?“ nennt ihn „nicht dabei“. Tado-Zonen und HomeKit-Ventile, die auch …_thermostat
heißen, bleiben beschreibbar.
"""

import asyncio
import logging
import time
from pathlib import Path

import pytest

from heatpilot import readiness
from heatpilot.config import Config
from heatpilot.control import Control
from heatpilot.discovery import Registry, Room, climate_info, discover
from heatpilot.ems_lock import LOCK_TEXT, is_ems_climate
from heatpilot.store import Store

from test_control import FakeHA, run
from test_control34 import climate, make, step, ts_of, MON

ROOT = Path(__file__).resolve().parents[1]
EMS = "climate.thermostat_hc1"
TADO = "climate.bad_thermostat"            # Tado-Cloud-Zone „Bad Thermostat“
HOMEKIT = "climate.wohnzimmer_thermostat"  # HomeKit-Ventil „Wohnzimmer Thermostat“


def state(eid, name):
    return {"entity_id": eid, "state": "heat",
            "attributes": {"friendly_name": name, "current_temperature": 20.0, "temperature": 21.0,
                           "hvac_modes": ["off", "heat", "auto"]}}


def registry(with_devices=True, ems_device=None):
    """EMS-ESP-Heizkreis über MQTT, Tado-Zone und HomeKit-Ventil – mit Geräten oder ohne (alte HA)."""
    ems_dev = ems_device or {"id": "dev_ems", "name": "EMS-ESP Thermostat", "manufacturer": "Bosch", "model": "RC310"}
    devices = [ems_dev, {"id": "dev_bad", "name": "Bad", "manufacturer": "Tado", "model": "RU01"},
               {"id": "dev_wz", "name": "tado Smart Radiator Thermostat VA02", "manufacturer": "tado",
                "model": "SRT01"},
               {"id": "dev_gw", "name": "EMS-ESP", "manufacturer": "proddy", "model": "EMS-ESP32"}]
    entities = [{"entity_id": EMS, "platform": "mqtt", "device_id": ems_dev["id"] if with_devices else None},
                {"entity_id": TADO, "platform": "tado", "device_id": "dev_bad" if with_devices else None},
                {"entity_id": HOMEKIT, "platform": "homekit_controller", "device_id": "dev_wz" if with_devices else None}]
    states = [state(EMS, "Thermostat hc1"), state(TADO, "Bad Thermostat"), state(HOMEKIT, "Wohnzimmer Thermostat")]
    return states, Registry(entities, devices if with_devices else [], [])


# ------------------------------------------------------------------ Erkennen
@pytest.mark.parametrize("with_devices", [True, False])
def test_ems_heating_circuit_locked_tado_and_homekit_thermostats_not(with_devices):
    states, reg = registry(with_devices)
    idx, by_id = reg.lookup(), {s["entity_id"]: s for s in states}
    assert is_ems_climate(EMS, idx) and climate_info(EMS, idx, by_id)["ems"] is True
    for eid in (TADO, HOMEKIT):  # heißen auch …_thermostat – bleiben Räume, die HeatPilot beschreiben darf
        assert not is_ems_climate(eid, idx) and climate_info(eid, idx, by_id)["ems"] is False, eid


@pytest.mark.parametrize("device", [
    {"id": "d", "name": "Heizung", "manufacturer": "proddy", "model": "RC310"},             # Hersteller proddy
    {"id": "d", "name": "Thermostat", "manufacturer": "Bosch", "model": "ems-esp RC310"},   # Modell, klein geschrieben
    {"id": "d", "name": "Heizkreis", "manufacturer": "Bosch", "model": "RC310", "via_device_id": "dev_gw"},  # über EMS-ESP
])
def test_ems_device_by_manufacturer_model_or_gateway(device):
    _, reg = registry(ems_device=device)
    assert is_ems_climate(EMS, reg.lookup())


def test_name_alone_counts_only_without_device_and_only_for_mqtt():
    reg = Registry([{"entity_id": "climate.ems_esp_thermostat_hc2", "platform": "mqtt"},
                    {"entity_id": "climate.wohnzimmer", "platform": "mqtt"},
                    {"entity_id": "climate.flur_thermostat", "platform": "zha"},
                    {"entity_id": "climate.keller_thermostat", "platform": "mqtt", "device_id": "dev_z"}],
                   [{"id": "dev_z", "name": "Zigbee-Thermostat Keller", "manufacturer": "Moes", "model": "BRT-100"}])
    idx = reg.lookup()
    assert is_ems_climate("climate.ems_esp_thermostat_hc2", idx)
    assert not is_ems_climate("climate.wohnzimmer", idx)          # MQTT, aber kein Heizkreis-Name
    assert not is_ems_climate("climate.flur_thermostat", idx)     # anderer Name, andere Plattform
    assert not is_ems_climate("climate.keller_thermostat", idx)   # MQTT mit Gerät eines anderen Herstellers
    assert not is_ems_climate("climate.unbekannt", idx)           # nicht in der Registry


def test_configured_ems_room_never_managed_with_warning(caplog):
    states, reg = registry()
    cfg = Config.from_raw({"rooms": [{"climate": EMS, "name": "Heizkreis", "managed": True},
                                     {"climate": TADO, "name": "Bad", "managed": True},
                                     {"climate": HOMEKIT, "name": "Wohnzimmer", "managed": True}]})
    with caplog.at_level(logging.WARNING):
        found = discover(states, reg, cfg)
    rooms = {r.climate: r for r in found.rooms}
    assert rooms[EMS].ems and not rooms[EMS].managed
    assert rooms[TADO].managed and not rooms[TADO].ems and rooms[HOMEKIT].managed and not rooms[HOMEKIT].ems
    assert f"Heizkreis macht nicht mit: {LOCK_TEXT}" in caplog.text
    assert found.as_dict()["rooms"][0]["ems"] is True  # auch für Oberfläche und Diagnose
    # Gruppe mit einem EMS-Gerät: der ganze Raum macht nicht mit (es würde sonst auch dorthin geschrieben)
    cfg = Config.from_raw({"rooms": [{"climate": HOMEKIT, "group": "Wohnzimmer", "managed": True},
                                     {"climate": EMS, "group": "Wohnzimmer", "managed": True}]})
    room = discover(states, reg, cfg).rooms[0]
    assert room.ems and not room.managed and room.climates == [HOMEKIT, EMS]
    # Vorschläge: der Heizkreis wird nicht als Raum angeboten, Tado und HomeKit schon
    found = discover(states, reg, Config(), all_rooms=True)
    assert EMS not in [r.climate for r in found.rooms] and {TADO, HOMEKIT} <= {r.climate for r in found.rooms}


def test_ems_device_with_other_entity_name_not_suggested():
    """Umbenannte Entität (climate.heizung) eines EMS-ESP-Geräts: kein Raumvorschlag, als Raum gesperrt."""
    reg = Registry([{"entity_id": "climate.heizung", "platform": "mqtt", "device_id": "dev_ems"}],
                   [{"id": "dev_ems", "name": "EMS-ESP Thermostat", "manufacturer": "Bosch", "model": "RC310"}])
    states = [state("climate.heizung", "Heizung")]
    assert discover(states, reg, Config(), all_rooms=True).rooms == []
    cfg = Config.from_raw({"rooms": [{"climate": "climate.heizung", "managed": True}]})
    room = discover(states, reg, cfg).rooms[0]
    assert room.ems and not room.managed


# ------------------------------------------------------------------ Schreiben hart abgewiesen
def test_control_rejects_every_write_to_ems_room(tmp_path):
    ha = FakeHA()
    rooms = [Room("Heizkreis", EMS, platform="mqtt", managed=True, ems=True),  # selbst wenn „macht mit“ durchkäme
             Room("Bad", TADO, platform="tado", managed=True)]
    ctl = Control(ha, Store(tmp_path / "c.db"), rooms)
    with pytest.raises(PermissionError, match="Heizkreis von EMS-ESP"):
        run(ctl._call("climate", "set_temperature", EMS, {"temperature": 20.0}))
    for res in (run(ctl.boost([EMS], 21.0, 60)), run(ctl.set_temp(EMS, 20.0)), run(ctl.set_mode(EMS, "off"))):
        assert not res["ok"] and "gesperrt" in res["errors"][0] and LOCK_TEXT in res["errors"][0]
    assert "hint" in run(ctl.schedule([EMS]))  # kein Tado: nur der Hinweis, nichts geschrieben
    with pytest.raises(PermissionError):
        run(ctl.apply(EMS, 20.0, "Regelschleife"))
    assert ha.calls == []
    assert EMS not in ctl.rooms and TADO in ctl.rooms
    res = run(ctl.boost([TADO], 21.0, 60))  # die Tado-Zone „…_thermostat“ bleibt beschreibbar
    assert res["ok"] and ha.calls == [("tado", "set_climate_timer", TADO, {"temperature": 21.0,
                                                                        "time_period": "01:00:00"})]


def test_control_loop_never_writes_to_ems_room(tmp_path):
    states, reg = registry()
    cfg = Config.from_raw({"control_mode": "heatpilot",
                           "rooms": [{"climate": EMS, "name": "Heizkreis", "managed": True},
                                     {"climate": TADO, "name": "Bad", "managed": True}]})
    found = discover(states, reg, cfg)
    eng, ha, _, loop = make(tmp_path, rooms=found.rooms, cfg=cfg)
    for eid in (EMS, TADO):
        climate(eng, eid, 20.0, 19.0)
    step(eng, loop, ts_of(MON, 12))
    assert ha.calls and all(c[2] == TADO for c in ha.calls)
    assert loop.rooms[EMS]["managed"] is False


# ------------------------------------------------------------------ Bereitschaft und Oberfläche
def test_readiness_ems_room_not_taking_part(tmp_path):
    from test_readiness36 import house, room

    eng, ha, _ = house(tmp_path, [room("Wz", "climate.wz"), room("Heizkreis", EMS, managed=False, ems=True)])
    res = readiness.assess(eng, int(time.time()))
    hk = next(r for r in res["rooms"] if r["climate"] == EMS)
    assert hk["state"] == "nicht dabei" and hk["text"] == LOCK_TEXT and hk["managed"] is False
    assert f"Heizkreis: {LOCK_TEXT} – macht nicht mit" in res["notes"]
    assert res["level"] == "bereit" and res["counts"]["managed"] == 1
    # macht nur der Heizkreis „mit“: nicht bereit, er zählt nicht
    eng, _, _ = house(tmp_path / "b", [room("Heizkreis", EMS, managed=True, ems=True)])
    res = readiness.assess(eng, int(time.time()))
    assert res["level"] == "nicht bereit" and res["headline"] == "Nicht bereit – kein Raum macht mit."
    assert ha.calls == []


def test_api_heat_settings_and_entities_carry_ems_flag(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot.web import make_app as web_app
    from test_control34 import make_app

    states, reg = registry()
    app, ha, _ = make_app(tmp_path / "eng")
    for s in states:
        app.eng.states[s["entity_id"]] = s
    cfg = Config.from_raw({"rooms": [{"climate": EMS, "name": "Heizkreis", "managed": True},
                                     {"climate": TADO, "name": "Bad", "managed": True}]})
    app.eng.found = discover(states, reg, cfg)
    app.cfg = app.eng.cfg = cfg
    app.control = Control(ha, app.eng.store, app.eng.found.rooms, states=app.eng.states)

    async def registry_():
        return reg

    app.registry = registry_

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            heat = await (await client.get("/api/heat")).json()
            rooms = {r["climate"]: r for r in heat["rooms"]}
            assert rooms[EMS]["ems"] is True and rooms[EMS]["managed"] is False and rooms[TADO]["ems"] is False
            hk = next(r for r in heat["readiness"]["rooms"] if r["climate"] == EMS)
            assert hk["state"] == "nicht dabei" and hk["text"] == LOCK_TEXT
            r = await client.post("/api/heat/temp", json={"room": EMS, "temp": 20})
            j = await r.json()
            assert not j["ok"] and LOCK_TEXT in j["errors"][0]
            ents = {e["id"]: e for e in await (await client.get("/api/entities")).json()}
            assert ents[EMS]["ems"] is True and ents[TADO]["ems"] is False and ents[HOMEKIT]["ems"] is False
            s = await (await client.get("/api/settings")).json()
            disc = {r["climate"]: r for r in s["discovered"]["rooms"]}
            assert disc[EMS]["ems"] is True and disc[EMS]["managed"] is False
        finally:
            await client.close()

    asyncio.run(go())
    assert ha.calls == []


def test_docs_say_hard_rejected():
    src = (ROOT / "ems_heatpilot" / "heatpilot" / "control.py").read_text(encoding="utf-8")
    doc = src.split('"""')[1]
    assert "Heizkreis von EMS-ESP" in doc and "hart abgewiesen" in doc.split("Heizkreis von EMS-ESP")[1]
    assert "Eine eigene Sperre für EMS-ESP gibt es hier nicht" not in doc
    for path in (ROOT / "README.md", ROOT / "ems_heatpilot" / "DOCS.md"):
        text = path.read_text(encoding="utf-8")
        assert "Heizkreis von EMS-ESP" in text and "wird hart abgewiesen" in text, path
        assert "eine eigene Sperre dafür gibt es noch nicht" not in text, path
