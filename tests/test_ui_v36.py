"""0.36: Oberfläche zu den Nachtzeiten aus dem Stromverbrauch – statische Prüfung.

Wie tests/test_ui_v35.py: die Oberfläche ist eine HTML-Datei ohne Build; geprüft wird, dass die neuen Felder
angebunden sind (Listen, Häkchen, Spalten nur mit Sensoren, Datalist-Filter, collect(), Zeitplan-Box mit
Herkunft „Strom“) und das Inline-Skript gültiges JavaScript ist. Verhalten im Browser prüft zusätzlich das
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


def test_power_picker_filters_power_sensors_only():
    # nur sensor.* mit device_class power oder Einheit W/kW – kein Energiezähler, kein Temperaturfühler; seit 0.39.1 der
    # Filter der Seite „Entitäten“ (entity_map, in der Oberfläche emFits) – beide Listen nutzen ihn
    from heatpilot import entity_map as em
    f = em.filter_of("power")
    assert f == {"domains": ["sensor"], "units": ["W", "kW"], "device_classes": ["power"]}
    assert em.fits("sensor.haus", "W", None, f) and em.fits("sensor.haus", "kW", None, f)
    assert em.fits("sensor.haus", None, "power", f)
    assert not em.fits("sensor.zaehler", "kWh", "energy", f) and not em.fits("sensor.t", "°C", "temperature", f)
    assert not em.fits("input_number.haus", "W", None, f)
    assert [em.FIELD_ENTRIES[k][2:4] for k in ("power_sensors", "power_exclude")] == [("power", True), ("power", True)]
    assert "isPower" not in script() and "entList(" not in script()


def test_control_card_has_power_section_lists_and_checkbox():
    js, markup = script(), html()
    card = between(js, "function controlCard(s)", "function modeHint()")
    assert "Nachtzeiten aus dem Stromverbrauch</h3>" in card
    assert "const power = s.power_sensors || [], powerEx = s.power_exclude || [];" in card
    # Sensoren seit 0.39.1 unter „Entitäten“ → Strom; hier, was gewählt ist (und was fehlt), mit Sprung dorthin
    assert "${powerRef(power, powerEx, missing)}" in card
    ref = between(js, "function powerRef(power, powerEx, missing) {", "function modeHint()")
    assert "Leistungssensoren: ${power.length ? list(power) : \"keine gewählt – die Funktion ist aus\"}" in ref
    assert "onclick=\"emGo('strom');return false\">ändern unter Entitäten</a>" in ref
    from heatpilot.entity_map import FIELD_ENTRIES
    assert FIELD_ENTRIES["power_sensors"][1] == "Leistungssensoren (Haus, ggf. je Phase)"
    assert "bei drei Phasen alle drei" in FIELD_ENTRIES["power_sensors"][5]
    assert FIELD_ENTRIES["power_exclude"][1] == "Nicht zählen (Waschmaschine, Wallbox …)"
    assert 'field("Gelernte Zeiten verwenden"' in card
    assert '<input type="checkbox" id="f-night_plan_auto" ${s.night_plan_auto ? "checked" : ""}>' in card
    assert "Sonst nur Vorschlag; wo nichts Belastbares gelernt ist, gilt der Wochenplan oben." in card
    # Sensoren, die Home Assistant nicht kennt, werden genannt (found.power aus sleep.sensors)
    assert "const missing = SLEEP ? power.filter((p) => !(SLEEP.sensors || []).includes(p)) : [];" in card
    # Mehrfach-Liste (seit 0.39.1 auf der Seite „Entitäten“): je Entität ein ×, das Suchfeld hängt eine an
    assert "function emDrop(key, n) { EM_VAL[key] = (EM_VAL[key] || []).filter((_, i) => i !== n); emRefresh(key); }" in js
    assert "if (e.multi) { if (!(EM_VAL[key] || []).includes(id)) EM_VAL[key] = [...(EM_VAL[key] || []), id];" in js
    assert ".em-pick input { flex: 1; width: auto; min-width: 0; }" in markup


def test_learned_columns_only_with_sensors_and_mark_used_halves():
    js = script()
    card = between(js, "function controlCard(s)", "function modeHint()")
    # Spalten und Zeile nur, wenn Sensoren gesetzt sind und die Zusammenfassung da ist
    assert "const learned = power.length && SLEEP && (SLEEP.plan || []).length === 7 ? SLEEP.plan : null;" in card
    assert '${learned ? "<th>gelernt ab</th><th>gelernt bis</th>" : ""}' in card
    assert '${learned ? `<td class="np-learned">${learnedCell(learned[i], "start", effPlan[i])}</td><td class="np-learned">${learnedCell(learned[i], "end", effPlan[i])}</td>` : ""}' in card
    assert '${learned ? `<p class="muted" id="sleep-line" style="margin:4px 0 0">${sleepLine(SLEEP)}</p>` : ""}' in card
    # „gilt“ aus dem wirksamen Wochenplan (api/settings → schedule.effective.plan), nicht aus der Zusammenfassung
    assert "const effPlan = (SCHED && SCHED.effective && SCHED.effective.plan) || [];" in card
    cell = between(js, "function learnedCell(d, half, eff)", "function sleepLine(sl)")
    assert 'const used = !!eff && eff["source_" + half] === "gelernt (Strom)";' in cell
    assert "✓ gilt" in cell
    # Text „21:35 · 5 Nächte ± 20 min“, grau wenn nicht belastbar, Strich ohne Wert
    assert '`${esc(d[half])} · ${n} ${n === 1 ? "Nacht" : "Nächte"}${sp !== null && sp !== undefined ? ` ± ${sp} min` : ""}`' in cell
    assert '<span class="${ok ? "" : "muted"}"' in cell
    assert "if (!d || !d[half]) return '<span class=\"muted\">–</span>';" in cell
    line = between(js, "function sleepLine(sl)", "// Steuerung (0.34)")
    assert "`Gelernt aus ${sl.nights} ${sl.nights === 1 ? \"Nacht\" : \"Nächten\"}${skipped ? `; ausgelassen: ${skipped}` : \"\"}.`" in line
    assert "Object.entries(sl.invalid || {}).map(([r, n]) => `${n}× ${esc(r)}`)" in line


def test_collect_sends_power_lists_and_auto_flag():
    js = script()
    coll = between(js, "function collect()", "async function saveSettings")
    # Sensorlisten seit 0.39.1 über die Seite „Entitäten“ (emCollect: Felder als Arrays), Häkchen als bool
    assert "return emCollect(out);" in coll
    em = between(js, "function emCollect(out) {", "\n}\n")
    assert 'else if (s.kind === "field" && !EM_CUSTOM[e.key]) out[s.name] = e.multi ? [...(v || [])] : String(v || "").trim();' in em
    assert 'out.night_plan_auto = $("f-night_plan_auto").checked;' in coll
    # Speichern: geänderte Sensoren → Hinweis, dass die Historie neu eingelesen wird
    save = between(js, "async function saveSettings()", "function saveNote(j)")
    assert 'const reload = !same("power_sensors") || !same("power_exclude");' in save
    assert "const same = (k) => !(k in body) ||" in save  # nicht mitgeschickt (Seite nicht geladen): nichts neu einlesen
    assert "Historie wird neu eingelesen – die gelernten Nachtzeiten erscheinen danach." in save
    # Daten aus der api/settings-Payload: sleep neben settings/schedule
    load = between(js, "async function loadSettings()", "let OV_STALE")
    assert "SLEEP = j.sleep || null;" in load
    assert "SLEEP = null;" in js.split("function sunText")[0]


def test_overview_schedule_marks_learned_source():
    js = script()
    box = between(js, "function renderSchedule(s)", "function roomCheckText")
    # wirksame Zeit je Tag, Herkunft „Strom“ aus source_start/source_end (api/summary → schedule.effective.plan)
    assert 'const strom = (d, k) => d["source_" + k] === "gelernt (Strom)";' in box
    assert "<td>${cell(d, \"start\")}</td><td>${cell(d, \"end\")}</td>" in box
    assert '<span class="badge" title="aus dem Hausstromverbrauch gelernt">Strom</span>' in box
    assert "const sl = s.sleep || {}, powered = (sl.sensors || []).length > 0;" in box
    assert "aus dem Hausstromverbrauch gelernt (${sl.nights || 0} Nächte, Median der letzten ${sl.weeks || 6} Wochen)" in box
    # Beobachten: nur Hinweis auf den Vorschlag
    assert "Vorschlag unter Einstellungen → Steuerung, wirksam erst bei „HeatPilot steuert“" in box


def test_docs_describe_power_settings():
    doc = re.sub(r"\s+", " ", DOCS.read_text(encoding="utf-8"))
    for rule in ("### Nachtzeiten aus dem Stromverbrauch", "Leistungssensoren (Haus, ggf. je Phase)", "Nicht zählen",
                 "Gelernte Zeiten verwenden", "„gelernt ab“ / „gelernt bis“"):
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
