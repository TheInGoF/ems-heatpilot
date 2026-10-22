"""0.37 Vorschau (Trockenlauf): Vorlauf-Soll, Heizbetrieb und Warmwasser-Soll der nächsten 24 h – nur Anzeige.

Geprüft: Nacht senkt den Vorlauf bzw. schaltet den Heizbetrieb aus, Vorheizen vor dem Nachtende hebt ihn an, eine
kältere Vorhersage hebt ihn an, Warmwasser vor einer großen Zapfung höher, Desinfektionstermin markiert,
Schreibzählung (nur bei Änderung ab 1 K, Haltezeit), ohne Modelle kein Vorlauf (mit Grund), Zeitumstellung
(23/25 Stunden am Tag), Anwesenheit aus Gewohnheit. Dazu Stufe 2 der Steuerung: Vorheizdauer mit der
Außentemperatur zum Zielzeitpunkt. Nichts davon schreibt an den Kessel.
"""

import json
import time
from datetime import datetime, timedelta

import pytest

from heatpilot import preview as pv
from heatpilot.config import Config
from heatpilot.controlloop import Presence, desired, preheat_hours
from heatpilot.dhw import SLOTS, DhwModel
from heatpilot.models import RoomModel
from heatpilot.presence_plan import timeline
from heatpilot.web import clean

from test_control34 import FRI, MON, SUN, at, climate, make, run, slow_room, ts_of, wz


# Die Testhäuser dieser Datei brauchen 28–32 °C Vorlauf. Seit 0.39 heizt die Vorschau erst ab 35 °C (darunter
# „Heizbetrieb aus“, Wunsch Ingo) – diese Tests prüfen anderes (Vorhersage, Gewohnheit, Protokoll …) und laufen
# deshalb mit der alten Untergrenze; die 35-°C-Regel selbst prüft tests/test_flow_min39.py.
@pytest.fixture(autouse=True)
def _old_flow_floor(monkeypatch):
    monkeypatch.setattr(pv, "FLOW_MIN", 25.0)

TUE = (2026, 10, 13)


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def house(tmp_path, cfg=None, temp=21.0, target=21.0, model=None, t_out=5.0, **found_kw):
    """Engine mit einem Raum „Wz“ (macht mit, Komfort 21 °C), Außenfühler und plausiblen Modellen."""
    eng, ha, _, loop = make(tmp_path, cfg=cfg, outdoor=["sensor.out"], **found_kw)
    eng.states["sensor.out"] = {"state": str(t_out), "attributes": {}}
    climate(eng, "climate.wz", temp, target)
    eng.rooms["climate.wz"] = model or slow_room()
    eng.house.rls.updates = 500
    eng.house.rls.theta = [0.2, 2.0, 0.3]  # 200 W/K, Trägheit 10 h – plausibel
    return eng, ha, loop


def by_hour(p: dict) -> dict[str, dict]:
    """Stunden der Vorschau nach Wanduhr „HH:MM“ (am ersten Auftreten)."""
    out = {}
    for h in p["hours"]:
        out.setdefault(h["t"][11:16], h)
    return out


def forecast(start_ts: int, temps: list[float]) -> list[dict]:
    return [{"datetime": datetime.fromtimestamp(start_ts + i * 3600).astimezone().isoformat(), "temperature": t}
            for i, t in enumerate(temps)]


# ------------------------------------------------------------------ Schreibvorgänge
def test_count_writes_only_on_change_and_hold_time():
    t0 = datetime(2026, 10, 12, 12, 0).astimezone()
    series = [(t0 + timedelta(minutes=i), v) for i, v in enumerate([40, 40.4, 40.9, 41.2, 43, 42, 43, 41, 44, 44])]
    w = pv.count_writes(series, 40, 1.0, hold_min=5)
    # 41,2 ist die erste Änderung ab 1 K; danach 5 min Haltezeit: 43 (12:04) wartet, um 12:08 steht 44 → geschrieben
    assert [(t.minute, v, prev) for t, v, prev in w] == [(3, 41.2, 40), (8, 44, 41.2)]
    # ohne Haltezeit hätte jede Änderung ab 1 K gezählt
    assert len(pv.count_writes(series, 40, 1.0, hold_min=0)) == 6
    # an/aus: jeder Wechsel, Rückwechsel in der Haltezeit verfällt; None wird übersprungen
    onoff = [(t0 + timedelta(minutes=i), v) for i, v in enumerate([True, None, False, True, True, True, True, True])]
    assert [(t.minute, v) for t, v, _ in pv.count_writes(onoff, True, None, hold_min=5)] == [(2, False), (7, True)]
    back = onoff[:7] + [(t0 + timedelta(minutes=7), False)]  # nach der Haltezeit wieder wie geschrieben: nichts
    assert [(t.minute, v) for t, v, _ in pv.count_writes(back, True, None, hold_min=5)] == [(2, False)]
    # unbekannter Startwert: der erste Wert zählt
    assert pv.count_writes([(t0, 50)], None, 1.0) == [(t0, 50, None)]
    assert pv.count_writes([(t0, 50.6)], 50, 1.0) == []  # unter 1 K: nicht schreiben


# ------------------------------------------------------------------ Vorlauf
def test_night_lowers_flow_or_switches_heating_off_and_preheat_raises_it(tmp_path):
    eng, ha, _ = house(tmp_path)
    p = pv.preview(eng, ts_of(MON, 12))
    h = by_hour(p)
    assert len(p["hours"]) == 24 and p["hours"][0]["t"] == at(MON, 12).isoformat()
    day = h["14:00"]
    hold = round(slow_room().required_flow(21.0, 5.0))  # 29 °C hält 21 °C bei 5 °C außen
    assert day["heating"] is True and day["flow"] == hold and day["rooms"]["Wz"] == 21.0
    assert day["flow_reason"] == "Wz hält 21,0 °C" and day["t_out_src"] == "aktuell"
    # Nacht Mo 21:00 bis Di 04:30: Soll 18 °C, der Raum kühlt erst ab – kein Raum braucht Wärme
    night = h["23:00"]
    assert night["rooms"]["Wz"] == 18.0 and night["phases"]["Wz"] == "nacht" and night["night"] is True
    assert night["heating"] is False and night["flow"] is None
    # seit 0.46.5 (Standard „Pumpe läuft weiter“): kein Bedarf heißt nur Pumpe – Heizbetrieb an, Vorlauf-Soll 20 °C
    assert night["flow_reason"] == "kein Raum braucht Wärme – Pumpe läuft, Vorlauf-Soll 20 °C" and night["idle"] is True
    # vor dem Nachtende vorheizen (Raum ≈ 20,3 °C): höherer Vorlauf als zum Halten, bis der Raum am Soll ist – auf die
    # Minute in ``steps``, je Stunde der höchste in ``flow_max``. Seit 0.50.4 mit dem Aufheiz-Vorlauf (Soll in ≈ 1 h)
    pre = [s for s in p["steps"] if s["flow"] and s["flow"] > hold]
    assert len(pre) == 1 and at(TUE, 3) <= datetime.fromisoformat(pre[0]["t"]) < at(TUE, 4, 30)
    assert 35 <= pre[0]["flow"] <= eng.cfg.max_flow_temp  # 0.50.4: Aufheiz-Vorlauf
    assert h["03:00"]["flow_max"] == pre[0]["flow"] and h["03:00"]["night"] is True
    assert any(w["what"] == "flow" and w["value"] == pre[0]["flow"] for w in h["03:00"]["writes"])
    # schon warm: halten – und das Vorheizen bleibt bis zum Nachtende (0.46.5, kein Pendeln zurück in die Nacht)
    assert all(x["phases"]["Wz"] == "vorheizen" for k, x in h.items() if "04:00" <= k <= "04:00")
    assert h["04:00"]["flow"] == hold and h["04:00"]["heating"] is True
    assert h["06:00"]["flow"] == hold and h["06:00"]["phases"]["Wz"] == "komfort"
    # Schreibvorgänge: Vorlauf 20 °C um 21:00 (nur Pumpe, seit 0.46.5 statt Heizbetrieb aus), hoch zum Vorheizen und
    # wieder runter – der Heizbetrieb bleibt an
    assert p["writes_24h"]["heating"] <= 1 and p["writes_24h"]["flow"] >= 3
    assert any(w["what"] == "flow" and w["value"] == 20 for w in h["21:00"]["writes"])
    assert sum(len(x["writes"]) for x in p["hours"]) == sum(p["writes_24h"].values())
    flows = [w for x in p["hours"] for w in x["writes"] if w["what"] == "flow"]
    assert all(w["from"] is None or abs(w["value"] - w["from"]) >= 1 for w in flows)
    assert p["dry_run"] is True and p["note"] == pv.NOTE
    assert ha.calls == []  # nichts geschrieben (auch nicht an Tado)


def test_colder_forecast_raises_flow(tmp_path):
    eng, _, _ = house(tmp_path, weather="weather.home")
    now = ts_of(MON, 12)
    warm = by_hour(pv.preview(eng, now))["14:00"]
    eng.forecast = forecast(now, [5.0] + [-5.0] * 30)  # jetzt wie der Fühler (5 °C), ab 13 Uhr −5 °C
    cold = by_hour(pv.preview(eng, now))["14:00"]
    assert warm["t_out_src"] == "aktuell" and cold["t_out_src"] == "vorhersage" and cold["t_out"] == -5.0
    assert cold["flow"] == round(slow_room().required_flow(21.0, -5.0)) > warm["flow"]
    # Vorhersagepunkt weiter als 90 min weg: aktuelle Außentemperatur
    eng.forecast = forecast(now + 6 * 3600, [-5.0])
    assert by_hour(pv.preview(eng, now))["14:00"]["t_out_src"] == "aktuell"


def test_flow_capped_and_rounded(tmp_path):
    eng, _, _ = house(tmp_path, model=slow_room(a=0.004), cfg=Config(control_mode="heatpilot", max_flow_temp=60))
    h = by_hour(pv.preview(eng, ts_of(MON, 12)))["14:00"]
    assert h["flow"] == 60 and isinstance(h["flow"], int)  # bräuchte 61 °C, gedeckelt auf max_flow_temp


def test_without_models_no_flow_with_reason(tmp_path):
    eng, _, _ = house(tmp_path, model=RoomModel("Wz"))  # frisch, nicht plausibel
    p = pv.preview(eng, ts_of(MON, 12))
    assert all(h["flow"] is None and h["heating"] is None for h in p["hours"])
    assert p["flow_note"] == pv.NO_MODELS and p["writes_24h"]["flow"] == 0
    assert p["rooms_used"] == [] and p["rooms_skipped"] == [{"name": "Wz", "why": "Raummodell noch nicht plausibel"}]
    # Fußbodenheizung zählt nicht für den Vorlauf
    eng, _, _ = house(tmp_path / "b", cfg=Config(control_mode="heatpilot", floor_heating_rooms=["climate.wz"]))
    p = pv.preview(eng, ts_of(MON, 12))
    assert p["flow_note"] == pv.NO_MODELS and "Fußbodenheizung" in p["rooms_skipped"][0]["why"]
    # ohne Außentemperatur
    eng, _, _ = house(tmp_path / "c")
    del eng.states["sensor.out"]
    assert pv.preview(eng, ts_of(MON, 12))["flow_note"] == pv.NO_T_OUT


def test_observe_mode_uses_tado_setpoints(tmp_path):
    """Beobachten: Tado bestimmt das Soll – tagsüber das heutige, nachts der Nachtwert (Einstellung/gelernt)."""
    cfg = Config(control_mode="beobachten")
    eng, _, _ = house(tmp_path, cfg=cfg, target=20.5)
    h = by_hour(pv.preview(eng, ts_of(MON, 12)))
    assert h["14:00"]["rooms"]["Wz"] == 20.5 and h["14:00"]["phases"]["Wz"] == "komfort"
    assert h["23:00"]["rooms"]["Wz"] == 18.0 and h["23:00"]["phases"]["Wz"] == "nacht"  # Nacht 22:30–06:00
    assert h["14:00"]["flow"] == round(slow_room().required_flow(20.5, 5.0))
    # nachts gefragt: tagsüber gilt der Komfortwert des Raums, nicht das heutige Nacht-Soll
    climate(eng, "climate.wz", 19.0, 18.0)
    h = by_hour(pv.preview(eng, ts_of((2026, 10, 13), 1)))
    assert h["12:00"]["rooms"]["Wz"] == 21.0
    # Thermostat aus: bleibt aus, braucht keine Wärme
    climate(eng, "climate.wz", 19.0, None, mode="off")
    h = by_hour(pv.preview(eng, ts_of(MON, 12)))
    assert h["14:00"]["rooms"]["Wz"] is None and h["14:00"]["heating"] is False


# ------------------------------------------------------------------ Anwesenheit
def _habit(eng, person="person.a", away=(8, 17), visits=5):
    """Gewohnheit: werktags von ``away[0]`` bis ``away[1]`` Uhr weg, sonst zu Hause (verlässlich gelernt)."""
    p = eng.persons[person]
    for d in range(7):
        for s in range(96):
            p.week[d * 96 + s] = 0.1 if d < 5 and away[0] * 4 <= s < away[1] * 4 else 0.9
    p.visits = [visits] * len(p.visits)
    return p


def _state(eng, home, person="person.a"):
    """Zustand der Person jetzt – gelernt (``home``) und in Home Assistant (die Vorschau liest ihn dort)."""
    eng.persons[person].home = home
    eng.states[person] = {"state": {True: "home", False: "not_home", None: "unavailable"}[home], "attributes": {}}


def test_home_until_habit_departure_then_preheat_for_arrival(tmp_path):
    eng, _, _ = house(tmp_path, persons=["person.a"])
    _habit(eng)
    _state(eng, True)
    h = by_hour(pv.preview(eng, ts_of(MON, 7)))
    assert h["07:00"]["home"] == 1 and h["07:00"]["phases"]["Wz"] == "komfort"
    assert h["10:00"]["home"] == 0 and h["10:00"]["phases"]["Wz"] == "abwesend" and h["10:00"]["rooms"]["Wz"] == 19.0
    assert h["16:00"]["phases"]["Wz"] == "vorheizen"  # Ankunft 17:00 laut Gewohnheit
    assert h["18:00"]["home"] == 1 and h["18:00"]["phases"]["Wz"] == "komfort"
    # schon weg: Ankunft laut Gewohnheit, davor Abwesenheit
    _state(eng, False)
    h = by_hour(pv.preview(eng, ts_of(MON, 10)))
    assert h["11:00"]["phases"]["Wz"] == "abwesend" and h["18:00"]["phases"]["Wz"] == "komfort"


def test_unlearned_habit_keeps_comfort_and_unknown_tracker(tmp_path):
    eng, _, _ = house(tmp_path, persons=["person.a"])
    _habit(eng, visits=0)  # Gewohnheit lernt noch: Ankunft offen → Komfort (wie die Steuerung)
    _state(eng, False)
    h = by_hour(pv.preview(eng, ts_of(MON, 10)))
    assert h["11:00"]["phases"]["Wz"] == "komfort" and h["11:00"]["home"] == 0
    _state(eng, None)  # Tracker unbekannt
    h = by_hour(pv.preview(eng, ts_of(MON, 10)))
    assert h["11:00"]["home"] is None and h["11:00"]["phases"]["Wz"] == "komfort"


def test_timeline_points_and_unknown_slots(tmp_path):
    eng, _, _ = house(tmp_path, persons=["person.a"])
    p = _habit(eng)
    p.home = True
    now, end = at(MON, 7, 3), at(MON, 7) + timedelta(hours=24)
    pts, unknown = timeline(p, now.replace(second=17), end, None, False)
    # Abfahrt und Ankunft zur vollen Viertelstunde, nicht mit den Sekunden von „jetzt“
    assert [(t.strftime("%a %H:%M:%S"), s) for t, s in pts[1:]] == [("Mon 08:00:00", False), ("Mon 17:00:00", True)]
    assert unknown == []
    p.visits[0 * 96 + 12 * 4] = 0  # Mo 12:00 noch nicht gelernt: dort ist die Ankunft offen
    _, unknown = timeline(p, now, end, None, False)
    assert unknown == [at(MON, 12)]
    _, unknown = timeline(p, now, end, None, True)  # GPS zählt nur für eine laufende Abwesenheit
    assert unknown == [at(MON, 12)]
    p.home = False  # schon weg, GPS kennt die Ankunft (17:00): nichts offen
    _, unknown = timeline(p, now, end, at(MON, 17), True)
    assert unknown == []


def test_vacation_until_return_then_comfort(tmp_path):
    cfg = Config(control_mode="heatpilot", vacations=["2026-10-05 - 2026-10-12 18:00"], arrival_margin_k=0.0)
    eng, _, _ = house(tmp_path, cfg=cfg)
    h = by_hour(pv.preview(eng, ts_of(MON, 12)))
    assert h["13:00"]["vacation"] is True and h["13:00"]["phases"]["Wz"] == "urlaub"
    assert h["17:00"]["phases"]["Wz"] == "vorheizen"
    assert h["19:00"]["vacation"] is False and h["19:00"]["phases"]["Wz"] == "komfort"
    cfg.arrival_margin_k = 1.0  # seit 0.49 Standard: kaum ausgekühlt (21 → 20,x °C) – vorheizen erst kurz vorher
    h = by_hour(pv.preview(eng, ts_of(MON, 12)))
    assert h["17:00"]["phases"]["Wz"] == "urlaub" and h["19:00"]["phases"]["Wz"] == "komfort"


# ------------------------------------------------------------------ Warmwasser
def dhw_model(sundays=4, size=14.0):
    """Plausibles Warmwasser-Modell: sonntags 18 Uhr eine Wanne (≈ ``size`` K) in den letzten Wochen."""
    m = DhwModel()
    m.charges, m.charge_rate_kh = 10, 20.0
    m.kwh_per_k, m.kwh_per_k_n = {"kalt": 0.18, "warm": 0.18}, {"kalt": 5, "warm": 5}
    m.profile_n = [8] * SLOTS
    for w in range(1, sundays + 1):
        m.big_draws.append([(at(SUN, 18) - timedelta(days=7 * w)).isoformat(timespec="minutes"), size, False])
    return m


def test_dhw_higher_before_big_draw_and_disinfection_marked(tmp_path):
    cfg = Config.from_raw({"control_mode": "heatpilot", "dhw_disinfect_day": "So", "dhw_disinfect_time": "16:30"})
    eng, _, _ = house(tmp_path, cfg=cfg)
    eng.dhw = dhw_model()
    p = pv.preview(eng, ts_of(SUN, 10))
    h = by_hour(p)
    # seit 0.46.2: tagsüber Komfort 50 °C als Soll (Speicher 45–50 °C, Hysterese 5 K angenommen), nachts
    # Minimum 40 + 5 K, vor der Wanne Minimum 40 + 14 K ohne Hysterese
    assert h["12:00"]["dhw"] == 50 and "Speicher 45–50 °C" in h["12:00"]["dhw_reason"]
    assert h["17:00"]["dhw"] == 54 and "große Zapfung" in h["17:00"]["dhw_reason"]
    assert h["21:00"]["dhw"] == 45 and h["21:00"]["dhw_reason"].startswith("Nacht")
    assert h["16:00"]["disinfect"] == {"temp": 70.0, "counts": False, "at": at(SUN, 16, 30).isoformat()}
    assert h["15:00"]["disinfect"] is None
    dhw_writes = [w for x in p["hours"] for w in x["writes"] if w["what"] == "dhw"]
    # zurück auf Komfort, Nacht, vor dem Aufstehen (Nachtende 04:30) wieder Komfort
    assert [w["value"] for w in dhw_writes] == [50, 54, 50, 45, 50] and p["writes_24h"]["dhw"] == 5
    # Modell lernt noch: kein Warmwasser-Soll, Grund steht da
    eng.dhw = DhwModel()
    p = pv.preview(eng, ts_of(SUN, 10))
    assert p["hours"][0]["dhw"] is None and p["dhw_note"].startswith("lernt noch")


# ------------------------------------------------------------------ Zeitumstellung
def test_dst_days_hours_by_wall_clock(tmp_path, berlin):
    eng, _, _ = house(tmp_path)
    # Herbst: So 25.10.2026 03:00 MESZ → 02:00 MEZ – 02:00 kommt zweimal, die Nacht endet So 06:30 Wanduhr
    now = int(datetime(2026, 10, 24, 12, 0).timestamp())
    p = pv.preview(eng, now)
    ts = [datetime.fromisoformat(h["t"]).timestamp() for h in p["hours"]]
    assert len(ts) == 24 and all(b - a == 3600 for a, b in zip(ts, ts[1:]))
    wall = [h["t"][11:13] for h in p["hours"]]
    assert wall.count("02") == 2 and wall[-1] == "10"
    h = by_hour(p)
    assert h["05:00"]["night"] is True and h["07:00"]["night"] is False and h["07:00"]["phases"]["Wz"] == "komfort"
    # Frühjahr: So 29.03.2026 02:00 MEZ → 03:00 MESZ – 02:00 fehlt
    p = pv.preview(eng, int(datetime(2026, 3, 28, 12, 0).timestamp()))
    wall = [h["t"][11:13] for h in p["hours"]]
    assert len(wall) == 24 and "02" not in wall and wall[-1] == "12"


# ------------------------------------------------------------------ Abruf und Antwort
def test_cached_and_json_clean(tmp_path, monkeypatch):
    eng, _, _ = house(tmp_path)
    calls = []
    real = pv.preview
    monkeypatch.setattr(pv, "preview", lambda e, now=None, written=None: calls.append(now) or real(e, now, written))
    now = ts_of(MON, 12, 1)
    first = pv.cached(eng, now)
    assert pv.cached(eng, now + 200) is first and len(calls) == 1  # 5 min Cache
    pv.cached(eng, now + 400)
    pv.cached(eng, ts_of(MON, 13))  # neue Stunde: neu gerechnet
    assert len(calls) == 3
    text = json.dumps(clean(first), allow_nan=False)
    assert "NaN" not in text and set(first) >= {"hours", "steps", "writes_24h", "start", "rules", "note"}
    assert first["rules"] == {"flow_k": 1.0, "dhw_k": 1.0, "hold_min": 5}
    # Stufen: jede Änderung genau, beginnend mit dem ersten Schritt
    assert first["steps"][0]["t"] == at(MON, 12).isoformat()


def test_start_state_counts_first_write_against_boiler(tmp_path):
    eng, _, _ = house(tmp_path)
    eng.found.boiler["sel_flow_temp"] = "sensor.sel_flow"
    eng.states["sensor.sel_flow"] = {"state": "45"}
    p = pv.preview(eng, ts_of(MON, 12))
    first = [w for w in p["hours"][0]["writes"] if w["what"] == "flow"]
    assert p["start"]["flow"] == 45.0 and first[0]["value"] == 29 and first[0]["from"] == 45.0
    # derselbe Wert steht schon am Kessel: kein Schreibvorgang
    p = pv.preview(eng, ts_of(MON, 12), written={"flow": 29, "heating": True, "dhw": None})
    assert not [w for w in p["hours"][0]["writes"] if w["what"] in ("flow", "heating")]
    assert p["written_next"]["flow"] == 29


# ------------------------------------------------------------------ Steuerung: Vorhersage beim Vorheizen
def test_desired_preheat_uses_outdoor_at_target_time():
    cfg = Config(control_mode="heatpilot")
    m = slow_room()
    base = desired(wz(), at(MON, 23), cfg, Presence(home_count=1), m, 19.0, 5.0)
    asked = []

    def cold(when):
        asked.append(when)
        return -10.0

    colder = desired(wz(), at(MON, 23), cfg, Presence(home_count=1), m, 19.0, 5.0, cold)
    assert base.phase == colder.phase == "nacht"
    assert asked and all(t == at(TUE, 4, 30) for t in asked)  # gefragt wird nach dem Zielzeitpunkt
    # seit 0.46.5 sanft: 19 → 21 °C braucht in beiden Fällen fast den ganzen Deckel (3 h) – kälter heißt dann ein
    # höherer Vorlauf, nicht früher; der Beginn rechnet mit −10 °C
    end = at(TUE, 4, 30)
    assert end - colder.until == timedelta(hours=preheat_hours(m, 19.0, 21.0, cfg.max_flow_temp, -10.0, 3.0))
    assert end - base.until == timedelta(hours=preheat_hours(m, 19.0, 21.0, cfg.max_flow_temp, 5.0, 3.0))
    # reicht bei 5 °C schon der niedrigste Vorlauf (35 °C) in kürzerer Zeit, beginnt es bei Kälte früher
    near = desired(wz(), at(MON, 23), cfg, Presence(home_count=1), m, 20.8, 5.0)
    near_cold = desired(wz(), at(MON, 23), cfg, Presence(home_count=1), m, 20.8, 5.0, cold)
    assert end - near_cold.until == timedelta(hours=preheat_hours(m, 20.8, 21.0, cfg.max_flow_temp, -10.0, 3.0))
    # Vorhersage ohne Punkt: wie bisher mit der aktuellen Außentemperatur; Deckel bleibt
    assert desired(wz(), at(MON, 23), cfg, Presence(home_count=1), m, 19.0, 5.0, lambda w: None) == base
    capped = desired(wz(), at(MON, 23), cfg, Presence(home_count=1), m, 15.0, 5.0, lambda w: -20.0)
    assert capped.until == at(TUE, 4, 30) - timedelta(hours=cfg.preheat_max_h)
    # Ankunft: Außentemperatur zur Ankunft (Raum 20,8 °C wie oben: bei 5 °C reicht der niedrigste Vorlauf) – ganz
    # bis Komfort (seit 0.49 reicht zur Ankunft sonst Komfort − 1 K, die hat der Raum schon)
    cfg.arrival_margin_k = 0.0
    away = Presence(home_count=0, arrival=at(MON, 17))
    warm = desired(wz(), at(MON, 12), cfg, away, m, 20.8, 5.0)
    cold_arr = desired(wz(), at(MON, 12), cfg, away, m, 20.8, 5.0, lambda w: -10.0 if w == at(MON, 17) else None)
    assert cold_arr.until != warm.until  # 0.50.4: kälter → höherer Vorlauf, Soll weiter in ≈ 1 h


def test_control_loop_passes_forecast(tmp_path):
    # Raum 20,8 °C: bei 5 °C reicht der niedrigste Vorlauf in kürzerer Zeit als dem Deckel – kälter beginnt dann früher
    # (seit 0.46.5 sanft; bei 19 °C bräuchte es in beiden Fällen fast den ganzen Deckel)
    eng, ha, loop = house(tmp_path, temp=19.5, weather="weather.home")  # 0.50.5: muss vorheizen
    ts = ts_of(MON, 23)
    sample, rooms, _ = eng.snapshot()
    loop.plan(ts, sample, rooms)
    plain = loop.rooms["climate.wz"]["next_change"]
    eng.forecast = forecast(int(at(TUE, 4).timestamp()), [-10.0, -10.0])  # kalt ums Nachtende
    loop.plan(ts, sample, rooms)
    assert loop.rooms["climate.wz"]["next_change"] != plain  # mit −10 °C gerechnet
    run(loop.apply())
    assert all(c[1] == "set_temperature" for c in ha.calls)  # nur Tado, nie der Kessel


def test_fri_night_plan_respected(tmp_path):
    eng, _, _ = house(tmp_path)
    h = by_hour(pv.preview(eng, ts_of(FRI, 20)))
    assert h["21:00"]["phases"]["Wz"] == "komfort" and h["23:00"]["phases"]["Wz"] == "nacht"  # Fr: Beginn 22:00
