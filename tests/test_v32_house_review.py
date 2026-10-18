"""Gegenprüfung 0.32 (Hausmodell, Zeitplan, Empfehlungen, Woche): Funde haus:1, 2, 4, 5, 6.

Nicht auf eine Anlage zugeschnitten: Zeitpläne mit Abendkomfort über dem Tag, eigene Blöcke je
Wochentag, Abtastraten 60 und 300 s, fehlende Stunden nach einem Neustart.
"""

from datetime import datetime, timedelta

import pytest

from heatpilot import advice, schedule, weekly
from heatpilot.config import Config
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

MON = datetime(2026, 11, 2, 0, 0)  # Montag


def _ts(day: int, hh: int, mm: int = 0, ss: int = 0) -> int:
    return int((MON + timedelta(days=day, hours=hh, minutes=mm, seconds=ss)).timestamp())


# --- haus:1 Nachtende am Tagesniveau, nicht am Abendkomfort -----------------------------------------

def _schedule_store(tmp_path, target, days: int = 14) -> Store:
    store = Store(tmp_path / "s.db")
    rows = []
    for k in range(days * 288):
        t = MON + timedelta(minutes=5 * k)
        rows += [(int(t.timestamp()), r, 20.0, target(t.hour * 60 + t.minute), 10.0, None)
                 for r in ("climate.a", "climate.b", "climate.c")]
    store.add_room_samples(rows)
    store.commit()
    return store


def _flat(m: int) -> float:
    """Tag 20 °C, Abendkomfort 22 °C 18:00–22:30, Nacht 17 °C bis 05:30 – morgens nur zurück auf 20."""
    if m < 5 * 60 + 30 or m >= 22 * 60 + 30:
        return 17.0
    return 22.0 if m >= 18 * 60 else 20.0


def _stepped(m: int) -> float:
    """Wie _flat, aber der Abend fällt um 21:00 auf 20,5 °C – erst 22:30 ist die Nacht."""
    if m < 5 * 60 + 30 or m >= 22 * 60 + 30:
        return 17.0
    if m >= 21 * 60:
        return 20.5
    return 22.0 if m >= 18 * 60 else 20.0


@pytest.mark.parametrize("target", [_flat, _stepped])
def test_evening_comfort_above_day_level_still_finds_the_night(tmp_path, target):
    learned = schedule.learn(_schedule_store(tmp_path, target), _ts(14, 0))
    assert learned["nights"] == 13
    assert learned["night_start"] == "22:30" and learned["night_end"] == "05:30"
    assert learned["night_temp"] == 17.0


def test_afternoon_off_keeps_the_evening_as_reference(tmp_path):
    """Tado nachmittags „aus“: das Tagesniveau (5 °C) ist kein Bezug – der Abend bleibt es."""
    def target(m):
        if m < 6 * 60 or m >= 22 * 60:
            return 17.0
        return schedule.OFF_TEMP if 12 * 60 <= m < 18 * 60 else 21.0
    learned = schedule.learn(_schedule_store(tmp_path, target), _ts(14, 0))
    assert learned["nights"] == 13 and learned["night_start"] == "22:00" and learned["night_end"] == "06:00"


# --- haus:2 Frühstart aus „aus“: Blockbeginn je Wochentag ---------------------------------------------

def _room_day(day: int, rise: tuple[int, int], warm_after_min: int, step: int = 300) -> list[tuple]:
    """Nachts „aus“, ab ``rise`` Soll 21; der Raum wird linear in ``warm_after_min`` warm."""
    rows, t_rise = [], _ts(day, *rise)
    for ts in range(_ts(day, 0, 0), _ts(day + 1, 0, 0), step):
        on = t_rise <= ts < _ts(day, 22, 0)
        if on:
            temp = min(17.0 + 4 * (ts - t_rise) / 60 / warm_after_min, 21.0)
            rows.append((ts, temp, 21.0, 80.0 if temp < 21 else 20.0))
        else:
            rows.append((ts, 17.0, advice.OFF_TEMP, 0.0))
    return rows


def _groups(events):
    out = {}
    for e in events:
        d = datetime.fromtimestamp(e.get("block_ts", e["ts"]))
        out.setdefault((d.weekday() < 5, d.strftime("%H:%M")), []).append(e)
    return out


def test_saturday_and_sunday_blocks_stay_apart():
    """Sa 07:30, So 09:00, kein Frühstart, je 40 min zu spät: zwei Blöcke, nicht „Sa–So 09:00 mit Frühstart“."""
    series = []
    for day in range(21):
        wd = (MON + timedelta(days=day)).weekday()
        rise = {5: (7, 30), 6: (9, 0)}.get(wd, (6, 0))
        series += _room_day(day, rise, 40 if wd >= 5 else 15)
    g = _groups(advice.schedule_events(series, {}, set()))
    assert set(g) == {(False, "07:30"), (False, "09:00"), (True, "06:00")}
    for key in ((False, "07:30"), (False, "09:00")):
        assert len(g[key]) == 3
        assert all(e["late_min"] == 40 and not e["early_open"] for e in g[key])


def test_friday_block_does_not_swallow_monday_to_thursday():
    """Mo–Do 06:00, Fr 07:00, jeweils 35 min zu spät: die Empfehlung gilt Mo–Fr 06:00, nicht „07:00, 0 min“."""
    series = []
    for day in range(21):
        wd = (MON + timedelta(days=day)).weekday()
        series += _room_day(day, {4: (7, 0), 5: (8, 0), 6: (8, 0)}.get(wd, (6, 0)), 35)
    g = _groups(advice.schedule_events(series, {}, set()))
    assert len(g[(True, "06:00")]) == 12 and len(g[(True, "07:00")]) == 3
    assert all(e["late_min"] == 35 and not e["early_open"] for e in g[(True, "06:00")])


def test_few_days_still_group_by_weekday_kind():
    """Nur eine Woche (je Wochentag ein Anstieg): weiter Werktage gemeinsam – der Frühstart bleibt erkennbar."""
    series = []
    for day in range(3):
        series += _room_day(day, (6, 30), 20)
    for day in (3, 4):
        series += _room_day(day, (5, 50), 45)  # kalt: Tado springt früher
    events = advice.schedule_events(series, {}, set())
    assert {datetime.fromtimestamp(e["block_ts"]).strftime("%H:%M") for e in events} == {"06:30"}
    assert all(e["early_open"] for e in events[3:])


# --- haus:5 Aussetzer und Heimkehr nach Zeit, nicht nach Messpunkten -------------------------------------

@pytest.mark.parametrize("step", [60, 300])
def test_short_dropout_is_no_block_at_any_sample_rate(step):
    rows = []
    for ts in range(_ts(0, 14, 0), _ts(0, 17, 0), step):
        off = _ts(0, 15, 0) <= ts < _ts(0, 15, 10)  # 10 min „aus“, danach dasselbe Soll
        rows.append((ts, 19.5, advice.OFF_TEMP if off else 21.0, 30.0))
    assert advice.schedule_events(rows, {}, set()) == []


@pytest.mark.parametrize("step", [60, 300])
def test_arrival_rise_within_15_min_counts_at_any_sample_rate(step):
    a = _ts(0, 17, 0)
    series = [(ts, 17.0, 21.0 if ts >= a + 10 * 60 else advice.OFF_TEMP, 0.0)
              for ts in range(_ts(0, 15, 0), _ts(0, 19, 0), step)]
    ev = advice.arrival_events(series, [a])
    assert len(ev) == 1 and ev[0]["deficit"] == 4.0
    late = [(ts, 17.0, 21.0 if ts >= a + 20 * 60 else advice.OFF_TEMP, 0.0)
            for ts in range(_ts(0, 15, 0), _ts(0, 19, 0), step)]
    assert advice.arrival_events(late, [a]) == []  # erst nach 20 min: keine Geofencing-Heimkehr


# --- haus:4 Woche: Brennerstarts erst ab dem ersten Brennerereignis ------------------------------------

def test_starts_ignore_hours_before_the_first_burner_event(tmp_path):
    """history_start = jetzt − 30 Tage, der HA-Recorder liefert aber nur 10 Tage Brennerdaten."""
    store = Store(tmp_path / "w.db")
    now = _ts(35, 0)
    start, first = now - 30 * 86400, now - 10 * 86400
    store.add_hourly([(ts, 5.0, 20.0, 1.0, 0.0, 1.0, 0.0, None, 0.0) for ts in range(start - 20 * 86400, now, 3600)])
    for ts in range(first, now, 3600):  # wirklich 2 Starts je Stunde
        for k in (0, 1):
            store.add_burner_event(ts + 600 + k * 1200, True)
            store.add_burner_event(ts + 900 + k * 1200, False)
    store.put("history_start", start)
    store.commit()
    grid = weekly.collect(store, now, 56)["metrics"]["starts"]["grid"]
    vals = [x for row in grid for x in row if x is not None]
    assert vals and all(x == 2 for x in vals)


# --- haus:6 Tageswert bleibt, wenn die Stunde 00–01 fehlt ------------------------------------------------

def _engine(tmp_path) -> Engine:
    tmp_path.mkdir(exist_ok=True)
    return Engine(Config(), Store(tmp_path / "e.db"), Discovered())


def _hours(days: int, skip: set[int] = frozenset()) -> list[tuple]:
    return [(t, 3.0, 20.0, 2.0, 0.0, 1.0, 0.0, None, 0.0)
            for t in range(_ts(0, 0), _ts(days, 0), 3600) if t not in skip]


def test_missing_midnight_hour_keeps_the_day(tmp_path):
    full = _engine(tmp_path / "a")
    full.store.add_hourly(_hours(7))
    full.store.commit()
    for r in _hours(7):
        full.learn_house_hour(r[0])
    gap = _engine(tmp_path / "b")
    rows = _hours(7, skip={_ts(3, 0)})  # Stunde 00–01 Uhr am 4. Tag fehlt (Neustart um Mitternacht)
    gap.store.add_hourly(rows)
    gap.store.commit()
    for r in rows:
        gap.learn_house_hour(r[0])  # wie replay(): nur vorhandene Stunden
    assert gap.house.daily.updates == full.house.daily.updates == 4
    assert [h[0] for h in gap.house.daily_hist] == [h[0] for h in full.house.daily_hist]


def test_aggregate_hour_without_samples_still_closes_the_day(tmp_path):
    """Live: um Mitternacht neu gestartet, die Stunde 00–01 hat keine Messwerte – aggregate_hour bricht ab."""
    eng = _engine(tmp_path)
    eng.store.add_hourly(_hours(3))
    eng.store.commit()
    for r in _hours(3):
        if r[0] < _ts(2, 23):
            eng.learn_house_hour(r[0])
    assert eng.house.daily.updates == 0 and len(eng.house.daily_hist) == 2  # 2 Tage als Zustand, 3. noch offen
    eng.aggregate_hour(_ts(3, 0))  # keine samples für 00–01 Uhr des 4. Tages
    assert len(eng.house.daily_hist) == 3 and eng.house.daily.updates == 1


def test_a_day_is_learned_only_once(tmp_path):
    eng = _engine(tmp_path)
    eng.store.add_hourly(_hours(4))
    eng.store.commit()
    for r in _hours(4):
        eng.learn_house_hour(r[0])
    n = eng.house.daily.updates
    eng.__dict__.pop("_house_day_open", None)  # Neustart: der Vortag wird noch einmal angestoßen
    eng.learn_house_hour(_ts(3, 23))
    assert eng.house.daily.updates == n
