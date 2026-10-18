"""0.34 „HeatPilot steuert“: Wunschzustand, Schreiben, Boost lokal, Gruppen, Konfiguration, Export.

Sicherheitsregeln (jede hat einen Test): automatisch geschrieben wird nur bei Schalter ``heatpilot``
UND Raum „macht mit“, nur über ``Control._call`` (erlaubte Dienste), nur auf gewählte Räume, 5–25 °C.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from heatpilot import diagnostics, guards, schedule
from heatpilot.config import DEFAULT_NIGHT_PLAN, Config, save_settings, validate
from heatpilot.control import Control, ControlError
from heatpilot.controlloop import (MAX_FAILS, PREHEAT_DEFAULT_H, PREHEAT_MIN_H, RETRY_MIN, ControlLoop, Presence,
                                   desired, preheat_hours, reach_c)
from heatpilot.discovery import Discovered, Room, discover, group_rooms
from heatpilot.engine import Engine
from heatpilot.models import RoomModel
from heatpilot.store import Store

from test_control import FakeHA, run

MON, FRI, SAT, SUN = (2026, 10, 12), (2026, 10, 16), (2026, 10, 17), (2026, 10, 18)


def at(day, h, m=0):
    return datetime(*day, h, m).astimezone()


def ts_of(day, h, m=0):
    return int(at(day, h, m).timestamp())


HOME, AWAY, UNKNOWN = Presence(home_count=1), Presence(home_count=0), Presence(home_count=None)


def slow_room(a=0.02, b=0.01):
    """Plausibles Raummodell (für guards.check_room) mit einstellbarer Heizwirkung."""
    m = RoomModel("Wz")
    m.rls.theta = [a, b, 0.0, 0.0, 0.0, 0.0]
    m.rls.updates, m.excitation = 500, 100.0
    return m


def wz(**kw):
    return Room("Wz", "climate.wz", managed=True, comfort=21.0, platform="homekit_controller", **kw)


# ------------------------------------------------------------------ desired(): reine Funktion
def test_weekday_calendar_assumption():
    assert at(MON, 12).weekday() == 0 and at(FRI, 12).weekday() == 4 and at(SUN, 12).weekday() == 6


def test_desired_all_phases():
    cfg = Config(control_mode="heatpilot")
    d = desired(wz(), at(MON, 12), cfg, HOME)
    assert (d.setpoint, d.phase) == (21.0, "komfort") and d.until == at(MON, 21)
    d = desired(wz(), at(MON, 22), cfg, HOME)  # Nacht Mo 21:00 bis Di 04:30, Vorheizen pauschal 1 h ohne Modell
    assert (d.setpoint, d.phase) == (18.0, "nacht") and d.until == at((2026, 10, 13), 3, 30)
    d = desired(wz(), at((2026, 10, 13), 3, 45), cfg, HOME)
    assert (d.setpoint, d.phase) == (21.0, "vorheizen") and d.until == at((2026, 10, 13), 4, 30)
    d = desired(wz(), at(MON, 12), cfg, AWAY)
    assert (d.setpoint, d.phase) == (19.0, "abwesend") and d.until == at(MON, 21)
    arrival = Presence(home_count=0, arrival=at(MON, 14))
    d = desired(wz(), at(MON, 12), cfg, arrival)
    assert d.phase == "abwesend" and d.until == at(MON, 13)  # eine Stunde vor der Ankunft wird vorgeheizt
    d = desired(wz(), at(MON, 13, 30), cfg, arrival)
    assert (d.setpoint, d.phase) == (21.0, "vorheizen") and "14:00" in d.reason
    d = desired(wz(), at(MON, 12), cfg, UNKNOWN)  # Tracker unbekannt → Komfort
    assert (d.setpoint, d.phase) == (21.0, "komfort")
    d = desired(wz(), at(MON, 12), cfg, Presence(home_count=0, arrival_unknown=at(MON, 12, 30)))
    assert (d.setpoint, d.phase) == (21.0, "komfort") and "lernt noch" in d.reason
    d = desired(wz(), at(MON, 12), cfg, Presence(home_count=0, arrival_unknown=at(MON, 18)))
    assert d.phase == "abwesend"  # offen erst in 6 h: bis dahin Abwesenheit
    d = desired(wz(), at(MON, 12), cfg, Presence(home_count=0, vacation=True))
    assert (d.setpoint, d.phase) == (19.0, "urlaub") and d.until is None
    back = Presence(home_count=0, vacation=True, arrival=at(MON, 18))
    assert desired(wz(), at(MON, 12), cfg, back).until == at(MON, 17)
    d = desired(wz(), at(MON, 17, 30), cfg, back)
    assert (d.setpoint, d.phase) == (21.0, "vorheizen") and "Urlaub" in d.reason
    d = desired(wz(), at(MON, 23), cfg, Presence(home_count=0, vacation=True))  # Urlaub schlägt Nacht
    assert d.phase == "urlaub"


def test_night_plan_over_midnight_and_weekend():
    cfg = Config(control_mode="heatpilot")
    assert desired(wz(), at(FRI, 21, 30), cfg, HOME).phase == "komfort"  # Fr: Beginn 22:00
    d = desired(wz(), at(FRI, 22, 30), cfg, HOME)
    assert d.phase == "nacht" and d.until == at(SAT, 5, 30)  # Sa: Ende 06:30, 1 h Vorheizen
    assert desired(wz(), at(SAT, 5), cfg, HOME).phase == "nacht"
    assert desired(wz(), at(SAT, 6), cfg, HOME).phase == "vorheizen"
    assert desired(wz(), at(SAT, 7), cfg, HOME).phase == "komfort"
    assert desired(wz(), at(SUN, 21, 30), cfg, HOME).phase == "nacht"  # So: Beginn 21:00 …
    assert desired(wz(), at((2026, 10, 19), 4, 45), cfg, HOME).phase == "komfort"  # … Ende Mo 04:30
    # Nacht bei Abwesenheit: Nachttemperatur, am Ende direkt Abwesenheit (kein Vorheizen auf 19 °C)
    d = desired(wz(), at(MON, 23), cfg, AWAY)
    assert (d.setpoint, d.phase) == (18.0, "nacht") and d.until == at((2026, 10, 13), 4, 30)
    assert desired(wz(), at((2026, 10, 13), 4), cfg, AWAY).phase == "nacht"


def test_preheat_from_model_capped_and_unreachable_accepted():
    cfg = Config(control_mode="heatpilot")
    slow = slow_room()  # 18 → 21 °C dauert ~3,1 h bei 75 °C Vorlauf
    assert guards.check_room(slow)["ok"]
    assert preheat_hours(slow, 18.0, 21.0, 75, 5.0, cfg.preheat_max_h) == 3.0  # Deckel
    assert 1.0 < preheat_hours(slow, 20.0, 21.0, 75, 5.0, cfg.preheat_max_h) < 1.2
    d = desired(wz(), at(MON, 23), cfg, HOME, model=slow, temp=18.0, t_out=5.0)
    assert d.phase == "nacht" and d.until == at((2026, 10, 13), 1, 30)
    cfg.preheat_max_h = 2.0
    assert desired(wz(), at(MON, 23), cfg, HOME, model=slow, temp=18.0, t_out=5.0).until == at((2026, 10, 13), 2, 30)
    assert desired(wz(), at((2026, 10, 13), 2, 45), cfg, HOME, model=slow, temp=18.0, t_out=5.0).phase == "vorheizen"
    # unerreichbar (Ventil dauernd offen, Temperatur bleibt darunter): Deckel, nichts hochgedreht, Anzeige „erreicht ≈“
    weak = slow_room(a=0.001)
    assert weak.time_to_reach(18.0, 21.0, 75, 5.0) is None
    assert preheat_hours(weak, 18.0, 21.0, 75, 5.0, 2.0) == 2.0
    d = desired(wz(), at((2026, 10, 13), 3), cfg, HOME, model=weak, temp=18.0, t_out=5.0)
    assert d.phase == "vorheizen" and d.setpoint == 21.0
    assert reach_c(weak, 21.0, 75, 5.0) == 11.5 and reach_c(slow, 21.0, 75, 5.0) is None
    # Untergrenze 15 min: schon warm → trotzdem kurz vorher umschalten; ohne Modell pauschal 1 h
    assert preheat_hours(slow, 21.5, 21.0, 75, 5.0, 3.0) == PREHEAT_MIN_H
    assert preheat_hours(None, 18.0, 21.0, 75, 5.0, 3.0) == PREHEAT_DEFAULT_H


def test_setpoint_limits():
    cfg = Config(control_mode="heatpilot", away_temp=19.0, night_temp=18.0)
    room = wz()
    room.comfort = 24.0
    for p, now in ((HOME, at(MON, 12)), (AWAY, at(MON, 12)), (HOME, at(MON, 23))):
        sp = desired(room, now, cfg, p).setpoint
        assert 18.0 <= sp <= 24.0 and sp * 2 == int(sp * 2)
    room.comfort = 16.0  # Komfort unter der Nachttemperatur: nie über Komfort
    assert desired(room, at(MON, 23), cfg, HOME).setpoint == 16.0
    assert desired(room, at(MON, 12), cfg, AWAY).setpoint == 16.0
    room.comfort = 30.0  # Komfort außerhalb 5–25 (Validierung lässt das nicht zu, der Regler auch nicht)
    assert desired(room, at(MON, 12), cfg, HOME).setpoint == 25.0


# ------------------------------------------------------------------ Regelschleife (Engine + Control + FakeHA)
def make(tmp_path, rooms=None, cfg=None, fail=(), **found_kw):
    cfg = cfg or Config(control_mode="heatpilot")
    found = Discovered(rooms=rooms or [wz()], **found_kw)
    tmp_path.mkdir(exist_ok=True)
    eng = Engine(cfg, Store(tmp_path / "c.db"), found)
    ha = FakeHA(fail)
    ctl = Control(ha, eng.store, found.rooms, states=eng.states)
    loop = ControlLoop(eng, ctl)
    eng.control_loop = loop
    return eng, ha, ctl, loop


def climate(eng, eid, temp, target, mode="heat", **attrs):
    eng.states[eid] = {"state": mode, "attributes": {"current_temperature": temp, "temperature": target,
                                                      "hvac_modes": ["off", "heat"], **attrs}}


def step(eng, loop, ts):
    eng.tick(ts)
    run(loop.apply())
    return eng.control_loop.rooms


def test_writes_only_on_phase_change_and_sets_heat_first(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 19.0, mode="off")
    step(eng, loop, ts_of(MON, 12))
    assert ha.calls == [("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "heat"}),
                        ("climate", "set_temperature", "climate.wz", {"temperature": 21.0})]
    climate(eng, "climate.wz", 20.0, 21.0)  # HA meldet den neuen Zustand
    step(eng, loop, ts_of(MON, 12, 5))
    step(eng, loop, ts_of(MON, 15))
    assert len(ha.calls) == 2  # dieselbe Phase: nicht alle 5 min schreiben
    info = step(eng, loop, ts_of(MON, 22))["climate.wz"]
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": 18.0})
    assert info["phase"] == "nacht" and info["desired"] == 18.0 and info["last_written"] == 18.0
    assert info["next_change"] == at((2026, 10, 13), 3, 30).isoformat() and info["next_phase"] == "vorheizen"
    assert {a[1] for a in ha.calls} <= {"set_temperature", "set_hvac_mode"}  # nur erlaubte Dienste
    assert all(a[2] == "climate.wz" for a in ha.calls)
    rows = eng.store.control_log(10)
    assert len(rows) == 3 and rows[0]["action"] == "set_temperature" and rows[0]["value"] == 18.0
    assert rows[0]["reason"].startswith("nacht:")
    st = loop.as_dict(ts_of(MON, 22))
    assert st["active"] and st["mode"] == "heatpilot" and "HeatPilot steuert" in st["status_text"]
    assert "Nacht bis 04:30" in st["status_text"] and "03:30 Vorheizen" in st["status_text"]
    assert st["log"][0]["name"] == "Wz" and len(st["log"]) == 3


def test_never_writes_when_observing(tmp_path):
    eng, ha, ctl, loop = make(tmp_path, cfg=Config())  # Standard: beobachten
    climate(eng, "climate.wz", 20.0, 19.0, mode="off")
    for h in (12, 22, 23):
        info = step(eng, loop, ts_of(MON, h))["climate.wz"]
    assert ha.calls == [] and info["phase"] is None and info["desired"] is None
    assert eng.store.control_log() == []
    st = loop.as_dict()
    assert st["mode"] == "beobachten" and not st["active"] and st["status_text"] == "Beobachten"


def test_never_writes_rooms_that_do_not_take_part(tmp_path):
    rooms = [wz(), Room("Bad", "climate.bad", managed=False, comfort=23.0, platform="homekit_controller")]
    eng, ha, ctl, loop = make(tmp_path, rooms)
    climate(eng, "climate.wz", 20.0, 19.0)
    climate(eng, "climate.bad", 20.0, 19.0)
    info = step(eng, loop, ts_of(MON, 12))
    assert [a[2] for a in ha.calls] == ["climate.wz"]
    assert info["climate.bad"]["managed"] is False and info["climate.wz"]["managed"] is True
    assert "climate.bad" not in loop.state
    # auch die Regelschleife selbst kommt an fremde Entitäten nicht vorbei
    with pytest.raises(ControlError):
        run(ctl.apply("climate.garage", 21.0, "test"))
    with pytest.raises(ControlError):
        run(ctl.apply("climate.wz", 30.0, "test"))
    with pytest.raises(PermissionError):
        run(ctl._call("number", "set_value", "climate.wz", {}))
    assert [a[2] for a in ha.calls] == ["climate.wz"]


def test_manual_change_respected_until_next_phase(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 19.0)
    step(eng, loop, ts_of(MON, 12))
    climate(eng, "climate.wz", 20.0, 21.0)
    step(eng, loop, ts_of(MON, 12, 5))
    climate(eng, "climate.wz", 20.5, 23.0)  # jemand dreht am Ventil
    info = step(eng, loop, ts_of(MON, 13))["climate.wz"]
    assert len(ha.calls) == 1 and info["manual"] and info["manual_until"] == at(MON, 21).isoformat()
    info = step(eng, loop, ts_of(MON, 16))["climate.wz"]
    assert len(ha.calls) == 1 and info["manual"]
    info = step(eng, loop, ts_of(MON, 22))["climate.wz"]  # Phasenwechsel: HeatPilot schreibt wieder
    assert ha.calls[-1][3] == {"temperature": 18.0} and not info["manual"]
    climate(eng, "climate.wz", 20.0, 18.0)
    climate(eng, "climate.wz", 20.0, 21.0)  # von Hand genau auf den kommenden Wunsch gedreht …
    step(eng, loop, ts_of((2026, 10, 13), 3, 0))
    n = len(ha.calls)
    info = step(eng, loop, ts_of((2026, 10, 13), 3, 45))["climate.wz"]
    assert info["phase"] == "vorheizen" and len(ha.calls) == n  # … also nichts zu schreiben


def test_pause_stops_writing(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 21.0)
    step(eng, loop, ts_of(MON, 12))
    n = len(ha.calls)
    loop.pause(2)
    info = step(eng, loop, ts_of(MON, 22))["climate.wz"]
    assert len(ha.calls) == n and info["paused"] and info["phase"] == "nacht"
    st = loop.as_dict()
    assert st["paused_until"] and st["pause_left_min"] and not st["active"] and "pausiert" in st["status_text"]
    loop.pause(0)
    step(eng, loop, ts_of(MON, 22, 5))
    assert ha.calls[-1][3] == {"temperature": 18.0}
    with pytest.raises(ValueError):
        loop.pause(100)
    # Pause überlebt einen Neustart (kv)
    loop.pause(1)
    assert ControlLoop(eng, ctl).paused()


def test_write_error_retried_after_15_min_then_warning(tmp_path):
    eng, ha, ctl, loop = make(tmp_path, fail={"climate.wz"})
    climate(eng, "climate.wz", 20.0, 19.0)
    t0 = ts_of(MON, 12)
    info = step(eng, loop, t0)["climate.wz"]
    assert ha.calls == [] and loop.state["climate.wz"]["fails"] == 1
    assert info["errors"] == 1 and info["retry_at"] == at(MON, 12, RETRY_MIN).isoformat()
    step(eng, loop, t0 + 300)
    assert loop.pending == [] and loop.state["climate.wz"]["fails"] == 1  # noch nicht wieder
    step(eng, loop, t0 + (RETRY_MIN + 1) * 60)  # 15 min um: erneut
    assert loop.state["climate.wz"]["fails"] == 2 and loop.as_dict()["warnings"] == []
    info = step(eng, loop, t0 + 2 * (RETRY_MIN + 1) * 60)["climate.wz"]
    assert loop.state["climate.wz"]["fails"] == MAX_FAILS and info["errors"] == MAX_FAILS
    assert any("Schreibfehler" in w for w in loop.as_dict()["warnings"])
    ha.fail.clear()
    step(eng, loop, t0 + 3 * (RETRY_MIN + 1) * 60)
    assert ha.calls[-1][3] == {"temperature": 21.0} and loop.state["climate.wz"]["fails"] == 0


def test_controller_is_measured_and_written_alone(tmp_path):
    room = Room("Wz", "climate.wand", controller=True, managed=True, comfort=21.0, platform="homekit_controller",
                members=[{"climate": "climate.v1", "heating_power": None, "humidity": None, "temp_sensor": None,
                          "platform": "homekit_controller"},
                         {"climate": "climate.v2", "heating_power": None, "humidity": None, "temp_sensor": None,
                          "platform": "homekit_controller"}])
    eng, ha, ctl, loop = make(tmp_path, [room])
    climate(eng, "climate.wand", 20.0, 19.0, hvac_action="idle")
    climate(eng, "climate.v1", 24.0, 19.0, hvac_action="heating")  # Ventil misst am warmen Heizkörper
    climate(eng, "climate.v2", 23.0, 19.0, hvac_action="idle")
    _, rooms, _ = eng.snapshot()
    assert rooms == [("climate.wand", 20.0, 19.0, 100.0 / 3, None)]  # Temperatur nur vom Regler, Ventil im Mittel
    step(eng, loop, ts_of(MON, 12))
    assert [a[2] for a in ha.calls] == ["climate.wand"]
    assert room.write_targets == ["climate.wand"] and room.climates == ["climate.wand", "climate.v1", "climate.v2"]


def test_without_controller_all_valves_written_and_temperature_averaged(tmp_path):
    room = Room("Wz", "climate.v1", managed=True, comfort=21.0, platform="homekit_controller",
                members=[{"climate": "climate.v2", "heating_power": "sensor.v2_heat", "humidity": None,
                          "temp_sensor": None, "platform": "homekit_controller"}])
    eng, ha, ctl, loop = make(tmp_path, [room])
    climate(eng, "climate.v1", 20.0, 19.0, hvac_action="heating")
    climate(eng, "climate.v2", 22.0, 19.5, hvac_action="idle")
    eng.states["sensor.v2_heat"] = {"state": "40", "attributes": {}}
    _, rooms, _ = eng.snapshot()
    assert rooms == [("climate.v1", 21.0, 19.0, 70.0, None)]
    step(eng, loop, ts_of(MON, 12))
    assert [a[2] for a in ha.calls] == ["climate.v1", "climate.v2"]
    assert all(a[3] == {"temperature": 21.0} for a in ha.calls)
    assert "climate.v2" in eng._live_entities and "climate.v2" in eng.found.climate_ids()


# ------------------------------------------------------------------ Boost: lokal oder Tado-Dienst
def test_local_boost_timer_and_restore_only_own_setpoint(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 21.0)
    step(eng, loop, ts_of(MON, 12))
    n = len(ha.calls)
    res = run(ctl.boost(["climate.wz"], 24, 30))
    assert res["ok"] and ha.calls[n:] == [("climate", "set_temperature", "climate.wz", {"temperature": 24.0})]
    b = ctl.boosts["climate.wz"]
    assert b["local"] and b["before"] == 21.0 and 29 <= (b["end"] - time.time()) / 60 <= 30
    climate(eng, "climate.wz", 20.0, 24.0)
    assert ctl.active({"climate.wz": 24.0})["climate.wz"]["left_min"] in (29, 30)
    info = step(eng, loop, ts_of(MON, 12, 5))["climate.wz"]
    assert info["boost"] and not info["manual"] and len(ha.calls) == n + 1  # Boost läuft: nicht eingreifen
    ctl.boosts["climate.wz"]["end"] = ts_of(MON, 12, 30)  # Zeit um (Boost rechnet mit der Wanduhr)
    step(eng, loop, ts_of(MON, 12, 35))
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": 21.0})  # Wunschzustand zurück
    assert "climate.wz" not in ctl.boosts and loop.state["climate.wz"]["last_written"] == 21.0
    assert eng.store.control_log(1)[0]["reason"] == "Boost zu Ende"
    # steht inzwischen etwas anderes (jemand hat gedreht): nichts wiederherstellen
    run(ctl.boost(["climate.wz"], 24, 30))
    climate(eng, "climate.wz", 20.0, 22.5)
    ctl.boosts["climate.wz"]["end"] = ts_of(MON, 12, 59)
    m = len(ha.calls)
    step(eng, loop, ts_of(MON, 13))
    assert len(ha.calls) == m and "climate.wz" not in ctl.boosts


def test_local_boost_in_observe_mode_restores_previous_setpoint(tmp_path):
    eng, ha, ctl, loop = make(tmp_path, cfg=Config())
    climate(eng, "climate.wz", 20.0, 19.5)
    run(ctl.boost(["climate.wz"], 23, 60))
    climate(eng, "climate.wz", 20.0, 23.0)
    ctl.boosts["climate.wz"]["end"] = ts_of(MON, 11, 59)
    step(eng, loop, ts_of(MON, 12))
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": 19.5})
    assert [a for a in ha.calls if a[1] == "set_hvac_mode"] == []  # war schon heat
    # Raum stand auf „aus“: Boost schaltet ein, danach wieder aus
    climate(eng, "climate.wz", 20.0, None, mode="off")
    run(ctl.boost(["climate.wz"], 23, 60))
    assert ha.calls[-2:] == [("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "heat"}),
                             ("climate", "set_temperature", "climate.wz", {"temperature": 23.0})]
    climate(eng, "climate.wz", 20.0, 23.0)
    ctl.boosts["climate.wz"]["end"] = ts_of(MON, 12, 59)
    step(eng, loop, ts_of(MON, 13))
    assert ha.calls[-1] == ("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "off"})


def test_tado_cloud_entity_keeps_tado_timer_and_schedule(tmp_path):
    tado = Room("Wz", "climate.wz", managed=True, platform="tado")
    eng, ha, ctl, loop = make(tmp_path, [tado])
    climate(eng, "climate.wz", 20.0, 21.0, mode="auto")
    run(ctl.boost(["climate.wz"], 23, 45))
    assert ha.calls == [("tado", "set_climate_timer", "climate.wz", {"temperature": 23.0, "time_period": "00:45:00"})]
    assert not ctl.boosts["climate.wz"]["local"] and ctl.expired_boosts(time.time() + 3600) == []
    res = run(ctl.schedule(["climate.wz"]))
    assert ha.calls[-1] == ("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "auto"}) and "hint" not in res


def test_schedule_button_on_homekit_entity(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 23.0)
    step(eng, loop, ts_of(MON, 12))
    res = run(ctl.schedule(["climate.wz"], loop.desired_setpoints()))  # heatpilot: Wunschzustand schreiben
    assert res["ok"] and ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": 21.0})
    eng2, ha2, ctl2, loop2 = make(tmp_path / "b", cfg=Config())
    climate(eng2, "climate.wz", 20.0, 23.0)
    res = run(ctl2.schedule(["climate.wz"], loop2.desired_setpoints()))  # beobachten: nur der Hinweis
    assert ha2.calls == [] and "Tado-App" in res["hint"] and res["ok"]


# ------------------------------------------------------------------ Konfiguration und Gruppen
def test_config_validation_of_new_fields():
    ok, errors = validate({
        "control_mode": "heatpilot", "preheat_max_h": "2,5",
        "night_plan": [{"start": "21:00", "end": "4:30"}] * 5 + [{"start": "22:00", "end": "06:30"}] * 2,
        "rooms": [{"climate": "climate.a", "group": "Wohnzimmer", "controller": True, "managed": "on", "comfort": "20,7"},
                  {"climate": "climate.b", "group": "Wohnzimmer"}],
    })
    assert errors == [] and ok["control_mode"] == "heatpilot" and ok["preheat_max_h"] == 2.5
    assert ok["night_plan"][0] == {"start": "21:00", "end": "04:30"} and len(ok["night_plan"]) == 7
    assert ok["rooms"][0] == {"name": "", "climate": "climate.a", "group": "Wohnzimmer", "controller": True,
                              "managed": True, "comfort": 20.5}
    assert ok["rooms"][1] == {"name": "", "climate": "climate.b", "group": "Wohnzimmer"}
    _, errors = validate({"control_mode": "auto"})
    assert errors and "beobachten, heatpilot" in errors[0]
    _, errors = validate({"night_plan": [{"start": "21:00", "end": "04:30"}] * 6})
    assert errors and "7 Einträge" in errors[0]
    _, errors = validate({"night_plan": [{"start": "09:00", "end": "04:30"}] * 7})
    assert errors and "Mo" in errors[0] and "Abend" in errors[0]
    _, errors = validate({"night_plan": [{"start": "21:00", "end": "13:00"}] * 7})
    assert errors and "Morgen" in errors[0]
    _, errors = validate({"preheat_max_h": 7})
    assert errors
    _, errors = validate({"rooms": [{"climate": "climate.a", "comfort": 30}]})
    assert errors and "Komfort" in errors[0]
    _, errors = validate({"rooms": [{"climate": "climate.a", "comfort": "warm"}]})
    assert errors


def test_old_settings_without_new_fields(tmp_path):
    (tmp_path / "options.json").write_text("{}")
    save_settings({"rooms": [{"name": "Bad", "climate": "climate.bad"}, {"name": "Wz", "climate": "climate.wz"}],
                   "night_temp": 17.5}, tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.control_mode == "beobachten" and not cfg.is_managing()
    assert cfg.night_plan == DEFAULT_NIGHT_PLAN and cfg.preheat_max_h == 3.0
    raw = cfg.to_raw()
    assert raw["control_mode"] == "beobachten" and raw["night_plan"] == DEFAULT_NIGHT_PLAN
    assert validate(raw)[1] == []
    states = [{"entity_id": c, "state": "heat", "attributes": {"current_temperature": 20}} for c in ("climate.bad", "climate.wz")]
    d = discover(states, [{"entity_id": "climate.bad", "platform": "tado"}], cfg)
    assert [(r.name, r.climate, r.managed, r.comfort, r.members, r.platform) for r in d.rooms] == [
        ("Bad", "climate.bad", False, 21.0, [], "tado"), ("Wz", "climate.wz", False, 21.0, [], None)]
    # kaputter Plan in alten Dateien: Standard statt Absturz
    cfg2 = Config.from_raw({"night_plan": "x", "control_mode": "egal"})
    assert cfg2.night_plan == DEFAULT_NIGHT_PLAN and cfg2.control_mode == "beobachten"


def test_is_night_at_by_weekday():
    cfg = Config()
    assert cfg.is_night_at(at(MON, 21)) and not cfg.is_night_at(at(MON, 20, 59))
    assert cfg.is_night_at(at((2026, 10, 13), 4, 29)) and not cfg.is_night_at(at((2026, 10, 13), 4, 30))
    assert not cfg.is_night_at(at(FRI, 21, 30)) and cfg.is_night_at(at(FRI, 22))
    assert cfg.is_night_at(at(SAT, 6)) and not cfg.is_night_at(at(SAT, 6, 30))
    assert cfg.night_end_at(at(FRI, 23)) == at(SAT, 6, 30) and cfg.night_end_at(at(SAT, 2)) == at(SAT, 6, 30)
    assert cfg.night_start_after(at(SAT, 12)) == at(SAT, 22) and cfg.night_start_after(at(SUN, 12)) == at(SUN, 21)
    cfg.night_plan[4] = {"start": "23:30", "end": "05:00"}
    assert not cfg.is_night_at(at(FRI, 23)) and cfg.is_night_at(at(FRI, 23, 45))
    assert cfg.is_night(datetime(2026, 1, 1, 23).time())  # alte feste Zeiten bleiben für Altaufrufer


def test_groups_from_settings():
    reg = {"climate.wand": {"platform": "homekit_controller"}, "climate.v1": {"platform": "homekit_controller"},
           "climate.alt": {"platform": "tado"}}
    rooms = group_rooms([
        {"climate": "climate.v1", "name": "Ventil 1", "group": "Wohnzimmer", "managed": True, "heating_power": "sensor.v1_h"},
        {"climate": "climate.wand", "group": "Wohnzimmer", "controller": True, "comfort": 20.0},
        {"climate": "climate.alt", "name": "Altbau"},
    ], {"climate.alt": {"attributes": {"friendly_name": "Altbau Thermostat"}}}, reg)
    w, a = rooms
    assert (w.name, w.climate, w.controller, w.managed, w.comfort, w.platform) == (
        "Wohnzimmer", "climate.wand", True, True, 20.0, "homekit_controller")
    assert [m["climate"] for m in w.members] == ["climate.v1"] and w.members[0]["heating_power"] == "sensor.v1_h"
    assert (a.name, a.climate, a.group, a.managed, a.platform, a.members) == ("Altbau", "climate.alt", None, False, "tado", [])
    found = Discovered(rooms=rooms)
    assert found.climate_ids() == ["climate.wand", "climate.v1", "climate.alt"]
    assert found.room_of("climate.v1") is w and "sensor.v1_h" in found.all_sensor_ids()
    assert json.dumps(found.as_dict(private=False))  # JSON-fähig (Diagnose)


def test_effective_schedule_shows_weekly_plan_when_managing():
    learned = {"nights": 20, "night_start": "22:30", "night_end": "06:00", "night_temp": 19.5, "away_temp": 19.5}
    obs = schedule.effective(Config(), learned)
    assert obs.source["night_start"] == "gelernt" and obs.plan is None
    eff = schedule.effective(Config(control_mode="heatpilot"), learned)
    assert eff.source == {k: "wochenplan" for k in ("night_start", "night_end", "night_temp", "away_temp")}
    assert eff.night_start.strftime("%H:%M") == "21:00" and eff.night_end.strftime("%H:%M") == "04:30"
    assert eff.night_temp == 18.0 and eff.away_temp == 19.0
    d = eff.as_dict()
    assert [p["day"] for p in d["plan"]] == ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"] and d["plan"][5]["end"] == "06:30"


# ------------------------------------------------------------------ Web, Export
def make_app(tmp_path):
    eng, ha, ctl, loop = make(tmp_path, persons=["person.a"], home_zone=(53.55, 10.0))
    eng.states["person.a"] = {"state": "home", "attributes": {"latitude": 53.9, "longitude": 10.4, "friendly_name": "A"}}
    climate(eng, "climate.wz", 20.0, 19.0)
    connected = asyncio.Event()
    connected.set()
    app = SimpleNamespace(eng=eng, cfg=eng.cfg, control=ctl, status="läuft", configured=True,
                          ha=SimpleNamespace(connected=connected, info=lambda: {"connected": True, "token_present": True,
                                                                                "last_error": ""}))

    async def refresh_rooms():
        return None

    async def registry():
        return []

    async def suggestions():
        return {}

    app.refresh_rooms, app.registry, app.suggestions = refresh_rooms, registry, suggestions
    return app, ha, loop


def test_export_and_bundle_contain_control_log_without_coordinates(tmp_path):
    app, ha, loop = make_app(tmp_path)
    step(app.eng, loop, int(time.time()) - int(time.time()) % 300)
    assert ha.calls
    data = diagnostics.export(app, 1)
    text = json.dumps(data, default=str)
    st = data["steuerung"]
    assert st["schalter"] == "heatpilot" and "set_temperature" in str(st["protokoll"]["zeilen"])
    assert st["zustand"]["rooms"]["climate.wz"]["phase"] in ("komfort", "nacht", "vorheizen")
    assert "53.9" not in text and "10.4" not in text and "latitude" not in text and "53.55" not in text
    bundle = json.dumps(diagnostics.bundle(app), default=str)
    assert "control_log" in bundle and "53.9" not in bundle and "latitude" not in bundle


def test_api_heat_reports_control_and_pause(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot.web import make_app as web_app

    app, ha, loop = make_app(tmp_path)
    step(app.eng, loop, ts_of(MON, 12))

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            j = await (await client.get("/api/heat")).json()
            room = j["rooms"][0]
            assert room["managed"] and room["comfort"] == 21.0 and room["climates"] == ["climate.wz"]
            assert room["devices"][0]["climate"] == "climate.wz" and room["tado_cloud"] is False
            c = room["control"]
            assert c["phase"] == "komfort" and c["desired"] == 21.0 and c["manual_until"] is None
            assert c["next_change"] == at(MON, 21).isoformat() and c["next_phase"] == "nacht" and c["reach_c"] is None
            assert j["control"]["mode"] == "heatpilot" and j["control"]["active"] and j["control"]["paused_until"] is None
            assert j["control"]["log"][0]["room"] == "climate.wz" and j["limits"]["pause_max_h"] == 48
            r = await client.post("/api/heat/pause", json={"hours": 2})
            j = await r.json()
            assert j["ok"] and j["paused_until"] and j["control"]["paused_until"] and not j["control"]["active"]
            j = await (await client.post("/api/heat/pause", json={"hours": 0})).json()
            assert j["ok"] and j["paused_until"] is None and j["control"]["active"]
            r = await client.post("/api/heat/pause", json={"hours": 99})
            assert r.status == 400
            s = await (await client.get("/api/settings")).json()
            assert s["control"]["modes"] == ["beobachten", "heatpilot"] and s["climate_targets"]["climate.wz"] == 19.0
            assert s["settings"]["night_plan"] == DEFAULT_NIGHT_PLAN and s["control"]["weekdays"][0] == "Mo"
        finally:
            await client.close()

    asyncio.run(go())
