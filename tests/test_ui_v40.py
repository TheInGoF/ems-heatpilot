"""0.40: Oberfläche zu „Ventile führen“ – statische Prüfung und Texte in Node.

Wie tests/test_ui_v39.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird der Einbau (Schalter unter
Einstellungen → Steuerung samt Speichern, Block „Ventile führen“ in „Bereit für die Steuerung?“ vor dem Warmwasser,
Zeile „Ventil geführt: …“ im Raum-Dialog, „schreibt“ in Übersicht → Vorschau → Ventile), dass die neuen Teile nichts
schreiben (kein neuer Aufruf an die API) und ältere Antworten ohne die Felder still weglassen, und die Texte – in Node
mit Antworten des echten Backends (Regelschleife mit Ventilführung an einem künstlichen Haus aus
tests/test_valve_control40.py). Bilder in 390/1100 px, hell/dunkel prüft das Playwright-Skript, das nicht Teil der
Suite ist.
"""

import json
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from heatpilot import readiness
from heatpilot import valve_control as vc
from heatpilot.config import Config
from test_ui_v361 import NODE, between, html, plain, script, source
from test_ui_v39 import as_json, berlin, run_js  # noqa: F401 – berlin ist eine Fixture
from test_valve_control40 import BAD, KIND, T0, WZ, burner, room, setup, throttle_ready, tick

NAMES = ("fmt", "esc", "clock", "timeOf", "shortName", "WEEKDAYS", "wday", "dateOf", "field", "READY", "ROOM_READY_CSS",
         "readyDot", "VD_NOTE", "VD_RULE", "VD_C", "renderValves", "vdFacts", "vdRules", "vdLiveRules", "vdActive",
         "vdDays", "vdDayName", "vdTable", "vdExamples", "vdEffect", "valveReadyHtml", "valveGuideLine", "VC_TEXT",
         "valveSwitchHtml")
# DOM-Ersatz: $() liefert je id ein Objekt; #vd hat eine Überschrift (h3) – den Balkengrafen prüft Playwright
STUBS = """
const ELS = {}, HEAD = { textContent: "Ventile (Trockenlauf)" };
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "",
  querySelector: (q) => (id === "vd" && q === "h3" ? HEAD : null) });
let CHART = null;
function vdDayChart(box, days) { CHART = days; }
"""


def js_run(tmp_path, body: str):
    """Wie test_ui_v39.run_js, mit den Funktionen und dem DOM-Ersatz von hier."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    code = STUBS + "\n".join(source(script(), n) for n in NAMES) + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe40.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={"TZ": "Europe/Berlin", "PATH": "/usr/bin:/bin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def hm(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Berlin")).strftime("%H:%M")


def live_house(tmp_path):
    """Echte Antworten: Schalter an, Wz gerade früh gedrosselt (geschrieben), Bad im Trockenlauf mitgezählt."""
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    eng.store.add_valve_dry({"ts": T0 + 30, "room": BAD, "rule": "gemeinsam", "action": "anheben", "setpoint": 21.5,
                             "base": 21.0, "temp": 20.9, "valve": 0.0, "reliable": 0, "managed": 1, "written": 0,
                             "reason": "nur Wz fordert an, Kessel taktet (5 Starts/h) – Bad würde in 60 min ohnehin heizen"})
    return eng, loop


# ------------------------------------------------------------------ Einbau
def test_switch_ready_dialog_and_preview_are_wired():
    js = script()
    card = between(js, "function controlCard(s) {", "// Ventile führen (0.40)")
    assert card.index("Es gibt keinen Zwischenmodus") < card.index("${valveSwitchHtml(s)}") < card.index("Nachtabsenkung – Wochenplan")
    sw = between(js, "function valveSwitchHtml(s) {", "// Leistungssensoren")
    assert '<input type="checkbox" id="f-valve_control" ${s.valve_control ? "checked" : ""} onchange="modeHint()">' in sw
    save = between(js, "out.night_plan_auto = $(\"f-night_plan_auto\").checked;", "out.rooms = [];")
    assert 'if ($("f-valve_control")) out.valve_control = $("f-valve_control").checked;' in save
    hint = between(js, "function modeHint() {", "// Bereit für die Steuerung?")
    assert "An, wirkt aber erst mit „HeatPilot steuert“ – bis dahin nur Trockenlauf." in hint
    box = between(js, "function renderReadyBox(r, err = \"\") {", "function readyBoxHtml(")
    assert box.index("valveReadyHtml(r.valves, READY_SHOW.valves)") < box.index("dhwReadyHtml(r.dhw, READY_SHOW.dhw)")
    assert "if (r && r.valves)" in box  # ältere Version ohne „valves“: kein Block
    assert 'if (c.valve) h += "<br>" + valveGuideLine(c.valve);' in between(js, "function ctlInfo(r) {", "function valveGuideLine(")
    rv = between(js, "function renderValves(v, now = new Date()) {", "// Kennzahlen")
    assert "vdExamples(v.examples || [], v.live)" in rv and "(v.live ? vdLiveRules(v.rules || {}) : \"\")" in rv
    # nur Anzeige: die neuen Teile rufen keine API auf und schalten nichts
    for name in ("valveSwitchHtml", "valveReadyHtml", "valveGuideLine", "vdLiveRules", "vdActive", "vdTable", "vdExamples"):
        body = source(js, name)
        for word in ("api/", "getJSON", "heatPost", "fetch("):
            assert word not in body, (name, word)


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    f = tmp_path / "inline40.js"
    f.write_text(re.findall(r"<script>(.*?)</script>", html(), re.S)[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_new_functions_small():
    for name in ("valveSwitchHtml", "valveReadyHtml", "valveGuideLine", "vdLiveRules", "vdActive", "vdTable",
                 "vdExamples", "vdEffect", "vdFacts", "renderValves"):
        assert source(script(), name).count("\n") <= 60, name


# ------------------------------------------------------------------ Texte mit echten Antworten
@pytest.mark.usefixtures("berlin")
def test_ready_block_and_dialog_line(tmp_path):
    rooms = [room("Wz", WZ, managed=True), room("Bad", BAD, managed=True), room("Kind", KIND, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    from heatpilot import valve_model as vm
    eng.valves[KIND] = vm.ValveModel()
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0), KIND: (20.0, 0.0)})
    rd = as_json(readiness.assess(eng, T0 + 60)["valves"])
    v = as_json(loop.rooms[WZ]["valve"])
    join = {**v, "rule": "gemeinsam", "rule_text": "gemeinsam anfordern", "setpoint": 21.5, "back_latest": True}
    r = js_run(tmp_path, f"""const R = {json.dumps(rd)}, V = {json.dumps(v)}, J = {json.dumps(join)};
      out({{ box: valveReadyHtml(R, true), closed: valveReadyHtml(R, false), none: valveReadyHtml(undefined),
            line: valveGuideLine(V), join: valveGuideLine(J), nothing: valveGuideLine(null),
            sw: valveSwitchHtml({{ valve_control: true }}), off: valveSwitchHtml({{ valve_control: false }}) }});""")
    box = plain(r["box"])
    assert box.startswith("Ventile führen Läuft – führt 2 Räume (Wz und Bad); Kind lernt noch. Einzelheiten "
                          "(2 bereit, 1 lernen noch · Schalter an) Läuft: Wz – früh drosseln, Soll 20,5 statt 21 °C seit "
                          f"{hm(rd['running'][0]['since'])} Höchstens ein Schritt vom Wunschsoll")
    assert "Wz bereit Raum- und Ventilmodell belastbar, Schritt 0,5 K" in box and "Kind lernt Ventilmodell lernt noch (" in box
    assert 'id="ready-valves-details" open' in r["box"] and 'id="ready-valves-details" open' not in r["closed"]
    assert box.endswith("was HeatPilot dabei schreibt bzw. schreiben würde, steht in der Übersicht → Vorschau → Ventile.")
    assert r["none"] == "" and r["nothing"] == ""
    assert plain(r["line"]) == f"Ventil geführt: 20,5 statt 21 °C – früh drosseln, zurück ≈ {hm(v['back_at'])}"
    assert plain(r["join"]) == f"Ventil geführt: 21,5 statt 21 °C – gemeinsam anfordern, zurück spätestens {hm(v['back_at'])}"
    assert 'id="f-valve_control" checked' in r["sw"] and 'id="f-valve_control" ' in r["off"] and "checked" not in r["off"]
    assert plain(r["sw"]).startswith("Ventile führen HeatPilot führt den Regler der Thermostate über das Soll – höchstens "
                                     "einen Schritt des Thermostats (Tado 0,5 K) vom Wunschsoll")
    assert "Früh drosseln:" in r["sw"] and "Gemeinsam anfordern:" in r["sw"] and "nie mehr als 0,3 K unter dem Soll" in r["sw"]


@pytest.mark.usefixtures("berlin")
def test_preview_says_writes_with_switch_on(tmp_path):
    eng, loop = live_house(tmp_path)
    s = as_json(vc.summary(eng, T0 + 120))
    eng.cfg.valve_control = False
    dry = as_json(vc.summary(eng, T0 + 120))
    r = js_run(tmp_path, f"""const S = {json.dumps(s)}, D = {json.dumps(dry)};
      renderValves(S, new Date({(T0 + 120) * 1000}));
      const get = (id) => ({{ html: $(id).innerHTML, text: $(id).textContent, hidden: $(id).hidden }});
      const live = {{ head: HEAD.textContent, ...Object.fromEntries(["vd-note", "vd-text", "vd-facts", "vd-active", "vd-ex",
        "vd-rooms", "vd-rules"].map((id) => [id, get(id)])) }};
      renderValves(D, new Date({(T0 + 120) * 1000}));
      out({{ live, dry: {{ head: HEAD.textContent, note: $("vd-note").innerHTML, ex: $("vd-ex").innerHTML, rooms: $("vd-rooms").innerHTML,
        rules: $("vd-rules").innerHTML, active: $("vd-active").innerHTML }},
        eff: vdEffect({{ ...S.examples.find((x) => x.written), act_over_k: 0.08 }}) }});""")
    lv = r["live"]
    assert lv["head"] == "Ventile – HeatPilot schreibt" and r["dry"]["head"] == "Ventile (Trockenlauf)"
    assert lv["vd-note"]["html"].startswith("<b>Ventile führen ist an – HeatPilot schreibt an die Thermostate der Räume, die "
                                            "es führt; die übrigen nur im Trockenlauf.</b> Gezählt wird, was HeatPilot mit den "
                                            "Thermostaten als Stellglied tut („geschrieben“) bzw. in den übrigen Räumen tun würde")
    assert lv["vd-text"]["text"] == s["text"] and s["text"].startswith("In 7 Tagen hat HeatPilot 1× früh gedrosselt")
    facts = [plain(x) for x in re.findall(r"<li>(.*?)</li>", lv["vd-facts"]["html"])]
    assert facts[2] == ("Schreibvorgänge: 2 – früh drosseln 1, gemeinsam 1, jeweils mit dem Zurücksetzen; davon 1 "
                        "geschrieben, 1 nur im Trockenlauf. 2 der Eingriffe in Räumen mit „macht mit“.")
    since = hm(s["active"][0]["since"])
    assert plain(lv["vd-active"]["html"]) == f"Läuft gerade (geschrieben): Wz – früh drosseln, Soll 21 → 20,5 °C seit {since}"
    ex = lv["vd-ex"]["html"]
    assert ex.count('<span class="badge vd-w">geschrieben</span>') == 1 and ex.count(">würde schreiben</span>") == 1
    assert "vorhergesagt mit Eingriff ≈ " in plain(ex)
    assert '<th>Schreib&shy;vorgänge <span class="muted">(davon ge&shy;schrie&shy;ben)</span></th>' in lv["vd-rooms"]["html"]
    assert re.search(r'<td>Wz</td><td>1</td><td>0</td><td>1 <span class="muted">\(1\)</span></td>', lv["vd-rooms"]["html"])
    assert "<b>Ventile führen:</b> In Räumen mit „macht mit“ und belastbarem Raum- und Ventilmodell schreibt HeatPilot das " \
           "wirklich – höchstens einen Schritt vom Wunschsoll (Schritt des Thermostats, höchstens 0,5 K); früh drosseln " \
           "nur bei Komfort und Vorheizen und höchstens 0,3 K unter dem Soll" in lv["vd-rules"]["html"]
    assert r["eff"].endswith("mit Eingriff gemessen: bis 0,1 K über Soll")
    # Schalter aus: wie 0.39 – keine Kennzeichen, keine Spalte, keine Regel zum Schreiben
    d = r["dry"]
    assert d["note"].startswith("<b>Trockenlauf – HeatPilot schreibt nichts an die Thermostate.</b>")
    assert "würde schreiben" not in d["ex"] and "vd-w" in d["ex"]  # geschriebener Eintrag bleibt gekennzeichnet
    assert "Ventile führen:" not in d["rules"] and "(davon" in d["rooms"]
    assert "(geschrieben)" in d["active"]  # der laufende Schritt steht noch am Thermostat, bis er zurückgeht


def test_old_answers_without_fields(tmp_path):
    r = js_run(tmp_path, """renderValves({ window_days: 7, writes: 0, text: "x", rules: {}, active: [], examples: [] });
      out({ head: HEAD.textContent, note: $("vd-note").innerHTML, active: vdActive([]),
            table: vdTable("Raum", [["Wz", { drosseln: 1, gemeinsam: 0, writes: 2 }]]),
            ex: vdExamples([{ t: "2026-10-12T07:00:00+02:00", name: "Wz", rule: "drosseln", base: 21, setpoint: 20.5,
                             reason: "r", temp: 20.7, valve: 90, reliable: true, managed: true }]) });""")
    assert r["head"] == "Ventile (Trockenlauf)" and r["note"].startswith("<b>Trockenlauf – HeatPilot schreibt nichts")
    assert r["active"] == "" and "davon" not in r["table"] and "badge vd-w" not in r["ex"] and "würde schreiben" not in r["ex"]


def test_settings_answer_has_switch(tmp_path):
    assert Config().to_raw()["valve_control"] is False
    eng, _, loop = setup(tmp_path, cfg=Config(control_mode="heatpilot"))
    assert readiness.assess(eng, T0)["valves"]["switch"] is False
    burner(eng, T0)  # nur Anzeige: auch bei taktendem Kessel kein Schritt ohne Schalter
    assert tick(loop, T0, {WZ: (20.4, 80.0), BAD: (20.9, 0.0)}) == []


def test_docs_name_the_places():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    docs = re.sub(r"\s+", " ", (root / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8"))
    for text in ("### Ventile führen", "Einstellungen → Steuerung", "„Bereit für die Steuerung?“",
                 "Ventil geführt: 20,5 statt 21 °C – früh drosseln, zurück ≈ 15:40", "„Ventile – HeatPilot schreibt“"):
        assert text in docs, text
    log = (root / "CHANGELOG.md").read_text(encoding="utf-8").split("## 0.39.1")[0]
    assert log.startswith("# Changelog\n\n## 0.40.0") and "Ventile führen" in log
