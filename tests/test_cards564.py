"""0.56.4: Einstellungen → Erweitert → „Karten für Home Assistant“ – api/cards liefert Stand und Entitäten, die Seite
zeigt Einrichtung und YAML der drei Karten; die Karten bedienen nur noch boost, schedule, mode."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

from heatpilot import card_bridge as cb


def test_api_cards_lists_helper_rooms_and_weather():
    rooms = [NS(climate="climate.tado_1", name="Wohnzimmer"), NS(climate="climate.tado_2", name="kleines Bad")]
    eng = NS(found=NS(rooms=rooms, weather="weather.wetter_lokal"), states={cb.COMMAND: {"state": ""}})
    app = NS(eng=eng)
    handler = cb.routes(app, lambda d, status=200: d)[0][2]
    d = asyncio.run(handler(None))
    assert d["helper"] is True and d["weather"] == "weather.wetter_lokal" and d["repo"] == cb.REPO
    assert [r["entity"] for r in d["rooms"]] == ["sensor.heatpilot_raum_wohnzimmer", "sensor.heatpilot_raum_kleines_bad"]


def test_temp_is_no_card_command_anymore():
    assert cb.parse(json.dumps({"a": "temp", "room": "climate.x", "temp": 21})) is None
    assert cb.parse(json.dumps({"a": "mode", "rooms": ["climate.x"], "mode": "off"}))["a"] == "mode"


def test_settings_page_shows_cards_section():
    page = (Path(__file__).parents[1] / "ems_heatpilot/heatpilot/static/index.html").read_text()
    assert 'card("Karten für Home Assistant"' in page and 'getJSON("api/cards")' in page
    assert "custom:heatpilot-control-card" in page and "custom:heatpilot-boost-card" not in page
    assert 'if (SET_PAGE === "erweitert") loadCards()' in page
