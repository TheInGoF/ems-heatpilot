"""0.39 Ventilverhalten lernen (Stufe 1): Kennlinie, Sprungantwort, PI-Modell, Schaltschwellen, Belastbarkeit.

Geprüft mit künstlichen Thermostaten (``sim_valve``) bekannter Eigenschaften – stetig (P bzw. PI mit Totband, Totzeit,
Stellmotor, Sättigung) und nur an/aus: Der Lerner trifft die Kennlinie (e50 ±0,1 K) und die Sprungzeiten (±2 min),
Rauschen, Fremdwärme, Fenster und Heizbetrieb aus werden ausgelassen, zu wenig Daten heißt „lernt“. Dazu Nachlernen
aus der Historie in Abschnitten, Gruppen, Schrittweite je Gerät, Speichern und Modellstand.
"""

import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pytest
from heatpilot import __version__
from heatpilot import valve_learn as vl
from heatpilot import valve_model as vm
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.store import Store
from sim_valve import Room as SimRoom
from sim_valve import Scenario, Thermostat, simulate, store_rows

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
C = "climate.wz"
# Mitternacht UTC, nicht Ortszeit: der Simulator plant Tag/Nacht in UTC-Stunden – so lernen die Modelle in jeder
# Zeitzone dieselben Daten (sonst liegen z. B. Sprungzeiten mit TZ=Europe/Berlin auf ,5 und runden anders)
START = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())


def learn(th: Thermostat, sc: Scenario | None = None, tmp=None, events=(), binary_dev=None, room=None):
    """Simulieren, speichern wie HeatPilot, abschnittweise lernen. (Modell, Wahrheit je Minute, Store)."""
    sc = sc or Scenario()
    rows, truth = simulate(th, room or SimRoom(), sc, START)
    store = Store(Path(tmp) / "v.db")
    store_rows(store, C, rows)
    for a, b in events:
        store.add_room_event(a, b, C, 1.0)
    for m0, dur in sc.heating_off:
        store.db.execute("UPDATE samples SET hc_day = 0 WHERE ts >= ? AND ts < ?",
                         (START + m0 * 60, START + (m0 + dur) * 60))
    store.commit()
    model = vm.ValveModel(binary_dev=th.binary if binary_dev is None else binary_dev)
    until = rows[-1][0] + 300
    while vl.learn_chunk(store, model, C, until):
        pass
    return model, truth, store


def truth_jumps(truth: list[tuple]) -> dict:
    """Sprungzeiten aus der Wahrheit (1 min, genauer Sprungzeitpunkt) – dieselbe Festlegung wie der Lerner."""
    out = {"up": [], "down": []}
    for m in range(1, len(truth)):
        if abs(truth[m][2] - truth[m - 1][2]) < vm.JUMP_K - 1e-6:
            continue
        up, v0 = truth[m][2] > truth[m - 1][2], truth[m - 1][3]
        pts = [(0, 0.0)]
        for k in range(m, min(m + 91, len(truth))):
            if abs(truth[k][2] - truth[m][2]) >= 0.05:
                break
            pts.append((k - m + 1, (1 if up else -1) * (truth[k][3] - v0)))
        full = max(p[1] for p in pts)
        if pts[-1][0] < 30 or full < vm.RESP_MIN_PCT:
            continue
        dead = next(t for t, v in pts if v >= vm.MOVE_PCT)
        t63 = next(t for t, v in pts if v >= 0.63 * full)
        out["up" if up else "down"].append((dead, t63))
    return {d: (statistics.median(x[0] for x in v), statistics.median(x[1] for x in v)) for d, v in out.items()}


def truth_curve(truth: list[tuple]) -> list[tuple[float, float]]:
    """Kennlinie aus der Wahrheit: Median je 0,1-K-Klasse in Beharrung (30 min ohne Soll-Wechsel), monoton geglättet."""
    by: dict[int, list] = {}
    last_change = -10**9
    for m in range(1, len(truth)):
        if abs(truth[m][2] - truth[m - 1][2]) >= 0.05:
            last_change = m
        if m - last_change >= 30:
            by.setdefault(vm.e_class(truth[m][2] - truth[m][1]), []).append(truth[m][3])
    pts = [(vm.class_e(i), statistics.median(v), len(v) / 60) for i, v in sorted(by.items()) if len(v) >= 30]
    return vm.isotonic(pts)


# ------------------------------------------------------------------ Kennlinie und Sprungantwort
def test_p_thermostat_curve_and_step_times(tmp_path):
    th = Thermostat(kp=100, ki=0, deadband=0.1, dead_min=4, tau_up_min=6, tau_down_min=10)
    m, truth, _ = learn(th, tmp=tmp_path)
    p = m.points()
    assert m.kind == "stetig"
    assert abs(p["e50"] - 0.6) <= 0.1  # Totband 0,1 K + 50 % / 100 %/K
    assert abs(p["e0"] - 0.15) <= 0.1 and p["deadband_k"] == round(max(p["e0"], 0), 2)
    assert p["e100"] is not None and 0.8 <= p["e100"] <= 1.1
    want = truth_jumps(truth)
    got = m.response()
    for d in ("up", "down"):
        assert got[d]["n"] >= 8
        assert abs(got[d]["dead_min"] - want[d][0]) <= 2, (d, got[d], want[d])
        assert abs(got[d]["t63_min"] - want[d][1]) <= 2, (d, got[d], want[d])
    assert got["down"]["t63_min"] > got["up"]["t63_min"]  # der Stellmotor schließt langsamer
    kp, ki, _ = m.gains()
    assert 60 <= kp <= 130 and ki < 0.1  # P-Regler: Kp ≈ 100 %/K, kein Integrator
    err30, n30 = m.error(30)
    assert n30 >= vm.ERR_MIN_N and err30 <= vm.RELIABLE_ERR
    ok, reasons = m.reliable()
    assert ok and reasons == []


def test_pi_thermostat_matches_truth_curve(tmp_path):
    th = Thermostat(kp=60, ki=0.5, deadband=0.1, dead_min=3, tau_up_min=4, tau_down_min=8)
    m, truth, _ = learn(th, Scenario(seed=1), tmp=tmp_path)
    want = dict(truth_curve(truth))
    assert abs(m.points()["e50"] - vm.crossing(sorted(want.items()), vm.HALF_PCT)) <= 0.1
    # der Integrator hält das Ventil auch über dem Soll offen: die Kennlinie ist flach – über alle Klassen vergleichen
    diffs = [abs(p["valve"] - want[p["e"]]) for p in m.curve() if p["e"] in want]
    assert len(diffs) >= 15 and statistics.mean(diffs) <= 10
    assert m.points()["e0"] < 0  # öffnet schon über dem Soll
    jumps, got = truth_jumps(truth), m.response()
    for d in ("up", "down"):
        assert abs(got[d]["t63_min"] - jumps[d][1]) <= 2, (d, got[d], jumps[d])
    kp, ki, e_i = m.gains()
    assert kp > 20 and 0.1 < ki < 1.5 and abs(e_i) <= vm.I_ZERO_MAX
    assert m.reliable()[0]
    # Vorhersage: Raum nähert sich dem Soll – der Integrator hält das Ventil teilweise offen
    assert m.predict_path(60.0, 0.5, 15) > 0


def test_other_step_sizes_and_strong_valve(tmp_path):
    """Allgemein: steilere Kennlinie, schnellerer Stellmotor, andere Zeiten – kein Wert ist fest eingebaut."""
    th = Thermostat(kp=250, ki=0, deadband=0.0, dead_min=1, tau_up_min=2, tau_down_min=3)
    m, truth, _ = learn(th, Scenario(seed=3, step_s=180), tmp=tmp_path)
    assert abs(m.points()["e50"] - 0.2) <= 0.1  # 50 % / 250 %/K
    want, got = truth_jumps(truth), m.response()
    assert abs(got["up"]["t63_min"] - want["up"][1]) <= 2


def test_binary_thresholds(tmp_path):
    th = Thermostat(binary=True, e_on=0.3, e_off=-0.1, dead_min=2)
    m, _, _ = learn(th, tmp=tmp_path)
    assert m.kind == "binär"
    e_on, e_off = m.switch_e()
    assert abs(e_on - 0.3) <= 0.1 and abs(e_off + 0.1) <= 0.1
    assert m.step(0.0, 0.0, 0.5, 5) == 100.0 and m.step(100.0, 0.0, -0.3, 5) == 0.0
    assert m.step(100.0, 0.0, 0.1, 5) == 100.0 and m.step(0.0, 0.0, 0.1, 5) == 0.0  # Hysterese
    assert m.reliable()[0]
    s = m.summary(0.5, "gerät")
    assert s["text"].startswith("schaltet ein bei 0,3 K unter Soll, aus bei 0,1 K über Soll")
    assert m.open_at() == e_on and s["switch"]["e_on"] == e_on


def test_binary_detected_from_data_with_sensor(tmp_path):
    """Ein Ventilsensor, der nur 0 und 100 % meldet: nach genug Messwerten „binär“, ohne Neulernen."""
    m, _, _ = learn(Thermostat(binary=True, e_on=0.3, e_off=-0.1), tmp=tmp_path, binary_dev=False)
    assert not m.binary_dev and m.valve_n >= vm.BINARY_MIN_N and m.kind == "binär"


def test_noise_ext_window_and_heating_off_are_skipped(tmp_path):
    th = Thermostat(kp=100, ki=0, deadband=0.1)
    day = 1440
    windows = [(d * day + 9 * 60 + 17, 20) for d in range(6)]          # täglich Stoßlüften, Ventil zu
    ext = [(d * day + 19 * 60, 120, 1.2) for d in (1, 3, 5)]             # Kaminabende
    off = [(2 * day + 13 * 60, 180)]                                     # Heizbetrieb 3 h aus
    sc = Scenario(seed=11, noise_k=0.05, windows=windows, ext=ext, heating_off=off)
    events = [(START + m0 * 60, START + (m0 + dur) * 60) for m0, dur, _ in ext]
    m, _, _ = learn(th, sc, tmp=tmp_path, events=events)
    assert abs(m.points()["e50"] - 0.6) <= 0.1
    sk = m.skipped_h
    assert sk["fenster"] >= 6 * 0.75 and sk["fremdwaerme"] >= 3 * 1.5 and sk["heizung_aus"] >= 2.5


def test_windows_would_distort_without_detection(tmp_path, monkeypatch):
    """Gegenprobe: Stoßlüften schließt das Ventil bei großer Regelabweichung – erkannt fehlt es in der Kennlinie."""
    th = Thermostat(kp=100, ki=0, deadband=0.1)
    windows = [(d * 1440 + h * 60 + 7, 30) for d in range(6) for h in (8, 12, 16, 20)]
    sc = Scenario(seed=5, windows=windows)
    good, _, _ = learn(th, sc, tmp=tmp_path)
    monkeypatch.setattr(vl, "WINDOW_DROP_K", 99.0)
    (tmp_path / "b").mkdir()
    bad, _, _ = learn(th, sc, tmp=tmp_path / "b")
    assert abs(good.points()["e50"] - 0.6) <= 0.1
    assert good.skipped_h["fenster"] > 0 and bad.skipped_h["fenster"] == 0
    def closed_share(mod) -> float:
        """Anteil „Ventil zu“ in den Klassen ab 0,9 K unter Soll (dort ist es sonst offen)."""
        cells = mod.hist[vm.e_class(0.9):]
        return sum(b[0] for b in cells) / sum(sum(b) for b in cells)

    assert closed_share(bad) > closed_share(good) + 0.05


def test_too_little_data_learns_with_reasons(tmp_path):
    m, _, _ = learn(Thermostat(), Scenario(days=1, extra_jumps=1), tmp=tmp_path)
    ok, reasons = m.reliable()
    assert not ok
    assert any(r.startswith("erst ") and "Soll-Sprüngen" in r for r in reasons)
    assert any("h Beharrung" in r for r in reasons)
    s = m.summary(0.5, "angenommen")
    assert s["state"] == "lernt" and s["text"].endswith(f"lernt ({'; '.join(reasons)})")
    assert "Schritt 0,5 K (angenommen)" in s["text"]
    empty = vm.ValveModel()
    assert empty.step(50, 0.1, 0.1, 5) is None and empty.predict_path(50, 0.5, 10) is None
    assert empty.points() == {"e0": None, "e50": None, "e100": None, "deadband_k": None}
    assert "Kennlinie lernt noch" in empty.summary(0.5, "gerät")["text"]


def test_summary_fields_and_text(tmp_path):
    m, _, _ = learn(Thermostat(), tmp=tmp_path)
    s = m.summary(0.5, "gerät", now_ts=START + 6 * 86400)
    for key in ("kind", "state", "reliable", "reasons", "curve", "e0", "e50", "e100", "deadband_k", "response",
                "jumps", "steady_h", "pi", "switch", "error", "skipped_h", "step_k", "step_source", "catching_up",
                "learned_until", "rules", "text"):
        assert key in s, key
    assert s["state"] == "belastbar" and s["curve"][0]["e"] == -1.0
    assert all(a["valve"] <= b["valve"] for a, b in zip(s["curve"], s["curve"][1:]))  # monoton
    assert s["text"].startswith("öffnet ab 0,2 K unter Soll, halb offen bei 0,6 K, ganz bei ")
    assert " · reagiert auf +0,5 K in ≈ " in s["text"] and ", schließt in ≈ " in s["text"]
    assert s["text"].endswith(" · Schritt 0,5 K · belastbar")
    assert vm.text({**s, "e0": -0.3, "e50": -0.05, "e100": None, "step_k": 0.1}).startswith(
        "öffnet ab 0,3 K über Soll, halb offen bei 0,1 K über Soll · ")  # über Soll offen (PI-Regler)
    assert "Schritt 0,1 K" in vm.text({**s, "step_k": 0.1}) and "Schritt 0,25 K" in vm.text({**s, "step_k": 0.25})


# ------------------------------------------------------------------ Nachlernen, Speichern
def engine(tmp_path, rooms, states=None):
    eng = Engine(Config(), Store(tmp_path / "e.db"), Discovered(rooms=rooms))
    eng.states.update(states or {})
    return eng


def test_catch_up_from_history_in_chunks_then_live(tmp_path):
    rows, _ = simulate(Thermostat(), SimRoom(), Scenario(days=4), START)
    room = Room("Wz", C, heating_power="sensor.wz_heat")
    eng = engine(tmp_path, [room])
    store_rows(eng.store, C, rows[: len(rows) - 72])  # die letzten 6 h kommen „live“
    eng._last_room_step = rows[len(rows) - 72][0] - rows[len(rows) - 72][0] % 1800
    assert eng.valves[C].learned_until is None
    assert eng.learn_valves(budget_s=0.0) is True  # ein Abschnitt (≤ 1 Tag), dann ist das Budget aufgebraucht
    m = eng.valves[C]
    assert m.learned_until - rows[0][0] <= vl.CHUNK_S
    info = eng.valve_info(C)
    assert info["catching_up"] and info["reasons"][0] == "lernt aus der gespeicherten Historie nach"
    assert eng.learn_valves(budget_s=None) is False and m.learned_until == eng._last_room_step
    jumps = sum(len(v) for v in m.jumps.values())
    # live: weitere Messpunkte, je abgeschlossenem Raumschritt
    store_rows(eng.store, C, rows[len(rows) - 72:])
    eng._last_room_step = rows[-1][0] - rows[-1][0] % 1800
    assert eng.learn_valves() is False and m.learned_until == eng._last_room_step
    assert not eng.valve_info(C)["catching_up"]
    assert sum(len(v) for v in m.jumps.values()) >= jumps
    # nichts doppelt: erneutes Lernen bis zum selben Zeitpunkt ändert nichts
    before = m.to_dict()
    assert vl.learn_chunk(eng.store, m, C, eng._last_room_step) is False and m.to_dict() == before


def test_chunked_equals_one_pass(tmp_path):
    """Abschnitte von einem Tag oder von 30 min: dieselben Sprünge, praktisch dieselbe Kennlinie."""
    rows, _ = simulate(Thermostat(), SimRoom(), Scenario(days=4), START)
    store = Store(tmp_path / "c.db")
    store_rows(store, C, rows)
    until = rows[-1][0] - rows[-1][0] % 1800
    a, b = vm.ValveModel(), vm.ValveModel()
    while vl.learn_chunk(store, a, C, until):
        pass
    b.learned_until = rows[0][0]
    for step in range(rows[0][0] + 1800, until + 1, 1800):
        vl.learn_chunk(store, b, C, step)
    assert a.learned_until == b.learned_until == until
    assert a.jumps == b.jumps  # Sprünge hängen nicht vom Modell ab
    # Schaltpunkte nutzen die verzögerte Regelabweichung (Totzeit aus den Sprüngen bis dahin): nahezu gleich
    assert [len(v) for v in a.switches.values()] == [len(v) for v in b.switches.values()]
    assert all(abs(x - y) <= 0.05 for k in a.switches for x, y in zip(a.switches[k], b.switches[k]))
    assert abs(a.steady_h - b.steady_h) / a.steady_h < 0.03
    assert abs(a.points()["e50"] - b.points()["e50"]) <= 0.05


def test_saved_restart_version_and_kind_change(tmp_path):
    rows, _ = simulate(Thermostat(), SimRoom(), Scenario(days=2), START)
    room = Room("Wz", C, heating_power="sensor.wz_heat")
    eng = engine(tmp_path, [room])
    store_rows(eng.store, C, rows)
    eng._last_room_step = rows[-1][0] - rows[-1][0] % 1800
    eng.learn_valves(budget_s=None)
    eng.save()
    saved = eng.store.get(f"model.valve.{C}")
    assert saved["version"] == vm.VERSION and saved["learned_until"] == eng._last_room_step
    again = Engine(Config(), eng.store, Discovered(rooms=[room]))
    assert again.valves[C].to_dict() == eng.valves[C].to_dict()
    # anderer Modellstand: neu, lernt aus der gespeicherten Historie nach
    eng.store.put(f"model.valve.{C}", {**saved, "version": vm.VERSION + 1})
    assert Engine(Config(), eng.store, Discovered(rooms=[room])).valves[C].learned_until is None
    # Geräteart geändert (kein Ventilsensor mehr): neu
    eng.store.put(f"model.valve.{C}", saved)
    plain = Engine(Config(), eng.store, Discovered(rooms=[Room("Wz", C)]))
    assert plain.valves[C].binary_dev and plain.valves[C].learned_until is None
    # „Lernstand neu berechnen“ verwirft die Ventilmodelle, das Protokoll des Trockenlaufs bleibt
    eng.store.add_valve_dry({"ts": rows[-1][0], "room": C, "rule": "drosseln", "action": "senken", "setpoint": 20.5})
    eng.store.reset_learning("2000-01-01")
    assert eng.store.get(f"model.valve.{C}") is None and len(eng.store.valve_dry_log(0)) == 1


def test_running_ext_event_is_skipped(tmp_path):
    rows, _ = simulate(Thermostat(), SimRoom(), Scenario(days=1), START)
    eng = engine(tmp_path, [Room("Wz", C, heating_power="sensor.wz_heat")])
    store_rows(eng.store, C, rows)
    eng._last_room_step = rows[-1][0] - rows[-1][0] % 1800
    eng.room_ext[C] = {"start_ts": rows[-1][0] - 6 * 3600, "t_start": 21.0, "peak": 22.0, "rise": 1.0}
    eng.learn_valves(budget_s=None)
    assert eng.valves[C].skipped_h["fremdwaerme"] >= 6 - vl.LAG_S / 3600 - 0.5


# ------------------------------------------------------------------ Gruppen, Schrittweite
def test_groups_step_size_and_kind():
    head = Room("Wohnen", "climate.a", heating_power="sensor.a_heat",
                members=[{"climate": "climate.b", "heating_power": "sensor.b_heat"}])
    states = {"climate.a": {"attributes": {"target_temp_step": 0.5}},
              "climate.b": {"attributes": {"target_temp_step": "0.1"}}}
    assert vl.step_of(head, states) == (0.5, "gerät")  # gröbster Schritt der Schreibziele
    assert vl.step_of(head, {"climate.b": {"attributes": {"target_temp_step": 0.1}}}) == (0.1, "gerät")
    assert vl.step_of(head, {}) == (vl.DEFAULT_STEP_K, "angenommen")
    assert vl.step_of(head, {"climate.a": {"attributes": {"target_temp_step": "kaputt"}}})[1] == "angenommen"
    wall = Room("Wohnen", "climate.wall", controller=True, members=[{"climate": "climate.b"}])
    assert vl.step_of(wall, {"climate.wall": {"attributes": {"target_temp_step": 0.2}}, **states}) == (0.2, "gerät")
    assert not vl.binary_device(head)
    assert vl.binary_device(Room("Bad", "climate.bad", members=[{"climate": "climate.bad2"}]))
    mixed = Room("Flur", "climate.f", members=[{"climate": "climate.f2", "heating_power": "sensor.f2"}])
    assert not vl.binary_device(mixed)  # ein Ventilsensor reicht: stetig (Mittel der Ventile)


def test_group_learns_one_model_on_mean_valve(tmp_path):
    head = Room("Wohnen", "climate.a", heating_power="sensor.a_heat",
                members=[{"climate": "climate.b", "heating_power": "sensor.b_heat"}])
    eng = engine(tmp_path, [head])
    eng.states.update({
        "climate.a": {"state": "heat", "attributes": {"current_temperature": 20.0, "temperature": 21.0}},
        "climate.b": {"state": "heat", "attributes": {"current_temperature": 20.4, "temperature": 21.0}},
        "sensor.a_heat": {"state": "40"}, "sensor.b_heat": {"state": "60"}})
    assert list(eng.valves) == ["climate.a"]  # Mitläufer bekommen kein eigenes Modell
    assert eng.room_reading(head) == ("climate.a", 20.2, 21.0, 50.0, None)
    assert eng.valve_info("climate.a")["kind"] == "stetig" and eng.valve_info("climate.b") is None


def test_files_readable_and_documented():
    for name in ("valve_model.py", "valve_learn.py", "valve_dry.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 600, name
        assert text.startswith('"""'), name
    assert (PKG / "valve_model.py").read_text(encoding="utf-8").startswith('"""Ventilmodell je Raum (seit 0.39)')


@pytest.mark.parametrize("e,cls", [(-3.0, 0), (-1.0, 0), (0.0, 10), (0.04, 10), (0.06, 11), (2.0, 30), (5.0, 30)])
def test_classes(e, cls):
    assert vm.e_class(e) == cls


# ------------------------------------------------------------------ Version, Doku
def test_version_changelog_docs():
    assert __version__ == "0.39.0"
    assert re.search(r'^version: "0\.39\.0"$', (ROOT / "ems_heatpilot" / "config.yaml").read_text(), re.MULTILINE)
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = log.split("\n## ")[1]
    assert head.startswith("0.39.0") and "nichts Neues geschrieben" in head
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    assert "## Ventilverhalten" in docs and "### Ventile im Trockenlauf" in docs
    assert "`target_temp_step`" in docs and "nur Protokoll, es wird nichts geschrieben" in docs
    plan = (ROOT / "docs" / "planung-vorlauf-ems.md").read_text(encoding="utf-8")
    assert "Stufe 1 gebaut (0.39)" in plan
    main = (PKG / "main.py").read_text(encoding="utf-8")
    assert "DATA_VERSION = 13" in main and "valve_model.VERSION" in main  # eigener Modellstand statt Neuberechnung
