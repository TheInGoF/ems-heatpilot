# EMS HeatPilot – Hinweise für Claude Code

- Bei jeder Arbeit an der Oberfläche (`ems_heatpilot/heatpilot/static/index.html`) gilt `docs/ui-styleguide.md`.
- Änderungen an der Oberfläche erst als Screenshot zeigen, dann pushen.
- Antworten auf Deutsch, knapp. Keine Code-Leichen, keine Aliasse für Umbenanntes.
- Version gleich in `ems_heatpilot/config.yaml`, `heatpilot/__init__.py`, `CHANGELOG.md` (ausführlich) und
  `ems_heatpilot/CHANGELOG.md` (Stichworte für Home Assistant); `tests/test_struktur.py` prüft das und die Längen.
- Tests gezielt, die volle Reihe einmal vor dem Push.
