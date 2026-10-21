"""0.48.6: Abwesenheit – die Gewohnheit „kommt heim“ erst bei einem echten Anstieg, Kessel-Soll in der Haltezeit
erneuern, Heimkehr-Kontrolle und Reisen ohne Doppel."""

from datetime import datetime, timedelta

from heatpilot import boiler_rule as br
from heatpilot.config import Config
from heatpilot.controlloop import Presence, desired
from heatpilot.presence import SLOTS_PER_DAY, PersonLearner, next_arrival, slot_of

from test_boiler41 import FLOW, T0, calls, house, shows, step
from test_control34 import climate, wz

N = 7 * SLOTS_PER_DAY
WED = datetime(2026, 10, 21).astimezone()  # Mittwoch


def _at(h, m=0, day=WED):
    return day.replace(hour=h, minute=m)


def _grid(p, day, blocks):
    """Raster eines Tages setzen: [(von_h, von_min, p, Besuche), …] bis zum nächsten Block bzw. Tagesende."""
    for i, (h, m, prob, visits) in enumerate(blocks):
        start = slot_of(day.replace(hour=h, minute=m))
        end = (slot_of(day.replace(hour=blocks[i + 1][0], minute=blocks[i + 1][1])) if i + 1 < len(blocks)
               else slot_of(day.replace(hour=0, minute=0)) + SLOTS_PER_DAY)
        for s in range(start, end):
            p.week[s % N], p.visits[s % N] = prob, visits


def _leaves(p, at, dist):
    """Person geht um ``at`` und steht dann ``dist`` km entfernt (wie die Heimkehr-Kontrolle sie live sieht)."""
    p.observe(at - timedelta(minutes=5), True, 0.0)
    p.observe(at, False, dist)
    return p


def _maria():
    """Wie im Bericht: mittwochs bis 06:30 meist zu Hause (0,56), 06:45–16:45 jeden zweiten Mittwoch (0,51), ab 17 Uhr
    sicher (0,73) – auch donnerstags."""
    p = PersonLearner("person.m", approach_speed_kmh=26.4)
    _grid(p, WED, [(0, 0, 0.56, 7), (6, 45, 0.5123305, 6), (17, 0, 0.7342795, 6)])
    _grid(p, WED + timedelta(days=1), [(0, 0, 0.7342795, 6)])
    return p


def _ingo():
    p = PersonLearner("person.i", approach_speed_kmh=53.0)
    _grid(p, WED, [(0, 0, 0.67, 9), (6, 15, 0.38, 9), (17, 0, 0.59, 9)])
    _grid(p, WED + timedelta(days=1), [(0, 0, 0.59, 9), (6, 15, 0.19, 9), (16, 0, 0.59, 9)])
    return p


# ------------------------------------------------------------------ Gewohnheit: Ankunft erst bei echtem Anstieg
def test_coin_flip_habit_does_not_roll_with_the_clock():
    """Fall aus dem Bericht: Maria geht 06:45, steht 14 km weg – die Ankunft lief 07:30 → 07:45 → 08:15 mit, das Haus
    heizte durchgehend vor. Jetzt: die nächste echte Heimkehr (17 Uhr), stabil über den Vormittag."""
    p = _leaves(_maria(), _at(6, 45), 14.3)
    for h, m in ((6, 45), (7, 0), (7, 30), (8, 15), (12, 0), (16, 0)):
        p.observe(_at(h, m), False, 14.3)
        e = p.eta(_at(h, m))
        assert e["source"] == "gewohnheit" and e["eta"] == _at(17), (h, m, e["eta"])
        assert e["confidence"] == 0.73


def test_regular_commute_keeps_its_arrival():
    """Wer zur gewohnten Zeit geht, kommt zur gewohnten Zeit heim – wie bisher."""
    p = _leaves(_ingo(), _at(6, 10), 45.0)
    p.observe(_at(9), False, 45.0)
    assert p.eta(_at(9))["eta"] == _at(17)
    q = _leaves(_maria(), _at(6, 45), 14.3)
    assert next_arrival([p, q], _at(9))["eta"] == _at(17)


def test_out_of_pattern_absence_has_no_habit_arrival():
    """Sonst den ganzen Tag zu Hause, heute um 10 Uhr weg: die Gewohnheit kennt keine Heimkehr – keine Ankunft
    „gleich nach der Fahrzeit“ (GPS übernimmt, sobald die Person heimfährt)."""
    p = PersonLearner("person.h")
    _grid(p, WED, [(0, 0, 0.76, 7)])
    _grid(p, WED + timedelta(days=1), [(0, 0, 0.76, 7)])
    _leaves(p, _at(10), 8.0)
    p.observe(_at(10, 30), False, 8.0)
    assert p.eta(_at(10, 30))["eta"] is None


def test_unlearned_valley_needs_more_than_half_as_before():
    """Ist seit dem Verlassen kein Feld verlässlich gelernt, gilt wie bisher das erste Feld über 50 %."""
    p = PersonLearner("person.n")
    _grid(p, WED, [(0, 0, 0.5, 0), (9, 0, 0.3, 4), (12, 0, 0.7, 4)])
    _leaves(p, _at(8), 2.0)
    assert p.next_home_by_habit(_at(8, 30), since=_at(8)) == (_at(12), 0.7)
    _grid(p, WED, [(0, 0, 0.5, 0), (9, 0, 0.55, 4)])
    p._backup = None
    assert p.next_home_by_habit(_at(8, 30), since=_at(8)) == (_at(9), 0.55)


def test_long_absence_looks_at_the_last_week_only():
    p = _ingo()
    p.observe(_at(6, 10) - timedelta(days=9), False, 45.0)  # seit 9 Tagen weg (regelmäßig, nicht „lange weg“)
    assert p._habit_low(_at(9), p._away_since) == 0.38


def test_late_arrival_rolls_for_an_hour_then_the_block_counts_no_more():
    p = _leaves(_ingo(), _at(6, 10), 45.0)
    p.observe(_at(17, 20), False, 45.0)  # erwartet 17 Uhr, noch nicht da: noch dieser Block
    assert p.eta(_at(17, 20))["eta"] >= _at(17, 30)
    assert p.eta(_at(17, 20))["eta"] < _at(19)
    p.observe(_at(18, 5), False, 45.0)  # 65 min vorbei: der Abend zählt nicht mehr – nächste Heimkehr Do 16:00
    assert p.eta(_at(18, 5))["eta"] == _at(16, 0, WED + timedelta(days=1))


def test_rooms_go_down_when_nobody_is_home_in_the_morning():
    """Die Steuerung: um 07:00 Abwesenheitstemperatur bis kurz vor 17 Uhr (Raum braucht laut Deckel 3 h)."""
    cfg = Config(control_mode="heatpilot", away_temp=16.0, preheat_max_h=3.0)
    m = _leaves(_maria(), _at(6, 45), 14.3)
    i = _leaves(_ingo(), _at(6, 10), 45.0)
    eta = next_arrival([m, i], _at(7))["eta"]
    d = desired(wz(), _at(7), cfg, Presence(home_count=0, arrival=eta))
    assert d.phase == "abwesend" and d.setpoint == 16.0
    assert d.until is not None and _at(13, 59) <= d.until <= _at(16, 45)
    assert desired(wz(), _at(16, 50), cfg, Presence(home_count=0, arrival=eta)).phase == "vorheizen"


def test_preview_timeline_uses_the_same_rule():
    """Die 24-h-Vorschau (``presence_plan``): nach der gewohnten Abfahrt die nächste echte Heimkehr."""
    from heatpilot.presence_plan import timeline

    p = _ingo()
    p.observe(_at(5, 55), True, 0.0)
    pts, _ = timeline(p, _at(6), _at(23), None, False, True)
    assert pts[1] == (_at(6, 15), False) and pts[2] == (_at(17), True)


# ------------------------------------------------------------------ Heimkehr-Kontrolle und Reisen ohne Doppel
def test_replayed_arrivals_and_trips_are_not_doubled():
    p = PersonLearner("person.d")
    t = _at(8)
    for _ in range(2):  # Neuberechnung spielt dieselbe Zeit noch einmal ab
        p.home = None
        p.observe(t, True, 0.0)
        p.observe(t + timedelta(minutes=5), False, 30.0)
        p.observe(t + timedelta(hours=30), True, 0.0)
    assert len(p.arrivals) == 1 and len(p.away_trips) == 1
    d = p.to_dict()
    d["arrivals"] = [*d["arrivals"], {**d["arrivals"][0], "checks": []}, {**d["arrivals"][0], "ts": _at(7).isoformat()}]
    d["away_trips"] = d["away_trips"] * 3
    q = PersonLearner.from_dict(d)
    assert [a["ts"] for a in q.arrivals] == [_at(7).isoformat(), d["arrivals"][0]["ts"]]  # zeitlich geordnet
    assert len(q.away_trips) == 1


# ------------------------------------------------------------------ Kessel: Soll in der Haltezeit erneuern
def test_held_setpoint_is_renewed_during_the_ten_minutes_before_lowering():
    T = 1_800_000_000
    d = br.decide_flow(39, 54.0, T, T - 61, down_since=T - 120, keepalive=True)
    assert d.write and d.value == 54 and "erneuert" in d.why and "10 min" in d.why
    assert not br.decide_flow(39, 54.0, T, T - 30, down_since=T - 120, keepalive=True).write  # noch keine Minute
    assert not br.decide_flow(39, 54.0, T, T - 61, down_since=T - 120).write                   # ohne Erneuern
    assert br.decide_flow(39, 54.0, T, T - 61, down_since=T - 600, keepalive=True).value == 39  # danach senken


def test_renewing_keeps_the_lowering_window(tmp_path):
    """Im Ablauf: das Erneuern startet das 10-min-Fenster nicht neu – nach 10 min sinkt das Soll."""
    eng, ha, bc = house(tmp_path, boiler_keepalive=True)
    t0 = T0
    assert step(bc, t0) == 1 and calls(ha)[-1][3] == {"value": 46}
    shows(eng, flow=46)
    step(bc, t0 + 60)
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="idle")  # warm: Ziel 41 °C
    written = []
    for k in range(1, 23):
        n = len(calls(ha))
        step(bc, t0 + 60 + 30 * k)
        written += [c[3]["value"] for c in calls(ha)[n:] if c[2] == FLOW]
        if written and written[-1] == 46:
            shows(eng, flow=46)
    assert 46 in written[:-1]           # in der Haltezeit erneuert
    assert written[-1] == 41            # und nach 10 min gesenkt
