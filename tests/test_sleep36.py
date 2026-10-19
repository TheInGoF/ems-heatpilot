"""0.36 Schlafenszeit aus dem Hausstromverbrauch: Nachterkennung, Wochenplan, Wirkung auf die Steuerung.

Synthetisches Haus im 60-s-Takt: Standby 180 W mit Kühlschrank-Zacken (+120 W, 15 min je 45 min),
Abend 450 W bis 21:30 Mo–Do, 23:00 Fr/Sa, 22:00 So; Aufstehen 05:00 Mo–Fr (Kaffeemaschine 1200 W
für 2 min, danach 300 W Dauer), 07:30 Sa/So.
"""

import asyncio
import time
from datetime import date, datetime, time as dtime, timedelta
from types import SimpleNamespace

import pytest

from heatpilot import backfill, engine as engine_mod, schedule, sleep
from heatpilot.config import Config
from heatpilot.controlloop import desired
from heatpilot.discovery import Discovered, Room, discover
from heatpilot.engine import Engine
from heatpilot.store import Store

from test_control34 import HOME, at, wz

START = datetime(2026, 8, 30, 18, 0)                     # Sonntagabend: erste Nacht ist vollständig
NOW = int(datetime(2026, 10, 12, 13, 0).timestamp())     # Montagmittag, 6 Wochen später: Nacht So→Mo ist fertig
STANDBY, FRIDGE, EVENING, DAY, COFFEE = 180.0, 120.0, 450.0, 480.0, 1200.0
BED = {0: 21 * 60 + 30, 1: 21 * 60 + 30, 2: 21 * 60 + 30, 3: 21 * 60 + 30, 4: 23 * 60, 5: 23 * 60, 6: 22 * 60}
WAKE = {0: 5 * 60, 1: 5 * 60, 2: 5 * 60, 3: 5 * 60, 4: 5 * 60, 5: 7 * 60 + 30, 6: 7 * 60 + 30}
EXPECT = {"Mo": ("21:30", "05:00"), "Di": ("21:30", "05:00"), "Mi": ("21:30", "05:00"), "Do": ("21:30", "05:00"),
          "Fr": ("23:00", "05:00"), "Sa": ("23:00", "07:30"), "So": ("22:00", "07:30")}


def mins(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def house_w(d: datetime, bed=BED, wake=WAKE, evening=EVENING) -> float:
    """Hausleistung in W zur Ortszeit ``d``; ``bed`` je Abend-Wochentag (über 1440 = nach Mitternacht)."""
    m = d.hour * 60 + d.minute
    wd = d.weekday()
    p = STANDBY + (FRIDGE if m % 45 < 15 else 0.0)
    if m < 12 * 60:  # Morgen: Nacht des Vorabends
        asleep = m + 1440 >= bed[(wd - 1) % 7] and m < wake[wd]
    else:
        asleep = m >= bed[wd]
    if asleep:
        return p
    if wake[wd] <= m < wake[wd] + 2:
        return p + COFFEE + 300.0
    return p + ((evening - STANDBY) if m >= 18 * 60 else (DAY - STANDBY))


def fill(store, start=START, days=43, step=60, power=house_w, extra=None):
    """Messwerte wie ``engine.tick`` sie schreibt; ``extra(d)`` überschreibt Felder je Messpunkt."""
    t0 = int(start.timestamp())
    for ts in range(t0, t0 + days * 86400, step):
        d = datetime.fromtimestamp(ts)
        row = {"power_w": power(d), "power_ex_w": None, "vacation": 0, "away_long": 0, "home_count": None}
        if extra:
            row.update(extra(d) or {})
        store.add_sample(ts, row)
    store.commit()
    return store


def in_window(d: datetime, evening: date) -> bool:
    return (d.date() == evening and d.hour >= 18) or (d.date() == evening + timedelta(days=1) and d.hour < 12)


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# ------------------------------------------------------------------ Nächte und Wochenplan
def test_plan_hits_bed_and_wake_times_with_weekend_semantics(tmp_path):
    store = fill(Store(tmp_path / "s.db"))
    nights = sleep.learn(store, NOW)
    assert len(nights) == 43 and all(n["valid"] for n in nights.values())
    fr = nights["2026-10-09"]  # Nacht Fr→Sa: Beginn zählt für Fr, Ende für Sa
    assert fr["start_min"] == 23 * 60 and fr["end_min"] == 7 * 60 + 30 and fr["reason"] == "ok"
    assert fr["base_w"] == 180.0 and 440 <= fr["eve_w"] <= 460 and 300 < fr["thr_w"] < 440  # über dem Kühlschrank
    plan = sleep.plan(nights, NOW)
    assert [d["day"] for d in plan] == ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    for d in plan:
        s, e = EXPECT[d["day"]]
        assert abs(mins(d["start"]) - mins(s)) <= 10 and abs(mins(d["end"]) - mins(e)) <= 10, d
        assert d["reliable_start"] and d["reliable_end"] and d["n_start"] == 6 and d["n_end"] == 6
        assert d["spread_start_min"] <= 10 and d["spread_end_min"] <= 10
    s = sleep.summary(nights, NOW, Config(night_plan_auto=True), ["sensor.l1", "sensor.l2"], ["sensor.wm"])
    assert s["active"] and s["nights"] == 42 and s["invalid"] == {} and s["sensors"] == ["sensor.l1", "sensor.l2"]
    assert s["exclude"] == ["sensor.wm"] and s["weeks"] == 6
    assert sleep.summary(nights, NOW, Config(), ["sensor.l1"], [])["active"] is False  # ohne Schalter nur Vorschlag
    assert sleep.learned_plan(plan)[5] == {"start": "23:00", "end": "07:30"}


def test_learn_is_incremental_and_idempotent(tmp_path, monkeypatch):
    store = fill(Store(tmp_path / "s.db"), days=10)
    mid = int(datetime(2026, 9, 5, 13, 0).timestamp())
    first = sleep.learn(store, mid)
    assert set(first) == {f"2026-08-3{d}" for d in (0, 1)} | {f"2026-09-0{d}" for d in range(1, 5)}
    assert store.get("sleep.nights") == first
    assert sleep.learn(store, mid + 1800) == first  # offene Nacht So 05.09. (Fenster bis 12:00 am 06.09.) noch nicht
    done = dict(first)
    monkeypatch.setattr(sleep, "learn_night", lambda rows, day: (_ for _ in ()).throw(AssertionError(day)))
    assert sleep.learn(store, mid + 3600) == done  # nichts doppelt
    monkeypatch.undo()
    later = sleep.learn(store, int(datetime(2026, 9, 10, 13, 0).timestamp()))
    assert len(later) == 10 and later["2026-09-05"]["valid"]
    # älter als 56 Tage fällt heraus
    assert sleep.learn(store, int(datetime(2026, 11, 5, 13, 0).timestamp())) == {}


def test_washing_machine_excluded_or_outvoted(tmp_path):
    wm_dates = {date(2026, 9, 8), date(2026, 9, 22)}  # zwei Dienstage, 2 kW von 22:00 bis 00:00

    def with_wm(exclude):
        def extra(d):
            on = d.date() in wm_dates and d.hour >= 22
            return {"power_w": house_w(d) + (2000.0 if on else 0.0),
                    "power_ex_w": (2000.0 if on else 0.0) if exclude else None}
        return extra

    nights = sleep.learn(fill(Store(tmp_path / "a.db"), extra=with_wm(True)), NOW)
    for day in wm_dates:  # mit Ausschluss-Sensor exakt
        n = nights[day.isoformat()]
        assert n["valid"] and n["start_min"] == 21 * 60 + 30 and n["end_min"] == 5 * 60
    nights = sleep.learn(fill(Store(tmp_path / "b.db"), extra=with_wm(False)), NOW)
    di = sleep.plan(nights, NOW)[1]  # ohne Ausschluss entscheidet der Median der 6 Wochen
    assert di["start"] == "21:30" and di["reliable_start"] and di["n_start"] == 6
    assert sleep.plan(nights, NOW)[2]["end"] == "05:00"


def test_nights_away_or_without_data_are_skipped(tmp_path):
    vac, away, nobody, somebody, gap = (date(2026, 9, d) for d in (2, 9, 16, 17, 23))

    def extra(d):
        m = d.hour * 60 + d.minute
        if in_window(d, vac):
            return {"vacation": 1}
        if in_window(d, away):
            return {"away_long": 1}
        if in_window(d, nobody):  # Personen da, um 22:00 niemand zu Hause
            return {"home_count": 0 if 21 * 60 + 50 <= m <= 22 * 60 + 10 else 1}
        if in_window(d, somebody):  # um 22:00 jemand da – tagsüber weg stört nicht
            return {"home_count": 1 if 21 * 60 + 50 <= m <= 22 * 60 + 10 else 0}
        if in_window(d, gap) and m >= 18 * 60 and m < 23 * 60 + 30:  # 5,5 h ohne Hausleistung = 30 % der Bins
            return {"power_w": None}
        return {}

    nights = sleep.learn(fill(Store(tmp_path / "s.db"), days=28, extra=extra), int(datetime(2026, 9, 28, 13).timestamp()))
    for day in (vac, away, nobody):
        assert nights[day.isoformat()] == {**nights[day.isoformat()], "valid": False, "reason": "nicht zu Hause"}
    assert nights[somebody.isoformat()]["valid"] and nights[somebody.isoformat()]["start_min"] == 21 * 60 + 30
    assert nights[gap.isoformat()]["reason"] == "keine Daten" and nights[gap.isoformat()]["start_min"] is None
    assert nights["2026-09-10"]["valid"]  # home_count unbekannt (None) schließt nicht aus
    s = sleep.summary(nights, int(datetime(2026, 9, 28, 13).timestamp()), Config(night_plan_auto=True), ["sensor.l1"], [])
    assert s["invalid"] == {"nicht zu Hause": 3, "keine Daten": 1} and s["nights"] == 24 and s["active"]
    # Abend wie Nacht (kein Abendverbrauch): zu wenig Unterschied
    quiet = fill(Store(tmp_path / "q.db"), days=4, power=lambda d: house_w(d, evening=STANDBY))
    nights = sleep.learn(quiet, int(datetime(2026, 9, 3, 13).timestamp()))
    assert nights and {n["reason"] for n in nights.values()} == {"zu wenig Unterschied"}
    assert all(n["eve_w"] - n["base_w"] < 60 for n in nights.values())
    # Nacht ohne Aufstehen bis 11:00 (Wochenende lang geschlafen): nur der Beginn zählt
    late = {k: 12 * 60 for k in WAKE}
    nights = sleep.learn(fill(Store(tmp_path / "l.db"), days=4, power=lambda d: house_w(d, wake=late)),
                         int(datetime(2026, 9, 3, 13).timestamp()))
    n = nights["2026-09-01"]
    assert n["valid"] and n["start_min"] == 21 * 60 + 30 and n["end_min"] is None


def test_late_bedtime_found_but_not_plausible(tmp_path):
    bed, wake = {**BED, 1: 26 * 60}, {**WAKE, 2: 7 * 60}  # Di erst um 02:00 ins Bett, Mi um 07:00 raus
    nights = sleep.learn(fill(Store(tmp_path / "s.db"), power=lambda d: house_w(d, bed=bed, wake=wake)), NOW)
    n = nights["2026-09-08"]
    assert n["valid"] and n["start_min"] == 26 * 60 and n["end_min"] == 7 * 60
    di = sleep.plan(nights, NOW)[1]
    assert di["start"] == "02:00" and di["n_start"] == 6 and not di["reliable_start"]  # 19:00–01:30 ist plausibel
    mi = sleep.plan(nights, NOW)[2]
    assert mi["end"] == "07:00" and mi["reliable_end"] and mi["start"] == "21:30"
    assert sleep.learned_plan(sleep.plan(nights, NOW))[1] == {"start": None, "end": "05:00"}


def test_plan_reliability_limits():
    def nights_of(weekday: int, starts=None, ends=None) -> dict:
        d0 = date(2026, 9, 7) + timedelta(days=weekday)  # Mo 07.09. + Wochentag
        out = {}
        for i, v in enumerate(starts if starts is not None else ends):
            out[(d0 + timedelta(weeks=i)).isoformat()] = {
                "start_min": v if starts is not None else 21 * 60 + 30,
                "end_min": v if ends is not None else None, "valid": True, "reason": "ok"}
        return out

    p = sleep.plan(nights_of(0, starts=[27 * 60] * 4), NOW)[0]  # Beginn 03:00: außerhalb 19:00–01:30
    assert p["start"] == "03:00" and p["n_start"] == 4 and p["spread_start_min"] == 0 and not p["reliable_start"]
    assert sleep.plan(nights_of(0, starts=[25 * 60 + 30] * 3), NOW)[0]["reliable_start"]        # 01:30 gerade noch
    assert not sleep.plan(nights_of(0, starts=[25 * 60 + 40] * 3), NOW)[0]["reliable_start"]
    assert not sleep.plan(nights_of(0, starts=[18 * 60 + 50] * 3), NOW)[0]["reliable_start"]
    # Ende: Nacht So→Mo zählt für Mo; 03:30–09:30 plausibel
    assert sleep.plan(nights_of(6, ends=[4 * 60 + 45] * 3), NOW)[0] == {
        "day": "Mo", "start": None, "end": "04:45", "n_start": 0, "n_end": 3, "spread_start_min": None,
        "spread_end_min": 0, "reliable_start": False, "reliable_end": True}
    assert not sleep.plan(nights_of(6, ends=[3 * 60] * 3), NOW)[0]["reliable_end"]
    assert not sleep.plan(nights_of(6, ends=[10 * 60] * 3), NOW)[0]["reliable_end"]
    # Spannweite über 90 min oder unter 3 Nächten: nicht belastbar; Rundung auf 5 min
    p = sleep.plan(nights_of(2, starts=[21 * 60, 23 * 60 + 30, 21 * 60, 23 * 60 + 30]), NOW)[2]
    assert p["spread_start_min"] > 90 and not p["reliable_start"] and p["n_start"] == 4
    p = sleep.plan(nights_of(2, starts=[21 * 60 + 33, 21 * 60 + 33]), NOW)[2]
    assert p["start"] == "21:35" and p["n_start"] == 2 and not p["reliable_start"]
    assert sleep.plan(nights_of(2, starts=[21 * 60 + 33] * 3), NOW)[2]["reliable_start"]
    # ungültige Nächte und ältere als 6 Wochen zählen nicht
    old = {k: v for k, v in nights_of(3, starts=[22 * 60] * 3).items()}
    old["2026-08-20"] = {"start_min": 20 * 60, "end_min": None, "valid": True, "reason": "ok"}
    old["2026-09-17"]["valid"] = False
    assert sleep.plan(old, NOW)[3]["n_start"] == 2
    assert sleep.plan({}, NOW)[0] == {"day": "Mo", "start": None, "end": None, "n_start": 0, "n_end": 0,
                                      "spread_start_min": None, "spread_end_min": None,
                                      "reliable_start": False, "reliable_end": False}


def test_night_across_dst_change(tmp_path, berlin):
    store = fill(Store(tmp_path / "d.db"), start=datetime(2026, 10, 22, 18, 0), days=5)  # Do bis Di
    nights = sleep.learn(store, int(datetime(2026, 10, 27, 13, 0).timestamp()))
    sa = nights["2026-10-24"]  # Sa→So: um 03:00 wird es 02:00 – 25-h-Nacht nach Wanduhr
    assert sa["valid"] and sa["start_min"] == 23 * 60 and sa["end_min"] == 7 * 60 + 30
    so = nights["2026-10-25"]
    assert so["valid"] and so["start_min"] == 22 * 60 and so["end_min"] == 5 * 60
    assert all(n["valid"] for n in nights.values()) and len(nights) == 5


# ------------------------------------------------------------------ Messwerte
def test_power_sum_units_and_missing_phase(tmp_path):
    found = Discovered(power=["sensor.l1", "sensor.l2", "sensor.l3"], power_exclude=["sensor.wm", "sensor.wallbox"],
                       power_units={"sensor.l3": "W", "sensor.wallbox": "kW"})
    eng = Engine(Config(), Store(tmp_path / "p.db"), found)

    def st(v, unit=None):
        return {"state": v, "attributes": {"unit_of_measurement": unit} if unit else {}}

    eng.states.update({"sensor.l1": st("0.25", "kW"), "sensor.l2": st("300", "W"), "sensor.l3": st("150"),
                       "sensor.wm": st("unavailable", "W"), "sensor.wallbox": st("1.5")})
    sample = eng.snapshot()[0]
    assert sample["power_w"] == 700.0 and sample["power_ex_w"] == 1500.0  # kW ×1000, Einheit notfalls aus der Erkennung
    eng.states["sensor.l2"] = st("unavailable", "W")
    assert eng.snapshot()[0]["power_w"] is None  # Phasensumme ohne eine Phase wäre falsch
    assert eng.snapshot()[0]["power_ex_w"] == 1500.0  # fehlender Ausschluss-Sensor zählt 0
    del eng.states["sensor.l2"]
    assert eng.snapshot()[0]["power_w"] is None
    eng.states["sensor.l2"] = st("-5", "kW")  # Einspeisung: das ist kein Hausverbrauch
    assert eng.snapshot()[0]["power_w"] is None and "power_w" in eng._rejected
    eng.states["sensor.l2"] = st("300", "W")
    for e in ("sensor.wm", "sensor.wallbox"):
        del eng.states[e]
    assert eng.snapshot()[0]["power_ex_w"] == 0.0
    none = Engine(Config(), Store(tmp_path / "q.db"), Discovered()).snapshot()[0]
    assert none["power_w"] is None and none["power_ex_w"] is None


def test_discovery_takes_power_sensors_from_settings_only():
    states = [{"entity_id": "sensor.l1", "state": "100", "attributes": {"unit_of_measurement": "W", "device_class": "power"}},
              {"entity_id": "sensor.l2", "state": "0.2", "attributes": {"unit_of_measurement": "kW"}},
              {"entity_id": "sensor.wm", "state": "0", "attributes": {"unit_of_measurement": "W"}}]
    cfg = Config(power_sensors=["sensor.l1", "sensor.l2", "sensor.fehlt"], power_exclude=["sensor.wm", "sensor.l1"])
    found = discover(states, [], cfg)
    assert found.power == ["sensor.l1", "sensor.l2"] and found.power_exclude == ["sensor.wm"]
    assert found.power_units == {"sensor.l1": "W", "sensor.l2": "kW", "sensor.wm": "W"}
    assert {"sensor.l1", "sensor.l2", "sensor.wm"} <= set(found.all_sensor_ids())
    d = found.as_dict(private=False)
    assert d["power"] == ["sensor.l1", "sensor.l2"] and d["power_exclude"] == ["sensor.wm"]
    assert discover(states, [], Config()).power == []  # keine Automatik


def test_tick_stores_power_and_runs_sleep_hourly(tmp_path):
    found = Discovered(power=["sensor.l1"], power_units={"sensor.l1": "W"})
    eng = Engine(Config(power_sensors=["sensor.l1"]), Store(tmp_path / "t.db"), found)
    eng.states["sensor.l1"] = {"state": "321", "attributes": {"unit_of_measurement": "W"}}
    ts = int(datetime(2026, 10, 12, 20, 0).timestamp())
    eng.tick(ts)
    row = eng.store.query("SELECT power_w, power_ex_w FROM samples")[0]
    assert row["power_w"] == 321.0 and row["power_ex_w"] is None
    assert eng._sleep_ts == ts and eng.sleep_summary()["nights"] == 0
    assert len(eng.summary()["sleep"]["plan"]) == 7


def test_backfill_reads_power_history_with_discovered_unit(tmp_path, monkeypatch):
    calls = []

    class HA:
        async def history(self, ids, s, e, attributes=False):
            calls.append((set(ids), attributes))
            t0 = int(s.timestamp())
            if "sensor.l1" not in ids:
                return {}
            return {"sensor.l1": [{"s": "0.2", "lu": t0}], "sensor.l2": [{"s": "300", "lu": t0}],
                    "sensor.wm": [{"s": "unavailable", "lu": t0}]}

    end_ts = int(datetime(2026, 10, 16, 12, 0).timestamp())
    monkeypatch.setattr(backfill, "time", SimpleNamespace(time=lambda: end_ts))
    found = Discovered(power=["sensor.l1", "sensor.l2"], power_exclude=["sensor.wm"], power_units={"sensor.l1": "kW"})
    eng = Engine(Config(), Store(tmp_path / "b.db"), found)
    asyncio.run(backfill.backfill_history(HA(), eng, 1))
    plain = [ids for ids, attrs in calls if not attrs]
    assert plain and all({"sensor.l1", "sensor.l2", "sensor.wm"} <= ids for ids in plain)
    rows = eng.store.query("SELECT power_w, power_ex_w FROM samples")
    assert rows and {(r["power_w"], r["power_ex_w"]) for r in rows} == {(500.0, 0.0)}  # kW laut Erkennung, ohne Attribute


def test_apply_settings_reloads_history_when_power_sensors_change(tmp_path, monkeypatch):
    from heatpilot import main as main_mod

    app = main_mod.App.__new__(main_mod.App)
    app.cfg, app.store = Config(), Store(tmp_path / "a.db")
    current = [["sensor.l1"], []]
    app.eng = SimpleNamespace(found=Discovered(power=current[0], power_exclude=current[1]))
    reloads = []

    async def build(backfill=False):
        app.eng = SimpleNamespace(found=Discovered(power=list(current[0]), power_exclude=list(current[1])))

    async def reload_history():
        reloads.append(True)

    app.build, app.reload_history = build, reload_history
    monkeypatch.setattr(main_mod, "save_settings", lambda clean: None)
    monkeypatch.setattr(main_mod, "set_debug", lambda flag: None)

    async def go():
        assert await app.apply_settings({"night_plan_auto": True}) == []
        await asyncio.sleep(0)
        assert reloads == []  # Sensoren gleich: kein Neueinlesen
        app.store.add_sample(1_000, {"power_w": 300.0, "power_ex_w": 10.0})
        current[0] = ["sensor.l1", "sensor.l2"]
        assert await app.apply_settings({"power_sensors": current[0]}) == []
        await asyncio.sleep(0)
        assert reloads == [True]
        # Werte der alten Sensoren sind verworfen – sie zählen nicht als Nächte
        row = app.store.query("SELECT power_w, power_ex_w FROM samples WHERE ts = 1000")[0]
        assert row["power_w"] is None and row["power_ex_w"] is None
        current[1] = ["sensor.wm"]
        assert await app.apply_settings({"power_exclude": current[1]}) == []
        await asyncio.sleep(0)
        assert reloads == [True, True]
        assert await app.apply_settings({"power_sensors": ["kaputt"]})  # Fehler: nichts passiert
        await asyncio.sleep(0)
        assert reloads == [True, True]

    asyncio.run(go())


# ------------------------------------------------------------------ Wirkung auf die Steuerung
def test_learned_times_replace_plan_only_with_auto_and_where_reliable():
    cfg = Config(control_mode="heatpilot")
    cfg.night_plan_learned = ([{"start": "23:00", "end": None}] + [{"start": None, "end": None}] * 5
                              + [{"start": "22:30", "end": "07:00"}])
    assert cfg.night_times(0) == (dtime(21, 0), dtime(4, 30))  # Schalter aus → eingestellt
    cfg.night_plan_auto = True
    assert cfg.night_times(0) == (dtime(23, 0), dtime(4, 30))  # Beginn gelernt, Ende nicht belastbar → eingestellt
    assert cfg.night_times(1) == (dtime(21, 0), dtime(4, 30))
    assert cfg.night_times(6) == (dtime(22, 30), dtime(7, 0))
    assert cfg.night_entry(0) == {"start": "23:00", "end": "04:30", "source_start": "gelernt (Strom)",
                                  "source_end": "eingestellt"}
    # Steuerung: Mo 22:00 ist mit gelerntem Beginn 23:00 noch Komfort, 23:30 Nacht bis Di 04:30 (1 h Vorheizen)
    mon = (2026, 10, 12)
    assert desired(wz(), at(mon, 22), cfg, HOME).phase == "komfort"
    d = desired(wz(), at(mon, 23, 30), cfg, HOME)
    assert d.phase == "nacht" and d.until == at((2026, 10, 13), 3, 30)
    assert desired(wz(), at((2026, 10, 18), 22, 45), cfg, HOME).phase == "nacht"   # So: gelernt 22:30
    assert desired(wz(), at((2026, 10, 19), 4, 45), cfg, HOME).phase == "komfort"    # Mo-Ende bleibt eingestellt 04:30
    cfg.night_plan_auto = False
    assert desired(wz(), at(mon, 22), cfg, HOME).phase == "nacht"
    # effective(): Herkunft je Hälfte, Struktur sonst wie bisher
    cfg.night_plan_auto = True
    eff = schedule.effective(cfg, None)
    plan = eff.as_dict()["plan"]
    assert plan[0] == {"day": "Mo", "start": "23:00", "end": "04:30", "source_start": "gelernt (Strom)",
                       "source_end": "eingestellt"}
    assert plan[1] == {"day": "Di", "start": "21:00", "end": "04:30", "source_start": "eingestellt",
                       "source_end": "eingestellt"}
    assert plan[6]["source_end"] == "gelernt (Strom)" and eff.source["night_start"] == "wochenplan"
    assert schedule.effective(Config(night_plan_auto=True), None).plan is None  # Beobachten: wie bisher
    cfg.night_plan_learned = [{"start": "23:00"}]  # unvollständig: eingestellt
    assert cfg.night_times(0) == (dtime(21, 0), dtime(4, 30))
    cfg.night_plan_learned = None
    assert cfg.night_entry(0)["source_start"] == "eingestellt"


def test_engine_learns_plan_sets_config_and_survives_rebuild(tmp_path, monkeypatch):
    store = fill(Store(tmp_path / "e.db"), days=28)
    now = int(datetime(2026, 9, 28, 13, 0).timestamp())
    # Uhr für die ganze Engine anhalten: time.time selbst ersetzen – die Teile der Engine (engine_*.py) binden
    # time jeweils selbst, ein Ersatz von heatpilot.engine.time erreichte nur den Kern (wie test_dhw_v32)
    monkeypatch.setattr(engine_mod.time, "time", lambda: now)
    cfg = Config(control_mode="heatpilot", power_sensors=["sensor.l1"], night_plan_auto=True)
    found = Discovered(rooms=[Room("Wz", "climate.wz")], power=["sensor.l1"])
    eng = Engine(cfg, store, found)
    assert eng.sleep_summary()["active"] is False and cfg.night_plan_learned == [{"start": None, "end": None}] * 7
    assert eng.eff.as_dict()["plan"][0]["source_start"] == "eingestellt"
    eng.update_sleep(now)
    nights = store.get("sleep.nights")
    assert len(nights) == 28 and all(n["valid"] for n in nights.values())
    assert cfg.night_plan_learned[0] == {"start": "21:30", "end": "05:00"}
    assert cfg.night_plan_learned[5] == {"start": "23:00", "end": "07:30"}
    s = eng.sleep_summary()
    assert s["active"] and s["sensors"] == ["sensor.l1"] and s["plan"][4]["start"] == "23:00" and s["nights"] == 28
    assert eng.eff.as_dict()["plan"][0] == {"day": "Mo", "start": "21:30", "end": "05:00",
                                            "source_start": "gelernt (Strom)", "source_end": "gelernt (Strom)"}
    assert cfg.is_night_at(datetime(2026, 9, 28, 21, 45).astimezone())
    assert not cfg.is_night_at(datetime(2026, 9, 28, 21, 15).astimezone())
    assert eng.summary()["sleep"] == s
    # stündlich: eine Minute später nichts; nach einer Stunde wird keine Nacht doppelt gerechnet
    monkeypatch.setattr(sleep, "learn_night", lambda rows, day: pytest.fail(f"Nacht {day} doppelt gerechnet"))
    eng.update_sleep(now + 60)
    eng.update_sleep(now + 3600)
    assert store.get("sleep.nights") == nights and cfg.night_plan_learned[0]["start"] == "21:30"
    # neue Engine (Einstellungen gespeichert) kennt den Plan sofort aus der Datenbank – vor dem ersten Takt
    cfg2 = Config(control_mode="heatpilot", power_sensors=["sensor.l1"], night_plan_auto=True)
    eng2 = Engine(cfg2, store, found)
    assert cfg2.night_plan_learned[0] == {"start": "21:30", "end": "05:00"} and eng2.sleep_summary()["nights"] == 28
    assert eng2.eff.as_dict()["plan"][6]["source_start"] == "gelernt (Strom)"
    # Beobachten ohne Schalter: gelernt und angezeigt, aber ohne Wirkung
    cfg3 = Config(power_sensors=["sensor.l1"])
    eng3 = Engine(cfg3, store, found)
    assert eng3.sleep_summary()["active"] is False and eng3.sleep_summary()["plan"][0]["start"] == "21:30"
    assert cfg3.night_times(0) == (dtime(21, 0), dtime(4, 30)) and eng3.eff.plan is None
    # Sensoren abgewählt: nichts gelernt, nichts aktiv
    eng4 = Engine(Config(night_plan_auto=True), store, Discovered(rooms=[Room("Wz", "climate.wz")]))
    assert eng4.sleep_summary() == {"active": False, "auto": True, "sensors": [], "exclude": [], "nights": 0,
                                    "invalid": {}, "weeks": 6, "plan": sleep.plan({}, now)}
    assert eng4.cfg.night_plan_learned is None
    eng4.update_sleep(now)  # nichts zu tun
    # Lernstand verwerfen löscht die Nächte mit
    store.reset_learning("2026-01-01")
    assert store.get("sleep.nights") is None
