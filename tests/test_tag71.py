"""0.71: Knopf „Tag“ – die Nacht endet sofort (samstags früh zur Arbeit), es gilt, was tagsüber gälte; und die Knöpfe
lehren nichts: Nächte mit einem Override zählen für die Nachterkennung nicht."""

from types import SimpleNamespace

from heatpilot import override, sleep

from test_override62 import ROOMS, T0, Store, loop, natural


def test_day_ends_the_night_with_comfort_or_away():
    lp = loop()
    override.set_mode(lp.store, "tag", T0)
    out = override.apply(lp, T0, natural("nacht"), SimpleNamespace(home_count=1), ROOMS)
    assert [(d.setpoint, d.phase, d.reason) for d in out.values()] == [(21.0, "komfort", "nur jetzt: Tag"),
                                                                        (20.0, "komfort", "nur jetzt: Tag")]
    out = override.apply(lp, T0 + 60, natural("nacht"), SimpleNamespace(home_count=0), ROOMS)
    assert out["climate.a"].setpoint == 17.0 and out["climate.a"].phase == "abwesend"  # niemand da: Abwesenheit
    assert override.apply(lp, T0 + 120, natural("komfort"), SimpleNamespace(home_count=1), ROOMS) == natural()
    assert override.get(lp.store) is None  # der Plan ist von selbst am Tag


def test_every_button_is_logged_with_start_and_end():
    st = Store()
    override.set_mode(st, "tag", T0)
    override.set_mode(st, "nacht", T0 + 600)  # anderer Knopf: der erste endet
    override.set_mode(st, "aus", T0 + 900)
    assert override.periods(st, T0 + 1000) == [(T0, T0 + 600), (T0 + 600, T0 + 900)]
    override.set_mode(st, "abwesend", T0 + 2000)
    assert override.periods(st, T0 + 2500)[-1] == (T0 + 2000, T0 + 2500)  # läuft noch


def test_a_night_with_a_button_is_not_learned(tmp_path):
    from heatpilot.store import Store as DB
    from test_sensor_bucket63 import NOW, fill

    db = fill(DB(tmp_path / "s.db"))
    clean = sleep.learn(db, NOW)
    key = max(k for k, n in clean.items() if n["valid"])
    db2 = fill(DB(tmp_path / "t.db"))
    a = sleep._at(sleep.date.fromisoformat(key), sleep.WINDOW[0])
    db2.put(override.LOG, [[a + 10 * 3600, a + 11 * 3600, "tag"]])  # morgens Tag gedrückt
    nights = sleep.learn(db2, NOW)
    assert not nights[key]["valid"] and nights[key]["reason"] == sleep.BUTTON
    assert sum(n["valid"] for n in nights.values()) == sum(n["valid"] for n in clean.values()) - 1
