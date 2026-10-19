"""Funde der Prüfung von 0.36.1 – nachgestellt und behoben.

* Ein einzelner Raum, der sein Soll nicht erreicht (nur der Flur), macht nie „eingeschränkt“ – auch nicht,
  wenn er als Einziger mitmacht oder die anderen noch lernen.
* Ohne Wetter-Entität rechnet der Vorheizplan nicht mit einer alten Vorhersage; nach einem Wechsel der
  Entität gilt die alte nicht weiter, die neue wird sofort abgefragt.
* Hängt EMS-ESP bei den Kesselsteuerungen, steht poll_emsesp nicht 15 × 15 s – kurzes Zeitlimit je
  Abfrage, nach dem ersten Zeitüberlauf bricht die Runde ab. Weiterhin nur GET.
* Schalter von EMS-ESP („on“/„off“, „an“/„aus“, 1/0) stehen als „an“/„aus“ in der Kesseltabelle.
* Der Trend der Kachel „Vorhersage“ zeigt die 0,5 K des Backends, auch ab 2 K.
* CHANGELOG und DOCS nennen die Wochenraster (Farbstufen, Legende).
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from heatpilot import emsesp as emsmod, main as main_mod, readiness
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.emsesp import EmsEsp, control_info
from heatpilot.models import RoomModel
from heatpilot.store import Store

from test_forecast36 import _plan, iso, make as make_forecast
from test_readiness36 import by_name, house, room, weak
from test_ui_v361 import plain, run_js

NOW = int(time.time())
ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ Bereitschaft: ein Raum ohne Soll
def _home(eng):
    eng.states["person.a"] = {"state": "home", "attributes": {"friendly_name": "A"}}


def test_only_flur_managed_and_unreachable_is_ready(tmp_path):
    rooms = [room("Wz", "climate.wz", managed=False), room("Küche", "climate.k", managed=False),
             room("Flur", "climate.f")]
    eng, _, _ = house(tmp_path, rooms, t_out=-5.0, models={"climate.f": weak()}, persons=["person.a"])
    _home(eng)
    res = readiness.assess(eng, NOW)
    assert res["level"] == "bereit" and res["limits"] == []
    assert res["headline"] == ("Bereit – 1 Raum schreibbar, 1 mit belastbarem Modell. "
                               "Flur erreicht sein Soll nicht und wird akzeptiert.")
    assert by_name(res)["Flur"]["state"] == "akzeptiert"


def test_flur_with_learning_rooms_is_not_more_than_half(tmp_path):
    rooms = [room("Wz", "climate.wz"), room("Küche", "climate.k"), room("Flur", "climate.f")]
    eng, _, _ = house(tmp_path, rooms, t_out=-5.0,
                      models={"climate.wz": RoomModel("Wz"), "climate.k": RoomModel("Küche"), "climate.f": weak()},
                      persons=["person.a"])
    _home(eng)
    res = readiness.assess(eng, NOW)
    assert res["level"] == "eingeschränkt"  # wegen der lernenden Räume, nicht wegen des Flurs
    assert not [x for x in res["limits"] if x.startswith("Mehr als die Hälfte")]
    assert "mehr als die Hälfte" not in res["headline"]
    assert "Vorheizen für 2 Räume noch nach Pauschale" in res["headline"]


def test_two_of_three_accepted_is_a_limit_also_with_a_learning_room(tmp_path):
    """Zwei akzeptierte von drei beschreibbaren Räumen (der dritte lernt) sind mehr als die Hälfte."""
    rooms = [room("Wz", "climate.wz"), room("Küche", "climate.k"), room("Flur", "climate.f")]
    eng, _, _ = house(tmp_path, rooms, t_out=-5.0,
                      models={"climate.wz": RoomModel("Wz"), "climate.k": weak(), "climate.f": weak()})
    res = readiness.assess(eng, NOW)
    assert any(x.startswith("Mehr als die Hälfte der Räume kommt nicht auf Soll (Küche und Flur)")
               for x in res["limits"])
    # zwei von vier beschreibbaren sind nicht mehr als die Hälfte
    rooms4 = rooms + [room("Bad", "climate.b")]
    eng4, _, _ = house(tmp_path / "vier", rooms4, t_out=-5.0, models={"climate.k": weak(), "climate.f": weak()})
    res4 = readiness.assess(eng4, NOW)
    assert res4["level"] == "bereit" and res4["counts"]["accepted"] == 2


# ------------------------------------------------------------------ Vorhersage ohne/mit anderer Wetter-Entität
def test_plan_without_weather_entity_uses_current_temperature(tmp_path, monkeypatch):
    eng = make_forecast(tmp_path, weather=None)
    now = time.time()
    eng.forecast = [{"datetime": iso(now + 2 * 3600), "temperature": -9.0}]
    eng.forecast_ts = now
    plan, got = _plan(eng, monkeypatch)
    assert eng.summary()["forecast"] is None
    assert plan["t_out_source"] == "aktuell" and plan["t_out_used"] == 8.0 and got["t_out"] == 8.0
    when = datetime.fromtimestamp(now + 2 * 3600).astimezone()
    assert eng.forecast_point_at(when) is None and eng.outdoor_forecast_at(when) is None


def _app(tmp_path, monkeypatch, weather):
    class HA:
        async def get_states(self):
            return []

    app = main_mod.App.__new__(main_mod.App)
    app.ha, app.store, app.pending, app._lock, app._reset = HA(), Store(tmp_path / "a.db"), {}, asyncio.Lock(), False
    app._registry, app._ems_task, app._status, app.eng = None, None, "", None
    monkeypatch.setattr(main_mod.Config, "load", staticmethod(lambda: Config()))

    async def registry():
        return None

    app.registry = registry
    monkeypatch.setattr(main_mod, "discover",
                        lambda states, reg, cfg: Discovered(rooms=[Room("Wz", "climate.wz")], outdoor=["sensor.out"],
                                                            weather=weather[0]))
    app._restart_emsesp = lambda: None
    return app


def test_build_keeps_forecast_only_for_the_same_weather_entity(tmp_path, monkeypatch):
    weather = ["weather.home"]
    app = _app(tmp_path, monkeypatch, weather)
    point = [{"datetime": iso(time.time() + 3600), "temperature": -9.0}]

    async def go(next_weather):
        await app.build(backfill=False)
        app.eng.forecast, app.eng.forecast_ts = point, time.time()
        weather[0] = next_weather
        await app.build(backfill=False)
        return app.eng

    eng = asyncio.run(go("weather.home"))  # gleiche Entität (z. B. andere Einstellung gespeichert): bleibt
    assert eng.forecast == point and eng.forecast_ts is not None
    for other in (None, "weather.anders"):  # geleert oder gewechselt: verworfen
        weather[0] = "weather.home"
        eng = asyncio.run(go(other))
        assert eng.found.weather == other and eng.forecast == [] and eng.forecast_ts is None
        assert eng.forecast_point_at(datetime.fromtimestamp(time.time() + 3600).astimezone()) is None


def test_run_fetches_at_once_after_weather_entity_changed(monkeypatch):
    """run(): stündlich je Entität – eine neue Entität sofort, auch wenn der letzte Abruf jung ist."""
    calls = []

    def eng(weather, ts=None):
        return SimpleNamespace(found=SimpleNamespace(weather=weather), forecast=[], forecast_ts=ts,
                               tick=lambda: None, control_loop=None, summary=lambda: {},
                               note_changes=lambda s: None)

    class HA:
        async def start(self, cb):
            pass

        async def weather_forecast(self, entity):
            calls.append(entity)
            return [{"datetime": iso(time.time()), "temperature": 1.0}]

    class Stop(Exception):
        pass

    # Ablauf je Takt: A (neu) → B mit jungem Stand → B → ohne Entität → wieder A
    steps = [eng("weather.b", time.time()), None, eng(None), eng("weather.a", time.time())]
    app = SimpleNamespace(ha=HA(), on_state=None, cfg=SimpleNamespace(sample_interval_s=30), eng=eng("weather.a"))

    async def build(backfill=True):
        pass

    async def nothing(*args, **kwargs):
        pass

    real_sleep = asyncio.sleep

    async def sleep(_):
        if not steps:
            raise Stop
        step = steps.pop(0)
        if step is not None:
            app.eng = step
        await real_sleep(0)

    app.build = build
    monkeypatch.setattr(main_mod, "live_loop", nothing)
    monkeypatch.setattr(main_mod, "publish", nothing)
    monkeypatch.setattr(main_mod.asyncio, "sleep", sleep)
    with pytest.raises(Stop):
        asyncio.run(main_mod.run(app))
    assert calls == ["weather.a", "weather.b", "weather.a"]


# ------------------------------------------------------------------ EMS-ESP hängt bei den Kesselsteuerungen
def test_hanging_entities_abort_after_first_timeout_get_only(monkeypatch):
    monkeypatch.setattr(emsmod, "ENTITY_TIMEOUT_S", 0.2)
    methods = []

    async def device(req):
        methods.append(req.method)
        return web.json_response({"curflowtemp": 40.0})

    async def entity(req):
        methods.append(req.method)
        await asyncio.sleep(1)
        return web.json_response({"value": 1})

    async def go():
        app = web.Application()
        app.router.add_get("/api/{device}", device)
        app.router.add_route("*", "/api/boiler/{name:.+}", entity)
        server = TestServer(app)
        await server.start_server()
        ems = EmsEsp(str(server.make_url("")).rstrip("/"))  # eigene Sitzung mit 15 s wie im Betrieb
        try:
            t0 = time.monotonic()
            with pytest.raises(TimeoutError, match="keine Antwort auf boiler/selflowtemp"):
                await ems.controls()
            took = time.monotonic() - t0
            single = await ems.entity("boiler", "selflowtemp")  # einzeln: None statt Ausnahme
        finally:
            await ems.close()
            await server.close()
        return took, single

    took, single = asyncio.run(go())
    assert took < 0.2 * 3  # nicht Zahl der Pfade × Zeitlimit
    assert single is None
    assert set(methods) <= {"GET"} and 1 <= len(methods) <= 2


def test_poll_keeps_previous_controls_after_timeout(caplog):
    class FakeEms:
        ok, reads = False, 0

        async def read(self):
            self.reads += 1
            if self.reads > 2:
                raise asyncio.CancelledError
            self.ok = True
            return {"boiler": {"curflowtemp": 40.0}}

        async def controls(self):
            raise TimeoutError("keine Antwort auf boiler/selflowtemp in 3 s")

        async def close(self):
            pass

    old = {"selflowtemp": {"found": True, "writeable": True}}
    eng = SimpleNamespace(ems_controls=old, ems_controls_ts=123.0, set_ems=lambda v, status=None: None,
                          store=SimpleNamespace(commit=lambda: None))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main_mod.poll_emsesp(FakeEms(), SimpleNamespace(eng=eng), interval_s=0))
    assert eng.ems_controls is old and eng.ems_controls_ts == 123.0
    assert "Kesselsteuerungen nicht lesbar: keine Antwort auf boiler/selflowtemp in 3 s" in caplog.text


# ------------------------------------------------------------------ Schalter als an/aus
def test_switch_values_become_booleans():
    def value(v, kind="boolean"):
        return control_info({"value": v, "type": kind, "writeable": True})["value"]

    assert [value(v) for v in ("on", "off", "an", "aus", "ON", "true", "false", 1, 0, "1", "0", True)] == \
        [True, False, True, False, True, True, False, True, False, True, False, True]
    assert value("halb") == "halb" and value(None) is None  # Unbekanntes bleibt
    assert value(1, "number") == 1 and value("on", "enum") == "on"  # nur Schalter
    assert control_info({"value": "on"})["value"] == "on"  # ohne type (ältere EMS-ESP): unverändert


def test_boiler_table_shows_switches_as_an_aus(tmp_path):
    rows = {"heatingactivated": {"value": "on", "type": "boolean", "writeable": True},
            "wwonetime": {"value": "off", "type": "boolean", "writeable": True},
            "burnminperiod": {"value": 10, "type": "number", "uom": "minutes", "min": 0, "max": 120, "writeable": True}}
    controls = {k: {**control_info(v), "path": k} for k, v in rows.items()}
    eng = SimpleNamespace(cfg=Config(emsesp_host="ems-esp.local"), ems_controls=controls, ems_controls_ts=NOW,
                          ems={"boiler": {}})
    b = readiness.boiler(eng)
    r = run_js(tmp_path, f"out(boilerHtml({__import__('json').dumps(b)}, true));")
    text = plain(r)
    assert "Heizbetrieb heatingactivated an" in text and "Warmwasser-Einmalladung wwonetime aus" in text
    assert "Antipendelzeit burnminperiod 10 min" in text
    assert " on " not in f" {text} " and " off " not in f" {text} "


# ------------------------------------------------------------------ Trend auf 0,5 K
def test_forecast_trend_keeps_half_kelvin(tmp_path):
    r = run_js(tmp_path, """out([-6.5, -2.5, 2.5, 3.5, -3, 0, -0.5].map((t) => forecastTile({ age_min: 20, points: 48,
      hours: 47, next12: { min: -2.2, min_at: "2026-10-19T23:00:00+02:00", max: 7.4 }, trend_k: t }, "weather.home")));""")
    trends = [plain(h).split("Trend ")[1].split(" ·")[0] for h in r]
    assert trends == ["−6,5 K", "−2,5 K", "+2,5 K", "+3,5 K", "−3 K", "±0 K", "−0,5 K"]
    # kelvin() für die Empfehlungen bleibt, wie es ist (ab 2 K ganze Kelvin)
    assert run_js(tmp_path, "out(kelvin(-6.5));") == "−7 K"


# ------------------------------------------------------------------ Doku: Wochenraster
def test_changelog_and_docs_name_week_grids():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("## 0.36.0")[0]
    assert "**Wochenraster**" in log and "Legende" in log and "Dunkelmodus" in log and "Rahmen" in log
    docs = (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
    week = docs.split("## Die Woche im Blick")[1].split("## ")[0]
    assert "Legende" in week and "sieben gleichmäßige Stufen" in week
    assert "die Legende darunter nennt sie" in docs.split("### Anwesenheit")[1].split("## ")[0]
