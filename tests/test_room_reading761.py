"""0.76.1: Raumregler ohne Temperatur (Thermostat offline, ``current_temperature`` fehlt) – kein Absturz.

Am 21.11.2026 brach damit jeder Live-Schritt ab (TypeError in ``room_reading``): Live, Heizen, Übersicht, Diagnose und
Kessel über HeatPilot standen."""

from heatpilot.discovery import Room
from test_valve39 import engine


def test_controller_without_temperature(tmp_path):
    wall = Room("Wohnen", "climate.wall", controller=True, members=[{"climate": "climate.b"}])
    eng = engine(tmp_path, [wall])
    eng.states.update({
        "climate.wall": {"state": "heat", "attributes": {"temperature": 21.0, "hvac_action": "heating"}},
        "climate.b": {"state": "heat", "attributes": {"current_temperature": 20.4, "temperature": 21.0}}})
    assert eng.room_reading(wall) == ("climate.wall", 20.4, 21.0, 50.0, None)  # Ersatz: das Mittel der übrigen
    eng.states["climate.b"]["attributes"].pop("current_temperature")
    assert eng.room_reading(wall)[1] is None
    eng.states["climate.wall"]["attributes"]["current_temperature"] = 19.8
    eng.states["climate.b"]["attributes"]["current_temperature"] = 20.4
    assert eng.room_reading(wall)[1] == 19.8  # mit Wert zählt nur der Raumregler
    eng.snapshot()  # läuft durch
