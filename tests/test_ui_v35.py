"""0.35: Oberfläche und Doku zu Räumen (Bereiche, Gerätesensoren) und Hydraulik-Helfer – statische Prüfung.

Wie tests/test_ui_v34.py: die Oberfläche ist eine HTML-Datei ohne Build; geprüft wird, dass die Regeln aus
dem Entwurf sichtbar und die neuen Felder angebunden sind. Verhalten im Browser prüft zusätzlich das
Playwright-Skript (Screenshots), das nicht Teil der Suite ist.
HEATPILOT_STATIC / HEATPILOT_DOCS zeigen für den Vorher-Nachher-Vergleich auf andere Dateien.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "ems_heatpilot"
STATIC = Path(os.environ.get("HEATPILOT_STATIC", ROOT / "heatpilot" / "static"))
DOCS = Path(os.environ.get("HEATPILOT_DOCS", ROOT / "DOCS.md"))


def html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def script() -> str:
    return re.search(r"<script>(.*)</script>", html(), re.S).group(1)


def between(js: str, start: str, end: str) -> str:
    assert start in js, start
    return js.split(start)[1].split(end)[0]


def test_room_rows_show_area_and_device_sorted_by_area():
    js = script()
    rows = between(js, "function roomRows()", "function applyAreas()")
    # Zeile „Bereich: … · Gerät: … (Modell)“ unter der Entität
    assert "`Bereich: ${esc(area)}`" in rows and "`Gerät: ${esc(device)}`" in rows
    assert "c.device_model ? ` (${c.device_model})`" in rows
    # Sortierung: Bereich (ohne Bereich zuletzt), dann Name
    srt = between(js, "const climateEntities = () =>", "function roomRows()")
    assert "(a.area ? 0 : 1) - (b.area ? 0 : 1)" in srt and 'localeCompare(String(b.area || ""), "de")' in srt
    assert 'localeCompare(String(b.name || ""), "de")' in srt
    assert "const climates = climateEntities();" in rows
    # Gerätesensoren als Vorbelegung (Vorschlag, sonst Geschwister-Sensor des Geräts)
    assert 'r.heating_power || g.heating_power || sibling(c, "heating_power")' in rows
    assert 'r.temperature || g.temp_sensor || sibling(c, "temperature")' in rows
    assert 'r.humidity || g.humidity || sibling(c, "humidity")' in rows
    assert 'const sibling = (c, kind) => ((c.siblings || []).find((s) => s.kind === kind) || {}).id || ""' in js


def test_room_group_prefilled_only_when_empty_and_area_button():
    js = script()
    rows = between(js, "function roomRows()", "function applyAreas()")
    # gespeicherter Raum: nur der gespeicherte Wert, Bereich als Platzhalter; neuer Raum: Bereich als Wert
    assert 'const group = isSet ? (r.group || "") : (g.group || area);' in rows
    assert 'placeholder="${esc(area || "z. B. Wohnzimmer")}"' in rows
    assert 'data-area="${esc(area)}"' in rows
    # Knopf „Bereiche übernehmen“ füllt nur leere Felder
    btn = between(js, "function applyAreas()", "function learnedHint")
    assert "if (inp.value.trim() || !tr.dataset.area) continue;" in btn
    assert "inp.value = tr.dataset.area" in btn
    assert 'onclick="applyAreas()">Bereiche übernehmen</button>' in js
    # Raumregler: Vorschlag bei Wandthermostat nur für noch nicht gespeicherte Räume, mit Hinweis
    assert "const wall = !!(g.controller || c.controller), controller = isSet ? !!r.controller : wall;" in rows
    assert 'wall && !isSet ? "Vorschlag"' in rows
    sug = between(js, "function applySuggestions()", "async function testEms")
    assert 'if (g.controller) tr.querySelector(".r-controller").checked = true;' in sug
    assert ".r-managed" not in sug  # „macht mit“ setzt nie ein Vorschlag


def test_room_rows_preset_columns_and_collect():
    js, markup = script(), html()
    rows = between(js, "function roomRows()", "function applyAreas()")
    assert 'class="r-preset" min="0" max="${pmax}" step="1" value="${esc(r.preset ?? "")}"' in rows
    assert 'class="r-preset-max" min="0" max="${pmax}" step="1" value="${esc(r.preset_max ?? "")}"' in rows
    assert "const pmax = lim.preset_max || 12;" in rows
    # Hilfetexte: Zahl am Ventilunterteil, leer = unbekannt, Stufen 0 = kein Voreinstellventil
    assert "leer = unbekannt" in rows and "0 = kein Voreinstellventil" in rows
    assert '${field("Voreinstellung (Stufe am Ventil)", `<input type="number" class="r-preset"' in js  # 0.43: Raumliste
    assert "z. B. 5 von 6 – leer = unbekannt, Stufen 0 = kein Voreinstellventil" in js
    assert "Schlüsselgeräts</b> (Raumregler, sonst das erste)" in js
    coll = between(js, "function collect()", "async function saveSettings")
    assert 'preset: tr.querySelector(".r-preset").value.trim()' in coll
    assert 'preset_max: tr.querySelector(".r-preset-max").value.trim()' in coll
    assert 'table.form input.r-preset, table.form input.r-preset-max' in markup


def test_save_reports_key_device_and_preset_changes():
    js = script()
    save = between(js, "async function saveSettings()", "async function testEms")
    assert "const note = saveNote(j);" in save
    assert '"Gespeichert und übernommen." + note' in save
    assert "Schlüsselgerät ${nameOf(r.climate)}${r.controller ? \" (Raumregler)\" : \" (erstes Gerät, kein Raumregler)\"}" in save
    assert "(j.rooms || []).filter((r) => (r.members || []).length)" in save
    assert "(j.preset_changes || [])" in save and "Tage keine Abgleich-Empfehlung für den Raum" in save
    assert "BAL = j.balance || {}" in js


def test_advice_shows_steps_expectation_and_effect():
    js = script()
    assert "renderAdvice(s.advice || { items: [], resolved: [] });" in js
    adv = between(js, "function adviceItem(a)", "function renderSchedule(s)")
    # Stufe als Badge, Erwartung je Raum als Chips (negativ = grün), Wirkungszeile abgesetzt, Info-Karten ohne „seit“
    assert "Voreinstellung ${esc(p.from)} → ${esc(p.to)}${p.max ? ` von ${esc(p.max)}` : \"\"}" in adv
    assert 'Erwartung (grob), Vorlauf:' in adv and 'class="${e.delta_k < 0 ? "down" : ""}"' in adv
    assert "kelvin(e.delta_k)" in adv and 'const kelvin = (d) => `${d < 0 ? "−" : "+"}' in js
    assert "/^(.*?)\\s(Wirkung(?::| der Voreinstellung).*)$/s" in adv
    assert 'a.kind === "abgleich_wirkung" ? a.text : ""' in adv
    assert 'const info = a.impact === "Info";' in adv and "if (!info && a.since) ev.push(`seit ${dateOf(a.since)}`);" in adv
    assert 'class="adv-effect"' in adv
    assert "Voreinstellung und Stufen der Ventile stehen unter Einstellungen → Räume" in adv


def test_docs_describe_rooms_and_balance_rules():
    doc = re.sub(r"\s+", " ", DOCS.read_text(encoding="utf-8"))
    for rule in ("Bereich: Wohnzimmer · Gerät: tado Smart Radiator Thermostat VA… (SRT01)", "Bereiche übernehmen",
                 "Raumregler-Häkchen bei Wandthermostaten", "Gespeichertes wird nie überschrieben",
                 "Voreinstellung von 5 auf 6 stellen (Skala 1–6)", "mehr geht am Ventil nicht", "Bei Stufe 1 wird kein „zurück“ empfohlen",
                 "Stufen 0 heißt: kein Voreinstellventil", "Rücklaufverschraubung",
                 "kv ∝ (p / p_max)²", "Durchfluss^0,3", "(T_req − T_Raum) · (Anteil_alt / Anteil_neu)^0,3",
                 "Erwartung (grob): Flur ≈ −5 K, Wohnzimmer ≈ +2 K, Küche ≈ +1,0 K Vorlauf",
                 "10 Tage lang keine neue Abgleich-Empfehlung", "HeatPilot misst neu", "Ab 14 Tagen bis 60 Tage",
                 "braucht jetzt 58 °C statt 64 °C Vorlauf", "`abgleich_aenderungen`", "Schlüsselgerät",
                 "Voreinstellung und Stufen des Raums sind die des Schlüsselgeräts"):
        assert rule in doc, rule


def test_inline_script_is_valid_javascript(tmp_path):
    node = shutil.which("node") or next((p for p in ("/opt/node22/bin/node",) if Path(p).exists()), None)
    if not node:
        pytest.skip("node nicht verfügbar")
    scripts = re.findall(r"<script>(.*?)</script>", html(), re.S)
    assert len(scripts) == 1
    f = tmp_path / "inline.js"
    f.write_text(scripts[0], encoding="utf-8")
    res = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
