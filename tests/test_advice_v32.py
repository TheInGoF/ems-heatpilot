"""0.32: Frühstart aus „aus“, kurze Aussetzer, Geofencing-Heimkehr bei Tado „aus“."""

from datetime import datetime, timedelta

from heatpilot import advice

MON = datetime(2026, 10, 5, 0, 0)  # Montag
STEP = 300


def _ts(day: int, hh: int, mm: int) -> int:
    return int((MON + timedelta(days=day, hours=hh, minutes=mm)).timestamp())


def _day(day: int, rise: tuple[int, int], warm: tuple[int, int]) -> list[tuple]:
    """Nachts „aus“ (5 °C), ab ``rise`` Soll 21, ab ``warm`` ist der Raum warm."""
    rows = []
    t_rise, t_warm = _ts(day, *rise), _ts(day, *warm)
    for ts in range(_ts(day, 4, 0), _ts(day, 9, 0), STEP):
        target = 21.0 if ts >= t_rise else advice.OFF_TEMP
        temp = 21.0 if ts >= t_warm else 17.0
        rows.append((ts, temp, target, 100.0 if ts >= t_rise else 0.0))
    return rows


def test_early_start_from_off_is_measured_at_the_block():
    """Tado springt an kalten Tagen früher von „aus“ auf das Soll – das ist Frühstart, nicht der Block."""
    series = []
    for day in range(3):                       # mild: Soll um 06:30, warm 06:50
        series += _day(day, (6, 30), (6, 50))
    for day in (3, 4):                         # kalt: Tado startet um 05:50, warm 06:35
        series += _day(day, (5, 50), (6, 35))
    events = advice.schedule_events(series, {}, set())
    assert len(events) == 5 and all(e["from_off"] for e in events)
    block = {datetime.fromtimestamp(e["block_ts"]).strftime("%H:%M") for e in events}
    assert block == {"06:30"}
    mild, cold = events[:3], events[3:]
    assert all(e["late_min"] == 20 and not e["early_open"] for e in mild)
    assert all(e["late_min"] == 5 and e["early_open"] for e in cold)


def test_short_off_dropout_is_no_new_block():
    rows = []
    for ts in range(_ts(0, 7, 0), _ts(0, 14, 0), STEP):
        off = _ts(0, 12, 0) <= ts < _ts(0, 12, 10) or _ts(0, 13, 0) <= ts < _ts(0, 13, 30)
        rows.append((ts, 19.5, advice.OFF_TEMP if off else 21.0, 0.0))
    events = advice.schedule_events(rows, {}, set())
    # 10 min „aus“ ist ein Aussetzer, 30 min sind ein echter Block
    assert [datetime.fromtimestamp(e["ts"]).strftime("%H:%M") for e in events] == ["13:30"]


def test_arrival_while_tado_off_needs_a_rise():
    a = _ts(0, 17, 0)
    base = [(ts, 18.0, advice.OFF_TEMP, 0.0) for ts in range(_ts(0, 15, 0), _ts(0, 18, 0), STEP)]
    assert advice.arrival_events(base, [a]) == []  # bleibt aus: keine Geofencing-Heimkehr
    risen = [(ts, temp, 21.0 if ts >= a + 600 else tgt, v) for ts, temp, tgt, v in base]
    ev = advice.arrival_events(risen, [a])
    assert len(ev) == 1 and ev[0]["deficit"] == 3.0
