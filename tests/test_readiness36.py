"""0.36.1 „Bereit für die Steuerung?“ und „Kessel über EMS-ESP“ – nur Anzeige, nichts wird geschrieben.

Räume, die ihr Soll nie erreichen, sind kein Hindernis: Sie werden akzeptiert und benannt. Der Kessel-Abschnitt
ändert die Stufe der Raumsteuerung nicht. EMS-ESP wird nur gelesen (GET).
"""

import asyncio
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from heatpilot import main as main_mod, readiness
from heatpilot.config import Config
from heatpilot.controlloop import MAX_FAILS
from heatpilot.discovery import Room
from heatpilot.emsesp import BOILER_CONTROLS, EmsEsp, control_info
from heatpilot.models import RoomModel

from test_control34 import climate, make, slow_room

NOW = int(time.time())


def room(name, climate_id, managed=True, comfort=21.0, **kw):
    return Room(name, climate_id, managed=managed, comfort=comfort, platform="homekit_controller", **kw)


def house(tmp_path, rooms, cfg=None, t_out=5.0, models=None, **found_kw):
    """Engine mit Räumen, Außenfühler und plausiblen Raummodellen (``models``: climate → Modell)."""
    eng, ha, _, loop = make(tmp_path, rooms=rooms, cfg=cfg or Config(), outdoor=["sensor.out"], **found_kw)
    if t_out is not None:
        eng.states["sensor.out"] = {"state": str(t_out), "attributes": {}}
    for r in rooms:
        for c in r.climates:
            climate(eng, c, 20.0, r.comfort, friendly_name=f"{r.name} Thermostat")
        eng.rooms[r.climate] = (models or {}).get(r.climate, slow_room())
    eng.house.rls.updates = 500  # Laufzeit: nicht „jung“
    return eng, ha, loop


def weak():
    """Plausibles Raummodell mit kleinem Heizkörper (kalter Flur): bei −5 °C außen und vollem Vorlauf nur
    ≈ 13,5 °C, bei −12 °C 8 °C; bei 5 °C außen reicht es gerade."""
    return slow_room(a=0.003)


def by_name(res):
    return {r["name"]: r for r in res["rooms"]}


# ------------------------------------------------------------------ Stufen
def test_no_room_managed_not_ready(tmp_path):
    eng, ha, _ = house(tmp_path, [room("Wz", "climate.wz", managed=False)])
    res = readiness.assess(eng, NOW)
    assert res["level"] == "nicht bereit" and res["headline"] == "Nicht bereit – kein Raum macht mit."
    assert res["blockers"][0] == "Kein Raum macht mit – in den Einstellungen → Räume ankreuzen"
    assert by_name(res)["Wz"]["state"] == "nicht dabei" and res["counts"]["managed"] == 0
    assert ha.calls == []  # nur Auswertung


def test_entity_missing_or_unavailable_not_ready(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz")])
    del eng.states["climate.wz"]
    res = readiness.assess(eng, NOW)
    assert res["level"] == "nicht bereit" and "kein Thermostat beschreibbar" in res["headline"]
    wz = by_name(res)["Wz"]
    assert wz["state"] == "nicht erreichbar" and wz["text"] == "fehlt in Home Assistant"
    assert res["counts"] == {"managed": 1, "writable": 0, "models_ok": 0, "unreachable": 1, "accepted": 0,
                             "learning": 0}
    eng.states["climate.wz"] = {"state": "unavailable", "attributes": {}}
    res = readiness.assess(eng, NOW)
    assert res["level"] == "nicht bereit" and by_name(res)["Wz"]["text"] == "nicht verfügbar"
    assert "Wz (nicht verfügbar)" in res["blockers"][0]


def test_write_checks_modes_and_features():
    ok = {"state": "off", "attributes": {"hvac_modes": ["off", "heat"], "supported_features": 17}}
    assert readiness.write_problem(ok) is None  # „aus“ ist beschreibbar (HeatPilot stellt erst auf heizen)
    assert readiness.write_problem({"state": "heat", "attributes": {}}) is None  # ohne Angaben: durchlassen
    assert readiness.write_problem({"state": "auto", "attributes": {"hvac_modes": ["auto", "off"]}}) is None
    assert readiness.write_problem({"state": "heat", "attributes": {"hvac_modes": ["HVACMode.HEAT"]}}) is None
    assert "kann nicht heizen" in readiness.write_problem({"state": "cool", "attributes": {"hvac_modes": ["off", "cool"]}})
    assert readiness.write_problem({"state": "heat", "attributes": {"supported_features": 384}}) == "Soll nicht einstellbar"
    assert readiness.write_problem({"state": "unknown", "attributes": {}}) == "Zustand unbekannt"
    assert readiness.write_problem(None) == "fehlt in Home Assistant"


def test_implausible_model_limited_room_learns(tmp_path):
    eng, _, _ = house(tmp_path, [room("Küche", "climate.k")], models={"climate.k": RoomModel("Küche")},
                      persons=["person.a"])
    eng.states["person.a"] = {"state": "home", "attributes": {"friendly_name": "A"}}
    res = readiness.assess(eng, NOW)
    assert res["level"] == "eingeschränkt"
    assert res["headline"] == "Eingeschränkt bereit – Küche lernt noch (Vorheizen nach Pauschale)."
    k = by_name(res)["Küche"]
    assert k["state"] == "lernt" and k["text"].startswith("lernt noch (erst 0 Lernschritte)") and "pauschal 1 h" in k["text"]
    assert res["counts"]["learning"] == 1 and res["counts"]["models_ok"] == 0 and res["counts"]["writable"] == 1
    # bei „HeatPilot steuert“: „läuft“ statt „wäre bereit“
    eng.cfg.control_mode = "heatpilot"
    res = readiness.assess(eng, NOW)
    assert res["headline"] == "Läuft – eingeschränkt: Küche lernt noch (Vorheizen nach Pauschale)." and res["managing"]
    # mehrere lernende Räume: zusammengefasst
    eng2, _, _ = house(tmp_path / "zwei", [room("Küche", "climate.k"), room("Bad", "climate.b")],
                       models={"climate.k": RoomModel("Küche"), "climate.b": RoomModel("Bad")})
    assert "Vorheizen für 2 Räume noch nach Pauschale" in readiness.assess(eng2, NOW)["headline"]


def test_unreachable_setpoint_is_accepted_not_a_blocker(tmp_path):
    rooms = [room("Wz", "climate.wz"), room("Küche", "climate.k"), room("Flur", "climate.f")]
    eng, _, _ = house(tmp_path, rooms, t_out=-5.0, models={"climate.f": weak()}, persons=["person.a"])
    eng.states["person.a"] = {"state": "home", "attributes": {"friendly_name": "A"}}
    res = readiness.assess(eng, NOW)
    assert res["level"] == "bereit" and res["blockers"] == [] and res["limits"] == []
    assert res["headline"] == ("Bereit – 3 Räume schreibbar, 3 mit belastbarem Modell. "
                               "Flur erreicht sein Soll nicht und wird akzeptiert.")
    f = by_name(res)["Flur"]
    assert f["state"] == "akzeptiert" and "höchstens 13,5 °C bei −5 °C außen, 8 °C bei −12 °C" in f["text"]
    assert "wird akzeptiert, kein Hindernis" in f["text"]  # zweiter Wert: bei Auslegungstemperatur
    assert any(n.startswith("Flur erreicht sein Soll nicht") for n in res["notes"])
    assert by_name(res)["Wz"]["state"] == "bereit" and res["counts"]["accepted"] == 1
    # bei „HeatPilot steuert“: „Läuft – …“
    eng.cfg.control_mode = "heatpilot"
    assert readiness.assess(eng, NOW)["headline"].startswith("Läuft – 3 Räume schreibbar")


def test_more_than_half_unreachable_limits(tmp_path):
    rooms = [room("Wz", "climate.wz"), room("Küche", "climate.k"), room("Flur", "climate.f")]
    eng, _, _ = house(tmp_path, rooms, t_out=-5.0, models={"climate.k": weak(), "climate.f": weak()})
    res = readiness.assess(eng, NOW)
    assert res["level"] == "eingeschränkt"
    assert any("Mehr als die Hälfte der Räume kommt nicht auf Soll" in x and "Vorlauf/Abgleich" in x
               for x in res["limits"])
    assert "Küche und Flur erreichen ihr Soll nicht und werden akzeptiert." in res["headline"]


def test_design_temperature_as_second_value_only(tmp_path):
    """Schafft der Raum heute sein Soll, aber nicht bei Auslegungstemperatur: bereit, mit Hinweis im Text."""
    eng, _, _ = house(tmp_path, [room("Bad", "climate.b")], models={"climate.b": slow_room(a=0.0035)})
    res = readiness.assess(eng, NOW)
    b = by_name(res)["Bad"]
    assert b["state"] == "bereit" and b["text"] == "beschreibbar, Modell belastbar · bei −12 °C außen höchstens 10,5 °C"
    assert res["level"] == "bereit" and res["counts"]["accepted"] == 0


def test_write_failures_block_room(tmp_path):
    eng, _, loop = house(tmp_path, [room("Wz", "climate.wz"), room("Küche", "climate.k")],
                         cfg=Config(control_mode="heatpilot"))
    loop.state["climate.k"] = {"fails": MAX_FAILS, "last_written": 21.0}
    res = readiness.assess(eng, NOW)
    assert res["level"] == "nicht bereit"
    assert res["blockers"] == [f"Küche: {MAX_FAILS} Schreibfehler in Folge – Thermostat prüfen"]
    assert res["headline"] == f"Nicht bereit – Küche: {MAX_FAILS} Schreibfehler in Folge."
    assert by_name(res)["Küche"]["state"] == "nicht erreichbar" and res["counts"]["unreachable"] == 1
    loop.state["climate.k"]["fails"] = MAX_FAILS - 1
    assert readiness.assess(eng, NOW)["level"] == "bereit"


def test_one_room_not_writable_others_left_is_a_limit(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz"), room("Küche", "climate.k")])
    eng.states["climate.k"] = {"state": "unavailable", "attributes": {}}
    res = readiness.assess(eng, NOW)
    assert res["level"] == "eingeschränkt" and res["limits"][0].startswith("Nicht beschreibbar: Küche (nicht verfügbar)")
    assert "Küche nicht beschreibbar" in res["headline"] and res["counts"]["writable"] == 1


def test_group_with_one_dead_valve(tmp_path):
    wz = room("Wohnzimmer", "climate.v1", group="Wohnzimmer",
              members=[{"climate": "climate.v2", "heating_power": None, "humidity": None, "temp_sensor": None,
                        "platform": "homekit_controller", "preset": None, "preset_max": None}])
    eng, _, _ = house(tmp_path, [wz])
    eng.states["climate.v2"] = {"state": "unavailable", "attributes": {"friendly_name": "Ventil 2"}}
    res = readiness.assess(eng, NOW)  # geschrieben wird auf alle Ventile – ein totes hält die Gruppe auf
    w = by_name(res)["Wohnzimmer"]
    assert w["state"] == "nicht erreichbar" and w["text"] == "Ventil 2: nicht verfügbar"
    assert res["level"] == "nicht bereit" and "Wohnzimmer (Ventil 2: nicht verfügbar)" in res["blockers"][0]
    eng.states["climate.v2"] = {"state": "heat", "attributes": {"hvac_modes": ["off", "heat"]}}
    assert readiness.assess(eng, NOW)["level"] == "bereit"


def test_presence_notes_and_limits(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz")])
    res = readiness.assess(eng, NOW)
    assert any("Ohne Personen gibt es keine Abwesenheits-Absenkung" in n for n in res["notes"])
    assert res["level"] == "bereit"
    eng2, _, _ = house(tmp_path / "p", [room("Wz", "climate.wz")], persons=["person.a"], home_zone=(53.55, 10.0))
    eng2.states["person.a"] = {"state": "unknown", "attributes": {"friendly_name": "Anna", "latitude": 53.9,
                                                                   "longitude": 10.4}}
    res = readiness.assess(eng2, NOW)
    assert res["level"] == "eingeschränkt" and "Anwesenheit unklar" in res["headline"]
    assert any("Tracker ohne Zustand: Anna" in x for x in res["limits"])
    text = json.dumps(res)
    assert "53.9" not in text and "10.4" not in text and "latitude" not in text and "53.55" not in text
    # gespeicherte Messpunkte der letzten Stunde zählen: meist jemand bekannt → nicht unklar
    for i in range(6):
        eng2.store.add_sample(NOW - 3000 + i * 300, {"home_count": 1})
    eng2.store.commit()
    assert readiness.assess(eng2, NOW)["level"] == "bereit"


def test_no_outdoor_temperature_limits(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz")], t_out=None)
    res = readiness.assess(eng, NOW)
    assert res["level"] == "eingeschränkt" and "keine Außentemperatur" in res["headline"]
    assert any("5 °C" in x for x in res["limits"])


def test_night_plan_and_young_models_notes(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz")])
    notes = readiness.assess(eng, NOW)["notes"]
    assert any("ohne Leistungssensoren" in n for n in notes) and not any("jung" in n for n in notes)
    eng._sleep = {"sensors": ["sensor.l1"], "auto": False, "active": False, "nights": 12}
    assert any("nur Vorschlag (12 Nächte)" in n for n in readiness.assess(eng, NOW)["notes"])
    eng._sleep = {"sensors": ["sensor.l1"], "auto": True, "active": True, "nights": 30}
    assert any("gelernt und wirksam (30 Nächte)" in n for n in readiness.assess(eng, NOW)["notes"])
    eng._sleep = {"sensors": ["sensor.l1"], "auto": True, "active": False, "nights": 2}
    assert any("noch nicht belastbar" in n for n in readiness.assess(eng, NOW)["notes"])
    eng.house.rls.updates = 40
    assert "Modelle sind jung (40 h gelernt)" in readiness.assess(eng, NOW)["notes"]


# ------------------------------------------------------------------ Anbindung: Übersicht und GET api/heat
def test_summary_and_heat_state_carry_readiness_in_observe_mode(tmp_path):
    from heatpilot.web import make_app as web_app

    eng, ha, loop = house(tmp_path, [room("Wz", "climate.wz")], persons=["person.a"], home_zone=(53.55, 10.0))
    eng.states["person.a"] = {"state": "home", "attributes": {"latitude": 53.9, "longitude": 10.4,
                                                              "friendly_name": "A"}}
    s = eng.summary()["readiness"]
    assert set(s) == {"level", "headline"} and s["level"] == "bereit"
    connected = asyncio.Event()
    connected.set()
    app = SimpleNamespace(eng=eng, cfg=eng.cfg, control=loop.control, status="läuft", configured=True,
                          ha=SimpleNamespace(connected=connected))

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            j = await (await client.get("/api/heat")).json()
            r = j["readiness"]
            assert j["control"]["mode"] == "beobachten" and r["level"] == "bereit" and not r["managing"]
            assert r["headline"].startswith("Bereit – 1 Raum schreibbar, 1 mit belastbarem Modell.")
            assert r["rooms"][0]["state"] == "bereit" and r["boiler"]["configured"] is False
            text = json.dumps(j)
            assert "53.9" not in text and "latitude" not in text
            j = await (await client.get("/api/summary")).json()
            assert j["readiness"] == {"level": "bereit", "headline": r["headline"]}
        finally:
            await client.close()

    asyncio.run(go())
    assert ha.calls == []  # Beobachten: nichts geschrieben


# ------------------------------------------------------------------ Kessel über EMS-ESP
def _controls(**over):
    """Ergebnis von EmsEsp.controls(): alles gefunden und schreibbar, ``over`` ersetzt Einträge."""
    out = {k: {"found": True, "writeable": True, "value": 1, "min": 0, "max": 90, "uom": "°C", "path": k}
           for k in BOILER_CONTROLS}
    out.update(over)
    return out


def test_boiler_section_texts_and_level_untouched(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz")])
    b = readiness.boiler(eng)
    assert b["configured"] is False and b["polled"] is False and not b["ready"]
    assert b["text"] == "EMS-ESP nicht eingerichtet – ohne EMS-ESP keine Kesselsteuerung"
    assert [c["key"] for c in b["controls"]] == list(BOILER_CONTROLS) and b["controls"][0]["found"] is None
    eng.cfg.emsesp_host = "ems-esp.local"
    assert readiness.boiler(eng)["text"].startswith("noch nicht abgefragt")
    eng.ems_controls, eng.ems_controls_ts = _controls(), time.time()
    b = readiness.boiler(eng)
    assert b["ready"] and b["polled"] and b["polled_at"]
    assert b["text"] == ("Alle 5 nötigen Steuerungen vorhanden und schreibbar – eine Kesselsteuerung wäre möglich "
                         "(nicht gebaut; am Kessel schreibt HeatPilot nur das Warmwasser-Soll, mit „HeatPilot setzt“)")
    first = b["controls"][0]
    assert first == {"key": "selflowtemp", "name": "Vorlauf-Soll", "required": True, "found": True,
                     "writeable": True, "value": 1, "min": 0, "max": 90, "uom": "°C"}
    # Info-Einträge fehlen: bleibt bereit
    eng.ems_controls = _controls(heatingoff={"found": False}, burnminperiod={"found": False},
                                 pumpdelay={"found": True, "writeable": False, "value": 8})
    assert readiness.boiler(eng)["ready"]
    # eine nötige nicht schreibbar, eine fehlt
    eng.ems_controls = _controls(selflowtemp={"found": True, "writeable": False, "value": 7},
                                 wwonetime={"found": False}, wwactivated={"found": True, "writeable": None})
    b = readiness.boiler(eng)
    assert not b["ready"]
    assert b["text"] == ("Es fehlen: Vorlauf-Soll (nicht schreibbar), Warmwasser-Betrieb (schreibbar unbekannt), "
                         "Warmwasser-Einmalladung (nicht gefunden)")
    res = readiness.assess(eng, NOW)
    assert res["level"] == "bereit" and res["boiler"]["ready"] is False  # Tado-Steuerung braucht den Kessel nicht


def test_emsesp_entity_and_controls_get_only():
    methods = []
    objects = {
        "selflowtemp": {"name": "selflowtemp", "fullname": "Gewählte Vorlauftemperatur", "value": 7, "type": "number",
                        "min": 0, "max": 90, "uom": "°C", "readable": True, "writeable": True, "visible": True},
        "dhw/seltemp": {"name": "seltemp", "value": 45, "min": 0, "max": 254, "uom": "°C", "writeable": True},
        "heatingactivated": {"value": "an"},                       # nur der Wert (ältere Versionen)
        "wwactivated": {"message": "unknown command"},            # Meldung statt Entität
        "dhw/activated": {"value": True, "writeable": "true"},
        "burnminperiod": {"value": 25, "uom": "minutes", "writeable": False, "min": 0, "max": 120},
        "pumpdelay": 8,                                            # nackter Wert
        "heatingtemp": {"dhw": {"seltemp": 45}},                   # Unterverzeichnis, keine Entität
    }

    async def handler(req):
        methods.append(req.method)
        name = req.match_info["name"]
        if name not in objects:
            return web.json_response({"message": "not found"}, status=404)
        return web.json_response(objects[name])

    async def go():
        app = web.Application()
        app.router.add_route("*", "/api/boiler/{name:.+}", handler)
        server = TestServer(app)
        await server.start_server()
        ems = EmsEsp(str(server.make_url("")).rstrip("/"))
        try:
            single = await ems.entity("boiler", "selflowtemp")
            missing = await ems.entity("boiler", "gibtsnicht")
            controls = await ems.controls()
        finally:
            await ems.close()
            await server.close()
        return single, missing, controls

    single, missing, c = asyncio.run(go())
    assert single["writeable"] is True and single["max"] == 90 and missing is None
    assert c["selflowtemp"] == {"found": True, "writeable": True, "value": 7, "min": 0, "max": 90, "uom": "°C",
                                "fullname": "Gewählte Vorlauftemperatur", "path": "selflowtemp"}
    assert c["wwseltemp"]["path"] == "dhw/seltemp" and c["wwseltemp"]["value"] == 45  # neuere EMS-ESP: unter dhw
    assert c["heatingactivated"]["found"] and c["heatingactivated"]["writeable"] is None
    assert c["wwactivated"]["path"] == "dhw/activated" and c["wwactivated"]["writeable"] is True
    assert c["wwonetime"] == {"found": False} and c["heatingtemp"] == {"found": False}
    assert c["burnminperiod"]["writeable"] is False and c["burnminperiod"]["uom"] == "minutes"
    assert c["pumpdelay"]["value"] == 8 and c["pumpdelay"]["writeable"] is None
    assert set(methods) == {"GET"} and len(methods) >= len(BOILER_CONTROLS)
    assert control_info(None) == {"found": False}


def test_emsesp_has_no_write_path():
    src = (Path(__file__).parent.parent / "ems_heatpilot" / "heatpilot" / "emsesp.py").read_text()
    assert not re.search(r"\.(post|put|patch|delete|request)\(", src)
    assert "keinen Schreibpfad" in src and "ausschließlich lesen (GET)" in src


def test_poll_reads_controls_at_start_then_hourly_errors_only_logged(caplog):
    class FakeEms:
        def __init__(self, fail=False):
            self.ok, self.reads, self.calls, self.fail, self.closed = False, 0, 0, fail, False

        async def read(self):
            self.reads += 1
            if self.reads > 4:
                raise asyncio.CancelledError
            self.ok = self.reads > 1  # erst ab der zweiten Abfrage erreichbar
            return {"boiler": {"curflowtemp": 40.0}} if self.ok else {}

        async def controls(self):
            self.calls += 1
            if self.fail:
                raise RuntimeError("EMS-ESP weg")
            return _controls()

        async def close(self):
            self.closed = True

    def run(ems, eng):
        app = SimpleNamespace(eng=eng)
        try:
            asyncio.run(main_mod.poll_emsesp(ems, app, interval_s=0))
        except asyncio.CancelledError:
            pass

    eng = SimpleNamespace(ems_controls={}, ems_controls_ts=None, set_ems=lambda v: None,
                          store=SimpleNamespace(commit=lambda: None))
    ems = FakeEms()
    run(ems, eng)
    assert ems.calls == 1 and ems.closed and eng.ems_controls == _controls() and eng.ems_controls_ts
    bad, eng2 = FakeEms(fail=True), SimpleNamespace(ems_controls={}, ems_controls_ts=None, set_ems=lambda v: None,
                                                   store=SimpleNamespace(commit=lambda: None))
    run(bad, eng2)
    assert bad.calls == 1 and eng2.ems_controls == {} and "Kesselsteuerungen nicht lesbar" in caplog.text
