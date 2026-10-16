"""Personen wählbar: Dummies ohne Gerät nicht automatisch, Anwesenheit nach Auswahl neu zählen."""

from heatpilot.config import Config
from heatpilot.discovery import discover
from heatpilot.store import Store


def states():
    return [
        {"entity_id": "person.ingo", "state": "home", "attributes": {"device_trackers": ["device_tracker.phone"]}},
        {"entity_id": "person.maria", "state": "not_home", "attributes": {"device_trackers": ["device_tracker.p2"]}},
        {"entity_id": "person.dummy", "state": "home", "attributes": {"device_trackers": []}},
        {"entity_id": "person.auto", "state": "unknown", "attributes": {}},
    ]


def test_dummies_are_not_chosen_automatically():
    assert discover(states(), [], Config()).persons == ["person.ingo", "person.maria"]


def test_chosen_persons_win_and_unknown_ids_are_dropped():
    cfg = Config(persons=["person.maria", "person.weg"])
    assert discover(states(), [], cfg).persons == ["person.maria"]


def test_recount_home_after_selection_change(tmp_path):
    store = Store(tmp_path / "p.db")
    for i in range(12):
        ts = 3600 + i * 300
        # Dummy ist immer "zu Hause", die echte Person ist weg
        store.add_person_samples([(ts, "person.dummy", 1, None), (ts, "person.maria", 0, 12.0)])
        store.add_sample(ts, {"home_count": 1})
    store.add_hourly([(3600, 5.0, 20.0, 1.0, 0.0, 1.0)])
    store.commit()
    store.recount_home(["person.maria"])
    assert {r["home_count"] for r in store.query("SELECT home_count FROM samples")} == {0}
    assert store.query("SELECT home_frac FROM hourly")[0]["home_frac"] == 0.0
    assert set(store.home_by_ts(0, ["person.maria"]).values()) == {False}
    assert set(store.home_by_ts(0, None).values()) == {True}
