"""0.34: Oberfläche und Doku zur Steuerung – statische Prüfung von index.html und DOCS.md.

Die Oberfläche ist eine HTML-Datei ohne Build; geprüft wird, dass die Regeln aus dem Entwurf
sichtbar und die neuen Felder angebunden sind. Verhalten im Browser prüft zusätzlich das
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


def test_settings_card_steuerung_first_with_switch_warning_plan_and_limits():
    js = script()
    # Karte „Steuerung“ ganz oben, Schalter mit genau den zwei Stellungen, Warnhinweis laut Entwurf
    assert 'card("Steuerung", controlCard(s)) +\n    card("Verbindung"' in js
    assert 'name="control_mode" value="beobachten"' in js and 'name="control_mode" value="heatpilot"' in js
    assert "Zurück zu Tado nur hier" in js and "zuletzt geschriebene Soll stehen" in js
    assert "keinen automatischen Rückfall" in js
    # Nachtplan 7 Zeilen Mo..So mit Beginn/Ende, Nacht-/Abwesenheitstemperatur, Vorheizdeckel
    assert 'WEEKDAYS.map((d, i) =>' in js and 'class="np-start"' in js and 'class="np-end"' in js
    assert 'num("f-night_temp"' in js and 'num("f-away_temp"' in js and 'num("f-preheat_max_h"' in js
    # Standard beim Speichern ohne Auswahl: beobachten
    assert 'out.control_mode = cm ? cm.value : "beobachten"' in js
    assert "out.night_plan = [...document.querySelectorAll(\"tr.np\")]" in js
    assert '"preheat_max_h"' in js.split("function collect()")[1].split("out.debug_logging")[0]


def test_room_table_sends_group_controller_managed_comfort():
    js = script()
    rows = js.split("function roomRows()")[1].split("function learnedHint")[0]
    for cls in ("r-managed", "r-comfort", "r-group", "r-controller"):
        assert f'class="{cls}"' in rows
    # Komfort-Startwert: gespeichert; schon gewählter Raum ohne Wert 21 (wie das Backend); neuer Raum: aktuelles Soll, sonst 21
    assert "r.comfort ?? (chosen[c.id] ? 21 : (CT[c.id] ?? 21))" in rows
    coll = js.split("function collect()")[1].split("function saveSettings")[0]
    assert 'group: tr.querySelector(".r-group").value.trim()' in coll
    assert 'controller: tr.querySelector(".r-controller").checked' in coll
    assert 'managed: tr.querySelector(".r-managed").checked' in coll
    assert 'comfort: tr.querySelector(".r-comfort").value.trim()' in coll
    # „macht mit“ setzt nie ein Vorschlag – nur der Nutzer
    sug = js.split("function applySuggestions()")[1].split("async function testEms")[0]
    assert ".r-managed" not in sug


def test_heat_tab_pause_only_when_managing_and_status_from_control():
    js, markup = script(), html()
    assert 'id="heat-status"' in markup and 'id="heat-log-box"' in markup and 'id="heat-foot"' in markup
    render = js.split("function renderHeat()")[1].split("function act(")[0]
    assert '(pil ? act("pause", "Pause"' in render  # vierte Kachel nur bei heatpilot
    assert 'const managing = () => ctl().mode === "heatpilot"' in js
    assert "c.status_text" in js and "c.warnings" in js and "c.pause_left_min" in js
    # Pause-Dialog: 1 h / 2 h / 4 h / bis morgen / weiter über POST api/heat/pause {hours}
    pause = js.split("function pauseDlg()")[1].split("function syncBoost")[0]
    assert "heatPost('pause', { hours: ${h} }" in pause and "heatPost('pause', { hours: 0 }" in pause
    assert 'btn(1, "1 h")}${btn(2, "2 h")}${btn(4, "4 h")}${btn(morn' in pause
    assert "function hoursUntilMorning()" in js
    # Antwort einer Aktion enthält control (seit 0.36.1 auch readiness) – darf nicht verloren gehen
    assert "HEAT = { rooms: j.rooms, limits: j.limits, control: j.control, readiness: j.readiness }" in js
    # Protokoll: die letzten 20 Einträge
    assert "(c.log || []).slice(0, 20)" in js
    # Fußzeile „schaltet nie von selbst“ nur noch bei Beobachten
    assert '$("heat-foot").textContent = pil' in render
    assert "schreibt die Solltemperaturen der Räume mit „macht mit“ selbst" in render


def test_room_tile_state_from_control_status():
    js = script()
    st = js.split("function roomState(r)")[1].split("function roomTile")[0]
    assert "managing() && r.managed && c && c.phase" in st  # ohne Steuerung wie bisher
    assert "`HeatPilot: ${PHASE[c.phase] || c.phase} ${d}`" in st and "`Vorheizen → ${d}`" in st
    assert "manuell ${t}${until}" in st and "erreicht ≈ ${fmt(c.reach_c)}°" in st and "`Pause · ${t}`" in st
    assert "const PHASE = { komfort:" in js and '"urlaub"' not in st  # Phasennamen zentral


def test_boost_and_schedule_texts_depend_on_tado_cloud():
    js = script()
    assert "BOOST.rooms.includes(r.climate) && !r.tado_cloud" in js
    assert "Läuft als Timer in HeatPilot" in js and "Setzt in Tado einen Timer" in js
    sched = js.split("function schedDlg(j)")[1].split("function pauseDlg")[0]
    assert "r.tado_cloud" in sched and "Tado-App" in sched and "Wunschzustand von HeatPilot" in sched
    assert "j.hint" in js  # Hinweis der Antwort („Zeitplan in der Tado-App fortsetzen“) wird gezeigt


def test_overview_shows_weekly_plan_when_managing():
    js, markup = script(), html()
    assert 'id="sched"' in markup
    rs = js.split("function renderSchedule(s)")[1].split("function roomCheckText")[0]
    assert 's.control_mode === "heatpilot" && e.plan' in rs and "e.plan.map((d) =>" in rs
    assert "Zeitplan – HeatPilot steuert" in rs and "Zeitplan – Beobachten" in rs
    assert '"HeatPilot steuert" : "Beobachten – greift nicht ein"' in js
    # Einstellungen: „gelernt“-Hinweis entfällt beim Wochenplan
    assert 'src === "wochenplan") return ""' in js


def test_docs_describe_control_rules():
    doc = re.sub(r"\s+", " ", DOCS.read_text(encoding="utf-8"))  # Zeilenumbrüche der Doku spielen keine Rolle
    assert "## Steuerung: Beobachten oder HeatPilot steuert" in doc
    for rule in ("„macht mit“", "Komforttemperatur", "Raumregler", "Wochenplan", "Zurück zu Tado",
                 "nur beim Wechsel", "Phasenwechsel", "Pause", "15 min erneut", "ab drei Fehlschlägen in Folge",
                 "5–25 °C", "EMS-ESP wird weiterhin nur gelesen", "ohne Koordinaten", "erreicht ≈ 19,5°",
                 "Vorheizdeckel", "Beginn So–Do 21:00, Fr und Sa 22:00; Ende Mo–Fr 04:30, Sa und So 06:30",
                 "zirkulär", "bleibt das zuletzt geschriebene Soll stehen", "Bei „Beobachten“ oder ohne „macht mit“ schreibt HeatPilot nie von selbst"):
        assert rule in doc, rule
    assert "nur auf Knopfdruck, HeatPilot schaltet nie von selbst." not in doc  # gilt nur noch bei Beobachten


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
