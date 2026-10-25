"""Struktur des Repos an einer Stelle (0.52.1) – ersetzt die früher je Feature-Datei wiederholten Prüfungen.

Regeln: Jedes Modul beginnt mit einem Docstring und hat höchstens 600 Zeilen; Funktionen höchstens 60 Zeilen,
Methoden 65 (gemessen am Quelltext samt Docstring, wie bisher); die Version ist in config.yaml, ``__init__`` und im
neuesten CHANGELOG-Abschnitt dieselbe. Feature-Dateien prüfen seither nur noch, was ihr Thema betrifft."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ems_heatpilot" / "heatpilot"

# Bestandsschutz: diese Funktionen waren schon länger – sie dürfen nicht weiter wachsen, neue Ausnahmen gibt es nicht
FROZEN = {
    "advice.py:build": 204, "analysis.py:estimate_savings": 198, "analysis.py:preheat_plan": 73,
    "backfill.py:backfill_history": 73, "certificate.py:collect": 191, "certificate.py:render": 176,
    "config.py:validate": 83, "dhw.py:step": 114, "dhw_watch.py:step": 78, "diagnostics.py:self_check": 138,
    "diagnostics.py:export": 65, "diagnostics.py:sensor_table": 72, "discovery.py:discover": 95,
    "engine.py:__init__": 104, "engine_boiler.py:update_live": 68, "engine_learn.py:aggregate_hour": 72,
    "engine_sample.py:snapshot": 73, "engine_summary.py:summary": 214, "publisher.py:publish": 89,
    "readiness.py:assess": 167, "schedule.py:learn": 69, "web.py:make_app": 499, "web_adopt.py:routes": 83,
    "weekly.py:collect": 119,
}


def test_modules_start_with_docstring_and_stay_small():
    files = sorted(PKG.glob("*.py"))
    assert len(files) > 80  # das ganze Paket, nicht ein leeres Verzeichnis
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert text.startswith('"""'), f"{f.name}: beginnt nicht mit einem Docstring"
        assert len(text.splitlines()) <= 600, f"{f.name}: {len(text.splitlines())} Zeilen (höchstens 600)"


def test_functions_stay_short():
    for f in sorted(PKG.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        checks = [(node, 60) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        checks += [(sub, 65) for node in tree.body if isinstance(node, ast.ClassDef)
                   for sub in node.body if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node, limit in checks:
            start = min((d.lineno for d in node.decorator_list), default=node.lineno)
            lines = node.end_lineno - start + 1
            limit = max(limit, FROZEN.get(f"{f.name}:{node.name}", 0))
            assert lines <= limit, f"{f.name}:{node.lineno} {node.name}: {lines} Zeilen (höchstens {limit})"


def test_version_is_consistent_everywhere():
    from heatpilot import __version__

    cfg = (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    assert f'version: "{__version__}"' in cfg
    for path in (ROOT / "CHANGELOG.md", ROOT / "ems_heatpilot" / "CHANGELOG.md"):  # ausführlich · kurz für HA (0.56.8)
        log = path.read_text(encoding="utf-8")
        newest = log.split("\n## ", 1)[1].split("\n", 1)[0].strip()
        assert newest == __version__, f"{path.relative_to(ROOT)} beginnt mit {newest}, Version ist {__version__}"
