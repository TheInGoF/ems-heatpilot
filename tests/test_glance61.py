"""0.61: Kessel und Haus auf einen Blick (Konzeptpapier, Abschnitt 5) – Aktoren und Sensoren als Gerätekarten aus der
Zuordnung der Entitäten, oben auf den Seiten Kessel und Haus."""

from test_ui_v361 import between, script, source


def test_glance_cards_on_kessel_and_haus():
    js = script()
    assert "page(k, glance(k) + (P[k] || \"\") + mainGroups(k) + expert(k" in between(js, "function renderSettings()", "\nfunction ")
    g = between(js, "const GLANCE = {", "};")
    for key in ("flow_set_entity", "heating_switch_entity", "dhw_set_entity", "flow_temp", "return_temp",
                "outdoor_sensors", "weather_entity", "gas_sensor", "persons"):
        assert f'"{key}"' in g, key
    assert '"Kessel auf einen Blick"' in g and '"Haus auf einen Blick"' in g and "raeume" not in g
    gc = source(js, "glanceCard")
    assert '"nicht erreichbar"' in gc and '"über EMS-ESP"' in gc and 'on: "an", off: "aus", home: "zu Hause"' in gc
