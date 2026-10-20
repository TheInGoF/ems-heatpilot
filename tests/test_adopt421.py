"""0.42.1: Gelerntes übernehmen – Raumdaten von alten Entitäten (z. B. Tado-Cloud) auf neue (z. B. HomeKit).

Geprüft: verwaiste Schlüssel (Messwerte bzw. Modelle ohne Gerät eines aktuellen Raums) mit Name, Tagen und Herkunft;
Vorschläge nur bei sicherem Treffer (gleicher Name, Tado-Zone wie der Bereich, gleicher Bereich; keine bei Doppelten,
ohne Treffer oder zu wenig Daten); die Übernahme in einer Transaktion (Messwerte nur zu Zeitpunkten ohne eigenen
Messpunkt des Raums, Rest des alten gelöscht, Ersatzwerte, Fremdwärme, Ventil-Protokoll, Tagesstände, Hydraulik,
Empfehlungen, Modelle verworfen, Protokoll); danach lernen Raum- und Ventilmodell aus den Messwerten neu (Raum mit
künstlicher Historie danach so plausibel wie vorher unter dem alten Schlüssel); das Neueinlesen der Historie lässt
übernommene ältere Raumwerte und Schlüssel ohne Raum stehen; die Abfragen (nur verwaiste Schlüssel als Quelle, nur
aktuelle Räume als Ziel, nichts an Home Assistant); Version, Changelog, Doku. Nicht auf eine Anlage zugeschnitten:
Tado-Cloud → HomeKit genauso wie ein neu gekoppeltes Zigbee-Thermostat.
"""

import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from heatpilot import backfill, guards, main as main_mod, room_adopt as ra, valve_learn as vl, valve_model as vm, web
from heatpilot.balance import PRESET_CHANGES_KEY
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Registry, Room
from heatpilot.engine import Engine
from heatpilot.store import Store
from sim_valve import Room as SimRoom
from sim_valve import Scenario, Thermostat, simulate

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
DAY = 86400
STEP = 1800
START = int(datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp())
HK = "homekit_controller"


def days_of(ts_list) -> int:
    """Tage mit Messwerten wie ``room_adopt`` (Ortszeit mit dem Versatz von jetzt)."""
    off = time.localtime().tm_gmtoff
    return len({(t + off) // DAY for t in ts_list})


def fill(store, key: str, d0: float, d1: float, temp: float = 20.0, step: int = STEP) -> list[int]:
    """Messwerte eines Schlüssels von Tag ``d0`` bis vor Tag ``d1``."""
    ts = list(range(int(START + d0 * DAY), int(START + d1 * DAY), step))
    store.add_room_samples((t, key, temp, 21.0, 10.0, None) for t in ts)
    store.commit()
    return ts


def registry(entries) -> Registry:
    """``entries``: [(entity_id, Integration, Gerätename, Bereich)] – je Entität ein Gerät."""
    ents, devs, areas = [], [], {}
    for i, (eid, platform, device, area) in enumerate(entries):
        ents.append({"entity_id": eid, "platform": platform, "device_id": f"d{i}"})
        devs.append({"id": f"d{i}", "name": device, **({"area_id": f"a_{area}"} if area else {})})
        if area:
            areas[f"a_{area}"] = area
    return Registry(ents, devs, [{"area_id": a, "name": n} for a, n in areas.items()])


def climate(friendly: str) -> dict:
    return {"state": "heat", "attributes": {"friendly_name": friendly, "current_temperature": 20.0, "temperature": 21.0}}


# ------------------------------------------------------------------ Namen
def test_norm_and_names():
    assert ra.norm("Küche Thermostat") == ra.norm("kueche") == ra.norm(ra.key_name("climate.kuche")) == "kuche"
    assert ra.norm("Kinderzimmer 1") != ra.norm("Kinderzimmer 2")
    assert ra.norm("tado Smart Radiator Thermostat VA1234567890") == ""
    assert ra.norm("Wohnzimmer Heizung") == ra.norm("WOHNZIMMER") == "wohnzimmer"
    assert ra.key_name("climate.gaeste_zimmer") == "Gaeste Zimmer"


# ------------------------------------------------------------------ Verwaiste Schlüssel
def test_orphans_name_days_source_and_current_devices_left_out(tmp_path):
    st = Store(tmp_path / "o.db")
    wz = fill(st, "climate.wohnzimmer", 0, 45)
    fill(st, "climate.gaeste", 0, 10)
    fill(st, "climate.hk_bad2", 0, 30)  # früher eigener Raum, jetzt Mitläufer im Bad: Werte ohne Raummodell
    fill(st, "climate.hk_bad", 43, 45)
    st.put("model.room.climate.gaeste", {"room": "Gästezimmer"})  # so nannte HeatPilot den Raum
    st.put("model.valve.climate.alt", {"version": vm.VERSION})    # nur ein Modell, keine Messwerte
    st.put("model.room.climate.hk_bad", {"room": "Bad"})
    found = Discovered(rooms=[Room("Bad", "climate.hk_bad", platform=HK,
                                   members=[{"climate": "climate.hk_bad2", "platform": HK}])])
    states = {"climate.wohnzimmer": climate("Wohnzimmer"), "climate.hk_bad": climate("Bad VA1")}
    idx = registry([("climate.wohnzimmer", "tado", "Wohnzimmer", "Wohnzimmer"),
                    ("climate.gaeste", "zha", "Thermostat", None)]).lookup()
    got = {o["key"]: o for o in ra.orphans(st, found, states, idx)}
    # meiste Tage zuerst; der heutige Mitläufer zählt mit (seit der Prüfung von 0.42.1), der Raumschlüssel nicht
    assert list(got) == ["climate.wohnzimmer", "climate.hk_bad2", "climate.gaeste", "climate.alt"]
    assert got["climate.hk_bad2"]["member_of"] == "climate.hk_bad" and got["climate.wohnzimmer"]["member_of"] is None
    w = got["climate.wohnzimmer"]
    assert (w["name"], w["source"], w["area"], w["still_exists"]) == ("Wohnzimmer", "Tado-Cloud", "Wohnzimmer", True)
    assert (w["rows"], w["days"], w["first_ts"], w["last_ts"]) == (len(wz), days_of(wz), wz[0], wz[-1])
    g = got["climate.gaeste"]
    assert g["name"] == "Gästezimmer" and g["source"] == "Zigbee (ZHA), nicht mehr in Home Assistant"
    assert g["models"] == ["room"] and not g["still_exists"]
    a = got["climate.alt"]
    assert (a["rows"], a["days"], a["first_ts"], a["name"], a["models"]) == (0, 0, None, "Alt", ["valve"])
    assert a["source"] == "nicht mehr in Home Assistant"  # ohne Registry-Eintrag
    # ohne Registry: ohne Herkunft und Bereich
    w = next(o for o in ra.orphans(st, found, states) if o["key"] == "climate.wohnzimmer")
    assert (w["source"], w["area"]) == ("", None)


# ------------------------------------------------------------------ Vorschläge
def suggest_scene(tmp_path):
    """Tado-Cloud-Zonen (alt) und HomeKit-Ventile (neu, je 2 Tage) in einem Haus."""
    st = Store(tmp_path / "s.db")
    for key, days in (("climate.wohnzimmer", 45), ("climate.kuche", 40), ("climate.zone_7", 30), ("climate.bad", 30),
                      ("climate.flur", 25), ("climate.gaeste", 10)):
        fill(st, key, 0, days)
    rooms = [Room("Wohnzimmer", "climate.hk_va01", platform=HK, area="Wohnzimmer"),
             Room("Kochen", "climate.hk_va02", platform=HK, area="Küche"),     # Raum umbenannt, Bereich „Küche“
             Room("Arbeit", "climate.hk_va03", platform=HK, area="Büro"),
             Room("Bad VA1", "climate.hk_va04", platform=HK, area="Bad"),      # zwei Ventile im Bad als zwei Räume
             Room("Bad VA2", "climate.hk_va05", platform=HK, area="Bad"),
             Room("Flur", "climate.hk_va06", platform=HK, area="Flur")]
    for i, r in enumerate(rooms):
        fill(st, r.climate, 43 if r.name != "Flur" else 25, 45)
    st.put("model.room.climate.gaeste", {"room": "Gästezimmer"})
    found = Discovered(rooms=rooms)
    states = {r.climate: climate(f"tado Smart Radiator Thermostat VA0{i + 1}") for i, r in enumerate(rooms)}
    states.update({k: climate(n) for k, n in (("climate.wohnzimmer", "Wohnzimmer"), ("climate.kuche", "Küche"),
                                              ("climate.zone_7", "Zone 7"), ("climate.bad", "Bad"),
                                              ("climate.flur", "Flur"))})
    reg = [(r.climate, HK, f"tado Smart Radiator Thermostat VA0{i + 1}", r.area) for i, r in enumerate(rooms)]
    reg += [("climate.wohnzimmer", "tado", "Wohnzimmer", "Wohnzimmer"), ("climate.kuche", "tado", "Küche", None),
            ("climate.zone_7", "tado", "Zone 7", "Büro"), ("climate.bad", "tado", "Bad", None),
            ("climate.flur", "tado", "Flur", None)]
    return st, found, states, registry(reg).lookup()


def test_suggest_name_area_tado_zone_none_duplicates_and_little_data(tmp_path):
    st, found, states, idx = suggest_scene(tmp_path)
    res = ra.overview(st, found, states, idx)
    got = {s["to"]: (s["from"], s["reason"], s["source"]) for s in res["suggestions"]}
    assert got == {
        "climate.hk_va01": ("climate.wohnzimmer", "gleicher Name · gleicher Bereich", "Tado-Cloud"),
        "climate.hk_va02": ("climate.kuche", "Tado-Zone heißt wie der Bereich", "Tado-Cloud"),
        "climate.hk_va03": ("climate.zone_7", "gleicher Bereich", "Tado-Cloud"),
    }  # Bad: zwei Räume passen gleich gut – keiner; Flur: zu wenig mehr Daten; Gästezimmer: kein Raum
    s = res["suggestions"][0]
    assert (s["to_name"], s["from_name"], s["new_days"]) == ("Wohnzimmer", "Wohnzimmer", res["rooms"][0]["days"])
    assert s["days"] >= max(2 * s["new_days"], s["new_days"] + ra.MORE_DAYS)
    assert [r["key"] for r in res["rooms"]] == [r.climate for r in found.rooms]
    assert {o["key"] for o in res["orphans"]} == {"climate.wohnzimmer", "climate.kuche", "climate.zone_7",
                                                  "climate.bad", "climate.flur", "climate.gaeste"}
    # Flur mit genug mehr Daten: dann ja
    fill(st, "climate.flur", -40, 0)
    got = {s["to"]: s["from"] for s in ra.overview(st, found, states, idx)["suggestions"]}
    assert got["climate.hk_va06"] == "climate.flur"


def test_suggest_needs_a_unique_match_both_ways(tmp_path):
    st, found, states, idx = suggest_scene(tmp_path)
    # zweite alte Entität „Wohnzimmer“ (z. B. vor einem früheren Wechsel), ohne Bereich: es gewinnt der Treffer, der
    # mehr belegt (Name und Bereich gegen nur Name)
    fill(st, "climate.wohnzimmer_alt", 0, 20)
    states["climate.wohnzimmer_alt"] = climate("Wohnzimmer")
    got = {s["to"]: s["from"] for s in ra.overview(st, found, states, idx)["suggestions"]}
    assert got["climate.hk_va01"] == "climate.wohnzimmer" and "climate.hk_va02" in got
    # beide mit Name und Bereich: gleich gut – für das Wohnzimmer kein sicherer Treffer, die anderen Räume bleiben
    idx = registry([("climate.wohnzimmer", "tado", "Wohnzimmer", "Wohnzimmer"),
                    ("climate.wohnzimmer_alt", "tado", "Wohnzimmer", "Wohnzimmer"),
                    ("climate.hk_va01", HK, "VA01", "Wohnzimmer")]).lookup()
    got = {s["to"]: s["from"] for s in ra.overview(st, found, states, idx)["suggestions"]}
    assert "climate.hk_va01" not in got
    # ohne Registry (alte HA): auch der Raum kennt keinen Bereich – nur über den Namen
    kochen = Room("Kochen", "climate.hk_va02", platform=HK)
    got = {s["to"]: s["from"] for s in ra.overview(st, Discovered(rooms=[kochen]), states)["suggestions"]}
    assert got == {}  # „Kochen“ heißt nicht wie eine alte Zone, Bereich unbekannt
    got = {s["to"]: s["from"] for s in ra.overview(st, Discovered(rooms=[found.rooms[1]]), states)["suggestions"]}
    assert got == {"climate.hk_va02": "climate.kuche"}  # mit dem Bereich aus der Erkennung: Zone „Küche“


def test_zigbee_repaired_thermostat_is_found_too(tmp_path):
    """Nicht nur Tado: ein neu gekoppeltes Zigbee-Thermostat bekommt eine neue Entität im selben Bereich."""
    st = Store(tmp_path / "z.db")
    fill(st, "climate.kinderzimmer_thermostat", 0, 30)
    fill(st, "climate.kinderzimmer_thermostat_2", 29, 30)
    found = Discovered(rooms=[Room("Kinderzimmer", "climate.kinderzimmer_thermostat_2", platform="zha")])
    idx = registry([("climate.kinderzimmer_thermostat_2", "zha", "TRV Kinderzimmer", "Kinderzimmer")]).lookup()
    res = ra.overview(st, found, {"climate.kinderzimmer_thermostat_2": climate("Kinderzimmer Thermostat")}, idx)
    (s,) = res["suggestions"]
    assert (s["from"], s["from_name"], s["source"]) == ("climate.kinderzimmer_thermostat", "Kinderzimmer Thermostat",
                                                        "nicht mehr in Home Assistant")
    assert s["reason"] == "gleicher Name"


# ------------------------------------------------------------------ Übernehmen
OLD, NEW, OTHER = "climate.wohnzimmer", "climate.hk_va01", "climate.hk_va02"


def adopt_scene(tmp_path):
    st = Store(tmp_path / "a.db")
    old_ts = fill(st, OLD, 0, 10, temp=19.0)
    new_ts = fill(st, NEW, 8, 11, temp=21.0)   # zwei Tage gleichzeitig (beide Räume gewählt)
    fill(st, OTHER, 0, 11, temp=20.0)
    st.add_valve_subst(START + 2 * DAY, [OLD])
    st.add_valve_subst(START + 9 * DAY, [OLD, NEW])
    st.add_room_event(START + 3 * DAY, START + 3 * DAY + 7200, OLD, 1.5)
    st.add_room_event(START + 9 * DAY, START + 9 * DAY + 3600, OLD, 0.8)
    st.add_room_event(START + 9 * DAY, START + 9 * DAY + 5400, NEW, 1.1)
    st.add_valve_dry({"ts": START + 4 * DAY, "room": OLD, "rule": "drosseln", "action": "senken", "setpoint": 20.5})
    st.add_valve_dry({"ts": START + 9 * DAY, "room": OLD, "rule": "drosseln", "action": "senken", "setpoint": 20.0})
    st.add_snapshot(START + DAY, "room", OLD, {"room": "Wohnzimmer", "tau_h": 50})
    st.add_snapshot(START + 9 * DAY, "room", OLD, {"room": "Wohnzimmer", "tau_h": 51})
    st.add_snapshot(START + 9 * DAY, "room", NEW, {"room": "Wohnzimmer", "tau_h": 9})
    st.add_control_log(START + 5 * DAY, OLD, "set_temperature", 21.0, "Komfort")
    for kind, key in (("room", OLD), ("room", NEW), ("valve", OLD), ("valve", NEW), ("room", OTHER)):
        st.put(f"model.{kind}.{key}", {"room": "x"})
    st.put("model.room_ext", {OLD: {"start_ts": 1}, NEW: {"start_ts": 2}, OTHER: {"start_ts": 3}})
    st.put(PRESET_CHANGES_KEY, [{"ts": START, "room": OLD, "from": 3, "to": 4}, {"ts": START, "room": OTHER}])
    st.put(ra.ADVICE_KEY, {f"{OLD}|abgleich|Wohnzimmer": {"since": 1}, f"{OTHER}|x|y": {"since": 2}})
    st.commit()
    return st, old_ts, new_ts


def count(st, sql, *p) -> int:
    return st.query(sql, p)[0][0]


def test_adopt_moves_rows_keeps_the_rooms_own_and_rewrites_keys(tmp_path):
    st, old_ts, new_ts = adopt_scene(tmp_path)
    other_before = count(st, "SELECT COUNT(*) FROM room_samples WHERE room = ?", OTHER)
    res = ra.adopt(st, OLD, NEW, now_ts=START + 11 * DAY)
    moved = [t for t in old_ts if t not in set(new_ts)]
    assert (res["rows"], res["skipped"]) == (len(moved), len(old_ts) - len(moved))
    assert (res["from_days"], res["first_ts"], res["last_ts"]) == (days_of(moved), moved[0], moved[-1])
    assert count(st, "SELECT COUNT(*) FROM room_samples WHERE room = ?", OLD) == 0
    rows = {r["ts"]: r["temp"] for r in st.query("SELECT ts, temp FROM room_samples WHERE room = ?", (NEW,))}
    assert set(rows) == set(old_ts) | set(new_ts)
    assert {rows[t] for t in new_ts} == {21.0} and {rows[t] for t in moved} == {19.0}  # gleiche Zeit: der Raum bleibt
    assert count(st, "SELECT COUNT(*) FROM room_samples WHERE room = ?", OTHER) == other_before
    # Ersatzwerte, Fremdwärme, Ventil-Protokoll, Tagesstände: wie die Messwerte
    assert [r["ts"] for r in st.query("SELECT ts FROM valve_subst WHERE room = ? ORDER BY ts", (NEW,))] == \
        [START + 2 * DAY, START + 9 * DAY]
    ev = {r["start_ts"]: r["rise_k"] for r in st.query("SELECT * FROM room_events WHERE room = ?", (NEW,))}
    assert ev == {START + 3 * DAY: 1.5, START + 9 * DAY: 1.1}
    dry = [r["ts"] for r in st.query("SELECT ts FROM valve_dry_log WHERE room = ? ORDER BY ts", (NEW,))]
    assert dry == [START + 4 * DAY]
    snaps = {r["ts"]: json.loads(r["data"])["tau_h"] for r in st.query(
        "SELECT ts, data FROM snapshots WHERE kind = 'room' AND key = ?", (NEW,))}
    assert snaps == {START + DAY: 50, START + 9 * DAY: 9}
    for table, col in (("valve_subst", "room"), ("room_events", "room"), ("valve_dry_log", "room"),
                       ("snapshots", "key")):
        assert count(st, f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", OLD) == 0, table
    assert count(st, "SELECT COUNT(*) FROM control_log WHERE room = ?", OLD) == 1  # was geschrieben wurde, bleibt
    # Modelle beider Schlüssel verworfen (lernen neu), die anderer Räume bleiben
    for kind in ("room", "valve"):
        assert st.get(f"model.{kind}.{OLD}") is None and st.get(f"model.{kind}.{NEW}") is None
    assert st.get(f"model.room.{OTHER}") == {"room": "x"}
    assert st.get("model.room_ext") == {OTHER: {"start_ts": 3}}
    assert [c["room"] for c in st.get(PRESET_CHANGES_KEY)] == [NEW, OTHER]  # Hydraulik: derselbe Heizkörper
    assert set(st.get(ra.ADVICE_KEY)) == {f"{NEW}|abgleich|Wohnzimmer", f"{OTHER}|x|y"}
    assert st.get(ra.KV_LOG) == [{"ts": START + 11 * DAY, "from": OLD, "to": NEW, "rows": len(moved)}]


def test_adopt_is_one_transaction(tmp_path):
    st, old_ts, new_ts = adopt_scene(tmp_path)
    before = {t: count(st, f"SELECT COUNT(*) FROM {t}") for t in ("room_samples", "valve_subst", "room_events")}
    by_room = st.query("SELECT room, COUNT(*) FROM room_samples GROUP BY room")

    class Failing:
        """Datenbank, die beim letzten Schritt (Protokoll) scheitert."""

        def __init__(self, db):
            self._db = db

        def execute(self, sql, params=()):
            if isinstance(params, tuple) and ra.KV_LOG in params:
                raise sqlite3.OperationalError("Platte voll")
            return self._db.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._db, name)

        def __enter__(self):
            return self._db.__enter__()

        def __exit__(self, *exc):
            return self._db.__exit__(*exc)

    real, st.db = st.db, Failing(st.db)
    with pytest.raises(sqlite3.OperationalError):
        ra.adopt(st, OLD, NEW)
    st.db = real
    assert {t: count(st, f"SELECT COUNT(*) FROM {t}") for t in before} == before
    assert [tuple(r) for r in st.query("SELECT room, COUNT(*) FROM room_samples GROUP BY room")] == \
        [tuple(r) for r in by_room]
    assert st.get(f"model.room.{OLD}") == {"room": "x"} and st.get(ra.KV_LOG) is None
    with pytest.raises(ra.AdoptError):
        ra.adopt(st, OLD, OLD)


def test_check_only_orphans_with_data_into_current_rooms(tmp_path):
    st, *_ = adopt_scene(tmp_path)
    found = Discovered(rooms=[Room("Wohnzimmer", NEW, members=[{"climate": "climate.hk_va09"}]), Room("K", OTHER)])
    fill(st, "climate.hk_va09", 0, 3)
    assert ra.check(st, found, OLD, NEW) is None
    assert "kein aktueller Raum" in ra.check(st, found, OLD, "climate.hk_va09")   # Mitläufer ist kein Raumschlüssel
    assert "kein aktueller Raum" in ra.check(st, found, OLD, OLD)
    assert ra.check(st, found, "climate.hk_va09", NEW) is None                  # Mitläufer: in seinen Raum
    assert "als Mitläufer zum Raum Wohnzimmer" in ra.check(st, found, "climate.hk_va09", OTHER)  # nur dorthin
    assert "gehört zu einem aktuellen Raum" in ra.check(st, found, OTHER, NEW)
    assert "keine Messwerte" in ra.check(st, found, "climate.fremd", NEW)
    assert "gehört zu einem aktuellen Raum" in ra.check(st, found, "", NEW)


# ------------------------------------------------------------------ Neu lernen
def history(store, key: str, d0: int, d1: int, t0: float = 20.0) -> float:
    """Künstlicher Raum (``sim_valve``: Heizkörper mit PI-Thermostat, Tag/Nacht, Soll-Sprünge), Außentemperatur je Tag
    anders; Kesselwerte wie HeatPilot sie speichert. Gibt die letzte Raumtemperatur zurück."""
    t = t0
    for d in range(d0, d1):
        t_out = 2.0 + 2.0 * ((d * 7) % 5)
        rows, _ = simulate(Thermostat(kp=60, ki=0.5), SimRoom(a=0.03, b=0.02, flow=50.0, t_out=t_out, t0=t),
                           Scenario(days=1, seed=d + 1), START + d * DAY)
        store.add_room_samples((ts, key, temp, sp, valve, None) for ts, temp, sp, valve in rows)
        for ts, *_ in rows:
            store.add_sample(ts, {"t_out": t_out, "flow": 50.0, "pump_mod": 99.0, "heating": 1, "burner_on": 1,
                                  "dhw": 0, "vacation": 0, "away_long": 0, "hc_day": 1, "heating_off": 0})
        t = rows[-1][1]
    store.commit()
    return t


def engine(store, key: str, until_day: int) -> Engine:
    eng = Engine(Config(), store, Discovered(rooms=[Room("Wohnzimmer", key, heating_power=f"sensor.{key[8:]}_heat")]))
    eng._last_room_step = START + until_day * DAY
    return eng


def test_after_adopt_room_and_valve_relearn_as_plausible_as_before(tmp_path):
    st = Store(tmp_path / "r.db")
    t = history(st, OLD, 0, 14)
    before = engine(st, OLD, 14)
    before.relearn_rooms({OLD})
    old_model = before.rooms[OLD]
    assert guards.check_room(old_model)["ok"]
    old_valve = vm.ValveModel()
    while vl.learn_chunk(st, old_valve, OLD, START + 14 * DAY):
        pass
    # Umstieg: neuer Schlüssel, ein Tag Messwerte – lernt von vorn
    history(st, NEW, 14, 15, t)
    eng = engine(st, NEW, 15)
    eng.relearn_rooms({NEW})
    assert guards.check_room(eng.rooms[NEW])["reasons"][0].startswith("erst ")
    eng.learn_valves(budget_s=None)
    short = eng.valves[NEW]
    res = ra.run(eng, OLD, NEW)
    assert (res["rows"], res["skipped"], res["from_name"], res["to_name"]) == (14 * 288, 0, "Wohnzimmer", "Wohnzimmer")
    m = eng.rooms[NEW]
    assert guards.check_room(m)["ok"] and m.rls.updates > old_model.rls.updates  # alte Tage und der neue
    assert abs(m.a - old_model.a) / old_model.a < 0.05 and abs(m.b - old_model.b) / old_model.b < 0.05
    assert st.get(f"model.room.{NEW}")["rls"]["updates"] == m.rls.updates  # gespeichert
    # Ventilmodell: von vorn, lernt im Takt aus allen gespeicherten Messwerten des Raums nach
    v = eng.valves[NEW]
    assert v.learned_until is None and eng.valve_info(NEW)["catching_up"]
    assert eng.learn_valves(budget_s=None) is False and v.learned_until == eng._last_room_step
    assert sum(map(len, v.jumps.values())) > sum(map(len, short.jumps.values()))
    assert v.steady_h > short.steady_h * 10
    assert abs(v.points()["e50"] - old_valve.points()["e50"]) <= 0.1
    assert st.get(f"model.room.{OLD}") is None and st.get(f"model.valve.{OLD}") is None


def test_relearn_detects_ext_heat_anew_and_leaves_other_rooms(tmp_path):
    st = Store(tmp_path / "x.db")
    t = history(st, OLD, 0, 6)
    history(st, OTHER, 0, 6)
    history(st, NEW, 6, 7, t)
    st.add_room_event(START + DAY, START + DAY + 3600, OLD, 3.0)  # Ereignis des alten Modells (anderes Modell)
    eng = Engine(Config(), st, Discovered(rooms=[Room("Wohnzimmer", NEW), Room("Küche", OTHER)]))
    eng._last_room_step = START + 7 * DAY
    eng.relearn_rooms({NEW, OTHER})
    other = eng.rooms[OTHER].to_dict()
    eng.room_ext[NEW] = {"start_ts": START, "t_start": 20.0, "peak": 21.0, "rise": 1.0}
    ra.run(eng, OLD, NEW)
    assert eng.rooms[OTHER].to_dict() == other  # nur der Raum lernt neu
    assert NEW not in eng.room_ext and not st.query("SELECT 1 FROM room_events WHERE room = ? AND rise_k = 3.0", (NEW,))
    m = eng.rooms[NEW]
    assert m.rls.updates >= 6 * 48 - 2 and m.room == "Wohnzimmer"


def test_relearn_without_room_step_waits_for_the_tick(tmp_path):
    """Noch kein Raumschritt (frisch gestartet): Modelle von vorn, gelernt wird im Takt – nichts geht verloren."""
    st = Store(tmp_path / "w.db")
    history(st, OLD, 0, 2)
    eng = Engine(Config(), st, Discovered(rooms=[Room("Wohnzimmer", NEW)]))
    assert eng._last_room_step is None
    ra.run(eng, OLD, NEW)
    assert eng.rooms[NEW].rls.updates == 0 and count(st, "SELECT COUNT(*) FROM room_samples WHERE room = ?", NEW) == 576


# ------------------------------------------------------------------ Neueinlesen der Historie
class HistHA:
    """HA-Historie: je Entität [(ts, Zustand, Attribute)]; liefert wie HA den Zustand zum Start mit."""

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


def test_reload_history_keeps_adopted_rows_and_keys_without_room(tmp_path, monkeypatch):
    end = START + 30 * DAY
    monkeypatch.setattr(backfill, "time", SimpleNamespace(time=lambda: end))
    st = Store(tmp_path / "b.db")
    found = Discovered(rooms=[Room("Wohnzimmer", NEW)], boiler={"flow_temp": "sensor.flow"})
    eng = Engine(Config(), st, found)
    adopted = fill(st, NEW, 10, 28, temp=19.0, step=300)      # übernommen (vor der HA-Historie der neuen Entität)
    fill(st, NEW, 28, 30, temp=19.5, step=300)                # eigene – ersetzt HA
    orphan = fill(st, OLD, 10, 25, temp=18.0, step=300)       # Schlüssel ohne Raum
    for ts in range(START + 20 * DAY, end, 300):
        st.add_sample(ts, {"flow": 99.0})
    st.commit()
    born = START + 28 * DAY  # HomeKit-Entität erst seit zwei Tagen in Home Assistant
    ha = HistHA({NEW: [(born, "heat", {"current_temperature": 21.0, "temperature": 21.0})],
                 "sensor.flow": [(START, "45", {})]})
    asyncio.run(backfill.backfill_history(ha, eng, 10))
    rows = {r["ts"]: r["temp"] for r in st.query("SELECT ts, temp FROM room_samples WHERE room = ?", (NEW,))}
    assert all(rows[t] == 19.0 for t in adopted)                       # übernommene Werte bleiben
    assert {v for t, v in rows.items() if t >= born} == {21.0}         # ab der HA-Historie aus HA
    assert count(st, "SELECT COUNT(*) FROM room_samples WHERE room = ?", OLD) == len(orphan)
    assert {r["flow"] for r in st.query("SELECT flow FROM samples WHERE ts >= ?", (START + 20 * DAY,))} == {45.0}
    assert backfill.room_starts({NEW: [(born + 5, "heat", {}), (born, "heat", {})]}, found.rooms, end - 10 * DAY) \
        == {NEW: born}
    assert backfill.room_starts({}, found.rooms, 0) == {}  # ohne HA-Historie bleiben die Werte
    # ohne Räume wie bisher: alle Raumwerte des Zeitraums
    st.delete_range(START, end)
    assert count(st, "SELECT COUNT(*) FROM room_samples") == 0


# ------------------------------------------------------------------ Abfragen
class NoHA:
    """Home Assistant, das jeden Zugriff merkt – die Übernahme darf es nicht anfassen."""

    last_error = ""

    def __init__(self):
        self.used = []

    def __getattr__(self, name):
        self.used.append(name)

        async def call(*a, **k):
            return None
        return call


def make_app(eng, reg: Registry):
    app = main_mod.App.__new__(main_mod.App)
    app._lock, app.eng, app._status, app.ha = asyncio.Lock(), eng, "läuft", NoHA()
    app._registry = (time.time() + 10 ** 6, reg)
    app.cfg = eng.cfg if eng is not None else Config()
    return app


async def api(app, calls):
    client = TestClient(TestServer(web.make_app(app)))
    await client.start_server()
    out = []
    try:
        for method, body in calls:
            if callable(method):
                await method()
                continue
            kw = {"data": body} if isinstance(body, str) else {"json": body} if body is not None else {}
            r = await (client.get if method == "GET" else client.post)("/api/rooms/adopt", **kw)
            out.append((r.status, await r.json()))
    finally:
        await client.close()
    return out


def test_api_lists_and_adopts_only_orphans_into_rooms_and_writes_nothing_to_ha(tmp_path):
    st, found, states, idx = suggest_scene(tmp_path)
    eng = Engine(Config(), st, found)
    eng.states.update(states)
    reg = registry([("climate.wohnzimmer", "tado", "Wohnzimmer", "Wohnzimmer"),
                    ("climate.hk_va01", HK, "VA01", "Wohnzimmer")])
    app = make_app(eng, reg)
    total = count(st, "SELECT COUNT(*) FROM room_samples")
    res = asyncio.run(api(app, [
        ("GET", None),
        ("POST", {"from": "climate.fremd", "to": "climate.hk_va01"}),
        ("POST", {"from": "climate.hk_va02", "to": "climate.hk_va01"}),
        ("POST", {"from": "climate.wohnzimmer", "to": "climate.wohnzimmer"}),
        ("POST", "kein json"),
        ("POST", ["climate.wohnzimmer"]),
        (app._lock.acquire, None), ("POST", {"from": "climate.wohnzimmer", "to": "climate.hk_va01"}),
        (lambda: asyncio.sleep(0, app._lock.release()), None),
        ("POST", {"from": "climate.wohnzimmer", "to": "climate.hk_va01"}),
        ("GET", None),
    ]))
    (s0, first), *errs, (s9, ok), (s10, after) = res
    assert s0 == 200 and {s["to"]: s["from"] for s in first["suggestions"]}["climate.hk_va01"] == "climate.wohnzimmer"
    assert next(o for o in first["orphans"] if o["key"] == "climate.wohnzimmer")["source"] == "Tado-Cloud"
    assert [(s, e["ok"]) for s, e in errs] == [(400, False)] * 5 + [(409, False)]
    assert "keine Messwerte" in errs[0][1]["error"] and "gehört zu einem aktuellen Raum" in errs[1][1]["error"]
    assert "kein aktueller Raum" in errs[2][1]["error"] and errs[3][1]["error"] == "Ungültige Daten"
    assert "baut gerade neu auf" in errs[5][1]["error"]
    assert s9 == 200 and ok["ok"] and ok["rows"] > 0 and (ok["from_name"], ok["to_name"]) == ("Wohnzimmer", "Wohnzimmer")
    assert "climate.wohnzimmer" not in {o["key"] for o in after["orphans"]}  # Zählung nach der Übernahme frisch
    assert next(r for r in after["rooms"] if r["key"] == "climate.hk_va01")["rows"] == ok["rows"] + 2 * 48
    assert after["log"][-1]["from"] == "climate.wohnzimmer"
    # nichts doppelt, nichts verloren: weg sind nur die alten Werte zu Zeitpunkten, die der Raum schon selbst hatte
    assert count(st, "SELECT COUNT(*) FROM room_samples") == total - ok["skipped"] and ok["skipped"] == 2 * 48
    assert set(app.ha.used) <= {"list_statistic_ids"}  # nur gelesen (alter Temperatursensor), nichts geschrieben
    assert ok["alias"] is None and ok["restore_suggested"] is False  # keine Statistik-Liste, keine Stundenwerte


def test_api_while_starting(tmp_path):
    app = make_app(None, Registry())
    app.eng = None
    (s0, g), (s1, p) = asyncio.run(api(app, [("GET", None), ("POST", {"from": OLD, "to": NEW})]))
    assert (s0, g["starting"]) == (200, True) and s1 == 503 and not p["ok"]


# ------------------------------------------------------------------ Lesbarkeit, Version, Doku
def test_new_files_small_with_purpose_docstrings():
    for name in ("room_adopt.py", "web_adopt.py"):
        src = (PKG / name).read_text(encoding="utf-8")
        assert src.startswith('"""') and src.count("\n") <= 600, name
        for fn in re.split(r"\n(?=def |    def |    async def |async def )", src)[1:]:
            assert fn.count("\n") <= 60, (name, fn.splitlines()[0])
    for name in ("web.py", "main.py", "engine_learn.py", "store.py", "backfill.py"):
        assert (PKG / name).read_text(encoding="utf-8").count("\n") <= 600, name
    text = (PKG / "room_adopt.py").read_text(encoding="utf-8") + (PKG / "web_adopt.py").read_text(encoding="utf-8")
    for word in ("call_service", "set_temperature", "turn_on", "ha."):  # schreibt nichts an Home Assistant
        assert word not in text, word


def test_version_changelog_docs():
    from heatpilot import __version__

    assert __version__ == "0.42.1"
    assert 'version: "0.42.1"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## ")[1].split("\n## ")[0]
    assert head.startswith("0.42.1") and "Gelerntes übernehmen" in head and "Hausdaten wiederherstellen" in head
    assert "tests/test_adopt421.py" in head and "tests/test_restore421.py" in head and "tests/test_ui_v421.py" in head
    for personal in ("Ingo", "BK15", "Sieger", "ES73", "VA0", "Hamburg"):
        assert personal not in head, personal
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for text in ("## Räume wechseln (z. B. Tado-Cloud → HomeKit)", "Gespeicherte Daten ohne Raum",
                 "„übernehmen in …“", "Nicht rückgängig zu machen", "Historie neu einlesen",
                 "**Hausdaten wiederherstellen**", "**Aus gespeicherten Daten neu lernen**", "**Karte „Modelle“**"):
        assert text in docs, text
