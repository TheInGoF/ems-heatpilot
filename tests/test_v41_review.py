"""Prüfung von 0.41 (Weg B): die nachgestellten Funde – jeder mit dem sicheren Verhalten, das jetzt gilt.

1. Regler am Bus: Scheitert nur die Abfrage ``/api/thermostat``, bleibt gesperrt (unbekannt bzw. eben gesehen); „kein
   Regler“ erst nach mehreren ausdrücklichen „kein Gerät“ in Folge über 5 min und 30 min nach dem letzten Sehen.
2. Kein Pendeln: Schwankt ein Raum an einer Schwelle, wird nur wenige Male je Stunde geschrieben (senken erst nach
   10 min niedrigerem Ziel, Boost zurück erst warm bzw. 10 min nach dem Wunsch, Heizbetrieb aus erst nach 15 min).
3. Fußboden-Grenze greift neu bzw. Kesselwert unklar und darüber: sofort auf die Grenze (Schutz).
4. Heizbetrieb an nur mit bekanntem Vorlauf-Soll bis zur Grenze (oder im selben Schritt geschrieben) – auch in Control.
5. Eingriff des Wächters: erkannt und gemeldet, keine Pause; Änderungen eines Schritts zählen als ein Ereignis.
6. Meldungen: kein Rückfall bei getrennter Verbindung, keiner auf einen selbst gewählten Dienst.
7. Vorlauf-Soll und Warmwasser-Soll dieselbe Entität: nicht speicherbar, beide Schreibwege gesperrt.
8. Grenzen der Entität: Ziel darauf begrenzt; eine Abweisung von Control steht als Hinweis da und wird gemeldet.
9. Kein Ziel rechenbar: nach 15 min Rückfall-Vorlauf und Heizbetrieb an, mit Hinweis und Meldung.
10. Anleitung Wächter: ohne Rückfallwert kein Vorschlag; mit Bedingung „kessel ist heatpilot“.
"""

import asyncio
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from heatpilot import boiler_control, boiler_entity as be, boiler_flow, boiler_rule as br, boiler_status
from heatpilot import boiler_watch, floor_limit, regulator, watchdog
from heatpilot import main as main_mod
from heatpilot.boiler_control import BoilerControl
from heatpilot.boiler_status import ready
from heatpilot.boiler_watch import K_BLIND, K_FOREIGN, K_REJECTED, K_WATCHDOG
from heatpilot.config import validate
from heatpilot.control import Control
from heatpilot.discovery import Room
from heatpilot.emsesp import ABSENT, FAILED, OK, EmsEsp
from heatpilot.ha_client import HAConnectionLost, HAError
from heatpilot.notify import Notifier
from heatpilot.store import Store

from test_boiler41 import FLOW, HEAT, T0, actions, calls, climate, cycle, house, run, shows, step, wz

PKG = Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot"


# ------------------------------------------------------------------ 1. Regler am Bus
class _Resp:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    """EMS-ESP: der Kessel antwortet; die Antwort auf ``/api/thermostat`` bestimmt ``thermostat``."""

    def __init__(self, thermostat):
        self.thermostat = thermostat

    def get(self, url, timeout=None):
        if url.endswith("/api/boiler"):
            return _Resp(200, {"selflowtemp": 30, "heatingactivated": "on"})
        if url.endswith("/api/thermostat"):
            if isinstance(self.thermostat, Exception):
                raise self.thermostat
            return _Resp(*self.thermostat)
        return _Resp(400, {"message": "unknown device"})

    async def close(self):
        pass


def _read(ems, thermostat):
    ems._session = FakeSession(thermostat)
    return asyncio.run(ems.read())


def test_failed_thermostat_query_keeps_the_lock(tmp_path):
    eng, ha, bc = house(tmp_path)
    eng.regulator_watch.clear()  # noch nichts gesehen
    ems = EmsEsp("ems.local")
    eng.set_ems(_read(ems, (200, {"hc1": {"seltemp": 21.0}})), ems.status)
    assert ems.status == {"boiler": OK, "thermostat": OK} and be.regulator(eng)["present"] is True
    assert step(bc, T0) == 0 and calls(ha) == []
    # nächste Abfrage: nur der Regler läuft in einen Zeitüberlauf – der Kessel antwortet frisch
    vals = _read(ems, aiohttp.ServerTimeoutError("timeout"))
    assert "thermostat" not in vals and ems.ok and ems.status["thermostat"] == FAILED
    eng.set_ems(vals, ems.status)
    r = be.regulator(eng)
    assert r["present"] is not False
    assert step(bc, T0 + 30) == 0 and calls(ha) == [] and bc.view["blocked"]
    with pytest.raises(PermissionError, match="Regler"):  # auch die letzte Sperre in Control
        run(bc.control.set_flow(46, "Test"))
    # ohne den gesehenen Regler (z. B. nach 30 min): ebenfalls gesperrt – seit 0.46.5 zählt der stumme Regler bei
    # antwortendem Kessel wie „kein Gerät“, aber eine Abfrage ist noch keine Folge
    eng.regulator_watch.pop("seen")
    r = be.regulator(eng)
    assert r["present"] is None and r["text"].startswith("Der Regler (Gerät thermostat) antwortet EMS-ESP nicht, der "
                                                          "Kessel schon – HeatPilot prüft noch (1 von 3")


def test_answers_of_emsesp_are_told_apart(tmp_path):
    ems = EmsEsp("ems.local")
    for answer, outcome in (((400, {"message": "unknown device"}), ABSENT), ((200, {"error": "no device"}), ABSENT),
                            ((500, {"message": "busy"}), FAILED), ((200, {}), ABSENT), ((404, ValueError()), FAILED),  # 0.46.4: leer = Gerät meldet nichts
                            (asyncio.TimeoutError(), FAILED), (aiohttp.ClientConnectionError("weg"), FAILED),
                            ((200, {"hc1": {"seltemp": 21}}), OK)):
        _read(ems, answer)
        assert ems.status["thermostat"] == outcome, answer


def _note(eng, outcome, ts, boiler=OK):
    eng.ems = {"boiler": {"selflowtemp": 30}, **({"thermostat": {"hc1_seltemp": 21}} if outcome == OK else {})}
    eng.ems_ts = ts
    regulator.note(eng, {"boiler": boiler, "thermostat": outcome}, ts)


def test_no_regulator_only_after_a_streak_and_the_hold(tmp_path):
    eng, ha, bc = house(tmp_path)
    eng.regulator_watch.clear()
    t = 1_000_000.0
    _note(eng, OK, t)  # Regler gesehen, dann abgebaut und EMS-ESP neu gestartet
    for i in range(1, 4):
        _note(eng, ABSENT, t + i * 30)
    assert regulator.regulator(eng, t + 90)["present"] is True  # eben noch gesehen: 30 min gilt er als da
    later = t + regulator.HOLD_S
    _note(eng, ABSENT, later)  # Lücke in den Antworten: die Folge beginnt neu
    r = regulator.regulator(eng, later)
    assert r["present"] is None and "1 von 3" in r["text"]
    for i in range(1, 10):
        _note(eng, ABSENT, later + i * 30)
    assert regulator.regulator(eng, later + 270)["present"] is None  # 3 Abfragen, aber noch keine 5 min
    _note(eng, ABSENT, later + 300)
    assert regulator.regulator(eng, later + 300)["present"] is False
    _note(eng, FAILED, later + 330, boiler=FAILED)  # ein Fehler (auch der Kessel): unbekannt, die Folge beginnt neu
    assert regulator.regulator(eng, later + 330)["present"] is None
    _note(eng, ABSENT, later + 360)
    assert regulator.regulator(eng, later + 360)["present"] is None
    assert eng.store.get(regulator.KV)["last"] == ABSENT  # der Stand überlebt einen Neustart
    # ohne Ausgang je Gerät (ältere Aufrufer): ohne Werte des Reglers nie „kein Regler“
    eng.set_ems({"boiler": {"selflowtemp": 30}})
    assert be.regulator(eng)["present"] is None


# ------------------------------------------------------------------ 2. kein Pendeln
def _flicker(tmp_path, temps, cycling=False, action="heating", minutes=60):
    eng, ha, bc = house(tmp_path)
    if cycling:
        cycle(eng, T0 + minutes * 60, starts=6, run_min=3)
    for i in range(minutes * 2):
        climate(eng, "climate.wz", temps[i % len(temps)], 21.0, hvac_action=action)
        step(bc, T0 + i * 30)
        flows = [c for c in calls(ha) if c[0] == "number"]
        switches = [c for c in calls(ha) if c[0] == "switch"]
        if flows:
            shows(eng, flow=flows[-1][3]["value"])  # der Kessel übernimmt sofort
        if switches:
            shows(eng, heating=switches[-1][1] == "turn_on")
    return eng, ha, bc


def test_flicker_at_thresholds_writes_rarely(tmp_path):
    _, ha, _ = _flicker(tmp_path / "a", [20.7, 20.6])  # Aufheizzuschlag an der Schwelle Soll − 0,3 K
    # anheben sofort, dann bleibt es (kein 41, 46, 41 …); seit 0.50 nach 30 min ohne Anstieg der Zuschlag je 15 min
    assert [c[3]["value"] for c in calls(ha)] == [41, 45, 47, 49]
    _, ha, bc = _flicker(tmp_path / "b", [20.8, 20.79], cycling=True)  # Boost-Schwelle Soll − 0,2 K
    values = [c[3]["value"] for c in calls(ha)]
    assert values == sorted(values) and len(values) <= 6  # nur aufwärts (Boost je 10 min), kein 41, 43, 41 …
    _, ha, _ = _flicker(tmp_path / "c", [21.3, 21.4], action="idle")  # „kühlt ab“ an Soll + 0,3 K
    assert [c for c in calls(ha) if c[0] == "switch"] == []


def test_dry_run_log_stays_calm(tmp_path):
    eng, ha, bc = house(tmp_path, mode="trocken")
    for i in range(120):
        climate(eng, "climate.wz", [20.7, 20.6][i % 2], 21.0, hvac_action="heating")
        step(bc, T0 + i * 30)
    assert calls(ha) == [] and actions(eng).count("boiler_dry") <= 4  # 41, 46 – seit 0.50 dazu zwei Zuschlag-Schritte
    assert all("Zuschlag" in r["reason"] for r in eng.store.control_log(2))  # (kommt eine Stunde nicht hoch)


def test_boost_back_waits_unless_the_room_is_warm():
    b = br.boost_step(None, True, T0, 30, settled=False)
    assert b["k"] == 2 and b["want"] == T0
    later = br.boost_step(b, False, T0 + 30, 30, settled=False)
    assert later["k"] == 2  # nicht mehr wartend, aber nicht warm: erst 10 min nach dem Wunsch zurück
    assert br.boost_step(later, False, T0 + 599, 30, settled=False)["k"] == 2
    assert br.boost_step(later, False, T0 + 600, 30, settled=False)["k"] == 0
    assert br.boost_step(b, False, T0 + 30, 30, settled=True)["k"] == 0  # warm: sofort zurück


def test_raise_at_once_lower_after_ten_minutes():
    assert br.decide_flow(48, 46.0, T0, down_since=T0).write  # anheben: sofort
    d = br.decide_flow(41, 46.0, T0 + 599, down_since=T0)
    assert not d.write and d.until == T0 + 600 and "10 min" in d.why
    assert br.decide_flow(41, 46.0, T0 + 600, down_since=T0).write
    d = br.decide_heating(False, True, T0 + 899, off_since=T0)
    assert not d.write and d.until == T0 + 900
    assert br.decide_heating(False, True, T0 + 900, off_since=T0).write


# ------------------------------------------------------------------ 3. Fußboden-Grenze greift neu, unklarer Wert
def _bath(tmp_path, kind="rtl", **kw):
    eng, ha, bc = house(tmp_path, rooms=[wz(), Room("Bad", "climate.bad", platform="homekit_controller")], **kw)
    eng.cfg.rooms = [{"climate": "climate.bad", "name": "Bad", "floor_type": kind, "floor_max_flow": 40}]
    eng.cfg.floor_heating_rooms = ["climate.bad"]
    bc.control.floors = floor_limit.floor_rooms(eng.cfg, eng.found.rooms)
    return eng, ha, bc


def _kind(eng, bc, kind):
    """Anschluss des Bads umstellen (Einstellungen), wie nach dem Speichern."""
    eng.cfg.rooms[0]["floor_type"] = kind
    bc.control.floors = floor_limit.floor_rooms(eng.cfg, eng.found.rooms)


def test_rtl_cap_applies_between_two_steps_at_once(tmp_path):
    # seit 0.46.5 gilt die RTL-Grenze immer (auch ohne Anforderung) – neu greifen kann sie zwischen zwei Schritten nur
    # noch mit einem anderen Anschluss (hier Mischer → RTL-Ventil); der Schutz wirkt dann sofort wie bisher
    eng, ha, bc = _bath(tmp_path, kind="mischer")
    climate(eng, "climate.bad", 22.0, 21.0, hvac_action="idle")  # Mischer: keine Grenze
    assert step(bc, T0) == 1 and calls(ha)[-1][3] == {"value": 46}
    shows(eng, flow=46)
    _kind(eng, bc, "rtl")  # Bad mit RTL-Ventil: höchstens 40 °C
    assert step(bc, T0 + 30) == 1 and calls(ha)[-1][3] == {"value": 40}  # sofort – die offene Prüfung ist ersetzt
    assert bc.state["flow"]["check"]["value"] == 40 and "Schutz" in bc.view["decision"]["flow"]
    assert "Schutz" in eng.store.query("SELECT reason FROM control_log WHERE action = 'flow_set' "
                                       "ORDER BY rowid DESC LIMIT 1")[0]["reason"]
    assert step(bc, T0 + 60) == 0  # HA zeigt noch 46: die Prüfung des Schutzwerts läuft, nicht jedes Mal neu
    shows(eng, flow=40)
    assert step(bc, T0 + 90) == 0 and bc.state["last_check"]["ok"]


def test_unclear_value_above_cap_is_lowered(tmp_path):
    eng, ha, bc = _bath(tmp_path, kind="mischer")  # seit 0.46.5 gilt RTL immer: die Grenze kommt über den Anschluss
    climate(eng, "climate.bad", 22.0, 21.0, hvac_action="idle")
    assert step(bc, T0) == 1
    shows(eng, flow=46)
    step(bc, T0 + 60)  # Prüfung ok
    _kind(eng, bc, "rtl")  # Grenze 40 gilt jetzt
    eng.ems["boiler"]["selflowtemp"] = 44.0  # EMS-ESP direkt zeigt anderes als Home Assistant (46): unklar
    eng.ems_ts = time.time()
    assert bc.current("flow") is None
    assert step(bc, T0 + 90) == 1 and calls(ha)[-1][3] == {"value": 40}
    # Warmwasser lädt: dann wartet auch der Schutz (das Vorlauf-Soll gehört der Ladung)
    eng2, ha2, bc2 = _bath(tmp_path / "b", kind="direkt")
    shows(eng2, flow=55)
    eng2.ems["boiler"]["dhw_charging"] = "on"
    assert step(bc2, T0) == 0 and "Warmwasser lädt" in bc2.view["decision"]["flow"]


def test_lowered_max_flow_is_enforced_at_once(tmp_path):
    eng, ha, bc = house(tmp_path)
    assert step(bc, T0) == 1
    shows(eng, flow=46)
    step(bc, T0 + 60)
    eng.cfg.max_flow_temp = 42  # in den Einstellungen gesenkt
    bc.control.flow_max = 42
    assert step(bc, T0 + 90) == 1 and calls(ha)[-1][3] == {"value": 42}


# ------------------------------------------------------------------ 4. Heizbetrieb an nur mit passendem Vorlauf
def test_heating_on_waits_for_a_known_flow_within_the_cap(tmp_path):
    eng, ha, bc = _bath(tmp_path, kind="direkt")
    climate(eng, "climate.bad", 20.0, 21.0, hvac_action="heating")
    shows(eng, flow=55, heating=False)  # HA: 55 °C, Heizbetrieb aus
    eng.ems["boiler"].update(selflowtemp=52.0, heatingactivated="off")  # EMS-ESP direkt: 52 °C – unklar
    eng.ems_ts = time.time()
    assert step(bc, T0) == 2
    assert calls(ha) == [("number", "set_value", FLOW, {"value": 40}), ("switch", "turn_on", HEAT, {})]  # erst senken
    # Warmwasser lädt: Vorlauf wartet – dann bleibt auch der Heizbetrieb aus
    eng2, ha2, bc2 = _bath(tmp_path / "b", kind="direkt")
    climate(eng2, "climate.bad", 20.0, 21.0, hvac_action="heating")
    shows(eng2, flow=55, heating=False)
    eng2.ems["boiler"]["dhw_charging"] = "on"
    assert step(bc2, T0) == 0 and calls(ha2) == []
    assert "über der Grenze" in bc2.view["decision"]["heating"] and "bleibt aus" in bc2.view["decision"]["heating"]


def test_heating_on_with_unclear_values_within_the_cap(tmp_path):
    eng, ha, bc = _bath(tmp_path, kind="direkt")
    climate(eng, "climate.bad", 20.0, 21.0, hvac_action="heating")
    shows(eng, flow=38, heating=False)  # HA 38, EMS-ESP 36: unklar, aber beide unter der Grenze 40
    eng.ems["boiler"].update(selflowtemp=36.0, heatingactivated="off")
    eng.ems_ts = time.time()
    assert step(bc, T0) == 1 and calls(ha) == [("switch", "turn_on", HEAT, {})]
    assert "unbekannt" in bc.view["decision"]["flow"]  # geschrieben wird erst, wenn der Wert klar ist
    eng2, ha2, bc2 = _bath(tmp_path / "b", kind="direkt")  # gar kein Wert: der Heizbetrieb bleibt aus
    climate(eng2, "climate.bad", 20.0, 21.0, hvac_action="heating")
    eng2.states[FLOW]["state"] = "unknown"
    shows(eng2, heating=False)
    bc2.plan(T0)
    assert "unbekannt – Heizbetrieb bleibt aus" in bc2.view["decision"]["heating"] and not bc2.pending


def test_control_refuses_heating_on_above_the_cap(tmp_path):
    eng, ha, bc = _bath(tmp_path, kind="direkt")
    shows(eng, flow=55, heating=False)
    with pytest.raises(PermissionError, match="über 40 °C"):
        run(bc.control.set_heating(True, "Test"))
    run(bc.control.set_heating(False, "Test"))  # aus geht immer
    run(bc.control.set_flow(40, "Test"))  # gerade bis zur Grenze geschrieben: an geht, auch wenn HA noch 55 zeigt
    run(bc.control.set_heating(True, "Test"))
    eng.states[FLOW]["state"] = "unavailable"
    bc.control._flow_sent = None
    with pytest.raises(PermissionError, match="unbekannt"):
        run(bc.control.set_heating(True, "Test"))


def test_heating_off_at_once_when_flow_above_cap_without_demand(tmp_path):
    eng, ha, bc = _bath(tmp_path, kind="direkt")
    eng.cfg.boiler_idle = "aus"  # „Heizbetrieb ganz aus“ (bis 0.46.4 immer)
    climate(eng, "climate.wz", 21.6, 21.0, hvac_action="idle")  # niemand braucht Wärme
    climate(eng, "climate.bad", 22.0, 21.0, hvac_action="idle")
    shows(eng, flow=55, heating=True)  # z. B. vom Regler übrig: über der Grenze des direkt angeschlossenen Bodens
    assert step(bc, T0) == 1 and calls(ha) == [("switch", "turn_off", HEAT, {})]  # Schutz: nicht erst nach 15 min
    assert "Schutz" in bc.view["decision"]["heating"]
    # seit 0.46.5 Standard „Pumpe läuft weiter“: der Schutz senkt sofort auf 20 °C, der Heizbetrieb bleibt an
    eng, ha, bc = _bath(tmp_path / "pumpe", kind="direkt")
    climate(eng, "climate.wz", 21.6, 21.0, hvac_action="idle")
    climate(eng, "climate.bad", 22.0, 21.0, hvac_action="idle")
    shows(eng, flow=55, heating=True)
    assert step(bc, T0) == 1 and calls(ha) == [("number", "set_value", FLOW, {"value": 20})]
    assert "Schutz" in bc.view["decision"]["flow"] and bc.view["target"].idle


# ------------------------------------------------------------------ 5. Eingriff des Wächters
def _off_after_15_min(tmp_path):
    eng, ha, bc = house(tmp_path)
    eng.cfg.boiler_idle = "aus"  # seit 0.46.5: Heizbetrieb aus nur bei „Heizbetrieb ganz aus“
    climate(eng, "climate.wz", 21.6, 21.0, hvac_action="idle")  # warm: Heizbetrieb aus (nach 15 min)
    for i in range(31):
        step(bc, T0 + i * 30)
    assert calls(ha) == [("switch", "turn_off", HEAT, {})]
    shows(eng, heating=False)
    step(bc, T0 + 960)  # Prüfung ok, gesehen: aus / 30 °C
    return eng, ha, bc


def test_watchdog_intervention_is_reported_not_paused(tmp_path):
    eng, ha, bc = _off_after_15_min(tmp_path)
    shows(eng, flow=45, heating=True)  # HeatPilot lief nicht; nach 60 min setzt der Wächter 45 °C und an
    again = BoilerControl(eng, bc.control, eng.notifier, {"flow": (FLOW, "erkannt"), "heating": (HEAT, "erkannt")})
    again.plan(T0 + 4 * 3600)
    assert not again.paused() and not again.state.get("foreign") and again.state["watchdog_hit"]["gap_min"] >= 60
    assert K_WATCHDOG in eng.notifier.open_keys() and K_FOREIGN not in eng.notifier.open_keys()
    assert any("Wächter in Home Assistant hat" in w for w in again.as_dict(T0 + 4 * 3600)["warnings"])
    rows = eng.store.query("SELECT reason FROM control_log WHERE action = 'boiler_foreign'")
    assert [r["reason"] for r in rows][-1].startswith("Der Wächter in Home Assistant hat eingegriffen")


def test_two_changes_in_one_step_are_one_event(tmp_path):
    eng, ha, bc = _off_after_15_min(tmp_path)
    shows(eng, flow=55, heating=True)  # kein Rückfallwert: ein fremder Schreiber – aber ein Ereignis
    bc.plan(T0 + 990)
    assert len(bc.state["foreign"]) == 1 and not bc.paused() and actions(eng).count("boiler_foreign") == 2
    # nach kurzer Pause von HeatPilot (unter 60 min) gilt auch der Rückfallwert als fremd
    eng2, ha2, bc2 = _off_after_15_min(tmp_path / "b")
    shows(eng2, flow=45, heating=True)
    bc2.plan(T0 + 1200)
    assert len(bc2.state["foreign"]) == 1 and not bc2.state.get("watchdog_hit")


# ------------------------------------------------------------------ 6. Meldungen nicht doppelt
class NotifyHA:
    def __init__(self, first_error=None, fail_service="send_message"):
        self.first_error, self.fail_service, self.payloads = first_error, fail_service, []

    async def call(self, payload, timeout=0):
        self.payloads.append(payload)
        if self.first_error is not None and len(self.payloads) == 1:
            raise self.first_error
        if self.first_error is None and payload.get("service") == self.fail_service:
            raise HAError("mobile_app: push failed")

    async def get_services(self):
        return {"notify": {"mobile_app_iphone": {}, "send_message": {}}}


def _notify_ctl(tmp_path, ha, targets=("notify.iphone",)):
    states = {"notify.iphone": {"state": "unknown", "attributes": {"friendly_name": "iPhone"}}}
    return Control(ha, Store(tmp_path / "n.db"), [], states=states, notify_targets=list(targets))


def test_connection_drop_is_not_sent_twice(tmp_path):
    for err in (HAConnectionLost("Verbindung getrennt"), HAError("Verbindung getrennt")):
        ha = NotifyHA(err)
        with pytest.raises(HAError):
            run(_notify_ctl(tmp_path, ha).notify("notify.iphone", "HeatPilot", "Kessel: Prüfung fehlgeschlagen"))
        assert len(ha.payloads) == 1


def test_selected_service_is_not_used_as_fallback(tmp_path):
    ha = NotifyHA()
    targets = ["notify.iphone", "notify.mobile_app_iphone"]
    ctl = _notify_ctl(tmp_path, ha, targets)
    sent, errors = run(Notifier(Store(tmp_path / "k.db"), ctl, targets)._send_all("Kessel: pausiert"))
    assert [p.get("service") for p in ha.payloads] == ["send_message", "mobile_app_iphone"]
    assert sent == ["notify.mobile_app_iphone"] and len(errors) == 1
    ha = NotifyHA()  # nur die Entität gewählt: einmal über den Dienst
    run(_notify_ctl(tmp_path, ha).notify("notify.iphone", "HeatPilot", "x"))
    assert [p.get("service") for p in ha.payloads] == ["send_message", "mobile_app_iphone"]


# ------------------------------------------------------------------ 7. dieselbe Entität
def test_same_entity_for_flow_and_dhw_is_refused(tmp_path):
    e = "number.kessel_soll"
    clean, errors = validate({"flow_set_entity": e, "dhw_set_entity": e})
    assert errors and "zwei verschiedene" in errors[0]
    states = {e: {"state": "50", "attributes": {"unit_of_measurement": "°C", "min": 0, "max": 90}}}
    ha = NotifyHA()
    ctl = Control(ha, Store(tmp_path / "s.db"), [], states=states, dhw_entity=e, dhw_limits=(40.0, 60.0),
                  boiler={"flow": e, "heating": None, "max": 75, "floors": []}, boiler_lock=lambda: None)
    for call in (ctl.set_dhw(50, "Test"), ctl.set_flow(45, "Test")):
        with pytest.raises(PermissionError, match="dieselbe Entität"):
            run(call)
    assert ha.payloads == []
    eng, _, bc = house(tmp_path / "b")
    app = SimpleNamespace(eng=eng, cfg=eng.cfg)  # Speichern: auch gegen das erkannte Vorlauf-Soll
    eng.found.boiler["sel_flow_temp"] = "number.boiler_wunsch"
    eng.states["number.boiler_wunsch"] = {"state": "40", "attributes": {"unit_of_measurement": "°C"}}
    eng.cfg.flow_set_entity = None
    assert be.flow_target(eng.cfg, eng.found, eng.states)[0] == "number.boiler_wunsch"
    errors = run(main_mod.App.apply_settings(app, {"dhw_set_entity": "number.boiler_wunsch"}))
    assert len(errors) == 1 and "dieselbe Entität (number.boiler_wunsch)" in errors[0]
    eng.found.boiler["sel_flow_temp"] = FLOW
    eng.cfg.dhw_set_entity = FLOW  # z. B. von Hand in der Datei: auch gegen das Erkannte
    assert any("dieselbe Entität" in p for p in be.write_problems(eng, FLOW, HEAT))
    assert any("dieselbe Entität" in b for b in ready(eng, T0)["blockers"])


# ------------------------------------------------------------------ 8. Grenzen der Entität, Abweisung
def test_target_respects_entity_max(tmp_path):
    eng, ha, bc = house(tmp_path)
    eng.states[FLOW]["attributes"]["max"] = 40
    shows(eng, heating=False)
    t, _ = boiler_flow.compute(eng, T0, None, bc.bounds())
    assert t.flow == 40 and t.hi == 40 and "Höchstwert der Entität" in t.reason
    assert step(bc, T0) == 2 and calls(ha) == [("number", "set_value", FLOW, {"value": 40}),
                                               ("switch", "turn_on", HEAT, {})]
    assert br.fallback_value(eng.cfg, None, (None, 40)) == 40 and br.flow_limits(eng.cfg, None, (38.5, None))[0] == 39


def test_rejection_is_shown_and_reported(tmp_path, monkeypatch):
    eng, ha, bc = house(tmp_path)

    def refuse(entity_id, data):
        raise PermissionError("Vorlauf-Soll 46 °C außerhalb der Grenzen")

    monkeypatch.setattr(bc.control, "_check_flow", refuse)
    n = sum(step(bc, T0 + i * 30) for i in range(31))
    assert n == 0 and bc.state["rejected"]["what"] == "flow" and bc.state.get("fails", 0) == 0
    d = bc.as_dict(T0 + 900)
    assert any("darf Vorlauf-Soll 46 °C nicht schreiben" in w for w in d["warnings"]) and d["rejected"]
    assert K_REJECTED in eng.notifier.open_keys()
    monkeypatch.undo()
    assert step(bc, T0 + 930) == 1 and not bc.state.get("rejected")
    step(bc, T0 + 1300)
    assert K_REJECTED not in eng.notifier.open_keys()


# ------------------------------------------------------------------ 9. kein Ziel rechenbar
def test_blind_target_falls_back_after_15_minutes(tmp_path):
    eng, ha, bc = _off_after_15_min(tmp_path)
    eng.states["sensor.out"]["state"] = "unavailable"  # Außenfühler fällt aus
    eng.ems["boiler"].pop("outdoortemp", None)
    climate(eng, "climate.wz", 18.0, 21.0, hvac_action="heating")  # der Raum kühlt aus
    t = T0 + 990
    assert step(bc, t) == 0 and bc.view["target"].heating is None
    assert any("nach 15 min" in w for w in bc.as_dict(t)["warnings"])
    n = sum(step(bc, t + i * 30) for i in range(1, 30))
    assert n == 0
    assert step(bc, t + 900) == 2 and calls(ha)[-2:] == [("number", "set_value", FLOW, {"value": 45}),
                                                         ("switch", "turn_on", HEAT, {})]
    assert K_BLIND in eng.notifier.open_keys() and "Rückfall-Vorlauf 45 °C" in bc.as_dict(t + 900)["status_text"]
    shows(eng, flow=45, heating=True)
    assert step(bc, t + 960) == 0 and step(bc, t + 1500) == 0  # einmal geschrieben, dann bleibt es
    eng.states["sensor.out"]["state"] = "5.0"  # wieder da: normal weiter
    step(bc, t + 1530)
    assert not bc.state.get("blind") and K_BLIND not in eng.notifier.open_keys()
    assert bc.view["target"].flow == 68  # 0.50.4: Aufheizen aus dem Modell


# ------------------------------------------------------------------ 10. Anleitung Wächter
def test_guide_without_fallback_and_with_condition(tmp_path):
    g = watchdog.guide(FLOW, HEAT, None, [], cap=30.0)
    assert g["steps"] == [] and g["values"]["flow_value"] is None and "Mischer" in g["warning"]
    g = watchdog.guide(FLOW, HEAT, 45, [])
    assert any("„Bedingung hinzufügen“" in s and "kessel" in s and "heatpilot" in s for s in g["steps"])
    assert g["warning"] is None and g["values"]["condition"]
    eng, ha, bc = _bath(tmp_path, kind="direkt")
    eng.cfg.rooms[0]["floor_max_flow"] = 30  # strengste Fußboden-Grenze unter 35 °C
    assert boiler_status.fallback(eng) is None and bc.as_dict(T0)["watchdog"]["guide"]["warning"]
    eng.watchdog = {"ts": T0, "error": None, "checked": 1,
                    "found": [{"entity_id": "automation.w", "name": "W", "on": True, "sets_flow": True,
                               "checks_mode": False}]}
    assert "Bedingung „kessel ist heatpilot“ fehlt" in watchdog.status(eng, "heatpilot")["text"]


def test_guide_warning_and_condition_in_the_ui(tmp_path):
    from test_ui_v41 import js_run

    g_none, g_ok = watchdog.guide(FLOW, HEAT, None, [], cap=30.0), watchdog.guide(FLOW, HEAT, 45, [])
    r = js_run(tmp_path, f"""out([boilerGuideHtml({json.dumps(g_none)}), boilerGuideHtml({json.dumps(g_ok)})]);""")
    assert 'id="boiler-guide-warn"' in r[0] and "Mischer" in r[0] and "boiler-steps" not in r[0]
    assert "<td>Bedingung</td>" in r[1] and "kessel" in r[1] and 'id="boiler-guide-warn"' not in r[1]


# ------------------------------------------------------------------ Lesbarkeit
def test_new_files_short_with_purpose_and_short_functions():
    for name in ("regulator.py", "boiler_watch.py", "boiler_control.py", "boiler_status.py", "boiler_rule.py",
                 "boiler_flow.py", "boiler_entity.py", "watchdog.py", "control.py", "emsesp.py", "config.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 600 and text.startswith('"""'), name
    for mod in (regulator, boiler_watch, boiler_control, boiler_flow, br, boiler_status, watchdog):
        for _, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ == mod.__name__:
                assert len(inspect.getsource(fn).splitlines()) <= 60, fn.__qualname__
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            for _, fn in inspect.getmembers(cls, inspect.isfunction):
                if fn.__module__ == mod.__name__ and fn.__qualname__.startswith(cls.__name__ + "."):
                    try:
                        lines = len(inspect.getsource(fn).splitlines())
                    except OSError:  # von dataclass erzeugt
                        continue
                    assert lines <= 60, fn.__qualname__
