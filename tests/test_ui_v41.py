"""0.41: Oberfläche zu „Kessel über HeatPilot“ (Weg B) – statische Prüfung und Texte in Node.

Wie tests/test_ui_v40.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird der Einbau (eigene Karte
„Kessel über HeatPilot“ nach „Haus & Kessel“ mit Schalter, Rückfall-Vorlauf, Warnhinweisen, Statusbox und Knopf
„Anleitung Wächter“; Speichern schickt Schalter und Rückfall-Vorlauf und fragt beim Umschalten auf „HeatPilot setzt“
einmal nach; Statuszeile in Heizen und Übersicht; Kessel-Zeilen im Protokoll; Block in „Bereit für die Steuerung?“;
„schreibt“ in der Vorschau; Auswahl der zwei Entitäten; „empfohlen“ bei den Benachrichtigungen), dass die Oberfläche
selbst nichts an den Kessel schreibt (einziger Aufruf: „wieder einschalten“ mit leerem Inhalt) und ältere Antworten ohne
die Felder still weglassen, und die Texte – in Node mit Antworten des echten Backends (``BoilerControl`` an einem
künstlichen Haus aus tests/test_boiler41.py: schreibt, gesperrt durch einen Regler, Trockenlauf, pausiert,
zurückgeschaltet, aus). Bilder in 390/1100 px, hell/dunkel prüft das Playwright-Skript, das nicht Teil der Suite ist.
"""

import asyncio
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer
from heatpilot import main as main_mod
from heatpilot import notify
from heatpilot import preview as pv
from heatpilot.boiler_status import ready
from heatpilot.config import Config
from heatpilot.web import make_app
from test_boiler41 import FLOW, HEAT, T0, cycle, fake_app, house, run, shows, step
from test_ui_v361 import NODE, between, html, plain, script, source
from test_ui_v39 import as_json, berlin  # noqa: F401 – berlin ist eine Fixture

ROOT = Path(__file__).resolve().parents[1]
SECTION = "// ---------------------------------------------------------------- Kessel über HeatPilot (Weg B, 0.41)"
SECTION_END = "// ---------------------------------------------------------------- Benachrichtigungen (0.38)"
NAMES = ("fmt", "esc", "clock", "timeOf", "degC", "READY", "readyDot", "ICON", "field", "shortName", "DROP", "DHW_ACTION",
         "ACTION_NAME", "heatLogRows", "renderHeatLog",
         "BOILER_LABEL", "BOILER_WHAT", "BOILER_ACTION", "BOILER_RULES", "FLAME", "WD_DOT", "BOILC", "BOILER_OPEN",
         "boilerWarnings", "boilerDot", "boilerVal", "boilerLine", "renderBoilerLine", "boilerSectionHtml", "boilerIntro",
         "boilerWarnText", "boilerEntityRef", "boilerBoxHtml", "boilerFacts", "boilerTarget", "boilerDecision",
         "boilerHistory", "boilerWatchHtml", "boilerGuideHtml", "boilerGuide", "boilerModeHint", "boilerModeText",
         "boilerConfirm", "boilerReadyHtml", "boilerEntCell", "boilerLogValue",
         "PV_NOTE", "PV_WHAT", "PV_C", "PHASE", "WEEKDAYS", "tsOf", "tclock", "wday", "tipKey", "pvWrite", "pvRooms",
         "pvNote", "pvSummary", "pvTip", "pvTable", "NOTIFY_KIND", "notifyHtml", "notifyState")
# DOM-Ersatz: $() liefert je id ein Objekt; RADIO ist die gewählte Stellung des Schalters, confirm() merkt sich die Frage
STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "", attrs: {},
  setAttribute(k, v) { this.attrs[k] = v; } });
let RADIO = null, SET = null, EM_BY = {}, EM_VAL = {}, HEAT = null;
const document = { querySelector: (q) => q.includes("boiler_control") && RADIO ? { value: RADIO } : null, querySelectorAll: () => [] };
const emValue = (k) => EM_VAL[k];
const ASKED = [];
const confirm = (t) => { ASKED.push(t); return true; };
"""


def js_run(tmp_path, body: str):
    """``body`` mit den Funktionen von oben und dem DOM-Ersatz in Node; gibt das JSON von ``out(...)`` zurück."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    code = STUBS + "\n".join(source(script(), n) for n in NAMES) + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe41.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def hm(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Berlin")).strftime("%H:%M")


def scene(tmp_path, kind: str):
    """Echte Antworten von Weg B: (eng, bc, Status wie ``api/boiler``, Bereitschaft, Zeitpunkt)."""
    if kind == "schreibt":  # 46 °C geschrieben und übernommen, dann taktet der Kessel: Boost +2 K → 48 °C
        eng, ha, bc = house(tmp_path)
        step(bc, T0)
        shows(eng, flow=46)
        step(bc, T0 + 60)
        cycle(eng, T0 + 90)
        step(bc, T0 + 120)
        now = T0 + 150
    elif kind == "gesperrt":  # EMS-ESP meldet einen Regler: nichts geschrieben; keine Wächter-Automation
        eng, ha, bc = house(tmp_path, regulator=True)
        eng.watchdog = {"ts": T0, "found": [], "error": None, "checked": 2}
        step(bc, T0)
        now = T0 + 30
    elif kind == "trocken":  # Trockenlauf mit Regler am Bus: würde 46 °C schreiben
        eng, ha, bc = house(tmp_path, mode="trocken", regulator=True)
        step(bc, T0)
        now = T0 + 30
    elif kind == "pausiert":  # der Kessel nimmt den Wert dreimal nicht an
        eng, ha, bc = house(tmp_path)
        t = T0
        for _ in range(3):
            step(bc, t)
            t += 60
            step(bc, t)
        now = t + 30
    elif kind == "zurueck":  # von „HeatPilot setzt“ zurück auf „Aus“: einmal 45 °C und Heizbetrieb an
        eng, ha, bc = house(tmp_path)
        step(bc, T0)
        run(main_mod.App._boiler_back(SimpleNamespace(cfg=Config(boiler_control="aus")), eng, eng))
        eng.cfg.boiler_control = "aus"
        bc.plan(T0 + 30)
        now = T0 + 60
    else:  # Standard „aus“
        eng, ha, bc = house(tmp_path, mode="aus")
        step(bc, T0)
        now = T0 + 30
    return eng, bc, as_json(bc.as_dict(now)), as_json(ready(eng, now)), now


def settings_x(eng, bc, now):
    """``boiler_control`` aus ``GET api/settings`` (wie web.py) und die gespeicherten Einstellungen."""
    from heatpilot import boiler_entity as be
    from heatpilot import boiler_rule as br

    x = {"modes": list(br.BOILER_MODES), "suggested": {"flow": be.suggest_flow(eng.found, eng.states),
                                                       "heating": be.suggest_heat(eng.found, eng.states)},
         "fallback_range": [br.FALLBACK_MIN, br.FALLBACK_MAX], "status": bc.as_dict(now), "readiness": ready(eng, now)}
    return as_json(x), as_json(eng.cfg.to_raw())


# ------------------------------------------------------------------ Einbau
def test_card_status_lines_log_ready_and_save_are_wired():
    js, page = script(), html()
    rs = between(js, "function renderSettings()", "function addVac()")
    assert re.findall(r'card\("([^"]+)"', rs).index("Kessel über HeatPilot") == 6  # nach „Haus & Kessel“
    assert 'card("Kessel über HeatPilot", boilerSectionHtml(s, BOILC)) +' in rs
    assert "boilerModeHint(); boilerScroll();" in rs
    assert "BOILC = j.boiler_control || null;" in between(js, "async function loadSettings()", "let OV_STALE")
    # Statuszeilen unter der des Warmwassers; heatPost behält den Kessel-Status
    assert '<p class="sub dhw-line" id="heat-dhw" hidden></p>\n    <p class="sub dhw-line" id="heat-boiler" hidden></p>' in page
    assert '<p class="sub dhw-line" id="ov-dhw" hidden></p>\n  <p class="sub dhw-line" id="ov-boiler" hidden></p>' in page
    assert 'renderBoilerLine("heat-boiler", j.boiler);' in between(js, "function renderHeat()", "function act(")
    assert 'renderBoilerLine("ov-boiler", s.boiler_control);' in between(js, "async function load() {", '$("tiles").innerHTML')
    assert "HEAT.boiler = j.boiler || null; HEAT.dhw = j.dhw; renderHeat();" in between(js, "async function heatPost(", "function heatAction(")
    log = between(js, "function renderHeatLog(c, d) {", "async function loadHeat()")
    assert 'k = l.kind === "boiler"' in log and "FLAME + esc(l.name || \"Kessel\")" in log and "boilerLogValue(l)" in log
    # „Bereit für die Steuerung?“: Block direkt nach „Kessel über EMS-ESP“, vor Ventilen und Warmwasser
    box = between(js, "function renderReadyBox(r, err = \"\") {", "function readyBoxHtml(")
    assert box.index("boilerReadyHtml(r.boiler_control, READY_SHOW.boiler_hp)") < box.index("valveReadyHtml(r.valves")
    assert "if (r && r.boiler_control)" in box  # ältere Version ohne Block: nichts
    # Speichern: Schalter und Rückfall-Vorlauf; vor dem Umschalten auf „HeatPilot setzt“ einmal nachfragen
    coll = between(js, "function collect()", "async function saveSettings")
    assert '"boiler_fallback_flow"]) out[k] = v("f-" + k);' in coll and "if (bc) out.boiler_control = bc.value;" in coll
    save = between(js, "async function saveSettings()", "function saveNote(j)")
    assert save.index("if (!boilerConfirm(body))") < save.index('await fetch("api/settings"')
    # Entitäten: Auswahl nur passender Entitäten (api/entities: flow.fits, heating.fits), Hinweis folgt der Wahl
    pickers = between(js, "const PICKERS = {", "};")
    assert 'flow: { filter: (e) => !!(e.flow && e.flow.fits), label: "Vorlauf-Soll (number, °C)" }' in pickers
    assert 'heating: { filter: (e) => !!(e.heating && e.heating.fits), label: "Heizbetrieb (switch)" }' in pickers
    assert 'if (key === "flow_set_entity" || key === "heating_switch_entity") boilerModeHint();' in source(js, "emRefresh")
    # Vorschau: Überschrift und gefüllte Ringe bei „schreibt“
    assert '"Vorschau – Kessel über HeatPilot schreibt" : "Vorschau (Trockenlauf)"' in source(js, "renderPreview")
    assert 'PV_C.flow, p.flow_writes === "schreibt");' in source(js, "pvFlowChart")
    assert '`<span><i class="ring own flow"></i>schreibt${ownD ? " (Kessel)" : ""}</span>`' in source(js, "pvLegend")


def test_ui_writes_nothing_to_the_boiler():
    """Die neuen Teile fragen nichts ab und schicken nichts – außer „wieder einschalten“ mit leerem Inhalt; welche
    Entität und welcher Wert, entscheidet das Backend."""
    js = script()
    assert re.findall(r'"api/boiler[^"]*"', js) == ['"api/boiler/resume"']
    part = re.sub(r"^\s*//.*$", "", between(js, SECTION, SECTION_END), flags=re.M)
    assert part.count("getJSON(") == 1 and 'body: "{}"' in source(part, "boilerResume")
    for name in re.findall(r"^(?:async )?function (\w+)\(", part, re.M):
        if name != "boilerResume":
            for word in ("api/", "getJSON(", "fetch(", "heatPost(", "method:", "set_value", "turn_on"):
                assert word not in source(part, name).replace("<code>number.set_value</code>", "").replace(
                    "<code>switch.turn_on</code>", ""), (name, word)
    for word in ("number.set_value", "switch.turn_on", "heatingactivated", "selflowtemp"):  # nie als Aufruf
        assert not re.search(rf'"api/[^"]*{word}', js)


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    f = tmp_path / "inline41.js"
    f.write_text(re.findall(r"<script>(.*?)</script>", html(), re.S)[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_new_functions_small_and_css_tokens():
    part = between(script(), SECTION, SECTION_END)
    names = re.findall(r"^(?:async )?function (\w+)\(", part, re.M)
    assert {"boilerLine", "boilerSectionHtml", "boilerBoxHtml", "boilerReadyHtml", "boilerGuideHtml", "boilerModeText",
            "boilerResume", "boilerConfirm"} <= set(names)
    for name in names:
        assert source(part, name).count("\n") <= 60, name
    page = html()
    css = between(page, "/* Kessel über HeatPilot (0.41)", "</style>")
    root = between(page, ":root {", "}")
    tokens = set(re.findall(r"var\((--[\w-]+)\)", css))
    assert tokens and all(t + ":" in root for t in tokens), tokens  # nur Farben, die es in beiden Themen gibt
    assert "#heat-boiler { margin: -4px 0 12px; } #ov-boiler { margin: -10px 0 16px; }" in css
    assert "#boiler-guide[hidden] { display: none; }" in css


# ------------------------------------------------------------------ Texte mit echten Antworten
@pytest.mark.usefixtures("berlin")
def test_status_line_and_box_when_writing(tmp_path):
    eng, bc, b, rd, now = scene(tmp_path, "schreibt")
    x, s = settings_x(eng, bc, now)
    r = js_run(tmp_path, f"""const B = {json.dumps(b)}, R = {json.dumps(rd)}, X = {json.dumps(x)}, S = {json.dumps(s)};
      BOILC = X; SET = S;
      renderBoilerLine("heat-boiler", B);
      out({{ line: $("heat-boiler").innerHTML, hidden: $("heat-boiler").hidden, box: boilerBoxHtml(B, R),
            sec: boilerSectionHtml(S, X), guideOpen: (boilerGuide(), $("boiler-guide").hidden) }});""")
    assert b["status_text"] == "Kessel: HeatPilot · Vorlauf 48 °C (Wz heizt auf 21,0 °C (+5 K)) · Boost +2 K"
    assert plain(r["line"]) == b["status_text"] and r["hidden"] is False and "dot-ok" in r["line"]
    assert "boilerResume" not in r["line"] and "Einstellungen →" not in r["line"]  # läuft: kein Knopf, kein Link
    facts = [plain(li) for li in re.findall(r"<li>(.*?)</li>", r["box"].split('id="boiler-facts">')[1].split("</ul>")[0])]
    assert facts[0] == "Kessel jetzt: Vorlauf-Soll 46 °C · Heizbetrieb an"
    assert facts[1] == ("Ziel: Vorlauf 48 °C , Heizbetrieb an – Wz heizt auf 21,0 °C (+5 K) · Boost +2 K Bedarf 46 °C · "
                        "Takt-Boost +2 K (wartet: Wz; Kessel taktet: 4 Starts/h, Läufe Ø 3 min)")
    assert facts[2].startswith("Abnahme: Mindestleistung unbekannt")  # Grund aus dem Backend
    assert facts[3] == "Entscheidung: Vorlauf-Soll: 46 → 48 °C · Heizbetrieb an passt"
    assert facts[4] == "Regler am Bus: nein (Kein Regler am Bus (EMS-ESP meldet kein Gerät thermostat))"
    lw, lc = b["last_write"]["flow"], b["last_check"]
    assert facts[5] == f"zuletzt geschrieben: Vorlauf-Soll {hm(lw['time'])}: 46 → 48 °C"
    assert facts[6] == f"Prüfung {hm(lc['time'])}: Vorlauf-Soll 46 °C übernommen"
    assert facts[7] == f"nächste Prüfung: Vorlauf-Soll {hm(b['check_due']['flow'])}"
    box = plain(r["box"])
    assert box.startswith(b["status_text"]) and "Wächter in Home Assistant: Wächter unbekannt (Automationen noch nicht gelesen)" in box
    assert "Heartbeat sensor.heatpilot_heartbeat noch nicht gesetzt" in box and "pausiert" not in box.lower()
    assert box.endswith(f"{rd['headline']} Einzelheiten unter „Bereit für die Steuerung?“")
    # Anleitung Wächter: zu, mit Knopf; die Schritte und Werte zum Abtippen aus dem Backend
    assert '<div id="boiler-guide" hidden>' in r["box"] and ">Anleitung Wächter</button>" in r["box"]
    g = b["watchdog"]["guide"]
    steps = [plain(x) for x in re.findall(r"<li>(.*?)</li>", r["box"].split('id="boiler-steps">')[1].split("</ol>")[0])]
    assert steps == g["steps"] and not any("yaml" in t.lower() for t in steps)
    vals = [plain(x) for x in re.findall(r"<tr>(.*?)</tr>", r["box"].split('id="boiler-vals">')[1].split("</table>")[0])]
    assert vals == ["Auslöser: Entität sensor.heatpilot_heartbeat", "„Für“ 01:00:00",
                    "Bedingung Attribut „kessel“ von sensor.heatpilot_heartbeat ist „heatpilot“", f"Vorlauf-Soll: Ziel {FLOW}",
                    "Vorlauf-Soll: Wert 45", f"Heizbetrieb: Ziel {HEAT}", f"Nachricht {g['values']['message']}",
                    "Name HeatPilot-Wächter"]
    assert r["guideOpen"] is False  # Knopf öffnet die Anleitung
    # Karte: Schalter, Entitäten mit Wert und Grenzen, Rückfall-Vorlauf, Warnhinweise
    sec = r["sec"]
    assert re.findall(r'value="(\w+)" (checked|)', sec) == [("aus", ""), ("trocken", ""), ("heatpilot", "checked")]
    assert [plain(x) for x in re.findall(r"<label>(.*?)</label>", sec)][:3] == ["Aus", "Trockenlauf", "HeatPilot setzt"]
    refs = [plain(x) for x in re.findall(r'<p class="em-ref" id="boiler-ref-\w+">(.*?)</p>', sec)]
    assert refs == [f"Vorlauf-Soll: {FLOW} (automatisch erkannt). Boiler Gewählte Vorlauftemperatur: jetzt 46 °C · in Home "
                    "Assistant 0–90 °C – ändern unter Entitäten",
                    f"Heizbetrieb: {HEAT} (automatisch erkannt). Boiler Heizbetrieb aktiviert: jetzt an – ändern unter Entitäten"]
    assert "emGo('kessel_hp')" in sec
    assert '<input id="f-boiler_fallback_flow" type="number" step="1" min="35" max="75" value="45">' in sec
    intro = plain(re.search(r'id="boiler-intro"[^>]*>(.*?)</p>', sec).group(1))
    assert intro == ("HeatPilot kann den Kessel selbst führen – Vorlauf-Soll live und Heizbetrieb an/aus, unabhängig vom Schalter "
                     "unter „Steuerung“ und vom Warmwasser. Geschrieben wird nur über Home Assistant, nur auf die zwei Entitäten "
                     "unten und nie, solange ein Regler am Bus ist.")
    assert '<details class="boiler-rules" id="boiler-rules"><summary>Nach welchen Regeln</summary>' in sec  # eingeklappt
    rules = plain(re.search(r'id="boiler-rules-text">(.*?)</p>', sec).group(1))
    for text in ("number.set_value auf die eine Entität des Vorlauf-Solls (ganze Grad ab 35 °C", "ab 1 K und höchstens einmal je 60 s",
                 "Ziel ist der Vorlauf, den der anspruchsvollste Raum braucht (gerechnet wie die Vorschau)",
                 "braucht kein Raum 35 °C, ist der Heizbetrieb aus",
                 "5 min Haltezeit", "60 s später prüft HeatPilot", "3 Fehlschläge in Folge oder 2 fremde Änderungen in 1 h: Pause",
                 "2-K-Schritten bis +10 K (Takt-Boost)", "EMS-ESP bleibt nur lesend, die Antipendelzeit des Kessels bleibt, wie sie ist."):
        assert text in rules, text
    warn = plain(re.search(r'<div class="warnbox" id="boiler-warn">(.*?)</div>', sec).group(1))
    assert warn.startswith("Regler am Bus: Ein Regler am EMS-Bus überschreibt alle 60 s, was am Kessel gesetzt wird")
    assert "Fällt HeatPilot aus (Add-on gestoppt, Home Assistant neu gestartet), bleibt der letzte Vorlauf stehen" in warn
    assert "Zurück auf „Aus“ oder „Trockenlauf“: HeatPilot schreibt einmal den Rückfall-Vorlauf (jetzt 45 °C) und Heizbetrieb an" in warn


@pytest.mark.usefixtures("berlin")
def test_blocked_dry_paused_back_and_off(tmp_path):
    out = {}
    for kind in ("gesperrt", "trocken", "pausiert", "zurueck", "aus"):
        eng, bc, b, rd, now = scene(tmp_path / kind, kind)
        out[kind] = (b, rd)
    data = json.dumps({k: {"b": v[0], "rd": v[1]} for k, v in out.items()})
    r = js_run(tmp_path, f"""const D = {data};
      out(Object.fromEntries(Object.entries(D).map(([k, v]) => [k, {{ line: boilerLine(v.b), box: boilerBoxHtml(v.b, v.rd),
        ready: boilerReadyHtml(v.rd, true), closed: boilerReadyHtml(v.rd, false) }}])));""")
    # gesperrt: Statuszeile mit Sperre (nicht doppelt als Hinweis), Link zur Karte; Block „nicht bereit“
    g, gb = r["gesperrt"], out["gesperrt"][0]
    assert gb["status_text"].startswith("Kessel: gesperrt – EMS-ESP meldet einen Regler (Gerät thermostat) – Regler am Bus")
    line = plain(g["line"])
    assert line.startswith(gb["status_text"]) and line.count("Regler am Bus – er überschreibt alle 60 s") == 1
    assert line.endswith("Einstellungen → Kessel über HeatPilot") and "dot-warn" in g["line"]
    gbox = plain(g["box"])  # die Sperre steht einmal im Status, nicht noch in Entscheidung und Regler-Zeile
    assert "Regler am Bus: ja – sperrt Wächter in Home Assistant" in gbox
    assert "Entscheidung: Vorlauf-Soll: gesperrt (siehe oben) · Heizbetrieb an passt" in gbox
    assert gbox.count("Erst Trockenlauf prüfen, dann Regler abbauen") == 2  # Status und „Bereit für die Steuerung?“
    assert "⚠ Wächter nicht erkannt" not in gbox and "Wächter in Home Assistant: Wächter nicht erkannt" in gbox
    ready_text = plain(g["ready"])
    assert ready_text.startswith("Kessel über HeatPilot Nicht bereit – EMS-ESP meldet einen Regler (Gerät thermostat)")
    rows = [plain(x) for x in re.findall(r"<tr>(.*?)</tr>", g["ready"])]
    assert rows[0] == "Schalter HeatPilot setzt"
    assert rows[1] == f"Vorlauf-Soll {FLOW} erkannt · 30 °C (0–90 °C)" and rows[2] == f"Heizbetrieb {HEAT} erkannt · an"
    assert rows[3] == "schreibbar laut EMS-ESP selflowtemp unbekannt · heatingactivated unbekannt"
    assert rows[4].startswith("Regler am Bus ja – EMS-ESP meldet einen Regler") and rows[5] == "Heizen abschalten aus"
    assert rows[7] == "Mindestleistung unbekannt – Abnahme gilt nicht" and rows[8] == "Rückfall-Vorlauf 45 °C"
    assert rows[9:] == ["letzte Prüfung noch keine", "pausiert nein", "fremder Schreiber in den letzten 24 h keiner"]
    assert 'id="ready-boiler-hp-details" open' in g["ready"] and 'id="ready-boiler-hp-details" open' not in g["closed"]
    assert plain(g["ready"]).endswith("Schalter und „Anleitung Wächter“ unter Einstellungen → Kessel über HeatPilot, "
                                      "Entitäten unter Einstellungen → Entitäten → Kessel über HeatPilot.")
    # Trockenlauf: „würde …“, ohne Knopf und Link
    t, tb = r["trocken"], out["trocken"][0]
    assert tb["status_text"].startswith("Kessel: Trockenlauf · würde Vorlauf 46 °C setzen (Wz")
    assert plain(t["line"]).startswith(tb["status_text"]) and "<a " not in t["line"]
    dry = tb["dry_last"]["flow"]
    assert f"Trockenlauf zuletzt: würde schreiben Vorlauf-Soll 46 °C ({hm(dry['time'])})" in plain(t["box"])
    assert "würde setzen: Vorlauf 46 °C , Heizbetrieb an" in plain(t["box"]) and "zuletzt geschrieben" not in plain(t["box"])
    # pausiert: seit …, Knopf „wieder einschalten“, der Hinweis zur Pause nicht doppelt
    p, pb = r["pausiert"], out["pausiert"][0]
    assert pb["paused"] and pb["status_text"] == "Kessel: pausiert – 3 Fehlschläge in Folge"
    assert plain(p["line"]).startswith(f"Kessel: pausiert – 3 Fehlschläge in Folge · seit {hm(pb['paused_since'])} ")
    assert "Kessel über HeatPilot pausiert (" not in p["line"] and 'onclick="boilerResume()">wieder einschalten' in p["line"]
    assert f"Pausiert seit {hm(pb['paused_since'])} (3 Fehlschläge in Folge)" in plain(p["box"]) and 'id="boiler-resume"' in p["box"]
    assert "Entscheidung:" not in plain(p["box"])  # pausiert: keine Entscheidung
    assert "pausiert ja – 3 Fehlschläge in Folge · seit " in plain(p["ready"])
    # zurückgeschaltet: bei „Aus“ steht die Zeile, solange der Hinweis gilt
    z, zb = r["zurueck"], out["zurueck"][0]
    assert zb["mode"] == "aus" and zb["status_text"] == "Kessel: aus – Regler bzw. Kessel regeln selbst"
    assert plain(z["line"]) == (f"{zb['status_text']} ⚠ HeatPilot schreibt den Kessel nicht mehr (Vorlauf-Soll 45 °C und "
                                "Heizbetrieb an geschrieben) – Regler wieder anschließen bzw. den Kessel selbst stellen "
                                "Einstellungen → Kessel über HeatPilot")
    assert "zurückgeschaltet " in plain(z["box"]) and "Vorlauf-Soll 45 °C und Heizbetrieb an geschrieben" in plain(z["box"])
    # aus: keine Zeile, die Box nennt nur den Stand
    a, ab = r["aus"], out["aus"][0]
    assert a["line"] == "" and plain(a["box"]).startswith("Kessel: aus – Regler bzw. Kessel regeln selbst Kessel jetzt:")
    assert "Ziel:" not in plain(a["box"]) and "Entscheidung:" not in plain(a["box"])
    assert plain(a["ready"]).startswith("Kessel über HeatPilot ") and "Schalter Aus – schreibt nicht" in plain(a["ready"])


def test_mode_hint_confirm_and_old_answers(tmp_path):
    st = {"fallback": {"value": 45}, "regulator": {"present": False}}
    r = js_run(tmp_path, f"""const ST = {json.dumps(st)}, E = {{ flow: "{FLOW}", heating: "{HEAT}" }};
      const t = (m, saved, e = E, s = ST) => boilerModeText(m, saved, e, s);
      SET = {{ boiler_control: "aus", flow_set_entity: "", heating_switch_entity: "" }};
      BOILC = {{ suggested: {{ flow: "{FLOW}", heating: null }}, status: ST }}; RADIO = "heatpilot"; boilerModeHint();
      const noHeat = $("boiler-mode-hint").innerHTML;
      EM_BY = {{ heating_switch_entity: {{}} }}; EM_VAL = {{ heating_switch_entity: "switch.neu" }}; boilerModeHint();
      const picked = $("boiler-mode-hint").innerHTML;
      const asks = [boilerConfirm({{ boiler_control: "heatpilot" }}), boilerConfirm({{ boiler_control: "trocken" }})];
      SET.boiler_control = "heatpilot"; asks.push(boilerConfirm({{ boiler_control: "heatpilot" }}));
      out({{ aus: t("aus", "aus"), trocken: t("trocken", "aus"), hp: t("heatpilot", "aus"), back: t("aus", "heatpilot"),
            backReg: t("trocken", "heatpilot", E, {{ fallback: {{ value: 45 }}, regulator: {{ present: true }} }}),
            backNone: t("aus", "heatpilot", E, {{ fallback: {{ value: null }} }}),
            reg: t("heatpilot", "aus", E, {{ regulator: {{ present: true }} }}), unknown: t("heatpilot", "aus", E, {{ regulator: {{ present: null }} }}),
            noHeat, picked, asks, asked: ASKED,
            none: [boilerLine(undefined), boilerLine(null), boilerReadyHtml(undefined), boilerBoxHtml(null)],
            start: boilerLine({{ mode: "heatpilot", modes: ["aus", "trocken", "heatpilot"], active: false, writes: true, paused: false,
                                warnings: [], log: [], status_text: "Kessel: HeatPilot – noch nicht gerechnet" }}),
            startBox: boilerBoxHtml({{ mode: "aus", active: false, writes: false, paused: false, warnings: [], log: [],
                                     status_text: "Kessel: aus – Regler bzw. Kessel regeln selbst" }}),
            old: boilerSectionHtml({{ boiler_control: "kaputt" }}, null) }});""")
    assert r["aus"] == "Wie bisher: Regler bzw. Kessel regeln selbst – HeatPilot rechnet den Kessel nicht und schreibt nichts."
    assert r["trocken"].startswith("HeatPilot rechnet alle 30 s nach den Regeln oben und schreibt ins Protokoll (Reiter Heizen)")
    assert "Geht auch mit Regler am Bus" in r["trocken"]
    assert plain(r["hp"]) == f"HeatPilot schreibt das Vorlauf-Soll auf {FLOW} und schaltet den Heizbetrieb über {HEAT} – nur ohne Regler am Bus."
    assert r["back"].endswith("Beim Zurückschalten schreibt HeatPilot einmal 45 °C Vorlauf und Heizbetrieb an – danach den Regler "
                              "wieder anschließen bzw. den Kessel selbst stellen.")
    assert "einmal 45 °C Vorlauf und Heizbetrieb an (mit Regler am Bus nichts)" in r["backReg"]
    assert "schreibt HeatPilot nichts zurück – der Rückfall-Vorlauf läge unter 35 °C" in r["backNone"]
    assert "Zurzeit gesperrt: EMS-ESP meldet einen Regler." in plain(r["reg"])
    assert "Zurzeit gesperrt: ob ein Regler am Bus ist, ist unbekannt." in plain(r["unknown"])
    assert plain(r["noHeat"]) == "Keine Entität für den Heizbetrieb – unter Entitäten wählen, sonst schreibt HeatPilot nichts."
    assert "über switch.neu" in plain(r["picked"])  # die Wahl unter „Entitäten“ zählt sofort
    assert r["asks"] == [True, True, True] and len(r["asked"]) == 1  # nur beim Umschalten auf „HeatPilot setzt“
    assert r["asked"][0].startswith("Ab jetzt schreibt HeatPilot Vorlauf-Soll und Heizbetrieb des Kessels – aber nur, solange "
                                    "kein Regler am Bus ist.") and "Wächter" in r["asked"][0]
    assert r["none"] == ["", "", "", '<span class="muted">Status nicht verfügbar</span>']
    assert plain(r["start"]) == "Kessel: HeatPilot – noch nicht gerechnet"  # Start ohne Instanz: nur der Schalter
    assert plain(r["startBox"]) == "Kessel: aus – Regler bzw. Kessel regeln selbst"
    old = r["old"]
    assert re.findall(r'value="(\w+)" (checked|)', old) == [("aus", "checked"), ("trocken", ""), ("heatpilot", "")]
    assert 'value="45"' in old and "keine number-Entität mit °C erkannt – bitte wählen." in plain(old)
    assert "Status nicht verfügbar" in old


def test_decision_with_hold_in_browser_time(tmp_path):
    """Haltezeit und Abstand je 60 s: die Uhrzeit aus ``decision_until`` in der Zeitzone des Browsers."""
    until = {"flow": "2026-10-12T12:03:00+02:00", "heating": "2026-10-12T12:07:00+02:00"}
    r = js_run(tmp_path, f"""const U = {json.dumps(until)};
      out([boilerDecision({{ decision: {{ flow: "höchstens ein Vorlauf-Schreiben je 60 s", heating: "Haltezeit bis 10:07" }},
                            decision_until: U }}),
           boilerDecision({{ decision: {{ flow: "Warmwasser lädt – Vorlauf-Soll bleibt, bis die Ladung vorbei ist",
                                         heating: "Heizbetrieb an → aus" }}, decision_until: {{}} }}),
           boilerDecision({{ decision: {{}} }})]);""")
    assert [plain(x) for x in r] == [
        "Entscheidung: Vorlauf-Soll: nächstes Schreiben frühestens 12:03 · Heizbetrieb: Haltezeit bis 12:07",
        "Entscheidung: Vorlauf-Soll: Warmwasser lädt – Vorlauf-Soll bleibt, bis die Ladung vorbei ist · Heizbetrieb an → aus", ""]


@pytest.mark.usefixtures("berlin")
def test_heat_log_rows_for_the_boiler(tmp_path):
    eng, bc, b, rd, now = scene(tmp_path, "schreibt")
    climate_off = {"ts": now, "room": FLOW, "action": "heating_off", "value": 0, "reason": "kein Raum braucht Wärme",
                   "time": b["log"][0]["time"], "kind": "boiler", "name": "Kessel"}
    dry = {**climate_off, "action": "boiler_dry", "value": 0, "reason": "Trockenlauf: würde Heizbetrieb aus schreiben (x)"}
    fail = {**climate_off, "action": "boiler_check_failed", "value": 46, "reason": "Vorlauf-Soll 46 °C nicht übernommen"}
    rows = [climate_off, dry, fail, *b["log"]]
    r = js_run(tmp_path, f"""const L = {json.dumps(rows)};
      renderHeatLog({{ log: L }}, null);
      out({{ html: $("heat-log").innerHTML, hidden: $("heat-log-box").hidden }});""")
    assert r["hidden"] is False
    got = [plain(x) for x in re.findall(r'<tr class="log-boiler">(.*?)</tr>', r["html"])]
    t = lambda iso: datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Berlin")).strftime("%a %H:%M")  # noqa: E731
    assert len(got) == 5 and all(" Kessel " in x for x in got)
    assert got[0].endswith("Kessel Heizbetrieb aus aus kein Raum braucht Wärme")
    assert got[1].endswith("Kessel würde schreiben aus Trockenlauf: würde Heizbetrieb aus schreiben (x)")
    assert got[2].endswith("Kessel nicht übernommen 46 °C Vorlauf-Soll 46 °C nicht übernommen")
    assert got[3].endswith("Kessel Vorlauf-Soll gesetzt 48 °C Wz heizt auf 21,0 °C (+5 K) · Boost +2 K (vorher 46 °C)")
    assert got[4].endswith("Kessel Vorlauf-Soll gesetzt 46 °C Wz heizt auf 21,0 °C (+5 K) (vorher 30 °C)")
    assert t(b["log"][0]["time"])[-5:] in got[3]
    assert r["html"].count('<span class="boiler-ico">') == 5


@pytest.mark.usefixtures("berlin")
def test_preview_says_writes_for_the_boiler(tmp_path):
    eng, ha, bc = house(tmp_path)
    step(bc, T0)
    p = as_json(pv.preview(eng, T0 + 30))
    assert p["flow_writes"] == "schreibt" and p["note"] == pv.NOTE_BOILER and p["dry_run"] is False
    i = next(k for k, h in enumerate(p["hours"]) if any(w["what"] != "dhw" for w in h["writes"]))
    r = js_run(tmp_path, f"""const P = {json.dumps(p)}, D = {{ ...P, flow_writes: "würde schreiben", note: PV_NOTE }};
      const B = {{ ...D, boiler_mode: "heatpilot", boiler_blocked: "Regler am Bus", boiler_paused: false }};
      out({{ note: pvNote(P), sum: pvSummary(P), tip: pvTip(P, {i}), table: pvTable(P),
            quiet: pvSummary({{ ...P, writes_24h: {{ flow: 0, heating: 0, dhw: 0 }} }}),
            both: pvSummary({{ ...P, dhw_writes: "schreibt" }}), bothNote: pvNote({{ ...P, dhw_writes: "schreibt" }}),
            dNote: pvNote(D), dSum: pvSummary(D), dTip: pvTip(D, {i}),
            blocked: pvNote(B), paused: pvNote({{ ...B, boiler_blocked: null, boiler_paused: true }}),
            dry: pvNote({{ ...D, boiler_mode: "trocken" }}),
            empty: pvTip({{ ...P, hours: P.hours.map((h) => ({{ ...h, writes: [] }})) }}, 0) }});""")
    w = p["writes_24h"]
    n = w["flow"] + w["heating"]
    assert plain(r["note"]).startswith(f"{pv.NOTE_BOILER}. Schreibvorgänge an Vorlauf-Soll und Heizbetrieb heißen hier „schreibt“ – "
                                       "nach den Regeln unter Einstellungen → Kessel über HeatPilot; das Warmwasser-Soll ist hier nur Vorschau.")
    parts = ", ".join(f"{k} {w[key]}×" for k, key in (("Vorlauf", "flow"), ("Heizbetrieb", "heating")) if w[key])
    dhw = f" und würde das Warmwasser-Soll {w['dhw']}× schreiben" if w["dhw"] else " und würde das Warmwasser-Soll nicht schreiben"
    assert plain(r["sum"]).startswith(f"In den nächsten 24 h schreibt HeatPilot {n}× ({parts}){dhw} – nur bei Änderung")
    assert plain(r["quiet"]).startswith("In den nächsten 24 h schreibt HeatPilot Vorlauf und Heizbetrieb nicht neu und würde "
                                        "das Warmwasser-Soll nicht schreiben – ")
    assert "schreibt HeatPilot " in plain(r["both"]) and "würde" not in plain(r["both"]).split(" – ")[0]
    assert "das Warmwasser-Soll setzt HeatPilot ebenfalls selbst" in plain(r["bothNote"])
    tip = plain(r["tip"])
    assert "schreibt: " in tip and not re.search(r"würde schreiben:[^:]*Vorlauf", tip)
    assert "schreibt: " in plain(r["table"]) and plain(r["empty"]).endswith("nichts zu schreiben")
    # „würde schreiben“ (Trockenlauf bzw. älter): wie 0.38, mit Hinweis auf Sperre, Pause, Trockenlauf
    assert r["dNote"] == f"<b>{pv.NOTE}.</b> Die Vorschau ist ein Vorschlag zum Vergleichen – wann und ob umgestellt wird, entscheidest du."
    assert plain(r["dSum"]).startswith(f"In den nächsten 24 h würde HeatPilot {n + w['dhw']}× schreiben")
    assert "würde schreiben: " in plain(r["dTip"]) and "schreibt: " not in plain(r["dTip"])
    assert "Kessel über HeatPilot ist gesperrt (Regler am Bus) – Vorlauf und Heizbetrieb sind hier nur Vorschau." in r["blocked"]
    assert "Kessel über HeatPilot ist pausiert – Vorlauf und Heizbetrieb sind hier nur Vorschau." in r["paused"]
    assert "Kessel über HeatPilot läuft im Trockenlauf – was es live schreiben würde, steht im Protokoll (Reiter Heizen)." in r["dry"]


def test_notify_recommends_the_phone_app(tmp_path):
    avail = notify.available({"notify.handy": {"state": "unknown", "attributes": {"friendly_name": "Handy"}}},
                             {"notify": {"mobile_app_handy": {}, "familie": {}, "send_message": {}}}, ["notify.handy"])
    n = as_json({"targets": ["notify.handy"], "problems": [], "last": None, "available": avail, "services_error": None})
    r = js_run(tmp_path, f"""const N = {json.dumps(n)};
      out({{ html: notifyHtml(N), plainList: notifyHtml({{ ...N, available: N.available.map((a) => ({{ ...a, recommended: false }})) }}) }});""")
    rows = [[plain(c) for c in re.findall(r"<td>(.*?)</td>", x)] for x in re.findall(r"<tr><td><input.*?</tr>", r["html"])]
    assert rows[0] == ["", "mobile app handy empfohlen notify.mobile_app_handy", "Dienst Handy-App"]  # oben
    assert rows[1:] == [["", "Handy notify.handy", "Entität über notify.send_message"], ["", "familie notify.familie", "Dienst"]]
    assert '<span class="badge n-rec">empfohlen</span>' in r["html"]
    assert plain(r["html"]).lower().count("empfohlen") == 2 and "Empfohlen: der Dienst der Handy-App ( notify.mobile_app_… ) – er kommt am zuverlässigsten an." in plain(r["html"])
    assert "Kessel (Vorlauf-Soll bzw. Heizbetrieb nicht übernommen, pausiert, anderer Schreiber, gesperrt)" in plain(r["html"])
    assert "empfohlen" not in r["plainList"].lower()  # ältere Antwort ohne recommended: wie 0.38


def test_entity_page_offers_only_fitting_entities(tmp_path):
    from test_ui_v391 import run_em

    eng, ha, bc = house(tmp_path)
    eng.states["number.boiler_hc1_heatingtemp"] = {"state": "60", "attributes": {"unit_of_measurement": "°C",
                                                                                 "friendly_name": "Heizkreis Heiztemperatur"}}
    eng.states["switch.boiler_heatingoff"] = {"state": "off", "attributes": {"friendly_name": "Heizen abschalten"}}
    app = fake_app(eng, ha)

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            return (await (await client.get("/api/entities")).json(), await (await client.get("/api/entity_map")).json())
        finally:
            await client.close()

    ents, emap = asyncio.run(go())
    group = next(g for g in emap["groups"] if g["key"] == "kessel_hp")
    assert group["title"] == "Kessel über HeatPilot" and [e["key"] for e in group["entries"]] == ["flow_set_entity",
                                                                                                  "heating_switch_entity"]
    r = run_em(tmp_path, {"ent": ents, "map": emap}, """
      const ids = (k) => emOptions(EM_BY[k], "").list.map((x) => x.id);
      const all = (k, q) => emOptions(EM_BY[k], q).list.map((x) => [x.id, x.fit]);
      out({ flow: ids("flow_set_entity"), heat: ids("heating_switch_entity"), heatAll: all("heating_switch_entity", "heizen"),
            row: emRow(EM_BY.flow_set_entity), group: emGroup(EMAP.groups.find((g) => g.key === "kessel_hp")) });""")
    assert r["flow"] == [FLOW] and r["heat"] == [HEAT]  # nie Heiztemperatur, nie „Heizen abschalten“
    assert ["switch.boiler_heatingoff", False] in r["heatAll"]  # mit Suchbegriff darunter, als unpassend markiert
    assert "automatisch gefunden" in plain(r["row"]) and FLOW in plain(r["row"])
    assert 'id="em-g-kessel_hp"' in r["group"]


def test_docs_and_changelog_name_the_places():
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for text in ("Einstellungen → Kessel über HeatPilot", "**HeatPilot setzt**", "„Anleitung Wächter“",
                 "„Vorschau – Kessel über HeatPilot schreibt“", "**Rückfall-Vorlauf**", "„wieder einschalten“"):
        assert text in docs, text
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## ")[1]
    assert head.startswith("0.41.0") and "**Oberfläche:** neue Karte **Einstellungen → Kessel über HeatPilot**" in head
    assert "tests/test_ui_v41.py" in head
