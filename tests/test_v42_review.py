"""Prüfung von 0.42 (Desinfektion auslösen): nachgestellte Funde und ihre Behebung.

* Seit 0.74.1 ist der Schalter ein Taster: HeatPilot schaltet nur ein, prüft ihn nicht und schaltet nie aus – auch
  nicht, wenn ein Regler auftaucht, der Schalter flattert oder ein Ablauf von vor 0.74.1 nach einem Neustart weiterläuft.
* Lag der Speicher schon beim Einschalten über dem Ziel, zählt erst die volle Desinfektionstemperatur.
* Termin während eines Ablaufs verlegt: keine zweite Desinfektion binnen 24 h (außer der Ablauf schlug fehl).
* Regler eben abgebaut (nur noch „gehalten“): der Termin wartet und wird nachgeholt, statt still als „macht der
  Regler“ zu gelten.
* Zeitüberlauf beim Einschalten: Text nie leer; der Ausgang ist unklar – der Speicher entscheidet.
* Uhr ging beim Scharfschalten vor: Termine zählen ab der richtigen Zeit.
* Gescheiterter Termin zählt im Warmwasser-Modell nicht als gelieferte Wärme.
"""

import asyncio
import time
from datetime import datetime
from datetime import time as dtime
from unittest import mock

import pytest

from heatpilot import boiler_power, dhw_disinfect
from heatpilot import disinfect_rule as rule
from heatpilot.control import Control
from heatpilot.dhw_control import special_running
from heatpilot.disinfect_control import K_FAIL
from heatpilot.disinfect_status import ACT_SKIP
from heatpilot.engine import Engine
from heatpilot.regulator import HOLD_S
from heatpilot.values import local

from test_dhw_control38 import RecHA
from test_disinfect42 import (SUN, SW, actions, armed, berlin, house, notes, shows, step, switches, ts_of,  # noqa: F401
                              view, wire)

T = ts_of(SUN)


# ---------------------------------------------------------------- Taster: nie ausschalten, Schalter nie prüfen
def test_regulator_appears_mid_run_switches_nothing(tmp_path):
    """Ein Regler taucht während des Ablaufs auf: HeatPilot schaltet nichts mehr – der Speicher entscheidet weiter."""
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on", tank=55)
    step(eng, dc, T + 60)
    eng.ems["thermostat"] = {"hc1_seltemp": 21.0}
    deadline = dc.state["run"]["deadline"]
    for k in range(0, 12):
        step(eng, dc, deadline + k * 300)
    assert switches(ha) == [("turn_on", SW)] and "run" not in dc.state
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – Speicher erreichte nur 55 °C, nötig 67 °C"]
    assert set(eng.notifier.open_keys("disinfect_")) == {K_FAIL}


def test_flapping_switch_state_no_spam(tmp_path):
    """Der Schalter flattert während und nach dem Ablauf: keine Meldung, kein Schalten."""
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    for k, sw in enumerate(["off", "on", "unavailable", "off", "on", "off"] * 5):
        shows(eng, switch=sw, tank=68 if k > 10 else 55)
        step(eng, dc, T + 60 + k * 60)
    assert notes(ha) == [] and switches(ha) == [("turn_on", SW)] and dc.state["last"]["ok"] is True


# ---------------------------------------------------------------- Speicher schon heiß
def test_hot_tank_alone_is_no_success(tmp_path):
    eng, ha, dc = house(tmp_path, tank=58.0, dhw_disinfect_temp=60)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="off")
    step(eng, dc, T + 60)
    assert dc.active() and rule.need(dc.state["run"]) == 60.0  # 58 °C liegt schon über 60 − 3 K
    step(eng, dc, dc.state["run"]["deadline"])
    last = dc.state["last"]
    assert last["ok"] is False and last["result"] == "fehlgeschlagen"
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – Speicher erreichte nur 58 °C, nötig 60 °C"]


def test_hot_start_needs_full_temperature(tmp_path):
    """Speicher schon über dem Ziel: erst die volle Desinfektionstemperatur ist ein Erfolg – auch wenn der Kessel den
    Schalter vorher zurücksetzt."""
    eng, ha, dc = house(tmp_path, tank=67.5)
    armed(eng, dc)
    step(eng, dc, T)
    run = dc.state["run"]
    assert rule.need(run) == 70.0 and view(eng, dc, T + 60)["run"]["goal"] == 70.0
    shows(eng, switch="off")
    step(eng, dc, T + 1800)
    assert dc.active()
    shows(eng, tank=70.0)
    step(eng, dc, T + 2400)
    assert dc.state["last"]["ok"] is True and "run" not in dc.state
    assert switches(ha) == [("turn_on", SW)] and notes(ha) == []


def test_hot_start_without_full_temperature_fails_at_deadline(tmp_path):
    eng, ha, dc = house(tmp_path, tank=67.5)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on")
    step(eng, dc, T + 60)
    step(eng, dc, dc.state["run"]["deadline"])
    assert dc.state["last"]["ok"] is False
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – Speicher erreichte nur 67,5 °C, nötig 70 °C"]
    assert switches(ha) == [("turn_on", SW)]


# ---------------------------------------------------------------- Termin während des Ablaufs verlegt
def _run_with_change(tmp_path, ok=True):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    if not ok:
        ha.fail = [RuntimeError("abgelehnt")]  # Einschalten scheitert: Fehlschlag
    step(eng, dc, T)
    if ok:
        shows(eng, switch="on", tank=55)
        step(eng, dc, T + 60)
    eng.cfg.dhw_disinfect_time = dtime(18, 0)  # Termin verlegt, z. B. „Vorschlag übernehmen“
    step(eng, dc, T + 900)
    if ok:
        shows(eng, tank=68)
        step(eng, dc, T + 1200)
    assert "run" not in dc.state
    for k in range(0, 40):
        step(eng, dc, T + 1800 + k * 600)
    return eng, ha, dc


def test_termin_changed_during_run_does_not_fire_again(tmp_path):
    eng, ha, dc = _run_with_change(tmp_path)
    assert switches(ha).count(("turn_on", SW)) == 1
    log = [r for r in eng.store.control_log(20) if r["action"] == ACT_SKIP]
    assert len(log) == 1 and log[0]["reason"] == ("Termin So 18:00 übersprungen – die Desinfektion So 17:30 lief vor "
                                                  "weniger als 24 h")
    assert dc.state["last"]["result"] == "gelungen" and notes(ha) == []
    step(eng, dc, T + 7 * 86400 + 1800)  # nächste Woche: So 18:00 gilt
    assert switches(ha).count(("turn_on", SW)) == 2


def test_termin_changed_after_failed_run_is_a_new_attempt(tmp_path):
    eng, ha, dc = _run_with_change(tmp_path, ok=False)
    assert switches(ha) == [("turn_on", SW)] and ACT_SKIP not in actions(eng)  # So 18:00 eingeschaltet


def test_too_soon_rule():
    t = datetime(2026, 10, 18, 18, 0).astimezone()
    on = {"ts": t.timestamp() - 1800, "due": "a"}
    assert rule.too_soon(t, on, {"due": "a", "ok": True})
    assert rule.too_soon(t, on, None)  # läuft noch
    assert not rule.too_soon(t, on, {"due": "a", "ok": False})  # gescheitert: neuer Versuch
    assert not rule.too_soon(t, {"ts": t.timestamp() - rule.GAP_S}, None)
    assert not rule.too_soon(t, None, None)


# ---------------------------------------------------------------- Regler eben abgebaut
def test_regulator_removed_shortly_before_term_waits_and_catches_up(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    eng.regulator_watch.update(seen=T - 600)  # ES73 10 min vor dem Termin abgebaut
    step(eng, dc, T)
    assert switches(ha) == [] and dc.state["waiting"]["why"].startswith("Regler zuletzt")
    x = view(eng, dc, T)
    assert x["state"] == "heatpilot" and x["text"].startswith("Desinfektion: löst HeatPilot aus")
    assert x["blocked"].startswith("Regler zuletzt")
    fired = [ts for ts in (T + k * 300 for k in range(1, 12)) if step(eng, dc, ts)]
    assert fired == [T + 1200] and switches(ha) == [("turn_on", SW)]  # 30 min nach dem letzten Sehen


def test_regulator_freshly_reported_still_does_it(tmp_path):
    eng, ha, dc = house(tmp_path, regulator=True)
    armed(eng, dc)
    step(eng, dc, T)
    assert dc.state["last"]["result"] == "regler" and switches(ha) == []


@pytest.mark.parametrize("variant", ["stale", "failed", "absent_n", "seen", "absent_short"])
def test_unknown_or_held_regulator_never_switches(tmp_path, variant):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    w = eng.regulator_watch
    for k in range(0, 30):
        ts = T + k * 600
        if variant == "failed":
            w.update(last="fehler", ts=ts)
        elif variant == "absent_n":
            w.update(last="fehlt", ts=ts, absent_since=ts - 1000, absent_n=2)
        elif variant == "absent_short":
            w.update(last="fehlt", ts=ts, absent_since=ts - 200, absent_n=5)
        elif variant == "seen":
            w.update(seen=ts - HOLD_S + 60)
        step(eng, dc, ts, ems_alive=variant != "stale")
    assert "waiting" in dc.state or dc.state.get("last", {}).get("result") == "verpasst"
    assert switches(ha) == []


# ---------------------------------------------------------------- Zeitüberlauf beim Einschalten
def test_timeout_on_switch_on_not_arrived_fails_by_tank_with_reason(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    ha.fail = [asyncio.TimeoutError()]
    assert not step(eng, dc, T)
    run = dc.state["run"]
    assert run["unclear"] == "Zeitüberlauf" and dc.active()  # Ausgang unklar: der Speicher entscheidet
    step(eng, dc, run["deadline"])  # nicht angekommen – der Speicher blieb kalt
    text = "Speicher erreichte nur 52 °C, nötig 67 °C (das Einschalten meldete „Zeitüberlauf“)"
    assert dc.state["last"]["text"] == text and "run" not in dc.state
    assert notes(ha) == [f"Desinfektion So 17:30 nicht gelaufen – {text}"]
    for k in range(1, 20):  # kein neuer Versuch zu diesem Termin
        step(eng, dc, run["deadline"] + k * 600)
    assert switches(ha) == []


def test_timeout_on_switch_on_but_arrived_is_watched(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    ha.fail = [asyncio.TimeoutError()]
    step(eng, dc, T)
    shows(eng, switch="on", tank=68)  # der Aufruf kam doch an
    step(eng, dc, T + 1800)
    assert dc.state["last"]["ok"] is True and "run" not in dc.state and switches(ha) == []
    shows(eng, switch="off")  # der Kessel ist fertig
    with mock.patch("time.time", return_value=float(T + 4 * 3600)):
        assert not special_running(eng, {})


def test_permission_error_on_switch_on_is_plain_failure(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    ha.fail = [PermissionError("gesperrt")]
    step(eng, dc, T)
    assert dc.state["last"]["text"] == "nicht gestartet (gesperrt)" and "run" not in dc.state


# ---------------------------------------------------------------- Uhr
def test_clock_ahead_at_arming_is_corrected(tmp_path):
    eng, ha, dc = house(tmp_path)
    step(eng, dc, T + 365 * 86400)  # erster Schritt mit falscher Uhr (ein Jahr voraus)
    step(eng, dc, T - 3600)  # Uhr korrigiert: Termine zählen ab jetzt
    assert dc.state["armed"]["ts"] == T - 3600
    assert step(eng, dc, T) and switches(ha) == [("turn_on", SW)]


def test_clock_jumps_back_after_run_no_second_trigger(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on", tank=68)
    step(eng, dc, T + 60)
    assert "run" not in dc.state
    for back in (3600, 600, 60, 0):
        step(eng, dc, T + back)
    assert switches(ha) == [("turn_on", SW)]


def test_clock_jumps_forward_during_run(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on", tank=55)
    step(eng, dc, T + 60)
    step(eng, dc, T + 13 * 3600)
    assert switches(ha) == [("turn_on", SW)] and len(notes(ha)) == 1


# ---------------------------------------------------------------- Warmwasser-Modell nach einem Fehlschlag
def test_failed_term_does_not_count_as_heat_in_model(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    assert eng.cfg.disinfect_triggered
    ha.fail = [RuntimeError("abgelehnt")]
    step(eng, dc, T)
    assert dc.state["last"]["ok"] is False
    step(eng, dc, T + 600)
    now = local(T + 600)
    assert not eng.cfg.disinfect_triggered and not dhw_disinfect.plan_counts(eng.dhw, eng.cfg, now)
    assert dhw_disinfect.planned_for(eng.dhw, eng.cfg, now, 6, eng.disinfect_ready_h()) == []
    with mock.patch("time.time", return_value=float(T + 600)):
        info = eng.disinfect_info(now_ts=T + 600)
    assert info["counts"] is False and info["counts_text"] == ("Desinfektion So 17:30 nicht gelaufen – ihre Wärme "
                                                               "zählt im Warmwasser-Modell nicht")
    # nach dem Fenster des Modells (Ladedauer + 12 h + 1 h) zählt der nächste Termin wieder
    after = T + int((eng.disinfect_ready_h() + 13) * 3600) + 60
    step(eng, dc, after)
    assert eng.cfg.disinfect_triggered and dhw_disinfect.plan_counts(eng.dhw, eng.cfg, local(after))


def test_failed_recently_rule():
    t = datetime(2026, 10, 18, 17, 30).astimezone()
    last = {"due": t.isoformat(timespec="minutes"), "ok": False}
    assert rule.failed_recently(last, t.timestamp() + 3600, 1.0)
    assert not rule.failed_recently(last, t.timestamp() + 14 * 3600 + 1, 1.0)
    assert not rule.failed_recently({**last, "ok": True}, t.timestamp() + 3600, 1.0)
    assert not rule.failed_recently({**last, "ok": None}, t.timestamp() + 3600, 1.0)
    assert not rule.failed_recently({"ok": False, "due": "kaputt"}, t.timestamp(), 1.0)


def test_main_build_uses_triggered(tmp_path):
    """Auch der Neuaufbau (main) setzt ``disinfect_triggered`` über ``triggered`` – nicht für einen eben gescheiterten
    Termin."""
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    ha.fail = [RuntimeError("abgelehnt")]
    step(eng, dc, T)
    with mock.patch("time.time", return_value=float(T + 60)):
        eng.ems_ts = float(T + 60)
        assert dc.triggered(T + 60) is False
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot" /
           "main.py").read_text()
    assert "cfg.disinfect_triggered = eng.disinfect_control.triggered(time.time())" in src


# ---------------------------------------------------------------- Neustart, Zeitumstellung (unverändert sicher)
@pytest.mark.parametrize("phase", [None, "check", "running", "after", "off", "off_check"])
def test_restart_mid_run_no_second_on_and_old_phases_end_quietly(tmp_path, phase):
    """Neustart mitten im Ablauf – auch mit einem gespeicherten Ablauf von vor 0.74.1 (Prüfung, Nachlauf, Ausschalten):
    nie ein zweites Einschalten, nie ein Ausschalten; stand das Ergebnis schon fest, endet er still."""
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    if phase is not None:
        dc.state["run"]["phase"] = phase
    done = phase in ("after", "off", "off_check")
    if done:  # vor 0.74.1: Ergebnis stand fest, der Schalter sollte noch aus
        dc.state["last"] = {"due": dc.state["run"]["due"], "label": "So 17:30", "ts": T, "end": T + 900, "ok": True,
                            "result": "gelungen", "text": "68 °C erreicht (Ziel 67 °C)"}
    dc._save()
    eng2, ha2, dc2 = wire(Engine(eng.cfg, eng.store, eng.found), carry=eng)
    eng2.states.update(eng.states)
    eng2.ems, eng2.regulator_watch = eng.ems, eng.regulator_watch
    shows(eng2, switch="on", tank=55)
    for k in range(1, 200):
        step(eng2, dc2, T + 60 + k * 120)
    assert switches(ha2) == [] and "run" not in dc2.state
    if done:
        assert dc2.state["last"]["ok"] is True and notes(ha2) == []  # kein falsches „nicht gelaufen“
    else:
        assert dc2.state["last"]["ok"] is False  # der Speicher blieb bei 55 °C: nach der Frist ein Fehlschlag


@pytest.mark.usefixtures("berlin")
def test_dst_spring_forward_nonexistent_time_fires_once(tmp_path):
    eng, ha, dc = house(tmp_path, at="02:30")
    start = int(datetime(2026, 3, 28, 20, 0).astimezone().timestamp())
    fired = []
    for k in range(0, 12 * 24):
        ts = start + k * 300
        if step(eng, dc, ts):
            fired.append(ts)
        shows(eng, switch="off")
    assert len(fired) == 1


@pytest.mark.usefixtures("berlin")
@pytest.mark.parametrize("cadence", [60, 300, 900])
def test_dst_weeks_with_slow_cadence_fire_once_each(tmp_path, cadence):
    eng, ha, dc = house(tmp_path, at="02:30")
    start = int(datetime(2026, 10, 17, 0, 0).astimezone().timestamp())
    fired = []
    for k in range(0, 21 * 86400 // cadence):
        ts = start + k * cadence
        if step(eng, dc, ts):
            fired.append(ts)
        shows(eng, switch="off")
    assert len(fired) in (2, 3)
    assert all(b - a > rule.GAP_S for a, b in zip(fired, fired[1:]))


# ---------------------------------------------------------------- Schreibweg, Meldungen (unverändert)
def test_control_rejects_other_switches_and_wrong_disinfect_entities(tmp_path):
    eng, ha, dc = house(tmp_path)
    for bad in ("switch.boiler_wwonetime", "switch.boiler_dhw_onetime", "switch.boiler_wwcirc",
                "switch.boiler_heatingactivated", "switch.thermostat_hc1_wwdisinfecting", "switch.boiler_wwactivated",
                "switch.boiler_chimneysweeper"):
        eng.states[bad] = {"state": "off", "attributes": {"friendly_name": "Desinfektion"}}
        ctl = Control(RecHA(), eng.store, [], states=eng.states, disinfect=bad, disinfect_lock=lambda: None)
        with mock.patch("time.time", return_value=time.time()):
            with pytest.raises(PermissionError):
                asyncio.run(ctl.start_disinfect("x"))
        assert ctl.ha.payloads == [], bad
    ctl = Control(RecHA(), eng.store, [], states=eng.states, boiler={"heating": SW, "flow": None, "max": 60},
                  boiler_lock=lambda: None)
    with pytest.raises(PermissionError):
        asyncio.run(ctl.set_heating(True, "x"))
    eng.ems["thermostat"] = {"hc1_seltemp": 20}
    with pytest.raises(PermissionError):
        asyncio.run(dc.control.start_disinfect("x"))


def test_maybe_arrived_classifies_errors(tmp_path):
    eng, ha, dc = house(tmp_path)
    ctl = dc.control
    assert ctl.maybe_arrived(asyncio.TimeoutError()) and ctl.maybe_arrived(ConnectionError("x"))
    assert not ctl.maybe_arrived(RuntimeError("abgelehnt"))
    assert not dc._unclear(PermissionError("gesperrt")) and dc._unclear(asyncio.TimeoutError())


def test_mode_change_mid_run_leaves_switch_alone(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on", tank=55)
    step(eng, dc, T + 60)
    eng.cfg.dhw_control = "trocken"
    for k in range(1, 20):
        step(eng, dc, T + 60 + k * 600)
    assert switches(ha) == [("turn_on", SW)] and dc.state["last"]["result"] == "abgebrochen"


def test_persistent_blocker_notifications_once_per_week(tmp_path):
    eng, ha, dc = house(tmp_path)
    del eng.states[SW]
    armed(eng, dc)
    for k in range(0, 4 * 7 * 48):
        step(eng, dc, T + k * 1800)
    assert len(notes(ha)) == 4


# ---------------------------------------------------------------- Mindestleistung (unverändert)
@pytest.mark.parametrize("ctrl,pct,ems_min,expect", [
    ({"burnmaxpower": {"found": True, "value": 100, "min": 32, "max": 100}}, 0.0, None, 32),
    ({"burnmaxpower": {"found": True, "value": 100, "min": 0, "max": 100}}, 0.0, None, None),
    ({"burnmaxpower": {"found": True, "value": 100, "min": 32, "max": 100}}, 25.0, None, None),
    ({"burnmaxpower": {"found": True, "value": 100, "min": 32, "max": 100}}, 0.0, 20, None),
    ({"burnmaxpower": {"found": True, "value": 100, "min": 32, "max": 100}}, 0.0, 0, 32),
    ({"burnmaxpower": {"found": True, "value": 100, "min": "32", "max": "100"}}, 0.0, None, 32),
    ({"burnmaxpower": {"found": True, "value": 100, "min": 100, "max": 100}}, 0.0, None, None),
    ({"burnmaxpower": {"found": True, "value": 100, "min": 32, "max": 160}}, 0.0, None, None),
    ({"burnmaxpower": {"found": False}}, 0.0, None, None),
    (None, 0.0, None, None),
])
def test_min_power_suggestion_cases(tmp_path, ctrl, pct, ems_min, expect):
    eng, ha, dc = house(tmp_path, boiler_min_power_pct=pct)
    if ctrl is not None:
        eng.ems_controls, eng.ems_controls_ts = ctrl, time.time()
    if ems_min is not None:
        eng.ems["boiler"]["burnminpower"] = ems_min
    eng.ems["boiler"]["nompower"] = 18
    sg = boiler_power.suggestion(eng)
    assert (sg or {}).get("pct") == expect
    assert eng.cfg.boiler_min_power_pct == pct  # nie übernommen
