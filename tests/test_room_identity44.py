"""0.44: Feste Raumkennung, Thermostatwechsel erkennen und entscheiden, HA füllt nur Lücken.

Geprüft: Kennungen in den Einstellungen (Rundlauf, alte Einstellungen ohne Kennung bleiben gültig, Migration beim
ersten Start ohne Datenverlust – auch verwaiste Werte bleiben übernehmbar); beim Speichern bleibt die Kennung beim Raum
(über das Gerät bzw. den Namen) und das bisherige Schlüsselgerät vorn (Ursache 0.43: ein zweites Gerät im Raum, in der
Liste davor, wurde Schlüssel); Auto-Modus aus Bereich bzw. Name; Wechsel erkennen (erster Start nur säen, HA-Umbenennung
ohne Rückfrage, ohne Daten still, zurück auf dem alten Schlüssel erledigt); die drei Wahlen (gleiches Thermostat,
anderes Thermostat – Ventil lernt ab dem Wechsel –, alles neu); „Ventil neu lernen“; die Abfragen; Lesen aus HA löscht
und überschreibt keine eigenen Raumwerte, Messpunkte, Personenwerte und Brennerereignisse.
"""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web as aw
from aiohttp.test_utils import TestClient, TestServer
from heatpilot import backfill, config, room_adopt, room_identity, room_ids, room_switch, valve_learn, valve_since
from heatpilot import web_switch
from heatpilot.config import Config, validate
from heatpilot.discovery import Discovered, Registry, Room, group_rooms
from heatpilot.engine import Engine
from heatpilot.store import Store

ROOT = Path(__file__).resolve().parents[1]
DAY, STEP = 86400, 300
T0 = int(time.time()) // DAY * DAY - 20 * DAY
OLD, NEW = "climate.wohnzimmer", "climate.hk_va01"


def fill(st, key, d0, d1, temp=20.0, valve=30.0, hum=None) -> list[int]:
    ts = list(range(T0 + int(d0 * DAY), T0 + int(d1 * DAY), STEP))
    st.add_room_samples((t, key, temp, 21.0, valve, hum) for t in ts)
    st.commit()
    return ts


def count(st, key) -> int:
    return st.query("SELECT COUNT(*) AS n FROM room_samples WHERE room = ?", (key,))[0]["n"]


def found_of(*rooms) -> Discovered:
    return Discovered(rooms=list(rooms))


def reg(*entries) -> Registry:
    """[(entity_id, Registry-id, unique_id, device_id)]"""
    return Registry([{"entity_id": e, "id": i, "unique_id": u, "platform": "homekit_controller", "device_id": d}
                     for e, i, u, d in entries])


class FakeApp:
    def __init__(self, eng):
        self.eng, self._lock, self.status = eng, asyncio.Lock(), "läuft"

    async def registry(self):
        return Registry()


def switched(tmp_path, old_days=(0, 10), since_day=12):
    """Raum „Wohnzimmer“ (Kennung r1) war unter OLD, misst seit ``since_day`` unter NEW – Wechsel offen."""
    st = Store(tmp_path / "s.db")
    fill(st, OLD, *old_days, temp=19.0, valve=55.0)
    fill(st, NEW, since_day, 14, temp=21.0, valve=10.0)
    st.put(room_identity.KEY, {"r1": {"key": OLD, "name": "Wohnzimmer"}})
    room = Room("Wohnzimmer", NEW, room_id="r1")
    found = found_of(room)
    room_identity.observe(st, found, Registry().lookup(), T0 + since_day * DAY)
    eng = Engine(Config(), st, found)
    return st, eng


# ------------------------------------------------------------------ Einstellungen: Kennung
def test_config_round_trip_with_id_and_old_settings_without_id():
    clean, errors = validate({"rooms": [{"climate": NEW, "id": "r1abc", "group": "Wohnzimmer"},
                                        {"climate": OLD, "id": "Nicht gültig!"}, {"climate": "climate.bad"}]})
    assert not errors
    assert clean["rooms"][0]["id"] == "r1abc" and "id" not in clean["rooms"][1] and "id" not in clean["rooms"][2]
    cfg = Config.from_raw({"rooms": [{"climate": OLD, "name": "Wohnzimmer"}]})  # Einstellungen vor 0.44
    assert cfg.rooms == [{"climate": OLD, "name": "Wohnzimmer"}]
    assert Config.from_raw(cfg.to_raw()).rooms == cfg.rooms


def test_assign_shares_id_per_room_and_keeps_it_by_device_and_name():
    entries = [{"climate": "climate.a", "group": "Bad"}, {"climate": "climate.b", "group": "Bad"},
               {"climate": "climate.c", "name": "Küche"}]
    out = room_ids.assign(entries)
    assert out[0]["id"] == out[1]["id"] != out[2]["id"]
    assert room_ids.assign(entries) == out  # gleiche Eingabe, gleiche Kennung
    before = [Room("Bad", "climate.a", group="Bad", room_id="r-bad", members=[{"climate": "climate.b"}]),
              Room("Küche", "climate.c", room_id="r-kue")]
    # Küche bekommt ein neues Thermostat (altes abgewählt): gleicher Name → gleiche Kennung; Bad bleibt beim Gerät
    again = room_ids.assign([{"climate": "climate.b", "group": "Bad"}, {"climate": "climate.d", "name": "Kueche"}],
                            before)
    assert [e["id"] for e in again] == ["r-bad", "r-kue"]
    # das alte Küchen-Thermostat ist noch gewählt: der neue Eintrag ist ein eigener, neuer Raum
    both = room_ids.assign([{"climate": "climate.c", "name": "Küche"}, {"climate": "climate.d", "name": "Küche 2"}],
                           before)
    assert both[0]["id"] == "r-kue" and both[1]["id"] not in ("r-kue", "r-bad")
    # mitgeschickte Kennung (Oberfläche) zählt, doppelte nur einmal
    sent = room_ids.assign([{"climate": "climate.x", "id": "r-kue"}, {"climate": "climate.y", "id": "r-kue"}])
    assert sent[0]["id"] == "r-kue" and sent[1]["id"] != "r-kue"


def test_cause_043_second_device_in_room_does_not_take_over_the_key():
    """0.43: „Vorschläge übernehmen“ (auch unter „Entitäten“) hakte die Tado-Cloud-Zone im Raum mit an – sie steht
    alphabetisch vor dem HomeKit-Ventil und wurde ohne Raumregler Schlüssel. Jetzt bleibt das bisherige vorn."""
    entries = [{"climate": "climate.a_tado_wz", "group": "Wohnzimmer"}, {"climate": NEW, "group": "Wohnzimmer"}]
    assert group_rooms(entries, {}, {})[0].climate == "climate.a_tado_wz"  # so bis 0.43
    before = [Room("Wohnzimmer", NEW, group="Wohnzimmer", room_id="r1")]
    out = room_ids.assign(entries, before)
    (room,) = group_rooms(out, {}, {})
    assert room.climate == NEW and [m["climate"] for m in room.members] == ["climate.a_tado_wz"]
    assert {e["id"] for e in out} == {"r1"}
    # ein ausdrücklich gewählter Raumregler gilt (dann meldet room_identity den Wechsel)
    ctl = room_ids.assign([{**entries[0], "controller": True}, entries[1]], before)
    assert group_rooms(ctl, {}, {})[0].climate == "climate.a_tado_wz" and {e["id"] for e in ctl} == {"r1"}
    html = (ROOT / "ems_heatpilot" / "heatpilot" / "static" / "index.html").read_text()
    sug = html.split("function applySuggestions()")[1].split("async function testEms")[0]
    assert "const keep = (SET.rooms || []).length > 0;" in sug and "continue; }" in sug


def test_auto_mode_id_from_area_or_name():
    tado = Room("Wohnzimmer", "climate.wohnzimmer", platform="tado")
    homekit = Room("Wohnzimmer", "climate.hk_va01", area="Wohnzimmer", device_name="VA01")
    f1, f2 = found_of(tado, Room("Bad", "climate.bad")), found_of(homekit, Room("Bad 2", "climate.x", area="Bad"))
    room_ids.label(f1, [])
    room_ids.label(f2, [])
    assert f1.rooms[0].room_id == f2.rooms[0].room_id == "auto:wohnzimmer"
    assert f1.rooms[1].room_id == f2.rooms[1].room_id == "auto:bad"
    twin = found_of(Room("Bad", "climate.p", area="Bad"), Room("Bad Ost", "climate.q", area="Bad"))
    room_ids.label(twin, [])
    assert [r.room_id for r in twin.rooms] == ["auto:bad", "auto:bad-ost"]


# ------------------------------------------------------------------ Migration erster Start
def test_first_start_migrates_ids_seeds_identity_and_loses_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "keine_sicherung")
    raw = {"rooms": [{"climate": NEW, "name": "Wohnzimmer", "group": "Wohnzimmer"},
                     {"climate": "climate.hk_va02", "group": "Wohnzimmer"}], "backfill_days": 30}
    (tmp_path / config.SETTINGS_NAME).write_text(json.dumps(raw))
    st = Store(tmp_path / "m.db")
    fill(st, NEW, 10, 14)
    fill(st, OLD, 0, 10, temp=19.0)                                     # verwaist (Tado-Cloud vor HomeKit)
    st.put(f"model.room.{OLD}", {"room": "Wohnzimmer"})
    before = {k: count(st, k) for k in (NEW, OLD)}
    kv_before = {r["key"]: r["value"] for r in st.query("SELECT key, value FROM kv")}
    cfg = Config.load(tmp_path / "options.json", tmp_path / config.SETTINGS_NAME)
    app = SimpleNamespace(cfg=cfg, configured=True, store=st)
    found = found_of(Room("Wohnzimmer", NEW, group="Wohnzimmer", members=[{"climate": "climate.hk_va02"}]))
    assert room_switch.prepare(app, found, Registry()) == []
    saved = json.loads((tmp_path / config.SETTINGS_NAME).read_text())
    ids = {e["id"] for e in saved["rooms"]}
    assert len(ids) == 1 and found.rooms[0].room_id in ids and saved["backfill_days"] == 30
    assert room_identity.open_switches(st) == [] and st.get(room_identity.KEY)[found.rooms[0].room_id]["key"] == NEW
    assert {k: count(st, k) for k in (NEW, OLD)} == before
    assert all(st.get(k) == json.loads(v) for k, v in kv_before.items())   # nichts umgedeutet
    assert [o["key"] for o in room_adopt.orphans(st, found, {})] == [OLD]  # weiter übernehmbar
    # zweiter Start: nichts mehr zu ergänzen, Datei bleibt
    text = (tmp_path / config.SETTINGS_NAME).read_text()
    app.cfg = Config.load(tmp_path / "options.json", tmp_path / config.SETTINGS_NAME)
    assert room_ids.migrate(app.cfg.rooms) is None
    room_switch.prepare(app, found_of(Room("Wohnzimmer", NEW, group="Wohnzimmer")), Registry())
    assert (tmp_path / config.SETTINGS_NAME).read_text() == text


# ------------------------------------------------------------------ Wechsel erkennen
def test_observe_seeds_detects_and_clears(tmp_path):
    st = Store(tmp_path / "o.db")
    fill(st, OLD, 0, 5)
    idx = Registry().lookup()
    assert room_identity.observe(st, found_of(Room("Wohnzimmer", OLD, room_id="r1")), idx, T0) == []
    assert room_identity.observe(st, found_of(Room("Wohnzimmer", NEW, room_id="r1")), idx, T0 + DAY) == []
    (sw,) = room_identity.open_switches(st)
    assert sw == {"room_id": "r1", "name": "Wohnzimmer", "old_key": OLD, "new_key": NEW, "since": T0 + DAY}
    room_identity.observe(st, found_of(Room("Wohnzimmer", NEW, room_id="r1")), idx, T0 + 2 * DAY)
    assert room_identity.open_switches(st)[0]["since"] == T0 + DAY                # bleibt offen, gleicher Beginn
    assert st.get(room_identity.KEY)["r1"]["key"] == OLD                          # gemerkt bis zur Wahl
    room_identity.observe(st, found_of(Room("Wohnzimmer", OLD, room_id="r1")), idx, T0 + 3 * DAY)
    assert room_identity.open_switches(st) == []                                  # zurück auf dem alten Schlüssel
    # zum alten Schlüssel nichts gespeichert: nur merken, keine Rückfrage
    for day, bad in ((3, "climate.bad"), (4, "climate.bad2")):
        rooms = (Room("Wohnzimmer", OLD, room_id="r1"), Room("Bad", bad, room_id="r2"))
        room_identity.observe(st, found_of(*rooms), idx, T0 + day * DAY)
    assert room_identity.open_switches(st) == [] and st.get(room_identity.KEY)["r2"]["key"] == "climate.bad2"
    assert st.get(room_identity.LOG)[-1]["what"] == "ohne Daten"


def test_ha_rename_is_followed_without_asking(tmp_path):
    st = Store(tmp_path / "r.db")
    fill(st, OLD, 0, 5, temp=19.0)
    before = reg((OLD, "e1", "va01", "d1")).lookup()
    room_identity.observe(st, found_of(Room("Wohnzimmer", OLD, room_id="r1")), before, T0)
    found = found_of(Room("Wohnzimmer", NEW, room_id="r1"))
    renames = room_identity.observe(st, found, reg((NEW, "e1", "va01", "d1")).lookup(), T0 + DAY)
    assert renames == [{"room_id": "r1", "name": "Wohnzimmer", "old_key": OLD, "new_key": NEW}]
    assert room_identity.open_switches(st) == [] and st.get(room_identity.LOG)[-1]["what"] == "umbenannt"
    eng = Engine(Config(), st, found)
    asyncio.run(room_switch.follow(eng, renames))
    assert count(st, OLD) == 0 and count(st, NEW) == 5 * DAY // STEP
    # gleiches Gerät, alte Entität weg → Umbenennung; alte noch da → Rückfrage
    prev = {"key": OLD, "device_id": "d1"}
    assert room_identity.is_rename(prev, {"device_id": "d1"}, reg((NEW, "e2", "x", "d1")).lookup())
    assert not room_identity.is_rename(prev, {"device_id": "d1"}, reg((NEW, "e2", "x", "d1"), (OLD, "e1", "y", "d1"))
                                       .lookup())


# ------------------------------------------------------------------ Die drei Wahlen
def test_choice_same_moves_everything(tmp_path):
    st, eng = switched(tmp_path)
    st.db.execute("INSERT INTO valve_dry_log(ts, room, rule, action) VALUES(?, ?, 'drosseln', 'senken')",
                  (T0 + DAY, OLD))
    res = asyncio.run(room_switch.resolve(FakeApp(eng), "r1", "same"))
    assert res["rows"] == 10 * DAY // STEP and res["old_key"] == OLD and res["new_key"] == NEW
    assert count(st, OLD) == 0 and count(st, NEW) == 12 * DAY // STEP
    assert st.query("SELECT room FROM valve_dry_log")[0]["room"] == NEW and valve_since.of(st, NEW) == 0
    assert room_identity.open_switches(st) == [] and st.get(room_identity.KEY)["r1"]["key"] == NEW
    assert st.get(room_identity.LOG)[-1]["what"] == "same"


def test_choice_other_keeps_room_values_and_valve_learns_from_switch(tmp_path, monkeypatch):
    st, eng = switched(tmp_path)
    t = T0 + 12 * DAY + 3600  # gleicher Zeitpunkt unter beiden Schlüsseln: neues Ventil ohne Wert
    st.db.execute("UPDATE room_samples SET valve = NULL, humidity = NULL WHERE room = ? AND ts = ?", (NEW, t))
    st.add_room_samples([(t, OLD, 19.0, 21.0, 77.0, 48.0)])
    st.db.execute("INSERT INTO valve_dry_log(ts, room, rule, action) VALUES(?, ?, 'drosseln', 'senken')",
                  (T0 + DAY, OLD))
    st.commit()
    res = asyncio.run(room_switch.resolve(FakeApp(eng), "r1", "other"))
    since = T0 + 12 * DAY
    assert res["rows"] == 10 * DAY // STEP and count(st, OLD) == 0
    row = st.query("SELECT valve, humidity FROM room_samples WHERE room = ? AND ts = ?", (NEW, t))[0]
    assert row["valve"] is None and row["humidity"] == 48.0                       # altes Ventil ergänzt nicht
    assert st.query("SELECT valve FROM room_samples WHERE room = ? AND ts = ?", (NEW, T0))[0]["valve"] == 55.0
    assert st.query("SELECT room FROM valve_dry_log")[0]["room"] == OLD           # Ventil-Protokoll bleibt beim alten
    assert valve_since.of(st, NEW) == since and res["valve_since"]
    seen = []
    monkeypatch.setattr(valve_learn, "learn_span", lambda m, rows, lo, hi: seen.append(lo))
    model = eng.valves[NEW]
    assert model.learned_until is None                                            # Ventilmodell von vorn
    valve_learn.learn_chunk(st, model, NEW, T0 + 14 * DAY)
    assert seen and seen[0] >= since                                              # lernt erst ab dem Wechsel
    assert eng.store.get(room_adopt.KV_LOG)[-1]["valve"] is False


def test_choice_new_moves_nothing_and_old_values_stay_adoptable(tmp_path):
    st, eng = switched(tmp_path)
    res = asyncio.run(room_switch.resolve(FakeApp(eng), "r1", "new"))
    assert res["rows"] == 0 and count(st, OLD) == 10 * DAY // STEP and count(st, NEW) == 2 * DAY // STEP
    assert room_identity.open_switches(st) == [] and st.get(room_identity.KEY)["r1"]["key"] == NEW
    assert [o["key"] for o in room_adopt.orphans(st, eng.found, {})] == [OLD]


def test_choice_errors(tmp_path):
    st, eng = switched(tmp_path)
    app = FakeApp(eng)
    for room_id, choice, word in (("r1", "egal", "Unbekannte Wahl"), ("r9", "same", "kein Thermostatwechsel")):
        with pytest.raises(room_adopt.AdoptError, match=word):
            asyncio.run(room_switch.resolve(app, room_id, choice))

    async def busy():
        async with app._lock:
            await room_switch.resolve(app, "r1", "same")
    with pytest.raises(room_adopt.AdoptBusy):
        asyncio.run(busy())


def test_relearn_valve_keeps_data_and_notes_what_was_forgotten(tmp_path):
    st, eng = switched(tmp_path)
    eng.valves[NEW].valve_n = 123.0
    rows = count(st, NEW)
    t0 = int(time.time())
    res = asyncio.run(room_switch.relearn_valve(FakeApp(eng), "r1"))
    assert res["key"] == NEW and valve_since.of(st, NEW) >= t0 and count(st, NEW) == rows
    assert eng.valves[NEW].valve_n == 0 and st.get(f"model.valve.{NEW}")["valve_n"] == 0
    entry = st.get(room_identity.LOG)[-1]
    assert entry["what"] == "ventil neu" and entry["forgot"]["valve_n"] == 123
    with pytest.raises(room_adopt.AdoptError):
        asyncio.run(room_switch.relearn_valve(FakeApp(eng), "r9"))


def test_switch_api(tmp_path):
    st, eng = switched(tmp_path)
    app = FakeApp(eng)

    async def go():
        web_app = aw.Application()
        for method, path, handler in web_switch.routes(app, aw.json_response):
            web_app.router.add_route(method, path, handler)
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            got = await (await client.get("/api/rooms/switch")).json()
            bad = await client.post("/api/rooms/switch", json={"room_id": "r1", "choice": "x"})
            ok = await (await client.post("/api/rooms/switch", json={"room_id": "r1", "choice": "other"})).json()
            valve = await (await client.post("/api/rooms/valve_relearn", json={"room_id": "r1"})).json()
            after = await (await client.get("/api/rooms/switch")).json()
            return got, bad.status, ok, valve, after
        finally:
            await client.close()

    got, bad, ok, valve, after = asyncio.run(go())
    (sw,) = got["switches"]
    assert sw["room_id"] == "r1" and sw["old_key"] == OLD and sw["new_key"] == NEW and sw["old_rows"] == 2880
    assert sw["old_days"] >= 10 and sw["since"] and set(got["choices"]) == {"same", "other", "new"}
    assert got["rooms"] == [{"room_id": "r1", "key": NEW, "name": "Wohnzimmer", "valve": True, "valve_since": None}]
    assert bad == 400 and ok["ok"] and ok["choice"] == "other" and valve["ok"] and valve["valve_since"]
    assert after["switches"] == [] and after["log"][0]["what"] == "ventil neu" and after["rooms"][0]["valve_since"]


# ------------------------------------------------------------------ HA füllt nur Lücken
class HistHA:
    def __init__(self, series):
        self.series = series

    async def history(self, ids, s, e, attributes=False):
        a, b = s.timestamp(), e.timestamp()
        out = {}
        for eid in ids:
            pts = self.series.get(eid, [])
            got = [p for p in pts if a <= p[0] < b]
            before = [p for p in pts if p[0] < a]
            if before:
                got = [(a, *before[-1][1:])] + got
            if got:
                out[eid] = [{"s": st, "lu": ts, **({"a": at} if attributes else {})} for ts, st, at in got]
        return out


def test_reading_from_ha_keeps_own_rooms_samples_persons_and_burner(tmp_path, monkeypatch):
    end = T0 + 10 * DAY
    monkeypatch.setattr(backfill, "time", SimpleNamespace(time=lambda: end))
    st = Store(tmp_path / "h.db")
    found = Discovered(rooms=[Room("Wohnzimmer", NEW)], persons=["person.a"],
                       boiler={"flow_temp": "sensor.flow", "burner_on": "binary_sensor.brenner"})
    eng = Engine(Config(), st, found)
    own = range(T0 + 5 * DAY, T0 + 8 * DAY, STEP)
    for ts in own:
        st.add_sample(ts, {"flow": 50.0, "burner_on": 1})
    st.add_person_samples([(ts, "person.a", 1, None) for ts in own])
    fill(st, NEW, 5, 8, temp=19.5)
    st.add_burner_event(T0 + 5 * DAY + 600, True)
    st.add_burner_event(T0 + 5 * DAY + 3600, False)
    st.commit()
    ha = HistHA({NEW: [(T0, "heat", {"current_temperature": 21.0, "temperature": 21.0})],
                 "sensor.flow": [(T0, "45", {})], "person.a": [(T0, "not_home", {})],
                 "binary_sensor.brenner": [(T0 + 6 * DAY, "on", {}), (T0 + 6 * DAY + 900, "off", {}),
                                           (T0 + 9 * DAY, "on", {}), (T0 + 9 * DAY + 1800, "off", {})]})
    asyncio.run(backfill.backfill_history(ha, eng, 10))
    flows = {r["ts"]: r["flow"] for r in st.query("SELECT ts, flow FROM samples")}
    assert {flows[t] for t in own} == {50.0} and flows[T0 + 9 * DAY] == 45.0
    temps = {r["ts"]: r["temp"] for r in st.query("SELECT ts, temp FROM room_samples WHERE room = ?", (NEW,))}
    assert {temps[t] for t in own} == {19.5} and temps[T0 + 9 * DAY] == 21.0
    home = {r["ts"]: r["home"] for r in st.query("SELECT ts, home FROM person_samples")}
    assert {home[t] for t in own} == {1} and home[T0 + 9 * DAY] == 0
    events = [(r["ts"], r["state"]) for r in st.query("SELECT ts, state FROM burner_events ORDER BY ts")]
    assert events == [(T0 + 5 * DAY + 600, 1), (T0 + 5 * DAY + 3600, 0), (T0 + 9 * DAY, 1), (T0 + 9 * DAY + 1800, 0)]
