"""0.63: Sensor-Eimer für die Nachtzeiten (Konzeptpapier, Abschnitt 6) – Lampen, Bewegungsmelder & Co. als
Aktivitätssignal, auch ohne Leistungssensor; ein kurzes Licht nachts ist eine Unterbrechung, kein Aufwachen; die
Sicherheitsangabe („sehr sicher“) je Wochentag."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from heatpilot import sleep
from heatpilot.config import Config
from heatpilot.engine_sample import ACTIVE_STATES
from heatpilot.store import Store

from test_sleep36 import BED, NOW, START, WAKE, berlin, house_w, mins  # noqa: F401


def lamps(d: datetime, toilet=True) -> dict:
    """Lampen an, solange jemand wach ist (wie house_w); nachts um 2 Uhr kurz das Licht im Bad."""
    m, wd = d.hour * 60 + d.minute, d.weekday()
    if m < 12 * 60:
        asleep = m + 1440 >= BED[(wd - 1) % 7] and m < WAKE[wd]
    else:
        asleep = m >= BED[wd]
    n = 0 if asleep else 2 if m >= 17 * 60 or m < 9 * 60 else 0  # tagsüber Licht aus
    if toilet and 2 * 60 <= m < 2 * 60 + 5:
        n = 1  # 5 min Licht im Bad
    return n


def fill(store, power=True, toilet=True, days=43):
    t0 = int(START.timestamp())
    for ts in range(t0, t0 + days * 86400, 60):
        d = datetime.fromtimestamp(ts)
        store.add_sample(ts, {"power_w": house_w(d) if power else None, "power_ex_w": None, "vacation": 0,
                              "away_long": 0, "home_count": None, "activity_n": lamps(d, toilet)})
    store.commit()
    return store


def test_bucket_alone_learns_bed_and_wake_toilet_does_not_wake(tmp_path):
    nights = sleep.learn(fill(Store(tmp_path / "s.db"), power=False), NOW)
    ok = [n for n in nights.values() if n["valid"]]
    assert len(ok) >= 40 and all(n["src"] == "eimer" for n in ok)
    plan = sleep.plan(nights, NOW)
    mo, sa = plan[0], plan[5]
    assert abs(mins(mo["start"]) - (21 * 60 + 30)) <= 10 and abs(mins(mo["end"]) - 5 * 60) <= 10
    assert abs(mins(sa["end"]) - (7 * 60 + 30)) <= 10  # Wochenende später – nicht das Licht um 2 Uhr
    assert mo["sure_end"] == "sehr sicher" and mo["reliable_end"]


def test_power_and_bucket_together_and_summary(tmp_path):
    nights = sleep.learn(fill(Store(tmp_path / "s.db")), NOW)
    assert all(n["src"] == "beide" for n in nights.values() if n["valid"])
    s = sleep.summary(nights, NOW, Config(night_plan_auto=True), [], [], ["light.flur"])
    assert s["active"] and s["activity"] == ["light.flur"] and s["nights"] >= 40
    assert sleep.summary(nights, NOW, Config(), [], [])["nights"] == 0  # ohne Sensoren: aus


def test_no_evening_activity_is_no_contrast(tmp_path):
    store = Store(tmp_path / "s.db")
    t0 = int(START.timestamp())
    for ts in range(t0, t0 + 3 * 86400, 60):
        store.add_sample(ts, {"activity_n": 0, "vacation": 0, "away_long": 0, "home_count": None})
    store.commit()
    nights = sleep.learn(store, t0 + 3 * 86400)
    assert nights and all(n["reason"] == sleep.LOW_CONTRAST for n in nights.values())


def test_confidence_and_activity_count():
    assert sleep.confidence(6, 20, True) == "sehr sicher" and sleep.confidence(3, 60, True) == "sicher"
    assert sleep.confidence(2, 200, False) == "unsicher" and sleep.confidence(0, None, False) is None
    from heatpilot.engine_sample import SampleMixin
    fake = SimpleNamespace(found=SimpleNamespace(activity=["light.a", "binary_sensor.b", "light.c"]),
                           states={"light.a": {"state": "on"}, "binary_sensor.b": {"state": "off"},
                                   "light.c": {"state": "unavailable"}})
    assert SampleMixin.activity_count(fake) == 1 and "on" in ACTIVE_STATES
    fake.found.activity = []
    assert SampleMixin.activity_count(fake) is None


def test_lamps_and_media_players_are_offered():
    """0.65.1: Die Auswahl der Einstellungen (GET api/entities) listet Lampen und Mediaplayer – sonst ließ sich der
    Sensor-Eimer nur mit Schaltern und Meldern füllen."""
    from heatpilot import entity_map, web
    assert set(entity_map.KINDS["activity"][0]) <= set(web.PICK_DOMAINS)
