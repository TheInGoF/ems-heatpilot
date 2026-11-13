"""0.56.4: Einstellungen → Haus → Experte → „Karten für Home Assistant“ – api/cards liefert Stand und Entitäten, die Seite
zeigt Einrichtung und YAML der drei Karten. Seit 0.75.3 bedienen die Karten auch wieder das Soll (temp – das Pop-up der
Raumkachel) und nennen Räume als Nummer."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from heatpilot import card_bridge as cb


def test_api_cards_lists_helper_rooms_and_weather():
    rooms = [NS(climate="climate.tado_1", name="Wohnzimmer"), NS(climate="climate.tado_2", name="kleines Bad")]
    eng = NS(found=NS(rooms=rooms, weather="weather.wetter_lokal"), states={cb.COMMAND: {"state": ""}})
    app = NS(eng=eng)
    handler = cb.routes(app, lambda d, status=200: d)[0][2]
    d = asyncio.run(handler(None))
    assert d["helper"] is True and d["weather"] == "weather.wetter_lokal" and d["repo"] == cb.REPO
    assert [r["entity"] for r in d["rooms"]] == ["sensor.heatpilot_raum_wohnzimmer", "sensor.heatpilot_raum_kleines_bad"]


def test_temp_is_a_card_command_and_pause_is_not():
    assert cb.parse(json.dumps({"a": "temp", "room": 0, "temp": 21}))["a"] == "temp"
    assert cb.parse(json.dumps({"a": "mode", "rooms": ["climate.x"], "mode": "off"}))["a"] == "mode"
    assert cb.parse(json.dumps({"a": "pause", "hours": 2})) is None


def test_rooms_by_number_become_entities():
    eng = NS(found=NS(rooms=[NS(climate="climate.tado_1"), NS(climate="climate.tado_2")]))
    assert cb._climates(eng, {"a": "temp", "room": 1, "temp": 21}) == {"a": "temp", "room": "climate.tado_2", "temp": 21}
    assert cb._climates(eng, {"a": "boost", "rooms": [0, "climate.x"]})["rooms"] == ["climate.tado_1", "climate.x"]
    with pytest.raises(cb.control_mod.ControlError):
        cb._climates(eng, {"a": "temp", "room": 2})


def test_settings_page_shows_cards_section():
    page = (Path(__file__).parents[1] / "ems_heatpilot/heatpilot/static/index.html").read_text()
    assert 'card("Karten für Home Assistant"' in page and 'getJSON("api/cards")' in page
    assert "custom:heatpilot-control-card" in page and "custom:heatpilot-boost-card" not in page
    assert 'if (SET_PAGE === "haus") loadCards()' in page
