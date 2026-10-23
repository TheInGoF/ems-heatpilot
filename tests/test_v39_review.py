"""0.39 Prüfung: nachgestellte Funde als Tests (Ventilmodell, Trockenlauf, Oberfläche).

Jeder Test stellt einen Fund der Prüfung nach und zeigt, dass er behoben ist:

* Zeitbombe: die Abfrage bleibt „belastbar“, auch wenn die Uhr Tage weiterläuft.
* Früh drosseln löst bei einem Thermostat mit Integrator, das das Soll hält, nicht aus (Raummodell richtig, falsch
  oder keins); ein P-Regler, der vor dem Soll schließt, bekommt keinen Vorschlag; die Schätzung trifft den Simulator.
* Gemeinsam anfordern hebt Räume über dem Soll mit geschlossenem Ventil (PI) nicht an.
* „Belastbar“ ist trennscharf: besser als „Ventil bleibt“, je Richtung genug Sprünge, keine große Streuung.
* Mit Integrator heißt die Kennlinie lastabhängig; Kp unter Messrauschen; Sommer-Historie bleibt stetig.
* Zeitzone und Rundung, Wortlaut, Hystereseschleife im Graf, lange Messpause, Ersatzwerte bei Sensorausfall, kein
  Ventilmodell für den EMS-Heizkreis, Nachtrag aus dem Startzähler, Gruppen gekennzeichnet.
"""

import ast
import json
import math
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import test_valve_dry39 as T
from heatpilot import valve_dry as vd
from heatpilot import valve_learn as vl
from heatpilot import valve_model as vm
from heatpilot import valve_text
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.models import RoomModel
from heatpilot.store import Store
from heatpilot.values import local
from sim_valve import Room as SimRoom
from sim_valve import Scenario, Thermostat, simulate, store_rows
from test_control34 import MON, slow_room, ts_of
from test_ui_v361 import NODE, between, script, source
from test_valve39 import START, learn, truth_jumps

PKG = Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot"
C = "climate.wz"
P100 = Thermostat(kp=100, ki=0, deadband=0.1, dead_min=4, tau_up_min=6, tau_down_min=10)
PI60 = Thermostat(kp=60, ki=0.5, deadband=0.1, dead_min=3, tau_up_min=4, tau_down_min=8)


def sub(path: Path) -> Path:
    """Eigenes Verzeichnis je Lauf (Store braucht es)."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def thermostat_step(th: Thermostat, s: dict, sp: float) -> None:
    """Ein Minutenschritt des künstlichen Thermostats (wie sim_valve) auf dem Zustand ``s`` (t, v, i, h)."""
    e = sp - s["t"]
    s["h"].append(e)
    d = round(th.dead_min)
    ed = s["h"][-1 - d] if len(s["h"]) > d else e
    if th.binary:
        s["v"] = 100.0 if ed >= th.e_on else 0.0 if ed <= th.e_off else s["v"]
        return
    edb = 0.0 if abs(ed) <= th.deadband else ed - math.copysign(th.deadband, ed)
    s["i"] = min(max(s["i"] + th.ki * edb, 0.0), 100.0)
    u = min(max(th.kp * edb + s["i"], 0.0), 100.0)
    s["v"] += (u - s["v"]) * (1 - math.exp(-1 / (th.tau_up_min if u > s["v"] else th.tau_down_min)))


# ------------------------------------------------------------------ Zeitbombe
def test_api_stays_reliable_when_the_clock_runs_on(tmp_path, monkeypatch):
    """Die Abfrage bewertet das Gütemaß der letzten 14 Tage bis heute – der Test hängt nicht mehr am Datum."""
    real = time.time
    for days in (2, 30):
        monkeypatch.setattr(time, "time", lambda d=days: real() + d * 86400)
        T.test_api_fields(sub(tmp_path / str(days)))
    stale = T.valve_model("binary")  # gelernt im Oktober 2026: einen Monat später „noch nicht geprüft“
    assert not stale.reliable(local(START + 40 * 86400).date().isoformat())[0]


# ------------------------------------------------------------------ früh drosseln
def _steady_house(tmp_path, mode: str, days: int = 1) -> list[dict]:
    """Zwei Räume mit festem Soll 21 °C und PI-Thermostat (hält das Ventil am Soll offen), Vorlauf konstant, Rauschen
    0,03 K; Raummodell wahr, 20 % daneben oder keins. Alle 5 min Messpunkt und Schritt des Trockenlaufs."""
    import random

    rng = random.Random(1)
    eng, ha, _ = T.house(tmp_path)
    truth = {T.WZ: (0.03, 0.02, 20.6), T.BAD: (0.025, 0.02, 20.7)}
    for c, (a, b, _) in truth.items():
        eng.rooms[c] = {"wahr": slow_room(a=a, b=b), "a+20%": slow_room(a=a * 1.2, b=b)}.get(mode) or RoomModel(c)
        eng.valves[c] = T.valve_model("pi")
    st = {c: {"t": t0, "v": 0.0, "i": 0.0, "h": []} for c, (_, _, t0) in truth.items()}
    start, entries = ts_of(MON, 0), []
    for m in range(days * 1440):
        for c, (a, b, _) in truth.items():
            thermostat_step(T.THERMOSTATS["pi"], st[c], 21.0)
            st[c]["t"] += (a * (50.0 - st[c]["t"]) * st[c]["v"] / 100 + b * (5.0 - st[c]["t"])) / 60
        if m % 5 == 0:
            readings = [(c, round(st[c]["t"] + rng.gauss(0, 0.03), 2), 21.0, round(st[c]["v"], 1), None) for c in truth]
            eng.store.add_room_samples((start + m * 60, *x) for x in readings)
            entries += vd.step(eng, start + m * 60, {"flow": 50.0, "heating": 1, "pump_on": 1, "t_out": 5.0}, readings)
    assert ha.calls == []
    return entries


@pytest.mark.parametrize("mode", ["wahr", "a+20%", "keins"])
def test_no_throttle_while_pi_holds_the_setpoint(tmp_path, mode):
    """Vorher: 7 / 62 / 23 Eingriffe in 2 Tagen ohne Überschwingen (Rauschen in der Steigung, Raummodell daneben)."""
    assert _steady_house(tmp_path, mode) == []


def _split_run(kind: str, a: float, b: float, tmp_path) -> tuple[dict | None, float, float]:
    """Raum heizt von 18 auf 21 °C (Soll-Sprung), Raummodell = Wahrheit, Ventilmodell am selben Thermostat gelernt.
    Beim ersten Vorschlag wird der Lauf geteilt: ohne Eingriff bzw. Soll einen Schritt tiefer bis zum Erreichen.
    (Eintrag, wahres Überschwingen ohne, wahre Wirkung)."""
    import copy

    th = T.THERMOSTATS[kind]
    eng, _, _ = T.house(tmp_path, rooms=[T.room("Wz", T.WZ, sensor=kind != "binary")])
    eng.rooms[T.WZ] = slow_room(a=a, b=b)
    eng.valves[T.WZ] = T.valve_model(kind)
    s, start = {"t": 18.0, "v": 0.0, "i": 0.0, "h": []}, ts_of(MON, 5)

    def advance(state, sp):
        thermostat_step(th, state, sp)
        state["t"] += (a * (60.0 - state["t"]) * state["v"] / 100 + b * (5.0 - state["t"])) / 60

    def overshoot(state, lowered):
        state, peak, sp = copy.deepcopy(state), state["t"], 21.0 - lowered
        for minute in range(90):
            sp = 21.0 if state["t"] >= 21.0 or minute >= 60 else sp
            advance(state, sp)
            peak = max(peak, state["t"])
        return max(peak - 21.0, 0.0)

    for m in range(240):
        sp = 21.0 if m >= 60 else 18.0
        advance(s, sp)
        if m % 5 == 0:
            reading = (T.WZ, round(s["t"], 2), sp, round(s["v"], 1), None)
            eng.store.add_room_samples([(start + m * 60, *reading)])
            new = vd.step(eng, start + m * 60, {"flow": 60.0, "heating": 1, "pump_on": 1, "t_out": 5.0}, [reading])
            if any(e["action"] == "senken" for e in new):
                base = overshoot(s, 0.0)
                return new[0], base, base - overshoot(s, 21.0 - new[0]["setpoint"])
    return None, 0.0, 0.0


def test_throttle_estimate_matches_simulator(tmp_path):
    """Vorher: P-Thermostat bekam bei 20,23 °C einen Vorschlag mit „0,12 K belastbar“, obwohl es nie überschwingt;
    beim PI lag die Schätzung 2- bis 3-fach zu hoch."""
    e, _, _ = _split_run("p", 0.08, 0.03, sub(tmp_path / "p"))
    assert e is None  # schließt vor dem Soll – kein Überschwingen, kein Vorschlag
    e, base, gain = _split_run("pi", 0.08, 0.03, sub(tmp_path / "pi"))
    assert e is not None and e["reliable"] == 1
    assert abs(e["est_k"] - gain) <= 0.04 and abs(e["pred_over_k"] - base) <= 0.15
    e, _, gain = _split_run("binary", 0.08, 0.03, sub(tmp_path / "bin"))
    assert e is not None and e["est_k"] <= 0.03 and gain <= 0.03  # an/aus gewinnt kaum etwas


def test_eta_rounded_down_in_reason(tmp_path):
    """Die Regel heißt „unter 20 min“ – der Grund rundet ab („19 min“ statt „20 min“ bei 19,9 min)."""
    eng, _, _ = T.house(tmp_path, models=False)
    t0 = ts_of(MON, 7)
    T.approach(eng, T.WZ, t0, 20.7, rate_kh=0.3 / 19.9 * 60, valve=60.0, rise_min=None)
    new = vd.step(eng, t0, T.SAMPLE, [T.reading(T.WZ, 20.7)])
    assert len(new) == 1 and 19.5 <= new[0]["eta_min"] < 20
    assert new[0]["reason"] == "erreicht 21,0 °C in ≈ 19 min, Ventil dann noch ≈ 60 % offen"


# ------------------------------------------------------------------ gemeinsam anfordern
@pytest.mark.parametrize("over", [0.2, 0.5, 0.7])
def test_join_does_not_raise_pi_room_above_setpoint(tmp_path, over):
    """Vorher: open_at() eines PI = e0 der Beharrungs-Kennlinie (−0,78 K) – Bad über dem Soll wurde angehoben, obwohl
    das Ventil in 60 min Abkühlen nicht öffnet (Integrator abgebaut)."""
    eng, _, _ = T.house(tmp_path)
    eng.valves[T.BAD] = T.valve_model("pi")
    eng.rooms[T.BAD] = slow_room(a=0.025, b=0.0125)
    t0 = ts_of(MON, 10)
    T.burner(eng, t0)
    readings = [T.reading(T.WZ, 20.4, valve=80.0), T.reading(T.BAD, 21.0 + over, valve=0.0)]
    assert vd.step(eng, t0, T.SAMPLE, readings) == []
    # Gegenprobe: knapp unter dem Soll und kühlt ab – das PI-Ventil öffnet bald, der Raum wird angehoben
    eng2, _, _ = T.house(sub(tmp_path / "b"))
    eng2.valves[T.BAD] = T.valve_model("pi")
    T.burner(eng2, t0)
    new = vd.step(eng2, t0, T.SAMPLE, [T.reading(T.WZ, 20.4, valve=80.0), T.reading(T.BAD, 20.95, valve=0.0)])
    assert [(e["room"], e["action"]) for e in new] == [(T.BAD, "anheben")]


def test_actuals_from_starts_counter(tmp_path):
    """Vorher: ohne Brennerereignisse blieb der Nachtrag leer, obwohl der Startzähler den Eingriff ausgelöst hatte."""
    eng, _, _ = T.house(tmp_path)
    t0 = ts_of(MON, 10)
    for i, n in enumerate((100, 101, 102, 104, 105, 107, 108, 110, 112)):
        eng.store.add_sample(t0 - 3000 + i * 900, {"burn_starts": n})
    eng.store.commit()
    assert vd.cycling(eng.store, t0)["cycling"]
    assert [e["action"] for e in vd.step(eng, t0, T.SAMPLE, [T.reading(T.WZ, 20.4, valve=80.0),
                                                              T.reading(T.BAD, 20.9, valve=0.0)])] == ["anheben"]
    assert vd.fill_actuals(eng.store, t0 + vd.CHECK_S) == 1
    row = eng.store.valve_dry_log(0)[0]
    assert row["act_starts"] == 5 and row["checked"] == 1  # Zähler 105 → 110 in der Stunde danach


# ------------------------------------------------------------------ Belastbar
def test_reliable_needs_more_than_persistence_both_directions_and_little_spread():
    d = T.valve_model("p").to_dict()
    base = vm.ValveModel.from_dict(d)
    assert base.reliable()[0]
    # Vorhersage nicht besser als „Ventil bleibt, wie es ist“
    same = vm.ValveModel.from_dict({**d, "err": {k: [c[0], c[1], c[2], c[3], c[5], c[5], c[6], c[7], c[8]]
                                                 for k, c in d["err"].items()}})
    ok, reasons = same.reliable()
    assert not ok and any(r.startswith("Vorhersage kaum besser als „Ventil bleibt, wie es ist“") for r in reasons)
    # genug Sprünge insgesamt, aber nur 3 nach unten
    few = vm.ValveModel.from_dict({**d, "jumps": {"up": d["jumps"]["up"][:9], "down": d["jumps"]["down"][:3]}})
    assert "erst 3 Soll-Sprünge nach unten (mindestens 4 je Richtung)" in few.reliable()[1]
    # Reaktionszeiten nach unten streuen stark (z. B. 4 Sprünge, 9 bis 33 min)
    wide = vm.ValveModel.from_dict({**d, "jumps": {"up": d["jumps"]["up"],
                                                   "down": [[5, 9, 5], [5, 10, 5], [5, 30, 5], [5, 33, 5]]}})
    assert any(r.startswith("Reaktion nach unten streut stark") for r in wide.reliable()[1])


def test_first_reliable_model_matches_truth(tmp_path):
    """Vorher: PI60 (Seed 1) wurde nach 55 h mit 4 Sprüngen je Richtung belastbar – schließen 32,6 statt 11,5 min."""
    rows, truth = simulate(PI60, SimRoom(), Scenario(seed=1, days=6, extra_jumps=1), START)
    store = Store(tmp_path / "f.db")
    store_rows(store, C, rows)
    m = vm.ValveModel()
    m.learned_until = rows[0][0]
    for until in range(rows[0][0] + 1800, rows[-1][0], 1800):
        vl.learn_chunk(store, m, C, until)
        if m.reliable()[0]:
            break
    assert m.reliable()[0]
    want, got = truth_jumps(truth), m.response()
    for d in ("up", "down"):
        assert got[d]["n"] >= vm.RELIABLE_DIR_JUMPS and abs(got[d]["t63_min"] - want[d][1]) <= 2.5, (d, got[d])
    err, keep = m.error(30)[0], m.keep_error()
    assert err <= vm.RELIABLE_VS_KEEP * keep


# ------------------------------------------------------------------ Kennlinie mit Integrator, Rauschen, Sommer
@pytest.mark.parametrize("t_out", [-5.0, 12.0])
def test_pi_curve_is_load_dependent(tmp_path, t_out):
    """Vorher: dieselbe PI-Kennlinie lag je nach Last bei e50 −0,5 … +0,5 K und hieß „belastbar“ – jetzt zeigt der Satz
    Reglerwerte statt Punkten, die Vorhersage nutzt die Kennlinie nicht."""
    m, _, _ = learn(PI60, Scenario(seed=5), tmp=tmp_path, room=SimRoom(t_out=t_out))
    s = m.summary(0.5, "gerät")
    assert m.regler() == "PI" and s["load_dependent"] and s["text"].startswith("regelt mit Integrator, Ruhepunkt ")
    assert "die Kennlinie hängt von der Last ab" in s["text"] and "halb offen" not in s["text"]
    assert 40 <= s["pi"]["kp"] <= 75 and 60 <= s["pi"]["tn_min"] <= 180  # wahr: 60 %/K, 120 min
    assert isinstance(m.predictor(), vl.vm.vp.PI)
    p, _, _ = learn(P100, Scenario(seed=5), tmp=sub(tmp_path / "p"), room=SimRoom(t_out=t_out))
    assert p.regler() == "P" and not p.summary(0.5, "gerät")["load_dependent"]


def test_kp_not_crushed_by_measurement_noise(tmp_path):
    """Vorher: Rauschen 0,1 K drückte Kp von 100 auf 22 (P) bzw. von 60 auf 12 (PI), trotzdem belastbar."""
    for th, kp_min in ((P100, 70), (PI60, 35)):
        m, _, _ = learn(th, Scenario(seed=4, noise_k=0.1), tmp=sub(tmp_path / str(th.kp)))
        assert m.gains()[0] >= kp_min and abs(m.noise_k() - 0.1) <= 0.02
    rows, _ = simulate(PI60, SimRoom(), Scenario(seed=4, noise_k=0.15), START)
    rows = [(ts, round(round(t * 10) / 10, 2), sp, float(round(v))) for ts, t, sp, v in rows]  # wie Tado/HA gerundet
    store = Store(tmp_path / "q.db")
    store_rows(store, C, rows)
    m = vm.ValveModel()
    while vl.learn_chunk(store, m, C, rows[-1][0] + 300):
        pass
    s = m.summary(0.5, "gerät")
    assert s["pi"]["kp_uncertain"] and not s["reliable"]
    assert any(r.startswith("Messrauschen ≈ 0,1") and r.endswith("K überdeckt die Verstärkung Kp") for r in s["reasons"])


def test_summer_history_stays_continuous(tmp_path):
    """Vorher: 45 Tage Sommer mit geschlossenem Ventil zählten als „nur an/aus“ – nach dem Update im Herbst stand
    „Schaltpunkte lernen noch“, und die Sprünge aus dieser Zeit verfälschten t63."""
    summer = 30
    rows_s, _ = simulate(P100, SimRoom(t_out=22.0, t0=22.0), Scenario(seed=1, days=summer), START)
    start2 = START + summer * 86400
    rows_a, truth = simulate(P100, SimRoom(t0=rows_s[-1][1]), Scenario(seed=2, days=3), start2)
    store = Store(tmp_path / "s.db")
    store_rows(store, C, rows_s, heat_on=False)
    store_rows(store, C, rows_a)
    m = vm.ValveModel()
    while vl.learn_chunk(store, m, C, start2 + 3600):
        pass
    assert m.kind == "stetig" and m.valve_n < 50  # der Sommer zählt nicht
    while vl.learn_chunk(store, m, C, rows_a[-1][0]):
        pass
    assert m.kind == "stetig" and abs(m.response()["up"]["t63_min"] - truth_jumps(truth)["up"][1]) <= 2


def test_kind_change_starts_jumps_and_check_anew():
    m = vm.ValveModel.from_dict(T.valve_model("p").to_dict())
    m.note_kind()
    assert m.jumps["up"] and m.err
    m.valve_n, m.inter_n = 1000.0, 5.0  # die Daten zeigen jetzt nur 0 und 100 %
    m.note_kind()
    assert m.kind == "binär" and m.jumps == {"up": [], "down": []} and m.err == {} and m.seen_kind == "binär"


# ------------------------------------------------------------------ Text, Rundung, Zeitzone
def test_text_wording_and_half_up_minutes():
    s = T.valve_model("p").summary(0.5, "gerät", START + 6 * 86400)
    assert valve_text.whole_min(14.5) == "15" and valve_text.whole_min(4.5) == "5" and valve_text.whole_min(4.4) == "4"
    s14 = {**s, "response": {**s["response"], "down": {**s["response"]["down"], "t63_min": 14.5}}}
    assert "schließt in ≈ 15 min" in vm.text(s14)  # wie die Tabelle der Oberfläche (fmt rundet Halbe auf)
    over = {**s, "regler": "P", "e0": -0.78, "e50": 0.5, "e100": 0.6}
    assert vm.text(over).startswith("öffnet ab 0,8 K über Soll, halb offen bei 0,5 K unter Soll, ganz bei 0,6 K unter Soll")
    at = {**s, "regler": "P", "e0": -0.3, "e50": -0.03, "e100": None}
    assert vm.text(at).startswith("öffnet ab 0,3 K über Soll, halb offen am Soll · ")
    assert vm.text({**s, "e0": 0.2, "e50": 0.03}).startswith("öffnet ab 0,2 K unter Soll, halb offen am Soll, ganz bei ")


def test_learned_times_do_not_depend_on_time_zone(tmp_path, monkeypatch):
    """Vorher: START war lokale Mitternacht – in Europe/Berlin lag die Simulation anders, Sprungzeiten auf ,5 und
    Python rundete sie anders als die Oberfläche (2 Tests rot)."""
    got = {}
    try:
        for tz in ("UTC", "Europe/Berlin", "America/New_York", "Asia/Tokyo"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            r = learn(Thermostat(), tmp=sub(tmp_path / tz.replace("/", "_")))[0].response()
            got[tz] = (r["up"]["dead_min"], r["up"]["t63_min"], r["down"]["dead_min"], r["down"]["t63_min"])
    finally:
        monkeypatch.undo()
        time.tzset()
    assert len(set(got.values())) == 1, got


# ------------------------------------------------------------------ lange Messpause
def test_long_sampling_interval(tmp_path):
    """Vorher: bei 900 s zählte die Beharrung nur zu 2/3 (Messpause auf 600 s gedeckelt), und Sprungzeiten waren über
    eine halbe Messpause zu lang, ohne Hinweis."""
    fine, _, _ = learn(P100, Scenario(seed=1, step_s=300), tmp=tmp_path)
    coarse, _, _ = learn(P100, Scenario(seed=1, step_s=900), tmp=sub(tmp_path / "c"))
    assert abs(coarse.steady_h - fine.steady_h) / fine.steady_h < 0.05
    rf, rc = fine.response(), coarse.response()
    assert not rf["up"]["coarse"] and rc["up"]["coarse"] and rc["up"]["step_min"] == 15.0
    assert "(grob – Messpause 15 min)" in coarse.summary(0.5, "gerät")["text"]


# ------------------------------------------------------------------ Ersatzwerte, EMS, Gruppen
def _engine(tmp_path, rooms, states):
    eng = Engine(Config(), Store(tmp_path / "e.db"), Discovered(rooms=rooms))
    eng.states.update(states)
    return eng


def test_substituted_valve_values_are_not_learned(tmp_path):
    """Vorher: Fiel der Ventilsensor aus, landete hvac_action (0/100 %) als Ventil im stetigen Modell (e50 0,25 statt
    0,58). Jetzt merkt der Takt solche Messpunkte (valve_subst), das Ventilmodell lässt ihr Ventil aus."""
    room = Room("Wz", C, heating_power="sensor.wz_heat")
    eng = _engine(tmp_path, [room], {
        C: {"state": "heat", "attributes": {"current_temperature": 20.0, "temperature": 21.0, "hvac_action": "heating"}},
        "sensor.wz_heat": {"state": "unavailable"}})
    t0 = ts_of(MON, 10)
    eng.tick(t0)
    assert eng.store.query("SELECT room FROM valve_subst")[0]["room"] == C
    assert eng.store.query("SELECT valve FROM room_samples")[0]["valve"] == 100.0  # Raummodelle wie bisher
    assert vl.fetch(eng.store, C, t0 - 60, t0 + 60)[0].valve is None
    eng.states["sensor.wz_heat"] = {"state": "40"}
    eng.tick(t0 + 300)
    assert len(eng.store.query("SELECT * FROM valve_subst")) == 1 and vl.fetch(eng.store, C, t0, t0 + 400)[1].valve == 40
    # ein halber Tag Ausfall je Tag: mit Kennzeichnung bleibt die Kennlinie, wie sie ohne Ausfall wäre
    rows, _ = simulate(P100, SimRoom(), Scenario(seed=1), START)
    store = Store(tmp_path / "o.db")
    out = [(ts, t, sp, (100.0 if v > 0.5 else 0.0) if ((ts - START) // 3600) % 24 in range(6, 18) else v)
           for ts, t, sp, v in rows]
    store_rows(store, C, out)
    for ts, *_ in out:
        if ((ts - START) // 3600) % 24 in range(6, 18):
            store.add_valve_subst(ts, [C])
    store.commit()
    m = vm.ValveModel()
    while vl.learn_chunk(store, m, C, rows[-1][0] + 300):
        pass
    assert abs(m.points()["e50"] - 0.58) <= 0.1 and m.kind == "stetig"


def test_ems_circuit_gets_no_valve_model(tmp_path):
    eng = _engine(tmp_path, [Room("Heizkreis 1", "climate.ems_hc1", ems=True), Room("Wz", C)], {})
    assert list(eng.valves) == [C] and eng.valve_info("climate.ems_hc1") is None


def test_groups_are_labelled_and_mixed_ones_not_reliable(tmp_path):
    states = {c: {"state": "heat", "attributes": {"current_temperature": 20.0, "temperature": 21.0}}
              for c in ("climate.a", "climate.b")}
    two = Room("Wohnen", "climate.a", heating_power="sensor.a", members=[{"climate": "climate.b", "heating_power": "sensor.b"}])
    eng = _engine(tmp_path, [two], states)
    eng.valves["climate.a"] = T.valve_model("p")
    eng._last_room_step = eng.valves["climate.a"].learned_until
    info = eng.valve_info("climate.a")
    assert info["group"] == {"n": 2, "mixed": False} and info["text"].startswith("2 Ventile, Mittel: öffnet ab ")
    assert info["reliable"]
    mixed = Room("Wohnen", "climate.a", heating_power="sensor.a", members=[{"climate": "climate.b"}])
    eng = _engine(sub(tmp_path / "m"), [mixed], states)
    eng.valves["climate.a"] = T.valve_model("p")
    eng._last_room_step = eng.valves["climate.a"].learned_until
    info = eng.valve_info("climate.a")
    assert info["group"]["mixed"] and info["text"].startswith("Mittel aus 2 Ventilen verschiedener Art: ")
    assert not info["reliable"] and "Gruppe mit stetigen und an/aus-Ventilen – das Mittel beschreibt keines" in info["reasons"]
    binary = Room("Bad", "climate.a", members=[{"climate": "climate.b"}])
    assert vl.group_of(binary) == {"n": 2, "mixed": False}
    s = T.valve_model("binary").summary(0.5, "gerät", group=vl.group_of(binary))
    assert s["text"].startswith("2 Ventile, ein mit dem ersten, aus mit dem letzten: schaltet ein bei ")


# ------------------------------------------------------------------ Oberfläche: Hystereseschleife
def test_switch_loop_is_closed():
    """Vorher: Die obere Linie begann erst bei „ein“ – bei „aus“ stand eine einzelne Senkrechte ohne Verbindung."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = between(script(), "// ---------------------------------------------------------------- Ventilverhalten (0.39)",
                 "// Anwesenheit: wie HeatPilot Entfernung und Heimkehr deutet")
    code = (source(js, "VALVE_E") + source(js, "valveSwitchMarks") +
            "const out = []; function el(tag, a) { out.push(a.d || null); return { set textContent(v) {} }; }\n"
            "valveSwitchMarks({ sx: (x) => (x + 1) * 100, sy: (y) => 100 - y }, { e_on: 0.3, e_off: -0.1 }, {});\n"
            "console.log(JSON.stringify(out[0]));")
    f = Path(tempfile.mkdtemp()) / "loop.js"
    f.write_text(code, encoding="utf-8")
    d = json.loads(subprocess.run([NODE, str(f)], capture_output=True, text=True, check=True).stdout)
    # unten (y 100) von links bis „ein“ (130), hoch auf 100 % (y 0); oben vom rechten Rand (300) bis „aus“ (90), runter
    assert d == "M0,100H130V0M300,0H90V100"


