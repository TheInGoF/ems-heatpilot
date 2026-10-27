"""0.42: Oberfläche zu „Desinfektion auslösen“ und „Mindestleistung“ – statische Prüfung und Texte in Node.

Wie tests/test_ui_v41.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird der Einbau (Zeile „Auslösen“ in
Einstellungen → Kessel mit Zustand und letzter Desinfektion; Zeile im Block „Warmwasser über HeatPilot“ von „Bereit
für die Steuerung?“; neue Rolle „Desinfektion auslösen (Schalter)“ auf der Seite „Entitäten“ mit Auswahl nur passender
Schalter; Vorschlag der kleinsten Leistung mit Knopf „übernehmen“ in Haus & Kessel; Desinfektions-Zeilen im Protokoll),
dass die Oberfläche nichts schaltet und nichts speichert (der Knopf füllt nur das Feld) und die Texte – in Node mit
Antworten des echten Backends (``DisinfectControl`` und ``boiler_power`` an einem künstlichen Haus aus
tests/test_disinfect42.py). Bilder in 390/1100 px, hell/dunkel prüft das Playwright-Skript, das nicht Teil der Suite
ist.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from heatpilot import boiler_power, readiness
from heatpilot.controlloop import ControlLoop
from heatpilot.disinfect_status import ACTIONS as DIS_ACTIONS
from heatpilot.engine import Engine
from heatpilot.config import Config
from heatpilot.discovery import Discovered
from heatpilot.store import Store
from test_disinfect42 import SUN, SW, armed, house, shows, step, ts_of, view
from test_ui_v361 import NODE, between, html, plain, script, source
from test_ui_v39 import as_json, berlin  # noqa: F401 – berlin ist eine Fixture

ROOT = Path(__file__).resolve().parents[1]
SECTION = "// ---------------------------------------------------------------- Desinfektion auslösen (0.42)"
SECTION_END = "// " + "-" * 64 + " Warmwasser über HeatPilot (Weg W, 0.38)"
NAMES = ("fmt", "esc", "clock", "timeOf", "degC", "READY", "readyDot", "ICON", "DROP", "FLAME", "field", "shortName",
         "WEEKDAYS", "DAY_NAMES", "CHARGE_FROM", "DIS_BOILER", "DIS_NOT_YET", "disinfectHtml", "disinfectState",
         "disinfectSuggestHtml", "DIS_RESULT_CSS", "disWhen", "disDot", "disLast", "disNow", "disinfectTriggerHtml",
         "minPowerHint", "applyMinPower", "DHW_LABEL", "dhwReadyHtml", "ACTION_NAME", "DHW_ACTION", "BOILER_LABEL",
         "BOILER_ACTION", "boilerLogValue", "heatLogRows", "renderHeatLog")
STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "", className: "",
  disabled: false, value: "" });
let BPOW = null, SET = {};
const POSTS = [];
const fetch = (...a) => { POSTS.push(a); return Promise.resolve({}); };
"""


def js_run(tmp_path, body: str):
    """``body`` mit den Funktionen oben und dem DOM-Ersatz in Node; gibt das JSON von ``out(...)`` zurück."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    funcs = "\n".join(source(script(), n) for n in NAMES)
    code = STUBS + funcs + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe42.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def scene(tmp_path, kind: str):
    """Echte Antworten: (eng, Auslösen wie ``disinfect.trigger``, Bereitschaft Warmwasser, Zeitpunkt)."""
    t = ts_of(SUN)
    if kind == "regler":
        eng, ha, dc = house(tmp_path, regulator=True)
    elif kind == "aus":
        eng, ha, dc = house(tmp_path, mode="aus")
    elif kind == "trocken":
        eng, ha, dc = house(tmp_path, mode="trocken")
    else:
        eng, ha, dc = house(tmp_path)
    armed(eng, dc)
    now = t - 600
    if kind in ("läuft", "gelungen", "fehlgeschlagen"):
        step(eng, dc, t)
        shows(eng, switch="on", tank=61)
        step(eng, dc, t + 60)
        now = t + 900
        if kind == "gelungen":
            shows(eng, tank=68)
            step(eng, dc, t + 1800)
            shows(eng, switch="off")
            step(eng, dc, t + 1860)
            now = t + 3600
        elif kind == "fehlgeschlagen":
            now = dc.state["run"]["deadline"]
            step(eng, dc, now)
            shows(eng, switch="off")
            step(eng, dc, now + 60)
            now += 600
        step(eng, dc, now)
    elif kind == "gesperrt":
        eng.dhw_control.state["paused"] = {"ts": t - 3600, "reason": "3 Fehlschläge in Folge"}
        step(eng, dc, t)
        now = t + 600
    x = as_json(view(eng, dc, now))
    import unittest.mock as um
    with um.patch("time.time", return_value=float(now)):
        rd = as_json(readiness.dhw(eng, now))
    return eng, x, rd, now


# ------------------------------------------------------------------ Einbau
def test_trigger_row_ready_line_entities_power_and_log_are_wired():
    js, page = script(), html()
    dis = source(js, "disinfectHtml")
    assert "${disinfectState(d)}${disinfectTriggerHtml(d.trigger)}${disinfectSuggestHtml(d.suggestion)}" in dis
    assert "Ohne Regler am Bus löst HeatPilot sie bei „Warmwasser-Soll: HeatPilot setzt“ zum Termin aus" in dis
    assert 'const dis = !ds ? "" : `<p class="ready-dis" id="ready-dhw-dis">' in source(js, "dhwReadyHtml")
    pickers = between(js, "const PICKERS = {", "};")
    assert ('disinfect: { filter: (e) => !!(e.disinfect && e.disinfect.fits), label: "Schalter „Desinfizieren“ '
            '(switch)" }') in pickers
    rs = between(js, "function renderSettings()", "function addVac()")
    assert ('${field("Kleinste Leistung (%)", num("f-boiler_min_power_pct", s.boiler_min_power_pct, "1"), '
            'minPowerHint(BPOW))}') in rs
    assert "BPOW = j.boiler_power || null;" in between(js, "async function loadSettings()", "let OV_STALE")
    log = source(js, "renderHeatLog")
    assert 'l.action === "disinfect_on" ? "an" : l.action === "disinfect_off" ? "aus"' in log
    acts = between(js, "const DHW_ACTION = {", "};")
    assert set(re.findall(r"(disinfect_\w+):", acts)) == set(DIS_ACTIONS)  # jede Aktion des Backends beschriftet
    trig = source(js, "disinfectTriggerHtml")
    assert "<details" not in trig and 'id="dis-trigger"' in trig
    assert ".minpow-off { color: var(--warn); }" in page


def test_ui_switches_and_saves_nothing():
    """Die neuen Teile fragen nichts ab und schicken nichts; „übernehmen“ füllt nur das Feld."""
    js = script()
    part = re.sub(r"^\s*//.*$", "", between(js, SECTION, SECTION_END), flags=re.M)
    for word in ("api/", "getJSON(", "fetch(", "heatPost(", "method:", "turn_on", "turn_off", "saveSettings("):
        assert word not in part, word
    assert not re.search(r'"api/[^"]*disinfect', js)


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    f = tmp_path / "inline42.js"
    f.write_text(re.findall(r"<script>(.*?)</script>", html(), re.S)[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_new_functions_small_and_css_tokens():
    part = between(script(), SECTION, SECTION_END)
    names = re.findall(r"^(?:async )?function (\w+)\(", part, re.M)
    assert {"disLast", "disNow", "disinfectTriggerHtml", "minPowerHint", "applyMinPower"} <= set(names)
    for name in names:
        assert source(part, name).count("\n") <= 60, name
    page = html()
    css = between(page, "/* Desinfektion auslösen, Mindestleistung (0.42)", "</style>")
    root = between(page, ":root {", "}")
    tokens = set(re.findall(r"var\((--[\w-]+)\)", css))
    assert tokens and all(t + ":" in root for t in tokens), tokens  # nur Farben, die es in beiden Themen gibt


# ------------------------------------------------------------------ Texte mit echten Antworten
@pytest.mark.usefixtures("berlin")
def test_trigger_row_running_success_failure(tmp_path):
    out = {}
    for kind in ("bereit", "läuft", "gelungen", "fehlgeschlagen"):
        eng, x, rd, now = scene(tmp_path / kind, kind)
        out[kind] = (x, js_run(tmp_path, f"out(disinfectTriggerHtml({json.dumps(x)}));"))
    x, h = out["bereit"]
    assert plain(h) == (f"Auslösen: löst HeatPilot aus (So 17:30) Schalter {SW} (automatisch erkannt, jetzt aus) – "
                        "ändern")  # 0.43: die Entität steht auf derselben Unterseite (Warmwasser)
    assert '<span class="dot dot-ok"></span>' in h and "emGo('warmwasser')" in h
    x, h = out["läuft"]
    since = re.search(r"seit (\d\d:\d\d)", plain(h)).group(1)
    assert since == "17:30" and plain(h).endswith(f"läuft seit {since} – Speicher 61 °C, Ziel 67 °C")
    x, h = out["gelungen"]
    assert plain(h).endswith("zuletzt So., 18.10., 17:30: gelungen – 68 °C erreicht (Ziel 67 °C)")
    assert '<span class="msg-ok">gelungen</span>' in h
    x, h = out["fehlgeschlagen"]
    assert plain(h).endswith("zuletzt So., 18.10., 17:30: nicht gelaufen – Speicher erreichte nur 61 °C, nötig 67 °C")
    assert '<span class="dot dot-warn"></span>' in h and '<span class="msg-err">nicht gelaufen</span>' in h


@pytest.mark.usefixtures("berlin")
def test_trigger_row_regulator_off_dry_and_blocked(tmp_path):
    rows = {}
    for kind in ("regler", "aus", "trocken", "gesperrt"):
        eng, x, rd, now = scene(tmp_path / kind, kind)
        rows[kind] = plain(js_run(tmp_path, f"out(disinfectTriggerHtml({json.dumps(x)}));"))
    assert rows["regler"].startswith("Auslösen: macht der Regler Schalter")
    assert rows["aus"].startswith("Auslösen: aus – HeatPilot löst sie nur bei „Warmwasser-Soll: HeatPilot setzt“ ohne "
                                  "Regler am Bus aus Schalter")
    assert rows["trocken"].startswith("Auslösen: Beobachten – würde So 17:30 auslösen Schalter")
    assert rows["gesperrt"].endswith("wartet seit 17:30: Warmwasser über HeatPilot ist pausiert – erst „wieder "
                                     "einschalten“")
    assert js_run(tmp_path, "out(disinfectTriggerHtml(null));") == ""


@pytest.mark.usefixtures("berlin")
def test_warmwasser_card_and_ready_block(tmp_path):
    eng, x, rd, now = scene(tmp_path / "a", "gelungen")
    with __import__("unittest.mock").mock.patch("time.time", return_value=float(now)):
        d = as_json(eng.disinfect_info(suggest=True, now_ts=now))
    s = {"dhw_disinfect_day": "So", "dhw_disinfect_time": "17:30", "dhw_disinfect_temp": 70.0}
    r = js_run(tmp_path, f"out({{ card: disinfectHtml({json.dumps(s)}, {json.dumps(d)}), "
                         f"ready: dhwReadyHtml({json.dumps(rd)}, false) }});")
    card = plain(r["card"])
    assert "Auslösen: löst HeatPilot aus (So 17:30)" in card
    assert card.endswith("HeatPilot löst sie aus – zum Termin schaltet es den Schalter „Desinfizieren“ und prüft, ob "
                         "der Speicher die Desinfektionstemperatur erreicht")
    assert card.index("Nächste Desinfektion") < card.index("Auslösen:") < card.index("Zeitvorschlag")
    line = plain(re.search(r'<p class="ready-dis" id="ready-dhw-dis">(.*?)</p>', r["ready"]).group(1))
    assert line == "Desinfektion: löst HeatPilot aus (So 17:30) · zuletzt So., 17:30: gelungen"
    notes = [plain(x) for x in re.findall(r'<li class="note">(.*?)</li>', r["ready"])]
    assert "Letzte Desinfektion (So 17:30) gelungen – 68 °C erreicht (Ziel 67 °C)" in notes
    _, _, rd2, _ = scene(tmp_path / "b", "regler")
    r = js_run(tmp_path, f"out(dhwReadyHtml({json.dumps(rd2)}, false));")
    assert plain(re.search(r'id="ready-dhw-dis">(.*?)</p>', r).group(1)) == "Desinfektion: macht der Regler"
    old = {k: v for k, v in rd2.items() if k != "disinfect"}  # ältere Antwort ohne Feld: keine Zeile
    assert "ready-dhw-dis" not in js_run(tmp_path, f"out(dhwReadyHtml({json.dumps(old)}, false));")


def power(tmp_path, controls=None, **cfg):
    tmp_path.mkdir(parents=True, exist_ok=True)
    eng = Engine(Config(**cfg), Store(tmp_path / "p.db"), Discovered())
    if controls is not None:
        eng.ems_controls, eng.ems_controls_ts = controls, time.time()
    return as_json(boiler_power.info(eng))


def test_min_power_hint_suggestion_and_apply_fills_only_the_field(tmp_path):
    p = power(tmp_path / "a", {"burnmaxpower": {"found": True, "min": 32, "max": 100}}, boiler_nominal_kw=18.0)
    r = js_run(tmp_path, f"""BPOW = {json.dumps(p)};
      const hint = minPowerHint(BPOW);
      $("f-boiler_min_power_pct").value = "0";
      applyMinPower();
      out({{ hint, value: $("f-boiler_min_power_pct").value, msg: $("save-msg").textContent,
            btn: [$("minpow-apply").disabled, $("minpow-apply").textContent], posts: POSTS.length }});""")
    assert plain(r["hint"]) == ("Untere Modulationsgrenze, z. B. 30 Der Kessel meldet keine – ohne Wert ist die "
                                "Abnahme-Regel bei „Kessel über HeatPilot“ aus. Vorschlag 32 % – aus dem Bereich der "
                                "maximalen Brennerleistung (32–100 %) übernehmen")
    assert 'onclick="applyMinPower()">übernehmen</button>' in r["hint"]
    assert r["value"] == 32 and r["msg"] == "Kleinste Leistung 32 % eingetragen – prüfen und speichern."
    assert r["btn"] == [True, "eingetragen"] and r["posts"] == 0  # nicht gespeichert
    boiler = power(tmp_path / "b", {"burnminpower": {"found": True, "value": 30}})
    own = power(tmp_path / "c", boiler_min_power_pct=30.0)
    none = power(tmp_path / "d")
    r = js_run(tmp_path, f"out([minPowerHint({json.dumps(boiler)}), minPowerHint({json.dumps(own)}), "
                         f"minPowerHint({json.dumps(none)}), minPowerHint(null)]);")
    assert plain(r[0]) == "Untere Modulationsgrenze, z. B. 30 · leer bzw. 0 = 30 % laut Kessel (EMS-ESP)"
    assert r[1] == "Untere Modulationsgrenze, z. B. 30" and r[3] == r[1]
    assert plain(r[2]).endswith("ohne Wert ist die Abnahme-Regel bei „Kessel über HeatPilot“ aus.")
    assert "übernehmen" not in r[2]


@pytest.mark.usefixtures("berlin")
def test_heat_log_rows_for_the_disinfection(tmp_path):
    eng, x, rd, now = scene(tmp_path, "gelungen")
    loop = ControlLoop(eng, eng.disinfect_control.control)
    rows = as_json(loop.as_dict(now))["log"]
    assert [r["kind"] for r in rows] == ["dhw"] * 3 and {r["name"] for r in rows} == {"Desinfektion"}
    r = js_run(tmp_path, f"""renderHeatLog({{ log: {json.dumps(rows)} }}, null);
      out($("heat-log").innerHTML);""")
    got = [plain(x) for x in re.findall(r'<tr class="log-dhw">(.*?)</tr>', r)]
    assert [g.split(" Desinfektion ", 1)[1] for g in got] == [  # der Kessel setzte den Schalter selbst zurück
        "gelungen 68 °C Desinfektion So 17:30 gelungen – 68 °C erreicht (Ziel 67 °C)",
        "läuft – Desinfektion So 17:30 läuft (Schalter an) – Ziel 67 °C im Speicher",
        "eingeschaltet an Desinfektion So 17:30 (70 °C)"]
    off = {**rows[0], "action": "disinfect_off", "value": 0, "reason": "Desinfektion So 17:30 vorbei – Schalter aus"}
    r = js_run(tmp_path, f"""renderHeatLog({{ log: [{json.dumps(off)}] }}, null); out($("heat-log").innerHTML);""")
    assert plain(r).endswith("Desinfektion ausgeschaltet aus Desinfektion So 17:30 vorbei – Schalter aus")


def test_entities_page_offers_only_the_disinfect_switch(tmp_path):
    eng, ha, dc = house(tmp_path)
    for eid, name in (("switch.boiler_wwonetime", "WWK Einmalladung"), ("switch.garten", "Gartenpumpe"),
                      ("switch.boiler_heatingactivated", "Heizbetrieb aktiviert")):
        eng.states[eid] = {"state": "off", "attributes": {"friendly_name": name}}
    from heatpilot import disinfect_entity as de
    ents = [{"id": eid, "name": eid, "disinfect": de.picker(eid, st, eng.found)} for eid, st in eng.states.items()
            if eid.startswith("switch.")]
    filt = between(script(), "  disinfect: { filter: ", ", label: ")
    r = js_run(tmp_path, f"const F = {filt};\nout({json.dumps(ents)}.filter(F).map((e) => e.id));")
    assert r == [SW]


def test_docs_and_changelog_name_the_places():
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for text in ("Einstellungen → Kessel", "**Auslösen**", "Einstellungen → Kessel → Warmwasser steuern",
                 "„Desinfektion auslösen (Schalter)“", "Einstellungen → Kessel"):  # 0.43: Unterseite Kessel
        assert text in docs, text
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## 0.42.0")[1].split("\n## ")[0]  # seit 0.42.1
    assert "**Oberfläche:**" in head and "tests/test_ui_v42.py" in head
