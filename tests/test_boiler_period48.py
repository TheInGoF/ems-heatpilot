"""0.48: Antipendelzeit anpassen – HeatPilot führt die Taktsperre des Kessels mit (``boiler_period``, ``period_control``).

Sicherheitsregeln (jede hat einen Test): Schalter Standard aus; geschrieben wird nur mit „Antipendelzeit anpassen“ und
„heatpilot“, nur ``number.set_value`` auf die eine Entität der Antipendelzeit, ganze Minuten 5–60; „Beobachten“ protokolliert
nur; nur bei Änderung ab 5 min, höchstens alle 15 min; Prüfung nach 60 s; fremde Änderungen erkannt (2 in 1 h → Pause);
gesperrt wie der Kessel; beim Ausschalten einmal der Grundwert zurück.
"""

from types import SimpleNamespace

import pytest
from heatpilot import boiler_period as bp
from heatpilot import main as main_mod
from heatpilot.boiler_flow import Target
from heatpilot.config import Config, validate
from heatpilot.control import Control
from test_boiler41 import FLOW, T0, calls, climate, house, run
from test_boiler41 import shows as shows_flow
from test_boiler41 import step as step41

PER = "number.boiler_burnminperiod"
LOAD = "Abnahme: 2 Räume nehmen die Mindestleistung 5,4 kW ab 52 °C ab"


def setup(tmp_path, mode="heatpilot", period=True, value=25, enough=True, **kw):
    """Haus wie in test_boiler41 mit Entität der Antipendelzeit; Schreibstellen wie ``main.App._build_control``.
    ``enough``: Mindestleistung bekannt, Raum am Soll mit offenem Ventil – die Abnahme reicht ab 52 °C."""
    if enough:
        kw.update(boiler_min_power_pct=30.0, boiler_nominal_kw=18.0)
    eng, ha, _ = house(tmp_path, mode=mode, boiler_period=period, **kw)
    if enough:
        climate(eng, "climate.wz", 21.0, 21.0, hvac_action="heating")
    eng.states[PER] = {"state": str(value), "attributes": {"unit_of_measurement": "min", "min": 0, "max": 120,
                                                           "friendly_name": "Boiler Antipendelzeit"}}
    app = SimpleNamespace(cfg=eng.cfg, ha=ha, store=eng.store)
    main_mod.App._build_control(app, eng, eng.found, None)
    return eng, ha, eng.boiler_control


def step(bc, ts):
    """Ein Schritt wie ``main.boiler_step``; der Kessel übernimmt das Vorlauf-Soll (hier geht es um die Sperre)."""
    n = step41(bc, ts)
    flow = (bc.state.get("flow", {}).get("last") or {}).get("value")
    if flow is not None:
        shows_flow(bc.eng, flow=flow)
    return n


def per_calls(ha):
    return [c[3]["value"] for c in calls(ha) if c[2] == PER]


def acts(eng, prefix="boiler_period"):
    return [r["action"] for r in eng.store.query("SELECT action FROM control_log ORDER BY rowid")
            if r["action"].startswith(prefix)]


def shows(eng, value):
    eng.states[PER]["state"] = str(value)


# ------------------------------------------------------------------ Regel
def test_rule_short_long_unchanged():
    t = Target(True, 52, "x", load=52, load_text=LOAD, cycling={"cycling": False, "starts_h": 1})
    assert bp.want(t, 30) == (10, "Abnahme reicht: 2 Räume nehmen die Mindestleistung 5,4 kW ab 52 °C ab")
    long = bp.want(Target(True, 45, "x", load=None, load_text="Abnahme-Regel aus: Mindestleistung unbekannt"), 30)
    assert long == (30, "Abnahme reicht nicht – Abnahme-Regel aus: Mindestleistung unbekannt")
    cyc = Target(True, 52, "x", load=52, load_text=LOAD, cycling={"cycling": True, "starts_h": 4})
    assert bp.want(cyc, 25) == (25, "Kessel taktet (4 Starts in 1 h) – lange Sperre")
    for t in (Target(True, 20, "x", idle=True), Target(False, None, "x"), Target(None, None, "x"),
              Target(True, 52, "x", load=52, load_text=LOAD, charging=True), None):
        assert bp.want(t, 25)[0] is None  # nur Pumpe, aus, nicht rechenbar, Warmwasser lädt: nichts ändern
    assert [bp.base_of(v) for v in (None, 10, 25, 40, 90)] == [25, 25, 25, 40, 60]  # mindestens 25, höchstens 60
    assert (bp.SHORT_MIN, bp.LONG_MIN, bp.LO_MIN, bp.HI_MIN, bp.STEP_MIN, bp.HOLD_S) == (10, 25, 5, 60, 5, 900)


def test_decide_step_hold_check():
    assert bp.decide(10, 25, T0).write
    assert not bp.decide(22, 25, T0).write and "unter 5 min" in bp.decide(22, 25, T0).why
    held = bp.decide(10, 25, T0, last_ts=T0 - 600)
    assert not held.write and held.until == T0 + 300 and held.why.startswith("Haltezeit 15 min bis")
    assert bp.decide(10, 25, T0, last_ts=T0 - 900).write
    assert not bp.decide(10, 25, T0, checking=True).write and not bp.decide(10, 25, T0, paused=True).write
    assert not bp.decide(10, None, T0).write and not bp.decide(None, 25, T0).write


# ------------------------------------------------------------------ Einstellungen, Standard aus
def test_default_off_and_settings(tmp_path):
    assert Config().boiler_period is False and Config().period_set_entity is None
    clean, errors = validate({"boiler_period": True, "period_set_entity": PER})
    assert not errors and clean == {"boiler_period": True, "period_set_entity": PER}
    assert validate({"period_set_entity": "sensor.boiler_burnminperiod"})[1]  # nur number
    cfg = Config.from_raw({"boiler_period": "ja", "period_set_entity": "switch.x"})
    assert cfg.boiler_period is False and cfg.period_set_entity is None  # nur ein echtes „an“, nur number
    eng, ha, bc = setup(tmp_path, period=False)
    assert bc.control.period_entity is None and bc.period.entity == PER  # erkannt, aber nicht beschreibbar
    assert step(bc, T0) == 1 and per_calls(ha) == [] and acts(eng) == []  # nur das Vorlauf-Soll
    d = bc.as_dict(T0)
    assert d["period"]["status_text"] is None and "Die Antipendelzeit des Kessels bleibt, wie sie ist" in d["notes"]
    with pytest.raises(PermissionError):
        run(bc.control.set_period(10, "Test"))


# ------------------------------------------------------------------ Schreiben, Prüfung, Haltezeit
def test_writes_short_checks_and_holds(tmp_path):
    eng, ha, bc = setup(tmp_path)
    assert step(bc, T0) == 2 and per_calls(ha) == [10]  # Vorlauf-Soll 52 °C und Antipendelzeit 10 min
    assert acts(eng) == ["boiler_period"] and bc.period.state["base"] == 25
    assert bc.period.state["check"]["due"] == T0 + 60
    shows(eng, 10)
    step(bc, T0 + 60)
    assert bc.period.state["last_check"]["ok"] and bc.period.state["fails"] == 0
    p = bc.as_dict(T0 + 60)["period"]
    assert p["status_text"] == ("Antipendelzeit: 10 min (Abnahme reicht: 1 Raum nimmt die Mindestleistung 5,4 kW ab "
                                "52 °C ab)")
    assert bc.log_rows()[0]["kind"] == "boiler" and any(r["action"] == "boiler_period" for r in bc.log_rows())
    eng.cfg.boiler_min_power_pct = 0.0  # Abnahme unbekannt: lang = Grundwert – aber erst nach 15 min
    step(bc, T0 + 90)
    assert per_calls(ha) == [10] and bc.period.view["decision"].startswith("Haltezeit 15 min")
    step(bc, T0 + 900)  # (das Vorlauf-Soll sinkt hier ebenfalls)
    assert per_calls(ha) == [10, 25]


def test_check_fails_and_foreign_change(tmp_path):
    eng, _, bc = setup(tmp_path)
    step(bc, T0)
    step(bc, T0 + 60)  # Kessel zeigt weiter 25
    assert acts(eng) == ["boiler_period", "boiler_period_failed"] and bc.period.state["fails"] == 1
    eng2, ha2, bc2 = setup(tmp_path / "b")
    step(bc2, T0)
    shows(eng2, 10)
    step(bc2, T0 + 60)
    shows(eng2, 30)  # jemand anderes stellt 30 min
    step(bc2, T0 + 120)
    assert acts(eng2)[-1] == "boiler_period_foreign" and bc2.period.state["base"] == 30
    assert bc2.period.view["decision"].startswith("Haltezeit") and per_calls(ha2) == [10]  # Haltezeit neu
    shows(eng2, 35)
    step(bc2, T0 + 180)
    assert bc2.period.state["paused"]["reason"] == "2 fremde Änderungen in 1 h"
    assert bc2.as_dict(T0 + 180)["period"]["status_text"] == "Antipendelzeit: pausiert – 2 fremde Änderungen in 1 h"
    eng2.cfg.boiler_period = False  # Schalter aus und wieder an: Pause vorbei
    step(bc2, T0 + 240)
    eng2.cfg.boiler_period = True
    step(bc2, T0 + 270)
    assert not bc2.period.state.get("paused") and bc2.period.state["base"] == 35


def test_dry_run_only_logs(tmp_path):
    eng, ha, bc = setup(tmp_path, mode="trocken")
    step(bc, T0)
    step(bc, T0 + 30)
    assert per_calls(ha) == [] and acts(eng) == ["boiler_period_dry"]  # nicht jeden Schritt dasselbe
    assert "würde Antipendelzeit 10 min schreiben" in eng.store.query(
        "SELECT reason FROM control_log WHERE action = 'boiler_period_dry'")[0]["reason"]
    assert bc.as_dict(T0)["period"]["status_text"].startswith("Antipendelzeit: würde 10 min (Abnahme reicht")
    assert bc.control.period_entity is None  # bei „Beobachten“ gar nicht beschreibbar


# ------------------------------------------------------------------ Zurück, Sperren, Control
def test_back_writes_base_once_when_switched_off(tmp_path):
    eng, ha, bc = setup(tmp_path, value=40)
    step(bc, T0)
    assert per_calls(ha) == [10] and bc.period.state["base"] == 40
    shows(eng, 10)
    new = SimpleNamespace(cfg=Config(boiler_control="heatpilot", boiler_period=False))
    run(main_mod.App._boiler_back(new, eng))  # wie App.build: über die alte Schreibstelle
    assert per_calls(ha) == [10, 40] and acts(eng)[-1] == "boiler_period_back" and bc.period.state["back"]["ok"]
    assert calls(ha)[-1][2] == PER  # „Kessel über HeatPilot“ bleibt: kein Rückfall-Vorlauf
    # „Kessel über HeatPilot“ aus: nur der Grundwert (seit 0.59 kein Rückfall-Vorlauf)
    eng2, ha2, bc2 = setup(tmp_path / "b")
    step(bc2, T0)
    shows(eng2, 10)
    run(main_mod.App._boiler_back(SimpleNamespace(cfg=Config(boiler_control="aus", boiler_period=True)), eng2))
    assert per_calls(ha2) == [10, 25] and calls(ha2)[-1][2] == PER
    # nichts geschrieben (Beobachten): nichts zurück
    _, ha3, bc3 = setup(tmp_path / "c", mode="trocken")
    step(bc3, T0)
    assert run(bc3.period.back(Config(boiler_control="aus"))) is None and per_calls(ha3) == []


def test_blocked_like_boiler(tmp_path):
    eng, ha, bc = setup(tmp_path, regulator=True)
    step(bc, T0)
    assert per_calls(ha) == [] and acts(eng) == []
    assert bc.as_dict(T0)["period"]["status_text"].startswith("Antipendelzeit: gesperrt – EMS-ESP meldet einen Regler")
    with pytest.raises(PermissionError, match="Regler"):
        run(bc.control.set_period(10, "Test"))
    eng2, ha2, bc2 = setup(tmp_path / "b")
    eng2.ems["boiler"]["heatingoff"] = 1  # „Heizen abschalten“ an: EMS-ESP hält die Heizung aus
    step(bc2, T0)
    assert per_calls(ha2) == [] and bc2.period.view["blocked"].startswith("„Heizen abschalten“ ist an")
    bc2.state["paused"] = {"ts": T0, "reason": "Test"}  # Kessel pausiert: auch die Antipendelzeit nicht
    del eng2.ems["boiler"]["heatingoff"]
    step(bc2, T0 + 30)
    assert per_calls(ha2) == [] and bc2.period.view["blocked"] == "Kessel über HeatPilot pausiert"


def test_control_only_the_period_entity_within_limits(tmp_path):
    eng, ha, bc = setup(tmp_path)
    c = bc.control
    assert c.period_entity == PER
    run(c.set_period(5, "Test"))
    run(c.set_period(60, "Test"))
    for bad in (4, 61, 10.5, True, "10", None):
        with pytest.raises(PermissionError):
            run(c.set_period(bad, "Test"))
    with pytest.raises(PermissionError):
        run(c._call("number", "set_value", PER, {"value": 10, "x": 1}))
    with pytest.raises(PermissionError):
        run(c._call("number", "set_value", "number.boiler_burnmaxpower", {"value": 10}))
    eng.states[PER]["attributes"]["max"] = 30  # Grenzen der Entität
    with pytest.raises(PermissionError):
        run(c.set_period(40, "Test"))
    eng.states[PER]["attributes"]["unit_of_measurement"] = "°C"  # keine Zeit
    with pytest.raises(PermissionError, match="Minuten"):
        run(c.set_period(10, "Test"))
    assert per_calls(ha) == [5, 60]
    assert bp.entity_for({"flow": FLOW, "period": FLOW}, None) is None  # nie das Vorlauf-Soll …
    assert bp.entity_for({"period": PER}, PER) is None  # … oder das Warmwasser-Soll
    assert bp.entity_for({"period": "sensor.x"}, None) is None and bp.entity_for({"period": PER}, None) == PER
    no = Control(ha, eng.store, eng.found.rooms, states=eng.states, boiler={"flow": FLOW, "max": 75})
    with pytest.raises(PermissionError):  # ohne „Antipendelzeit anpassen“: nie
        run(no._call("number", "set_value", PER, {"value": 10}))
