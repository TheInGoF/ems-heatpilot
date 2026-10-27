"""0.39.1: Oberfläche zur Fußbodenheizung je Raum – statische Prüfung und Texte in Node.

Wie tests/test_ui_v39.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird der Einbau (Spalte „Fußboden“ in
Einstellungen → Räume: Auswahl des Anschlusses statt des Häkchens, darunter „höchster Vorlauf (°C)“ nur bei RTL-Ventil,
direkt am Kessel und unbekanntem Anschluss; collect() schreibt Anschluss, Grenze und floor_heating_rooms konsistent),
dass die Konstanten zum Backend passen, die Zellen und das Speichern in Node mit erfundenen Räumen – bis durch die
echte Prüfung ``config.validate`` und ``floor_limit`` –, die Zeile „Womit gerechnet wurde“ der Vorschau und der Hinweis
in „Bereit für die Steuerung?“ aus dem echten Backend. Darstellung bei 390/1100 px und hell/dunkel prüft das
Playwright-Skript, das nicht Teil der Suite ist.
"""

import json
import os
import re
import subprocess

import pytest

from heatpilot import floor_limit as fl
from heatpilot import preview as pv
from heatpilot import readiness
from heatpilot.config import Config, validate
from heatpilot.web import clean
from test_control34 import MON, ts_of, wz
from test_floor391 import bad, cfg_with, preview_house
from test_ui_v361 import NODE, between, html, plain, script, source

STUBS = """
let SET = { rooms: [], floor_heating_rooms: [] };
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "" });
// Zeile der Raum-Tabelle ohne DOM: querySelector nach Klasse
const row = (vals) => ({ querySelector: (sel) => vals[sel] });
"""
NAMES = ("fmt", "esc", "clock", "FLOOR_KINDS", "FLOOR_MAX", "FLOOR_HINT", "floorKind", "floorCell", "floorSync",
         "floorOf", "kelvin", "PV_NOTE", "pvBasis", "READY", "ROOM_READY_CSS", "readyDot", "EMS_UOM", "boilerHtml",
         "readyBoxHtml")


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


# ------------------------------------------------------------------ Einbau
def test_room_table_has_floor_selection_instead_of_checkbox():
    js, page = script(), html()
    rows = between(js, "function roomRows()", "function applyAreas()")
    assert '${field("Fußboden", floorCell(c.id, r))}' in rows  # 0.43: Raumliste, Fußboden unter „Einzelheiten“
    assert 'class="r-floor" ${(SET.floor_heating_rooms' not in js  # das Häkchen ist in der Auswahl aufgegangen
    info = between(js, 'card("Räume", `', "${ENT.some(")
    for text in ("<b>mit Mischer</b>", "<b>mit RTL-Ventil</b>", "<b>direkt am Kessel</b>", "<b>höchste Vorlauf</b>",
                 "leer = ${FLOOR_MAX.def} °C", "HeatPilot plant nie einen Vorlauf darüber"):
        assert text in info, text
    assert "Fußbodenheizung ohne Stellventil (z. B. festes RTL)" not in js
    # Hilfen stehen vor roomRows im Abschnitt der Raum-Tabelle (die älteren Prüfungen schneiden ihn so aus)
    region = between(js, "const sibling =", "// „Bereiche übernehmen“")
    for name in ("const FLOOR_KINDS", "function floorCell(", "function floorSync(", "function floorOf("):
        assert name in region, name
    assert "table.form select.r-floor { min-width: 132px; } table.form input.r-floor-max" in page
    assert ".r-floor-box[hidden] { display: none; }" in page


def test_collect_writes_floor_consistently():
    coll = between(script(), "function collect()", "async function saveSettings")
    assert "const c = tr.dataset.climate, fl = floorOf(tr);" in coll
    assert "floor_type: fl.floor_type, floor_max_flow: fl.floor_max_flow });" in coll
    assert "if (fl.floor) out.floor_heating_rooms.push(c);" in coll
    assert '.r-floor").checked' not in coll


def test_ui_knows_no_write_path_for_floor():
    """Nur Einstellungen: die Fußboden-Teile fragen nichts ab und schicken nichts – gespeichert wird mit „Speichern“."""
    js = script()
    part = between(js, "// Fußbodenheizung je Raum (0.39.1)", "function roomRows()")
    for word in ("fetch(", "getJSON(", "api/", "POST", "method:"):
        assert word not in part, word


def test_constants_match_backend(tmp_path):
    r = run_js(tmp_path, "out({ kinds: Object.keys(FLOOR_KINDS), max: FLOOR_MAX, hint: FLOOR_HINT });")
    assert r["kinds"] == ["", *fl.KINDS, fl.UNKNOWN]
    assert r["max"] == {"def": fl.DEFAULT_MAX_FLOW, "min": fl.MAX_FLOW_MIN, "max": fl.MAX_FLOW_MAX}
    assert set(r["hint"]) == {*fl.KINDS, fl.UNKNOWN}


# ------------------------------------------------------------------ Zellen
def options(cell: str) -> list[tuple[str, bool]]:
    return [(m.group(1), bool(m.group(2))) for m in re.finditer(r'<option value="([^"]*)"( selected)?>', cell)]


def test_floor_cell_per_connection(tmp_path):
    r = run_js(tmp_path, """
      SET = { rooms: [], floor_heating_rooms: ["climate.alt"] };
      out({ none: floorCell("climate.neu", {}), rtl: floorCell("climate.bad", { floor_type: "rtl", floor_max_flow: 52.5 }),
            mix: floorCell("climate.kueche", { floor_type: "mischer", floor_max_flow: 40 }),
            direct: floorCell("climate.kind", { floor_type: "direkt" }), old: floorCell("climate.alt", { climate: "climate.alt" }),
            evil: floorCell("climate.x", { floor_type: "<b>", floor_max_flow: '"><b>' }) });""")
    none, rtl, mix, direct, old = (r[k] for k in ("none", "rtl", "mix", "direct", "old"))
    assert options(none) == [("", True), ("mischer", False), ("rtl", False), ("direkt", False)]  # kein „Anschluss wählen“
    assert '<label class="r-floor-box" hidden>' in none and '<div class="hint muted r-floor-hint"></div>' in none
    assert ("rtl", True) in options(rtl) and '<label class="r-floor-box">höchster Vorlauf (°C) <input type="number" ' \
           'class="r-floor-max" min="25" max="70" step="0.5" value="52.5" placeholder="45"' in rtl
    assert "gilt immer – das RTL öffnet, sobald der Rücklauf kalt ist" in rtl  # seit 0.46.5 (vorher: solange angefordert)
    assert ("mischer", True) in options(mix) and '<label class="r-floor-box" hidden>' in mix
    assert "Mischer regelt selbst – keine Grenze" in mix
    assert ("direkt", True) in options(direct) and 'value=""' in direct and ">gilt immer<" in direct
    # Einstellungen vor 0.39.1: nur in floor_heating_rooms – „Anschluss wählen“ ist gewählt, das Feld sichtbar
    assert options(old)[-1] == ("unbekannt", True) and "Anschluss wählen</option>" in old
    assert '<label class="r-floor-box">' in old and "gilt immer, bis der Anschluss gewählt ist" in old
    # unbekannter Wert in den Daten: kein Fußboden gewählt, nichts unmaskiert
    assert options(r["evil"])[0] == ("", True) and "<b>" not in r["evil"] and 'value="&quot;>&lt;b>"' in r["evil"]


def test_floor_sync_toggles_field_and_hint(tmp_path):
    r = run_js(tmp_path, """
      const box = { hidden: true }, hint = { textContent: "" };
      const td = { querySelector: (s) => s === ".r-floor-box" ? box : hint };
      const res = [];
      for (const v of ["rtl", "mischer", "", "direkt"]) { floorSync({ parentNode: td, value: v }); res.push([v, box.hidden, hint.textContent]); }
      out(res);""")
    # seit 0.46.5 gilt die RTL-Grenze immer
    assert r == [["rtl", False, "gilt immer – das RTL öffnet, sobald der Rücklauf kalt ist"], ["mischer", True, "Mischer regelt selbst – keine Grenze"],
                 ["", True, ""], ["direkt", False, "gilt immer"]]


def test_save_roundtrip_through_backend(tmp_path):
    """Was collect() je Zeile schickt (floorOf), geht durch die echte Prüfung und ergibt die erwarteten Fußboden-Räume."""
    r = run_js(tmp_path, """
      const rows = { "climate.wz": ["", "50"], "climate.bad": ["rtl", "55"], "climate.kueche": ["mischer", "40"],
                     "climate.kind": ["direkt", ""], "climate.alt": ["unbekannt", ""] };
      out(Object.entries(rows).map(([c, [k, max]]) => [c, floorOf(row({ ".r-floor": { value: k }, ".r-floor-max": { value: " " + max } }))]));""")
    got = dict((c, v) for c, v in r)
    assert got["climate.wz"] == {"floor_type": "", "floor_max_flow": "", "floor": False}
    assert got["climate.bad"] == {"floor_type": "rtl", "floor_max_flow": "55", "floor": True}
    assert got["climate.kueche"] == {"floor_type": "mischer", "floor_max_flow": "", "floor": True}  # Mischer: keine Grenze
    assert got["climate.alt"] == {"floor_type": "", "floor_max_flow": "", "floor": True}  # bleibt „unbekannt“
    body = {"rooms": [{"climate": c, "floor_type": v["floor_type"], "floor_max_flow": v["floor_max_flow"]} for c, v in r],
            "floor_heating_rooms": [c for c, v in r if v["floor"]]}
    ok, errors = validate(body)
    assert errors == []
    cfg = Config.from_raw(ok)
    from heatpilot.discovery import Room
    rooms = [Room(c.split(".")[1], c) for c, _ in r]
    assert [(f.climate, f.kind, f.max_flow) for f in fl.floor_rooms(cfg, rooms)] == [
        ("climate.bad", "rtl", 55.0), ("climate.kueche", "mischer", 45.0), ("climate.kind", "direkt", 45.0),
        ("climate.alt", "unbekannt", 45.0)]
    # und zurück in die Tabelle: dieselbe Auswahl
    back = run_js(tmp_path, f"SET = {json.dumps(cfg.to_raw())};"
                            "out(SET.rooms.map((x) => [x.climate, floorKind(x.climate, x)]));")
    assert dict(back) == {"climate.wz": "", "climate.bad": "rtl", "climate.kueche": "mischer", "climate.kind": "direkt",
                          "climate.alt": "unbekannt"}


# ------------------------------------------------------------------ Vorschau und „Bereit für die Steuerung?“
def test_preview_basis_names_floor_rule(tmp_path):
    eng, _ = preview_house(tmp_path, cfg_with("rtl", 50), bad_temp=20.0)
    p = as_json(pv.preview(eng, ts_of(MON, 12)))
    r = run_js(tmp_path, f"const P = {json.dumps(p)}; out({{ b: pvBasis(P), old: pvBasis({{ ...P, floor: undefined }}) }});")
    # seit 0.46.5 gilt die RTL-Grenze immer
    assert plain(r["b"]).endswith("Fußboden-Grenze für den Vorlauf: Bad höchstens 50 °C, immer (RTL-Ventil).")
    assert "Fußboden-Grenze" not in r["old"]  # ältere Antwort ohne das Feld: nichts
    assert any("begrenzt durch Fußboden Bad" in h["flow_reason"] for h in p["hours"])  # Stunde und Tooltip zeigen es


def test_ready_box_shows_floor_notes(tmp_path):
    eng, _ = preview_house(tmp_path, cfg_with(None, legacy=True))
    res = as_json(readiness.assess(eng, ts_of(MON, 12)))
    r = run_js(tmp_path, f"out(readyBoxHtml({json.dumps(res)}, '', true, false));")
    text = plain(r)
    assert "Vorlauf-Grenze der Fußbodenheizung jetzt: Fußboden Bad (Anschluss unbekannt – wie direkt am Kessel): " \
           "höchstens 45 °C" in text
    assert "Fußboden Bad: Anschluss wählen (Einstellungen → Räume: Mischer, RTL-Ventil oder direkt am Kessel)" in text
    assert '<li class="note">Fußboden Bad: Anschluss wählen' in r
    assert [x.name for x in fl.floor_rooms(eng.cfg, [wz(), bad()])] == ["Bad"]


# ================================================================== Teil B: Seite „Entitäten“
# Umschalter „Allgemein | Entitäten“, Gruppen aus GET api/entity_map, Zeilen mit Status, Auswahl mit Suche, „Automatik“,
# Filter „nur fehlende“; Speichern über api/settings (emCollect). Die Zeilen rechnet die Oberfläche mit denselben Regeln
# wie entity_map – geprüft mit Antworten der echten make_app (api/entities, api/entity_map).
from heatpilot import entity_map as em  # noqa: E402
from test_entity_map391 import base_states, registry  # noqa: E402

EM_STUBS = """
let SET = { rooms: [], persons: [] }, SUG = { persons: [] }, ENT = [], DHWC = null, NOTIFY = null;
const ELS = {};
const $ = (id) => ELS[id] || null;
const document = { querySelectorAll: () => [], querySelector: () => null };
const notifyHtml = () => "<div id=\\"notify-list\\">Ziele</div>";
function dhwModeHint() {}
"""
EM_NAMES = ("esc", "isTemp", "isSensor", "PICKERS", "dhwRank", "personRows", "EMAP", "EM_BY", "EM_OPEN", "EM_FOLD",
            "EM_MAX", "EM_PROBLEMS", "EM_BADGE", "EM_CUSTOM", "emInit", "emFits", "emOptions", "emItem", "emState",
            "emValue", "emHas", "emStateOf", "emSaved", "emCounts", "emRoomCount", "emCountText", "setNav", "expert",
            "emGroup", "emReq", "emRow", "emBadge", "GONE_HINT", "emNow", "emEnt", "emEdit", "emExtra", "emPersons",
            "emRef", "emCollect", "SET_PAGES", "SET_KEYS", "setInfo", "navRow", "PAGE_GROUPS", "EM_PAGE_OF", "EXPERT_GROUPS",
            "pageMissing", "emInline", "eid", "more",
            "DHW_LABEL", "BOILER_LABEL")  # 0.43: Startliste der Einstellungen, Entitäten bei ihrer Funktion
SECTION_B = "// ---------------------------------------------------------------- Entitäten (0.39.1)"


def run_em(tmp_path, data: dict, body: str):
    """``body`` mit den Funktionen der Seite „Entitäten“ in Node; ``data``: ENT, EMAP (Antworten der echten make_app)."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    code = (EM_STUBS + "\n".join(source(js, n) for n in EM_NAMES) + "\nconst out = (v) => console.log(JSON.stringify(v));\n"
            + f"ENT = {json.dumps(data['ent'])}; EMAP = {json.dumps(data['map'])}; emInit(EMAP);\n" + body)
    f = tmp_path / "em.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def api_answers(tmp_path, raw=None, drop=(), ems=None) -> dict:
    """api/entities und api/entity_map aus der echten make_app (Haus aus test_entity_map391)."""
    import asyncio
    from types import SimpleNamespace

    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot.discovery import discover
    from heatpilot.engine import Engine
    from heatpilot.store import Store
    from heatpilot.web import make_app

    tmp_path.mkdir(parents=True, exist_ok=True)
    states = base_states()
    for e in drop:
        states.pop(e)
    lst, reg, cfg = [{"entity_id": k, **v} for k, v in states.items()], registry(states), Config.from_raw(raw or {})
    eng = Engine(cfg, Store(tmp_path / "ui.db"), discover(lst, reg, cfg))
    eng.states, eng.ems = dict(states), ems or {}

    async def get_services():
        return {"notify": {"mobile_app_tablet": {}}}

    async def registry_():
        return reg

    async def suggestions():
        return discover(lst, reg, Config.from_raw({}), all_rooms=True).as_dict(private=False)

    app = SimpleNamespace(eng=eng, cfg=cfg, status="läuft", registry=registry_, suggestions=suggestions,
                          ha=SimpleNamespace(get_services=get_services))

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            return {"ent": await (await client.get("/api/entities")).json(),
                    "map": await (await client.get("/api/entity_map")).json()}
        finally:
            await client.close()

    return asyncio.run(go())


# ------------------------------------------------------------------ Einbau
def test_settings_split_into_general_and_entities():
    js, page = script(), html()
    rs = between(js, "function renderSettings()", "function addVac()")
    # 0.58: drei Seiten Räume, Kessel, Haus (Styleguide) – die Teile in ihrer Reihenfolge, „exp-“ = unter „Experte“
    assert 'emInit(EMAP);' in rs and '$("set-menu").innerHTML = setNav();' in rs and '$("set-body").innerHTML = datalists() +' in rs
    assert re.findall(r'part\("([\w-]+)"', rs) == ["haus", "raeume", "exp-haus", "raeume", "haus", "kessel", "kessel", "haus",
                                                 "haus", "exp-haus"]
    assert 'SET_KEYS.map((k) => page(k, (P[k] || "") + expert(k, P["exp-" + k]))).join("")' in rs
    assert "emSum();" in rs
    titles = re.findall(r'card\("([^"]+)"', rs)
    assert titles == ["Verbindung", "Haus", "Gas & Kosten", "Räume", "Personen", "Urlaub", "Warmwasser steuern", "Temperaturen",
                      "Kesseldaten", "Kessel steuern", "Benachrichtigungen", "Karten für Home Assistant", "Erweitert"]  # Steuerung: controlCard
    # „Allgemein“ ohne Entitätsfelder – höchstens ein Verweis
    for gone in ("Zuordnungen (Experten)", 'class="ov"', "outdoor-list", "power-list", "f-gas_sensor", "f-weather_entity",
                 "f-demand_sensor", "f-vacation_entity", "list=\"dl-any\"", "ROLES", "DISC."):
        assert gone not in js, gone
    assert 'card("Gas & Kosten", `${emRef("gas_sensor", "gas", "Gaszähler")}' in rs
    assert '${emr("vacation_entity")}' in rs  # 0.43: Urlaubs-Schalter bei Anwesenheit & Urlaub
    assert "${powerRef(power, powerEx, missing)}" in between(js, "function controlCard(s)", "function modeHint()")
    assert 'id="dhw-entity-ref">Entität des Warmwasser-Solls: ${dhwEntityHint(s, x)}' in js
    # Räume bleiben in „Allgemein“ (Name, Gruppe, Komfort, Fußboden …) – die Seite „Entitäten“ springt dorthin
    assert '<input class="r-valve" list="dl-sensor"' in between(js, "function roomRows()", "function applyAreas()")
    load = between(js, "async function loadSettings()", "let OV_STALE")
    assert 'try { EMAP = await getJSON("api/entity_map"); if (EMAP.starting) EMAP = null; }' in load
    assert "catch (e) { EMAP = { error: e.message || String(e) }; }" in load
    assert "return emCollect(out);" in between(js, "function collect()", "async function saveSettings")
    assert 'if ($("em-persons")) out.persons =' in between(js, "function collect()", "async function saveSettings")
    assert len(re.findall(r'getJSON\("api/entity_map"\)', page)) == 1  # nur lesen, an einer Stelle
    assert 'openDiag("ready-card");' in between(js, "function openReadiness() {", "// Daten aus GET api/heat")


def test_entities_section_knows_no_write_path():
    """Nur Anzeige und Felder: die Seite fragt nichts ab und schickt nichts – gespeichert wird mit „Speichern“."""
    part = re.sub(r"^\s*//.*$", "", between(script(), SECTION_B, "function renderSettings()"), flags=re.M)  # ohne Kommentare
    for word in ("fetch(", "getJSON(", "api/", "POST", "method:"):
        assert word not in part, word


def test_constants_and_filter_match_backend(tmp_path):
    data = {"ent": [], "map": {"groups": []}}
    samples = [("sensor.t", "°C", None), ("number.t", None, "temperature"), ("sensor.h", "%", "humidity"),
               ("binary_sensor.b", None, None), ("sensor.e", "kWh", "energy"), ("sensor.p", "W", None),
               ("sensor.p2", None, "power"), ("weather.w", None, None), ("input_boolean.u", None, None),
               ("climate.c", None, None), ("sensor.g", "m³", "gas"), ("select.m", None, None), ("counter.c", None, None)]
    kinds = [k for k in em.KINDS if k not in em.PICKER]
    filters = {k: em.filter_of(k) for k in kinds}
    ent = [{"id": i, "unit": u, "device_class": d} for i, u, d in samples]
    r = run_em(tmp_path, data, f"""const F = {json.dumps(filters)}, E = {json.dumps(ent)};
      out({{ problems: EM_PROBLEMS, badges: Object.keys(EM_BADGE), fits: Object.fromEntries(Object.entries(F).map(([k, f]) =>
        [k, E.map((e) => emFits(e, f))])) }});""")
    assert r["problems"] == list(em.PROBLEMS)
    assert set(r["badges"]) >= {em.SOURCE_SET, em.SOURCE_AUTO, em.SOURCE_EMS, em.SOURCE_MISSING, *em.PROBLEMS, em.ISSUE_NOT_SET}
    for k in kinds:  # dieselbe Regel wie entity_map.fits
        assert r["fits"][k] == [em.fits(i, u, d, filters[k]) for i, u, d in samples], k


def test_rows_compute_like_backend(tmp_path):
    """Jede Zeile: Quelle, Zustand und Ersatz in der Oberfläche wie im Backend – mit Antworten der echten make_app."""
    cases = [({"rooms": [{"climate": "climate.wz", "name": "Wz", "humidity": "sensor.wz_feuchte"}],
               "overrides": ["boiler_temp=sensor.gibt_es_nicht"], "notify_targets": ["notify.handy_anna", "notify.weg"]}, (), None),
             ({"dhw_control": "heatpilot"}, ("binary_sensor.boiler_burngas", "number.boiler_dhw_seltemp"), None),
             ({}, ("binary_sensor.boiler_burngas", "sensor.boiler_outdoortemp"), {"boiler": {"curburnpow": 30, "rettemp": 38}})]
    for i, (raw, drop, ems) in enumerate(cases):
        data = api_answers(tmp_path / str(i), raw, drop, ems)
        r = run_em(tmp_path, data, """out(Object.values(EM_BY).map((e) => { const s = emStateOf(e.key);
          return [e.key, s.source, s.issue, s.problem, s.alt ? EM_BY[s.alt].label : null]; }));""")
        back = {e["key"]: e for g in data["map"]["groups"] for e in g["entries"]}
        for key, source, issue, problem, alt in r:
            e = back[key]
            assert [source, issue, problem, alt] == [e["source"], e["issue"], e["problem"], e["covered_by"]], key
        c = run_em(tmp_path, data, "out(emCounts(Object.values(EM_BY)));")
        assert {k: c[k] for k in ("total", "found", "missing", "unavailable")} == {
            k: data["map"]["counts"][k] for k in ("total", "found", "missing", "unavailable")}


def test_picker_options_suggestion_first_fitting_then_unfit(tmp_path):
    data = api_answers(tmp_path, {"rooms": [{"climate": "climate.wz", "name": "Wz"}]})
    r = run_em(tmp_path, data, """const ids = (o) => o.list.map((x) => [x.id, x.fit, x.det]);
      out({ gas: emOptions(EM_BY.gas_sensor, ""), temp: emOptions(EM_BY.gas_sensor, "speicher"),
            flow: emOptions(EM_BY.flow_temp, "").list.slice(0, 2).map((x) => [x.id, x.det]),
            two: ids(emOptions(EM_BY.flow_temp, "boiler außen")),
            power: ids(emOptions(EM_BY.power_sensors, "", ["sensor.hausleistung"])),
            dhw: ids(emOptions(EM_BY.dhw_set_entity, "")) });""")
    assert [[x["id"], x["fit"], x["det"]] for x in r["gas"]["list"]] == [["sensor.gaszaehler", True, True],
                                                                        ["sensor.boiler_nrgtotal", True, False]]
    assert r["gas"]["hidden"] > 0 and r["gas"]["more"] == 0  # unpassende erst mit Suchbegriff
    assert [x["id"] for x in r["temp"]["list"]] == ["sensor.boiler_dhw_curtemp"] and r["temp"]["list"][0]["fit"] is False
    assert r["flow"][0] == ["sensor.boiler_curflowtemp", True]  # Vorschlag der Erkennung zuerst
    assert r["two"] == [["sensor.boiler_outdoortemp", True, False]]  # alle Wörter, auch im Namen
    assert r["power"] == []  # schon gewählt: nicht noch einmal
    assert r["dhw"] == [["number.boiler_dhw_seltemp", True, True]]  # PICKERS.dhw: nur, was als Warmwasser-Soll taugt


def test_rows_render_status_and_escape(tmp_path):
    data = api_answers(tmp_path, {"overrides": ["flow_temp=sensor.gibt_es_nicht", 'x"><b>=sensor.y']},
                       drop=("binary_sensor.boiler_burngas",))
    data["map"]["extra_overrides"].append({"role": "<i>", "entity": "sensor.z"})
    r = run_em(tmp_path, data, """const row = (k) => emRow(EM_BY[k]);
      out({ flow: row("flow_temp"), bp: row("burner_power"), dhw: row("dhw_active"), auto: row("dhw_temp"),
            nav: setNav(), sum: emCountText(emCounts(EMAP.groups[0].entries)), extra: emExtra(),
            page: expert("kessel") + expert("haus"), inl: emInline("personen") + emInline("benachrichtigungen"),
            err: (() => { const m = EMAP; EMAP = { error: "<b>kaputt" }; const h = expert("haus"); EMAP = m; return h; })(),
            none: (() => { const m = EMAP; EMAP = null; const h = expert("haus"); EMAP = m; return h; })(),
            ref: emRef("gas_sensor", "gas", "Gaszähler"), rooms: emRoomCount(EMAP.groups[7].entries) });""")  # 0.41: Kessel über HeatPilot davor
    flow = plain(r["flow"])
    assert flow.startswith("Vorlauftemperatur (Ist) nötig Daraus lernt HeatPilot") and "nicht verfügbar" in flow
    assert "sensor.gibt_es_nicht nicht in Home Assistant" in flow and 'data-issue="1"' in r["flow"]
    assert 'value="sensor.gibt_es_nicht"' in r["flow"] and 'class="btn small em-auto"' in r["flow"]
    assert "fehlt eines genügt: Brennerleistung (%) oder Brenner an (Gas)" in plain(r["bp"])
    assert "Ersatz kein eigener Wert – Speichertemperatur genügt" in plain(r["dhw"])
    assert "automatisch gefunden sensor.boiler_dhw_curtemp Speicher · 51.5 °C · Keller" in plain(r["auto"])
    assert " disabled>Automatik</button>" in r["auto"]  # schon automatisch
    # 0.58: Startseite mit drei Kacheln – rot je Seite nur, wenn Nötiges fehlt (2 beim Kessel)
    assert re.findall(r'data-page="(\w+)"', r["nav"]) == ["raeume", "kessel", "haus"] and 'class="nav-list set-tiles"' in r["nav"]
    assert '<span class="em-alert" id="em-alert-kessel">2</span>' in r["nav"] and 'id="em-alert-haus" hidden' in r["nav"]
    assert r["sum"] == "6 von 39 gefunden · 2 nötige fehlen · 2 nicht verfügbar"  # 0.41: Heizbetrieb aktiviert
    assert "<i>" not in r["extra"] and "&lt;i>=sensor.z" in r["extra"] and 'x&quot;>&lt;b>=sensor.y' in r["extra"]
    groups = re.findall(r'<details class="card em-group" id="em-g-(\w+)"', r["page"])
    # 0.43: „Entitäten“ zeigt nur gelesene Werte; Schreib-Entitäten, Räume, Personen und Ziele stehen bei ihrer Funktion
    assert groups == ["kessel", "regler", "aussen", "gas", "strom"]
    assert 'Weitere Werte, die die Erkennung nicht gefunden hat <span class="muted">(' in r["page"]
    assert 'id="em-persons"' in r["inl"] and '<div id="notify-list">Ziele</div>' in r["inl"]
    assert '<details class="expert" id="exp-kessel">' in r["page"] and '<details class="expert" id="exp-haus">' in r["page"]
    assert "<b>kaputt" not in r["err"] and "Nicht lesbar: &lt;b>kaputt" in r["err"] and "Speichern ändert sie nicht" in r["err"]
    assert r["none"] == ""  # nichts geladen: kein „Experte“
    assert plain(r["ref"]) == "Gaszähler: sensor.gaszaehler automatisch gefunden – ändern unter Experte"
    assert r["rooms"] == "1 Raum · 2 Sensoren gewählt"  # automatisch: Feuchte und Heizleistung am Gerät


def test_collect_sends_overrides_and_fields_through_settings(tmp_path):
    data = api_answers(tmp_path, {"overrides": ["flow_temp=sensor.boiler_curflowtemp", "rolle_alt=sensor.alt"],
                                  "power_sensors": ["sensor.hausleistung"], "notify_targets": ["notify.handy_anna"]})
    r = run_em(tmp_path, data, """const same = emCollect({ persons: ["person.anna"] });
      EM_VAL.flow_temp = ""; EM_VAL.burner_power = " sensor.boiler_curflowtemp "; EM_VAL.gas_sensor = "sensor.gaszaehler";
      EM_VAL.outdoor_sensors = ["sensor.boiler_outdoortemp"]; EM_VAL.power_sensors = []; EM_EXTRA = [];
      const changed = emCollect({});
      EMAP = { error: "x" };
      out({ same, changed, err: emCollect({}) });""")
    s = r["same"]
    assert s["overrides"] == ["flow_temp=sensor.boiler_curflowtemp", "rolle_alt=sensor.alt"]
    assert s["power_sensors"] == ["sensor.hausleistung"] and s["gas_sensor"] == "" and s["outdoor_sensors"] == []
    assert s["persons"] == ["person.anna"] and "notify_targets" not in s  # eigene Tabellen (collect)
    assert set(s) == {"overrides", "persons", "dhw_set_entity", "outdoor_sensors", "weather_entity", "gas_sensor",
                      "demand_sensor", "power_sensors", "power_exclude", "vacation_entity",
                      "flow_set_entity", "heating_switch_entity",  # Weg B seit 0.41
                      "disinfect_entity",  # Desinfektion auslösen seit 0.42
                      "period_set_entity"}  # Antipendelzeit seit 0.48
    c = r["changed"]
    assert c["overrides"] == ["burner_power=sensor.boiler_curflowtemp"] and c["gas_sensor"] == "sensor.gaszaehler"
    assert c["outdoor_sensors"] == ["sensor.boiler_outdoortemp"] and c["power_sensors"] == []
    assert r["err"] == {}  # Seite nicht lesbar: nichts davon – das Gespeicherte bleibt
    # durch die echte Prüfung: gültig, Zuordnung hin und zurück
    clean, errors = validate({**c, "persons": []})
    assert errors == [] and Config.from_raw(clean).overrides == {"burner_power": "sensor.boiler_curflowtemp"}
    back = Config.from_raw({**clean, "overrides": s["overrides"]}).to_raw()["overrides"]
    assert back == s["overrides"]


def test_entities_css_tokens_and_small_functions():
    page = html()
    css = between(page, "/* Entitäten (0.39.1)", "</style>")
    root = between(page, ":root {", "}")
    tokens = set(re.findall(r"var\((--[\w-]+)\)", css))
    assert tokens and all(t + ":" in root for t in tokens), tokens  # nur Farben, die es in beiden Themen gibt
    assert "@media (max-width: 760px) { .em-row { grid-template-columns: minmax(0, 1fr); gap: 6px; } }" in css
    assert ".em-list[hidden] { display: none; }" in css
    part = between(script(), SECTION_B, "function renderSettings()")
    names = re.findall(r"^(?:async )?function (\w+)\(", part, re.M)
    assert {"emInit", "emState", "emOptions", "emRow", "emNow", "emEdit", "emOpen", "emPick", "emAuto", "emCollect",
            "setNav", "setPage", "emGo", "emRoom"} <= set(names)
    for name in names:
        assert source(part, name).count("\n") <= 60, name
