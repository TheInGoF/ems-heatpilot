"""0.41 Weg B: Kessel über HeatPilot – Vorlauf-Soll live, Heizbetrieb, Takt-Boost, Abnahme, Fußboden-Grenze, Wächter.

Sicherheitsregeln (jede hat einen Test): Schalter Standard aus; geschrieben wird nur mit „heatpilot“, nur
``number.set_value`` auf die eine Vorlauf-Soll-Entität (ganzzahlig, 35 °C…``max_flow_temp`` und Fußboden-Grenze) und
``switch.turn_on``/``turn_off`` auf die eine Heizbetrieb-Entität; solange ein Regler am Bus ist (oder es unbekannt ist),
wird nicht geschrieben; Trockenlauf protokolliert nur; Prüfung nach 60 s, Pause nach 3 Fehlschlägen bzw. 2 fremden
Änderungen in 1 h; Zurückschalten schreibt einmal den Rückfallwert. EMS-ESP bleibt nur lesend.
"""

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from heatpilot import boiler_entity as be
from heatpilot import boiler_rule as br
from heatpilot import floor_limit, main as main_mod, readiness, watchdog
from heatpilot.boiler_control import (ACT_DRY, ACT_FAIL, ACT_FALLBACK, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME, K_FAIL,
                                      K_FOREIGN, K_PAUSED, BoilerControl)
from heatpilot.boiler_flow import compute
from heatpilot.boiler_status import ready
from heatpilot.config import Config, validate
from heatpilot.control import Control
from heatpilot.ha_client import HAError
from heatpilot.notify import Notifier
from heatpilot.web import make_app

from heatpilot.discovery import Room

from test_control34 import MON, climate, make, run, slow_room, ts_of, wz
from test_dhw_control38 import RecHA

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
FLOW, HEAT = "number.boiler_selflowtemp", "switch.boiler_heatingactivated"
TARGETS = ["notify.handy"]
T0 = ts_of(MON, 12)


def house(tmp_path, mode="heatpilot", temp=20.5, target=21.0, a=0.1, regulator=False, rooms=None, **cfg_kw):
    """Engine mit Raum „Wz“ (macht mit, braucht ≈ 41 °C zum Halten bei 5 °C außen, heizt auf: 46 °C in 1 h), Kessel-Entitäten
    in HA, EMS-ESP antwortet (ohne Regler) und Weg B wie in ``main.App._build_control``."""
    cfg_kw.setdefault("boiler_keepalive", False)  # seit 0.48.1 eigene Tests (test_keepalive481)
    cfg = Config(control_mode="heatpilot", boiler_control=mode, emsesp_host="ems.local", notify_targets=TARGETS,
                 **cfg_kw)
    eng, _, _, _ = make(tmp_path, rooms=rooms, cfg=cfg, outdoor=["sensor.out"],
                        boiler={"sel_flow_temp": FLOW, "heating_activated": HEAT})
    eng.states["sensor.out"] = {"state": "5.0", "attributes": {}}
    climate(eng, "climate.wz", temp, target, hvac_action="heating")
    eng.rooms["climate.wz"] = slow_room(a=a, b=a * 1.25)  # 0.50.4: Aufheizen aus dem Modell
    eng.house.rls.updates = 500
    eng.house.rls.theta = [0.2, 2.0, 0.3]  # 200 W/K – plausibel
    eng.states[FLOW] = {"state": "30", "attributes": {"unit_of_measurement": "°C", "min": 0, "max": 90,
                                                      "friendly_name": "Boiler Gewählte Vorlauftemperatur"}}
    eng.states[HEAT] = {"state": "on", "attributes": {"friendly_name": "Boiler Heizbetrieb aktiviert"}}
    eng.states["notify.handy"] = {"state": "unknown", "attributes": {"friendly_name": "Handy"}}
    eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 40.0}}, time.time()
    # EMS-ESP meldet seit Langem ausdrücklich kein Gerät thermostat (regulator.note: viele Abfragen in Folge)
    eng.regulator_watch.update(last="fehlt", ts=time.time(), absent_since=0, absent_n=99)
    if regulator:
        eng.ems["thermostat"] = {"hc1_seltemp": 21.0}
    ha = RecHA()
    ents = {"flow": be.flow_target(cfg, eng.found, eng.states), "heating": be.heat_target(cfg, eng.found, eng.states)}
    writes = {"flow": ents["flow"][0], "heating": ents["heating"][0], "max": cfg.max_flow_temp,
              "floors": floor_limit.floor_rooms(cfg, eng.found.rooms)} if cfg.writes_boiler() else None
    ctl = Control(ha, eng.store, eng.found.rooms, states=eng.states, notify_targets=cfg.notify_targets, boiler=writes,
                  boiler_lock=lambda: be.regulator_block(eng))
    eng.notifier = Notifier(eng.store, ctl, cfg.notify_targets)
    eng.boiler_control = BoilerControl(eng, ctl, eng.notifier, ents)
    return eng, ha, eng.boiler_control


def step(bc, ts):
    """Ein Schritt wie ``main.boiler_step``: planen, schreiben."""
    bc.plan(ts)
    return run(bc.apply(ts))


def shows(eng, flow=None, heating=None):
    """Der Kessel zeigt (über HA, wie EMS-ESP es per MQTT meldet)."""
    if flow is not None:
        eng.states[FLOW]["state"] = str(flow)
    if heating is not None:
        eng.states[HEAT]["state"] = "on" if heating else "off"


def calls(ha):
    return [(p["domain"], p["service"], p["target"]["entity_id"], p["service_data"]) for p in ha.payloads
            if p["domain"] in ("number", "switch")]


def actions(eng):
    """Aktionen im Protokoll in der Reihenfolge des Eintragens (Control schreibt die echte Uhrzeit, der Ablauf die
    des Tests)."""
    return [r["action"] for r in eng.store.query("SELECT action FROM control_log ORDER BY rowid")]


def cycle(eng, ts, starts=4, run_min=3):
    """Kessel taktet: ``starts`` kurze Läufe in der letzten Stunde."""
    for i in range(starts):
        on = ts - 3000 + i * 600
        eng.store.add_burner_event(on, True)
        eng.store.add_burner_event(on + run_min * 60, False)
    eng.store.commit()


# ------------------------------------------------------------------ Regeln (reine Funktionen)
def test_decide_flow_one_kelvin_and_one_write_per_minute():
    t = T0
    assert br.decide_flow(42, 30.0, t).write and br.decide_flow(42, 30.0, t).value == 42
    assert not br.decide_flow(42, 41.5, t).write and "passt" in br.decide_flow(42, 41.5, t).why  # unter 1 K
    assert br.decide_flow(42, 41.0, t).write  # genau 1 K
    assert not br.decide_flow(42, 42.0, t).write  # kein „42, 42, 42“
    d = br.decide_flow(45, 42.0, t + 59, last_ts=t)
    assert not d.write and "je 60 s" in d.why and d.until == t + 60
    assert br.decide_flow(45, 42.0, t + 60, last_ts=t).write
    assert "Warmwasser lädt" in br.decide_flow(45, 42.0, t, charging=True).why
    assert not br.decide_flow(45, 42.0, t, paused=True).write
    assert not br.decide_flow(45, 42.0, t, checking=True).write
    assert "unbekannt" in br.decide_flow(45, None, t).why and not br.decide_flow(None, 42.0, t).write


def test_decide_heating_hold_five_minutes():
    t = T0
    assert br.decide_heating(False, True, t).write and br.decide_heating(False, True, t).value is False
    assert not br.decide_heating(True, True, t).write
    d = br.decide_heating(True, False, t + 299, last_ts=t)
    assert not d.write and d.why.startswith("Haltezeit bis") and d.until == t + 300
    assert br.decide_heating(True, False, t + 300, last_ts=t).write
    assert not br.decide_heating(True, None, t).write and not br.decide_heating(None, True, t).write


def test_boost_steps_of_two_up_to_ten_and_back():
    b, t = None, T0
    b = br.boost_step(b, True, t, 30)
    assert b["k"] == 2  # der erste Schritt sofort
    assert br.boost_step(b, True, t + 599, 30)["k"] == 2  # dann je 10 min
    for i in range(1, 10):
        b = br.boost_step(b, True, t + i * 600, 30)
    assert b["k"] == 10  # höchstens +10 K
    b = br.boost_step(b, False, t + 6000, 30)
    assert b["k"] == 8  # zurück, sobald kein Raum mehr wartet – sofort der erste Schritt
    assert br.boost_step(b, False, t + 6100, 30)["k"] == 8
    b = br.boost_step(b, False, t + 6600, 30)
    assert b["k"] == 6
    assert br.boost_step({"k": 6, "ts": t, "on": True}, True, t + 600, 3)["k"] == 3  # nur bis zur Grenze
    assert br.boost_step({"k": 4, "ts": t, "on": False}, False, t + 10, 0)["k"] == 0


def test_load_flow_and_limits():
    # Mindestleistung 5,4 kW, 22 kW je K/h, ein Raum a = 0,008 bei 20 °C: 0,176 kW je K → ab 51 °C
    assert br.load_flow(5.4, 22.0, [(0.008, 20.0)], 35, 75) == 51
    assert br.load_flow(5.4, 22.0, [(0.008, 20.0)], 35, 50) is None  # reicht auch der höchste nicht: nichts
    assert br.load_flow(0.5, 22.0, [(0.008, 20.0)], 35, 75) == 35
    assert br.load_flow(5.4, 22.0, [], 35, 75) is None
    cfg = Config(max_flow_temp=60)
    assert br.flow_limits(cfg) == (35, 60) and br.flow_limits(cfg, 40.5) == (35, 40) and br.flow_limits(cfg, 30)[1] == 30
    assert br.fallback_value(Config(), None) == 45 and br.fallback_value(Config(), 40.0) == 40
    assert br.fallback_value(Config(boiler_fallback_flow=50, max_flow_temp=48), None) == 48
    assert br.fallback_value(Config(), 30.0) is None  # unter 35 °C: nichts


def test_settings_default_off_and_clean():
    cfg = Config()
    assert (cfg.boiler_control, cfg.flow_set_entity, cfg.heating_switch_entity, cfg.boiler_fallback_flow) == (
        "aus", None, None, 45)
    assert not cfg.writes_boiler() and Config(boiler_control="heatpilot").writes_boiler()
    bad = Config.from_raw({"boiler_control": "voll", "flow_set_entity": "sensor.x", "heating_switch_entity": "Switch.X",
                           "boiler_fallback_flow": 99})
    assert (bad.boiler_control, bad.flow_set_entity, bad.heating_switch_entity, bad.boiler_fallback_flow) == (
        "aus", None, None, 45)
    clean, errors = validate({"boiler_control": "trocken", "flow_set_entity": FLOW, "heating_switch_entity": HEAT,
                              "boiler_fallback_flow": "50"})
    assert errors == [] and clean["boiler_fallback_flow"] == 50 and clean["heating_switch_entity"] == HEAT
    for raw in ({"flow_set_entity": "number.boiler_heatingtemp"}, {"flow_set_entity": "switch.x"},
                {"heating_switch_entity": "switch.boiler_heatingoff"},
                {"heating_switch_entity": "switch.thermostat_hc1_summermode"},
                {"boiler_control": "an"}, {"boiler_fallback_flow": 30}, {"dhw_set_entity": "number.boiler_selflowtemp"}):
        assert validate(raw)[1], raw


def test_entity_detection_and_strict_check():
    found = SimpleNamespace(boiler={"sel_flow_temp": "sensor.boiler_selflowtemp"}, thermostat={})
    states = {"number.ems_esp_boiler_selflowtemp": {}, "switch.ems_esp_boiler_heatingactivated": {},
              "switch.boiler_heatingoff": {}, "number.boiler_heatingtemp": {}}
    assert be.suggest_flow(found, states) == "number.ems_esp_boiler_selflowtemp"  # mit Präfix, nur number
    assert be.suggest_heat(found, states) == "switch.ems_esp_boiler_heatingactivated"
    assert be.flow_target(Config(flow_set_entity="number.mein_vorlauf"), found, {}) == ("number.mein_vorlauf",
                                                                                       "eingestellt")
    assert be.heat_target(Config(heating_switch_entity="input_boolean.x"), found, {}) == (None, None)
    ok = {"state": "40", "attributes": {"unit_of_measurement": "°C"}}
    assert be.flow_problem(FLOW, ok) is None
    assert "Heiztemperatur" in be.flow_problem("number.boiler_heatingtemp", ok)  # die Obergrenze, nie das Soll
    assert "Heizkreis" in be.flow_problem("number.thermostat_hc1_designtemp", ok)
    assert "Warmwasser-Soll" in be.flow_problem("number.boiler_dhw_seltemp", ok)
    assert "keine Temperatur" in be.flow_problem(FLOW, {"state": "40", "attributes": {"unit_of_measurement": "%"}})
    assert "nicht verfügbar" in be.flow_problem(FLOW, {"state": "unavailable", "attributes": {}})
    assert be.heat_problem(HEAT, {"state": "on"}) is None
    assert "Heizen abschalten" in be.heat_problem("switch.boiler_heatingoff", {"state": "on"})  # umgekehrt – nie
    assert "Warmwasser-Betrieb" in be.heat_problem("switch.boiler_wwactivated", {"state": "on"})
    assert "weder an noch aus" in be.heat_problem(HEAT, {"state": "auto"})
    assert be.flow_doubt("number.kessel_soll", ok) is not None and be.flow_doubt(FLOW, ok) is None
    assert be.flow_picker(FLOW, ok)["fits"] and not be.flow_picker("number.boiler_heatingtemp", ok)["fits"]
    assert be.heat_picker(HEAT, {"state": "on"}) == {"fits": True, "hint": True}


# ------------------------------------------------------------------ Ziel jetzt (boiler_flow)
def test_target_from_demand_floor_35_and_off(tmp_path):
    eng, ha, bc = house(tmp_path)
    t, _ = compute(eng, T0)
    assert (t.heating, t.flow, t.base) == (True, 46, 46) and t.reason == "Wz heizt auf 21,0 °C (ca. 1 h)"
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="idle")
    assert compute(eng, T0)[0].flow == 41  # hält 21 °C
    eng.rooms["climate.wz"] = slow_room(a=0.02, b=0.01)  # braucht nur 29 °C: unter 35 → kein Bedarf
    # seit 0.46.5 (Standard „Pumpe läuft weiter“): Heizbetrieb bleibt an, Vorlauf-Soll 20 °C – nur die Pumpe läuft
    t, _ = compute(eng, T0)
    assert (t.heating, t.flow, t.idle) == (True, br.PUMP_FLOW, True) and "unter 35 °C" in t.reason
    assert t.reason.endswith("Pumpe läuft, Vorlauf-Soll 20 °C")
    eng.cfg.boiler_idle = "aus"  # „Heizbetrieb ganz aus“: wie bis 0.46.4
    t, _ = compute(eng, T0)
    assert t.heating is False and t.flow is None and "unter 35 °C" in t.reason and t.reason.endswith("Heizbetrieb aus")
    climate(eng, "climate.wz", 21.0, 18.0, hvac_action="idle")  # Nacht: Raum über Soll – braucht keine Wärme
    eng.rooms["climate.wz"] = slow_room(a=0.008, b=0.01)
    assert compute(eng, ts_of(MON, 23))[0].heating is False


def test_target_minimum_from_load(tmp_path):
    eng, ha, bc = house(tmp_path, boiler_min_power_pct=30.0, boiler_nominal_kw=18.0)
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="heating")  # am Soll, Ventil offen
    t, _ = compute(eng, T0)
    # Haus 3,5 kW bei 21/5 °C auf 0,16 K/h verteilt, Heizwirkung 0,008 → 0,175 kW je K: 5,4 kW ab 52 °C
    assert t.base == 41 and t.load == 52 and t.flow == 52 and "Abnahme" in t.reason
    eng.cfg.max_flow_temp = 50  # reicht auch der höchste nicht: keine Sonderlogik
    t, _ = compute(eng, T0)
    assert t.load is None and t.flow == 41 and "lange Pausen" in t.load_text
    eng.cfg.max_flow_temp, eng.cfg.boiler_min_power_pct = 75, 0.0
    t, _ = compute(eng, T0)
    assert t.load is None and "Mindestleistung unbekannt" in t.load_text


def test_boost_when_cycling_and_room_waits_and_floor_cap_hard(tmp_path):
    eng, ha, bc = house(tmp_path, rooms=[wz(), Room("Bad", "climate.bad", platform="homekit_controller")])
    cycle(eng, T0)
    t, b = compute(eng, T0)
    assert t.boost_wanted and t.waiting == ["Wz"] and t.boost_k == 2 and t.flow == 48 and "Boost +2 K" in t.reason
    t, b = compute(eng, T0 + 600, b)
    assert t.boost_k == 4 and t.flow == 50
    # Fußboden im Bad direkt am Kessel, höchstens 49 °C: hart – auch beim Boost
    eng.cfg.rooms = [{"climate": "climate.bad", "name": "Bad", "floor_type": "direkt", "floor_max_flow": 49}]
    eng.cfg.floor_heating_rooms = ["climate.bad"]
    t, b = compute(eng, T0 + 1200, b)
    assert t.flow == 49 and t.cap == 49 and t.limited and t.boost_k == 3 and "begrenzt durch Fußboden Bad" in t.reason
    eng.cfg.rooms[0]["floor_max_flow"] = 30  # Grenze unter 35 °C: Heizbetrieb aus, kein Vorlauf darunter
    t, b = compute(eng, T0 + 1800, b)
    assert t.heating is False and t.flow is None and "unter 35 °C" in t.reason and b["k"] == 0


def test_boost_follows_cycling_not_the_room(tmp_path):
    """0.50: Der Boost hängt nur am Takten (mit offenem Ventil) – ein warmer Raum beendet ihn nicht mehr; 10 min,
    nachdem er nicht mehr gewollt ist, zurück in 2-K-Schritten."""
    eng, ha, bc = house(tmp_path)
    cycle(eng, T0)
    t, b = compute(eng, T0)
    t, b = compute(eng, T0 + 600, b)
    assert t.boost_k == 4
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="heating")  # warm, Ventil offen, Kessel taktet weiter
    t, b = compute(eng, T0 + 630, b)
    assert t.boost_k == 4 and t.boost_wanted and t.waiting == []
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="idle")  # Ventil zu: nicht mehr gewollt …
    t, b = compute(eng, T0 + 660, b)
    assert t.boost_k == 4 and not t.boost_wanted
    t, b = compute(eng, T0 + 1260, b)  # … 10 min danach zurück in 2-K-Schritten
    assert t.boost_k == 2
    t, b = compute(eng, T0 + 1860, b)
    assert t.boost_k == 0


# ------------------------------------------------------------------ Ablauf: aus, trocken, heatpilot
def test_off_does_nothing(tmp_path):
    eng, ha, bc = house(tmp_path, mode="aus")
    assert step(bc, T0) == 0 and calls(ha) == [] and eng.store.control_log(10) == []
    assert bc.as_dict(T0)["status_text"] == "Kessel: aus – Regler bzw. Kessel regeln selbst"
    with pytest.raises(PermissionError):  # ohne „heatpilot“ gibt es den Schreibweg gar nicht
        run(bc.control.set_flow(45, "Test"))


def test_dry_run_logs_even_with_regulator(tmp_path):
    eng, ha, bc = house(tmp_path, mode="trocken", regulator=True)
    assert step(bc, T0) == 0 and calls(ha) == []
    assert actions(eng) == [ACT_DRY] and "würde Vorlauf-Soll 46 °C schreiben" in eng.store.control_log(1)[0]["reason"]
    assert step(bc, T0 + 30) == 0 and actions(eng) == [ACT_DRY]  # nicht jeden Schritt dasselbe
    assert bc.as_dict(T0)["status_text"].startswith("Kessel: Beobachten · würde Vorlauf 46 °C setzen (Wz")
    assert bc.reference() == {"flow": 46, "heating": True}


def test_regulator_on_bus_blocks_writing(tmp_path):
    eng, ha, bc = house(tmp_path, regulator=True)
    assert step(bc, T0) == 0 and calls(ha) == [] and eng.store.control_log(10) == []
    d = bc.as_dict(T0)
    assert d["blocked"].startswith("EMS-ESP meldet einen Regler") and "überschreibt alle 60 s" in d["blocked"]
    assert d["status_text"].startswith("Kessel: gesperrt – EMS-ESP meldet einen Regler") and d["active"] is False
    with pytest.raises(PermissionError, match="Regler"):  # auch Control sperrt
        run(bc.control.set_flow(46, "Test"))
    del eng.ems["thermostat"]
    eng.ems_ts = None  # EMS-ESP antwortet nicht: unbekannt – ebenfalls gesperrt
    assert step(bc, T0 + 30) == 0 and "unbekannt" in bc.view["blocked"]
    eng.cfg.emsesp_host = None
    assert be.regulator(eng)["present"] is None and "ohne EMS-ESP" in be.regulator(eng)["text"]
    eng.cfg.emsesp_host, eng.ems_ts = "ems.local", time.time()
    # frische Regler-Werte in Home Assistant sperren auch
    eng.found.thermostat["hc_seltemp"] = "sensor.thermostat_hc1_seltemp"
    eng.states["sensor.thermostat_hc1_seltemp"] = {"state": "21", "last_updated": "2999-01-01T00:00:00+00:00"}
    assert be.regulator(eng, time.time())["present"] is True
    eng.states["sensor.thermostat_hc1_seltemp"]["last_updated"] = "2020-01-01T00:00:00+00:00"  # alt: zählt nicht
    assert be.regulator(eng)["present"] is False
    assert step(bc, T0 + 60) == 1 and calls(ha) == [("number", "set_value", FLOW, {"value": 46})]


def test_writes_checks_after_60_s_and_accepts(tmp_path):
    eng, ha, bc = house(tmp_path)
    assert step(bc, T0) == 1 and calls(ha) == [("number", "set_value", FLOW, {"value": 46})]
    assert actions(eng) == ["flow_set"] and bc.state["flow"]["check"]["due"] == T0 + 60
    shows(eng, flow=46)
    assert step(bc, T0 + 30) == 0 and bc.state["flow"].get("check")  # noch keine Prüfung
    assert step(bc, T0 + 60) == 0 and not bc.state["flow"].get("check")
    assert bc.state["last_check"]["ok"] and bc.state["fails"] == 0
    d = bc.as_dict(T0 + 60)
    assert d["status_text"] == "Kessel: HeatPilot · Vorlauf 46 °C (Wz heizt auf 21,0 °C (ca. 1 h))" and d["active"]
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="idle")  # warm: 41 °C – 5 K weniger
    assert step(bc, T0 + 90) == 0 and "10 min niedriger" in bc.view["decision"]["flow"]  # senken erst nach 10 min
    assert bc.as_dict(T0 + 90)["decision_until"]["flow"] is not None
    assert step(bc, T0 + 660) == 0 and step(bc, T0 + 690) == 1 and calls(ha)[-1][3] == {"value": 41}


def test_heating_off_and_on_with_hold(tmp_path):
    eng, ha, bc = house(tmp_path)
    eng.cfg.boiler_idle = "aus"  # seit 0.46.5 nur bei „Heizbetrieb ganz aus“ (Standard: Pumpe läuft weiter)
    climate(eng, "climate.wz", 21.6, 21.0, hvac_action="idle")  # über Soll: braucht keine Wärme
    assert step(bc, T0) == 0 and "erst nach 15 min ohne Bedarf" in bc.view["decision"]["heating"]  # aus erst nach 15 min
    assert step(bc, T0 + 870) == 0 and step(bc, T0 + 900) == 1 and calls(ha) == [("switch", "turn_off", HEAT, {})]
    assert actions(eng) == ["heating_off"]
    shows(eng, heating=False)
    climate(eng, "climate.wz", 20.5, 21.0, hvac_action="heating")  # gleich wieder unter Soll: Haltezeit 5 min
    assert step(bc, T0 + 1020) == 1 and bc.view["decision"]["heating"].startswith("Haltezeit bis")
    assert calls(ha)[1:] == [("number", "set_value", FLOW, {"value": 46})]  # das Vorlauf-Soll schon vorher
    shows(eng, flow=46)
    assert step(bc, T0 + 1200) == 1 and calls(ha)[-1] == ("switch", "turn_on", HEAT, {})


def test_three_failures_pause_and_resume(tmp_path):
    eng, ha, bc = house(tmp_path)
    t = T0
    for i in range(3):  # der Kessel nimmt den Wert nicht an
        step(bc, t)
        t += 60
        step(bc, t)  # Prüfung → Fehlschlag, im selben Schritt der nächste Versuch (60 s um)
    assert bc.paused() and bc.state["paused"]["reason"] == "3 Fehlschläge in Folge"
    assert actions(eng).count(ACT_FAIL) == 3 and ACT_PAUSE in actions(eng)
    assert {K_FAIL, K_PAUSED} <= set(eng.notifier.open_keys())
    n = len(calls(ha))
    assert step(bc, t + 600) == 0 and len(calls(ha)) == n  # pausiert: nichts
    assert "pausiert" in bc.as_dict(t)["status_text"]
    assert bc.resume(t + 700) and not bc.resume(t + 701)  # ohne Pause ändert sich nichts
    assert actions(eng)[-1] == ACT_RESUME and step(bc, t + 760) == 1


def test_foreign_writer_twice_in_an_hour_pauses(tmp_path):
    eng, ha, bc = house(tmp_path)
    step(bc, T0)
    shows(eng, flow=46)
    step(bc, T0 + 60)
    shows(eng, flow=55)  # der Regler hängt doch noch dran bzw. eine Automation
    step(bc, T0 + 90)
    assert ACT_FOREIGN in actions(eng) and K_FOREIGN in eng.notifier.open_keys()
    assert "Regler noch am Bus" in eng.notifier.open_keys()[K_FOREIGN]
    step(bc, T0 + 150)  # HeatPilot schreibt zurück auf 46
    shows(eng, flow=46)
    step(bc, T0 + 210)
    shows(eng, flow=60)
    step(bc, T0 + 240)
    assert bc.paused() and "fremde Änderungen" in bc.state["paused"]["reason"]


def test_dhw_charge_leaves_flow_alone(tmp_path):
    eng, ha, bc = house(tmp_path)
    eng.ems["boiler"]["dhw_charging"] = "on"
    assert step(bc, T0) == 0 and calls(ha) == []
    assert "Warmwasser lädt" in bc.view["decision"]["flow"] and "Warmwasser lädt" in bc.as_dict(T0)["status_text"]
    eng.ems["boiler"]["dhw_charging"] = "off"
    assert step(bc, T0 + 30) == 1


def test_switch_back_writes_fallback_once(tmp_path):
    eng, ha, bc = house(tmp_path)
    step(bc, T0)
    new = SimpleNamespace(cfg=Config(boiler_control="aus"))
    run(main_mod.App._boiler_back(new, eng, eng))  # wie App.build: über die alte Schreibstelle
    assert calls(ha)[-2:] == [("number", "set_value", FLOW, {"value": 45}), ("switch", "turn_on", HEAT, {})]
    assert actions(eng)[-1] == ACT_FALLBACK and bc.state["fallback"]["ok"]
    eng.cfg.boiler_control = "aus"
    bc.plan(T0 + 30)
    assert any("Regler wieder anschließen" in w for w in bc.warnings(T0 + 30))
    run(main_mod.App._boiler_back(new, eng, eng))  # schon „aus“: nichts mehr
    assert len(calls(ha)) == 3
    # mit Regler am Bus wird nicht zurückgeschrieben
    eng2, ha2, bc2 = house(tmp_path / "b", regulator=True)
    res = run(bc2.fallback(45))
    assert not res["ok"] and "Regler" in res["text"] and calls(ha2) == []


def test_restart_keeps_state_and_check(tmp_path):
    eng, ha, bc = house(tmp_path)
    step(bc, T0)
    again = BoilerControl(eng, bc.control, eng.notifier, {"flow": (FLOW, "erkannt"), "heating": (HEAT, "erkannt")})
    assert again.state["flow"]["check"]["value"] == 46 and again.state["mode"] == "heatpilot"
    shows(eng, flow=46)
    again.plan(T0 + 60)
    assert again.state["last_check"]["ok"]


def test_build_control_writes_only_with_heatpilot_and_carries_state(tmp_path):
    eng, ha, bc = house(tmp_path)
    step(bc, T0)
    app = SimpleNamespace(cfg=eng.cfg, ha=ha, store=eng.store)
    old = SimpleNamespace(notifier=eng.notifier, dhw_control=None, control_loop=None, boiler_control=bc,
                          watchdog={"ts": 1}, heartbeat_ts=5.0)
    main_mod.App._build_control(app, eng, eng.found, old)
    new = eng.boiler_control
    assert new is not bc and new.state is bc.state and bc.stopped and eng.heartbeat_ts == 5.0
    assert (app.control.flow_entity, app.control.heat_entity) == (FLOW, HEAT)
    assert new.entities == {"flow": FLOW, "heating": HEAT} and new.sources["flow"] == "erkannt"
    eng.ems["thermostat"] = {"hc1_seltemp": 21.0}  # die letzte Sperre in Control: Regler am Bus
    with pytest.raises(PermissionError, match="Regler"):
        run(app.control.set_heating(False, "Test"))
    app.cfg = eng.cfg = Config(boiler_control="trocken")
    main_mod.App._build_control(app, eng, eng.found, None)
    assert app.control.flow_entity is None and app.control.heat_entity is None
    with pytest.raises(PermissionError):
        run(app.control.set_flow(45, "Test"))


# ------------------------------------------------------------------ Control: nur die zwei Entitäten, nur in den Grenzen
def test_control_only_two_entities_within_limits(tmp_path):
    eng, ha, bc = house(tmp_path, max_flow_temp=60)
    c = bc.control
    run(c.set_flow(35, "Test"))
    run(c.set_flow(60, "Test"))
    run(c.set_heating(False, "Test"))
    for bad in (34, 61, 45.5, True, "45"):
        with pytest.raises(PermissionError):
            run(c.set_flow(bad, "Test"))
    with pytest.raises(PermissionError):
        run(c._call("number", "set_value", "number.boiler_heatingtemp", {"value": 50}))
    with pytest.raises(PermissionError):
        run(c._call("number", "set_value", FLOW, {"value": 50, "x": 1}))
    with pytest.raises(PermissionError):
        run(c._call("switch", "turn_on", "switch.boiler_heatingoff", {}))
    with pytest.raises(PermissionError):
        run(c._call("number", "set_value", "number.boiler_burnminperiod", {"value": 10}))  # Antipendelzeit: nie
    with pytest.raises(PermissionError):
        run(c._call("switch", "toggle", HEAT, {}))
    with pytest.raises(PermissionError):
        run(c.set_heating(1, "Test"))
    # Fußboden-Grenze hart, mit den Messwerten von jetzt
    c.floors = [floor_limit.FloorRoom("Bad", "climate.wz", "direkt", 40.0, (("climate.wz", None),))]
    run(c.set_flow(40, "Test"))
    with pytest.raises(PermissionError, match="Fußboden"):
        run(c.set_flow(41, "Test"))
    eng.states[FLOW]["attributes"]["max"] = 38  # Grenzen der Entität
    with pytest.raises(PermissionError):
        run(c.set_flow(39, "Test"))
    assert calls(ha) == [("number", "set_value", FLOW, {"value": 35}), ("number", "set_value", FLOW, {"value": 60}),
                         ("switch", "turn_off", HEAT, {}), ("number", "set_value", FLOW, {"value": 40})]
    assert actions(eng) == ["flow_set", "flow_set", "heating_off", "flow_set"]


def test_emsesp_read_only_and_new_files_readable():
    src = (PKG / "emsesp.py").read_text(encoding="utf-8")
    assert not re.search(r"\.(post|put|patch|delete|request)\(", src)
    http = re.compile(r"(session|_open\(\))\.(post|put|patch|delete|request)\(")
    assert {p.name for p in PKG.glob("*.py") if http.search(p.read_text(encoding="utf-8"))} == {"ha_client.py"}
    for name in ("boiler_control.py", "boiler_rule.py", "boiler_status.py", "boiler_flow.py", "boiler_entity.py",
                 "watchdog.py", "entity_guard.py", "room_entry.py", "control.py", "config.py", "main.py", "web.py",
                 "preview.py", "readiness.py", "notify.py", "controlloop.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 600 and text.startswith('"""'), name
    for name in ("boiler_control.py", "boiler_rule.py", "boiler_status.py", "boiler_flow.py", "watchdog.py",
                 "entity_guard.py"):
        code = re.sub(r'""".*?"""', "", (PKG / name).read_text(encoding="utf-8"), flags=re.DOTALL).lower()
        assert "emsesp" not in code.replace("ems-esp", ""), name
    yaml = (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    assert "options: {}" in yaml and "schema: {}" in yaml  # kein YAML in HeatPilot


# ------------------------------------------------------------------ Wächter, Heartbeat
class AutoHA:
    def __init__(self, configs, fail=()):
        self.configs, self.fail, self.set = configs, set(fail), []

    async def automation_config(self, eid):
        if eid in self.fail:
            raise HAError("nicht lesbar")
        return {"config": self.configs.get(eid, {})}

    async def set_state(self, entity_id, state, attributes):
        self.set.append((entity_id, state, attributes))


def test_watchdog_scan_status_and_guide(tmp_path):
    eng, ha, bc = house(tmp_path)
    states = {"automation.waechter": {"state": "on", "attributes": {"friendly_name": "HeatPilot-Wächter"}},
              "automation.licht": {"state": "on", "attributes": {}}, "sensor.x": {"state": "1"}}
    configs = {"automation.waechter": {"triggers": [{"trigger": "state", "entity_id": [watchdog.HEARTBEAT],
                                                     "for": {"hours": 1}}],
                                       "conditions": [{"condition": "state", "entity_id": watchdog.HEARTBEAT,
                                                       "attribute": "kessel", "state": "heatpilot"}],
                                       "actions": [{"action": "number.set_value", "target": {"entity_id": FLOW}}]},
               "automation.licht": {"trigger": [{"platform": "state", "entity_id": "binary_sensor.tuer"}]}}
    assert watchdog.status(eng, "heatpilot")["state"] == "unbekannt"
    eng.watchdog = run(watchdog.scan(AutoHA(configs), states, FLOW))
    w = watchdog.status(eng, "heatpilot")
    assert w["state"] == "erkannt" and w["text"] == "Wächter erkannt: HeatPilot-Wächter"
    states["automation.waechter"]["state"] = "off"
    eng.watchdog = run(watchdog.scan(AutoHA(configs), states, FLOW))
    assert watchdog.status(eng, "heatpilot")["state"] == "aus"
    eng.watchdog = run(watchdog.scan(AutoHA({}), states, FLOW))
    assert watchdog.status(eng, "heatpilot")["state"] == "fehlt"
    eng.watchdog = run(watchdog.scan(AutoHA(configs, fail={"automation.waechter", "automation.licht"}), states))
    assert watchdog.status(eng, "heatpilot")["state"] == "unbekannt" and "nicht lesbar" in eng.watchdog["error"]
    g = watchdog.guide(FLOW, HEAT, 45, TARGETS)
    assert g["values"] == {"trigger_entity": "sensor.heatpilot_heartbeat", "for": "01:00:00", "flow_entity": FLOW,
                           "flow_value": 45, "heating_entity": HEAT, "message": g["values"]["message"],
                           "condition": "Attribut „kessel“ von sensor.heatpilot_heartbeat ist „heatpilot“"}
    assert any(FLOW in s and "45" in s for s in g["steps"]) and any(HEAT in s for s in g["steps"])
    assert not any("yaml" in s.lower() for s in g["steps"])


def test_heartbeat_every_minute_and_scan_only_when_not_off(tmp_path):
    eng, ha, bc = house(tmp_path)
    auto = AutoHA({})
    app = SimpleNamespace(eng=eng, ha=auto)
    assert run(main_mod.heartbeat(app, 1000.0)) and not run(main_mod.heartbeat(app, 1030.0))
    assert run(main_mod.heartbeat(app, 1060.0)) and len(auto.set) == 2
    eid, state, attrs = auto.set[0]
    assert eid == watchdog.HEARTBEAT and attrs["device_class"] == "timestamp" and attrs["rueckfall_vorlauf"] == 45
    assert run(main_mod.scan_watchdog(app, 2000.0)) and not run(main_mod.scan_watchdog(app, 2300.0))
    eng.cfg.boiler_control = "aus"
    eng.watchdog = {}
    assert not run(main_mod.scan_watchdog(app, 5000.0))


def test_boiler_step_in_live_loop_sends_notes(tmp_path):
    eng, ha, bc = house(tmp_path)
    app = SimpleNamespace(eng=eng, ha=ha)
    assert run(main_mod.boiler_step(app, T0)) == 1
    assert ha.set and ha.set[-1][0] == "binary_sensor.heatpilot_problem"


# ------------------------------------------------------------------ notify: Rückfall auf die Handy-App
def test_notify_entity_falls_back_to_mobile_app_service(tmp_path):
    eng, ha, bc = house(tmp_path)
    c = bc.control

    async def services():
        return {"notify": {"mobile_app_handy": {}, "send_message": {}}}

    ha.get_services = services
    ha.fail = [HAError("Failed to send via mobile_app")]
    run(c.notify("notify.handy", "HeatPilot", "Text"))
    assert [p["service"] for p in ha.notes()] == ["mobile_app_handy"]  # einmal über den Dienst
    ha.fail = [asyncio.TimeoutError()]  # Zeitüberlauf: nicht doppelt schicken
    with pytest.raises(asyncio.TimeoutError):
        run(c.notify("notify.handy", "HeatPilot", "Text"))
    ha.fail = [HAError("Failed")]

    async def no_services():
        return {"notify": {"send_message": {}}}

    c._services, ha.get_services = None, no_services
    with pytest.raises(HAError):  # kein passender Dienst: der Fehler bleibt
        run(c.notify("notify.handy", "HeatPilot", "Text"))
    assert len(ha.notes()) == 1


# ------------------------------------------------------------------ Bereit für die Steuerung?
def test_readiness_block(tmp_path):
    eng, ha, bc = house(tmp_path, regulator=True)
    r = readiness.assess(eng, T0)["boiler_control"]
    assert r["level"] == "nicht bereit" and r["headline"].startswith("Nicht bereit – EMS-ESP meldet einen Regler")
    assert r["flow"]["entity"] == FLOW and r["flow"]["ok"] and r["heating"]["ok"] and r["regulator"]["present"]
    del eng.ems["thermostat"]
    r = ready(eng, T0)
    assert r["level"] == "eingeschränkt" and any("Wächter" in x for x in r["limits"])
    assert any("Mindestleistung unbekannt" in x for x in r["limits"]) and r["fallback"] == 45
    eng.watchdog = {"ts": T0, "found": [{"entity_id": "automation.w", "name": "W", "on": True, "sets_flow": True}],
                    "error": None, "checked": 1}
    eng.cfg.boiler_min_power_pct, eng.cfg.boiler_nominal_kw = 30.0, 18.0
    r = ready(eng, T0)
    assert r["level"] == "bereit" and r["headline"].startswith("Läuft – ") and r["min_power_kw"] == 5.4
    eng.ems["boiler"]["heatingoff"] = "on"  # „Heizen abschalten“ an: EMS-ESP hält die Heizung aus
    r = ready(eng, T0)
    assert r["level"] == "nicht bereit" and "Heizen abschalten" in r["blockers"][0]
    assert step(bc, T0) == 0 and "Heizen abschalten" in bc.view["blocked"]


# ------------------------------------------------------------------ Abfragen
def fake_app(eng, ha):
    connected = asyncio.Event()
    connected.set()

    async def get_services():
        return {"notify": {}}

    async def none():
        return {}

    async def registry():
        return []

    app = SimpleNamespace(eng=eng, cfg=eng.cfg, control=eng.boiler_control.control, status="läuft", configured=True,
                          ha=SimpleNamespace(connected=connected, get_services=get_services,
                                             info=lambda: {"connected": True, "token_present": True, "last_error": ""}))
    app.refresh_rooms, app.registry, app.suggestions = none, registry, none
    return app


def test_api_boiler_resume_heat_entities(tmp_path):
    eng, ha, bc = house(tmp_path)
    step(bc, T0)
    bc.state["paused"] = {"ts": T0, "reason": "3 Fehlschläge in Folge"}
    app = fake_app(eng, ha)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            j = await (await client.get("/api/boiler")).json()
            assert j["boiler"]["mode"] == "heatpilot" and j["boiler"]["paused"] is True
            assert j["boiler"]["entities"]["flow"]["entity"] == FLOW and j["boiler"]["rules"]["boost_max_k"] == 10
            assert j["boiler"]["watchdog"]["guide"]["values"]["flow_entity"] == FLOW
            assert j["readiness"]["level"] == "nicht bereit"
            r = await client.post("/api/boiler/resume", json={})
            assert r.status == 200 and (await r.json())["boiler"]["paused"] is False
            r = await client.post("/api/boiler/resume", json={})
            assert r.status == 409
            heat = await (await client.get("/api/heat")).json()
            assert heat["boiler"]["mode"] == "heatpilot" and "boiler_control" in heat["readiness"]
            assert any(row["kind"] == "boiler" for row in heat["control"]["log"])
            ents = {e["id"]: e for e in await (await client.get("/api/entities")).json()}
            assert ents[FLOW]["flow"]["fits"] and ents[HEAT]["heating"]["fits"]
            s = await (await client.get("/api/settings")).json()
            assert s["settings"]["boiler_control"] == "heatpilot" and s["boiler_control"]["suggested"] == {
                "flow": FLOW, "heating": HEAT, "period": None}  # 0.48: Antipendelzeit (hier keine erkannt)
            assert s["boiler_control"]["fallback_range"] == [35, 75]
            summ = await (await client.get("/api/summary")).json()
            assert summ["boiler_control"]["mode"] == "heatpilot"
            p = await (await client.get("/api/preview")).json()
            assert p["preview"]["boiler_mode"] == "heatpilot" and p["preview"]["flow_writes"] == "schreibt"
        finally:
            await client.close()

    n = len(calls(ha))
    asyncio.run(go())
    assert len(calls(ha)) == n  # keine Abfrage schreibt an den Kessel


# ------------------------------------------------------------------ Version, Doku
def test_version_changelog_docs():
    from heatpilot import __version__

    assert __version__ >= "0.41.0"  # seit 0.42 prüft test_disinfect42 die genaue Version
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## 0.41.0")[1].split("\n## ")[0]
    assert head.startswith("\n") and "Kessel über HeatPilot" in head and "Standard aus" in head
    for personal in ("Ingo", "BK15", "ES73", "Sieger", "18 kW", "Tado-App"):  # keine persönlichen Werte
        assert personal not in head, personal
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for text in ("## Kessel über HeatPilot (Weg B)", "sensor.heatpilot_heartbeat", "Ablauf zum Regler-Abbau",
                 "Regler am Bus – er überschreibt alle 60 s", "„Anleitung Wächter“", "kein YAML nötig"):
        assert text in docs, text
    plan = (ROOT / "docs" / "planung-vorlauf-ems.md").read_text(encoding="utf-8")
    assert "## Weg B – gebaut (0.41)" in plan and "## Desinfektion – gebaut (0.42)" in plan  # seit 0.42 gebaut
    row = next(line for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines() if line.startswith("| 6 |"))
    assert "0.41 – Weg B gebaut" in row


def test_functions_stay_short():
    import inspect

    from heatpilot import boiler_control, boiler_entity, boiler_flow, boiler_rule, boiler_status, watchdog

    for mod in (boiler_control, boiler_entity, boiler_flow, boiler_rule, boiler_status, watchdog):
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ == mod.__name__:
                assert len(inspect.getsource(fn).splitlines()) <= 60, f"{mod.__name__}.{name}"
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ == mod.__name__:
                for name, fn in inspect.getmembers(cls, inspect.isfunction):
                    if fn.__module__ == mod.__name__ and fn.__qualname__.startswith(cls.__name__ + "."):
                        try:
                            lines = len(inspect.getsource(fn).splitlines())
                        except OSError:  # von dataclass erzeugt
                            continue
                        assert lines <= 60, f"{cls.__name__}.{name}"
