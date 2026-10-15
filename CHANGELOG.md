# Changelog

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
