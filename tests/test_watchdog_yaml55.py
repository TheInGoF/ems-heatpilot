"""0.55.1: Der Wächter kommt als fertiges YAML zum Kopieren – mit den gewählten Entitäten, gültig und so, dass
HeatPilot ihn am Auslöser wiedererkennt."""

import yaml

from heatpilot import watchdog as w

FLOW, HEAT = "number.boiler_selflowtemp", "switch.boiler_heatingactivated"


def test_yaml_is_valid_and_uses_the_chosen_entities():
    d = yaml.safe_load(w.guide(FLOW, HEAT, 55, ["notify.mobile_app_pixel", "notify.familie"])["yaml"])
    assert d["alias"] == w.NAME and d["triggers"] == [{"trigger": "state", "entity_id": w.HEARTBEAT, "for": "01:00:00"}]
    assert d["conditions"][0] == {"condition": "state", "entity_id": w.HEARTBEAT, "attribute": "kessel", "state": "heatpilot"}
    acts = d["actions"]
    assert acts[0] == {"action": "number.set_value", "target": {"entity_id": FLOW}, "data": {"value": 55}}
    assert acts[1] == {"action": "switch.turn_on", "target": {"entity_id": HEAT}}
    assert acts[2]["action"] == "notify.mobile_app_pixel"  # Handy-App: Dienst direkt
    assert acts[3]["action"] == "notify.send_message" and acts[3]["target"] == {"entity_id": "notify.familie"}


def test_without_notify_target_a_message_in_home_assistant():
    d = yaml.safe_load(w.guide(FLOW, HEAT, 50)["yaml"])
    assert d["actions"][-1]["action"] == "persistent_notification.create"


def test_no_yaml_without_flow_entity_or_fallback():
    assert w.guide(None, HEAT, 55)["yaml"] is None
    assert "yaml" not in w.guide(FLOW, HEAT, None)  # Rückfall unter 35 °C: kein Wächter, nur der Hinweis


def test_heatpilot_recognises_its_own_yaml():
    config = yaml.safe_load(w.guide(FLOW, HEAT, 55)["yaml"])
    assert w.HEARTBEAT in str(w._triggers(config))
