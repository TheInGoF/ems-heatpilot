"""0.65: Räume, die keine sind, dauerhaft ausblenden (hidden_rooms, auch für die Erkennung)."""

from heatpilot.config import Config
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
