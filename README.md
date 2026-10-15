# EMS HeatPilot

**Ein Programm, das eine bestehende Gas- oder Ölheizung besser macht.**

Für Wärmepumpen gibt es viele schlaue Steuerungen. Wer aber zur Miete wohnt oder
aus anderen Gründen die Energiequelle nicht wählen kann, sitzt auf einem
Gas- oder Ölkessel – oft schlecht eingestellt, mit Takten, Nachlauf und
unnötig warmem Warmwasserspeicher. Genau dafür ist HeatPilot gedacht: Es lernt,
wie *dieses* Haus und *diese* Heizung sich verhalten, und zeigt (und später
nutzt), wo Energie verloren geht – ohne Komfort zu opfern und ohne Cloud.

HeatPilot ist ein Home-Assistant-Add-on für Kessel mit **EMS-Bus** (Buderus,
Bosch, Junkers, Sieger u. a.) über **EMS-ESP**, mit Raumregelung über **Tado**
und Anwesenheit über die Home-Assistant-App.

## Was es heute kann (0.8, Beobachtungsmodus)

- **Haus lernen:** Wärmeverlust (W/K), Trägheit, Heizlast bei jeder Außentemperatur
- **Räume lernen:** Aufheizen und Auskühlen je Raum, auch Fußbodenheizung ohne Stellventil
- **Warmwasser lernen:** Bereitschaftsverlust je Temperatur, Ladekosten, wann gezapft wird
- **Anwesenheit lernen:** Gewohnheiten je Person und Ankunft per GPS-Annäherung, beliebig viele Personen
- **Schwachstellen messen:** Brennerstarts und Laufzeit je Start, Wartezeit durch Taktsperre,
  Startverzögerung der Automationen, Brenner-Nachlauf ohne Wärmebedarf
- **Ersparnis ehrlich schätzen:** nur bei gleichem Komfort, mit realistischem Wiederaufheizen
- **Urlaubsmodus:** Gewohnheiten pausieren, nichts wird umgelernt
- **Alles in der Oberfläche einstellbar** – Einrichtung ohne YAML, mit Vorschlägen
- Oberfläche in der HA-Seitenleiste und Sensoren `sensor.heatpilot_*` für eigene Dashboards

## Grundsätze

- **Lokal.** Alle Daten und alles Lernen bleiben auf dem Home-Assistant-Rechner.
- **Erst beobachten, dann handeln.** Bis jetzt schreibt HeatPilot nichts an Kessel,
  Regler oder Tado – im Code hart gesperrt. Eingriffe kommen nur einzeln und nach Freigabe.
- **Sparen ja, übertreiben nein.** Komfort ist eine feste Grenze, gezählt wird am
  Gaszähler fürs ganze Haus, „gar nicht heizen“ ist keine Strategie, unsichere
  Modelle empfehlen nichts (siehe [Leitplanken](ems_heatpilot/DOCS.md)).
- **Tado bleibt Chef über die Raumtemperaturen.** App, Zeitpläne und Geofencing
  bleiben, wie sie sind. HeatPilot setzt auf der Kesselseite an.

## Phasen

| Phase | Inhalt | Status |
|---|---|---|
| 1 | Beobachten: Daten sammeln, Modelle lernen, Auswertung, Empfehlungen | **0.1–0.8** |
| 2 | Kesselseite: Heizfreigabe ereignisgesteuert, Sammelbetrieb bei Abwesenheit statt vieler Kurzstarts, Nachlauf vermeiden | geplant |
| 3 | Warmwasser-Soll nach Nutzung und Anwesenheit | geplant |
| 4 | Optional: Vorheizen vor Ankunft per Tado-Übersteuerung „bis zum nächsten Zeitplanblock“ | Idee |
| 5 | Wirkungsgrad je Vorlauftemperatur aus dem Gasverbrauch lernen | geplant |

## Voraussetzungen

- Home Assistant OS oder Supervised (für Add-ons), z. B. auf einem Raspberry Pi 5
- EMS-ESP am Kessel (empfohlen ≥ 3.7), in HA eingebunden; optional direkter Zugriff per IP
- Tado-Integration in HA
- Gaszähler in HA (z. B. Reed-Kontakt am EMS-ESP-Zähleingang), sonst Schätzung aus EMS-ESP
- Außentemperatur (Fühler oder Wetter-Integration)

## Installation

1. Home Assistant → Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories
2. URL dieses Repos eintragen (privates Repo: siehe [DOCS.md](ems_heatpilot/DOCS.md))
3. „EMS HeatPilot“ installieren und starten
4. In der Seitenleiste „HeatPilot“ öffnen – der Reiter **Einstellungen** führt
   durch die Einrichtung (Vorschläge sind vorausgefüllt, kein YAML nötig)

Ausführliche Doku: [ems_heatpilot/DOCS.md](ems_heatpilot/DOCS.md) · Änderungen: [CHANGELOG.md](CHANGELOG.md)

## Entwicklung

```bash
pip install aiohttp pytest
pytest tests                    # Modell- und Leitplanken-Tests
python tools/fake_ha.py         # simuliertes Home Assistant + EMS-ESP mit 30 Tagen Daten
cd ems_heatpilot && HA_URL=http://127.0.0.1:8123 SUPERVISOR_TOKEN=x \
  HEATPILOT_DATA=/tmp/hp python -m heatpilot.main   # Oberfläche: http://localhost:8099
```

## Lizenz

Wie [EMS-ESP](https://github.com/emsesp/EMS-ESP32) steht EMS HeatPilot unter der
**GNU Lesser General Public License v3.0** (oder einer späteren Version) –
siehe [LICENSE](LICENSE). Da die LGPL auf der GNU GPL v3 aufbaut, liegt deren
Text als [COPYING](COPYING) bei.

`SPDX-License-Identifier: LGPL-3.0-or-later`
