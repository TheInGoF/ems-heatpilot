# HeatPilot – UI-Styleguide Add-on

Stand: 27.10.2026. Gilt nur für die Add-on-Oberfläche; das Lovelace-Dashboard (Repository `heatpilot-card`) bleibt bei
wenigen Kacheln (Raumtemperatur, künftig Anwesenheit/Abwesenheit). Farben bleiben wie bisher. Dieses Regelwerk gilt bei
jeder UI-Arbeit.

## 1. Ausgangslage
- Vier Tabs: Steuerung, Übersicht (nur Text), Live-Daten (Graphen), Einstellungen mit vielen Untermenüs.
- Teilweise doppelte Inhalte zwischen den Tabs.
- Die Live-Daten sind fürs Debugging unverzichtbar: Soll und Ist, Brenner an/aus, Anzahl der Takte – Graphen statt
  Text und Logs.

## 2. Informationsarchitektur
- Jede Information hat genau einen Ort; andere Stellen verlinken dorthin statt sie zu wiederholen.
- **Übersicht** zeigt den Zustand des Hauses mit Grafik statt nur Text: pro Raum Soll/Ist, Heizstatus, Kesselstatus.
- **Live-Daten** bleiben als Diagnose-Tab mit Graphen (Soll/Ist, Brenner an/aus, Takte pro Zeitraum).
- **Einstellungen** maximal zwei Ebenen tief: Startseite mit drei Kacheln Räume, Kessel, Haus → jeweils eine
  Detailseite.
- Experten-/Debug-Optionen in einem ausklappbaren Bereich am Ende jeder Seite, nicht als eigene Untermenüs.

## 3. Typografie
- Genau drei Schriftgrößen: Titel (Seiten- und Kartenüberschrift), Text, Klein (Einheiten, Zeitstempel, Hinweise).
- Große Zahl nur für Hauptwerte wie die Raumtemperatur.
- Schrift und Grundfarben aus den Theme-Variablen von Home Assistant, damit Hell- und Dunkelmodus passen.

## 4. Abstände und Raster
- Ein fester Abstandsschritt (z. B. 8 px) und nur Vielfache davon: 8, 16, 24, 32.
- Einheitliches Kartenraster; Karten haben gleiche Innenabstände und Eckenradien.

## 5. Einheitliche Bausteine
- **Raumkarte:** Name, Ist-Temperatur groß, Sollwert, Heizstatus, Thermostatstufe, Lernfortschritt.
- **Gerätekarte:** Sensor oder Aktor mit Icon, aktuellem Wert, Verbindungsstatus.
- **Diagrammkarte:** einheitliche Achsen, Zeitraumwahl, gleiche Farben für Soll/Ist überall.
- **Einstellungszeile:** Bezeichnung links, Bedienelement rechts, Erklärung klein darunter.
- **Hinweisbox:** Info, Warnung, Fehler – immer gleich aufgebaut.

## 6. Einheitliche Untermenüs
- Jede Detailseite folgt demselben Aufbau: Kopf mit Name und Status → Hauptinhalte als Karten → ausklappbare
  Experteneinstellungen.
- Zurück-Navigation immer an derselben Stelle.
- Speichern einheitlich (entweder überall automatisch oder überall mit Knopf).

## 7. Vorgehen
- Bestehende Seiten gegen diese Regeln prüfen und doppelte Inhalte auflisten.
- Erst die Einstellungen umbauen, dann die Übersicht grafisch aufwerten.
- Nach jedem Umbau Screenshots prüfen.
