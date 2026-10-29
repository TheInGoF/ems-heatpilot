"""0.69: Zur Unzeit ist Näherkommen oft nur eine Fahrt zum nächsten Ziel (27.10. 10:15, von Baustelle zu Baustelle) –
liegt die GPS-Ankunft über ``UNEXPECTED_H`` vor der gewohnten Heimkehr, zählt erst eine bestätigte Heimfahrt."""

from datetime import datetime, timedelta

from heatpilot.presence import SLOTS_PER_DAY, PersonLearner


def _learner():
    """Zu Hause bis 7 Uhr und ab 16 Uhr, gut gelernt; um 7:20 los, 20 km weg."""
    p = PersonLearner("person.a")
    for i in range(7 * SLOTS_PER_DAY):
        h = i % SLOTS_PER_DAY // 4
        p.week[i], p.visits[i] = (0.95 if h < 7 or h >= 16 else 0.05), 10
    t0 = datetime(2026, 10, 27, 7, 0).astimezone()
    p.observe(t0, True, 0.0, learn=False)
    p.observe(t0 + timedelta(minutes=20), False, 20.0, learn=False)
    return p


def _drive(p, h, minutes, start_km=25.0, kmh=40.0):
    t = datetime(2026, 10, 27, h, 0).astimezone()
    for i in range(minutes + 1):
        p.observe(t + timedelta(minutes=i), False, start_km - i * kmh / 60, learn=False)
    return p.eta(t + timedelta(minutes=minutes))


def test_short_drive_towards_home_at_an_odd_time_is_no_return():
    e = _drive(_learner(), 10, 15)
    assert e["source"] == "gewohnheit" and e["eta"].hour == 16


def test_same_drive_near_the_usual_time_counts_at_once():
    e = _drive(_learner(), 15, 15)
    assert e["source"] == "gps" and e["eta"] < datetime(2026, 10, 27, 15, 45).astimezone()


def test_confirmed_return_counts_at_any_time():
    e = _drive(_learner(), 10, 35, start_km=35.0)  # 35 min am Stück näher, 23 km geschafft
    assert e["source"] == "gps" and e["eta"].hour == 10
