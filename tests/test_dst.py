"""Zeitumstellung (letzter Oktobersonntag): Tagesgrenzen, Warmwasser-Profil und Gewohnheiten nach Wanduhr."""

import time
from datetime import datetime, timedelta

import pytest

from heatpilot.dhw import DhwModel, slot_of
from heatpilot.engine import local, midnight
from heatpilot.presence import SLOT_MIN, SLOTS_PER_DAY, PersonLearner


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_midnight_on_25_hour_day(berlin):
    sunday = local(datetime(2026, 10, 25, 20, 0).timestamp())  # nach der Umstellung, +01:00
    start, end = midnight(sunday), midnight(sunday + timedelta(days=1))
    assert end - start == 25 * 3600
    assert datetime.fromtimestamp(start).hour == 0
    # fester Versatz des Abends + replace(hour=0) läge eine Stunde daneben
    assert int(sunday.replace(hour=0).timestamp()) != start


def test_dhw_slot_after_crossing_the_change(berlin):
    sat = local(datetime(2026, 10, 24, 20, 0).timestamp())   # Sa 20:00 Sommerzeit
    sun_18 = sat + timedelta(hours=23)                       # absolut 23 h später = So 18:00 Winterzeit
    assert slot_of(sun_18) == 6 * 24 + 18
    m = DhwModel()
    m.daily[18], m.daily[19] = 5.0, 0.0
    assert m.slot_draw(sun_18) == 5.0


def test_habit_arrival_keeps_wall_clock(berlin):
    p = PersonLearner("person.a")
    p.week = [0.0] * (7 * SLOTS_PER_DAY)
    p.week[6 * SLOTS_PER_DAY + 17 * 60 // SLOT_MIN] = 0.9    # sonntags 17:00 zu Hause
    sat = local(datetime(2026, 10, 24, 20, 0).timestamp())
    eta, conf = p.next_home_by_habit(sat)
    assert (eta.hour, eta.minute, eta.weekday()) == (17, 0, 6) and conf == 0.9
