"""0.37: Oberfläche zur Vorschau (Trockenlauf) und zum Vergleich mit Regler/Automation – statische Prüfung.

Wie tests/test_ui_v363.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird, dass die neuen Teile
angebunden sind (Karten in der Übersicht gleich unter den Kacheln, eigene Abfrage GET api/preview, Farben in beiden
Themen) und dass keine zweite y-Achse entsteht. Die Anzeige-Funktionen laufen in Node mit Antworten der echten
Vorschau bzw. des echten Protokolls – so ist der fertige Text geprüft (Satz, Grundlage, Tooltip je Stunde, Tabelle
mit Zeitumstellung, Kennzahlen, Tagestabelle, Wochenraster), nicht nur der Quelltext. Verhalten im Browser
(Grafen, Hover, 390/1100 px, hell/dunkel) prüft das Playwright-Skript, das nicht Teil der Suite ist.
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

from heatpilot import preview as pv, preview_log as pl
from heatpilot.config import Config
from heatpilot.web import clean

from test_control34 import MON, ts_of


# Die Testhäuser dieser Datei brauchen 28–32 °C Vorlauf. Seit 0.39 heizt die Vorschau erst ab 35 °C (darunter
# „Heizbetrieb aus“, Wunsch Ingo) – diese Tests prüfen anderes (Vorhersage, Gewohnheit, Protokoll …) und laufen
# deshalb mit der alten Untergrenze; die 35-°C-Regel selbst prüft tests/test_flow_min39.py.
@pytest.fixture(autouse=True)
def _old_flow_floor(monkeypatch):
    monkeypatch.setattr(pv, "FLOW_MIN", 25.0)
from test_preview37 import dhw_model, forecast, house
from test_ui_v361 import NODE, between, html, plain, script, source

ROOT = Path(__file__).resolve().parents[1]

# DOM-Ersatz: $() liefert je id ein Objekt; renderCompare ohne Grafen (nur „sammelt“)
STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "" });
"""
NAMES = ("fmt", "esc", "clock", "kelvin", "dateOf", "WEEKDAYS", "PHASE", "timeTicks", "PV_NOTE", "PV_C", "PV_WHAT", "PV",
         "CMP_GRID_MIN", "tsOf", "tclock", "wday", "degC", "tipKey", "span10", "pvBandOf", "pvWrite", "pvSummary", "pvBasis",
         "pvTip", "pvTable", "pvRooms", "stepPath", "dayTicks", "renderCompare", "cmpFacts", "cmpDays", "cmpGrid")


@pytest.fixture
def berlin(monkeypatch):
    """Python wie Node in Europe/Berlin – Uhrzeiten im Text gleich."""
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


def preview_json(tmp_path, now=None, **kw):
    """Vorschau der echten Engine als JSON (wie GET api/preview): ein Raum „Wz“ (macht mit), Vorhersage, Warmwasser
    mit großer Zapfung sonntags 18 Uhr, Kessel steht auf 45 °C Vorlauf und 55 °C Warmwasser."""
    eng, ha, _ = house(tmp_path, cfg=Config(control_mode="heatpilot", **kw), weather="weather.home")
    now = now or ts_of(MON, 12)
    eng.forecast = forecast(now - 3600, [6.0, 6.0, 5.0, 4.0, 3.0, 2.0] + [0.0] * 30)
    eng.dhw = dhw_model()
    eng.found.boiler.update({"sel_flow_temp": "sensor.sel_flow", "dhw_seltemp": "sensor.dhw_set"})
    eng.states.update({"sensor.sel_flow": {"state": "45"}, "sensor.dhw_set": {"state": "55"}})
    p = json.loads(json.dumps(clean(pv.preview(eng, now)), allow_nan=False))
    assert ha.calls == []  # nur Anzeige
    return p


def hour_of(p, hhmm):
    return next(i for i, h in enumerate(p["hours"]) if h["t"][11:16] == hhmm)


# ------------------------------------------------------------------ Einbau
def test_overview_cards_after_tiles_with_own_request(tmp_path):
    page = html()
    ov = between(page, '<section id="view-overview" hidden>', "</section>")
    # 0.43 Schritt 6: eigene Unterseite „Vorschau“ (Zeile zum Wochenplan, Vorschau, Vergleich zugeklappt darunter)
    vs = between(ov, '<div id="ov-vorschau" hidden>', "</section>")
    ids = re.findall(r'^  <(?:div|details) class="[\w -]+" id="([\w-]+)"', vs, re.M)
    assert ids[:4] == ["sched", "pv", "cmp-fold", "cmp"]
    assert '<h2>Vorschau (24 h)</h2>' in ov and "Vorschau gegen Ist (7 Tage)" in ov  # 0.43: Begriffe
    js = script()
    # eigene Abfrage beim Laden der Übersicht (nicht im summary, nicht im 30-s-Takt); ältere Version: Karten weg
    assert "loadPreview().catch(previewOff);" in between(js, "async function load() {", "// ---------------------------------------------------------------- Einstellungen")
    lp = between(js, "async function loadPreview() {", "function previewOff()")
    assert 'getJSON("api/preview")' in lp and "if (j.starting) return;" in lp
    assert 'function previewOff() { $("pv").hidden = true; $("cmp").hidden = true; }' in js
    # Hinweis gleich dem Backend; Phase „aus“ (Thermostat aus) hat einen Namen
    r = run_js(tmp_path, "out({ note: PV_NOTE, aus: PHASE.aus });")
    assert r == {"note": pv.NOTE, "aus": "Aus"}
    # Doku und Changelog nennen die Stelle
    docs = re.sub(r"\s+", " ", (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8"))
    assert "Zu finden in der Übersicht, Abschnitt „Vorschau (Trockenlauf)“" in docs
    assert "Oberfläche, Übersicht → „Vorschau (Trockenlauf)“" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_no_second_y_axis_and_hover_per_hour():
    js = script()
    for fn, nxt in (("function pvFlowChart(", "function pvDhwChart("), ("function pvDhwChart(", "// Stufenlinie"),
                    ("function cmpChart(", "// Tage als Achse")):
        body = between(js, fn, nxt)
        assert body.count("frame(box, [x0, x1]") == 1, fn  # eine Skala je Graf
        assert "right:" not in body and "rightLines" not in body, fn  # keine zweite y-Achse (frame/timeChart „right“)
        assert re.search(r'hourHover\("(pv|cmp)", f, ', body), fn  # Tooltip je Stunde, in beiden Grafen gleich
    # erst der Tooltip (blendet die vorige Markierung aus), dann die Stunde in allen Grafen der Gruppe markieren
    hover = between(js, "function hourHover(", "function pvTip(")
    assert hover.index("showTip(e, html(i)") < hover.index("all.forEach((m) => m.show(i));")
    # Helfer des Live-Reiters und das Wochenraster werden nur benutzt, nicht verändert
    assert "function frame(container, xs, ys, opts = {}) {" in js and "function heatmap(box, grid, o = {}) {" in js
    assert "heatmap(box, grid, { unit: \"K\", digits: 0, diverging: true" in between(js, "function cmpGrids(", "\n}\n")


def test_css_tokens_in_both_themes():
    css = html().split("<style>")[1].split("</style>")[0]
    light = css.split("@media (prefers-color-scheme: dark)")[0]
    dark_media = between(css, "@media (prefers-color-scheme: dark)", ":root[data-theme=\"dark\"]")
    dark_attr = between(css, ':root[data-theme="dark"] {', "}")
    for block in (light, dark_media, dark_attr):
        assert "--band-night:" in block and "--band-away:" in block
    assert "#cmp-mode[hidden] { display: none; }" in css  # .row setzt display: flex – hidden allein reicht nicht
    assert ".tip-why { display: inline-block; white-space: normal;" in css


# ------------------------------------------------------------------ Vorschau: Satz, Grundlage, Tooltip, Tabelle
def test_summary_and_basis_from_real_preview(tmp_path, berlin):
    p = preview_json(tmp_path)
    w = p["writes_24h"]
    n = w["flow"] + w["heating"] + w["dhw"]
    assert w["heating"] >= 2 and w["dhw"] >= 1
    r = run_js(tmp_path, f"const P = {json.dumps(p)}; out({{ sum: pvSummary(P), basis: pvBasis(P),"
                         f" on: pvSummary({{ ...P, start: {{ ...P.start, heating: true }} }}),"
                         f" none: pvSummary({{ ...P, writes_24h: {{ flow: 0, heating: 0, dhw: 0 }}, start: {{}} }}) }});")
    assert plain(r["sum"]) == (f"In den nächsten 24 h würde HeatPilot {n}× schreiben (Vorlauf {w['flow']}×, Heizbetrieb "
                               f"{w['heating']}×, Warmwasser {w['dhw']}×) – nur bei Änderung (Vorlauf ab 1 K, Warmwasser ab "
                               "1 K, Heizbetrieb an/aus), danach 5 min Haltezeit. Heute am Kessel: Vorlauf-Soll 45 °C · "
                               "Warmwasser-Soll 55 °C.")  # Betriebsart unbekannt: kein „Heizbetrieb an/aus“
    assert plain(r["on"]).endswith("Heute am Kessel: Vorlauf-Soll 45 °C · Warmwasser-Soll 55 °C · Heizbetrieb an.")
    assert plain(r["none"]).startswith("In den nächsten 24 h würde HeatPilot nichts schreiben – nur bei Änderung")
    assert plain(r["none"]).endswith("Was heute am Kessel steht, ist unbekannt – der erste Wert zählt als Schreibvorgang.")
    # Fühler 5 °C, Vorhersage für jetzt 6 °C: angeglichen, mit dem Versatz (0.37, Fund aus der Prüfung)
    assert plain(r["basis"]) == ("Gerechnet wie bei „HeatPilot steuert“: Räume mit „macht mit“ nach dem Ziel der "
                                 "Steuerung, die übrigen nach Tado. Außentemperatur aus der Vorhersage, an den Außenfühler "
                                 "angeglichen: Er misst jetzt 1 K weniger als vorhergesagt, der Unterschied klingt über 6 h "
                                 "ab. Vorlauf aus Wz.")
    same = plain(run_js(tmp_path, f"out(pvBasis({json.dumps({**p, 'forecast_offset_k': 0.2})}));"))
    assert "Außentemperatur aus der Vorhersage. Vorlauf aus Wz." in same
    # Beobachten, ohne Vorhersage, Räume ohne Modell: Gründe zusammengefasst
    q = {**p, "control_mode": "beobachten", "forecast": False, "rooms_used": [],
         "rooms_skipped": [{"name": "Bad", "why": "Raummodell noch nicht plausibel"},
                           {"name": "Flur", "why": "Raummodell noch nicht plausibel"},
                           {"name": "Diele", "why": "Fußbodenheizung – zählt nicht für den Vorlauf"}]}
    b = plain(run_js(tmp_path, f"out(pvBasis({json.dumps(q)}));"))
    assert b == ("Gerechnet mit dem, was Tado voraussichtlich will (Beobachten): tagsüber das heutige Soll, nachts und bei "
                 "Abwesenheit abgesenkt. Keine Vorhersage – alle Stunden mit der aktuellen Außentemperatur. Nicht dabei: "
                 "Bad, Flur (Raummodell noch nicht plausibel); Diele (Fußbodenheizung – zählt nicht für den Vorlauf).")


def test_tooltip_per_hour_night_preheat_and_writes(tmp_path, berlin):
    p = preview_json(tmp_path)
    night, pre = hour_of(p, "23:00"), next(i for i, h in enumerate(p["hours"]) if "heizt vor" in h["flow_reason"])
    r = run_js(tmp_path, f"const P = {json.dumps(p)}; out([pvTip(P, {night}), pvTip(P, {pre}), pvTip(P, 0)]);")
    t_night, t_pre, t_first = (plain(x) for x in r)
    assert t_night.startswith("Mo 23:00–00:00 · Nacht Vorlauf-Soll: Heizbetrieb aus kein Raum braucht Wärme – Heizbetrieb aus")
    assert "Außen: 0 °C (Vorhersage)" in t_night and "Warmwasser-Soll: 40 °C" in t_night
    h = p["hours"][pre]
    peak = f" (bis {h['flow_max']} °C)" if h["flow_max"] > h["flow"] else ""
    assert f"Vorlauf-Soll: {h['flow']} °C{peak} {h['flow_reason']}" in t_pre
    assert h["flow_reason"] in t_pre and t_pre.startswith(f"Di {h['t'][11:16]}–")
    # Schreibvorgänge einzeln mit Uhrzeit, vorher → nachher; erste Stunde zählt gegen den Kessel (45 °C)
    first = p["hours"][0]["writes"]
    assert first and "würde schreiben:" in t_first
    for w in first:
        what = {"flow": "Vorlauf", "heating": "Heizbetrieb", "dhw": "Warmwasser"}[w["what"]]
        assert f"{w['t'][11:16]} {what}" in t_first
    assert any(w["what"] == "flow" and w["from"] == 45 for w in first) and "45 → " in t_first
    # Linien-Schlüssel statt Punkte, Werte fett, Gründe als eigene, umbrechende Zeile
    assert r[0].count('class="tip-key"') == 3 and "border-top:2px dashed var(--series-1)" in r[0]
    assert '<span class="tip-why">kein Raum braucht Wärme – Heizbetrieb aus</span>' in r[0]


def test_table_rows_with_reasons_room_changes_and_dst(tmp_path, berlin):
    p = preview_json(tmp_path / "a")
    r = run_js(tmp_path, f"out(pvTable({json.dumps(p)}));")
    assert len(re.findall(r'<tr class="pv-h">', r)) == 24 and len(re.findall(r'<tr class="pv-why">', r)) == 24
    rows = re.findall(r'<tr class="pv-why"><td colspan="5">(.*?)</td></tr>', r)
    # erste Stunde: alle Räume; danach nur Änderungen (Nachtbeginn), sonst keine Raumliste
    assert plain(rows[0]).startswith(f"Vorlauf: {p['hours'][0]['flow_reason']} · Warmwasser: {p['hours'][0]['dhw_reason']}"
                                     " · Räume: Wz 21 °C (Komfort)")
    i21 = hour_of(p, "21:00")
    assert "Räume: Wz 18 °C (Nacht)" in plain(rows[i21]) and "Räume:" not in plain(rows[1])
    cells = [plain(c) for c in re.findall(r'<tr class="pv-h"><td>(.*?)</td>', r)]
    assert cells[0] == "Mo 12:00" and cells[hour_of(p, "00:00")].startswith("Di 00:00 Nacht")
    assert "Heizbetrieb aus" not in cells[0] and "aus" in plain(re.findall(r'<tr class="pv-h">(.*?)</tr>', r)[hour_of(p, "23:00")])
    # Herbst: 02:00 kommt zweimal – die zweite ist gekennzeichnet
    q = preview_json(tmp_path / "b", now=int(datetime(2026, 10, 24, 12, 0).timestamp()))
    r = run_js(tmp_path, f"out(pvTable({json.dumps(q)}));")
    cells = [plain(c) for c in re.findall(r'<tr class="pv-h"><td>(.*?)</td>', r)]
    assert len(cells) == 24 and [c for c in cells if c.startswith("02:00")] == ["02:00 Nacht", "02:00 (2.) Nacht"]
    assert 'title="Zeitumstellung: diese Stunde gibt es heute zweimal"' in r


def test_step_path_bands_and_day_axis(tmp_path, berlin):
    r = run_js(tmp_path, """
      const sx = (t) => t, sy = (v) => v;
      const d0 = Date.parse("2026-10-13T00:00:00+02:00") / 1000, x1 = d0 + 7 * 86400 - 12 * 3600;
      out({ steps: stepPath([0, 10, 20, 30, 40], [10, 20, 30, 40, 50], [5, 6, null, 7, 7], sx, sy),
            gap: stepPath([0, 3600, 10800], [3600, 7200, 14400], [1, 2, 3], sx, sy),
            days: dayTicks(d0, x1).map(([t, l]) => [new Date(t * 1000).toISOString(), l]),
            short: dayTicks(d0, d0 + 24 * 3600).length === timeTicks(d0, d0 + 24 * 3600).length,
            band: [pvBandOf({ night: true, home: 0 }), pvBandOf({ night: false, home: 0 }), pvBandOf({ vacation: true }),
                   pvBandOf({ night: false, home: 2 })],
            span: [span10([40, 40]), span10([25, 75])] });""")
    # Stufen: anschließend verbunden (V), Lücke bei null und bei fehlender Stunde neu angesetzt (M)
    assert r["steps"] == "M0.0,5.0H10.0V6.0H20.0M30.0,7.0H40.0V7.0H50.0"
    assert r["gap"] == "M0.0,1.0H3600.0V2.0H7200.0M10800.0,3.0H14400.0"
    # Tage: Wochentag mitten im sichtbaren Teil des Tages (der letzte, halbe Tag mittags um 6 Uhr)
    assert [x[1] for x in r["days"]] == ["Di", "Mi", "Do", "Fr", "Sa", "So", "Mo"]
    assert r["days"][0][0] == "2026-10-13T10:00:00.000Z" and r["days"][-1][0] == "2026-10-19T04:00:00.000Z"
    assert r["short"] is True  # unter 36 h: Uhrzeiten wie im Live-Reiter
    # Nacht vor Abwesenheit (wie die Phasen der Räume), sonst kein Band
    assert r["band"] == ["var(--band-night)", "var(--band-away)", "var(--band-away)", None]
    # konstante Werte nicht als feine Schwankung: mindestens 10 K
    assert r["span"] == [{"yMin": 35, "yMax": 45}, {}]


# ------------------------------------------------------------------ Vergleich mit Regler/Automation
def compare_json(tmp_path, collecting=False):
    eng, _, _ = house(tmp_path)
    eng.dhw = dhw_model()
    s = eng.store
    if not collecting:
        for d, (flow, act) in enumerate(((30, 36), (30, 38))):  # Sonntag und Montag 12 Uhr
            ts = ts_of(MON, 12) - (1 - d) * 86400
            s.add_preview_plan(ts, {"flow": flow, "heating": 1, "dhw": 45, "t_out": 5.0, "w_flow": 1, "w_heating": 0, "w_dhw": 1})
            s.set_preview_actual(ts, {"act_flow": act, "act_heating": 1.0, "act_dhw": 55.0, "act_n": 12,
                                      "ch_flow": 3, "ch_heating": 0, "ch_dhw": 1})
        s.add_preview_plan(ts_of(MON, 13), {"flow": None, "heating": 0, "dhw": 45, "t_out": 5.0})
        s.set_preview_actual(ts_of(MON, 13), {"act_flow": 44, "act_heating": 0.8, "act_dhw": 55.0, "act_n": 12,
                                              "ch_flow": 1, "ch_heating": 0, "ch_dhw": 0})
    else:
        s.add_preview_plan(ts_of(MON, 13), {"flow": 30, "heating": 1, "dhw": 45, "t_out": 5.0})
    return json.loads(json.dumps(clean(pl.compare(s, ts_of(MON, 14, 30), eng.dhw)), allow_nan=False))


def test_compare_facts_and_days_from_real_log(tmp_path, berlin):
    c = compare_json(tmp_path)
    t = c["total"]
    assert not c["collecting"] and t["heating_off_h"] == 1 and t["dhw_kwh"] < 0
    r = run_js(tmp_path, f"const C = {json.dumps(c)}; out({{ facts: cmpFacts(C.total), days: cmpDays(C.days) }});")
    facts = [plain(x) for x in re.findall(r"<li>(.*?)</li>", r["facts"])]
    assert facts == [
        "Vorlauf: 2 Stunden verglichen, in denen beide heizen – im Mittel −7 K; 2 h tiefer, 0 h höher (ab 1 K).",
        "Heizbetrieb: 1 h wäre HeatPilot aus, während der Regler heizt.",
        "Warmwasser: 3 Stunden verglichen – im Mittel −10 K; 3 h tiefer, 0 h höher.",
        f"Schreibvorgänge: HeatPilot {t['writes_plan']} (Vorlauf 2, Heizbetrieb 0, Warmwasser 2) · Regler und Automation "
        f"{t['changes_act']} Wechsel (Vorlauf 7, Heizbetrieb 0, Warmwasser 2).",
    ]
    rows = re.findall(r"<tr title=\"([^\"]*)\">(.*?)</tr>", r["days"])
    assert plain(re.search(r"<tr>(.*?)</tr>", r["days"]).group(1)) == "Tag Vorlauf Warmw. Schreiben Speicher kWh"
    assert [plain(x[1]).split(" ")[:2] for x in rows] == [["Mo", "12."], ["So", "11."]]  # neuester oben
    mon = c["days"][-1]
    assert rows[0][0] == mon["text"]  # der Satz des Tages im title
    kwh = f"{abs(mon['dhw_kwh']):.1f}".replace(".", ",")
    assert plain(rows[0][1]) == f"Mo 12. {mon['hours']} h −8 K −10 K {mon['writes_plan']} statt {mon['changes_act']} −{kwh}"


def test_compare_collecting_hides_charts(tmp_path, berlin):
    c = compare_json(tmp_path, collecting=True)
    assert c["collecting"] and c["text"] == pl.EMPTY
    r = run_js(tmp_path, f"""renderCompare({json.dumps(c)});
      out({{ text: $("cmp-text").textContent, facts: $("cmp-facts").innerHTML, card: $("cmp").hidden,
            hidden: ["cmp-mode", "cmp-lines", "cmp-grid", "cmp-days-box"].map((id) => $(id).hidden) }});""")
    assert r["text"] == "sammelt – erste Werte nach einer Stunde" and r["card"] is False
    assert plain(r["facts"]) == "Eine Stunde mit Plan – der Ist-Wert kommt nach Ablauf der Stunde dazu."
    assert r["hidden"] == [True, True, True, True]
    r = run_js(tmp_path, 'renderCompare({ collecting: true, text: "sammelt – erste Werte nach einer Stunde", series: [], days: [], total: null });'
                         ' out($("cmp-facts").innerHTML);')
    assert r == ""


def test_week_grid_by_weekday_and_hour(tmp_path, berlin):
    c = compare_json(tmp_path)
    r = run_js(tmp_path, f"const S = {json.dumps(c['series'])}; out({{ flow: cmpGrid(S, 'flow', 'act_flow'), dhw: cmpGrid(S, 'dhw', 'act_dhw') }});")
    flow, dhw = r["flow"], r["dhw"]
    assert len(flow) == 7 and all(len(row) == 24 for row in flow)
    # So 12 Uhr −6 K, Mo 12 Uhr −8 K; Mo 13 Uhr HeatPilot aus → kein Vorlauf-Vergleich, Warmwasser schon
    assert flow[6][12] == -6 and flow[0][12] == -8 and flow[0][13] is None
    assert dhw[0][13] == -10 and sum(v is not None for row in flow for v in row) == 2


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    scripts = re.findall(r"<script>(.*?)</script>", html(), re.S)
    assert len(scripts) == 1
    f = tmp_path / "inline.js"
    f.write_text(scripts[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_pv_section_small_functions():
    """Lesbarkeit: die neuen Funktionen bleiben kurz (Richtwert ≤ ~60 Zeilen)."""
    js = between(script(), "// ---------------------------------------------------------------- Vorschau (Trockenlauf, 0.37)",
                 "// Anwesenheit: wie HeatPilot Entfernung")
    for m in re.finditer(r"^(?:async )?function (\w+)\(", js, re.M):
        body = source(js, m.group(1))
        assert body.count("\n") <= 60, m.group(1)
