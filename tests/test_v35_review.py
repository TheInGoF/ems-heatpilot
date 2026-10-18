"""0.35 Gegenprüfung: nachgestellte Funde zu Raum-Erkennung, Vorschlägen, Abgleich-Verfolgung und Anzeige.

Je Fund ein Test; alle schlagen auf dem Stand vor der Behebung fehl.
HEATPILOT_STATIC zeigt für den Vorher-Nachher-Vergleich auf einen anderen static-Ordner.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from heatpilot import advice
from heatpilot.config import Config
from heatpilot.discovery import Registry, discover

from test_advice import balance_engine, by_kind
from test_balance35 import WEAK, STRONG, A, B, with_presets

ROOT = Path(__file__).resolve().parents[1] / "ems_heatpilot"
STATIC = Path(os.environ.get("HEATPILOT_STATIC", ROOT / "heatpilot" / "static"))
NODE = shutil.which("node") or next((p for p in ("/opt/node22/bin/node",) if Path(p).exists()), None)


def script() -> str:
    return re.search(r"<script>(.*)</script>", (STATIC / "index.html").read_text(encoding="utf-8"), re.S).group(1)


def between(js: str, start: str, end: str) -> str:
    assert start in js, start
    return js.split(start)[1].split(end)[0]


def clim(eid, name, dev, platform="homekit_controller", area=None):
    st = {"entity_id": eid, "state": "heat", "attributes": {"friendly_name": name, "current_temperature": 20.0}}
    return st, {"entity_id": eid, "platform": platform, "device_id": dev, "area_id": area}


def run_js(src: str, tmp_path: Path) -> str:
    """Ein Stück des Inline-Skripts in node ausführen (Stubs für DOM und globale Daten)."""
    f = tmp_path / "probe.js"
    f.write_text(src, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout


# [1] Tado-Zone/HomeKit-Ventil mit „thermostat“ in der entity_id bleibt Raum ---------------------------------
def test_tado_zone_and_homekit_valve_named_thermostat_stay_rooms():
    s1, e1 = clim("climate.bad_thermostat", "Bad Thermostat", "z1", platform="tado")
    s2, e2 = clim("climate.wohnzimmer", "Wohnzimmer", "z2", platform="tado")
    s3, e3 = clim("climate.ems_esp_thermostat_hc1", "Thermostat hc1", "ems", platform="mqtt")
    reg = Registry([e1, e2, e3], [{"id": "z1", "name": "Bad Thermostat", "model": "HEATING"},
                                  {"id": "z2", "name": "Wohnzimmer", "model": "HEATING"},
                                  {"id": "ems", "name": "EMS-ESP Thermostat", "model": "RC310"}], [])
    # wie 0.34: beide Zonen, der EMS-Heizkreis nicht – auch mit nackter Entitätenliste
    assert sorted(r.climate for r in discover([s1, s2, s3], reg, Config()).rooms) == ["climate.bad_thermostat", "climate.wohnzimmer"]
    assert sorted(r.climate for r in discover([s1, s2, s3], [e1, e2, e3], Config()).rooms) == ["climate.bad_thermostat", "climate.wohnzimmer"]
    assert "climate.ems_esp_thermostat_hc1" not in {r.climate for r in discover([s1, s2, s3], reg, Config(), all_rooms=True).rooms}
    # HomeKit-Ventil, das in Apple Home „Wohnzimmer Thermostat“ heißt: in den Vorschlägen und im Betrieb
    s4, e4 = clim("climate.wohnzimmer_thermostat", "Wohnzimmer Thermostat", "d1", area="wz")
    s5, e5 = clim("climate.kueche", "Küche", "d2", area="k")
    reg = Registry([e4, e5, e3], [{"id": "d1", "name": "Wohnzimmer Thermostat", "model": "SRT01"},
                                  {"id": "d2", "name": "Küche", "model": "SRT01"}, {"id": "ems", "name": "EMS"}],
                   [{"area_id": "wz", "name": "Wohnzimmer"}, {"area_id": "k", "name": "Küche"}])
    for all_rooms in (False, True):
        found = discover([s3, s4, s5], reg, Config(), all_rooms=all_rooms)
        assert sorted(r.climate for r in found.rooms) == ["climate.kueche", "climate.wohnzimmer_thermostat"]


# [2] Auto-Modus: vorab angehakt nur die Betriebsauswahl – kein Ventil doppelt, kein Auto als Raum ------------
def house_with_cloud_and_homekit():
    s_zone, e_zone = clim("climate.wohnzimmer", "Wohnzimmer", "zone", platform="tado")
    s_va1, e_va1 = clim("climate.tado_smart_radiator_thermostat_va1", "tado Smart Radiator Thermostat VA1", "d1")
    s_ru1, e_ru1 = clim("climate.tado_smart_thermostat_ru1", "tado Smart Thermostat RU1", "d2")
    s_car, e_car = clim("climate.auto", "BMW Klima", "car", platform="bmw_connected_drive")
    devices = [{"id": "zone", "name": "Wohnzimmer", "model": "HEATING", "area_id": "wz"},
               {"id": "d1", "name": "tado Smart Radiator Thermostat VA1", "model": "SRT01", "area_id": "wz"},
               {"id": "d2", "name": "tado Smart Thermostat RU1", "model": "ST01", "area_id": "bad"},
               {"id": "car", "name": "BMW", "model": "i3"}]
    areas = [{"area_id": "wz", "name": "Wohnzimmer"}, {"area_id": "bad", "name": "Bad"}]
    return [s_zone, s_va1, s_ru1, s_car], [e_zone, e_va1, e_ru1, e_car], devices, areas


def test_suggestions_name_the_operating_rooms_and_ui_prechecks_only_those(tmp_path):
    states, ents, devices, areas = house_with_cloud_and_homekit()
    sug = discover(states, Registry(ents, devices, areas), Config(), all_rooms=True)
    assert {r.climate for r in sug.rooms} == {s["entity_id"] for s in states}  # Vorschläge für alle (Felder)
    assert sug.auto_rooms == ["climate.wohnzimmer"]  # Betrieb: nur die Cloud-Zone – wie discover() ohne all_rooms
    assert sug.auto_rooms == discover(states, Registry(ents, devices, areas), Config()).climate_ids()
    assert json.loads(json.dumps(sug.as_dict(private=False)))["auto_rooms"] == ["climate.wohnzimmer"]
    # ohne Tado-Cloud: die HomeKit-Geräte, nicht das Auto (Klimagerät fremder Plattform) – Betrieb und Vorschlag gleich
    no_cloud = discover(states[1:], Registry(ents[1:], devices[1:], areas), Config(), all_rooms=True)
    assert no_cloud.auto_rooms == ["climate.tado_smart_radiator_thermostat_va1", "climate.tado_smart_thermostat_ru1"]
    assert discover(states[1:], Registry(ents[1:], devices[1:], areas), Config()).climate_ids() == no_cloud.auto_rooms
    # nur fremde Plattform mit Gerät: bleibt (einziger Kandidat) wie bisher
    assert discover(states[3:], Registry(ents[3:], devices[3:], areas), Config()).climate_ids() == ["climate.auto"]
    assert discover(states, Registry(ents, devices, areas), Config()).auto_rooms == []  # nur bei all_rooms gefüllt

    js = script()
    rows = between(js, "function roomRows()", "function applyAreas()")
    assert "const pre = new Set(SUG.auto_rooms || []);" in rows and "const on = isSet ? true : (auto && pre.has(c.id));" in rows
    apply = between(js, "function applySuggestions()", "async function testEms")
    assert 'tr.querySelector(".r-on").checked = pre.has(tr.dataset.climate);' in apply
    if not NODE:
        pytest.skip("node nicht verfügbar")
    # Szenario im Browser-Skript: Auto-Modus, Tado-Cloud-Zone + HomeKit-Ventil desselben Raums + Auto
    sug_json = json.dumps(sug.as_dict(private=False))
    ent = [{"id": s["entity_id"], "name": s["attributes"]["friendly_name"], "platform": e["platform"],
            "area": next((d.get("area_id") for d in devices if d["id"] == e["device_id"]), None), "siblings": []}
           for s, e in zip(states, ents)]
    src = "\n".join(line for line in js.splitlines()
                    if line.startswith(("const $ =", "const fmt =", "const esc =", "const shortName =")))
    src += "\n" + "const sibling =" + between(js, "const sibling =", "// „Bereiche übernehmen“")
    src += f"\nfunction modeHint(){{}}\nconst document = {{ getElementById: () => ({{}}) }};\n" \
           f"const ENT = {json.dumps(ent)}; const SET = {{ rooms: [], floor_heating_rooms: [] }};\n" \
           f"const SUG = {sug_json}; const CTRL = {{}}; const CT = {{}};\n" \
           "for (const m of roomRows().matchAll(/data-climate=\"([^\"]*)\"[^>]*>\\s*<td><input type=\"checkbox\" class=\"r-on\" ?(checked)?>/g)) " \
           "console.log(m[1], m[2] ? 'on' : 'off');\n"
    out = dict(line.split() for line in run_js(src, tmp_path).strip().splitlines())
    assert out == {"climate.wohnzimmer": "on", "climate.tado_smart_radiator_thermostat_va1": "off",
                   "climate.tado_smart_thermostat_ru1": "off", "climate.auto": "off"}


# [3] Messpause nach Voreinstellungs-Änderung: Empfehlung weder aktuell noch „behoben“ --------------------------
def test_track_keeps_paused_balance_advice_unresolved(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (5, 6)})
    items = advice.build(eng, now)
    assert advice.track(eng.store, items, now) == []
    title = by_kind(items)[(WEAK, "abgleich")]["title"]
    key = f"{WEAK}|abgleich|{title}"
    # Nutzer stellt 5 -> 6; einen Tag später fehlt die Empfehlung wegen der Pause, die Info-Karte „misst neu“ steht
    eng.store.put(advice.PRESET_CHANGES_KEY, [{"ts": now, "room": WEAK, "from": 5, "to": 6, "flow_before": 70.0}])
    day = 86400
    items2 = advice.build(eng, now + day)
    kinds = {k[1] for k in by_kind(items2) if k[0] == WEAK}
    assert "abgleich_misst" in kinds and "abgleich" not in kinds
    assert advice.track(eng.store, items2, now + day) == []  # kein „Behoben“
    assert eng.store.get("advice_state")[key] == {"since": now, "title": title}  # Eintrag unverändert
    # nach der Pause ist der Raum immer noch unterversorgt: Empfehlung wieder da, „seit“ bleibt der Anfang
    items3 = advice.build(eng, now + 11 * day)
    assert advice.track(eng.store, items3, now + 11 * day) == []
    assert by_kind(items3)[(WEAK, "abgleich")]["since"] == now
    # andere Empfehlungen des Raums bleiben vom Pausenschutz unberührt (hier: der überversorgte Raum wird behoben)
    eng.store.put("advice_state", {f"{STRONG}|abgleich|x": {"since": now, "title": "x"}})
    assert [r["title"] for r in advice.track(eng.store, items2, now + day)] == ["x"]


# [4] Erwartungswerte im Satz und in den Chips gleich gerundet ---------------------------------------------------
def test_expectation_rounds_the_same_in_text_and_chips(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (5, 6), STRONG: (4, 6), A: (4, 6), B: (4, 6)})
    under = by_kind(advice.build(eng, now))[(WEAK, "abgleich")]
    assert under["expected"]
    for e in under["expected"]:
        assert e["k"] == advice._fmt_k(e["delta_k"]) and f"{e['room_name']} ≈ {e['k']}" in under["text"]
    assert advice._fmt_k(2.6) == "+3 K" and advice._fmt_k(-4.1) == "−4 K" and advice._fmt_k(1.3) == "+1,3 K"
    js = script()
    adv = between(js, "function adviceItem(a)", "function renderSchedule(s)")
    assert "${esc(e.room_name)} ${esc(e.k || kelvin(e.delta_k))}</span>" in adv
    if not NODE:
        pytest.skip("node nicht verfügbar")
    kelvin = next(line for line in js.splitlines() if line.startswith("const kelvin ="))
    out = run_js(kelvin + "\nconsole.log(JSON.stringify([2.6, -4.1, 1.3, 0.0, -0.02, 0.46, 1.96].map(kelvin)));", tmp_path)
    assert json.loads(out) == ["+3 K", "−4 K", "+1,3 K", "±0 K", "±0 K", "+0,5 K", "+2,0 K"]
    assert [advice._fmt_k(v) for v in (2.6, -4.1, 1.3, 0.0, -0.02, 0.46, 1.96)] == json.loads(out)


# [5] Auto-Modus ohne Tado-Cloud: zwei Ventile im selben Bereich bleiben unterscheidbar -------------------------
def test_auto_mode_names_valves_in_the_same_area_apart():
    s1, e1 = clim("climate.tado_smart_radiator_thermostat_va1", "tado Smart Radiator Thermostat VA1", "d1")
    s2, e2 = clim("climate.tado_smart_radiator_thermostat_va2", "tado Smart Radiator Thermostat VA2", "d2")
    s3, e3 = clim("climate.tado_smart_radiator_thermostat_va3", "tado Smart Radiator Thermostat VA3", "d3")
    devices = [{"id": d, "name": f"tado Smart Radiator Thermostat VA{i}", "model": "SRT01", "area_id": a}
               for i, (d, a) in enumerate((("d1", "wz"), ("d2", "wz"), ("d3", "k")), 1)]
    reg = Registry([e1, e2, e3], devices, [{"area_id": "wz", "name": "Wohnzimmer"}, {"area_id": "k", "name": "Küche"}])
    auto = discover([s1, s2, s3], reg, Config())
    assert [r.name for r in auto.rooms] == ["Wohnzimmer VA1", "Wohnzimmer VA2", "Küche"]
    assert all(r.group is None for r in auto.rooms)
    # Vorschläge (Einstellungen): Name = Bereich, Gruppe = Bereich – dort fasst die Gruppe die Ventile zusammen
    sug = discover([s1, s2, s3], reg, Config(), all_rooms=True)
    assert [(r.name, r.group) for r in sug.rooms] == [("Wohnzimmer", "Wohnzimmer")] * 2 + [("Küche", "Küche")]
    # gleicher HA-Name obendrein: die Entität unterscheidet
    s2["attributes"]["friendly_name"] = "tado Smart Radiator Thermostat VA1"
    names = [r.name for r in discover([s1, s2, s3], reg, Config()).rooms]
    assert len(set(names)) == 3 and names[2] == "Küche"
