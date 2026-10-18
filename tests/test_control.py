"""Bedienung von Tado: nur erlaubte Aufrufe, nur gewählte Räume, Timer läuft in Tado."""

import asyncio

import pytest

from heatpilot.control import Control, ControlError
from heatpilot.discovery import Room
from heatpilot.store import Store


class FakeHA:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    async def call(self, payload, timeout=0):
        if payload["target"]["entity_id"] in self.fail:
            raise RuntimeError("Tado nicht erreichbar")
        self.calls.append((payload["domain"], payload["service"], payload["target"]["entity_id"], payload["service_data"]))


def run(coro):
    return asyncio.run(coro)


def make(tmp_path, fail=()):
    ha = FakeHA(fail)
    # seit 0.34 entscheidet die Plattform laut Registry: Tado-Cloud-Entitäten bekommen den Tado-Timer
    rooms = [Room("Bad", "climate.bad", platform="tado"), Room("Flur", "climate.flur", platform="tado")]
    return ha, Control(ha, Store(tmp_path / "c.db"), rooms)


def test_boost_uses_tado_timer_only(tmp_path):
    ha, c = make(tmp_path)
    res = run(c.boost(["climate.bad", "climate.flur"], 21.3, 90))
    assert res["ok"] and res["done"] == ["climate.bad", "climate.flur"]
    assert ha.calls[0] == ("tado", "set_climate_timer", "climate.bad", {"temperature": 21.5, "time_period": "01:30:00"})
    assert {s for _, s, _, _ in ha.calls} == {"set_climate_timer"}
    act = c.active({"climate.bad": 21.5, "climate.flur": 18.0})
    assert act["climate.bad"]["left_min"] in (89, 90) and "climate.flur" not in act  # Soll geändert: kein Timer mehr


def test_unknown_room_and_limits_rejected(tmp_path):
    ha, c = make(tmp_path)
    with pytest.raises(ControlError):
        run(c.boost(["climate.garage"], 21, 60))
    with pytest.raises(ControlError):
        run(c.boost(["climate.bad"], 30, 60))
    with pytest.raises(ControlError):
        run(c.boost(["climate.bad"], 21, 5))
    with pytest.raises(ControlError):
        run(c.set_mode("climate.bad", "boost"))
    assert ha.calls == []


def test_only_allowed_services(tmp_path):
    ha, c = make(tmp_path)
    with pytest.raises(PermissionError):
        run(c._call("switch", "turn_on", "climate.bad", {}))
    with pytest.raises(PermissionError):
        run(c._call("climate", "set_temperature", "number.boiler_dhw_seltemp", {}))


def test_schedule_ends_timer_and_errors_per_room(tmp_path):
    ha, c = make(tmp_path, fail={"climate.flur"})
    run(c.boost(["climate.bad"], 22, 60))
    res = run(c.schedule(["climate.bad", "climate.flur"]))
    assert not res["ok"] and res["done"] == ["climate.bad"] and "climate.flur" in res["errors"][0]
    assert ha.calls[-1] == ("climate", "set_hvac_mode", "climate.bad", {"hvac_mode": "auto"})
    assert c.active() == {}


def test_all_rooms_off_in_one_call(tmp_path):
    """Dialog „Ausschalten“: Betriebsart für mehrere Räume, ein einzelner Raum weiter als Text."""
    ha, c = make(tmp_path)
    res = run(c.set_mode(["climate.bad", "climate.flur"], "off"))
    assert res["ok"] and res["done"] == ["climate.bad", "climate.flur"]
    assert [x[2] for x in ha.calls] == ["climate.bad", "climate.flur"]
    assert all(x[:2] == ("climate", "set_hvac_mode") and x[3] == {"hvac_mode": "off"} for x in ha.calls)
    run(c.set_mode("climate.bad", "auto"))
    assert ha.calls[-1][2:] == ("climate.bad", {"hvac_mode": "auto"})
    with pytest.raises(ControlError):
        run(c.set_mode(["climate.bad", "climate.garage"], "off"))
