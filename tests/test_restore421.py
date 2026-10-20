"""0.42.1 (Erweiterung): Hausdaten bleiben beim Raumwechsel, lassen sich wiederherstellen, Modellgüte ist sichtbar.

Der Fall aus dem Live-Test: 60 Tage Stundenwerte mit Innentemperatur aus den alten Tado-Cloud-Sensoren, davon 20 Tage
mit eigenen Messwerten; die Räume wechseln auf HomeKit-Entitäten, deren Sensoren erst seit gestern Abend Statistik
haben. Geprüft:

* Regressionstest: der Neuaufbau, den 0.42.0 beim Raumwechsel machte (Lernstand verwerfen, Langzeitstatistik und
  Historie neu einlesen, nachlernen), überschreibt keine Innentemperatur mehr und lässt die Raumwerte der alten
  Entitäten stehen – das Hausmodell ist danach so plausibel wie vorher;
* der neue Raumwechsel holt nur die Historie der neuen Räume nach (keine Langzeitstatistik, nichts gelöscht) und lernt
  aus den gespeicherten Daten neu;
* „Hausdaten wiederherstellen“ füllt nach dem Schaden von 0.42.0 die fehlende Innentemperatur aus den Aliassen (alter
  Sensor am Gerät, Name in der Statistik-Liste) und holt die Raumwerte der alten Entität nach;
* „aus gespeicherten Daten neu lernen“ fragt Home Assistant nur nach den Zuständen (Neuaufbau), löscht keine
  Messwerte und lässt Warmwasser- und Personenmodelle stehen;
* Zusammenführen in ``add_hourly``/``aggregate_hour``/``fill_hourly``, Alias-Erkennung, Karte „Modelle“, Zeitraum im
  Zertifikat, die Abfragen und der Ablauf in ``main.App``. Nichts davon schreibt an Home Assistant.
"""

import asyncio
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from heatpilot import (backfill, certificate, guards, house_data, house_restore, main as main_mod, model_card,
                       room_adopt, room_alias, web)
from heatpilot.config import Config
from heatpilot.dhw import DhwModel
from heatpilot.discovery import Discovered, Registry, Room
from heatpilot.engine import Engine
from heatpilot.store import Store

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
H_KW, C_KWH, OFFSET = 0.18, 3.0, -0.2      # Haus: 180 W/K, 3 kWh/K, etwas Fremdwärme
NOW_H = int(time.time()) // 3600 * 3600    # Langzeitstatistik rechnet mit der echten Uhr
DAYS, OWN_DAYS, HIST_DAYS = 60, 20, 10      # Statistik, eigene Messwerte, Recorder-Historie
START = NOW_H - DAYS * 86400
OWN = NOW_H - OWN_DAYS * 86400
HIST = NOW_H - HIST_DAYS * 86400
BORN = NOW_H - 20 * 3600                   # HomeKit-Entitäten „seit gestern Abend“
FROZEN = NOW_H + 1800                      # Uhr in der Mitte der Stunde anhalten: die Suite läuft über Stundengrenzen


class _FrozenDateTime(datetime):
    """datetime.now() für backfill: dieselbe angehaltene Uhr wie time.time()."""

    @classmethod
    def now(cls, tz=None):
        return datetime.fromtimestamp(FROZEN, tz)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Ohne angehaltene Uhr rechnet der Code nach einem Stundenwechsel während der Suite eine Stunde mehr als NOW_H."""
    monkeypatch.setattr(time, "time", lambda: float(FROZEN))
    monkeypatch.setattr(backfill, "datetime", _FrozenDateTime)
STEP = 300
OLD = {"climate.wohnzimmer": ("sensor.wohnzimmer_temperature", 0.3),
       "climate.kueche": ("sensor.kueche_temperature", -0.3)}
NEW = {"climate.hk_va01": "sensor.hk_va01_temperature", "climate.hk_va02": None}
HK = "homekit_controller"


def t_out(ts: float) -> float:
    """Außentemperatur, je Stunde fest (Statistik, Messpunkte und Historie zeigen dasselbe)."""
    h = int(ts) // 3600 * 3600
    d = (h - START) / 86400
    return 4.0 + 4.0 * math.sin(2 * math.pi * d / 9) + 2.0 * math.cos(2 * math.pi * (h % 86400) / 86400)


def t_in(ts: float) -> float:
    """Innentemperatur (Mittel der Räume), je Stunde fest: Tagesgang und ein 5-Tage-Gang."""
    h = int(ts) // 3600 * 3600
    d = (h - START) / 86400
    return 20.5 + 0.6 * math.sin(2 * math.pi * d / 5) + 0.4 * math.sin(2 * math.pi * (h % 86400) / 86400)


def gas(h: int) -> float:
    """Gas = H·(innen − außen) + C·dT_innen/dt + Achsabschnitt (Trägheit C/H ≈ 17 h)."""
    return round(max(H_KW * (t_in(h) - t_out(h)) + C_KWH * (t_in(h + 3600) - t_in(h - 3600)) / 2 + OFFSET, 0.0), 4)


_METER: dict[int, float] = {}


def meter(ts: float) -> float:
    """Zählerstand (kWh): Summe der Stunden, in der laufenden Stunde anteilig."""
    if not _METER:
        total = 0.0
        for x in range(START, NOW_H + 2 * 3600, 3600):
            _METER[x] = total
            total += gas(x)
    h = int(ts) // 3600 * 3600
    return round(_METER[h] + gas(h) * (ts - h) / 3600, 4)


def cfg() -> Config:
    return Config(backfill_days=HIST_DAYS, statistics_backfill_days=DAYS)


def found(rooms: str = "new") -> Discovered:
    if rooms == "old":
        rs = [Room("Wohnzimmer", "climate.wohnzimmer", temp_sensor=OLD["climate.wohnzimmer"][0], platform="tado"),
              Room("Küche", "climate.kueche", temp_sensor=OLD["climate.kueche"][0], platform="tado")]
    else:
        rs = [Room("Wohnzimmer", "climate.hk_va01", temp_sensor=NEW["climate.hk_va01"], platform=HK, area="Wohnzimmer"),
              Room("Küche", "climate.hk_va02", platform=HK, area="Küche")]
    return Discovered(rooms=rs, outdoor=["sensor.out"], gas="sensor.gas", gas_unit="kWh")


def make_store(path) -> Store:
    """So stand es vor dem Wechsel: 60 Tage Stundenwerte mit Innentemperatur, 20 Tage eigene Messwerte."""
    st = Store(path)
    rows = []
    for h in range(START, NOW_H, 3600):
        own = h >= OWN
        rows.append((h, round(t_out(h), 3), round(t_in(h), 3), gas(h), 0.0 if own else None,
                     None, 0.0 if own else None, None, 0.0 if own else None, 0.0))
    st.add_hourly(rows)
    for ts in range(OWN, NOW_H, STEP):
        st.add_sample(ts, {"t_out": round(t_out(ts), 3), "gas": meter(ts), "dhw": 0, "vacation": 0, "away_long": 0})
        st.add_room_samples((ts, key, round(t_in(ts) + off, 3), 21.0, 20.0, None) for key, (_, off) in OLD.items())
    st.put("history_start", OWN - 10 * 86400)
    st.put("backfill_done", True)
    st.commit()
    return st


def damage(st: Store) -> None:
    """Was der Neuaufbau von 0.42.0 hinterließ: Innentemperatur der Statistik-Stunden und der Recorder-Stunden weg,
    Raumwerte der alten Entitäten im Recorder-Fenster gelöscht."""
    st.db.execute("UPDATE hourly SET t_in = NULL WHERE ts < ? OR (ts >= ? AND ts < ?)", (OWN, HIST, BORN))
    st.db.execute("DELETE FROM room_samples WHERE ts >= ? AND room IN ('climate.wohnzimmer', 'climate.kueche')",
                  (HIST,))
    st.commit()


def climate_state(temp: float) -> dict:
    return {"current_temperature": round(temp, 2), "temperature": 21.0, "hvac_action": "idle"}


class FakeHA:
    """Home Assistant nur zum Lesen: Langzeitstatistik, Historie, Statistik-Liste, Registry, Zustände.
    Merkt jeden Aufruf; jeder Schreibversuch scheitert."""

    def __init__(self, states=None):
        self.calls: list[str] = []
        self.states = states or []
        self.last_error = ""

    async def statistics(self, ids, s, e, period="hour"):
        self.calls.append("statistics")
        a, b = int(s.timestamp()), int(e.timestamp())
        out = {}
        for sid in ids:
            first = BORN - BORN % 3600 if sid == "sensor.hk_va01_temperature" else START
            pts = []
            for h in range(max(a, first), min(b, NOW_H), 3600):
                if sid == "sensor.gas":
                    pts.append({"start": h * 1000, "change": gas(h), "sum": meter(h + 3600)})
                elif sid == "sensor.out":
                    pts.append({"start": h * 1000, "mean": round(t_out(h), 3)})
                elif sid in {v[0] for v in OLD.values()}:
                    off = next(o for s2, o in OLD.values() if s2 == sid)
                    pts.append({"start": h * 1000, "mean": round(t_in(h) + off, 3)})
                elif sid == "sensor.hk_va01_temperature":
                    pts.append({"start": h * 1000, "mean": round(t_in(h) + 0.3, 3)})
            if pts:
                out[sid] = pts
        return out

    async def history(self, ids, s, e, attributes=False):
        self.calls.append("history")
        a, b = int(s.timestamp()), int(e.timestamp())
        out = {}
        for eid in ids:
            born = BORN if eid in NEW else HIST
            pts = []
            for ts in range(max(a - a % 900, born), b, 900):
                if eid == "sensor.out":
                    pts.append({"s": str(round(t_out(ts), 3)), "lu": ts})
                elif eid == "sensor.gas":
                    pts.append({"s": str(meter(ts)), "lu": ts})
                elif eid in OLD or eid in NEW:
                    off = OLD[eid][1] if eid in OLD else (0.3 if eid == "climate.hk_va01" else -0.3)
                    pts.append({"s": "heat", "lu": ts, **({"a": climate_state(t_in(ts) + off)} if attributes else {})})
            if pts:
                out[eid] = pts
        return out

    async def list_statistic_ids(self, statistic_type="mean"):
        self.calls.append("list_statistic_ids")
        temp = [{"statistic_id": s, "statistics_unit_of_measurement": "°C", "has_mean": True, "name": None}
                for s in ("sensor.out", "sensor.wohnzimmer_temperature", "sensor.kueche_temperature",
                          "sensor.hk_va01_temperature")]
        return temp + [{"statistic_id": "sensor.gas", "statistics_unit_of_measurement": "kWh", "has_sum": True}]

    async def get_states(self):
        self.calls.append("get_states")
        return self.states

    async def entity_registry(self):
        self.calls.append("entity_registry")
        return REG.entities

    async def device_registry(self):
        return REG.devices

    async def area_registry(self):
        return REG.areas

    def __getattr__(self, name):  # call_service, set_state …: verboten
        raise AssertionError(f"Home Assistant darf nicht {name} – nur lesen")


# Tado-Cloud noch eingerichtet: das Wohnzimmer-Gerät hat seinen Temperatursensor; die Küche steht noch in den Zuständen,
# ihr Gerät aber nicht mehr in der Registry (ihr alter Sensor nur über den Namen in der Statistik-Liste)
REG = Registry(
    [{"entity_id": "climate.wohnzimmer", "platform": "tado", "device_id": "d1"},
     {"entity_id": "sensor.wohnzimmer_temperature", "platform": "tado", "device_id": "d1"},
     {"entity_id": "climate.hk_va01", "platform": HK, "device_id": "d2"},
     {"entity_id": "climate.hk_va02", "platform": HK, "device_id": "d3"}],
    [{"id": "d1", "name": "Wohnzimmer", "area_id": "wz"}, {"id": "d2", "name": "VA01", "area_id": "wz"},
     {"id": "d3", "name": "VA02", "area_id": "ku"}],
    [{"area_id": "wz", "name": "Wohnzimmer"}, {"area_id": "ku", "name": "Küche"}])
STATES = {"climate.wohnzimmer": {"state": "heat", "attributes": {"friendly_name": "Wohnzimmer", **climate_state(20.8)}},
          "sensor.wohnzimmer_temperature": {"state": "20.8", "attributes": {"device_class": "temperature",
                                                                            "unit_of_measurement": "°C"}},
          "climate.kueche": {"state": "heat", "attributes": {"friendly_name": "Küche", **climate_state(20.2)}},
          "climate.hk_va01": {"state": "heat", "attributes": {"friendly_name": "VA01", **climate_state(20.8)}},
          "climate.hk_va02": {"state": "heat", "attributes": {"friendly_name": "VA02", **climate_state(20.2)}}}


def t_in_rows(st: Store) -> dict[int, float | None]:
    return {r["ts"]: r["t_in"] for r in st.query("SELECT ts, t_in FROM hourly ORDER BY ts")}


def counts(st: Store) -> dict[str, int]:
    return {t: st.query(f"SELECT COUNT(*) FROM {t}")[0][0]
            for t in ("samples", "room_samples", "hourly", "person_samples", "valve_subst")}


def learned_house(st: Store, rooms: str = "new") -> Engine:
    """Aus gespeicherten Daten neu lernen wie ``App.relearn``: Modelle verwerfen, neue Engine, nachlernen."""
    since = house_restore.prepare(st)
    eng = Engine(cfg(), st, found(rooms))
    asyncio.run(house_restore.relearn(eng, since))
    return eng


def h_w(eng: Engine) -> float:
    return eng.house.h_kw_per_k * 1000


@pytest.fixture(scope="module")
def before(tmp_path_factory):
    """Hausmodell vor dem Wechsel (alte Räume, alle Daten)."""
    eng = learned_house(make_store(tmp_path_factory.mktemp("b") / "b.db"), "old")
    assert guards.check_house(eng.house)["ok"], guards.check_house(eng.house)
    return h_w(eng)


# ------------------------------------------------------------------ Regressionstest: der Fall aus dem Live-Test
def test_rebuild_after_room_change_keeps_indoor_temperature_and_old_room_values(tmp_path, before):
    """Genau der Neuaufbau von 0.42.0 nach dem Wechsel auf HomeKit: Lernstand verwerfen, Langzeitstatistik (die neuen
    Sensoren haben erst seit gestern Abend welche) und Historie neu einlesen, nachlernen."""
    st = make_store(tmp_path / "r.db")
    t0, n0 = t_in_rows(st), counts(st)
    old_rows = st.query("SELECT COUNT(*) FROM room_samples WHERE room = 'climate.wohnzimmer' AND ts >= ?", (HIST,))[0][0]
    eng = Engine(cfg(), st, found("new"))
    st.reset_learning("2000-01-01")
    asyncio.run(backfill.run_backfill(FakeHA(), eng))
    t1 = t_in_rows(st)
    assert set(t1) == set(t0) and all(t1[h] is not None for h in t0)          # keine Stunde ohne Innentemperatur
    assert max(abs(t1[h] - t0[h]) for h in t0 if h < OWN) < 1e-6               # Statistik-Stunden unverändert
    assert max(abs(t1[h] - t0[h]) for h in t0 if h >= OWN) < 0.35              # eigene Stunden: Mittel der Räume
    # „Optisch nur bis gestern Abend 21 Uhr“: die Raumwerte der alten Entitäten im Recorder-Fenster bleiben
    assert st.query("SELECT COUNT(*) FROM room_samples WHERE room = 'climate.wohnzimmer' AND ts >= ?",
                    (HIST,))[0][0] == old_rows
    assert st.query("SELECT MIN(ts) FROM room_samples WHERE room = 'climate.hk_va01'")[0][0] >= BORN
    assert counts(st)["hourly"] == n0["hourly"]
    chk = guards.check_house(eng.house)
    assert chk["ok"], chk
    assert abs(h_w(eng) - before) / before < 0.05                              # gleich plausibel wie vorher
    cov = house_data.coverage(st)
    assert cov["no_t_in"] == 0 and cov["usable_from"] == house_data._iso(START)


def test_room_change_fetches_only_new_rooms_and_relearns_without_deleting(tmp_path, before):
    """Der neue Raumwechsel (``App.update_rooms``): nur die Historie der neuen Räume, nichts überschrieben, dann aus den
    gespeicherten Daten neu lernen."""
    st = make_store(tmp_path / "c.db")
    st.add_room_samples([(NOW_H - 3600, "climate.hk_va01", 25.0, 21.0, 50.0, None)])  # schon gespeichert: bleibt
    st.commit()
    t0, n0 = t_in_rows(st), counts(st)
    ha = FakeHA()
    eng = Engine(cfg(), st, found("new"))
    added = asyncio.run(backfill.backfill_rooms(ha, eng, eng.found.rooms, HIST_DAYS))
    assert set(ha.calls) == {"history"}                                         # keine Langzeitstatistik
    rows = st.query("SELECT ts, temp FROM room_samples WHERE room = 'climate.hk_va01' ORDER BY ts")
    assert rows[0]["ts"] >= BORN and {r["temp"] for r in rows if r["ts"] == NOW_H - 3600} == {25.0}
    assert added == counts(st)["room_samples"] - n0["room_samples"] > 0
    assert {k: v for k, v in counts(st).items() if k != "room_samples"} == {k: v for k, v in n0.items()
                                                                              if k != "room_samples"}
    eng = learned_house(st)
    assert counts(st)["samples"] == n0["samples"] and counts(st)["hourly"] == n0["hourly"]
    t1 = t_in_rows(st)
    assert all(t1[h] is not None for h in t0) and max(abs(t1[h] - t0[h]) for h in t0 if h < OWN) < 1e-6
    assert guards.check_house(eng.house)["ok"] and abs(h_w(eng) - before) / before < 0.05


def test_restore_fills_indoor_temperature_from_aliases_and_old_room_history(tmp_path, before):
    """Nach dem Schaden von 0.42.0: Aliasse finden (alter Sensor am Gerät, Name in der Statistik-Liste),
    Hausdaten wiederherstellen, neu lernen – das Hausmodell ist wieder wie vorher."""
    st = make_store(tmp_path / "w.db")
    damage(st)
    eng = learned_house(st)
    card = model_card.collect(eng)["house"]
    assert card["restore"] and "Hausdaten wiederherstellen" in card["warning"] and card["data"]["no_t_in"] > 1000
    ha = FakeHA()
    idx = REG.lookup()
    eng.states.update(STATES)
    stat_list = asyncio.run(ha.list_statistic_ids())
    a = room_alias.detect(st, eng.found, "climate.wohnzimmer", "climate.hk_va01", eng.states, idx, stat_list)
    assert a == {"stat": "sensor.wohnzimmer_temperature", "from": "climate.wohnzimmer", "how": "device"}
    room_alias.put(st, "climate.hk_va01", a["stat"], a["from"], a["how"])
    st.put(room_adopt.KV_LOG, [{"ts": 1, "from": "climate.kueche", "to": "climate.hk_va02", "rows": 1}])
    n0 = counts(st)
    res = asyncio.run(house_restore.restore(ha, eng, idx, stat_list))
    assert set(ha.calls) <= {"list_statistic_ids", "statistics", "history"}
    # Küche: kein Gerät mehr in der Registry – ihr alter Sensor über den Namen in der Statistik-Liste
    assert res["assigned"] == [{"room": "climate.hk_va02", "name": "Küche", "stat": "sensor.kueche_temperature",
                                "how": "statistics"}]
    assert res["no_t_in_after"] == 0 and res["usable_after"] > res["usable_before"] + 1000
    t1, ref = t_in_rows(st), t_in_rows(make_store(tmp_path / "ref.db"))
    assert max(abs(t1[h] - ref[h]) for h in ref) < 0.35
    assert max(abs(t1[h] - ref[h]) for h in ref if h < OWN) < 0.002            # alter Sensor je Raum, gemittelt
    # Raumwerte der alten Entitäten (noch in HA) im Recorder-Fenster: unter dem neuen Raum nachgeholt
    got = st.query("SELECT room, MIN(ts) AS a, COUNT(*) AS n FROM room_samples WHERE room LIKE 'climate.hk_%' "
                   "GROUP BY room")
    assert {r["room"] for r in got} == {"climate.hk_va01", "climate.hk_va02"}
    assert max(r["a"] for r in got) <= HIST + 3600 and res["room_rows"] == sum(r["n"] for r in got)
    assert all(counts(st)[k] >= n0[k] for k in n0)                              # nichts gelöscht
    eng = learned_house(st)
    assert guards.check_house(eng.house)["ok"] and abs(h_w(eng) - before) / before < 0.05
    assert not model_card.collect(eng)["house"]["restore"]
    assert house_data.last_restore(st)["kind"] == "relearn"
    assert [e["kind"] for e in st.get(house_restore.KV_LOG)][-2:] == ["restore", "relearn"]


def test_relearn_deletes_nothing_and_keeps_dhw_persons_and_counters(tmp_path):
    st = make_store(tmp_path / "n.db")
    st.add_person_samples([(OWN, "person.a", 1, 0.0)])
    st.add_daily("2026-01-01", "ignored_values", 3)
    dhw = DhwModel()
    dhw.charges = 7
    st.put("model.dhw", dhw.to_dict())
    st.put("model.person.person.a", {"keep": True})
    st.commit()
    n0, t0 = counts(st), t_in_rows(st)
    eng = learned_house(st)
    assert counts(st) == n0 and t_in_rows(st) == pytest.approx(t0, abs=0.35)
    assert eng.dhw.charges == 7 and st.get("model.dhw")["charges"] == 7     # Warmwasser bleibt
    assert st.get("model.person.person.a") == {"keep": True}                # Personen bleiben
    assert st.daily("2026-01-01")["2026-01-01"]["ignored_values"] == 3   # Zähler aus der Abtastung bleiben
    assert st.get("backfill_done") is True and eng.house.rls.updates > 1000
    assert st.query("SELECT COUNT(*) FROM snapshots WHERE kind = 'house'")[0][0] >= DAYS - 1  # Lernkurve neu


# ------------------------------------------------------------------ Zusammenführen
def test_add_hourly_merges_and_fill_only_fills(tmp_path):
    st = Store(tmp_path / "m.db")
    st.add_hourly([(3600, 5.0, 20.0, 1.5, 0.1, 1.0, 0.0, None, 0.1, 0.0)])
    st.add_hourly([(3600, None, None, 2.0)])                       # nichts Neues bei Temperaturen: bleiben
    r = dict(st.query("SELECT * FROM hourly")[0])
    assert (r["t_out"], r["t_in"], r["gas_kwh"], r["dhw_frac"], r["home_frac"]) == (5.0, 20.0, 2.0, 0.1, 1.0)
    st.add_hourly([(3600, 6.0, 21.0, None, None, None, None, 9.0)], replace=("gas_kwh", "gas_spike_kwh"))
    r = dict(st.query("SELECT * FROM hourly")[0])
    assert (r["t_out"], r["t_in"], r["gas_kwh"], r["gas_spike_kwh"]) == (6.0, 21.0, None, 9.0)
    st.add_hourly([(7200, None, None, 1.0)])
    assert st.fill_hourly([(3600, 1.0, 1.0), (7200, 4.0, 19.0), (10800, 4.0, 19.0)]) == 1  # nur Lücken, nur da
    rows = {r["ts"]: (r["t_out"], r["t_in"]) for r in st.query("SELECT * FROM hourly")}
    assert rows == {3600: (6.0, 21.0), 7200: (4.0, 19.0)}


def test_aggregate_hour_keeps_stored_values_it_does_not_know(tmp_path):
    st = Store(tmp_path / "a.db")
    eng = Engine(Config(), st, Discovered(rooms=[Room("Wz", "climate.wz")], gas="sensor.gas", gas_unit="kWh"))
    h = 1_800_000_000 - 1_800_000_000 % 3600
    st.add_hourly([(h, 3.0, 20.5, 2.0, None, None, None, None)])
    for k, ts in enumerate(range(h, h + 3600, STEP)):                # Messwerte ohne Raum und ohne Gaszähler
        st.add_sample(ts, {"t_out": 4.0, "dhw": 0})
    st.commit()
    eng.aggregate_hour(h, learn=False)
    r = dict(st.query("SELECT * FROM hourly WHERE ts = ?", (h,))[0])
    assert (r["t_in"], r["gas_kwh"], r["t_out"], r["dhw_frac"]) == (20.5, 2.0, 4.0, 0.0)
    for k, ts in enumerate(range(h, h + 3601, STEP)):                # jetzt mit Zählerstand: ein Sprung
        st.add_sample(ts, {"t_out": 4.0, "dhw": 0, "gas": 100.0 + (500.0 if ts == h + 3600 else 0.0)})
    st.commit()
    eng.aggregate_hour(h, learn=False)
    r = dict(st.query("SELECT * FROM hourly WHERE ts = ?", (h,))[0])
    assert (r["gas_kwh"], r["gas_spike_kwh"], r["t_in"]) == (None, 500.0, 20.5)  # entschieden: nicht lernen


# ------------------------------------------------------------------ Aliasse
def test_alias_detection_registry_statistics_none_and_ambiguous(tmp_path):
    st = Store(tmp_path / "al.db")
    f = found("new")
    idx = REG.lookup()
    stats = asyncio.run(FakeHA().list_statistic_ids())
    det = room_alias.detect
    assert det(st, f, "climate.wohnzimmer", "climate.hk_va01", STATES, idx, stats)["how"] == "device"
    assert det(st, f, "climate.kueche", "climate.hk_va02", STATES, idx, stats) == {
        "stat": "sensor.kueche_temperature", "from": "climate.kueche", "how": "statistics"}
    assert det(st, f, "climate.gaeste", "climate.hk_va02", STATES, idx, stats) is None       # kein Treffer
    twice = stats + [{"statistic_id": "sensor.kuche_temp", "statistics_unit_of_measurement": "°C", "mean_type": 1}]
    assert det(st, f, "climate.kueche", "climate.hk_va02", STATES, idx, twice) is None       # nicht eindeutig
    fahrenheit = [{"statistic_id": "sensor.kueche_temperature", "statistics_unit_of_measurement": "°F",
                   "has_mean": True}]
    assert det(st, f, "climate.kueche", "climate.hk_va02", STATES, idx, fahrenheit) is None
    assert det(st, f, "climate.kueche", "climate.hk_va02", STATES, None, None) is None       # ohne Liste, ohne Registry
    # vergeben: aktueller Sensor eines Raums, Außenfühler, Alias eines anderen Raums
    room_alias.put(st, "climate.hk_va01", "sensor.kueche_temperature", how="user")
    assert det(st, f, "climate.kueche", "climate.hk_va02", STATES, idx, stats) is None
    assert room_alias.snorm("sensor.kueche_temperature") == room_alias.snorm("Küche Temperatur") == "kuche"
    assert room_alias.sources(st, f) == [("climate.hk_va01", "sensor.hk_va01_temperature", "sensor.kueche_temperature"),
                                         ("climate.hk_va02", None, None)]


def test_alias_overview_check_and_auto_assign(tmp_path):
    st = Store(tmp_path / "ao.db")
    f = found("new")
    stats = asyncio.run(FakeHA().list_statistic_ids())
    ov = room_alias.overview(st, f, STATES, REG.lookup(), stats)
    by = {r["key"]: r for r in ov["rooms"]}
    assert by["climate.hk_va01"]["suggested"] == "sensor.wohnzimmer_temperature"   # Name des Raums
    assert by["climate.hk_va02"]["suggested"] == "sensor.kueche_temperature"       # Bereich „Küche“
    assert {s["stat"] for s in ov["stats"]} == {"sensor.wohnzimmer_temperature", "sensor.kueche_temperature"}
    assert room_alias.check(st, f, "climate.x", "sensor.kueche_temperature", stats).startswith("„climate.x“ ist kein")
    assert "Außenfühler" in room_alias.check(st, f, "climate.hk_va02", "sensor.out", stats)
    assert "°C" in room_alias.check(st, f, "climate.hk_va02", "sensor.gas", stats)
    assert "keine Statistik-ID" in room_alias.check(st, f, "climate.hk_va02", "Robert'); DROP", stats)
    assert room_alias.check(st, f, "climate.hk_va02", None, None) is None
    # schon vergeben: aktueller Sensor eines anderen Raums (wie die Auswahl der Oberfläche)
    assert "aktuelle Temperatursensor von Wohnzimmer" in room_alias.check(
        st, f, "climate.hk_va02", "sensor.hk_va01_temperature", stats)
    done = room_alias.auto_assign(st, f, STATES, REG.lookup(), stats)
    assert [d["stat"] for d in done] == ["sensor.wohnzimmer_temperature", "sensor.kueche_temperature"]
    assert room_alias.auto_assign(st, f, STATES, REG.lookup(), stats) == []        # schon gesetzt: bleibt


# ------------------------------------------------------------------ Karte „Modelle“, Zertifikat
def test_model_card_texts_and_warning(tmp_path):
    st = make_store(tmp_path / "mc.db")
    damage(st)
    eng = learned_house(st)
    m = model_card.collect(eng)
    h = m["house"]
    assert h["restore"] and h["data"]["usable_from"] == house_data._iso(OWN)  # nur noch eigene Stunden
    assert re.search(r"Stunden \(\d\d\.\d\d\.\d{4}–\d\d\.\d\d\.\d{4}\) haben Gas und Außentemperatur", h["warning"])
    # gelernt (Lernschritte) getrennt von den Stunden mit allen Werten (seit der Prüfung von 0.42.1)
    assert h["learned_text"] == (f"aus {h['hours_learned']} Stunden und {h['days_learned']} Tageswerten gelernt – von "
                                 f"{h['data']['usable']} Stunden mit Gas, Außen- und Innentemperatur seit "
                                 f"{house_data._date(h['data']['usable_from'])}")
    assert m["rooms"]["total"] == 2 and m["rooms"]["text"].endswith("von 2 plausibel")
    assert m["valves"]["total"] == 2 and {i["state"] for i in m["valves"]["items"]} == {"lernt nach"}
    assert m["dhw"]["state"] == "lernt" and m["dhw"]["text"].startswith("lernt – ")
    assert m["last_restore"]["kind"] == "relearn"
    ok = learned_house(make_store(tmp_path / "ok.db"))
    h = model_card.collect(ok)["house"]
    assert h["state"] == "plausibel" and h["warning"] is None and not h["restore"]
    assert h["heat_loss_sd_w_per_k"] is not None and h["heat_loss_sd_w_per_k"] < 0.2 * h["heat_loss_w_per_k"]
    assert re.match(r"plausibel · Wärmeverlust \d+ ± \d+ W/K · Vorhersagefehler ±\d+,\d\d kW je (Tag|Stunde) · aus ",
                    h["text"]), h["text"]
    assert ok.summary()["models"]["house"]["ok"]


def test_certificate_names_period_and_missing_hours(tmp_path):
    st = make_store(tmp_path / "ce.db")
    damage(st)
    eng = learned_house(st)
    data = certificate.collect(eng)
    b = data["basis"]
    assert b["house_from"] == house_data._iso(OWN) and b["year_from"] == house_data._iso(START)
    miss = {m["key"]: m for m in b["missing"]}
    assert miss["no_t_in"]["hours"] > 1000 and "Innentemperatur" in miss["no_t_in"]["reason"]
    assert b["note"].startswith("Hausmodell aus ") and "nicht berücksichtigt: " in b["note"]
    assert any("ohne Innentemperatur" in p for p in data["preliminary"])
    assert certificate.pdf(eng)[:5] == b"%PDF-"
    ok = certificate.collect(learned_house(make_store(tmp_path / "ok.db")))
    assert ok["basis"]["missing"] == [] and not any("Innentemperatur" in p for p in ok["preliminary"])


# ------------------------------------------------------------------ Abfragen und Ablauf in main.App
def app_for(tmp_path, monkeypatch, st: Store, rooms: str = "new"):
    """main.App mit FakeHA – ``_build`` echt, Konfiguration und Erkennung fest."""
    app = main_mod.App.__new__(main_mod.App)
    app.ha, app.store, app.pending, app._lock, app._reset = FakeHA([{"entity_id": k, **v} for k, v in STATES.items()]), \
        st, {}, asyncio.Lock(), False
    app._registry, app._ems_task, app._status, app.eng, app.job = (time.time() + 10 ** 6, REG), None, "läuft", None, None
    app.cfg = cfg()
    monkeypatch.setattr(main_mod.Config, "load", staticmethod(cfg))
    monkeypatch.setattr(main_mod, "discover", lambda states, reg, c: found(rooms))
    app._restart_emsesp = lambda: None
    return app


async def call(app, steps):
    client = TestClient(TestServer(web.make_app(app)))
    await client.start_server()
    out = []
    try:
        for method, path, body in steps:
            if method == "WAIT":
                while app.job is None or app.job.get("running"):
                    await asyncio.sleep(0.05)
                continue
            r = await (client.get(path) if method == "GET" else client.post(path, json=body))
            out.append((r.status, await r.json()))
    finally:
        await client.close()
    return out


def test_api_restore_relearn_models_and_aliases(tmp_path, monkeypatch):
    st = make_store(tmp_path / "api.db")
    damage(st)
    app = app_for(tmp_path, monkeypatch, st)

    async def go():
        await app.build(backfill=False)
        app.eng.states.update(STATES)
        return await call(app, [
            ("GET", "/api/models", None),
            ("GET", "/api/rooms/aliases", None),
            ("POST", "/api/rooms/aliases", {"room": "climate.hk_va02", "stat": "sensor.out"}),
            ("POST", "/api/rooms/aliases", {"room": "climate.nope", "stat": ""}),
            ("POST", "/api/rooms/aliases", {"room": "climate.hk_va02", "stat": "sensor.kueche_temperature"}),
            ("POST", "/api/house/restore", None),
            ("POST", "/api/house/relearn", None),
            ("WAIT", None, None),
            ("GET", "/api/house/job", None),
            ("GET", "/api/models", None),
            ("POST", "/api/house/relearn", None),
            ("WAIT", None, None),
            ("GET", "/api/summary", None),
        ])

    res = asyncio.run(go())
    (s0, m0), (s1, al), (s2, e2), (s3, e3), (s4, ok4), (s5, r5), (s6, busy), (s7, job), (s8, m8), (s9, r9), \
        (s10, summ) = res
    assert s0 == 200 and m0["house"]["restore"] and m0["busy"] is None
    assert s1 == 200 and {r["key"]: r["suggested"] for r in al["rooms"]}["climate.hk_va02"] == "sensor.kueche_temperature"
    assert (s2, s3) == (400, 400) and "Außenfühler" in e2["error"] and "kein aktueller Raum" in e3["error"]
    assert s4 == 200 and ok4["alias"]["stat"] == "sensor.kueche_temperature" and ok4["alias"]["how"] == "user"
    assert s5 == 202 and r5["started"] == "Hausdaten wiederherstellen"
    assert s6 == 409 and not busy["ok"]                                          # läuft schon
    assert s7 == 200 and job["job"]["kind"] == "restore" and job["job"]["error"] is None
    assert job["job"]["result"]["no_t_in_after"] == 0 and job["job"]["result"]["relearn"]["kind"] == "relearn"
    assert s8 == 200 and not m8["house"]["restore"] and m8["house"]["ok"]
    assert s9 == 202 and s10 == 200 and summ["models"]["house"]["ok"]
    assert app.job["kind"] == "relearn" and app.job["error"] is None and app.status == "läuft"
    assert not {c for c in app.ha.calls} - {"statistics", "history", "list_statistic_ids", "get_states"}
    # neu lernen fragt Home Assistant nur nach den aktuellen Zuständen (Neuaufbau) – keine Historie, keine Statistik
    n = len(app.ha.calls)
    rows = counts(st)
    asyncio.run(app.relearn())
    assert set(app.ha.calls[n:]) == {"get_states"} and counts(st) == rows


def test_apply_settings_room_change_fetches_rooms_instead_of_rebuilding(tmp_path, monkeypatch):
    app = main_mod.App.__new__(main_mod.App)
    app.cfg, app.store = Config(), Store(tmp_path / "s.db")
    rooms = [[Room("Wz", "climate.wohnzimmer")]]
    power = [["sensor.l1"]]
    app.eng = SimpleNamespace(found=Discovered(rooms=rooms[0], power=power[0]))
    calls = []

    async def build(backfill=False):
        app.eng = SimpleNamespace(found=Discovered(rooms=list(rooms[0]), power=list(power[0])))

    async def reload_history():
        calls.append("reload")

    async def update_rooms(climates):
        calls.append(("rooms", sorted(climates)))
        return {}

    app.build, app.reload_history, app.update_rooms, app.job = build, reload_history, update_rooms, None
    monkeypatch.setattr(main_mod, "save_settings", lambda clean: None)
    monkeypatch.setattr(main_mod, "set_debug", lambda flag: None)

    async def go():
        rooms[0] = [Room("Wz", "climate.hk_va01")]
        assert await app.apply_settings({"night_plan_auto": True}) == []
        await asyncio.sleep(0)
        assert calls == [("rooms", ["climate.hk_va01"])]                 # nur die neuen Räume, kein Neuaufbau
        assert app.job["kind"] == "rooms" and not app.job["running"]     # als Ablauf sichtbar (api/house/job)
        rooms[0], power[0] = [Room("Wz", "climate.hk_va02")], ["sensor.l2"]
        assert await app.apply_settings({"power_sensors": power[0]}) == []
        await asyncio.sleep(0)
        assert calls[1:] == ["reload"]                                   # Leistungssensoren: wie bisher

    asyncio.run(go())


def test_update_rooms_flow_in_app(tmp_path, monkeypatch):
    st = make_store(tmp_path / "u.db")
    app = app_for(tmp_path, monkeypatch, st)
    n0 = counts(st)

    async def go():
        await app.build(backfill=False)
        await app.update_rooms({"climate.hk_va01", "climate.hk_va02"})

    asyncio.run(go())
    n1 = counts(st)
    assert n1["room_samples"] > n0["room_samples"] and n1["samples"] == n0["samples"] and n1["hourly"] == n0["hourly"]
    assert "statistics" not in app.ha.calls and app.status == "läuft"
    assert guards.check_house(app.eng.house)["ok"] and house_data.last_restore(st)["kind"] == "relearn"


# ------------------------------------------------------------------ Lesbarkeit, nur lesen
def test_new_files_small_with_purpose_docstrings_and_read_only():
    names = ("room_alias.py", "house_data.py", "house_restore.py", "model_card.py", "web_restore.py", "app_jobs.py",
             "room_adopt.py", "web_adopt.py")
    for name in names:
        src = (PKG / name).read_text(encoding="utf-8")
        assert src.startswith('"""') and src.count("\n") <= 600, name
        for fn in re.split(r"\n(?=def |    def |    async def |async def )", src)[1:]:
            assert fn.count("\n") <= 60, (name, fn.splitlines()[0])
        for word in ("call_service", "set_state", "set_temperature", "turn_on", "delete_range"):
            assert word not in src, (name, word)
    for name in ("backfill.py", "store.py", "main.py", "engine_summary.py", "certificate.py", "ha_client.py", "web.py"):
        assert (PKG / name).read_text(encoding="utf-8").count("\n") <= 600, name
    src = (PKG / "ha_client.py").read_text(encoding="utf-8")
    assert '"recorder/list_statistic_ids"' in src
    json.dumps(room_alias.HOW_TEXT)  # Texte für die Oberfläche
