"""0.74: Funde aus zwei Tagen 30-s-Werten (30.10.–01.11., Schichtbetrieb, Homeoffice, neues Flur-Ventil).

A Vorheizen lernt nicht aus Eingriffen; das Vorheiz-Soll liegt nie unter der Ist-Temperatur, wenn es noch zu kalt ist.
B „Anwesend“/„Tag“ enden nachts erst am Nachtende.
C Ventil-PID über dem Soll zu; Ventilräume fordern nur unter HeatPilots Soll Wärme an.
D Shelly: Schritte ab 5 %, Prüfung ±5 %, ganz zu immer.
E Raum ohne Raummodell mit weit offenem Ventil: mindestens 45 °C Vorlauf.
G Kein Warmwasser-Wechsel im ersten Takt nach dem Start.
(F – Betriebsart in den 30-s-Werten – steht in ``test_live_log492``.)
"""

from types import SimpleNamespace

from heatpilot import boiler_flow, heatup, override, valve_pid
from heatpilot.config import Config
from heatpilot.controlloop import Desired
from heatpilot.discovery import Room

from test_control34 import MON, at, slow_room, ts_of
from test_override62 import Store
from test_valve_pid51 import ENT, T0, vhouse, vstep, writes


# ------------------------------------------------------------------ A: Vorheizen
def test_preheat_setpoint_is_never_below_a_room_that_still_needs_heat():
    assert heatup.preheat_sp(20.0, 1.5, 18.6) == 20.0  # Küche 01.11.: sonst 18,5 – Tado heizte gar nicht
    assert heatup.preheat_sp(20.0, 1.5, 17.0) == 18.5  # kalt: abgesenkt, die Restwärme bringt den Rest
    assert heatup.preheat_sp(20.0, 1.5, 19.8) == 18.5  # fast am Ziel: abgesenkt lassen
    assert heatup.preheat_sp(20.0, 1.5, None) == 18.5


def _preheat(hands_mid: bool) -> dict:
    st, t0 = {}, ts_of(MON, 5)
    until = at(MON, 6)
    heatup.track(st, "vorheizen", 19.0, until, 18.0, t0, False)
    heatup.track(st, "vorheizen", 19.0, until, 22.4, t0 + 1800, False, hands=hands_mid)  # Boost auf 22 °C
    heatup.track(st, "komfort", 20.0, None, 22.4, t0 + 3600 + heatup.COAST_S + 60, False)
    return st


def test_a_boost_during_preheat_teaches_nothing():
    assert "tune" not in _preheat(hands_mid=True) and "pre" not in _preheat(hands_mid=True)
    assert _preheat(hands_mid=False)["tune"]["margin"] > heatup.MARGIN_DEFAULT  # ohne Eingriff: Überschwingen gelernt


def test_no_tracking_starts_while_hands_are_on():
    st = {}
    heatup.track(st, "vorheizen", 19.0, at(MON, 6), 18.0, ts_of(MON, 5), False, hands=True)
    assert "pre" not in st


# ------------------------------------------------------------------ B: „Anwesend“ nachts bis zum Nachtende
def test_present_at_night_lasts_until_night_end_not_the_first_preheat():
    loop = SimpleNamespace(store=Store(), eng=SimpleNamespace(cfg=Config()))  # Nacht Mo bis 04:30
    rooms = [SimpleNamespace(climate="climate.a", comfort=21), SimpleNamespace(climate="climate.b", comfort=20)]

    def plan(a_phase, b_phase):
        return {"climate.a": Desired(18.0, a_phase, "x", at(MON, 3, 45)), "climate.b": Desired(18.0, b_phase, "x",
                                                                                                 at(MON, 4, 15))}
    t = ts_of(MON, 3)
    override.set_mode(loop.store, "anwesend", t)
    home = SimpleNamespace(home_count=2)
    out = override.apply(loop, t, plan("nacht", "nacht"), home, rooms)
    assert override.get(loop.store)["until"] == ts_of(MON, 4, 30)  # bisher 03:45 – der erste Vorheiz-Beginn
    assert out["climate.b"].setpoint == 20.0 and out["climate.b"].phase == "komfort"
    out = override.apply(loop, ts_of(MON, 4, 20), plan("vorheizen", "vorheizen"), home, rooms)
    assert out["climate.a"].reason == "nur jetzt: Anwesend"  # Vorheizen ist noch nicht Komfort
    assert override.apply(loop, ts_of(MON, 4, 31), plan("komfort", "komfort"), home, rooms)["climate.a"].reason == "x"
    assert override.get(loop.store) is None


# ------------------------------------------------------------------ C: Ventil über dem Soll
def test_valve_closes_above_setpoint_despite_feedforward():
    st = {}
    out = valve_pid.step(st, 20.0, 20.5, 55.0, 5.0, slow_room(), T0)
    assert out == 0.0 and st["i"] <= 0.0
    assert valve_pid.step(st, 20.0, 19.0, 55.0, 5.0, slow_room(), T0 + 60) > 0  # darunter wieder auf


def _valve_eng(temp, valve=7.0, model=None, managed=True):
    flur = Room("Flur", "climate.flur", managed=managed, comfort=20.0, lead="ventil", valve_pos=ENT, platform="shelly")
    return SimpleNamespace(found=SimpleNamespace(rooms=[flur]), rooms={"climate.flur": model} if model else {},
                           control_loop=SimpleNamespace(rooms={"climate.flur": {"desired": 20.0}}),
                           room_reading=lambda r: ("climate.flur", temp, 5.0, valve, None))  # Shelly-Soll 5 °C


def test_valve_room_asks_for_heat_only_below_heatpilots_setpoint():
    warm = _valve_eng(21.5)
    readings = boiler_flow._readings(warm)
    assert readings["climate.flur"][2] == 20.0  # HeatPilots Soll, nicht die 5 °C am Shelly
    assert boiler_flow._open(warm, readings) == []  # 01.11. 07:13: Kessel lief sonst nur für den warmen Flur
    cold = _valve_eng(19.0)
    assert boiler_flow._open(cold, boiler_flow._readings(cold)) == ["Flur"]
    own = _valve_eng(21.5, managed=False)  # ohne „macht mit“ regelt der Shelly selbst – sein Soll gilt
    assert boiler_flow._readings(own)["climate.flur"][2] == 5.0 and boiler_flow._open(own, boiler_flow._readings(own))


# ------------------------------------------------------------------ D: Shelly-Schritte
def test_small_steps_are_not_written_but_closing_always_is():
    assert not valve_pid.decide(10, 6.0, T0, None).write  # 4 % – führt der Shelly nicht aus
    assert valve_pid.decide(0, 4.0, T0, None).write        # ganz zu immer …
    assert not valve_pid.decide(0, 2.0, T0, None, last_value=0).write  # … einmal: meldet es danach 2 %, passt das
    assert valve_pid.decide(12, 6.0, T0, None).write


def test_check_accepts_within_five_percent(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path, temp=18.0)
    vstep(eng, loop, vp, T0)
    want = writes(ha)[-1][1]
    eng.states[ENT]["state"] = str(want - 3)  # Shelly meldet 3 % daneben
    vstep(eng, loop, vp, T0 + 70)
    st = vp.state["rooms"]["climate.wz"]
    assert not st.get("check") and st.get("fails", 0) == 0


# ------------------------------------------------------------------ E: neuer Raum ohne Raummodell
def test_room_without_model_and_open_valve_gets_a_minimum_flow():
    eng = _valve_eng(19.0, valve=90.0)
    readings = boiler_flow._readings(eng)
    value, reason = boiler_flow._new_rooms(eng, readings, 35, 75, "Bedarf")
    assert value == boiler_flow.NEW_ROOM_FLOW and "Flur ohne Raummodell" in reason
    assert boiler_flow._new_rooms(eng, readings, 35, 40, "x")[0] == 40  # nie über die Obergrenze
    modelled = _valve_eng(19.0, valve=90.0, model=slow_room())
    assert boiler_flow._new_rooms(modelled, boiler_flow._readings(modelled), 35, 75, "x") == (35, "x")


# ------------------------------------------------------------------ G: Warmwasser im ersten Takt
def test_dhw_writes_nothing_before_the_control_loop_knows_the_heat_start(tmp_path):
    from test_dhw_control38 import make

    eng, ha, dc = make(tmp_path)
    eng.control_loop = SimpleNamespace(planned_ts=None)
    eng.dhw_rec = {"target": 55, "reason": "Komfort"}
    t = ts_of(MON, 15)
    dc.plan(t, {})
    assert dc.pending is None and dc.view == {}
    eng.control_loop.planned_ts = t
    dc.plan(t + 30, {})
    assert dc.pending == (55, "Komfort")

