"""Prüfung von 0.38 (Weg W): die nachgestellten Funde als Tests für das behobene Verhalten.

R1 nur ein Warmwasser-Soll ist Schreibziel (kein Vorlauf-Soll, keine Heizkurve, keine Entität ohne Einheit) ·
R2 Schwelle auch für die ungerundete Empfehlung (Zählsperre seit 0.39 entfernt, Wunsch Ingo) · R3 EMS-ESP nur mit frischen
Werten, die beschriebene Entität zuerst, Widerspruch = unklar · R4 erledigter Fehlschlag schließt Hinweis, Meldung und
Problem-Sensor · R5 „wieder einschalten“ nur bei einer Pause · R6 Neustart (Gegenprobe) · R7 Benachrichtigungsziele in
Kleinbuchstaben · R8 „nicht bereit“ heißt: nichts schreiben · R9 Minimum unter 50 °C sichtbar · R10 Doku und Sperrtext ·
R11 Uhrzeiten in der Zeitzone des Browsers.
"""

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from heatpilot import dhw_control as dc_mod
from heatpilot import main as main_mod
from heatpilot import preview as pv
from heatpilot import readiness
from heatpilot.config import Config, validate
from heatpilot.control import Control
from heatpilot.dhw_control import HOLD_S, K_FAIL, DhwControl, target_entity
from heatpilot.dhw_entity import picker
from heatpilot.discovery import Discovered
from heatpilot.ems_lock import LOCK_TEXT
from heatpilot.engine import Engine
from heatpilot.notify import Notifier
from heatpilot.store import Store
from heatpilot.web import make_app

from test_control34 import MON, at, run, ts_of
from test_dhw_control38 import ENT, TARGETS, RecHA, boiler, log_actions, make, step
from test_notify38 import fake_app
from test_ui_v361 import NODE, script, source
from test_ui_v38 import NAMES, STUBS, as_json

ROOT = Path(__file__).resolve().parents[1]
WRONG = (("number.boiler_selflowtemp", {"unit_of_measurement": "°C", "min": 0, "max": 90,
                                        "friendly_name": "Boiler Vorlauf-Soll"}),
         ("number.thermostat_hc1_designtemp", {"unit_of_measurement": "°C", "min": 30, "max": 90,
                                               "friendly_name": "HK1 Auslegungstemperatur"}),
         ("number.boiler_heatingtemp", {"min": 0, "max": 90}))


def engine_with(tmp_path, entity, attrs, state="45", **cfg_kw):
    """Wie main.App._build_control – mit einer frei gewählten number-Entität (z. B. aus einer älteren Einstellung)."""
    cfg = Config(dhw_control="heatpilot", dhw_set_entity=entity, notify_targets=list(TARGETS), **cfg_kw)
    found = Discovered(boiler={"dhw_seltemp": ENT})
    tmp_path.mkdir(parents=True, exist_ok=True)
    eng = Engine(cfg, Store(tmp_path / "w.db"), found)
    eng.states[entity] = {"state": state, "attributes": attrs}
    ha = RecHA()
    ent = target_entity(cfg, found, eng.states)
    ctl = Control(ha, eng.store, found.rooms, states=eng.states, dhw_entity=ent[0] if cfg.writes_dhw() else None,
                  dhw_limits=(cfg.dhw_min_temp, cfg.dhw_max_temp), notify_targets=cfg.notify_targets)
    eng.notifier = Notifier(eng.store, ctl, cfg.notify_targets)
    eng.dhw_control = DhwControl(eng, ctl, eng.notifier, ent)
    return eng, ha, eng.dhw_control


def run_js(tmp_path, body: str, extra=()):
    """Wie test_ui_v38.run_js (Node in Europe/Berlin), mit weiteren Namen aus index.html."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    code = (STUBS + "\n".join(source(js, n) for n in (*NAMES, *extra))
            + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body)
    f = tmp_path / "probe.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def sensor(eng, ha):
    """Meldungen schicken und den Problem-Sensor setzen wie main.send_notes – (an/aus, Gründe)."""
    run(main_mod.send_notes(SimpleNamespace(eng=eng, ha=ha), eng))
    return ha.set[-1][1], ha.set[-1][2]["reasons"]


# ------------------------------------------------------------------ R1: nur das Warmwasser-Soll
def test_r1_other_boiler_values_are_never_the_dhw_target(tmp_path):
    t = ts_of(MON, 15)
    for entity, attrs in WRONG:
        _, errors = validate({"dhw_set_entity": entity, "dhw_control": "heatpilot"})
        assert len(errors) == 1 and "nicht das Warmwasser-Soll" in errors[0]  # schon beim Speichern
        eng, ha, dc = engine_with(tmp_path / entity, entity, attrs)          # gespeichert vor 0.38-Korrektur
        eng.ems_controls, eng.ems_controls_ts = {"wwseltemp": {"found": True, "writeable": True}}, t
        rd = readiness.dhw(eng, t)
        assert rd["level"] == "nicht bereit" and not rd["entity_ok"] and "nicht das Warmwasser-Soll" in rd["entity_problem"]
        assert step(eng, dc, t, 55) is False and ha.writes() == []
        assert dc.view["decision"] == rd["entity_problem"] and int(dc.state.get("fails", 0)) == 0
        with pytest.raises(PermissionError, match="nicht das Warmwasser-Soll"):  # der Schreibweg selbst auch
            run(dc.control.set_dhw(55, "Test"))
        assert ha.payloads == []


def test_r1_unit_and_recognised_role_are_required(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    eng.states[ENT]["attributes"].pop("unit_of_measurement")  # ohne Einheit: keine Temperatur sicher
    assert "meldet keine Einheit" in readiness.dhw(eng, t)["entity_problem"]
    assert step(eng, dc, t, 55) is False and ha.writes() == []
    with pytest.raises(PermissionError, match="keine Einheit"):
        run(dc.control.set_dhw(55, "Test"))
    eng.states[ENT]["attributes"]["unit_of_measurement"] = "°C"
    eng.found.boiler["sel_flow_temp"] = ENT  # dieselbe Entität ist auch eine andere Rolle (z. B. per Zuordnung)
    assert "laut Erkennung „Vorlauftemperatur (Soll am Kessel)“" in readiness.dhw(eng, t)["entity_problem"]
    assert step(eng, dc, t + HOLD_S, 55) is False and ha.writes() == []
    eng.found.boiler.pop("sel_flow_temp")
    assert step(eng, dc, t + 2 * HOLD_S, 55) is True  # wieder in Ordnung: schreibt


def test_r1_unusual_name_is_allowed_but_flagged(tmp_path):
    t = ts_of(MON, 15)
    eng, ha, dc = engine_with(tmp_path / "a", "number.kessel_soll",
                              {"unit_of_measurement": "°C", "min": 30, "max": 70, "friendly_name": "Kessel Soll"},
                              dhw_min_temp=50)
    rd = readiness.dhw(eng, t)
    assert rd["level"] == "eingeschränkt" and rd["entity_ok"]
    assert rd["limits"][0] == rd["entity_doubt"] == ("Name von number.kessel_soll deutet nicht auf ein Warmwasser-Soll – "
                                                     "prüfen, ob es wirklich das Speicher-Soll ist")
    assert step(eng, dc, t, 55) is True  # andere Kessel heißen anders: erlaubt, aber sichtbar
    eng2, _, _ = engine_with(tmp_path / "b", "number.kessel_soll",
                             {"unit_of_measurement": "°C", "friendly_name": "Warmwasser Soll"}, dhw_min_temp=50)
    assert readiness.dhw(eng2, t)["entity_doubt"] is None  # Anzeigename mit Warmwasser genügt


def test_r1_picker_offers_only_fitting_entities(tmp_path):
    c = {"unit_of_measurement": "°C"}
    assert picker(ENT, {"attributes": c}) == {"fits": True, "hint": True}
    assert picker("number.boiler_selflowtemp", {"attributes": c}) == {"fits": False, "hint": False}
    assert picker("number.thermostat_hc1_designtemp", {"attributes": c})["fits"] is False
    assert picker("number.boiler_burnminperiod", {"attributes": {"unit_of_measurement": "min"}})["fits"] is False
    assert picker("number.boiler_dhw_seltemp", {"attributes": {}})["fits"] is False  # ohne Einheit
    assert picker("number.kessel_soll", {"attributes": {**c, "friendly_name": "Kessel"}}) == {"fits": True, "hint": False}
    eng, ha, dc = make(tmp_path)
    eng.states["number.boiler_selflowtemp"] = {"state": "35", "attributes": c}
    eng.states["number.kessel_soll"] = {"state": "50", "attributes": c}
    app = fake_app(eng, ha)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            return await (await client.get("/api/entities")).json()
        finally:
            await client.close()

    rows = {e["id"]: e.get("dhw") for e in asyncio.run(go()) if e["id"].startswith("number.")}
    assert rows == {ENT: {"fits": True, "hint": True}, "number.boiler_selflowtemp": {"fits": False, "hint": False},
                    "number.kessel_soll": {"fits": True, "hint": False}}
    r = run_js(tmp_path, f"""const ENT = {json.dumps([{"id": k, "name": k, "dhw": v} for k, v in sorted(rows.items())])};
      DHWC = {{ suggested: "number.kessel_soll" }};
      const P = {{ dhw: {{ filter: (e) => !!(e.dhw && e.dhw.fits), sort: (a, b) => dhwRank(b) - dhwRank(a) }} }};
      out(ENT.filter(P.dhw.filter).sort(P.dhw.sort).map((e) => e.id));""", extra=("dhwRank",))
    assert r == ["number.kessel_soll", ENT]  # Vorschlag zuerst, dann Namen mit Warmwasser; Vorlauf-Soll fehlt


# ------------------------------------------------------------------ R2: Schwelle und Tageslimit
def test_r2_flapping_recommendation_writes_after_each_hold_without_count_lock(tmp_path):
    """Keine Zählsperre (Ingo, 0.39): Springt die Empfehlung, wird nach jeder Haltezeit von 5 min geschrieben –
    nie öfter, aber auch nie gesperrt, nur weil es viele Wechsel waren."""
    eng, ha, dc = make(tmp_path, sample_interval_s=60)
    t0 = ts_of(MON, 8)
    for i in range(3 * 60):                  # 3 h mit 60-s-Takt, die Empfehlung springt jede Minute
        ts = t0 + 60 * i
        step(eng, dc, ts, 55 if i % 2 == 0 else 45)
        if dc.state.get("check"):            # Kessel übernimmt sofort
            boiler(eng, dc.state["check"]["value"])
            dc.verify(dc.state["check"]["due"])
    stamps = [w["ts"] for w in ha.writes()] if ha.writes() and isinstance(ha.writes()[0], dict) and "ts" in ha.writes()[0] \
        else None
    assert len(ha.writes()) > 24 and not dc.paused()
    assert len(ha.writes()) <= 3 * 60 // (HOLD_S // 60) + 1   # höchstens einmal je Haltezeit
    assert dc.as_dict(t0 + 3 * 3600)["writes_24h"] == len(ha.writes())
    assert stamps is None or all(b - a >= HOLD_S for a, b in zip(stamps, stamps[1:]))


def test_r2_jitter_at_rounding_boundary_does_not_alternate(tmp_path):
    eng, ha, dc = make(tmp_path, entity_state="52")
    t0 = ts_of(MON, 8)
    for i in range(12):                      # 52,6 ↔ 52,4: gerundet 53 ↔ 52, ungerundet nur 0,6 K vom Kessel-Soll
        step(eng, dc, t0 + HOLD_S * i, 52.6 if i % 2 == 0 else 52.4)
    assert ha.writes() == [] and dc.view["decision"] == "Kessel-Soll 52 °C passt"
    assert step(eng, dc, t0 + HOLD_S * 12, 53.0) is True  # volle 1 K: geschrieben
    assert dc_mod.decide(53, 52.0, 0, exact=52.6).write is False and dc_mod.decide(53, 52.0, 0, exact=53.0).write
    # Vorschau: dieselbe Regel
    assert pv.count_writes([(at(MON, 8, 5), 53, 52.6), (at(MON, 8, 15), 52, 52.4)], 52, 1.0) == []
    assert pv.count_writes([(at(MON, 8, 5), 53, 53.1)], 52, 1.0) == [(at(MON, 8, 5), 53, 52)]
    assert pv.count_writes([(at(MON, 8, 5), 53)], 52, 1.0) == [(at(MON, 8, 5), 53, 52)]  # ohne Zusatzwert wie bisher


# ------------------------------------------------------------------ R3: EMS-ESP nur frisch
ACT_FAIL = dc_mod.ACT_FAIL


def test_r3_stale_emsesp_value_is_ignored(tmp_path):
    eng, ha, dc = make(tmp_path)
    eng.set_ems({"boiler": {"dhw_seltemp": 45}})
    eng.ems_ts -= 600                          # EMS-ESP antwortet seit 10 min nicht mehr
    t = ts_of(MON, 15)
    assert step(eng, dc, t, 55) is True
    boiler(eng, 55)                            # die Entität (MQTT) zeigt den neuen Wert
    assert dc.verify(t + 60) is True and dc.state["last_check"]["ok"] and dc.state["fails"] == 0
    for i in range(1, 4):
        step(eng, dc, t + i * HOLD_S, 55)
    assert len(ha.writes()) == 1 and not dc.paused() and ACT_FAIL not in log_actions(eng)


def test_r3_stale_emsesp_does_not_hide_foreign_writer(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    boiler(eng, 55)
    eng.set_ems({"boiler": {"dhw_seltemp": 55}})
    dc.verify(t + 60)
    eng.ems_ts -= 600                          # danach keine Antwort mehr
    boiler(eng, 45)                            # fremde Automation über HA
    step(eng, dc, t + HOLD_S, 55)
    st = dc.as_dict(t + HOLD_S)
    assert st["foreign_last"]["to"] == 45.0 and dc_mod.ACT_FOREIGN in log_actions(eng) and st["current"] == 45.0


def test_r3_fresh_emsesp_contradicting_entity_is_unclear(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    eng.set_ems({"boiler": {"dhw_seltemp": 50}})  # Entität zeigt 45, EMS-ESP 50
    assert step(eng, dc, t, 55) is False and ha.writes() == []
    assert dc.view["decision"] == ("Kessel-Soll unklar: Home Assistant zeigt 45 °C, EMS-ESP 50 °C – nichts geschrieben")
    rd = readiness.dhw(eng, t)
    assert any(x.startswith("Kessel-Soll unklar: Home Assistant zeigt 45 °C, EMS-ESP 50 °C") for x in rd["limits"])
    assert "Kessel-Soll unklar" in dc.as_dict(t)["status_text"] and int(dc.state.get("fails", 0)) == 0
    eng.set_ems({"boiler": {"dhw_seltemp": 45}})  # wieder gleich: schreibt
    assert step(eng, dc, t + 60, 55) is True


def test_r3_unclear_at_check_waits_then_counts(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    boiler(eng, 55)
    eng.set_ems({"boiler": {"dhw_seltemp": 45}})  # EMS-ESP noch beim alten Wert
    assert dc.verify(t + 60) is False and dc.state.get("check")  # unklar: weiter prüfen
    eng.set_ems({"boiler": {"dhw_seltemp": 55}})
    assert dc.verify(t + 90) is True and dc.state["last_check"]["ok"]
    # bleibt es unklar, zählt es nach 5 min als Fehlschlag – mit beiden Werten im Text
    step(eng, dc, t + HOLD_S, 50)
    boiler(eng, 50)
    assert dc.verify(t + HOLD_S + 60) is False
    eng.set_ems({"boiler": {"dhw_seltemp": 55}})
    assert dc.verify(t + HOLD_S + dc_mod.CHECK_MAX_S) is True and dc.state["fails"] == 1
    assert dc.state["last_check"]["shown"] == "Kessel-Soll unklar: Home Assistant zeigt 50 °C, EMS-ESP 55 °C"


# ------------------------------------------------------------------ R4: erledigte Fehlschläge
def test_r4_late_acceptance_during_pause_closes_problem(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    for i in range(3):
        step(eng, dc, t + i * HOLD_S, 50)
        dc.verify(t + i * HOLD_S + 60)
    assert dc.paused()
    boiler(eng, 50)                               # übernimmt verspätet, während pausiert
    step(eng, dc, t + 4 * HOLD_S, 50)
    assert dc.state["fails"] == 0 and K_FAIL not in eng.notifier.open_keys()
    assert dc.resume(t + 5 * HOLD_S) is True
    for i in range(6, 30):
        step(eng, dc, t + i * HOLD_S, 50)
    assert sensor(eng, ha) == ("off", []) and dc.warnings(t + 30 * HOLD_S) == [] and len(ha.writes()) == 3
    assert any(p["service_data"]["message"] == "Warmwasser-Soll wieder in Ordnung – 50 °C übernommen"
               for p in ha.notes())


def test_r4_unknown_at_check_waits_and_late_value_is_success(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    boiler(eng, "unavailable")                    # EMS-ESP startet neu, genau während der Prüfung
    assert dc.verify(t + 60) is False and dc.state["fails"] == 0 and dc.state.get("check")
    boiler(eng, 55)                               # wieder da – mit dem geschriebenen Wert
    for i in range(1, 25):
        step(eng, dc, t + i * HOLD_S, 55)
    assert dc.state["fails"] == 0 and dc.state["last_check"]["ok"] and len(ha.writes()) == 1
    assert sensor(eng, ha) == ("off", []) and dc.warnings(t + 25 * HOLD_S) == []


def test_r4_unknown_for_five_minutes_is_a_failure(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    boiler(eng, "unavailable")
    assert dc.verify(t + 60) is False and dc.verify(t + 299) is False
    assert dc.verify(t + dc_mod.CHECK_MAX_S) is True and dc.state["fails"] == 1
    assert dc.state["last_check"]["shown"] == "Kessel-Soll unbekannt"


def test_r4_wish_reached_without_write_closes_problem_but_keeps_count(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    dc.verify(t + 60)                             # Kessel bleibt bei 45: Fehlschlag 1
    assert sensor(eng, ha)[0] == "on"
    step(eng, dc, t + HOLD_S, 45)                 # Empfehlung jetzt 45 = Kessel-Soll: kein neuer Versuch nötig
    assert K_FAIL not in eng.notifier.open_keys() and sensor(eng, ha) == ("off", [])
    assert dc.state["fails"] == 1                 # gezählt wird weiter: scheitert der nächste, gilt Fehlschlag 2
    w = dc.warnings(t + HOLD_S)
    assert w == ["Warmwasser-Soll 55 °C nicht übernommen (Kessel zeigt 45 °C) – Fehlschlag 1 von 3, Kessel-Soll passt "
                 "inzwischen zur Empfehlung, kein neuer Versuch nötig"]
    assert any("kein neuer Versuch nötig – Kessel zeigt 45 °C" in p["service_data"]["message"] for p in ha.notes())
    step(eng, dc, t + 2 * HOLD_S, 55)
    dc.verify(t + 2 * HOLD_S + 60)
    assert dc.state["fails"] == 2


# ------------------------------------------------------------------ R5: „wieder einschalten“ nur bei Pause
def test_r5_resume_without_pause_changes_nothing(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    for i in range(3):
        ts = t + i * HOLD_S
        step(eng, dc, ts, 50)
        dc.verify(ts + 60)                        # Kessel bleibt bei 45
        if dc.state["fails"] == 2:
            assert dc.resume(ts + 61) is False and dc.state["fails"] == 2 and "resumed" not in dc.state
    assert dc.paused() and len(ha.writes()) == 3
    for i in range(3, 9):
        step(eng, dc, t + i * HOLD_S, 50)
    assert len(ha.writes()) == 3                  # pausiert: nichts mehr


def test_r5_api_resume_answers_409_without_pause(tmp_path):
    eng, ha, dc = make(tmp_path)
    dc.state["fails"] = 2
    app = fake_app(eng, ha)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            r = await client.post("/api/dhw/resume", json={})
            return r.status, await r.json()
        finally:
            await client.close()

    status, j = asyncio.run(go())
    assert status == 409 and j["ok"] is False and j["was_paused"] is False
    assert j["error"] == "Warmwasser über HeatPilot ist nicht pausiert – nichts geändert" and dc.state["fails"] == 2


# ------------------------------------------------------------------ R6: Neustart (Gegenprobe)
def test_r6_restart_keeps_hold_and_pending_check(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    eng2, ha2, dc2 = make(tmp_path)               # gleiche Datenbank: Zustand aus kv
    assert dc2.state["last"]["value"] == 55 and dc2.state["check"]["value"] == 55
    assert step(eng2, dc2, t + 30, 50) is False
    boiler(eng2, 55)
    assert dc2.verify(t + 61) is True and dc2.state["last_check"]["ok"]
    assert step(eng2, dc2, t + 240, 50) is False and dc2.view["decision"].startswith("Haltezeit")
    assert step(eng2, dc2, t + HOLD_S, 50) is True


# ------------------------------------------------------------------ R7: Ziele in Kleinbuchstaben
def test_r7_notify_target_must_match_control(tmp_path):
    _, errors = validate({"notify_targets": ["notify.Handy"]})
    assert errors == ["notify_targets: nur notify-Entitäten in Kleinbuchstaben (wie in Home Assistant): notify.Handy"]
    assert validate({"notify_targets": ["notify.handy", "notify.mobile_app_pixel_7"]})[1] == []
    assert Config.from_raw({"notify_targets": ["notify.Handy", "notify.handy"]}).notify_targets == ["notify.handy"]
    assert Config.from_raw({"dhw_set_entity": "number.Boiler_WW"}).dhw_set_entity is None


# ------------------------------------------------------------------ R8: nicht bereit = nicht schreiben
def test_r8_unavailable_entity_or_not_writeable_writes_nothing(tmp_path):
    eng, ha, dc = make(tmp_path, emsesp_host="ems-esp.local")
    boiler(eng, "unavailable")
    eng.set_ems({"boiler": {"dhw_seltemp": 45}})
    t = ts_of(MON, 15)
    rd = readiness.dhw(eng, t)
    assert rd["level"] == "nicht bereit" and "nicht verfügbar" in rd["entity_problem"]
    for i in range(3):
        step(eng, dc, t + i * HOLD_S, 55)
        dc.verify(t + i * HOLD_S + 60)
    assert ha.writes() == [] and not dc.paused() and int(dc.state.get("fails", 0)) == 0
    assert dc.view["decision"] == rd["entity_problem"] and eng.notifier.open_keys() == {}
    boiler(eng, 45)
    eng.ems_controls, eng.ems_controls_ts = {"wwseltemp": {"found": True, "writeable": False}}, t
    assert step(eng, dc, t + 4 * HOLD_S, 55) is False and ha.writes() == []
    assert dc.view["decision"] == "EMS-ESP lässt das Warmwasser-Soll nicht schreiben (wwseltemp nicht schreibbar)"
    assert readiness.dhw(eng, t)["blockers"] == [dc.view["decision"]]
    eng.ems_controls = {"wwseltemp": {"found": True, "writeable": True}}
    assert step(eng, dc, t + 5 * HOLD_S, 55) is True


def test_r8_apply_checks_again_before_writing(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    eng.dhw_rec = {"target": 55, "reason": "x"}
    dc.plan(t, {}, now=t)
    assert dc.pending == (55, "x")
    boiler(eng, "unavailable")                    # zwischen Takt und Schreiben weg
    assert run(dc.apply(now=t)) is False and ha.writes() == [] and int(dc.state.get("fails", 0)) == 0


# ------------------------------------------------------------------ R9: Minimum unter 50 °C
def test_r9_low_minimum_is_visible(tmp_path):
    t = ts_of(MON, 9)
    eng, ha, dc = make(tmp_path / "a", dhw_min_temp=30, dhw_comfort_temp=35, dhw_max_temp=40, entity_state="40")
    rd = readiness.dhw(eng, t)
    assert rd["level"] == "eingeschränkt" and rd["limits"] == [
        "Minimum 30 °C liegt unter 50 °C – HeatPilot schreibt so tiefe Werte wirklich an den Kessel, und bei einem "
        "Ausfall bleiben sie stehen (Legionellen) – nur mit regelmäßiger Desinfektion (Termin unter Einstellungen → "
        "Warmwasser)"]
    eng2, _, _ = make(tmp_path / "b", dhw_min_temp=40, dhw_disinfect_day="So")
    rd2 = readiness.dhw(eng2, t)
    assert rd2["level"] == "bereit" and any("die wöchentliche Desinfektion ist eingetragen" in n for n in rd2["notes"])
    eng3, _, _ = make(tmp_path / "c", dhw_min_temp=50)
    assert not any("Legionellen" in x for x in readiness.dhw(eng3, t)["limits"] + readiness.dhw(eng3, t)["notes"])


# ------------------------------------------------------------------ R10: Doku und Sperrtext
def test_r10_docs_and_lock_text_name_the_dhw_write_path():
    assert LOCK_TEXT == "Heizkreis von EMS-ESP – HeatPilot schreibt nicht an den Heizkreis"
    docs = re.sub(r"\s+", " ", (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8"))
    part = docs.split("## Was das Add-on macht")[1].split("\n## ")[0]
    assert "schreibt es von selbst nur seine eigenen `sensor.heatpilot_*`" not in part
    for text in ("`binary_sensor.heatpilot_problem`", "`number.set_value`", "„HeatPilot setzt“",
                 "nur an die gewählten Ziele"):
        assert text in part, text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Nur Anzeige: HeatPilot schreibt nichts an den Kessel" not in readme


# ------------------------------------------------------------------ R11: Uhrzeiten im Browser
@pytest.fixture
def utc(monkeypatch):
    """Add-on in UTC, Browser (Node) in Europe/Berlin – wie im Bildschirmfoto der Prüfung."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_r11_times_are_formatted_in_the_browser_zone(tmp_path, utc):
    """Add-on in UTC, Browser in Berlin (UTC+2): Pause, Prüfung, letztes Schreiben, fremder Schreiber und Statuszeile
    stehen überall mit Berliner Uhrzeit – keine Uhrzeit des Add-ons in den Texten."""
    eng, ha, dc = make(tmp_path / "p")
    tf = ts_of(MON, 12, 30)                       # 12:30 UTC = 14:30 in Berlin
    step(eng, dc, tf - 600, 45)                   # Kessel-Soll 45 passt: Bezug gemerkt
    boiler(eng, 55)                               # fremd; danach übernimmt der Kessel nichts mehr
    for i in range(3):                            # 3 Fehlschläge → Pause um 12:41 UTC
        step(eng, dc, tf + i * HOLD_S, 45)
        dc.verify(tf + i * HOLD_S + 60)
    assert dc.paused() and dc.state["foreign_last"]["ts"] == tf
    d, rd = as_json(dc.as_dict(tf + 700)), as_json(readiness.dhw(eng, tf + 700))
    clock = re.compile(r"\b\d{1,2}:\d{2}\b")
    assert not clock.search(" ".join([*d["warnings"], rd["headline"], *rd["blockers"], *rd["limits"]]))
    assert d["warnings"][1].startswith("Anderer Schreiber aktiv (zuletzt 45 → 55 °C, vor 11 min)")
    eng2, _, dc2 = make(tmp_path / "s")
    step(eng2, dc2, tf, 50)
    boiler(eng2, 50)
    dc2.verify(tf + 60)
    step(eng2, dc2, tf + 120, 55)                 # Haltezeit bis 12:35 UTC
    s = as_json(dc2.as_dict(tf + 130))
    assert s["status"]["text"].startswith("HeatPilot setzt · 50 °C seit {since}") and s["status_text"].startswith(
        "HeatPilot setzt · 50 °C seit 12:30") and s["decision"] == "Haltezeit bis 12:35"
    r = run_js(tmp_path, f"const D = {json.dumps(d)}, R = {json.dumps(rd)}, S = {json.dumps(s)};"
                         "out({ line: dhwLine(D), box: dhwBoxHtml(D, R), ready: dhwReadyHtml(R, true),"
                         " set: dhwLine(S), sbox: dhwBoxHtml(S, null) });")
    shown = re.sub(r"<[^>]+>", " ", " ".join(r.values()))
    assert not re.search(r"\b12:[234]\d\b", shown), shown  # keine Uhrzeit des Add-ons
    for text in ("pausiert – 3 Fehlschläge in Folge · seit 14:41", "Pausiert seit 14:41", "zuletzt geschrieben 14:40",
                 "Prüfung 14:41", "seit Mo., 14:41", "zuletzt Mo., 14:30", "HeatPilot setzt · 50 °C seit 14:30",
                 "Entscheidung: Haltezeit bis 14:35"):
        assert text in shown, text
