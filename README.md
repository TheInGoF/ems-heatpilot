<p align="center"><img src="ems_heatpilot/logo.png" alt="HeatPilot" width="250"></p>

# EMS HeatPilot

**Ein Programm, das eine bestehende Gas- oder Ölheizung besser macht.**

Für Wärmepumpen gibt es viele schlaue Steuerungen. Wer aber zur Miete wohnt oder
aus anderen Gründen die Energiequelle nicht wählen kann, sitzt auf einem
Gas- oder Ölkessel – oft schlecht eingestellt, mit Takten, Nachlauf und
unnötig warmem Warmwasserspeicher. Genau dafür ist HeatPilot gedacht: Es lernt,
wie *dieses* Haus und *diese* Heizung sich verhalten, zeigt, wo Energie verloren
geht, und regelt auf Wunsch die Räume selbst – ohne Komfort zu opfern und ohne Cloud.

HeatPilot ist ein Home-Assistant-Add-on für Kessel mit **EMS-Bus** (Buderus,
Bosch, Junkers, Sieger u. a.) über **[EMS-ESP](https://github.com/emsesp/EMS-ESP32)**,
mit Raumregelung über **Tado** und Anwesenheit über die Home-Assistant-App.

> **Beta (0.x): Raumregelung mit Tado** – über die Tado-Cloud-Integration oder lokal
> über die Tado-Bridge in HomeKit (`homekit_controller`). Andere `climate`-Thermostate
> in Home Assistant werden erkannt und lassen sich wählen, sind aber ungetestet.

## Was es heute kann (0.36)

### Lernen

- **Haus:** Wärmeverlust (W/K), Fremdwärme und Heizgrenze, Trägheit, Heizlast bei jeder Außentemperatur
- **Räume:** Aufheizen und Auskühlen je Raum, auch Fußbodenheizung ohne Stellventil; Sonne je Raum ohne Sensor, aus Sonnenstand und Bewölkung
- **Fremdwärme je Raum:** Kamin, Ofen oder viele Gäste werden erkannt – diese Zeit lernen weder Raum- noch Hausmodell
- **Warmwasser:** Bereitschaftsverlust je Temperatur, Ladekosten, Zapfprofil; Legionellenschaltung und Einmalladung laufen mit, ohne gelernt zu werden
- **Anwesenheit:** Gewohnheiten je Person und Ankunft per GPS-Annäherung, beliebig viele Personen; Reisen und regelmäßiges Pendeln werden erkannt, die Vorhersage wird bei jeder Heimkehr kontrolliert
- **Nachtzeiten:** aus dem Tado-Zeitplan und – mit Leistungssensoren des Hauses – aus dem Stromverbrauch je Wochentag: wann das Haus schlafen geht und aufsteht
- **Ausreißer werden nicht gelernt:** Gaszähler-Nachträge, verpasste Zählungen, unmögliche Werte und Einzelspitzen aus Tado, EMS-ESP oder Home Assistant
- **Urlaub:** Gewohnheiten pausieren, nichts wird umgelernt

### Steuern

- **Schalter „Steuerung“:** „Beobachten“ (Standard) oder „HeatPilot steuert“. Dann schreibt HeatPilot die Raum-Solltemperaturen selbst: Komfort je Raum, Nacht nach Wochenplan (wahlweise mit den gelernten Zeiten aus dem Stromverbrauch), Abwesenheit, wenn niemand zu Hause ist, Urlaub
- **Vorheizen** vor der Ankunft und vor dem Nachtende, so früh, wie der Raum laut Modell braucht – gedeckelt; Räume, die ihr Soll nicht schaffen, werden akzeptiert statt hochgedreht
- **Hand, Pause, Protokoll:** von Hand Gedrehtes bleibt bis zum nächsten Phasenwechsel, Pause (1 h, 2 h, 4 h oder bis morgen früh), jeder Schreibvorgang mit Grund im Protokoll
- **Räume aus Home Assistant:** Bereiche und Geräte werden gelesen und vorgeschlagen; mehrere Thermostate bilden einen Raum, ein Wandthermostat kann Raumregler sein
- **Tado lokal über HomeKit:** die Ventile der Tado-Bridge auch ohne Tado-Cloud – der Boost läuft dann als Timer in HeatPilot
- **Heizen per Knopfdruck:** Boost für gewählte Räume (oder alle), Zeitplan und Ausschalten für alle Räume, Soll und Betriebsart je Raum – aufgebaut wie die Tado-App
- **„Bereit für die Steuerung?“:** Ampel mit Zustand je Raum, ob HeatPilot steuern könnte – Thermostate beschreibbar, Raummodelle, Anwesenheit, Nachtzeiten, Außentemperatur; dazu, nur lesend, ob EMS-ESP die nötigen Kesselsteuerungen anbietet

### Auswerten

- **Live-Ansicht:** was Kessel, Warmwasser und jeder Raum gerade tun, mit Grafen der letzten Stunden
- **Die Woche im Blick:** Gas, Starts, Anwesenheit, Tado-Soll, Ventile und Fremdwärme je Wochentag und Stunde – auch wetterbereinigt und als Abweichung vom Hausmodell
- **Schwachstellen messen:** Brennerstarts und Laufzeit je Start, Wartezeit durch Taktsperre, Startverzögerung des Reglers, Brenner-Nachlauf ohne Wärmebedarf
- **Vorlauf gegen Takten:** ob ein höherer Vorlauf bei deinem Kessel weniger Starts und Gas bringt oder Verschwendung ist
- **Ersparnis ehrlich schätzen:** nur bei gleichem Komfort – geprüft je Raum, mit realistischem Wiederaufheizen über die Heizkörper
- **Empfehlungen für Tado:** wo Tado zu spät oder zu früh heizt – Zeitplan, Frühstart, Geofencing je Raum
- **Hydraulischer Abgleich:** welcher Heizkörper zu wenig oder zu viel Heizwasser bekommt – mit eingetragenen Stufen konkret („von 5 auf 6 stellen“), mit grober Wirkung auf die anderen Räume und Nachmessen nach der Änderung
- **Warmwasser-Empfehlung:** sparsamste Speichertemperatur nach Zapfprofil und Anwesenheit; die Legionellenladung des Reglers ist eingerechnet, die große Zapfung danach braucht keine eigene Ladung – nur Empfehlung, gesetzt wird nichts
- **Vorhersage sichtbar:** Minimum und Trend der nächsten 12 h; der Vorheizplan nennt die Außentemperatur, mit der er rechnet
- **Zertifikat als PDF:** Haus und Heizung auf einer Seite – Heizlast, Kesselgröße, Takten, Energieklasse und Gesamtbewertung, bezogen auf die letzten 12 Monate
- **Diagnose und Export:** Selbsttest, Log und alle gelernten Werte als Datei zum Gegenprüfen – ohne Koordinaten und Zugangsdaten
- **Einrichtung komplett in der Oberfläche** – keine YAML-Optionen, Vorschläge vorausgefüllt
- Oberfläche in der HA-Seitenleiste und Sensoren `sensor.heatpilot_*` für eigene Dashboards

## Wie rechnet HeatPilot?

- [Das Hausmodell](docs/hausmodell.md) – Wärmeverlust, Fremdwärme, Heizlast und Trägheit: Gleichung, woher die Zahlen kommen, wann gelernt wird, Grenzen

## Grundsätze

- **Lokal.** Alle Daten und alles Lernen bleiben auf dem Home-Assistant-Rechner.
- **Erst beobachten, dann handeln.** Standard ist „Beobachten“: HeatPilot lernt und
  empfiehlt, geschaltet wird nur, was du im Reiter „Heizen“ selbst antippst. Mit
  „HeatPilot steuert“ schreibt HeatPilot die Raum-Sollwerte selbst – nur für Räume mit
  „macht mit“, nur 5–25 °C, jeder Schreibvorgang im Protokoll. Zurück geht es nur über
  denselben Schalter. An EMS-ESP schickt HeatPilot nur Lese-Anfragen (GET); geschrieben
  wird nur auf die Thermostate, die du als Raum wählst – den Heizkreis von EMS-ESP
  (`climate.…_thermostat_hc1`) deshalb nicht als Raum wählen, sonst ginge ein Soll über Home
  Assistant an den Regler (siehe [Steuerung](ems_heatpilot/DOCS.md#steuerung-beobachten-oder-heatpilot-steuert)).
- **Sparen ja, übertreiben nein.** Komfort ist eine feste Grenze, gezählt wird am
  Gaszähler fürs ganze Haus, „gar nicht heizen“ ist keine Strategie, unsichere
  Modelle empfehlen nichts (siehe [Leitplanken](ems_heatpilot/DOCS.md)).
- **HeatPilot übernimmt die Räume, wenn du es einschaltest – Tado bleibt das Stellglied.**
  Die Thermostate regeln ihre Ventile weiter selbst, HeatPilot gibt ihnen nur das Soll vor.
  Bei „Beobachten“ bleiben Tado-App, Zeitpläne und Geofencing, wie sie sind. Nach dem
  Zurückschalten setzt du den Zeitplan in der Tado-App fort; fällt HeatPilot aus, bleibt das
  zuletzt geschriebene Soll stehen.

## Phasen

Stand der Absprachen 19.10.2026, Details in [docs/planung-vorlauf-ems.md](docs/planung-vorlauf-ems.md).

| Phase | Inhalt | Status |
|---|---|---|
| 1 | Beobachten, lernen, empfehlen | erfüllt (0.1–0.33) |
| 2 | Räume steuern: Komfort, Nacht nach Wochenplan bzw. Stromverbrauch, Abwesenheit, Vorheizen; „Bereit für die Steuerung?“ | erfüllt (0.34–0.36.1) |
| 3 | Vorschau (Trockenlauf): welcher Vorlauf und welches Warmwasser-Soll je Stunde, Vorhersage in der Steuerung | 0.37 |
| 4 | Warmwasser über EMS-ESP: Speicher-Soll nur bei Änderung, Prüfung nach 60 s, Desinfektion nach Termin, Fehler in HA melden | 0.36.3 / 0.38 |
| 5 | Thermostate als Stellglied: Ventilverhalten lernen, Soll in voller Auflösung, Räume gemeinsam anfordern (weniger Takten) | 0.39 |
| 6 | Kessel übernehmen: Vorlauf live und Heizbetrieb über EMS-ESP, eigene Witterungsführung – der Regler (z. B. ES73) entfällt | geplant |
| 7 | Wirkungsgrad je Vorlauf gelernt statt fester Richtwert (Vorlauf-Studie → Planung) | teilweise (gemessen) |
| 8 | Offene Thermostate (Zigbee, Z-Wave, Matter, Homematic) getestet | optional |

## Voraussetzungen

- Home Assistant OS oder Supervised (für Add-ons), z. B. auf einem Raspberry Pi 5
- [EMS-ESP](https://github.com/emsesp/EMS-ESP32) am Kessel (empfohlen ≥ 3.7, [Doku](https://emsesp.org)),
  in HA eingebunden; optional direkter Zugriff per IP
- Tado-Integration in HA oder HomeKit-Controller mit der Tado-Bridge (lokal, ohne Cloud)
- Gaszähler in HA (z. B. Reed-Kontakt am EMS-ESP-Zähleingang), sonst Schätzung aus EMS-ESP
- Außentemperatur (Fühler oder Wetter-Integration)
- Optional: Personen mit der Home-Assistant-App (Anwesenheit), eine Wetter-Integration
  (Vorhersage für den Vorheizplan, Bewölkung für die Sonne), Leistungssensor(en) fürs Haus (Nachtzeiten aus dem Stromverbrauch)

## Installation

1. Home Assistant → Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories
2. `https://github.com/TheInGoF/ems-heatpilot` eintragen
3. „EMS HeatPilot“ installieren und starten
4. In der Seitenleiste „HeatPilot“ öffnen – der Reiter **Einstellungen** führt
   durch die Einrichtung (Vorschläge sind vorausgefüllt). Das Add-on hat keine YAML-Optionen.

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

---

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/lordvonbaum)

> Spenden sind freiwillig und unterstützen ausschließlich das Projekt. Sie haben keinen Einfluss auf die Priorisierung von Fehlern, Funktionswünschen oder Support.
