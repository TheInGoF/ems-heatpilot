"""0.59.2: Fehlerbericht – anonymisiert (Räume, Personen, Orte, Entitäten), mit Version, Thermostatstufen und
Kesselmodell; die Oberfläche fragt vor dem Melden auf GitHub nach, die vollen Daten stehen eingeklappt mit Warnung."""

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from heatpilot import __version__, report
from heatpilot.discovery import Registry, Room

ROOT = Path(__file__).resolve().parents[1]


def fake_eng():
    rooms = [Room(name="Wohnzimmer", climate="climate.wohnzimmer_va1", platform="tado", area="Wohnzimmer",
                  room_id="auto:wohnzimmer"),
             Room(name="Kinderzimmer Lea", climate="climate.lea", platform="shelly", temp_sensor="sensor.lea_t")]
    states = {"climate.wohnzimmer_va1": {"attributes": {"friendly_name": "Wohnzimmer"}},
              "climate.lea": {"attributes": {}}, "sensor.lea_t": {"attributes": {}},
              "person.ingo": {"attributes": {"friendly_name": "Ingo", "latitude": 52.1}},
              "zone.arbeit": {"attributes": {"friendly_name": "Firma Müller"}},
              "sensor.boiler_curflowtemp": {"attributes": {}}}
    cfg = SimpleNamespace(rooms=[{"climate": "climate.lea", "name": "Kinderzimmer Lea", "lead": "ventil",
                                  "valve_pos": "number.lea_ventil"}],
                          control_mode="beobachten", boiler_control="aus", dhw_control="aus")
    found = SimpleNamespace(rooms=rooms, persons=["person.ingo"])
    return SimpleNamespace(found=found, states=states, cfg=cfg)


def test_anonymizer_replaces_names_keeps_ems_entities():
    a = report.Anonymizer(fake_eng())
    assert a.text("auto:wohnzimmer") == "raum_1"
    text = a.text("Wohnzimmer und Kinderzimmer Lea; Ingo bei Firma Müller; climate.lea, sensor.lea_t, "
                  "sensor.boiler_curflowtemp; Wohnzimmer ist")
    assert "Wohnzimmer" not in text and "Lea" not in text and "Ingo" not in text and "Müller" not in text
    assert text.startswith("Raum 1 und Raum 2; Person 1 bei Ort 1; climate.raum_2, sensor.e")
    assert "sensor.boiler_curflowtemp" in text
    data = a.data({"person.ingo": {"latitude": 52.1, "name": "Ingo"}, "emsesp_host": "192.168.1.5"})
    assert data == {"person.p1": {"name": "Person 1"}}


def test_stage_and_info():
    assert report.stage({"platform": "tado"}) == 1 and report.stage({"platform": "homekit_controller"}) == 1
    assert report.stage({"platform": "shelly"}) == 2
    assert report.stage({"platform": "shelly", "lead": "ventil", "valve_pos": "number.v"}) == 3
    eng = fake_eng()
    reg = Registry(entities=[{"entity_id": "sensor.boiler_curflowtemp", "device_id": "d1"}],
                   devices=[{"id": "d1", "manufacturer": "Buderus", "model": "GB172"}])
    assert report.boiler_model(eng, reg) == "Buderus GB172" and report.boiler_model(eng, Registry()) is None


def test_build_zip(monkeypatch):
    eng = fake_eng()
    monkeypatch.setattr(report.diagnostics, "export", lambda app, days: {"days": days, "rooms": ["Wohnzimmer"]})
    monkeypatch.setattr(report.diagnostics, "self_check", lambda app: [])
    monkeypatch.setattr(report.live_log, "csv_text", lambda eng, h: f"zeit,Wohnzimmer ist\n{h},20\n")
    app = SimpleNamespace(eng=eng, cfg=eng.cfg)
    z = zipfile.ZipFile(io.BytesIO(report.build(app, Registry(), 12)))
    assert sorted(z.namelist()) == ["info.json", "kurzzeit_12h.csv", "langzeit_30_tage.json"]
    info = json.loads(z.read("info.json"))
    assert info["version"] == __version__ and info["boiler_model"] == "unbekannt"
    assert [t["stage"] for t in info["thermostats"]] == [1, 3] and info["thermostats"][1]["climate"] == "climate.raum_2"
    assert json.loads(z.read("langzeit_30_tage.json")) == {"days": 30, "rooms": ["Raum 1"]}
    assert z.read("kurzzeit_12h.csv").decode() == "zeit,Raum 1 ist\n12,20\n"


def test_ui_and_issue_form():
    page = (ROOT / "ems_heatpilot" / "heatpilot" / "static" / "index.html").read_text(encoding="utf-8")
    files = page.split('id="diag-files"')[1].split('id="diag-full"')[0]
    assert 'href="api/report?hours=24"' in files and 'onclick="return issueConfirm()"' in files
    full = page.split('id="diag-full"')[1].split("</details>\n    </div>")[0]
    assert "Achtung, sensible Daten" in full and "api/diagnostics/bundle" in full and "api/export?days=14" in full
    assert "issues/new?template=fehler.yml" in page and report.ISSUE_URL in page
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "fehler.yml").read_text(encoding="utf-8")
    assert "required: true" in form and "schläft" in form and "Fehlerbericht herunterladen" in form
