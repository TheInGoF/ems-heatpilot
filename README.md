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

> **Beta (0.x).** Getestet an einer Anlage: EMS-Kessel mit Tado V3 und Shelly TRV. Was unterstützt wird und was
> nicht, steht unter [Was unterstützt wird](#was-unterstützt-wird).

## Was es heute kann (0.37)

### Lernen

- **Haus:** Wärmeverlust (W/K), Fremdwärme und Heizgrenze, Trägheit, Heizlast bei jeder Außentemperatur
- **Räume:** Aufheizen und Auskühlen je Raum, auch mit Fußbodenheizung – seit 0.39.1 mit Anschluss (Mischer, RTL-Ventil, direkt am Kessel) und höchstem Vorlauf, über den die Vorschau nie plant; Sonne je Raum ohne Sensor, aus Sonnenstand und Bewölkung
- **Fremdwärme je Raum:** Kamin, Ofen oder viele Gäste werden erkannt – diese Zeit lernen weder Raum- noch Hausmodell
- **Ventilverhalten je Raum (seit 0.39):** wie das Thermostat sein Ventil abhängig vom Abstand zum Soll stellt – Kennlinie, Reaktionszeit auf Soll-Sprünge, kleines Reglermodell mit Vorhersagefehler; bei Thermostaten ohne Ventilwert die Schaltpunkte. Gelernt aus den eigenen Messwerten, ohne Gerätedaten
- **Warmwasser:** Bereitschaftsverlust je Temperatur, Ladekosten, Zapfprofil; Legionellenschaltung und Einmalladung laufen mit, ohne gelernt zu werden
- **Anwesenheit:** Gewohnheiten je Person und Ankunft per GPS-Annäherung, beliebig viele Personen; Reisen und regelmäßiges Pendeln werden erkannt, die Vorhersage wird bei jeder Heimkehr kontrolliert
- **Nachtzeiten:** aus dem Tado-Zeitplan und – mit Leistungssensoren des Hauses – aus dem Stromverbrauch je Wochentag: wann das Haus schlafen geht und aufsteht
- **Ausreißer werden nicht gelernt:** Gaszähler-Nachträge, verpasste Zählungen, unmögliche Werte und Einzelspitzen aus Tado, EMS-ESP oder Home Assistant
- **Urlaub:** Gewohnheiten pausieren, nichts wird umgelernt

### Steuern

- **Schalter „Steuerung“:** „Beobachten“ (Standard) oder „HeatPilot steuert“. Dann schreibt HeatPilot die Raum-Solltemperaturen selbst: Komfort je Raum, Nacht nach Wochenplan (wahlweise mit den gelernten Zeiten aus dem Stromverbrauch), Abwesenheit, wenn niemand zu Hause ist, Urlaub
- **Vorheizen** vor der Ankunft und vor dem Nachtende, so früh, wie der Raum laut Modell braucht – gedeckelt; Räume, die ihr Soll nicht schaffen, werden akzeptiert statt hochgedreht. Die Vorheizdauer rechnet mit der Außentemperatur zum Zielzeitpunkt laut Vorhersage: Wird es bis dahin kälter, beginnt das Vorheizen früher
- **Hand, Pause, Protokoll:** von Hand Gedrehtes bleibt bis zum nächsten Phasenwechsel, Pause (1 h, 2 h, 4 h oder bis morgen früh), jeder Schreibvorgang mit Grund im Protokoll
- **Ventile führen (seit 0.40, eigener Schalter, Standard aus):** HeatPilot verschiebt das Soll um höchstens einen Schritt des Thermostats – früh drosseln, wenn ein Raum gleich am Soll ist und sonst überschwingen würde, gemeinsam anfordern, wenn der Kessel für einen Raum allein taktet. Nur in Räumen mit „macht mit“ und belastbaren Modellen, nur bei Änderung mit 5 min Haltezeit; Hand und Phasenwechsel gehen vor, wer Wärme braucht, bekommt sie sofort
- **Ventilgeführte Räume (seit 0.51):** Räume mit Stellventil samt Prozent-Entität (z. B. Shelly BLU TRV) regelt HeatPilot direkt über die Ventilöffnung – PID mit Vorsteuerung aus dem Raummodell, lernende Verstärkung je Raum, höchstens ein Schreiben je 5 min nur über die eine zugewiesene number-Entität (0–100 %, Prüfung nach 60 s, Pause nach 3 Fehlschlägen, Hand hält 1 h). Dazu ein automatischer hydraulischer Abgleich: lernende Öffnungs-Deckel drosseln Räume, die es leicht haben, bis unterversorgte mitkommen (5 Punkte je Tag, nie unter 40 %)
- **Kessel über HeatPilot (seit 0.41, Weg B, eigener Schalter, Standard aus):** HeatPilot führt den Kessel selbst – Vorlauf-Soll live alle 30 s aus dem anspruchsvollsten Raum (nur bei Änderung ab 1 K, höchstens einmal je Minute), Heizbetrieb an/aus mit 5 min Haltezeit, ohne Pendeln (anheben sofort, senken bzw. ausschalten erst, wenn es 10 bzw. 15 min dabei bleibt), Mindest-Vorlauf aus der Abnahme der offenen Räume, Zuschlag von 2 K je 15 min, wenn ein Raum mit offenem Ventil nicht hochkommt (seit 0.50), Takt-Boost in 2-K-Schritten bis +10 K, wenn der Kessel taktet, kein Senken in den ersten 10 min eines Brennerlaufs, Fußboden-Grenze hart vor jedem Schreiben (steht der Kessel darüber, sofort zurück). Erst Trockenlauf (nur Protokoll), dann schreiben – aber nur ohne Regler am Bus; Prüfung nach 60 s, Pause bei Fehlschlägen oder fremden Änderungen; beim Zurückschalten schreibt es nichts mehr. Für den Ausfall ein Wächter in Home Assistant, der aufs Handy meldet (`sensor.heatpilot_heartbeat`, Anleitung in der Oberfläche). Meldet der Kessel keine kleinste Leistung, schlägt HeatPilot sie seit 0.42 aus dem Bereich der maximalen Brennerleistung vor (nur „übernehmen“ trägt sie ein)
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
- **Warmwasser-Empfehlung:** sparsamste Speichertemperatur nach Zapfprofil und Anwesenheit; die Legionellenladung des Reglers ist eingerechnet, die große Zapfung danach braucht keine eigene Ladung – im Standard nur Empfehlung; seit 0.38 setzt HeatPilot das Speicher-Soll auf Wunsch selbst (eigener Schalter „HeatPilot setzt“)
- **Desinfektion (Legionellen) mit Terminvorschlag:** Termin in den Einstellungen, Temperatur vom Kessel; HeatPilot schlägt die Zeit kurz vor deiner größten regelmäßigen Zapfung vor („So 18 Uhr zapfst du am meisten – Desinfektion So 17:30“) und plant sie ein. Seit 0.42 löst HeatPilot sie ohne Regler am Bus selbst aus (mit „Warmwasser-Soll: HeatPilot setzt“): Schalter „Desinfizieren“ zum Termin, Prüfung nach 60 s, Erfolg bei Desinfektionstemperatur − 3 K (nur, wenn der Kessel sie bestätigt), sonst Meldung; danach wieder aus (bei kurzer Sperre weiter versucht, sonst Meldung) – höchstens einmal je Termin und je 24 h, verpasste binnen 12 h nachgeholt
- **Vorhersage sichtbar:** Minimum und Trend der nächsten 12 h; der Vorheizplan nennt die Außentemperatur, mit der er rechnet
- **Vorschau (24 h)** (bis 0.42 „Vorschau (Trockenlauf)“): welchen Vorlauf, welchen Heizbetrieb und welches Warmwasser-Soll HeatPilot in den nächsten 24 h je Stunde am Kessel setzen würde – aus Raummodellen, Anwesenheit, Nachtplan, Vorhersage und Warmwasser-Empfehlung, mit den Schreibvorgängen, die es gäbe. Nur Anzeige – am Kessel schreibt HeatPilot davon nur das Warmwasser-Soll, und nur mit „HeatPilot setzt“ (seit 0.38)
- **Ventile beobachten (seit 0.39, bis 0.42 „Trockenlauf“):** wie oft HeatPilot ein Raum-Soll früh um einen Schritt gesenkt hätte (der Raum würde überschwingen) oder Räume gemeinsam hätte anfordern lassen, wenn der Kessel für einen Raum allein taktet – mit geschätzter Wirkung, wo die Modelle belastbar sind. Ohne den Schalter „Ventile führen“ nur Protokoll; mit ihm zeigt derselbe Abschnitt, was HeatPilot wirklich geschrieben hat („schreibt“) und was danach gemessen wurde
- **Vorschau gegen Ist (7 Tage):** der stündlich gemerkte Plan neben dem, was Regler (z. B. ES73) und Warmwasser-Automation tatsächlich gesetzt haben – je Tag und über 7 Tage, mit Unterschied in K und Zahl der Schreibvorgänge
- **Zertifikat als PDF:** Haus und Heizung auf einer Seite – Heizlast, Kesselgröße, Takten, Energieklasse und Gesamtbewertung, bezogen auf die letzten 12 Monate
- **Diagnose und Export** (Live → Diagnose): Selbsttest, Bereitschaft, Protokoll, Log und alle gelernten Werte als Datei zum Gegenprüfen – ohne Koordinaten und Zugangsdaten
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
  wird nur auf die Thermostate, die du als Raum wählst, und – nur mit dem eigenen Schalter
  „HeatPilot setzt“ (seit 0.38) – über Home Assistant auf die eine Entität des Warmwasser-Solls
  (ganze Grad zwischen Minimum und Maximum, nur bei Änderung, Prüfung nach 60 s); mit dem Schalter „Kessel über
  HeatPilot“ (seit 0.41) zusätzlich auf die eine Entität des Vorlauf-Solls (ganze Grad, 35 °C bis zum höchsten Vorlauf
  und zur Fußboden-Grenze) und die eine des Heizbetriebs – nie, solange ein Regler am Bus ist; die Antipendelzeit
  bleibt, wie sie ist. Den Heizkreis von EMS-ESP
  (`climate.…_thermostat_hc1`) erkennt HeatPilot am Gerät: Er macht nie mit, und jeder Schreibversuch
  darauf wird hart abgewiesen (siehe [Räume und Geräte](ems_heatpilot/DOCS.md#räume-und-geräte)).
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
| 3 | Vorschau (Trockenlauf): welcher Vorlauf und welches Warmwasser-Soll je Stunde, Vorhersage in der Steuerung | erfüllt (0.37) |
| 4 | Warmwasser am Kessel: Speicher-Soll nur bei Änderung, Prüfung nach 60 s, Fehler in HA melden, Desinfektion nach Termin | erfüllt (0.36.3 / 0.38 / 0.42) – Warmwasser-Soll gebaut, Desinfektion auslösen gebaut (0.42) |
| 5 | Thermostate als Stellglied: Ventilverhalten lernen, Soll in voller Auflösung, Räume gemeinsam anfordern (weniger Takten) | 0.39 – Ventilverhalten und Trockenlauf gebaut; 0.40 – Ventile führen gebaut (eigener Schalter) |
| 6 | Kessel übernehmen: Vorlauf live und Heizbetrieb über Home Assistant, eigene Witterungsführung – der Regler entfällt | 0.41 – Weg B gebaut (Vorlauf live, Heizbetrieb, Abnahme, Takt-Boost, Wächter; eigener Schalter); mit 0.42 auch die Desinfektion – der Regler kann nach dem Trockenlauf entfallen |
| 7 | Wirkungsgrad je Vorlauf gelernt statt fester Richtwert (Vorlauf-Studie → Planung) | teilweise (gemessen) |
| 8 | Ventile direkt stellen: PID je Raum über die Ventilöffnung, automatischer hydraulischer Abgleich (z. B. Shelly BLU TRV) | 0.51 gebaut – im Einsatz zu erproben |
| 9 | Heizen für alle im Haushalt: Reiter „Heizen“ als Lovelace-Karte ohne Einstellungen | 0.52 gebaut |
| 10 | Weitere offene Thermostate (Zigbee, Z-Wave, Matter, Homematic) getestet | optional |

## Voraussetzungen

- Home Assistant OS oder Supervised (für Add-ons), z. B. auf einem Raspberry Pi 5
- [EMS-ESP](https://github.com/emsesp/EMS-ESP32) am Kessel (empfohlen ≥ 3.7, [Doku](https://emsesp.org)),
  in HA eingebunden; optional direkter Zugriff per IP
- Tado-Integration in HA oder HomeKit-Controller mit der Tado-Bridge (lokal, ohne Cloud)
- Thermostate, die das Soll in **0,5 K oder feiner** stellen (Tado: 0,5 K). „Ventile führen“ arbeitet mit einem
  Schritt dieser Größe – Thermostate mit gröberen Schritten (z. B. 1 K) werden dafür nicht genutzt; solche besser
  nicht einsetzen
- Gaszähler in HA (z. B. Reed-Kontakt am EMS-ESP-Zähleingang), sonst Schätzung aus EMS-ESP
- Außentemperatur (Fühler oder Wetter-Integration)
- Optional: Personen mit der Home-Assistant-App (Anwesenheit), eine Wetter-Integration
  (Vorhersage für den Vorheizplan, Bewölkung für die Sonne), Leistungssensor(en) fürs Haus (Nachtzeiten aus dem Stromverbrauch)

## Was unterstützt wird

HeatPilot ist ein Hobbyprojekt. Getestet ist es an **einer** Anlage – der des Entwicklers. Alles andere gilt als
„Community / ungetestet“: Es kann funktionieren, einen Anspruch auf Hilfe für fremde Anlagen gibt es nicht. Fehler
bitte mit dem anonymisierten **Fehlerbericht** als Issue melden (Live → Diagnose → Dateien → „Fehler auf GitHub
melden“); Wünsche für andere Hardware nur mit eigenem Test.

**Kessel:** ausschließlich mit **EMS-Bus über EMS-ESP**. Andere Kessel (OpenTherm, eBUS, Wärmepumpen) werden nicht
unterstützt. Ohne Kesselsteuerung bleibt HeatPilot eine lernende Raumsteuerung nach Anwesenheit und gelernten Zeiten.

> **Voraussetzung am Kessel:** Jeder externe Heizungsregler, der Vorlauf-Soll oder Heizbetrieb selbst schreibt
> (z. B. **ES73**, RC310, RC35 am EMS-Bus), muss abgebaut oder abgeschaltet sein, bevor HeatPilot den Kessel steuert.
> Sonst überschreibt er die Werte von HeatPilot regelmäßig, und beide arbeiten gegeneinander. Zum Beobachten und Lernen
> darf er bleiben.

**Thermostate** – drei Stufen, nach Güte der Regelung:

| Stufe | Beispiel | Wie HeatPilot regelt | Stand |
|---|---|---|---|
| 1 – indirekt, träge | tado V3 (stellen lokal über HomeKit, Ventilstellung über die Cloud-Integration) | über das Soll, vorausschauend (z. B. 21 → 20 °C, wenn der Raum erfahrungsgemäß überheizt); ca. 5 min Verzögerung | getestet |
| 2 – Soll direkt | Zigbee-Thermostate mit Ventilstellung in Home Assistant | über das Soll, Ventil live sichtbar | ungetestet |
| 3 – Ventil direkt | Shelly TRV (Ventilöffnung als beschreibbare Entität) | PID über die Ventilöffnung, schleicht sich an das Soll heran | im Test |

tado X: ungetestet, nicht bewertbar. Für jede Stufe dringend empfohlen: ein **externer Raumfühler** je Raum – der Fühler
im Thermostat sitzt am Heizkörper und misst zu hoch (Einstellungen → Räume → Einzelheiten).

## Installation

1. Home Assistant → Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories
2. `https://github.com/TheInGoF/ems-heatpilot` eintragen
3. „EMS HeatPilot“ installieren und starten
4. In der Seitenleiste „HeatPilot“ öffnen – der Reiter **Einstellungen** führt
   durch die Einrichtung (Vorschläge sind vorausgefüllt). Das Add-on hat keine YAML-Optionen. Die Entitäten, auf die
   HeatPilot schreibt, wählst du bei ihrer Funktion (Einstellungen → Kessel); was es nur liest, steht unter
   Einstellungen → Haus → Experte.

**Oberfläche (seit 0.58):** vier Reiter – **Heizen** (Räume bedienen), **Übersicht** (Räume und Kessel grafisch,
Wetter, die nächsten 24 Stunden, Empfehlungen), **Live** (Messwerte, darunter **Diagnose**) und **Einstellungen**
(drei Kacheln Räume, Kessel, Haus; Seltenes unter „Experte“). Die Schalter heißen überall
„Aus · Beobachten · HeatPilot steuert“.

**Seit 0.44:** Wechselt ein Raum das Thermostat, fragt HeatPilot, ob Gelerntes übernommen wird (gleiches Thermostat, neu
angebunden), nur das Ventil neu lernt (anderes Thermostat) oder alles neu lernt. Die eigene Datenbank ist die Wahrheit:
Home Assistant füllt nur Lücken, eigene Werte bleiben 720 Tage (einstellbar).

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
