# Changelog

## 0.18.1
- Empfehlungen für Tado werten Zeitpläne mit „aus“-Blöcken aus: „aus“ zählt als Absenkung (Frostschutz 5 °C), das Einschalten (z. B. 16:00) als Zeitplanwechsel und das Anheben bei Heimkehr als Geofencing – vorher fehlten genau diese Wechsel. Komfort-Sollwerte werden ohne „aus“-Zeiten bestimmt

## 0.18.0
- **Warmwasserladung ohne Ladesignal erkennen:** Meldet der Kessel „Warmwasser lädt“ nicht (oder nie), erkennt HeatPilot die Ladung am schnellen Anstieg der Speichertemperatur bei laufendem Brenner. Vorher wurden Ladungen als „Auskühlen“ gelernt und Ladekosten nie bestimmt
- Warmwasser-Auskühlen wird über ruhige 30-min-Abschnitte gelernt statt über einzelne Messschritte – bei 1–2-min-Abtastung und 0,1 K Fühlerauflösung war das zu verrauscht
- Tado-Räume auf „aus“ zählen beim Lernen der Zeiten als Absenkung (Frostschutz 5 °C) statt als „kein Soll“
- Auswertungen rechnen mit der Zeit statt mit „12 Messpunkte = 1 Stunde“ – stimmt jetzt bei jedem Abtastintervall
- Historie neu einlesen ersetzt die gespeicherten Messwerte des Zeitraums (keine zwei Raster nebeneinander, keine doppelten Brennerereignisse)
- **Diagnose → Alle Sensoren:** Tabelle mit Bereich, Zweck, Sensor/Quelle (HA oder EMS-ESP), Name in HA, aktuellem Wert, Stand und Zustand (ok, fehlt, veraltet, nicht gefunden); filterbar, optional mit allen nicht verwendeten EMS-ESP-Werten
- Export kompakt: Spaltenformat, gerundet, Messwerte auf 5 min verdichtet (14 Tage ≈ 1–3 MB statt bis zu 18 MB); Größe wird vorab aus den Daten geschätzt
- Beim Update wird der Lernstand einmal neu berechnet

## 0.17.2
- Warmwasser „Wann gezapft wird“: seltene große Zapfungen (z. B. Badewanne alle ein, zwei Wochen) überdecken das übliche Profil nicht mehr – sie zählen nur gedeckelt und stehen als Liste unter der Grafik (letzte 60 Tage). Erst nach ~2 Wochen je Tagesstunde wird etwas als Ausreißer eingestuft
- Live: eigene Anzeige „Heizungspumpe“ (an/aus und Modulation in %), Pumpe im Brenner-Grafen; Hinweis, wenn der Wert fehlt
- Zertifikat: ungültige Zahlen (NaN aus einem entgleisten Modell) können das PDF nicht mehr unlesbar machen; zusätzlich „Anzeigen“ zum Öffnen im Browser
- Beim Update wird der Lernstand einmal neu berechnet (Zapfprofil)

## 0.17.1
- Behoben: Das Warmwasser-Modell lernte steigende Speichertemperatur ohne Ladesignal (Fühler zieht nach der Ladung nach, Ladesignal fehlt) als „negatives Auskühlen“ – das verbog die Kurve und erzeugte Ausreißer. Jetzt nur echtes Auskühlen, erst 30 min nach Ladeende
- Grafik „Wärmebedarf des Hauses“: standardmäßig Tagesmittel der letzten 12 Monate (Energiesignatur), umschaltbar auf Stunden; einzelne Stunden streuen stark, weil der Kessel taktet. Stunden mit Warmwasserladung zählen nicht
- Grafik „Was Warmhalten kostet“: Skala nach dem Großteil der Messungen, einzelne Ausreißer-Punkte quetschen die Linie nicht mehr an den Rand (werden gezählt); Linie nur im gemessenen Bereich
- Beim Update wird der Lernstand einmal neu berechnet, damit das falsch gelernte Warmwasser-Modell verschwindet

## 0.17.0
- **Gelernte Werte exportieren** (Reiter Diagnose): alle Modelle in Klartext mit Gütemaß (Fehler, gekappte/verworfene Punkte, plausibel ja/nein), Lernkurve, alle Stundenwerte und die Messwerte der letzten 7/14/30 Tage – zum Nachrechnen, ob ein Modell etwas falsch verstanden hat; ohne GPS-Koordinaten
- Warmwasser „Was Warmhalten kostet“: zeigt jetzt die gemessenen Auskühl-Werte (Punkte, letzte 14 Tage) gegen das gelernte Modell (Linie) – sichtbar, ob das Modell passt. Bisher zeigte die Grafik 0 W, solange die Ladekosten noch nicht gelernt waren

## 0.16.3
- Einstellungen werden zusätzlich als `heatpilot-einstellungen.json` im Add-on-Konfigurationsordner gesichert (`/addon_configs/<slug>/`, per Samba erreichbar) und automatisch zurückgeholt, falls sie in `/data` fehlen
- Während HeatPilot nach einem Update die Historie neu einliest, zeigen Übersicht und Einstellungen das klar an („Deine Einstellungen sind gespeichert“) statt nur „Startet“

## 0.16.2
- Tado-Räume: Heizleistung/Ventil wird auch bei deutschen (oder anderen) Entitätsnamen erkannt, z. B. `sensor.kuche_heizleistung` – über den Übersetzungsschlüssel der Tado-Integration, den Namen oder als einziger %-Sensor am Thermostat
- Werden die Sensoren eines Raums geändert oder ein Raum neu gewählt, liest HeatPilot die Historie automatisch neu ein und lernt neu – sonst fehlten diesem Raum die Werte der Vergangenheit

## 0.16.1
- Personen: Fahrzeuge (oder Geräte), die als Person angelegt sind, werden erkannt – Gerät, aber kein Home-Assistant-Benutzer – und nicht automatisch gewählt; Hinweis in der Tabelle
- Räume: Spalte „Quelle“ (Integration) mit Hinweis bei Nicht-Tado-Einträgen, damit sich Klimaanlagen von Fahrzeugen oder Heizkreise leicht abwählen lassen

## 0.16.0
- Einstellungen: neuer Bereich **Personen** – wer für die Anwesenheit zählt, mit Hinweis auf Dummies ohne Gerät; automatisch werden nur Personen mit Gerät gewählt
- Nach geänderter Personenauswahl wird die Anwesenheit in den gespeicherten Daten neu gezählt
- Behoben: abgewählte Räume und Personen flossen über die gespeicherten Daten weiter in Empfehlungen und das Lernen der Zeiten ein

## 0.15.0
- Ersparnis-Schätzung prüft den Komfort **je Raum**: jeder Raum kühlt und heizt nach seinem Modell (höchstens volles Ventil und maximaler Vorlauf), vorgeheizt wird so früh, wie der langsamste Raum braucht; käme ein Raum nicht mehr hoch, wird nicht abgesenkt. Übersicht zeigt geprüfte Räume und zu kalte Stunden je Raum
- EMS-ESP: neben Kessel und Regler werden alle weiteren Geräte gelesen, die EMS-ESP kennt (Mischer, Solar, Sensoren …); Test und Selbsttest zeigen, was ankommt und ob Nennleistung/kleinste Leistung fehlen

## 0.14.0
- **Ausreißer werden nicht gelernt:**
  - Gaszähler: Sprünge/Nachträge (mehr Gas, als der Brenner in der Stunde verbrennen konnte, bzw. ein Vielfaches der Vorwoche) und verpasste Zählungen (Brenner lief, Zähler stand) werden nicht gelernt; Nachträge zählen weiter in Verbrauch und Kosten
  - Messwerte außerhalb physikalischer Grenzen (z. B. EMS-Platzhalter 3276,7) und einzelne Temperaturspitzen werden verworfen; defekter Außenfühler (ab drei Fühlern) wird ignoriert
  - Lernen: stark abweichende Punkte zählen nur begrenzt, grobe Ausreißer gar nicht
  - Übersicht, Selbsttest und Log zeigen, was ignoriert wurde
- Behoben: Der Gasverbrauch je Stunde ließ die letzten 5 Minuten jeder Stunde weg (rund 8 % zu wenig) – Hausmodell und Heizlast waren dadurch etwas zu niedrig
- Beim Update wird der Lernstand einmal mit dem neuen Schutz neu berechnet; „Historie neu einlesen“ lernt jetzt ebenfalls vollständig neu

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
