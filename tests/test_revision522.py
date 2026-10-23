"""0.52.2: Revision – Werte eines abgebauten Reglers gelten nicht mehr als Fehler.

Ist der Regler abgebaut (vom Nutzer bestätigt oder EMS-ESP antwortet ohne Gerät „thermostat“), fehlen seine Werte zu
Recht: Entitätenseite und Sensor-Diagnose zeigen „Regler abgebaut“ statt „nicht verfügbar“/„fehlt“ und zählen sie
nicht als Problem. Mit Regler bleibt alles wie bisher."""

from heatpilot import entity_map as em
from heatpilot.config import Config

from test_entity_map391 import build, group, st

GONE = {"select.thermostat_hc1_mode": st("unavailable", name="Betriebsart")}


def test_regulator_values_are_not_problems_once_removed():
    m = build(extra=GONE, cfg=Config.from_raw({"regulator_removed": True}))
    regler = group(m, "regler")
    assert all(e["issue"] == em.ISSUE_REMOVED and not e["problem"] for e in regler["entries"] if not e["required"])
    assert regler["counts"]["problems"] == 0 and regler["counts"]["unavailable"] == 0


def test_ems_without_thermostat_counts_as_removed_too():
    m = build(extra=GONE, ems={"boiler": {"curflowtemp": 40.0}})
    assert all(e["issue"] == em.ISSUE_REMOVED for e in group(m, "regler")["entries"] if not e["required"])
    assert em.regulator_removed(Config(), {"boiler": {"x": 1}}) is True
    assert em.regulator_removed(Config(), {}) is False                                        # EMS-ESP unbekannt
    assert em.regulator_removed(Config(), {"boiler": {"x": 1}, "thermostat": {"y": 2}}) is False


def test_with_regulator_unavailable_stays_a_problem():
    m = build(extra=GONE)
    mode = next(e for e in group(m, "regler")["entries"] if e["key"] == "hc_mode")
    assert mode["issue"] == em.ISSUE_OFF and mode["problem"]


def test_sensor_table_marks_regulator_rows(tmp_path):
    from heatpilot import diagnostics

    from test_control34 import make

    eng, _, _, _ = make(tmp_path, thermostat={"hc_mode": "select.thermostat_hc1_mode"})
    eng.states["select.thermostat_hc1_mode"] = {"state": "unavailable", "attributes": {}}
    rows = [r for r in diagnostics.sensor_table(eng) if r["group"] == "Regler"]
    assert rows and any(r["status"] == "fehlt" for r in rows)       # Regler unbekannt: wie bisher
    eng.cfg.regulator_removed = True
    rows = [r for r in diagnostics.sensor_table(eng) if r["group"] == "Regler"]
    assert rows and all(r["status"] == "Regler abgebaut" for r in rows)


def test_ui_knows_the_new_state():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot" / "static"
            / "index.html").read_text(encoding="utf-8")
    assert '"Regler abgebaut": ["Regler abgebaut – nicht gebraucht"' in html
    assert "hasCalc ?" in html and "n.hc_mode !== null" in html
