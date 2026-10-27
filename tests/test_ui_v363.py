"""0.36.3: Oberfläche zu Desinfektion (Legionellen) und Sperre gegen den EMS-ESP-Heizkreis – statische Prüfung.

Wie tests/test_ui_v361.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird, dass die neuen Teile
angebunden sind (Abschnitt in Einstellungen → Kessel, collect(), Kachel in der Übersicht, Häkchen
„macht mit“, Bedienung im Heizen-Reiter, Hinweis im Kessel-Block) und das Inline-Skript gültiges JavaScript
ist. Die Anzeige-Funktionen laufen in Node mit Antworten der echten Engine – so ist der fertige Text geprüft,
nicht nur der Quelltext. Die Texte der Oberfläche sind dieselben wie die Konstanten im Backend. Verhalten im
Browser prüft das Playwright-Skript (Screenshots), das nicht Teil der Suite ist.
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

import pytest

from heatpilot import dhw_disinfect as dd, readiness
from heatpilot.config import DISINFECT_DAYS, FIELDS, WEEKDAYS, Config
from heatpilot.discovery import Discovered
from heatpilot.ems_lock import LOCK_TEXT
from heatpilot.engine import Engine
from heatpilot.store import Store

from test_disinfect363 import bath_model
from test_readiness36 import _controls, house, room
from test_ui_v361 import NODE, between, html, plain, script, source

# DOM-Ersatz: $() liefert je id ein Objekt mit value/disabled/textContent; HEAT wie aus GET api/heat
STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, value: "", disabled: false, textContent: "", className: "", innerHTML: "",
  style: { setProperty() {} } });
let HEAT = null, DIS = null, ENT = [], SET = { rooms: [] }, SUG = {}, CTRL = {}, CT = {};
let SWITCH = null, SWITCH_BUSY = false, VFOUND = {};  // VFOUND: gefundene Heizleistung (0.45)
"""
NAMES = ("fmt", "esc", "clock", "WEEKDAYS", "field", "readyDot", "EMS_UOM", "boilerHtml",
         "DIS_NOT_YET", "splitWhen", "disinfectTile", "DAY_NAMES", "CHARGE_FROM", "DIS_BOILER",
         "disinfectHtml", "disinfectState", "disinfectSuggestHtml", "disinfectSync", "applyDisinfect",
         "EMS_LOCK", "ctlRooms", "allRooms", "shortName", "hm", "leftText", "tileColors", "PHASE", "ctl", "managing",
         "unreachable", "roomState", "ctlInfo", "roomDlg", "sibling", "climateEntities", "emsLock", "EM_BY", "roomStates",
         "FLOOR_KINDS", "FLOOR_MAX", "FLOOR_HINT", "floorKind", "floorCell", "SGONE", "GONE_HINT", "roomRows",  # Fußboden seit 0.39.1
         "swRoom", "relearnHtml",  # „Ventil neu lernen“ seit 0.44
         "degC", "DIS_RESULT_CSS", "disWhen", "disDot", "disLast", "disNow", "disinfectTriggerHtml")  # Auslösen (0.42)


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


def disinfect(tmp_path, suggest=True, ems_temp=70, confirmed=False, **raw):
    """disinfect-Block wie GET api/settings (``suggest``) bzw. summary.dhw.disinfect_plan – aus der echten Engine
    mit einem Zapfprofil „sonntags 18 Uhr Wanne, 7 von 8 Wochen“. ``confirmed``: Der Regler hat zur Zeit des
    Termins zuletzt desinfiziert (erkannt 40 min nach dem Beginn) – der Termin zählt."""
    cfg = Config.from_raw({"boiler_nominal_kw": 18.0, "dhw_comfort_temp": 50, **raw})
    eng = Engine(cfg, Store(tmp_path / "t.db"), Discovered())
    if ems_temp is not None:
        eng.ems = {"boiler": {"wwdisinfectiontemp": ems_temp}}
    eng.dhw, now = bath_model()
    if confirmed:
        last = cfg.disinfections(now.astimezone() - timedelta(days=7), now.astimezone())[-1]
        eng.dhw.disinfects = dd.remember([], last + timedelta(minutes=40))
    info = eng.disinfect_info(suggest=suggest, now_ts=now.timestamp())
    if suggest:  # wie web.py
        info.update(days=list(DISINFECT_DAYS), temp_range=list(FIELDS["dhw_disinfect_temp"][1:]))
    return eng, info


# ------------------------------------------------------------------ Texte gleich dem Backend
def test_ui_texts_equal_backend_constants(tmp_path):
    r = run_js(tmp_path, "out({ lock: EMS_LOCK, notYet: DIS_NOT_YET, boiler: DIS_BOILER, days: Object.keys(DAY_NAMES) });")
    assert r["lock"] == LOCK_TEXT and r["notYet"] == dd.NOT_YET and r["boiler"] == dd.SOURCE_BOILER
    assert r["days"] == list(WEEKDAYS)
    assert readiness.DISINFECT_NOTE == "Desinfektion braucht WWK Desinfizieren (schreibbar)"


# ------------------------------------------------------------------ Einstellungen → Kessel
def test_dhw_card_has_disinfect_section_and_collect_saves_fields():
    js = script()
    # Abschnitt am Ende der Karte Warmwasser, Daten aus GET api/settings → disinfect
    assert ('${field("Maximum (°C)", num("f-dhw_max_temp", s.dhw_max_temp, "1"))}\n    </div>${disinfectHtml(s, DIS)}`)) +'
            in js)  # 0.43: Karte „Temperaturen“ der Unterseite Warmwasser
    load = between(js, "async function loadSettings()", "let OV_STALE")
    assert "DIS = j.disinfect || null;" in load and "let DIS = null;" in js
    assert "modeHint(); disinfectSync();" in between(js, "function renderSettings()", "function addVac()")
    # collect(): Tag und Uhrzeit als Text ("" = aus), Temperatur wie die anderen Zahlen
    coll = between(js, "function collect()", "async function saveSettings")
    strings = between(coll, "for (const k of [", "]) out[k] = v(")
    assert '"dhw_disinfect_day", "dhw_disinfect_time"' in strings
    numbers = between(coll, 'for (const k of ["gas_calorific_value"', "]) out[k] = v(")
    assert '"dhw_disinfect_temp"' in numbers
    # „Vorschläge übernehmen“ (Erkennung) setzt keinen Termin – keine persönlichen Vorgaben
    assert "dhw_disinfect" not in between(js, "function applySuggestions()", "async function testEms")


def test_disinfect_section_with_date_temperature_and_suggestion(tmp_path):
    _, d = disinfect(tmp_path, dhw_disinfect_day="So", dhw_disinfect_time="13:00")
    sg = d["suggestion"]
    assert sg["ok"] and (sg["day"], sg["time"]) == ("So", "17:30") and d["temp_source"] == "laut Kessel"
    s = {"dhw_disinfect_day": "So", "dhw_disinfect_time": "13:00", "dhw_disinfect_temp": 70.0}
    r = run_js(tmp_path, f"out(disinfectHtml({json.dumps(s)}, {json.dumps(d)}));")
    t = plain(r)
    assert t.startswith("Desinfektion (Legionellen) Einmal pro Woche heizt der Kessel den Speicher über 60 °C auf. "  # 0.43: Kurztext + mehr
                        "Einmal je Woche heizt der Kessel den Speicher auf die Desinfektionstemperatur.")
    # Tag: „aus“ und Mo…So mit vollem Namen, gespeicherter Tag gewählt; Werte wie die Einstellung
    opts = re.findall(r'<option value="([^"]*)"( selected)?>([^<]*)</option>', r)
    assert [o[0] for o in opts] == list(DISINFECT_DAYS)
    assert [o[2] for o in opts] == ["aus", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    assert [o[0] for o in opts if o[1]] == ["So"]
    assert '<input id="f-dhw_disinfect_time" type="time" value="13:00"' in r
    # Temperatur gelesen, Einstellung nur als Rückfall (60–80 °C)
    assert "Temperatur 70 °C laut Kessel gelesen vom Kessel (EMS-ESP wwdisinfectiontemp)" in t
    assert '<input id="f-dhw_disinfect_temp" type="number" step="1" min="60" max="80" value="70">' in r
    assert "60–80 °C; gilt nur, wenn EMS-ESP keine Desinfektionstemperatur meldet" in t
    # Zustand, Vorschlag mit Begründung und Knopf, Hinweis
    assert "Nächste Desinfektion So 13:00 (eingestellt)" in t
    assert f"Zeitvorschlag: Sonntag 17:30 Vorschlag übernehmen {sg['text']} {sg['saving_text']} Ladedauer ≈ 12 min (aus der Kesselleistung)" in t
    assert 'onclick="applyDisinfect()">Vorschlag übernehmen</button>' in r
    assert t.endswith(dd.NOT_YET) and '<div class="warnbox" id="dis-hint">' in r


def test_disinfect_section_without_date_shows_reason_last_special_and_controller(tmp_path):
    eng, _ = disinfect(tmp_path, ems_temp=None)
    eng.dhw._last_special = datetime(2026, 2, 22, 13, 40).astimezone()
    eng.ems = {"thermostat": {"wwdisinfectday": "So", "wwdisinfecthour": 13}}
    eng.dhw.profile_n = [2] * len(eng.dhw.profile_n)  # erst zwei Wochen gelernt
    d = eng.disinfect_info(suggest=True)
    s = {"dhw_disinfect_day": "", "dhw_disinfect_time": "13:00", "dhw_disinfect_temp": 70.0}
    t = plain(run_js(tmp_path, f"out(disinfectHtml({json.dumps(s)}, {json.dumps(d)}));"))
    assert "Temperatur 70 °C eingestellt EMS-ESP meldet keine Desinfektionstemperatur – es gilt der eingestellte Wert" in t
    assert "Kein Termin eingestellt · letzte Desinfektion So 13:40 (erkannt) · Regler meldet So 13:00" in t
    assert ("Zeitvorschlag Zapfprofil erst 2 Wochen gelernt – ein Vorschlag braucht mindestens 4 Wochen") in t
    assert "Vorschlag übernehmen" not in t
    # ältere Antwort ohne disinfect-Block: Abschnitt trotzdem bedienbar, Tage und Bereich als Rückfall
    r = run_js(tmp_path, f"out(disinfectHtml({json.dumps(s)}, null));")
    assert len(re.findall(r"<option ", r)) == 8 and 'min="60" max="80"' in r and plain(r).endswith(dd.NOT_YET)


def test_counts_text_when_controller_disinfects_at_other_time(tmp_path):
    eng, _ = disinfect(tmp_path, dhw_disinfect_day="Sa", dhw_disinfect_time="10:00")
    eng.dhw._last_special = datetime(2026, 2, 22, 13, 40).astimezone()  # Regler: So 13:40, der Termin Sa 10:00
    eng.dhw.disinfects = [eng.dhw._last_special.isoformat(timespec="minutes")]
    d = eng.disinfect_info(suggest=False, now_ts=datetime(2026, 2, 24, 9, 0).astimezone().timestamp())
    assert not d["counts"]
    r = run_js(tmp_path, f"out(disinfectState({json.dumps(d)}));")
    assert '<p class="dis-state dis-counts" id="dis-counts">' in r
    assert plain(r).endswith(d["counts_text"]) and d["counts_text"].startswith(dd.NOT_CONFIRMED)
    assert d["counts_text"].endswith("der Regler desinfiziert zu anderer Zeit (zuletzt So 13:40) – es zählt seine Zeit")


def test_apply_suggestion_fills_day_and_time_saving_only_with_speichern(tmp_path):
    _, d = disinfect(tmp_path)
    r = run_js(tmp_path, f"""DIS = {json.dumps(d)};
      const day = $("f-dhw_disinfect_day"), at = $("f-dhw_disinfect_time"), btn = $("dis-apply");
      day.value = ""; at.value = "13:00";
      let fetched = 0; globalThis.fetch = () => {{ fetched++; }};
      disinfectSync(); const off = at.disabled;
      applyDisinfect();
      const after = {{ day: day.value, at: at.value, disabled: at.disabled, btn: btn.disabled, label: btn.textContent,
                      msg: $("save-msg").textContent }};
      at.value = "18:00"; disinfectSync();
      out({{ off, after, again: [btn.disabled, btn.textContent], fetched }});""")
    assert r["off"] is True  # ohne Tag ist die Uhrzeit nicht bedienbar (bleibt gespeichert)
    assert r["after"] == {"day": "So", "at": "17:30", "disabled": False, "btn": True, "label": "Vorschlag eingetragen",
                          "msg": "Desinfektion So 17:30 eingetragen – prüfen und speichern."}
    assert r["again"] == [False, "Vorschlag übernehmen"] and r["fetched"] == 0  # nichts gespeichert


# ------------------------------------------------------------------ Übersicht → Warmwasser
def test_overview_tile_next_date_or_last_special(tmp_path):
    js = script()
    load = between(js, "async function load() {", "LAST_DHW = w;")
    # 0.43: eine Kachel „Desinfektion“ – die ES73-Kachel nur, wenn kein Termin/keine Sonderladung bekannt ist
    assert load.index("disinfectTile(w.disinfect_plan),") < load.index('w.disinfect && !disinfectTile(w.disinfect_plan) ? tile("Desinfektion",')
    _, plan = disinfect(tmp_path, suggest=False, confirmed=True, dhw_disinfect_day="So", dhw_disinfect_time="17:30")
    assert "suggestion" not in plan and plan["counts"]
    r = run_js(tmp_path, f"""const P = {json.dumps(plan)};
      out({{ plan: disinfectTile(P), other: disinfectTile({{ ...P, counts: false, counts_text: "Termin noch nicht bestätigt – kein Regler desinfiziert zu dieser Zeit" }}),
            last: disinfectTile({{ ...P, configured: false, next: null, next_text: null, last_special_text: "So 13:40 (erkannt)" }}),
            none: disinfectTile({{ ...P, configured: false, next_text: null, last_special_text: null, temp_source: "eingestellt" }}),
            old: disinfectTile(undefined) }});""")
    # Zeit groß als Wert, die Herkunft vorn in der Notiz (als Einheit bräche sie in schmalen Kacheln um)
    # Text seit 0.42: HeatPilot löst nur bei „Warmwasser-Soll: HeatPilot setzt“ ohne Regler am Bus aus
    assert plain(r["plan"]) == "Nächste Desinfektion So 17:30 eingestellt · 70 °C laut Kessel · HeatPilot löst sie nicht aus"
    assert '<div class="value">So 17:30</div>' in r["plan"]
    assert plain(r["other"]).endswith("eingestellt · 70 °C laut Kessel · Termin noch nicht bestätigt – kein Regler "
                                      "desinfiziert zu dieser Zeit")
    assert plain(r["last"]) == ("Letzte Desinfektion So 13:40 erkannt · kein Desinfektionstermin eingestellt – "
                                "Einstellungen → Kessel")
    assert 'class="tile off"' in r["none"] and plain(r["none"]).startswith("Desinfektion kein Termin")
    assert r["old"] == "" and 'class="tile"' in r["plan"]


# ------------------------------------------------------------------ Räume: „macht mit“ gesperrt
ENT = [{"id": "climate.thermostat_hc1", "name": "Thermostat HC1", "platform": "mqtt", "ems": True, "siblings": []},
       {"id": "climate.bad_thermostat", "name": "Bad Thermostat", "platform": "tado", "ems": False, "siblings": []},
       {"id": "climate.flur", "name": "Flur Heizung", "platform": "homekit_controller", "ems": False, "siblings": []},
       {"id": "climate.kueche", "name": "Küche Heizung", "platform": "homekit_controller", "siblings": []}]
SAVED = [{"climate": "climate.thermostat_hc1", "managed": True, "group": "Keller"},  # „macht mit“ gespeichert
         {"climate": "climate.bad_thermostat", "managed": True},
         {"climate": "climate.flur", "managed": True, "group": "Keller"},  # im selben Raum wie der Heizkreis
         {"climate": "climate.kueche", "managed": True, "group": "Küche"}]


def test_room_table_locks_managed_for_ems_heating_circuit(tmp_path):
    r = run_js(tmp_path, f"ENT = {json.dumps(ENT)}; SET = {{ rooms: {json.dumps(SAVED)}, floor_heating_rooms: [] }}; out(roomRows());")
    rows = {m.group(1): m.group(0) for m in re.finditer(r'<div class="room" data-climate="([^"]+)".*?</details></div>', r, re.S)}
    box = lambda row: re.search(r'<input type="checkbox" class="r-managed"[^>]*>', row).group(0)  # noqa: E731
    ems, flur = rows["climate.thermostat_hc1"], rows["climate.flur"]
    assert "checked" not in box(ems) and f'disabled title="{LOCK_TEXT}"' in box(ems)  # gespeichertes „macht mit“ zählt nicht
    assert f'<div class="hint ems-lock">„macht mit“ gesperrt: {LOCK_TEXT}</div>' in ems
    assert f'<label title="{LOCK_TEXT}">' in ems  # 0.43: Raumliste – der Grund steht am Häkchen
    # gleicher „Raum“ wie der Heizkreis: das Backend nimmt den ganzen Raum heraus – hier ebenso gesperrt
    assert "disabled" in box(flur) and f"„macht mit“ gesperrt: gleicher Raum wie der {LOCK_TEXT}" in flur
    # Tado-Zone „…_thermostat“ und Räume ohne EMS-ESP bleiben frei
    for c in ("climate.bad_thermostat", "climate.kueche"):
        assert "disabled" not in box(rows[c]) and "checked" in box(rows[c]) and "ems-lock" not in rows[c], c
    # collect(): offene Häkchen wie bisher; gesperrt behält ein Gerät im selben Raum sein gespeichertes „macht mit“
    # (Prüfung 0.36.3, test_v363_review), der Heizkreis selbst macht nie mit
    assert "managed: managedOf(tr, c)," in between(script(), "function collect()", "async function saveSettings")


# ------------------------------------------------------------------ Heizen: Heizkreis nur Anzeige
HEAT_ROOMS = [
    {"climate": "climate.wz", "name": "Wohnzimmer", "mode": "heat", "target": 21, "temp": 20.5, "valve": 30, "modes": ["auto", "heat", "off"],
     "managed": True, "ems": False, "tado_cloud": True, "devices": []},
    {"climate": "climate.thermostat_hc1", "name": "Heizkreis", "mode": "auto", "target": 20, "temp": 21, "valve": None,
     "modes": ["auto", "heat", "off"], "managed": False, "ems": True, "tado_cloud": False, "devices": []}]


def test_heat_tab_never_operates_ems_circuit(tmp_path):
    js = script()
    assert "function allRooms() { return ctlRooms(HEAT).map((r) => r.climate); }" in js
    assert '$("boost-rooms").innerHTML = ctlRooms(j).map(' in js
    assert "const n = ctlRooms(j).length, cloud = ctlRooms(j).filter((r) => r.tado_cloud).length;" in js
    assert "const n = own.length, running = own.filter((r) => r.boost)" in js
    r = run_js(tmp_path, f"""HEAT = {{ rooms: {json.dumps(HEAT_ROOMS)}, control: {{ mode: "heatpilot" }} }};
      out({{ all: allRooms(), lock: roomDlg(HEAT.rooms[1]), free: roomDlg(HEAT.rooms[0]),
            state: roomState(HEAT.rooms[1]) }});""")
    assert r["all"] == ["climate.wz"]  # Boost „Alle“, Zeitplan, Ausschalten: ohne Heizkreis
    lock, free = r["lock"], r["free"]
    assert len(re.findall(r"<button[^>]*disabled", lock)) == 5  # −, + und die drei Betriebsarten
    assert re.findall(r"<button[^>]*disabled", free) == []
    assert f'<div class="ctl-info ems-lock" id="dlg-ems">{LOCK_TEXT} – Soll und Betriebsart nur am Regler.</div>' in lock
    assert "Macht nicht mit" not in lock  # kein Verweis auf „macht mit“, das gesperrt ist
    assert r["state"] == "Nur Anzeige · 20°"  # 0.43: Schema „Wer · Soll"


# ------------------------------------------------------------------ Kessel-Block: wofür Desinfektion nötig ist
def test_boiler_block_shows_disinfect_note(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz")])
    eng.cfg.emsesp_host, eng.cfg.dhw_disinfect_day = "ems-esp.local", "So"
    eng.ems_controls = _controls(wwdisinfecting={"found": True, "writeable": False, "value": False})
    eng.ems_controls_ts = time.time()
    b = readiness.boiler(eng)
    assert b["text"] == "Es fehlen: Desinfektion (nicht schreibbar)"
    w = run_js(tmp_path, f"out(boilerHtml({json.dumps(b)}, true));")
    assert "Steuerungen (6 nötig, 4 zur Info)" in plain(w)
    row = next(plain(x) for x in re.findall(r"<tr[^>]*><td>.*?</tr>", w) if "wwdisinfecting" in x)
    assert row.startswith(f"Desinfektion wwdisinfecting {readiness.DISINFECT_NOTE} aus") and row.endswith("nein nötig")
    eng.cfg.dhw_disinfect_day = ""  # ohne Termin: nur Info, kein Hinweis
    w = run_js(tmp_path, f"out(boilerHtml({json.dumps(readiness.boiler(eng))}, true));")
    assert readiness.DISINFECT_NOTE not in w and "Steuerungen (5 nötig, 5 zur Info)" in plain(w)


def test_css_for_new_parts_in_both_themes():
    css = html().split("<style>")[1].split("</style>")[0]
    # nur Variablen – hell und dunkel ohne eigene Regeln
    for rule in (".field select.sel { width: 100%;", ".dis-sug { border: 1px solid var(--border); border-left: 3px solid var(--series-1);",
                 ".hint.ems-lock, .ems-lock { color: var(--warn); }", ".dis-counts { border-left: 3px solid var(--warn);",
                 ".btn:disabled { opacity: .5; cursor: default; }"):
        assert rule in css, rule


def test_docs_describe_ui_parts():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    docs = re.sub(r"\s+", " ", (root / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8"))
    log = (root / "CHANGELOG.md").read_text(encoding="utf-8").split("## 0.36.2")[0]
    assert "„Vorschlag übernehmen“ trägt Tag und Uhrzeit ein; gespeichert wird erst mit „Speichern“" in docs
    assert "Im Reiter „Heizen“ steht er nur zur Anzeige („nur Anzeige“): −/+ und Betriebsart sind gesperrt" in docs
    assert "„Vorschlag übernehmen“** trägt Tag und Uhrzeit ins Formular ein" in log
    assert "im Reiter Heizen steht er nur zur Anzeige" in log


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    scripts = re.findall(r"<script>(.*?)</script>", html(), re.S)
    assert len(scripts) == 1
    f = tmp_path / "inline.js"
    f.write_text(scripts[0], encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
