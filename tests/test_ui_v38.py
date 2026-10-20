"""0.38: Oberfläche zu Warmwasser über HeatPilot (Weg W) und Benachrichtigungen – statische Prüfung.

Wie tests/test_ui_v37.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird, dass die neuen Teile angebunden
sind (Statuszeilen in Heizen und Übersicht, Protokoll mit Warmwasser-Zeilen, Schalter und Statusbox in Einstellungen →
Warmwasser, Karte „Benachrichtigungen“, Block in „Bereit für die Steuerung?“, gesendete Felder, Rückfrage beim
Umschalten auf „HeatPilot setzt“) und dass die Oberfläche keinen weiteren Schreibweg kennt. Die Anzeige-Funktionen
laufen in Node mit Antworten des echten Backends (DhwControl.as_dict, readiness.dhw, Notifier, Vorschau) – so ist der
fertige Text geprüft, nicht nur der Quelltext. Verhalten im Browser (Knopf „wieder einschalten“, Testnachricht,
390/1100 px, hell/dunkel) prüft das Playwright-Skript, das nicht Teil der Suite ist.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from heatpilot import dhw_control as dc_mod, notify, preview as pv, readiness
from heatpilot.config import DHW_MODES, Config
from heatpilot.disinfect_status import ACTIONS as DISINFECT_ACTIONS
from heatpilot.controlloop import ControlLoop
from heatpilot.dhw_control import DhwControl
from heatpilot.web import clean

from test_control34 import MON, SUN, run, ts_of
from test_dhw_control38 import ENT, boiler, make, step
from test_preview37 import dhw_model, house
from test_ui_v361 import NODE, between, html, plain, script, source

ROOT = Path(__file__).resolve().parents[1]

# DOM-Ersatz: $() liefert je id ein Objekt; Häkchen der Ziele über CHECKED, Rückfrage über CONFIRM
STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "", className: "", disabled: false, value: "" });
let SET = {}, DHWC = null, NOTIFY = null, HEAT = null, CHECKED = [], CONFIRM = false;
const CONFIRMS = [], confirm = (m) => { CONFIRMS.push(m); return CONFIRM; };
const document = { querySelector: () => null,
  querySelectorAll: (sel) => sel.includes("n-on") ? CHECKED.map((id) => ({ dataset: { id } })) : [] };
"""
NAMES = ("fmt", "esc", "clock", "isoOff", "hhmm", "isoClock", "HP_OFF", "hpOff", "hpDate", "hpClock", "hpDay", "hpDayKey", "hpHours", "hpTicks", "timeOf", "degC", "READY", "readyDot", "ICON", "DROP", "field", "shortName",
         "DHW_LABEL", "DHW_NOTES", "DHW_PAUSE", "dhwStatus", "dhwDecision", "dhwWarnings", "dhwDot", "dhwLine", "renderDhwLine", "dhwEntityHint",
         "dhwBoxHtml", "dhwModeText", "dhwSectionHtml", "dhwConfirm", "dhwReadyHtml", "NOTIFY_KIND", "notifyHtml",
         "notifyState", "notifySync", "ACTION_NAME", "DHW_ACTION", "heatLogRows", "renderHeatLog",
         "PV_NOTE", "PV_WHAT", "PV_C", "PHASE", "WEEKDAYS", "tsOf", "tclock", "wday", "tipKey", "pvWrite", "pvRooms",
         "pvNote", "pvSummary", "pvTip", "pvTable")
SECTION = "// ---------------------------------------------------------------- Warmwasser über HeatPilot (Weg W, 0.38)"


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


def as_json(v):
    return json.loads(json.dumps(clean(v), allow_nan=False))


def t0() -> int:
    """Mo 15:03 – erst im Test gerechnet (nach dem Umstellen der Zeitzone durch ``berlin``)."""
    return ts_of(MON, 15, 3)


def dhw_state(tmp_path, kind: str):
    """Echter Stand von Weg W: (engine, DhwControl, as_dict, readiness.dhw) – wie GET api/dhw."""
    T = t0()
    mode = {"trocken": "trocken", "aus": "aus"}.get(kind, "heatpilot")
    eng, ha, dc = make(tmp_path / kind, mode=mode)
    if kind in ("setzt", "zurueck"):
        step(eng, dc, T, 55 if kind == "zurueck" else 50)
        boiler(eng, 55 if kind == "zurueck" else 50)
        dc.verify(T + 60)
        if kind == "zurueck":
            eng.cfg.dhw_control = "aus"
            step(eng, dc, T + 600, 45)
    elif kind == "trocken":
        step(eng, dc, T, 50)
    elif kind == "pausiert":  # fremde Änderung, dann 3 Fehlschläge in Folge
        step(eng, dc, T, 45)
        boiler(eng, 55)
        step(eng, dc, T + 300, 45)
        boiler(eng, 45)
        dc.verify(T + 360)
        for i in range(3):
            ts = T + 3600 + i * dc_mod.HOLD_S
            step(eng, dc, ts, 50)
            dc.verify(ts + 60)
        assert dc.paused()
    now = T + 3 * 3600 if kind == "pausiert" else T + 900
    return eng, dc, as_json(dc.as_dict(now)), as_json(readiness.dhw(eng, now))


# ------------------------------------------------------------------ Einbau
def test_status_lines_log_and_settings_are_wired():
    page, js = html(), script()
    # 0.43: keine Warmwasser-Zeile mehr im Heizen – sie steht in Übersicht → Status (erster Abschnitt), aus api/heat
    # (renderOvStatus aus renderHeat) und aus api/summary (load)
    assert 'id="heat-dhw"' not in page and '<h3 class="st-h">Warmwasser</h3><p class="sub dhw-line" id="ov-dhw" hidden></p>' in page
    assert page.index('id="ov-status"') < page.index('<p class="sub" id="status">Lade …</p>')
    render = between(js, "function renderHeat()", "function act(")
    assert "renderOvStatus();" in render and "renderHeatLog(c, j.dhw);" in render
    assert 'renderDhwLine("ov-dhw", j.dhw);' in between(js, "function renderOvStatus(lines = true) {", "\n}\n")
    assert "HEAT.dhw = j.dhw; renderHeat();" in between(js, "async function heatPost(", "function heatAction(")
    assert 'renderDhwLine("ov-dhw", s.dhw_control);' in between(js, "async function load() {", '$("tiles").innerHTML')
    rb = between(js, 'function renderReadyBox(r, err = "") {', "function readyBoxHtml(")
    assert 'box.insertAdjacentHTML("beforeend", dhwReadyHtml(r.dhw, READY_SHOW.dhw));' in rb
    assert "DHWC = j.dhw_control || null; NOTIFY = j.notify || null;" in between(js, "async function loadSettings()", "let OV_STALE")
    rs = between(js, "function renderSettings()", "function addVac()")
    # 0.43: Unterseite Warmwasser – Schalter mit den Schreib-Entitäten (E3), dann Temperaturen und Desinfektion
    assert 'page("warmwasser", card("Warmwasser steuern", dhwSectionHtml(s, DHWC) + emInline("warmwasser")) +\n    card("Temperaturen", `<div class="form-grid">' in rs
    # Benachrichtigungen: eigene Unterseite mit der Zeile der Ziele (dieselbe Karte)
    assert '</div>${disinfectHtml(s, DIS)}`)) +' in rs and 'page("benachrichtigungen", card("Benachrichtigungen", emInline("benachrichtigungen")))' in rs
    assert "notify_targets: () => notifyHtml(NOTIFY) };" in js
    assert "dhwModeHint(); notifySync(); dhwScroll();" in rs
    pickers = between(js, "const PICKERS = {", "};")
    assert ('dhw: { filter: (e) => !!(e.dhw && e.dhw.fits), sort: (a, b) => dhwRank(b) - dhwRank(a), '
            'label: "Warmwasser-Soll (number, °C)" }') in pickers and "number:" not in pickers  # nur passende (0.38)
    assert "${ENT.filter(p.filter).sort(p.sort || (() => 0)).map((e) =>" in between(js, "function datalists() {", "const ent =")
    # gesendet: Schalter, Entität (leer = erkannt; seit 0.39.1 über die Seite „Entitäten“), Häkchen – fehlt ein Teil der
    # Seite, bleibt das Gespeicherte
    coll = between(js, "function collect()", "async function saveSettings")
    assert "if (dc) out.dhw_control = dc.value;" in coll
    assert "return emCollect(out);" in coll and "if (!EMAP || EMAP.error || !EMAP.groups) return out;" in js
    assert ('if ($("notify-list")) out.notify_targets = [...document.querySelectorAll("#notify-list input.n-on:checked")]'
            '.map((i) => i.dataset.id);') in coll
    save = between(js, "async function saveSettings()", "function saveNote(j)")
    assert save.index("if (!dhwConfirm(body))") < save.index('await fetch("api/settings"')


def test_ui_knows_no_other_write_path():
    """Die Oberfläche schreibt nichts selbst: „wieder einschalten“ und „Testnachricht“ schicken nur {} – welche
    Entität und welche Ziele, entscheidet das Backend aus den gespeicherten Einstellungen."""
    js = script()
    assert re.findall(r'"api/(?:dhw|notify)[^"]*"', js) == ['"api/dhw/resume"', '"api/notify/test"']
    resume = between(js, "async function dhwResume() {", "// Neuer Stand nach")
    assert 'getJSON("api/dhw/resume", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })' in resume
    test = between(js, "async function notifyTest() {", "function renderSettings()")
    assert 'fetch("api/notify/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })' in test
    assert "set_value" not in between(js, SECTION, "function renderSettings()").replace("number.set_value", "")


def test_texts_match_backend(tmp_path):
    r = run_js(tmp_path, "out({ modes: Object.keys(DHW_LABEL), notes: DHW_NOTES, actions: Object.keys(DHW_ACTION),"
                         " labels: Object.values(DHW_LABEL) });")
    assert r["modes"] == list(DHW_MODES) and r["labels"] == ["Aus", "Beobachten", "HeatPilot steuert"]
    assert r["notes"] == [dc_mod.HINT_AUTOMATION, dc_mod.HINT_FAILSAFE]
    # seit 0.42 auch die Zeilen der Desinfektion (kind „dhw“, disinfect_status.ACTIONS)
    assert set(r["actions"]) == set(dc_mod.ACTIONS) | set(DISINFECT_ACTIONS)
    # die Doku nennt die Stellen in der Oberfläche
    docs = re.sub(r"\s+", " ", (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8"))
    for text in ("Einstellungen → Warmwasser, Schalter „Warmwasser-Soll“", "**Trockenlauf**", "**HeatPilot setzt**",
                 "„wieder einschalten“", "Einstellungen → Entitäten → Benachrichtigungen", "„Testnachricht senden“",
                 "„Warmwasser über HeatPilot“", "beim Speichern mit „HeatPilot setzt“ fragt die Oberfläche einmal danach",
                 "solange geänderte Häkchen nicht gespeichert sind, ist der Knopf gesperrt"):
        assert text in docs, text


# ------------------------------------------------------------------ Statuszeile Heizen/Übersicht
def test_status_line_per_state(tmp_path, berlin):
    d = {k: dhw_state(tmp_path, k)[2] for k in ("setzt", "pausiert", "trocken", "zurueck", "aus")}
    r = run_js(tmp_path, f"const D = {json.dumps(d)};"
                         "out({ ...Object.fromEntries(Object.entries(D).map(([k, v]) => [k, dhwLine(v)])),"
                         " note: dhwLine(D.setzt, 'Wieder eingeschaltet'), none: dhwLine(null) });")
    assert plain(r["setzt"]) == f"Warmwasser-Soll: {d['setzt']['status_text']}"
    assert d["setzt"]["status_text"].startswith("HeatPilot setzt · 50 °C seit 15:03")
    assert '<span class="dot dot-ok"></span>' in r["setzt"] and "<button" not in r["setzt"] and "<a " not in r["setzt"]
    # pausiert: seit wann, Hinweis zur Pause nicht doppelt, der zum fremden Schreiber schon; Knopf und Link
    p = d["pausiert"]
    assert len(p["warnings"]) == 2 and r["pausiert"].count('class="warn"') == 1
    assert plain(r["pausiert"]).startswith(f"Warmwasser über HeatPilot pausiert – 3 Fehlschläge in Folge · seit "
                                           f"{p['paused_since'][11:16]} ⚠ Anderer Schreiber aktiv (zuletzt 45 → 55 °C, "
                                           "vor 3 h)")  # Abstand statt Uhrzeit des Add-ons (0.38)
    assert '<button class="btn small" onclick="dhwResume()">wieder einschalten</button>' in r["pausiert"]
    assert 'onclick="dhwOpenSettings();return false">Einstellungen → Warmwasser</a>' in r["pausiert"]
    assert '<span class="dot dot-warn"></span>' in r["pausiert"]
    assert plain(r["trocken"]) == "Warmwasser-Soll: Beobachten · würde 50 °C seit 15:03"
    assert '<span class="dot"></span>' in r["trocken"]
    # „Aus“: nur mit Hinweis (nach dem Zurückschalten), sonst keine Zeile
    assert plain(r["zurueck"]) == ("Warmwasser-Soll: aus – HeatPilot empfiehlt nur ⚠ HeatPilot schreibt das Warmwasser-Soll "
                                   "nicht mehr – der Kessel behält 55 °C; die HA-Automation ggf. wieder einschalten "
                                   "Einstellungen → Warmwasser")
    assert "<button" not in r["zurueck"] and r["aus"] == "" and r["none"] == ""
    assert r["note"].endswith('<span class="dhw-note">Wieder eingeschaltet</span>')


def test_heat_log_shows_room_and_dhw_rows(tmp_path, berlin):
    eng, dc, d, _ = dhw_state(tmp_path, "pausiert")
    T = t0()
    eng.store.add_control_log(T + 1800, "climate.wz", "set_temperature", 21.0, "Komfort")
    c = as_json(ControlLoop(eng, dc.control).as_dict(T + 3 * 3600))
    kinds = {row["kind"] for row in c["log"]}
    assert kinds == {"room", "dhw"} and len(d["log"]) == 9  # fremd, 4× gesetzt, 3× nicht übernommen, pausiert
    r = run_js(tmp_path, f"""const C = {json.dumps(c)}, D = {json.dumps(d)};
      const rows = heatLogRows(C, D); renderHeatLog(C, D);
      out({{ rows, only: heatLogRows({{ log: [] }}, D), html: $("heat-log").innerHTML, hidden: $("heat-log-box").hidden }});""")
    assert r["rows"] == c["log"][:20] and r["hidden"] is False  # control.log enthält Raum- und Warmwasser-Zeilen
    assert r["only"] == d["log"]  # ohne Regelschleife: die Warmwasser-Zeilen aus dhw.log
    rows = [plain(x) for x in re.findall(r"<tr[^>]*>(.*?)</tr>", r["html"])[1:]]
    assert any(x.endswith("climate.wz Soll 21 °C Komfort") for x in rows)  # Raum ohne Namen: die Entität
    acts = {x.split(" Warmwasser-Soll ")[1].split(" ")[0] for x in rows if " Warmwasser-Soll " in x}
    assert acts == {"pausiert", "nicht", "gesetzt", "fremde"}
    assert any("Warmwasser-Soll nicht übernommen 50 °C Warmwasser-Soll 50 °C nicht übernommen (Kessel zeigt 45 °C)" in x
               for x in rows)
    assert r["html"].count('<tr class="log-dhw">') == sum(" Warmwasser-Soll " in x for x in rows)


# ------------------------------------------------------------------ Einstellungen → Warmwasser
def test_settings_section_switch_entity_and_box(tmp_path, berlin):
    eng, dc, d, rd = dhw_state(tmp_path, "setzt")
    x = {"modes": list(DHW_MODES), "suggested": dc_mod.suggest_entity(eng.found, eng.states), "status": d, "readiness": rd}
    s = as_json(eng.cfg.to_raw())
    own = {**s, "dhw_set_entity": "number.anderes"}
    eng.cfg.dhw_set_entity, eng.dhw_control = "number.anderes", None  # wie nach dem Speichern neu aufgebaut
    rd2 = as_json(readiness.dhw(eng, t0() + 900))
    r = run_js(tmp_path, f"""const S = {json.dumps(s)}, O = {json.dumps(own)}, X = {json.dumps(x)}, R2 = {json.dumps(rd2)};
      out({{ sec: dhwSectionHtml(S, X), own: dhwEntityHint(O, {{ ...X, readiness: R2 }}),
            none: dhwEntityHint({{ ...S, dhw_set_entity: "" }}, {{ ...X, suggested: null, readiness: {{}} }}),
            old: dhwSectionHtml({{ dhw_control: "kaputt" }}, null) }});""")
    sec = r["sec"]
    assert re.findall(r'value="(\w+)" (checked|)', sec) == [("aus", ""), ("trocken", ""), ("heatpilot", "checked")]
    assert [plain(x) for x in re.findall(r"<label>(.*?)</label>", sec)] == ["Aus", "Beobachten", "HeatPilot steuert"]
    # die Entität wird seit 0.39.1 unter „Entitäten“ gewählt – hier ein Verweis mit dem, was gilt
    assert 'id="f-dhw_set_entity"' not in sec
    ref = plain(re.search(r'<p class="em-ref" id="dhw-entity-ref">(.*?)</p>', sec).group(1))
    assert ref == (f"Entität des Warmwasser-Solls: {ENT} (automatisch erkannt). Boiler WW-Soll: jetzt 50 °C · in Home "
                   "Assistant 30–70 °C")  # 0.43: die Zeile zum Ändern steht direkt darunter (emInline)
    warn = plain(re.search(r'<div class="warnbox" id="dhw-warn">(.*?)</div>', sec).group(1))
    assert warn.startswith(f"{dc_mod.HINT_AUTOMATION}. {dc_mod.HINT_FAILSAFE}. Zurück auf „Aus“ oder „Beobachten“: "
                           "HeatPilot schreibt nichts zurück")
    assert f'<b id="dhw-status">{d["status_text"]}</b>' in sec
    assert "zuletzt geschrieben 15:03: 45 → 50 °C · Prüfung 15:04: 50 °C übernommen" in plain(sec)
    assert f"{rd['headline']} Einzelheiten unter „Bereit für die Steuerung?“" in plain(sec)
    assert rd["headline"].startswith("Läuft – eingeschränkt: Minimum 40 °C liegt unter 50 °C")  # Hygiene (0.38)
    # eingestellte Entität, die es nicht gibt; nichts erkannt; unbekannter Schalter → „Aus“, Hinweise wie im Backend
    assert plain(r["own"]) == f"number.anderes (gewählt; Automatik wäre {ENT}). number.anderes fehlt in Home Assistant"
    assert plain(r["none"]).startswith("keine number-Entität fürs Warmwasser-Soll erkannt – bitte wählen")
    assert 'value="aus" checked' in r["old"] and "Status nicht verfügbar" in r["old"]
    assert f"{dc_mod.HINT_FAILSAFE}." in r["old"]


def test_status_box_pause_dry_run_and_mode_hints(tmp_path, berlin):
    _, _, p, rp = dhw_state(tmp_path, "pausiert")
    _, _, t, _ = dhw_state(tmp_path, "trocken")
    r = run_js(tmp_path, f"""const P = {json.dumps(p)}, RP = {json.dumps(rp)}, T = {json.dumps(t)};
      out({{ p: dhwBoxHtml(P, RP), t: dhwBoxHtml(T, null), none: dhwBoxHtml(null, null),
            aus: dhwModeText("aus", "aus", "{ENT}", {{}}), dry: dhwModeText("trocken", "aus", "{ENT}", {{}}),
            back: dhwModeText("trocken", "heatpilot", "{ENT}", {{ current: 55 }}), back2: dhwModeText("aus", "heatpilot", "", {{}}),
            hp: dhwModeText("heatpilot", "aus", "{ENT}", {{}}), noent: dhwModeText("heatpilot", "aus", "", {{}}) }});""")
    box = plain(r["p"])
    assert f"Pausiert seit {p['paused_since'][11:16]} (3 Fehlschläge in Folge) – bis du es wieder einschaltest" in box
    assert '<button class="btn small" id="dhw-resume" onclick="dhwResume()">wieder einschalten</button>' in r["p"]
    assert "Entscheidung" not in box and r["p"].count('class="warn"') == 1  # Entscheidung von vor der Pause: weg
    assert "Prüfung 16:14: 50 °C nicht übernommen (Kessel zeigt 45 °C)" in box
    assert f"{rp['headline']} Einzelheiten" in box and '<span class="dot"></span>' in r["p"]  # nicht bereit: grau
    assert "Beobachten zuletzt 15:03: würde 50 °C schreiben" in plain(r["t"]) and "Entscheidung: 45 → 50 °C" in plain(r["t"])
    assert r["none"] == '<span class="muted">Status nicht verfügbar</span>'
    assert r["aus"] == "Wie bisher: HeatPilot empfiehlt das Warmwasser-Soll nur (Übersicht, Vorschau) und schreibt nichts."
    assert r["dry"].startswith("HeatPilot rechnet in jedem Takt nach denselben Regeln und schreibt ins Protokoll")
    assert r["back"].endswith("Beim Zurückschalten schreibt HeatPilot nichts zurück – der Kessel behält 55 °C; die "
                              "HA-Automation ggf. wieder einschalten.")
    assert "der Kessel behält das letzte Soll" in r["back2"]
    assert plain(r["hp"]) == (f"HeatPilot schreibt das Warmwasser-Soll auf {ENT} – nach den Regeln oben. Vorher die "
                              "HA-Automation für das Speicher-Soll abschalten.")
    assert 'class="msg-err"' in r["noent"] and "sonst schreibt HeatPilot nichts" in r["noent"]


def test_switching_to_heatpilot_asks_once(tmp_path):
    r = run_js(tmp_path, """
      SET = { dhw_control: "trocken" };
      const a = dhwConfirm({ dhw_control: "heatpilot" });  CONFIRM = true;
      const b = dhwConfirm({ dhw_control: "heatpilot" });
      const c = dhwConfirm({ dhw_control: "aus" }); SET = { dhw_control: "heatpilot" };
      const d = dhwConfirm({ dhw_control: "heatpilot" }), e = dhwConfirm({});
      out({ res: [a, b, c, d, e], n: CONFIRMS.length, text: CONFIRMS[0] });""")
    assert r["res"] == [False, True, True, True, True] and r["n"] == 2  # nur beim Umschalten auf „HeatPilot setzt“
    assert r["text"].startswith("Ab jetzt schreibt HeatPilot das Warmwasser-Soll des Kessels.")
    assert "Zwei Schreiber gleichzeitig darf es nicht geben." in r["text"]


# ------------------------------------------------------------------ Bereit für die Steuerung?
def test_ready_block_warmwasser(tmp_path, berlin):
    _, _, _, rp = dhw_state(tmp_path, "pausiert")
    eng_s, _, _, _ = dhw_state(tmp_path, "setzt")
    eng_s.cfg.dhw_min_temp = 50  # ab 50 °C ohne Hygiene-Einschränkung: „bereit“ (grün)
    rs = as_json(readiness.dhw(eng_s, t0() + 900))
    r = run_js(tmp_path, f"out({{ p: dhwReadyHtml({json.dumps(rp)}, false), s: dhwReadyHtml({json.dumps(rs)}, true),"
                         " none: dhwReadyHtml(null, true) });")
    p = r["p"]
    assert p.startswith('<div class="ready-boiler" id="ready-dhw"><h3>Warmwasser über HeatPilot</h3>')
    assert f'<span id="ready-dhw-headline">{rp["headline"]}</span>' in p and '<span class="dot"></span>' in p
    assert f'<div class="warnbox" id="ready-dhw-hint">{dc_mod.HINT_AUTOMATION}</div>' in p  # sichtbar, nicht in den Einzelheiten
    assert '<details id="ready-dhw-details" ontoggle="READY_SHOW.dhw = this.open">' in p
    rows = dict(tuple(plain(c) for c in re.findall(r"<td>(.*?)</td>", row)) for row in re.findall(r"<tr>(.*?)</tr>", p))
    assert rows == {"Schalter": "HeatPilot steuert", "Entität": f"{ENT} erkannt", "Wert": "45 °C (30–70 °C)",
                    "schreibbar laut EMS-ESP": "unbekannt – die Prüfung nach 60 s zeigt es",
                    "letzte Prüfung": "16:14: 50 °C nicht übernommen",
                    "pausiert": "ja – 3 Fehlschläge in Folge · seit Mo., 16:14",
                    "fremder Schreiber": "zuletzt Mo., 15:08: 45 → 55 °C · 1× in 24 h"}
    assert [plain(x) for x in re.findall(r'<li class="block">(.*?)</li>', p)] == rp["blockers"]
    s = r["s"]
    assert '<span class="dot dot-ok"></span>' in s and "ready-dhw-hint" not in s and 'id="ready-dhw-details" open' in s
    assert "<td>pausiert</td><td>nein</td>" in s and "in den letzten 24 h keiner" in s
    assert "Schalter und Entität unter Einstellungen → Warmwasser." in plain(s)  # „Nur Anzeige“ einmal oben (0.43)
    assert r["none"] == ""


# ------------------------------------------------------------------ Benachrichtigungen
def notify_json(tmp_path, targets, services, fail=()):
    eng, ha, dc = make(tmp_path / "n", targets=targets)
    n = eng.notifier
    n.problem(dc_mod.K_PAUSED, "Warmwasser über HeatPilot pausiert", t0())
    ha.fail = list(fail)
    with mock.patch("time.time", return_value=float(t0() + 60)):  # gesendet kurz danach – nicht zur echten Uhrzeit
        run(n.flush())
    return as_json({**n.as_dict(), "available": notify.available(eng.states, services, n.targets),
                    "services_error": None if services is not None else "Dienste nicht lesbar (alt) – nur notify-Entitäten zur Auswahl"})


def test_notify_card_list_test_button_and_state(tmp_path):
    full = notify_json(tmp_path / "a", ["notify.handy", "notify.weg"], {"notify": {"tablet": {}, "send_message": {}}},
                       fail=[None, RuntimeError("Dienst fehlt")])
    old = notify_json(tmp_path / "b", [], None)
    r = run_js(tmp_path, f"""const F = {json.dumps(full)}, O = {json.dumps(old)};
      const sync = (targets, checked) => {{ NOTIFY = {{ targets }}; CHECKED = checked; notifySync();
        return [$("notify-test").disabled, $("notify-msg").textContent]; }};
      out({{ full: notifyHtml(F), old: notifyHtml(O), empty: notifyHtml({{ ...O, available: [], services_error: null }}),
            none: notifyHtml(null),
            sync: [sync(["notify.a", "notify.b"], ["notify.b", "notify.a"]), sync(["notify.a"], ["notify.a"]),
                   sync(["notify.a"], ["notify.a", "notify.b"]), sync([], [])] }});""")
    f = r["full"]
    rows = [[plain(c) for c in re.findall(r"<td>(.*?)</td>", x)] for x in re.findall(r"<tr><td><input.*?</tr>", f)]
    assert rows == [["", "Handy notify.handy", "Entität über notify.send_message"], ["", "tablet notify.tablet", "Dienst"],
                    ["", "notify.weg notify.weg", "fehlt in Home Assistant – abwählen"]]
    assert re.findall(r'data-id="([\w.]+)" (checked|)', f) == [("notify.handy", "checked"), ("notify.tablet", ""),
                                                               ("notify.weg", "checked")]
    assert 'onchange="notifySync()"' in f and '<button class="btn small" id="notify-test" onclick="notifyTest()">' in f
    assert "höchstens eine je Art in 6 h, Titel „HeatPilot“" in plain(f)
    state = plain(re.search(r'<div class="notify-state" id="notify-state">(.*)</div>', f).group(1))
    assert state.startswith("Zuletzt Mo., ") and "„Warmwasser über HeatPilot pausiert“ an notify.handy · Fehler: notify.weg: Dienst fehlt" in state
    assert state.endswith("Gerade offen – binary_sensor.heatpilot_problem ist an: Warmwasser über HeatPilot pausiert")
    o = plain(r["old"])
    assert "Handy notify.handy Entität" in o and "Dienste nicht lesbar (alt) – nur notify-Entitäten zur Auswahl" in o
    assert "Noch nichts gesendet." in o and "binary_sensor.heatpilot_problem ist an" in o  # Sensor läuft auch ohne Ziele
    assert 'id="notify-list">Keine Ziele in Home Assistant gefunden' in r["empty"]
    assert "nicht verfügbar" in r["none"]
    # Testknopf nur mit gespeicherten Zielen und unveränderten Häkchen
    assert r["sync"] == [[False, "Geht an die 2 gespeicherten Ziele."], [False, "Geht an das gespeicherte Ziel."],
                         [True, "Häkchen geändert – erst speichern; die Testnachricht geht nur an gespeicherte Ziele."],
                         [True, "Erst Ziele wählen und speichern."]]


# ------------------------------------------------------------------ Vorschau: „schreibt“ bei HeatPilot setzt
def preview_json(tmp_path, mode="heatpilot"):
    cfg = Config.from_raw({"control_mode": "heatpilot", "dhw_control": mode})
    eng, _, _ = house(tmp_path, cfg=cfg)
    eng.dhw = dhw_model()
    eng.states[ENT] = {"state": "45", "attributes": {"unit_of_measurement": "°C"}}
    eng.found.boiler["dhw_seltemp"] = ENT
    eng.dhw_control = DhwControl(eng, None, None, (ENT, "erkannt"))
    return as_json(pv.preview(eng, ts_of(SUN, 10)))


def test_preview_says_writes_for_dhw_only(tmp_path, berlin):
    p = preview_json(tmp_path / "a")
    assert p["dhw_writes"] == "schreibt" and p["writes_24h"]["dhw"] >= 1
    i = next(k for k, h in enumerate(p["hours"]) if any(w["what"] == "dhw" for w in h["writes"]))
    r = run_js(tmp_path, f"""const P = {json.dumps(p)}, W = {{ ...P, dhw_writes: "würde schreiben" }}, OLD = {{ ...P }};
      delete OLD.dhw_writes; delete OLD.dhw_paused; delete OLD.dhw_mode;
      out({{ note: pvNote(P), sum: pvSummary(P), tip: pvTip(P, {i}), table: pvTable(P),
            wNote: pvNote(W), wSum: pvSummary(W), wTip: pvTip(W, {i}), wTable: pvTable(W),
            oNote: pvNote(OLD), oSum: pvSummary(OLD), oTip: pvTip(OLD, {i}), oTable: pvTable(OLD),
            paused: pvNote({{ ...W, dhw_paused: true }}), quiet: pvSummary({{ ...P, writes_24h: {{ flow: 0, heating: 0, dhw: 0 }} }}),
            empty: pvTip({{ ...P, hours: P.hours.map((h) => ({{ ...h, writes: [] }})) }}, 0) }});""")
    w = p["writes_24h"]
    assert plain(r["note"]).startswith("Beobachten – HeatPilot schreibt nichts an den Kessel – außer dem Warmwasser-Soll. "
                                       "Das Warmwasser-Soll setzt HeatPilot selbst (Einstellungen → Warmwasser: „HeatPilot setzt“)")
    dry = w["flow"] + w["heating"]
    parts = ", ".join(f"{k} {w[key]}×" for k, key in (("Vorlauf", "flow"), ("Heizbetrieb", "heating")) if w[key])
    head = (f"In den nächsten 24 h würde HeatPilot {dry}× schreiben ({parts})" if dry
            else "In den nächsten 24 h würde HeatPilot an Vorlauf und Heizbetrieb nichts schreiben")
    assert plain(r["sum"]).startswith(f"{head} und schreibt das Warmwasser-Soll {w['dhw']}× – nur bei Änderung")
    assert plain(r["quiet"]).startswith("In den nächsten 24 h würde HeatPilot an Vorlauf und Heizbetrieb nichts schreiben "
                                        "und schreibt das Warmwasser-Soll nicht neu – ")
    dw = next(x for x in p["hours"][i]["writes"] if x["what"] == "dhw")
    one = f"{dw['t'][11:16]} Warmwasser {dw['from']:g} → {dw['value']} °C"
    tip = plain(r["tip"])
    assert f"schreibt: {one}" in tip and not re.search(r"würde schreiben:[^:]*Warmwasser", tip)
    assert f"schreibt: {one}" in plain(r["table"]) and f"würde schreiben: {one}" not in plain(r["table"])
    assert plain(r["empty"]).endswith("nichts zu schreiben")
    # „würde schreiben“ (Trockenlauf, pausiert) und ältere Antworten ohne die Felder: alles wie in 0.37
    assert r["wNote"] == r["oNote"] == f"<b>{pv.NOTE}.</b> Die Vorschau ist ein Vorschlag zum Vergleichen – wann und ob umgestellt wird, entscheidest du."
    assert r["wSum"] == r["oSum"] and f"Warmwasser {w['dhw']}×" in plain(r["wSum"])
    assert r["wTip"] == r["oTip"] and f"würde schreiben: " in plain(r["wTip"]) and one in plain(r["wTip"])
    assert r["wTable"] == r["oTable"] and "schreibt: " not in r["oTable"]
    assert "Warmwasser über HeatPilot ist pausiert – auch das Warmwasser-Soll ist hier nur Vorschau." in r["paused"]
    # Legende und Ringe: „schreibt“ gefüllt (Quelltext, gezeichnet wird im Browser)
    js = script()
    assert '`<span><i class="ring own"></i>schreibt</span>`' in between(js, "function pvLegend(p) {", "function pvFlowChart(")
    assert 'pvMarks(f, p.hours.flatMap((h) => h.writes).filter((w) => w.what === "dhw"), PV_C.dhw, p.dhw_writes === "schreibt");' in js
    assert 'fill: own ? color : "var(--surface-1)"' in between(js, "function pvMarks(f, writes, color, own = false) {", "const HOVER")


# ------------------------------------------------------------------ Stil, Gültigkeit, Lesbarkeit
def test_css_rules_and_theme_tokens():
    css = html().split("<style>")[1].split("</style>")[0]
    for rule in ("#ov-dhw { margin: 0; }",  # 0.43: Zeile in Übersicht → Status
                 ".legend i.ring.own { background: var(--series-4); border-color: var(--series-4); }",
                 "@media (max-width: 440px) { .seg.seg3 label { padding: 8px 2px; font-size: 13px; } }",
                 "table.notify-t td { vertical-align: middle; overflow-wrap: anywhere; }"):
        assert rule in css, rule
    # neue Regeln nutzen nur Farben, die es in beiden Themen gibt
    new = css.split("/* Warmwasser über HeatPilot (0.38)")[1].split("/* Entitäten (0.39.1)")[0]  # 0.39.1: test_ui_v391
    assert set(re.findall(r"var\((--[\w-]+)\)", new)) <= {"--warn", "--series-4", "--border", "--bg", "--text-primary",
                                                          "--text-secondary"}


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    f = tmp_path / "inline.js"
    f.write_text(re.findall(r"<script>(.*?)</script>", html(), re.S)[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_new_section_small_functions():
    """Lesbarkeit: die neuen Funktionen bleiben kurz (Richtwert ≤ ~60 Zeilen)."""
    js = between(script(), SECTION, "function renderSettings()")
    names = re.findall(r"^(?:async )?function (\w+)\(", js, re.M)
    assert {"dhwLine", "dhwSectionHtml", "dhwBoxHtml", "dhwReadyHtml", "notifyHtml", "notifySync", "notifyTest"} <= set(names)
    for name in names:
        assert source(js, name).count("\n") <= 60, name
