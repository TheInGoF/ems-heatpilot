"""Funde aus der Prüfung von 0.37 (Vorschau und Trockenlauf-Protokoll) – je Fund ein Test.

Ist-Werte ohne Speicherladung, Takt ohne Anforderung und doppelt gezählten Nachtwechsel; Abfahrt nur an einem
echten Wechsel der Gewohnheit (Homeoffice); Heizbetrieb bleibt an, solange ein Raum ohne eigenen Vorlauf Wärme
braucht; Vorheiz-Vorlauf rechtzeitig statt Zu-spät-Plan; Tages-Soll nicht gesteuerter Räume, nachts abgefragt;
Tracker „unavailable“ wie in der Steuerung; Räume ohne Vorlauf hält ihr Thermostat am Soll; Außenfühler statt
Vorhersage für jetzt; README auf Stand 0.37. Nichts davon schreibt an den Kessel.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from heatpilot import preview as pv, preview_log as pl
from heatpilot.config import Config
from heatpilot.discovery import Room
from heatpilot.models import RoomModel
from heatpilot.presence_plan import timeline
from heatpilot.values import local

from test_control34 import MON, at, climate, slow_room, ts_of, wz
from test_preview37 import TUE, _habit, _state, by_hour, forecast, house


# Die Testhäuser dieser Datei brauchen 28–32 °C Vorlauf. Seit 0.39 heizt die Vorschau erst ab 35 °C (darunter
# „Heizbetrieb aus“, Wunsch Ingo) – diese Tests prüfen anderes (Vorhersage, Gewohnheit, Protokoll …) und laufen
# deshalb mit der alten Untergrenze; die 35-°C-Regel selbst prüft tests/test_flow_min39.py.
@pytest.fixture(autouse=True)
def _old_flow_floor(monkeypatch):
    monkeypatch.setattr(pv, "FLOW_MIN", 25.0)

ROOT = Path(__file__).resolve().parents[1]
H12 = ts_of(MON, 12)


# ------------------------------------------------------------------ Ist-Werte (r03, r07)
def _actual(tmp_path, plan: dict, rows: list[dict], before: dict, start=H12):
    eng, _, _ = house(tmp_path)
    s = eng.store
    s.add_preview_plan(start, {"t_out": 5.0, "w_flow": 0, "w_heating": 0, "w_dhw": 0, **plan})
    s.add_sample(start - 300, before)
    for i, r in enumerate(rows):
        s.add_sample(start + i * 300, r)
    s.commit()
    pl.fill_actuals(s, start + 3600)
    return s


def test_actuals_skip_dhw_charge_and_prefer_circuit_setpoint(tmp_path):
    """Speicherladung (Ventil auf Warmwasser, Kessel-Soll 75 °C) ist kein Vorlauf-Soll des Heizkreises."""
    rows = [{"sel_flow": 75 if i in (4, 5) else 45, "dhw": 1 if i in (4, 5) else 0, "calc_flow": 45,
             "dhw_set": 55, "hc_day": 1, "heating_off": 0} for i in range(12)]
    s = _actual(tmp_path, {"flow": 45, "heating": 1, "dhw": 55}, rows,
                {"sel_flow": 45, "dhw_set": 55, "hc_day": 1, "heating_off": 0, "dhw": 0})
    r = s.preview_log(0)[0]
    assert (r["act_flow"], r["ch_flow"]) == (45.0, 0)
    assert pl.compare(s, H12 + 3600, None)["text"].startswith("HeatPilot läge im Mittel etwa gleich beim Vorlauf")
    # nicht markierte Ladung (dhw fehlt, Kessel-Soll weit über dem Heizkreis) ebenso; Heizkreis-Soll gilt
    rows = [{"sel_flow": 70 if i == 3 else 44, "calc_flow": 43, "dhw_set": 55, "hc_day": 1} for i in range(12)]
    s = _actual(tmp_path / "b", {"flow": 45, "heating": 1}, rows, {"sel_flow": 44, "calc_flow": 43, "hc_day": 1})
    r = s.preview_log(0)[0]
    assert (r["act_flow"], r["ch_flow"]) == (43.0, 0)


def test_actuals_skip_no_demand_phases(tmp_path):
    """Regler im Tagbetrieb ohne Anforderung (Kessel-Soll 7 °C) zählt weder im Mittel noch als Wechsel."""
    rows = [{"sel_flow": 40 if i % 4 < 2 else 7, "dhw_set": 55, "hc_day": 1, "heating_off": 0} for i in range(12)]
    s = _actual(tmp_path, {"flow": 40, "heating": 1, "dhw": 55}, rows,
                {"sel_flow": 40, "dhw_set": 55, "hc_day": 1, "heating_off": 0})
    r = s.preview_log(0)[0]
    assert (r["act_flow"], r["ch_flow"], r["act_heating"]) == (40.0, 0, 1.0)
    # eine echte Änderung zwischen zwei Anforderungen zählt weiter (über die Pause hinweg, wie im Plan)
    rows = [{"sel_flow": [40, 7, 7, 43][i] if i < 4 else 43, "hc_day": 1} for i in range(12)]
    s = _actual(tmp_path / "b", {"flow": 40, "heating": 1}, rows, {"sel_flow": 7, "hc_day": 1})
    r = s.preview_log(0)[0]
    assert r["ch_flow"] == 1 and r["act_flow"] == round((40 + 43 * 9) / 10, 2)


def test_night_switch_counts_like_the_plan(tmp_path):
    """Tag → Nacht (45 → 10 °C, Betriebsart Nacht): im Ist wie im Plan ein Wechsel – Heizbetrieb, nicht Vorlauf."""
    h21 = ts_of(MON, 21)
    rows = [{"sel_flow": 10, "dhw_set": 50, "hc_day": 0, "heating_off": 0} for _ in range(12)]
    s = _actual(tmp_path, {"flow": None, "heating": 0, "dhw": 50}, rows,
                {"sel_flow": 45, "dhw_set": 50, "hc_day": 1, "heating_off": 0}, start=h21)
    r = s.preview_log(0)[0]
    assert (r["ch_flow"], r["ch_heating"], r["act_flow"]) == (0, 1, None)
    t0 = local(h21)
    plan = [(t0 - timedelta(minutes=5), 45)] + [(t0 + timedelta(minutes=5 * i), None) for i in range(12)]
    onoff = [(t0 - timedelta(minutes=5), True)] + [(t0 + timedelta(minutes=5 * i), False) for i in range(12)]
    assert len(pv.count_writes(plan, 45, 1.0)) + len(pv.count_writes(onoff, True, None)) == 1


# ------------------------------------------------------------------ Abfahrt nach Gewohnheit (r09)
def test_home_against_habit_stays_until_next_regular_departure(tmp_path):
    """Homeoffice: Gewohnheit werktags 8–17 Uhr weg, heute um 10 Uhr zu Hause – Komfort bis morgen früh."""
    eng, _, _ = house(tmp_path, persons=["person.a"])
    p = _habit(eng)
    _state(eng, True)
    h = by_hour(pv.preview(eng, ts_of(MON, 10)))
    for hh in ("10:00", "11:00", "14:00", "16:00", "20:00"):
        assert h[hh]["home"] == 1 and h[hh]["phases"]["Wz"] in ("komfort", "nacht"), hh
    assert h["14:00"]["heating"] is True and h["14:00"]["flow"] is not None
    pts, _ = timeline(p, at(MON, 10), at(MON, 10) + timedelta(hours=24), None, False)
    assert pts[1:] == [(at(TUE, 8), False)]  # nächste reguläre Abfahrt: Di 08:00
    # zur gewohnten Zeit zu Hause (07:50): Abfahrt wie gewohnt um 08:00
    pts, _ = timeline(p, at(MON, 7, 50), at(MON, 7, 50) + timedelta(hours=24), None, False)
    assert pts[1] == (at(MON, 8), False)
    # Protokoll: jede Stunde des Homeoffice-Tags mit Heizbetrieb an
    for hh in (9, 10, 11, 12, 13, 14, 15):
        eng.__dict__.pop("_preview_logged", None)
        pl.record(eng, ts_of(MON, hh))
    assert [r["heating"] for r in eng.store.preview_log(0)] == [1] * 7


# ------------------------------------------------------------------ Anwesenheit jetzt (r04)
def test_unavailable_tracker_means_unknown_like_the_controller(tmp_path):
    eng, _, loop = house(tmp_path, persons=["person.a"], temp=19.5)
    p = _habit(eng)
    p.home = False  # zuletzt beobachtet: weg
    eng.states["person.a"] = {"state": "unavailable", "attributes": {}}
    ts = ts_of(MON, 10)
    sample, rooms, _ = eng.snapshot()
    loop.plan(ts, sample, rooms)
    ctl = loop.rooms["climate.wz"]
    row = pv._Run(eng, ts).step(local(ts))
    assert (ctl["desired"], ctl["phase"]) == (row["sps"]["Wz"], row["phases"]["Wz"]) == (21.0, "komfort")
    assert row["home"] is None and sample["home_count"] is None


# ------------------------------------------------------------------ Heizbetrieb aus (r02)
def _bad(**kw):
    return Room("Bad", "climate.bad", managed=True, comfort=22.0, platform="homekit_controller", **kw)


def test_heating_stays_on_while_room_without_flow_is_below_setpoint(tmp_path):
    for variant in ("ohne Modell", "Fußboden"):
        cfg = Config(control_mode="heatpilot", floor_heating_rooms=["climate.bad"] if variant == "Fußboden" else [])
        eng, _, _ = house(tmp_path / variant, cfg=cfg, temp=22.5, rooms=[wz(), _bad()])  # Wz über dem Soll
        climate(eng, "climate.bad", 17.0, 22.0)
        eng.rooms["climate.bad"] = slow_room() if variant == "Fußboden" else RoomModel("Bad")
        h0 = pv.preview(eng, ts_of(MON, 12))["hours"][0]
        assert h0["heating"] is True and h0["flow"] == round(pv.FLOW_MIN), variant
        kind = "Fußbodenheizung" if variant == "Fußboden" else "ohne Raummodell"
        assert h0["flow_reason"] == f"Bad unter Soll ({kind}) – Heizbetrieb bleibt an"
    # Raum ohne Modell am Soll (Messwert): zählt nicht – kein Raum braucht Wärme
    eng, _, _ = house(tmp_path / "am Soll", temp=22.5, rooms=[wz(), _bad()])
    climate(eng, "climate.bad", 21.8, 22.0)
    eng.rooms["climate.bad"] = RoomModel("Bad")
    h0 = pv.preview(eng, ts_of(MON, 12))["hours"][0]
    assert h0["heating"] is False and h0["flow_reason"] == "kein Raum braucht Wärme – Heizbetrieb aus"
    # Temperatur unbekannt: Heizbetrieb bleibt an
    climate(eng, "climate.bad", None, 22.0)
    h0 = pv.preview(eng, ts_of(MON, 12))["hours"][0]
    assert h0["heating"] is True and "ohne Raumtemperatur" in h0["flow_reason"]


# ------------------------------------------------------------------ Vorheizen (r01)
def test_preheat_flow_is_in_time_not_the_late_plan(tmp_path):
    """Vorheizen mit dem niedrigsten Vorlauf, der ab dem Beginn laut Steuerung rechtzeitig reicht."""
    eng, _, _ = house(tmp_path, t_out=9.0)
    run = pv._Run(eng, ts_of(MON, 12))
    until = None
    for ts in range(run.start_ts, run.end_ts, pv.STEP_MIN * 60):
        row = run.step(local(ts))
        if row["phases"]["Wz"] == "vorheizen" and until is None:
            until = next(iter(run.preheat))[1]
            start, flow = row["t"], row["flow"]
        run.advance(row, pv.STEP_MIN / 60)
        if until is not None and row["t"] + timedelta(minutes=pv.STEP_MIN) >= until:
            break
    assert until == at(TUE, 4, 30) and start < until
    assert run.rooms[0].temp >= 21.0 - pv.HEATUP_GAP_K  # rechtzeitig warm, nicht zu spät
    m = slow_room()
    assert flow == round(flow) and pv.FLOW_MIN <= flow <= eng.cfg.max_flow_temp
    # direkt gerechnet: kurzer Vorlauf (15 min, Mindestdauer der Steuerung) aus 20,8 °C braucht weniger als 75 °C
    r = run.rooms[0]
    r.temp = 20.8
    t = at(TUE, 4)
    low = run._preheat_flow(r, 21.0, t + timedelta(minutes=15), t)
    assert low < eng.cfg.max_flow_temp and m.time_to_reach(20.8, 21.0, low, 9.0) <= 0.25
    assert m.time_to_reach(20.8, 21.0, low - 1, 9.0) is None or m.time_to_reach(20.8, 21.0, low - 1, 9.0) > 0.25
    # reicht auch der maximale nicht: der maximale
    r.temp = 15.0
    assert run._preheat_flow(r, 21.0, t + timedelta(minutes=5), t) == eng.cfg.max_flow_temp
    assert "sparsamsten machbaren" not in (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
    assert "sparsamsten machbaren" not in pv.__doc__


# ------------------------------------------------------------------ Tages-Soll, nachts abgefragt (r08)
def _day_samples(eng, target_day, target_night=17.0):
    """Mo 08:00–22:00 Tado-Soll ``target_day`` (jemand zu Hause), nachts davor und danach ``target_night``."""
    s = eng.store
    for i in range(int(18.5 * 4)):  # Mo 05:00 … Di 00:30 im 15-min-Raster
        ts = ts_of(MON, 5) + i * 900
        night = eng.eff.is_night(local(ts).time())
        s.add_sample(ts, {"home_count": 1, "vacation": 0})
        s.add_room_samples([(ts, "climate.wz", 20.0, target_night if night else target_day, None, None)])
    s.commit()


def test_unmanaged_day_setpoint_from_measured_day_when_asked_at_night(tmp_path):
    eng, _, _ = house(tmp_path, cfg=Config(control_mode="beobachten"))
    _day_samples(eng, 19.5)
    climate(eng, "climate.wz", 19.0, None, mode="off")  # Tado-Zeitplanblock „aus“ in der Nacht
    h = by_hour(pv.preview(eng, ts_of(TUE, 1)))
    assert h["12:00"]["rooms"]["Wz"] == 19.5 and h["12:00"]["phases"]["Wz"] == "komfort"
    assert h["12:00"]["heating"] is True
    climate(eng, "climate.wz", 19.0, 17.0)  # nachts 17 °C
    assert by_hour(pv.preview(eng, ts_of(TUE, 1)))["12:00"]["rooms"]["Wz"] == 19.5
    # tagsüber aus gewesen: bleibt aus
    eng2, _, _ = house(tmp_path / "aus", cfg=Config(control_mode="beobachten"))
    _day_samples(eng2, None)
    climate(eng2, "climate.wz", 19.0, None, mode="off")
    assert by_hour(pv.preview(eng2, ts_of(TUE, 1)))["12:00"]["rooms"]["Wz"] is None
    # ohne Messung: Komfortwert des Raums
    eng3, _, _ = house(tmp_path / "leer", cfg=Config(control_mode="beobachten"))
    climate(eng3, "climate.wz", 19.0, None, mode="off")
    assert by_hour(pv.preview(eng3, ts_of(TUE, 1)))["12:00"]["rooms"]["Wz"] == 21.0


# ------------------------------------------------------------------ Räume ohne Vorlauf (r06)
def test_rooms_without_flow_are_held_at_setpoint(tmp_path):
    m = RoomModel("climate.wz", floor=True)
    m.rls.theta = [0.03, 0.03, 0.05, 0.0, 0.0, 0.0, 0.0]
    m.rls.updates, m.excitation = 500, 100.0
    cfg = Config(control_mode="heatpilot", floor_heating_rooms=["climate.wz"])
    eng, _, _ = house(tmp_path, model=m, temp=21.0, cfg=cfg)
    run = pv._Run(eng, ts_of(MON, 12))
    temps = {}
    for ts in range(run.start_ts, run.end_ts, pv.STEP_MIN * 60):
        row = run.step(local(ts))
        assert row["heating"] is None  # kein Raum bestimmt den Vorlauf
        run.advance(row, pv.STEP_MIN / 60)
        temps[local(ts).strftime("%a %H:%M")] = run.rooms[0].temp
    assert temps["Mon 15:00"] == temps["Mon 20:00"] == 21.0  # tagsüber hält das Thermostat Komfort
    assert temps["Tue 01:00"] >= 18.0  # nachts kühlt er bis aufs Nacht-Soll, nicht darunter


# ------------------------------------------------------------------ Außentemperatur jetzt (r10)
def test_current_outdoor_from_sensor_forecast_offset_fades(tmp_path):
    eng, _, _ = house(tmp_path, t_out=0.0, weather="weather.home")
    now = ts_of(MON, 12)
    eng.forecast = forecast(now, [6.0] * 30)  # Wetterdienst 6 K über dem Fühler, auch für jetzt
    p = pv.preview(eng, now)
    assert p["steps"][0]["flow"] == round(slow_room().required_flow(21.0, 0.0))  # jetzt: der Fühler
    assert p["forecast_offset_k"] == -6.0 and p["hours"][0]["t_out_src"] == "angeglichen"
    assert p["hours"][0]["t_out"] < 1.0
    h = by_hour(p)
    assert h["19:00"]["t_out_src"] == "vorhersage" and h["19:00"]["t_out"] == 6.0  # nach 6 h abgeklungen
    assert 0.0 < h["15:00"]["t_out"] < 6.0
    # Vorhersage wie der Fühler: keine Angleichung
    eng.forecast = forecast(now, [0.0] * 30)
    p = pv.preview(eng, now)
    assert p["forecast_offset_k"] == 0.0 and p["hours"][0]["t_out_src"] == "vorhersage"
    ui = (ROOT / "ems_heatpilot" / "heatpilot" / "static" / "index.html").read_text(encoding="utf-8")
    assert "angeglichen:" in ui and "forecast_offset_k" in ui


# ------------------------------------------------------------------ README (r12)
def test_readme_on_037():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Was es heute kann (0.37)" in text and "Vorschau (Trockenlauf)" in text
    row = next(line for line in text.splitlines() if line.startswith("| 3 |"))
    assert row.rstrip().endswith("| erfüllt (0.37) |")
