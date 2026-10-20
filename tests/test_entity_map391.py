"""0.39.1 Teil B: Seite „Entitäten“ – alle Rollen in Gruppen mit Zweck, nötig/optional, Filter und dem, was gilt.

Ingo: „Eine vernünftige Übersicht, wo man alle Entitäten einstellen kann … geordnet die von HeatPilot benötigten Entitäten
sehen, was er braucht, und ich dann anhand des Home Assistant die Entitäten zuweisen könnte, wenn er die nicht schon selbst
gefunden hat.“ Geprüft: jede Rolle aus ``discovery_roles`` genau einmal, die Quelle (gewählt, automatisch, EMS-ESP direkt,
fehlt), was „nötig“ ist (im Code geprüft, mit Ersatz), Filter je Rolle, gespeichert nur über die bestehenden Einstellungen
(Zuordnungen hin und zurück, alte ``gas=``/``weather=``-Zuordnungen), Raum-Entitäten, Benachrichtigungsziele, Personen
ohne Koordinaten und dass ``GET api/entity_map`` nur liest. Alle Häuser und Entitäten sind erfunden.
"""

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from heatpilot import entity_map as em
from heatpilot.config import FIELDS, Config, save_settings, validate
from heatpilot.discovery import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES, Registry, discover

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"
SERVICES = {"notify": {"mobile_app_tablet": {}, "send_message": {}}}


def st(state, unit=None, dc=None, name=None, **attrs):
    a = {**({"friendly_name": name} if name else {}), **attrs}
    if unit:
        a["unit_of_measurement"] = unit
    if dc:
        a["device_class"] = dc
    return {"state": state, "attributes": a}


def base_states() -> dict:
    """Kessel über EMS-ESP (Kurznamen), ohne Brennerleistung und Ladesignal; Tado-Raum, Wetter, Gas, Personen, Ziele."""
    b = "sensor.boiler_"
    return {
        b + "curflowtemp": st("46.5", "°C", "temperature", "Vorlauf"),
        b + "rettemp": st("unavailable", "°C", "temperature", "Rücklauf"),
        "binary_sensor.boiler_burngas": st("on", name="Gas"),
        b + "dhw_curtemp": st("51.5", "°C", "temperature", "Speicher"),
        "number.boiler_dhw_seltemp": st("50", "°C", None, "Warmwasser-Soll", min=30, max=70),
        b + "outdoortemp": st("6.5", "°C", "temperature", "Außen am Kessel"),
        b + "nrgtotal": st("15873.2", "kWh", "energy", "Energie gesamt"),
        "select.thermostat_hc1_mode": st("day", name="Betriebsart"),
        "climate.wz": st("heat", name="Wohnzimmer Thermostat", current_temperature=20.5, temperature=21),
        "sensor.wz_feuchte": st("48", "%", "humidity", "Wohnzimmer Feuchte"),
        "sensor.wz_heizleistung": st("12", "%", None, "Wohnzimmer Heizleistung"),
        "weather.zuhause": st("cloudy", name="Zuhause"),
        "sensor.gaszaehler": st("8412.37", "m³", "gas", "Gaszähler", state_class="total_increasing"),
        "sensor.hausleistung": st("412", "W", "power", "Hausleistung"),
        "person.anna": st("home", name="Anna", user_id="u1", device_trackers=["device_tracker.anna"], latitude=1.0,
                          longitude=2.0, gps_accuracy=12),
        "person.auto": st("not_home", name="Auto", device_trackers=["device_tracker.auto"], latitude=3.0, longitude=4.0),
        "zone.home": st("1", latitude=5.0, longitude=6.0),
        "notify.handy_anna": st("unknown", name="Handy Anna"),
    }


def registry(states: dict) -> Registry:
    """Kessel im Keller, Thermostat mit Feuchte- und Heizleistungssensor am selben Gerät im Wohnzimmer."""
    dev = {"climate.wz": "dev_wz", "sensor.wz_feuchte": "dev_wz", "sensor.wz_heizleistung": "dev_wz"}
    ents = [{"entity_id": e, "platform": "tado" if e == "climate.wz" else "x",
             "device_id": dev.get(e) or ("dev_boiler" if "boiler_" in e or "thermostat_" in e else None)} for e in states]
    return Registry(ents, [{"id": "dev_wz", "name": "Thermostat", "area_id": "wz"}, {"id": "dev_boiler", "name": "Boiler",
                                                                                      "area_id": "keller"}],
                    [{"area_id": "wz", "name": "Wohnzimmer"}, {"area_id": "keller", "name": "Keller"}])


def build(raw=None, drop=(), extra=None, ems=None, services=SERVICES, cfg=None):
    states = {**base_states(), **(extra or {})}
    for e in drop:
        states.pop(e)
    cfg = cfg or Config.from_raw(raw or {})
    reg = registry(states)
    lst = [{"entity_id": k, **v} for k, v in states.items()]
    found = discover(lst, reg, cfg)
    auto = discover(lst, reg, Config.from_raw({}), all_rooms=True).as_dict(private=False)
    return em.build(cfg, found, auto, states, reg.lookup(), ems or {}, services)


def by_key(m) -> dict:
    return {e["key"]: e for g in m["groups"] for e in g["entries"]}


def group(m, key) -> dict:
    return next(g for g in m["groups"] if g["key"] == key)


# ------------------------------------------------------------------ Rollen und Gruppen
def test_every_role_once_in_its_group():
    m = build()
    keys = [e["key"] for g in m["groups"] for e in g["entries"]]
    assert len(keys) == len(set(keys))  # jede Rolle genau einmal
    kessel, regler = group(m, "kessel")["entries"], group(m, "regler")["entries"]
    assert sorted(e["key"] for e in kessel) == sorted(BOILER_ROLES) and len(kessel) == len(BOILER_ROLES)
    assert sorted(e["key"] for e in regler) == sorted(THERMOSTAT_ROLES) and len(regler) == len(THERMOSTAT_ROLES)
    assert set(em.ROLES) == set(BOILER_ROLES) | set(THERMOSTAT_ROLES)
    for e in kessel + regler:
        assert e["label"] == ROLE_LABELS[e["key"]] and e["purpose"] == em.ROLES[e["key"]][1]
        assert e["setting"] == {"kind": "override", "name": "overrides", "role": e["key"]}
        assert e["purpose"].endswith(".") and len(e["purpose"]) < 120  # ein Satz
    # nötige zuerst, sonst in der Reihenfolge der Rollen-Tabelle
    assert [e["key"] for e in kessel[:5]] == ["flow_temp", "burner_power", "burner_on", "dhw_active", "dhw_temp"]
    assert [e["key"] for e in kessel[5:]] == [r for r in BOILER_ROLES if r not in em.NEED]
    assert [g["key"] for g in m["groups"]] == ["kessel", "regler", "warmwasser", "kessel_hp", "aussen", "gas", "strom",
                                               "raeume", "personen", "benachrichtigungen"]  # kessel_hp seit 0.41
    assert [g["title"] for g in m["groups"]][:2] == ["Kessel (EMS-ESP)", "Regler (EMS-ESP)"]


def test_other_entries_are_saved_in_existing_settings_fields():
    """Keine zweite Speicherstelle: jedes Feld der Seite ist ein Entitätsfeld der Einstellungen, Raum-Sensoren am
    Raum-Eintrag, Kessel/Regler in ``overrides``."""
    m = build({"rooms": [{"climate": "climate.wz", "name": "Wz"}]})
    for e in by_key(m).values():
        s = e["setting"]
        if s["kind"] == "field":
            assert FIELDS[s["name"]][0] == ("entities" if e["multi"] else "entity"), s["name"]
        elif s["kind"] == "room":
            assert s["index"] == 0 and s["field"] in ("climate", "temperature", "humidity", "heating_power")
        else:
            assert s["kind"] == "override" and FIELDS["overrides"] == ("overrides",)
    assert set(em.FIELD_ENTRIES) == {"dhw_set_entity", "outdoor_sensors", "weather_entity", "gas_sensor", "demand_sensor",
                                     "power_sensors", "power_exclude", "persons", "vacation_entity", "notify_targets",
                                     "flow_set_entity", "heating_switch_entity",  # Weg B seit 0.41
                                     "disinfect_entity"}  # Desinfektion auslösen seit 0.42


# ------------------------------------------------------------------ Quelle
def test_source_auto_set_missing_and_ems_direct():
    e = by_key(build())
    assert (e["flow_temp"]["source"], e["flow_temp"]["current"]) == ("automatisch", "sensor.boiler_curflowtemp")
    assert e["flow_temp"]["state"] == "46.5" and e["flow_temp"]["unit"] == "°C" and e["flow_temp"]["area"] == "Keller"
    assert e["flow_temp"]["available"] is True and e["flow_temp"]["fits"] is True and e["flow_temp"]["issue"] is None
    assert e["burner_power"]["source"] == "fehlt" and e["burner_power"]["covered_by"] == "Brenner an (Gas)"
    assert e["burner_power"]["missing"] is False and e["burner_power"]["issue"] is None
    # gewählt: die Zuordnung gilt, auch vor dem Erkannten; „Automatik“ (leer) wieder die Erkennung
    e = by_key(build({"overrides": ["flow_temp=sensor.boiler_dhw_curtemp"]}))
    f = e["flow_temp"]
    assert (f["source"], f["current"], f["chosen"], f["detected"]) == ("gewählt", "sensor.boiler_dhw_curtemp",
                                                                       "sensor.boiler_dhw_curtemp", "sensor.boiler_curflowtemp")
    # EMS-ESP direkt: ohne Entität kein Fehlen
    e = by_key(build(drop=["binary_sensor.boiler_burngas"], ems={"boiler": {"curburnpow": 30, "rettemp": 38.5}}))
    assert (e["burner_power"]["source"], e["burner_power"]["ems_key"], e["burner_power"]["state"]) == ("EMS-ESP", "boiler/curburnpow", 30)
    assert e["burner_on"]["covered_by"] == "Brennerleistung (%)" and not e["burner_on"]["missing"]
    # Entität nicht verfügbar, aber EMS-ESP liefert den Wert: kein Problem
    assert e["return_temp"]["available"] is False and e["return_temp"]["ems_key"] == "boiler/rettemp"
    assert e["return_temp"]["issue"] is None


def test_unavailable_and_unknown_entities():
    e = by_key(build({"overrides": ["boiler_temp=sensor.gibt_es_nicht"]}))
    assert e["return_temp"]["issue"] == "nicht verfügbar" and e["return_temp"]["exists"] is True
    x = e["boiler_temp"]
    assert (x["source"], x["exists"], x["available"], x["issue"]) == ("gewählt", False, False, "nicht verfügbar")
    assert e["flame_current"]["issue"] == "nicht gefunden" and e["flame_current"]["problem"]
    assert e["demand_sensor"]["issue"] == "nicht gewählt" and not e["demand_sensor"]["problem"]  # ohne Erkennung


# ------------------------------------------------------------------ nötig (im Code geprüft)
def test_required_roles_and_their_replacements():
    m = build({"rooms": [{"climate": "climate.wz", "name": "Wz"}]})
    req = {k for k, e in by_key(m).items() if e["required"]}
    assert req == {"flow_temp", "burner_on", "burner_power", "dhw_active", "dhw_temp", "outdoor_sensors",
                   "rooms.climate.wz.climate"}
    assert not by_key(m)["return_temp"]["required"]  # Rücklauf: nur Vorlauf-Auswertung und Live-Anzeige
    assert m["counts"]["missing"] == 0
    # beide Brenner-Werte fehlen: beide nötig, ohne Ersatz
    e = by_key(build(drop=["binary_sensor.boiler_burngas"]))
    assert e["burner_on"]["missing"] and e["burner_power"]["missing"] and e["burner_on"]["issue"] == "fehlt"
    # Warmwasser: Ladesignal fehlt, Speichertemperatur ersetzt es; ohne beides fehlt beides
    e = by_key(build())
    assert e["dhw_active"]["covered_by"] == "Speichertemperatur" and not e["dhw_active"]["missing"]
    e = by_key(build(drop=["sensor.boiler_dhw_curtemp"]))
    assert e["dhw_active"]["missing"] and e["dhw_temp"]["missing"]
    # Vorlauf: kein Ersatz außer EMS-ESP direkt
    e = by_key(build(drop=["sensor.boiler_curflowtemp"]))
    assert e["flow_temp"]["missing"] and e["flow_temp"]["alt"] == []
    assert not by_key(build(drop=["sensor.boiler_curflowtemp"], ems={"boiler": {"curflowtemp": 45}}))["flow_temp"]["missing"]
    # Außentemperatur: Außenfühler am Kessel, sonst das Wetter, sonst fehlt
    e = by_key(build(drop=["sensor.boiler_outdoortemp"]))
    assert e["outdoor_sensors"]["source"] == "fehlt" and e["outdoor_sensors"]["covered_by"] == "Wetter"
    e = by_key(build(drop=["sensor.boiler_outdoortemp", "weather.zuhause"]))
    assert e["outdoor_sensors"]["missing"]


def test_dhw_entity_required_only_when_heatpilot_writes():
    assert not by_key(build())["dhw_set_entity"]["required"]
    e = by_key(build({"dhw_control": "heatpilot"}))["dhw_set_entity"]
    assert e["required"] and (e["source"], e["current"]) == ("automatisch", "number.boiler_dhw_seltemp")
    assert e["filter"]["picker"] == "dhw"  # Auswahl nur, was als Warmwasser-Soll taugt (dhw_entity)
    e = by_key(build({"dhw_control": "heatpilot"}, drop=["number.boiler_dhw_seltemp"]))["dhw_set_entity"]
    assert e["missing"] and e["issue"] == "fehlt"


# ------------------------------------------------------------------ Filter je Rolle
def test_filter_per_role():
    e = by_key(build({"rooms": [{"climate": "climate.wz", "name": "Wz"}]}))
    for key, entry in e.items():
        f = entry["filter"]
        assert set(f) >= {"domains", "units", "device_classes"} and f["domains"], key
    assert e["flow_temp"]["filter"] == {"domains": ["sensor", "number"], "units": ["°C"], "device_classes": ["temperature"]}
    assert e["burner_on"]["filter"]["domains"] == ["binary_sensor", "switch", "sensor"]
    assert e["gas_sensor"]["filter"] == {"domains": ["sensor"], "units": ["m³", "m3", "kWh"], "device_classes": ["gas", "energy"]}
    assert e["weather_entity"]["filter"]["domains"] == ["weather"] and e["persons"]["filter"]["domains"] == ["person"]
    assert e["vacation_entity"]["filter"]["domains"] == ["input_boolean", "switch", "binary_sensor"]
    assert e["rooms.climate.wz.climate"]["filter"]["domains"] == ["climate"]
    # alles, was die Erkennung findet, passt zu seiner Rolle
    for key, entry in e.items():
        assert entry["fits"] in (None, True), key
    f = em.filter_of("temp")
    assert em.fits("sensor.x", "°C", None, f) and em.fits("number.x", None, "temperature", f)
    assert not em.fits("sensor.x", "%", "humidity", f) and not em.fits("binary_sensor.x", "°C", None, f)
    assert em.fits("binary_sensor.x", None, None, em.filter_of("flag")) and not em.fits("sensor.x", "%", None, em.filter_of("flag"))
    assert em.fits("weather.x", None, None, em.filter_of("weather"))  # nur Domäne


# ------------------------------------------------------------------ Speichern über die Einstellungen
def test_overrides_roundtrip_through_settings(tmp_path):
    """Was die Seite schickt (``overrides`` rolle=entity, Felder) geht durch die echte Prüfung, wird gespeichert und
    gelesen – und zeigt sich danach als „gewählt“; leer geschickt gilt wieder die Erkennung."""
    body = {"overrides": ["flow_temp=sensor.boiler_dhw_curtemp", "rolle_alt=sensor.alt"], "gas_sensor": "sensor.gaszaehler",
            "outdoor_sensors": ["sensor.boiler_dhw_curtemp"], "weather_entity": "", "power_sensors": ["sensor.hausleistung"],
            "vacation_entity": "", "dhw_set_entity": "number.boiler_dhw_seltemp", "demand_sensor": ""}
    clean, errors = validate(body)
    assert errors == []
    save_settings(clean, tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    e = by_key(m := build(cfg=cfg))
    assert e["flow_temp"]["source"] == "gewählt" and e["gas_sensor"]["source"] == "gewählt"
    assert e["outdoor_sensors"]["chosen"] == ["sensor.boiler_dhw_curtemp"] and e["power_sensors"]["source"] == "gewählt"
    assert e["weather_entity"]["source"] == "automatisch" and e["dhw_set_entity"]["source"] == "gewählt"
    assert m["extra_overrides"] == [{"role": "rolle_alt", "entity": "sensor.alt"}]  # bleibt, bis man sie entfernt
    clean, errors = validate({"overrides": [], "gas_sensor": "", "outdoor_sensors": []})
    save_settings(clean, tmp_path)
    e = by_key(build(cfg=Config.load(tmp_path / "options.json")))
    assert e["flow_temp"]["source"] == "automatisch" and e["gas_sensor"]["source"] == "automatisch"
    assert e["outdoor_sensors"]["current"] == ["sensor.boiler_outdoortemp"]
    assert validate({"overrides": ["flow_temp="]})[1]  # leere Zuordnung schickt die Seite nie, die Prüfung lehnt sie ab


def test_old_settings_with_overrides_load(tmp_path):
    """Alte Zuordnungen ``gas=``/``weather=`` (frühere Add-on-Optionen) wandern in die Felder; Unlesbares stört nicht."""
    (tmp_path / "options.json").write_text(json.dumps({"overrides": ["gas=sensor.gaszaehler", "weather=weather.zuhause",
                                                                     "flow_temp=sensor.boiler_curflowtemp", "kaputt"],
                                                       "gas_sensor": "sensor.anderer"}))
    cfg = Config.load(tmp_path / "options.json")
    assert (cfg.gas_sensor, cfg.weather_entity) == ("sensor.gaszaehler", "weather.zuhause")  # die Zuordnung hatte Vorrang
    assert cfg.overrides == {"flow_temp": "sensor.boiler_curflowtemp"}
    assert cfg.to_raw()["overrides"] == ["flow_temp=sensor.boiler_curflowtemp"]
    e = by_key(m := build(cfg=cfg))
    assert e["gas_sensor"]["source"] == "gewählt" and e["weather_entity"]["chosen"] == "weather.zuhause"
    assert m["extra_overrides"] == []
    assert validate(cfg.to_raw())[1] == []  # gespeichert wie geladen: gültig


# ------------------------------------------------------------------ Räume, Personen, Benachrichtigungen
def test_room_entities_from_room_entries_with_suggestion():
    m = build({"rooms": [{"climate": "climate.wz", "name": "Wohnzimmer", "heating_power": "sensor.wz_heizleistung"}]})
    rooms = group(m, "raeume")["entries"]
    assert [e["setting"]["field"] for e in rooms] == ["climate", "temperature", "humidity", "heating_power"]
    assert {e["room"] for e in rooms} == {"Wohnzimmer"} and {e["room_climate"] for e in rooms} == {"climate.wz"}
    c, t, h, v = rooms
    assert (c["source"], c["required"], c["state"], c["area"]) == ("gewählt", True, "heat", "Wohnzimmer")
    assert (v["source"], v["current"], v["unit"]) == ("gewählt", "sensor.wz_heizleistung", "%")
    # leeres Feld: HeatPilot nutzt es nicht – der Sensor am Gerät steht als Vorschlag dabei, nicht als „automatisch“
    assert (h["source"], h["issue"], h["suggested"]) == ("fehlt", "nicht gewählt", "sensor.wz_feuchte")
    assert t["suggested"] is None and not t["problem"]
    # ohne eigene Räume: die erkannten Geräte mit ihren Sensoren, automatisch
    auto = group(build(), "raeume")["entries"]
    assert {(e["setting"]["field"], e["source"]) for e in auto} >= {("climate", "automatisch")}
    assert all(e["setting"]["index"] is None for e in auto)


def test_persons_only_name_and_state_no_coordinates():
    m = build()
    p = by_key(m)["persons"]
    assert p["source"] == "automatisch" and p["current"] == ["person.anna"]  # das Auto ist kein Benutzer
    assert all(set(i) == {"entity_id", "friendly_name", "exists", "state", "available"} for i in p["items"])
    text = json.dumps(m)
    for word in ("latitude", "longitude", "gps_accuracy", "device_trackers", "user_id"):
        assert word not in text, word
    assert by_key(build({"persons": ["person.auto"]}))["persons"]["source"] == "gewählt"


def test_notify_targets_entity_service_missing():
    e = by_key(build({"notify_targets": ["notify.handy_anna", "notify.mobile_app_tablet", "notify.weg"]}))["notify_targets"]
    items = {i["entity_id"]: (i["kind"], i["available"]) for i in e["items"]}
    assert items == {"notify.handy_anna": ("entity", True), "notify.mobile_app_tablet": ("service", True),
                     "notify.weg": (None, False)}  # „unknown“ ist bei notify-Entitäten normal
    assert e["issue"] == "nicht verfügbar"
    # Dienste nicht lesbar: offen statt „fehlt“
    e = by_key(build({"notify_targets": ["notify.mobile_app_tablet"]}, services=None))["notify_targets"]
    assert e["items"][0]["available"] is None and e["issue"] is None


def test_gas_fallback_energy_estimate_and_counts():
    m = build(drop=["sensor.gaszaehler"])
    g = by_key(m)["gas_sensor"]
    assert (g["source"], g["current"]) == ("automatisch", "sensor.boiler_nrgtotal")
    assert g["fallback"] == "Ersatz: Energie-Schätzung von EMS-ESP – kein Gaszähler gefunden"
    for gr in m["groups"]:
        c = gr["counts"]
        assert c == em.counts(gr["entries"]) and c["total"] == len(gr["entries"])
        assert c["found"] == sum(e["source"] != "fehlt" for e in gr["entries"])
    assert m["counts"]["total"] == sum(gr["counts"]["total"] for gr in m["groups"])
    assert m["counts"]["unavailable"] == 1 and m["sources"] == ["gewählt", "automatisch", "EMS-ESP", "fehlt"]


# ------------------------------------------------------------------ Abfrage: nur lesen
def test_api_entity_map_only_reads(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot.engine import Engine
    from heatpilot.store import Store
    from heatpilot.web import make_app

    states = base_states()
    lst = [{"entity_id": k, **v} for k, v in states.items()]
    reg = registry(states)
    cfg = Config.from_raw({"overrides": ["flow_temp=sensor.boiler_curflowtemp"]})
    eng = Engine(cfg, Store(tmp_path / "e.db"), discover(lst, reg, cfg))
    eng.states = dict(states)
    calls = []

    async def get_services():
        raise RuntimeError("unknown command")  # ältere HA: Dienste nicht lesbar

    async def registry_():
        return reg

    async def suggestions():
        return discover(lst, reg, Config.from_raw({}), all_rooms=True).as_dict(private=False)

    async def call_service(*a, **kw):  # darf nie aufgerufen werden
        calls.append(a)

    app = SimpleNamespace(eng=eng, cfg=cfg, status="läuft", configured=True, registry=registry_, suggestions=suggestions,
                          ha=SimpleNamespace(get_services=get_services, call_service=call_service))

    async def go():
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            r = await client.get("/api/entity_map")
            assert r.status == 200
            j = await r.json()
            app.eng = None
            starting = await (await client.get("/api/entity_map")).json()
            post = await client.post("/api/entity_map", json={})
            return j, starting, post.status
        finally:
            await client.close()

    j, starting, post = asyncio.run(go())
    e = by_key(j)
    assert e["flow_temp"]["source"] == "gewählt" and e["notify_targets"]["items"] == []
    assert starting == {"starting": True, "status": "läuft"} and post == 405  # nur GET
    assert calls == [] and "latitude" not in json.dumps(j)


# ------------------------------------------------------------------ Lesbarkeit, Doku, Version
def test_module_readable_and_documented():
    text = (PKG / "entity_map.py").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 600 and text.startswith('"""Seite „Entitäten“ (seit 0.39.1)')
    for m in re.finditer(r"^def (\w+)\(.*?(?=^def |^class |^@|\Z)", text, re.M | re.S):
        assert m.group(0).count("\n") <= 60, m.group(1)
    for name in ("web.py", "config.py", "discovery.py"):
        assert len((PKG / name).read_text(encoding="utf-8").splitlines()) <= 600, name
    assert "„Nötig“ heißt: ohne den Wert fehlt HeatPilot etwas ohne Ersatz – im Code geprüft" in text
    assert "Der Rücklauf ist optional" in text


def test_docs_and_changelog_name_the_page():
    docs = " ".join((ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8").split())
    assert "## Entitäten" in docs and "Zuordnungen (Experten)" not in docs
    for text in ("Einstellungen → Entitäten", "„Automatik“", "nur fehlende und nicht verfügbare", "automatisch gefunden",
                 "über EMS-ESP", "nicht verfügbar", "`overrides`", "eines genügt", "Rücklauf", "api/entity_map"):
        assert text in docs, text
    head = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## 0.39.1")[1].split("\n## ")[0]  # seit 0.40
    assert "Einstellungsseite „Entitäten“" in head and "entity_map.py" in head
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Entitäten" in readme
