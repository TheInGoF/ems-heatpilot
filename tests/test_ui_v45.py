"""0.45: Oberfläche – gefundene Heizleistung im Raumfeld mit Herkunft, Ergebnis von „Hausdaten wiederherstellen“ je
Raum (Stunden aus der Stundenstatistik), Abfrage ``valve_found``, Version, Changelog und Doku. Erfundene Namen."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_ui_v361 import NODE, html, plain, script, source

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
STUBS = """
const emsLock = () => "", floorCell = () => "", roomStates = () => "", relearnHtml = () => "";
let SET = {}, SUG = {}, ENT = [], CT = {}, CTRL = {};
"""


def js_run(tmp_path, names, body: str):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    code = STUBS + "\n".join(source(js, n) for n in names) + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe45.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


ROOM_NAMES = ("esc", "field", "sibling", "climateEntities", "shortName", "VFOUND", "roomRows")
FOUND = {"climate.hk_wohnen": {"sensor": "sensor.alt_wohnen_heating", "how_text": "vom alten Thermostat",
                               "from_name": "Wohnen Zone", "how": "adopted", "from": "climate.alt_wohnen"}}


def test_room_field_shows_found_heating_power_with_origin(tmp_path):
    got = js_run(tmp_path, ROOM_NAMES, f"""
ENT = [{{ id: "climate.hk_wohnen", name: "VA11" }}];
VFOUND = {json.dumps(FOUND)};
SET = {{ rooms: [{{ climate: "climate.hk_wohnen", name: "Wohnen" }}] }};
const found = roomRows();
SET = {{ rooms: [{{ climate: "climate.hk_wohnen", name: "Wohnen", heating_power: "sensor.eigenes_ventil" }}] }};
const own = roomRows();
VFOUND = {{}};
SET = {{ rooms: [{{ climate: "climate.hk_wohnen", name: "Wohnen" }}] }};
out({{ found, own, none: roomRows() }});
""")
    assert 'class="r-valve" list="dl-sensor" value="sensor.alt_wohnen_heating"' in got["found"]
    hint = "gefunden vom alten Thermostat Wohnen Zone und schon genutzt – Speichern trägt sie fest ein"
    assert hint in plain(got["found"])
    assert 'value="sensor.eigenes_ventil"' in got["own"] and "gefunden" not in got["own"]  # eingetragen hat Vorrang
    assert 'class="r-valve" list="dl-sensor" value=""' in got["none"] and "gefunden" not in got["none"]


def test_restore_result_names_hours_per_room(tmp_path):
    rooms = [{"name": "Wohnen", "hours": 4210, "heating": True, "first": 1_760_000_000, "last": 1_775_000_000},
             {"name": "Flur", "hours": 300, "heating": False, "heat_why": "keine Heizleistung (%) gefunden",
              "first": 1_760_000_000, "last": 1_761_000_000},
             {"name": "Bad", "hours": 0, "why": "kein alter Temperatursensor (Alias)"}]
    got = js_run(tmp_path, ("fmt", "esc", "tsDay", "restoreNotes"), f"""
out({{ with: restoreNotes({{ room_stats: {json.dumps(rooms)} }}), without: restoreNotes({{}}) }});
""")
    text = re.sub(r" ([,.])", r"\1", plain(got["with"]))                  # plain trennt an Tags mit Leerzeichen
    assert "Räume aus der Stundenstatistik: Wohnen 4.210 h, Flur 300 h ohne Heizleistung." in text
    assert "Nicht aus der Stundenstatistik: Bad (kein alter Temperatursensor (Alias))." in text
    assert 'title="keine Heizleistung (%) gefunden"' in got["with"] and 'title="09.10.2025–' in got["with"]
    assert got["without"] == ""                                          # ältere Protokolleinträge: wie bisher


def test_settings_api_and_loader_carry_valve_found():
    web = (PKG / "web.py").read_text(encoding="utf-8")
    assert '"valve_found": getattr(app.eng.found, "valve_found", None) or {}' in web
    js = source(script(), "loadSettings")
    assert "VFOUND = j.valve_found || {};" in js
    ask = source(script(), "JOB_ASK")
    assert "dazu je Raum die Stundenwerte alter Sensoren (Temperatur, Heizleistung)" in ask
    assert "an Home Assistant wird nichts geschrieben" in ask


def test_inline_script_parses():
    if not NODE:
        pytest.skip("node nicht verfügbar")
    res = subprocess.run([NODE, "--check", "-"], input=script(), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert len(re.findall(r"<script>", html())) == 1


def test_version_changelog_docs():
    from heatpilot import __version__
    assert __version__ >= "0.45.0"
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = log.split("\n## 0.45.0\n")[1].split("\n## ")[0]
    for word in ("room_heating.py", "room_stats.py", "room_stats_learn.py", "0,3–6", "tests/test_room_stats45.py",
                 "tests/test_ui_v45.py", "valve_found"):
        assert word in head, word
    docs = (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
    for word in ("**Heizleistung vom alten Thermostat** (seit 0.45)", "**Räume aus der Stundenstatistik** (seit 0.45",
                 "zwischen 0,3 und 6", "Raumwerte aus der Stundenstatistik"):
        assert word in docs, word
