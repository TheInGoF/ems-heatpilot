"""0.46.5: sanftes Vorheizen, RTL-Grenze immer, Plausibilität großer Heizkörper, 35 °C nur als Untergrenze beim Heizen,
stummer Regler bei antwortendem Kessel, „nur Pumpe“ ohne Bedarf und die Pumpe in Live.

Rückmeldung aus dem Betrieb: morgens kurz 60 °C, dann 35 °C – fast nur ein Schalter, und die 60 °C übersteuern die
Fußbodenheizung; zwei Räume mit großen Heizkörpern fielen aus der Vorlaufberechnung; alle Räume geboostet und unter
Soll, trotzdem „Heizbetrieb aus“; ein abgebauter Regler, den EMS-ESP mit „Fehler“ statt „kein Gerät“ beantwortet;
„die Pumpe muss zumindest an bleiben“. Alle Häuser und Werte sind erfunden.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from heatpilot import __version__, guards, regulator
from heatpilot import boiler_rule as br
from heatpilot import floor_limit as fl
from heatpilot import preview as pv
from heatpilot.boiler_flow import compute
from heatpilot.config import Config, validate
from heatpilot.controlloop import PREHEAT_FLOW_FROM, Presence, desired, preheat_hours
from heatpilot.emsesp import FAILED, OK
from heatpilot.models import RoomModel

from test_boiler41 import FLOW, T0, run
from test_boiler41 import house as boiler_house
from test_control34 import MON, at, climate, slow_room, ts_of, wz
from test_floor391 import floors, reading
from test_flow_min39 import _run as flow_run
from test_preview37 import house as pv_house

ROOT = Path(__file__).resolve().parents[1]
TUE = (2026, 10, 13)
CFG = Config(control_mode="heatpilot")


# ------------------------------------------------------------------ 1. Vorheizen sanft
def test_preheat_takes_lowest_flow_within_cap_and_starts_earlier():
    room = slow_room()  # Beispielraum: 20 → 21 °C bei 5 °C außen
    # niedrigster Vorlauf ab 35 °C, der im Deckel (3 h) ans Ziel kommt: 45 °C – 44 °C bräuchte länger
    assert room.time_to_reach(20.0, 21.0, 44.0, 5.0) > 3.0 >= room.time_to_reach(20.0, 21.0, 45.0, 5.0)
    lead = preheat_hours(room, 20.0, 21.0, 75, 5.0, 3.0, soft=True)  # seit 0.50.4 nur vor dem Urlaubsende
    assert lead == pytest.approx(room.time_to_reach(20.0, 21.0, 45.0, 5.0))
    assert lead > 2.5 and room.time_to_reach(20.0, 21.0, 75.0, 5.0) < 1.2  # früher als mit dem maximalen Vorlauf
    d = desired(wz(), at(MON, 23), CFG, Presence(home_count=1), room, 20.0, 5.0)
    daily = preheat_hours(room, 20.0, 21.0, 75, 5.0, 3.0)  # täglich: Aufheiz-Vorlauf (≈ 1 h)
    assert d.phase == "nacht" and d.until == at(TUE, 4, 30) - timedelta(hours=daily) and daily < 1.2
    # reicht schon der niedrigste (35 °C), so lange wie er braucht – nicht länger
    assert PREHEAT_FLOW_FROM == 35.0
    assert preheat_hours(room, 20.8, 21.0, 75, 5.0, 3.0, soft=True) == pytest.approx(room.time_to_reach(20.8, 21.0, 35.0, 5.0))


def test_preheat_unreachable_within_cap_takes_the_cap():
    room = slow_room()
    assert room.time_to_reach(18.0, 21.0, 75.0, 5.0) > 3.0  # auch der maximale schafft es nicht im Deckel
    assert preheat_hours(room, 18.0, 21.0, 75, 5.0, 3.0) == 3.0
    assert preheat_hours(slow_room(a=0.001), 18.0, 21.0, 75, 5.0, 2.0) == 2.0  # nie erreichbar


def test_preview_finds_the_same_low_flow(tmp_path):
    eng, _, _ = pv_house(tmp_path, temp=20.0)
    run_ = pv._Run(eng, ts_of(MON, 23))
    r, until = run_.rooms[0], at(TUE, 4, 30)
    lead = preheat_hours(r.model, 20.0, 21.0, CFG.max_flow_temp, 5.0, 3.0)
    from heatpilot.heatup import heatup_flow
    assert run_._preheat_flow(r, 21.0, until, until - timedelta(hours=lead)) == heatup_flow(r.model, 20.0, 21.0, 5.0, 75)


def test_begun_preheat_stays_until_the_target(tmp_path, monkeypatch):
    # warm vor der Zeit: ohne laufendes Vorheizen hieße das Nacht – ein begonnenes bleibt bis zum Nachtende
    room, now, end = slow_room(), at(TUE, 3), at(TUE, 4, 30)
    assert desired(wz(), now, CFG, Presence(home_count=1), room, 21.0, 5.0).phase == "nacht"
    d = desired(wz(), now, CFG, Presence(home_count=1), room, 21.0, 5.0, preheating=end)
    assert (d.phase, d.setpoint, d.until) == ("vorheizen", 21.0, end)
    # ein anderes Ziel hält nicht
    assert desired(wz(), now, CFG, Presence(home_count=1), room, 21.0, 5.0, preheating=end + timedelta(days=1)).phase \
        == "nacht"
    # Vorschau: genau ein Wechsel Nacht → Vorheizen, kein Pendeln
    monkeypatch.setattr(pv, "FLOW_MIN", 25.0)
    eng, _, loop = pv_house(tmp_path)
    phases = [row["phases"]["Wz"] for row in pv._Run(eng, ts_of(MON, 12)).run()]
    seq = [p for i, p in enumerate(phases) if not i or p != phases[i - 1]]
    assert seq == ["komfort", "nacht", "vorheizen", "komfort"]
    # Steuerung: merkt sich das Ziel und übergibt es (auch der Vorschau jetzt)
    climate(eng, "climate.wz", 20.0, 18.0)
    sample, rooms, _ = eng.snapshot()
    loop.plan(ts_of(TUE, 4), sample, rooms)  # 0.50.4: Vorheizen ≈ 1 h vorher
    assert loop.rooms["climate.wz"]["phase"] == "vorheizen"
    assert loop.state["climate.wz"]["preheat"] == int(end.timestamp())
    climate(eng, "climate.wz", 21.2, 21.0)  # schneller warm als geplant
    sample, rooms, _ = eng.snapshot()
    loop.plan(ts_of(TUE, 4) + 600, sample, rooms)
    assert loop.rooms["climate.wz"]["phase"] == "vorheizen"
    eng.control_loop = loop
    assert pv._Run(eng, ts_of(TUE, 3)).held == {"climate.wz": end}


# ------------------------------------------------------------------ 2. RTL-Grenze immer
def test_rtl_limit_applies_without_demand():
    f = floors("rtl", 50)
    quiet = {"climate.bad": reading(22.0, 22.0)}  # am Soll, Ventil zu: fordert nicht an
    assert fl.demand(f[0], quiet) is False and fl.applies(f[0], False)
    assert fl.flow_cap(f, quiet) == (50.0, "Fußboden Bad (RTL-Ventil, fordert nicht an): höchstens 50 °C")
    assert fl.rule(f[0]) == "höchstens 50 °C, immer (RTL-Ventil)"
    assert fl.flow_cap(floors("mischer", 50), quiet) == (None, None)  # Mischer weiter nie


# ------------------------------------------------------------------ 3. große Heizkörper plausibel
def test_radiator_ratio_5_plausible_7_not():
    say = lambda m: [x for x in guards.check_room(m)["reasons"] if x.startswith("Heizwirkung/Auskühlen")]
    radiator = RoomModel("Kind")
    radiator.rls.theta[:2] = [0.10, 0.02]  # (a + f) / b = 5
    assert say(radiator) == []
    radiator.rls.theta[:2] = [0.14, 0.02]  # 7
    assert say(radiator) == ["Heizwirkung/Auskühlen 7 außerhalb 0.3–6"]


# ------------------------------------------------------------------ 4. 35 °C ist die Untergrenze, wenn geheizt wird
def test_heating_up_below_35_heats_with_35_only_holding_is_off():
    boosted = [(29.0, "Flur", "heizt auf 22,0 °C (ca. 1 h)"), (28.0, "Wz", "hält 21,0 °C")]
    assert flow_run()._flow(boosted, 5.0) == (35, True, "Flur heizt auf 22,0 °C (ca. 1 h)")
    pre = [(31.0, "Bad", "heizt vor bis 04:30")]
    assert flow_run()._flow(pre, 5.0)[:2] == (35, True)
    flow, heating, why = flow_run()._flow([(29.0, "Flur", "hält 21,0 °C"), (28.0, "Wz", "hält 21,0 °C")], 5.0)
    assert (flow, heating) == (None, False) and why == "nötiger Vorlauf unter 35 °C (Flur) – Heizbetrieb aus"


def test_boosted_rooms_below_setpoint_keep_heating_on(tmp_path):
    eng, _, _ = boiler_house(tmp_path, temp=20.0, a=0.02)  # braucht zum Halten 29 °C, liegt 1 K unter dem Soll
    t, _ = compute(eng, T0)
    assert t.heating is True and t.flow >= 35 and t.reason.startswith("Wz heizt auf 21,0 °C")


# ------------------------------------------------------------------ 5. Regler stumm, Kessel antwortet
def test_silent_regulator_with_answering_boiler_counts_as_absent(tmp_path):
    eng, _, _ = boiler_house(tmp_path)
    eng.regulator_watch.clear()

    def note(thermostat, boiler, ts):
        eng.ems = {"boiler": {"selflowtemp": 30}} if boiler == OK else {}
        eng.ems_ts = ts
        regulator.note(eng, {"boiler": boiler, "thermostat": thermostat}, ts)

    t = 1_000_000.0
    note(OK, OK, t)  # Regler gesehen, dann abgebaut
    for i in range(1, 11):
        note(FAILED, OK, t + i * 30)
    r = regulator.regulator(eng, t + 300)
    assert r["present"] is True and r["hold"]  # Sicherheitsfrist nach dem letzten Lebenszeichen bleibt
    later = t + regulator.HOLD_S
    for i in range(11):
        note(FAILED, OK, later + i * 30)
    r = regulator.regulator(eng, later + 300)
    assert r["present"] is False
    assert r["text"] == "Kein Regler am Bus (der Regler (Gerät thermostat) antwortet EMS-ESP nicht, der Kessel schon)"
    note(FAILED, FAILED, later + 330)  # auch der Kessel nicht: unbekannt wie bisher
    assert regulator.regulator(eng, later + 330)["present"] is None


# ------------------------------------------------------------------ 6. kein Bedarf: Pumpe läuft weiter
def test_no_demand_keeps_heating_on_with_pump_flow(tmp_path):
    eng, _, bc = boiler_house(tmp_path)
    climate(eng, "climate.wz", 21.6, 21.0, hvac_action="idle")  # über Soll: kein Raum braucht Wärme
    t, _ = compute(eng, T0)
    assert (t.heating, t.flow, t.idle) == (True, br.PUMP_FLOW, True) == (True, 20, True)
    assert t.reason == "kein Raum braucht Wärme – Pumpe läuft, Vorlauf-Soll 20 °C"
    assert compute(eng, T0, None, (25, 90))[0].flow == 25  # Mindestwert der Entität darüber
    assert compute(eng, T0, None, (40, 90))[0].heating is False  # das wäre schon Heizen: aus wie bisher
    # Kessel zeigt 30 °C: seit 0.50.5 sofort senken (kein Ventil offen), der Heizbetrieb bleibt an
    eng.states[FLOW]["state"] = "30"
    bc.plan(T0)
    assert bc.view["decision"]["flow"] == "30 → 20 °C" and not bc.view["write"]["heating"]
    assert bc.as_dict(T0)["status_text"] == "Kessel: HeatPilot · kein Bedarf – Pumpe läuft, Vorlauf-Soll 20 °C"
    # Control lässt genau den Pumpenwert unter 35 °C zu – sonst nichts darunter
    run(bc.control.set_flow(20, "Test"))
    for bad in (21, 34):
        with pytest.raises(PermissionError):
            run(bc.control.set_flow(bad, "Test"))
    bc.control.flow_pump = False  # „Heizbetrieb ganz aus“
    with pytest.raises(PermissionError):
        run(bc.control.set_flow(20, "Test"))


def test_heating_fully_off_setting_and_old_settings(tmp_path):
    eng, _, _ = boiler_house(tmp_path, boiler_idle="aus")
    climate(eng, "climate.wz", 21.6, 21.0, hvac_action="idle")
    t, _ = compute(eng, T0)
    assert (t.heating, t.flow, t.idle) == (False, None, False) and t.reason == "kein Raum braucht Wärme – Heizbetrieb aus"
    assert Config.from_raw({}).boiler_idle == "pumpe"  # alte Einstellungen ohne die Wahl: Standard
    assert Config.from_raw({"boiler_idle": "irgendwas"}).boiler_idle == "pumpe"
    ok, errors = validate({"boiler_idle": "aus"})
    assert errors == [] and ok["boiler_idle"] == "aus"
    assert validate({"boiler_idle": "nie"})[1]


def test_preview_says_pump_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(pv, "FLOW_MIN", 25.0)
    eng, _, _ = pv_house(tmp_path)
    p = pv.preview(eng, ts_of(MON, 12))
    night = next(h for h in p["hours"] if h["t"][11:16] == "23:00")
    assert night["heating"] is False and night["idle"] is True
    assert night["flow_reason"] == "kein Raum braucht Wärme – Pumpe läuft, Vorlauf-Soll 20 °C"
    assert not any(w["what"] == "heating" and w["value"] is False for h in p["hours"] for w in h["writes"])
    eng.cfg.boiler_idle = "aus"
    night = next(h for h in pv.preview(eng, ts_of(MON, 12))["hours"] if h["t"][11:16] == "23:00")
    assert night["idle"] is False and night["flow_reason"] == "kein Raum braucht Wärme – Heizbetrieb aus"


# ------------------------------------------------------------------ 7. Oberfläche, Version, Doku
def test_live_pump_tile_and_band_and_setting():
    page = (ROOT / "ems_heatpilot" / "heatpilot" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'kpi("Pumpe", pump' in page and "Nachlauf ${fmt(n.pump_delay, 0)} min" in page
    assert '{ name: "Pumpe läuft", values: pumpBand, color: "var(--band-pump)" }, ...bands]' in page
    assert "--band-pump:" in page and 'id="f-boiler_idle"' in page and "out.boiler_idle" in page
    assert 'rtl: "gilt immer – das RTL öffnet, sobald der Rücklauf kalt ist"' in page
    assert "solange der Raum Wärme anfordert" not in page


def test_version_changelog_docs():
    assert __version__ >= "0.46.5"
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## 0.46.5\n")[1].split("\n## ")[0]
    for word in ("sanft", "RTL", "0,3–6", "35 °C", "Pumpe läuft weiter", "thermostat", "tests/test_v465.py"):
        assert word in head, word
    for personal in ("Ingo", "4,4", "5,0", "Flur"):
        assert personal not in head, personal
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    for text in ("niedrigste Vorlauf ab 35 °C", "bleibt bis zum Ziel", "gilt seit 0.46.5 **immer**",
                 "zwischen 0,3 und 6 liegt (seit 0.46.5", "„Wenn kein Raum Wärme braucht“", "Kachel **Pumpe**"):
        assert text in docs, text


def test_timeline_names_pump_only(tmp_path):
    from test_ui_v463 import SAMPLE, run_nx
    r = run_nx(tmp_path, SAMPLE + """
P.steps[3] = { ...P.steps[3], idle: true, flow_why: "kein Raum braucht Wärme – Pumpe läuft, Vorlauf-Soll 20 °C" };
const runs = nxRuns(P, "flow");
out({ rows: runs.map((x, i) => [nxTime(P, x, i), nxName(x), ...nxVal(x, "flow")]), tip: nxTip(P, "flow", runs[1], 1) });
""")
    assert r["rows"][1] == ["23:00–05:00", "nur Pumpe", "Pumpe", ""]
    assert "kein Bedarf – nur Pumpe" in r["tip"] and "Heizbetrieb aus" not in r["tip"]
