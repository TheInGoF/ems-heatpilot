"""0.46.1: Übersicht → Warmwasser und → Kessel zeigen, was HeatPilot die nächsten 12 Stunden plant – aus der
vorhandenen Vorschau (api/preview, keine zweite Abfrage). Darunter nur die Wechsel als eine Zeile."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_preview37 import _old_flow_floor, by_hour, dhw_model, house  # noqa: F401 – autouse-Fixture wie dort
from test_control34 import SUN, ts_of
from test_ui_v361 import NODE, html, plain, script, source

from heatpilot import preview as pv
from heatpilot.config import Config

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("fmt", "esc", "tsOf", "tclock", "NX_H", "NX_EMPTY", "nxWin", "nxSteps", "NX_WHY", "nxWhy", "NX_LABEL", "nxLabel",
         "NX_MIN_S", "nxRuns", "nxLine", "NX_MORE", "nxLegend", "nxLanes", "drawNext", "nxDhw", "nxFlow")


def run_nx(tmp_path, body: str):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    stubs = """
const BOX = {};
const $ = (id) => (BOX[id] = BOX[id] || { innerHTML: "" });
const empty = (box, text) => { box.innerHTML = text; };
let PV = null;
"""
    code = stubs + "\n".join(source(js, n) for n in NAMES) + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# Beispiel ab Di 20:00 (Berlin): Wechsel um 22:00 (Warmwasser Nacht), 23:00 (Heizbetrieb aus), 05:00 (heizt vor),
# 05:30 (Warmwasser vor dem Aufstehen) – und um 09:00 einer hinter den 12 Stunden
SAMPLE = r"""
const T = (h, m = 0) => new Date(Date.UTC(2026, 9, 20, 18 + h, m)).toISOString();  // 20:00 Berlin = 18:00 UTC
const st = (h, m, heating, flow, dhw, flow_why, dhw_why, hold) => ({ t: T(h, m), heating, flow, dhw, flow_why, dhw_why, dhw_hold: hold });
const HOME = "jemand zu Hause – Speicher bleibt über 50 °C", NIGHT = "Nacht – Speicher darf bis 40 °C fallen";
const P = { hours: Array.from({ length: 24 }, (_, i) => ({ t: T(i), dhw_reason: i >= 2 && i < 9 ? NIGHT : HOME, night: i >= 2 && i < 10, home: 1 })),
  steps: [st(0, 0, true, 44, 55, "Wohnzimmer hält 21 °C", HOME, 50), st(1, 0, true, 43, 55, "Bad hält 21 °C", HOME, 50),
    st(2, 0, true, 43, 45, "Bad hält 21 °C", NIGHT, 40), st(3, 0, false, null, 45, "kein Raum braucht Wärme – Heizbetrieb aus", NIGHT, 40),
    st(9, 0, true, 46, 45, "Wohnzimmer heizt vor bis 06:30", NIGHT, 40),
    st(9, 30, true, 46, 55, "Wohnzimmer heizt vor bis 06:30", "vor dem Aufstehen aufheizen – Speicher bleibt über 50 °C", 50),
    st(13, 0, true, 40, 50, "Wohnzimmer hält 21 °C", "Zapfung erwartet (≈5 K) – Speicher bleibt über 45 °C", 45)] };
"""


def test_change_lines_for_dhw_and_boiler_only_12_hours(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
const w = nxWin(P);
out({ dhw: nxLine(nxRuns(P, "dhw")), flow: nxLine(nxRuns(P, "flow")), span: (w.x1 - w.x0) / 3600, n: w.idx.length,
      dis: nxLine(nxRuns(P, "dhw"), [[Date.parse(T(6)) / 1000, "02:00 Desinfektion (70 °C)"]]) });
""")
    assert r["dhw"] == "Jetzt 55 °C · ab 22:00 45 °C (Nacht) · ab 05:30 55 °C (vor dem Aufstehen)"
    # gleicher Grund („hält Soll“) wird eine Spanne; Heizbetrieb aus ohne Klammer
    assert r["flow"] == "Jetzt 43–44 °C · ab 23:00 Heizbetrieb aus · ab 05:00 46 °C (heizt vor)"
    assert r["span"] == 12 and r["n"] == 12  # 09:00 (50 °C, 40 °C) liegt dahinter und fehlt
    assert "50 °C" not in r["dhw"] and "40 °C" not in r["flow"]
    assert r["dis"].endswith("· 02:00 Desinfektion (70 °C) · ab 05:30 55 °C (vor dem Aufstehen)")


def test_on_off_every_few_minutes_is_one_block_not_a_wall_of_text(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
// 01:00–03:00 im 10-min-Takt an (37 °C) / aus, davor 40 min an, danach lang an; ganz kurz aus um 00:00 (10 min)
const S = [st(0, 0, true, 44, 55, "Wohnzimmer hält 21 °C", HOME, 50), st(4, 0, false, null, 55, "kein Raum braucht Wärme – Heizbetrieb aus", HOME, 50),
  st(4, 10, true, 44, 55, "Wohnzimmer hält 21 °C", HOME, 50), st(4, 20, true, 40, 55, "Bad heizt auf 21 °C (+5 K)", HOME, 50)];
for (let m = 0; m < 120; m += 10) S.push(st(5, m, m % 20 === 0, m % 20 === 0 ? 37 : null, 55, m % 20 ? "nötiger Vorlauf unter 35 °C (Bad) – Heizbetrieb aus" : "Bad heizt auf 21 °C (+5 K)", HOME, 50));
S.push(st(7, 0, true, 42, 55, "Wohnzimmer hält 21 °C", HOME, 50));
out(nxLine(nxRuns({ hours: P.hours, steps: S }, "flow")));
""")
    # 00:00–00:20 kurz aus/an zählt zu „Jetzt“; 01:10–03:00 (60 min aus, 50 min an) ein Block statt elf Wechseln
    assert r == "Jetzt 44 °C (zeitweise aus) · ab 00:20 37–40 °C (heizt auf) · ab 01:10 Heizbetrieb meist aus · ab 03:00 42 °C (hält Soll)"


def test_window_starts_now_mid_hour_and_ends_with_preview(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
const late = { hours: P.hours.slice(0, 6), steps: [st(0, 10, false, null, 55, "kein Raum braucht Wärme – Heizbetrieb aus", HOME, 50)] };
const w = nxWin(late), mid = nxWin({ ...P, steps: [{ ...P.steps[0], t: T(0, 10) }, ...P.steps.slice(1)] });
out({ span: (w.x1 - w.x0) / 3600, flow: nxLine(nxRuns(late, "flow")), mid: [(mid.x1 - mid.x0) / 3600, mid.idx.length] });
""")
    assert r["span"] == pytest.approx(6 - 1 / 6)  # Vorschau endet früher: nur bis dahin
    assert r["flow"] == "Jetzt Heizbetrieb aus"
    assert r["mid"] == [12, 13]  # 20:10–08:10 berührt 13 Stunden


def test_empty_preview_shows_sentence_instead_of_error(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
drawNext();
const a = [BOX["ch-nx-dhw"].innerHTML, BOX["ch-nx-flow"].innerHTML, BOX["nx-dhw-line"].innerHTML];
PV = { preview: { hours: [], steps: [] } }; drawNext();
const b = BOX["ch-nx-flow"].innerHTML;
PV = { preview: { ...P, dhw_note: "lernt noch: zu wenige Ladungen", steps: P.steps.map((s) => ({ ...s, dhw: null, heating: null, flow: null })) } };
drawNext();
out({ a, b, dhw: BOX["ch-nx-dhw"].innerHTML, flow: BOX["ch-nx-flow"].innerHTML, runs: nxRuns(null, "dhw"), win: nxWin({ hours: [] }) });
""")
    empty = "Noch keine Vorschau – sobald HeatPilot die nächsten Stunden berechnet hat, steht hier der Plan."
    assert r["a"][:2] == [empty, empty] and r["a"][2] == "" and r["b"] == empty
    assert r["dhw"] == "Kein Warmwasser-Soll – lernt noch: zu wenige Ladungen." and r["flow"] == "Kein Vorlauf-Soll."
    assert r["runs"] == [] and r["win"] is None


def test_short_reasons_and_labels(tmp_path):
    r = run_nx(tmp_path, """
out([nxWhy("Urlaub – Speicher darf bis 40 °C fallen", "dhw"), nxWhy("große Zapfung üblich (≈14 K) – Speicher bleibt über 54 °C", "dhw"),
     nxWhy("hier wird oft gezapft (in 40 % der Fälle) – Speicher bleibt über 50 °C", "dhw"),
     nxWhy("Bad hält 21 °C – begrenzt durch Fußboden Bad", "flow"), nxWhy("Bad heizt auf 21 °C (+5 K)", "flow"),
     nxLabel("vor der Ankunft"), nxLabel("Nacht"), nxLabel("lernt noch")]);
""")
    assert r == ["Urlaub", "große Zapfung üblich", "Zapfung erwartet", "Fußboden-Grenze", "heizt auf", ["Aufheizen"], ["Nacht"], []]


def test_cards_on_both_pages_and_one_preview_request():
    page = html()
    for page_id, k in (("ov-warmwasser", "dhw"), ("ov-kessel", "flow")):
        part = page.split(f'<div id="{page_id}" hidden>')[1].split('<div id="ov-')[0]
        card = part.split(f'id="nx-{k}"')[1].split("</div>\n  </div>")[0]
        assert "Nächste 12 Stunden" in card and f'id="ch-nx-{k}"' in card and f'id="nx-{k}-line"' in card
        assert "Noch keine Vorschau" in plain(card)
    # Warmwasser: direkt unter den Kacheln; Kessel: oben
    ww = page.split('<div id="ov-warmwasser" hidden>')[1]
    assert ww.index('id="dhw-tiles"') < ww.index('id="nx-dhw"') < ww.index("Wann gezapft wird")
    assert page.split('<div id="ov-kessel" hidden>')[1].lstrip().startswith('<div class="card nx-card" id="nx-flow">')
    js = script()
    assert len(re.findall(r'getJSON\("api/preview"\)', js)) == 1
    assert "drawNext();" in source(js, "loadPreview")
    assert 'OV_PAGE === "warmwasser" || OV_PAGE === "kessel") drawNext()' in source(js, "drawOverview")
    for fn in ("nxDhw", "nxFlow"):  # Projektregeln: frame() mit clipPath, Tokens, 2-px-Linie, Tooltip wie die Vorschau
        body = source(js, fn)
        assert "frame(box" in body and '"stroke-width": 2' in body and "pvTip(p, w.idx[k])" in body and "#" not in body


def test_preview_carries_hold_and_reasons_per_step(tmp_path):
    cfg = Config.from_raw({"control_mode": "heatpilot"})
    eng, _, _ = house(tmp_path, cfg=cfg)
    eng.dhw = dhw_model()
    p = pv.preview(eng, ts_of(SUN, 10))
    h = by_hour(p)
    assert h["12:00"]["dhw"] == 55 and h["12:00"]["dhw_hold"] == 50  # Soll − 5 K Hysterese (angenommen)
    assert h["21:00"]["dhw_hold"] == 40 and "Speicher darf bis 40 °C fallen" in h["21:00"]["dhw_reason"]
    for s in p["steps"]:
        assert {"flow_why", "dhw_why", "dhw_hold"} <= set(s)
        if s["dhw"] is not None:
            assert s["dhw_hold"] == pytest.approx(s["dhw"] - 5) and s["dhw_why"]


def test_version_changelog_docs():
    from heatpilot import __version__
    assert __version__ >= "0.46.1"
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert log.split("\n## ")[1].startswith("0.46.1\n")
    head = log.split("\n## 0.46.1\n")[1].split("\n## ")[0]
    for t in ("Nächste 12 Stunden", "api/preview", "dhw_hold", "tests/test_ui_v461.py"):
        assert t in head, t
    assert "Nächste 12 Stunden" in (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
