"""Tado-Sensoren auch mit deutschen (oder anderen) Entitätsnamen erkennen."""

from heatpilot.config import Config
from heatpilot.discovery import discover


def tado_room(room, heating_id, heating_attrs, reg_extra=None):
    dev = f"dev_{room}"
    states = [
        {"entity_id": f"climate.{room}", "state": "heat",
         "attributes": {"friendly_name": room.title(), "current_temperature": 20.0}},
        {"entity_id": heating_id, "state": "40", "attributes": heating_attrs},
        {"entity_id": f"sensor.{room}_luftfeuchtigkeit", "state": "50",
         "attributes": {"unit_of_measurement": "%", "device_class": "humidity"}},
        {"entity_id": f"sensor.{room}_temperatur", "state": "20",
         "attributes": {"unit_of_measurement": "°C", "device_class": "temperature"}},
    ]
    registry = [{"entity_id": e["entity_id"], "platform": "tado", "device_id": dev, **(reg_extra or {}).get(e["entity_id"], {})}
                for e in states]
    return states, registry


def room_of(states, registry):
    return discover(states, registry, Config()).rooms[0]


def test_german_name():
    st, reg = tado_room("kuche", "sensor.kuche_heizleistung", {"unit_of_measurement": "%", "friendly_name": "Küche Heizleistung"})
    r = room_of(st, reg)
    assert r.heating_power == "sensor.kuche_heizleistung"
    assert r.humidity == "sensor.kuche_luftfeuchtigkeit" and r.temp_sensor == "sensor.kuche_temperatur"


def test_by_registry_key_whatever_the_name():
    st, reg = tado_room("bad", "sensor.bad_xyz", {"unit_of_measurement": "%"},
                        {"sensor.bad_xyz": {"translation_key": "heating"}})
    assert room_of(st, reg).heating_power == "sensor.bad_xyz"


def test_only_percent_sensor_besides_humidity():
    st, reg = tado_room("flur", "sensor.flur_unbekannt", {"unit_of_measurement": "%"})
    assert room_of(st, reg).heating_power == "sensor.flur_unbekannt"


def test_english_name_still_works():
    st, reg = tado_room("wz", "sensor.wz_heating", {"unit_of_measurement": "%"})
    assert room_of(st, reg).heating_power == "sensor.wz_heating"
