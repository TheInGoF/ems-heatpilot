"""0.39 Ventile im Trockenlauf: früh drosseln, gemeinsam anfordern – nur Protokoll, nichts wird geschrieben.

Geprüft: beide Regeln mit belastbaren Modellen (mit Schätzung) und ohne (nur gezählt), früh drosseln nur bei echter
Annäherung (gemessen steigend, nach einem Soll-Anstieg oder deutlich unter dem Soll) mit vorhergesagtem Überschwingen,
danach gesperrt bis zum nächsten Soll, nur bei Änderung, volle Auflösung des Thermostats (Schritt je Gerät), 5 min
Haltezeit, Ende durch Zeitplan ohne Zurückschreiben, Nachtrag nach einer Stunde, Auswertung und Texte (die Schätzung
erst nach Nachprüfung), ein künstliches Haus über einen Tag (``sim_valve`` wie ``sim_house``) und dass weder bei
„Beobachten“ noch bei „HeatPilot steuert“ ein zusätzlicher Aufruf an Thermostate oder Kessel entsteht.
"""

import inspect
import tempfile
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

from heatpilot import valve_dry as vd
from heatpilot import valve_model as vm
from heatpilot.config import Config
from heatpilot.discovery import Room
from heatpilot.models import RoomModel
from heatpilot.values import local
from sim_valve import Room as SimRoom
from sim_valve import Scenario, Thermostat, simulate
from test_control34 import MON, climate, make, run, slow_room, ts_of
from test_valve39 import learn

WZ, BAD = "climate.wz", "climate.bad"
SAMPLE = {"flow": 60.0, "heating": 1, "pump_on": 1, "t_out": 5.0}
_MODELS: dict = {}


THERMOSTATS = {"p": Thermostat(), "binary": Thermostat(binary=True, e_on=0.3, e_off=-0.1, dead_min=2),
               "pi": Thermostat(kp=60, ki=0.5, deadband=0.1, dead_min=3, tau_up_min=4, tau_down_min=8)}


def valve_model(kind: str = "p", today: int | None = None) -> vm.ValveModel:
    """Belastbares Ventilmodell, gelernt an einem künstlichen Thermostat (P mit Totband, PI bzw. an/aus). ``today``:
    so verschoben, als endeten seine Daten an diesem Tag (das Gütemaß zählt die letzten 14 Tage bis heute)."""
    if kind not in _MODELS:
        _MODELS[kind] = learn(THERMOSTATS[kind], tmp=tempfile.mkdtemp())[0].to_dict()
    d = dict(_MODELS[kind])
    if today is not None:
        shift = (local(today).date() - date.fromisoformat(max(d["err"]))).days
        move = lambda days: {(date.fromisoformat(k) + timedelta(days=shift)).isoformat(): v for k, v in days.items()}  # noqa: E731
        d.update(err=move(d["err"]), e50_days=move(d["e50_days"]), learned_until=today, decay_ts=today)
    m = vm.ValveModel.from_dict(d, binary_dev=kind == "binary")
    assert m.reliable(None if today is None else local(today).date().isoformat())[0]
    return m


def room(name: str, climate_id: str, sensor: bool = True, **kw) -> Room:
    return Room(name, climate_id, heating_power=f"sensor.{name.lower()}_heat" if sensor else None, comfort=21.0,
                platform="tado", **kw)


def house(tmp_path, rooms=None, cfg=None, step=0.5, models=True):
    rooms = rooms or [room("Wz", WZ), room("Bad", BAD)]
    eng, ha, _, loop = make(tmp_path, rooms=rooms, cfg=cfg or Config())
    for r in rooms:
        climate(eng, r.climate, 20.0, 21.0, **({"target_temp_step": step} if step else {}))
        eng.rooms[r.climate] = slow_room(a=0.025, b=0.0125) if models else RoomModel(r.name)
        if models:
            eng.valves[r.climate] = valve_model("p" if r.heating_power else "binary")
    return eng, ha, loop


def reading(c, temp, target=21.0, valve=60.0):
    return (c, temp, target, valve, None)


def approach(eng, c, ts, temp, rate_kh=2.4, target=21.0, valve=100.0, rise_min=60):
    """Gemessene Annäherung: alle 5 min ein Messpunkt bis ``ts``, gleichmäßig steigend auf ``temp``; ``rise_min``
    Minuten vorher stieg das Soll (Zeitplan) von 18 °C auf ``target`` (None: kein Soll-Anstieg)."""
    span = rise_min + 10 if rise_min else 30
    eng.store.add_room_samples(
        (ts - m * 60, c, round(temp - rate_kh * m / 60, 3), 18.0 if rise_min and m > rise_min else target, valve, None)
        for m in range(0, span + 1, 5))
    eng.store.commit()


def strong(eng, c=WZ, kind="pi"):
    """Großer Heizkörper (Raummodell a = 0,08) und ein Thermostat, das das Ventil bis zum Soll offen hält (Integrator)."""
    eng.rooms[c] = slow_room(a=0.08, b=0.03)
    eng.valves[c] = valve_model(kind)


def burner(eng, ts, starts=5, run_min=4, every_min=12):
    """Brennerereignisse der letzten Stunde: ``starts`` kurze Läufe."""
    for i in range(starts):
        on = ts - 3600 + 60 + i * every_min * 60
        eng.store.add_burner_event(on, True)
        eng.store.add_burner_event(on + run_min * 60, False)
    eng.store.commit()


# ------------------------------------------------------------------ früh drosseln
def test_throttle_with_reliable_models_and_reset_on_reach(tmp_path):
    eng, ha, _ = house(tmp_path)
    strong(eng)
    t0 = ts_of(MON, 7)
    approach(eng, WZ, t0, 20.6)  # um 6 Uhr von 18 auf 21 °C, seitdem 2,4 K/h
    new = vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    assert len(new) == 1
    e = new[0]
    assert (e["room"], e["rule"], e["action"], e["setpoint"], e["base"]) == (WZ, "drosseln", "senken", 20.5, 21.0)
    assert e["reliable"] == 1 and e["eta_min"] < vd.THROTTLE_ETA_MIN and e["valve_pred"] > vd.THROTTLE_VALVE_PCT
    assert e["est_k"] is not None and e["est_k"] > 0  # weniger Überschwingen mit früh gesenktem Soll
    assert e["pred_over_k"] >= vd.OVERSHOOT_K  # ohne Eingriff vorhergesagt – wird nach 1 h nachgemessen
    assert e["reason"].startswith("erreicht 21,0 °C in ≈ ") and "% offen" in e["reason"]
    # nur bei Änderung: derselbe Zustand schreibt nichts
    assert vd.step(eng, t0 + 300, SAMPLE, [reading(WZ, 20.8, valve=100.0), reading(BAD, 21.2, valve=0.0)]) == []
    # Soll erreicht – zurück; in der Haltezeit erst danach
    eng.__dict__["_valve_dry"]["last"][WZ] = t0 + 400
    assert vd.step(eng, t0 + 600, SAMPLE, [reading(WZ, 21.0), reading(BAD, 21.2, valve=0.0)]) == []
    back = vd.step(eng, t0 + 700, SAMPLE, [reading(WZ, 21.05), reading(BAD, 21.2, valve=0.0)])
    assert [(b["action"], b["setpoint"], b["rule"]) for b in back] == [("zurück", 21.0, "drosseln")]
    assert vd.step(eng, t0 + 1000, SAMPLE, [reading(WZ, 21.1), reading(BAD, 21.2, valve=0.0)]) == []
    assert ha.calls == []


def test_throttle_locked_until_next_setpoint(tmp_path):
    """Nach einem Drosseln (zurück beim Erreichen oder nach 60 min) erst wieder nach neuem Soll oder deutlichem
    Auskühlen – sonst löst ein Thermostat, das um das Soll pendelt, alle paar Minuten aus."""
    eng, _, _ = house(tmp_path)
    strong(eng)
    t0 = ts_of(MON, 7)
    approach(eng, WZ, t0, 20.6)
    assert len(vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0)])) == 1
    t1 = t0 + vd.THROTTLE_MAX_MIN * 60
    assert [e["action"] for e in vd.step(eng, t1, SAMPLE, [reading(WZ, 20.7, valve=100.0)])] == ["zurück"]
    approach(eng, WZ, t1 + 600, 20.6, rise_min=None)
    assert vd.step(eng, t1 + 600, SAMPLE, [reading(WZ, 20.6, valve=100.0)]) == []  # gesperrt
    assert eng.__dict__["_valve_dry"]["lock"] == {WZ: 21.0}
    approach(eng, WZ, t1 + 1200, 20.4, rate_kh=-0.5, rise_min=None)  # ausgekühlt: 0,6 K unter Soll
    assert vd.step(eng, t1 + 1200, SAMPLE, [reading(WZ, 20.4, valve=100.0)]) == []
    assert eng.__dict__["_valve_dry"]["lock"] == {}
    # neues Soll (Zeitplan) hebt die Sperre ebenso auf
    eng.__dict__["_valve_dry"]["lock"][WZ] = 21.0
    vd.step(eng, t1 + 1500, SAMPLE, [reading(WZ, 20.4, target=22.0, valve=100.0)])
    assert eng.__dict__["_valve_dry"]["lock"] == {}


def test_throttle_needs_fast_approach_open_valve_and_heat(tmp_path):
    eng, _, _ = house(tmp_path)
    strong(eng)
    t0 = ts_of(MON, 7)
    approach(eng, WZ, t0, 20.6)
    rooms = [reading(BAD, 21.2, valve=0.0)]
    assert vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=20.0), *rooms]) == []  # Ventil kaum offen
    assert vd.step(eng, t0, {**SAMPLE, "pump_on": 0, "heating": 0}, [reading(WZ, 20.6), *rooms]) == []
    assert vd.step(eng, t0, SAMPLE, [reading(WZ, 21.1), *rooms]) == []          # schon am Soll
    assert vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, target=None), *rooms]) == []  # Thermostat aus
    eng.rooms[WZ] = slow_room(a=0.025, b=0.0125)
    assert vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), *rooms]) == []  # dauert noch lange
    # P-Regler: schließt vor dem Soll – kein Überschwingen vorhergesagt, kein Vorschlag
    strong(eng, kind="p")
    assert vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), *rooms]) == []


def test_throttle_needs_measured_approach(tmp_path):
    """Ein Thermostat mit Integrator hält das Soll mit offenem Ventil: ohne gemessenen Anstieg (nur Rauschen) und ohne
    Soll-Anstieg bzw. Abstand ist das keine Annäherung – auch wenn das Raummodell knapp unter dem Soll „gleich da“ sagt."""
    eng, _, _ = house(tmp_path)
    strong(eng)
    t0 = ts_of(MON, 7)
    noise = [0.0, 0.03, -0.02, 0.01, -0.03, 0.02, 0.0]
    eng.store.add_room_samples((t0 - m * 300, WZ, 20.97 + noise[m], 21.0, 40.0, None) for m in range(7))
    assert vd.step(eng, t0, SAMPLE, [reading(WZ, 20.97, valve=40.0)]) == []  # beharrt am Soll
    eng2, _, _ = house(tmp_path / "b")
    strong(eng2)
    approach(eng2, WZ, t0, 20.9, rise_min=None)  # steigt, aber nur 0,1 K unter dem Soll, ohne Soll-Anstieg
    assert vd.step(eng2, t0, SAMPLE, [reading(WZ, 20.9, valve=100.0)]) == []
    approach(eng2, WZ, t0, 20.9)  # derselbe Anstieg nach einem Soll-Anstieg vor einer Stunde: Annäherung
    assert [e["action"] for e in vd.step(eng2, t0, SAMPLE, [reading(WZ, 20.9, valve=100.0)])] == ["senken"]


def test_throttle_ends_without_write_when_schedule_changes_and_after_timeout(tmp_path):
    eng, _, _ = house(tmp_path)
    strong(eng)
    t0 = ts_of(MON, 7)
    approach(eng, WZ, t0, 20.6)
    assert len(vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])) == 1
    # Zeitplan senkt das Soll: Eingriff endet, HeatPilot schreibt nichts zurück
    assert vd.step(eng, t0 + 600, SAMPLE, [reading(WZ, 20.95, target=18.0), reading(BAD, 21.2, valve=0.0)]) == []
    assert eng.__dict__["_valve_dry"]["active"] == {}
    # spätestens nach THROTTLE_MAX_MIN zurück, auch wenn das Soll nicht erreicht ist
    eng2, _, _ = house(tmp_path / "b")
    strong(eng2)
    approach(eng2, WZ, t0, 20.6)
    vd.step(eng2, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    out = vd.step(eng2, t0 + vd.THROTTLE_MAX_MIN * 60, SAMPLE, [reading(WZ, 20.6, valve=10), reading(BAD, 21.2, 0)])
    assert [e["action"] for e in out] == ["zurück"]


def test_throttle_without_models_only_counted(tmp_path):
    eng, _, _ = house(tmp_path, models=False)
    t0 = ts_of(MON, 7)
    approach(eng, WZ, t0, 20.7, rate_kh=1.2, valve=60.0, rise_min=None)
    new = vd.step(eng, t0, SAMPLE, [reading(WZ, 20.7), reading(BAD, 21.2, valve=0.0)])
    assert len(new) == 1 and new[0]["reliable"] == 0 and new[0]["est_k"] is None and new[0]["pred_over_k"] is None
    assert 13 <= new[0]["eta_min"] <= 17  # 0,3 K bei gemessenen 1,2 K/h
    s = vd.summary(eng, t0)
    assert s["counted_only"] == 1 and s["effect"]["drosseln"]["est_k_mean"] is None
    assert s["text"].endswith("; 1 nur gezählt (Modelle lernen noch)")


def test_step_size_of_the_device(tmp_path):
    t0 = ts_of(MON, 7)
    eng, _, _ = house(tmp_path, step=0.1)
    strong(eng)
    approach(eng, WZ, t0, 20.95)
    new = vd.step(eng, t0, SAMPLE, [reading(WZ, 20.95, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    assert new[0]["setpoint"] == 20.9  # volle Auflösung: ein Schritt von 0,1 K
    eng, _, _ = house(tmp_path / "b", step=None)
    strong(eng)
    approach(eng, WZ, t0, 20.6)
    new = vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    assert new[0]["setpoint"] == 20.5  # ohne Angabe 0,5 K


def test_binary_valve_counted_but_gains_little(tmp_path):
    """Ein Ventil, das nur an/aus kennt, bleibt bis zum Ausschaltpunkt offen – früh drosseln schaltet es nur früher
    ab; nach dem Zurücksetzen heizt es wieder voll. Die Schätzung sagt das ehrlich (kaum Gewinn)."""
    t0 = ts_of(MON, 7)
    eng, _, _ = house(tmp_path, rooms=[room("Wz", WZ, sensor=False), room("Bad", BAD, sensor=False)])
    strong(eng, kind="binary")
    assert eng.valves[WZ].kind == "binär"
    approach(eng, WZ, t0, 20.6)
    new = vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    assert len(new) == 1 and new[0]["valve_pred"] == 100.0 and new[0]["reliable"] == 1
    assert 0.0 <= new[0]["est_k"] <= 0.05
    # stetiges Ventil mit Integrator (bleibt am Soll offen): deutlich weniger Überschwingen
    eng2, _, _ = house(tmp_path / "pi")
    strong(eng2)
    approach(eng2, WZ, t0, 20.6)
    pi = vd.step(eng2, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    assert len(pi) == 1 and pi[0]["est_k"] >= 0.04 and pi[0]["est_k"] > 2 * new[0]["est_k"]


# ------------------------------------------------------------------ gemeinsam anfordern
def test_join_when_one_room_demands_and_boiler_cycles(tmp_path):
    eng, ha, _ = house(tmp_path)
    t0 = ts_of(MON, 10)
    burner(eng, t0)
    assert vd.cycling(eng.store, t0) == {"starts_h": 5, "run_min": 4.0, "cycling": True}
    new = vd.step(eng, t0, SAMPLE, [reading(WZ, 20.4, valve=80.0), reading(BAD, 20.9, valve=0.0)])
    assert len(new) == 1
    e = new[0]
    assert (e["room"], e["rule"], e["action"], e["setpoint"]) == (BAD, "gemeinsam", "anheben", 21.5)
    assert e["reliable"] == 1 and e["est_starts"] == 1.0 and e["starts_h"] == 5
    assert e["reason"] == "nur Wz fordert an, Kessel taktet (5 Starts/h) – Bad würde in 60 min ohnehin heizen"
    # weiter, solange Wz anfordert; zurück, sobald nicht mehr (nach der Haltezeit)
    assert vd.step(eng, t0 + 300, SAMPLE, [reading(WZ, 20.6, valve=70.0), reading(BAD, 20.85, valve=0.0)]) == []
    back = vd.step(eng, t0 + 600, SAMPLE, [reading(WZ, 21.0, valve=0.0), reading(BAD, 20.8, valve=0.0)])
    assert [(b["room"], b["action"], b["setpoint"]) for b in back] == [(BAD, "zurück", 21.0)]
    assert ha.calls == []


def test_join_needs_cycling_single_demand_and_coming_heat(tmp_path):
    eng, _, _ = house(tmp_path, rooms=[room("Wz", WZ), room("Bad", BAD), room("Kind", "climate.kind")])
    t0 = ts_of(MON, 10)
    kind = reading("climate.kind", 22.0, valve=0.0)
    calm = [reading(WZ, 20.4, valve=80.0), reading(BAD, 20.9, valve=0.0), kind]
    assert vd.step(eng, t0, SAMPLE, calm) == []  # Kessel taktet nicht
    burner(eng, t0, starts=2)
    assert not vd.cycling(eng.store, t0)["cycling"]
    eng2, _, _ = house(tmp_path / "b", rooms=[room("Wz", WZ), room("Bad", BAD), room("Kind", "climate.kind")])
    burner(eng2, t0)
    two = [reading(WZ, 20.4, valve=80.0), reading(BAD, 20.9, valve=40.0), kind]
    assert vd.step(eng2, t0, SAMPLE, two) == []  # zwei fordern schon an
    one = vd.step(eng2, t0 + 300, SAMPLE, [reading(WZ, 20.4, valve=80.0), reading(BAD, 20.9, valve=0.0), kind])
    assert [e["room"] for e in one] == [BAD]  # Kind ist zu warm, um in 60 min zu heizen
    # lange Läufe sind kein Takten
    eng3, _, _ = house(tmp_path / "c")
    burner(eng3, t0, starts=4, run_min=11, every_min=14)
    assert not vd.cycling(eng3.store, t0)["cycling"]


def test_join_ends_after_max_time(tmp_path):
    eng, _, _ = house(tmp_path)
    t0 = ts_of(MON, 10)
    burner(eng, t0)
    vd.step(eng, t0, SAMPLE, [reading(WZ, 20.4, valve=80.0), reading(BAD, 20.9, valve=0.0)])
    out = vd.step(eng, t0 + vd.JOIN_MAX_MIN * 60, SAMPLE, [reading(WZ, 20.8, valve=60.0), reading(BAD, 20.8, valve=0)])
    assert [(e["room"], e["action"]) for e in out] == [(BAD, "zurück")]


def test_cycling_from_starts_counter_without_burner_events(tmp_path):
    eng, _, _ = house(tmp_path)
    t0 = ts_of(MON, 10)
    for i, n in enumerate((100, 101, 102, 104)):
        eng.store.add_sample(t0 - 3000 + i * 900, {"burn_starts": n})
    assert vd.cycling(eng.store, t0) == {"starts_h": 4, "run_min": None, "cycling": True}


# ------------------------------------------------------------------ Protokoll, Auswertung
def test_actuals_summary_and_text(tmp_path):
    eng, _, _ = house(tmp_path)
    strong(eng)
    t0 = ts_of(MON, 7)
    approach(eng, WZ, t0, 20.6)
    vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    eng.store.add_room_samples((t0 + i * 300, WZ, round(20.6 + 0.065 * i, 3), 21.0, 50.0, None) for i in range(1, 13))
    burner(eng, t0 + 3 * 3600)
    vd.step(eng, t0 + 3 * 3600, SAMPLE, [reading(WZ, 20.4, valve=80.0), reading(BAD, 20.9, valve=0.0)])
    assert vd.fill_actuals(eng.store, t0 + 3 * 3600) == 0  # das Drosseln hat schon nachgetragen
    rows = eng.store.valve_dry_log(0)
    first = next(r for r in rows if r["action"] == "senken")
    assert first["act_over_k"] == 0.38 and first["checked"] == 1  # 21,38 °C gemessen: 0,38 K über dem Soll
    s = vd.summary(eng, t0 + 3 * 3600)
    assert s["dry_run"] and s["note"] == vd.NOTE
    assert s["interventions"] == {"drosseln": 1, "gemeinsam": 1}
    assert s["writes"] == len(rows) and s["writes_by"]["drosseln"] == 2  # senken + zurück (nach einer Stunde)
    assert s["effect"]["drosseln"]["reliable"] == 1 and s["effect"]["drosseln"]["act_over_k_mean"] == 0.38
    # die Schätzung erst, wenn das Überschwingen ohne Eingriff ein paar Mal richtig vorhergesagt war
    assert s["effect"]["drosseln"]["checked"] == 0 and s["effect"]["drosseln"]["est_k_mean"] is None
    assert s["effect"]["drosseln"]["check"]["n"] == 1 and s["examples"][1]["pred_over_k"] == first["pred_over_k"]
    assert s["effect"]["gemeinsam"]["starts_saved_est"] == 1.0
    assert [r["name"] for r in s["rooms"]] and {d["day"] for d in s["days"]} == {"2026-10-12"}
    assert s["examples"][0]["rule"] == "gemeinsam" and s["examples"][0]["name"] == "Bad"
    assert s["active"] and s["active"][0]["rule"] == "gemeinsam"
    assert s["rules"]["hold_min"] == 5 and s["counted_only"] == 0
    assert s["text"] == ("In 7 Tagen hätte HeatPilot 1× früh gedrosselt und 1× Räume gemeinsam anfordern lassen"
                         f" – {len(rows)} Schreibvorgänge, nichts davon geschrieben; Schätzung des Überschwingens erst, "
                         "wenn die Vorhersage ohne Eingriff nachgemessen stimmt (1 von 3 Nachmessungen); ≈ 1 "
                         "Brennerstarts weniger")
    eng2, _, _ = house(tmp_path / "b")
    assert vd.summary(eng2, t0)["text"] == ("Noch kein Eingriff – in 7 Tagen hätte HeatPilot an den Thermostaten "
                                            "nichts verstellt")


def test_estimate_shown_only_after_check(tmp_path):
    """Die Schätzung „≈ x K weniger Überschwingen“ zeigt die Auswertung je Raum erst, wenn das vorhergesagte
    Überschwingen ohne Eingriff mindestens dreimal nah (im Mittel ≤ 0,2 K) am eine Stunde später gemessenen lag."""
    eng, _, _ = house(tmp_path)
    t0 = ts_of(MON, 7)
    cases = [(WZ, 0.5, 0.45), (WZ, 0.6, 0.7), (WZ, 0.4, 0.4), (BAD, 0.9, 0.2), (BAD, 0.8, 0.1), (BAD, 0.7, 0.2)]
    for i, (c, pred, _) in enumerate(cases):
        eng.store.add_valve_dry({"ts": t0 - 86400 - i * 7200, "room": c, "rule": "drosseln", "action": "senken",
                                 "setpoint": 20.5, "base": 21.0, "est_k": 0.1, "pred_over_k": pred, "reliable": 1,
                                 "managed": 0})
    measured = {(c, pred): act for c, pred, act in cases}  # eine Stunde danach gemessen (es wurde nichts geschrieben)
    for row in eng.store.valve_dry_log(0):
        eng.store.set_valve_dry_actual(row["id"], measured[(row["room"], row["pred_over_k"])], None)
    s = vd.summary(eng, t0)
    d = s["effect"]["drosseln"]
    assert d["reliable"] == 6 and d["checked"] == 3 and d["est_k_mean"] == 0.1  # nur Wz: Bad lag daneben
    assert vd.checked_rooms(eng.store.valve_dry_log(0))[BAD] == {"n": 3, "mae_k": 0.63, "ok": False}
    assert "geschätzt ≈ 0,1 K weniger Überschwingen je Drosseln (3 belastbar)" in s["text"]
    assert {x["climate"]: x["est_checked"] for x in s["examples"]} == {WZ: True, BAD: False}


def test_state_survives_restart(tmp_path):
    eng, _, _ = house(tmp_path)
    strong(eng)
    t0 = ts_of(MON, 7)
    approach(eng, WZ, t0, 20.6)
    vd.step(eng, t0, SAMPLE, [reading(WZ, 20.6, valve=100.0), reading(BAD, 21.2, valve=0.0)])
    eng.__dict__.pop("_valve_dry")  # wie nach einem Neustart: aus der Datenbank
    assert vd.step(eng, t0 + 300, SAMPLE, [reading(WZ, 20.8, valve=100.0), reading(BAD, 21.2, valve=0.0)]) == []
    assert eng.store.get(vd.KV_STATE)["active"][WZ]["sp"] == 20.5


def test_ems_circuit_never_included(tmp_path):
    eng, _, _ = house(tmp_path, rooms=[room("Wz", WZ, ems=True), room("Bad", BAD)])
    assert vd.step(eng, ts_of(MON, 7), SAMPLE, [reading(WZ, 20.9), reading(BAD, 21.2, valve=0.0)]) == []


# ------------------------------------------------------------------ nichts schreiben
def test_tick_runs_dry_run_and_writes_nothing(tmp_path):
    for mode in ("beobachten", "heatpilot"):
        rooms = [room("Wz", WZ, managed=True), room("Bad", BAD)]
        eng, ha, loop = house(tmp_path / mode, rooms=rooms, cfg=Config(control_mode=mode))
        eng.found.boiler = {"flow_temp": "sensor.flow", "heating_pump": "binary_sensor.pump"}
        eng.found.outdoor = ["sensor.out"]
        eng.states.update({"sensor.flow": {"state": "60", "attributes": {}},
                           "binary_sensor.pump": {"state": "on", "attributes": {}},
                           "sensor.out": {"state": "5", "attributes": {}},
                           "sensor.wz_heat": {"state": "60", "attributes": {}},
                           "sensor.bad_heat": {"state": "0", "attributes": {}}})
        eng.states["sensor.wz_heat"]["state"] = "90"
        strong(eng)
        climate(eng, BAD, 21.3, 21.0, target_temp_step=0.5)
        t0 = ts_of(MON, 12)
        for i in range(4):  # Wz heizt auf: 20,3 → 20,75 °C in 15 min
            climate(eng, WZ, round(20.3 + 0.15 * i, 2), 21.0, target_temp_step=0.5)
            eng.tick(t0 + i * 300)
            run(loop.apply())
        rows = eng.store.valve_dry_log(0)
        assert any(r["rule"] == "drosseln" and r["setpoint"] == 20.5 for r in rows), mode
        if mode == "beobachten":
            assert ha.calls == []
        else:  # die Raumsteuerung schreibt wie bisher ihr Wunsch-Soll – der Trockenlauf nichts dazu
            assert all(c[3].get("temperature") != 20.5 for c in ha.calls)
            assert len(eng.store.control_log(50)) == len(ha.calls)
    src = inspect.getsource(vd)
    for forbidden in ("await ", ".apply(", "ha.call", "set_temperature", "Control("):
        assert forbidden not in src, forbidden


# ------------------------------------------------------------------ künstliches Haus über einen Tag
def _house_day(eng, start: int, days: int = 1) -> list[dict]:
    """Drei Räume mit künstlichen Thermostaten und ein Kessel, der taktet, wenn nur ein Raum anfordert.

    Wz (großer Heizkörper, Thermostat mit Integrator) heizt morgens von der Nacht auf (Soll 18 → 21 °C um 6 Uhr) und
    kommt mit offenem Ventil am Soll an; Bad und Kind (P-Regler) halten 21 bzw. 20 °C. Der Kessel läuft bei mehreren
    Anforderungen durch, bei einer taktet er (4 min an, 8 min aus). Alle 5 min ein Messpunkt in die Datenbank und ein Schritt des Trockenlaufs."""
    sims = {WZ: (SimRoom(a=0.08, b=0.03, t0=18.5), THERMOSTATS["pi"], lambda h: 21.0 if 6 <= h < 22 else 18.0),
            BAD: (SimRoom(a=0.02, b=0.02, t0=21.0), Thermostat(), lambda h: 21.0),
            "climate.kind": (SimRoom(a=0.02, b=0.025, t0=20.3), Thermostat(), lambda h: 20.0)}
    state = {c: {"t": r.t0, "v": 0.0, "i": 0.0, "hist": []} for c, (r, _, _) in sims.items()}
    burner_on, since, entries = False, start, []
    for m in range(days * 1440):
        ts = start + m * 60
        hour = (m // 60) % 24
        demand = 0
        for c, (r, th, sp_at) in sims.items():
            s = state[c]
            e = sp_at(hour) - s["t"]
            s["hist"].append(e)
            delay = round(th.dead_min)
            ed = s["hist"][-1 - delay] if len(s["hist"]) > delay else e
            edb = 0.0 if abs(ed) <= th.deadband else ed - (th.deadband if ed > 0 else -th.deadband)
            s["i"] = min(max(s["i"] + th.ki * edb, 0.0), 100.0)
            u = min(max(th.kp * edb + s["i"], 0.0), 100.0)
            s["v"] += (u - s["v"]) * (1 - 2.718281828 ** (-1 / (th.tau_up_min if u > s["v"] else th.tau_down_min)))
            demand += s["v"] > vm.OPEN_PCT
        if demand >= 2:
            want = True
        elif demand == 1:
            want = (ts - since) < 240 if burner_on else (ts - since) >= 480
        else:
            want = False
        if want != burner_on:
            burner_on, since = want, ts
            eng.store.add_burner_event(ts, want)
        flow = 60.0 if burner_on else 40.0
        for c, (r, _, _) in sims.items():
            s = state[c]
            heat = r.a * (flow - s["t"]) * s["v"] / 100 if burner_on else 0.0
            s["t"] += (heat + r.b * (r.t_out - s["t"])) / 60
        if m % 5 == 0:
            readings = [(c, round(state[c]["t"], 2), sims[c][2](hour), round(state[c]["v"], 1), None) for c in sims]
            eng.store.add_room_samples((ts, *x) for x in readings)
            entries += vd.step(eng, ts, {"flow": flow, "heating": int(burner_on), "pump_on": int(burner_on),
                                         "t_out": 5.0}, readings)
    return entries


def test_synthetic_house_day(tmp_path):
    rooms = [room("Wz", WZ), room("Bad", BAD), room("Kind", "climate.kind")]
    eng, ha, _ = house(tmp_path, rooms=rooms)
    for c, (a, b) in {WZ: (0.08, 0.03), BAD: (0.02, 0.02), "climate.kind": (0.02, 0.025)}.items():
        eng.rooms[c] = slow_room(a=a, b=b)
    eng.valves[WZ] = valve_model("pi")
    start = ts_of(MON, 0)
    entries = _house_day(eng, start)
    rules = {e["rule"] for e in entries if e["action"] != "zurück"}
    assert rules == {"drosseln", "gemeinsam"}, entries
    assert any(e["rule"] == "drosseln" and e["room"] == WZ and 6 <= (e["ts"] - start) / 3600 < 12 for e in entries)
    by: dict[str, list] = {}
    for e in entries:
        by.setdefault(e["room"], []).append(e)
    for c, es in by.items():
        for a, b in pairwise(es):
            assert b["ts"] - a["ts"] >= 300, (c, a, b)  # Haltezeit
            assert b["setpoint"] != a["setpoint"] or b["base"] != a["base"], (c, a, b)  # nur bei Änderung
        for e in es:
            assert abs(e["setpoint"] * 2 - round(e["setpoint"] * 2)) < 1e-9  # Raster 0,5 K
            assert abs(e["setpoint"] - e["base"]) in (0.0, 0.5)
    s = vd.summary(eng, start + 86400 - 60)
    assert s["interventions"]["drosseln"] >= 1 and s["interventions"]["gemeinsam"] >= 1
    assert s["writes"] == len(entries) and s["counted_only"] == 0
    assert ha.calls == []


def test_files_and_store_columns():
    assert Path(vd.__file__).read_text(encoding="utf-8").startswith('"""Ventile im Trockenlauf (seit 0.39)')
    rows, _ = simulate(Thermostat(), SimRoom(), Scenario(days=1), ts_of(MON, 0))
    assert rows  # Simulator liefert Messpunkte im 5-min-Takt


# ------------------------------------------------------------------ Abfragen
def test_api_fields(tmp_path):
    import asyncio
    import json

    from aiohttp.test_utils import TestClient, TestServer
    from heatpilot import diagnostics
    from heatpilot.web import make_app as web_app
    from test_control34 import make_app

    app, ha, _ = make_app(tmp_path)
    eng = app.eng
    import time
    now = int(time.time())  # api/heat bewertet das Gütemaß der letzten 14 Tage bis heute
    eng.valves[WZ] = valve_model("binary", today=now)  # „Wz“ hat keinen Ventilsensor: nur an/aus
    eng.rooms[WZ] = slow_room(a=0.08, b=0.03)
    approach(eng, WZ, now - 600, 20.6)
    vd.step(eng, now - 600, SAMPLE, [reading(WZ, 20.6, valve=100.0)])  # die Auswertung zählt 7 Tage

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            heat = await (await client.get("/api/heat")).json()
            v = heat["rooms"][0]["valve_model"]
            assert v["kind"] == "binär" and v["state"] == "belastbar" and v["switch"]["e_on"] is not None
            assert v["text"].startswith("schaltet ein bei ") and v["step_k"] == 0.5 and v["step_source"] == "angenommen"
            assert {"curve", "e0", "e50", "e100", "response", "pi", "error", "reasons", "skipped_h"} <= set(v)
            summ = await (await client.get("/api/summary")).json()
            r = summ["rooms"][0]
            assert r["climate"] == WZ and r["valve_model"]["kind"] == "binär"
            prev = await (await client.get("/api/preview")).json()
            valves = prev["valves"]
            assert valves["dry_run"] and valves["interventions"]["drosseln"] == 1 and valves["writes"] == 1
            assert valves["examples"][0]["name"] == "Wz" and valves["rules"]["hold_min"] == 5
        finally:
            await client.close()

    asyncio.run(go())
    data = diagnostics.export(app, 30)
    assert data["ventile"]["modelle"][WZ]["kind"] == "binär"
    assert data["ventile"]["trockenlauf"]["zeilen"]
    bundle = json.dumps(diagnostics.bundle(app), default=str)
    assert "valve_dry_log" in bundle and "53.9" not in bundle
    assert ha.calls == [] or all(c[3].get("temperature") != 20.5 for c in ha.calls)


def test_room_without_samples_starts_live(tmp_path):
    eng, _, _ = house(tmp_path, models=False)
    eng.valves[WZ] = vm.ValveModel()
    eng._last_room_step = ts_of(MON, 7)
    assert eng.learn_valves() is False and eng.valves[WZ].learned_until == ts_of(MON, 7)
