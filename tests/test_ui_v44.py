"""0.44: Oberfläche zum Thermostatwechsel, „Ventil neu lernen“, Aufbewahrung und „Lücken aus Home Assistant füllen“.

Wie die übrigen UI-Tests: statisch (Markup, Verdrahtung, collect) und in Node mit nachgebauten Daten aus
``GET api/rooms/switch`` – der Hinweis mit den drei Wahlen, die Rückfragen, die Ergebnisse, die Zeile in Übersicht →
Status, der Knopf „Ventil neu lernen“ und das Protokoll. Die Werte sind erfunden, keine persönlichen Daten.
"""

import re
from pathlib import Path

from test_ui_v361 import between, html, plain, script, source
from test_ui_v43 import run_js

ROOT = Path(__file__).resolve().parents[1]
FIX = """
SWITCH = { switches: [{ room_id: "r1", name: "Wohnzimmer", old_key: "climate.wohnzimmer", new_key: "climate.wohnzimmer_2",
  since: "2026-10-20T10:05:00+02:00", old_name: "Wohnzimmer (alt)", old_source: "Tado-Cloud", new_source: "HomeKit",
  old_rows: 1200, old_days: 40, old_first: "2026-09-01T00:00:00+02:00", old_last: "2026-10-20T10:00:00+02:00" }],
  choices: { same: "alle Daten ziehen um", other: "das Ventil lernt neu", new: "nichts zieht um" },
  rooms: [{ room_id: "r1", key: "climate.wohnzimmer_2", name: "Wohnzimmer", valve: true, valve_since: null },
          { room_id: "r2", key: "climate.bad", name: "Bad", valve: true, valve_since: "2026-10-01T08:00:00+02:00" },
          { room_id: "r3", key: "climate.ems_hc1", name: "Heizkreis", valve: false, valve_since: null }],
  log: [{ ts: "2026-10-19T09:00:00+02:00", room_id: "r2", name: "Bad", what: "ventil neu", key: "climate.bad",
          forgot: { kind: "valve", valve_n: 300, steady_h: 12.4, learned_until: 0 } },
        { ts: "2026-10-18T09:00:00+02:00", room_id: "r4", name: "Küche", what: "umbenannt", old_key: "a", new_key: "b", rows: 50 }] };
"""
NAMES = ("ADOPT", "dayWord", "adoptLock", "isoDay", "isoSpan", "SWITCH", "SWITCH_LABEL", "SWITCH_WHAT", "isoWhen", "swRoom",
         "switchAsk", "switchItem", "switchHtml", "forgotText", "switchLogHtml", "relearnHtml", "ovSwitchHtml", "switchDone")


def probe(tmp_path, body: str):
    js = script()
    return run_js(tmp_path, FIX + body, extra="\n".join(source(js, n) for n in NAMES) + "\n")


def test_switch_box_offers_three_choices_and_locks_until_saved(tmp_path):
    r = probe(tmp_path, 'out({ box: switchHtml(SWITCH), lock: switchHtml(SWITCH, "Erst speichern."), none: switchHtml({ switches: [] }),'
                        ' msg: switchHtml({ switches: [] }, "", "fertig"), err: switchHtml({ error: "kaputt" }) });')
    box = r["box"]
    assert box.count('class="callout warn sw-item"') == 1 and box.count("onclick=\"switchGo(this)\"") == 3
    for c, label in (("same", "Gleiches Thermostat, neue Anbindung"), ("other", "Anderes Thermostat – Ventil lernt neu"),
                     ("new", "Alles neu lernen")):
        assert f'data-room="r1" data-choice="{c}" onclick="switchGo(this)">{label}</button>' in box
    text = plain(box)
    assert "Wohnzimmer: anderes Thermostat erkannt seit 20.10. 10:05 – bisher Wohnzimmer (alt) (Tado-Cloud), jetzt HomeKit." in text
    assert "Bis zu deiner Wahl misst der Raum normal weiter." in text and "40 Tage Messwerte (01.09.2026–20.10.2026)" in text
    assert "climate.wohnzimmer_2" in box and "das Ventil lernt neu" in text  # Einzelheiten hinter „mehr“
    assert " disabled" not in box and r["lock"].count(" disabled") == 3 and "Erst speichern." in r["lock"]
    assert r["none"] == "" and "fertig" in r["msg"] and "kaputt" in r["err"]


def test_questions_and_results_say_what_moves_and_what_relearns(tmp_path):
    r = probe(tmp_path, """const s = SWITCH.switches[0];
out({ same: switchAsk(s, "same"), other: switchAsk(s, "other"), nw: switchAsk(s, "new"),
  dSame: switchDone({ ok: true, choice: "same", name: "Wohnzimmer", from_days: 40, skipped: 3 }, s),
  dOther: switchDone({ ok: true, choice: "other", name: "Wohnzimmer", from_days: 40, skipped: 0, valve_since: "2026-10-20T10:05:00+02:00" }, s),
  dNew: switchDone({ ok: true, choice: "new", name: "Wohnzimmer" }, s) });""")
    assert r["same"].startswith("Wohnzimmer: gleiches Thermostat, nur neu angebunden?\n\nAlle Messwerte und Gelerntes von Wohnzimmer (alt) (40 Tage Messwerte)")
    assert "Raumwerte" in r["other"] and "das Raummodell bleibt. Das Ventil lernt ab dem Wechsel neu." in r["other"]
    assert "Nichts zieht um" in r["nw"] and "„Gespeicherte Daten ohne Raum“" in r["nw"] and "rückgängig" not in r["nw"]
    assert plain(r["dSame"]).startswith("Übernommen: 40 Tage Messwerte von Wohnzimmer (alt) in Wohnzimmer (an 3 Zeitpunkten")
    assert "das Ventil lernt ab 20.10. 10:05 neu." in plain(r["dOther"]) and "Raumwerte übernommen" in plain(r["dOther"])
    assert plain(r["dNew"]).startswith("Wohnzimmer lernt neu.")


def test_overview_status_line_relearn_button_and_log(tmp_path):
    r = probe(tmp_path, """out({ ov: ovSwitchHtml(SWITCH), ovNone: ovSwitchHtml({ switches: [] }), ovNull: ovSwitchHtml(null),
  r1: relearnHtml("climate.wohnzimmer_2"), r2: relearnHtml("climate.bad"), ems: relearnHtml("climate.ems_hc1"),
  gone: relearnHtml("climate.x"), log: switchLogHtml(SWITCH.log), noLog: switchLogHtml([]) });""")
    assert 'class="st st-warn"' in r["ov"] and "Thermostat gewechselt: Wohnzimmer – wartet auf deine Wahl" in plain(r["ov"])
    assert "openSettings('raeume', 'room-switch')" in r["ov"] and r["ovNone"] == "" and r["ovNull"] == ""
    assert 'data-room="r1" onclick="valveRelearn(this)">Ventil neu lernen</button>' in r["r1"] and "Messwerte bleiben" in r["r1"]
    assert "lernt seit 01.10.2026" in r["r2"] and "lernt seit" not in r["r1"]
    assert r["ems"] == "" and r["gone"] == ""  # Heizkreis von EMS-ESP ohne Ventilmodell, unbekannter Raum: kein Knopf
    log = plain(r["log"])
    assert "Thermostatwechsel – Protokoll (2)" in log
    assert "19.10. 09:00 · Bad · Ventil neu gelernt · verlernt: Ventilmodell aus 300 Werten, 12 h ruhig" in log
    assert "Küche · von Home Assistant umbenannt, übernommen · 50 Messpunkte" in log and r["noLog"] == ""


def test_wiring_settings_overview_and_save():
    page, js = html(), script()
    for i in ("room-switch", "ov-switch"):
        assert len(re.findall(r'id="%s"' % i, page)) == 1, i
    assert '<div id="room-switch"></div><div id="room-restore"></div><div id="room-adopt"></div>' in js
    assert '<div class="r-relearn" data-climate="${esc(c.id)}">${relearnHtml(c.id)}</div>' in source(js, "roomRows")
    assert "loadSwitch();" in source(js, "renderSettings") and "loadSwitch();" in source(js, "load")
    assert "loadSwitch();" in source(js, "jobFinished") and 'if (name === "settings" && SET) loadSwitch();' in source(js, "showTab")
    assert 'swPost("api/rooms/switch", { room_id: s.room_id, choice: c })' in source(js, "switchGo")
    assert 'swPost("api/rooms/valve_relearn", { room_id: r.room_id })' in source(js, "valveRelearn")
    for fn in ("switchGo", "valveRelearn"):  # Rückfrage vor jeder Änderung, gesperrt bei ungespeicherten Einstellungen
        body = source(js, fn)
        assert "if (!confirm(" in body and "adoptLock()" in body and "SWITCH_BUSY = true" in body
    assert "Nur das Ventil von ${r.name} verlernen?" in source(js, "valveRelearn")
    save = source(js, "saveSettings")
    assert "const sw = j.switches || [];" in save and 'openSettings("raeume", "room-switch")' in save
    assert "renderSwitch();" in source(js, "roomsDirty")


def test_collect_carries_room_id_unchanged_and_keep_days():
    js = script()
    coll = source(js, "collect")
    assert 'const rid = ((SET.rooms || []).find((r) => r.climate === c) || {}).id;' in coll
    assert "out.rooms.push({ ...(rid ? { id: rid } : {}), climate: c," in coll  # neuer Raum: kein id-Feld
    assert '"statistics_backfill_days", "keep_days",' in coll
    sets = source(js, "renderSettings")
    assert '${field("Daten aufbewahren (Tage)", num("f-keep_days", s.keep_days, "1"), "365–3650, Standard 720.' in sets


def test_fill_gaps_instead_of_reload():
    js, page = script(), html()
    adv = source(js, "advHouseHtml")
    assert 'onclick="reloadHistory()">Lücken aus Home Assistant füllen</button>' in adv and "Historie neu einlesen<" not in page
    assert "eigene Werte bleiben" in adv
    ask = source(js, "RELOAD_ASK")
    assert ask.startswith('const RELOAD_ASK = "Lücken aus Home Assistant füllen?') and "nichts wird gelöscht" in ask
    assert "Eigene gespeicherte Werte gewinnen immer" in ask
    assert "Lücken aus Home Assistant werden gefüllt" in source(js, "reloadHistory")
    # der Kasten für Altdaten bleibt
    assert "Gespeicherte Daten ohne Raum:" in source(js, "adoptHtml")


def test_version_changelog_docs_readme():
    from heatpilot import __version__
    assert __version__ >= "0.44.0"
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "\n## 0.44.0\n" in log
    head = log.split("\n## 0.44.0\n")[1].split("\n## ")[0]
    for t in ("Thermostat", "Ventil neu lernen", "Lücken aus Home Assistant füllen", "keep_days", "tests/test_ui_v44.py"):
        assert t in head, t
    for personal in ("Ingo", "BK15", "Hamburg", "°C seit"):
        assert personal not in head, personal
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for t in ("### Thermostat wechseln", "„Ventil neu lernen“", "Daten aufbewahren", "Lücken aus Home Assistant füllen"):
        assert t in docs, t
    assert "Thermostat" in between((ROOT / "README.md").read_text(encoding="utf-8"), "0.44", "\n\n")
