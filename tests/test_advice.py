"""Empfehlungen an Tado: Zeitplan/Frühstart, Geofencing, Abwesenheit, Soll nicht erreichbar."""

from datetime import datetime, timedelta

from heatpilot import advice
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.models import RoomModel
from heatpilot.store import Store

START = datetime(2026, 10, 1, 0, 0)
DAYS = 14
STEP = 300


def scenario(tmp_path, vacation=False, fine=False):
    """Zwei Personen, werktags 8–17 Uhr weg. Fünf Räume mit je einem typischen Problem.

    ``fine=True``: alles richtig eingestellt – dann darf nichts empfohlen werden.
    """
    rooms = ["climate.bad", "climate.buero", "climate.wz", "climate.kueche", "climate.schlaf"]
    found = Discovered(rooms=[Room(r.split(".")[1].title(), r) for r in rooms], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "a.db"), found)
    k = eng.rooms["climate.kueche"]  # plausibles Modell: kann nach Absenkung schnell aufheizen
    k.rls.theta, k.rls.updates, k.excitation = [0.06, 0.04, 0.0], 500, 100.0
    t0 = int(START.timestamp())
    for i in range(DAYS * 288):
        ts = t0 + i * STEP
        d = datetime.fromtimestamp(ts)
        m = d.hour * 60 + d.minute
        weekday = d.weekday() < 5
        home = not (weekday and 8 * 60 <= m < 17 * 60)
        eng.store.add_person_samples([(ts, "person.a", int(home), 0.0 if home else 12.0)])
        eng.store.add_sample(ts, {"flow": 60.0, "vacation": int(vacation)})
        rows = []
        # Bad: werktags 06:30 Soll 18 -> 22; ohne Frühstart erst 45 min später warm
        morning = weekday and 6 * 60 + 30 <= m < 8 * 60
        if fine:
            rows.append((ts, "climate.bad", 22.0 if morning else 18.0, 22.0 if morning else 18.0, 0.0, None))
        else:
            warm = min(18.0 + max(m - 390, 0) * 0.09, 22.0) if morning else 18.0
            rows.append((ts, "climate.bad", warm, 22.0 if morning else 18.0, 100.0 if morning else 0.0, None))
        # Büro: Frühstart heizt schon ab 05:30, Raum ab 05:40 warm – Block 06:30 -> ~50 min zu früh
        # (gut eingestellt: Frühstart ab 06:05, warm ab 06:20)
        e0, w0 = (6 * 60 + 5, 6 * 60 + 20) if fine else (5 * 60 + 30, 5 * 60 + 40)
        early = weekday and e0 <= m < 6 * 60 + 30
        temp_b = 22.0 if (weekday and w0 <= m < 8 * 60) else 18.0
        rows.append((ts, "climate.buero", temp_b, 22.0 if morning else 18.0, 100.0 if early else 0.0, None))
        # Wohnzimmer: Geofencing hebt Soll erst 5 min vor Ankunft an -> bei Ankunft 1,2 K zu kalt
        geo_rise = weekday and 16 * 60 + 55 <= m < 17 * 60 + 30
        if fine:
            rows.append((ts, "climate.wz", 21.0, 21.0 if home or geo_rise else 18.0, 0.0, None))
        else:
            wz_temp = 19.8 if (weekday and 17 * 60 <= m < 17 * 60 + 15) else (21.0 if home else 18.5)
            rows.append((ts, "climate.wz", wz_temp, 21.0 if (home or geo_rise) else 18.0, 50.0, None))
        # Küche: hält bei Abwesenheit 20,5 °C statt 19
        rows.append((ts, "climate.kueche", 20.5 if not home else 21.0,
                     (19.0 if fine else 20.5) if not home else 21.0, 30.0, None))
        # Schlafzimmer: abends 3 h voll offen und trotzdem 1,5 K unter Soll
        evening = 19 * 60 <= m < 22 * 60 and not fine
        rows.append((ts, "climate.schlaf", 19.5 if evening else 21.0, 21.0, 100.0 if evening else 20.0, None))
        eng.store.add_room_samples(rows)
    eng.store.commit()
    return eng, t0 + DAYS * 86400


def by_kind(items):
    return {(a["room"], a["kind"]): a for a in items}


def test_each_problem_gets_one_concrete_recommendation(tmp_path):
    eng, now = scenario(tmp_path)
    items = by_kind(advice.build(eng, now))
    bad = items[("climate.bad", "frühstart")]
    assert "Frühstart" in bad["text"] and "06:30" in bad["title"]
    assert bad["evidence"]["Beobachtungen"] >= 4 and bad["evidence"]["Verspätung Median (min)"] >= 40
    assert "um 30 min vorziehen" in bad["text"]  # gedeckelt: max. 30 min auf einmal
    assert bad["evidence"]["Frühstart erkannt (%)"] == 0
    buero = items[("climate.buero", "zeitplan")]
    assert buero["impact"] == "Sparen" and "später" in buero["text"]
    wz = items[("climate.wz", "geofencing")]
    assert "Komfort" in wz["text"] and wz["evidence"]["zu kalt bei Ankunft Median (K)"] >= 1.0
    assert wz["evidence"]["Soll-Anhebung vor Ankunft Median (min)"] == 5
    kueche = items[("climate.kueche", "abwesenheit")]
    assert "19 °C senken" in kueche["text"] and "wieder auf 21" in kueche["text"]
    schlaf = items[("climate.schlaf", "nicht_erreichbar")]
    assert "Vorlauf war dabei am Limit" not in schlaf["text"]  # Vorlauf 60 < max 75
    assert "Heizkurve" not in schlaf["text"]  # 60 °C ist nicht auffällig niedrig
    assert [k for r, k in items if r == "climate.schlaf"] == ["nicht_erreichbar"]
    assert "erster Schritt" in bad["text"]  # 45 min zu spät, nur 30 min auf einmal
    assert len(items) == 5


def test_nothing_to_recommend_when_set_up_well(tmp_path):
    eng, now = scenario(tmp_path, fine=True)
    assert advice.build(eng, now) == []


def test_vacation_is_ignored(tmp_path):
    eng, now = scenario(tmp_path, vacation=True)
    kinds = {a["kind"] for a in advice.build(eng, now)}
    assert not kinds & {"frühstart", "zeitplan", "geofencing", "abwesenheit"}


def test_away_advice_only_if_room_can_recover(tmp_path):
    eng, now = scenario(tmp_path)
    eng.rooms["climate.kueche"].rls.theta = [0.002, 0.04, 0.0]  # schwacher Heizkörper
    assert ("climate.kueche", "abwesenheit") not in by_kind(advice.build(eng, now))


def test_tracking_remembers_and_reports_resolved(tmp_path):
    eng, now = scenario(tmp_path)
    first = advice.build(eng, now)
    assert advice.track(eng.store, first, now) == []
    later = [a for a in advice.build(eng, now) if a["room"] != "climate.wz"]
    resolved = advice.track(eng.store, later, now + 7 * 86400)
    assert [r["title"] for r in resolved] == ["Wz: bei Ankunft zu kalt"]
    assert all(a["since"] == now for a in later)


def test_geofencing_eco_only_after_real_preheat():
    """Soll 90 min vor Ankunft angehoben, Raum nach 30 min warm -> 60 min zu früh warm."""
    a = int(datetime(2026, 10, 5, 17, 0).timestamp())
    series = []
    for i in range(-48, 6):
        ts = a + i * STEP
        mins = i * 5
        target = 21.0 if mins >= -90 else 18.0
        temp = 21.0 if mins >= -60 else (19.5 if mins >= -90 else 18.0)
        series.append((ts, temp, target, 50.0))
    ev = advice.arrival_events(series, [a])[0]
    assert ev["lead_min"] == 90 and ev["warm_ahead_min"] == 60 and ev["deficit"] == 0.0
    # nicht abgesenkt (Soll immer 21, Raum immer warm): kein "zu früh warm"
    flat = [(ts, 21.0, 21.0, 0.0) for ts, *_ in series]
    ev = advice.arrival_events(flat, [a])[0]
    assert ev["lead_min"] is None and ev["warm_ahead_min"] == 0.0


def test_room_that_never_warms_gets_only_capacity_hint(tmp_path):
    """Soll-Anstieg 06:30, Raum bleibt bei 18 °C (Ventil offen): kein 'Block vorziehen'."""
    found = Discovered(rooms=[Room("Kalt", "climate.kalt")], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "k.db"), found)
    t0 = int(START.timestamp())
    for i in range(DAYS * 288):
        ts = t0 + i * STEP
        d = datetime.fromtimestamp(ts)
        m = d.hour * 60 + d.minute
        day = 6 * 60 + 30 <= m < 22 * 60
        eng.store.add_person_samples([(ts, "person.a", 1, 0.0)])
        eng.store.add_sample(ts, {"flow": 74.0})
        eng.store.add_room_samples([(ts, "climate.kalt", 18.0, 21.0 if day else 17.0, 100.0 if day else 0.0, None)])
    eng.store.commit()
    items = advice.build(eng, t0 + DAYS * 86400)
    assert [a["kind"] for a in items] == ["nicht_erreichbar"]
    assert "Vorlauf war dabei am Limit" in items[0]["text"]


def test_low_flow_points_to_heating_curve(tmp_path):
    found = Discovered(rooms=[Room("Kalt", "climate.kalt")], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "c.db"), found)
    t0 = int(START.timestamp())
    for i in range(7 * 288):
        ts = t0 + i * STEP
        m = datetime.fromtimestamp(ts).hour * 60
        day = 6 * 60 <= m < 22 * 60
        eng.store.add_person_samples([(ts, "person.a", 1, 0.0)])
        eng.store.add_sample(ts, {"flow": 38.0})
        eng.store.add_room_samples([(ts, "climate.kalt", 19.5, 21.0 if day else 17.0, 100.0 if day else 0.0, None)])
    eng.store.commit()
    text = advice.build(eng, t0 + 7 * 86400)[0]["text"]
    assert "Heizkurve des Reglers" in text and "38 °C" in text and "nur ändern, wenn du sicher bist" in text


def balance_engine(tmp_path, weak_valve=100.0, strong_valve=20.0, weak_gain=0.022):
    """Fünf Räume: drei normal, einer mit zu wenig Heizwasser, einer mit zu viel."""
    gains = {"climate.a": 0.035, "climate.b": 0.034, "climate.c": 0.036,
             "climate.schwach": weak_gain, "climate.stark": 0.07}
    found = Discovered(rooms=[Room(c.split(".")[1].title(), c) for c in gains], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "h.db"), found)
    for c, a in gains.items():
        eng.rooms[c].rls.theta, eng.rooms[c].rls.updates = [a, 0.05, 0.0], 500
        eng.rooms[c].excitation = 100.0
    t0 = int(START.timestamp())
    valves = {"climate.schwach": weak_valve, "climate.stark": strong_valve}
    for i in range(5 * 288):
        ts = t0 + i * STEP
        eng.store.add_person_samples([(ts, "person.a", 1, 0.0)])
        eng.store.add_sample(ts, {"flow": 55.0})
        eng.store.add_room_samples([(ts, c, 21.0, 21.0, valves.get(c, 50.0), None) for c in gains])
    eng.store.commit()
    return eng, t0 + 5 * 86400


def test_hydraulic_balance_finds_under_and_oversupplied_rooms(tmp_path):
    eng, now = balance_engine(tmp_path)
    items = by_kind(advice.build(eng, now))
    under = items[("climate.schwach", "abgleich")]
    assert "zu wenig Heizwasser" in under["title"] and "eine Stufe höher" in under["text"]
    assert "bestimmt den Vorlauf fürs ganze Haus" in under["text"]
    assert under["evidence"]["nötiger Vorlauf am kältesten Tag (°C)"] > under["evidence"]["Median aller Räume (°C)"] + 8
    over = items[("climate.stark", "abgleich")]
    assert "mehr Heizwasser als nötig" in over["title"] and "eine Stufe zurück" in over["text"]
    assert not any(k == "abgleich" for r, k in items if r in ("climate.a", "climate.b", "climate.c"))


def test_no_throttling_without_starved_room(tmp_path):
    # schwacher Raum, aber Ventil kaum offen: kein Beleg für Wassermangel -> auch nicht drosseln
    eng, now = balance_engine(tmp_path, weak_valve=30.0)
    assert not [a for a in advice.build(eng, now) if a["kind"] == "abgleich"]


def test_balance_skips_floor_heating_and_too_few_rooms(tmp_path):
    eng, now = balance_engine(tmp_path)
    eng.cfg.floor_heating_rooms = ["climate.schwach", "climate.a", "climate.b"]
    assert not [a for a in advice.build(eng, now) if a["kind"] == "abgleich"]


def test_schedule_with_off_blocks_is_evaluated(tmp_path):
    """Tado-Zeitplan „aus“ bis 16:00, dann 21 °C – Raum erst ~1 h später warm: Frühstart-Empfehlung."""
    found = Discovered(rooms=[Room("Wz", "climate.wz")], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "o.db"), found)
    t0 = int(START.timestamp())
    for i in range(DAYS * 288):
        ts = t0 + i * STEP
        d = datetime.fromtimestamp(ts)
        m = d.hour * 60 + d.minute
        on = 16 * 60 <= m < 22 * 60
        temp = min(18.0 + max(m - 960, 0) * 0.05, 21.0) if on else 18.0
        eng.store.add_person_samples([(ts, "person.a", 1, 0.0)])
        eng.store.add_sample(ts, {"flow": 60.0})
        # „aus“: kein Soll (None), Ventil zu
        eng.store.add_room_samples([(ts, "climate.wz", temp, 21.0 if on else None, 100.0 if on and temp < 21 else 0.0, None)])
    eng.store.commit()
    items = by_kind(advice.build(eng, t0 + DAYS * 86400))
    late = [a for (r, k), a in items.items() if r == "climate.wz" and k in ("frühstart", "zeitplan")]
    assert late and "16:00" in late[0]["title"] and late[0]["evidence"]["Verspätung Median (min)"] >= 40


def test_room_warm_anyway_is_not_early_heating(tmp_path):
    """Tado „aus“ bis 05:00, dann 18 °C – der Raum hat ohnehin 19,5 °C und das Ventil bleibt zu.

    Das ist kein unnötiges Vorheizen: der Zeitplanblock ist nur ein Soll, es fließt keine Wärme.
    """
    found = Discovered(rooms=[Room("Arbeitszimmer", "climate.az")], persons=["person.a"])
    eng = Engine(Config(), Store(tmp_path / "w.db"), found)
    t0 = int(START.timestamp())
    for i in range(DAYS * 288):
        ts = t0 + i * STEP
        d = datetime.fromtimestamp(ts)
        m = d.hour * 60 + d.minute
        block = d.weekday() < 5 and 5 * 60 <= m < 8 * 60
        eng.store.add_person_samples([(ts, "person.a", 1, 0.0)])
        eng.store.add_sample(ts, {"flow": 45.0})
        eng.store.add_room_samples([(ts, "climate.az", 19.5, 18.0 if block else None, 0.0, None)])
    eng.store.commit()
    items = by_kind(advice.build(eng, t0 + DAYS * 86400))
    assert ("climate.az", "zeitplan") not in items and ("climate.az", "frühstart") not in items
