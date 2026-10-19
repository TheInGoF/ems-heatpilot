"""Prüfung von 0.42 (Desinfektion auslösen): nachgestellte Funde und ihre Behebung.

* Ausschalten nach dem Ablauf: Sperrt etwas nur kurz (EMS-ESP antwortet nicht, Regler unbekannt, Pause von Weg W) oder
  ist der Schalter kurz unbekannt, versucht HeatPilot es weiter – erst nach 30 min kommt „nicht ausgeschaltet“.
  Gesperrt bleibt das Ausschalten unter denselben Bedingungen wie das Einschalten (kein Regler am Bus, keine Pause).
* „Gelungen“ nur nach der Bestätigung durch den Kessel; lag der Speicher schon beim Einschalten über dem Ziel, zählt
  die volle Desinfektionstemperatur bzw. dass der Kessel den Schalter selbst zurücksetzt – und HeatPilot schaltet
  dann nicht nach 15 min ab.
* Termin während eines Ablaufs verlegt: keine zweite Desinfektion binnen 24 h (außer der Ablauf schlug fehl).
* Regler eben abgebaut (nur noch „gehalten“): der Termin wartet und wird nachgeholt, statt still als „macht der
  Regler“ zu gelten.
* Zeitüberlauf beim Einschalten: Text nie leer; der Ausgang ist unklar – die Prüfung nach 60 s entscheidet.
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
from heatpilot.boiler_control import BoilerControl
from heatpilot.control import Control
from heatpilot.dhw_control import special_running
from heatpilot.disinfect_control import K_FAIL, K_SWITCH
from heatpilot.disinfect_status import ACT_SKIP
from heatpilot.engine import Engine
from heatpilot.regulator import HOLD_S
from heatpilot.values import local

from test_dhw_control38 import RecHA
from test_disinfect42 import (SUN, SW, actions, armed, berlin, house, notes, shows, step, switches, ts_of,  # noqa: F401
                              view, wire)

T = ts_of(SUN)


def _success_then_after(tmp_path, **kw):
    """Ablauf bis in den Nachlauf (Speicher heiß, Schalter an). Gibt (eng, ha, dc, Zeit des Ausschaltens)."""
    eng, ha, dc = house(tmp_path, **kw)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on", tank=68)
    step(eng, dc, T + 60)
    assert dc.state["run"]["phase"] == "after"
    return eng, ha, dc, T + 60 + rule.AFTER_S


def _off_done(eng, ha, dc, ts):
    """Nach dem Ausschalten: der Kessel zeigt „aus“, die Prüfung nach 60 s schließt den Ablauf."""
    shows(eng, switch="off")
    step(eng, dc, ts + rule.CHECK_S)
    assert "run" not in dc.state and dc.state["last"]["switch"] == "Schalter aus"


# ---------------------------------------------------------------- Ausschalten: kurz gesperrt → weiter versuchen
def test_switch_off_retried_after_short_ems_gap(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    eng.ems_ts = float(t_off - 120)  # eine Abfrage hing: Regler „unbekannt“ → gesperrt
    step(eng, dc, t_off, ems_alive=False)
    run = dc.state["run"]
    assert run["phase"] == "off" and "EMS-ESP antwortet gerade nicht" in run["off"]["why"]
    assert switches(ha) == [("turn_on", SW)] and notes(ha) == []
    x = view(eng, dc, t_off)  # die Anzeige sagt, dass das Ausschalten wartet (EMS-ESP antwortet wieder)
    assert x["run"]["off_wait"]["why"].startswith("EMS-ESP antwortet gerade nicht")
    assert "Ausschalten wartet" in x["run"]["text"]
    step(eng, dc, t_off + 600)  # EMS-ESP antwortet wieder: jetzt ausschalten
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    _off_done(eng, ha, dc, t_off + 600)
    assert notes(ha) == [] and K_SWITCH not in eng.notifier.open_keys()


def test_switch_off_retried_after_single_failed_thermostat_poll(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    eng.regulator_watch.update(last="fehler", ts=t_off)
    step(eng, dc, t_off)
    assert switches(ha) == [("turn_on", SW)] and dc.state["run"]["phase"] == "off"
    eng.regulator_watch.update(last="fehlt", ts=t_off + 600, absent_since=0, absent_n=99)
    step(eng, dc, t_off + 600)
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    _off_done(eng, ha, dc, t_off + 600)
    assert notes(ha) == []


def test_pause_mid_run_delays_switch_off_until_lifted(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    eng.dhw_control.state["paused"] = {"ts": T, "reason": "3 Fehlschläge in Folge"}
    step(eng, dc, t_off)
    assert switches(ha) == [("turn_on", SW)]  # pausiert: auch Ausschalten gesperrt (wie Einschalten)
    eng.dhw_control.state.pop("paused")
    step(eng, dc, t_off + 600)
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)] and notes(ha) == []


def test_long_pause_reports_switch_not_off_after_30_min(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    eng.dhw_control.state["paused"] = {"ts": T, "reason": "3 Fehlschläge in Folge"}
    for k in range(0, 6):
        step(eng, dc, t_off + k * 300)
    assert "run" in dc.state and notes(ha) == []  # 25 min: wartet noch, keine Meldung
    step(eng, dc, t_off + rule.OFF_WAIT_S)
    assert "run" not in dc.state and switches(ha) == [("turn_on", SW)]
    assert notes(ha) == ["Desinfektion So 17:30: Schalter nicht ausgeschaltet (Warmwasser über HeatPilot ist pausiert "
                         "– erst „wieder einschalten“) – bitte in Home Assistant prüfen"]
    for k in range(1, 10):  # einmal gemeldet, kein Spam
        step(eng, dc, t_off + rule.OFF_WAIT_S + k * 600)
    assert len(notes(ha)) == 1 and K_SWITCH in eng.notifier.open_keys()


def test_regulator_appears_mid_run_never_switches_off(tmp_path):
    """Ein Regler taucht während des Ablaufs auf: HeatPilot schaltet nichts mehr (auch nicht aus) – Meldung."""
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
    assert any("Schalter nicht ausgeschaltet (EMS-ESP meldet einen Regler" in n for n in notes(ha))
    assert K_FAIL in eng.notifier.open_keys() and K_SWITCH in eng.notifier.open_keys()


def test_switch_off_call_error_retried_then_reported(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    real = dc.control.set_disinfect
    calls = []

    async def flaky(on, reason):
        calls.append(on)
        if not on and len(calls) <= 1:
            raise RuntimeError("Home Assistant: Dienst nicht erreichbar")
        await real(on, reason)

    dc.control.set_disinfect = flaky
    step(eng, dc, t_off)
    assert dc.state["run"]["phase"] == "off" and dc.state["run"]["off"]["why"].startswith("Home Assistant: Dienst")
    step(eng, dc, t_off + 30)  # frühestens nach 60 s erneut
    assert calls == [False]
    step(eng, dc, t_off + 60)
    assert calls == [False, False] and switches(ha) == [("turn_on", SW), ("turn_off", SW)]
    _off_done(eng, ha, dc, t_off + 60)
    assert notes(ha) == []
    # bleibt der Aufruf gestört: nach 30 min eine Meldung mit dem Grund
    eng, ha, dc, t_off = _success_then_after(tmp_path / "b")

    async def broken(on, reason):
        raise RuntimeError("Dienst nicht erreichbar")

    dc.control.set_disinfect = broken
    for k in range(0, 32):
        step(eng, dc, t_off + k * 60)
    assert "run" not in dc.state
    assert notes(ha) == ["Desinfektion So 17:30: Schalter nicht ausgeschaltet (Dienst nicht erreichbar) – bitte in "
                         "Home Assistant prüfen"]


# ---------------------------------------------------------------- Schalter kurz „unavailable“ am Ende des Nachlaufs
def test_switch_briefly_unavailable_at_end_of_after_run_is_switched_off_later(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    shows(eng, switch="unavailable")
    step(eng, dc, t_off)
    assert dc.state["run"]["phase"] == "off" and dc.state["run"]["off"]["why"] == "Zustand des Schalters unbekannt"
    shows(eng, switch="on")
    step(eng, dc, t_off + 600)
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)] and notes(ha) == []
    _off_done(eng, ha, dc, t_off + 600)
    later = T + 3 * 86400  # Weg W und Weg B sind danach nicht eingefroren
    step(eng, dc, later)
    with mock.patch("time.time", return_value=float(later)):
        assert not special_running(eng, {}) and not BoilerControl(eng, None, None, {}).charging()


def test_switch_unavailable_for_long_is_reported(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    shows(eng, switch="unavailable")
    for k in range(0, 7):
        step(eng, dc, t_off + k * 300)
    assert "run" not in dc.state and switches(ha) == [("turn_on", SW)]
    assert notes(ha) == ["Desinfektion So 17:30: Schalter nicht ausgeschaltet (Zustand des Schalters unbekannt) – "
                         "bitte in Home Assistant prüfen"]


def test_boiler_resets_switch_while_off_waits(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    eng.dhw_control.state["paused"] = {"ts": T, "reason": "x"}
    step(eng, dc, t_off)
    shows(eng, switch="off")  # der Kessel setzt ihn selbst zurück
    step(eng, dc, t_off + 300)
    assert "run" not in dc.state and dc.state["last"]["switch"] == "Schalter aus" and notes(ha) == []
    assert switches(ha) == [("turn_on", SW)]


# ---------------------------------------------------------------- „gelungen“ nur mit Bestätigung des Kessels
def test_rejected_switch_is_failure_even_when_tank_already_hot(tmp_path):
    eng, ha, dc = house(tmp_path, tank=58.0, dhw_disinfect_temp=60)
    armed(eng, dc)
    step(eng, dc, T)
    eng.ems["boiler"]["wwdisinfecting"] = "off"
    shows(eng, switch="off")
    step(eng, dc, T + 60)
    last = dc.state["last"]
    assert last["ok"] is False and last["result"] == "fehlgeschlagen"
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – Schalter fällt sofort zurück – der Kessel nimmt die "
                         "Desinfektion nicht an"]


def test_hot_start_needs_full_temperature_or_boiler_reset(tmp_path):
    """Speicher schon über dem Ziel: nicht nach 60 s „gelungen“ und nicht nach 15 min ausgeschaltet."""
    eng, ha, dc = house(tmp_path / "a", tank=67.5)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on")
    step(eng, dc, T + 60)
    run = dc.state["run"]
    assert run["phase"] == "running" and rule.need(run) == 70.0
    assert view(eng, dc, T + 60)["run"]["goal"] == 70.0
    step(eng, dc, T + 60 + rule.AFTER_S)
    assert switches(ha) == [("turn_on", SW)] and dc.state["run"]["phase"] == "running"
    shows(eng, switch="off")  # der Kessel beendet sein Programm selbst: gelungen
    step(eng, dc, T + 1800)
    last = dc.state["last"]
    assert last["ok"] is True and "der Kessel hat sie beendet" in last["text"] and "run" not in dc.state
    assert switches(ha) == [("turn_on", SW)] and notes(ha) == []
    # volle Temperatur erreicht: gelungen, aber der Nachlauf dauert bis zur Frist des Ablaufs
    eng, ha, dc = house(tmp_path / "b", tank=67.5)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on")
    step(eng, dc, T + 60)
    deadline = dc.state["run"]["deadline"]
    shows(eng, tank=70.0)
    step(eng, dc, T + 600)
    assert dc.state["last"]["ok"] is True and dc.state["run"]["after_until"] == deadline
    step(eng, dc, T + 600 + rule.AFTER_S)
    assert switches(ha) == [("turn_on", SW)]
    step(eng, dc, deadline)
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]


def test_hot_start_without_full_temperature_fails_at_deadline(tmp_path):
    eng, ha, dc = house(tmp_path, tank=67.5)
    armed(eng, dc)
    step(eng, dc, T)
    shows(eng, switch="on")
    step(eng, dc, T + 60)
    step(eng, dc, dc.state["run"]["deadline"])
    assert dc.state["last"]["ok"] is False
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – Speicher erreichte nur 67,5 °C, nötig 70 °C"]
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)]


# ---------------------------------------------------------------- Termin während des Ablaufs verlegt
def _run_with_change(tmp_path, ok=True):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    if ok:
        shows(eng, switch="on", tank=55)
    else:
        eng.ems["boiler"]["wwdisinfecting"] = "off"
    step(eng, dc, T + 60)
    eng.cfg.dhw_disinfect_time = dtime(18, 0)  # Termin verlegt, z. B. „Vorschlag übernehmen“
    step(eng, dc, T + 900)
    if ok:
        shows(eng, tank=68)
        step(eng, dc, T + 2400)
        step(eng, dc, T + 2400 + rule.AFTER_S)
        shows(eng, switch="off", tank=66)
        step(eng, dc, T + 2400 + rule.AFTER_S + 60)
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
    assert switches(ha).count(("turn_on", SW)) == 2 and ACT_SKIP not in actions(eng)


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
def test_timeout_on_switch_on_not_arrived_has_reason(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    ha.fail = [asyncio.TimeoutError()]
    assert not step(eng, dc, T)
    assert dc.state["run"]["unclear"] == "Zeitüberlauf" and dc.active()
    step(eng, dc, T + 60)  # Schalter steht aus: nicht angekommen
    assert dc.state["last"]["text"] == "nicht gestartet (Zeitüberlauf) – Schalter aus" and "run" not in dc.state
    assert notes(ha) == ["Desinfektion So 17:30 nicht gelaufen – nicht gestartet (Zeitüberlauf) – Schalter aus"]
    for k in range(1, 20):  # kein neuer Versuch zu diesem Termin
        step(eng, dc, T + k * 600)
    assert switches(ha) == []


def test_timeout_on_switch_on_but_arrived_is_watched_and_switched_off(tmp_path):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    ha.fail = [asyncio.TimeoutError()]
    step(eng, dc, T)
    shows(eng, switch="on", tank=55)  # der Aufruf kam doch an
    step(eng, dc, T + 60)
    assert dc.state["run"]["phase"] == "running"
    assert "das Einschalten meldete „Zeitüberlauf“" in eng.store.control_log(1)[0]["reason"]
    shows(eng, tank=68)
    step(eng, dc, T + 1800)
    step(eng, dc, T + 1800 + rule.AFTER_S)
    assert switches(ha) == [("turn_off", SW)] and dc.state["last"]["ok"] is True
    _off_done(eng, ha, dc, T + 1800 + rule.AFTER_S)
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
    shows(eng, switch="off")
    step(eng, dc, T + 120)
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
    assert switches(ha) == [("turn_on", SW), ("turn_off", SW)] and len(notes(ha)) == 1


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
@pytest.mark.parametrize("phase", ["check", "running", "after", "off", "off_check"])
def test_restart_in_each_phase_no_second_on(tmp_path, phase):
    eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    step(eng, dc, T)
    if phase != "check":
        shows(eng, switch="on", tank=55 if phase == "running" else 68)
        step(eng, dc, T + 60)
    if phase == "off":  # Zustand ohne „off“ (vor dieser Behebung gespeichert)
        dc.state["run"]["phase"] = "off"
    if phase == "off_check":
        step(eng, dc, T + 60 + rule.AFTER_S)
    assert dc.state["run"]["phase"] == phase
    dc._save()
    eng2, ha2, dc2 = wire(Engine(eng.cfg, eng.store, eng.found), carry=eng)
    eng2.states.update(eng.states)
    eng2.ems, eng2.regulator_watch = eng.ems, eng.regulator_watch
    for k in range(1, 200):
        step(eng2, dc2, T + 60 + rule.AFTER_S + k * 120)
        if ("turn_off", SW) in switches(ha2):
            shows(eng2, switch="off")
    assert ("turn_on", SW) not in switches(ha2)
    if phase == "off":
        assert switches(ha2) == [("turn_off", SW)] and "run" not in dc2.state


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
                asyncio.run(ctl.set_disinfect(True, "x"))
        assert ctl.ha.payloads == [], bad
    ctl = Control(RecHA(), eng.store, [], states=eng.states, boiler={"heating": SW, "flow": None, "max": 60},
                  boiler_lock=lambda: None)
    with pytest.raises(PermissionError):
        asyncio.run(ctl.set_heating(True, "x"))
    eng.ems["thermostat"] = {"hc1_seltemp": 20}
    with pytest.raises(PermissionError):
        asyncio.run(dc.control.set_disinfect(False, "x"))


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


def test_flapping_switch_state_no_spam(tmp_path):
    eng, ha, dc, t_off = _success_then_after(tmp_path)
    step(eng, dc, t_off)
    step(eng, dc, t_off + 60)
    assert K_SWITCH in eng.notifier.open_keys()
    for k, s in enumerate(["off", "on", "unavailable", "off", "on", "off"] * 5):
        shows(eng, switch=s)
        step(eng, dc, t_off + 200 + k * 60)
    assert len(notes(ha)) <= 3


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
