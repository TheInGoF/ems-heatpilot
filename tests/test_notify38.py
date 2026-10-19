"""0.38 Benachrichtigungen, Problem-Sensor, Thermostat-Schreibfehler, Abfragen und Einstellungen von Weg W.

Geprüft: Entität → ``notify.send_message`` mit ``entity_id``, Dienst → ``notify.<dienst>``, nur eingestellte Ziele;
je Schlüssel einmal je Ereignis plus „wieder in Ordnung“, höchstens eine Meldung in 6 h; Testnachricht nur an die
gespeicherten Ziele; Problem-Sensor an/aus mit Gründen; Raumsteuerung meldet Schreibfehler ab ``MAX_FAILS``.
"""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from heatpilot import __version__
from heatpilot import notify as notify_mod
from heatpilot.config import Config, validate
from heatpilot.control import Control
from heatpilot.controlloop import MAX_FAILS, RETRY_MIN
from heatpilot.dhw_control import K_FAIL
from heatpilot.ha_client import HAClient
from heatpilot.notify import REPEAT_S, Notifier, available
from heatpilot.publisher import PROBLEM_ENTITY, publish_problem
from heatpilot.store import Store
from heatpilot.web import make_app

from test_control34 import MON, climate, run, step, ts_of
from test_control34 import make as make_loop
from test_dhw_control38 import ENT, TARGETS, RecHA, boiler, make
from test_dhw_control38 import step as dhw_step

ROOT = Path(__file__).resolve().parents[1]


class Sent:
    """Ersatz für Control.notify: merkt (Ziel, Titel, Text)."""

    def __init__(self, fail=()):
        self.items, self.fail = [], set(fail)

    async def notify(self, target, title, message):
        if target in self.fail:
            raise RuntimeError("nicht erreichbar")
        self.items.append((target, title, message))


def notifier(tmp_path, targets=("notify.a",), fail=()):
    sent = Sent(fail)
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Notifier(Store(tmp_path / "n.db"), sent, list(targets)), sent


# ------------------------------------------------------------------ Control.notify
def test_entity_uses_send_message_service_is_called_directly_others_rejected(tmp_path):
    ha = RecHA()
    states = {"notify.handy": {"state": "unknown", "attributes": {}}}
    ctl = Control(ha, Store(tmp_path / "c.db"), [], states=states,
                  notify_targets=["notify.handy", "notify.mobile_app_tablet", "sensor.x"])
    run(ctl.notify("notify.handy", "HeatPilot", "Text"))
    run(ctl.notify("notify.mobile_app_tablet", "HeatPilot", "Text"))
    assert ha.payloads == [
        {"type": "call_service", "domain": "notify", "service": "send_message",
         "service_data": {"message": "Text", "title": "HeatPilot"}, "target": {"entity_id": "notify.handy"}},
        {"type": "call_service", "domain": "notify", "service": "mobile_app_tablet",
         "service_data": {"message": "Text", "title": "HeatPilot"}}]
    for target in ("notify.fremd", "sensor.x", "notify.handy; x"):  # nicht eingestellt bzw. keine notify-Entität
        with pytest.raises(PermissionError):
            run(ctl.notify(target, "HeatPilot", "Text"))
    # ältere HA (2024.6/2024.7) kennt den Titel bei send_message nicht: einmal ohne
    ha.fail = [RuntimeError("extra keys not allowed @ data['title']")]
    run(ctl.notify("notify.handy", "HeatPilot", "Text"))
    assert ha.payloads[-1]["service_data"] == {"message": "HeatPilot: Text"} and len(ha.payloads) == 3
    ha.fail = [asyncio.TimeoutError()]  # anderer Fehler: nicht doppelt schicken
    with pytest.raises(asyncio.TimeoutError):
        run(ctl.notify("notify.handy", "HeatPilot", "Text"))
    assert len(ha.payloads) == 3


def test_get_services_and_problem_sensor_guard(tmp_path):
    calls = []

    class HA(HAClient):
        async def call(self, payload, timeout=120):
            calls.append(payload)
            return {"notify": {"mobile_app_handy": {}}}

    assert run(HA("http://x", "t").get_services()) == {"notify": {"mobile_app_handy": {}}}
    assert calls == [{"type": "get_services"}]
    ha = HAClient("http://x", "t")
    for entity in ("binary_sensor.other", "number.boiler_dhw_seltemp", "notify.handy"):
        with pytest.raises(PermissionError):
            run(ha.set_state(entity, "on", {}))
    rec = RecHA()
    run(publish_problem(rec, {"reasons": ["A", "B"]}))
    run(publish_problem(rec, {"reasons": []}))
    (e1, s1, a1), (e2, s2, a2) = rec.set
    assert e1 == e2 == PROBLEM_ENTITY == "binary_sensor.heatpilot_problem"
    assert (s1, a1["reasons"], a1["device_class"]) == ("on", ["A", "B"], "problem") and (s2, a2["reasons"]) == ("off", [])


# ------------------------------------------------------------------ Entprellung
def test_once_per_event_ok_once_and_six_hours(tmp_path):
    n, sent = notifier(tmp_path, targets=("notify.a", "notify.b"))
    t = 1_800_000_000
    assert n.problem("k", "kaputt", t) is True
    assert n.problem("k", "immer noch kaputt", t + 300) is False  # dasselbe Ereignis: nur Text aufgefrischt
    assert n.problem_state() == {"on": True, "reasons": ["immer noch kaputt"]}
    run(n.flush())
    assert sent.items == [("notify.a", "HeatPilot", "kaputt"), ("notify.b", "HeatPilot", "kaputt")]
    assert n.solved("k", "wieder in Ordnung", t + 600) is True
    assert n.solved("k", "wieder in Ordnung", t + 700) is False   # schon zu
    run(n.flush())
    assert [x[2] for x in sent.items[2:]] == ["wieder in Ordnung"] * 2
    assert n.problem("k", "kaputt", t + 3600) is False             # innerhalb 6 h: keine zweite Meldung …
    assert n.problem_state()["on"] is True                         # … aber der Sensor zeigt es
    assert n.solved("k", "wieder in Ordnung", t + 4000) is False   # nicht gemeldet → kein „wieder in Ordnung“
    assert n.problem("k", "kaputt", t + REPEAT_S + 1) is True      # nach 6 h wieder
    assert n.solved("k", "ok", t + REPEAT_S + 60, quiet=True) is False  # still geschlossen
    assert n.problem_state() == {"on": False, "reasons": []}
    # der Stand überlebt einen Neustart
    n2 = Notifier(n.store, sent, ["notify.a"])
    assert n2.state["k"]["sent"] == t + REPEAT_S + 1


def test_without_targets_only_problem_sensor_and_errors_per_target(tmp_path):
    n, sent = notifier(tmp_path, targets=())
    assert n.problem("k", "kaputt") is False and n.queue == [] and n.problem_state()["reasons"] == ["kaputt"]
    assert run(n.test()) == {"ok": False, "sent": [],
                             "errors": ["Keine Ziele gespeichert – erst wählen und speichern"]}
    n2, sent2 = notifier(tmp_path / "b", targets=("notify.a", "notify.b"), fail={"notify.b"})
    res = run(n2.test())
    assert res["ok"] is False and res["sent"] == ["notify.a"] and res["errors"] == ["notify.b: nicht erreichbar"]
    assert sent2.items == [("notify.a", "HeatPilot", notify_mod.TEST_TEXT)] and n2.as_dict()["last"]["key"] == "test"
    n2.settle("x", {"x1": "eins"})
    n2.problem("x1", "eins")
    n2.problem("x2", "zwei")
    n2.settle("x", {"x1": "eins, neu"})
    assert n2.open_keys("x") == {"x1": "eins, neu"}


def test_available_lists_entities_and_services(tmp_path):
    states = {"notify.handy": {"attributes": {"friendly_name": "Handy"}}, "sensor.x": {}}
    services = {"notify": {"send_message": {}, "mobile_app_tablet": {}, "persistent_notification": {}},
                "light": {"turn_on": {}}}
    got = available(states, services, ["notify.handy", "notify.alt"])
    assert got == [
        {"id": "notify.handy", "name": "Handy", "kind": "entity", "selected": True},
        {"id": "notify.mobile_app_tablet", "name": "mobile app tablet", "kind": "service", "selected": False},
        {"id": "notify.persistent_notification", "name": "persistent notification", "kind": "service",
         "selected": False},
        {"id": "notify.alt", "name": "notify.alt", "kind": "fehlt", "selected": True}]
    assert available(states, None) == [{"id": "notify.handy", "name": "Handy", "kind": "entity", "selected": False}]
    assert notify_mod.kind_of("notify.handy", states) == "entity" and notify_mod.kind_of("notify.x", states) == "service"


# ------------------------------------------------------------------ Raumsteuerung: Thermostat-Schreibfehler
def test_thermostat_write_failures_reported_from_max_fails(tmp_path):
    eng, ha, ctl, loop = make_loop(tmp_path, fail={"climate.wz"})
    n, sent = notifier(tmp_path / "n")
    eng.notifier = n
    climate(eng, "climate.wz", 20.0, 19.0)
    t0 = ts_of(MON, 12)
    for i in range(MAX_FAILS):
        step(eng, loop, t0 + i * (RETRY_MIN + 1) * 60)
        run(n.flush())
        assert bool(sent.items) == (i + 1 >= MAX_FAILS)
    assert sent.items == [("notify.a", "HeatPilot", f"Wz: {MAX_FAILS} Schreibfehler in Folge – Thermostat prüfen")]
    assert n.problem_state()["reasons"] == [f"Wz: {MAX_FAILS} Schreibfehler in Folge – Thermostat prüfen"]
    ha.fail.clear()
    step(eng, loop, t0 + MAX_FAILS * (RETRY_MIN + 1) * 60)
    run(n.flush())
    assert sent.items[-1][2] == "Wz: Thermostat nimmt das Soll wieder an" and not n.problem_state()["on"]
    # Schalter zurück auf Beobachten: offene Meldungen still schließen
    n.problem("room_write_failed:climate.wz", "x")
    eng.cfg.control_mode = "beobachten"
    step(eng, loop, t0 + 2 * 3600)
    assert n.open_keys() == {}


# ------------------------------------------------------------------ Abfragen
def fake_app(eng, ha, services=None):
    connected = asyncio.Event()
    connected.set()

    async def get_services():
        if services is None:
            raise RuntimeError("alte HA")
        return services

    app = SimpleNamespace(eng=eng, cfg=eng.cfg, control=eng.dhw_control.control, status="läuft", configured=True,
                          ha=SimpleNamespace(connected=connected, get_services=get_services,
                                             info=lambda: {"connected": True, "token_present": True, "last_error": ""}))

    async def none():
        return {}

    async def registry():
        return []

    app.refresh_rooms, app.registry, app.suggestions = none, registry, none
    return app


def test_api_dhw_resume_and_notify(tmp_path):
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    dc.state["paused"] = {"ts": t, "reason": "3 Fehlschläge in Folge"}
    app = fake_app(eng, ha, services={"notify": {"mobile_app_tablet": {}, "send_message": {}}})

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            j = await (await client.get("/api/dhw")).json()
            assert j["dhw"]["mode"] == "heatpilot" and j["dhw"]["paused"] is True and j["dhw"]["entity"] == ENT
            assert j["readiness"]["level"] == "nicht bereit" and j["dhw"]["rules"]["hold_min"] == 5
            r = await client.post("/api/dhw/resume", json={})
            j = await r.json()
            assert r.status == 200 and j["ok"] and j["was_paused"] and j["dhw"]["paused"] is False
            j = await (await client.get("/api/notify")).json()
            assert j["targets"] == TARGETS and j["services_error"] is None
            assert [(a["id"], a["kind"], a["selected"]) for a in j["available"]] == [
                ("notify.handy", "entity", True), ("notify.mobile_app_tablet", "service", True)]
            r = await client.post("/api/notify/test", json={"targets": ["notify.fremd"]})
            j = await r.json()
            assert r.status == 200 and j["ok"] and j["sent"] == TARGETS  # nur die gespeicherten Ziele
            assert {p["service"] for p in ha.notes()} == {"send_message", "mobile_app_tablet"}
            heat = await (await client.get("/api/heat")).json()
            assert heat["dhw"]["mode"] == "heatpilot" and "dhw" in heat["readiness"]
            s = await (await client.get("/api/settings")).json()
            assert s["settings"]["dhw_control"] == "heatpilot" and s["settings"]["notify_targets"] == TARGETS
            assert s["dhw_control"]["suggested"] == ENT and s["dhw_control"]["modes"] == ["aus", "trocken", "heatpilot"]
            assert s["notify"]["available"][0]["id"] == "notify.handy"
            summ = await (await client.get("/api/summary")).json()
            assert summ["dhw_control"]["status_text"].startswith("HeatPilot setzt")
        finally:
            await client.close()

    asyncio.run(go())
    assert ha.writes() == []  # keine Abfrage schreibt ans Warmwasser-Soll


def test_api_without_targets_and_old_ha(tmp_path):
    eng, ha, dc = make(tmp_path, targets=())
    app = fake_app(eng, ha, services=None)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            r = await client.post("/api/notify/test", json={})
            assert r.status == 400 and "Keine Ziele" in (await r.json())["errors"][0]
            j = await (await client.get("/api/notify")).json()
            assert j["services_error"].startswith("Dienste nicht lesbar") and j["available"][0]["kind"] == "entity"
        finally:
            await client.close()

    asyncio.run(go())
    assert ha.payloads == []


# ------------------------------------------------------------------ Einstellungen
def test_settings_validate_domains_and_defaults(tmp_path):
    cfg = Config()
    assert (cfg.dhw_control, cfg.dhw_set_entity, cfg.notify_targets) == ("aus", None, [])
    raw = cfg.to_raw()
    assert (raw["dhw_control"], raw["dhw_set_entity"], raw["notify_targets"]) == ("aus", "", [])
    assert validate(raw)[1] == []
    clean, errors = validate({"dhw_control": "heatpilot", "dhw_set_entity": "number.kessel_ww",
                              "notify_targets": ["notify.handy", "notify.handy", "notify.mobile_app_x"]})
    assert errors == [] and clean["notify_targets"] == ["notify.handy", "notify.mobile_app_x"]
    for bad, text in (({"dhw_control": "an"}, "dhw_control: erlaubt"),
                      ({"dhw_set_entity": "sensor.boiler_dhw_seltemp"}, "nur number-Entitäten"),
                      ({"notify_targets": ["notify.a", "light.flur"]}, "nur notify-Entitäten: light.flur")):
        assert any(text in e for e in validate(bad)[1]), bad
    odd = Config.from_raw({"dhw_control": "kaputt", "dhw_set_entity": "sensor.x", "notify_targets": ["light.x", 5,
                                                                                                  "notify.ok"]})
    assert (odd.dhw_control, odd.dhw_set_entity, odd.notify_targets) == ("aus", None, ["notify.ok"])
    assert Config.from_raw({"dhw_set_entity": ""}).dhw_set_entity is None
    assert Config(dhw_control="heatpilot").writes_dhw() and not Config(dhw_control="trocken").writes_dhw()


# ------------------------------------------------------------------ Version, Doku
def test_version_changelog_docs():
    assert __version__ == "0.38.0"
    assert re.search(r'^version: "0\.38\.0"$', (ROOT / "ems_heatpilot" / "config.yaml").read_text(), re.MULTILINE)
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert log.split("\n## ")[1].startswith("0.38.0")
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    assert "## Warmwasser über HeatPilot (Weg W)" in docs and "## Benachrichtigungen" in docs
    assert "binary_sensor.heatpilot_problem" in docs and "number.set_value" in docs
    row = next(line for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines() if line.startswith("| 4 |"))
    assert "Warmwasser-Soll gebaut, Desinfektion auslösen folgt mit Weg B" in row and "0.36.3 / 0.38" in row
    plan = (ROOT / "docs" / "planung-vorlauf-ems.md").read_text(encoding="utf-8")
    assert "Weg W" in plan and "gebaut (0.38)" in plan


def test_full_flow_message_texts(tmp_path):
    """Die Texte der Meldungen sind kurz und konkret (Entwurf)."""
    eng, ha, dc = make(tmp_path)
    t = ts_of(MON, 15)
    dhw_step(eng, dc, t, 50)
    dc.verify(t + 60)
    assert eng.notifier.open_keys()[K_FAIL] == ("Warmwasser-Soll 50 °C nicht übernommen (Kessel zeigt 45 °C) – "
                                                "2. Versuch in 5 min")
    boiler(eng, 50)
    dhw_step(eng, dc, t + 300, 50)
    run(eng.notifier.flush())
    assert [p["service_data"]["message"] for p in ha.notes()][-1] == ("Warmwasser-Soll wieder in Ordnung – 50 °C "
                                                                     "übernommen")
