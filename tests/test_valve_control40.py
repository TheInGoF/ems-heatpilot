"""0.40 Ventile führen: früh drosseln, gemeinsam anfordern – wirklich geschrieben, mit eigenem Schalter.

Sicherheitsregeln (jede hat einen Test): Schalter aus → dieselben Aufrufe wie 0.39 (nur Trockenlauf); geschrieben wird
nur über ``Control.apply`` (``climate.set_temperature`` auf die Schreibziele) bei „HeatPilot steuert“ für Räume mit
„macht mit“ und belastbaren Modellen; höchstens ein Schritt vom Wunschsoll (Schrittweite des Thermostats, 5–25 °C);
nie drosseln in nacht/abwesend und nie unter Soll − 0,3 K (sinkt der Raum darunter, sofort zurück); Haltezeit, nur
bei Änderung; Phasenwechsel und Hand gehen vor; Heizkreis von EMS-ESP nie; Raumregler-Gruppe schreibt nur den Regler;
Fehler wie die übrige Raumsteuerung. Dazu ein künstliches Haus über einen Tag mit geschlossenem Regelkreis
(Thermostate aus ``sim_valve``, die das Geschriebene übernehmen – wie ``sim_house`` für das Haus).
"""

import asyncio
import json
import math
import time
from itertools import pairwise

from heatpilot import diagnostics, readiness
from heatpilot import valve_control as vc
from heatpilot import valve_dry as vd
from heatpilot import valve_model as vm
from heatpilot.config import Config, validate
from heatpilot.controlloop import MAX_FAILS, RETRY_MIN, ControlLoop
from heatpilot.discovery import Room
from heatpilot.models import RoomModel
from sim_valve import Room as SimRoom
from sim_valve import Thermostat
from test_control import FakeHA
from test_control34 import MON, climate, run, slow_room, ts_of
from test_valve_dry39 import BAD, SAMPLE, THERMOSTATS, WZ, approach, burner, house, room, valve_model

T0 = ts_of(MON, 7)
KIND = "climate.kind"
ON = dict(control_mode="heatpilot", valve_control=True)


class EchoHA(FakeHA):
    """Wie Home Assistant: Was geschrieben wurde, steht danach im Zustand des Thermostats."""

    def __init__(self, eng, fail=()):
        super().__init__(fail)
        self.eng = eng

    async def call(self, payload, timeout=0):
        await super().call(payload, timeout)
        st = self.eng.states.setdefault(payload["target"]["entity_id"], {"state": "heat", "attributes": {}})
        data = payload["service_data"]
        if "temperature" in data:
            st["attributes"]["temperature"] = data["temperature"]
        if "hvac_mode" in data:
            st["state"] = data["hvac_mode"]


def setup(tmp_path, rooms=None, cfg=None, step=0.5, today=T0):
    """Wz (großer Heizkörper, Thermostat mit Integrator – überschwingt) und Bad (P-Regler), beide „macht mit“, belastbare
    Modelle; Schalter an. Ein erster Takt schreibt das Wunschsoll (Komfort 21 °C) – danach steht es am Thermostat."""
    rooms = rooms or [room("Wz", WZ, managed=True), room("Bad", BAD, managed=True)]
    eng, _, loop = house(tmp_path, rooms=rooms, cfg=Config(**ON) if cfg is None else cfg, step=step)
    ha = EchoHA(eng)
    loop.control.ha = ha
    for r in rooms:
        eng.valves[r.climate] = valve_model("p" if r.heating_power else "binary", today=today)
    eng.rooms[WZ] = slow_room(a=0.08, b=0.03)
    eng.valves[WZ] = valve_model("pi", today=today)
    tick(loop, T0 - 900, {r.climate: (20.3, 0.0) for r in rooms})
    ha.calls.clear()
    return eng, ha, loop


def readings(eng, values: dict) -> list[tuple]:
    """Messpunkte (climate → (Temperatur, Ventil)); das Soll so, wie es am Thermostat steht."""
    return [(c, t, (eng.states.get(c) or {}).get("attributes", {}).get("temperature"), v, None)
            for c, (t, v) in values.items()]


def tick(loop, ts, values, sample=SAMPLE):
    """Ein Takt der Regelschleife: planen, schreiben. Gibt die vorgemerkten Schreibvorgänge zurück."""
    loop.plan(ts, sample, readings(loop.eng, values))
    pending = list(loop.pending)
    run(loop.apply())
    return pending


def throttle_ready(eng, ts=T0, temp=20.72):
    """Wz nähert sich dem Soll: vor einer Stunde stieg es von 18 auf 21 °C, seitdem 2,4 K/h – laut Modellen gleich da,
    Ventil noch offen. Frühere Messpunkte des Raums weg (sonst mischten sich zwei Anstiege im gemessenen Verlauf)."""
    eng.store.db.execute("DELETE FROM room_samples WHERE room = ?", (WZ,))
    approach(eng, WZ, ts, temp, rise_min=60)


def written(eng):
    return [r for r in eng.store.valve_dry_log(0) if r["written"]]


def temps(ha):
    return [(c[2], c[3]["temperature"]) for c in ha.calls if c[1] == "set_temperature"]


# ------------------------------------------------------------------ Schalter
def test_switch_default_off_and_strict():
    assert Config().valve_control is False and not Config().guides_valves()
    assert Config.from_raw({"valve_control": "true", "control_mode": "heatpilot"}).valve_control is False
    assert Config.from_raw({"valve_control": True, "control_mode": "heatpilot"}).guides_valves()
    assert not Config.from_raw({"valve_control": True}).guides_valves()  # nur mit „HeatPilot steuert“
    ok, errors = validate({"valve_control": "on"})
    assert errors == [] and ok["valve_control"] is True
    assert validate({"valve_control": ""})[0]["valve_control"] is False
    assert Config().to_raw()["valve_control"] is False


def test_switch_off_writes_exactly_as_before(tmp_path):
    """Aus: die Regelschleife schreibt nur ihr Wunschsoll – der Trockenlauf zählt, was HeatPilot täte."""
    eng, ha, loop = setup(tmp_path, cfg=Config(control_mode="heatpilot"))
    throttle_ready(eng)
    assert tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    vd.step(eng, T0, SAMPLE, readings(eng, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}), loop.guided_rooms())
    assert ha.calls == [] and loop.guided_rooms() == set() and written(eng) == []
    dry = eng.store.valve_dry_log(0)
    assert [(r["room"], r["action"], r["setpoint"], r["written"]) for r in dry] == [(WZ, "senken", 20.5, 0)]
    # an, aber „Beobachten“: auch nichts – und ein Zustand der Führung bleibt nicht stehen
    eng2, ha2, loop2 = setup(tmp_path / "b", cfg=Config(valve_control=True))
    throttle_ready(eng2)
    assert tick(loop2, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == [] and ha2.calls == []
    assert loop2.guide.st == {k: {} for k in vc.KEYS}
    # dieselbe Lage mit Schalter an: genau ein Schritt mehr
    eng3, ha3, loop3 = setup(tmp_path / "c")
    throttle_ready(eng3)
    tick(loop3, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert temps(ha3) == [(WZ, 20.5)]


# ------------------------------------------------------------------ früh drosseln
def test_throttle_one_step_then_back_when_reached(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    out = tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert [(c, sp) for c, sp, _ in out] == [(WZ, 20.5)]
    assert out[0][2].startswith("Ventil: früh drosseln (erreicht 21,0 °C in ≈ ") and "% offen)" in out[0][2]
    assert ha.calls == [("climate", "set_temperature", WZ, {"temperature": 20.5})]
    assert eng.store.control_log(1)[0]["reason"] == out[0][2]
    e = written(eng)
    assert [(r["rule"], r["action"], r["setpoint"], r["base"], r["managed"]) for r in e] == [
        ("drosseln", "senken", 20.5, 21.0, 1)]
    assert e[0]["reliable"] == 1 and e[0]["pred_over_k"] is not None  # mit Eingriff vorhergesagt – wird nachgemessen
    info = loop.rooms[WZ]["valve"]
    assert (info["rule"], info["setpoint"], info["base"], info["back_latest"]) == ("drosseln", 20.5, 21.0, False)
    assert info["back_at"] and loop.rooms[WZ]["valve_guided"] and not loop.rooms[WZ]["manual"]
    # nur bei Änderung: derselbe Zustand schreibt nichts; am Soll in der Haltezeit noch nicht zurück
    assert tick(loop, T0 + 120, {WZ: (20.85, 90.0), BAD: (21.2, 0.0)}) == []
    assert tick(loop, T0 + 240, {WZ: (21.0, 60.0), BAD: (21.2, 0.0)}) == []
    back = tick(loop, T0 + 300, {WZ: (21.05, 40.0), BAD: (21.2, 0.0)})
    assert back == [(WZ, 21.0, "Ventil: zurück (Soll erreicht)")] and temps(ha)[-1] == (WZ, 21.0)
    assert loop.guide.st["active"] == {} and loop.guide.st["lock"] == {WZ: 21.0}
    assert [r["action"] for r in written(eng)] == ["senken", "zurück"]
    # danach erst wieder nach neuem Soll oder deutlichem Auskühlen: kein neues Drosseln
    throttle_ready(eng, T0 + 900, 20.75)
    assert tick(loop, T0 + 900, {WZ: (20.75, 100.0), BAD: (21.2, 0.0)}) == []


def test_throttle_back_after_60_min(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert tick(loop, T0 + 59 * 60, {WZ: (20.9, 50.0), BAD: (21.2, 0.0)}) == []
    assert tick(loop, T0 + 60 * 60, {WZ: (20.9, 50.0), BAD: (21.2, 0.0)}) == [
        (WZ, 21.0, f"Ventil: zurück (nach {vd.THROTTLE_MAX_MIN} min)")]


def test_never_throttle_below_base_minus_03_and_back_at_once(tmp_path):
    """0,4 K unter dem Soll drosselt der Trockenlauf – die Führung nicht. Fällt ein gedrosselter Raum unter Soll − 0,3 K,
    geht er sofort zurück, auch in der Haltezeit: Wer Wärme braucht, bekommt sie."""
    eng, ha, loop = setup(tmp_path)
    approach(eng, WZ, T0, 20.6, rise_min=60)
    r = vd._Room(WZ, "Wz", True, 20.6, 21.0, 100.0, 0.5, eng.rooms[WZ], eng.valves[WZ], True)
    assert vd._throttle(eng, r, SAMPLE, None, T0, False) is not None  # die Regel des Trockenlaufs allein
    assert tick(loop, T0, {WZ: (20.6, 100.0), BAD: (21.2, 0.0)}) == [] and ha.calls == []
    throttle_ready(eng, T0 + 300)
    assert [sp for _, sp, _ in tick(loop, T0 + 300, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})] == [20.5]
    # Fenster auf: 20,6 °C eine Minute später – in der Haltezeit, trotzdem sofort zurück
    out = tick(loop, T0 + 360, {WZ: (20.6, 100.0), BAD: (21.2, 0.0)})
    assert out == [(WZ, 21.0, "Ventil: zurück (Raum braucht Wärme)")] and temps(ha)[-1] == (WZ, 21.0)


def test_never_throttle_at_night_or_when_away(tmp_path):
    eng, ha, loop = setup(tmp_path)
    night = ts_of(MON, 22)
    tick(loop, night - 600, {WZ: (17.5, 0.0), BAD: (18.5, 0.0)})  # Phasenwechsel: Nacht 18 °C
    assert temps(ha) == [(WZ, 18.0), (BAD, 18.0)]
    approach(eng, WZ, night, 17.72, target=18.0, rise_min=None)
    r = vd._Room(WZ, "Wz", True, 17.72, 18.0, 100.0, 0.5, eng.rooms[WZ], eng.valves[WZ], True)
    assert vd._throttle(eng, r, SAMPLE, None, night, False) is not None
    n = len(ha.calls)
    assert tick(loop, night, {WZ: (17.72, 100.0), BAD: (18.5, 0.0)}) == [] and len(ha.calls) == n
    assert loop.rooms[WZ]["phase"] == "nacht" and loop.rooms[WZ]["valve"] is None
    # niemand zu Hause: Abwesenheit 19 °C
    eng2, ha2, loop2 = setup(tmp_path / "b")
    away = {**SAMPLE, "home_count": 0}
    tick(loop2, T0 - 600, {WZ: (18.5, 0.0), BAD: (19.5, 0.0)}, away)
    assert loop2.rooms[WZ]["phase"] == "abwesend" and temps(ha2) == [(WZ, 19.0), (BAD, 19.0)]
    approach(eng2, WZ, T0, 18.72, target=19.0, rise_min=None)
    assert tick(loop2, T0, {WZ: (18.72, 100.0), BAD: (19.5, 0.0)}, away) == []


def test_phase_change_has_priority(tmp_path):
    """Gedrosselt, dann Nacht: sofort das neue Wunschsoll mit dem Grund der Steuerung, die Führung beginnt neu."""
    eng, ha, loop = setup(tmp_path)
    t = ts_of(MON, 20, 50)
    throttle_ready(eng, t)
    tick(loop, t, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert temps(ha) == [(WZ, 20.5)]
    out = tick(loop, ts_of(MON, 21, 1), {WZ: (20.9, 60.0), BAD: (21.2, 0.0)})
    assert [(c, sp) for c, sp, _ in out] == [(WZ, 18.0), (BAD, 18.0)] and out[0][2].startswith("nacht:")
    assert loop.guide.st["active"] == {} and [r["action"] for r in written(eng)] == ["senken"]  # kein „zurück“


def test_hand_before_automation(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    climate(eng, WZ, 20.8, 22.0, target_temp_step=0.5)  # jemand dreht in der App auf 22 °C
    assert tick(loop, T0 + 600, {WZ: (20.8, 100.0), BAD: (21.2, 0.0)}) == []
    assert loop.rooms[WZ]["manual"] and loop.guide.st["active"] == {} and WZ in loop.guide.st["hands"]
    # auch genau aufs Wunschsoll gedreht: Hand – die Führung fasst den Raum bis zum Phasenwechsel nicht an
    climate(eng, WZ, 20.8, 21.0, target_temp_step=0.5)
    throttle_ready(eng, T0 + 1200)
    assert tick(loop, T0 + 1200, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == [] and len(ha.calls) == 1
    assert written(eng)[-1]["action"] == "senken"  # kein Zurückschreiben, die Hand gilt
    # Phasenwechsel (Nacht): wieder frei
    tick(loop, ts_of(MON, 21, 1), {WZ: (20.9, 0.0), BAD: (21.2, 0.0)})
    assert WZ not in loop.guide.st["hands"]


def test_boost_is_hands_off(tmp_path):
    eng, ha, loop = setup(tmp_path)
    climate(eng, WZ, 20.7, 21.0, target_temp_step=0.5)
    run(loop.control.boost([WZ], 23, 30))
    throttle_ready(eng)
    assert tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    assert WZ in loop.guide.st["hands"] and all(sp != 20.5 for _, sp in temps(ha))


def test_hold_time_also_right_after_switching_on(tmp_path):
    """Schreibt die Steuerung mit Schalter aus (Phasenwechsel) und wird er gleich danach eingeschaltet, gilt die
    Haltezeit trotzdem."""
    eng, ha, loop = setup(tmp_path, cfg=Config(control_mode="heatpilot"))
    assert loop.guide.st["last"]  # auch ohne Schalter: jede Schreibzeit gemerkt
    eng.cfg.valve_control = True
    throttle_ready(eng, T0 - 900 + 120)
    assert tick(loop, T0 - 900 + 120, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    throttle_ready(eng, T0 - 900 + 300)
    assert [sp for _, sp, _ in tick(loop, T0 - 900 + 300, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})] == [20.5]


def test_hold_time_after_any_write(tmp_path):
    eng, ha, loop = setup(tmp_path)
    loop.guide.st["last"][WZ] = T0 - 120  # vor 2 min geschrieben (z. B. Phasenwechsel)
    throttle_ready(eng)
    assert tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    throttle_ready(eng, T0 + 180, 20.78)
    assert [sp for _, sp, _ in tick(loop, T0 + 180, {WZ: (20.78, 100.0), BAD: (21.2, 0.0)})] == [20.5]


# ------------------------------------------------------------------ gemeinsam anfordern
def test_join_one_step_up_and_back_when_trigger_ends(tmp_path):
    rooms = [room("Wz", WZ, managed=False), room("Bad", BAD, managed=True), room("Kind", KIND, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    burner(eng, T0)
    out = tick(loop, T0, {WZ: (20.4, 80.0), BAD: (20.9, 0.0), KIND: (22.4, 0.0)})
    assert [(c, sp) for c, sp, _ in out] == [(BAD, 21.5)]  # Wz macht nicht mit, zählt aber als anfordernd
    assert out[0][2] == ("Ventil: gemeinsam anfordern (nur Wz fordert an, Kessel taktet (5 Starts/h) – Bad würde in "
                         "60 min ohnehin heizen)")
    info = loop.rooms[BAD]["valve"]
    assert info["rule"] == "gemeinsam" and info["back_latest"] and info["setpoint"] == 21.5
    assert tick(loop, T0 + 300, {WZ: (20.6, 70.0), BAD: (20.95, 40.0), KIND: (22.4, 0.0)}) == []
    back = tick(loop, T0 + 600, {WZ: (21.0, 0.0), BAD: (21.2, 30.0), KIND: (22.3, 0.0)})
    assert back == [(BAD, 21.0, "Ventil: zurück (Wz fordert nicht mehr an)")]
    assert [(r["room"], r["rule"], r["action"], r["setpoint"]) for r in written(eng)] == [
        (BAD, "gemeinsam", "anheben", 21.5), (BAD, "gemeinsam", "zurück", 21.0)]
    assert all(c != WZ for c, _ in temps(ha))  # der Raum ohne „macht mit“ wird nie beschrieben


def test_join_no_chain_from_a_raised_room(tmp_path):
    """Fordert danach nur der Raum an, den HeatPilot selbst angehoben hat, löst er kein neues Anheben aus – sein Ventil
    ist ja seinetwegen offen."""
    rooms = [room("Wz", WZ, managed=False), room("Bad", BAD, managed=True), room("Kind", KIND, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    burner(eng, T0)
    assert [c for c, _, _ in tick(loop, T0, {WZ: (20.4, 80.0), BAD: (20.9, 0.0), KIND: (21.6, 0.0)})] == [BAD]
    out = tick(loop, T0 + 600, {WZ: (21.0, 0.0), BAD: (21.1, 35.0), KIND: (20.9, 0.0)})
    assert out == [(BAD, 21.0, "Ventil: zurück (Wz fordert nicht mehr an)")]  # Kind wird nicht angehoben
    r = vd._Room(KIND, "Kind", True, 20.9, 21.0, 0.0, 0.5, eng.rooms[KIND], eng.valves[KIND], True)
    assert vd._would_heat(eng, r, SAMPLE, T0 + 600)  # die Regel allein hätte Kind angehoben


def test_join_needs_outdoor_temperature_and_ends_after_60_min(tmp_path):
    rooms = [room("Wz", WZ, managed=False), room("Bad", BAD, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    burner(eng, T0)
    no_out = {**SAMPLE, "t_out": None}
    assert tick(loop, T0, {WZ: (20.4, 80.0), BAD: (20.9, 0.0)}, no_out) == []  # „laut Raummodell“ geht nicht
    tick(loop, T0 + 300, {WZ: (20.4, 80.0), BAD: (20.9, 0.0)})
    assert temps(ha) == [(BAD, 21.5)]
    out = tick(loop, T0 + 300 + vd.JOIN_MAX_MIN * 60, {WZ: (20.8, 60.0), BAD: (21.3, 0.0)})
    assert out == [(BAD, 21.0, f"Ventil: zurück (nach {vd.JOIN_MAX_MIN} min)")]


# ------------------------------------------------------------------ nur belastbar, nur wo erlaubt
def test_only_reliable_models(tmp_path):
    eng, ha, loop = setup(tmp_path)
    eng.valves[WZ] = vm.ValveModel()  # lernt noch
    throttle_ready(eng)
    assert tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    assert "Ventilmodell lernt noch" in loop.guide.why[WZ] and WZ not in loop.guided_rooms()
    eng.valves[WZ] = valve_model("pi", today=T0)
    eng.rooms[WZ] = RoomModel("Wz")  # Raummodell nicht plausibel
    assert tick(loop, T0 + 300, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    assert loop.guide.why[WZ].startswith("Raummodell lernt noch")
    # läuft ein Eingriff und das Modell ist nicht mehr belastbar: sofort zurück
    eng.rooms[WZ] = slow_room(a=0.08, b=0.03)
    throttle_ready(eng, T0 + 600)
    assert [sp for _, sp, _ in tick(loop, T0 + 600, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})] == [20.5]
    eng.valves[WZ] = vm.ValveModel()
    out = tick(loop, T0 + 660, {WZ: (20.8, 100.0), BAD: (21.2, 0.0)})
    assert out[0][:2] == (WZ, 21.0) and out[0][2].startswith("Ventil: zurück (Ventilmodell lernt noch")


def test_switch_off_while_active_returns_at_once(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    eng.cfg.valve_control = False
    assert tick(loop, T0 + 60, {WZ: (20.8, 100.0), BAD: (21.2, 0.0)}) == [
        (WZ, 21.0, "Ventil: zurück (Ventile führen aus)")]
    assert loop.guide.st["active"] == {} and temps(ha)[-1] == (WZ, 21.0)


def test_pause_blocks_guided_writes(tmp_path):
    eng, ha, loop = setup(tmp_path)
    loop.pause(1)
    throttle_ready(eng)
    tick(loop, int(time.time()), {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert ha.calls == [] and loop.guide.st["active"] == {}


def test_ems_circuit_never(tmp_path):
    rooms = [Room("Regler", WZ, managed=True, ems=True, comfort=21.0, platform="mqtt",
                  heating_power="sensor.wz_heat"), room("Bad", BAD, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    ok, why = vc.eligible(eng, rooms[0], T0)
    assert not ok and "EMS-ESP" in why and all(c[2] != WZ for c in ha.calls)


def test_controller_group_writes_only_the_controller(tmp_path):
    wand = Room("Wz", WZ, controller=True, managed=True, comfort=21.0, platform="homekit_controller",
                heating_power="sensor.wz_heat",
                members=[{"climate": "climate.v1", "heating_power": "sensor.v1", "humidity": None, "temp_sensor": None,
                          "platform": "homekit_controller"}])
    eng, ha, loop = setup(tmp_path, rooms=[wand, room("Bad", BAD, managed=True)])
    climate(eng, "climate.v1", 22.0, 21.0, target_temp_step=0.5)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert [c[2] for c in ha.calls] == [WZ] and temps(ha) == [(WZ, 20.5)]


def test_step_of_the_device(tmp_path):
    eng, ha, loop = setup(tmp_path, step=0.1)
    throttle_ready(eng, T0, 20.85)
    tick(loop, T0, {WZ: (20.85, 100.0), BAD: (21.2, 0.0)})
    assert temps(ha) == [(WZ, 20.9)]  # volle Auflösung: ein Schritt von 0,1 K, nicht auf 0,5 K gerundet
    eng2, ha2, loop2 = setup(tmp_path / "b", step=1.0)
    throttle_ready(eng2)
    assert tick(loop2, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    assert loop2.guide.why[WZ] == "Schritt 1,0 K – zu grob zum Führen (höchstens 0,5 K)"


def test_one_step_limits():
    assert vc.one_step(21.0, 0.5, -1) == 20.5 and vc.one_step(21.0, 0.1, 1) == 21.1
    assert vc.one_step(25.0, 0.5, 1) is None and vc.one_step(5.0, 0.5, -1) is None  # 5–25 °C
    assert vc.one_step(20.5, 0.3, -1) is None  # Raster passt nicht: lieber nichts als mehr als ein Schritt
    assert vc.one_step(24.8, 0.5, 1) == 25.0 and vc.one_step(24.9, 0.1, 1) == 25.0  # an der Grenze: weniger, nie mehr


def test_write_error_like_room_control(tmp_path):
    eng, ha, loop = setup(tmp_path)
    ha.fail.add(WZ)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    st = loop.state[WZ]
    assert st["fails"] == 1 and st["retry_at"] == T0 + RETRY_MIN * 60 and st["last_written"] == 21.0
    assert loop.guide.st["active"] == {} and written(eng) == []  # erst ein Erfolg macht den Schritt gültig
    throttle_ready(eng, T0 + 300, 20.75)
    assert tick(loop, T0 + 300, {WZ: (20.75, 100.0), BAD: (21.2, 0.0)}) == []  # Wartezeit
    for i in (1, 2):
        throttle_ready(eng, T0 + i * (RETRY_MIN + 1) * 60, 20.72)
        tick(loop, T0 + i * (RETRY_MIN + 1) * 60, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert loop.state[WZ]["fails"] == MAX_FAILS and any("Schreibfehler" in w for w in loop.warnings())
    ha.fail.clear()
    t = T0 + 3 * (RETRY_MIN + 1) * 60
    throttle_ready(eng, t, 20.72)
    tick(loop, t, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert temps(ha) == [(WZ, 20.5)] and loop.state[WZ]["fails"] == 0 and len(written(eng)) == 1


def test_only_through_control_apply():
    import inspect

    src = inspect.getsource(vc).replace(vc.__doc__, "")  # der Code, nicht die Beschreibung des Wegs
    for forbidden in ("await ", ".apply(", "ha.call", "set_temperature", "Control(", "_call("):
        assert forbidden not in src, forbidden


def test_state_survives_restart_and_dry_run_skips_guided_rooms(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    values = {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}
    tick(loop, T0, values)
    assert vd.step(eng, T0, SAMPLE, readings(eng, values), loop.guided_rooms()) == []  # Wz geführt: kein Trockenlauf
    again = ControlLoop(eng, loop.control)
    assert again.guide.st["active"][WZ]["sp"] == 20.5
    eng.control_loop = again
    assert tick(again, T0 + 120, {WZ: (20.85, 90.0), BAD: (21.2, 0.0)}) == []


# ------------------------------------------------------------------ Anzeige, Abfragen
def test_readiness_block(tmp_path):
    rooms = [room("Wz", WZ, managed=True), room("Bad", BAD, managed=True), room("Kind", KIND, managed=True),
             room("Flur", "climate.flur")]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    eng.valves[KIND] = vm.ValveModel()
    v = readiness.assess(eng, T0)["valves"]
    assert v["switch"] and v["active"] and v["level"] == "eingeschränkt"
    assert v["headline"] == "Läuft – führt 2 Räume (Wz und Bad); Kind lernt noch."
    by = {r["name"]: r for r in v["rooms"]}
    assert set(by) == {"Wz", "Bad", "Kind"} and by["Kind"]["state"] == "lernt"
    assert by["Kind"]["text"].startswith("Ventilmodell lernt noch (") and by["Wz"]["text"].endswith("Schritt 0,5 K")
    assert v["counts"] == {"managed": 3, "ready": 2, "learning": 1} and v["notes"] == [vc.NOTE_RULES]
    eng.cfg.valve_control = False
    assert readiness.assess(eng, T0)["valves"]["headline"].startswith("Aus – 2 Räume wären bereit (Wz und Bad)")
    eng.cfg.valve_control, eng.cfg.control_mode = True, "beobachten"
    assert readiness.assess(eng, T0)["valves"]["headline"].startswith("Eingeschaltet, wirkt erst mit „HeatPilot steuert“")
    for c in (WZ, BAD):
        eng.valves[c] = vm.ValveModel()
    assert readiness.assess(eng, T0)["valves"]["headline"] == (
        "Nicht bereit – noch kein Raum mit belastbarem Raum- und Ventilmodell (3 lernen noch).")


def test_summary_live_and_texts(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    s = vc.summary(eng, T0 + 60)
    assert s["live"] and not s["dry_run"] and s["note"] == vd.NOTE_LIVE and s["written"] == 1
    assert s["interventions_written"] == {"drosseln": 1, "gemeinsam": 0}
    assert s["active"] == [{"climate": WZ, "name": "Wz", "rule": "drosseln", "setpoint": 20.5, "base": 21.0,
                            "since": s["active"][0]["since"], "back_at": s["active"][0]["back_at"], "written": True}]
    assert s["examples"][0]["written"] and s["rooms"][0]["written"] == 1
    assert s["text"].startswith("In 7 Tagen hat HeatPilot 1× früh gedrosselt und 0× Räume gemeinsam anfordern lassen "
                                "– 1 Schreibvorgänge")
    eng.store.add_valve_dry({"ts": T0 + 30, "room": BAD, "rule": "gemeinsam", "action": "anheben", "setpoint": 21.5,
                             "base": 21.0, "reliable": 0, "managed": 1, "written": 0})
    assert "; im Trockenlauf dazu 0× früh drosseln und 1× gemeinsam anfordern – 1 Schreibvorgänge, nicht geschrieben" in (
        vc.summary(eng, T0 + 60)["text"])
    eng2, _, _ = setup(tmp_path / "b")
    assert vc.summary(eng2, T0)["text"] == ("Noch kein Eingriff – in 7 Tagen hat HeatPilot an den Thermostaten nichts "
                                            "verstellt")
    eng2.cfg.valve_control = False
    s2 = vc.summary(eng2, T0)
    assert s2["dry_run"] and s2["note"] == vd.NOTE and s2["text"].endswith("hätte HeatPilot an den Thermostaten nichts "
                                                                          "verstellt")


def test_actuals_measured_with_real_intervention(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    eng.store.add_room_samples((T0 + i * 300, WZ, round(20.72 + 0.03 * i, 3), 20.5, 40.0, None) for i in range(1, 13))
    eng.store.commit()
    assert vd.fill_actuals(eng.store, T0 + 3600) == 1
    row = written(eng)[0]
    assert row["checked"] == 1 and row["act_over_k"] == 0.08  # mit dem Eingriff gemessen: 21,08 °C


def test_api_heat_preview_and_export(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from heatpilot.web import make_app as web_app
    from test_control34 import make_app

    app, _, loop = make_app(tmp_path)
    eng = app.eng
    eng.cfg.valve_control = True
    eng.found.rooms[0].heating_power = "sensor.wz_heat"
    loop.control.ha = EchoHA(eng)
    now = int(time.time())
    eng.rooms[WZ] = slow_room(a=0.08, b=0.03)
    eng.valves[WZ] = valve_model("pi", today=now)
    loop.guide.st["active"][WZ] = {"rule": "drosseln", "sp": 20.5, "base": 21.0, "since": now - 120, "for": None,
                                   "reason": "erreicht 21,0 °C in ≈ 8 min, Ventil dann noch ≈ 70 % offen",
                                   "back_at": now + 360}

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            loop.plan(now - now % 300, SAMPLE, readings(eng, {WZ: (20.8, 90.0)}))
            heat = await (await client.get("/api/heat")).json()
            c = heat["rooms"][0]["control"]
            assert heat["readiness"]["valves"]["switch"] is True and "valves" in heat["readiness"]
            assert c["valve_guided"] in (True, False) and "valve" in c
            prev = await (await client.get("/api/preview")).json()
            assert prev["valves"]["live"] in (True, False) and "written" in prev["valves"]
            s = await (await client.get("/api/settings")).json()
            assert s["settings"]["valve_control"] is True and s["defaults"]["valve_control"] is False
        finally:
            await client.close()

    asyncio.run(go())
    data = diagnostics.export(app, 1)
    assert data["ventile"]["fuehren"]["switch"] is True
    text = json.dumps(diagnostics.bundle(app), default=str)
    assert "valve_control.state" in text and "53.9" not in text


# ------------------------------------------------------------------ künstliches Haus über einen Tag
def _closed_loop_day(eng, loop, start: int, hours: int = 24) -> list[dict]:
    """Drei Räume mit künstlichen Thermostaten (``sim_valve``), die das Geschriebene übernehmen, und ein Kessel, der
    taktet, wenn nur ein Raum anfordert (4 min an, 8 min aus). Wz (großer Heizkörper, Integrator) heizt nach der Nacht
    auf und käme mit offenem Ventil am Soll an; Bad und Kind (P-Regler) halten ihr Soll. Alle 5 min ein Messpunkt, ein
    Takt der Regelschleife (schreibt über Control) und einer des Trockenlaufs. Gibt den Verlauf je Takt zurück."""
    sims = {WZ: (SimRoom(a=0.08, b=0.03, t0=18.0), THERMOSTATS["pi"]), BAD: (SimRoom(a=0.02, b=0.02, t0=19.0), Thermostat()),
            KIND: (SimRoom(a=0.02, b=0.025, t0=18.5), Thermostat())}
    s = {c: {"t": r.t0, "v": 0.0, "i": 0.0, "hist": []} for c, (r, _) in sims.items()}
    burner_on, since, trace = False, start, []
    for m in range(hours * 60):
        ts = start + m * 60
        demand = 0
        for c, (_, th) in sims.items():
            x, sp = s[c], eng.states[c]["attributes"]["temperature"]
            x["hist"].append(sp - x["t"])
            ed = x["hist"][-1 - round(th.dead_min)] if len(x["hist"]) > th.dead_min else x["hist"][-1]
            edb = 0.0 if abs(ed) <= th.deadband else ed - math.copysign(th.deadband, ed)
            x["i"] = min(max(x["i"] + th.ki * edb, 0.0), 100.0)
            u = min(max(th.kp * edb + x["i"], 0.0), 100.0)
            x["v"] += (u - x["v"]) * (1 - math.exp(-1 / (th.tau_up_min if u > x["v"] else th.tau_down_min)))
            demand += x["v"] > vm.OPEN_PCT
        want = demand >= 2 or (demand == 1 and ((ts - since) < 240 if burner_on else (ts - since) >= 480))
        if want != burner_on:
            burner_on, since = want, ts
            eng.store.add_burner_event(ts, want)
        flow = 60.0 if burner_on else 40.0
        for c, (r, _) in sims.items():
            x = s[c]
            heat = r.a * (flow - x["t"]) * x["v"] / 100 if burner_on else 0.0
            x["t"] += (heat + r.b * (r.t_out - x["t"])) / 60
        if m % 5 == 0:
            vals = {c: (round(s[c]["t"], 2), round(s[c]["v"], 1)) for c in sims}
            rd = readings(eng, vals)
            eng.store.add_room_samples((ts, *x) for x in rd)
            sample = {"flow": flow, "heating": int(burner_on), "pump_on": int(burner_on), "t_out": 5.0}
            loop.plan(ts, sample, rd)
            run(loop.apply())
            vd.step(eng, ts, sample, rd, loop.guided_rooms())
            trace.append({"ts": ts, "vals": vals, "rooms": {c: dict(loop.rooms[c]) for c in sims}})
    return trace


def test_synthetic_house_day_closed_loop(tmp_path):
    kind = Room("Kind", KIND, heating_power="sensor.kind_heat", comfort=20.0, platform="tado", managed=True)
    rooms = [room("Wz", WZ, managed=True), room("Bad", BAD, managed=True), kind]
    start = ts_of(MON, 0)
    eng, ha, loop = setup(tmp_path, rooms=rooms, today=start)
    for c, (a, b) in {BAD: (0.02, 0.02), KIND: (0.02, 0.025)}.items():
        eng.rooms[c] = slow_room(a=a, b=b)
    for c in (WZ, BAD, KIND):
        climate(eng, c, 19.0, 18.0, target_temp_step=0.5)
    loop.state, loop.guide.st = {}, {k: {} for k in vc.KEYS}
    trace = _closed_loop_day(eng, loop, start)
    rows = written(eng)
    assert {r["rule"] for r in rows if r["action"] != "zurück"} == {"drosseln", "gemeinsam"}, rows
    by_ts = {t["ts"]: t for t in trace}
    steps = {c: 0.5 for c in (WZ, BAD, KIND)}
    for r in rows:  # jeder Schritt höchstens einer vom Wunschsoll, im Raster, in erlaubter Phase, nicht zu kalt
        info = by_ts[r["ts"]]["rooms"][r["room"]]
        assert abs(r["setpoint"] - info["desired"]) <= steps[r["room"]] + 1e-9, r
        assert abs(r["setpoint"] * 2 - round(r["setpoint"] * 2)) < 1e-9
        if r["rule"] == "drosseln" and r["action"] == "senken":
            assert info["phase"] in vc.THROTTLE_PHASES and r["temp"] >= r["base"] - vc.BELOW_K - 1e-9, r
    calls = [c for c in ha.calls if c[1] == "set_temperature"]
    assert {c[2] for c in calls} <= {WZ, BAD, KIND} and {c[1] for c in ha.calls} <= {"set_temperature", "set_hvac_mode"}
    logs = eng.store.control_log(500)
    assert sum(1 for x in logs if x["reason"].startswith("Ventil: ")) == len(rows)
    for c in (WZ, BAD, KIND):  # Haltezeit und nur bei Änderung je Raum (das sofortige Zurück ausgenommen)
        mine = [r for r in rows if r["room"] == c]
        for a, b in pairwise(mine):
            assert b["ts"] - a["ts"] >= 300 or b["reason"] == "zurück (Raum braucht Wärme)", (a, b)
            assert b["setpoint"] != a["setpoint"], (a, b)
    # wer Wärme braucht, bekommt sie: nie gedrosselt, während der Raum unter Soll − 0,3 K liegt
    for t in trace:
        for c, info in t["rooms"].items():
            v = info["valve"]
            if v and v["rule"] == "drosseln":
                assert t["vals"][c][0] >= v["base"] - vc.BELOW_K - 0.15, (t["ts"], c, t["vals"][c], v)
    s = vc.summary(eng, start + 86400 - 60)
    assert s["live"] and s["written"] == len(rows) and s["interventions_written"]["drosseln"] >= 1


# ------------------------------------------------------------------ Version, Doku
def test_version_changelog_docs_and_files():
    from pathlib import Path

    from heatpilot import __version__

    root = Path(__file__).resolve().parents[1]
    assert __version__ >= "0.40.0"  # seit 0.41 prüft test_boiler41 die genaue Version
    head = (root / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## 0.40.0\n")[1].split("\n## ")[0]
    assert "Ventile führen" in head and "Standard aus" in head
    for personal in ("Ingo", "55 °C", "BK15", "ES73", "Tado-App"):  # keine persönlichen Werte
        assert personal not in head, personal
    src = (root / "ems_heatpilot" / "heatpilot" / "valve_control.py").read_text(encoding="utf-8")
    assert src.startswith('"""Ventile führen (seit 0.40)') and len(src.splitlines()) <= 600
    for name in ("controlloop.py", "valve_dry.py", "readiness.py", "config.py", "control.py", "valve_model.py"):
        assert len((root / "ems_heatpilot" / "heatpilot" / name).read_text(encoding="utf-8").splitlines()) <= 600, name
    plan = (root / "docs" / "planung-vorlauf-ems.md").read_text(encoding="utf-8")
    assert "Stufe 2 gebaut (0.40)" in plan
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "| 5 | Thermostate als Stellglied" in readme and "0.40 – Ventile führen gebaut" in readme


def test_functions_stay_short():
    import inspect

    from heatpilot import controlloop

    for mod in (vc, controlloop):
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ == mod.__name__:
                assert len(inspect.getsource(fn).splitlines()) <= 60, name
        for cls in (vc.Guide, controlloop.ControlLoop):
            for name, fn in inspect.getmembers(cls, inspect.isfunction):
                assert len(inspect.getsource(fn).splitlines()) <= 65, name
