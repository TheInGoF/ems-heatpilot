"""0.42: Desinfektion auslösen und Mindest-Brennerleistung vorschlagen.

Sicherheitsregeln (jede hat einen Test): HeatPilot schaltet nur ``switch.turn_on``/``turn_off`` auf die eine Entität
des Schalters „Desinfizieren“ – nur mit Termin, Warmwasser-Soll „HeatPilot setzt“, ohne Regler am Bus und ohne Pause von
Weg W; im Trockenlauf nur Protokoll; ein Termin wird höchstens einmal ausgelöst (auch nach einem Neustart), verpasste
werden binnen 12 h nachgeholt, sonst gemeldet; nach 60 s geprüft, Erfolg bei Desinfektionstemperatur − 3 K, sonst nach
Ladedauer + 60 min Fehlschlag mit Meldung; danach ausschalten (einmal, mit Prüfung). Ortszeit auch in der Woche der
Zeitumstellung. Weg W und Weg B lassen den Kessel währenddessen in Ruhe. Die Mindestleistung wird aus dem Bereich der
maximalen Brennerleistung nur vorgeschlagen, nie übernommen; ohne sie ist die Abnahme-Regel aus. EMS-ESP bleibt nur
lesend.
"""

import asyncio
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from heatpilot import boiler_power, dhw_disinfect, entity_map
from heatpilot import disinfect_entity as de
from heatpilot import disinfect_rule as rule
from heatpilot import main as main_mod
from heatpilot import readiness
from heatpilot.boiler_control import BoilerControl
from heatpilot.boiler_flow import min_power_kw
from heatpilot.boiler_status import ready as boiler_ready
from heatpilot.config import Config, validate
from heatpilot.control import ALLOWED, Control
from heatpilot.dhw_control import DhwControl, special_running, target_entity
from heatpilot.discovery import Discovered
from heatpilot.disinfect_control import K_FAIL, K_MISSED, K_SWITCH, DisinfectControl
from heatpilot.disinfect_status import (ACT_DONE, ACT_DRY, ACT_FAIL, ACT_MISSED, ACT_OFF, ACT_ON, ACT_RUN,
                                        HINT_HEATPILOT, HINT_REGULATOR, NOBODY)
from heatpilot.emsesp import BOILER_CONTROLS, POWER_CONTROLS, EmsEsp
from heatpilot.engine import Engine
from heatpilot.notify import Notifier
from heatpilot.store import Store
from heatpilot.web import make_app

from test_dhw_control38 import RecHA

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
SW, ENT, TANK = "switch.boiler_wwdisinfecting", "number.boiler_dhw_seltemp", "sensor.boiler_dhw_curtemp"
TARGETS = ["notify.handy"]
SUN = datetime(2026, 10, 18, 17, 30)  # Sonntag (vor der Zeitumstellung)


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def ts_of(dt: datetime) -> int:
    return int(dt.astimezone().timestamp())


def house(tmp_path, mode="heatpilot", day="So", at="17:30", regulator=False, ems=True, switch="off", tank=52.0,
          **raw):
    """Engine mit Weg W, Speicher-Soll, Schalter „Desinfizieren“, Speichertemperatur, Meldungen und dem Auslösen – wie
    ``main.App._build_control``. EMS-ESP antwortet seit Langem ohne Regler (``regulator``: mit Regler)."""
    cfg = Config.from_raw({"dhw_control": mode, "dhw_disinfect_day": day, "dhw_disinfect_time": at,
                           "emsesp_host": "ems.local" if ems else "", "notify_targets": TARGETS, **raw})
    found = Discovered(boiler={"dhw_seltemp": ENT, "dhw_disinfecting": SW, "dhw_temp": TANK})
    tmp_path.mkdir(parents=True, exist_ok=True)
    eng = Engine(cfg, Store(tmp_path / "d.db"), found)
    eng.states[ENT] = {"state": "50", "attributes": {"unit_of_measurement": "°C", "min": 30, "max": 70,
                                                     "friendly_name": "Boiler WWK gewählte Temperatur"}}
    eng.states[SW] = {"state": switch, "attributes": {"friendly_name": "Boiler WWK Desinfizieren"}}
    eng.states[TANK] = {"state": str(tank), "attributes": {"unit_of_measurement": "°C"}}
    eng.states["notify.handy"] = {"state": "unknown", "attributes": {"friendly_name": "Handy"}}
    if ems:
        eng.ems, eng.ems_ts = {"boiler": {"curflowtemp": 40.0}}, time.time()
        eng.regulator_watch.update(last="fehlt", ts=time.time(), absent_since=0, absent_n=99)
        if regulator:
            eng.ems["thermostat"] = {"hc1_seltemp": 21.0}
    return wire(eng)


def wire(eng, carry=None):
    """Schreibstelle, Meldungen, Weg W und Auslösen wie ``main.App._build_control``."""
    cfg, ha = eng.cfg, RecHA()
    dis = de.target(cfg, eng.found, eng.states)
    ctl = Control(ha, eng.store, eng.found.rooms, states=eng.states,
                  dhw_entity=target_entity(cfg, eng.found, eng.states)[0] if cfg.writes_dhw() else None,
                  dhw_limits=(cfg.dhw_min_temp, cfg.dhw_max_temp), notify_targets=cfg.notify_targets,
                  disinfect=dis[0] if rule.writes(cfg) else None, disinfect_lock=lambda: rule.lock(eng))
    eng.notifier = Notifier(eng.store, ctl, cfg.notify_targets, carry=getattr(carry, "notifier", None))
    eng.dhw_control = DhwControl(eng, ctl, eng.notifier, target_entity(cfg, eng.found, eng.states))
    eng.disinfect_control = DisinfectControl(eng, ctl, eng.notifier, dis)
    return eng, ha, eng.disinfect_control


def step(eng, dc, ts, ems_alive=True):
    """Ein 30-s-Schritt wie ``main.disinfect_step`` zur Zeit ``ts`` (EMS-ESP antwortet frisch)."""
    with mock.patch("time.time", return_value=float(ts)):
        if ems_alive and eng.cfg.emsesp_host:
            eng.ems_ts = float(ts)
        dc.plan(ts)
        done = asyncio.run(dc.apply(ts))
        asyncio.run(eng.notifier.flush())
    return done


def view(eng, dc, ts):
    """``as_dict`` zur Zeit ``ts`` (EMS-ESP antwortet frisch)."""
    with mock.patch("time.time", return_value=float(ts)):
        if eng.cfg.emsesp_host:
            eng.ems_ts = float(ts)
        return dc.as_dict(ts)


def switches(ha):
    return [(p["service"], p["target"]["entity_id"]) for p in ha.payloads if p["domain"] == "switch"]


def notes(ha):
    return [p["service_data"]["message"] for p in ha.payloads if p["domain"] == "notify"]


def actions(eng):
    return [r["action"] for r in eng.store.query("SELECT action FROM control_log ORDER BY rowid")]


def shows(eng, switch=None, tank=None):
    """Der Kessel zeigt (über Home Assistant): Schalter und Speichertemperatur."""
    if switch is not None:
        eng.states[SW]["state"] = switch
    if tank is not None:
        eng.states[TANK]["state"] = str(tank)


def armed(eng, dc, before_h=24):
    """HeatPilot läuft seit ``before_h`` Stunden vor dem Termin (Termine ab dann zählen)."""
    step(eng, dc, ts_of(SUN) - before_h * 3600)


# ------------------------------------------------------------------ Ablauf
def test_triggers_once_checks_after_60s_success_and_switches_off(tmp_path):
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    armed(eng, dc)
    assert not step(eng, dc, t - 60) and switches(ha) == []  # vor dem Termin nichts
    assert step(eng, dc, t) and switches(ha) == [("turn_on", SW)]  # zum Termin genau einmal
    run = dc.state["run"]
    assert run["phase"] == "check" and run["goal"] == 67.0 and dc.active()
    shows(eng, switch="on")
    step(eng, dc, t + 30)
    assert dc.state["run"]["phase"] == "check"  # geprüft wird erst nach 60 s
    step(eng, dc, t + 60)
    assert dc.state["run"]["phase"] == "running" and actions(eng)[-1] == ACT_RUN
    shows(eng, tank=63.0)
    step(eng, dc, t + 1800)
    assert dc.state["run"]["phase"] == "running" and dc.state["run"]["peak"] == 63.0
    shows(eng, tank=67.5)  # Desinfektionstemperatur 70 − 3 K erreicht: gelungen
    step(eng, dc, t + 2400)
    last = dc.state["last"]
    assert last["ok"] is True and last["result"] == "gelungen" and last["text"] == "67,5 °C erreicht (Ziel 67 °C)"
    assert dc.state["run"]["phase"] == "after" and switches(ha) == [("turn_on", SW)]  # Nachlauf 15 min
    step(eng, dc, t + 2400 + rule.AFTER_S - 30)
    assert switches(ha) == [("turn_on", SW)]
    step(eng, dc, t + 2400 + rule.AFTER_S)  # steht noch an: einmal ausschalten …
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)] and dc.state["run"]["phase"] == "off_check"
    shows(eng, switch="off")
    step(eng, dc, t + 2400 + rule.AFTER_S + 60)  # … und nach 60 s prüfen
    assert "run" not in dc.state and dc.state["last"]["switch"] == "Schalter aus" and not dc.active()
    for k in range(1, 30):  # danach nichts mehr bis zum nächsten Termin
        step(eng, dc, t + 4000 + k * 1800)
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    assert actions(eng) == [ACT_ON, ACT_RUN, ACT_DONE, ACT_OFF]
    assert notes(ha) == [] and eng.notifier.problem_state()["on"] is False
    log = {r["action"]: r for r in eng.store.control_log(10)}
    assert log[ACT_ON]["room"] == SW and log[ACT_ON]["reason"] == "Desinfektion So 17:30 (70 °C)"
    assert log[ACT_DONE]["value"] == 67.5


def test_switch_stays_on_after_switching_off_is_reported(tmp_path):
    """Ausschalten einmal, mit Prüfung: bleibt der Schalter an, kommt eine Meldung – kein zweiter Versuch."""
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    armed(eng, dc)
    step(eng, dc, t)
    shows(eng, switch="on", tank=68)
    step(eng, dc, t + 60)
    step(eng, dc, t + 60 + rule.AFTER_S)
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    step(eng, dc, t + 120 + rule.AFTER_S)  # 60 s danach: steht noch an
    assert "run" not in dc.state and K_SWITCH in eng.notifier.open_keys()
    assert notes(ha) == ["Desinfektion So 17:30: Schalter steht nach dem Ausschalten noch an – bitte in Home Assistant "
                         "prüfen"]
    for k in range(1, 10):
        step(eng, dc, t + 120 + rule.AFTER_S + k * 600)
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    shows(eng, switch="off")  # von Hand ausgeschaltet: „wieder in Ordnung“
    step(eng, dc, t + 8000)
    assert K_SWITCH not in eng.notifier.open_keys() and notes(ha)[-1] == "Desinfektion: Schalter wieder aus"


def test_boiler_resets_switch_itself_in_after_run(tmp_path):
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    armed(eng, dc)
    step(eng, dc, t)
    shows(eng, switch="on", tank=68)
    step(eng, dc, t + 60)
    assert dc.state["run"]["phase"] == "after"
    shows(eng, switch="off")  # der Kessel setzt ihn mit der vollen Temperatur selbst zurück
    step(eng, dc, t + 300)
    assert "run" not in dc.state and switches(ha) == [("turn_on", SW)]
    assert dc.state["last"]["switch"] == "Schalter aus (vom Kessel)"


def test_timeout_failure_message_switch_off_and_next_success_is_ok(tmp_path):
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    armed(eng, dc)
    step(eng, dc, t)
    shows(eng, switch="on", tank=55)
    step(eng, dc, t + 60)
    deadline = dc.state["run"]["deadline"]
    assert deadline == t + int(eng.disinfect_ready_h() * 3600) + 3600  # Ladedauer laut Modell + 60 min
    step(eng, dc, deadline - 30)
    assert dc.state["run"]["phase"] == "running" and notes(ha) == []
    step(eng, dc, deadline)
    assert dc.state["last"]["ok"] is False
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – Speicher erreichte nur 55 °C, nötig 67 °C"]
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]  # nach einem Fehlschlag gleich aus
    assert eng.notifier.problem_state()["on"] and K_FAIL in eng.notifier.open_keys()
    assert actions(eng)[-2:] == [ACT_FAIL, ACT_OFF]
    shows(eng, switch="off")
    step(eng, dc, deadline + 60)
    assert "run" not in dc.state
    # kein neuer Versuch vor dem nächsten Termin; der nächste gelingt: „wieder in Ordnung“
    t2 = t + 7 * 86400
    for k in range(1, 300):
        step(eng, dc, deadline + k * 1800) if deadline + k * 1800 < t2 else None
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    shows(eng, tank=52)
    step(eng, dc, t2)
    shows(eng, switch="on", tank=68)
    step(eng, dc, t2 + 60)
    assert dc.state["last"]["ok"] is True and K_FAIL not in eng.notifier.open_keys()
    assert notes(ha)[-1] == "Desinfektion wieder in Ordnung – So 17:30 gelaufen (68 °C erreicht (Ziel 67 °C))"


def test_switch_falls_back_or_unknown_is_failure(tmp_path):
    eng, ha, dc = house(tmp_path / "a")
    t = ts_of(SUN)
    armed(eng, dc)
    step(eng, dc, t)
    eng.ems["boiler"]["wwdisinfecting"] = "off"  # EMS-ESP meldet frisch: keine Desinfektion
    step(eng, dc, t + 60)  # Schalter steht (wieder) aus
    assert dc.state["last"]["text"] == "Schalter fällt sofort zurück – der Kessel nimmt die Desinfektion nicht an"
    assert "run" not in dc.state and switches(ha) == [("turn_on", SW)]  # nichts auszuschalten
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – Schalter fällt sofort zurück – der Kessel nimmt die "
                         "Desinfektion nicht an"]
    # Zustand unbekannt (Entität nicht verfügbar, EMS-ESP ohne Wert): bis 5 min weiter prüfen
    eng, ha, dc = house(tmp_path / "b")
    armed(eng, dc)
    step(eng, dc, t)
    shows(eng, switch="unavailable")
    step(eng, dc, t + 60)
    step(eng, dc, t + 240)
    assert dc.state["run"]["phase"] == "check"
    step(eng, dc, t + rule.CHECK_MAX_S + 1)
    assert dc.state["last"]["text"] == "Zustand des Schalters unbekannt – nicht bestätigt"


def test_ems_reports_disinfection_counts_as_running(tmp_path):
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    armed(eng, dc)
    step(eng, dc, t)
    shows(eng, switch="unavailable")
    eng.ems["boiler"]["wwdisinfecting"] = "on"  # EMS-ESP meldet die Desinfektion
    step(eng, dc, t + 60)
    assert dc.state["run"]["phase"] == "running"
    assert "EMS-ESP meldet die Desinfektion" in eng.store.control_log(1)[0]["reason"]


# ------------------------------------------------------------------ nur unter allen Bedingungen
@pytest.mark.parametrize("case", ["kein Termin", "aus", "Regler", "unbekannt", "Pause", "Einmalladung", "EMS-ESP"])
def test_switches_only_under_all_conditions(tmp_path, case):
    kw = {"kein Termin": {"day": ""}, "aus": {"mode": "aus"}, "Regler": {"regulator": True},
          "unbekannt": {"ems": False}, "Einmalladung": {"disinfect_entity": "switch.boiler_wwonetime"}}.get(case, {})
    eng, ha, dc = house(tmp_path, **kw)
    if case == "Pause":
        eng.dhw_control.state["paused"] = {"ts": 0, "reason": "3 Fehlschläge in Folge"}
    if case == "Einmalladung":
        eng.states["switch.boiler_wwonetime"] = {"state": "off", "attributes": {"friendly_name": "WWK Einmalladung"}}
    if case == "EMS-ESP":
        eng.ems_controls, eng.ems_controls_ts = {"wwdisinfecting": {"found": True, "writeable": False}}, time.time()
    t = ts_of(SUN)
    armed(eng, dc)
    for k in range(0, 30):
        step(eng, dc, t + k * 600, ems_alive=case != "unbekannt")
    assert switches(ha) == [] and "run" not in dc.state
    st = dc.state
    if case == "Regler":  # er macht die Desinfektion – der Termin ist damit erledigt
        assert st["last"]["result"] == "regler" and notes(ha) == []
        x = view(eng, dc, t)
        assert x["text"] == "Desinfektion: macht der Regler" and x["hint"] == HINT_REGULATOR
    elif case in ("kein Termin", "aus"):
        assert "last" not in st and view(eng, dc, t)["text"] == "Desinfektion: aus" and not rule.writes(eng.cfg)
        assert dc.control.disinfect_entity is None  # main gibt Control den Schalter gar nicht erst
    else:  # gesperrt: wartet bis 12 h nach dem Termin, dann „verpasst“ mit dem Grund
        why = {"unbekannt": "Ob ein Regler am Bus ist, lässt sich ohne EMS-ESP nicht prüfen",
               "Pause": "Warmwasser über HeatPilot ist pausiert – erst „wieder einschalten“",
               "Einmalladung": "switch.boiler_wwonetime ist ein anderer Kesselwert („Warmwasser-Einmalladung“)",
               "EMS-ESP": "EMS-ESP lässt die Desinfektion nicht schreiben (wwdisinfecting nicht schreibbar)"}[case]
        assert st["waiting"]["label"] == "So 17:30" and st["waiting"]["why"].startswith(why)
        step(eng, dc, t + rule.CATCHUP_S + 60, ems_alive=case != "unbekannt")
        assert st["last"]["result"] == "verpasst" and switches(ha) == []
        assert notes(ha) == [f"Desinfektion So 17:30 verpasst – {st['last']['text']}; nächster Termin So 25.10. 17:30"]


def test_dry_run_only_logs(tmp_path):
    eng, ha, dc = house(tmp_path, mode="trocken")
    t = ts_of(SUN)
    armed(eng, dc)
    for k in range(0, 40):
        step(eng, dc, t - 1200 + k * 600)
    assert switches(ha) == [] and actions(eng) == [ACT_DRY] and dc.control.disinfect_entity is None
    assert eng.store.control_log(1)[0]["reason"] == (
        f"Trockenlauf: würde Desinfektion So 17:30 starten (70 °C, Schalter {SW})")
    assert view(eng, dc, t)["text"] == "Desinfektion: Trockenlauf – würde So 17:30 auslösen"


# ------------------------------------------------------------------ einmal, nachholen, verpasst
def test_once_per_termin_even_after_restart(tmp_path):
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    armed(eng, dc)
    step(eng, dc, t)
    assert switches(ha) == [("turn_on", SW)]
    # Neustart mitten im Ablauf: Zustand aus der Datenbank, es geht mit der Prüfung weiter – kein zweites Einschalten
    eng2, ha2, dc2 = wire(Engine(eng.cfg, eng.store, eng.found), carry=eng)
    eng2.states.update(eng.states)
    eng2.ems, eng2.regulator_watch = eng.ems, eng.regulator_watch
    shows(eng2, switch="on")
    step(eng2, dc2, t + 90)
    assert dc2.state["run"]["phase"] == "running" and switches(ha2) == []
    # auch wenn das Einschalten scheiterte, wird der Termin nicht noch einmal ausgelöst
    eng3, ha3, dc3 = house(tmp_path / "b")
    armed(eng3, dc3)
    ha3.fail = [RuntimeError("Home Assistant antwortet nicht")]
    step(eng3, dc3, t)
    assert dc3.state["last"]["text"] == "nicht gestartet (Home Assistant antwortet nicht)"
    for k in range(1, 20):
        step(eng3, dc3, t + k * 600)
    assert switches(ha3) == [] and len(ha3.payloads) == 1  # nur die Meldung
    assert notes(ha3) == ["Desinfektion So 17:30 nicht gelaufen – nicht gestartet (Home Assistant antwortet nicht)"]


def test_missed_termin_caught_up_within_12h_else_message(tmp_path):
    t = ts_of(SUN)
    eng, ha, dc = house(tmp_path / "a")
    armed(eng, dc)
    step(eng, dc, t + 3 * 3600)  # HeatPilot lief zum Termin nicht, startet 3 h später: nachholen
    assert switches(ha) == [("turn_on", SW)] and dc.state["run"]["label"] == "So 17:30"
    eng, ha, dc = house(tmp_path / "b")
    armed(eng, dc)
    step(eng, dc, t + 13 * 3600)  # 13 h später: verpasst – Meldung, dann der nächste Termin
    assert switches(ha) == [] and dc.state["last"]["result"] == "verpasst"
    assert notes(ha) == ["Desinfektion So 17:30 verpasst – HeatPilot lief zum Termin nicht; nächster Termin "
                         "So 25.10. 17:30"]
    assert K_MISSED in eng.notifier.open_keys() and actions(eng) == [ACT_MISSED]
    step(eng, dc, t + 7 * 86400)
    assert switches(ha) == [("turn_on", SW)]


def test_new_termin_or_first_start_does_not_fire_past_termin(tmp_path):
    """Gerade eingetragen (bzw. erster Start mit 0.42): ein Termin von eben zählt nicht – keine Überraschung."""
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    step(eng, dc, t + 600)  # erster Schritt 10 min nach dem Termin
    for k in range(1, 40):
        step(eng, dc, t + 600 + k * 1800)
    assert switches(ha) == [] and notes(ha) == []
    eng.cfg.dhw_disinfect_time = datetime(2026, 1, 1, 18, 0).time()  # Termin geändert: ab jetzt
    step(eng, dc, t + 7 * 86400)
    assert switches(ha) == []
    step(eng, dc, t + 7 * 86400 + 1800)  # So 18:00
    assert switches(ha) == [("turn_on", SW)]


@pytest.mark.usefixtures("berlin")
@pytest.mark.parametrize("day,utc_hour", [((2026, 10, 25), 12), ((2026, 3, 29), 11)])
def test_dst_week_keeps_wall_clock_and_fires_once(tmp_path, day, utc_hour):
    """Woche der Zeitumstellung: „So 13:00“ ist 13:00 Ortszeit (Winter 12:00 UTC, Sommer 11:00 UTC), genau einmal."""
    eng, ha, dc = house(tmp_path, at="13:00")
    start = int(datetime(*day, 0, 0).astimezone().timestamp()) - 36 * 3600
    fired = []
    for k in range(0, 60 * 6):
        ts = start + k * 600
        if step(eng, dc, ts):
            fired.append(ts)
        shows(eng, switch="off")
    assert len(fired) == 1 and switches(ha) == [("turn_on", SW)]
    assert datetime.fromtimestamp(fired[0], timezone.utc).hour == utc_hour
    assert datetime.fromtimestamp(fired[0]).hour == 13


@pytest.mark.usefixtures("berlin")
def test_dst_doubled_hour_fires_once(tmp_path):
    """So 02:30 in der Nacht der Umstellung auf Winterzeit (die Stunde gibt es zweimal): nur einmal ausgelöst."""
    eng, ha, dc = house(tmp_path, at="02:30")
    start = int(datetime(2026, 10, 24, 20, 0).astimezone().timestamp())
    fired = [ts for ts in (start + k * 300 for k in range(0, 12 * 24)) if step(eng, dc, ts)]
    assert len(fired) == 1 and switches(ha) == [("turn_on", SW)]


# ------------------------------------------------------------------ Schreibweg, Weg W und B
def test_control_allows_only_the_one_switch(tmp_path):
    eng, ha, dc = house(tmp_path)
    ctl = dc.control
    assert ctl.disinfect_entity == SW and ALLOWED == {
        ("tado", "set_climate_timer"), ("climate", "set_temperature"), ("climate", "set_hvac_mode"),
        ("number", "set_value"), ("switch", "turn_on"), ("switch", "turn_off")}  # keine neuen Dienste

    def call(domain, service, entity, data=None):
        return asyncio.run(ctl._call(domain, service, entity, data or {}, "Test"))

    with mock.patch("time.time", return_value=float(time.time())):
        eng.ems_ts = time.time()
        asyncio.run(ctl.set_disinfect(True, "Test"))
        asyncio.run(ctl.set_disinfect(False, "Test"))
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    for args in (("switch", "turn_on", "switch.boiler_wwonetime"), ("switch", "turn_on", "switch.boiler_wwcirc"),
                 ("switch", "turn_on", SW, {"x": 1}), ("switch", "toggle", SW), ("number", "set_value", SW)):
        with pytest.raises(PermissionError):
            call(*args)
    eng.ems["thermostat"] = {"hc1_seltemp": 21.0}  # Regler am Bus: gesperrt
    with pytest.raises(PermissionError, match="Regler"):
        asyncio.run(ctl.set_disinfect(True, "Test"))
    del eng.ems["thermostat"]
    eng.dhw_control.state["paused"] = {"ts": 0, "reason": "x"}
    with pytest.raises(PermissionError, match="pausiert"):
        asyncio.run(ctl.set_disinfect(True, "Test"))
    eng.dhw_control.state.pop("paused")
    with pytest.raises(PermissionError):
        asyncio.run(ctl.set_disinfect("ja", "Test"))
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    # nie dieselbe Entität wie der Heizbetrieb; ohne Schalter gesperrt
    twin = Control(RecHA(), eng.store, [], states=eng.states, disinfect=SW, boiler={"heating": SW})
    assert twin.disinfect_entity is None
    with pytest.raises(PermissionError):
        asyncio.run(Control(RecHA(), eng.store, [], states=eng.states).set_disinfect(True, "x"))


def test_weg_w_and_b_leave_the_boiler_alone_during_run(tmp_path):
    eng, ha, dc = house(tmp_path, tank=52)
    eng.found.boiler.pop("dhw_disinfecting")  # eigener Schalter gewählt: Weg W sieht ihn nicht als Rolle
    eng.cfg.disinfect_entity = SW
    t = ts_of(SUN)
    armed(eng, dc)
    assert not special_running(eng, {})
    step(eng, dc, t)
    assert special_running(eng, {})  # schon vor der Meldung des Kessels
    eng.dhw_rec = {"target": 58, "reason": "Zapfung erwartet"}
    with mock.patch("time.time", return_value=float(t + 30)):
        eng.dhw_control.plan(t + 30, {}, now=t + 30)
    assert eng.dhw_control.pending is None and "Sonderladung" in eng.dhw_control.view["decision"]
    with mock.patch("time.time", return_value=float(t + 30)):
        assert BoilerControl(eng, None, None, {}).charging()  # Weg B: Vorlauf-Soll bleibt unberührt
    shows(eng, switch="on", tank=68)
    step(eng, dc, t + 60)
    shows(eng, switch="off")
    step(eng, dc, t + 120)
    assert not dc.active() and not special_running(eng, {})
    with mock.patch("time.time", return_value=float(t + 120)):
        assert not BoilerControl(eng, None, None, {}).charging()


def test_switch_mode_mid_run_aborts_without_writing(tmp_path):
    eng, ha, dc = house(tmp_path)
    t = ts_of(SUN)
    armed(eng, dc)
    step(eng, dc, t)
    shows(eng, switch="on")
    step(eng, dc, t + 60)
    eng.cfg.dhw_control = "aus"
    step(eng, dc, t + 90)
    assert "run" not in dc.state and dc.state["last"]["result"] == "abgebrochen"
    assert switches(ha) == [("turn_on", SW)] and notes(ha) == []


def test_termin_counts_in_model_when_heatpilot_triggers(tmp_path):
    eng, ha, dc = house(tmp_path)
    now = SUN.astimezone()
    assert not dhw_disinfect.plan_counts(eng.dhw, eng.cfg, now)  # nicht bestätigt, noch nicht gerechnet
    step(eng, dc, ts_of(SUN) - 3600)
    assert eng.cfg.disinfect_triggered and dhw_disinfect.plan_counts(eng.dhw, eng.cfg, now)
    assert eng.disinfect_info(now_ts=ts_of(SUN) - 3600)["counts"] is True
    eng.ems["thermostat"] = {"hc1_seltemp": 21.0}  # mit Regler zählt nur die Bestätigung
    step(eng, dc, ts_of(SUN) - 3000)
    assert not eng.cfg.disinfect_triggered and not dhw_disinfect.plan_counts(eng.dhw, eng.cfg, now)


def test_missing_switch_and_hints(tmp_path):
    eng, ha, dc = house(tmp_path)
    del eng.states[SW]
    eng.found.boiler.pop("dhw_disinfecting")
    eng, ha, dc = wire(eng)
    x = view(eng, dc, ts_of(SUN))
    assert x["entity"] is None and x["blocked"].startswith("keine Entität für die Desinfektion gefunden")
    info = eng.disinfect_info(now_ts=ts_of(SUN))
    assert info["triggers"] is False and info["trigger"]["state"] == "heatpilot"
    eng2, _, dc2 = house(tmp_path / "b")
    step(eng2, dc2, ts_of(SUN) - 600)
    with mock.patch("time.time", return_value=float(ts_of(SUN) - 600)):
        info = eng2.disinfect_info(now_ts=ts_of(SUN) - 600)
    assert info["triggers"] and info["hint"] == HINT_HEATPILOT and info["counts"]
    assert info["trigger"]["text"] == "Desinfektion: löst HeatPilot aus (So 17:30)"


# ------------------------------------------------------------------ Entität
def test_entity_suggest_check_picker_and_settings(tmp_path):
    states = {SW: {"state": "off", "attributes": {"friendly_name": "WWK Desinfizieren"}},
              "switch.ems_esp_boiler_dhw_disinfecting": {"state": "off", "attributes": {}},
              "switch.boiler_wwonetime": {"state": "off", "attributes": {"friendly_name": "WWK Einmalladung"}},
              "switch.boiler_wwcirc": {"state": "off", "attributes": {}},
              "switch.garten": {"state": "on", "attributes": {"friendly_name": "Gartenpumpe"}},
              "switch.legionellen": {"state": "off", "attributes": {"friendly_name": "Legionellenschaltung"}}}
    found = Discovered(boiler={"dhw_disinfecting": SW})
    assert de.suggest(found, states) == SW and de.suggest(Discovered(), states) == SW
    assert de.target(Config(), found, states) == (SW, "erkannt")
    assert de.target(Config(disinfect_entity="switch.legionellen"), found, states) == ("switch.legionellen",
                                                                                         "eingestellt")
    assert de.problem(SW, states[SW], found) is None
    assert de.problem("switch.legionellen", states["switch.legionellen"]) is None
    assert "Einmalladung" in de.problem("switch.boiler_wwonetime", states["switch.boiler_wwonetime"])
    assert "Zirkulation" in de.problem("switch.boiler_wwcirc", states["switch.boiler_wwcirc"])
    assert "deutet nicht auf die Desinfektion" in de.problem("switch.garten", states["switch.garten"])
    assert "keine switch-Entität" in de.problem("binary_sensor.boiler_wwdisinfecting", {"state": "off"})
    assert "nicht verfügbar" in de.problem(SW, {"state": "unavailable"})
    assert "weder an noch aus" in de.problem(SW, {"state": "läuft"})
    assert de.picker(SW, states[SW], found) == {"fits": True, "hint": True}
    assert not de.picker("switch.garten", states["switch.garten"])["fits"]
    assert not de.picker("switch.boiler_wwonetime", states["switch.boiler_wwonetime"])["fits"]
    assert validate({"disinfect_entity": SW}) == ({"disinfect_entity": SW}, [])
    assert validate({"disinfect_entity": "switch.boiler_wwonetime"})[1]
    assert validate({"disinfect_entity": "number.x"})[1]
    assert validate({"disinfect_entity": ""}) == ({"disinfect_entity": ""}, [])
    assert Config.from_raw({"disinfect_entity": "number.x"}).disinfect_entity is None
    assert Config.from_raw({"disinfect_triggered": True}).disinfect_triggered is False  # Laufzeit, nie aus der Datei
    assert Config().disinfect_entity is None and "disinfect_triggered" not in Config().to_raw()


def test_entity_page_row_and_api_entities(tmp_path):
    eng, ha, dc = house(tmp_path)
    m = entity_map.build(eng.cfg, eng.found, {}, eng.states)
    group = next(g for g in m["groups"] if g["key"] == "warmwasser")
    e = next(x for x in group["entries"] if x["key"] == "disinfect_entity")
    assert e["label"] == "Desinfektion auslösen (Schalter)" and e["required"] and e["current"] == SW
    assert e["source"] == "automatisch" and e["filter"]["picker"] == "disinfect"
    assert e["filter"]["domains"] == ["switch"]
    assert e["setting"] == {"kind": "field", "name": "disinfect_entity"}
    off = entity_map.build(Config(), eng.found, {}, eng.states)
    assert not next(x for g in off["groups"] for x in g["entries"] if x["key"] == "disinfect_entity")["required"]
    app = fake_app(eng)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            ents = await (await client.get("/api/entities")).json()
            dhw = await (await client.get("/api/dhw")).json()
            s = await (await client.get("/api/settings")).json()
        finally:
            await client.close()
        return ents, dhw, s

    ents, dhw, s = asyncio.run(go())
    assert next(x for x in ents if x["id"] == SW)["disinfect"] == {"fits": True, "hint": True}
    assert dhw["disinfect"]["text"] == "Desinfektion: löst HeatPilot aus (So 17:30)" and "log" in dhw["disinfect"]
    trig = s["disinfect"]["trigger"]
    assert trig["entity"] == SW and trig["state"] == "heatpilot" and s["disinfect"]["hint"] == HINT_HEATPILOT
    assert s["boiler_power"]["suggestion"] is None and switches(ha) == []  # Abfragen schalten nichts


def fake_app(eng):
    connected = asyncio.Event()
    connected.set()

    async def get_services():
        return {"notify": {}}

    async def none():
        return {}

    async def registry():
        return []

    app = SimpleNamespace(eng=eng, cfg=eng.cfg, control=eng.disinfect_control.control, status="läuft",
                          configured=True, ha=SimpleNamespace(connected=connected, get_services=get_services))
    app.refresh_rooms, app.registry, app.suggestions = none, registry, none
    return app


# ------------------------------------------------------------------ Bereitschaft
def test_readiness_dhw_block_says_who_disinfects(tmp_path):
    t = ts_of(SUN)
    eng, ha, dc = house(tmp_path / "a")
    armed(eng, dc)
    step(eng, dc, t)
    shows(eng, switch="on", tank=68)
    step(eng, dc, t + 60)
    rd = readiness.dhw(eng, t + 120)
    assert rd["disinfect"]["text"] == "Desinfektion: löst HeatPilot aus (So 17:30)"
    assert "Desinfektion: löst HeatPilot aus (So 17:30)" in rd["notes"]
    assert "Letzte Desinfektion (So 17:30) gelungen – 68 °C erreicht (Ziel 67 °C)" in rd["notes"]
    eng, ha, dc = house(tmp_path / "b", regulator=True)
    assert readiness.dhw(eng, t)["disinfect"]["text"] == "Desinfektion: macht der Regler"
    eng, ha, dc = house(tmp_path / "c", mode="aus")  # kein Regler, Termin – aber niemand löst aus
    rd = readiness.dhw(eng, t)
    assert rd["disinfect"]["text"] == "Desinfektion: aus" and NOBODY in rd["limits"]
    eng, ha, dc = house(tmp_path / "d", disinfect_entity="switch.garten")  # HeatPilot kann nicht: Einschränkung
    eng.states["switch.garten"] = {"state": "off", "attributes": {"friendly_name": "Gartenpumpe"}}
    rd = readiness.dhw(eng, t)
    assert any(x.startswith("Desinfektion: HeatPilot kann sie zurzeit nicht auslösen – Name von switch.garten")
               for x in rd["limits"]) and rd["level"] == "eingeschränkt"


def test_main_builds_and_steps_disinfection(tmp_path):
    eng, ha, dc = house(tmp_path)
    app = SimpleNamespace(cfg=eng.cfg, ha=RecHA(), store=eng.store, control=None)
    main_mod.App._build_control(app, eng, eng.found, None)
    assert app.control.disinfect_entity == SW and eng.disinfect_control.entity_id == SW
    assert eng.cfg.disinfect_triggered  # zählt im Warmwasser-Modell sofort, nicht erst nach dem ersten Schritt
    assert eng.disinfect_control.control is app.control
    eng.cfg.dhw_control = "trocken"
    main_mod.App._build_control(app, eng, eng.found, SimpleNamespace(**{k: getattr(eng, k) for k in (
        "notifier", "dhw_control", "boiler_control", "disinfect_control", "control_loop", "watchdog",
        "heartbeat_ts")}))
    assert app.control.disinfect_entity is None  # ohne „HeatPilot setzt“ kein Schreibweg
    step_app = SimpleNamespace(eng=eng, ha=app.ha)
    with mock.patch("time.time", return_value=float(ts_of(SUN))):
        assert asyncio.run(main_mod.disinfect_step(step_app, ts_of(SUN))) is False
    assert "disinfect_step" in main_mod.live_loop.__code__.co_names  # im 30-s-Takt


# ------------------------------------------------------------------ Mindestleistung
def power_engine(tmp_path, controls=None, **cfg):
    tmp_path.mkdir(parents=True, exist_ok=True)
    eng = Engine(Config(**cfg), Store(tmp_path / "p.db"), Discovered())
    if controls is not None:
        eng.ems_controls, eng.ems_controls_ts = controls, time.time()
    return eng


def test_min_power_suggested_from_burnmaxpower_range_never_applied(tmp_path):
    ctl = {"burnmaxpower": {"found": True, "writeable": True, "value": 100, "min": 32, "max": 100, "uom": "%"},
           "burnminpower": {"found": False}}
    eng = power_engine(tmp_path, ctl, boiler_nominal_kw=18.0)
    sg = boiler_power.suggestion(eng)
    assert sg == {"pct": 32.0, "min": 32.0, "max": 100.0, "source": "EMS-ESP",
                  "text": "aus dem Bereich der maximalen Brennerleistung (32–100 %)"}
    # nie automatisch: Einstellung bleibt 0, die Abnahme-Regel bleibt aus – mit Vorschlag im Hinweis
    assert eng.cfg.boiler_min_power_pct == 0.0 and boiler_power.pct(eng) == (None, None)
    kw, why = min_power_kw(eng)
    assert kw is None and why == ("Abnahme-Regel aus: Mindestleistung unbekannt – der Kessel meldet keine kleinste "
                                  "Brennerleistung; sie unter Einstellungen → Haus & Kessel eintragen (Vorschlag 32 % "
                                  "aus dem Bereich der maximalen Brennerleistung (32–100 %))")
    assert why in boiler_ready(eng, int(time.time()))["limits"]
    info = boiler_power.info(eng)
    assert info["pct"] is None and info["suggestion"]["pct"] == 32.0 and info["missing"] == why
    # übernommen (gespeichert) gilt sie: 32 % von 18 kW
    eng.cfg.boiler_min_power_pct = 32.0
    assert min_power_kw(eng) == (pytest.approx(5.76), None) and boiler_power.suggestion(eng) is None


def test_min_power_from_entity_range_and_no_suggestion_without_range(tmp_path):
    eng = power_engine(tmp_path / "a", boiler_nominal_kw=18.0)
    eng.found.boiler["max_power"] = "number.boiler_burnmaxpower"
    eng.states["number.boiler_burnmaxpower"] = {"state": "100", "attributes": {"min": 32, "max": 100,
                                                                               "unit_of_measurement": "%"}}
    assert boiler_power.suggestion(eng)["source"] == "number.boiler_burnmaxpower"
    eng.states["number.boiler_burnmaxpower"]["attributes"]["min"] = 0  # 0 % ist keine Leistung: kein Vorschlag
    assert boiler_power.suggestion(eng) is None
    assert boiler_power.suggestion(power_engine(tmp_path / "b")) is None  # ohne Bereich nichts
    eng = power_engine(tmp_path / "c", {"burnmaxpower": {"found": True, "min": 32, "max": 100},
                                        "burnminpower": {"found": True, "value": 30}})
    assert boiler_power.pct(eng) == (30.0, "laut Kessel") and boiler_power.suggestion(eng) is None
    assert boiler_power.missing(eng) == ("Abnahme-Regel aus: Mindestleistung unbekannt – Nennleistung des Kessels "
                                         "unter Einstellungen → Haus & Kessel eintragen")
    eng.ems = {"boiler": {"nompower": 18}}
    assert boiler_power.missing(eng) is None and min_power_kw(eng) == (pytest.approx(5.4), None)
    eng = power_engine(tmp_path / "d", boiler_min_power_pct=30.0)
    assert boiler_power.suggestion(eng) is None and boiler_power.pct(eng) == (30.0, "eingestellt")
    assert boiler_power.missing(power_engine(tmp_path / "e")).endswith(
        "kleinste Brennerleistung (%) und Nennleistung des Kessels unter Einstellungen → Haus & Kessel eintragen")


def test_emsesp_reads_burner_power_range_only_with_get():
    assert set(POWER_CONTROLS) == {"burnmaxpower", "burnminpower"} and not set(POWER_CONTROLS) & set(BOILER_CONTROLS)
    seen = []

    class Resp:
        def __init__(self, url):
            self.url, self.status = url, 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            name = self.url.rsplit("/", 1)[-1]
            if name == "burnmaxpower":
                return {"name": "burnmaxpower", "value": 100, "min": 32, "max": 100, "uom": "%", "writeable": True}
            return {"message": "unknown"}

    class Session:
        def get(self, url, timeout=None):
            seen.append(("GET", url))
            return Resp(url)

    ems = EmsEsp("ems.local", session=Session())
    out = asyncio.run(ems.controls())
    assert out["burnmaxpower"]["min"] == 32 and out["burnmaxpower"]["max"] == 100 and not out["burnminpower"]["found"]
    assert {m for m, _ in seen} == {"GET"} and ("GET", "http://ems.local/api/boiler/burnmaxpower") in seen
    src = (PKG / "emsesp.py").read_text(encoding="utf-8")
    assert not re.search(r"\.(post|put|patch|delete)\(", src)


def test_settings_carry_power_suggestion_without_saving(tmp_path, monkeypatch):
    eng, ha, dc = house(tmp_path)
    eng.ems_controls = {"burnmaxpower": {"found": True, "min": 32, "max": 100}}
    eng.ems_controls_ts = time.time()
    monkeypatch.setattr("heatpilot.config.DATA_DIR", tmp_path)
    app = fake_app(eng)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            return await (await client.get("/api/settings")).json()
        finally:
            await client.close()

    s = asyncio.run(go())
    assert s["boiler_power"]["suggestion"]["pct"] == 32 and s["settings"]["boiler_min_power_pct"] == 0.0
    assert not (tmp_path / "settings.json").exists()  # angezeigt, nicht gespeichert


# ------------------------------------------------------------------ Sicherheit, Lesbarkeit, Doku
def test_no_write_path_to_emsesp_and_files_small():
    for name in ("disinfect_entity.py", "disinfect_rule.py", "disinfect_control.py", "disinfect_status.py",
                 "boiler_power.py"):
        src = (PKG / name).read_text(encoding="utf-8")
        assert "call_service" not in src and "EmsEsp" not in src and "aiohttp" not in src, name
        assert src.startswith('"""') and src.count("\n") <= 600, name
        for fn in re.split(r"\n(?=    def |def |    async def |async def )", src)[1:]:
            assert fn.count("\n") <= 60, (name, fn.splitlines()[0])
    ctl = (PKG / "control.py").read_text(encoding="utf-8")
    assert ctl.count("\n") <= 600 and (PKG / "config.py").read_text(encoding="utf-8").count("\n") <= 600


def test_version_changelog_docs():
    from heatpilot import __version__

    assert __version__ >= "0.42.0"  # seit 0.42.1 prüft test_adopt421 die genaue Version
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## 0.42.0")[1].split("\n## ")[0]
    assert head.startswith("\n") and "Desinfektion auslösen" in head and "Mindestleistung" in head
    assert "tests/test_disinfect42.py" in head and "tests/test_ui_v42.py" in head
    for personal in ("Ingo", "BK15", "Sieger", "ES73"):
        assert personal not in head, personal
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for text in ("**Desinfektion auslösen**", "Desinfektionstemperatur − 3 K", "„übernehmen“", "Abnahme-Regel aus"):
        assert text in docs, text
    plan = (ROOT / "docs" / "planung-vorlauf-ems.md").read_text(encoding="utf-8")
    assert "Desinfektion – gebaut (0.42)" in plan
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Desinfektion auslösen gebaut (0.42)" in readme
