"""0.39: Oberfläche zum Ventilverhalten und zu den Ventilen im Trockenlauf – statische Prüfung.

Wie tests/test_ui_v38.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird der Einbau (Abschnitt
„Ventilverhalten“ im Raum-Dialog des Heizen-Reiters mit Graf erst im offenen Dialog und Tooltip über dem Dialog,
Tabelle „Ventilverhalten“ in Übersicht → Räume, Abschnitt „Ventile (Trockenlauf)“ am Ende der Vorschau), dass die neuen
Teile nichts schreiben und ältere Antworten ohne die Felder still weglassen, und die Texte – in Node mit Antworten des
echten Backends (ValveModel.summary an künstlichen Thermostaten aus tests/sim_valve.py, valve_dry.summary eines
künstlichen Hauses über drei Tage). Graf, Hover, 390/1100 px und hell/dunkel prüft das Playwright-Skript, das nicht Teil
der Suite ist. Minuten rundet die Oberfläche wie das Backend (Halbe auf) – die Erwartungen hier ebenso (``half_up``), in
jeder Zeitzone.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from heatpilot import valve_dry as vd
from heatpilot import valve_model as vm
from heatpilot.web import clean
from sim_valve import Scenario, Thermostat
from test_control34 import MON, ts_of
from test_ui_v361 import NODE, between, html, plain, script, source
from test_valve39 import START, learn
from test_valve_dry39 import BAD, WZ, _house_day, house, room, slow_room, valve_model

ROOT = Path(__file__).resolve().parents[1]
SECTION = "// ---------------------------------------------------------------- Ventilverhalten (0.39)"
SECTION_END = "// Anwesenheit: wie HeatPilot Entfernung und Heimkehr deutet"

# DOM-Ersatz: $() liefert je id ein Objekt; den Balkengrafen (SVG) prüft das Playwright-Skript – hier nur die Tage
STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "" });
let CHART = null;
function vdDayChart(box, days) { CHART = days; }
"""
NAMES = ("fmt", "esc", "clock", "timeOf", "shortName", "WEEKDAYS", "wday", "dateOf", "VALVE_E", "kUnder", "eText",
         "VALVE_REG", "switchKnown", "valveGraph", "valveHtml", "valveDetails", "valveCurveTable", "valveRoomsHtml", "valveRoomRow",
         "VD_NOTE", "VD_RULE", "VD_C", "renderValves", "vdFacts", "vdRules", "vdActive", "vdDays", "vdDayName", "vdTable",
         "vdExamples", "vdEffect")


@pytest.fixture
def berlin(monkeypatch):
    """Python wie Node in Europe/Berlin – Tage und Uhrzeiten im Text gleich."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def run_js(tmp_path, body: str):
    """``body`` mit den Funktionen aus index.html in Node ausführen; gibt das JSON von ``out(...)`` zurück."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    code = STUBS + "\n".join(source(js, n) for n in NAMES) + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def as_json(v):
    return json.loads(json.dumps(clean(v), allow_nan=False))


def half_up(x: float) -> int:
    """Wie fmt(x, 0) in der Oberfläche und whole_min im Backend: Halbe aufrunden (Python round rundet 14,5 auf 14)."""
    return int(x + 0.5)


def models(tmp_path) -> dict:
    """Echte valve_model-Antworten (wie api/heat): stetig mit Gerät-Schritt, mit Integrator (Schritt angenommen), nur
    an/aus, zu wenig Daten (lernt, Schritt 0,1 K), frisch beim Nachlernen aus der Historie."""
    t = START + 6 * 86400  # Gütemaß der gelernten Tage – nicht vom heutigen Datum abhängig
    few, _, _ = learn(Thermostat(), Scenario(days=1, extra_jumps=1), tmp=tmp_path)
    return as_json({"p": valve_model("p").summary(0.5, "gerät", t), "pi": valve_model("pi").summary(0.5, "angenommen", t),
                    "bin": valve_model("binary").summary(0.5, "gerät", t), "few": few.summary(0.1, "gerät"),
                    "catch": vm.ValveModel().summary(0.5, "angenommen", catching_up=True)})


def dry_summary(tmp_path) -> tuple[dict, int]:
    """valve_dry.summary eines künstlichen Hauses über drei Tage (beide Regeln, Nachtrag nach 1 h – nach dem dritten
    Morgen ist die Vorhersage des Überschwingens dreimal nachgemessen, die Schätzung steht da) und das Ende."""
    eng, _, _ = house(tmp_path, rooms=[room("Wz", WZ), room("Bad", BAD), room("Kind", "climate.kind")])
    for c, (a, b) in {WZ: (0.08, 0.03), BAD: (0.02, 0.02), "climate.kind": (0.02, 0.025)}.items():
        eng.rooms[c] = slow_room(a=a, b=b)
    eng.valves[WZ] = valve_model("pi")
    end = ts_of(MON, 0) + 3 * 86400 - 60
    _house_day(eng, ts_of(MON, 0), 3)
    return as_json(vd.summary(eng, end)), end


# ------------------------------------------------------------------ Einbau
def test_dialog_overview_and_preview_are_wired():
    page, js = html(), script()
    # Raum-Dialog: Abschnitt unter Steuerung und Geräten, nur mit valve_model (ältere Version: nichts, kein Aufruf)
    assert 'ctlInfo(r) + (r.valve_model ? valveHtml(r.valve_model) : "");' in between(js, "function roomDlg(r) {", "// Was die Regelschleife")
    # Graf erst mit Breite: nach showModal und bei jedem Neuaufbau des offenen Dialogs
    assert "if (!d.open) d.showModal(); dlgValveChart(); }" in between(js, "function openDlg(kind, room) {", "function closeDlg()")
    assert "d.built = key;\n    dlgValveChart();" in between(js, "function renderDlg() {", "function boostDlg(")
    assert "if (!box || !box.clientWidth || !v || !valveGraph(v)) return;" in between(js, "function valveChart(box, v) {", "function valveCurveMarks(")
    # Tooltip über dem Dialog: ein offener Dialog liegt in der obersten Ebene – das Hinweisfeld kommt dort hinein
    tipfn = between(js, "function showTip(e, html, onHide) {", "function hideTip()")
    assert 'const host = (e.target && e.target.closest && e.target.closest("dialog[open]")) || document.body;' in tipfn
    assert tipfn.index("host.appendChild(tip)") < tipfn.index("tip.offsetWidth")  # erst umhängen, dann messen
    # Übersicht → Räume: Tabelle unter den Raummodellen; Vorschau: Abschnitt am Ende der Karte, mit ihr ausgeblendet
    assert '$("rooms-valves").innerHTML = valveRoomsHtml(s.rooms);' in between(js, "async function load() {", "// ---------------------------------------------------------------- Einstellungen")
    assert '<div class="scroll"><table id="rooms"></table></div>\n    <div id="rooms-valves"></div>' in page
    pv = between(page, '<div class="card" id="pv" style="margin-bottom:12px" hidden>', '<div class="card" id="cmp"')
    assert '<div id="vd" hidden>\n      <h3>Ventile (Trockenlauf)</h3>' in pv and pv.index('id="pv-hours"') < pv.index('id="vd"')
    assert "renderValves(PV.valves);" in between(js, "function drawPreview() {", "function renderPreview(p)")
    assert 'function previewOff() { $("pv").hidden = true; $("cmp").hidden = true; }' in js  # #vd liegt in #pv
    assert "openDlg(\"room\", c);" in between(js, "function openValve(c) {", "// ----")


def test_ui_knows_no_write_path_for_valves():
    """Nur Anzeige: die neuen Teile fragen nichts ab und schicken nichts – die Felder kommen mit api/heat, api/summary
    und api/preview, die es schon gibt."""
    js = re.sub(r"^\s*//.*$", "", between(script(), SECTION, SECTION_END), flags=re.M)  # ohne Kommentarzeilen
    for word in ("fetch(", "getJSON(", "heatPost(", "api/", "POST", "set_temperature", "method:"):
        assert word not in js, word
    assert not re.search(r'"api/[^"]*valve', script())


def test_constants_match_backend(tmp_path):
    r = run_js(tmp_path, "out({ note: VD_NOTE, rules: Object.keys(VD_RULE), colors: VD_C, e: VALVE_E });")
    assert r["note"] == vd.NOTE and r["rules"] == list(vd.RULES)
    assert r["e"] == [vm.E_MIN, round(vm.E_MIN + (vm.N_CLASSES - 1) * vm.E_STEP, 1)]
    assert r["colors"] == {"drosseln": "var(--series-1)", "gemeinsam": "var(--series-2)"}  # Farbe je Regel, fest


# ------------------------------------------------------------------ Raum-Dialog
def test_dialog_section_from_real_models(tmp_path):
    m = models(tmp_path)
    r = run_js(tmp_path, f"const M = {json.dumps(m)};"
                         "out({ ...Object.fromEntries(Object.entries(M).map(([k, v]) => [k, valveHtml(v)])), none: valveHtml(null),"
                         " evil: valveHtml({ ...M.p, text: '<b>x</b>' }) });")
    p, pi, b, few, catch = (r[k] for k in ("p", "pi", "bin", "few", "catch"))
    # stetig, belastbar: Titel, Kasten für den Graf, der Satz aus dem Backend, Einzelheiten
    assert p.startswith('<div class="valve" id="dlg-valve"><h3 class="sec-h">Ventilverhalten</h3>')
    assert '<div class="pv-title"><span>Kennlinie: Ventil (%) je K unter Soll</span></div><div class="valve-chart" id="dlg-valve-chart"></div>' in p
    assert f'<p class="valve-text" id="dlg-valve-text">{m["p"]["text"]}</p>' in p
    assert m["p"]["text"].startswith("öffnet ab 0,2 K unter Soll, halb offen bei 0,6 K, ganz bei ")
    rows = [plain(x) for x in re.findall(r"<tr>(.*?)</tr>", p.split("Kennlinie als Tabelle")[0])]
    up, dn = m["p"]["response"]["up"], m["p"]["response"]["down"]
    assert rows[:4] == ["Art stetig – Ventil in %",
                        f"Soll-Sprünge {m['p']['jumps']} ({up['n']} auf, {dn['n']} zu; belastbar ab 8, je Richtung 4)",
                        f"öffnen (+0,5 K) Totzeit {half_up(up['dead_min'])} min, 63 % nach {half_up(up['t63_min'])} min ({up['n']}×)",
                        f"schließen (−0,5 K) Totzeit {half_up(dn['dead_min'])} min, 63 % nach {half_up(dn['t63_min'])} min ({dn['n']}×)"]
    # Satz und Tabelle zeigen dieselben Minuten (Halbe aufgerundet)
    assert f"reagiert auf +0,5 K in ≈ {half_up(up['t63_min'])} min, schließt in ≈ {half_up(dn['t63_min'])} min" in m["p"]["text"]
    assert rows[4] == f"Beharrung {half_up(m['p']['steady_h'])} h (belastbar ab 48 h)"
    assert rows[5].startswith("Vorhersagefehler 30 min voraus ") and "(höchstens 20); „Ventil bleibt, wie es ist“ " in rows[5]
    assert "Prozentpunkte (höchstens das 0,7-Fache), ein Messschritt " in rows[5]
    assert rows[6].startswith("Reglermodell Kp ") and "%/(K·min)" in rows[6]
    assert "wie ein P-Regler: die Kennlinie gehört zum Gerät" in rows[6] and "Messrauschen ≈ 0,03 K herausgerechnet" in rows[6]
    assert rows[7:9] == ["Schritt 0,5 K (vom Gerät)", "nicht gelernt nichts ausgelassen"]
    curve = re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", p.split("Kennlinie als Tabelle")[1])
    assert len(curve) == len(m["p"]["curve"]) and curve[0][0] == "1 K oder mehr über Soll" and curve[-1][0] == "2 K oder mehr unter Soll"
    # mit Integrator: öffnet schon über dem Soll – die Kennlinie hängt von der Last ab, der Satz nennt die Reglerwerte
    assert "Schritt 0,5 K (angenommen – das Gerät meldet keinen)" in plain(pi) and m["pi"]["e0"] < 0
    assert "Kennlinie: Ventil (%) je K unter Soll · hängt von der Last ab (Regler mit Integrator)" in plain(pi)
    assert m["pi"]["text"].startswith("regelt mit Integrator, Ruhepunkt am Soll (Kp ") and "Nachstellzeit" in m["pi"]["text"]
    assert "mit Integrator: die Kennlinie hängt von der Last ab" in plain(pi)
    # nur an/aus: Schaltpunkte statt Reglermodell
    assert '<h3 class="sec-h">Ventilverhalten <span class="muted">· nur an/aus</span></h3>' in b
    assert "Schaltpunkte: Ventil (%) je K unter Soll" in b and "Reglermodell" not in b
    sw = m["bin"]["switch"]
    assert f"Schaltpunkte ein bei 0,3 K unter Soll ({sw['n_on']}×), aus bei 0,1 K über Soll ({sw['n_off']}×)" in plain(b)
    assert "einschalten schaltet nach " in plain(b) and "Art nur an/aus – Ventil 0 oder 100 %" in plain(b)
    # lernt: Graf vorläufig (gestrichelt), Satz mit Grund; frisch: kein Graf, nur der Satz
    assert '<span class="muted">· vorläufig, gestrichelt</span>' in few and "Schritt 0,1 K (vom Gerät)" in plain(few)
    assert m["few"]["text"].endswith(f"lernt ({'; '.join(m['few']['reasons'])})") and m["few"]["text"] in few
    assert "dlg-valve-chart" not in catch and "Kennlinie als Tabelle" not in catch
    assert plain(catch).startswith("Ventilverhalten Kennlinie lernt noch · Schritt 0,5 K (angenommen) · lernt (lernt aus der "
                                   "gespeicherten Historie nach;")
    assert r["none"] == "" and "&lt;b>x&lt;/b>" in r["evil"]


def test_words_for_distance_to_setpoint(tmp_path):
    r = run_js(tmp_path, "out([eText(0.6), eText(-0.3), eText(0.04), eText(-1), eText(2), eText(null), kUnder(-0.78), kUnder(0.18)]);")
    assert r == ["0,6 K unter Soll", "0,3 K über Soll", "am Soll", "1 K oder mehr über Soll", "2 K oder mehr unter Soll",
                 "–", "−0,8", "0,2"]


def test_chart_is_one_axis_with_setpoint_line_and_switch_loop():
    js = between(script(), SECTION, SECTION_END)
    chart = between(js, "function valveChart(box, v) {", "function valveCurveMarks(")
    assert chart.count("frame(box, VALVE_E, [0, 100]") == 1 and "right:" not in chart  # eine y-Achse, 0–100 %
    assert '[x, x === 0 ? "Soll" : kUnder(x)]' in chart  # x: K unter Soll, 0 heißt „Soll“
    assert '...(v.reliable ? {} : { "stroke-dasharray": "5 4" })' in chart  # lernt: gestrichelt wie „Modell vorläufig“
    loop = between(js, "function valveSwitchMarks(f, sw, line) {", "// Fadenkreuz")
    # unten zu vom linken Rand bis „ein“, dort hoch; oben offen vom rechten Rand bis „aus“, dort runter – geschlossen
    assert "M${x0},${y0}H${f.sx(on)}V${y1}M${x1},${y1}H${f.sx(off)}V${y0}" in loop
    hover = between(js, "function valveHover(f, v) {", "const valveSwitchTip")
    assert "scrub(f.svg, hit," in hover and "showTip(e," in hover  # Maus und Finger wie die anderen Grafen


# ------------------------------------------------------------------ Übersicht → Räume
def test_overview_rows_per_room(tmp_path):
    m = models(tmp_path)
    rooms = [{"room": "Wohnzimmer Heizung", "climate": "climate.wz", "valve_model": m["p"]},
             {"room": "Schlafzimmer", "climate": "climate.sz", "valve_model": m["bin"]},
             {"room": "Küche", "climate": "climate.kueche", "valve_model": m["few"]},
             {"room": "Flur", "climate": "climate.flur", "valve_model": m["catch"]},
             {"room": "Gast", "climate": "climate.gast", "valve_model": None}]
    r = run_js(tmp_path, f"const R = {json.dumps(rooms)};"
                         "out({ html: valveRoomsHtml(R), old: valveRoomsHtml(R.map(({ valve_model, ...x }) => x)), none: valveRoomsHtml(undefined) });")
    h = r["html"]
    rows = [[plain(c) for c in re.findall(r"<td>(.*?)</td>", x)] for x in re.findall(r"<tr title=.*?</tr>", h)]
    p, up, dn = m["p"], m["p"]["response"]["up"], m["p"]["response"]["down"]
    assert rows[0][:5] == ["Wohnzimmer", "öffnet 0,2 · halb 0,6 · ganz 0,9", f"{half_up(up['t63_min'])} / {half_up(dn['t63_min'])} min",
                           f"{half_up(p['error']['pp_30'])} %-Punkte", "0,5 K"]
    assert rows[0][5] == "belastbar" and '<span class="badge vb-ok">belastbar</span>' in h
    assert '<a href="#" onclick="openValve(\'climate.wz\');return false">Wohnzimmer</a>' in h  # Name öffnet den Dialog
    assert rows[1][1:3] == ["an/aus: ein 0,3 · aus −0,1", f"schaltet in {half_up(m['bin']['response']['up']['dead_min'])} min"]
    assert rows[1][4] == "0,5 K" and rows[3][4] == "0,5 K angenommen"
    assert rows[2][5] == "lernt " + "; ".join(m["few"]["reasons"]) and rows[2][4] == "0,1 K"
    assert rows[3][1:4] == ["lernt noch", "–", "–"] and len(rows) == 4  # ohne Modell: keine Zeile
    assert f'title="{m["p"]["text"]}"' in h  # der ganze Satz beim Zeigen auf die Zeile
    assert r["old"] == "" and r["none"] == ""  # ältere Version: kein Abschnitt


# ------------------------------------------------------------------ Vorschau → Ventile (Trockenlauf)
def test_dry_run_section_from_real_house(tmp_path, berlin):
    v, end = dry_summary(tmp_path)
    assert v["interventions"]["drosseln"] >= 1 and v["interventions"]["gemeinsam"] >= 1
    r = run_js(tmp_path, f"""const V = {json.dumps(v)}, NOW = new Date({end * 1000});
      renderValves(V, NOW);
      const get = (id) => ({{ hidden: $(id).hidden, html: $(id).innerHTML, text: $(id).textContent }});
      out({{ ids: Object.fromEntries(["vd", "vd-note", "vd-text", "vd-facts", "vd-rules", "vd-active", "lg-vd", "ch-vd", "vd-ex-box",
              "vd-days-box", "vd-ex", "vd-rooms", "vd-days"].map((id) => [id, get(id)])), chart: CHART }});""")
    ids = r["ids"]
    assert not ids["vd"]["hidden"] and ids["vd-text"]["text"] == v["text"]
    assert ids["vd-note"]["html"].startswith("<b>Trockenlauf – HeatPilot schreibt nichts an die Thermostate.</b> Gezählt wird, was "
                                             "HeatPilot mit den Thermostaten als Stellglied tun würde – in den letzten 7 Tagen, nach Räumen.")
    facts = [plain(x) for x in re.findall(r"<li>(.*?)</li>", ids["vd-facts"]["html"])]
    d, g, n = v["effect"]["drosseln"], v["effect"]["gemeinsam"], v["interventions"]
    assert facts[0].startswith(f"Früh drosseln: {n['drosseln']}× ({d['reliable']} belastbar) – geschätzt ≈ ")
    assert f"K weniger Überschwingen je Eingriff ({d['checked']} nachgeprüft); " in facts[0] and d["checked"] >= 3
    assert facts[0].endswith("K über Soll in der Stunde danach.") and "ohne Eingriff gemessen: im Mittel bis " in facts[0]
    assert facts[1].startswith(f"Gemeinsam anfordern: {n['gemeinsam']}× ({g['reliable']} belastbar) – geschätzt ≈ "
                               f"{g['starts_saved_est']:.0f} Brennerstarts weniger (grob einer je mit angefordertem Raum)")
    assert facts[2] == (f"Schreibvorgänge: {v['writes']} – früh drosseln {v['writes_by']['drosseln']}, gemeinsam "
                        f"{v['writes_by']['gemeinsam']}, jeweils mit dem Zurücksetzen; nichts davon geschrieben.")
    rules = plain(ids["vd-rules"]["html"])
    assert rules.startswith("Früh drosseln: Nähert sich ein Raum seinem Soll (gemessen steigend, nach einem Soll-Anstieg oder "
                            "mindestens 0,2 K bzw. einen halben Schritt darunter), erreicht er es laut Raum- und Ventilmodell in "
                            "weniger als 20 min, wäre das Ventil dann noch über 30 % offen und sagen die Modelle mindestens "
                            "0,2 K Überschwingen voraus")
    assert "danach erst wieder nach einem neuen Soll oder 0,5 K Auskühlen" in rules
    assert "(ab 3 Starts je Stunde, Läufe im Mittel unter 10 min)" in rules and rules.endswith("5 min Haltezeit je Raum.")
    assert all(not ids[k]["hidden"] for k in ("lg-vd", "ch-vd", "vd-ex-box", "vd-days-box"))
    assert plain(ids["lg-vd"]["html"]) == "früh drosseln gemeinsam anfordern Eingriffe je Tag"
    # Balken: alle 7 Tage bis zum Ende des Fensters, ohne Eingriff mit 0 (die Achse überspringt keinen Tag)
    days = r["chart"]
    assert [x["day"] for x in days] == [f"2026-10-{d:02d}" for d in range(8, 15)]
    assert {x["day"]: x["writes"] for x in days if x["writes"]} == {x["day"]: x["writes"] for x in v["days"]}
    # Beispiele (neueste zuerst): Kopf, Grund mit Stand des Raums, Wirkung
    ex = [plain(x) for x in re.findall(r"<li>(.*?)</li>", ids["vd-ex"]["html"])]
    assert len(ex) == len(v["examples"])
    first = v["examples"][0]
    assert re.match(r"Mi\., \d\d:\d\d · Wz – früh drosseln, Soll 21 → 20,5 °C erreicht 21,0 °C in ≈ \d+ min, Ventil dann noch ≈ \d+ % "
                    r"offen · Raum [\d,]+ °C, Ventil 100 % geschätzt ≈ [\d,]+ K weniger Überschwingen · ohne Eingriff gemessen: bis "
                    r"[\d,]+ K über Soll$", ex[0]), ex[0]
    assert first["rule"] == "drosseln" and first["act_over_k"] is not None
    assert any("gemeinsam anfordern, Soll" in x and "Brennerstart weniger · ohne Eingriff gemessen: " in x for x in ex)
    rooms = [[plain(c) for c in re.findall(r"<td>(.*?)</td>", x)] for x in re.findall(r"<tr><td>.*?</tr>", ids["vd-rooms"]["html"])]
    assert rooms == [[x["name"], str(x["drosseln"]), str(x["gemeinsam"]), str(x["writes"])] for x in v["rooms"]]
    drows = re.findall(r"<tr><td>(.*?)</td>", ids["vd-days"]["html"])
    assert drows[0] == "Mi 14.10." and drows[-1] == "Do 08.10." and len(drows) == 7  # neuester oben


def test_dry_run_empty_old_active_and_counted_only(tmp_path, berlin):
    eng, _, _ = house(tmp_path)
    empty = as_json(vd.summary(eng, ts_of(MON, 7)))
    v, _ = dry_summary(tmp_path / "b")
    ex = v["examples"][0]
    act = {"climate": WZ, "name": "Wz", "rule": "drosseln", "setpoint": 20.5, "base": 21.0, "since": "2026-10-13T06:55:00+02:00"}
    r = run_js(tmp_path, f"""const E = {json.dumps(empty)}, V = {json.dumps(v)}, X = {json.dumps(ex)}, A = {json.dumps(act)};
      renderValves(E, new Date({ts_of(MON, 7) * 1000}));
      const e = Object.fromEntries(["vd", "vd-text", "vd-facts", "vd-rules", "lg-vd", "ch-vd", "vd-ex-box", "vd-days-box"].map((id) => [id, [$(id).hidden, $(id).textContent || $(id).innerHTML]]));
      renderValves(undefined);
      out({{ e, old: $("vd").hidden, active: vdActive([A]), none: vdActive([]),
            counted: vdEffect({{ ...X, reliable: false, est_k: null, pred_over_k: null, act_over_k: null }}),
            unchecked: vdEffect({{ ...X, est_checked: false, pred_over_k: 0.73, act_over_k: null }}),
            binary: vdEffect({{ ...X, est_k: 0 }}), join: vdEffect({{ ...X, rule: "gemeinsam", est_starts: 1, act_starts: 3 }}) }});""")
    e = r["e"]
    assert e["vd"][0] is False and e["vd-text"][1] == "Noch kein Eingriff – in 7 Tagen hätte HeatPilot an den Thermostaten nichts verstellt"
    assert e["vd-facts"][1] == "" and e["vd-rules"][1].startswith("<b>Früh drosseln:</b>")  # Regeln auch ohne Eingriff
    assert all(e[k][0] for k in ("lg-vd", "ch-vd", "vd-ex-box", "vd-days-box"))  # ohne Eingriff: kein Graf, keine Tabellen
    assert r["old"] is True  # ältere Version ohne „valves“: Abschnitt weg
    assert plain(r["active"]) == "Läuft gerade (nur im Protokoll): Wz – früh drosseln, Soll 21 → 20,5 °C seit 06:55" and r["none"] == ""
    assert r["counted"] == "nur gezählt (Modelle lernen noch) · gemessen wird nach 1 h"
    assert r["binary"].startswith("geschätzt ≈ 0 K weniger Überschwingen")  # an/aus gewinnt durchs Drosseln kaum etwas
    # noch nicht nachgeprüft: statt der Schätzung das vorhergesagte Überschwingen, das nach 1 h nachgemessen wird
    assert r["unchecked"] == "vorhergesagt ohne Eingriff ≈ 0,7 K über Soll (Schätzung wird nachgeprüft) · gemessen wird nach 1 h"
    assert r["join"] == "geschätzt ≈ 1 Brennerstart weniger · ohne Eingriff gemessen: 3 Brennerstarts in der Stunde"


def test_day_window_until_now(tmp_path):
    r = run_js(tmp_path, """const V = { window_days: 7, days: [{ day: "2026-10-12", drosseln: 1, gemeinsam: 2, writes: 6 },
        { day: "2026-10-01", drosseln: 9, gemeinsam: 0, writes: 9 }] };
      out({ days: vdDays(V, new Date(2026, 9, 14, 8)), names: [vdDayName("2026-10-12"), vdDayName("2026-10-12", true)] });""")
    assert [d["day"] for d in r["days"]] == [f"2026-10-{d:02d}" for d in range(8, 15)]
    assert r["days"][4] == {"day": "2026-10-12", "drosseln": 1, "gemeinsam": 2, "writes": 6}
    assert all(d["writes"] == 0 for i, d in enumerate(r["days"]) if i != 4)  # außerhalb des Fensters: nicht gezeigt
    assert r["names"] == ["Mo", "Mo 12.10."]


def test_day_chart_stacked_one_axis_with_legend_colors():
    js = between(script(), SECTION, SECTION_END)
    chart = between(js, "function vdDayChart(box, days) {", "// Je Raum bzw. Tag")
    assert chart.count("frame(box, [-0.5, days.length - 0.5], [0, max]") == 1 and "right:" not in chart
    assert "Math.max(3, ...days.map(" in chart  # ganze Zahlen auf der Achse
    assert "for (const k of Object.keys(VD_RULE))" in chart and "fill: VD_C[k]" in chart  # Farbe folgt der Regel
    assert "f.sy(base) - (base ? 1 : 0), y1 = f.sy(base + d[k]) + 1" in chart  # 2 px Fuge zwischen den Teilen
    assert 'hit.addEventListener("pointerdown"' in chart  # Tooltip auch mit dem Finger
    legend = between(js, "function renderValves(v, now = new Date()) {", "// Kennzahlen")
    assert '$("lg-vd").innerHTML = Object.keys(VD_RULE).map((k) => `<span><i style="background:${VD_C[k]}"></i>${VD_RULE[k]}</span>`)' in legend


# ------------------------------------------------------------------ Stil, Gültigkeit, Lesbarkeit, Doku
def test_css_rules_and_theme_tokens():
    css = html().split("<style>")[1].split("</style>")[0]
    for rule in ("#lg-vd[hidden] { display: none; }  /* .legend setzt display: flex */",
                 "svg text.halo { paint-order: stroke; stroke: var(--surface-1); stroke-width: 3px; stroke-linejoin: round; }",
                 "dialog.hp p.valve-text { color: var(--text-primary); margin: 6px 0 4px; }",
                 "#valve-rooms td:last-child { white-space: normal; min-width: 300px; }"):
        assert rule in css, rule
    # neue Regeln nutzen nur Farben, die es in beiden Themen gibt (dunkel umdefiniert bzw. gleich: --good)
    new = between(css, "/* Ventile (0.39)", "/* Warmwasser über HeatPilot (0.38)")
    used = set(re.findall(r"var\((--[\w-]+)\)", new))
    assert used <= {"--border", "--text-primary", "--text-secondary", "--good", "--surface-1"}, used
    dark = between(css, ':root[data-theme="dark"] {', "}")
    assert all(f"{t}:" in dark for t in used - {"--good"})


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    f = tmp_path / "inline.js"
    f.write_text(re.findall(r"<script>(.*?)</script>", html(), re.S)[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_new_section_small_functions():
    """Lesbarkeit: die neuen Funktionen bleiben kurz (Richtwert ≤ ~60 Zeilen)."""
    js = between(script(), SECTION, SECTION_END)
    names = re.findall(r"^(?:async )?function (\w+)\(", js, re.M)
    assert {"valveHtml", "valveDetails", "valveChart", "valveHover", "valveRoomsHtml", "renderValves", "vdFacts",
            "vdDayChart", "vdExamples"} <= set(names)
    for name in names:
        assert source(js, name).count("\n") <= 60, name


def test_docs_name_the_places():
    docs = re.sub(r"\s+", " ", (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8"))
    for text in ("Zu finden im Reiter **Heizen → Raum antippen**, Abschnitt „Ventilverhalten“",
                 "In der **Übersicht → Räume** steht unter der Tabelle der Raummodelle die Tabelle „Ventilverhalten“",
                 "Abschnitt „Ventile (Trockenlauf)“ am Ende der Vorschau",
                 "darunter das gelernte Ventilverhalten des Raums (seit 0.39"):
        assert text in docs, text
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("## 0.38.0")[0]
    assert "- **Oberfläche:** Heizen → Raum antippen – neuer Abschnitt „Ventilverhalten“" in log
    assert "Übersicht → Vorschau: Abschnitt „Ventile (Trockenlauf)“" in log
