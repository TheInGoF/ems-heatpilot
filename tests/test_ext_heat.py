"""0.33: Fremdwärme-Ereignis je Raum (Kamin, Ofen, Gäste) – erkannt, nicht gelernt, im Haus ausgelassen.

Nicht auf eine Anlage zugeschnitten: Abtastraten 60 und 300 s, junges und gelerntes Modell,
Sonne, Neustart mitten im Ereignis, Neuberechnung (Replay), Export ohne Koordinaten.
"""

import json
import math
from datetime import datetime, timedelta
from types import SimpleNamespace

from heatpilot import backfill, diagnostics, weekly
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import EXT_DAY_SKIP_H, Engine
from heatpilot.models import RoomModel
from heatpilot.store import Store

MON = datetime(2026, 11, 2, 0, 0)  # Montag, Heizperiode
WZ, K = "climate.wz", "climate.k"


def _ts(day: int, hh: int, mm: int = 0) -> int:
    return int((MON + timedelta(days=day, hours=hh, minutes=mm)).timestamp())


def make_engine(tmp_path, interval: int = 300, name: str = "e") -> Engine:
    found = Discovered(rooms=[Room("Wohnzimmer", WZ), Room("Küche", K)])
    return Engine(Config(sample_interval_s=interval), Store(tmp_path / f"{name}.db"), found)


def learned_room(name: str = "Wohnzimmer", a: float = 0.03, b: float = 1 / 60, c: float = 0.1,
                 sun=(0.0, 0.0, 0.0), updates: int = 300) -> RoomModel:
    """Raummodell mit bekannten Parametern und kleiner Unsicherheit – wie nach Wochen Lernen."""
    m = RoomModel(name)
    m.rls.theta = [a, b, c, *sun, 0.0]  # 0.54: + Wind
    m.rls.P = [[1e-4 if i == j else 0.0 for j in range(7)] for i in range(7)]
    m.rls.updates, m.rls.err_sq_ewma = updates, 0.01
    return m


def feed(eng: Engine, t0: int, hours: float, temp, valve=lambda h: 0.0, *, flow: float = 45.0,
         pump: int = 1, t_out: float = 5.0, sun=(0.0, 0.0, 0.0), step: int = 300, room: str = WZ,
         target: float | None = 21.0) -> None:
    """Messwerte wie tick in den Store schreiben: Verlauf als Funktion der Stunden seit ``t0``.

    ``sun=None``: keine Sonnenwerte (kein Standort in zone.home).
    """
    sun_e, sun_s, sun_w = sun or (None, None, None)
    for ts in range(t0, t0 + int(hours * 3600) + 1, step):
        h = (ts - t0) / 3600
        eng.store.add_sample(ts, {"t_out": t_out, "flow": flow, "pump_mod": 99.0 if pump else 0.0,
                                  "heating": pump, "burner_on": pump, "dhw": 0, "vacation": 0, "away_long": 0,
                                  "sun_e": sun_e, "sun_s": sun_s, "sun_w": sun_w})
        eng.store.add_room_samples([(ts, room, round(temp(h), 2), target, valve(h), None)])
    eng.store.commit()


def steps(eng: Engine, t0: int, hours: float):
    for st in range(t0, t0 + int(hours * 3600), 1800):
        eng.learn_rooms(st, st + 1800)
        yield st + 1800


def fire_evening(h: float) -> float:
    """2 h ruhig bei 21 °C, dann Kamin: +3 K in 2 h, 1 h halten, in 4 h zurück auf 21 °C."""
    if h <= 2:
        return 21.0
    if h <= 4:
        return 21.0 + 1.5 * (h - 2)
    if h <= 5:
        return 24.0
    return max(24.0 - 0.75 * (h - 5), 21.0)


# --- (a) Kaminabend mit gelerntem Modell ----------------------------------------------------------

def test_fire_evening_detected_not_learned_and_ends_after_cooling(tmp_path):
    eng = make_engine(tmp_path)
    eng.rooms[WZ] = learned_room()
    t0 = _ts(0, 17)
    feed(eng, t0, 10, fire_evening)
    before = None
    for end in steps(eng, t0, 10):
        if end == t0 + 2 * 3600:
            before = list(eng.rooms[WZ].rls.theta)
        if end == t0 + int(2.5 * 3600):
            assert WZ in eng.room_ext and eng.room_ext[WZ]["start_ts"] == t0 + 2 * 3600  # erster Kaminschritt
        if end == t0 + 5 * 3600:
            assert eng.room_ext[WZ]["rise"] == 3.0  # Spitze erreicht, Ereignis läuft
            assert eng.rooms[WZ].rls.theta == before  # währenddessen nichts gelernt
    assert WZ not in eng.room_ext  # nach dem Abkühlen beendet
    ev = eng.store.query("SELECT * FROM room_events")
    assert len(ev) == 1
    assert ev[0]["room"] == WZ and ev[0]["start_ts"] == t0 + 2 * 3600 and ev[0]["rise_k"] == 3.0
    assert ev[0]["end_ts"] == t0 + 9 * 3600  # Schritt, in dem der Raum wieder bis auf 0,3 K am Start war
    assert eng.rooms[WZ].rls.updates > 300  # ruhige Schritte davor und danach werden gelernt


def test_window_drop_is_no_event(tmp_path):
    eng = make_engine(tmp_path)
    eng.rooms[WZ] = learned_room()
    t0 = _ts(0, 20)
    feed(eng, t0, 2, lambda h: 21.0 if h < 1 else 21.0 - 1.8 * min(h - 1, 1))
    for _ in steps(eng, t0, 2):
        pass
    assert not eng.room_ext and not eng.store.query("SELECT * FROM room_events")


# --- (b) normales Aufheizen, junges Modell --------------------------------------------------------

def test_heating_with_open_valve_and_young_model_learns(tmp_path):
    eng = make_engine(tmp_path)
    t0 = _ts(0, 6)
    feed(eng, t0, 2, lambda h: 19.0 if h < 1 else 19.0 + 2.0 * (h - 1), lambda h: 0.0 if h < 1 else 100.0)
    for _ in steps(eng, t0, 2):
        pass
    assert not eng.room_ext and not eng.store.query("SELECT * FROM room_events")
    assert eng.rooms[WZ].rls.updates == 4 and eng.rooms[WZ].excitation > 0


def test_radiator_just_closed_is_no_event(tmp_path):
    """Thermostat am Heizkörper: Ventil war eben 50 % offen, schließt, danach steigt der Raum noch 1 K."""
    eng = make_engine(tmp_path)
    eng.rooms[WZ] = learned_room()
    t0 = _ts(0, 3)
    feed(eng, t0, 2, lambda h: 20.0 if h < 1 else 20.0 + 2.0 * min(h - 1, 0.5),
         lambda h: 50.0 if 0.5 <= h < 1 else 0.0)
    for _ in steps(eng, t0, 2):
        pass
    assert not eng.room_ext and not eng.store.query("SELECT * FROM room_events")


# --- (c)/(d) Sonne --------------------------------------------------------------------------------

def test_sunny_rise_with_young_model_is_learned_not_flagged(tmp_path):
    eng = make_engine(tmp_path)
    t0 = _ts(0, 12)
    feed(eng, t0, 2, lambda h: 21.0 if h < 1 else 21.0 + 1.2 * (h - 1), sun=(0.0, 0.8, 0.0))
    for _ in steps(eng, t0, 2):
        pass
    assert not eng.room_ext and not eng.store.query("SELECT * FROM room_events")
    assert eng.rooms[WZ].rls.updates == 4 and eng.rooms[WZ].solar[1] > 0


def test_learned_sun_explains_rise_unexplained_rest_is_event(tmp_path):
    rate_sun = 2.0  # K/h bei voller Sonne, gelernt
    for rise_kh, expect in ((1.5, False), (2.6, True)):
        eng = make_engine(tmp_path, name=f"s{rise_kh}")
        eng.rooms[WZ] = learned_room(sun=(0.0, rate_sun, 0.0))
        t0 = _ts(0, 12)
        feed(eng, t0, 2, lambda h, r=rise_kh: 21.0 if h < 1 else 21.0 + r * (h - 1), sun=(0.0, 0.8, 0.0))
        for _ in steps(eng, t0, 2):
            pass
        assert (WZ in eng.room_ext) is expect, rise_kh


def test_sun_not_learned_yet_blocks_even_with_many_steps(tmp_path):
    """200 Schritte ohne Sonne: Sonnenparameter noch unsicher – der erste sonnige Nachmittag bleibt lernbar."""
    m = learned_room()
    m.rls.P[4][4] = 1.0  # Süd nie angeregt
    assert m.inputs_learned((0.0, 0.8, 0.0)) is False
    assert m.inputs_learned((0.0, 0.0, 0.0)) is True


# --- (e) Hausmodell -------------------------------------------------------------------------------

def _hourly(ts: int, ext: float = 0.0, gas: float = 1.0) -> tuple:
    return (ts, 5.0, 20.0, gas, 0.0, None, 0.0, None, 0.0, ext)


def test_house_hour_with_event_not_learned(tmp_path):
    for ext, expect in ((0.5, 0), (0.0, 1)):
        eng = make_engine(tmp_path, name=f"h{ext}")
        h = _ts(1, 12)
        eng.store.add_hourly([_hourly(h - k * 3600) for k in range(1, 26)] + [_hourly(h, ext), _hourly(h + 3600)])
        eng.store.commit()
        eng.learn_house_hour(h + 3600)
        assert eng.house.rls.updates == expect, ext


def test_house_day_skipped_at_four_event_hours_learned_at_one(tmp_path):
    eng = make_engine(tmp_path)
    rows = []
    for d in range(10):
        for hh in range(24):
            ext = 1.0 if (d == 8 and 18 <= hh < 22) or (d == 9 and hh == 19) else 0.0
            rows.append(_hourly(_ts(d, hh), ext))
    eng.store.add_hourly(rows)
    eng.store.commit()
    assert 4 >= EXT_DAY_SKIP_H > 1
    for d in range(8):
        eng.learn_house_day(_ts(d, 12))
    assert eng.house.daily.updates == 6  # ab Tag 2 (Vortag und Woche davor liegen vor)
    eng.learn_house_day(_ts(8, 12))
    assert eng.house.daily.updates == 6  # Kaminabend: nicht gelernt …
    assert eng.house.daily_hist[-1][0] == (MON + timedelta(days=8)).date().isoformat()  # … zählt aber als Vortag
    eng.learn_house_day(_ts(9, 12))
    assert eng.house.daily.updates == 7


def test_hourly_ext_frac_from_closed_and_running_events(tmp_path):
    eng = make_engine(tmp_path)
    h = _ts(0, 20)
    eng.store.add_room_event(h - 1800, h + 1800, WZ, 1.5)  # halbe Stunde hinein
    eng.store.add_room_event(h, h + 1200, "climate.fremd", 1.0)  # nicht gewählter Raum zählt nicht
    assert eng.ext_frac(h) == 0.5
    eng.room_ext[K] = {"start_ts": h + 1800, "t_start": 20.0, "peak": 21.0, "rise": 1.0}
    assert eng.ext_frac(h) == 1.0  # Vereinigung: 0:00–0:30 abgeschlossen, 0:30–1:00 laufend
    feed(eng, h, 1, lambda _h: 21.0, room=K)
    eng.aggregate_hour(h, learn=False)
    assert eng.store.query("SELECT ext_frac FROM hourly WHERE ts = ?", (h,))[0]["ext_frac"] == 1.0


# --- (f) Vorheizplan -----------------------------------------------------------------------------

def test_preheat_plan_skips_room_with_running_event(tmp_path):
    eng = make_engine(tmp_path)
    eng.room_ext[WZ] = {"start_ts": _ts(0, 19), "t_start": 21.0, "peak": 23.5, "rise": 2.5}
    rooms = [(WZ, 23.5, 21.0, 0.0, None), (K, 19.0, 21.0, 0.0, None)]
    checks = {WZ: {"ok": True}, K: {"ok": True}}
    inputs, skipped = eng.plan_rooms(rooms, checks)
    assert [m.room for m, _, _ in inputs] == ["Küche"] and skipped == ["Wohnzimmer"]
    ext = eng.ext_summary(_ts(0, 20))
    assert ext[WZ]["ext_event"]["rise_k"] == 2.5 and ext[WZ]["ext_events_7d"] == 1 and ext[K]["ext_event"] is None


# --- (g) Neustart und Neuberechnung ---------------------------------------------------------------

def test_restart_mid_event_continues_from_kv(tmp_path):
    eng = make_engine(tmp_path)
    eng.rooms[WZ] = learned_room()
    t0 = _ts(0, 17)
    feed(eng, t0, 10, fire_evening)
    for _ in steps(eng, t0, 4):  # bis mitten in den Kaminabend
        pass
    assert WZ in eng.room_ext
    eng._last_room_step = t0 + 4 * 3600
    eng.save()
    assert eng.store.get("model.room_ext")[WZ]["start_ts"] == t0 + 2 * 3600

    eng2 = Engine(eng.cfg, eng.store, eng.found)
    assert eng2.room_ext[WZ]["start_ts"] == t0 + 2 * 3600
    for _ in steps(eng2, t0 + 4 * 3600, 6):
        pass
    ev = eng2.store.query("SELECT * FROM room_events")
    assert len(ev) == 1 and ev[0]["start_ts"] == t0 + 2 * 3600 and ev[0]["rise_k"] == 3.0
    assert not eng2.room_ext
    eng.store.reset_learning("2000-01-01")
    assert not eng.store.query("SELECT * FROM room_events") and eng.store.get("model.room_ext") is None


def test_replay_detects_event_and_marks_hours(tmp_path):
    eng = make_engine(tmp_path)
    t0 = _ts(0, 0)
    feed(eng, t0, 17, lambda h: 21.0)  # ruhiger Tag
    feed(eng, _ts(0, 17), 7, fire_evening)
    eng.store.put("history_start", t0)
    eng._last_hour = eng._last_room_step = _ts(1, 0)
    backfill.replay(eng)
    assert eng.room_ext[WZ]["start_ts"] == _ts(0, 19) and eng.room_ext[WZ]["rise"] == 3.0  # läuft um Mitternacht noch
    assert not eng.store.query("SELECT * FROM room_events")
    frac = {r["ts"]: r["ext_frac"] for r in eng.store.query("SELECT ts, ext_frac FROM hourly")}
    assert frac[_ts(0, 18)] == 0.0 and frac[_ts(0, 19)] == 1.0 and frac[_ts(0, 23)] == 1.0
    assert eng.house.rls.updates == 0  # Hausmodell: Stunden mit Ereignis nicht gelernt (die übrigen haben kein Gas)


# --- (h) Export -----------------------------------------------------------------------------------

def test_export_contains_room_events_without_coordinates(tmp_path):
    eng = make_engine(tmp_path)
    eng.found.home_zone = (53.55, 9.99)
    eng.store.add_room_event(_ts(0, 19), _ts(0, 23), WZ, 2.8)
    eng.store.commit()
    data = diagnostics.export(SimpleNamespace(eng=eng, cfg=Config()), days=30)
    text = json.dumps(data, default=str)
    assert "53.55" not in text and "9.99" not in text and "latitude" not in text
    t = data["fremdwaerme"]
    assert t["spalten"] == ["start_ts", "end_ts", "room", "rise_k"] and t["zeilen"] == [[_ts(0, 19), _ts(0, 23), WZ, 2.8]]
    assert "fremdwaerme" in data["hinweis"]


def test_week_shows_ext_metric(tmp_path):
    store = Store(tmp_path / "w.db")
    store.add_hourly([_hourly(_ts(0, 20), 1.0), _hourly(_ts(7, 20), 0.5), _hourly(_ts(0, 21), 0.0), _hourly(_ts(7, 21), 0.0)])
    store.commit()
    w = weekly.collect(store, _ts(8, 0), 56)
    assert w["metrics"]["ext"]["grid"][0][20] == 75 and w["metrics"]["ext"]["grid"][0][21] == 0
    assert w["metrics"]["ext"]["findings"][0].startswith("Am häufigsten: Mo 20 Uhr (75 %)")


# --- (i) Abtastrate -------------------------------------------------------------------------------

def test_same_event_at_60s_and_300s_sampling(tmp_path):
    results = []
    for step in (60, 300):
        eng = make_engine(tmp_path, interval=step, name=f"r{step}")
        eng.rooms[WZ] = learned_room()
        t0 = _ts(0, 17)
        feed(eng, t0, 10, fire_evening, step=step)
        for _ in steps(eng, t0, 10):
            pass
        results.append([tuple(r) for r in eng.store.query("SELECT start_ts, end_ts, room, rise_k FROM room_events")])
    assert results[0] == results[1] and len(results[0]) == 1


# --- (j) Stoßlüften mit Erholung, Sonne unbekannt ------------------------------------------------

def airing(h: float, open_at: float) -> float:
    """21 °C; ab ``open_at`` 10 min Fenster auf → 18,5 °C, dann Erholung aus Wänden/Möbeln (τ 35 min) auf 20,9 °C."""
    if h < open_at:
        return 21.0
    if h < open_at + 10 / 60:
        return 21.0 - 2.5 * (h - open_at) / (10 / 60)
    return 20.9 - 2.4 * math.exp(-(h - open_at - 10 / 60) / (35 / 60))


def test_recovery_after_airing_with_closed_valve_is_no_event(tmp_path):
    """Nachtabsenkung (Ventil zu, Pumpe aus): 22:10 gelüftet, danach +2 K/h aus den Wänden – keine Fremdwärme.

    Lüften mitten im Schritt und genau an der Schrittgrenze; Raum und Haus lernen die Nacht durch.
    """
    for name, open_at in (("mid", 2 + 10 / 60), ("edge", 2.0)):
        eng = make_engine(tmp_path, name=name)
        eng.rooms[WZ] = learned_room()
        t0 = _ts(0, 20)
        feed(eng, t0, 14, lambda h, o=open_at: airing(h, o), pump=0, target=17.0)
        for hour in range(t0, t0 + 14 * 3600, 3600):
            for st in range(hour, hour + 3600, 1800):
                eng.learn_rooms(st, st + 1800)
            eng.aggregate_hour(hour, learn=False)
        assert not eng.room_ext and not eng.store.query("SELECT * FROM room_events"), name
        assert eng.store.query("SELECT COALESCE(SUM(ext_frac), 0) AS h FROM hourly")[0]["h"] == 0, name
        assert eng.rooms[WZ].rls.updates >= 300 + 26, name  # nur die beiden Lüftungsschritte (|ΔT| > 2 K) fehlen


def test_fire_after_airing_starts_above_old_level(tmp_path):
    """20:00 gelüftet, ab 22:00 Kamin (+2 K/h): Ereignis erst über 21,4 °C, Startwert ist das alte Niveau 21,0."""
    eng = make_engine(tmp_path)
    eng.rooms[WZ] = learned_room()
    t0 = _ts(0, 19)
    feed(eng, t0, 4, lambda h: airing(h, 1.0) + 2.0 * max(h - 3, 0), pump=0, target=17.0)
    for _ in steps(eng, t0, 4):
        pass
    ev = eng.room_ext[WZ]
    assert ev["start_ts"] == t0 + 3 * 3600 and ev["t_start"] == 21.0 and ev["rise"] == round(ev["peak"] - 21.0, 2)


def test_sun_unknown_daytime_rise_is_learned_evening_rise_is_event(tmp_path):
    """Kein Standort, also nie Sonnenwerte: tagsüber kann jeder Anstieg Sonne sein – lernen statt erkennen."""
    for hh, expect in ((12, False), (21, True)):
        eng = make_engine(tmp_path, name=f"n{hh}")  # junges Modell
        t0 = _ts(0, hh)
        feed(eng, t0, 2, lambda h: 21.0 if h < 1 else 21.0 + 1.2 * (h - 1), pump=0, sun=None)
        for _ in steps(eng, t0, 2):
            pass
        assert (WZ in eng.room_ext) is expect, hh
        assert eng.rooms[WZ].rls.updates == (2 if expect else 4), hh


# --- (k) Raum ohne Ventilwert: Heizkörperwärme nicht von Fremdwärme zu trennen – nicht erkennen, lernen

def test_room_without_valve_value_never_starts_an_event(tmp_path):
    eng = make_engine(tmp_path)
    eng.rooms[WZ] = RoomModel("Wohnzimmer")  # junges Modell
    t0 = _ts(0, 19)
    feed(eng, t0, 8, fire_evening, valve=lambda h: None, pump=0)
    for _ in steps(eng, t0, 8):
        pass
    assert WZ not in eng.room_ext and not eng.store.query("SELECT * FROM room_events")
    assert eng.rooms[WZ].rls.updates >= 10
