"""0.43 Prüfung: Funde der Oberflächen-Prüfung nachgestellt und behoben.

Startliste der Einstellungen im Auto-Modus, keine Entwicklerbegriffe in Hinweisen und Kessel-Regeln, abgefangene
Fehler beim Speichern und beim EMS-ESP-Test.
"""
import json
import os
import re
import subprocess

import pytest

from test_ui_v361 import NODE, script, source

from heatpilot.entity_map import ROLES


def _setinfo(tmp_path, set_, sug):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    code = ("let EM_BY = {}; const DHW_LABEL = {}, BOILER_LABEL = {};\n" + source(js, "esc") +
            f"\nlet SET = {json.dumps(set_)}, SUG = {json.dumps(sug)};\n" + source(js, "setInfo") +
            "\nconsole.log(JSON.stringify(setInfo('raeume')));")
    f = tmp_path / "setinfo.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, check=False, env=dict(os.environ))
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_start_list_rooms_falls_back_to_auto_detection(tmp_path):
    auto = {"auto_rooms": ["climate.a", "climate.b", "climate.c", "climate.d", "climate.e"]}
    assert _setinfo(tmp_path, {"rooms": []}, auto) == "5 Räume · automatisch"
    assert _setinfo(tmp_path, {"rooms": []}, None) == "automatisch"
    assert _setinfo(tmp_path, {"rooms": [{"managed": True}, {"managed": False}]}, auto) == "2 Räume · 1 machen mit"


def test_entity_hints_without_internal_terms():
    hints = " ".join(str(v[1]) for v in ROLES.values() if isinstance(v, tuple) and len(v) > 1)
    for bad in ("Weg W", "Weg B", "Trockenlauf"):
        assert bad not in hints, bad


def test_boiler_rules_without_service_names():
    rules = source(script(), "boilerIntro")
    for bad in ("number.set_value", "switch.turn_on", "turn_off"):
        assert bad not in rules, bad
    assert "HeatPilot schreibt nur das Vorlauf-Soll" in rules


def test_save_and_ems_test_catch_failed_post():
    js = script()
    for name in ("saveSettings", "testEms"):
        body = source(js, name)
        assert re.search(r"try \{ j = await readJSON\(await fetch\(", body), name
        assert "catch (e)" in body, name
