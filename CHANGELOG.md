# Changelog

## 0.8.0
- Einstellungen komplett in der Weboberfläche: Reiter „Einstellungen“ mit Entitäten-Auswahl (Suche), Raumtabelle, Urlauben, Gas/Kosten, Komfort, Warmwasser, Zuordnungen und Erweitert
- Vorschläge aus der automatischen Erkennung vorausgefüllt; bei Erstinstallation öffnet sich die Seite direkt in den Einstellungen
- Prüfung aller Eingaben mit verständlichen Fehlermeldungen; Speichern wirkt sofort ohne Neustart (Lernstand bleibt)
- EMS-ESP-Verbindungstest, „Historie neu einlesen“
- Einstellungen in `/data/settings.json`, Vorrang vor den YAML-Optionen

## 0.7.2
- Urlaubsrückkehr per GPS nur bei sicherer Heimfahrt: ≥ 30 min ununterbrochen näher, ≥ 15 km und 10 % der Strecke, Schnitt ≥ 20 km/h; Rast/kleine Umwege erlaubt, Kehrtwende oder 45 min ohne Fortschritt heben auf; Ankunft aus Durchschnittstempo der Heimfahrt

## 0.7.1
- Urlaubsrückkehr: schnelle GPS-Annäherung (≥ 20 km/h) schlägt das geplante Ende, auch bei früherer/späterer Rückkehr; geplantes Ende mit Uhrzeit wird genau genommen, nur Datum = `vacation_arrival_time`
- Urlaub endet automatisch, sobald jemand 10 min zu Hause ist (bleibt über Neustarts gespeichert); ohne Rückkehr bis zu 3 Tage nach Plan-Ende aktiv
- Im Urlaub wird die GPS-Position weiter verfolgt, nur Gewohnheiten pausieren

## 0.7.0
- Vorheizplan berücksichtigt, dass Warmwasser und Heizen sich die Therme teilen: erst Speicher laden, direkt danach Räume heizen, gemeinsamer früherer Start plus Puffer; „zu spät“ wird mit Minuten ausgewiesen
- Speicher-Auskühlen bis zum Planstart und Ladedauer aus dem gelernten Modell (vorsichtige 40 K/h, solange das Modell noch lernt)
- Ersparnis-Rechnung: während Warmwasserladung keine Heizleistung für die Räume

## 0.6.0
- Fußbodenheizung ohne Stellventil (`floor_heating_rooms`): zweiter, träger Wärmeeingang im Raummodell, Heizkörper als Boost
- Urlaubsmodus (`vacations`, `vacation_entity`): Gewohnheiten (Anwesenheit, Zapfprofil, Raummodelle) pausieren, Physik lernt weiter, Urlaub zählt nicht zur Ersparnis, Urlaubsende = Ankunft
- Datenbank: Urlaubs-Kennzeichen in Abtast- und Stundenwerten (automatische Migration)
- Neue oder umgestellte Raummodelle lernen sofort aus der gespeicherten Historie

## 0.5.0
- Leitplanken (`guards.py`): Plausibilitätsgrenzen für alle Modelle, keine Empfehlung bei unsicheren/jungen Modellen
- Ersparnis-Rechnung Heizen: realistisches Wiederaufheizen mit begrenzter Brennerleistung, rechtzeitiges Vorheizen, Zählung zu kalter Stunden – vorher heizte die Rechnung sofort wieder auf und schönte die Ersparnis
- Warmwasser: gedeckelte Selbstkorrektur der Reserve nach zu kalten Zapfungen
- Oberfläche: Feld „Leitplanken“ mit Modellstatus und Komfort-Kontrolle

## 0.4.0
- Warmwasser lernen: Auskühlen/Bereitschaftsverlust je Temperatur, Ladekosten je Kelvin (kalter vs. warmer Start), Zapfprofil (Tag + Woche), Nachladegeschwindigkeit
- Warmwasser-Empfehlung nach Anwesenheit, Ankunft und erwarteter Zapfung; Strategie läuft im Schatten mit (Ersparnis, „zu kalt“-Zähler)
- Neue Optionen `dhw_min_temp`, `dhw_comfort_temp`, `dhw_max_temp`; neue Sensoren und Oberflächenbereich „Warmwasser“

## 0.3.1
- Gaszähler am EMS-ESP-Zähleingang (m³) unterstützt; Dezimalkomma wird verstanden
- Fehlt der eingestellte Gaszähler, wird gewarnt und automatisch gesucht
- Optionaler Sensor für die Heizungsanforderung (`demand_sensor`) wird aufgezeichnet; Zähler für Tag/Nacht-Wechsel pro Tag
- Historie beim ersten Start bis 365 Tage einstellbar (Vorgabe 60)

## 0.3.0
- EMS-ESP-Entitäten mit langen Namen (Standard in HA) werden erkannt, auch mit Präfix
- EMS-ESP-Adresse als Option (`emsesp_host`); nur lesend
- Harte Schreibsperre: Add-on darf nur `sensor.heatpilot_*` setzen
- Regler-Betriebsart (Tag/Nacht) als Heizfreigabe; neue Zähler: Startverzögerung bis zur Freigabe, Sperrzeit
- Zusätzlich erfasst: Raum-Soll und Remote-Temperatur am Regler, Pumpenmodulation, Flammenstrom, Speichertemperatur

## 0.2.0
- Räume, Außenfühler (mehrere, Minimum) und Gasabrechnung (Brennwert, Zustandszahl, Arbeits- und Grundpreis) konfigurierbar
- Optional alle Werte direkt von EMS-ESP lesen (`emsesp_host`, nur lesen)
- Regler-Werte (ES73/RC35: berechnete Vorlauftemperatur), „Heizen abschalten“, Zähler der Therme
- Wärmebedarf aus Tado-Soll statt Ventilstellung; ereignisgenaue Zähler für Brenner-Nachlauf ohne Bedarf und Wartezeit durch Taktsperre
- Starts und Laufzeit je Heizstart aus den Zählern der Therme, inkl. Wert seit Einbau
- Gaskosten; Jahreswerte nur noch bei vollem Datenjahr (keine verzerrte Hochrechnung)

## 0.1.0
- Erste Version: Beobachten und Lernen (Haus-, Raum-, Anwesenheitsmodell), Datenübernahme aus HA, Weboberfläche, Sensoren
