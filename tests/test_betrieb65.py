"""0.65: Räume, die keine sind, dauerhaft ausblenden (hidden_rooms, auch für die Erkennung) und die drei
Betriebsschalter für das Dashboard (Befehl „betrieb“, sensor.heatpilot_betrieb)."""

import asyncio
from types import SimpleNamespace

import pytest

from heatpilot import card_bridge, heat_actions
from heatpilot.config import Config
from heatpilot.control import ControlError
from heatpilot.discovery import discover

from test_rooms_ac591 import climate
from test_ui_v361 import script, source


def test_hidden_rooms_are_not_discovered():
    rows = [climate("climate.wohnzimmer", ["off", "heat"]), climate("climate.auto", ["off", "heat"])]
    states, registry = [r[0] for r in rows], [r[1] for r in rows]
    assert [r.climate for r in discover(states, registry, Config()).rooms] == ["climate.auto", "climate.wohnzimmer"]
    cfg = Config.from_raw({"hidden_rooms": ["climate.auto"]})
    assert [r.climate for r in discover(states, registry, cfg, all_rooms=True).rooms] == ["climate.wohnzimmer"]


def test_ui_hides_and_saves_hidden_rooms():
    js = script()
    assert "out.hidden_rooms = [...ROOM_HIDE];" in source(js, "collect")
    assert "ROOM_HIDE = new Set(s.hidden_rooms || []);" in source(js, "renderSettings")
    tiles = source(js, "roomTiles")
    assert "ausgeblendet – zeigen" in tiles and "roomHideBtn(tr.dataset.climate)" in tiles
    assert '"Ausblenden – kein Raum"' in source(js, "roomHideBtn")
    assert '.querySelector(".r-on").checked = false' in source(js, "roomHide")


class App(SimpleNamespace):
    async def apply_settings(self, raw):
        self.saved = raw
        if raw.get("boiler_control") not in (None, "aus", "trocken", "heatpilot"):
            return ["boiler_control: unbekannt"]
        for k, v in raw.items():
            setattr(self.cfg, k, v)
        return []


def test_betrieb_action_and_sensor():
    app = App(cfg=Config(), eng=SimpleNamespace(control_loop=None), control=None)
    res = asyncio.run(heat_actions.run(app, "betrieb", {"what": "kessel", "mode": "trocken"}))
    assert res["ok"] and app.saved == {"boiler_control": "trocken"} and app.cfg.boiler_control == "trocken"
    with pytest.raises(ControlError):
        asyncio.run(heat_actions.run(app, "betrieb", {"what": "garage", "mode": "aus"}))
    with pytest.raises(ControlError):
        asyncio.run(heat_actions.run(app, "betrieb", {"what": "kessel", "mode": "turbo"}))
    eid, state, attrs = card_bridge.betrieb(app.cfg)
    assert (eid, state, attrs["kessel"], attrs["warmwasser"]) == ("sensor.heatpilot_betrieb", "beobachten", "trocken", "aus")
    assert "betrieb" in card_bridge.CARD_ACTIONS
