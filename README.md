# EMS HeatPilot

Lokales, selbstlernendes Home-Assistant-Add-on zur Heizungsoptimierung für
Gasthermen mit EMS-ESP (z. B. Sieger BK15), Tado-Raumregelung und
Anwesenheitserkennung. Keine Cloud: alle Daten bleiben auf dem Home-Assistant-Rechner.

## Ziel

- Das Haus lernen: Wärmeverlust, Trägheit, Aufheizverhalten jedes Raums.
- Abwesenheit und Nacht automatisch absenken, rechtzeitig vor Ankunft vorheizen.
- Ankunft selbst vorhersagen (GPS-Annäherung + gelernte Gewohnheiten), für beliebig viele Personen.
- Takten der Therme reduzieren, ohne dass die Taktsperre echten Wärmebedarf blockiert.
- Transparent zeigen, was das Modell gelernt hat und was es sparen würde.

## Phasen

| Phase | Inhalt | Status |
|---|---|---|
| 1 | Beobachten: Daten sammeln, Modelle lernen, Auswertung, Empfehlungen | **diese Version** |
| 2 | Taktsperre dynamisch steuern (`burnminperiod`, Hysterese), Vorlauf vorgeben | geplant |
| 3 | Absenken bei Abwesenheit/nachts + Vorheizen über Tado-Solltemperaturen | geplant |
| 4 | Wirkungsgrad je Vorlauftemperatur aus dem Gasverbrauch lernen | geplant |

In Phase 1 schreibt das Add-on **nichts** an die Therme oder an Tado.

## Installation

1. Home Assistant → Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories
2. URL dieses Repos eintragen (privates Repo: siehe DOCS.md)
3. „EMS HeatPilot“ installieren, Außenfühler und Gaszähler in den Optionen eintragen, starten
4. In der Seitenleiste erscheint „HeatPilot“

Ausführliche Doku: [ems_heatpilot/DOCS.md](ems_heatpilot/DOCS.md)

## Entwicklung

```bash
pip install aiohttp pytest
pytest tests                    # Modelltests
python tools/fake_ha.py         # simuliertes Home Assistant mit 30 Tagen Daten
cd ems_heatpilot && HA_URL=http://127.0.0.1:8123 SUPERVISOR_TOKEN=x \
  HEATPILOT_DATA=/tmp/hp python -m heatpilot.main   # Oberfläche: http://localhost:8099
```
