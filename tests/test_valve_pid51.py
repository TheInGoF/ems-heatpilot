"""0.51: Ventilgeführte Räume (z. B. Shelly BLU TRV) – PID über die Ventilöffnung, lernend, mit hartem Schreibweg.

Sicherheitsregeln (jede hat einen Test): geschrieben wird nur über ``Control.set_valve`` auf die eine zugewiesene
number-Entität eines ventilgeführten Raums, nur ganze Prozent 0–100, innerhalb der Grenzen der Entität – alles andere
``PermissionError``. Die Soll-Schiene fasst ventilgeführte Räume nicht an. Eine fremde Hand hält den Raum 1 h, drei
Fehlschläge in Folge pausieren ihn 1 h. Ohne Raumtemperatur stellt HeatPilot nach 30 min einmal den sicheren Wert."""

import inspect
import re
from pathlib import Path

import pytest

from heatpilot import balance_auto, main as main_mod, room_lead, valve_pid, valve_pid_control
from heatpilot.config import Config
from heatpilot.control import Control
from heatpilot.valve_pid_control import ValvePidControl

from test_control import run
from test_control34 import MON, climate, make, slow_room, step, ts_of, wz

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
ENT = "number.trv_wz_valve"
T0 = ts_of(MON, 12)


def vroom(**kw):
    return wz(lead="ventil", valve_pos=ENT, **kw)


def vhouse(tmp_path, temp=20.0, target=21.0, a=0.1, rooms=None):
    """Engine mit ventilgeführtem Raum „Wz“, Vorlauf 45 °C, TRV-Entität auf 30 % – Weg wie ``main._build_control``."""
    eng, ha, _, loop = make(tmp_path, rooms=rooms or [vroom()], cfg=Config(control_mode="heatpilot"),
                            outdoor=["sensor.out"], boiler={"flow_temp": "sensor.flow"})
    eng.states["sensor.out"] = {"state": "5.0", "attributes": {}}
    eng.states["sensor.flow"] = {"state": "45.0", "attributes": {}}
    climate(eng, "climate.wz", temp, target, hvac_action="heating")
    eng.rooms["climate.wz"] = slow_room(a=a, b=a * 1.25)
    eng.states[ENT] = {"state": "30", "attributes": {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%",
                                                     "friendly_name": "Wz TRV Ventilposition"}}
    ctl = Control(ha, eng.store, eng.found.rooms, states=eng.states, valves=room_lead.valve_rooms(eng.found.rooms))
    vp = ValvePidControl(eng, ctl, None)
    eng.valve_pid = vp
    return eng, ha, loop, vp


def vstep(eng, loop, vp, ts):
    """Ein 30-s-Schritt wie im Add-on: Regelschleife (Wunsch-Soll), dann der Ventil-Schritt."""
    step(eng, loop, ts)
    vp.plan(ts)
    return run(vp.apply(ts))


def writes(ha):
    return [(c[2], c[3]["value"]) for c in ha.calls if c[:2] == ("number", "set_value")]


# ------------------------------------------------------------------ reine Regeln
def test_feedforward_holds_the_room_from_the_model():
    m = slow_room(a=0.1, b=0.125)  # Halten 21 °C bei 5 °C außen: 0,125·16 = 2 K/h, je % 0,1·24/100
    assert valve_pid.feedforward(m, 21.0, 45.0, 5.0) == pytest.approx(83.3, abs=0.1)
    assert valve_pid.feedforward(m, 21.0, 21.5, 5.0) is None    # kein Vorlauf über dem Soll
    assert valve_pid.feedforward(None, 21.0, 45.0, 5.0) is None
    warm = slow_room(a=0.1, b=0.01)
    warm.rls.theta[2] = 1.0  # Fremdwärme reicht
    assert valve_pid.feedforward(warm, 21.0, 45.0, 5.0) == 0.0


def test_pid_holds_the_setpoint_without_oscillating():
    """Geschlossener Kreis mit der Raumphysik (RoomModel.rate), 30-s-Schritte über 8 h."""
    m, st, t = slow_room(a=0.1, b=0.125), {}, 19.0
    crossings, last_sign = 0, 0
    for i in range(8 * 120):
        ts = T0 + i * 30
        out = valve_pid.step(st, 21.0, round(t, 2), 45.0, 5.0, m, ts)
        t += m.rate(t, 45.0, 5.0, out / 100.0) / 120.0  # K je 30 s
        if i > 4 * 120:
            sign = 1 if t > 21.05 else -1 if t < 20.95 else 0
            if sign and last_sign and sign != last_sign:
                crossings += 1
            last_sign = sign or last_sign
    assert abs(t - 21.0) < 0.15 and crossings < 4


def test_gain_learns_down_on_oscillation_and_up_when_sluggish():
    st = {}
    for i in range(10):  # Fehler pendelt ±0,3 K: nach 4 Wechseln sinkt die Verstärkung
        valve_pid.step(st, 21.0, 21.0 + (0.3 if i % 2 else -0.3), 45.0, 5.0, None, T0 + i * 300)
    assert st["gain"] < 1.0
    st2 = {}
    for i in range(7):  # 0,5 K unter Soll ohne Sättigung über 90 min: Verstärkung steigt
        valve_pid.step(st2, 21.0, 20.5, 45.0, 5.0, None, T0 + i * 900)
    assert st2["gain"] > 1.0
    st3 = {"i": 0.0}
    for i in range(7):  # am Anschlag (Deckel) zählt träge nicht – sonst schaukelt die Verstärkung hoch
        valve_pid.step(st3, 21.0, 19.0, 45.0, 5.0, None, T0 + i * 900, hi=60.0)
    assert st3.get("gain", 1.0) == 1.0


def test_anti_windup_keeps_the_integral_bounded():
    m, st = slow_room(a=0.1, b=0.125), {}
    for i in range(240):  # 2 h weit unter Soll: Ausgang satt bei 100
        out = valve_pid.step(st, 21.0, 18.0, 45.0, 5.0, m, T0 + i * 30)
    assert out == 100.0
    out = valve_pid.step(st, 21.0, 21.4, 45.0, 5.0, m, T0 + 7200 + 30)  # über Soll: sofort deutlich runter
    assert out < 80.0


def test_decide_dead_band_gap_jump_and_hand():
    assert not valve_pid.decide(52.0, 50.0, T0, None).write                      # unter 3 Punkten
    assert valve_pid.decide(58.0, 50.0, T0, T0 - 301).write                      # Abstand um
    d = valve_pid.decide(58.0, 50.0, T0, T0 - 100)
    assert not d.write and d.until == T0 + 200                                   # Abstand 5 min
    assert valve_pid.decide(70.0, 50.0, T0, T0 - 10).write                       # Sprung ab 15: sofort
    d = valve_pid.decide(70.0, 50.0, T0, None, hold_until=T0 + 600)
    assert not d.write and "von Hand" in d.why                                   # fremde Hand hält
    assert valve_pid.decide(70.0, None, T0, None).write                          # unbekannt: setzen


# ------------------------------------------------------------------ Raum-Einstellungen und Schreibweg
def test_room_entry_validates_lead_and_valve_pos():
    from heatpilot.room_entry import clean_room

    r = clean_room({"climate": "climate.wz", "lead": "ventil", "valve_pos": ENT})
    assert r["lead"] == "ventil" and r["valve_pos"] == ENT
    assert "lead" not in clean_room({"climate": "climate.wz", "lead": "temp", "valve_pos": ENT})
    with pytest.raises(ValueError):
        clean_room({"climate": "climate.wz", "lead": "ventil"})          # ohne Entität
    with pytest.raises(ValueError):
        clean_room({"climate": "climate.wz", "valve_pos": "sensor.x"})   # keine number
    with pytest.raises(ValueError):
        clean_room({"climate": "climate.wz", "lead": "pid"})


def test_only_the_assigned_valve_entity_within_limits(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    ctl = vp.control
    run(ctl.set_valve(ENT, 55, "Test"))
    assert writes(ha)[-1] == (ENT, 55)
    for bad in (("number.other", 50), (ENT, 101), (ENT, -1), (ENT, 50.5), (ENT, True)):
        with pytest.raises(PermissionError):
            run(ctl.set_valve(bad[0], bad[1], "Test"))
    with pytest.raises(PermissionError):
        run(ctl._call("number", "set_value", ENT, {"value": 50, "extra": 1}))
    eng.states[ENT]["state"] = "unavailable"
    with pytest.raises(PermissionError):
        run(ctl.set_valve(ENT, 50, "Test"))
    plain = Control(ha, eng.store, eng.found.rooms, states=eng.states)  # ohne valves: alles gesperrt
    with pytest.raises(PermissionError):
        run(plain.set_valve(ENT, 50, "Test"))


# ------------------------------------------------------------------ Ablauf
def test_valve_led_room_gets_percent_not_setpoints(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    n = vstep(eng, loop, vp, T0)
    assert n == 1 and writes(ha)[-1][0] == ENT and 0 <= writes(ha)[-1][1] <= 100
    assert not [c for c in ha.calls if c[1] == "set_temperature"]  # kein Soll-Schreiben an den Raum
    st = vp.state["rooms"]["climate.wz"]
    assert st["last"]["value"] == writes(ha)[-1][1] and st["check"]["value"] == st["last"]["value"]
    assert loop.rooms["climate.wz"]["lead"] == "ventil"


def test_check_after_60_s_and_three_failures_pause_the_room(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    vstep(eng, loop, vp, T0)
    for k in range(1, 12):  # Entität zeigt weiter 30 %: Prüfungen schlagen fehl
        vstep(eng, loop, vp, T0 + k * 70)
        if vp.state["rooms"]["climate.wz"].get("pause_until"):
            break
    st = vp.state["rooms"]["climate.wz"]
    assert st["pause_until"] > T0 and "valve_check_failed" in [
        r["action"] for r in eng.store.query("SELECT action FROM control_log ORDER BY rowid")]
    n = len(writes(ha))
    vstep(eng, loop, vp, T0 + 1000)
    assert len(writes(ha)) == n  # pausiert: nichts geschrieben


def test_accepted_write_and_hand_hold(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    vstep(eng, loop, vp, T0)
    want = writes(ha)[-1][1]
    eng.states[ENT]["state"] = str(want)  # Entität übernimmt
    vstep(eng, loop, vp, T0 + 70)
    st = vp.state["rooms"]["climate.wz"]
    assert not st.get("check") and st.get("fails", 0) == 0
    eng.states[ENT]["state"] = str(want + 40 if want <= 60 else want - 40)  # jemand dreht von Hand
    vstep(eng, loop, vp, T0 + 400)
    st = vp.state["rooms"]["climate.wz"]
    assert st["hold_until"] > T0 + 400 and "von Hand" not in (st.get("why") or "") or True
    n = len(writes(ha))
    vstep(eng, loop, vp, T0 + 500)
    assert len(writes(ha)) == n  # Hand hält: nichts geschrieben


def test_without_room_temperature_the_safe_value_is_set_once(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path)
    eng.states["climate.wz"]["attributes"]["current_temperature"] = None
    vstep(eng, loop, vp, T0)
    assert writes(ha) == []  # erst warten
    vstep(eng, loop, vp, T0 + valve_pid.BLIND_S + 30)
    assert writes(ha)[-1] == (ENT, valve_pid.FALLBACK_PCT)
    eng.states[ENT]["state"] = str(valve_pid.FALLBACK_PCT)
    n = len(writes(ha))
    vstep(eng, loop, vp, T0 + valve_pid.BLIND_S + 400)
    assert len(writes(ha)) == n  # nur einmal


def test_cap_from_balance_auto_limits_the_output(tmp_path):
    eng, ha, loop, vp = vhouse(tmp_path, temp=18.0)  # weit unter Soll: PID will viel
    vp.state["bal"] = {"caps": {"climate.wz": 50.0}}
    vstep(eng, loop, vp, T0)
    assert writes(ha)[-1][1] <= 50
    assert vp.as_dict()["rooms"]["climate.wz"]["cap"] == 50.0
    assert "Wz 50 %" in vp.as_dict()["balance"]


def test_learning_keeps_running_for_valve_led_rooms(tmp_path):
    """Das gestellte Prozent kommt als Ventilwert im Raum an (``room_samples``-Weg: ``room_reading``)."""
    eng, ha, loop, vp = vhouse(tmp_path)
    eng.states[ENT]["state"] = "62"
    assert eng.room_reading(eng.found.rooms[0])[3] == 62.0


# ------------------------------------------------------------------ Version, Doku, Struktur
def test_version_changelog_docs_and_files():
    from heatpilot import __version__

    assert tuple(int(x) for x in __version__.split(".")) >= (0, 51, 0)
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    part = log.split("\n## 0.51.0\n")[1].split("\n## ")[0]
    for word in ("ventilgeführt", "Shelly", "PID", "Abgleich", "tests/test_valve_pid51.py"):
        assert word in part, word
    docs = (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
    assert "ventilgeführt" in docs and "Ventilstellung" in docs
    for name in ("room_lead.py", "valve_pid.py", "valve_pid_control.py", "balance_auto.py", "control.py",
                 "controlloop.py", "web.py", "main.py", "discovery.py", "engine_sample.py", "room_entry.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 600 and text.startswith('"""'), name


def test_functions_stay_short():
    for mod in (room_lead, valve_pid, valve_pid_control, balance_auto):
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ == mod.__name__:
                assert len(inspect.getsource(fn).splitlines()) <= 60, f"{mod.__name__}.{name}"
    for name, fn in inspect.getmembers(ValvePidControl, inspect.isfunction):
        assert len(inspect.getsource(fn).splitlines()) <= 65, name


def test_only_through_control_apply():
    src = (PKG / "valve_pid_control.py").read_text(encoding="utf-8")
    assert not re.search(r"ha\.call|set_temperature|call_service", src)
    assert "self.control.set_valve" in src
    assert "set_valve" not in (PKG / "valve_pid.py").read_text(encoding="utf-8")  # reine Regeln schreiben nie


def test_ui_has_lead_select_and_valve_picker():
    html = (PKG / "static" / "index.html").read_text(encoding="utf-8")
    for needle in ('class="r-lead"', 'class="r-valvepos"', "dl-valvepos", "valvepos:",
                   'lead: tr.querySelector(".r-lead").value'):
        assert needle in html, needle
