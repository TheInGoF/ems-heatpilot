"""0.45: Heizleistung vom alten Thermostat, gemischte Ventilquellen, Räume aus der Langzeitstatistik, Fußbodengrenze.

Erfundener Fall: Der Raum „Wohnen“ wechselte von einer Tado-Cloud-Zone (``climate.alt_wohnen``, Integration noch
eingerichtet, am Gerät Temperatur und Heizleistung in %) auf ein HomeKit-Thermostat ohne Heizleistung. HeatPilot hat
drei Tage eigene Raumwerte, Home Assistant 30 Tage Stundenmittel der alten Sensoren und des Vorlaufs. Geprüft:

* die Heizleistung wird über das alte Gerät (bzw. Alias-Gerät, Bereich, Name) gefunden und zur Laufzeit genutzt, ein
  eingetragener Sensor hat Vorrang, die Einstellungen bleiben unverändert;
* echte Ventilwerte werden nicht durch 0/100 % anderer Geräte verwässert; ein veralteter Sensor ist Ersatzwert;
* die Stundenstatistik füllt nur Stunden ohne eigene Raumwerte (berichtigt um den geprüften Versatz), das Raummodell
  lernt sie (Heizbetrieb > 0 K·h), das Ventilmodell nicht; Ergebnis und Protokoll je Raum; Aufbewahrung;
* Fußbodenräume haben eine eigene obere Grenze für Heizwirkung/Auskühlen.
"""

import asyncio
import math
import time
from datetime import datetime, timezone

import pytest
from heatpilot import backfill, guards, house_data, house_restore, keep_data, room_alias, room_heating, room_stats
from heatpilot import room_stats_learn
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Registry, Room
from heatpilot.engine import Engine
from heatpilot.models import RoomModel
from heatpilot.store import Store

NOW = 1_790_000_000 // 3600 * 3600          # feste Stunde (Ende Oktober 2026)
DAYS, OWN_DAYS = 30, 3
START, OWN = NOW - DAYS * 86400, NOW - OWN_DAYS * 86400
KEY, OLD = "climate.hk_wohnen", "climate.alt_wohnen"
T_ALIAS, HEAT, FLOW = "sensor.alt_wohnen_temperature", "sensor.alt_wohnen_heating", "sensor.kessel_vorlauf"
ALIAS_OFF = 0.5                              # alter Sensor misst 0,5 K wärmer als das neue Thermostat


def heating(h: int) -> bool:
    return 5 <= (h % 86400) // 3600 <= 21


def t_out(h: int) -> float:
    return round(5 + 3 * math.sin(2 * math.pi * (h % 86400) / 86400), 2)


def valve(h: int) -> float:
    return round(40 + 20 * math.sin(2 * math.pi * h / (5 * 3600)), 1) if heating(h) else 0.0


def flow(h: int) -> float:
    return 45.0 if heating(h) else 28.0


_T: dict[int, float] = {}


def temp(h: int) -> float:
    """Raumtemperatur (Stundenmittel) aus einem einfachen Raummodell."""
    if not _T:
        t = 20.0
        for x in range(START - 3600, NOW + 7200, 3600):
            _T[x] = round(t, 3)
            warm = (flow(x) - t) * valve(x) / 100 if heating(x) else 0.0
            t += 0.012 * warm + 0.02 * (t_out(x) - t) + 0.05
    return _T[h - h % 3600]


REG = Registry(
    [{"entity_id": OLD, "platform": "tado", "device_id": "d1"},
     {"entity_id": T_ALIAS, "platform": "tado", "device_id": "d1"},
     {"entity_id": HEAT, "platform": "tado", "device_id": "d1", "translation_key": "heating", "unique_id": "1 2 heating"},
     {"entity_id": KEY, "platform": "homekit_controller", "device_id": "d2"},
     {"entity_id": "climate.hk_schlafen", "platform": "homekit_controller", "device_id": "d3"}],
    [{"id": "d1", "name": "Wohnen", "area_id": "wo"}, {"id": "d2", "name": "VA11", "area_id": "wo"},
     {"id": "d3", "name": "VA12", "area_id": "sz"}],
    [{"area_id": "wo", "name": "Wohnen"}, {"area_id": "sz", "name": "Schlafen"}])
STATES = {OLD: {"state": "heat", "attributes": {"friendly_name": "Wohnen Zone", "hvac_action": "heating"}},
          T_ALIAS: {"state": "20.5", "attributes": {"device_class": "temperature", "unit_of_measurement": "°C"}},
          HEAT: {"state": "35", "attributes": {"unit_of_measurement": "%", "friendly_name": "Wohnen Heizleistung"}},
          KEY: {"state": "off", "attributes": {"current_temperature": 20.0, "hvac_action": "idle"}},
          "climate.hk_schlafen": {"state": "off", "attributes": {"current_temperature": 18.0, "hvac_action": "idle"}}}
STAT_LIST = [{"statistic_id": T_ALIAS, "statistics_unit_of_measurement": "°C", "has_mean": True},
             {"statistic_id": FLOW, "statistics_unit_of_measurement": "°C", "has_mean": True},
             {"statistic_id": HEAT, "statistics_unit_of_measurement": "%", "has_mean": True, "name": "Wohnen Heizleistung"},
             {"statistic_id": "sensor.wohnen_feuchte", "statistics_unit_of_measurement": "%", "has_mean": True}]


class FakeHA:
    """Nur lesen: Langzeitstatistik (und leere Historie). Merkt die abgefragten Zeiträume; alles andere ist verboten."""

    def __init__(self, heat: bool = True):
        self.heat, self.spans = heat, []

    async def statistics(self, ids, s, e, period="hour"):
        a, b = int(s.timestamp()), int(e.timestamp())
        self.spans.append((a, b, tuple(ids)))
        out = {}
        for sid in ids:
            fn = {T_ALIAS: lambda h: temp(h) + ALIAS_OFF, HEAT: valve if self.heat else None, FLOW: flow}.get(sid)
            if fn:
                out[sid] = [{"start": h * 1000, "mean": fn(h)} for h in range(max(a, START), min(b, NOW), 3600)]
        return out

    async def history(self, ids, s, e, attributes=False):
        return {}                                                     # Recorder-Historie: hier leer

    def __getattr__(self, name):
        raise AssertionError(f"Home Assistant darf nicht {name} – nur lesen")


def found(heating_power=None, floor=False) -> Discovered:
    return Discovered(rooms=[Room("Wohnen", KEY, heating_power=heating_power, area="Wohnen",
                                  platform="homekit_controller")],
                      outdoor=["sensor.out"], gas="sensor.gas", boiler={"flow_temp": FLOW})


def make_store(path, alias=True) -> Store:
    st = Store(path)
    st.add_hourly([(h, t_out(h), temp(h), 2.0 if heating(h) else 0.0) for h in range(START, NOW, 3600)])
    st.add_room_samples((ts, KEY, temp(ts), None, 100.0 if heating(ts) else 0.0, None) for ts in range(OWN, NOW, 300))
    st.put("rooms.adopted", [{"ts": OWN, "from": OLD, "to": KEY}])
    if alias:
        room_alias.put(st, KEY, T_ALIAS, OLD, "device")
    st.commit()
    return st


def engine(st, f=None, **cfg) -> Engine:
    eng = Engine(Config(**{"statistics_backfill_days": DAYS, "backfill_days": 10, **cfg}), st, f or found())
    eng.states = dict(STATES)
    return eng


def restore(eng, ha=None, stat_list=STAT_LIST):
    return asyncio.run(room_stats.restore(ha or FakeHA(), eng, REG.lookup(), stat_list, now=NOW + 1800))


# ------------------------------------------------------------------ A: Heizleistung vom alten Thermostat
def test_heating_power_found_via_old_device_and_used_without_changing_settings(tmp_path):
    st = make_store(tmp_path / "a.db")
    f = found()
    entries = [{"climate": KEY, "name": "Wohnen"}]
    hits = room_heating.attach(st, f, REG.lookup(), STATES)
    assert hits[KEY]["sensor"] == HEAT and hits[KEY]["how"] == "adopted" and hits[KEY]["from"] == OLD
    assert hits[KEY]["how_text"] == "vom alten Thermostat" and hits[KEY]["from_name"] == "Wohnen Zone"
    assert f.rooms[0].heating_power == HEAT and f.valve_found == hits and f.as_dict()["valve_found"] == hits
    assert entries == [{"climate": KEY, "name": "Wohnen"}]           # Einstellungen bleiben, wie sie sind
    assert HEAT in f.all_sensor_ids()                                # wie ein Geschwister-Sensor beobachtet


def test_heating_power_via_alias_device_area_name_and_never_twice(tmp_path):
    st = Store(tmp_path / "b.db")
    room_alias.put(st, KEY, T_ALIAS, None, "statistics")              # nur der Alias zeigt aufs alte Gerät
    assert room_heating.find(st, found(), REG.lookup(), STATES)[KEY]["how"] == "alias"
    st.put(room_alias.KV, {})                                        # gar nichts gemerkt: gleicher Bereich
    assert room_heating.find(st, found(), REG.lookup(), STATES)[KEY]["how"] == "area"
    f = found()
    f.rooms[0].area = None                                           # ohne Bereich: passender Name
    assert room_heating.find(st, f, REG.lookup(), STATES)[KEY]["how"] == "name"
    two = Discovered(rooms=[Room("Wohnen", KEY), Room("Wohnen", "climate.hk_schlafen")])
    assert room_heating.find(st, two, REG.lookup(), STATES) == {}    # passt zu zwei Räumen: keiner
    assert room_heating.find(st, found(), None, STATES) == {}        # ohne Registry nichts
    st.put("rooms.adopted", [{"ts": OWN, "from": OLD, "to": KEY}])
    near = Discovered(rooms=[Room("Wohnen", KEY, area="Wohnen"), Room("Ecke", "climate.hk_schlafen", area="Wohnen")])
    assert {k: h["how"] for k, h in room_heating.find(st, near, REG.lookup(), STATES).items()} == {KEY: "adopted"}


def test_registered_sensor_wins_and_sensor_of_current_thermostat_is_not_found(tmp_path):
    st = make_store(tmp_path / "c.db")
    f = found(heating_power="sensor.eigenes_ventil")
    assert room_heating.attach(st, f, REG.lookup(), STATES) == {}
    assert f.rooms[0].heating_power == "sensor.eigenes_ventil"
    both = Discovered(rooms=[Room("Wohnen", KEY), Room("Zone", OLD)])  # altes Thermostat ist wieder ein Raum
    assert room_heating.find(st, both, REG.lookup(), STATES) == {}


# ------------------------------------------------------------------ B: echte und Ersatz-Ventile
def _reader(states):
    r = backfill._RoomReader()
    r.states = states
    r.valve_subst = set()
    return r


def test_real_valves_are_not_diluted_by_on_off_of_other_devices():
    room = Room("Wohnen", KEY, heating_power=HEAT, members=[{"climate": "climate.hk_schlafen"}])
    states = {KEY: {"state": "heat", "attributes": {"current_temperature": 20.0, "temperature": 21.0}},
              HEAT: {"state": "40"},
              "climate.hk_schlafen": {"state": "heat", "attributes": {"current_temperature": 20.4,
                                                                      "hvac_action": "heating"}}}
    assert _reader(states).room_reading(room)[3] == 40.0             # nicht (40 + 100) / 2
    del states[HEAT]                                                  # Sensor still: nur Ersatzwerte – Mittel wie bisher
    r = _reader(states)
    assert r.room_reading(room)[3] == 50.0 and r.valve_subst == {KEY}
    plain = Room("Wohnen", KEY, members=[{"climate": "climate.hk_schlafen"}])
    assert _reader(states).room_reading(plain)[3] == 50.0            # keiner echt: Mittel der 0/100-Werte


def test_stale_valve_sensor_counts_as_substitute():
    old = datetime.fromtimestamp(time.time() - 7 * 3600, timezone.utc).isoformat()
    room = Room("Wohnen", KEY, heating_power=HEAT)
    states = {KEY: {"state": "heat", "attributes": {"current_temperature": 20.0, "hvac_action": "heating"}},
              HEAT: {"state": "0", "last_changed": old, "last_updated": old}}
    r = _reader(states)
    assert r.room_reading(room)[3] == 100.0 and r.valve_subst == {KEY}  # 0 % seit 7 h, Thermostat heizt
    states[KEY]["attributes"]["hvac_action"] = "idle"
    r = _reader(states)
    assert r.room_reading(room)[3] == 0.0 and r.valve_subst == set()  # still, aber stimmig: echter Wert
    assert not room_heating.stale({"state": "0"}, 0.0, "heating", time.time())  # ohne Zeit: nie veraltet


# ------------------------------------------------------------------ C: Räume aus der Langzeitstatistik
def test_hour_statistics_fill_only_gaps_with_checked_offset(tmp_path):
    st = make_store(tmp_path / "d.db")
    eng = engine(st)
    res = restore(eng)
    rows = st.query("SELECT ts, temp, valve, flow FROM room_stats WHERE room = ? ORDER BY ts", (KEY,))
    assert len(rows) == (DAYS - OWN_DAYS) * 24 == res["hours"]
    assert rows[0]["ts"] == START and rows[-1]["ts"] < OWN           # nur Stunden ohne eigene Raumwerte
    assert all(abs(r["temp"] - round(temp(r["ts"]), 2)) < 0.02 for r in rows)  # um +0,5 K berichtigt
    assert {r["valve"] for r in rows if not heating(r["ts"])} == {0.0} and rows[0]["flow"] == flow(START)
    (room,) = res["rooms"]
    assert room["heat_stat"] == HEAT and room["heat_how"] == "adopted" and room["heating"] is True
    assert room["offset"] == pytest.approx(ALIAS_OFF) and room["offset_how"] == "room"
    assert room["first"] == START and room["last"] == OWN - 3600 and res["flow_stat"] == FLOW
    own = st.query("SELECT COUNT(*) AS n FROM room_samples")[0]["n"]
    restore(eng)                                                     # noch einmal: ergänzt nur, nichts doppelt
    assert st.query("SELECT COUNT(*) AS n FROM room_stats")[0]["n"] == len(rows)
    assert st.query("SELECT COUNT(*) AS n FROM room_samples")[0]["n"] == own  # eigene Raumwerte unberührt
    room_alias.put(st, KEY, None)                                    # alter Sensor entfernt: Zeilen bleiben (0.45.1)
    res = restore(eng)
    assert res["hours"] == 0 and res["rooms"][0]["kept"] == len(rows)
    assert st.query("SELECT COUNT(*) AS n FROM room_stats")[0]["n"] == len(rows)


def test_room_model_learns_hour_steps_valve_model_does_not(tmp_path):
    def relearned(path, ha):
        st = make_store(path)
        restore(engine(st), ha)
        since = house_restore.prepare(st, [KEY])
        eng = engine(st)
        asyncio.run(house_restore.relearn(eng, since))
        eng.learn_valves(until=NOW, budget_s=None)
        return st, eng

    st, eng = relearned(tmp_path / "e.db", FakeHA())
    model = eng.rooms[KEY]
    assert model.excitation > 0 and model.rls.updates > 500          # Heizbetrieb gelernt (K·h), Stundenschritte
    learner = room_stats_learn.StatLearner(eng)
    assert learner.hours[-1] < OWN and not set(learner.hours) & learner.own[KEY]
    st2, eng2 = relearned(tmp_path / "f.db", FakeHA(heat=False))       # ohne Heizleistung: nur Auskühlstunden
    assert eng2.rooms[KEY].excitation == 0 and 0 < eng2.rooms[KEY].rls.updates < model.rls.updates
    st.db.execute("DELETE FROM room_stats")
    eng3 = engine(st)
    eng3.valves[KEY] = type(eng.valves[KEY]).from_dict(None, eng.valves[KEY].binary_dev)
    eng3.learn_valves(until=NOW, budget_s=None)
    assert eng3.valves[KEY].to_dict() == eng.valves[KEY].to_dict()   # Ventilmodell sieht room_stats nicht


def test_relearn_of_single_room_includes_hour_statistics(tmp_path):
    st = make_store(tmp_path / "g.db")
    eng = engine(st)
    restore(eng)
    eng.rooms[KEY] = RoomModel("Wohnen")
    eng._last_room_step = NOW
    eng.relearn_rooms({KEY})
    assert eng.rooms[KEY].excitation > 0


def test_result_and_log_per_room_and_room_without_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: float(NOW + 1800))
    st = make_store(tmp_path / "h.db")
    f = found()
    f.rooms.append(Room("Schlafen", "climate.hk_schlafen", area="Schlafen"))
    eng = engine(st, f)
    entry = asyncio.run(house_restore.restore(FakeHA(), eng, REG.lookup(), STAT_LIST))
    by = {r["room"]: r for r in entry["room_stats"]}
    assert by[KEY]["hours"] == entry["room_stat_hours"] > 0 and by[KEY]["heating"]
    assert by["climate.hk_schlafen"]["hours"] == 0 and "Alias" in by["climate.hk_schlafen"]["why"]
    assert by["climate.hk_schlafen"]["heat_why"] == "keine Heizleistung (%) in der Langzeitstatistik gefunden"
    assert house_data.last_restore(st)["room_stats"] == entry["room_stats"]


def test_unfitting_alias_and_missing_reference_leave_room_empty(tmp_path):
    st = make_store(tmp_path / "i.db")
    st.db.execute("DELETE FROM room_samples")                        # kein eigener Bezug, Alias nie geprüft
    res = restore(engine(st))
    assert res["hours"] == 0 and "kein Bezug über das Hausmittel" in res["rooms"][0]["why"]
    assert st.query("SELECT COUNT(*) AS n FROM room_stats")[0]["n"] == 0


def test_keep_days_bound_the_import_and_retention_prunes(tmp_path):
    st = make_store(tmp_path / "j.db")
    ha = FakeHA()
    restore(engine(st, statistics_backfill_days=400, keep_days=365), ha)  # Aufbewahrung kürzer als die Statistik
    assert ha.spans and {a for a, _, _ in ha.spans} == {NOW - 365 * 86400}  # nichts älter als die Aufbewahrung
    assert room_stats.window(engine(st, keep_days=10), NOW + 1800) == (NOW - DAYS * 86400, NOW)  # mind. 365 Tage
    st.db.execute("INSERT INTO room_stats VALUES(?, ?, 20, 0, NULL)", (NOW - 800 * 86400, KEY))
    st.add_sample(NOW - 7200, {"flow": 40.0})
    assert keep_data.prune(st, 720, NOW)["room_stats"] == 1


# ------------------------------------------------------------------ D: Fußbodenräume
def test_floor_rooms_have_their_own_upper_limit():
    assert guards.LIMITS["room_static_gain"] == (0.3, 3.0) and guards.LIMITS["room_static_gain_floor"] == (0.3, 6.0)
    floor, radiator = RoomModel("Bad", floor=True), RoomModel("Bad")
    floor.rls.theta[:3] = [0.02, 0.07, 0.02]                          # (a + f) / b = 4,5
    radiator.rls.theta[:2] = [0.09, 0.02]
    say = lambda m: [x for x in guards.check_room(m)["reasons"] if x.startswith("Heizwirkung/Auskühlen")]
    assert say(floor) == [] and say(radiator) == ["Heizwirkung/Auskühlen 4.5 außerhalb 0.3–3"]
    floor.rls.theta[1] = 0.2                                          # 11: auch für Fußboden zu viel
    assert say(floor) == ["Heizwirkung/Auskühlen 11 außerhalb 0.3–6"]


def test_new_files_small_documented_and_read_only():
    from pathlib import Path
    pkg = Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot"
    for name in ("room_heating.py", "room_stats.py", "room_stats_learn.py"):
        text = (pkg / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 600 and text.startswith('"""'), name
        for word in ("call_service", "set_state", "set_temperature", "latitude"):
            assert word not in text, (name, word)
