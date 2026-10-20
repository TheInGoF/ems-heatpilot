"""0.42.1 – Funde der Prüfung, nachgestellt und behoben.

Am Haus aus tests/test_restore421.py (60 Tage Stundenwerte, 20 Tage eigene Messwerte, Wechsel Tado-Cloud → HomeKit),
mit deutlich verschieden warmen Räumen (±1 bis ±2 K um das Hausmittel):

* Teilmittel: Ein Neuaufbau bzw. „Hausdaten wiederherstellen“ mit nur einem alten Sensor (oder einem behaltenen Sensor)
  ersetzt keine gespeicherte Innentemperatur; Lücken füllt es je Quelle um ihren Versatz zum Hausmittel berichtigt –
  das Hausmodell bleibt, wie es war; ``aggregate_hour`` mit fehlenden Räumen ebenso;
* falscher Alias: abgewiesen, wenn ihn ein anderer Raum nutzt; ein unpassender bleibt beim Einlesen weg (mit Grund);
  entfernt nimmt er die Stunden zurück, die er gefüllt hat;
* Neuaufbau: Kesselwerte direkt von EMS-ESP und Brennerereignisse ohne HA-Entität bleiben, „Historie neu einlesen“
  behält Warmwasser- und Personenmodelle;
* Übernahme bei Überschneidung: spaltenweise zusammenführen, gemessene Ventilöffnung geht vor; die Übersicht nennt die
  Überschneidung;
* Wiederherstellen holt die Raumwerte einer noch vorhandenen alten Entität auch ohne Alias (Protokoll der Übernahmen)
  und sagt je Entität, was nachgeholt wurde;
* Neu lernen verwirft die Modelle verwaister Schlüssel nicht (Name und Vorschlag bleiben);
* ein heutiger Mitläufer mit alten Messwerten ist übernehmbar (in seinen Raum);
* Karte „Modelle“: gelernt getrennt von „Stunden mit allen Werten“;
* zwei Abläufe nacheinander: jeder hat seinen eigenen Stand, der zweite wartet;
* Neustart während „neu lernen“: der Start lernt fertig;
* Übernahme lernt den Raum in Tagesabschnitten (die Ereignisschleife bleibt frei), die Zählung läuft in einem Thread.

Nichts davon schreibt an Home Assistant (``FakeHA`` lässt nur Lesen zu).
"""

import asyncio
import time
from pathlib import Path

import pytest
import test_restore421 as R
from heatpilot import backfill, guards, house_data, house_restore, main as main_mod, model_card, room_adopt, room_alias
from heatpilot.dhw import DhwModel
from heatpilot.discovery import Discovered, Registry, Room
from heatpilot.engine import Engine
from heatpilot.models import RoomModel
from heatpilot.store import Store
from test_restore421 import frozen_clock  # noqa: F401,E402 – angehaltene Uhr (autouse)

WZ, KU = "sensor.wohnzimmer_temperature", "sensor.kueche_temperature"


def spread(monkeypatch, d: float) -> None:
    """Räume ±d K um das Hausmittel (Wohnzimmer wärmer, Küche kühler)."""
    monkeypatch.setitem(R.OLD, "climate.wohnzimmer", (WZ, d))
    monkeypatch.setitem(R.OLD, "climate.kueche", (KU, -d))


def bias(st: Store, ref: dict, lo: int, hi: int) -> list[float]:
    t = R.t_in_rows(st)
    return [t[h] - ref[h] for h in ref if lo <= h < hi and t[h] is not None]


def found_mixed() -> Discovered:
    """Wohnzimmer: neues Thermostat, aber der alte Raumsensor bleibt (lange Statistik); Küche ohne Sensor."""
    return Discovered(rooms=[Room("Wohnzimmer", "climate.hk_va01", temp_sensor=WZ), Room("Küche", "climate.hk_va02")],
                      outdoor=["sensor.out"], gas="sensor.gas", gas_unit="kWh")


# ------------------------------------------------------------------ Teilmittel (Schwere hoch)
@pytest.mark.parametrize("d", [1.0, 2.0])
def test_rebuild_and_restore_with_part_of_the_rooms_keep_indoor_temperature(tmp_path, monkeypatch, d):
    spread(monkeypatch, d)
    h0 = R.h_w(R.learned_house(R.make_store(tmp_path / "ref.db"), "old"))
    # Neuaufbau („Historie neu einlesen“): Wohnzimmer mit altem Sensor, Küche ohne
    st = R.make_store(tmp_path / "a.db")
    t0 = R.t_in_rows(st)
    eng = Engine(R.cfg(), st, found_mixed())
    st.reset_learning("2000-01-01")
    asyncio.run(backfill.run_backfill(R.FakeHA(), eng))
    assert max(abs(x) for x in bias(st, t0, 0, R.OWN)) < 1e-9          # gespeichertes Hausmittel bleibt
    assert abs(R.h_w(eng) - h0) / h0 < 0.05 and guards.check_house(eng.house)["ok"]
    # Wiederherstellen mit einem Alias (für einen von zwei Räumen), unbeschädigte Daten
    st = R.make_store(tmp_path / "b.db")
    eng = Engine(R.cfg(), st, R.found("new"))
    room_alias.put(st, "climate.hk_va01", WZ, "climate.wohnzimmer", "user")
    asyncio.run(house_restore.restore(R.FakeHA(), eng, None, None))
    eng = R.learned_house(st)
    assert max(abs(x) for x in bias(st, t0, 0, R.OWN)) < 1e-9
    assert abs(R.h_w(eng) - h0) / h0 < 0.05 and model_card.collect(eng)["house"]["ok"]


def test_user_case_one_alias_old_kitchen_gone_fills_without_offset(tmp_path, monkeypatch):
    """Schaden von 0.42.0, Räume ±1,5 K, nur das Wohnzimmer hat einen alten Sensor, die alte Küche ist nicht mehr in
    Home Assistant: die Lücken werden gefüllt, aber um den Versatz des Wohnzimmers berichtigt."""
    spread(monkeypatch, 1.5)
    h0 = R.h_w(R.learned_house(R.make_store(tmp_path / "ref.db"), "old"))
    ref = R.t_in_rows(R.make_store(tmp_path / "ref2.db"))
    st = R.make_store(tmp_path / "u.db")
    R.damage(st)
    states = {k: v for k, v in R.STATES.items() if k != "climate.kueche"}
    eng = Engine(R.cfg(), st, R.found("new"))
    eng.states.update(states)
    room_alias.put(st, "climate.hk_va01", WZ, "climate.wohnzimmer", "device")
    res = asyncio.run(house_restore.restore(R.FakeHA(), eng, R.REG.lookup(), None))
    eng = R.learned_house(st)
    stat, own = bias(st, ref, 0, R.OWN), bias(st, ref, R.HIST, R.BORN)
    assert len(stat) == (R.OWN - R.START) // 3600                       # alle Statistik-Stunden gefüllt
    assert max(map(abs, stat)) < 0.05 and own and max(map(abs, own)) < 0.2  # nicht +1,5 K
    assert abs(R.h_w(eng) - h0) / h0 < 0.05 and guards.check_house(eng.house)["ok"]
    assert {r["t_in_src"] for r in st.query("SELECT t_in_src FROM hourly WHERE ts < ?", (R.OWN,))} == {f"stat:{WZ}"}
    check = room_alias.get(st)["climate.hk_va01"]["check"]
    assert check["ok"] and abs(check["offset"] - 1.5) < 0.05 and res["checks"][0]["ok"]
    assert [(o["from"], o["rows"] > 0) for o in res["old"]] == [("climate.wohnzimmer", True)]


def test_aggregate_hour_with_missing_room_keeps_or_corrects(tmp_path):
    st = Store(tmp_path / "g.db")
    eng = Engine(R.cfg(), st, Discovered(rooms=[Room("A", "climate.a"), Room("B", "climate.b")]))
    h = R.NOW_H - 3600
    for ts in range(h - 3 * 86400, h, 300):        # drei Tage beide Räume, gespeichertes Hausmittel 20 °C
        st.add_room_samples([(ts, "climate.a", 21.5, 21, 0, None), (ts, "climate.b", 18.5, 21, 0, None)])
    st.add_hourly([(x, 5.0, 20.0, 1.0, None, None, None, None, None, None, "rooms")
                   for x in range(h - 3 * 86400, h, 3600)])
    for ts in range(h, h + 3600, 300):              # die Stunde: nur Raum A (1,5 K über dem Hausmittel)
        st.add_sample(ts, {"t_out": 5.0, "dhw": 0})
        st.add_room_samples([(ts, "climate.a", 21.5, 21, 0, None)])
    st.commit()
    eng.aggregate_hour(h, learn=False)
    r = st.query("SELECT t_in, t_in_src FROM hourly WHERE ts = ?", (h,))[0]
    assert abs(r["t_in"] - 20.0) < 1e-6 and r["t_in_src"] == "rooms~"   # Lücke: berichtigt gefüllt
    st.db.execute("UPDATE hourly SET t_in = 19.0, t_in_src = 'stat:x' WHERE ts = ?", (h,))
    eng.aggregate_hour(h, learn=False)
    assert tuple(st.query("SELECT t_in, t_in_src FROM hourly WHERE ts = ?", (h,))[0]) == (19.0, "stat:x")  # bleibt
    st.add_room_samples([(ts, "climate.b", 18.5, 21, 0, None) for ts in range(h, h + 3600, 300)])
    eng.aggregate_hour(h, learn=False)                # alle Räume: eigenes Mittel wie bisher
    assert tuple(st.query("SELECT t_in, t_in_src FROM hourly WHERE ts = ?", (h,))[0]) == (20.0, "rooms")


def test_room_missing_from_the_start_keeps_the_plain_mean(tmp_path):
    """Ein Raum ohne Temperatur von Anfang an (oder noch nicht gewählt): kein Bezug zum Berichtigen – das einfache
    Mittel wie bisher, Stunde für Stunde, und keine Lücken."""
    st = Store(tmp_path / "p.db")
    eng = Engine(R.cfg(), st, Discovered(rooms=[Room("A", "climate.a"), Room("B", "climate.b")]))
    h0 = R.NOW_H - 3 * 86400
    for ts in range(h0, R.NOW_H, 300):
        st.add_sample(ts, {"t_out": 5.0, "dhw": 0})
        st.add_room_samples([(ts, "climate.a", 21.5, 21, 0, None)])
    st.commit()
    for h in range(h0, R.NOW_H, 3600):
        eng.aggregate_hour(h, learn=False)
    got = {tuple(r) for r in st.query("SELECT t_in, t_in_src FROM hourly")}
    assert got == {(21.5, "rooms-")} and st.query("SELECT COUNT(*) FROM hourly")[0][0] == 72


# ------------------------------------------------------------------ Falscher Alias
def test_alias_of_another_room_is_rejected_and_removal_reverts_its_hours(tmp_path, monkeypatch):
    st = R.make_store(tmp_path / "w.db")
    R.damage(st)
    app = R.app_for(tmp_path, monkeypatch, st)

    async def go(steps):
        if app.eng is None:
            await app.build(backfill=False)
            app.eng.states.update(R.STATES)
        return await R.call(app, steps)

    (s1, e1), (s2, ok2), (s3, e3) = asyncio.run(go([
        ("POST", "/api/rooms/aliases", {"room": "climate.hk_va02", "stat": "sensor.hk_va01_temperature"}),
        ("POST", "/api/rooms/aliases", {"room": "climate.hk_va01", "stat": WZ}),
        ("POST", "/api/rooms/aliases", {"room": "climate.hk_va02", "stat": WZ})]))
    assert s1 == 400 and "aktuelle Temperatursensor von Wohnzimmer" in e1["error"]
    assert s2 == 200 and s3 == 400 and "schon der alte Sensor von Wohnzimmer" in e3["error"]
    room_alias.put(st, "climate.hk_va02", KU, None, "user")
    asyncio.run(house_restore.restore(R.FakeHA(), app.eng, None, None))
    both = st.t_in_hours(KU)
    assert both > 1000 and st.t_in_hours(WZ) >= both
    (s4, ok4), (s5, ov) = asyncio.run(go([("POST", "/api/rooms/aliases", {"room": "climate.hk_va02", "stat": ""}),
                                         ("GET", "/api/rooms/aliases", None)]))
    assert s4 == 200 and ok4["alias"] is None and ok4["reverted"] == both
    assert st.t_in_hours(KU) == 0 and house_data.coverage(st)["no_t_in"] >= both    # zurück auf „unbekannt“
    assert {r["key"]: (r["alias"] or {}).get("hours") for r in ov["rooms"]}["climate.hk_va01"] == st.t_in_hours(WZ)
    asyncio.run(house_restore.restore(R.FakeHA(), app.eng, None, None))   # füllt mit dem Wohnzimmer allein neu
    ref = R.t_in_rows(R.make_store(tmp_path / "ref.db"))
    assert max(map(abs, bias(st, ref, 0, R.OWN))) < 0.05 and st.t_in_hours(WZ) > 1000


class Cellar(R.FakeHA):
    """Ein Sensor, der zu keinem Raum passt: Keller, gleichbleibend 9 °C."""

    async def statistics(self, ids, s, e, period="hour"):
        out = await super().statistics([i for i in ids if i != "sensor.keller"], s, e, period)
        if "sensor.keller" in ids:
            out["sensor.keller"] = [{"start": h * 1000, "mean": 9.0 + 0.01 * (h % 7)}
                                    for h in range(R.START, R.NOW_H, 3600)]
        return out


def test_alias_that_does_not_fit_is_not_used(tmp_path):
    st = R.make_store(tmp_path / "k.db")
    R.damage(st)
    eng = Engine(R.cfg(), st, R.found("new"))
    room_alias.put(st, "climate.hk_va02", "sensor.keller", None, "user")
    res = asyncio.run(house_restore.restore(Cellar(), eng, None, None))
    (c,) = res["checks"]
    assert c["stat"] == "sensor.keller" and c["ok"] is False and "neben dem Hausmittel" in c["why"]
    assert st.t_in_hours("sensor.keller") == 0 and res["no_t_in_after"] == res["no_t_in_before"]


# ------------------------------------------------------------------ Neuaufbau
def test_full_rebuild_keeps_emsesp_values_and_burner_events(tmp_path):
    st = R.make_store(tmp_path / "f.db")
    st.db.execute("UPDATE samples SET flow = 45.0, burner_on = 1, burner_pow = 60")
    st.add_burner_event(R.HIST + 3600, True)
    st.add_burner_event(R.HIST + 7200, False)
    st.commit()
    q = "SELECT COUNT(*) AS n, SUM(flow IS NOT NULL) AS f, SUM(burner_on IS NOT NULL) AS b FROM samples WHERE ts >= ?"
    before = dict(st.query(q, (R.HIST,))[0])
    st.reset_learning("2000-01-01")
    asyncio.run(backfill.run_backfill(R.FakeHA(), Engine(R.cfg(), st, R.found("new"))))
    after = dict(st.query(q, (R.HIST,))[0])
    assert after["f"] == after["b"] == before["n"] and after["n"] >= before["n"]  # nichts durch „unbekannt“ ersetzt
    assert st.query("SELECT COUNT(*) FROM burner_events")[0][0] == 2               # HA hat keinen Brenner: bleiben


def test_reload_history_keeps_dhw_and_person_models(tmp_path, monkeypatch):
    st = R.make_store(tmp_path / "d.db")
    dhw = DhwModel()
    dhw.charges = 42
    st.put("model.dhw", dhw.to_dict())
    st.put("model.person.person.a", {"keep": True})
    app = R.app_for(tmp_path, monkeypatch, st)

    async def go():
        await app.build(backfill=False)
        await app.reload_history()

    asyncio.run(go())
    assert st.get("model.dhw")["charges"] == 42 and app.eng.dhw.charges == 42
    assert st.get("model.person.person.a") == {"keep": True} and st.get(house_restore.PENDING) is None
    assert guards.check_house(app.eng.house)["ok"]


# ------------------------------------------------------------------ Übernahme bei Überschneidung
def test_adopt_overlap_merges_columns_measured_valve_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "BORN", R.NOW_H - 9 * 86400)   # HomeKit schon 9 Tage in HA (parallel eingerichtet)
    st = R.make_store(tmp_path / "v.db")                   # alte Raumwerte: Ventil 20 %
    st.db.execute("UPDATE room_samples SET humidity = 45.0 WHERE room = 'climate.wohnzimmer'")
    st.commit()
    eng = Engine(R.cfg(), st, R.found("new"))
    asyncio.run(backfill.backfill_rooms(R.FakeHA(), eng, eng.found.rooms, R.HIST_DAYS))
    ov = room_adopt.overview(st, eng.found, R.STATES, None)
    o = next(x for x in ov["orphans"] if x["key"] == "climate.wohnzimmer")
    both = st.query("SELECT COUNT(*) FROM room_samples n JOIN room_samples o ON o.ts = n.ts AND o.room = "
                    "'climate.wohnzimmer' WHERE n.room = 'climate.hk_va01'")[0][0]
    assert both > 2000 and o["overlap"]["climate.hk_va01"]["rows"] == both
    res = room_adopt.adopt(st, "climate.wohnzimmer", "climate.hk_va01")
    assert res["skipped"] == both and res["filled"] == both
    got = {tuple(r) for r in st.query("SELECT valve, humidity FROM room_samples WHERE room = 'climate.hk_va01' "
                                      "AND ts >= ? AND ts < ?", (R.BORN, R.NOW_H - 3600))}
    assert got == {(20.0, 45.0)}                            # gemessen und ergänzt statt 0 % aus hvac_action
    assert st.query("SELECT COUNT(*) FROM room_samples WHERE room = 'climate.wohnzimmer'")[0][0] == 0


# ------------------------------------------------------------------ Wiederherstellen ohne Alias
def test_restore_fetches_old_entity_from_adoption_log_and_reports(tmp_path, monkeypatch):
    st = R.make_store(tmp_path / "n.db")
    R.damage(st)
    app = R.app_for(tmp_path, monkeypatch, st)
    st.put(room_adopt.KV_LOG, [{"ts": 1, "from": "climate.gone", "to": "climate.hk_va02", "rows": 5}])

    async def go():
        await app.build(backfill=False)
        app.eng.states.update(R.STATES)
        res = await R.call(app, [("POST", "/api/rooms/adopt", {"from": "climate.wohnzimmer", "to": "climate.hk_va01"}),
                                 ("POST", "/api/house/restore", None), ("WAIT", None, None),
                                 ("GET", "/api/house/job", None),
                                 ("POST", "/api/rooms/aliases", {"room": "climate.hk_va01", "stat": WZ})])
        return res

    (sa, a), _, (_, job), (_, al) = asyncio.run(go())
    assert sa == 200
    res = job["job"]["result"]
    old = {o["from"]: o for o in res["old"]}
    assert old["climate.wohnzimmer"]["rows"] > 2000 and old["climate.wohnzimmer"]["to"] == "climate.hk_va01"
    assert old["climate.gone"]["why"] == "nicht mehr in Home Assistant" and "rows" not in old["climate.gone"]
    n = st.query("SELECT COUNT(*) FROM room_samples WHERE room = 'climate.hk_va01' AND ts >= ? AND ts < ?",
                 (R.HIST, R.BORN))[0][0]
    assert n > 2000
    assert al["alias"]["from"] == "climate.wohnzimmer"      # selbst gewählt: alte Entität aus dem Protokoll


# ------------------------------------------------------------------ Waisen: Name, Mitläufer
def test_relearn_keeps_models_and_name_of_orphans(tmp_path):
    st = Store(tmp_path / "o.db")
    old = "climate.0x00158d00045a1b2c"                      # Zigbee neu gekoppelt: alte Entität weg
    now = int(time.time()) // 300 * 300
    st.add_room_samples((ts, old, 21.0, 21.0, 30.0, None) for ts in range(now - 40 * 86400, now - 86400, 300))
    st.put(f"model.room.{old}", RoomModel("Bad").to_dict())
    st.put("model.room.climate.bad_trv", RoomModel("Bad").to_dict())
    st.commit()
    f = Discovered(rooms=[Room("Bad", "climate.bad_trv", area="Bad")], outdoor=["sensor.out"])
    states = {"climate.bad_trv": {"state": "heat", "attributes": {"friendly_name": "Bad TRV"}}}
    idx = Registry([{"entity_id": "climate.bad_trv", "platform": "zha", "device_id": "d"}],
                   [{"id": "d", "name": "Bad TRV", "area_id": "b"}], [{"area_id": "b", "name": "Bad"}]).lookup()
    house_restore.prepare(st, ["climate.bad_trv"])          # wie App._build nach einem Raumwechsel
    assert st.get(f"model.room.{old}") is not None and st.get("model.room.climate.bad_trv") is None
    ov = room_adopt.overview(st, f, states, idx)
    assert [(o["key"], o["name"]) for o in ov["orphans"]] == [(old, "Bad")]
    assert [(s["from"], s["reason"]) for s in ov["suggestions"]] == [(old, "gleicher Name")]
    st.reset_learning("2000-01-01")                         # auch der komplette Neuaufbau: Name gemerkt
    assert st.get(f"model.room.{old}") is None and room_adopt.name_of(st, states, old) == "Bad"


def test_member_with_old_values_is_adoptable_into_its_room(tmp_path):
    st = R.make_store(tmp_path / "g.db")
    member = {"climate": "climate.wohnzimmer", "heating_power": None, "humidity": None, "temp_sensor": None,
              "platform": "tado", "preset": None, "preset_max": None}
    f = Discovered(rooms=[Room("Wohnzimmer", "climate.hk_va01", group="Wohnzimmer", members=[member]),
                          Room("Küche", "climate.hk_va02")], outdoor=["sensor.out"], gas="sensor.gas", gas_unit="kWh")
    ov = room_adopt.overview(st, f, R.STATES, None)
    o = next(x for x in ov["orphans"] if x["key"] == "climate.wohnzimmer")
    assert o["member_of"] == "climate.hk_va01"
    assert ("climate.wohnzimmer", "climate.hk_va01", "Mitläufer im Raum Wohnzimmer") in \
        [(s["from"], s["to"], s["reason"]) for s in ov["suggestions"]]
    assert "nur dorthin" in room_adopt.check(st, f, "climate.wohnzimmer", "climate.hk_va02")
    eng = Engine(R.cfg(), st, f)
    res = room_adopt.run(eng, "climate.wohnzimmer", "climate.hk_va01")
    assert res["rows"] == R.OWN_DAYS * 288 and st.query(
        "SELECT COUNT(*) FROM room_samples WHERE room = 'climate.wohnzimmer'")[0][0] == 0


# ------------------------------------------------------------------ Karte „Modelle“
def test_model_card_says_what_was_learned(tmp_path):
    st = R.make_store(tmp_path / "c.db")
    st.db.execute("UPDATE hourly SET t_out = 20.0, gas_kwh = 0.3 WHERE ts < ?", (R.START + 30 * 86400,))  # Sommer
    st.commit()
    h = model_card.collect(R.learned_house(st))["house"]
    assert h["hours_learned"] < h["data"]["usable_heat"] < h["data"]["usable"]
    assert h["learned_text"] == (f"aus {house_data._n(h['hours_learned'])} Stunden und {h['days_learned']} "
                                 f"Tageswerten gelernt – von {house_data._n(h['data']['usable'])} Stunden mit Gas, "
                                 f"Außen- und Innentemperatur seit {house_data._date(h['data']['usable_from'])}")


# ------------------------------------------------------------------ Abläufe
def test_two_jobs_each_keep_their_own_state():
    app = main_mod.App.__new__(main_mod.App)
    app._lock, app.job, app.eng, app._status = asyncio.Lock(), None, object(), "läuft"

    async def restore_house():
        async with app._lock:
            await asyncio.sleep(0.2)
            return {"kind": "restore"}

    async def update_rooms(climates):
        async with app._lock:
            await asyncio.sleep(0.2)
            return {"rooms": sorted(climates)}

    app.restore_house, app.update_rooms = restore_house, update_rooms

    async def go():
        first = app.start_job("restore")
        await asyncio.sleep(0.05)
        second = app.start_job("rooms", {"climate.x"})
        assert app.job is second and second["waiting"] and "Wartet schon" in app.job_busy()
        await asyncio.sleep(0.25)
        assert first["result"] == {"kind": "restore"} and not first["running"]
        assert second["running"] and not second["waiting"] and second["result"] is None
        await asyncio.sleep(0.25)
        assert second["result"] == {"rooms": ["climate.x"]} and not second["running"]

    asyncio.run(go())


def test_restart_during_relearn_finishes_on_start(tmp_path, monkeypatch):
    st = R.make_store(tmp_path / "r.db")
    R.learned_house(st)
    house_restore.prepare(st, ["climate.hk_va01", "climate.hk_va02"])  # neu lernen beginnt … Neustart
    assert house_restore.pending(st) and st.query("SELECT COUNT(*) FROM snapshots")[0][0] == 0
    app = R.app_for(tmp_path, monkeypatch, st)
    asyncio.run(app.build(backfill=True))                    # wie main.run beim Start
    assert not house_restore.pending(st) and app.eng.house.rls.updates > 1000
    assert guards.check_house(app.eng.house)["ok"] and st.query("SELECT COUNT(*) FROM snapshots")[0][0] > 0
    assert "statistics" not in app.ha.calls and "history" not in app.ha.calls   # nichts aus HA neu geholt


def test_adopt_frees_event_loop_and_counts_in_a_thread(tmp_path, monkeypatch):
    st = R.make_store(tmp_path / "t.db")
    app = R.app_for(tmp_path, monkeypatch, st)
    ticks = []

    async def ticker():
        while True:
            ticks.append(1)
            await asyncio.sleep(0)

    async def go():
        await app.build(backfill=False)
        app.eng._last_room_step = R.NOW_H - R.NOW_H % 10800
        app.eng.states.update(R.STATES)
        t = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        n0 = len(ticks)
        res = await app.adopt_room("climate.wohnzimmer", "climate.hk_va01")
        t.cancel()
        return res, len(ticks) - n0

    res, during = asyncio.run(go())
    assert res["rows"] == R.OWN_DAYS * 288 and during >= R.OWN_DAYS - 2  # je Tag einmal freigegeben
    assert not app.eng.__dict__.get("_room_busy") and st.get("model.room.climate.hk_va01") is not None  # gespeichert
    rooms = [r.climate for r in app.eng.found.rooms]
    assert room_adopt.tally_file(st.path, rooms) == room_adopt.tally(st, rooms)


def test_new_module_small_with_purpose_docstring():
    src = (Path(R.PKG) / "indoor_mean.py").read_text(encoding="utf-8")
    assert src.startswith('"""') and src.count("\n") <= 600
    for name in ("room_adopt.py", "room_alias.py", "house_restore.py", "app_jobs.py", "backfill.py", "store.py",
                 "main.py", "engine_learn.py", "web_adopt.py"):
        assert (Path(R.PKG) / name).read_text(encoding="utf-8").count("\n") <= 600, name


# ------------------------------------------------------------------ Oberfläche
def test_ui_texts_for_overlap_member_alias_check_waiting_and_reload(tmp_path):
    from test_ui_v421 import js_run, plain
    got = js_run(tmp_path, """
const ov = { rows: 2592, days: 9 };
const A = { orphans: [{ key: "climate.wohnzimmer", name: "Wohnzimmer", rows: 5760, days: 20, source: "Tado-Cloud",
  member_of: "climate.hk_va01", overlap: { "climate.hk_va01": ov } }],
  suggestions: [{ to: "climate.hk_va01", to_name: "Wohnzimmer", from: "climate.wohnzimmer", from_name: "Wohnzimmer",
  source: "Tado-Cloud", days: 20, new_days: 9, reason: "Mitläufer im Raum Wohnzimmer", overlap: ov }],
  rooms: [{ key: "climate.hk_va01", name: "Wohnzimmer", days: 9 }] };
const alias = (check, hours) => ({ key: "climate.hk_va02", name: "Küche", temp_sensor: null, candidates: [],
  alias: { stat: "sensor.keller", how: "user", how_text: "selbst gewählt", check, hours } });
const now = Math.floor(Date.now() / 1000);
out({
  ask: adoptAsk("Wohnzimmer", "Wohnzimmer", ov), box: adoptHtml(A),
  done: adoptDone({ skipped: 2592, filled: 2592, from_days: 11, from_name: "Wohnzimmer", to_name: "Wohnzimmer" }),
  good: aliasRow(alias({ ok: true, offset: -1.5 }, 960), [], ""),
  bad: aliasRow(alias({ ok: false, why: "liegt im Mittel -11.6 K neben dem Hausmittel – wohl kein Raumsensor" }, 0), [], ""),
  aliasAsk: aliasAsk("Küche", { stat: "sensor.kueche_temperature", hours: 1180 }, ""),
  removed: aliasDone("Küche", null, 1180), removedNone: aliasDone("Küche", null, 0),
  waiting: jobHtml({ job: { kind: "rooms", running: true, waiting: true }, status: "stelle Hausdaten wieder her" }),
  notes: restoreNotes({ checks: [{ name: "Küche", stat: "sensor.keller", ok: false, why: "passt nicht" }],
                        old: [{ from: "climate.gone", why: "nicht mehr in Home Assistant" }] }),
  aliases: restoreAliases([{ name: "Wohnzimmer", alias: { stat: "sensor.wohnzimmer_temperature" } },
                           { name: "Küche", alias: null, suggested: "sensor.kueche_temperature" }]),
  reload: RELOAD_ASK,
});
""")
    assert got["ask"] == ("Die Messwerte von Wohnzimmer werden Raum Wohnzimmer zugeordnet, Raum- und Ventilmodell neu "
                          "berechnet. An 2.592 Zeitpunkten (9 Tage) hat Wohnzimmer schon eigene Messwerte: Dort "
                          "bleiben seine Werte, nur was ihm fehlt (etwa die gemessene Ventilöffnung), kommt von "
                          "Wohnzimmer; die übrigen Werte von Wohnzimmer zu diesen Zeitpunkten fallen weg. Nicht "
                          "rückgängig zu machen.")
    box = plain(got["box"])
    assert "climate.wohnzimmer · Mitläufer im Raum Wohnzimmer" in box
    assert "Mitläufer im Raum Wohnzimmer · der Raum hat bisher 9 Tage Messwerte · an 2.592 Zeitpunkten (9 Tage) hat " \
           "der Raum schon eigene Messwerte – seine bleiben" in box
    assert "an 2.592 Zeitpunkten hatte der Raum schon eigene Messwerte – seine bleiben, an 2.592 davon fehlende Werte " \
           "aus Wohnzimmer ergänzt" in plain(got["done"])
    assert "passt (im Mittel −1,5 K zum Hausmittel, wird abgezogen) · füllt 960 Stunden" in plain(got["good"])
    assert "passt nicht – liegt im Mittel -11.6 K neben dem Hausmittel – wohl kein Raumsensor; nicht verwendet" in \
        plain(got["bad"]) and 'class="msg-err"' in got["bad"]
    assert got["aliasAsk"].startswith("Keinen alten Sensor für Küche?\n\nsensor.kueche_temperature hat 1.180 Stunden "
                                      "Innentemperatur gefüllt. Sie bekommen wieder „unbekannt“")
    assert "1.180 Stunden, die der vorige alte Sensor gefüllt hatte, sind wieder ohne Innentemperatur." in \
        plain(got["removed"]) and "houseJob('restore')" in got["removed"]
    assert plain(got["removedNone"]).endswith("Er hatte keine Stunden gefüllt.")
    assert plain(got["waiting"]).startswith("Wartet: Historie der neuen Räume nachholen – erst läuft noch der vorige")
    assert plain(got["notes"]) == ("Alter Sensor nicht verwendet: Küche (sensor.keller – passt nicht). Keine Raumwerte "
                                   "nachgeholt für climate.gone (nicht mehr in Home Assistant).")
    assert got["aliases"] == ("\n\nAlte Sensoren: Wohnzimmer: sensor.wohnzimmer_temperature.\n\nDabei merkt sich "
                              "HeatPilot als alten Sensor (eindeutiger Vorschlag): Küche: sensor.kueche_temperature.")
    assert "Gelerntes bleibt erhalten" not in got["reload"] and "Warmwasser- und Personenmodelle bleiben" in got["reload"]
    assert "etwa Kesselwerte direkt von EMS-ESP" in got["reload"]
