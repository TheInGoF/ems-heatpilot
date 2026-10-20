"""0.42.1: Oberfläche zu Karte „Modelle“, Gelerntes übernehmen, altem Sensor je Raum, Hausdaten wiederherstellen und
neu lernen – statische Prüfung und Texte in Node.

Wie tests/test_ui_v42.py: die Oberfläche ist eine HTML-Datei ohne Build. Geprüft wird der Einbau (Karte „Modelle“ oben
in der Übersicht vor den Kacheln, Klick → „Modelle – Einzelheiten“ unter Diagnose; Hinweis am Wärmebedarf; in
Einstellungen → Räume Hinweis „Hausdaten wiederherstellen“, Kasten „Gespeicherte Daten ohne Raum“ und „Alter
Temperatursensor je Raum“; Knöpfe in Erweitert; Grundlage im Zertifikat und in der Druckansicht), dass die Knöpfe erst
nach Rückfrage etwas starten und nichts an Home Assistant geht, und die Texte – in Node mit Antworten des echten
Backends (``model_card``, ``room_alias``, ``room_adopt``, ``house_restore``, ``certificate``) am Haus aus
tests/test_restore421.py: nach dem Schaden von 0.42.0, gesund und frisch. Bilder in 390/1100 px, hell/dunkel prüft das
Playwright-Skript, das nicht Teil der Suite ist.
"""

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
import test_restore421 as R
from heatpilot import certificate, house_data, house_restore, model_card, room_adopt, room_alias
from heatpilot.engine import Engine
from heatpilot.store import Store
from test_ui_v361 import NODE, between, html, plain, script, source
from test_ui_v39 import as_json
from test_restore421 import frozen_clock  # noqa: F401,E402 – angehaltene Uhr (autouse)

ROOT = Path(__file__).resolve().parents[1]
SECTION = "// ---------------------------------------------------------------- Karte „Modelle“ (0.42.1)"
SECTION_END = "// ---------------------------------------------------------------- Desinfektion auslösen (0.42)"
NAMES = ("fmt", "esc", "readyDot", "MODELS", "isoDay", "isoSpan", "tsWhen", "mcDot", "mcLoss", "mcRoom", "mcHouse",
         "mcNames", "mcRooms", "mcValves", "mcDhw", "mcLast", "restoreHint", "modelsHtml", "dmRow", "dmHouse",
         "dmRooms", "dmValves", "dmDhw", "dmLog", "modelsDetailHtml", "JOB", "JOB_POLL_MS", "JOB_NAME", "JOB_ASK",
         "jobRunning", "jobButton", "jobAge", "tsDay", "jobResult", "jobHtml", "advHouseHtml", "ALIAS", "HOW_TEXT", "statLabel",
         "aliasOptions", "aliasCheck", "aliasRow", "aliasHtml", "aliasAsk", "aliasDone", "certBasisHtml",
         "certMissingHtml", "dayWord", "adoptDate", "adoptSrc", "adoptAsk", "adoptOver", "adoptRow", "adoptHtml",
         "adoptDone", "restoreOld", "restoreNotes", "restoreAliases", "RELOAD_ASK")
STUBS = """
const ELS = {};
const $ = (id) => ELS[id] || (ELS[id] = { id, hidden: false, textContent: "", innerHTML: "" });
const document = { querySelectorAll: () => [] };
"""


def js_run(tmp_path, body: str):
    """``body`` mit den Funktionen oben und dem DOM-Ersatz in Node; gibt das JSON von ``out(...)`` zurück."""
    if not NODE:
        pytest.skip("node nicht verfügbar")
    funcs = "\n".join(source(script(), n) for n in NAMES)
    code = STUBS + funcs + "\nconst out = (v) => console.log(JSON.stringify(v));\n" + body
    f = tmp_path / "probe421.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run([NODE, str(f)], capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def n(v) -> str:
    """Ganzzahl wie fmt(v, 0) in der Oberfläche (Tausenderpunkt)."""
    return f"{round(v):,}".replace(",", ".")


def day(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """Antworten des echten Backends: Haus nach dem Schaden (Karte, Zertifikat), Gelerntes übernehmen mit Alias,
    Übersicht der Aliasse, Wiederherstellen und Neulernen; gesundes und frisches Haus."""
    d = tmp_path_factory.mktemp("ui421")
    st = R.make_store(d / "s.db")
    R.damage(st)
    eng = R.learned_house(st)
    eng.states.update(R.STATES)
    out = {"bad": as_json(model_card.collect(eng)), "cert_bad": as_json(certificate.collect(eng)["basis"])}
    idx, ha = R.REG.lookup(), R.FakeHA()
    stat_list = asyncio.run(ha.list_statistic_ids())
    out["orphans"] = as_json(room_adopt.overview(st, eng.found, eng.states, idx))
    res = room_adopt.run(eng, "climate.wohnzimmer", "climate.hk_va01")  # wie App.adopt_room
    a = room_alias.detect(st, eng.found, "climate.wohnzimmer", "climate.hk_va01", eng.states, idx, stat_list)
    res.update(ok=True, alias=room_alias.put(st, "climate.hk_va01", a["stat"], a["from"], a["how"]),
               restore_suggested=house_data.missing_in(house_data.coverage(st)))
    out["adopt"] = as_json(res)
    out["aliases"] = as_json(room_alias.overview(st, eng.found, eng.states, idx, stat_list))
    t0 = int(time.time())
    restored = asyncio.run(house_restore.restore(ha, eng, idx, stat_list))
    after = R.learned_house(st)
    relearned = house_data.last_restore(st)
    out["restore"] = as_json({**restored, "relearn": relearned})
    out["relearn"] = as_json(relearned)
    out["started"], out["done"] = t0, int(time.time())
    out["after"] = as_json(model_card.collect(after))
    ok = R.learned_house(R.make_store(d / "g.db"))
    out["ok"], out["cert_ok"] = as_json(model_card.collect(ok)), as_json(certificate.collect(ok)["basis"])
    fresh = Store(d / "n.db")
    fresh.add_hourly([(h, round(R.t_out(h), 3), round(R.t_in(h), 3), R.gas(h), None, None, None, None, None, 0.0)
                      for h in range(R.NOW_H - 48 * 3600, R.NOW_H, 3600)])
    fresh.commit()
    out["fresh"] = as_json(model_card.collect(Engine(R.cfg(), fresh, R.found("new"))))
    return out


# ------------------------------------------------------------------ Einbau
def test_card_on_top_of_overview_and_details_under_diagnose():
    page, js = html(), script()
    assert ('<p class="sub dhw-line" id="ov-boiler" hidden></p>\n  <div class="card" id="models" '
            'style="margin-bottom:12px"><h2>Modelle</h2><p class="muted">Lade …</p></div>\n\n  <div class="tiles" '
            'id="tiles"></div>') in page
    diag = between(page, '<section id="view-diag" hidden>', "</section>")
    assert diag.index('id="diag-checks"') < diag.index('<div class="card" id="diag-models"') < \
        diag.index("Diagnose-Datei")
    ld = between(js, "async function load() {", "// " + "-" * 64 + " Einstellungen")
    starting = between(ld, "if (s.starting) {", "setTimeout(load, 5000)")
    assert '$("models").innerHTML = `<h2>Modelle</h2><p class="muted">HeatPilot startet' in starting  # auch beim Start
    assert ld.index("renderModels(s.models);") < ld.index('$("tiles").innerHTML')  # vor den Kacheln, ohne Bedingung
    assert "demandNote((s.models || {}).house);" in ld
    demand = between(page, "Wärmebedarf des Hauses", 'id="chart-demand"')
    assert '<p class="muted" id="demand-note" hidden></p>' in demand
    assert 'if (name === "diag") { exportSize(); loadSensors(); loadModels(); }' in source(js, "showTab")
    assert 'showTab("diag");\n  $("diag-models").scrollIntoView' in source(js, "openModels")
    assert 'onclick="openModels()"' in source(js, "modelsHtml")  # Klick auf die Karte → Diagnose


def test_settings_rooms_advanced_certificate_and_polling_are_wired():
    js = script()
    rs = between(js, "function renderSettings()", "function addVac()")
    rooms = between(rs, 'card("Räume"', 'card("Gas & Kosten"')
    assert rooms.index('<div id="room-restore"></div><div id="room-adopt"></div>') < rooms.index(
        '<details class="adopt alias" id="alias-box" ontoggle="aliasToggle(this)"') < rooms.index(
        '<p class="job-msg" id="rooms-job">${jobHtml(JOB)}</p>') < rooms.index('id="room-table"')
    assert '</div>${advHouseHtml()}`, false)' in between(rs, 'card("Erweitert"', "set-entities")
    assert "ALIAS = null; ALIAS_MSG = \"\";" in rs and "renderRoomRestore(); if (ALIAS_OPEN) loadAliases();" in rs
    assert "aliasReveal(); advReveal();" in rs
    assert "loadRoomRestore();" in source(js, "loadAdopt")
    assert "renderAliases();" in source(js, "roomsDirty")  # Raumänderung sperrt auch die Alias-Auswahl
    assert "jobPoll();" in source(js, "saveSettings")  # Raumwechsel → Ablauf „rooms“ sichtbar
    assert "\njobPoll();  // 0.42.1: läuft gerade „Hausdaten wiederherstellen“ bzw. „neu lernen“?" in js  # beim Laden
    assert "certBasisHtml(c.basis) +" in source(js, "loadCert")
    assert '${bs.note ? `. ${esc(bs.note)}` : ""}</p>' in source(js, "certPrint")  # wie das PDF
    adv = source(js, "advHouseHtml")
    assert 'jobButton("restore")' in adv and 'jobButton("relearn")' in adv and 'onclick="reloadHistory()"' in adv


def test_buttons_ask_first_and_nothing_goes_to_home_assistant():
    js = script()
    hj = source(js, "houseJob")
    assert hj.index('confirm(kind === "restore" ? await restoreAsk() : JOB_ASK[kind])') < \
        hj.index("fetch(`api/house/${kind}`, { method: \"POST\" })")
    assert 'getJSON("api/rooms/aliases")' in source(js, "restoreAsk")  # nur lesen: welche alten Sensoren
    assert "if (jobRunning() ||" in hj  # läuft schon: kein zweiter Start
    assert source(js, "jobButton").count("houseJob('${kind}')") == 1
    poll = source(js, "jobPoll")
    assert 'getJSON("api/house/job")' in poll and "setTimeout(jobPoll, JOB_POLL_MS)" in poll and "jobFinished()" in poll
    assert 'fetch("api/rooms/aliases", { method: "POST"' in source(js, "aliasSave")
    sec = between(js, SECTION, SECTION_END)
    posts = re.findall(r'fetch\(([^,]+), \{ method: "POST"', sec)
    assert posts == ["`api/house/${kind}`", '"api/rooms/aliases"'], posts
    for word in ("call_service", "set_temperature", "localStorage", "api/settings"):
        assert word not in sec, word
    ask = source(js, "JOB_ASK")
    assert "nur ergänzend: Es löscht nichts und ersetzt keinen gespeicherten Wert durch „unbekannt“" in ask
    assert "an Home Assistant wird nichts geschrieben" in ask
    assert "Es holt nichts aus Home Assistant und löscht keine Messwerte" in ask


# ------------------------------------------------------------------ Texte mit Antworten des echten Backends
def test_models_card_texts(tmp_path, real):
    got = js_run(tmp_path, f"""
const B = {json.dumps(real["bad"])}, OK = {json.dumps(real["ok"])}, F = {json.dumps(real["fresh"])};
out({{ bad: modelsHtml(B), ok: modelsHtml(OK), fresh: modelsHtml(F) }});
""")
    b, h = real["bad"], real["bad"]["house"]
    t = plain(got["bad"])
    assert t.startswith("Modelle Einzelheiten unter Diagnose Haus " + h["state"]), t
    loss = f"Wärmeverlust {n(h['heat_loss_w_per_k'])} ± {n(h['heat_loss_sd_w_per_k'])} W/K"  # geschützte Leerzeichen
    assert loss.replace(" ", "\u00a0").replace("Wärmeverlust\u00a0", "Wärmeverlust ") in got["bad"]
    assert loss in t and f"{h['learned_text']} · Stundenwerte seit {day(h['data']['from'])}" in t
    assert h["learned_text"].startswith(f"aus {n(h['hours_learned'])} Stunden und ")  # gelernt ≠ nur vorhanden
    assert f"Räume {b['rooms']['text']} · lernen noch: Wohnzimmer, Küche" in t
    assert f"Ventile {b['valves']['text']} · lernen aus der Historie nach: Wohnzimmer, Küche" in t
    assert f"Warmwasser {b['dhw']['state']} – {b['dhw']['reasons'][0]}" in t
    assert h["restore"] and h["warning"] in t and h["data"]["no_t_in"] > 1000
    assert ('<button class="btn small job-btn" data-kind="restore" onclick="houseJob(\'restore\')">Hausdaten '
            'wiederherstellen</button>') in got["bad"]
    assert '<p class="job-msg" id="models-job"></p>' in got["bad"]
    assert "Zuletzt: aus gespeicherten Daten neu gelernt" in t
    assert got["bad"].count('class="dot dot-ok"') == 1 and 'id="mc-warn"' in got["bad"]  # Haus plausibel, Rest lernt
    o = plain(got["ok"])
    assert 'id="mc-warn"' not in got["ok"] and "Hausdaten wiederherstellen" not in o
    assert real['ok']['house']['learned_text'] in o and "im Tagesmittel" in o
    f = plain(got["fresh"])
    fd = real["fresh"]["house"]["data"]
    assert f"Haus lernt – {real['fresh']['house']['reasons'][0]}" in f and "Wärmeverlust" not in f  # nichts gelernt
    assert (f"noch nichts gelernt – {n(fd['usable'])} Stunden mit Gas, Außen- und Innentemperatur seit "
            f"{day(fd['usable_from'])}") in f


def test_diagnose_details_texts(tmp_path, real):
    got = js_run(tmp_path, f"""
const B = {json.dumps(real["bad"])}, OK = {json.dumps(real["ok"])};
out({{ bad: modelsDetailHtml(B), ok: modelsDetailHtml(OK) }});
""")
    d = real["bad"]["house"]["data"]
    rows = [plain(r) for r in re.findall(r"<tr>(.*?)</tr>", got["bad"].split('id="dm-house">')[1].split("</table>")[0])]
    assert rows[0].startswith(f"Wärmeverlust {n(real['bad']['house']['heat_loss_w_per_k'])}")
    assert f"Stundenwerte gespeichert {n(d['hours'])} {day(d['from'])}–{day(d['to'])}" in rows
    assert (f"mit Gas, Außen- und Innentemperatur {n(d['usable'])} davon gelernt "
            f"{n(real['bad']['house']['hours_learned'])} (nur Heizperiode, ohne Warmwasser und Fremdwärme) · "
            f"{n(d['usable_days'])} Tage, {day(d['usable_from'])}–{day(d['usable_to'])} · {n(d['usable_heat'])} davon "
            "bis 15 °C außen") in rows
    assert (f"ohne Innentemperatur {n(d['no_t_in'])} mit Gas und Außentemperatur, {day(d['no_t_in_from'])}–"
            f"{day(d['no_t_in_to'])} – lernt das Hausmodell nicht") in rows
    assert 'id="dm-warn"' in got["bad"] and 'id="dm-warn"' not in got["ok"]
    assert got["bad"].count("<tr>") == 8 + 3 + 3  # Haus, Räume (Kopf + 2), Ventile (Kopf + 2)
    t = plain(got["bad"])
    assert "Ventile: 0 von 2 belastbar" in t and "lernt nach lernt aus der gespeicherten Historie nach" in t
    assert "Zuletzt" in t and "Aus gespeicherten Daten neu gelernt: Hausmodell " in t and "mit allen Werten gelernt" in t
    assert 'onclick="openAdvanced();return false">Einstellungen → Erweitert</a>' in got["bad"]


def test_job_states_and_results(tmp_path, real):
    run = {"kind": "restore", "running": True, "started": None, "done": None, "result": None, "error": None}
    st = {"job": run, "status": "stelle Hausdaten wieder her", "busy": None}
    got = js_run(tmp_path, f"""
const R = {json.dumps(real["restore"])}, L = {json.dumps(real["relearn"])}, now = Math.floor(Date.now() / 1000);
const done = (kind, result, error = null, ago = 5) =>
  ({{ job: {{ kind, running: false, started: now - 60, done: now - ago, result, error }}, status: "läuft" }});
const run = {json.dumps(st)};
const started = {{ ...run, job: {{ ...run.job, started: now - 42 }} }};
out({{
  run: jobHtml(run), started: jobHtml(started), restore: jobHtml(done("restore", R)),
  relearn: jobHtml(done("relearn", L)),
  rooms: jobHtml(done("rooms", {{ rooms: ["climate.hk_va01"], room_rows: 1234, relearn: L }})),
  error: jobHtml(done("restore", null, "RuntimeError: kaputt")), old: jobHtml(done("relearn", L, null, 3600)),
  busy: jobHtml({{ ...run,
    note: "Nicht gestartet: Läuft schon: stelle Hausdaten wieder her – bitte warten, bis das fertig ist." }}),
  none: jobHtml({{ job: null }}), button: (JOB = run, jobButton("relearn")),
}});
""")
    assert plain(got["run"]) == "Läuft: Hausdaten wiederherstellen – stelle Hausdaten wieder her. Die Übersicht " \
                                "aktualisiert sich danach."
    assert re.search(r"\(seit 4[23] s\)\. Die Übersicht aktualisiert sich danach\.$", plain(got["started"]))
    r, rl = real["restore"], real["relearn"]
    assert r["no_t_in_after"] == 0 and r["usable_after"] > r["usable_before"]
    sec = f"{r['seconds'] + rl['seconds']:.1f}".replace(".", ",").replace(",0", "")
    assert plain(got["restore"]) == (
        f"Hausdaten wiederhergestellt: Stunden mit Innentemperatur {n(r['usable_before'])} → {n(r['usable_after'])}, "
        f"ohne {n(r['no_t_in_before'])} → 0 · alter Sensor gemerkt für Küche · {n(r['room_rows'])} Raumwerte alter "
        f"Thermostate nachgeholt (climate.wohnzimmer) · danach neu gelernt (zusammen {sec} s).")
    assert [(o["from"], o["rows"] > 0) for o in r["old"]] == [("climate.wohnzimmer", True)]
    assert [(c["stat"], c["ok"]) for c in r["checks"]] == [("sensor.wohnzimmer_temperature", True),
                                                          ("sensor.kueche_temperature", True)]
    since = time.strftime("%d.%m.%Y", time.localtime(rl["since"]))
    assert plain(got["relearn"]).startswith(f"Aus gespeicherten Daten neu gelernt: Hausmodell {n(rl['house_updates'])} "
                                            f"von {n(rl['hours'])} Stunden mit allen Werten gelernt, Räume ab {since} (")
    assert plain(got["rooms"]) == ("Historie der neuen Räume nachgeholt: 1.234 Messwerte, danach aus den "
                                   "gespeicherten Daten neu gelernt.")
    assert plain(got["error"]).startswith("Hausdaten wiederherstellen fehlgeschlagen: RuntimeError: kaputt – HeatPilot "
                                          "arbeitet mit dem bisherigen Stand weiter")
    assert got["old"] == "" and got["none"] == ""  # lange fertig bzw. nie gelaufen: nichts
    assert plain(got["busy"]).startswith("Nicht gestartet: Läuft schon: stelle Hausdaten wieder her – bitte warten")
    assert 'class="msg-err"' in got["busy"] and plain(got["busy"]).endswith("aktualisiert sich danach.")
    assert got["button"].endswith(" disabled>Aus gespeicherten Daten neu lernen</button>")  # gesperrt, solange es läuft


def test_alias_box_and_adopt_message(tmp_path, real):
    got = js_run(tmp_path, f"""
const A = {json.dumps(real["aliases"])}, J = {json.dumps(real["adopt"])};
out({{ box: aliasHtml(A),
      locked: aliasHtml(A, "Erst speichern – in der Tabelle sind Raumänderungen noch nicht gespeichert."),
      fail: aliasHtml({{ error: "Keine Verbindung zum Add-on" }}), adopt: adoptDone(J),
      noRestore: adoptDone({{ ...J, alias: null, restore_suggested: false }}) }});
""")
    a = {r["key"]: r for r in real["aliases"]["rooms"]}
    assert a["climate.hk_va01"]["alias"]["how"] == "device" and a["climate.hk_va02"]["suggested"]
    rows = re.findall(r'<div class="adopt-row alias-row" data-room="([^"]+)">(.*?)</div>', got["box"])
    assert [k for k, _ in rows] == ["climate.hk_va01", "climate.hk_va02"]
    wz, ku = rows[0][1], rows[1][1]
    assert plain(wz).startswith("Wohnzimmer · jetzt sensor.hk_va01_temperature alter Sensor: "
                                "sensor.wohnzimmer_temperature (Sensor am alten Gerät, von climate.wohnzimmer)")
    assert '<option value="sensor.wohnzimmer_temperature" selected>' in wz
    assert "sensor.hk_va01_temperature</option>" not in wz  # der aktuelle Sensor ist kein Alias
    assert "kein alter Sensor gemerkt – Vorschlag vorgewählt" in plain(ku)
    assert f'<option value="{a["climate.hk_va02"]["suggested"]}" selected>' in ku
    assert "Langzeitstatistik mit passendem Namen" in ku and 'value="sensor.out"' not in got["box"]  # Außenfühler nie
    assert got["box"].count(" disabled") == 0 and got["locked"].count(" disabled") == 4
    assert '<p class="adopt-lock">Erst speichern' in got["locked"]
    assert plain(got["fail"]) == "Nicht lesbar: Keine Verbindung zum Add-on"
    j = real["adopt"]
    t = plain(got["adopt"])
    assert t.startswith(f"Übernommen: {j['from_days']} Tage Messwerte von {j['from_name']} in {j['to_name']}.")
    assert "Alter Temperatursensor gemerkt: sensor.wohnzimmer_temperature (Sensor am alten Gerät) – er liefert die " \
           "Innentemperatur vor dem Wechsel." in t
    assert j["restore_suggested"] and "Vielen Stunden fehlt noch die Innentemperatur – das Hausmodell lernt sie " \
                                      "nicht. Hausdaten wiederherstellen liest sie mit den alten Sensoren neu ein." in t
    assert "Alter Temperatursensor" not in got["noRestore"] and "houseJob" not in got["noRestore"]


def test_adopt_box_from_real_overview(tmp_path, real):
    """Teilarbeit „Gelerntes übernehmen“: Kasten über der Raum-Tabelle mit der echten Übersicht nach dem Schaden."""
    got = js_run(tmp_path, f"""
const A = {json.dumps(real["orphans"])};
out({{ box: adoptHtml(A), locked: adoptHtml(A, "Erst speichern – die Einstellungen sind noch nicht gespeichert."),
      none: adoptHtml({{ orphans: [], suggestions: [], rooms: [] }}), ask: adoptAsk("Wohnzimmer", "Wohnzimmer") }});
""")
    o = real["orphans"]
    names = ", ".join(f"{x['name']} ({x['days']} Tage)" for x in o["orphans"])
    t = plain(got["box"])
    assert t.startswith(f"Gespeicherte Daten ohne Raum: {names} Messwerte von Thermostaten, die zu keinem Raum "
                        "mehr gehören")
    sugs = re.findall(r'data-from="([^"]+)" data-to="([^"]+)" onclick="adoptGo\(this\)"', got["box"])
    assert sugs == [(x["from"], x["to"]) for x in o["suggestions"]] and len(sugs) == 2
    assert f"Selbst zuordnen ({len(o['orphans'])})" in t and got["box"].count('class="adopt-row"') == len(o["orphans"])
    # gesperrt: je Vorschlag der Knopf, je alter Entität Auswahl und Knopf
    assert got["box"].count(" disabled") == 0 and got["locked"].count(" disabled") == len(sugs) + 2 * len(o["orphans"])
    assert got["none"] == ""
    assert got["ask"] == ("Die Messwerte von Wohnzimmer werden Raum Wohnzimmer zugeordnet, Raum- und Ventilmodell neu "
                          "berechnet. Nicht rückgängig zu machen.")


def test_certificate_basis(tmp_path, real):
    got = js_run(tmp_path, f"""
const BAD = {json.dumps(real["cert_bad"])}, OK = {json.dumps(real["cert_ok"])};
MODELS = {json.dumps(real["bad"])};
const bad = certBasisHtml(BAD);
MODELS = {json.dumps(real["ok"])};
out({{ bad, ok: certBasisHtml(OK) }});
""")
    b = real["cert_bad"]
    t = plain(got["bad"])
    assert b["year_from"] == b["from"]  # weniger als ein Jahr: kein eigener Zeitraum für Verbrauch und Kessel
    assert t.startswith(f"Grundlage: Stundenwerte {day(b['from'])}–{day(b['to'])} · Hausmodell aus "
                        f"{n(b['house_learned_hours'])} Stunden und {n(b['house_learned_days'])} Tageswerten gelernt – "
                        f"von {n(b['house_hours'])} Stunden mit Gas, Außen- und Innentemperatur seit "
                        f"{day(b['house_from'])} Nicht berücksichtigt: ")
    m = b["missing"][0]
    assert m["key"] == "no_t_in" and (f"{n(m['hours'])} Stunden ohne Innentemperatur – das Hausmodell lernt sie nicht "
                                      f"({day(m['from'])}–{day(m['to'])})") in t
    assert '<li class="limit">' in got["bad"] and "houseJob('restore')" in got["bad"]
    o = plain(got["ok"])
    assert real["cert_ok"]["missing"] == [] and "Nicht berücksichtigt" not in o and "houseJob" not in got["ok"]


def test_inline_script_is_valid_javascript(tmp_path):
    if not NODE:
        pytest.skip("node nicht verfügbar")
    f = tmp_path / "inline421.js"
    f.write_text(script(), encoding="utf-8")
    res = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_docs_and_changelog_name_the_places():
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for text in ("**Karte „Modelle“**", "„Modelle – Einzelheiten“", "**Alter Temperatursensor je Raum**",
                 "Einstellungen → Erweitert", "„Aus gespeicherten Daten neu lernen“", "Nicht berücksichtigt"):
        assert text in docs, text
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## ")[1].split("\n## ")[0]
    ui = next(line for line in head.splitlines() if line.startswith("- **Oberfläche:**"))
    for text in ("Karte „Modelle“", "Diagnose", "Alter Temperatursensor je Raum", "Erweitert", "Zertifikat"):
        assert text in ui, text
    assert "tests/test_ui_v421.py" in head
