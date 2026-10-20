"""0.38 Weg W: HeatPilot setzt das Warmwasser-Soll – Regel, Schreibweg, Prüfung nach 60 s, Pause, fremder Schreiber.

Sicherheitsregeln (jede hat einen Test): geschrieben wird nur mit Schalter „heatpilot“, nur ``number.set_value`` auf
die eine eingestellte Entität, ganzzahlig und nur Minimum…Maximum, nur bei Änderung ab 1 K, 5 min Haltezeit, nicht
während einer Sonderladung; nach 3 Fehlschlägen bzw. 2 fremden Änderungen in 1 h pausiert Weg W. EMS-ESP bleibt
nur lesend.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from heatpilot import dhw_control as dc_mod
from heatpilot import main as main_mod
from heatpilot import preview as pv
from heatpilot import readiness
from heatpilot.config import Config
from heatpilot.control import Control
from heatpilot.dhw_control import (ACT_DRY, ACT_FAIL, ACT_FOREIGN, ACT_PAUSE, ACT_RESUME, ACT_WRITE, HOLD_S,
                                   K_FAIL, K_FOREIGN, K_PAUSED, DhwControl, decide, suggest_entity, target_entity,
                                   wish)
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.notify import Notifier
from heatpilot.store import Store

from test_control34 import MON, SUN, at, run, ts_of
from test_preview37 import dhw_model, house

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
ENT = "number.boiler_dhw_seltemp"
TARGETS = ["notify.handy", "notify.mobile_app_tablet"]


class RecHA:
    """Home Assistant zum Mitschreiben: jeder Dienstaufruf als Payload; ``fail``: Fehler der nächsten Aufrufe."""

    def __init__(self, fail=()):
        self.payloads, self.fail, self.set = [], list(fail), []

    async def call(self, payload, timeout=0):
        if self.fail:
            err = self.fail.pop(0)
            if err is not None:
                raise err
        self.payloads.append(payload)

    async def set_state(self, entity_id, state, attributes):
        self.set.append((entity_id, state, attributes))

    def writes(self):
        return [p for p in self.payloads if p["domain"] == "number"]

    def notes(self):
        return [p for p in self.payloads if p["domain"] == "notify"]


def make(tmp_path, mode="heatpilot", targets=TARGETS, entity_state="45", **cfg_kw):
    """Engine mit Warmwasser-Soll als number-Entität, Weg W und Meldungen (wie main.App._build_control)."""
    cfg = Config(dhw_control=mode, notify_targets=list(targets), **cfg_kw)
    found = Discovered(boiler={"dhw_seltemp": ENT})
    tmp_path.mkdir(parents=True, exist_ok=True)
    eng = Engine(cfg, Store(tmp_path / "w.db"), found)
    eng.states[ENT] = {"state": entity_state, "attributes": {"unit_of_measurement": "°C", "min": 30, "max": 70,
                                                             "step": 1, "friendly_name": "Boiler WW-Soll"}}
    eng.states["notify.handy"] = {"state": "unknown", "attributes": {"friendly_name": "Handy"}}
    ha = RecHA()
    entity = target_entity(cfg, found, eng.states)
    ctl = Control(ha, eng.store, found.rooms, states=eng.states, dhw_entity=entity[0] if cfg.writes_dhw() else None,
                  dhw_limits=(cfg.dhw_min_temp, cfg.dhw_max_temp), notify_targets=cfg.notify_targets)
    eng.notifier = Notifier(eng.store, ctl, cfg.notify_targets)
    eng.dhw_control = DhwControl(eng, ctl, eng.notifier, entity)
    return eng, ha, eng.dhw_control


def boiler(eng, value):
    """Kessel-Soll laut HA (so wie EMS-ESP es über MQTT meldet)."""
    eng.states[ENT]["state"] = str(value)


def step(eng, dc, ts, target, sample=None, reason="Zapfung erwartet (≈8 K)"):
    """Ein Takt von Weg W: Empfehlung setzen, planen, schreiben (wie Engine.tick + main.run)."""
    eng.dhw_rec = {"target": target, "reason": reason}
    dc.plan(ts, sample or {}, now=ts)
    return run(dc.apply(now=ts))


def log_actions(eng):
    return [r["action"] for r in reversed(eng.store.control_log(100))]


# ------------------------------------------------------------------ Regel (reine Funktionen)
def test_wish_rounds_to_whole_degrees_and_clamps():
    cfg = Config(dhw_min_temp=40, dhw_max_temp=60)
    assert wish(None, cfg) is None
    assert (wish(52.4, cfg), wish(52.5, cfg), wish(52.6, cfg)) == (52, 53, 53)  # halbe Grad aufwärts
    assert (wish(35, cfg), wish(64.7, cfg)) == (40, 60)  # nie unter dem Minimum, nie über dem Maximum
    odd = Config(dhw_min_temp=40.5, dhw_max_temp=59.5)
    assert (wish(40.5, odd), wish(30, odd), wish(70, odd)) == (41, 41, 59)  # ganze Grad innerhalb der Grenzen
    assert dc_mod.limits(odd) == (41, 59)


def test_decide_only_on_change_and_hold_time():
    t = ts_of(MON, 15, 3)
    assert decide(55, 45.0, t).write and decide(55, 45.0, t).value == 55
    assert not decide(55, 54.5, t).write and "passt" in decide(55, 54.5, t).why  # unter 1 K: nichts
    assert decide(55, 54.0, t).write  # genau 1 K
    assert not decide(55, 55.0, t).write  # kein Wiederholen gleicher Werte
    # 15:03 geschrieben → frühestens 15:08
    blocked = decide(50, 55.0, ts_of(MON, 15, 7) + 59, last_ts=t)
    assert not blocked.write and blocked.why == "Haltezeit bis 15:08"
    assert decide(50, 55.0, ts_of(MON, 15, 8), last_ts=t).write
    special = decide(50, 55.0, t, special=True)
    assert not special.write and "Sonderladung" in special.why
    assert not decide(50, 55.0, t, paused=True).write
    assert not decide(50, None, t).write and "unbekannt" in decide(50, None, t).why
    assert not decide(None, 55.0, t).write
    assert not decide(50, 55.0, t, checking=True).write


def test_entity_suggestion_and_setting(tmp_path):
    found = Discovered(boiler={"dhw_seltemp": "sensor.boiler_dhw_seltemp"})
    states = {"number.ems_esp_boiler_wwseltemp": {}, "number.boiler_burnminperiod": {}}
    assert suggest_entity(found, states) == "number.ems_esp_boiler_wwseltemp"  # ältere EMS-ESP, mit Präfix
    assert suggest_entity(Discovered(boiler={"dhw_seltemp": ENT}), {}) == ENT
    assert suggest_entity(Discovered(), {"number.boiler_burnminperiod": {}}) is None
    assert target_entity(Config(), Discovered(boiler={"dhw_seltemp": ENT}), {}) == (ENT, "erkannt")
    assert target_entity(Config(dhw_set_entity="number.kessel_ww"), Discovered(), {}) == ("number.kessel_ww",
                                                                                          "eingestellt")
    assert target_entity(Config(dhw_set_entity="sensor.x"), Discovered(), {}) == (None, None)


# ------------------------------------------------------------------ Schreibweg (Control)
def test_control_writes_only_the_configured_entity_within_limits(tmp_path):
    eng, ha, dc = make(tmp_path)
    ctl = dc.control
    run(ctl.set_dhw(55, "Test"))
    assert ha.payloads == [{"type": "call_service", "domain": "number", "service": "set_value",
                            "service_data": {"value": 55}, "target": {"entity_id": ENT}}]
    row = eng.store.control_log(1)[0]
    assert (row["room"], row["action"], row["value"], row["reason"]) == (ENT, ACT_WRITE, 55.0, "Test")
    for value in (39, 61, 55.5, "55", True, None):  # außerhalb 40…60, nicht ganzzahlig, kein int
        with pytest.raises(PermissionError):
            run(ctl.set_dhw(value, "Test"))
    for domain, service, entity, data in (
            ("number", "set_value", "number.boiler_burnminperiod", {"value": 10}),  # andere number-Entität
            ("number", "set_value", ENT, {"value": 50, "cycle": 1}),               # mehr als der Wert
            ("climate", "set_temperature", ENT, {"temperature": 50}),              # anderer Dienst
            ("number", "increment", ENT, {}),
            ("switch", "turn_on", "switch.boiler_wwonetime", {})):
        with pytest.raises(PermissionError):
            run(ctl._call(domain, service, entity, data))
    assert len(ha.payloads) == 1
    # Grenzen der Entität in HA gelten zusätzlich, Einheit muss eine Temperatur sein
    eng.states[ENT]["attributes"]["max"] = 55
    with pytest.raises(PermissionError, match="außerhalb der Grenzen"):
        run(ctl.set_dhw(58, "Test"))
    eng.states[ENT]["attributes"]["unit_of_measurement"] = "min"
    with pytest.raises(PermissionError, match="keine Temperatur"):
        run(ctl.set_dhw(50, "Test"))
    assert len(ha.payloads) == 1


def test_control_without_switch_has_no_dhw_write_path(tmp_path):
    for mode in ("aus", "trocken"):
        eng, ha, dc = make(tmp_path / mode, mode=mode)
        assert dc.control.dhw_entity is None
        with pytest.raises(PermissionError):
            run(dc.control.set_dhw(50, "Test"))
        with pytest.raises(PermissionError):
            run(dc.control._call("number", "set_value", ENT, {"value": 50}))
        assert ha.payloads == []
    # auch eine als Entität „gewählte“ Nicht-number-Entität wird nie Schreibziel
    assert Control(RecHA(), Store(tmp_path / "x.db"), [], dhw_entity="sensor.boiler_dhw_seltemp",
                   dhw_limits=(40, 60)).dhw_entity is None


def test_emsesp_stays_read_only_and_only_ha_rest_posts():
    src = (PKG / "emsesp.py").read_text(encoding="utf-8")
    assert not re.search(r"\.(post|put|patch|delete|request)\(", src)
    http = re.compile(r"(session|_open\(\))\.(post|put|patch|delete|request)\(")
    posts = {p.name for p in PKG.glob("*.py") if http.search(p.read_text(encoding="utf-8"))}
    assert posts == {"ha_client.py"}  # nur /api/states für die eigenen Sensoren
    for name in ("dhw_control.py", "dhw_rule.py", "dhw_status.py", "dhw_entity.py", "notify.py", "control.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert "emsesp" not in re.sub(r'""".*?"""', "", text, flags=re.DOTALL).lower().replace("ems-esp", ""), name


# ------------------------------------------------------------------ Takt: aus, trocken, heatpilot
def test_off_does_nothing(tmp_path):
    eng, ha, dc = make(tmp_path, mode="aus")
    assert step(eng, dc, ts_of(MON, 15), 55) is False
    assert ha.payloads == [] and eng.store.control_log(10) == [] and dc.pending is None
    assert dc.as_dict(ts_of(MON, 15))["status_text"] == "Warmwasser-Soll: aus – HeatPilot empfiehlt nur"


def test_dry_run_only_logs_with_same_rule(tmp_path):
    eng, ha, dc = make(tmp_path, mode="trocken")
    t = ts_of(MON, 15, 3)
    step(eng, dc, t, 55)
    step(eng, dc, t + 60, 55)          # gleicher Wert: kein zweiter Eintrag
    step(eng, dc, t + 120, 50)         # Haltezeit (bis 15:08): noch nicht
    step(eng, dc, t + HOLD_S, 50)      # 15:08: jetzt
    step(eng, dc, t + HOLD_S + 300, 50.4)
    assert ha.payloads == []           # nichts geschrieben
    rows = list(reversed(eng.store.control_log(10)))
    assert [(r["action"], r["value"]) for r in rows] == [(ACT_DRY, 55.0), (ACT_DRY, 50.0)]
    assert rows[0]["reason"] == "Beobachten: würde 55 °C schreiben (Zapfung erwartet (≈8 K))"
    assert dc.as_dict(t + 900)["status_text"].startswith("Beobachten · würde 50 °C seit 15:08")


def test_heatpilot_writes_on_change_holds_and_checks_after_60_s(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15, 3)
    assert step(eng, dc, t, 55) is True
    assert ha.writes()[-1]["service_data"] == {"value": 55} and dc.state["check"]["due"] == t + 60
    assert step(eng, dc, t + 30, 55) is False          # Prüfung läuft, nichts Neues
    boiler(eng, 55)                                    # Kessel übernimmt
    assert dc.verify(t + 59) is False and dc.verify(t + 60) is True
    assert dc.state["last_check"]["ok"] is True and dc.state["fails"] == 0
    assert step(eng, dc, t + 240, 50) is False and dc.view["decision"] == "Haltezeit bis 15:08"
    assert step(eng, dc, t + HOLD_S, 50) is True and ha.writes()[-1]["service_data"] == {"value": 50}
    boiler(eng, 50)
    assert step(eng, dc, t + 2 * HOLD_S + 60, 50.4) is False  # unter 1 K: nicht schreiben
    assert len(ha.writes()) == 2 and ha.notes() == []         # alles gut: keine Meldung
    st = dc.as_dict(t + HOLD_S + 120)
    assert st["status_text"].startswith("HeatPilot setzt · 50 °C seit 15:08") and st["last_check"]["ok"]
    assert st["log"][0]["action"] == ACT_WRITE and st["log"][0]["name"] == "Warmwasser-Soll"


def test_special_charge_blocks_writing_until_done(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(SUN, 13)
    for sample in ({"dhw_special": 1}, {"dhw_disinfecting": 1}):
        step(eng, dc, t, 55, sample=sample)
        assert ha.writes() == [] and "Sonderladung" in dc.view["decision"]
    eng.dhw._charge = {"special": True}  # vom Modell erkannt (weit über das Soll geladen), samt Nachlauf
    step(eng, dc, t + 300, 55)
    assert ha.writes() == []
    eng.dhw._charge = None
    step(eng, dc, t + 600, 55)
    assert ha.writes()[-1]["service_data"] == {"value": 55}


# ------------------------------------------------------------------ Fehlschläge und Pause
def test_failed_check_reports_once_pauses_after_three_and_recovers(tmp_path):
    eng, ha, dc = make(tmp_path)
    n = eng.notifier
    t = ts_of(MON, 15, 3)
    for i in range(3):                       # Kessel bleibt bei 45 °C
        ts = t + i * HOLD_S
        assert step(eng, dc, ts, 50) is True
        assert dc.verify(ts + 60) is True
        run(n.flush())
    texts = [p["service_data"]["message"] for p in ha.notes()]
    fail_msg = "Warmwasser-Soll 50 °C nicht übernommen (Kessel zeigt 45 °C) – 2. Versuch in 5 min"
    # je Ziel einmal „nicht übernommen“ (nicht bei jedem Fehlschlag) und einmal „pausiert“
    assert texts.count(fail_msg) == 2 and sum("pausiert" in x for x in texts) == 2 and len(texts) == 4
    entity_call = next(p for p in ha.notes() if p["service"] == "send_message")
    assert entity_call["target"] == {"entity_id": "notify.handy"} and entity_call["service_data"]["title"] == "HeatPilot"
    assert any(p["service"] == "mobile_app_tablet" and "target" not in p for p in ha.notes())
    assert dc.paused() and dc.state["fails"] == 3
    assert log_actions(eng).count(ACT_FAIL) == 3 and ACT_PAUSE in log_actions(eng)
    # pausiert: kein Schreiben mehr, auch nach der Haltezeit
    assert step(eng, dc, t + 4 * HOLD_S, 50) is False and len(ha.writes()) == 3
    assert dc.as_dict(t + 4 * HOLD_S)["status_text"] == "Warmwasser über HeatPilot pausiert – 3 Fehlschläge in Folge"
    assert set(n.open_keys("dhw_")) == {K_FAIL, K_PAUSED}
    # „wieder einschalten“: Pause weg (still), Schreibfehler offen bis zum ersten Erfolg → „wieder in Ordnung“
    assert dc.resume(t + 5 * HOLD_S) is True and ACT_RESUME in log_actions(eng)
    assert step(eng, dc, t + 5 * HOLD_S, 50) is True
    boiler(eng, 50)
    dc.verify(t + 5 * HOLD_S + 60)
    run(n.flush())
    assert ha.notes()[-1]["service_data"]["message"] == "Warmwasser-Soll wieder in Ordnung – 50 °C übernommen"
    assert n.open_keys() == {} and dc.state["fails"] == 0


def test_write_error_counts_as_failure_and_waits_for_hold(tmp_path):
    eng, ha, dc = make(tmp_path)
    ha.fail = [RuntimeError("HA weg")]
    t = ts_of(MON, 9)
    assert step(eng, dc, t, 55) is False
    assert dc.state["fails"] == 1 and "nicht geschrieben (HA weg)" in eng.store.control_log(1)[0]["reason"]
    assert step(eng, dc, t + 120, 55) is False and dc.view["decision"] == "Haltezeit bis 09:05"
    assert step(eng, dc, t + HOLD_S, 55) is True


def test_late_acceptance_is_no_foreign_writer(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    dc.verify(t + 60)                  # Kessel zeigt noch 45 → Fehlschlag 1
    assert dc.state["fails"] == 1
    boiler(eng, 55)                    # übernimmt verspätet
    step(eng, dc, t + 120, 55)
    assert dc.state["fails"] == 0 and ACT_FOREIGN not in log_actions(eng) and K_FAIL not in eng.notifier.open_keys()


# ------------------------------------------------------------------ Fremder Schreiber
def test_foreign_writer_hint_then_pause_after_two_in_one_hour(tmp_path):
    eng, ha, dc = make(tmp_path)
    n = eng.notifier
    t = ts_of(MON, 6)
    step(eng, dc, t, 45)                       # Kessel-Soll 45 passt: nichts geschrieben, Bezug gemerkt
    assert ha.writes() == []
    boiler(eng, 55)                            # HA-Automation schreibt 55
    step(eng, dc, t + 300, 45)
    assert log_actions(eng) == [ACT_FOREIGN, ACT_WRITE] and K_FOREIGN in n.open_keys()
    assert "anderer Schreiber aktiv, HA-Automation abschalten" in n.open_keys()[K_FOREIGN]
    boiler(eng, 45)
    dc.verify(t + 360)
    w = dc.as_dict(t + 400)["warnings"]
    assert any("Anderer Schreiber aktiv (zuletzt 45 → 55 °C, vor 1 min)" in x for x in w)  # ohne Uhrzeit (0.38)
    boiler(eng, 55)                            # noch einmal innerhalb einer Stunde
    step(eng, dc, t + 900, 45)
    assert dc.paused() and dc.state["paused"]["reason"] == "2 fremde Änderungen in 1 h"
    assert len(ha.writes()) == 1               # nicht gegeneinander schreiben
    run(n.flush())
    assert sum("pausiert" in p["service_data"]["message"] for p in ha.notes()) == 2
    # wieder einschalten: fremde Änderungen ab jetzt neu gezählt
    dc.resume(t + 1000)
    step(eng, dc, t + 1200, 45)
    assert not dc.paused() and ha.writes()[-1]["service_data"] == {"value": 45}
    rd = readiness.dhw(eng, t + 1300)
    assert rd["foreign_24h"] == 2 and rd["hint"] == dc_mod.HINT_AUTOMATION


def test_foreign_problem_closes_after_quiet_hour(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 6)
    step(eng, dc, t, 45)
    boiler(eng, 46)
    step(eng, dc, t + 300, 46)
    assert K_FOREIGN in eng.notifier.open_keys()
    step(eng, dc, t + 300 + 3600, 46)
    run(eng.notifier.flush())
    assert K_FOREIGN not in eng.notifier.open_keys()
    assert ha.notes()[-1]["service_data"]["message"] == "Warmwasser-Soll: seit 1 h keine fremden Änderungen mehr"


def test_switching_back_writes_nothing_and_hints(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    boiler(eng, 55)
    dc.verify(t + 60)
    eng.notifier.problem(K_FOREIGN, "Test", t)
    eng.cfg.dhw_control = "aus"
    step(eng, dc, t + 600, 45)
    assert len(ha.writes()) == 1 and eng.notifier.open_keys("dhw_") == {}  # still geschlossen
    w = dc.as_dict(t + 700)["warnings"]
    assert w == ["HeatPilot schreibt das Warmwasser-Soll nicht mehr – der Kessel behält 55 °C; die HA-Automation "
                 "ggf. wieder einschalten"]


def test_rebuild_keeps_state_and_old_instance_writes_nothing(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 55)
    new = DhwControl(eng, dc.control, eng.notifier, (ENT, "erkannt"), carry=dc)
    assert new.state is dc.state and new.state["last"]["value"] == 55
    dc.stop()
    eng.dhw_rec = {"target": 50, "reason": "x"}
    dc.planned_ts, dc.pending = t + HOLD_S, (50, "x")
    assert run(dc.apply(now=t + HOLD_S)) is False and len(ha.writes()) == 1


def test_switch_to_heatpilot_resets_fails_but_not_pause(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    step(eng, dc, t, 50)
    dc.verify(t + 60)
    assert dc.state["fails"] == 1
    eng.cfg.dhw_control = "trocken"
    step(eng, dc, t + 600, 52)
    assert len(ha.writes()) == 1 and ACT_DRY in log_actions(eng)  # Trockenlauf schreibt nie
    dc.state["paused"] = {"ts": t, "reason": "2 fremde Änderungen in 1 h"}
    eng.cfg.dhw_control = "heatpilot"
    step(eng, dc, t + 1200, 52)
    assert dc.state["fails"] == 0 and dc.paused() and len(ha.writes()) == 1  # Pause hebt nur „wieder einschalten“ auf


def test_current_prefers_entity_fresh_emsesp_old_names_and_skips_placeholders(tmp_path):
    eng, ha, dc = make(tmp_path)
    assert dc.current() == 45.0                       # Zustand der beschriebenen Entität in HA
    eng.set_ems({"boiler": {"wwseltemp": "45"}})      # ältere EMS-ESP, frisch und gleich
    assert dc.current() == 45.0 and dc.readings()["ems"] == 45.0
    eng.set_ems({"boiler": {"wwseltemp": "52"}})      # widerspricht der Entität: unklar → nichts schreiben
    assert dc.current() is None and dc.readings()["unclear"]
    eng.set_ems({"boiler": {"dhw_seltemp": 3276.7}})  # Platzhalter: unplausibel, dann HA
    assert dc.current() == 45.0
    boiler(eng, "unavailable")
    eng.set_ems({"boiler": {"dhw_seltemp": 50}})      # Entität weg: EMS-ESP direkt, solange frisch
    assert dc.current() == 50.0
    eng.ems_ts -= dc_mod.EMS_FRESH_S + 1              # EMS-ESP antwortet nicht mehr: alter Wert zählt nicht
    assert dc.current() is None


# ------------------------------------------------------------------ Engine, main, Bereitschaft, Vorschau
def test_engine_tick_plans_and_main_check_verifies(tmp_path):
    eng, ha, dc = make(tmp_path)
    eng.dhw_recommend = lambda ts, sample: (setattr(eng, "dhw_rec", {"target": 55, "reason": "Komfort"}) or
                                            ({"ok": True, "reasons": []}, 55))
    t = ts_of(MON, 15)
    eng.tick(t)
    assert dc.pending == (55, "Komfort")
    run(dc.apply(now=t))
    boiler(eng, 50)  # nicht übernommen
    app = SimpleNamespace(eng=eng, ha=ha)
    assert run(main_mod.check_dhw(app, now=t + 30)) is False
    assert run(main_mod.check_dhw(app, now=t + 61)) is True
    problem = ha.set[-1]
    assert problem[0] == "binary_sensor.heatpilot_problem" and problem[1] == "on"
    assert problem[2]["reasons"] == ["Warmwasser-Soll 55 °C nicht übernommen (Kessel zeigt 50 °C) – 2. Versuch in 5 min"]
    assert len(ha.notes()) == 2  # an beide Ziele


def test_readiness_block(tmp_path):
    eng, ha, dc = make(tmp_path, emsesp_host="ems-esp.local", dhw_min_temp=50)  # ab 50 °C keine Hygiene-Einschränkung
    t = ts_of(MON, 12)
    rd = readiness.dhw(eng, t)
    assert rd["entity"] == ENT and rd["entity_source"] == "erkannt" and rd["entity_ok"]
    assert (rd["entity_min"], rd["entity_max"], rd["value"]) == (30.0, 70.0, 45.0)
    assert rd["writeable"] is None and rd["level"] == "bereit" and rd["writes"] is True
    eng.ems_controls, eng.ems_controls_ts = {"wwseltemp": {"found": True, "writeable": True}}, t
    assert readiness.dhw(eng, t)["writeable"] is True and readiness.dhw(eng, t)["headline"].startswith("Läuft")
    eng.ems_controls = {"wwseltemp": {"found": True, "writeable": False}}
    assert readiness.dhw(eng, t)["level"] == "nicht bereit"
    eng.ems_controls = {"wwseltemp": {"found": True, "writeable": True}}
    dc.state["paused"] = {"ts": t, "reason": "3 Fehlschläge in Folge"}
    rd = readiness.dhw(eng, t)
    assert rd["level"] == "nicht bereit" and rd["paused"] and "wieder einschalten" in rd["blockers"][0]
    dc.state.pop("paused")
    dc.state["last_check"] = {"ts": t, "value": 50, "seen": 45.0, "ok": False}
    assert readiness.dhw(eng, t)["level"] == "eingeschränkt"
    eng.states[ENT]["attributes"]["max"] = 55
    assert any("geht über die Grenzen" in x for x in readiness.dhw(eng, t)["limits"])
    eng.states.pop(ENT)
    assert readiness.dhw(eng, t)["entity_problem"] == f"{ENT} fehlt in Home Assistant"
    assert "dhw" in readiness.assess(eng, t)  # Teil von „Bereit für die Steuerung?“


def test_preview_uses_same_rule_and_says_writes(tmp_path):
    cfg = Config.from_raw({"control_mode": "heatpilot", "dhw_control": "heatpilot"})
    eng, _, _ = house(tmp_path, cfg=cfg)
    eng.dhw = dhw_model()
    eng.states[ENT] = {"state": "45", "attributes": {"unit_of_measurement": "°C"}}
    eng.found.boiler["dhw_seltemp"] = ENT
    dc = DhwControl(eng, None, None, (ENT, "erkannt"))
    eng.dhw_control = dc
    now = ts_of(SUN, 10)
    dc.state["last"] = {"ts": now, "value": 45}  # gerade geschrieben: Haltezeit gilt auch in der Vorschau
    p = pv.preview(eng, now)
    assert p["dhw_mode"] == "heatpilot" and p["dhw_writes"] == "schreibt" and p["start"]["dhw"] == 45.0
    first = next(w for h in p["hours"] for w in h["writes"] if w["what"] == "dhw")
    assert first["value"] == 50 and first["from"] == 45.0 and first["ts"] >= now + HOLD_S  # seit 0.46.2: Komfort 50
    dc.state["paused"] = {"ts": now, "reason": "x"}
    assert pv.preview(eng, now)["dhw_writes"] == "würde schreiben"
    eng.cfg.dhw_control = "trocken"
    dc.state.pop("paused")
    dc.state["dry"] = {"ts": now - 3600, "value": 40}
    p = pv.preview(eng, now)
    assert p["start"]["dhw"] == 40 and p["dhw_writes"] == "würde schreiben"
    assert pv.count_writes([(at(SUN, 10, 2), 50)], 45, 1.0, since=at(SUN, 10)) == []  # in der Haltezeit
    assert p["note"] == pv.NOTE  # Vorlauf bleibt Trockenlauf


def test_files_readable_and_documented():
    for name in ("dhw_control.py", "dhw_rule.py", "dhw_status.py", "dhw_entity.py", "notify.py", "control.py",
                 "controlloop.py", "readiness.py", "web.py", "preview.py", "config.py", "dhw.py", "main.py",
                 "publisher.py", "ha_client.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 600, name
        assert text.startswith('"""'), name
    assert (PKG / "dhw_control.py").read_text(encoding="utf-8").startswith('"""Weg W (seit 0.38)')
    assert (PKG / "notify.py").read_text(encoding="utf-8").startswith('"""Benachrichtigungen (seit 0.38)')
