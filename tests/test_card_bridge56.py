"""0.56: Brücke zu den HeatPilot-Karten – Raum- und Boost-Sensoren, Befehls-Helfer input_text.heatpilot_befehl.

Regeln: je Raum ein Sensor mit den Werten der Raumkachel; ein Boost erscheint in Raum- und Boost-Sensor; ein Befehl
aus dem Helfer läuft über dieselbe Stelle wie die Seite (heat_actions), das Feld wird geleert und das Ergebnis in
sensor.heatpilot_boost → last gemeldet; Unlesbares und Pause werden ignoriert; der Helfer wird nur angelegt, wenn er fehlt.
"""

import asyncio
import json
import time
from types import SimpleNamespace as NS

from heatpilot import card_bridge as cb
from heatpilot import control as control_mod

ROOM = NS(climate="climate.wohnzimmer", name="Wohnzimmer", heating_power=None, humidity=None, managed=True, ems=False)


class FakeCtl:
    def __init__(self):
        self.calls, self.boosts = [], {}

    def active(self, targets=None, now=None):
        return {r: {**b, "left_min": 60} for r, b in self.boosts.items()}

    async def boost(self, rooms, temp, minutes, own=frozenset()):
        if not rooms:
            raise control_mod.ControlError("Kein Raum gewählt")
        self.calls.append(("boost", rooms, temp, minutes))
        for r in rooms:
            self.boosts[r] = {"temp": float(temp), "end": time.time() + minutes * 60}
        return {"ok": True, "done": rooms, "errors": []}


class FakeHA:
    def __init__(self):
        self.states, self.calls = {}, []

    async def set_state(self, entity, state, attrs):
        self.states[entity] = (state, attrs)

    async def remove_state(self, entity):
        self.removed = getattr(self, "removed", []) + [entity]

    async def call(self, payload, timeout=120):
        self.calls.append(payload)


def make_app(helper=True):
    states = {"climate.wohnzimmer": {"state": "auto", "attributes": {"temperature": 21, "current_temperature": 20.4,
                                                                    "hvac_action": "heating", "current_humidity": 48}}}
    if helper:
        states[cb.COMMAND] = {"state": ""}
    loop = NS(rooms={"climate.wohnzimmer": {"phase": "komfort", "desired": 21.0, "lead": "temp"}},
              desired_setpoints=lambda: {})  # 0.71: von HeatPilot geführte Räume (hier keine)
    eng = NS(found=NS(rooms=[ROOM]), states=states, control_loop=loop, room_reading=lambda r: None,
             _val=lambda v: None, cfg=NS(is_managing=lambda: True), store={})  # 0.62: Override im kv

    async def refresh():
        return None
    return NS(eng=eng, control=FakeCtl(), ha=FakeHA(), refresh_rooms=refresh)


def run(coro):
    return asyncio.run(coro)


def test_room_and_boost_sensors_like_the_tile(monkeypatch):
    app = make_app()
    app.control.boosts["climate.wohnzimmer"] = {"temp": 23.0, "end": time.time() + 3600}
    assert run(cb.publish(app)) == 3  # Raum, Boost und seit 0.62 sensor.heatpilot_modus
    assert app.ha.states["sensor.heatpilot_modus"][0] == "auto"
    state, a = app.ha.states["sensor.heatpilot_raum_wohnzimmer"]
    assert state == 20.4 and a["target"] == 21 and a["phase"] == "komfort" and a["humidity"] == 48
    assert a["action"] == "heating" and a["boost"]["temp"] == 23.0 and a["boost"]["left_min"] == 60
    assert a["managing"] is True and a["manual"] is False and a["valve_hold"] is False  # 0.56.2: wie die Kachel
    n, b = app.ha.states[cb.BOOST]
    assert n == 1 and b["rooms"][0]["room"] == "Wohnzimmer" and b["command"] == cb.COMMAND


def test_command_runs_clears_field_and_reports(monkeypatch):
    monkeypatch.setattr(cb.asyncio, "sleep", lambda s, _real=asyncio.sleep: _real(0))
    app = make_app()
    cmd = json.dumps({"a": "boost", "rooms": ["climate.wohnzimmer"], "temp": 22, "minutes": 120, "n": 7})
    last = run(cb.handle(app, cmd))
    assert app.control.calls == [("boost", ["climate.wohnzimmer"], 22, 120)]
    assert last["ok"] and last["n"] == 7 and app.ha.states[cb.BOOST][1]["last"]["action"] == "boost"
    clear = [c for c in app.ha.calls if c.get("service") == "set_value"]
    assert clear and clear[0]["target"] == {"entity_id": cb.COMMAND} and clear[0]["service_data"] == {"value": ""}


def test_errors_are_reported_not_raised(monkeypatch):
    monkeypatch.setattr(cb.asyncio, "sleep", lambda s, _real=asyncio.sleep: _real(0))
    app = make_app()
    last = run(cb.handle(app, json.dumps({"a": "boost", "rooms": [], "temp": 22, "minutes": 60})))
    assert last["ok"] is False and last["text"] == "Kein Raum gewählt"


def test_unreadable_empty_and_pause_are_ignored():
    for v in ("", "unknown", "kaputt{", json.dumps({"a": "pause", "hours": 2}), json.dumps(["boost"]), None):
        assert cb.parse(v) is None
    app = make_app()
    assert run(cb.handle(app, "")) is None and not app.ha.calls


def test_helper_created_only_when_missing():
    app = make_app(helper=False)
    assert run(cb.ensure_command(app)) and app.ha.calls[0]["type"] == "input_text/create"
    app2 = make_app(helper=True)
    assert not run(cb.ensure_command(app2)) and not app2.ha.calls


def test_sensors_named_after_room_old_names_removed():  # 0.56.3
    assert cb.slug("kleines Bad") == "kleines_bad" and cb.slug("Büro/Gäste") == "buro_gaste" and cb.slug("Große Diele") == "grosse_diele"
    rs = [NS(climate="climate.tado_a", name="Bad"), NS(climate="climate.tado_b", name="Bad")]
    assert cb.room_entities(rs) == {"climate.tado_a": "sensor.heatpilot_raum_bad", "climate.tado_b": "sensor.heatpilot_raum_bad_2"}
    app = make_app()
    app.eng.states["sensor.heatpilot_raum_tado_smart_thermostat_su1"] = {"state": "20"}
    run(cb.publish(app))
    assert "sensor.heatpilot_raum_wohnzimmer" in app.ha.states
    assert app.ha.removed == ["sensor.heatpilot_raum_tado_smart_thermostat_su1"]
