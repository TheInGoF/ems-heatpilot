"""0.43 „Roundup“, Schritte 1 und 2: Bausteine, vier Reiter, Grafen-Grundlagen und der Reiter Heizen.

Wie die übrigen UI-Tests: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird statisch (Reiterleiste, CSS,
Markup des Reiters Heizen und des Abschnitts „Status“ der Übersicht) und in Node mit nachgebauten Daten: die Bausteine
(Mehr-Text, Statuszeile, Listenzeile, Umschalter, Kennzahl, Entitäts-ID), die Raumzustände nach dem Schema „Wer · Soll“,
die eine Zeile zum Raumbetrieb im Heizen, der Status der Übersicht (Räume, Warnungen, zuletzt geschrieben), die Legende
des Wochenrasters in schmalen Grafen und dass leere Grafen ihre Karte stehen lassen. Wunsch des Nutzers: im Heizen keine
Systemnachrichten – Warmwasser, Kessel, Bereitschaft, Warnungen und Protokoll stehen in Übersicht → Status bzw. Diagnose.
Darstellung bei 390/1100 px, hell/dunkel prüft das Playwright-Skript (nicht Teil der Suite).
"""

import json
import os
import re
import subprocess

import pytest
from test_ui_v361 import NODE, between, html, plain, script, source

STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, innerHTML: "", textContent: "", open: false, dataset: {},
  style: { setProperty() {} } });
const document = { querySelectorAll: () => [], querySelector: () => null };
let HEAT = null, LAST_SUMMARY = null, DLG = null, MSG_TIMER = null;
const setTimeout = () => 1, clearTimeout = () => {};
"""
NAMES = ("fmt", "esc", "clock", "timeOf", "READY", "readyDot", "readyRest", "readyLine", "ST_DOT", "statusLine", "more",
         "navRow", "seg", "kpi", "eid", "PHASE", "ctl", "managing", "unreachable", "boostLeft", "roomState", "heatStatus",
         "modeText", "ctlRooms", "shortName", "ovRoomsHtml", "ACTION_NAME", "DHW_ACTION", "BOILER_ACTION", "degC",
         "boilerVal", "boilerLogValue", "heatLogRows", "ovLastHtml", "WEEKDAYS", "SEQ_N", "_lin", "_gam", "toOklch",
         "fromOklch", "seqRamp", "seqStep", "heatLegend", "heatMsg", "leftText", "hm", "tileColors", "EMS_LOCK", "ctlInfo",
         "valveGuideLine", "VD_RULE", "roomDlg")


def run_js(tmp_path, body: str, extra: str = ""):
    """``body`` mit den Funktionen aus index.html in Node ausführen; gibt das JSON von ``out(...)`` zurück."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    code = (STUBS + extra + "\n".join(source(js, n) for n in NAMES) +
            "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body)
    f = tmp_path / "probe43.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, check=False, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def css() -> str:
    return html().split("<style>")[1].split("</style>")[0]


# ------------------------------------------------------------------ Schritt 1: Reiter, Bausteine, Grafen
def test_four_tabs_and_diagnose_as_last_settings_page():
    page, js = html(), script()
    tabs = re.findall(r'<button id="tab-(\w+)"[^>]*>([^<]+)</button>', between(page, '<nav class="tabs">', "</nav>"))
    assert tabs == [("heat", "Heizen"), ("overview", "Übersicht"), ("live", "Live"), ("settings", "Einstellungen")]
    assert 'id="tab-diag"' not in page and '<div id="view-diag">' in page  # Inhalt bleibt im DOM
    show = source(js, "showTab")
    assert 'if (name === "diag") return openDiag();' in show  # alte Sprünge landen im Tab Live
    # 0.58: Diagnose steht im Tab Live, geladen beim ersten Öffnen; Speicherleiste nur auf der Startseite aus
    assert "if (!DIAG_SEEN) { DIAG_SEEN = true; loadDiagPage(); }" in show
    live = between(page, '<section id="view-live" hidden>', "\n  </section>\n  <section")
    assert '<div id="view-diag">' in live and '<h3 class="nav-h">Diagnose</h3>' in live
    sp = source(js, "setPage")
    assert 'bar.hidden = SET_PAGE === "menu"' in sp and "diagnose" not in sp
    assert '`<div class="savebar sb-clean" id="savebar"${SET_PAGE === "menu" ? " hidden" : ""}>' in source(js, "renderSettings")
    # alle Unterseiten bleiben im DOM: nur versteckt (collect liest alle)
    assert 'const page = (k, html) => `<div id="set-${k}" class="subpage"${SET_PAGE === k ? "" : " hidden"}>${html}</div>`;' in source(js, "renderSettings")
    assert "for (const k of SET_KEYS) { const d = $(\"set-\" + k); if (d) d.hidden = SET_PAGE !== k; }" in sp


def test_settings_start_list_sidebar_and_save_bar():
    page, js = html(), script()
    keys = re.findall(r'\["(\w+)", "([^"]+)"\]', source(js, "SET_PAGES"))
    assert keys == [("raeume", "Räume"), ("kessel", "Kessel"), ("haus", "Haus")]  # 0.58: drei Kacheln (Styleguide)
    # Gerüst: Startseite, Kopf „‹ Einstellungen“, Inhalt
    sec = between(page, '<section id="view-settings"', "\n  </section>\n  <section")
    assert 'class="set-view set-on-menu" hidden>' in sec and '<nav class="set-menu" id="set-menu"' in sec
    assert "onclick=\"setPage('menu')\">‹ Einstellungen</button>" in sec and '<div id="set-body">' in sec
    assert '<div id="view-diag">' not in sec
    # ab 900 px Seitenleiste 220 px, Inhalt höchstens 760 px; dort ist immer eine Seite offen
    css = between(page, "<style>", "</style>")
    assert "grid-template-columns: 220px minmax(0, 760px)" in css and ".set-main { max-width: 760px;" in css
    assert 'if (p === "menu" && setWide()) p = SET_KEYS.includes(was) ? was : "raeume";' in source(js, "setPage")
    assert "SET_ALIAS" not in js  # keine Aliasse für alte Schlüssel
    assert 'store.set("hp-set-page", SET_PAGE)' in source(js, "setPage")  # zuletzt offene Unterseite
    # Speicherleiste erst bei Änderungen; „Verwerfen“ lädt das Gespeicherte
    assert ".savebar.sb-clean:has(#save-msg:empty) { display: none; }" in css
    rs = source(js, "renderSettings")
    assert '$("set-body").oninput = $("set-body").onchange = () => setDirty();' in rs
    assert 'onclick="loadSettings()">Verwerfen</button>' in rs and 'b.classList.remove("sb-clean")' in source(js, "setDirty")
    assert "setDirty();" in source(js, "roomsDirty") and "setDirty();" in source(js, "emRefresh")


def test_settings_contents_per_page_and_entities_at_their_function():
    js = script()
    rs = source(js, "renderSettings")
    # Haus: EMS-ESP, Haus (Wohnfläche, Auslegung), Gas & Kosten; Kessel: Kesseldaten mit max. Vorlauf (0.58)
    haus = between(rs, 'part("haus", sugBtn(', 'part("raeume"')
    for f in ("f-emsesp_host", "f-living_area_m2", "f-design_outdoor_temp", "f-gas_calorific_value", "kwh-hint"):
        assert f in haus, f
    kessel = between(rs, 'part("kessel", card("Kesseldaten"', 'part("haus", card("Benachrichtigungen"')
    for f in ("f-boiler_nominal_kw", "f-boiler_min_power_pct", "f-max_flow_temp", "boilerSectionHtml(s, BOILC)", 'emInline("kessel_hp")'):
        assert f in kessel, f
    # E3: Schreib-Entitäten bei der Funktion, gelesene Werte unter „Experte“
    assert 'emInline("warmwasser")' in between(rs, 'part("kessel", card("Warmwasser steuern"', 'part("kessel", card("Kesseldaten"')
    assert 'const PAGE_GROUPS = { raeume: ["raeume"], kessel: ["kessel_hp", "warmwasser", "kessel", "regler"],' in js
    assert 'const EXPERT_GROUPS = { raeume: [], kessel: ["kessel", "regler"], haus: ["aussen", "gas", "strom"] };' in js
    assert 'openSettings(EM_PAGE_OF[group] || "haus", "em-g-" + group);' in source(js, "emGo")
    # Steuerung in Karten, „Im Modus Beobachten“ zugeklappt mit den alten Feldern aus „Komfort & Strategie“
    cc = source(js, "controlCard")
    assert re.findall(r'card\("([^"]+)"', cc) == ["Räume steuern", "Temperaturen", "Nachtzeiten", "Ventile führen"]
    obs = between(cc, '<details class="fold" id="ctl-observe"><summary>Im Modus Beobachten</summary>', "</details>")
    assert all(f in obs for f in ("f-night_start", "f-night_end", "f-learn_schedule"))
    # Warnhinweis „Zurück zu Tado“ nur bei gewähltem „HeatPilot steuert“; Kopfsatz der Bereitschaft mit Link nach Diagnose
    assert '<div class="warnbox" id="mode-warn"${mode === "heatpilot" ? "" : " hidden"}><details class="more"><summary>Zurück zu Tado' in cc
    assert '$("mode-warn").hidden = !(m && m.value === "heatpilot")' in source(js, "modeHint")
    assert 'onclick="openReadiness();return false">Einzelheiten ›</a>' in source(js, "renderReadyBox")
    # E2: einheitliche Beschriftung, Backend-Werte bleiben
    for name in ("DHW_LABEL", "BOILER_LABEL"):
        assert '{ aus: "Aus", trocken: "Beobachten", heatpilot: "HeatPilot steuert" }' in source(js, name)
    # Räume als Liste: .room statt tr.room an allen Lesestellen
    assert "tr.room" not in js and '<div class="room" data-climate="${esc(c.id)}"' in source(js, "roomRows")
    assert '<details class="room-more"><summary>Einzelheiten</summary>' in source(js, "roomRows") and '<div class="r-devs"></div>' in source(js, "roomRows")


def test_diagnose_page_order_and_limits():
    page, js = html(), script()
    diag = between(page, '<div id="view-diag">', "\n  </div>\n")
    titles = re.findall(r"<h2>([^<]+)</h2>", diag)
    assert titles == ["Selbsttest", "Bereit für die Steuerung?", "Modelle", "Modelle – Einzelheiten", "Protokoll", "Alle Sensoren", "Log", "Dateien"]
    assert diag.count("Nur Anzeige") == 1 and '<pre id="disc"></pre>' in diag  # Gefundene Entitäten zugeklappt hier
    assert "Nur Anzeige" not in source(js, "dhwReadyHtml") and "docs/planung" not in source(js, "boilerHtml")
    assert "const LOG_MAX = 5, LOG_CUT = 200;" in js and "const SENSOR_MAX = 50" in js
    assert 'openDiag("ready-card");' in source(js, "openReadiness")


def test_tokens_in_both_themes_and_no_duplicate_dark_line():
    c = css()
    light = c.split("@media (prefers-color-scheme: dark)")[0]
    dark_media = between(c, "@media (prefers-color-scheme: dark)", ':root[data-theme="dark"]')
    dark_attr = between(c, ':root[data-theme="dark"] {', "}")
    for t in ("--err:", "--err-text:", "--warn-text:", "--good-text:", "--accent-text:", "--surface-2:"):
        assert t in light and t in dark_media and t in dark_attr, t
    assert dark_media.count("--heat-bg:") == 1  # doppelte Zeile im Dunkelblock entfernt
    for t in ("--s1: 4px", "--s2: 8px", "--s3: 12px", "--s4: 16px", "--s5: 24px"):
        assert t in light, t
    new = c.split("/* 0.43 Roundup – Bausteine")[1]
    root = between(html(), ":root {", "}")
    tokens = set(re.findall(r"var\((--[\w-]+)\)", new))
    assert tokens and all(t + ":" in root for t in tokens), tokens  # nur Farben, die es in beiden Themen gibt


def test_charts_clipped_one_axis_and_empty_keeps_card(tmp_path):
    c, js = css(), script()
    assert not re.search(r"svg \{[^}]*overflow: visible", c)  # Grafen ragen nicht mehr über ihre Karte
    fr = source(js, "frame")
    assert 'el("clipPath", { id: clip }' in fr and '"clip-path": `url(#${clip})`' in fr and "root: svg" in fr
    assert "svg = svg.ownerSVGElement || svg;" in source(js, "scrub")  # Maße vom svg, nicht von der Gruppe
    assert "valveChart" in js and "right:" not in source(js, "valveChart")  # eine y-Achse
    bar = source(js, "barChart")
    assert "24), 2);" in bar and "barPath(" in bar  # höchstens 24 px, runde Spitze, gerade Grundlinie
    assert source(js, "barPath").count("Q") == 2
    em = source(js, "empty")
    assert "hidden = true" not in em and "showCard(container);" in em
    # in Node: Karte bleibt sichtbar, Text steht drin
    r = run_js(tmp_path, source(js, "showCard") + "\n" + em + """
      const card = { hidden: false }, box = { innerHTML: "", closest: () => card };
      empty(box, "Noch keine Daten.");
      out({ hidden: card.hidden, html: box.innerHTML });""")
    assert r["hidden"] is False and "Noch keine Daten." in r["html"]


def test_heatmap_legend_fits_narrow_charts(tmp_path):
    r = run_js(tmp_path, """
      const el = (name, attrs, parent) => { const n = { name, attrs, children: [] }; if (parent) parent.children.push(n); return n; };
      const draw = (o, W) => { const svg = { children: [] }; const more = heatLegend(svg, 100, o, 0, 10, 10, W);
        const t = svg.children.find((n) => n.name === "text" && n.textContent === "keine Daten");
        const sw = svg.children.find((n) => n.name === "rect" && n.attrs.height === 12).attrs.width;
        const right = Math.max(...svg.children.map((n) => (n.attrs.x || 0) + (n.attrs.width || 0)));
        return { more, y: t.attrs.y, x: t.attrs.x, sw, right }; };
      out({ wide: draw({ diverging: true, unit: "K" }, 520), narrow: draw({ diverging: true, unit: "K" }, 324),
            seq: draw({ unit: "kW" }, 324), old: draw({ unit: "kW" }, Infinity) });""", "const getComputedStyle = () => ({ getPropertyValue: () => \"#2a78d6\" });\n")
    assert r["wide"]["more"] == 0 and r["wide"]["sw"] == 16  # breit wie bisher
    n = r["narrow"]
    assert n["more"] == 18 and n["sw"] < 16 and n["x"] == 30 + 16 + 6 and n["y"] == 128  # zweite Zeile, links
    assert n["right"] <= 324
    assert r["seq"]["more"] == 18 and r["old"]["more"] == 0


def test_building_blocks(tmp_path):
    r = run_js(tmp_path, """out({
      more: more("Kurz.", "Lang <b>x</b>."), single: more("Nur kurz."),
      st: statusLine("err", "Fehler", '<a href="#">Protokoll ›</a>', "x1"), off: statusLine("off", "Aus"),
      nav: navRow("", "Kessel", "4 Starts heute", "58 °C", "openOverview('kessel')"),
      seg: seg([["3", "3 h"], ["12", "12 h"]], "12", "setHours"),
      kpi: kpi("Wärmeverlust", "200", "W/K", "Leistung je Grad <b>Unterschied</b>"), none: kpi("Vorhersage", "–", "°C"),
      eid: eid('sensor.a"<b>') });""")
    assert r["more"] == '<details class="more"><summary>Kurz. <span class="m"></span></summary><div>Lang <b>x</b>.</div></details>'
    assert r["single"] == '<p class="more-p">Nur kurz.</p>'
    assert r["st"].startswith('<div class="st st-err" id="x1"><span class="dot dot-err"></span><span class="st-t">Fehler')
    assert '<span class="st-a"><a href="#">Protokoll ›</a></span>' in r["st"] and '<span class="dot"></span>' in r["off"]
    assert plain(r["nav"]) == "Kessel 4 Starts heute 58 °C ›" and "onclick=\"openOverview('kessel')\"" in r["nav"]
    assert re.findall(r'aria-pressed="(\w+)"', r["seg"]) == ["false", "true"] and 'class="on" onclick="setHours(\'12\')"' in r["seg"]
    assert 'title="Leistung je Grad Unterschied"' in r["kpi"] and plain(r["kpi"]).startswith("Wärmeverlust 200 W/K")
    assert 'class="kpi off"' in r["none"] and "°C" not in r["none"]
    assert r["eid"] == '<code class="eid" title="sensor.a&quot;&lt;b>">sensor.a&quot;&lt;b></code>'
    c = css()
    assert "details.more .m::before { content: \"mehr\"; }" in c and "details.more[open] .m::before { content: \"weniger\"; }" in c
    assert "code.eid { display: inline-block; max-width: 100%; vertical-align: bottom; white-space: nowrap;" in c


# ------------------------------------------------------------------ Schritt 2: Heizen ohne Systemnachrichten
def test_heat_tab_shows_only_line_actions_and_rooms():
    page, js = html(), script()
    heat = between(page, '<section id="view-heat">', "</section>")
    ids = re.findall(r'id="([\w-]+)"', heat)
    assert ids == ["heat-status", "heat-acts", "heat-now", "heat-rooms", "heat-foot", "heat-dlg", "heat-dlg-body"]  # 0.62: Nur jetzt
    assert "Antippen zum Bedienen." in heat and "onclick=\"openOverview('status')\"" in heat
    render = between(js, "function renderHeat()", "function act(")
    for word in ("renderDhwLine", "renderBoilerLine", "readyLine", "warnings", "heat-dhw", "heat-boiler"):
        assert word not in render, word
    assert "renderOvStatus();" in render and "renderHeatLog(c, j.dhw);" in render
    assert 'tiles.style.setProperty("--cols", k <= 4 ? Math.max(k, 1) : k <= 6 ? 3 : 4);' in render
    # Protokoll unter Live → Diagnose, die Meldung als Einblendung
    diag = between(page, '<div id="view-diag">', "\n  </div>\n")
    assert '<details id="heat-log-box" hidden>' in diag and '<table id="heat-log"' in diag
    assert '<div class="toast" id="heat-msg" role="status" aria-live="polite" hidden></div>' in page
    assert "min-height" not in between(page, '<section id="view-heat">', "</section>")


def test_overview_status_section_is_first_and_complete():
    page, js = html(), script()
    ov = between(page, '<section id="view-overview" hidden>', "</section>")
    assert between(ov, '<div id="ov-start">', "</div>").lstrip().startswith('<div class="card ov-status" id="ov-status">')
    heads = re.findall(r'<h3 class="st-h">([^<]+)</h3>', between(ov, 'id="ov-status"', '<p class="sub" id="status">'))
    assert heads == ["Räume", "Kessel und Warmwasser", "Zuletzt geschrieben"]  # 0.58: Wetter nur in der Wetter-Karte
    assert '$("ov-bars").innerHTML = ovBarsHtml(j);' in source(js, "renderOvStatus")
    assert 'if (!$("view-overview").hidden) loadOvNow()' in source(js, "loadHeat")
    st = source(js, "renderOvStatus")
    assert 'renderDhwLine("ov-dhw", j.dhw); renderBoilerLine("ov-boiler", j.boiler);' in st  # „wieder einschalten“ dort
    assert "ovRoomsHtml(c, j.readiness || (sum && sum.readiness), sum)" in st and "ovLastHtml(c, d)" in st
    assert "renderOvStatus(false);" in between(js, "async function load() {", '$("tiles").innerHTML')
    # Status bleibt frisch: api/heat auch, solange die Übersicht offen ist
    assert '!$("view-heat").hidden || !$("view-overview").hidden' in source(js, "loadHeat")
    assert 'if (name === "heat" || name === "overview") loadHeat()' in source(js, "showTab")
    assert "[hidden] + .st-fallback { display: block; }" in css()  # ohne Zeile: „aus“ mit dem Text des Backends


ROOM = {"climate": "climate.wz", "name": "Wohnzimmer Heizung", "mode": "auto", "target": 21, "temp": 20.5, "managed": True,
        "modes": ["auto", "heat", "off"], "devices": []}


def test_room_states_follow_who_and_setpoint(tmp_path):
    ctl = {"phase": "nacht", "desired": 18, "manual": False, "paused": False}
    cases = {
        "zeitplan": ({"mode": "beobachten"}, {}),
        "manuell": ({"mode": "beobachten"}, {"mode": "heat"}),
        "frost": ({"mode": "beobachten"}, {"mode": "off"}),
        "boost": ({"mode": "beobachten"}, {"boost": {"left_min": 45}}),
        "ems": ({"mode": "heatpilot"}, {"ems": True}),
        "heatpilot": ({"mode": "heatpilot"}, {"control": ctl}),
        "vorheizen": ({"mode": "heatpilot"}, {"control": {**ctl, "phase": "vorheizen", "desired": 21}}),
        "hand": ({"mode": "heatpilot"}, {"control": {**ctl, "manual": True, "manual_until": "2026-10-20T18:00:00+02:00"}}),
        "aus": ({"mode": "heatpilot"}, {"mode": "off", "control": ctl}),
        "pause": ({"mode": "heatpilot"}, {"control": {**ctl, "paused": True}}),
        "boost-hp": ({"mode": "heatpilot"}, {"boost": {"left_min": 75}, "control": ctl}),
    }
    r = run_js(tmp_path, f"""const R = {json.dumps(ROOM)}, C = {json.dumps(cases)};
      out(Object.fromEntries(Object.entries(C).map(([k, [c, x]]) => {{ HEAT = {{ control: c }}; return [k, roomState({{ ...R, ...x }})]; }})));""")
    assert all(__import__("re").search(r" bis \d\d:\d\d$", r[k]) for k in ("boost", "boost-hp"))  # 0.52.3: Endzeit
    r = {k: __import__("re").sub(r" bis \d\d:\d\d$", "", v) if k.startswith("boost") else v for k, v in r.items()}
    assert r == {"zeitplan": "Zeitplan · 21°", "manuell": "Manuell · 21°", "frost": "Frostschutz", "boost": "Boost · 21° · 0:45",
                 "ems": "Nur Anzeige · 21°", "heatpilot": "HeatPilot · Nacht 18°", "vorheizen": "Vorheizen → 21°",
                 "hand": "Manuell · 21° bis 18:00", "aus": "Aus", "pause": "Pause · 21°", "boost-hp": "Boost · 21° · 1:15"}
    assert "Timer" not in source(script(), "roomState")  # „Timer“ heißt überall „Boost“


def test_heat_line_is_one_short_line(tmp_path):
    soon = "2099-01-01T22:00:00+01:00"
    r = run_js(tmp_path, f"""out({{
      watch: heatStatus({{ mode: "beobachten", status_text: "Beobachten" }}),
      night: heatStatus({{ mode: "heatpilot", status_text: "HeatPilot steuert · Nacht bis 04:30 · nächste Änderung 03:30 Vorheizen", next_change: "{soon}", next_phase: "vorheizen" }}),
      day: heatStatus({{ mode: "heatpilot", status_text: "HeatPilot steuert · nächste Änderung 22:00 Nacht", next_change: "{soon}", next_phase: "nacht" }}),
      paused: heatStatus({{ mode: "heatpilot", status_text: "HeatPilot pausiert · Nacht bis 04:30", paused_until: "2026-10-20T16:00:00+02:00" }}),
      vac: heatStatus({{ mode: "beobachten", status_text: "Beobachten" }}, {{ active: true, until: "2026-10-24T18:00:00+02:00" }}),
      evil: heatStatus({{ mode: "beobachten", status_text: "<b>x" }}) }});""")
    assert plain(r["watch"]) == "Beobachten ›" and '<span class="dot"></span>' in r["watch"]
    assert plain(r["night"]) == "HeatPilot steuert · Nacht bis 04:30 ›" and "dot-ok" in r["night"]
    assert plain(r["day"]) == "HeatPilot steuert · ab 22:00 Nacht ›"
    assert plain(r["paused"]) == "HeatPilot pausiert bis 16:00 · Nacht bis 04:30 ›" and "dot-warn" in r["paused"]
    assert plain(r["vac"]) == "Beobachten · Urlaub bis Sa., 18:00 ›"
    assert "<b>x" not in r["evil"]
    for h in r.values():  # nie Warmwasser, Kessel, Bereitschaft oder Warnungen
        assert not re.search(r"Warmwasser|Kessel|bereit|⚠", plain(h))


def test_overview_status_rooms_warnings_and_last_write(tmp_path):
    c = {"mode": "heatpilot", "status_text": "HeatPilot steuert · Nacht bis 04:30", "managed_rooms": 4,
         "warnings": ["Bad: 3 Schreibfehler in Folge – Thermostat prüfen"],
         "log": [{"time": "2026-10-20T03:30:00+02:00", "kind": "room", "name": "Wohnzimmer Heizung", "action": "set_temperature",
                  "value": 18.0, "reason": "nacht"},
                 {"time": "2026-10-20T03:00:00+02:00", "kind": "dhw", "name": "Warmwasser-Soll", "action": "check_failed", "value": 50}]}
    rd = {"level": "eingeschränkt", "headline": "Läuft – eingeschränkt: Küche lernt noch."}
    r = run_js(tmp_path, f"""const C = {json.dumps(c)}, RD = {json.dumps(rd)};
      HEAT = {{ rooms: [{{ ems: false }}, {{ ems: false }}, {{ ems: false }}, {{ ems: false }}, {{ ems: false }}, {{ ems: true }}] }};
      out({{ rooms: ovRoomsHtml(C, RD, {{ vacation: {{ active: false }} }}), watch: ovRoomsHtml({{ mode: "beobachten", status_text: "Beobachten" }}, null, null),
            last: ovLastHtml(C, null), fail: ovLastHtml({{ log: C.log.slice(1) }}, null), none: ovLastHtml({{ log: [] }}, null) }});""")
    rooms = r["rooms"]
    assert plain(rooms).startswith("HeatPilot steuert · Nacht bis 04:30 · 4 von 5 Räumen machen mit")
    assert "Einschränkungen: Küche lernt noch. Einzelheiten ›" in plain(rooms)
    assert '<div class="st st-err"><span class="dot dot-err"></span><span class="st-t">⚠ Bad: 3 Schreibfehler' in rooms  # rot
    assert "was du von Hand einstellst" not in plain(rooms)  # 0.55: ohne Erklärsatz
    assert plain(r["watch"]) == "Beobachten"  # 0.55: ohne Erklärsatz, ohne „machen mit“
    assert plain(r["last"]) == "Di., 03:30 · Wohnzimmer · Soll 18 °C (nacht) Protokoll ›"
    assert "openDiag('heat-log-box')" in r["last"] and "st-off" in r["last"]
    assert "st-err" in r["fail"] and "nicht übernommen 50 °C" in plain(r["fail"])
    assert plain(r["none"]) == "Noch nichts geschrieben. Protokoll ›"


def test_dialogs_short_texts_blue_primary_and_room_dialog_without_valve(tmp_path):
    js = script()
    pause = source(js, "pauseDlg")
    assert 'more("HeatPilot schreibt in der Pause nichts – was eingestellt ist, bleibt.",' in pause
    assert 'class="btn primary big" onclick="heatPost(\'pause\', { hours: 0 }' in pause and '"btn big"' not in pause
    assert '<button class="btn primary big" id="boost-start"' in source(js, "boostDlg") and "#boost-start { background" not in css()
    assert 'more("Endet nach Ablauf von selbst.", local' in source(js, "syncBoost")
    assert 'more("Alle Räume auf Aus (Frostschutz 5 °C). Zurück über „Zeitplan“.",' in source(js, "renderDlg")
    assert "dlgValveChart" not in js
    r = run_js(tmp_path, f"""HEAT = {{ control: {{ mode: "heatpilot" }} }};
      out({{ free: roomDlg({json.dumps({**ROOM, "devices": [{"name": "A", "temp": 20}, {"name": "B", "temp": 21, "controller": True}],
                                         "valve_model": {"kind": "stetig", "text": "x"}})}), boost: roomDlg({json.dumps({**ROOM, "boost": {"left_min": 30}})}) }});""")
    free = r["free"]
    assert '<div class="seg seg-b modes" role="group" aria-label="Betriebsart">' in free
    assert re.findall(r'aria-pressed="(\w+)"', free) == ["true", "false", "false"]  # Zeitplan gewählt = blau
    assert "Ventilverhalten" not in free.split("Verlauf und Ventilverhalten")[0]  # kein Graf mehr im Dialog
    assert "openOverview('raeume', 'climate.wz')" in free and 'id="dlg-history"' in free
    assert '<details class="fold dlg-dev"><summary>Geräte <span class="muted">(2)</span></summary>' in free  # zugeklappt
    assert "Boost läuft noch 30 min" in plain(r["boost"]) and "Timer" not in r["boost"]


def test_toast_only_in_heat_tab_and_dialog_message_in_dialog(tmp_path):
    r = run_js(tmp_path, """
      $("heat-dlg").open = false; $("view-heat").hidden = false; $("dlg-msg").innerHTML = "";
      heatMsg('<span class="msg-ok">Erledigt.</span>');
      const toast = { hidden: $("heat-msg").hidden, dlg: $("dlg-msg").innerHTML };
      $("heat-dlg").open = true; heatMsg("Starte Boost …");
      const inDlg = { hidden: $("heat-msg").hidden, dlg: $("dlg-msg").innerHTML };
      $("heat-dlg").open = false; $("view-heat").hidden = true; heatMsg("x");
      out({ toast, inDlg, other: $("heat-msg").hidden });""")
    assert r["toast"] == {"hidden": False, "dlg": ""}
    assert r["inDlg"] == {"hidden": True, "dlg": "Starte Boost …"}
    assert r["other"] is True  # nicht über Übersicht, Live oder Einstellungen


def test_jumps_go_through_open_settings_and_open_overview():
    js = script()
    os_ = source(js, "openSettings")
    assert 'setPage(page || "menu");\n  showTab("settings");\n  reveal(anchor);' in os_
    assert "a.scrollIntoView" in source(js, "reveal") and 'showTab("live");' in source(js, "openDiag")
    for fn, call in (("dhwOpenSettings", 'openSettings("kessel")'), ("boilerOpenSettings", 'openSettings("kessel")'),
                     ("openAdvanced", 'openSettings("haus", "exp-haus")'), ("openAliases", 'openSettings("raeume")'),
                     ("emRoom", 'openSettings("raeume")'), ("dhwOpenReady", 'openDiag("ready-dhw")'),
                     ("boilerOpenReady", 'openDiag("ready-boiler-hp")')):
        assert call in source(js, fn), fn
    oo = source(js, "openOverview")
    assert "const [p, a0] = OV_ANCHOR[page] || [page, anchor];" in oo and "ovPage(p);" in oo
    assert 'const OV_ANCHOR = { status: ["start", "ov-status"], raeume: ["raeume", "ov-rooms"], ersparnis: ["haus", "ov-save"] };' in js


# ---------------------------------------------------------------- Schritte 8–10: Begriffe, Kurztexte, Abnahme (7.2)

def _code(js: str) -> str:
    """Skript ohne Zeilenkommentare – geprüft werden nur sichtbare Texte und Code, Kommentare dürfen alte Wörter behalten."""
    return re.sub(r"(^|\s)//[^\n]*", r"\1", js)


def test_data_ids_exist_exactly_once():
    """Jede ID, die Daten trägt, steht genau einmal in der Datei (statisch oder im Template) – nichts doppelt, nichts weg."""
    page = html()
    for i in ("ready-box", "em-persons", "heat-log", "disc", "rooms", "plan", "week", "dhw-tiles", "flowstudy", "pv-hours",
              "vd", "cmp", "chart-savings", "ov-status", "ov-valve", "ov-dhw", "ov-boiler", "diag-models"):
        assert len(re.findall(r'id="%s"' % re.escape(i), page)) == 1, i
    # notify-list: Tabelle oder Leertext – zwei Zweige einer Bedingung, gerendert genau einer
    nl = source(script(), "notifyHtml")
    assert nl.count('id="notify-list"') == 2 and '(rows ? `<div class="scroll"><table class="form notify-t" id="notify-list">' in nl


def test_rendered_texts_without_internals():
    """Sichtbare Texte nennen keine Versionen, Doku-Pfade, „Weg W/B“ oder Dienstaufrufe."""
    js = script()
    for name in ("boilerHtml", "readyBoxHtml", "notifyHtml", "dhwSectionHtml", "boilerSectionHtml", "expert"):
        text = _code(source(js, name))
        for pat in (r"seit 0\.\d", r"docs/", r"Weg [WB]", r"number\.set_value", r"switch\.turn_on"):
            assert not re.search(pat, text), (name, pat)


def test_terms_and_short_texts():
    """Begriffe aus Abschnitt 2 und Kurztexte aus Abschnitt 5 des Umbauplans."""
    js, page = _code(script()), html()
    # „Antipendelzeit“ seit 0.48 wieder: Einstellung „Antipendelzeit anpassen“ (so vom Nutzer gewünscht)
    for old in ("Wunschsoll", "Wunschzustand", "Legionellenschaltung", "Timer ·", "Sonderladung",
                "Heizbedarf", "Wärmebedarf", "Soll Kessel", "Soll Regler", "Trockenlauf gegen", "Vorschau (Trockenlauf)",
                "im Reiter Heizen)", "Protokoll (Reiter Heizen)"):
        assert old not in js, old
    assert "Trockenlauf" not in re.sub(r"/\*.*?\*/", "", page.split("<script>")[0], flags=re.S)  # Markup ohne CSS-Kommentare
    assert '"Vorlauf-Soll (Kessel)"' in js and '"Vorlauf-Soll (Regler)"' in js
    assert "<h2>Vorschau (24 h)</h2>" in page and "Vorschau gegen Ist (7 Tage)" in page
    assert 'const DHW_LABEL = { aus: "Aus", trocken: "Beobachten", heatpilot: "HeatPilot steuert" };' in js
    for short in ("Häkchen = HeatPilot lernt den Raum.", "HeatPilot verstellt das Soll um höchstens einen Schritt (0,5 K)",
                  "Ein Regler am Bus überschreibt das Vorlauf-Soll – „HeatPilot steuert“ bleibt dann gesperrt.",
                  "Zurück zu Tado nur über diesen Schalter, danach in der Tado-App den Zeitplan fortsetzen.",
                  "HeatPilot setzt das Warmwasser-Soll am Kessel – nur so warm wie nötig.",
                  "Einmal pro Woche heizt der Kessel den Speicher über 60 °C auf.",
                  "Plan der Stunde gegen das, was Regler und Automation eingestellt hatten.",
                  "Was du an Tado oder den Ventilen einstellen kannst. HeatPilot ändert hier nichts selbst.",
                  "Wann Warmwasser gebraucht wird – dann hält HeatPilot es bereit.",
                  "Gas gegen Temperaturunterschied innen–außen; die Linie ist das gelernte Modell.",
                  "Bringt ein höherer Vorlauf etwas?", "wie genau HeatPilot die Ankunft vorhergesagt hat",
                  "Nur Anzeige – schaltet nichts."):
        assert short in page, short
    # der lange Text bleibt hinter „mehr“ erhalten
    assert "Ein Regler am EMS-Bus überschreibt alle 60 s" in js and "Einmal je Woche heizt der Kessel den Speicher" in js


def test_quality_badge_three_levels(tmp_path):
    """Modellgüte: ein Abzeichen mit drei Stufen – lernt noch · vorläufig · verlässlich; Lernstand, Räume und Ventile darüber."""
    js = script()
    r = run_js(tmp_path, 'out({ l: qualityBadge("learn"), p: qualityBadge("prelim", "t"), o: qualityBadge("ok", "", "Haus"),'
                         ' x: qualityBadge("?") });', extra=source(js, "QUALITY") + "\n" + source(js, "qualityBadge"))
    assert r["l"] == '<span class="badge q-learn info">lernt noch</span>'
    assert r["p"] == '<span class="badge q-prelim warn" title="t">vorläufig</span>'
    assert r["o"] == '<span class="badge q-ok ok">Haus · verlässlich</span>' and "lernt noch" in r["x"]
    assert 'qualityBadge(ok ? "ok" : "learn", title, name)' in js            # Lernstand
    assert 'const learn = qualityBadge("learn"' in js                        # Übersicht → Räume
    assert 'const state = v.reliable ? qualityBadge("ok")' in js             # Ventilverhalten


def test_version_changelog_and_docs():
    """Version 0.43.0, Changelog oben, ohne persönliche Werte; Doku nennt die neuen Orte."""
    from pathlib import Path

    from heatpilot import __version__
    root = Path(__file__).resolve().parents[1]
    assert __version__ >= "0.43.0"  # seit 0.43.1 prüft test_web_cache431 die genaue Version
    assert f'version: "{__version__}"' in (root / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "\n## 0.43.0\n**Oberfläche aufgeräumt**" in log
    head = log.split("\n## 0.43.0\n")[1].split("\n## ")[0]
    assert "tests/test_ui_v43.py" in head and "Beobachten" in head
    for personal in ("Ingo", "°C seit", "kWh", "climate."):  # keine persönlichen Werte
        assert personal not in head, personal
    docs = (root / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
    assert "**Aufbau der Oberfläche (seit 0.43):**" in docs and "Reiter **Diagnose" not in docs
    assert "Einstellungen → Haus & Kessel" not in docs and "Protokoll im Heizen-Reiter" not in docs
    assert "**Oberfläche (seit 0.58):**" in (root / "README.md").read_text(encoding="utf-8")
