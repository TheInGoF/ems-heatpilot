"""0.55.1: Der Wächter kommt als fertiges YAML zum Kopieren – gültig und so, dass HeatPilot ihn am Auslöser
wiedererkennt. Seit 0.59 meldet er nur noch (kein Rückfall-Vorlauf, keine Schreib-Aktionen)."""

import yaml

from heatpilot import watchdog as w


def test_yaml_is_valid_and_only_notifies():
    d = yaml.safe_load(w.guide(["notify.mobile_app_pixel", "notify.familie"])["yaml"])
    assert d["alias"] == w.NAME and d["triggers"] == [{"trigger": "state", "entity_id": w.HEARTBEAT, "for": "01:00:00"}]
    assert d["conditions"][0] == {"condition": "state", "entity_id": w.HEARTBEAT, "attribute": "kessel", "state": "heatpilot"}
    acts = d["actions"]
    assert acts[0]["action"] == "notify.mobile_app_pixel" and acts[0]["data"]["message"] == w.TEXT  # Handy-App direkt
    assert acts[1]["action"] == "notify.send_message" and acts[1]["target"] == {"entity_id": "notify.familie"}
    assert len(acts) == 2 and "Drehschalter" in w.TEXT


def test_without_notify_target_a_message_in_home_assistant():
    d = yaml.safe_load(w.guide()["yaml"])
    assert [a["action"] for a in d["actions"]] == ["persistent_notification.create"]


def test_heatpilot_recognises_its_own_yaml():
    config = yaml.safe_load(w.guide()["yaml"])
    assert w.HEARTBEAT in str(w._triggers(config))
