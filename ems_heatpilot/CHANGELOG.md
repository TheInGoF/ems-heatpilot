# Änderungen

Kurzfassung der letzten Versionen. Ausführlich: CHANGELOG.md im Repository.

## 0.73.0
- Eigenes Climate je Raum: Zeitplan, Manuell, Aus, Boost
- Thermostat nur noch Einrichtung; Tado nur Temperaturen
- Kachelfarben nach Soll; Karte 0.6.0 mit eigenem Raum-Dialog

## 0.72.0
- Nachts keine Meldungen, morgens nur, was noch gilt
- Shelly-Thermostate: nur Ventil, nie Soll oder Betriebsart

## 0.71.0
- Stufe 3: keine Betriebsart mehr ans Thermostat
- Eigene Betriebsarten für ventilgeführte Räume (Aus, Manuell, Boost, Zeitplan)
- Tado bleibt auf manuell, „Automatik“ = HeatPilots Plan
- Knopf „Tag“: Nacht sofort beenden
- Knöpfe „nur jetzt“ werden nicht gelernt

## 0.70.0
- Raum wird nicht warm: erst Deckel weg, dann Abgleich, zuletzt Vorlauf
- Ventile: gelernte Wirkungsgrenze für den PID
- Meldung „Ventil schließt nicht“

## 0.69.0
- Fahrt zur Unzeit Richtung Zuhause: erst bestätigte Heimfahrt zählt

## 0.68.0
- Warmwasser nachts fertig 5 min vor dem Heizstart
- Ladedauer ab aktueller Speichertemperatur
- Fix: keine Kessel-Pause mehr durch Warmwasser-Ladung
- Fix: Soll nach Thermostat-Ausfall erneut geschrieben
- 30-s-Werte mit Anwesenheit je Person
- Fehlerbericht ohne Daten je Person

## 0.67.1
- Fix: Menü der Übersicht überlappte in der Seitenleiste
- Lange Kacheltitel brechen um

## 0.67.0
- Übersicht → Gewohnheiten: schlafend / wach / abwesend je Wochentag
- „Strom & Aktivität“ heißt „Gewohnheiten erkennen“
- Fix: Gruppen im Sensor-Eimer wählbar

## 0.66.0
- Einstellungen: nur Kacheln, Klick öffnet Unterseite
- Experte als Kacheln statt Aufklapper
- Strom & Aktivität als eigene Kachel unter Haus

## 0.65.1
- Fix: Lampen und Mediaplayer im Sensor-Eimer wählbar

## 0.65.0
- Räume, die keine sind, dauerhaft ausblenden

## 0.64.0
- Ersparnis: Stunden weniger geheizt als fester Zeitplan
- Vergleich mit früher je Gradtag, Zeitraum wählbar

## 0.63.0
- Sensor-Eimer: Lampen, Bewegungsmelder für die Nachtzeiten
- Licht nachts kurz an weckt nicht
- Aufwachzeit mit Sicherheitsangabe

## 0.62.0
- Nur jetzt: Nacht, Anwesend, Abwesend fürs ganze Haus
- Endet von selbst am nächsten Schaltpunkt
- Neue Karte heatpilot-mode-card (HACS)
- Fix: Raumkacheln im Reiter Heizen

## 0.61.0
- Kessel und Haus auf einen Blick als Gerätekarten

## 0.60.0
- Räume als Kacheln mit Qualitätsanzeige
- Klick öffnet die Raumansicht mit Gerätekarten
- tado: Hinweis „indirekte Regelung, träge“

## 0.59.2
- Anonymisierter Fehlerbericht zum Herunterladen
- Knopf „Fehler auf GitHub melden“ mit Datenschutz-Rückfrage
- Volle Daten eingeklappt mit Warnung

## 0.59.1
- Klimaanlagen werden nicht mehr als Raum vorgeschlagen

## 0.59.0
- Kein Rückfall-Vorlauf mehr
- Wächter meldet nur noch, schreibt nichts
- Wächter-Automation bitte neu kopieren
- Doku: Notfallanleitung Drehschalter

## 0.58.1
- Doku: was unterstützt wird, Thermostatstufen
- Doku: externe Regler wie ES73 vorher abbauen

## 0.58.0
- Einstellungen: drei Kacheln Räume, Kessel, Haus
- Seltenes unter „Experte“ am Seitenende
- Diagnose jetzt im Reiter Live
- Einheitliche Schriftgrößen und Karten
- Übersicht: Räume als Balken, Kessel als Kacheln
- Modelle jetzt unter Live → Diagnose

## 0.57.1
- Räume warten: Antipendelzeit 5 statt 10 min

## 0.57.0
- Warmwasser-Ladung löst keine Pause mehr aus
- 30-s-Protokoll: Spalte „hp_geschrieben“

## 0.56.9
- Vorheizen: alle Räume beginnen gemeinsam
- Vorheizen nie kürzer als das Raummodell

## 0.56.8
- Desinfektion: Kessel in Ruhe lassen, bis sein Vorlauf-Soll wieder niedrig ist
- Kurzes Changelog für Home Assistant

## 0.56.7
- Taster-Erkennung wieder entfernt

## 0.56.5
- Desinfektion: kein Vorlauf-Schreiben mehr während der Ladung

## 0.56.4
- Einstellungen → Erweitert: „Karten für Home Assistant“ mit YAML
- Karten senden nur Boost, Zeitplan, Aus

## 0.56.3
- Raum-Sensoren heißen nach dem Raum

## 0.56.2
- Raum-Sensoren vollständig für die Kacheln

## 0.56.1
- Übersicht: Wetter jetzt wie eine Wetter-Karte

## 0.56.0
- Sensoren und Befehls-Helfer für die HeatPilot-Karten (HACS)

## 0.55.2
- Übersicht: Wetter 24 h als Stundenbalken

## 0.55.1
- Wächter-Automation als YAML zum Kopieren

## 0.55.0
- Übersicht neu: Jetzt · Nächste 24 Stunden · Modelle

## 0.54.0
- Wind im Raummodell: Auskühlen je Raum mit Wind gelernt

## 0.53.0
- Raummodell lernt die Form des Heizkörpers
