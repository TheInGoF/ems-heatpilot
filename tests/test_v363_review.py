"""Prüfung 0.36.3: nachgestellte Funde zu Desinfektion und Sperre gegen den Heizkreis von EMS-ESP.

Je Fund ein Test, der ihn nachstellt und das richtige Verhalten festhält:
1. Ein Termin, zu dem niemand desinfiziert (kein ES73, HeatPilot löst nicht aus), ändert weder Empfehlung noch
   Schatten – sonst stiege die Ersparnis um Wärme, die es nicht gibt. Er zählt erst bestätigt.
2. Der Vorschlag nimmt die größte regelmäßige Zapfung, nicht die häufigste.
3. Eine Einmalladung ist keine Desinfektion: Sie bestätigt keinen Termin und setzt keinen aus.
4. Ein Termin deckt erst nach seiner Ladedauer – nicht die Zapfung zur selben Stunde.
6. Erste Woche: kein doppeltes Aufheizen im Schatten (Termin und Regler zu anderer Zeit).
7./8. Oberfläche: leere Uhrzeit bei „aus“ blockiert das Speichern nicht; die Sperre „macht mit“ überschreibt
   nicht das gespeicherte „macht mit“ der Ventile im selben Raum.
9. Desinfektionstemperatur vom Kessel nur im Bereich der Einstellung (60–80 °C).
10. Gespeicherter Boost auf den Heizkreis von EMS-ESP aus 0.36.2: verworfen, Hinweis im Reiter Heizen.
11. Bereitschaft nennt den Heizkreis einmal.
12. Lesbarkeit: discovery.py unter ~600 Zeilen, Rollen in eigener Datei; prev_disinfection weg.
Das Beispiel des Entwurfs „So 18:00 → So 17:00“ (Fund 5) ist eine Frage an den Nutzer: Nach der Formel des
Entwurfs ergibt es So 17:30 (test_disinfect363.test_suggestion_sunday_bath hält beides fest).
"""

import asyncio
import json
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import pytest

from heatpilot import dhw_disinfect as dd, discovery, discovery_roles, readiness
from heatpilot.config import FIELDS, Config, save_settings, validate
from heatpilot.control import KV, KV_EMS_LEFT, Control
from heatpilot.controlloop import ControlLoop
from heatpilot.dhw import SLOTS, DhwModel
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.store import SAMPLE_COLS, Store

from test_control import FakeHA
from test_dhw_special import MONDAY, STEP, _cfg
from test_disinfect363 import _sim_plan, bath_model, confirm
from test_readiness36 import house, room
from test_ui_v361 import plain, script
from test_ui_v363 import run_js

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
SUN18 = datetime(2026, 1, 4, 18, 0)


def plan_cfg(day: str, at: dtime) -> Config:
    cfg = _cfg()
    cfg.dhw_disinfect_day, cfg.dhw_disinfect_time = day, at
    return cfg


def same(a: dict, b: dict) -> bool:
    """Gleiche Ersparnis und gleich viele zu kalte Wannen (letzte Woche, Schatten wie echter Speicher)."""
    return (a["cold_sunday"], round(a["loss_s"], 3), round(a["loss"], 3)) == \
        (b["cold_sunday"], round(b["loss_s"], 3), round(b["loss"], 3))


# ------------------------------------------------------------------ 1. Termin zählt nur bestätigt
def test_date_without_any_disinfection_changes_neither_saving_nor_cold_baths():
    _, _, none = _sim_plan(3, _cfg())
    m, _, log = _sim_plan(3, plan_cfg("So", dtime(17, 30)))
    assert same(log, none) and log["cold_sunday"] >= 2 and log["mirrors"] == []
    assert m.special_charges == 0 and m.disinfects == []
    # die Empfehlung vor der Wanne ist dieselbe wie ohne Termin (vorher höher laden)
    sun_16 = MONDAY + timedelta(days=20, hours=16)
    cfg = plan_cfg("So", dtime(17, 30))
    assert m.recommend(sun_16, True, None, cfg) == m.recommend(sun_16, True, None, _cfg())
    assert dd.counts_text(m, cfg, sun_16) == "Termin noch nicht bestätigt – kein Regler desinfiziert zu dieser Zeit"


def test_controller_at_same_time_confirms_and_changes_nothing_else():
    """ES73 desinfiziert sonntags 13 Uhr, der Termin steht auf derselben Zeit: bestätigt; Ersparnis wie ohne Termin
    (die Wanne danach ist ohnehin gedeckt), mit und ohne Kesselschalter."""
    for flag in (True, False):
        _, _, base = _sim_plan(3, _cfg(), es73_hour=13, flag=flag)
        cfg = plan_cfg("So", dtime(13, 0))
        m, _, log = _sim_plan(3, cfg, es73_hour=13, flag=flag)
        assert log["cold_sunday"] == base["cold_sunday"] == 0, flag
        assert abs(log["loss_s"] - base["loss_s"]) < 0.05, flag
        assert len(m.disinfects) == 3 and dd.plan_counts(m, cfg, MONDAY + timedelta(days=20, hours=12)), flag


def test_controller_at_other_time_keeps_its_own_time():
    _, _, base = _sim_plan(3, _cfg(), es73_hour=13)
    cfg = plan_cfg("So", dtime(17, 30))
    m, _, log = _sim_plan(3, cfg, es73_hour=13)
    assert same(log, base) and log["mirrors"] == []
    now = MONDAY + timedelta(days=20, hours=17)
    assert not dd.plan_counts(m, cfg, now)
    assert dd.counts_text(m, cfg, now).endswith("der Regler desinfiziert zu anderer Zeit (zuletzt So 13:15) – es zählt "
                                                 "seine Zeit")


# ------------------------------------------------------------------ 2. größte statt häufigste Zapfung
def _model(weeks=8):
    m = DhwModel()
    m.profile_n = [weeks] * SLOTS
    m.kwh_per_k, m.kwh_per_k_n = {"kalt": 0.18, "warm": 0.18}, {"kalt": 3, "warm": 3}
    return m


def _add(m, first, size, weeks=8, skip=()):
    for w in range(weeks):
        if w not in skip:
            m.big_draws.append([(first + timedelta(days=7 * w)).isoformat(timespec="minutes"), size, False])


NOW = SUN18 + timedelta(days=49, hours=2)
WED19 = SUN18 - timedelta(days=4) + timedelta(hours=1)


def test_suggestion_takes_largest_regular_draw_not_most_frequent():
    m = _model()
    _add(m, SUN18, 14.0, skip=(3,))  # So 18 Uhr 14 K an 7 von 8 Sonntagen
    _add(m, WED19, 7.0)              # Mi 19 Uhr 7 K an 8 von 8 Mittwochen
    s = dd.suggestion(m, 18.0, 50.0, 70.0, NOW)
    assert (s["day"], s["time"], s["hits"], s["draw_k"]) == ("So", "17:30", 7, 14.0)
    assert s["text"].startswith("So 18 Uhr zapfst du am meisten (7 von 8 Sonntagen, ≈ 14 K)")
    m = _model()
    _add(m, SUN18, 30.0, skip=(1, 3, 5))  # 5 von 8, aber 30 K
    _add(m, WED19, 6.5, skip=(0, 4))      # 6 von 8, 6,5 K
    assert dd.suggestion(m, 18.0, 50.0, 70.0, NOW)["day"] == "So"
    # gleich groß: die häufigere
    m = _model()
    _add(m, SUN18, 10.0, skip=(3,))
    _add(m, WED19, 10.0)
    assert dd.suggestion(m, 18.0, 50.0, 70.0, NOW)["day"] == "Mi"


def test_night_limit_with_equal_hits_falls_back_to_next_largest():
    m = _model()
    _add(m, SUN18.replace(hour=6), 30.0)  # größte, aber Beginn läge nachts (05:30)
    _add(m, SUN18 - timedelta(days=3), 9.0)  # Do 18 Uhr, gleich oft
    _add(m, WED19, 5.0)                     # Mi 19 Uhr, kleiner
    s = dd.suggestion(m, 18.0, 50.0, 70.0, NOW)
    assert (s["day"], s["time"], s["hits"], s["draw_k"]) == ("Do", "17:30", 8, 9.0)


# ------------------------------------------------------------------ 3. Einmalladung ist keine Desinfektion
def _charge(m: DhwModel, start: datetime, peak: float, flagged: bool, disinfect_temp: float = 70.0) -> None:
    """Eine Sonderladung von 50 °C bis ``peak`` (Kessel meldet sie), danach Ruhe."""
    t, now, done = 50.0, start, False
    for _ in range(40):
        heated = not done and t < peak
        t_new = min(t + 5.0, peak) if heated else t - 0.05
        done = done or t_new >= peak
        m.step(now, STEP, round(t, 1), round(t_new, 1), heated, 10.8 if heated else None, special=heated,
               dhw_set=55.0, burner_kw_prev=10.8 if heated else 0.0, disinfect=flagged and heated,
               disinfect_temp=disinfect_temp)
        t, now = t_new, now + timedelta(minutes=5)


def test_onetime_charge_neither_confirms_nor_suspends_the_date():
    assert dd.is_disinfection(True, 55.0, 70.0) and dd.is_disinfection(False, 65.0, 70.0)
    assert not dd.is_disinfection(False, 64.9, 70.0) and not dd.is_disinfection(False, 60.0, None)
    cfg = plan_cfg("So", dtime(17, 30))
    m = DhwModel()
    _charge(m, MONDAY + timedelta(days=6, hours=17, minutes=30), 70.0, flagged=True)  # Regler zur Terminzeit
    assert m.special_charges == 1 and len(m.disinfects) == 1
    wed = MONDAY + timedelta(days=9, hours=19)
    _charge(m, wed, 60.0, flagged=False)  # Einmalladung Mi 19 Uhr: Sonderladung, keine Desinfektion
    assert m.special_charges == 2 and len(m.disinfects) == 1 and m.last_special.weekday() == 2
    sun = MONDAY + timedelta(days=13, hours=12)
    assert dd.plan_counts(m, cfg, sun) and dd.counts_text(m, cfg, sun) is None
    # nur die Einmalladung, kein Regler: nicht bestätigt – aber kein „Regler desinfiziert selbst“
    m2 = DhwModel()
    _charge(m2, wed, 60.0, flagged=False)
    assert m2.disinfects == [] and dd.counts_text(m2, cfg, sun) == dd.NOT_CONFIRMED
    # Gipfel nah an der Desinfektionstemperatur ohne Kesselschalter: Desinfektion (ES73 ohne wwdisinfecting)
    m3 = DhwModel()
    _charge(m3, wed, 66.0, flagged=False)
    assert len(m3.disinfects) == 1
    again = DhwModel.from_dict(json.loads(json.dumps(m.to_dict())))
    assert again.disinfects == m.disinfects and DhwModel.from_dict({**m.to_dict(), "disinfects": None}).disinfects == []


def test_onetime_in_simulation_changes_nothing_for_the_date():
    _, _, base = _sim_plan(3, _cfg(), onetime_wed=True)
    cfg = plan_cfg("So", dtime(17, 30))
    m, _, log = _sim_plan(3, cfg, onetime_wed=True)
    assert same(log, base) and m.special_charges == 3 and m.disinfects == []
    assert not dd.plan_counts(m, cfg, MONDAY + timedelta(days=20, hours=17))


def test_engine_passes_disinfecting_flag_not_to_database(tmp_path):
    eng = Engine(Config(), Store(tmp_path / "t.db"), Discovered())
    eng.ems = {"boiler": {"wwdisinfecting": "on", "wwonetime": "off"}}
    sample = eng.snapshot()[0]
    assert sample["dhw_disinfecting"] == 1 and sample["dhw_special"] == 1 and "dhw_disinfecting" not in SAMPLE_COLS
    eng.ems = {"boiler": {"wwonetime": 1}}
    assert eng.snapshot()[0]["dhw_disinfecting"] is None
    # Einmalladung über dhw_step (Sonderladung ohne Desinfektion, 60 °C): bestätigt nichts
    t0 = int(datetime(2026, 1, 14, 19, 0).timestamp())
    t = 50.0
    for i in range(30):
        charging = t < 60.0 and i < 15
        t = min(t + 2.0, 60.0) if charging else t - 0.05
        eng.dhw_step(t0 + i * 300, {"dhw_temp": round(t, 1), "dhw": int(charging), "dhw_special": int(charging),
                                    "dhw_disinfecting": 0, "burner_pow": 60 if charging else 0, "dhw_set": 55.0,
                                    "home_count": 1})
    assert eng.dhw.special_charges == 1 and eng.dhw.disinfects == []


# ------------------------------------------------------------------ 4. Deckung erst nach der Ladedauer
def test_date_at_draw_hour_does_not_cover_it():
    m, now = bath_model()
    m.charges, m.charge_rate_kh = 6, 40.0  # gelernt: 20 K in 30 min
    cfg = plan_cfg("So", dtime(18, 0))
    sun_1730 = now.replace(hour=17, minute=30) + timedelta(days=7)
    confirm(m, sun_1730.replace(minute=0, hour=18))
    assert dd.ready_hours(m, cfg) == pytest.approx(0.5)
    ready = dd.planned_for(m, cfg, sun_1730, 2.0)
    assert ready == [sun_1730.replace(hour=18, minute=30)]
    assert not dd.covers(ready, sun_1730.replace(hour=18, minute=0))  # Termin = Zapfstunde: deckt nicht
    assert dd.covers(ready, sun_1730.replace(hour=19, minute=0))
    # Schatten: Wärme erst nach der Ladedauer, nicht schon zum Beginn
    t18 = sun_1730.replace(hour=18, minute=0)
    assert dd.shadow_mirror(m, cfg, t18 - timedelta(minutes=5), t18, 70.0) is None
    assert dd.shadow_mirror(m, cfg, t18 + timedelta(minutes=25), t18 + timedelta(minutes=30), 70.0) == 70.0
    # nie kürzer als 15 min, auch wenn nichts zu laden ist
    assert dd.ready_hours(m, cfg, temp_typ=75.0, temp_dis=70.0) == pytest.approx(0.25)


def test_accepted_suggestion_covers_its_draw():
    """Wer den Vorschlag übernimmt (und der Regler desinfiziert dann dort), dessen Wanne ist gedeckt – Vorschlag und
    Deckung rechnen mit derselben Ladedauer."""
    for charges, kw in ((0, 18.0), (0, None), (6, 18.0)):
        m, now = bath_model(charges=charges, rate=80 / 3)
        s = dd.suggestion(m, kw, 50.0, 70.0, now)
        cfg = plan_cfg(s["day"], dtime(*map(int, s["time"].split(":"))))
        start = datetime.combine((now + timedelta(days=7)).date(), cfg.dhw_disinfect_time)
        confirm(m, start)
        ready = dd.planned_for(m, cfg, start - timedelta(hours=1), 3.0, dd.ready_hours(m, cfg, kw, 50.0, 70.0))
        assert dd.covers(ready, start.replace(hour=s["draw_hour"], minute=0)), (charges, kw, s["time"])


# ------------------------------------------------------------------ 6. erste Woche: kein doppeltes Aufheizen
def test_first_week_no_double_heat_in_shadow():
    """Frisches Modell, Termin Sa 17:30, der Regler desinfiziert So 13:00: Der Schatten bekommt nur die Wärme des
    Reglers – nicht zusätzlich die eines Termins, zu dem niemand desinfiziert."""
    cfg = plan_cfg("Sa", dtime(17, 30))
    m, _, log = _sim_plan(2, cfg, es73_hour=13)
    assert log["mirrors"] == [] and len(m.disinfects) == 2


# ------------------------------------------------------------------ 7. leere Uhrzeit bei „aus“
COLLECT_STUBS = """
globalThis.document = { querySelectorAll: () => [], querySelector: () => null };
"""


def test_empty_time_with_day_off_keeps_stored_time(tmp_path):
    names = ["collect", "managedOf", "EMAP", "emCollect"]  # 0.39.1: collect gibt emCollect(out) zurück
    body = COLLECT_STUBS + """
      const cases = [["", ""], ["", "07:30"], ["So", ""], ["So", "17:30"]].map(([d, t]) => {
        $("f-dhw_disinfect_day").value = d; $("f-dhw_disinfect_time").value = t;
        const o = collect(); return [o.dhw_disinfect_day, "dhw_disinfect_time" in o ? o.dhw_disinfect_time : null];
      });
      out(cases);"""
    r = run_js_with(tmp_path, names, body)
    # „aus“ + leer: Uhrzeit nicht gesendet (gespeicherte bleibt); mit Tag und leer: gesendet, die Prüfung meldet es
    assert r == [["", None], ["", "07:30"], ["So", ""], ["So", "17:30"]]
    save_settings(validate({"dhw_disinfect_day": "So", "dhw_disinfect_time": "07:30"})[0], tmp_path)
    clean, errors = validate({"dhw_disinfect_day": ""})
    assert errors == []
    save_settings(clean, tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.dhw_disinfect_day == "" and cfg.dhw_disinfect_time == dtime(7, 30)
    errors = validate({"dhw_disinfect_day": "So", "dhw_disinfect_time": ""})[1]
    assert errors == ["dhw_disinfect_time: Uhrzeit als HH:MM"]  # mit Tag: die Oberfläche zeigt den Fehler, Feld offen


def run_js_with(tmp_path, extra: list[str], body: str):
    """Wie test_ui_v363.run_js, mit weiteren Funktionen aus index.html."""
    from test_ui_v361 import source
    js = script()
    return run_js(tmp_path, "\n".join(source(js, n) for n in extra) + "\n" + body)


# ------------------------------------------------------------------ 8. Sperre überschreibt „macht mit“ nicht
def test_locked_row_keeps_saved_managed_of_valve_in_same_room(tmp_path):
    ent = [{"id": "climate.thermostat_hc1", "ems": True}, {"id": "climate.wz", "ems": False}]
    saved = [{"climate": "climate.wz", "group": "Wohnzimmer", "managed": True},
             {"climate": "climate.thermostat_hc1", "group": "Wohnzimmer", "managed": True}]
    body = f"""ENT = {json.dumps(ent)}; SET = {{ rooms: {json.dumps(saved)} }};
      const tr = (disabled, checked) => ({{ querySelector: () => ({{ disabled, checked }}) }});
      out([managedOf(tr(true, false), "climate.wz"), managedOf(tr(true, false), "climate.thermostat_hc1"),
           managedOf(tr(true, true), "climate.thermostat_hc1"), managedOf(tr(false, false), "climate.wz"),
           managedOf(tr(false, true), "climate.neu"), managedOf(tr(true, false), "climate.neu")]);"""
    r = run_js_with(tmp_path, ["managedOf"], body)
    # gesperrter Mitläufer: gespeichertes true bleibt; Heizkreis nie; offene Häkchen wie angeklickt; neu gesperrt: false
    assert r == [True, False, False, False, True, False]


# ------------------------------------------------------------------ 9. Desinfektionstemperatur 60–80 °C
def test_boiler_temperature_only_in_setting_range(tmp_path):
    assert dd.TEMP_RANGE == tuple(float(v) for v in FIELDS["dhw_disinfect_temp"][1:]) == (60.0, 80.0)
    eng = Engine(Config.from_raw({"dhw_disinfect_day": "So"}), Store(tmp_path / "t.db"), Discovered())
    for v, want in ((50, (70.0, "eingestellt")), (59.5, (70.0, "eingestellt")), (60, (60.0, "laut Kessel")),
                    (80, (80.0, "laut Kessel")), (85, (70.0, "eingestellt")), (True, (70.0, "eingestellt")),
                    ("unavailable", (70.0, "eingestellt"))):
        eng.ems = {"boiler": {"wwdisinfectiontemp": v}}
        assert eng.disinfect_temp() == want, v
    eng.ems = {"boiler": {"wwdisinfectiontemp": 55}}
    info = eng.disinfect_info()
    assert info["temp_note"] == "Kessel meldet 55 °C – zu niedrig für eine Desinfektion, es gilt die Einstellung"
    assert dd.temp_note(59.5).startswith("Kessel meldet 59,5 °C – zu niedrig") and dd.temp_note(85).count("zu hoch")
    assert dd.temp_note(None) is None and dd.temp_note(True) is None and dd.temp_note(70) is None
    s = {"dhw_disinfect_day": "So", "dhw_disinfect_time": "13:00", "dhw_disinfect_temp": 70.0}
    t = plain(run_js(tmp_path, f"out(disinfectHtml({json.dumps(s)}, {json.dumps(info)}));"))
    assert ("Temperatur 70 °C eingestellt Kessel meldet 55 °C – zu niedrig für eine Desinfektion, es gilt die "
            "Einstellung") in t


# ------------------------------------------------------------------ 10. Boost aus 0.36.2 auf den Heizkreis
EMS = "climate.thermostat_hc1"


def test_stored_boost_on_ems_circuit_is_dropped_with_hint(tmp_path):
    store = Store(tmp_path / "c.db")
    store.put(KV, {EMS: {"temp": 25.0, "end": time.time() - 60, "local": True, "before": 20.0, "before_mode": "heat",
                         "targets": [EMS]},
                   "climate.wz": {"temp": 23.0, "end": time.time() + 600, "local": True, "before": 20.0,
                                  "before_mode": "heat", "targets": ["climate.wz"]}})
    ha = FakeHA()
    states = {EMS: {"state": "heat", "attributes": {"temperature": 25.0, "hvac_modes": ["heat", "off", "auto"]}}}
    rooms = [Room("Heizkreis", EMS, platform="mqtt", ems=True), Room("Wohnzimmer", "climate.wz", platform="tado")]
    ctl = Control(ha, store, rooms, states=states)
    assert EMS not in ctl.boosts and "climate.wz" in ctl.boosts and ctl.expired_boosts() == []
    assert EMS not in (store.get(KV) or {}) and EMS in store.get(KV_EMS_LEFT)
    want = ("Heizkreis: Boost 25 °C aus einer früheren Version steht noch am Regler – HeatPilot schreibt nicht an den "
            "Heizkreis, Regler-Soll von Hand auf 20 °C zurückstellen")
    assert ctl.notes() == [want]
    eng = SimpleEngine(store, rooms)
    assert want in ControlLoop(eng, ctl).warnings()
    assert asyncio.run(ctl.end_boost(EMS, None)) is False and ha.calls == []  # nichts geschrieben
    # nach einem Neustart steht der Hinweis noch da, bis das Soll von Hand zurück ist
    ctl2 = Control(ha, store, rooms, states=states)
    assert ctl2.notes() == [want]
    states[EMS]["attributes"]["temperature"] = 20.0
    assert ctl2.notes() == [] and not store.get(KV_EMS_LEFT)
    assert Control(ha, store, rooms, states=states).notes() == [] and ha.calls == []


class SimpleEngine:
    """Nur was ControlLoop für die Hinweise braucht."""

    def __init__(self, store, rooms):
        self.store, self.found = store, Discovered(rooms=rooms)


# ------------------------------------------------------------------ 11. Bereitschaft: Heizkreis einmal genannt
def test_readiness_names_ems_circuit_once(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz"), room("Heizkreis", EMS, managed=True, ems=True)])
    res = readiness.assess(eng, int(time.time()))
    assert [n for n in res["notes"] if "Heizkreis" in n] == [
        "Heizkreis: Heizkreis von EMS-ESP – HeatPilot schreibt nicht an den Heizkreis – macht nicht mit"]
    assert next(r for r in res["rooms"] if r["climate"] == EMS)["state"] == "nicht dabei"
    eng2, _, _ = house(tmp_path / "b", [room("Wz", "climate.wz"), room("Bad", "climate.bad", managed=False)])
    assert "Machen nicht mit: Bad" in readiness.assess(eng2, int(time.time()))["notes"]  # gewöhnliche wie bisher


# ------------------------------------------------------------------ 12. Lesbarkeit
def test_discovery_split_and_unused_helper_removed():
    assert len((PKG / "discovery.py").read_text(encoding="utf-8").splitlines()) <= 600
    src = (PKG / "discovery_roles.py").read_text(encoding="utf-8")
    assert src.startswith('"""EMS-ESP-Rollen') and "def " not in src  # nur Tabellen
    for name in ("BOILER_ROLES", "THERMOSTAT_ROLES", "ROLE_LABELS"):  # durchgereicht, dieselben Tabellen
        assert getattr(discovery, name) is getattr(discovery_roles, name)
    assert set(discovery_roles.ROLE_LABELS) == set(discovery_roles.BOILER_ROLES) | set(discovery_roles.THERMOSTAT_ROLES)
    assert not hasattr(Config, "prev_disinfection")
    for f in ("dhw.py", "dhw_disinfect.py", "control.py", "engine_dhw.py", "config.py"):
        assert len((PKG / f).read_text(encoding="utf-8").splitlines()) <= 600, f
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    assert ("Die Übersicht (Warmwasser) zeigt die nächste Desinfektion als Wert („So 17:30“, Notiz "
            "„eingestellt“,") in docs
    assert "So 17:30 (eingestellt)" not in docs  # so steht es nicht in der Kachel
