# Changelog

## 0.13.0
- Neuer Reiter **Live**: Anzeigen und Grafen für Kessel, Brenner, Warmwasser, außen/innen und jeden Raum (3–24 h, aktualisiert alle 30 s)
- **Hydraulischer Abgleich:** Empfehlung je Raum aus den gelernten Raummodellen und der Ventilöffnung – Voreinstellung eine Stufe auf (zu wenig Heizwasser) oder zu (zu viel)
- **Zeiten und Temperaturen lernen:** Nachtzeiten, Nacht- und Abwesenheits-Soll aus Tado (letzte 4 Wochen); Einstellungen sind Startwerte, Temperaturen nie kälter als eingestellt; abschaltbar
- Feld „Empfehlungen“ umfasst jetzt Tado und Ventile

## 0.12.0
- Zertifikat über Haus & Heizung als PDF (Übersicht → „PDF herunterladen“): Gebäude mit Heizlast und Energieklasse, Kesselgröße gegen Heizlast, Takten, Warmwasser, Verbrauch, Urteil und Stempel „GEMESSEN“/„VORLÄUFIG“ – ohne Namen, Adressen oder Standortdaten
- Neue Einstellungen „Haus & Kessel“: Wohnfläche, Nennleistung und kleinste Leistung des Kessels
- PDF wird ohne zusätzliche Bibliothek erzeugt

## 0.11.1
- Behoben: Add-on startete auf Home Assistant OS nicht („auth_invalid“, danach Abbruch nach 60 s). Start jetzt über `run.sh` mit `with-contenv`, damit der Zugangsschlüssel (`SUPERVISOR_TOKEN`) sicher ankommt; notfalls wird er aus der s6-Umgebungsdatei gelesen
- Keine Zeitgrenze mehr beim Verbinden: Oberfläche und Diagnose laufen weiter, das Add-on versucht es erneut; Grund steht oben auf der Seite und im Selbsttest
- Log und Diagnose-Datei zeigen, ob ein Schlüssel vorhanden ist (nur ja/nein, Länge, Quelle – nie den Schlüssel)

## 0.11.0
- Empfehlungen für Tado je Raum: Zeitplan/Frühstart (Frühstart je Raum am Ventil erkannt), Geofencing (Vorheiz-Einstellung Eco ↔ Komfort, Abwesenheitstemperatur), Soll nicht erreichbar – mit Beleg, Leitplanken und „behoben“-Verfolgung
- Neues Feld „Empfehlungen für Tado“ in der Übersicht, Sensor `sensor.heatpilot_tado_empfehlungen`

## 0.10.2
- Seitenleiste: Symbol `mdi:fire` (Home Assistant erlaubt dort nur Material-Design-Icons, keine eigenen Bilder)

## 0.10.1
- Logo: Flamme aus Knoten – `icon.png` und `logo.png` für Home Assistant, Symbol im Kopf der HeatPilot-Seite und als Browser-Symbol, Vorlage als SVG unter `assets/`

## 0.10.0
- Einrichtung ausschließlich über die Weboberfläche: keine YAML-Optionen mehr im Add-on (Reiter „Konfiguration“ entfällt); Werte aus `options.json` älterer Versionen werden weiter übernommen
- Doku ohne YAML-Beispiele, beschreibt die Oberfläche

## 0.9.0
- Diagnose: Reiter „Diagnose“ mit laufendem Selbsttest, Log-Ansicht (filterbar) und Diagnose-Datei zum Weitergeben (ohne GPS-Koordinaten und Zugangsdaten)
- Vollständiges Log: Ringpuffer plus rotierende Datei unter `/addon_configs/<slug>/logs/` (Samba/File-Editor); Zustandswechsel werden protokolliert; optional ausführliches Log je Takt
- Behoben: Datenimport legte vor Beginn der Historie leere Messpunkte an
- Behoben: Standort des Zuhauses stand im Log „Gefunden: …“

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
