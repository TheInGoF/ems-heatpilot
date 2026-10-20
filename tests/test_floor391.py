"""0.39.1: Fußbodenheizung je Raum – Anschluss (Mischer, RTL-Ventil, direkt am Kessel) und höchster Vorlauf.

Ingo: „Wir bauen eine Auswahl, wie die Fußbodenheizung bestückt ist, und eine Temperaturgrenze, die der Nutzer
festlegt.“ Geprüft: die Grenze aus ``floor_limit`` (Mischer nie, RTL seit 0.46.5 immer – die Anforderung aus
Temperatur, Ventil, ``hvac_action``, bei Gruppen ein Gerät, nur noch im Grund –, direkt immer, auch ohne Messwert,
unbekannt wie direkt, Minimum mehrerer Räume), die Vorschau (Vorlauf und Vorheizen höchstens die Grenze, Grund in der Stunde, Grenze unter 35 °C), die
Einstellungen (Prüfung, Speichern und Laden, alte Einstellungen nur mit ``floor_heating_rooms``), „Bereit für die
Steuerung?“ (Grenze und „Anschluss wählen“), dass die Ventil-Regeln den Vorlauf nicht anheben und dass nichts
geschrieben wird. Alle Häuser und Werte sind erfunden.
"""

from pathlib import Path

import pytest

from heatpilot import __version__
from heatpilot import floor_limit as fl
from heatpilot import preview as pv
from heatpilot import readiness
from heatpilot import valve_dry as vd
from heatpilot.config import Config, save_settings, validate
from heatpilot.discovery import Room
from heatpilot.models import RoomModel

from test_control34 import MON, climate, make, slow_room, ts_of, wz

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"


def bad(**kw):
    return Room("Bad", "climate.bad", comfort=22.0, platform="tado", heating_power="sensor.bad_heizleistung", **kw)


def cfg_with(kind: str | None, max_flow=None, legacy=False, **kw) -> Config:
    """Einstellungen mit Wz (Heizkörper) und Bad (Fußboden ``kind``, ``max_flow``); ``legacy``: nur in
    floor_heating_rooms (Einstellungen vor 0.39.1)."""
    entry = {"climate": "climate.bad", "name": "Bad"}
    if kind:
        entry["floor_type"] = kind
    if max_flow is not None:
        entry["floor_max_flow"] = max_flow
    raw = {"control_mode": "heatpilot", "rooms": [{"climate": "climate.wz", "name": "Wz", "managed": True}, entry],
           "floor_heating_rooms": ["climate.bad"] if legacy or kind else [], **kw}
    return Config.from_raw(raw)


# ------------------------------------------------------------------ Grenze: reine Funktionen
def floors(kind, max_flow=None, legacy=False, rooms=None):
    return fl.floor_rooms(cfg_with(kind, max_flow, legacy), rooms or [wz(), bad()])


def reading(temp=22.0, target=22.0, valve=0.0, action="idle"):
    return {"temp": temp, "target": target, "valve": valve, "action": action}


def test_mixer_never_limits():
    f = floors("mischer", 40)
    assert [(r.name, r.kind) for r in f] == [("Bad", "mischer")]
    assert fl.flow_cap(f, {"climate.bad": reading(18.0, 22.0, 80.0, "heating")}) == (None, None)
    assert fl.flow_cap(f, {}) == (None, None)  # auch ohne Messwert
    assert fl.why(f[0], True) == "Fußboden Bad (Mischer): keine Grenze"


@pytest.mark.parametrize("r,expected", [
    (reading(21.95, 22.0), False),               # 0,05 K unter Soll: noch keine Anforderung
    (reading(21.85, 22.0), True),                # mehr als 0,1 K darunter
    (reading(22.0, 22.0, valve=6.0), True),      # Ventil über 5 %
    (reading(22.0, 22.0, valve=5.0), False),     # genau 5 %: nicht
    (reading(22.0, 22.0, action="heating"), True),
    (reading(22.0, None, valve=None, action="off"), False),  # ausgeschaltet
    (None, None),                                # kein Zustand: offen
    (reading(None, None, None, None), None),     # nichts bekannt
])
def test_rtl_limits_while_room_demands(r, expected):
    f = floors("rtl", 55)
    assert fl.demand(f[0], {"climate.bad": r}) is expected
    cap, why = fl.flow_cap(f, {"climate.bad": r})
    # seit 0.46.5 gilt die Grenze beim RTL-Ventil immer (es öffnet, sobald der Rücklauf kalt ist) – die Anforderung
    # steht nur noch im Grund
    assert cap == 55.0
    assert why == {True: "Fußboden Bad (RTL-Ventil, fordert an): höchstens 55 °C",
                   False: "Fußboden Bad (RTL-Ventil, fordert nicht an): höchstens 55 °C",
                   None: "Fußboden Bad (RTL-Ventil, Anforderung unbekannt): höchstens 55 °C"}[expected]


def test_rtl_group_one_device_is_enough():
    group = Room("Bad", "climate.bad", members=[{"climate": "climate.bad2", "heating_power": None}])
    cfg = Config.from_raw({"rooms": [{"climate": "climate.bad", "floor_type": "rtl", "floor_max_flow": 50},
                                     {"climate": "climate.bad2"}]})
    f = fl.floor_rooms(cfg, [group])
    assert f[0].devices == (("climate.bad", None), ("climate.bad2", None))
    quiet = reading(22.0, 22.0)
    assert fl.demand(f[0], {"climate.bad": quiet, "climate.bad2": quiet}) is False
    # das Mittel der Ventile (3 %) läge unter 5 % – ein Gerät mit 6 % genügt
    assert fl.demand(f[0], {"climate.bad": quiet, "climate.bad2": reading(22.0, 22.0, valve=6.0)}) is True
    assert fl.demand(f[0], {"climate.bad": quiet, "climate.bad2": reading(21.5, 22.0)}) is True
    assert fl.demand(f[0], {"climate.bad": quiet, "climate.bad2": None}) is None  # ein Gerät ohne Zustand: offen
    # Fußboden-Angabe nur am Mitläufer: zählt auch (die strengere Angabe gilt)
    cfg = Config.from_raw({"rooms": [{"climate": "climate.bad", "floor_type": "mischer"},
                                     {"climate": "climate.bad2", "floor_type": "direkt", "floor_max_flow": 40}]})
    assert [(r.kind, r.max_flow) for r in fl.floor_rooms(cfg, [group])] == [("direkt", 40.0)]


def test_direct_always_also_without_reading_or_model():
    f = floors("direkt", 48)
    for r in ({}, {"climate.bad": reading(23.0, 22.0)}, {"climate.bad": None}):
        assert fl.flow_cap(f, r) == (48.0, "Fußboden Bad (direkt am Kessel): höchstens 48 °C")
    assert fl.cap_of(f, {"climate.bad": False})[0] == 48.0  # unabhängig von der Anforderung
    assert floors("direkt")[0].max_flow == fl.DEFAULT_MAX_FLOW == 45.0  # ohne Angabe vorsichtig


def test_unknown_connection_like_direct():
    f = floors(None, legacy=True)  # Einstellungen vor 0.39.1: nur floor_heating_rooms
    assert [(r.kind, r.max_flow) for r in f] == [("unbekannt", 45.0)]
    assert fl.flow_cap(f, {"climate.bad": reading(23.0, 22.0)}) == (
        45.0, "Fußboden Bad (Anschluss unbekannt – wie direkt am Kessel): höchstens 45 °C")
    assert floors(None) == []  # kein Fußboden


def test_minimum_of_several_rooms():
    kind = Room("Kind", "climate.kind")
    flur = Room("Flur", "climate.flur")
    cfg = Config.from_raw({"rooms": [{"climate": "climate.bad", "floor_type": "rtl", "floor_max_flow": 40},
                                     {"climate": "climate.kind", "floor_type": "direkt", "floor_max_flow": 50},
                                     {"climate": "climate.flur", "floor_type": "mischer"}]})
    f = fl.floor_rooms(cfg, [wz(), bad(), kind, flur])
    assert [r.name for r in f] == ["Bad", "Kind", "Flur"]
    assert fl.cap_of(f, {"climate.bad": True})[0] == 40.0
    # seit 0.46.5 gilt RTL auch ohne Anforderung – das Minimum bleibt Bad; ohne Bad gilt Kind
    assert fl.cap_of(f, {"climate.bad": False}) == (40.0, "Fußboden Bad (RTL-Ventil, fordert nicht an): höchstens 40 °C")
    assert fl.cap_of(f[1:], {}) == (50.0, "Fußboden Kind (direkt am Kessel): höchstens 50 °C")
    d = fl.describe(f)
    assert d["text"] == ("Fußboden-Grenze für den Vorlauf: Bad höchstens 40 °C, immer (RTL-Ventil); "
                         "Kind höchstens 50 °C, immer (direkt am Kessel); Flur ohne Grenze (Mischer).")
    assert fl.describe([]) is None


def test_read_from_home_assistant_states():
    f = floors("rtl", 55)
    states = {"climate.bad": {"state": "heat", "attributes": {"current_temperature": 21.0, "temperature": 22.0,
                                                              "hvac_action": "idle"}},
              "sensor.bad_heizleistung": {"state": "12"}}
    assert fl.read(f, states) == {"climate.bad": {"temp": 21.0, "target": 22.0, "valve": 12.0, "action": "idle"}}
    states["climate.bad"]["state"] = "off"
    assert fl.read(f, states)["climate.bad"]["target"] is None
    states["climate.bad"]["state"] = "unavailable"
    assert fl.read(f, states) == {"climate.bad": None}


# ------------------------------------------------------------------ Einstellungen
def test_validate_floor_fields():
    ok, errors = validate({"rooms": [{"climate": "climate.bad", "floor_type": "rtl", "floor_max_flow": "52,4"},
                                     {"climate": "climate.wz", "floor_type": "", "floor_max_flow": ""}],
                           "floor_heating_rooms": []})
    assert errors == []
    assert ok["rooms"][0] == {"name": "", "climate": "climate.bad", "floor_type": "rtl", "floor_max_flow": 52.5}
    assert ok["rooms"][1] == {"name": "", "climate": "climate.wz"}  # keine Fußbodenheizung: nichts gespeichert
    assert ok["floor_heating_rooms"] == ["climate.bad"]  # Räume mit Anschluss stehen immer auch dort
    for bad_value, word in (({"floor_type": "fbh"}, "Anschluss"), ({"floor_max_flow": "heiß"}, "keine Zahl"),
                            ({"floor_max_flow": 80}, "zwischen 25 und 70"), ({"floor_max_flow": 20}, "zwischen 25")):
        _, errors = validate({"rooms": [{"climate": "climate.bad", "floor_type": "direkt", **bad_value}]})
        assert errors and word in errors[0] and "climate.bad" in errors[0], (bad_value, errors)


def test_settings_roundtrip_and_migration(tmp_path):
    (tmp_path / "options.json").write_text("{}")
    # alte Einstellungen (vor 0.39.1): Fußboden nur als Liste
    save_settings({"rooms": [{"name": "Bad", "climate": "climate.bad"}, {"name": "Wz", "climate": "climate.wz"}],
                   "floor_heating_rooms": ["climate.bad"], "overrides": ["flow_temp=sensor.vorlauf"]}, tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.floor_heating_rooms == ["climate.bad"] and cfg.overrides == {"flow_temp": "sensor.vorlauf"}
    assert [(r.name, r.kind) for r in fl.floor_rooms(cfg, [wz(), bad()])] == [("Bad", "unbekannt")]
    raw = cfg.to_raw()
    assert validate(raw)[1] == []  # alte Einstellungen gehen unverändert durch die Prüfung
    # aus der Oberfläche: Anschluss gewählt, Grenze eingetragen – beides konsistent gespeichert
    raw["rooms"][0].update(floor_type="rtl", floor_max_flow="55")
    clean, errors = validate(raw)
    assert errors == []
    save_settings(clean, tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.rooms[0]["floor_type"] == "rtl" and cfg.rooms[0]["floor_max_flow"] == 55.0
    assert cfg.floor_heating_rooms == ["climate.bad"]
    assert [(r.kind, r.max_flow) for r in fl.floor_rooms(cfg, [wz(), bad()])] == [("rtl", 55.0)]
    assert cfg.to_raw()["rooms"] == clean["rooms"]
    # Anschluss gesetzt, aber nicht in der Liste (von Hand bearbeitet): Liste wird ergänzt, Raummodell bleibt Fußboden
    cfg = Config.from_raw({"rooms": [{"climate": "climate.bad", "floor_type": "direkt"}]})
    assert cfg.floor_heating_rooms == ["climate.bad"]
    # unlesbare Angaben in der Datei: kein Absturz – Anschluss unbekannt (sicher), Grenze Standard
    cfg = Config.from_raw({"rooms": [{"climate": "climate.bad", "floor_type": "fbh", "floor_max_flow": "x"}]})
    assert cfg.rooms[0] == {"climate": "climate.bad"} and cfg.floor_heating_rooms == ["climate.bad"]
    assert [(r.kind, r.max_flow) for r in fl.floor_rooms(cfg, [bad()])] == [("unbekannt", 45.0)]
    assert validate(cfg.to_raw())[1] == []


# ------------------------------------------------------------------ Vorschau
def preview_house(tmp_path, cfg, bad_temp=22.0, wz_model=None):
    """Wz (Heizkörper, macht mit) braucht viel Vorlauf (≈ 61 °C bei 5 °C außen); Bad (Fußboden, macht nicht mit,
    ohne plausibles Modell) mit ``bad_temp`` bei Soll 22 °C."""
    eng, ha, _, _ = make(tmp_path, cfg=cfg, rooms=[wz(), bad()], outdoor=["sensor.out"])
    eng.states["sensor.out"] = {"state": "5.0", "attributes": {}}
    climate(eng, "climate.wz", 21.0, 21.0)
    climate(eng, "climate.bad", bad_temp, 22.0)
    eng.rooms["climate.wz"] = wz_model or slow_room(a=0.004)
    eng.rooms["climate.bad"] = RoomModel("Bad", floor=True)  # lernt noch
    eng.house.rls.updates = 500
    eng.house.rls.theta = [0.2, 2.0, 0.3]
    return eng, ha


def by_hour(p):
    out = {}
    for h in p["hours"]:
        out.setdefault(h["t"][11:16], h)
    return out


def test_preview_rtl_limits_only_while_room_needs_heat(tmp_path):
    # seit 0.46.5 gilt die RTL-Grenze immer – auch wenn das Bad am Soll ist und nicht anfordert (der Grund sagt es)
    free = by_hour(pv.preview(preview_house(tmp_path / "a", cfg_with("rtl", 50), bad_temp=22.0)[0], ts_of(MON, 12)))
    assert free["14:00"]["flow"] == 50 and free["14:00"]["flow_limited"] is True and free["14:00"]["flow_cap"] == 50.0
    assert free["14:00"]["flow_reason"] == ("Wz hält 21,0 °C – begrenzt durch Fußboden Bad (RTL-Ventil, fordert nicht "
                                            "an): höchstens 50 °C")
    eng, ha = preview_house(tmp_path / "b", cfg_with("rtl", 50), bad_temp=20.0)
    p = pv.preview(eng, ts_of(MON, 12))
    h = by_hour(p)
    assert h["14:00"]["flow"] == 50 and h["14:00"]["flow_limited"] is True and h["14:00"]["flow_cap"] == 50.0
    assert h["14:00"]["flow_reason"] == ("Wz hält 21,0 °C – begrenzt durch Fußboden Bad (RTL-Ventil, fordert an): "
                                         "höchstens 50 °C")
    # nachts will das Bad 18 °C, es ist 20 °C warm: fordert nicht an – seit 0.46.5 gilt die Grenze trotzdem, auch beim
    # Vorheizen (vorher dort 75 °C)
    assert h["23:00"]["flow_cap"] == 50.0 and h["03:00"]["flow_cap"] == 50.0
    rows = pv._Run(eng, ts_of(MON, 12)).run()
    assert all(r["cap"] == 50.0 for r in rows)
    assert any(r["heating"] for r in rows) and all(r["flow"] <= 50 for r in rows if r["heating"])
    assert all(w["value"] <= 50 for x in p["hours"] for w in x["writes"] if w["what"] == "flow")
    assert p["floor"]["text"] == "Fußboden-Grenze für den Vorlauf: Bad höchstens 50 °C, immer (RTL-Ventil)."
    assert ha.calls == []  # nichts geschrieben


def test_preview_direct_and_unknown_limit_always_mixer_never(tmp_path):
    plain = pv.preview(preview_house(tmp_path / "ohne", cfg_with(None), bad_temp=23.0)[0], ts_of(MON, 12))
    assert max(s["flow"] for s in plain["steps"] if s["heating"]) == 75  # Vorheizen ohne Grenze: maximaler Vorlauf
    for kind, legacy, cap in (("direkt", False, 48), (None, True, 45), ("mischer", False, None)):
        eng, _ = preview_house(tmp_path / str(kind), cfg_with(kind, 48 if kind else None, legacy), bad_temp=23.0)
        p = pv.preview(eng, ts_of(MON, 12))
        flows = [s["flow"] for s in p["steps"] if s["heating"]]
        assert flows, kind
        if cap is None:  # Mischer: wie ohne Fußboden-Grenze
            assert p["steps"] == plain["steps"] and not any(h["flow_limited"] or h["flow_cap"] for h in p["hours"])
        else:
            assert max(flows) == cap and all(h["flow_cap"] == cap for h in p["hours"])
            assert "begrenzt durch Fußboden Bad" in by_hour(p)["14:00"]["flow_reason"]


def test_preview_preheat_flow_limited(tmp_path):
    # Wz heizt vor dem Nachtende vor – ohne Grenze mit dem maximalen Vorlauf (reicht sonst nicht rechtzeitig)
    plain = pv._Run(preview_house(tmp_path / "a", cfg_with(None), bad_temp=23.0)[0], ts_of(MON, 12))
    pre = [r for r in plain.run() if "heizt vor" in r["why"]]
    assert pre and max(r["flow"] for r in pre) == 75
    # direkt am Kessel mit 44 °C: höchstens die Grenze, und die Stunde sagt es
    eng, _ = preview_house(tmp_path / "b", cfg_with("direkt", 44), bad_temp=23.0)
    p = pv.preview(eng, ts_of(MON, 12))
    rows = pv._Run(eng, ts_of(MON, 12)).run()
    pre = [r for r in rows if "heizt vor" in r["why"]]
    assert pre and all(r["flow"] <= 44 for r in pre) and any(r["limited"] for r in pre)
    assert all(r["why"].endswith("– begrenzt durch Fußboden Bad (direkt am Kessel): höchstens 44 °C")
               for r in pre if r["flow"] == 44)
    hour = next(h for h in p["hours"] if "heizt vor" in h["flow_reason"])
    assert hour["flow_limited"] is True and hour["flow"] == 44 and "begrenzt durch Fußboden Bad" in hour["flow_reason"]


def test_preview_cap_below_35_applies_when_a_room_needs_heat(tmp_path):
    # Wz braucht 29 °C (unter 35 °C: ohne Grenze „Heizbetrieb aus“); Fußboden direkt mit 30 °C: dann gilt die Grenze
    plain = by_hour(pv.preview(preview_house(tmp_path / "a", cfg_with(None), wz_model=slow_room())[0], ts_of(MON, 12)))
    assert plain["14:00"]["heating"] is False and plain["14:00"]["flow_reason"].startswith("nötiger Vorlauf unter 35 °C")
    eng, _ = preview_house(tmp_path / "b", cfg_with("direkt", 30), bad_temp=23.0, wz_model=slow_room())
    h = by_hour(pv.preview(eng, ts_of(MON, 12)))["14:00"]
    assert (h["heating"], h["flow"], h["flow_limited"]) == (True, 30, True)
    assert h["flow_reason"] == "Wz hält 21,0 °C – begrenzt durch Fußboden Bad (direkt am Kessel): höchstens 30 °C"


def test_capped_rule_directly():
    needs = [(61.0, "Wz", "hält 21,0 °C")]
    assert pv._Run._capped(needs, (61, True, "Wz hält"), 55.5, "Fußboden X") == (55, True, "Wz hält – begrenzt durch Fußboden X", True)
    assert pv._Run._capped(needs, (55, True, "Wz hält"), 55.0, "x") == (55, True, "Wz hält", False)  # nicht darüber
    assert pv._Run._capped(needs, (61, True, "w"), None, None) == (61, True, "w", False)
    assert pv._Run._capped([], (None, False, "kein Raum braucht Wärme"), 30.0, "x")[:2] == (None, False)
    assert pv._Run._capped(needs, (None, None, pv.NO_MODELS), 30.0, "x")[:2] == (None, None)


# ------------------------------------------------------------------ Bereit für die Steuerung?
def test_readiness_shows_cap_and_asks_for_connection(tmp_path):
    eng, ha, _, _ = make(tmp_path, cfg=cfg_with(None, legacy=True), rooms=[wz(), bad()], outdoor=["sensor.out"])
    eng.states["sensor.out"] = {"state": "5", "attributes": {}}
    climate(eng, "climate.wz", 20.0, 21.0)
    climate(eng, "climate.bad", 22.0, 22.0)
    res = readiness.assess(eng, ts_of(MON, 12))
    assert res["floor"]["cap"] == 45.0 and res["floor"]["unknown"] == ["Bad"]
    assert "Vorlauf-Grenze der Fußbodenheizung jetzt: Fußboden Bad (Anschluss unbekannt – wie direkt am Kessel): " \
           "höchstens 45 °C" in res["notes"]
    assert ("Fußboden Bad: Anschluss wählen (Einstellungen → Räume: Mischer, RTL-Ventil oder direkt am Kessel) – bis "
            "dahin gilt höchstens 45 °C immer, wie direkt am Kessel") in res["notes"]
    # RTL, Bad am Soll und Ventil zu: fordert nicht an – seit 0.46.5 gilt die Grenze trotzdem; kein „Anschluss wählen“
    eng.cfg = cfg_with("rtl", 55)
    eng.states["sensor.bad_heizleistung"] = {"state": "0"}
    res = readiness.assess(eng, ts_of(MON, 12))
    assert res["floor"]["cap"] == 55.0 and res["floor"]["rooms"][0]["demand"] is False
    assert res["floor"]["rooms"][0]["applies"] is True
    assert "Vorlauf-Grenze der Fußbodenheizung jetzt: Fußboden Bad (RTL-Ventil, fordert nicht an): höchstens 55 °C" \
           in res["notes"]
    assert not any("Anschluss wählen" in n for n in res["notes"])
    eng.states["sensor.bad_heizleistung"] = {"state": "30"}  # Ventil offen: fordert an
    res = readiness.assess(eng, ts_of(MON, 12))["floor"]
    assert res["cap"] == 55.0 and res["why"] == "Fußboden Bad (RTL-Ventil, fordert an): höchstens 55 °C"
    # ohne Fußboden: nichts
    eng.cfg = cfg_with(None)
    res = readiness.assess(eng, ts_of(MON, 12))
    assert res["floor"]["rooms"] == [] and not any("Fußboden" in n for n in res["notes"])
    assert ha.calls == []


# ------------------------------------------------------------------ nichts Neues geschrieben
def test_valve_rules_never_raise_the_flow():
    """Trockenlauf der Ventile: verschiebt nur Raum-Sollwerte, kennt keinen Vorlauf – kein Vorschlag über der Grenze."""
    entry = vd._entry(0, vd._Room("climate.bad", "Bad", True, 20.0, 22.0, 40.0, 0.5, None, None, False),
                      {"rule": "gemeinsam", "sp": 22.5}, 22.0, "anheben")
    assert not any("flow" in k for k in entry)
    src = (PKG / "valve_dry.py").read_text(encoding="utf-8")
    assert "sel_flow" not in src and "selflowtemp" not in src
    assert "nie den Vorlauf" in src and "floor_limit" in src


def test_floor_limit_writes_nothing_and_is_readable():
    src = (PKG / "floor_limit.py").read_text(encoding="utf-8")
    assert src.startswith('"""Fußbodenheizung je Raum (seit 0.39.1)') and len(src.splitlines()) <= 600
    for word in ("call_service", "set_temperature", "post(", "write", "ha_client", "emsesp"):
        assert word not in src.split('"""', 2)[2], word
    for name in ("preview.py", "readiness.py", "config.py", "valve_dry.py"):
        assert len((PKG / name).read_text(encoding="utf-8").splitlines()) <= 600, name


# ------------------------------------------------------------------ Version, Doku
def test_version_changelog_docs():
    assert __version__ >= "0.39.1"  # seit 0.40 prüft test_valve_control40 die genaue Version
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = log.split("\n## 0.39.1")[1].split("\n## ")[0]
    assert "Fußbodenheizung je Raum" in head and "nichts Neues geschrieben" in head
    assert "55 °C" not in head  # keine persönlichen Werte
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    assert "## Fußbodenheizung je Raum" in docs and "## Fußbodenheizung ohne Stellventil" not in docs
    for text in ("**mit Mischer**", "**mit RTL-Ventil**", "**direkt am Kessel**", "„Anschluss wählen“",
                 "begrenzt durch"):
        assert text in docs, text
    plan = (ROOT / "docs" / "planung-vorlauf-ems.md").read_text(encoding="utf-8")
    assert "harte Grenze vor jedem Schreiben" in plan and "floor_limit.flow_cap" in plan
