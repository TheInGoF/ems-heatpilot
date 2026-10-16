import asyncio
import json
import logging
import time
from types import SimpleNamespace

from heatpilot import diagnostics
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.store import Store


def make_app(tmp_path, **found_kw):
    found = Discovered(
        boiler={"flow_temp": "sensor.boiler_curflowtemp", "burner_on": "binary_sensor.boiler_burngas"},
        rooms=[Room("Wz", "climate.wz")], persons=["person.a"], home_zone=(53.55, 10.0),
        gas="sensor.gas", gas_unit="m³", outdoor=["sensor.out"], **found_kw,
    )
    eng = Engine(Config(), Store(tmp_path / "d.db"), found)
    connected = asyncio.Event()
    connected.set()
    return SimpleNamespace(eng=eng, cfg=Config(), status="läuft", configured=True,
                           ha=SimpleNamespace(connected=connected))


def checks(app):
    return {(c["level"], c["area"]): c["message"] for c in diagnostics.self_check(app)}


def test_self_check_finds_missing_stale_and_implausible(tmp_path):
    app = make_app(tmp_path)
    eng = app.eng
    old = "2026-01-01T00:00:00+00:00"
    eng.states.update({
        "sensor.out": {"state": "4", "attributes": {}, "last_updated": old},
        "sensor.boiler_curflowtemp": {"state": "140", "attributes": {}},
        "climate.wz": {"state": "heat", "attributes": {"current_temperature": 21, "temperature": 21}},
    })
    msgs = " | ".join(m for m in (c["message"] for c in diagnostics.self_check(app)))
    assert "sensor.gas existiert nicht" in msgs
    assert "sensor.out seit" in msgs and "nicht aktualisiert" in msgs
    assert "Vorlauftemperatur 140" in msgs
    assert "Noch keine Messwerte" in msgs


def test_self_check_gas_meter_not_counting(tmp_path):
    app = make_app(tmp_path)
    now = int(time.time())
    for i in range(12):  # 1 h Brenner an, Zähler steht
        app.eng.store.add_sample(now - 3600 + i * 300, {"gas": 1234.5, "burner_on": 1})
    app.eng.store.commit()
    assert any("Gaszähler blieb" in m for m in checks(app).values())


def test_self_check_reports_program_errors():
    diagnostics.RING.records.clear()
    logger = logging.getLogger("heatpilot.test")
    logger.addHandler(diagnostics.RING)
    try:
        raise ValueError("kaputt")
    except ValueError:
        logger.exception("Fehler im Takt")
    rec = diagnostics.RING.tail(5, "ERROR")[-1]
    assert rec["message"] == "Fehler im Takt" and "ValueError: kaputt" in rec["exception"]
    logger.removeHandler(diagnostics.RING)


def test_bundle_has_everything_but_no_gps(tmp_path):
    app = make_app(tmp_path)
    app.eng.states["person.a"] = {"state": "not_home", "attributes": {
        "latitude": 53.9, "longitude": 10.4, "gps_accuracy": 5, "friendly_name": "A"}}
    app.eng.states["climate.wz"] = {"state": "heat", "attributes": {"current_temperature": 20.5, "temperature": 21}}
    app.eng.tick(int(time.time()))
    data = diagnostics.bundle(app)
    text = json.dumps(data, default=str)
    assert "53.9" not in text and "10.4" not in text and "latitude" not in text
    assert "53.55" not in text  # auch nicht der Standort des Zuhauses (home_zone)
    assert data["discovered"]["home_zone"] is True
    for key in ("settings", "self_check", "log", "discovered", "summary", "models", "samples", "db"):
        assert key in data
    assert data["entity_states"]["person.a"]["attributes"] == {"friendly_name": "A"}
    assert data["samples"] and data["person_samples"][0]["dist_km"] > 30  # Entfernung bleibt
