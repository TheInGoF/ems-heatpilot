"""0.37 Trockenlauf-Protokoll: Plan je Stunde gespeichert (der damals gerechnete), Ist-Wert nachgetragen,
Auswertung je Tag und 7 Tage, Aufbewahrung 90 Tage, alte Datenbank ohne Tabelle, „neu lernen“ lässt es stehen,
Abruf über GET api/preview. Geschrieben wird nur in die eigene Datenbank – nie an den Kessel.
"""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from heatpilot import preview as pv, preview_log as pl
from heatpilot.store import PREVIEW_COLS, PREVIEW_LOG_KEEP_DAYS, Store

from test_control34 import MON, at, ts_of
from test_preview37 import dhw_model, house

H12 = ts_of(MON, 12)


def boiler(eng):
    """Kessel-Soll (Regler), Speicher-Soll (Automation), Betriebsart und „Heizen abschalten“ als HA-Sensoren."""
    eng.found.boiler.update({"sel_flow_temp": "sensor.sel_flow", "dhw_seltemp": "sensor.dhw_set",
                             "heating_off": "sensor.heating_off"})
    eng.found.thermostat["hc_mode"] = "sensor.hc_mode"
    set_boiler(eng, 45, 55, "day")


def set_boiler(eng, flow, dhw, mode="day", off="off"):
    eng.states.update({"sensor.sel_flow": {"state": str(flow)}, "sensor.dhw_set": {"state": str(dhw)},
                       "sensor.hc_mode": {"state": mode}, "sensor.heating_off": {"state": off}})


def samples(eng, start, flows, dhw=55, hc_day=1, heating_off=0):
    for i, f in enumerate(flows):
        eng.store.add_sample(start + i * 300, {"sel_flow": f, "dhw_set": dhw, "hc_day": hc_day,
                                               "heating_off": heating_off})
    eng.store.commit()


# ------------------------------------------------------------------ Plan je Stunde
def test_plan_saved_once_per_hour_with_writes(tmp_path):
    eng, ha, _ = house(tmp_path)
    boiler(eng)
    assert pl.record(eng, H12 + 60) is True
    rows = eng.store.preview_log(0)
    assert len(rows) == 1 and rows[0]["ts"] == H12
    r = rows[0]
    assert (r["flow"], r["heating"], r["t_out"]) == (29, 1, 5.0) and r["dhw"] is None  # Warmwasser lernt noch
    assert r["w_flow"] == 1 and r["act_n"] is None  # Kessel steht auf 45 °C, HeatPilot hätte einmal 29 °C geschrieben
    assert eng.store.get(pl.KV_WRITTEN) == {"ts": H12, "flow": 29, "heating": True, "dhw": 55.0}
    # dieselbe Stunde nicht noch einmal – auch nicht aus einer neuen Engine (Neustart)
    assert pl.record(eng, H12 + 600) is False
    eng.__dict__.pop("_preview_logged")
    assert pl.record(eng, H12 + 700) is False and len(eng.store.preview_log(0)) == 1
    # zu spät in der Stunde (nach einem Neustart um 13:20): kein Plan für 13:00
    eng.__dict__.pop("_preview_logged")
    assert pl.record(eng, ts_of(MON, 13, 20)) is False and len(eng.store.preview_log(0)) == 1
    assert ha.calls == []


def test_next_hour_counts_from_previous_plan(tmp_path):
    eng, _, _ = house(tmp_path)
    boiler(eng)
    pl.record(eng, H12)
    pl.record(eng, ts_of(MON, 13))  # derselbe Plan steht schon „am Kessel“: kein neuer Schreibvorgang
    r = eng.store.preview_log(0)[-1]
    assert r["ts"] == ts_of(MON, 13) and r["w_flow"] == 0 and r["w_heating"] == 0
    # Lücke (Vorstunde fehlt): Zählung beginnt wieder beim Kessel
    pl.record(eng, ts_of(MON, 15))
    assert eng.store.preview_log(0)[-1]["w_flow"] == 1


def test_tick_records_and_survives_errors(tmp_path, monkeypatch, caplog):
    eng, _, _ = house(tmp_path)
    eng.tick(H12)
    assert [r["ts"] for r in eng.store.preview_log(0)] == [H12]

    def broken(*a, **kw):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(pv, "preview", broken)
    eng.tick(ts_of(MON, 13))  # Fehler im Protokoll hält den Takt nicht auf
    assert "Trockenlauf-Protokoll fehlgeschlagen" in caplog.text
    assert eng.store.query("SELECT COUNT(*) AS n FROM samples")[0]["n"] == 2


# ------------------------------------------------------------------ Ist-Werte
def test_actuals_filled_after_the_hour(tmp_path):
    eng, _, _ = house(tmp_path)
    boiler(eng)
    pl.record(eng, H12)
    samples(eng, H12 - 300, [45])                          # 11:55 – Bezug für die Wechsel
    samples(eng, H12, [45] * 6 + [47] * 6)                 # 12:00–12:55: einmal um 2 K geändert
    eng.store.add_sample(H12 + 3600, {"sel_flow": 52.0, "dhw_set": 55, "hc_day": 1})  # gehört zu 13:00
    assert pl.fill_actuals(eng.store, H12 + 3599) == 0     # Stunde noch nicht vorbei
    assert pl.fill_actuals(eng.store, H12 + 3600) == 1
    r = eng.store.preview_log(0)[0]
    assert (r["act_flow"], r["act_dhw"], r["act_heating"], r["act_n"]) == (46.0, 55.0, 1.0, 12)
    assert (r["ch_flow"], r["ch_dhw"], r["ch_heating"]) == (1, 0, 0)
    assert pl.fill_actuals(eng.store, H12 + 7200) == 0     # schon nachgetragen


def test_actual_flow_only_while_heating_and_switch_changes(tmp_path):
    eng, _, _ = house(tmp_path)
    eng.store.add_preview_plan(H12, {"flow": 30, "heating": 1, "dhw": 50, "t_out": 5.0})
    for i in range(12):  # erste halbe Stunde Tag (45 °C), dann Nacht (Regler meldet 10 °C)
        day = i < 6
        eng.store.add_sample(H12 + i * 300, {"sel_flow": 45 if day else 10, "dhw_set": 50,
                                             "hc_day": 1 if day else 0, "heating_off": 0})
    pl.fill_actuals(eng.store, H12 + 3600)
    r = eng.store.preview_log(0)[0]
    assert r["act_flow"] == 45.0 and r["act_heating"] == 0.5 and r["ch_heating"] == 1
    # ohne Messpunkte: Stunde gilt als nachgetragen, ohne Werte
    eng.store.add_preview_plan(ts_of(MON, 14), {"flow": 30, "heating": 1})
    pl.fill_actuals(eng.store, ts_of(MON, 15))
    r = eng.store.preview_log(0)[-1]
    assert r["act_n"] == 0 and r["act_flow"] is None and r["act_heating"] is None


# ------------------------------------------------------------------ Auswertung
def test_compare_days_total_text_and_energy(tmp_path):
    eng, _, _ = house(tmp_path)
    eng.dhw = dhw_model()
    s = eng.store
    for d, (flow, act) in enumerate(((30, 36), (30, 38))):  # Sonntag und Montag
        ts = H12 - (1 - d) * 86400
        s.add_preview_plan(ts, {"flow": flow, "heating": 1, "dhw": 45, "t_out": 5.0,
                                "w_flow": 1, "w_heating": 0, "w_dhw": 1})
        s.set_preview_actual(ts, {"act_flow": act, "act_heating": 1.0, "act_dhw": 55.0, "act_n": 12,
                                  "ch_flow": 3, "ch_heating": 0, "ch_dhw": 1})
    s.add_preview_plan(ts_of(MON, 13), {"flow": 25, "heating": 0, "dhw": 45, "t_out": 5.0})  # läuft noch
    c = pl.compare(s, ts_of(MON, 13, 30), eng.dhw)
    t = c["total"]
    assert not c["collecting"] and t["hours"] == 2 and t["flow_diff_k"] == -7.0 and t["flow_lower_h"] == 2
    assert t["dhw_diff_k"] == -10.0 and t["dhw_lower_h"] == 2 and t["writes_plan"] == 4 and t["changes_act"] == 8
    assert t["dhw_kwh"] < 0  # 45 statt 55 °C: weniger Bereitschaftsverlust
    assert c["text"].startswith("HeatPilot läge im Mittel 7 K tiefer beim Vorlauf, 10 K tiefer beim Warmwasser; "
                                "4 statt 8 Schreibvorgänge; Bereitschaftsverlust Warmwasser ≈ ")
    assert c["text"].endswith(" kWh weniger") and "," in c["text"].split("≈ ")[1]
    assert [d["day"] for d in c["days"]] == [at(MON, 12).date().replace(day=11).isoformat(),
                                             at(MON, 12).date().isoformat()]
    assert c["days"][0]["flow_diff_k"] == -6.0 and c["days"][1]["text"].startswith("HeatPilot läge im Mittel 8 K")
    assert len(c["series"]) == 3 and c["series"][-1]["act_flow"] is None and c["series"][-1]["changes"] is None
    # ohne plausibles Warmwasser-Modell keine kWh; Heizbetrieb aus gegen Regler an wird gezählt
    s.set_preview_actual(ts_of(MON, 13), {"act_flow": 40, "act_heating": 1.0, "act_dhw": 45, "act_n": 12,
                                          "ch_flow": 0, "ch_heating": 0, "ch_dhw": 0})
    t = pl.compare(s, ts_of(MON, 14), None)["total"]
    assert t["dhw_kwh"] is None and t["heating_off_h"] == 1 and t["flow_hours"] == 2
    json.dumps(c, allow_nan=False)


def test_compare_empty_says_collecting(tmp_path):
    eng, _, _ = house(tmp_path)
    c = pl.compare(eng.store, H12, None)
    assert c["collecting"] and c["text"] == "sammelt – erste Werte nach einer Stunde" and c["total"] is None
    pl.record(eng, H12)
    c = pl.compare(eng.store, H12 + 600, None)
    assert c["collecting"] and len(c["series"]) == 1  # Plan da, Ist-Wert erst nach der Stunde


# ------------------------------------------------------------------ Datenbank
def test_retention_90_days_and_reset_learning_keeps_log(tmp_path):
    s = Store(tmp_path / "r.db")
    old = H12 - (PREVIEW_LOG_KEEP_DAYS + 1) * 86400
    s.add_preview_plan(old, {"flow": 30})
    s.add_preview_plan(H12 - 89 * 86400, {"flow": 31})
    assert len(s.preview_log(0)) == 2
    s.add_preview_plan(H12, {"flow": 32})
    assert [r["flow"] for r in s.preview_log(0)] == [31, 32]
    s.put(pl.KV_WRITTEN, {"ts": H12, "flow": 32})
    s.reset_learning("2026-01-01")
    assert len(s.preview_log(0)) == 2 and s.get(pl.KV_WRITTEN)["flow"] == 32


def test_old_database_gets_table_and_columns(tmp_path):
    path = tmp_path / "alt.db"
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE samples(ts INTEGER PRIMARY KEY, t_out REAL);"
                     "CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT);")
    db.close()
    s = Store(path)
    cols = {r["name"] for r in s.query("PRAGMA table_info(preview_log)")}
    assert {"ts", "flow", "heating", "dhw", "t_out", *PREVIEW_COLS} <= cols
    # Tabelle aus einem Zwischenstand ohne die Zusatzspalten: werden ergänzt
    path2 = tmp_path / "zwischen.db"
    db = sqlite3.connect(path2)
    db.executescript("CREATE TABLE preview_log(ts INTEGER PRIMARY KEY, flow REAL, heating INTEGER, dhw REAL, "
                     "t_out REAL); INSERT INTO preview_log VALUES (1, 30, 1, 50, 4);")
    db.close()
    s2 = Store(path2)
    assert {"act_flow", "w_flow", "act_n"} <= {r["name"] for r in s2.query("PRAGMA table_info(preview_log)")}
    assert s2.preview_log(0)[0]["flow"] == 30


# ------------------------------------------------------------------ GET api/preview
def test_api_preview(tmp_path):
    from heatpilot.web import make_app as web_app

    eng, ha, loop = house(tmp_path, persons=["person.a"], home_zone=(53.55, 10.0))
    eng.states["person.a"] = {"state": "home", "attributes": {"latitude": 53.9, "longitude": 10.4}}
    connected = asyncio.Event()
    connected.set()
    app = SimpleNamespace(eng=eng, cfg=eng.cfg, control=loop.control, status="läuft", configured=True,
                          ha=SimpleNamespace(connected=connected))

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            resp = await client.get("/api/preview")
            assert resp.status == 200
            j = await resp.json()
            p, c = j["preview"], j["compare"]
            assert len(p["hours"]) == 24 and p["dry_run"] is True and p["note"] == pv.NOTE
            assert set(p["writes_24h"]) == {"flow", "heating", "dhw"} and c["collecting"] is True
            text = json.dumps(j)
            assert "53.9" not in text and "latitude" not in text
            app.eng = None
            assert (await (await client.get("/api/preview")).json())["starting"] is True
        finally:
            await client.close()

    asyncio.run(go())
    assert ha.calls == []
