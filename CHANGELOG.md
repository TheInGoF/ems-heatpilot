# Changelog

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
- Gaszähler `sensor.analogsensor_21` (EMS-ESP-Zähleingang, m³) voreingestellt; Dezimalkomma wird verstanden
- Fehlt der eingestellte Gaszähler, wird gewarnt und automatisch gesucht
- `sensor.heizungsanforderung` wird aufgezeichnet; Zähler für Tag/Nacht-Wechsel pro Tag
- Historie beim ersten Start bis 365 Tage einstellbar (Vorgabe 60)

## 0.3.0
- EMS-ESP-Entitäten mit langen Namen (Standard in HA) werden erkannt, auch mit Präfix
- EMS-ESP-Adresse vorbelegt (192.168.188.102), weiterhin änderbar; nur lesend
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
