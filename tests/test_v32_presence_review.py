"""0.32 Gegenprüfung Anwesenheit (daten:5): Tracker „unknown“/„unavailable“ darf den Haushalt
nicht als leer zählen – home_count wird dann None (offen), nicht 0."""
from datetime import datetime

from heatpilot.config import Config
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store


def _engine(tmp_path, persons):
    eng = Engine(Config(), Store(tmp_path / "p.db"), Discovered(persons=persons))
    m = eng.dhw  # Modell als gelernt markieren, damit es eine Empfehlung gibt
    m.kwh_per_k, m.kwh_per_k_n = {"kalt": 0.2, "warm": None}, {"kalt": 10, "warm": 0}
    m.charges, m.loss.updates = 10, 500
    m.loss.theta = [0.29, -0.0145]
    m.comfort_margin = 3.0  # Reserve: Boden liegt über dem Minimum
    return eng


def _set(eng, **states):
    eng.states = {f"person.{k}": {"state": v, "attributes": {}} for k, v in states.items()}


def test_tracker_outage_keeps_household_open_and_dhw_off_minimum(tmp_path):
    eng = _engine(tmp_path, ["person.a"])
    ts = int(datetime(2026, 10, 12, 18, 0).timestamp())
    cfg = eng.cfg
    seen = []
    for i, st in enumerate(["home"] * 3 + ["unavailable"] * 3):
        _set(eng, a=st)
        t = ts + i * 300
        sample, _, persons = eng.snapshot()
        eng.observe_persons(t, persons, False)
        sample["dhw_set"] = 55.0
        check, (hold, target) = eng.dhw_recommend(t, sample)
        assert check["ok"]
        seen.append((sample["home_count"], eng.dhw_rec["reason"], hold))
    assert [h for h, _, _ in seen] == [1, 1, 1, None, None, None]
    assert eng.persons["person.a"].home is True  # Learner behält den letzten bekannten Zustand
    for _, reason, hold in seen[3:]:
        assert not reason.startswith("niemand zu Hause")
        # seit 0.46: Anwesenheit offen, wach – Komfort (+ Reserve), nicht Minimum
        assert hold >= cfg.dhw_comfort_temp > cfg.dhw_min_temp


def test_home_count_rules_with_two_persons(tmp_path):
    eng = _engine(tmp_path, ["person.a", "person.b"])
    _set(eng, a="home", b="unknown")
    assert eng.snapshot()[0]["home_count"] == 1  # Bekannte Anwesende zählen
    _set(eng, a="home", b="home")
    assert eng.snapshot()[0]["home_count"] == 2
    _set(eng, a="unknown", b="unknown")
    assert eng.snapshot()[0]["home_count"] is None  # alle unbekannt: offen
    _set(eng, a="not_home", b="unavailable")
    assert eng.snapshot()[0]["home_count"] is None  # einer weg, einer unbekannt: offen
    _set(eng, a="not_home")  # b fehlt ganz in HA: ebenso unbekannt
    assert eng.snapshot()[0]["home_count"] is None
    _set(eng, a="not_home", b="not_home")
    assert eng.snapshot()[0]["home_count"] == 0  # alle bekannt weg


def test_note_household_skips_open_household(tmp_path):
    eng = _engine(tmp_path, ["person.a"])
    eng.dhw.note_household(0, False)
    assert eng.dhw._backup is not None  # leer: Zapfprofil gesichert
    eng.dhw.note_household(None, False)
    assert eng.dhw._backup is not None  # offen: nichts verändert
    eng.dhw.note_household(1, False)
    assert eng.dhw._backup is None


def test_recount_home_uses_same_rule(tmp_path):
    store = Store(tmp_path / "r.db")
    store.add_person_samples([
        (100, "person.a", 1, None), (100, "person.b", None, None),  # einer zu Hause, einer unbekannt
        (200, "person.a", 0, None), (200, "person.b", None, None),  # einer weg, einer unbekannt
        (300, "person.a", None, None), (300, "person.b", None, None),  # alle unbekannt
        (400, "person.a", 0, None), (400, "person.b", 0, None),  # alle weg
        (500, "person.a", 1, None), (500, "person.b", 1, None),  # alle da
    ])
    for t in (100, 200, 300, 400, 500):
        store.add_sample(t, {"home_count": 0})
    store.add_hourly([(0, 5.0, 20.0, 1.0, 0.0, 0.0)])
    store.commit()
    store.recount_home(["person.a", "person.b"])
    rows = {r["ts"]: r["home_count"] for r in store.query("SELECT ts, home_count FROM samples")}
    assert rows == {100: 1, 200: None, 300: None, 400: 0, 500: 2}
    # Stundenanteil nur aus den bekannten Messpunkten: 2 von 3 mit jemandem zu Hause
    assert abs(store.query("SELECT home_frac FROM hourly")[0]["home_frac"] - 2 / 3) < 1e-9
    store.recount_home(["person.a"])  # nur A gewählt: ihr Zustand allein zählt
    rows = {r["ts"]: r["home_count"] for r in store.query("SELECT ts, home_count FROM samples")}
    assert rows == {100: 1, 200: 0, 300: None, 400: 0, 500: 1}
