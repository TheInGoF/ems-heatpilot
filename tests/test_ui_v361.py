"""0.36.1: Oberfläche zu Vorhersage, „Bereit für die Steuerung?“ und Kessel über EMS-ESP – statische Prüfung.

Wie tests/test_ui_v36.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird, dass die neuen Teile
angebunden sind (Kachel neben „Haus-Trägheit“, Zeile im Vorheizplan, Hilfetext Wetter, Zustandsbox über dem
Schalter, Statuszeilen, Badge) und das Inline-Skript gültiges JavaScript ist. Die reinen Anzeige-Funktionen
laufen zusätzlich in Node mit Beispieldaten – so ist der fertige Text geprüft, nicht nur der Quelltext.
Die Wochenraster (Farbstufen, Legende) prüft der letzte Test. Verhalten im Browser prüft das
Playwright-Skript (Screenshots), das nicht Teil der Suite ist.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "ems_heatpilot"
STATIC = Path(os.environ.get("HEATPILOT_STATIC", ROOT / "heatpilot" / "static"))
NODE = shutil.which("node") or next((p for p in ("/opt/node22/bin/node",) if Path(p).exists()), None)


def html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def script() -> str:
    return re.search(r"<script>(.*)</script>", html(), re.S).group(1)


def between(js: str, start: str, end: str) -> str:
    assert start in js, start
    return js.split(start)[1].split(end)[0]


# ------------------------------------------------------------------ Funktionen einzeln in Node
def _skip_string(js: str, i: int) -> int:
    """Index nach dem String/Template, der bei ``i`` beginnt (``${…}`` im Template wird mitgezählt)."""
    q, i = js[i], i + 1
    while js[i] != q:
        if js[i] == "\\":
            i += 1
        elif q == "`" and js.startswith("${", i):
            i = _skip_code(js, i + 2, "}")
            continue
        i += 1
    return i + 1


def _skip_code(js: str, i: int, close: str) -> int:
    """Index nach ``close`` auf der Ebene, auf der ``i`` beginnt (Strings, Kommentare, Regex übersprungen)."""
    depth, prev = 0, "("
    while True:
        c = js[i]
        if c in "'\"`":
            i, prev = _skip_string(js, i), "a"
            continue
        if js.startswith("//", i):
            i = js.index("\n", i)
            continue
        if js.startswith("/*", i):
            i = js.index("*/", i) + 2
            continue
        if c == "/" and prev in "(,=:[!&|?{};+":  # Regex-Literal
            i += 1
            while js[i] != "/":
                i += 2 if js[i] == "\\" else 1
            i, prev = i + 1, "a"
            continue
        if depth == 0 and c == close:
            return i + 1
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if not c.isspace():
            prev = c
        i += 1


def source(js: str, name: str) -> str:
    """Quelltext einer Funktion oder Konstante auf oberster Ebene."""
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(|^(?:const|let) {re.escape(name)}\b", js, re.M)
    assert m, name
    if not m.group(0).endswith("("):
        return js[m.start():_skip_code(js, m.start(), ";")]  # bis zum ; auf oberster Ebene
    # erst die Parameterliste schließen (Vorgabe-Objekt wie o = {}), dann der Rumpf
    body = js.index("{", _skip_code(js, m.end(), ")"))
    return js[m.start():_skip_code(js, body + 1, "}")]


# Hilfen ohne DOM – Farben wie im hellen und dunklen Thema
STUBS = """
const VARS = { light: { "--series-1": "#2a78d6", "--series-2": "#eb6834", "--series-3": "#1a9641", "--surface-1": "#fcfcfb" },
               dark: { "--series-1": "#3987e5", "--series-2": "#d95926", "--series-3": "#1a9641", "--surface-1": "#1a1a19" } };
let THEME = "light";
const document = { documentElement: {} };
const getComputedStyle = () => ({ getPropertyValue: (k) => VARS[THEME][k] || "" });
const el = (name, attrs, parent) => { const n = { name, attrs, children: [], addEventListener() {} };
  if (parent) parent.children.push(n); return n; };
const showCard = () => {}, empty = () => {}, showTip = () => {}, hideTip = () => {};
let HEAT = null;
"""
NAMES = ("fmt", "esc", "clock", "kelvin", "READY", "ROOM_READY_CSS", "readyDot", "readyRest", "readyLine",
         "trendK", "forecastTile", "planOutdoor", "EMS_UOM", "boilerHtml", "readyBoxHtml", "WEEKDAYS", "SEQ_N", "_lin", "_gam",
         "toOklch", "fromOklch", "seqRamp", "seqStep", "heatmap", "heatLegend")


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


def plain(h: str) -> str:
    """HTML → Text, wie ihn der Nutzer liest."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).replace("&quot;", '"').replace("&lt;", "<").replace("&amp;", "&").strip()


# ------------------------------------------------------------------ Vorhersage
def test_forecast_tile_next_to_inertia_and_grey_without_data(tmp_path):
    js = script()
    body = between(js, "async function load() {", "// ---------------------------------------------------------------- Einstellungen")
    # Kachel direkt hinter „Haus-Trägheit“; ohne plausibles Hausmodell steht sie trotzdem (gemessen, nicht gelernt)
    assert "const fc = forecastTile(s.forecast, (s.discovered || {}).weather);" in body
    assert re.search(r'tile\("Haus-Trägheit".*\n\s+fc,\n', body)
    assert "...(!s.guards.house.ok ? [fc] : [" in body
    r = run_js(tmp_path, """
      const now = new Date("2026-10-19T14:00:00+02:00");
      out({
        none: forecastTile(null, null), nodata: forecastTile(null, "weather.home"),
        empty12: forecastTile({ age_min: 5, points: 3, hours: 0, next12: null, trend_k: null }, "weather.home"),
        ok: forecastTile({ age_min: 20, points: 48, hours: 47, next12: { min: -1, min_at: "2026-10-20T06:00:00+02:00", max: 7.5 }, trend_k: -3 }, "weather.home"),
        old: forecastTile({ age_min: 190, points: 2, hours: 1, next12: { min: 4, min_at: "2026-10-19T15:00:00+02:00", max: 5 }, trend_k: 0 }, "weather.home"),
      });""")
    assert 'class="tile off"' in r["none"] and plain(r["none"]).startswith("Vorhersage keine Wetter-Entität")
    assert 'class="tile off"' in r["nodata"] and "keine Daten" in plain(r["nodata"])
    assert 'class="tile off"' in r["empty12"] and "keine Werte für die nächsten 12 h · Stand vor 5 min" in plain(r["empty12"])
    assert 'class="tile"' in r["ok"] and "off" not in r["ok"].split(">")[0]
    assert "nächste 12 h: min -1 °C um 06:00, Trend −3 K · Stand vor 20 min" in plain(r["ok"])
    assert "Stand vor 3 h" in plain(r["old"]) and "Trend ±0 K" in plain(r["old"])
    # grau über vorhandene Variablen (hell und dunkel)
    assert ".tile.off { background: var(--off-bg); }" in html()


def test_plan_line_says_which_outdoor_temperature(tmp_path):
    js = script()
    body = between(js, "const plan = s.plan;", '$("disc")')
    assert body.count("planOutdoor(plan)") == 2  # machbarer Plan und „kein machbarer Plan“
    assert "</p>${planOutdoor(plan)}<p>Start <b>" in body  # direkt nach der Ankunftszeile
    r = run_js(tmp_path, """out({
      fc: planOutdoor({ t_out_used: -3, t_out_source: "vorhersage", t_out_at: "2026-10-19T17:00:00+02:00" }),
      now: planOutdoor({ t_out_used: 8.4, t_out_source: "aktuell", t_out_at: null }),
      none: planOutdoor(null), old: planOutdoor({ feasible: false }) });""")
    assert plain(r["fc"]) == "Gerechnet mit -3 °C außen (Vorhersage für 17:00)"
    assert plain(r["now"]) == "Gerechnet mit 8,4 °C außen (aktuelle Außentemperatur – keine Vorhersage für den Zeitpunkt)"
    assert r["none"] == "" and r["old"] == ""  # ältere Antwort ohne t_out_used: keine Zeile


def test_weather_field_hint():
    # seit 0.39.1 auf der Seite „Entitäten“ (Gruppe Außen & Wetter): der Zweck kommt aus entity_map
    from heatpilot.entity_map import FIELD_ENTRIES
    group, label, kind, multi, auto, purpose = FIELD_ENTRIES["weather_entity"]
    assert (group, label, kind, multi, auto) == ("aussen", "Wetter", "weather", False, True)
    assert purpose == ("Vorhersage für Vorheizen und Vorschau, Bewölkung für die Sonnenanteile; ohne Außenfühler auch die "
                       "Außentemperatur.")
    assert 'field("Wetter", ent(' not in script()


# ------------------------------------------------------------------ Bereit für die Steuerung?
READY_SAMPLE = {
    "level": "eingeschränkt", "managing": False,
    "headline": "Eingeschränkt bereit – Küche lernt noch (Vorheizen nach Pauschale).",
    "blockers": ["Bad: 3 Schreibfehler in Folge – Thermostat prüfen"], "limits": ["Küche lernt noch"],
    "notes": ["Flur erreicht sein Soll nicht – wird akzeptiert", "Modelle sind jung (40 h)"],
    "rooms": [{"climate": "climate.wz", "name": "Wohnzimmer", "managed": True, "comfort": 21, "state": "bereit", "text": "schreibbar"},
              {"climate": "climate.k", "name": "Küche", "managed": True, "comfort": 20.5, "state": "lernt", "text": "lernt noch"},
              {"climate": "climate.f", "name": "Flur <b>", "managed": True, "comfort": 19, "state": "akzeptiert", "text": "höchstens 17 °C"},
              {"climate": "climate.g", "name": "Gast", "managed": False, "comfort": 20, "state": "nicht dabei", "text": "macht nicht mit"}],
    "counts": {"managed": 3, "writable": 3, "models_ok": 2, "unreachable": 0, "accepted": 1, "learning": 1},
    "boiler": {"configured": False, "polled": False, "polled_at": None, "ready": False,
               "text": "EMS-ESP nicht eingerichtet – ohne EMS-ESP keine Kesselsteuerung",
               "controls": [{"key": "selflowtemp", "name": "Vorlauf-Soll", "required": True, "found": None, "writeable": None,
                             "value": None, "min": None, "max": None, "uom": None}]},
}


def test_ready_box_above_switch_loaded_from_api_heat(tmp_path):
    js = script()
    card = between(js, "function controlCard(s)", "function modeHint()")
    # Zustandsbox über dem Schalter
    assert card.index('<div class="ready-box" id="ready-box"></div>') < card.index('<div class="seg" id="f-control_mode">')
    # Daten aus GET api/heat, nicht blockierend: erst der letzte Stand, dann frisch; Fehler → „Status nicht verfügbar“
    load = between(js, "async function loadReadiness() {", "function renderReadyBox(")
    assert 'await getJSON("api/heat")' in load and "HEAT = j;" in load
    assert "} catch (e) { renderReadyBox(null, e.message || String(e)); }" in load
    assert "renderReadyBox(HEAT && HEAT.readiness); loadReadiness();" in between(js, "function renderSettings()", "function addVac()")
    assert 'else if (name === "settings") loadReadiness();' in between(js, "function showTab(name)", "const isTemp")
    # Antworten von POST api/heat/* behalten readiness
    assert "HEAT = { rooms: j.rooms, limits: j.limits, control: j.control, readiness: j.readiness }" in js
    r = run_js(tmp_path, f"""const R = {json.dumps(READY_SAMPLE)};
      out({{ box: readyBoxHtml(R, "", false, false), open: readyBoxHtml(R, "", true, false),
            err: readyBoxHtml(null, "Keine Verbindung zum Add-on", false, false), wait: readyBoxHtml(null, "", false, false),
            ok: readyBoxHtml({{ ...R, level: "bereit", blockers: [], limits: [], notes: [] }}, "", false, false) }});""")
    box = r["box"]
    assert plain(box).startswith("Bereit für die Steuerung? Eingeschränkt bereit – Küche lernt noch (Vorheizen nach Pauschale).")
    assert '<span class="dot dot-warn"></span>' in box and '<span class="dot dot-ok"></span>' in r["ok"]
    assert '<details id="ready-details" ontoggle=' in box and '<details id="ready-details" open' in r["open"]
    assert "Einzelheiten (3 Räume machen mit · 1 Hindernis · 1 Einschränkung · 2 Hinweise)" in plain(box)
    # Blocker rot, Einschränkungen gelb, Hinweise grau – in dieser Reihenfolge
    assert re.search(r'<li class="block">Bad: 3 Schreibfehler.*<li class="limit">Küche lernt noch</li><li class="note">Flur', box)
    assert "li.block { border-color: #e34948;" in html() and "li.limit { border-color: var(--warn);" in html()
    # Ampel-Klassen eigen benannt: #heat-status .warn macht aus jedem .warn eine eigene Zeile
    assert ".dot.dot-ok { background: var(--good); } .dot.dot-warn { background: var(--warn); }" in html()
    assert 'dot: "dot-ok"' in js and 'dot: "dot-warn"' in js and 'dot: "warn"' not in js
    # Räume mit Zustand; Räume ohne „macht mit“ grau; Namen maskiert
    assert "Flur &lt;b>" in box and "Flur <b>" not in box
    assert '<tr class="muted"><td>Gast</td>' in box
    for state in ("bereit", "lernt", "akzeptiert", "nicht dabei"):
        assert f">{state}</span>" in box
    assert "Räume, die ihr Soll nicht erreichen, heißen „akzeptiert“ – sie sind kein Hindernis." in plain(box)
    assert plain(r["err"]) == "Bereit für die Steuerung? Status nicht verfügbar Keine Verbindung zum Add-on"
    assert plain(r["wait"]) == "Bereit für die Steuerung? Lade Status …"


def test_boiler_block_table_and_traffic_light(tmp_path):
    ctl = lambda key, name, req, **kw: {"key": key, "name": name, "required": req, "found": True, "writeable": True,  # noqa: E731
                                        "value": None, "min": None, "max": None, "uom": None, **kw}
    controls = [ctl("selflowtemp", "Vorlauf-Soll", True, value=45, min=0, max=90, uom="C"),  # Einheit wie EMS-ESP
                ctl("wwseltemp", "Warmwasser-Soll", True, value=52, min=30, max=60, uom="°C", writeable=None),
                ctl("heatingactivated", "Heizbetrieb", True, value=True),
                ctl("wwonetime", "Warmwasser-Einmalladung", True, found=False, writeable=False),
                ctl("burnminperiod", "Antipendelzeit", False, value=10, min=0, max=120, uom="minutes", writeable=False)]
    b = {"configured": True, "polled": True, "polled_at": "2026-10-19T13:05:00+02:00", "ready": False,
         "text": "Es fehlen: Warmwasser-Einmalladung (nicht gefunden)", "controls": controls}
    r = run_js(tmp_path, f"""const B = {json.dumps(b)};
      out({{ warn: boilerHtml(B, false), ok: boilerHtml({{ ...B, ready: true }}, true),
            grey: boilerHtml({{ ...B, configured: false, polled: false, polled_at: null }}, false), none: boilerHtml(null) }});""")
    w = r["warn"]
    assert plain(w).startswith("Kessel über EMS-ESP Es fehlen: Warmwasser-Einmalladung (nicht gefunden) · abgefragt 13:05, stündlich")
    assert '<span class="dot dot-warn"></span>' in w and '<span class="dot dot-ok"></span>' in r["ok"] and '<span class="dot"></span>' in r["grey"]
    assert '<details id="ready-boiler-details" ontoggle=' in w and '<details id="ready-boiler-details" open' in r["ok"]
    assert '<th>Steuerung</th><th>Wert</th><th class="rt-wide">Bereich</th><th>schreibbar</th><th></th>' in w
    # am Handy steht der Bereich unter dem Wert (eigene Spalte ausgeblendet), Einheiten in Kurzform
    assert '45 °C<div class="muted rt-narrow">0 – 90 °C</div></td><td class="rt-wide">0 – 90 °C</td>' in w
    assert "@media (max-width: 520px) { table.ready-t th, table.ready-t td { padding: 4px 4px; }" in html()
    assert "table.ready-t .rt-wide { display: none; } table.ready-t .rt-narrow { display: block; } }" in html()
    assert "Steuerungen (4 nötig, 1 zur Info)" in plain(w)
    rows = [plain(re.sub(r'<div class="muted rt-narrow">.*?</div>', "", x)) for x in re.findall(r"<tr[^>]*><td>.*?</tr>", w)]
    assert rows[0] == "Vorlauf-Soll selflowtemp 45 °C 0 – 90 °C ja nötig"
    assert rows[1] == "Warmwasser-Soll wwseltemp 52 °C 30 – 60 °C unbekannt nötig"
    assert rows[2] == "Heizbetrieb heatingactivated an – ja nötig"
    assert rows[3] == "Warmwasser-Einmalladung wwonetime nicht gefunden – – nötig"
    assert rows[4] == "Antipendelzeit burnminperiod 10 min 0 – 120 min nein Info"
    assert '<tr class="muted"><td>Warmwasser-Einmalladung' in w
    assert ("Nur Anzeige. Was HeatPilot damit tut, steht in der Planung (docs/planung-vorlauf-ems.md). Geschrieben "
            "wird nie über EMS-ESP, nur über Home Assistant und nur mit eigenem Schalter: Vorlauf-Soll und Heizbetrieb "
            "seit 0.41 – siehe „Kessel über HeatPilot“") in plain(w)  # 0.41: Weg B
    assert r["none"] == ""


def test_heat_status_line_and_overview_badge(tmp_path):
    js = script()
    assert "h += readyLine(r, c.mode === \"heatpilot\");" in between(js, "function heatStatus(c, r)", "function readyLine")
    assert '$("heat-status").innerHTML = heatStatus(c, j.readiness);' in js
    # Badge in der Übersicht nur bei Beobachten, Klick führt zu Einstellungen → Steuerung
    body = between(js, "async function load() {", "$(\"tiles\").innerHTML")
    assert 'const readyBadge = s.control_mode !== "heatpilot" && rdv' in body
    assert 'onclick="openReadiness();return false">Steuerung: ${rdv.word}</a>' in body
    assert '</span>${readyBadge} · außen ${fmt(s.t_out)} °C' in body
    hl = {"bereit": "Bereit – 3 Räume schreibbar, 3 mit belastbarem Modell. Flur erreicht sein Soll nicht und wird akzeptiert.",
          "eingeschränkt": "Eingeschränkt bereit – Küche lernt noch (Vorheizen nach Pauschale).",
          "nicht bereit": "Nicht bereit – kein Raum macht mit."}
    pil = {"bereit": "Läuft – 3 Räume schreibbar, 3 mit belastbarem Modell.",
           "eingeschränkt": "Läuft – eingeschränkt: Küche lernt noch (Vorheizen nach Pauschale).",
           "nicht bereit": "Nicht bereit – Bad: 3 Schreibfehler in Folge – Thermostat prüfen."}
    r = run_js(tmp_path, f"""const HL = {json.dumps(hl)}, PIL = {json.dumps(pil)};
      out({{ watch: Object.fromEntries(Object.entries(HL).map(([level, headline]) => [level, readyLine({{ level, headline }}, false)])),
            pil: Object.fromEntries(Object.entries(PIL).map(([level, headline]) => [level, readyLine({{ level, headline }}, true)])),
            none: readyLine(null, false), words: Object.values(READY).map((x) => x.word) }});""")
    w, p = r["watch"], r["pil"]
    link = " Einzelheiten unter Einstellungen → Steuerung"
    assert plain(w["bereit"]) == ("HeatPilot wäre bereit zu steuern – 3 Räume schreibbar, 3 mit belastbarem Modell. "
                                  "Flur erreicht sein Soll nicht und wird akzeptiert." + link)
    assert plain(w["eingeschränkt"]) == "HeatPilot wäre eingeschränkt bereit: Küche lernt noch (Vorheizen nach Pauschale)." + link
    assert plain(w["nicht bereit"]) == "HeatPilot wäre nicht bereit: kein Raum macht mit." + link
    assert 'onclick="openReadiness();return false"' in w["bereit"]
    # bei „HeatPilot steuert“: nichts, wenn alles bereit ist; sonst die Einschränkungen hinter dem Status
    assert p["bereit"] == ""
    assert plain(p["eingeschränkt"]) == "Einschränkungen: Küche lernt noch (Vorheizen nach Pauschale)." + link
    assert plain(p["nicht bereit"]) == "Nicht bereit: Bad: 3 Schreibfehler in Folge – Thermostat prüfen." + link
    assert r["none"] == "" and r["words"] == ["bereit", "eingeschränkt", "nicht bereit"]
    # „Einzelheiten“ öffnet die Einstellungen, klappt die Box auf und merkt sich das auch über ein Neuzeichnen
    op = between(js, "function openReadiness() {", "// Daten aus GET api/heat")
    assert "READY_OPEN = true;" in op and 'showTab("settings");' in op
    rb = between(js, "function renderReadyBox(r, err = \"\") {", "function readyBoxHtml(")
    assert "box.innerHTML = readyBoxHtml(r, err, READY_SHOW.details, READY_SHOW.boiler);" in rb
    assert "if (r || err) READY_OPEN = false;" in rb and 'box.closest("details.card")' in rb


# ------------------------------------------------------------------ Wochenraster
def test_heatmap_has_legend_and_seq_steps(tmp_path):
    js = script()
    assert "const SEQ_N = 7, _ramps = {};" in js
    hm = between(js, "function heatmap(box, grid, o = {}) {", "// Legende unter dem Raster")
    assert "heatLegend(svg, H - legH + 4, o, lo, hi, amax);" in hm
    assert 'if (none) fill = "none";' in hm  # fehlende Daten: nur Rahmen
    assert "breaks: [0.1, 0.25, 0.4, 0.5, 0.65, 0.85]" in js  # Gewohnheiten: feste Grenzen
    r = run_js(tmp_path, """
      const L = (hex) => toOklch(hex)[0];
      const ramps = {};
      for (const t of ["light", "dark"]) { THEME = t; ramps[t] = seqRamp("var(--series-1)").map(L); }
      THEME = "light";
      out({ ramps, n: SEQ_N, steps: [0, 0.09, 0.1, 0.3, 0.5, 0.9, 1].map((v) => seqStep(v, 0, 1, [0.1, 0.25, 0.4, 0.5, 0.65, 0.85])),
            even: [0, 0.5, 0.999, 1].map((v) => seqStep(v, 0, 1)) });""")
    for t, lum in r["ramps"].items():
        assert len(lum) == r["n"] == 7
        # gleichmäßig in der Helligkeit: hell → dunkel im hellen Thema, dunkel → hell im dunklen
        diffs = [b - a for a, b in zip(lum, lum[1:])]
        assert all(d < 0 for d in diffs) if t == "light" else all(d > 0 for d in diffs), (t, lum)
    assert r["steps"] == [0, 0, 1, 2, 4, 6, 6] and r["even"] == [0, 3, 6, 6]


def test_heatmap_draws_cells_legend_and_missing_frames(tmp_path):
    r = run_js(tmp_path, """
      // el() aus den Stubs merkt sich die Kinder: das svg hängt an box
      const box = { innerHTML: "", clientWidth: 520, children: [] };
      const grid = [...Array(7)].map((_, d) => [...Array(24)].map((_, h) => (d === 0 && h < 3) ? null : d * 24 + h));
      const draw = (o, g = grid) => {
        box.children = []; heatmap(box, g, o);
        const svg = box.children[0], rects = svg.children.filter((n) => n.name === "rect");
        return { cells: rects.filter((n) => n.attrs.height === 18 || n.attrs.height === 17).length,
                 missing: rects.filter((n) => n.attrs.fill === "none").length,
                 legend: rects.filter((n) => n.attrs.height === 12).map((n) => n.attrs.fill),
                 texts: svg.children.filter((n) => n.name === "text").map((n) => n.textContent), h: svg.attrs.height };
      };
      // zweifarbig (Abweichung): blau für minus, orange für plus, grau um null
      const dev = grid.map((row) => row.map((v) => v === null ? null : (v - 84) / 10));
      out({ seq: draw({ unit: "m³" }), div: draw({ diverging: true, unit: "K" }, dev),
            blue: seqRamp("var(--series-1)"), orange: seqRamp("var(--series-2)") });""")
    seq, div = r["seq"], r["div"]
    assert seq["cells"] == 7 * 24
    assert seq["missing"] == 3 + 1  # drei Stunden ohne Daten + Legendenfeld „keine Daten“
    assert seq["legend"] == r["blue"] and len(seq["legend"]) == 7  # SEQ_N Stufen, hell → dunkel
    assert "keine Daten" in seq["texts"] and "0 m³" in seq["texts"] and "167 m³" in seq["texts"]
    assert seq["h"] == 7 * 20 + 22 + 18  # Platz für die Legende
    # Legende: SEQ_N Stufen minus (außen am dunkelsten), Mitte grau, SEQ_N Stufen plus
    assert div["legend"] == r["blue"][::-1] + ["var(--grid)"] + r["orange"]
    assert "-8,3 K" in div["texts"] and "+8,3 K" in div["texts"]


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    scripts = re.findall(r"<script>(.*?)</script>", html(), re.S)
    assert len(scripts) == 1
    f = tmp_path / "inline.js"
    f.write_text(scripts[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
