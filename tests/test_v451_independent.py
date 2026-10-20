"""0.45.1: Alte Integration bzw. alte Thermostate löschen, ohne dass HeatPilot etwas verliert.

Erfundener Fall wie in ``test_room_stats45``: „Wohnen“ wechselte von einer Tado-Cloud-Zone (``climate.alt_wohnen``) auf
ein HomeKit-Thermostat (``climate.hk_wohnen``, nur ``hvac_action``). Danach wird die alte Integration gelöscht – ihre
Entitäten, Geräte und Statistiken sind weg. Geprüft:

* Eingelesenes bleibt: ``room_stats``, ``room_samples``, ``hourly``, Alias samt Prüfung; erneutes Wiederherstellen
  ohne Quelle löscht nichts;
* typische Ventilöffnung beim Heizen wird gelernt und ersetzt 100 % (Wärmezufuhr des Raummodells), ohne %-Historie
  bleibt es bei 100 %; Ersatzwerte lernt das %-Ventilmodell nicht;
* das Ventilmodell bleibt beim Wechsel der Art (ruht), lernt mit %-Werten weiter, „Ventil neu lernen“ setzt zurück;
* ein gelöschter Sensor zählt als nicht vorhanden (mit Hinweis), die gefundene Heizleistung nur, solange es sie gibt;
* Oberfläche, Version, Changelog, Doku, neue Dateien.
"""

import asyncio
import json
import re
import time
from pathlib import Path

import pytest
import test_room_stats45 as R
from heatpilot import (
    entity_map,
    house_restore,
    room_alias,
    room_heating,
    room_switch,
    sensor_gone,
    valve_learn,
)
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Registry, Room
from heatpilot.engine import Engine
from heatpilot.store import Store
from heatpilot.valve_model import ValveModel
from test_ui_v45 import ROOM_NAMES, js_run, plain

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
KEY, OLD, T_ALIAS, HEAT, FLOW, NOW, OWN = R.KEY, R.OLD, R.T_ALIAS, R.HEAT, R.FLOW, R.NOW, R.OWN
HEATING = {"state": "heat", "attributes": {"current_temperature": 20.0, "temperature": 21.0, "hvac_action": "heating"}}
IDLE = {"state": "heat", "attributes": {"current_temperature": 20.0, "temperature": 21.0, "hvac_action": "idle"}}
# nach dem Löschen: Registry, Zustände und Statistik-Liste ohne die alte Integration
REG_GONE = Registry([e for e in R.REG.entities if e.get("device_id") != "d1"],
                    [d for d in R.REG.devices if d["id"] != "d1"], R.REG.areas)
STATES_GONE = {k: v for k, v in R.STATES.items() if k not in (OLD, T_ALIAS, HEAT)}
STAT_GONE = [m for m in R.STAT_LIST if m["statistic_id"] not in (T_ALIAS, HEAT)]


class GoneHA(R.FakeHA):
    """Wie ``FakeHA``, aber die Statistiken der alten Sensoren sind gelöscht."""

    async def statistics(self, ids, s, e, period="hour"):
        return await super().statistics([i for i in ids if i not in (T_ALIAS, HEAT)], s, e, period)


def counts(st) -> dict:
    q = lambda sql: st.query(sql)[0]["n"]
    return {"room_stats": q("SELECT COUNT(*) AS n FROM room_stats"),
            "valve": q("SELECT COUNT(*) AS n FROM room_stats WHERE valve IS NOT NULL"),
            "room_samples": q("SELECT COUNT(*) AS n FROM room_samples"),
            "hourly": q("SELECT COUNT(*) AS n FROM hourly"), "t_in": q("SELECT COUNT(*) AS n FROM hourly WHERE t_in "
                                                                       "IS NOT NULL")}


def pct_store(path, real: bool = True) -> Store:
    """Drei Tage eigene Raumwerte: beim Heizen echte 30/50 % (Mittel 40 %) bzw. nur 0/100 % (``real=False``)."""
    st = Store(path)
    rows = []
    for i, ts in enumerate(range(OWN, NOW, 300)):
        v = (30.0 if i % 2 else 50.0) if R.heating(ts) else 0.0
        rows.append((ts, KEY, 20.0, 21.0, v if real else (100.0 if v else 0.0), None))
    st.add_room_samples(rows)
    st.commit()
    return st


def homekit(st, **cfg) -> Engine:
    eng = Engine(Config(**cfg), st, Discovered(rooms=[Room("Wohnen", KEY, room_id="r1")]))
    eng.states = {KEY: dict(HEATING)}
    return eng


def reading(eng, state) -> tuple[float, bool]:
    eng.states[KEY] = state
    eng.valve_subst = set()
    return eng.room_reading(eng.found.rooms[0])[3], KEY in eng.valve_subst


# ------------------------------------------------------------------ 1: Eingelesenes bleibt
def test_source_gone_keeps_everything_and_restore_without_source_deletes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: float(NOW + 1800))
    st = R.make_store(tmp_path / "a.db")
    first = R.restore(R.engine(st))
    room_alias.note_checks(st, {T_ALIAS: {"ok": True, "offset": 0.5, "spread": 0.2, "corr": 0.9, "hours": 500}})
    before, alias = counts(st), room_alias.get(st)[KEY]
    assert before["room_stats"] == first["hours"] > 0 and before["valve"] > 0
    eng = R.engine(st)
    eng.states = dict(STATES_GONE)
    entry = asyncio.run(house_restore.restore(GoneHA(), eng, REG_GONE.lookup(), STAT_GONE))
    assert counts(st) == before                                          # nichts gelöscht, nichts „unbekannt“
    assert room_alias.get(st)[KEY]["stat"] == T_ALIAS and room_alias.get(st)[KEY]["check"] == alias["check"]
    (room,) = entry["room_stats"]
    assert room["hours"] == 0 and room["kept"] == before["room_stats"]   # dieser Lauf nichts, Gespeichertes bleibt
    assert {o["from"]: o["why"] for o in entry["old"]}[OLD] == "nicht mehr in Home Assistant"
    again = asyncio.run(room_stats_restore(eng, GoneHA(heat=False)))
    assert again["hours"] == 0 and counts(st) == before


def room_stats_restore(eng, ha):
    from heatpilot import room_stats
    return room_stats.restore(ha, eng, REG_GONE.lookup(), STAT_GONE, now=NOW + 1800)


def test_new_run_only_fills_gaps_in_stored_hours(tmp_path):
    st = R.make_store(tmp_path / "b.db")
    R.restore(R.engine(st), R.FakeHA(heat=False))                       # erst ohne Heizleistung
    assert counts(st)["valve"] == 0
    st.db.execute("UPDATE room_stats SET temp = 30 WHERE ts = ?", (R.START,))  # gespeicherter Wert gewinnt
    R.restore(R.engine(st))                                              # dann mit: nur die Lücke kommt dazu
    assert counts(st)["valve"] == counts(st)["room_stats"]
    assert st.query("SELECT temp FROM room_stats WHERE ts = ?", (R.START,))[0]["temp"] == 30


def test_alias_check_stays_when_source_delivers_nothing(tmp_path):
    st = Store(tmp_path / "c.db")
    room_alias.put(st, KEY, T_ALIAS, OLD, "device")
    room_alias.note_checks(st, {T_ALIAS: {"ok": True, "offset": 0.5, "hours": 400}})
    room_alias.note_checks(st, {T_ALIAS: {"ok": False, "offset": None, "hours": 0, "why": "nur 0 gemeinsame Stunden"}})
    assert room_alias.get(st)[KEY]["check"]["ok"] is True                # keine Werte mehr ≠ passt nicht
    room_alias.note_checks(st, {T_ALIAS: {"ok": False, "offset": 4.0, "hours": 300, "why": "liegt daneben"}})
    assert room_alias.get(st)[KEY]["check"]["ok"] is False               # echte neue Prüfung gilt weiter


# ------------------------------------------------------------------ 2: typische Ventilöffnung






def test_substitutes_are_not_learned_by_pct_model_but_by_on_off_model(tmp_path):
    st = pct_store(tmp_path / "i.db")
    ts = NOW                                                             # Ersatzwert (typische Öffnung) nach echten
    st.add_room_samples([(ts, KEY, 20.0, 21.0, 40.0, None)])
    st.add_valve_subst(ts, [KEY])
    pct_rows = {r.ts: r.valve for r in valve_learn.fetch(st, KEY, ts - 300, ts + 1)}
    on_off = {r.ts: r.valve for r in valve_learn.fetch(st, KEY, ts - 300, ts + 1, binary=True)}
    assert pct_rows[ts] is None and on_off[ts] == 100.0                  # %-Modell: nicht; an/aus: „an“
    assert pct_rows[ts - 300] == on_off[ts - 300] in (30.0, 50.0)        # echte Werte unverändert


# ------------------------------------------------------------------ 3: Ventilmodell bleibt
def learned_pct_model() -> ValveModel:
    m = ValveModel()
    m.valve_n, m.inter_n, m.steady_h = 600.0, 400.0, 60.0
    m.hist[10][8] = 30.0
    m.learned_until = m.decay_ts = NOW
    return m


def rows(lo, hi, valve):
    return [valve_learn.Row(ts, 20.0, 21.0, valve, True) for ts in range(lo - valve_learn.LAG_S, hi, 300)]


def test_valve_model_kept_on_kind_change_rests_and_learns_on_when_pct_returns():
    saved = learned_pct_model().to_dict()
    rest = ValveModel.from_dict(saved, binary_dev=True)                  # Ventilsensor gelöscht: nur an/aus
    assert rest.no_pct and not rest.binary_dev and rest.kind == "stetig" and rest.to_dict() == saved
    s = rest.summary(0.5, "gerät", NOW)
    assert s["state"] == "ruht" and not s["reliable"] and s["no_pct"] and "ruht (nur an/aus gemeldet" in s["text"]
    hi = NOW + 86400
    valve_learn.learn_span(rest, rows(NOW, hi, None), NOW, hi)          # nur Ersatzwerte: lernt nichts, vergisst nichts
    assert rest.valve_n == 600.0 and rest.steady_h == 60.0 and rest.learned_until == hi
    back = ValveModel.from_dict(rest.to_dict(), binary_dev=False)        # %-Werte kommen zurück
    assert not back.no_pct
    valve_learn.learn_span(back, rows(hi, hi + 3600, 40.0), hi, hi + 3600)
    assert back.valve_n > 600.0                                          # lernt weiter
    on_off = ValveModel(binary_dev=True).to_dict()
    assert ValveModel.from_dict(on_off, binary_dev=False).binary_dev is False  # an/aus → %: lernt stetig weiter


def test_relearn_keeps_resting_model_but_relearn_valve_resets(tmp_path):
    st = pct_store(tmp_path / "j.db")
    st.put(f"model.valve.{KEY}", learned_pct_model().to_dict())
    room = Room("Wohnen", KEY, room_id="r1")
    assert valve_learn.resting(st, [room]) == [KEY]
    assert valve_learn.resting(st, [Room("Wohnen", KEY, heating_power="sensor.ventil")]) == []
    house_restore.prepare(st, [KEY], valve_learn.resting(st, [room]))   # „Hausdaten wiederherstellen“ lernt neu
    assert st.get(f"model.valve.{KEY}")["valve_n"] == 600.0 and st.get(f"model.room.{KEY}") is None
    eng = homekit(st)
    assert eng.valves[KEY].no_pct and eng.valves[KEY].valve_n == 600.0

    class App:
        def __init__(self):
            self.eng, self._lock, self.status = eng, asyncio.Lock(), "läuft"

    asyncio.run(room_switch.relearn_valve(App(), "r1"))                  # nur ausdrücklich: von vorn
    assert eng.valves[KEY].valve_n == 0 and eng.valves[KEY].binary_dev and not eng.valves[KEY].no_pct


# ------------------------------------------------------------------ 4: gelöschte Sensoren
def test_missing_sensor_counts_as_absent_with_hint(tmp_path):
    room = Room("Wohnen", KEY, heating_power="sensor.weg_ventil", humidity="sensor.weg_feuchte",
                temp_sensor="sensor.weg_temp", room_id="r1",
                members=[{"climate": "climate.hk_wohnen2", "heating_power": "sensor.weg_ventil2"}])
    f = Discovered(rooms=[room])
    reg = Registry([{"entity_id": KEY, "platform": "homekit_controller"}])
    states = {KEY: HEATING, "climate.hk_wohnen2": HEATING}
    gone = sensor_gone.drop(f, states, reg.lookup())
    assert gone == {KEY: {"heating_power": "sensor.weg_ventil", "humidity": "sensor.weg_feuchte"},
                    "climate.hk_wohnen2": {"heating_power": "sensor.weg_ventil2"}} == f.sensors_gone
    assert room.heating_power is None and room.humidity is None and room.members[0]["heating_power"] is None
    assert room.temp_sensor == "sensor.weg_temp"                          # nur Langzeitstatistik: bleibt am Raum
    assert valve_learn.binary_device(room)                                # zählt wie kein Sensor
    eng = Engine(Config(), Store(tmp_path / "k.db"), f)
    eng.states = dict(states)
    assert reading(eng, HEATING) == (100.0, False)                       # kein Ersatzwert-Dauerzustand
    eng.on_state("sensor.weg_ventil2", {"state": "20"})                  # meldet sich doch (später geladen)
    assert room.members[0]["heating_power"] == "sensor.weg_ventil2" and reading(eng, HEATING) == (20.0, False)
    assert f.sensors_gone == {KEY: {"heating_power": "sensor.weg_ventil", "humidity": "sensor.weg_feuchte"}}
    untouched = Discovered(rooms=[Room("Wohnen", KEY, heating_power="sensor.weg_ventil")])
    assert sensor_gone.drop(untouched, {}, Registry().lookup()) == {}     # Registry unbekannt: nichts entfernen
    assert untouched.rooms[0].heating_power == "sensor.weg_ventil"


def test_sensor_removed_at_runtime_counts_as_absent_and_returns(tmp_path):
    eng = Engine(Config(), Store(tmp_path / "l.db"), Discovered(rooms=[Room("Wohnen", KEY, heating_power=HEAT)]))
    eng.states = {KEY: dict(HEATING), HEAT: {"state": "35"}}
    assert reading(eng, HEATING) == (35.0, False)
    eng.on_state(HEAT, None)                                              # Integration gelöscht
    assert reading(eng, HEATING) == (100.0, False)
    eng.on_state(HEAT, {"state": "30"})                                   # wieder da
    assert reading(eng, HEATING) == (30.0, False)


class SettingsApp:
    def __init__(self, eng, cfg, reg):
        self.eng, self.cfg, self._reg = eng, cfg, reg

    async def registry(self):
        return self._reg


def test_settings_hint_and_found_sensor_only_while_present(tmp_path):
    cfg = Config(rooms=[{"climate": KEY, "name": "Wohnen", "heating_power": "sensor.weg_ventil",
                         "temperature": "sensor.weg_temp", "humidity": "sensor.da_feuchte"}])
    f = Discovered(rooms=[Room("Wohnen", KEY)])
    f.valve_found = {KEY: {"sensor": HEAT, "how": "adopted", "how_text": "vom alten Thermostat"}}
    eng = Engine(cfg, Store(tmp_path / "m.db"), f)
    eng.states = {KEY: HEATING, HEAT: {"state": "35"}, "sensor.da_feuchte": {"state": "50"}}
    res = asyncio.run(sensor_gone.for_settings(SettingsApp(eng, cfg, R.REG)))
    assert res["sensors_gone"] == {KEY: {"heating_power": "sensor.weg_ventil", "temperature": "sensor.weg_temp"}}
    assert res["valve_found"] == f.valve_found                           # gibt es noch: angeboten
    del eng.states[HEAT]
    assert asyncio.run(sensor_gone.for_settings(SettingsApp(eng, cfg, R.REG)))["valve_found"] == f.valve_found
    assert asyncio.run(sensor_gone.for_settings(SettingsApp(eng, cfg, REG_GONE)))["valve_found"] == {}
    assert room_heating.find(Store(tmp_path / "n.db"), R.found(), REG_GONE.lookup(), STATES_GONE) == {}
    m = entity_map.build(cfg, eng.found, {}, eng.states, REG_GONE.lookup())
    rows_ = {e["key"]: e for g in m["groups"] for e in g["entries"]}
    valve = rows_[f"rooms.{KEY}.heating_power"]
    assert valve["gone"] == "sensor.weg_ventil" and valve["issue"] == "gibt es nicht mehr" and not valve["problem"]
    assert rows_[f"rooms.{KEY}.humidity"]["gone"] is None


# ------------------------------------------------------------------ 5: Oberfläche, Doku, Dateien
def test_ui_room_field_hint_found_hint_and_model_card(tmp_path):
    found = {KEY: {"sensor": HEAT, "how_text": "vom alten Thermostat", "from_name": "Wohnen Zone"}}
    got = js_run(tmp_path, (*ROOM_NAMES, "fmt", "mcNames", "mcValves", "dmValves", "readyDot", "mcDot", "tsDay",
                               "restoreNotes"), f"""
ENT = [{{ id: "{KEY}", name: "VA11" }}];
SET = {{ rooms: [{{ climate: "{KEY}", name: "Wohnen", heating_power: "sensor.weg_ventil", humidity: "sensor.da" }}] }};
SGONE = {{ "{KEY}": {{ heating_power: "sensor.weg_ventil" }} }};
const gone = roomRows();
SET = {{ rooms: [{{ climate: "{KEY}", name: "Wohnen" }}] }}; SGONE = {{}}; VFOUND = {json.dumps(found)};
const vf = roomRows();
const v = {{ text: "0 von 1 belastbar", reliable: 0, total: 1, items: [{{ name: "Wohnen", state: "ruht", reliable: false,
  reason: "nur an/aus gemeldet" }}] }};
out({{ gone, vf, mc: mcValves(v), dm: dmValves(v),
       notes: restoreNotes({{ room_stats: [{{ name: "Wohnen", hours: 0, kept: 648, why: "keine Werte des alten Sensors" }}] }}) }});
""")
    text = plain(got["gone"])
    assert text.count("gibt es in Home Assistant nicht mehr – kann leer bleiben") == 1  # nur beim gelöschten Feld
    assert "nur solange es die alte Integration gibt; ihre Werte bleiben nach dem Löschen in HeatPilot" in plain(got["vf"])
    assert "ruhen (nur an/aus, Gelerntes bleibt): Wohnen" in plain(got["mc"])
    assert "typisch" not in plain(got["dm"]) and "„ruht“" in got["dm"]  # 0.45.2: keine typische Öffnung mehr
    assert "Wohnen (keine Werte des alten Sensors – 648 h von früher bleiben)" in plain(got["notes"])


def test_version_changelog_docs_and_new_files():
    from heatpilot import __version__
    assert __version__ >= "0.45.1"
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = log.split("\n## 0.45.1\n")[1].split("\n## ")[0]
    for word in ("sensor_gone.py", "sensors_gone", "no_pct", "tests/test_v451_independent.py"):
        assert word in head, word
    docs = (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
    sec = docs.split("### Alte Thermostate bzw. Integration entfernen")[1].split("\n### ")[0]
    assert "Hausdaten wiederherstellen" in sec and "Integration löschen" in sec and "typische Öffnung" not in sec
    for name in ("sensor_gone.py", "room_stats.py", "valve_model.py", "engine_sample.py"):
        src = (PKG / name).read_text(encoding="utf-8")
        assert len(src.splitlines()) <= 600 and src.startswith('"""'), name
    for name in ("sensor_gone.py",):
        src = (PKG / name).read_text(encoding="utf-8")
        assert not re.search(r"call_service|set_temperature|\.post\(|\.put\(.*http", src), name
