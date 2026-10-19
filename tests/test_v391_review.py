"""Nachprüfung 0.39.1: die Funde der Prüfung, nachgestellt und behoben.

1. Vorschau: RTL-Fußboden mit nicht verfügbarem Thermostat – die Grenze gilt in jeder Stunde (unbekannt = sicher), wie
   in „Bereit für die Steuerung?“ und wie es Docstring, Doku und Planungspapier sagen.
2. Vorschau „jetzt“ und „Bereit für die Steuerung?“ nehmen bei RTL dieselbe Anforderung (0,1 K unter dem Soll, Ventil
   über 5 %, hvac_action); ohne Raummodell bleibt sie, solange das Soll wie jetzt ist.
3. ``hvac_action`` „preheating“ (Home Assistant: heizt vor) zählt als Anforderung.
4. Seite „Entitäten“: ein Suchbegriff wird nie zum Wert – weder beim Verlassen des Felds noch nach Escape; frei
   eintragen nur als ``domain.name`` mit Enter; Speichern liest nur die Wahl, nie den Feldinhalt.
5. Keine Verweise auf Karten, die es nicht mehr gibt („Einstellungen → Messwerte“, „→ Personen“ …).
6. Außenfühler am Kessel nur über EMS-ESP: als „nur Anzeige“ gekennzeichnet – als Außentemperatur dient er nicht.
7. Feld „höchster Vorlauf“ in halben Grad wie das Backend.
8. Alte bzw. von Hand geänderte Einstellungen (Zuordnungen unlesbar, ``floor_heating_rooms`` als Text) laden ohne Fehler.

Alle Häuser, Räume und Werte sind erfunden.
"""

import json
import os
import re
import subprocess

import pytest

from heatpilot import entity_map as em
from heatpilot import floor_limit as fl
from heatpilot import preview as pv
from heatpilot import readiness
from heatpilot.config import Config, validate
from heatpilot.discovery import discover
from test_control34 import MON, climate, make, ts_of, wz
from test_entity_map391 import PKG, ROOT, by_key, st
from test_entity_map391 import build as em_build
from test_floor391 import bad, cfg_with, preview_house
from test_ui_v361 import NODE, between, script, source
from test_ui_v391 import EM_NAMES, EM_STUBS, api_answers, run_js


def by_hour(p):
    out = {}
    for h in p["hours"]:
        out.setdefault(h["t"][11:16], h)
    return out


# ------------------------------------------------------------------ 1. RTL ohne Zustand: Grenze in jeder Stunde
@pytest.mark.parametrize("state", ["unavailable", "unknown", None])
def test_preview_rtl_without_thermostat_state_limits_every_hour(tmp_path, state):
    eng, ha = preview_house(tmp_path, cfg_with("rtl", 50))
    if state is None:
        eng.states.pop("climate.bad")
    else:
        eng.states["climate.bad"] = {"state": state, "attributes": {}}
    now = ts_of(MON, 12)
    ready = readiness.assess(eng, now)["floor"]
    assert ready["cap"] == 50.0 and ready["why"] == "Fußboden Bad (RTL-Ventil, Anforderung unbekannt): höchstens 50 °C"
    p = pv.preview(eng, now)
    assert [h["flow_cap"] for h in p["hours"]] == [50.0] * len(p["hours"]) and len(p["hours"]) == 24
    assert max(h["flow_max"] or 0 for h in p["hours"]) <= 50
    h0 = p["hours"][0]
    assert h0["flow"] == 50 and h0["flow_limited"] is True
    assert h0["flow_reason"] == "Wz hält 21,0 °C – begrenzt durch " + ready["why"]
    rows = pv._Run(eng, now).run()
    assert all(r["cap"] == 50.0 for r in rows) and all(r["flow"] <= 50 for r in rows if r["heating"])
    assert ha.calls == []  # nichts geschrieben


def test_preview_direct_without_thermostat_state_still_limits(tmp_path):
    eng, _ = preview_house(tmp_path, cfg_with("direkt", 45))
    eng.states["climate.bad"] = {"state": "unavailable", "attributes": {}}
    p = pv.preview(eng, ts_of(MON, 12))
    assert {h["flow_cap"] for h in p["hours"]} == {45.0}


def test_blind_only_without_any_state():
    f = fl.floor_rooms(cfg_with("rtl", 50), [wz(), bad()])[0]
    assert fl.blind(f, {"climate.bad": None}) and fl.blind(f, {})
    assert not fl.blind(f, {"climate.bad": {"temp": None, "target": None, "valve": None, "action": None}})


# ------------------------------------------------------------------ 2. „jetzt“ wie „Bereit für die Steuerung?“
def rtl_house(tmp_path, temp, valve, action):
    eng, ha = preview_house(tmp_path, cfg_with("rtl", 50), bad_temp=temp)
    climate(eng, "climate.bad", temp, 22.0, hvac_action=action)
    eng.states["sensor.bad_heizleistung"] = {"state": str(valve)}
    return eng, ha


@pytest.mark.parametrize("temp,valve,action,limited", [
    (21.8, 30, "idle", True),       # 0,2 K unter dem Soll, Ventil 30 % – für das 0,3-K-Band der Vorschau „am Soll“
    (22.0, 0, "heating", True),
    (22.0, 0, "preheating", True),
    (22.0, 6, "idle", True),        # Ventil über 5 %
    (21.95, 0, "idle", False),      # 0,05 K darunter, Ventil zu: fordert nicht an
])
def test_preview_now_uses_the_same_demand_as_readiness(tmp_path, temp, valve, action, limited):
    eng, ha = rtl_house(tmp_path, temp, valve, action)
    now = ts_of(MON, 12)
    ready = readiness.assess(eng, now)["floor"]
    rows = pv._Run(eng, now).run()
    assert rows[0]["cap"] == ready["cap"] == (50.0 if limited else None)
    h0 = pv.preview(eng, now)["hours"][0]
    assert h0["flow_cap"] == ready["cap"]
    if limited:
        assert h0["flow"] == 50 and h0["flow_limited"] is True and h0["flow_reason"].endswith(ready["why"])
    else:
        assert h0["flow"] == 61 and h0["flow_limited"] is False
    assert ha.calls == []


def test_without_model_the_demand_of_now_holds_while_setpoint_stays(tmp_path):
    eng, _ = rtl_house(tmp_path, 21.8, 30, "idle")
    h = by_hour(pv.preview(eng, ts_of(MON, 12)))
    assert h["12:00"]["flow_cap"] == h["16:00"]["flow_cap"] == 50.0  # Soll 22 °C wie jetzt: fordert weiter an
    assert h["16:00"]["flow_reason"].endswith("Fußboden Bad (RTL-Ventil, fordert an): höchstens 50 °C")
    assert h["03:00"]["flow_cap"] is None  # nachts 18 °C bei 21,8 °C: fordert nicht an


def test_preview_docstring_and_docs_name_the_rule():
    doc = pv.__doc__
    assert "jetzt nach den Messwerten wie „Bereit für die" in doc and "die Grenze gilt (sicher)" in doc
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    assert "jetzt nach den Messwerten, genau wie „Bereit für die Steuerung?“" in docs
    assert "Ist das Thermostat jetzt nicht verfügbar, gilt sie dort in jeder Stunde." in docs


# ------------------------------------------------------------------ 3. preheating
@pytest.mark.parametrize("action,expected", [("heating", True), ("preheating", True), ("PreHeating", True),
                                             ("idle", False), ("off", False), ("cooling", False), ("fan", False)])
def test_preheating_counts_as_demand(action, expected):
    f = fl.floor_rooms(cfg_with("rtl", 50), [wz(), bad()])
    r = {"climate.bad": {"temp": 22.0, "target": 22.0, "valve": None, "action": action}}
    assert fl.demand(f[0], r) is expected
    assert fl.flow_cap(f, r) == ((50.0, "Fußboden Bad (RTL-Ventil, fordert an): höchstens 50 °C") if expected
                                 else (None, None))


def test_heating_actions_constant():
    assert fl.HEATING_ACTIONS == ("heating", "preheating") and not hasattr(fl, "HEATING_ACTION")
    assert "„preheating“" in fl.__doc__


# ------------------------------------------------------------------ 4. Suchtext ist kein Wert
PICKER_NAMES = ("EM_FREE", "emListHtml", "emOpen", "emKey", "emReset", "emPick", "emRefresh", "emBlur")
FIELD_STUB = """
// Suchfeld ohne DOM: Eingabefeld in seiner Zeile mit Trefferliste; FIELDS sind die Felder der Seite
const FIELDS = [];
document.querySelectorAll = (s) => s.includes(".em-in") ? FIELDS : [];
document.querySelector = (s) => s.includes(".em-in") ? FIELDS.find((f) => s.includes(`"${f.dataset.key}"`)) || null : null;
const field = (k) => { const list = { hidden: true, innerHTML: "", querySelectorAll: () => [] };
  const row = { querySelector: (s) => s === ".em-list" ? list : null };
  const inp = { dataset: { key: k }, value: "", list, closest: () => row };
  emReset(inp); FIELDS.push(inp); return inp; };
const press = (inp, k) => emKey({ key: k, preventDefault() {} }, inp);
const type = (inp, text) => { inp.value = text; emOpen(inp); };
"""


def run_picker(tmp_path, data: dict, body: str):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    js = script()
    code = (EM_STUBS + "\n".join(source(js, n) for n in (*EM_NAMES, *PICKER_NAMES)) + FIELD_STUB
            + "\nconst out = (v) => console.log(JSON.stringify(v));\n"
            + f"ENT = {json.dumps(data['ent'])}; EMAP = {json.dumps(data['map'])}; emInit(EMAP);\n" + body)
    f = tmp_path / "picker.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_search_text_never_becomes_the_value(tmp_path):
    data = api_answers(tmp_path / "app")
    r = run_picker(tmp_path, data, """
      const res = {};
      // Fund der Prüfung: gesucht, nichts gewählt, Escape, Feld verlassen
      const flow = field("flow_temp"), gas = field("gas_sensor"), ret = field("return_temp"), out_ = field("outdoor_sensors");
      type(flow, "sensor.boiler_cur"); press(flow, "Escape"); res.esc = [flow.value, flow.list.hidden]; emBlur(flow);
      type(gas, "gas"); emBlur(gas);                       // nur verlassen
      res.after = { flow: EM_VAL.flow_temp, flowField: flow.value, gas: EM_VAL.gas_sensor, gasField: gas.value };
      // Speichern liest nur die Wahl – auch wenn im Feld noch ein Suchbegriff steht
      type(ret, "sensor.boiler_ret"); res.collect = emCollect({});
      // eine Entität aus Home Assistant, genau so eingetragen, gilt auch beim Verlassen
      type(flow, "sensor.boiler_curflowtemp"); emBlur(flow); res.known = EM_VAL.flow_temp;
      // frei eintragen (Home Assistant kennt sie nicht): nur domain.name und nur mit Enter
      type(ret, "sensor.neu_ruecklauf"); emBlur(ret); res.freeBlur = EM_VAL.return_temp;
      type(ret, "sensor.neu_ruecklauf"); press(ret, "Enter"); res.freeEnter = EM_VAL.return_temp;
      type(ret, "kein muster"); press(ret, "Enter"); res.noPattern = [EM_VAL.return_temp, ret.value];
      // Enter mit Treffern übernimmt den markierten
      type(gas, "gaszähler"); press(gas, "Enter"); res.hit = [EM_VAL.gas_sensor, gas.value];
      // mehrere: Suchbegriff fällt weg, bekannte Entität kommt dazu
      type(out_, "sensor.aussen_x"); emBlur(out_); res.multiBlur = [EM_VAL.outdoor_sensors, out_.value];
      type(out_, "sensor.boiler_outdoortemp"); emBlur(out_); res.multiKnown = EM_VAL.outdoor_sensors;
      out(res);""")
    assert r["esc"] == ["", True]  # Escape: das Feld zeigt wieder, was gilt (Automatik = leer)
    assert r["after"] == {"flow": "", "flowField": "", "gas": "", "gasField": ""}
    c = r["collect"]
    assert c["overrides"] == [] and c["gas_sensor"] == ""
    clean, errors = validate({**c, "persons": []})  # durch die echte Prüfung: gültig, keine Zuordnung
    assert errors == [] and Config.from_raw(clean).overrides == {}
    assert r["known"] == "sensor.boiler_curflowtemp"
    assert r["freeBlur"] == "" and r["freeEnter"] == "sensor.neu_ruecklauf"
    assert r["noPattern"] == ["sensor.neu_ruecklauf", "kein muster"]  # nichts übernommen, die Suche bleibt stehen
    assert r["hit"] == ["sensor.gaszaehler", "sensor.gaszaehler"]  # Feld zeigt die Wahl
    assert r["multiBlur"] == [[], ""] and r["multiKnown"] == ["sensor.boiler_outdoortemp"]


def test_collect_reads_only_the_choice():
    js = script()
    coll = source(js, "emCollect")
    assert ".em-in" not in coll and "inp.value" not in coll
    blur = source(js, "emBlur")
    assert "EM_ENT.has(v)" in blur and "emReset(inp)" in blur and "EM_VAL[e.key] = v" not in blur
    assert "emReset(inp); box.hidden = true;" in source(js, "emKey")
    assert 'emValue("dhw_set_entity")' in source(js, "dhwModeHint") and '$("f-dhw_set_entity")' not in js
    for name in PICKER_NAMES[1:]:
        assert source(js, name).count("\n") <= 60, name


# ------------------------------------------------------------------ 5. Verweise auf Karten, die es gibt
def _targets() -> tuple[set[str], set[str]]:
    js = script()
    cards = set(re.findall(r'card\("([^"]+)"', between(js, "function renderSettings()", "\nfunction ")))
    return cards | {"Allgemein", "Entitäten"}, {title for title, _ in em.GROUPS.values()}


def _refs(text: str):
    for m in re.finditer(r"Einstellungen → ([^\n]*)", text):
        yield m.group(1).lstrip("*„\"[")


def test_settings_references_name_existing_cards():
    cards, groups = _targets()
    assert {"Steuerung", "Verbindung", "Räume", "Warmwasser", "Haus & Kessel"} <= cards
    assert "Messwerte" not in cards and "Personen" not in cards
    files = [*sorted(PKG.glob("*.py")), PKG / "static" / "index.html", ROOT / "ems_heatpilot" / "DOCS.md",
             ROOT / "README.md"]
    bad_refs = []
    for f in files:
        for ref in _refs(f.read_text(encoding="utf-8")):
            if f.name.endswith(".md") and ref.startswith("Add-ons"):
                continue  # Home Assistant: Einstellungen → Add-ons
            name = next((c for c in sorted(cards, key=len, reverse=True) if ref.startswith(c)), None)
            if name is None:
                bad_refs.append((f.name, ref[:40]))
            elif name == "Entitäten" and ref.startswith("Entitäten → "):
                if not any(ref[len("Entitäten → "):].lstrip("*„").startswith(g) for g in groups):
                    bad_refs.append((f.name, ref[:40]))
    assert bad_refs == []


def test_fixed_references(tmp_path):
    js = script()
    assert 'off("keine Wetter-Entität", "Einstellungen → Entitäten → Außen & Wetter")' in js
    assert "Schalter unter Einstellungen → Warmwasser, Entität unter Einstellungen → Entitäten." in js
    eng, _, _, _ = make(tmp_path, cfg=Config.from_raw({"control_mode": "heatpilot",
                                                        "rooms": [{"climate": "climate.wz", "managed": True}]}),
                        rooms=[wz()], outdoor=["sensor.out"])
    eng.found.persons = []
    notes = readiness.assess(eng, ts_of(MON, 12))["notes"]
    assert "Ohne Personen gibt es keine Abwesenheits-Absenkung (Einstellungen → Entitäten → Personen & Urlaub)" in notes


# ------------------------------------------------------------------ 6. Außenfühler am Kessel nur über EMS-ESP
def test_boiler_outdoor_from_ems_only_is_display_only():
    states = {"sensor.boiler_curflowtemp": st("45", "°C"),
              "climate.wz": st("heat", current_temperature=21, temperature=21)}
    cfg = Config.from_raw({"rooms": [{"climate": "climate.wz"}]})
    lst = [{"entity_id": k, **v} for k, v in states.items()]
    found = discover(lst, [], cfg)
    auto = discover(lst, [], Config.from_raw({}), all_rooms=True).as_dict(private=False)
    rows = by_key(em.build(cfg, found, auto, states, None, {"boiler": {"outdoortemp": 6.5, "curflowtemp": 45}}, None))
    e = rows["boiler_outdoor"]
    assert e["source"] == "EMS-ESP" and e["state"] == 6.5
    assert e["fallback"] == em.EMS_ONLY_SHOWN["boiler_outdoor"] and e["fallback"].startswith("Nur Anzeige")
    assert found.outdoor == [] and found.weather is None  # der Hinweis stimmt: keine Außentemperatur
    assert e["purpose"].endswith("nur als Entität in Home Assistant.")
    assert rows["outdoor_sensors"]["purpose"].endswith("leer = Außenfühler am Kessel aus Home Assistant.")
    assert rows["outdoor_sensors"]["issue"] == "fehlt"
    # andere Rollen direkt von EMS-ESP: kein solcher Hinweis
    assert all(x["fallback"] is None for k, x in rows.items() if x["source"] == "EMS-ESP" and k != "boiler_outdoor")
    # mit Entität in Home Assistant: gefunden, kein Hinweis
    full = by_key(em_build(ems={"boiler": {"outdoortemp": 6.5}}))
    assert full["boiler_outdoor"]["source"] == "automatisch" and full["boiler_outdoor"]["fallback"] is None


def test_boiler_outdoor_hint_in_the_page(tmp_path):
    data = api_answers(tmp_path / "app", drop=("sensor.boiler_outdoortemp", "weather.zuhause"),
                       ems={"boiler": {"outdoortemp": 6.5}})
    r = run_picker(tmp_path, data, 'out(emNow(EM_BY.boiler_outdoor, emStateOf("boiler_outdoor")));')
    assert "über EMS-ESP" in r and "Nur Anzeige: Der Wert direkt von EMS-ESP dient nicht als Außentemperatur" in r
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    assert "außer beim **Außenfühler am Kessel**" in docs


# ------------------------------------------------------------------ 7. höchster Vorlauf in halben Grad
def test_floor_max_field_in_half_degrees(tmp_path):
    r = run_js(tmp_path, 'out(floorCell("climate.bad", { floor_type: "rtl", floor_max_flow: 45.5 }));')
    assert 'class="r-floor-max" min="25" max="70" step="0.5" value="45.5"' in r
    for v in (25, 45.5, 45.3, 52.74, 69.9, "61,25"):
        got = fl.clean_entry({"floor_max_flow": v}, "climate.bad")["floor_max_flow"]
        assert ((got - fl.MAX_FLOW_MIN) / 0.5).is_integer(), (v, got)  # liegt auf dem Raster des Felds


# ------------------------------------------------------------------ 8. alte Einstellungen laden ohne Fehler
@pytest.mark.parametrize("value,expected", [
    (None, {}), ([1, None, "x", "=sensor.a", "flow_temp=", " flow_temp = sensor.a "], {"flow_temp": "sensor.a"}),
    ({"flow_temp": "sensor.a"}, {}), ("return_temp=sensor.r", {"return_temp": "sensor.r"}), (5, {}),
])
def test_old_overrides_load(value, expected):
    assert Config.from_raw({"overrides": value}).overrides == expected


def test_old_overrides_text_with_gas_moves_to_field():
    c = Config.from_raw({"overrides": "gas=sensor.zaehler"})
    assert c.overrides == {} and c.gas_sensor == "sensor.zaehler"


@pytest.mark.parametrize("value", ["climate.bad", ("climate.bad",)])
def test_floor_heating_rooms_as_text_still_counts(value):
    c = Config.from_raw({"rooms": [{"climate": "climate.wz"}, {"climate": "climate.bad"}], "floor_heating_rooms": value})
    assert c.floor_heating_rooms == ["climate.bad"]
    f = fl.floor_rooms(c, [wz(), bad()])
    assert [(x.name, x.kind) for x in f] == [("Bad", "unbekannt")]  # wie direkt am Kessel, „Anschluss wählen“
    for broken in (None, 5, {"climate.bad": 1}):
        assert Config.from_raw({"floor_heating_rooms": broken}).floor_heating_rooms == []


def test_changelog_names_the_review_fixes():
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## 0.39.1")[1].split("\n## ")[0]  # seit 0.40
    for text in ("preheating", "Escape", "nur Anzeige", "halben Grad", "nicht verfügbar"):
        assert text in head, text
    assert "55 °C" not in head and "Ingo" not in head  # keine persönlichen Werte
