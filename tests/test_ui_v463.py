"""0.46.3: „Nächste 12 Stunden“ (Übersicht → Warmwasser und → Kessel) als Zeitstrahl statt Graf: Abschnitte mit Uhrzeit
von–bis, Art und Wert aus den Schritten der Vorschau; Uhrzeiten aus der Ortszeit des Backends (ISO mit Versatz), nicht
aus der Zeitzone des Browsers; der erste Abschnitt beginnt jetzt."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_ui_v361 import NODE, html, plain, script, source

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("fmt", "esc", "tsOf", "tclock", "wday", "WEEKDAYS", "clock", "timeTicks", "tipKey", "PV_C", "isoOff", "hhmm", "isoClock",
         "HP_OFF", "hpOff", "hpDate", "hpClock", "hpDay", "hpDayKey", "hpHours", "hpTicks", "NX_H", "NX_EMPTY", "nxWin",
         "NX_KIND", "nxKind", "nxMin", "nxJoin", "nxSameVal", "nxSame", "nxRuns", "nxDis", "nxDur", "nxDeg", "nxVal",
         "nxTime", "nxTip", "nxWidths", "nxRender", "drawNext", "nxCard", "NX_MORE")


def run_nx(tmp_path, body: str, tz: str = "UTC"):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    stubs = """
const BOX = {};
const $ = (id) => (BOX[id] = BOX[id] || { innerHTML: "" });
const empty = (box, text) => { box.innerHTML = text; };
let PV = null, NOW;
const nxNow = () => NOW;
"""
    code = stubs + "\n".join(source(js, n) for n in NAMES) + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": tz, "LANG": "de_DE.UTF-8"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# Di 20.10.2026 ab 20:00 Ortszeit (+02:00) – der Browser läuft in UTC. Rechenzeitpunkt 20:03 (Schritte ab 20:00),
# Wechsel 22:00 (Warmwasser Nacht), 23:00 (Heizbetrieb aus), 05:00 (heizt vor), 05:30 (vor dem Aufstehen),
# 09:00 hinter den 12 Stunden; Desinfektion um 02:00
SAMPLE = r"""
const T = (h, m = 0) => new Date(Date.UTC(2026, 9, 20, 20 + h, m)).toISOString().slice(0, 19) + "+02:00";
const st = (h, m, heating, flow, dhw, flow_why, dhw_why, hold) => ({ t: T(h, m), heating, flow, dhw, flow_why, dhw_why, dhw_hold: hold });
const HOME = "jemand zu Hause – Speicher 50–55 °C", NIGHT = "Nacht – Speicher darf bis 40 °C fallen";
const P = { now: T(0, 3), hours: Array.from({ length: 24 }, (_, i) => ({ t: T(i), night: i >= 2 && i < 10, home: 1,
    disinfect: i === 6 ? { at: T(6), temp: 70 } : null })),
  steps: [st(0, 0, true, 44, 55, "Wohnzimmer hält 21 °C", HOME, 50), st(1, 0, true, 43, 55, "Bad hält 21 °C", HOME, 50),
    st(2, 0, true, 43, 45, "Bad hält 21 °C", NIGHT, 40), st(3, 0, false, null, 45, "kein Raum braucht Wärme – Heizbetrieb aus", NIGHT, 40),
    st(9, 0, true, 46, 45, "Wohnzimmer heizt vor bis 06:30", NIGHT, 40),
    st(9, 30, true, 46, 55, "Wohnzimmer heizt vor bis 06:30", "vor dem Aufstehen aufheizen – Speicher 50–55 °C", 50),
    st(13, 0, true, 40, 50, "Wohnzimmer hält 21 °C", "Zapfung erwartet (≈5 K) – Speicher 45–50 °C", 45)] };
const rows = (p, kind, w) => nxRuns(p, kind, w).map((r, i) => [nxTime(p, r, i), NX_KIND[r.k], ...nxVal(r, kind)]);
"""


def test_sections_for_dhw_and_boiler_first_starts_now_only_12_hours(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
const w = nxWin(P), runs = nxRuns(P, "flow");
out({ dhw: rows(P, "dhw"), flow: rows(P, "flow"), x0: w.x0 === tsOf(P.now), span: (w.x1 - w.x0) / 3600,
      end: runs[runs.length - 1].b === w.x1, first: runs[0].a === w.x0 });
""")
    assert r["dhw"] == [["jetzt–22:00", "zu Hause", "Soll 55 °C", "Speicher 50–55 °C"],
                        ["22:00–02:00", "Nacht", "Soll 45 °C", "darf bis 40 °C fallen"],
                        ["02:00–03:00", "Desinfektion", "Soll 70 °C", ""],
                        ["03:00–05:30", "Nacht", "Soll 45 °C", "darf bis 40 °C fallen"],
                        ["05:30–08:03", "heizt auf", "Soll 55 °C", "Speicher 50–55 °C"]]
    # 44 und 43 °C (±1 K) ein Abschnitt; 09:00 (40 °C, Zapfung) liegt hinter den 12 Stunden
    assert r["flow"] == [["jetzt–23:00", "Vorlauf", "Vorlauf 43–44 °C", ""], ["23:00–05:00", "Heizung aus", "aus", ""],
                         ["05:00–08:03", "Vorheizen", "Vorlauf 46 °C", ""]]
    assert r["x0"] and r["span"] == 12 and r["end"] and r["first"]


def test_times_from_backend_offset_not_browser_zone(tmp_path):
    body = SAMPLE + """
const w = nxWin(P), runs = nxRuns(P, "dhw");
out({ rows: rows(P, "dhw"), tip: nxTip(P, "dhw", runs[1], 1), tip0: nxTip(P, "dhw", runs[0], 0),
      ticks: hpTicks(P, tsOf(P.hours[0].t), tsOf(P.hours[0].t) + 24 * 3600).map((x) => x[1]).slice(0, 3), day: hpDay(P, tsOf(T(4))) });
"""
    utc, ny = run_nx(tmp_path, body, "UTC"), run_nx(tmp_path, body, "America/New_York")
    assert utc == ny
    assert utc["tip"].startswith("<b>Nacht</b> · Di 22:00–02:00 (4 h)") and "Nacht – Speicher darf bis 40 °C fallen" in utc["tip"]
    assert utc["tip0"].startswith("<b>zu Hause</b> · jetzt (Di 20:03)–22:00 (1 h 57 min)")
    assert utc["ticks"] == ["20:00", "00:00", "04:00"] and utc["day"] == "Mi"  # volle Ortsstunden, Mitternacht = Mittwoch


def test_merge_and_fold_short_sections_flicker_is_one_block(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
// Warmwasser: 55 → 54 °C (±1 K) ein Abschnitt, 20 min Zapfung dazwischen gehört dazu; 52 °C ist ein neuer
const D = [st(0, 0, true, 44, 55, "", HOME, 50), st(1, 0, true, 44, 54, "", HOME, 49),
  st(2, 0, true, 44, 54, "", "Zapfung erwartet (≈5 K) – Speicher 49–54 °C", 49), st(2, 20, true, 44, 54, "", HOME, 49),
  st(4, 0, true, 44, 52, "", HOME, 47)];
// Kessel: 40 min an, dann 2 h alle 10 min aus/an (37 °C), dann lang an (42 °C)
const F = [st(0, 0, true, 44, 55, "Wohnzimmer hält 21 °C", HOME, 50)];
for (let m = 40; m < 160; m += 10) F.push(st(0, m, m % 20 === 0, m % 20 === 0 ? 37 : null, 55, m % 20 ? "kein Raum braucht Wärme – Heizbetrieb aus" : "Bad heizt auf 21 °C", HOME, 50));
F.push(st(2, 40, true, 42, 55, "Wohnzimmer hält 21 °C", HOME, 50));
// kurz an mitten im Aus
const O = [st(0, 0, false, null, 55, "aus", HOME, 50), st(3, 0, true, 38, 55, "an", HOME, 50), st(3, 10, false, null, 55, "aus", HOME, 50)];
const W = (s) => ({ ...P, steps: s, hours: P.hours.map((h) => ({ ...h, disinfect: null })) });
const f = nxRuns(W(F), "flow");
out({ dhw: rows(W(D), "dhw"), flow: rows(W(F), "flow"), off: rows(W(O), "flow"), tip: nxTip(W(F), "flow", f[0], 0),
      short: f.every((x) => x.b - x.a >= 1800) });
""")
    assert r["dhw"] == [["jetzt–00:00", "zu Hause", "Soll 54–55 °C", "Speicher 49–55 °C"],
                        ["00:00–08:03", "zu Hause", "Soll 52 °C", "Speicher 47–52 °C"]]
    # an/aus im 10-min-Takt: kein Zickzack, sondern ein Abschnitt „zeitweise aus“ mit der Spanne der an-Zeiten
    assert r["flow"] == [["jetzt–22:40", "zeitweise aus", "Vorlauf 37–44 °C", "1 h aus"], ["22:40–08:03", "Vorlauf", "Vorlauf 42 °C", ""]]
    assert r["short"] and "Heizbetrieb aus: 1 h von 2 h 37 min" in r["tip"]
    assert r["off"] == [["jetzt–08:03", "Heizung aus", "aus", "zwischendurch 10 min an"]]


def test_window_follows_clock_and_preview_end(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
const x0 = tsOf(P.now), late = { ...P, hours: P.hours.slice(0, 6) };
out({ later: nxWin(P, x0 + 600).x0 - x0, capped: nxWin(P, x0 + 9 * 3600).x0 - x0, before: nxWin(P, x0 - 600).x0 - x0,
      late: rows(late, "flow").map((x) => x[0]), lateSpan: (nxWin(late).x1 - x0) / 3600 });
""")
    assert r["later"] == 600 and r["capped"] == 7200 and r["before"] == 0
    assert r["late"] == ["jetzt–23:00", "23:00–02:00"] and r["lateSpan"] == pytest.approx(6 - 3 / 60)


def test_render_timeline_and_empty_preview(tmp_path):
    r = run_nx(tmp_path, SAMPLE + """
NOW = tsOf(P.now) + 60; PV = { preview: { ...P, dhw_writes: "schreibt" } }; drawNext();
const a = { dhw: BOX["ch-nx-dhw"].innerHTML, flow: BOX["ch-nx-flow"].innerHTML, nd: BOX["nx-dhw-note"].innerHTML, nf: BOX["nx-flow-note"].innerHTML };
PV = null; drawNext();
const b = [BOX["ch-nx-dhw"].innerHTML, BOX["ch-nx-flow"].innerHTML, BOX["nx-dhw-note"].innerHTML];
PV = { preview: { hours: [], steps: [] } }; drawNext();
const c = BOX["ch-nx-flow"].innerHTML;
PV = { preview: { ...P, dhw_note: "lernt noch: zu wenige Ladungen", steps: P.steps.map((s) => ({ ...s, dhw: null, heating: null, flow: null })) } };
drawNext();
out({ a, b, c, dhw: BOX["ch-nx-dhw"].innerHTML, flow: BOX["ch-nx-flow"].innerHTML, runs: nxRuns(null, "dhw"), win: nxWin({ hours: [] }),
      w: nxWidths([0.9, 0.05, 0.05]) });
""")
    a = r["a"]
    assert a["dhw"].count('role="listitem" tabindex="0"') == 5 and 'role="list"' in a["dhw"]
    assert '<span class="nx-t">jetzt–22:00</span>' in a["dhw"]  # Uhr 20:04: jetzt ist jetzt, nicht 20:00
    assert '<span class="nx-now">jetzt</span>' in a["flow"]
    ticks = re.findall(r'style="left:[\d.]+%">(\d\d:\d\d)<', a["flow"])
    assert ticks == ["21:00", "00:00", "03:00", "06:00"]  # Ortszeit alle 3 h (schmal), Browser in UTC
    assert sum(float(x) for x in re.findall(r"--nx-w:([\d.]+)%", a["flow"])) == pytest.approx(100, abs=0.05)
    assert a["nd"].startswith("HeatPilot setzt dieses Soll selbst.") and "Vorschau 24 h ›" in a["nd"]
    assert a["nf"].startswith("Nur Vorschlag – HeatPilot schreibt nichts an den Kessel.")
    empty = "Noch keine Vorschau – sobald HeatPilot die nächsten Stunden berechnet hat, steht hier der Plan."
    assert r["b"] == [empty, empty, ""] and r["c"] == empty
    assert r["dhw"] == "Kein Warmwasser-Soll – lernt noch: zu wenige Ladungen." and r["flow"] == "Kein Vorlauf-Soll."
    assert r["runs"] == [] and r["win"] is None
    assert r["w"][1] == pytest.approx(9) and sum(r["w"]) == pytest.approx(100)  # Mindestbreite, Rest nach Dauer


def test_kinds_from_reasons(tmp_path):
    r = run_nx(tmp_path, """
const k = (dhw_why) => NX_KIND[nxKind({ dhw_why }, "dhw")];
out([k("Urlaub – Speicher darf bis 40 °C fallen"), k("niemand zu Hause – Speicher darf bis 40 °C fallen"),
     k("vor der Ankunft aufheizen – Speicher 50–55 °C"), k("große Zapfung üblich (≈14 K) – Speicher 54–59 °C"),
     k("hier wird oft gezapft (in 40 % der Fälle) – Speicher 50–55 °C"), k("Anwesenheit unbekannt – Speicher 50–55 °C"),
     NX_KIND[nxKind({ heating: true, flow_why: "Wohnzimmer heizt vor bis 06:00" }, "flow")],
     NX_KIND[nxKind({ heating: true, flow_why: "Bad heizt auf 21 °C" }, "flow")], NX_KIND[nxKind({ heating: false }, "flow")]]);
""")
    assert r == ["Urlaub", "niemand zu Hause", "heizt auf", "Zapfung erwartet", "Zapfung erwartet", "zu Hause",
                 "Vorheizen", "Vorlauf", "Heizung aus"]


def test_cards_markup_tokens_and_one_preview_request():
    page = html()
    for page_id, k in (("ov-warmwasser", "dhw"), ("ov-kessel", "flow")):
        part = page.split(f'<div id="{page_id}" hidden>')[1].split('<div id="ov-')[0]
        card = part.split(f'id="nx-{k}"')[1].split("</div>\n  </div>")[0]
        assert "Nächste 12 Stunden" in card and f'id="ch-nx-{k}"' in card and f'id="nx-{k}-note"' in card
        assert "Noch keine Vorschau" in plain(card) and "lg-nx" not in card and "nx-line" not in card
    ww = page.split('<div id="ov-warmwasser" hidden>')[1]
    assert ww.index('id="dhw-tiles"') < ww.index('id="nx-dhw"') < ww.index("Wann gezapft wird")
    css = page.split("/* Nächste 12 Stunden (0.46.3)")[1].split(".nx-note:empty")[0]
    assert "@container (min-width: 600px)" in css and not re.search(r"#[0-9a-fA-F]{3,6}\b", css)
    js = script()
    assert len(re.findall(r'getJSON\("api/preview"\)', js)) == 1
    assert "drawNext();" in source(js, "loadPreview")
    assert 'OV_PAGE === "warmwasser" || OV_PAGE === "kessel") drawNext()' in source(js, "drawOverview")
    for name in ("nxRender", "nxTip", "nxTime"):  # Uhrzeiten nur aus der Ortszeit des Backends
        assert "tclock(" not in source(js, name) and "toLocaleTimeString" not in source(js, name)
    assert "hpClock(p, t)" in source(js, "pvTip") and "hpTicks(p, a, b)" in source(js, "pvFlowChart")
    assert "showTip(e, nxTip(" in source(js, "nxRender") and 'addEventListener("focus"' in source(js, "nxRender")


def test_version_changelog_docs():
    from heatpilot import __version__
    assert __version__ >= "0.46.3"
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = log.split("\n## 0.46.3\n")[1].split("\n## ")[0]
    for t in ("Zeitstrahl", "Ortszeit", "zeitweise aus", "tests/test_ui_v463.py"):
        assert t in head, t
    assert "Zeitstrahl" in (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
