"""0.46.1: Übersicht → Warmwasser und → Kessel zeigen, was HeatPilot die nächsten 12 Stunden plant – aus der
vorhandenen Vorschau (api/preview, keine zweite Abfrage). Seit 0.46.3 als Zeitstrahl: die Prüfungen der Oberfläche
(Abschnitte, Fenster ab jetzt, leere Vorschau, Karten) stehen mit gleicher Absicht in tests/test_ui_v463.py."""
from pathlib import Path

import pytest
from test_preview37 import _old_flow_floor, by_hour, dhw_model, house  # noqa: F401 – autouse-Fixture wie dort
from test_control34 import SUN, ts_of

from heatpilot import preview as pv
from heatpilot.config import Config

ROOT = Path(__file__).resolve().parents[1]


def test_preview_carries_hold_and_reasons_per_step(tmp_path):
    cfg = Config.from_raw({"control_mode": "heatpilot"})
    eng, _, _ = house(tmp_path, cfg=cfg)
    eng.dhw = dhw_model()
    p = pv.preview(eng, ts_of(SUN, 10))
    h = by_hour(p)
    assert h["12:00"]["dhw"] == 50 and h["12:00"]["dhw_hold"] == 45  # seit 0.46.2: Komfort 50 − 5 K (angenommen)
    assert h["21:00"]["dhw_hold"] == 40 and "Speicher darf bis 40 °C fallen" in h["21:00"]["dhw_reason"]
    for s in p["steps"]:
        assert {"flow_why", "dhw_why", "dhw_hold"} <= set(s)
        if s["dhw"] is not None:
            assert s["dhw_hold"] == pytest.approx(s["dhw"] - 5) and s["dhw_why"]


def test_version_changelog_docs():
    from heatpilot import __version__
    assert __version__ >= "0.46.1"
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "\n## 0.46.1\n" in log  # seit 0.46.2 nicht mehr oben
    head = log.split("\n## 0.46.1\n")[1].split("\n## ")[0]
    for t in ("Nächste 12 Stunden", "api/preview", "dhw_hold", "tests/test_ui_v461.py"):
        assert t in head, t
    assert "Nächste 12 Stunden" in (ROOT / "ems_heatpilot" / "DOCS.md").read_text(encoding="utf-8")
