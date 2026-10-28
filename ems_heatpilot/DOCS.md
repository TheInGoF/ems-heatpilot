# EMS HeatPilot

Lokale, selbstlernende Heizungsoptimierung für Kessel mit
[EMS-ESP](https://github.com/emsesp/EMS-ESP32) ([Doku](https://emsesp.org)),
Tado und Anwesenheit.

> **Beta (0.x).** Getestet an einer Anlage: EMS-Kessel über EMS-ESP mit Tado V3 (Stufe 1, indirekt über das Soll)
> und Shelly TRV (Stufe 3, Ventil direkt). Andere EMS-Kessel und Thermostate gelten als „Community / ungetestet“,
> ohne Anspruch auf Hilfe. Kessel ohne EMS-Bus werden nicht unterstützt.

> **Voraussetzung, bevor HeatPilot den Kessel steuert:** Externe Heizungsregler, die Vorlauf-Soll oder Heizbetrieb
> selbst schreiben (z. B. **ES73**, RC310, RC35 am EMS-Bus), abbauen oder abschalten – sie überschreiben sonst
> regelmäßig die Werte von HeatPilot. Zum Beobachten und Lernen dürfen sie bleiben.

## Einrichten

Nach der Installation öffnet die HeatPilot-Seite (Seitenleiste) direkt den
Reiter **Einstellungen**. Eingerichtet wird ausschließlich dort – das Add-on hat
keine YAML-Optionen.

**Aufbau der Oberfläche (seit 0.43):** vier Reiter. **Heizen** bedient die Räume (Aktionen, Raumkacheln und eine Zeile
zum Raumbetrieb – keine Systemmeldungen). **Übersicht** ist seit 0.55 ein schneller Überblick in drei Karten:
**Jetzt** (seit 0.58 grafisch: je Raum ein Balken bis zur Ist-Temperatur – orange, solange das Thermostat heizt – mit
Strich beim Soll, Skala 15–25 °C, Ist / Soll rot, wenn mehr als 0,3 K darunter; Kessel als drei Kacheln Brenner, Vorlauf
und Warmwasser je Ist/Soll aus den 30-s-Werten; dazu Betrieb, Bereitschaft, Warnungen, Kessel- und Warmwasser-Modus,
zuletzt geschrieben – das Wetter steht nur noch in der Karte Wetter),
**Nächste 24 Stunden** (Zeitleiste aus der Vorschau: Raumphasen mit Soll, Warmwasser-Soll, Desinfektion, Heizbetrieb an/aus,
Vorlauf-Spanne; die Karte **Modelle** steht seit 0.58 unter Live → Diagnose); dazwischen seit 0.55.2 die Karte **Wetter** – seit 0.56.1 oben
wie eine Wetter-Karte das Wetter jetzt (Symbol, Temperatur, Zustand, gefühlt, Spanne 24 h, Wind mit Richtung und Böen,
Luftfeuchte, Luftdruck, UV, Wolken), darunter die nächsten 24 Stunden: je Stunde ein Balken für Temperatur,
Regen (blass dahinter die Wahrscheinlichkeit), Wolken, Sonne (UV-Index) und Wind (blass dahinter die Böen) – aus der
stündlichen Vorhersage der Wetter-Integration, Antippen eines Balkens zeigt den Wert; was die Integration nicht liefert,
fällt weg (Zustand von Haus, Räumen, Ventilen, Warmwasser). Darunter die Empfehlungen je eine Zeile
(antippen klappt den Beleg auf), die Ersparnis als eine Zeile (Karte und Lernstand stehen unter Haus; seit 0.64 dort
zusätzlich **gegenüber festem Zeitplan** – Stunden je Woche weniger geheizt durch gelernte Nachtzeiten und
Abwesenheit, ohne „Gelernte Zeiten verwenden“ als „möglich“ – und **gegenüber früher**: Heizgas je Gradtag (G 20/15,
nur Heiztage unter 15 °C, ohne Warmwasser) der letzten 30 Tage gegen einen **selbst gewählten Vergleichszeitraum**,
damit Zeiten mit früheren Automationen oder Tado-Zeitplänen ausgeschlossen werden können; je Zeitraum mindestens
7 Heiztage) und die Unterseiten Haus, Räume, Warmwasser, Kessel, Anwesenheit und
Vorschau. **Live** zeigt die Messwerte der letzten Stunden. **Einstellungen** öffnet eine Startseite mit drei Kacheln (seit 0.61 oben auf Kessel und Haus „auf einen Blick“:
die eingestellten Aktoren und Sensoren als Gerätekarten mit Wert und Verbindung): **Räume** (Räume, Steuerung, Nachtzeiten), **Kessel**
(Warmwasser, Desinfektion, Kessel steuern, Kesseldaten) und **Haus** (EMS-ESP, Haus, Gas & Kosten, Anwesenheit &
Urlaub, Benachrichtigungen, Karten, seit 0.66 auch Gewohnheiten erkennen). Seit 0.66 höchstens zwei Klicks tief:
Jede Seite zeigt nur Kacheln – je Abschnitt eine, bei Räume zusätzlich je Raum eine; ein Klick öffnet eine Unterseite
mit nur diesem Abschnitt bzw. Raum, „‹ Räume“ führt zurück. Seltenes (gelesene Entitäten, Regler, Erweitert) steht als
schlichtere Kacheln am Ende unter **Experte**. **Diagnose** (Selbsttest, Bereitschaft, Modelle, Protokoll, Sensoren, Log, Dateien) steht am Ende
des Reiters **Live**. Die Schalter heißen überall **Aus · Beobachten · HeatPilot steuert**; „Beobachten“ (früher
„Trockenlauf“) rechnet und protokolliert, schreibt aber nichts. Die Entitäten, auf die HeatPilot schreibt
(Warmwasser-Soll, Desinfektion, Vorlauf-Soll, Heizbetrieb), wählst du bei ihrer Funktion unter Einstellungen → Kessel;
was HeatPilot nur liest, steht unter Einstellungen → Haus → Experte ([Entitäten](#entitäten)). Wo diese Beschreibung
ältere Orte nennt („Allgemein“, „Grundlagen“, „Steuerung“, „Entitäten“, Reiter „Diagnose“), gilt die Seite gleichen
Inhalts. Die Inhalte von „Allgemein“:

- **Steuerung:** der Hauptschalter **Beobachten** (Standard: lernen, empfehlen, Tado
  nur auf Knopfdruck) oder **HeatPilot steuert** (HeatPilot schreibt die
  Raum-Solltemperaturen selbst – siehe [Steuerung](#steuerung-beobachten-oder-heatpilot-steuert)),
  dazu der Wochenplan der Nachtabsenkung, Nacht- und Abwesenheitstemperatur, der
  Vorheizdeckel und die **Nachtzeiten aus dem Stromverbrauch** (Häkchen „Gelernte Zeiten verwenden“; die
  Leistungssensoren des Hauses und „Nicht zählen“ für Waschmaschine & Co. stehen unter Entitäten → Strom – siehe
  [Nachtzeiten aus dem Stromverbrauch](#nachtzeiten-aus-dem-stromverbrauch))
- **Verbindung:** EMS-ESP-Adresse mit „Testen“-Knopf (nur lesend, kein Passwort/Token).
  Gelesen werden Kessel und Regler alle 30 s und alle weiteren Geräte, die EMS-ESP
  kennt (Mischer, Solar, Sensoren …), alle 5 min. Fehlen Werte, die die
  EMS-ESP-Oberfläche zeigt: In EMS-ESP unter „Customizations“ können Werte für
  API/MQTT ausgeschlossen sein – die gibt EMS-ESP dann nirgends heraus. Manche Werte
  meldet der Kessel erst nach dem ersten Brennerstart bzw. der ersten Warmwasserladung.
  Seit 0.36.1 fragt HeatPilot außerdem beim Start und dann stündlich die Kesselsteuerungen ab
  (ebenfalls nur lesen) – siehe [Bereit für die Steuerung?](#bereit-für-die-steuerung)
- **Räume** (seit 0.60 als **Kacheln**): je Raum Name, Ist/Soll und eine Qualitätsanzeige – Thermostatstufe
  (Stufe 1 indirekt über das Soll wie tado, träge; Stufe 2 Soll direkt; Stufe 3 Ventil direkt), Raumfühler
  vorhanden, Lernfortschritt des Raummodells; gelb, wo sich Nachrüsten oder Warten lohnt. Ein Klick öffnet darunter
  die **Raumansicht**: oben die Geräte als Karten (Thermostat mit Ist, Soll und Ventil, Raumfühler, Feuchte,
  Ventil/Heizleistung, Ventilstellung – „nicht erreichbar“ rot, ein fehlender Raumfühler als dringende Empfehlung),
  darunter die Einstellungen des Raums. Je Raum (Thermostate mit Quelle/Integration): Häkchen, Name,
  **„macht mit“** und **Komforttemperatur** (für „HeatPilot steuert“), **Raum** und
  **Raumregler** (mehrere Thermostate als ein Raum), [Fußbodenheizung](#fußbodenheizung-je-raum) mit
  Anschluss und höchstem Vorlauf, **Voreinstellung**
  und **Stufen** des Ventils (für den hydraulischen Abgleich), optionale
  Ventil-/Temperatur-/Feuchtesensoren. Unter jeder Entität stehen **Bereich** und
  **Gerät** aus Home Assistant (z. B. „Bereich: Wohnzimmer · Gerät: tado Smart Radiator
  Thermostat VA… (SRT01)“), die Zeilen sind nach Bereich und Name sortiert. Vorschläge
  füllen nur leere Felder: der Bereich als Raum (bei gespeicherten Räumen nur als
  Platzhalter – „Bereiche übernehmen“ trägt ihn ein), die Sensoren des Geräts als
  Temperatur/Feuchte/Ventil, das Raumregler-Häkchen bei Wandthermostaten (Modell
  ST01/RU0x bzw. „Smart Thermostat“ ohne „Radiator“, als „Vorschlag“ markiert).
  Gespeichertes wird nie überschrieben. Automatisch gewählt
  werden Tado-Räume (Tado-Cloud, sonst die HomeKit-Ventile der Tado-Bridge – dasselbe Ventil nie
  doppelt über beide Wege; ohne Cloud heißen zwei Ventile im selben Bereich „Wohnzimmer VA1“ und
  „Wohnzimmer VA2“, bis sie als ein Raum gespeichert sind); was kein Raum ist
  (Klimaanlage im Auto, Heizkreis, Warmwasser),
  bleibt abgewählt bzw. lässt sich abwählen. Seit 0.65 lässt sich, was kein Raum ist, in der Raumansicht mit
  **„Ausblenden – kein Raum“** dauerhaft ausblenden (`hidden_rooms`): Die Kachel verschwindet, die Erkennung übergeht
  die Entität auch nach Updates und neuer Suche; „x ausgeblendet – zeigen“ unter den Kacheln holt sie zum Wieder-
  einblenden zurück. Klimaanlagen (Betriebsarten Kühlen, Trocknen, Lüften oder Heizen/Kühlen)
  schlägt HeatPilot seit 0.59.1 nie als Raum vor – auch nicht von Tado. Abgewählt bleibt abgewählt: Sind Räume
  gespeichert, gilt nur diese Liste, auch nach Updates oder wenn Home Assistant neue Thermostate meldet. Beim Heizkreis von EMS-ESP ist „macht mit“ gesperrt
  (siehe [Räume und Geräte](#räume-und-geräte)). Ändern sich die Sensoren eines Raums oder kommt ein Raum
  hinzu, liest HeatPilot die Historie automatisch neu ein (dauert einige Minuten). Beim
  Speichern mehrerer Geräte in einem Raum sagt HeatPilot, welches das Schlüsselgerät ist
  (Raumregler, sonst das erste)
- **Personen** (seit 0.39.1 unter Entitäten → Personen & Urlaub): alle `person.*` mit Häkchen. Automatisch gewählt werden nur Personen
  mit Home-Assistant-Benutzer und Gerät. Fahrzeuge, die als Person angelegt sind
  (Bild, Abwesenheiten, aber kein Benutzer), sind als „Fahrzeug oder Gerät?“
  markiert und nicht gewählt; Dummies ohne Gerät ebenso. Abwählen geht jederzeit. Nach einer
  Änderung zählt HeatPilot die Anwesenheit in den gespeicherten Daten neu;
  abgewählte Räume und Personen fließen in keine Auswertung mehr ein.
- **Gas & Kosten, Komfort & Strategie, Warmwasser, Urlaub** (Gaszähler, Warmwasser-Soll-Entität und Urlaubs-Schalter
  stehen dort nur als Verweis „ändern unter Entitäten“) – unter Warmwasser seit 0.36.3 auch der
  Termin der **Desinfektion (Legionellen)** mit Vorschlag (siehe [Desinfektion](#desinfektion-legionellen)), seit
  0.42 mit der Zeile **Auslösen** (wer sie zum Termin auslöst, letzte Desinfektion mit Ergebnis),
  seit 0.38 der Schalter **Warmwasser-Soll** (Aus / Trockenlauf / HeatPilot setzt, siehe
  [Weg W](#warmwasser-über-heatpilot-weg-w))
- **Benachrichtigungen** (seit 0.38, seit 0.39.1 unter Entitäten): Ziele in Home Assistant für Meldungen, mit
  „Testnachricht senden“ (siehe [Benachrichtigungen](#benachrichtigungen))
- **Erweitert:** Abtastintervall, Historie, Aufbewahrung (seit 0.44, `keep_days`: eigene Rohdaten so viele Tage
  aufheben, Standard 720, erlaubt 365–3650 – täglich fallen ältere Messpunkte, Raum- und Personenwerte,
  Brennerereignisse, Ventil-Ersatzwerte, Fremdwärme-Ereignisse und Stundenwerte weg; Tageszähler und Gelerntes
  bleiben), „Lücken aus Home Assistant füllen“ (bis 0.43 „Historie neu einlesen“), seit 0.42.1 „Hausdaten
  wiederherstellen“ und „Aus gespeicherten Daten neu lernen“ (siehe [Räume wechseln](#räume-wechseln-z-b-tado-cloud--homekit))

Felder sind mit Vorschlägen der automatischen Erkennung vorausgefüllt
(„Vorschläge übernehmen“ setzt Gaszähler, Wetter und Außenfühler auf „Automatik“ und füllt leere Sensor-Felder der
Räume; seit 0.44 bleiben gespeicherte Räume dabei, wie sie sind – vorher hakte der Knopf die automatische Auswahl an,
und ein zweites Gerät im Raum konnte Schlüssel werden). **Speichern** prüft alle Eingaben,
erklärt Fehler und übernimmt sofort, ohne Neustart. Die Einstellungen liegen in
`/data/settings.json` und bleiben bei Updates erhalten.

## Entitäten

Seit 0.39.1 steht unter **Einstellungen → Haus → Experte** geordnet alles, was
HeatPilot aus Home Assistant liest – und die Entitäten, die es beschreiben darf (Warmwasser-Soll, seit 0.42 der Schalter
der Desinfektion, seit 0.41 Vorlauf-Soll und Heizbetrieb). Die Gruppen sind
aufklappbare Karten mit Zähler („Kessel (EMS-ESP) · 13 von 38 gefunden · 1 nicht verfügbar“): **Kessel (EMS-ESP)**,
**Regler (EMS-ESP)**, **Warmwasser über HeatPilot**, **Außen & Wetter** (Außenfühler, Wetter), **Gas & Bedarf**
(Gaszähler, Heizungsanforderung), **Strom** (Leistungssensoren, „Nicht zählen“), **Räume**, **Personen & Urlaub**
(Personen, Urlaubs-Schalter) und **Benachrichtigungen**. Die Zahl am Reiter „Entitäten“ nennt die nötigen, die fehlen,
und die nicht verfügbaren. Diese Seite ersetzt die frühere Karte mit den Zuordnungen und die einzelnen Entitätsfelder.

Je Zeile: Name und wofür HeatPilot den Wert braucht, **nötig** oder **optional**, was gerade gilt (Entität, Zustand mit
Einheit, Bereich) und ein Status:

- **automatisch gefunden** – die Erkennung hat die Entität gefunden,
- **gewählt** – von dir gewählt (bis zum Speichern mit „nicht gespeichert“),
- **über EMS-ESP** – keine Entität, aber EMS-ESP liefert den Wert direkt (Einstellungen → Haus; beim
  Außenfühler am Kessel nur zur Anzeige, siehe unten),
- **Ersatz** – kein eigener Wert, eine andere Rolle genügt (z. B. „Brenner an“ statt der Brennerleistung),
- **fehlt** (nötig, ohne Ersatz), **nicht verfügbar** (fehlt in Home Assistant oder meldet „unavailable“/„unknown“),
  **nicht gefunden** bzw. **nicht gewählt** (optional).

Daneben das Feld mit **Suche** (Name, Entität oder Bereich; alle Wörter müssen vorkommen). Die Liste zeigt die
passenden Entitäten zuerst – den Vorschlag der Erkennung, dann nach Bereich und Name. Passend heißt die richtige
Domäne und Einheit bzw. Geräteklasse, z. B. für den Vorlauf `sensor`/`number` mit °C, für den Gaszähler m³ oder kWh,
für die Leistungssensoren W oder kW, für das Warmwasser-Soll nur, was dafür taugt. Mit Suchbegriff stehen darunter auch
die unpassenden, markiert mit „passt nicht zur Rolle“. Pfeiltasten und Enter gehen auch. Was im Feld steht, ist nur
die Suche: Übernommen wird ein Treffer (Klick oder Enter) oder eine Entität aus Home Assistant, genau so eingetragen;
eine Entität, die Home Assistant (noch) nicht kennt, nur in der Form `domain.name` und mit Enter. Escape oder das
Verlassen des Felds ohne Wahl lässt den Wert, wie er ist. **„Automatik“** geht zurück zur Erkennung (bei Rollen ohne
Erkennung heißt der Knopf „leeren“). Bei
Außenfühlern und Leistungssensoren lassen sich mehrere wählen, × entfernt eine. Oben filtert **„nur fehlende und nicht
verfügbare“**; was die Erkennung bei Kessel und Regler nicht gefunden hat, steht eingeklappt unter „Weitere Werte“.

**Was nötig ist** – im Code geprüft, mit Ersatz: der **Vorlauf** (Ist; Heizkurve, Wärmemodell, Vorschau – Ersatz nur
EMS-ESP direkt); **Brenner an** oder **Brennerleistung** – eines genügt; **Warmwasser lädt** oder
**Speichertemperatur** – eines genügt (eine Ladung erkennt HeatPilot auch am Anstieg der Speichertemperatur und hält
sie aus dem Heizmodell heraus; ohne Speichertemperatur gibt es kein Warmwasser-Modell); eine **Außentemperatur**
(Außenfühler, sonst der Außenfühler am Kessel als Entität in Home Assistant, sonst das Wetter); je Raum das
**Thermostat**; die **Warmwasser-Soll-Entität** nur bei „HeatPilot setzt“, der Schalter **„Desinfektion auslösen
(Schalter)“** nur bei „HeatPilot setzt“ mit Desinfektionstermin (seit 0.42, siehe
[Desinfektion auslösen](#desinfektion-legionellen)). Der **Rücklauf** ist optional – er dient
nur der Vorlauf-Auswertung und der Live-Anzeige. Liefert EMS-ESP einen Kessel- oder Regler-Wert direkt, fehlt er
nicht – außer beim **Außenfühler am Kessel**: Den Wert direkt von EMS-ESP zeigt die Seite nur an, als
Außentemperatur dient er nicht (dafür eine Entität wählen, einen Außenfühler oder das Wetter).

**Räume** zeigt je Raum Thermostat, Temperatur-, Feuchte- und Ventilsensor mit Status; geändert wird in der
Räume-Tabelle unter „Allgemein“ („in der Räume-Tabelle ändern“ springt hin), weil dort auch Name, Raum, Komfort,
Fußboden und Voreinstellung stehen. Ein leeres Sensorfeld nutzt HeatPilot nicht – der Sensor am Gerät steht dann als
Vorschlag dabei. **Personen** sind eine Liste mit Häkchen (nur Name und Zustand, keine Koordinaten),
**Benachrichtigungen** die Ziele mit „Testnachricht senden“.

**Gespeichert** wird mit „Speichern“ wie alle Einstellungen (`POST api/settings`) – es gibt keine zweite
Speicherstelle: Kessel- und Regler-Rollen als `overrides` (`rolle=entity_id`, leer = Erkennung), alles andere in den
bisherigen Feldern (`gas_sensor`, `weather_entity`, `outdoor_sensors`, `demand_sensor`, `power_sensors`,
`power_exclude`, `persons`, `vacation_entity`, `dhw_set_entity`, `disinfect_entity`, `notify_targets`). In den Karten
unter „Allgemein“
steht nur noch, was gilt, mit „ändern unter Entitäten“. Alte Einstellungen bleiben gültig; frühere Zuordnungen
`gas=…`/`weather=…` übernimmt HeatPilot beim Laden in Gaszähler bzw. Wetter (sie hatten Vorrang), Zuordnungen ohne
bekannte Rolle stehen beim Kessel zum Entfernen. Die Seite liest `GET api/entity_map` (nur lesen); ist das nicht
möglich, steht der Grund da, und Speichern lässt die Zuordnungen, wie sie sind.

## Alle Sensoren

**Live → Diagnose → Sensoren** listet, was HeatPilot liest: Bereich, Zweck,
Sensor bzw. EMS-ESP-Wert, Name in Home Assistant, aktueller Wert, Stand und
Zustand (ok, fehlt, veraltet, nicht gefunden). Mit dem Filter findet man schnell
alles zu einem Raum oder alle fehlenden Werte; optional werden auch die von
EMS-ESP gelieferten, aber nicht verwendeten Werte gezeigt.

## Gelernte Werte exportieren

**Live → Diagnose → Dateien → Gelernte Werte herunterladen** liefert eine JSON-Datei mit allem,
was HeatPilot gelernt hat – Haus, Räume, Warmwasser, Zeiten, Gewohnheiten – in
Klartext mit Gütemaß (Fehler, gekappte und verworfene Punkte, plausibel ja/nein),
dazu Lernkurve, alle Stundenwerte und die Messwerte der gewählten Tage (7, 14 oder
30) – im Spaltenformat und auf 5 min verdichtet (14 Tage etwa 1–3 MB), dazu die
erkannten Fremdwärme-Ereignisse je Raum (`fremdwaerme`: nur Zeiten und Kelvin) und die
gemerkten Änderungen der Ventil-Voreinstellung (`abgleich_aenderungen`: Raum, Stufen,
Vorlauf vorher). Damit lässt sich nachrechnen, ob ein Modell etwas falsch verstanden hat. Ohne
GPS-Koordinaten (nur Entfernungen) und ohne Zugangsdaten.

Ob das Warmwasser-Modell passt, zeigt auch die Grafik „Was Warmhalten kostet“:
Punkte sind das gemessene Auskühlen (je 1 K gemittelt), die Linie das Modell.

## Einstellungen sichern

Die Einstellungen liegen in `/data/settings.json` und bleiben bei Updates erhalten.
Zusätzlich legt HeatPilot bei jedem Speichern eine Kopie als
`heatpilot-einstellungen.json` im Add-on-Konfigurationsordner an
(`/addon_configs/<slug>/`, per Samba oder File-Editor erreichbar). Fehlen die
Einstellungen in `/data` einmal, holt HeatPilot sie beim Start von dort zurück.

## Diagnose und Fehlersuche

**Live → Diagnose** auf der HeatPilot-Seite:

- **Selbsttest** – laufend: fehlende oder veraltete Sensoren, unmögliche Werte
  (Vorlauf, Raum, Speicher), Gaszähler zählt nicht obwohl der Brenner lief,
  Aufzeichnungslücken, EMS-ESP nicht erreichbar, Programmfehler der letzten 24 h,
  Modelle ohne Empfehlung (mit Grund).
- **„Modelle – Einzelheiten“** (seit 0.42.1; darüber seit 0.58 die Karte „Modelle“):
  Hausmodell mit Gründen, Wärmeverlust ± Unsicherheit, Vorhersagefehler, Lernschritte und die Datenlage der
  Stundenwerte – gespeichert (mit Zeitraum und Lücken), mit Gas, Außen- und Innentemperatur (daraus lernt das
  Hausmodell), ohne Innentemperatur, ohne verwertbaren Gaswert, ohne Außentemperatur; je Raum und Ventil Zustand und
  Grund, Warmwasser, was zuletzt wiederhergestellt, neu gelernt oder übernommen wurde (siehe
  [Räume wechseln](#räume-wechseln-z-b-tado-cloud--homekit)).
- **Log** – alle Meldungen mit Zeit und Fehlerdetails, filterbar. Wichtige
  Zustandswechsel (Leitplanken, Urlaub, Ankunft, Vorheizplan, Heizfreigabe,
  Warmwasser-Empfehlung) werden protokolliert. „Ausführliches Log“ (Einstellungen →
  Erweitert) schreibt zusätzlich jeden Takt mit allen Messwerten.
- **Log-Datei** – rotierend (5 × 2 MB) unter `/addon_configs/<slug>/logs/heatpilot.log`,
  erreichbar über Samba oder den File-Editor.
- **Fehlerbericht herunterladen** (seit 0.59.2, oben in „Dateien“) – eine ZIP-Datei für ein Issue auf GitHub,
  **anonymisiert**: Raum-, Bereichs-, Personen- und Ortsnamen, Gerätenamen und Entitäten sind durch „Raum 1“,
  „Person 1“, „Ort 1“, `climate.raum_1`, `sensor.e1` … ersetzt, Koordinaten, EMS-ESP-Adresse und
  Benachrichtigungsziele fehlen; die Entitäten von EMS-ESP (`boiler_…`, `thermostat_…`) bleiben lesbar. Drin:
  `info.json` (Version, Kesselmodell aus der Geräteregistry, Thermostatstufe je Raum, die drei Schalter, Selbsttest),
  `langzeit_30_tage.json` (das Gelernte mit den Stundenwerten der letzten 30 Tage) und `kurzzeit_12h.csv` bzw.
  `kurzzeit_24h.csv` (die 30-s-Werte). Seit 0.68 **ohne Daten je Person** (Anwesenheit, Entfernung, Ankunft,
  Personenmodelle) – die vollen Daten darunter behalten sie, damit die Entwicklung alles sieht, was HeatPilot weiß.
  **„Fehler auf GitHub melden“** öffnet das vorbereitete Issue-Formular – vorher
  fragt HeatPilot nach: Auch anonymisiert zeigt der Bericht, besonders mit den 30-s-Werten, wann jemand zu Hause ist
  und schläft; das Formular verlangt dieselbe Bestätigung. Die folgenden Dateien stehen darunter eingeklappt unter
  **„Volle Daten – Achtung, sensible Daten“**: nicht anonymisiert, nur für die Entwicklung, nie öffentlich posten.
- **Diagnose-Datei herunterladen** – Log, Selbsttest, Einstellungen, erkannte
  Entitäten und deren Zustände, gelernte Modelle, Datenbank-Übersicht und die
  Messwerte der letzten 48 Stunden als JSON. **Ohne GPS-Koordinaten** (weder
  Personen noch Zuhause – nur Entfernungen) und ohne Zugangsdaten. Diese Datei
  kann zur Fehlersuche weitergegeben werden.
- **30-s-Werte herunterladen** (seit 0.49.2) – die Werte der Live-Ansicht alle 30 s als CSV, wahlweise 12, 24 oder
  48 h: Zeit, Vorlauf, Rücklauf, Soll, Brennerleistung, Flamme, Pumpe, Warmwasser, Brennerstarts, Anlagendruck und je
  Raum Ist, Soll, Ventil und Feuchte. HeatPilot hebt sie 48 h in der eigenen Datenbank auf (übersteht einen Neustart)
  und löscht Älteres laufend. Damit lässt sich etwa die umgewälzte Wassermenge aus Brennerleistung und Spreizung
  nachrechnen. Seit 0.50 steht in jeder Zeile auch, was HeatPilot für den Kessel entschieden hat (Spalten `hp_…`:
  Heizbetrieb, Ziel, Bedarf, Abnahme, Zuschlag, Boost, Starts je Stunde, mittlere Laufzeit, wartende und nicht
  hochkommende Räume, Grund und Entscheidung zum Vorlauf-Soll) – leer, solange „Kessel über HeatPilot“ aus ist.
  Seit 0.68 auch die **Anwesenheit**: `home_count` (Anwesende) und `vacation`, je Person (Spalten nach der Entität,
  z. B. `person.anna km`) zu Hause, Entfernung in km, erwartete Ankunft und deren Quelle (gps, gewohnheit) – ohne
  Koordinaten. Die Datei soll alles enthalten, was ein großes Modell zum Nachtrainieren der Modelle im Add-on braucht. Ohne
  Personen und GPS.

Kommt keine Verbindung zu Home Assistant zustande (z. B. „Anmeldung bei Home
Assistant fehlgeschlagen“), läuft die HeatPilot-Seite trotzdem: oben steht der
Grund, der Selbsttest zeigt ihn als Fehler, und das Add-on versucht es laufend
weiter. Im Log steht, ob ein Zugangsschlüssel angekommen ist (nur ja/nein und
Länge, nie der Schlüssel selbst).

## Zertifikat über Haus & Heizung

In der Übersicht gibt es ein **Zertifikat als PDF** zum Herunterladen und
Weitergeben, z. B. an den Heizungsbauer. Es ist rein sachlich, bezieht sich auf die
letzten 12 Monate Messung und zeigt, gemessen statt geschätzt:

- **Gebäude:** Wärmeverlust, Heizlast am kältesten Tag, Trägheit; mit Wohnfläche
  auch Heizlast je m² und nach 12 Monaten eine Energieklasse (A+ bis H, Skala wie
  im Energieausweis)
- **Heizkessel:** Nennleistung und kleinste Leistung gegen die Heizlast, Anteil der
  Heizstunden unter der kleinsten Leistung (dort muss der Kessel takten),
  Heizstarts pro Tag und Laufzeit je Start (ohne Heizlaufzeit-Zähler aus den
  Brennerläufen der letzten 30 Tage). Ist der Kessel deutlich zu groß oder laufen die
  Starts sehr kurz: **Mehrkosten durch Takten** als Schätzung in € pro Jahr – solange
  noch kein volles Jahr gemessen ist, für den bisher gemessenen Zeitraum, nicht hochgerechnet
  (Erfahrungswerte 5–15 % des Heizgases, ausdrücklich nicht gemessen). Ohne Heizstart-Zähler
  heißen die Starts „Brennerstarts (mit Warmwasser)“
- **Warmwasser & Verbrauch:** Warmhalteverlust des Speichers, Gasverbrauch und -kosten
- **Urteil** in Klartext und ein Stempel: „GEMESSEN“ oder „VORLÄUFIG“ (mit Grund),
  solange das Hausmodell unsicher ist, weniger als 500 Heizstunden oder noch kein
  Frost gemessen wurden, seit 0.42.1 auch, solange vielen Stunden die Innentemperatur fehlt
- **Grundlage** (seit 0.42.1): über welchen Zeitraum es rechnet, aus wie vielen Stunden und seit wann das Hausmodell
  gelernt hat und welche Stunden fehlen – mit Grund (ohne Innentemperatur, ohne verwertbaren Gaswert, ohne
  Außentemperatur, ohne Stundenwert) statt still weniger zu nehmen. In der Übersicht steht das unter dem Zertifikat
  („Grundlage: Stundenwerte … · Hausmodell aus … Stunden … seit …“, darunter „Nicht berücksichtigt:“ je Grund mit
  Zeitraum; fehlt vielen Stunden die Innentemperatur, mit dem Knopf „Hausdaten wiederherstellen“), in PDF und
  Druckansicht in einem Satz im Absatz „Grundlage“

**Gesamtbewertung:** Alles auf einen Nenner – je Bereich 0–100 Punkte, linear zwischen
„sehr gut“ und „schlecht“: Gebäude nach Energiekennwert (30 → 250 kWh/m²a) bzw. ohne
volles Jahr nach Heizlast je m² (40 → 150 W/m²), Kesselgröße nach Nennleistung/Heizlast
(1,3- → 4-fach), Takten nach Laufzeit je Heizstart (30 → 5 min), Warmwasser nach
Warmhalteverlust bei 55 °C (40 → 200 W). Gewichtet 40/25/20/15 % ergibt das die Punkte
und die HeatPilot-Klasse A+ (ab 90) bis H (unter 20). Sie gibt es erst, wenn das Gebäude
und mindestens zwei weitere Bereiche bewertet sind. Kein Energieausweis.

Neben dem PDF gibt es eine **Druckansicht**: dasselbe Zertifikat als Seite, über
„Drucken / als PDF sichern“ erzeugt das Gerät selbst ein PDF. Unter iOS ist das der
sichere Weg zum Weitergeben (z. B. an WhatsApp): Druckansicht → Drucken → Teilen.

Wohnfläche, Nennleistung und kleinste Leistung des Kessels stehen unter
**Einstellungen → Kessel** (0 = unbekannt bzw. Wert von EMS-ESP; meldet der Kessel keine kleinste Leistung,
steht dort seit 0.42 ein Vorschlag mit „übernehmen“, siehe [Kessel über HeatPilot](#kessel-über-heatpilot-weg-b)). Das
Zertifikat enthält keine Namen, Adressen oder Standortdaten. Es ist kein
Energieausweis nach GEG, die Werte sind nicht witterungsbereinigt.

## Live

Der Reiter **Live** zeigt, was Heizung und Haus gerade tun und in den letzten
3, 6, 12 oder 24 Stunden getan haben; er aktualisiert sich alle 30 Sekunden.

- **Anzeigen:** Brenner (an/aus, Leistung, heizt oder lädt Warmwasser, Taktsperre
  mit Restzeit), seit 0.46.5 eine eigene Kachel **Pumpe** (an/aus, Modulation in %, Nachlauf, falls der Kessel ihn
  meldet), Vorlauf mit Soll von Kessel und Regler, Rücklauf mit Spreizung,
  Kessel und Pumpe, Warmwasser, außen, innen Ø, Luftfeuchte Ø, Wärmebedarf laut Tado, wer zu Hause ist
- **Grafen:** Kesseltemperaturen (hinterlegt, wann der Brenner heizt bzw.
  Warmwasser lädt und – seit 0.46.5, hellgrün – wann die Pumpe lief), Brennerleistung und Pumpe, außen/innen, Warmwasser
- **Räume gemeinsam:** Raumtemperatur, Luftfeuchte und Heizbedarf aller Räume in je
  einem Grafen (Raum antippen = aus-/einblenden), dazu Gas je Stunde und
  Außentemperatur; aufklappbar je Raum einzeln mit Tado-Soll und Ventil

Die letzten 12 Stunden hält HeatPilot im Speicher (alle 30 s, mit EMS-ESP direkt
am genauesten); ältere Abschnitte und die Zeit vor einem Neustart kommen aus der
Datenbank (alle 5 min).

### Anwesenheit – deutet HeatPilot es richtig?

Unter den Grafen steht je gewählter Person:

- **Deutung jetzt:** zu Hause · *Heimfahrt erkannt* (lückenlos näher, mind. 30 min,
  15 km und 20 km/h – erst dann zählt eine Urlaubsrückkehr) · *kommt näher* ·
  *entfernt sich* · *steht* (bleibt in der Gegend, z. B. Arbeit) – mit Entfernung und
  erwarteter Ankunft samt Quelle (GPS-Annäherung oder Gewohnheit). Als
  GPS-Annäherung zählt nur, wer mindestens halb so schnell wie das gelernte
  Heimfahrt-Tempo näher kommt oder seit Beginn der Annäherung mindestens 2 km
  näher ist – GPS-Schwankung am Arbeitsplatz ergibt keine Ankunft.
- **Lange weg:** Wer länger als 72 h weg ist (oder länger als 24 h und dabei über
  300 km entfernt), gilt als verreist: Die Gewohnheit lernt das nicht, das Raster
  bleibt auf dem Stand bei der Abfahrt. Ausnahme regelmäßige Abwesenheit
  (Wochenpendler, Wochenendfahrt alle 1–2 Wochen): Begann vor 7 oder 14 Tagen zur
  selben Uhrzeit (±90 min) schon eine solche Abwesenheit, lernt die Gewohnheit
  normal weiter – ab der zweiten Wiederholung. Dauert sie über 12 h länger als
  damals, zählt sie doch als Reise. Meldet Home Assistant für eine Person
  „unknown“/„unavailable“, zählt sie weder als zu Hause noch als weg und wird in
  dieser Zeit nicht gelernt. Ist dann niemand sicher zu Hause, gilt die Anwesenheit
  des Haushalts als offen: Die Warmwasser-Empfehlung geht nicht auf „niemand zu
  Hause“, sondern hält das Minimum plus Reserve, und das Zapfprofil wird weder
  gesichert noch zurückgenommen.
- **Entfernung im Verlauf** (12 h bis 7 Tage, grün hinterlegt: zu Hause).
- **Gewohnheit (gelernt):** Wahrscheinlichkeit „zu Hause“ je Wochentag und Stunde.
  Ohne GPS-Annäherung gilt das erste Feld über 50 % als erwartete Ankunft, das sich um mindestens
  10 Prozentpunkte vom tiefsten Wert seit dem Verlassen abhebt (seit 0.48.6). Ist jemand weg zu einer Zeit, in der er
  laut Gewohnheit genauso oft zu Hause ist wie danach (z. B. jeden zweiten Mittwoch den ganzen Tag: 51 %), sagt die
  Gewohnheit nichts über eine Heimkehr – die Ankunft ist dann die nächste echte Heimkehr der Gewohnheit (etwa 17 Uhr),
  nicht „gleich nach der Fahrzeit“. Ist eine so erwartete Heimkehr 60 min vorbei und die Person nicht da (und nicht per
  GPS auf dem Heimweg), zählt der Rest dieses Blocks nicht mehr als Ankunft. Die Farbe steht
  in festen Stufen (10, 25, 40, 50, 65, 85 %), die Legende darunter nennt sie.
- **Heimkehr-Kontrolle:** bei jeder Ankunft (nach mind. 30 min Abwesenheit) hält
  HeatPilot fest, was es 60, 30 und 15 min vorher erwartet hatte. „+5′“ heißt 5 min zu
  spät erwartet, „−5′“ zu früh. Darüber der mittlere Fehler und wie oft er innerhalb
  von ±15 min lag. Die Kontrolle beginnt mit dem Update auf 0.21 (bzw. mit der
  Datenübernahme beim Neulernen).

Gespeichert werden nur Entfernungen zum Zuhause, nie Koordinaten.

## Die Woche im Blick

In der Übersicht zeigt eine Wärmekarte Montag bis Sonntag × Stunde, was im Haus
üblicherweise passiert – gemittelt über 4 Wochen bis 1 Jahr, ohne Urlaub, Zählersprünge
herausgerechnet. Rechts die Summe (Gas, Starts) bzw. das Mittel je Tag, darunter in
Klartext die Spitzen, der stärkste Wochentag und Wochenende gegen Werktage. Die Farbe hat
sieben gleichmäßige Stufen, auch im Dunkelmodus; die Legende unter dem Raster nennt Anfangs- und
Endwert. Felder mit zu wenig Daten haben nur einen Rahmen – nicht zu verwechseln mit „wenig“.

| Ansicht | Was sie zeigt |
| --- | --- |
| Gas gesamt / Heizen / Warmwasser | Verbrauch in kW (= kWh je Stunde); Warmwasser = Gas, während der Speicher lädt |
| Gas wetterbereinigt | Heizgas je Kelvin innen–außen (W/K); mit plausiblem Hausmodell: Heizgas im Verhältnis zum Bedarf laut Modell, mal Wärmeverlust (durchgehend nach Modell geheizt = genau der Wärmeverlust; Absenken macht es kleiner, Aufheizen größer) – ist ein Tag hier höher, braucht das Haus dann wirklich mehr und war nicht nur kälter |
| Abweichung vom Hausmodell | gemessen minus erwartet. Das Hausmodell kennt keine Wochentage; feste Muster (Kochen, Lüften, Aufheizen nach der Absenkung) sieht man hier |
| Brennerstarts | wann der Kessel am meisten taktet – nur Stunden mit aufgezeichneten Brennerdaten (ab dem ersten Brennerereignis, nicht ab dem angeforderten Historienfenster) |
| Jemand zu Hause | gemessener Anteil der Zeit |
| Tado-Soll | Median der eingeschalteten Räume (nur wirklich eingestellte Werte) |
| Tado aus | Anteil der Räume, die Tado auf „aus“ hat – so sieht HeatPilot den Zeitplan |
| Ventile | mittlere Öffnung, auch je Raum |
| Fremdwärme | Anteil der Stunde, in dem ein Raum ohne Heizkörper deutlich wärmer wurde – Kamin, Ofen, Gäste (siehe „Fremdwärme“ unten). Diese Stunden lernt weder Raum- noch Hausmodell |

## Empfehlungen für Tado

Tado lernt selbst und entscheidet, *wann* ein Raum Wärme zieht. Fängt Tado zu
spät an, kann HeatPilot das über den Kessel nicht retten – ein höherer Vorlauf
hilft nur begrenzt. Deshalb misst HeatPilot je Raum und schlägt im Feld
**„Empfehlungen für Tado“** (und `sensor.heatpilot_tado_empfehlungen`) konkrete
Änderungen vor. Eingestellt wird in der Tado-App – HeatPilot ändert nichts selbst.

| Was gemessen wird | Mögliche Empfehlung |
|---|---|
| Zeitplanblock: wie lange nach dem Soll-Anstieg ist der Raum warm? Öffnet das Ventil schon vorher (Frühstart je Raum erkannt)? Springt Tado aus „aus“ an kalten Tagen früher auf das Soll, liegt der Block beim spätesten Anstieg – je Wochentag, sobald jeder Wochentag mindestens zweimal vorkommt (Sa 07:30 und So 09:00 bleiben getrennt), vorher Werktage bzw. Wochenende gemeinsam. Kurze Aussetzer (bis 15 min „aus“) zählen nicht, bei jeder Abtastrate | „Frühstart für den Raum einschalten“ · „Frühstart aktiv, reicht nicht: Block X min vorziehen“ · „unnötig früh warm: Block später / Frühstart aus“ |
| Geofencing bei jeder echten Heimkehr: wie lange vorher hebt Tado das Soll an, wie warm ist der Raum bei Ankunft? | „bei Ankunft zu kalt: Vorheiz-Einstellung einen Schritt Richtung Komfort“ · „lange vor Ankunft warm: Richtung Eco“ |
| Soll bei Abwesenheit | „Abwesenheitstemperatur senken“ – nur, wenn der Raum laut Modell danach in ≤ 2 h wieder warm ist |
| Raum über Stunden unter Soll, Ventil voll offen | „Soll senken oder Heizkörper/Ventil prüfen“ (mit Hinweis, ob der Vorlauf am Limit war) |
| Hydraulischer Abgleich (siehe unten) | „bekommt zu wenig Heizwasser: Voreinstellung von 5 auf 6 stellen (Skala 1–6)“ · „bekommt mehr als nötig: von 4 auf 3“ – mit grober Erwartung für die anderen Räume und Nachmessen |

Leitplanken: erst ab 4 Beobachtungen in 3 Wochen, Urlaub zählt nicht, Schritte
höchstens 30 min bzw. ein Reglerschritt, keine widersprüchlichen Ratschläge
(z. B. nicht „Abwesenheit senken“, wenn der Raum bei Ankunft ohnehin zu kalt ist).
Jede Empfehlung zeigt ihren Beleg und seit wann sie besteht; was danach nicht mehr
auftritt, erscheint 14 Tage lang als „behoben“.

### Hydraulischer Abgleich mit gelernten Raummodellen

In einem gut abgeglichenen Haus brauchen alle Räume für ihre Wunschtemperatur
ungefähr dieselbe Vorlauftemperatur. Jedes Raummodell weiß, wie stark der
Heizkörper den Raum je Grad Vorlauf erwärmt und wie schnell der Raum auskühlt –
daraus folgt, welchen Vorlauf der Raum am kältesten Tag bräuchte.

- Braucht ein Raum **mindestens 8 K mehr** als der Median der Räume und steht sein
  Ventil dabei oft weit offen, bekommt er zu wenig Heizwasser: Voreinstellung
  (Durchflussbegrenzer am Ventilunterteil) **eine Stufe höher** – vorher entlüften
  und prüfen, ob der Heizkörper zugestellt ist. Bestimmt dieser Raum den Vorlauf
  fürs ganze Haus, sagt HeatPilot, wie viel niedriger der Vorlauf danach sein könnte.
- Braucht ein Raum **mindestens 10 K weniger** und öffnet im Mittel höchstens 40 %,
  zieht er beim Öffnen viel Wasser, das anderen fehlt: **eine Stufe zurück**. Das
  wird nur empfohlen, wenn es gleichzeitig einen unterversorgten Raum gibt.

Leitplanken: erst ab 3 Räumen mit plausiblem Modell und je mindestens 12 Stunden
Heizbetrieb; Fußbodenheizungen zählen nicht mit; immer nur eine Stufe, danach
misst HeatPilot 1–2 Wochen neu. Die Voreinstellung gehört zur Heizungsanlage –
Änderungen am besten mit dem Heizungsbauer bzw. Eigentümer abstimmen.

**Konkrete Stufen.** Trägst du unter Einstellungen → Räume die **Voreinstellung**
(aktuelle Zahl am Ventilunterteil) und die **Stufen** (Skala, z. B. 6 oder 8) ein,
sagt die Empfehlung genau, was zu tun ist: „Voreinstellung von 5 auf 6 stellen
(Skala 1–6)“. Steht der Raum schon auf dem Maximum („6/6 – mehr geht am Ventil
nicht“), nennt HeatPilot Alternativen: Heizkörper entlüften, den Raum mit dem
größten Überschuss eine Stufe zurück (er wird genannt), Pumpenstufe oder Vorlauf
prüfen. Bei Stufe 1 wird kein „zurück“ empfohlen. Stufen 0 heißt: kein
Voreinstellventil – dann geht es um die Rücklaufverschraubung (Viertelumdrehung,
am besten vom Fachbetrieb). Ohne Angabe bleibt es beim bisherigen „eine Stufe
höher/zurück“ mit dem Hinweis, die Stufe einzutragen. Bei mehreren Geräten in einem
Raum gilt der Wert des Schlüsselgeräts (Raumregler, sonst das erste); weitere Ventile
dürfen eigene Werte haben, die Empfehlung nennt dann das Gerät.

**Wirkung auf die anderen Räume (grobe Näherung).** Ein Voreinstellventil öffnet
etwa quadratisch mit der Stufe: Durchflussbeiwert `kv ∝ (p / p_max)²`. Der
Durchflussanteil eines Raums ist sein kv geteilt durch die Summe aller kv (die
Anteile ergeben zusammen 1 – was ein Raum mehr bekommt, fehlt den anderen). Die
Leistung eines Heizkörpers steigt bei sonst gleichen Bedingungen nur mit
`Durchfluss^0,3`, deshalb skaliert der nötige Vorlauf eines Raums mit
`(T_req − T_Raum) · (Anteil_alt / Anteil_neu)^0,3`. So entsteht der Satz in der
Empfehlung: „Erwartung (grob): Flur ≈ −5 K, Wohnzimmer ≈ +2 K, Küche ≈ +1,0 K
Vorlauf“ – der geänderte Raum braucht weniger, die anderen etwas mehr. In der
Oberfläche stehen die Werte zusätzlich als Chips je Raum. Räume ohne eingetragene
Stufe werden nicht simuliert; die Empfehlung nennt sie („Ohne Voreinstellung in den
Einstellungen: Bad – eintragen, dann rechnet HeatPilot auch deren Wirkung“). Es
ist eine Richtgröße, kein Messwert – Ventilkennlinie, Heizkörpergröße und
Rohrnetz kennt HeatPilot nicht.

**Nachmessen.** Änderst du die Voreinstellung in den Einstellungen (bekannte Stufe
→ andere bekannte Stufe), merkt sich HeatPilot Zeitpunkt, Stufen und den nötigen
Vorlauf des Raums laut Modell vor der Änderung (ohne Koordinaten; im Export als
`abgleich_aenderungen`). Danach gibt es 10 Tage lang keine neue Abgleich-Empfehlung
für diesen Raum, stattdessen eine Info-Karte „Voreinstellung geändert – HeatPilot
misst neu“. Ab 14 Tagen bis 60 Tage nach der Änderung steht in der Empfehlung
bzw. als eigene Info-Karte die Wirkung: „braucht jetzt 58 °C statt 64 °C Vorlauf am
kältesten Tag (Voreinstellung am 18.10. von 5 auf 6)“. Info-Karten gelten nie als
„behoben“. Das erste Eintragen einer Stufe (unbekannt → 5) ist keine Änderung.

## Zusatzdaten aus EMS-ESP

Liefert der Kessel sie (Diagnose → Alle Sensoren), nutzt HeatPilot außerdem:

- **Warmwasser-Ladezähler** – jede Ladung exakt statt nur über den Temperaturanstieg;
  ohne Ladesignal zählt auch die Speicherladepumpe.
- **Legionellenschaltung und Einmalladung** – werden geladen, aber nicht gelernt
  (Ladekosten und Zapfprofil bleiben sauber). Im Vergleich bekommt die Strategie diese Wärme
  genauso (verhindern kann sie sie nicht). Eine große Zapfung, die üblicherweise darauf folgt –
  die Wanne nach der Legionellenschaltung –, gilt als dadurch gedeckt: HeatPilot empfiehlt dafür
  nicht, vorher höher zu laden, und zählt sie nicht als „zu kalt“.
- **Einschalt-Hysterese** – daraus die Kachel „Nächste Ladung“.
- **Heizkurve des Reglers** (Auslegungstemperatur, Offset) – „Vorlauf gegen Takten“ nennt
  den Versuch als konkreten Wert am Regler.
- **Kesselstatus, Anlagendruck, Wartung, Fehlercodes** – in Live und Diagnose. Eine
  erledigte Wartung muss im Kessel bzw. in EMS-ESP zurückgesetzt werden, sonst bleibt die
  Meldung stehen.

Was der Kessel gar nicht kennt, steht unter „Alle Sensoren“ als „Kessel liefert nicht“ –
da lässt sich nichts freischalten.

## Heizen (Bedienung)

Der Reiter **Heizen** bedient Tado. Im Modus **Beobachten** nur auf Knopfdruck – HeatPilot
schaltet dann nie von selbst; bei **HeatPilot steuert** schreibt HeatPilot die Solltemperaturen
der Räume mit „macht mit“ selbst (siehe „Steuerung“), die Knöpfe gelten dann bis zum nächsten
Phasenwechsel.

- **Statuszeile** oben: „Beobachten“ oder „HeatPilot steuert · Nacht bis 04:30 · nächste
  Änderung 03:30 Vorheizen“, bei Pause „HeatPilot pausiert · bis 23:00“, dazu Warnungen
  (z. B. „Küche: 3 Schreibfehler in Folge – Thermostat prüfen“).
- **Oben kleine Kacheln** (wie in der Tado-App), jede öffnet einen Dialog:
  **Boost** – Räume antippen (oder „Alle“), Temperatur mit −/+ und Dauer (15 min bis 12 h)
  wählen, **Starten**. Bei Räumen der Tado-Cloud-Integration setzt das in Tado einen Timer
  (`tado.set_climate_timer`); nach Ablauf kehrt Tado selbst zum Zeitplan zurück, auch wenn das
  Add-on gerade neu startet. Bei allen anderen Räumen (HomeKit) läuft der Timer in HeatPilot:
  nach Ablauf stellt es den Wunschzustand (HeatPilot steuert) bzw. das Soll von vorher
  (Beobachten) wieder her – aber nur, wenn das Boost-Soll noch steht; wurde inzwischen von
  Hand gedreht, bleibt das stehen. Läuft ein Timer, zeigt die Kachel die Restzeit.
  **Zeitplan** – Tado-Cloud-Räume zurück in den Tado-Zeitplan (beendet laufende Timer sofort);
  andere Räume bekommen bei „HeatPilot steuert“ den Wunschzustand, bei „Beobachten“ erscheint
  der Hinweis, den Zeitplan in der Tado-App fortzusetzen. **Ausschalten** – alle Räume aus,
  Tado hält dann nur den Frostschutz. **Pause** (nur bei „HeatPilot steuert“) – 1 h, 2 h, 4 h
  oder bis morgen früh (06:00) schreibt HeatPilot nichts (außer dem Zurück eines Schritts von
  [Ventile führen](#ventile-führen) aufs Wunschsoll); „Weiter“ hebt die Pause auf.
- **Nur jetzt** (seit 0.62, unter den Kacheln, wirkt bei „HeatPilot steuert“): **Nacht**, **Anwesend** oder
  **Abwesend** für das ganze Haus – für Ausnahmen wie früh ins Bett, nachts um 3 Uhr zur Arbeit oder spät heimkommen.
  Alle Räume mit „macht mit“ bekommen die Nacht-, Komfort- bzw. Abwesenheitstemperatur, bis zum nächsten
  Schaltpunkt des normalen Plans (höchstens 24 h). Früher endet es, wenn der Plan von selbst so weit ist (abends
  beginnt die Nacht), bei „Abwesend“ jemand heimkommt bzw. bei „Anwesend“ alle weg sind. Danach gilt wieder der
  normale Plan; noch einmal auf den aktiven Knopf tippen beendet es sofort. Ein Boost geht vor. Gelernt wird weiter
  wie sonst. Für das Dashboard gibt es dafür die Karte `custom:heatpilot-mode-card` (HACS „HeatPilot Card“ ab 0.4);
  sie liest `sensor.heatpilot_modus` (auto, nacht, anwesend, abwesend; Attribut `until`) und bedient über den Befehl
  `override`.
- **Raumkacheln** (zwei nebeneinander, wie bei Tado): Feuchte, Ist-Temperatur groß, Name und
  Zustand. Beobachten: „Zeitplan · 21°“, „Eingestellt auf 22°“, „Timer“, „Frostschutz“.
  HeatPilot steuert: „HeatPilot: Nacht 18°“, „Vorheizen → 21°“, „manuell 22° bis 21:00“
  (jemand hat von Hand gedreht – seit 0.68 nicht, wenn das Thermostat nach dem Schreiben nur den Wert von vorher
  meldet, etwa weil es kurz nicht erreichbar war: dann schreibt HeatPilot einmal erneut), „erreicht ≈ 19,5°“ (Ziel laut Raummodell nicht erreichbar),
  „Pause · 21°“. Heizt der Raum gerade, ist die Kachel orange; aus = grau mit
  Ausschalt-Symbol. Antippen öffnet den Dialog mit Soll (−/+), Betriebsart, Ventil, Restzeit
  eines Timers, den Geräten des Raums und – bei „HeatPilot steuert“ – Phase, Wunschsoll,
  Grund und nächster Änderung; darunter das gelernte Ventilverhalten des Raums (seit 0.39, siehe
  [Ventilverhalten](#ventilverhalten)).
- **Protokoll** (aufklappbar unter den Kacheln): die letzten 20 Schreibvorgänge mit Zeit,
  Raum, Aktion, Wert und Grund – egal ob Regelschleife oder Knopfdruck.

Erlaubt sind nur drei Aufrufe (`climate.set_temperature`, `climate.set_hvac_mode`,
`tado.set_climate_timer`) und nur auf die Thermostate der in den Einstellungen gewählten
Räume, Temperaturen 5–25 °C. Raumauswahl, Temperatur und Dauer merkt sich das Gerät.

## Steuerung: Beobachten oder HeatPilot steuert

Unter **Einstellungen → Räume** steht der Hauptschalter. Es gibt genau zwei Stellungen,
keinen Zwischenmodus und keinen automatischen Rückfall:

- **Beobachten** (Standard): wie bisher – lernen, empfehlen, Tado nur auf Knopfdruck.
- **HeatPilot steuert**: HeatPilot schreibt die Solltemperaturen der Räume mit Häkchen
  **„macht mit“** selbst. Zurück zu Tado geht es nur über diesen Schalter; danach den
  Zeitplan in der Tado-App selbst fortsetzen (HeatPilot stellt ihn nicht wieder her). Fällt
  HeatPilot aus (Add-on gestoppt, Home Assistant neu gestartet), bleibt das zuletzt
  geschriebene Soll stehen.

Darunter steht seit 0.40 ein eigener Schalter **„Ventile führen“** (Standard aus): Mit ihm verschiebt HeatPilot
das Soll um höchstens einen Schritt des Thermostats, um das Ventil früh zu drosseln bzw. Räume gemeinsam anfordern zu
lassen – nur bei „HeatPilot steuert“, siehe [Ventile führen](#ventile-führen). Schaltest du auf „Beobachten“, nimmt
HeatPilot einen laufenden Schritt vorher einmal zurück aufs Wunschsoll; fällt es mitten in einem Schritt aus, bleibt
dieser stehen (höchstens ein Schritt neben dem Wunschsoll).

### Bereit für die Steuerung?

Über dem Schalter steht seit 0.36.1, ob HeatPilot steuern könnte und was es hindert – bei
„Beobachten“ als „wäre bereit“, bei „HeatPilot steuert“ als „läuft“ mit den Einschränkungen dahinter.
Die Ampel kennt drei Stufen:

- **bereit** (grün): alles da – „Bereit – 6 Räume schreibbar, 5 mit belastbarem Modell. Flur erreicht
  sein Soll nicht und wird akzeptiert.“
- **eingeschränkt** (gelb): steuern geht, aber ohne Teile der Intelligenz – „Läuft – eingeschränkt:
  Küche lernt noch (Vorheizen nach Pauschale).“
- **nicht bereit** (grau): Schreiben ginge ins Leere – „Nicht bereit – kein Raum macht mit.“

Aufgeklappt stehen die Hindernisse (rot), Einschränkungen (gelb), Hinweise (grau) und je Raum der
Zustand. Geprüft wird in dieser Reihenfolge:

1. **Beschreibbar** – je Raum mit „macht mit“ jedes Thermostat, auf das geschrieben würde (Raumregler,
   sonst alle Ventile der Gruppe): in Home Assistant vorhanden, nicht „nicht verfügbar“/„unbekannt“, kann
   heizen (Betriebsart `heat` oder `auto`) und hat ein einstellbares Soll. Meldet eine Plattform das
   nicht, wird durchgelassen. Kein Raum mit „macht mit“ oder keiner beschreibbar → nicht bereit; einzelne
   Räume nicht beschreibbar → Einschränkung („diese Räume folgen nicht“).
2. **Schreibfehler** – ein Raum mit drei Fehlschlägen in Folge („Küche: 3 Schreibfehler in Folge –
   Thermostat prüfen“) ist ein Hindernis, bis wieder geschrieben werden kann.
3. **Raummodell** – noch nicht plausibel („lernt“, mit dem ersten Grund): Vorheizen für diesen Raum
   pauschal 1 h (höchstens der Vorheizdeckel) statt nach seiner Aufheizzeit → Einschränkung.
4. **Soll erreichbar** – schafft ein Raum bei maximalem Vorlauf und der aktuellen Außentemperatur sein
   Soll um mehr als 0,5 K nicht, heißt er **„akzeptiert“**: „erreicht sein Soll nicht (höchstens 19,5 °C
   bei 3 °C außen, 17 °C bei −12 °C) – wird akzeptiert, kein Hindernis“. Das ist kein Fehler – solche
   Räume werden nicht hochgedreht (siehe unten). Erst wenn es mindestens zwei Räume und mehr als die
   Hälfte der beschreibbaren Räume mit „macht mit“ betrifft (auch die, die noch lernen), ist es eine
   Einschränkung („Vorlauf/Abgleich prüfen“, siehe Empfehlungen) – ein einzelner Raum wie der Flur nie.
   Schafft ein Raum es nur bei Auslegungstemperatur nicht, steht das als Zusatz beim Raum.
5. **Anwesenheit** – ohne Personen gibt es keine Abwesenheits-Absenkung (Hinweis); ist die Anwesenheit
   in der letzten Stunde meist unklar (Tracker ohne Zustand), bleibt es im Zweifel bei Komfort
   (Einschränkung).
6. **Nachtzeiten** – eine Zeile: gelernt aus dem Stromverbrauch und wirksam, nur Vorschlag oder ohne
   Leistungssensoren nach dem Wochenplan (Hinweis).
7. **Außentemperatur** – fehlt sie, rechnet das Vorheizen mit 5 °C (Einschränkung).
8. **Laufzeit** – weniger als 72 gelernte Stunden im Hausmodell: „Modelle sind jung“ (Hinweis).
9. **Fußboden-Grenze** (seit 0.39.1, Hinweis) – mit Fußbodenheizung die Grenze für den Vorlauf jetzt („Vorlauf-Grenze
   der Fußbodenheizung jetzt: Fußboden Bad (RTL-Ventil, fordert an): höchstens 50 °C“ bzw. „gerade keine
   Vorlauf-Grenze“) und für jeden Raum, dessen Anschluss noch fehlt, **„Anschluss wählen“** – bis dahin gilt seine
   Grenze immer, wie direkt am Kessel. Die Raumsteuerung schreibt keinen Vorlauf; die Stufe ändert sich dadurch nicht.

Räume ohne „macht mit“ stehen als „nicht dabei“ in der Liste, ebenso der Heizkreis von EMS-ESP, falls
er als Raum gewählt ist („Heizkreis von EMS-ESP – HeatPilot schreibt nicht an den Heizkreis“). Die Übersicht zeigt bei „Beobachten“ die
Stufe im Abschnitt „Status“ (Bereitschaft), der Heizen-Reiter nur die eine Zeile zum Raumbetrieb.

**Kessel über EMS-ESP** (zweiter Block, nur Anzeige): Bevor es je einen Kessel-Schalter gibt, zeigt
HeatPilot, ob es alle nötigen Steuerungen hätte. Dazu fragt es beim Start und dann stündlich je
Steuerung `GET /api/boiler/<kurzname>` ab (bei neueren EMS-ESP liegen die Warmwasserwerte unter
`/api/boiler/dhw/…`) – mit Wert, Bereich, Einheit und ob EMS-ESP sie schreiben ließe (`writeable`).

| Steuerung | EMS-ESP | |
|---|---|---|
| Vorlauf-Soll | `selflowtemp` | nötig |
| Warmwasser-Soll | `wwseltemp` / `dhw/seltemp` | nötig |
| Heizbetrieb | `heatingactivated` | nötig |
| Warmwasser-Betrieb | `wwactivated` / `dhw/activated` | nötig |
| Warmwasser-Einmalladung | `wwonetime` / `dhw/onetime` | nötig |
| Heizen abschalten | `heatingoff` | Info |
| Antipendelzeit | `burnminperiod` | Info (nur lesen) |
| Pumpennachlauf | `pumpdelay` | Info |
| Heiztemperatur (Obergrenze) | `heatingtemp` | Info |
| Desinfektion | `wwdisinfecting` / `dhw/disinfecting` | Info – nötig mit Desinfektionstermin |

„Alle 5 nötigen Steuerungen vorhanden und schreibbar – HeatPilot kann den Kessel übernehmen (Schalter „Kessel über
HeatPilot“, Block unten)“ bzw. „Es fehlen: Vorlauf-Soll (nicht schreibbar), …“. Ist ein
Desinfektionstermin eingestellt (seit 0.36.3), ist die Desinfektion die sechste nötige Steuerung –
„Desinfektion braucht WWK Desinfizieren (schreibbar)“, denn ohne Regler löst HeatPilot sie seit 0.42
darüber aus. Ohne
EMS-ESP-Adresse: „EMS-ESP nicht eingerichtet“. Schalter (Heizbetrieb, Einmalladung …) stehen als
„an“/„aus“, gleich in welchem Bool-Format EMS-ESP sie meldet. Antwortet EMS-ESP auf eine dieser
Abfragen nicht in 3 s, bricht die Runde ab und wird nach einer Stunde wiederholt – die Kesselwerte
alle 30 s laufen davon unberührt weiter. Auch diese Abfragen sind reine Lese-Anfragen; einen
Schreibpfad zu EMS-ESP gibt es weiterhin nicht, auch keinen vorbereiteten. Was HeatPilot damit täte,
steht in der Planung ([docs/planung-vorlauf-ems.md](https://github.com/TheInGoF/ems-heatpilot/blob/main/docs/planung-vorlauf-ems.md));
Vorlauf und Heizbetrieb schreibt HeatPilot seit 0.41 mit dem eigenen Schalter „Kessel über HeatPilot“ – über Home
Assistant, nicht über EMS-ESP (das Warmwasser-Soll seit 0.38, siehe unten). Der Kessel-Block ändert die
Stufe der Raumsteuerung nicht – die Tado-Steuerung braucht ihn nicht.

Seit 0.41 der Block **„Kessel über HeatPilot“** (Weg B, nur Anzeige, ändert die Stufe der Raumsteuerung nicht):
Entitäten des Vorlauf-Solls und des Heizbetriebs gefunden und tauglich, schreibbar laut EMS-ESP (`selflowtemp`,
`heatingactivated`), **Regler am Bus** (ja – das ist ein Hindernis, solange er dran ist), „Heizen abschalten“ an
(Hindernis), Vorlauf-Soll und Warmwasser-Soll auf derselben Entität (Hindernis), **Wächter** in Home Assistant (nicht
erkannt bzw. ausgeschaltet ist eine Einschränkung), Mindestleistung für die Abnahme (fehlt sie, ist die Abnahme-Regel
aus – eine Einschränkung, seit 0.42 mit dem Vorschlag aus dem Bereich der maximalen Brennerleistung),
letzte Prüfung, Pause, fremder Schreiber. Was dort ein Hindernis ist, verhindert auch das Schreiben. Siehe [Kessel über HeatPilot](#kessel-über-heatpilot-weg-b).

Seit 0.38 kommt der Block **„Warmwasser über HeatPilot“** dazu (Weg W, nur Anzeige, ändert die Stufe der
Raumsteuerung ebenfalls nicht): Entität des Speicher-Solls gefunden und tauglich (number mit °C, mit ihren
Grenzen; kein Heizkreis, kein anderer Kesselwert – ein Name ohne Warmwasser-Hinweis ist eine Einschränkung),
schreibbar laut EMS-ESP (`wwseltemp` bzw. `dhw/seltemp`), Schalterstand, letzte Prüfung (übernommen ja/nein),
pausiert (seit wann), Kessel-Soll unklar (Entität und EMS-ESP verschieden), fremder Schreiber zuletzt gesehen,
Minimum unter 50 °C ohne Desinfektionstermin – und der Hinweis „HA-Automation für das Speicher-Soll abschalten“,
solange in den letzten 24 h fremde Änderungen kamen. Was dort ein Hindernis ist, verhindert auch das Schreiben. Siehe
[Weg W](#warmwasser-über-heatpilot-weg-w). Seit 0.42 steht unter der Überschrift, wer die Desinfektion auslöst –
„Desinfektion: löst HeatPilot aus (So 17:30)“, „macht der Regler“, „Trockenlauf“ oder „aus“ – mit der letzten
Desinfektion und ihrem Ergebnis. Kann HeatPilot sie ohne Regler am Bus nicht auslösen (Schalter fehlt oder taugt nicht,
Weg W pausiert), ging die letzte schief, oder löst einen eingetragenen Termin niemand aus (kein Regler, Warmwasser-Soll
nicht „HeatPilot setzt“), ist das eine Einschränkung.

Seit 0.40 steht davor der Block **„Ventile führen“** (nur Anzeige, ändert die Stufe der Raumsteuerung nicht): je Raum
mit „macht mit“ **bereit** (Raum- und Ventilmodell belastbar, Schritt bis 0,5 K) oder **lernt** mit dem Grund
(„Ventilmodell lernt noch (erst 5 von 8 Soll-Sprüngen)“, „Raummodell lernt noch (…)“, „Schritt 1,0 K – zu grob zum
Führen“), dazu Schalterstand, laufende Eingriffe und die Regeln. Der Satz sagt, was gilt: „Läuft – führt 3 Räume
(Wohnzimmer, Bad und Kinderzimmer); Küche lernt noch.“, „Aus – 3 Räume wären bereit (…); bis zum Einschalten nur
Trockenlauf“, „Eingeschaltet, wirkt erst mit „HeatPilot steuert““ oder „Nicht bereit – noch kein Raum mit belastbarem
Raum- und Ventilmodell“. Siehe [Ventile führen](#ventile-führen).

### Räume und Geräte

- Je Raum: **„macht mit“** (nur dann wird geschrieben) und die **Komforttemperatur**
  (Startwert beim Anlegen: aktuelles Soll des Thermostats, sonst 21 °C; 5–25 °C, halbe Grad).
- Mehrere Thermostate in einem Zimmer: gleicher Name im Feld **Raum** = derselbe Raum.
  Gibt es einen **Raumregler** (Wandthermostat), misst und schreibt HeatPilot nur ihn; sonst
  gilt das Mittel aller Geräte als Raumtemperatur und geschrieben wird auf alle Ventile.
  Gelernt wird der Raum unter seinem Schlüsselgerät (Raumregler, sonst das erste Gerät);
  auch Voreinstellung und Stufen des Raums sind die des Schlüsselgeräts. Nach dem Speichern
  nennt die Oberfläche das Schlüsselgerät jeder Gruppe.
  Bestehende Einstellungen ohne „Raum“ bleiben wie bisher: jedes Thermostat ein Raum.
- Die Tado-Bridge über HomeKit (`homekit_controller`) kennt nur „aus“ und „heizen“, keinen
  Zeitplan und keinen Timer. Vor dem ersten Schreiben stellt HeatPilot deshalb die
  Betriebsart auf „heizen“. Räume der Tado-Cloud-Integration behalten Timer und Zeitplan.
- Der Vorlauf am Regler (EMS-ESP) bleibt außen vor: EMS-ESP wird weiterhin nur gelesen.
  Den Heizkreis von EMS-ESP (`climate.…_thermostat_hc1`) erkennt HeatPilot seit 0.36.3 am Gerät in
  Home Assistant: „EMS-ESP“ in Hersteller, Name oder Modell, Hersteller „proddy“ oder angebunden über
  das EMS-ESP-Gateway; nur wenn Home Assistant kein Gerät kennt, am Namen – und nur bei MQTT. Er steht
  in der Raum-Tabelle, weil dort alle climate-Entitäten stehen, wird aber nicht vorgeschlagen, und
  „macht mit“ ist gesperrt („Heizkreis von EMS-ESP – HeatPilot schreibt nicht an den Heizkreis“). Ein
  früher gespeichertes „macht mit“ gilt nicht (Warnung im Protokoll), und jeder Schreibversuch auf ihn –
  Knopfdruck im Reiter „Heizen“ oder Regelschleife – wird hart abgewiesen. Im Reiter „Heizen“ steht er nur
  zur Anzeige („nur Anzeige“): −/+ und Betriebsart sind gesperrt, Boost, „Zeitplan“ und „Ausschalten“ lassen
  ihn aus. Liegt er mit einem Ventil in einem Raum, macht der ganze Raum nicht mit (in der Raum-Tabelle ist
  „macht mit“ dann für alle Geräte dieses Raums gesperrt; das gespeicherte „macht mit“ der Ventile bleibt und
  gilt wieder, sobald der Heizkreis nicht mehr in diesem Raum ist). Ein Boost auf den Heizkreis, den eine
  frühere Version noch gemerkt hat, wird beim Start verworfen – zurückstellen darf HeatPilot ihn nicht; der
  Reiter „Heizen“ sagt, auf welchen Wert das Regler-Soll von Hand zurück muss, bis es dort steht. Tado-Zonen
  und HomeKit-Ventile, die ebenfalls „…_thermostat“ heißen, bleiben beschreibbar – es zählt das Gerät, nicht
  der Name.

### Wunschzustand (jeder Takt, nur bei „HeatPilot steuert“)

Für jeden Raum mit „macht mit“ bestimmt HeatPilot eine Phase und das dazugehörige Soll:

1. **Urlaub** → Abwesenheitstemperatur; vor der erwarteten Rückkehr (siehe „Urlaub“)
   rechtzeitig vorheizen auf Komfort.
2. **Nacht** laut Wochenplan → Nachttemperatur (Standard 18 °C). Je Wochentag gilt ein Beginn am
   Abend dieses Tages und ein Ende am Morgen dieses Tages; Standard: Beginn So–Do 21:00, Fr und
   Sa 22:00; Ende Mo–Fr 04:30, Sa und So 06:30. Vor dem Nachtende wird so früh **vorgeheizt**,
   wie der Raum laut Raummodell braucht, mindestens 15 min, höchstens der **Vorheizdeckel** (Standard 3 h). Seit
   0.46.5 **sanft**: so früh, dass der **niedrigste Vorlauf ab 35 °C** reicht, der innerhalb des Deckels ans Ziel
   kommt – statt kurz mit dem höchsten Vorlauf (morgens 60 °C, danach wieder 35 °C). Schafft es auch der höchste
   Vorlauf nicht im Deckel, beginnt es mit dem Deckel. Ein begonnenes Vorheizen **bleibt bis zum Ziel**, auch wenn
   der Raum schneller warm wird als geplant – sonst sprängen Soll und Heizbetrieb zwischen Vorheizen und Nacht hin
   und her. Ohne plausibles Raummodell pauschal 1 h. Folgt nach
   der Nacht Abwesenheit, wird nicht vorgeheizt. Seit 0.37 rechnet die Aufheizzeit mit der
   Außentemperatur **zum Zielzeitpunkt** (Nachtende, Ankunft, Rückkehr aus dem Urlaub) laut
   Vorhersage – höchstens 90 min daneben –, sonst wie bisher mit der aktuellen: Wird es bis zum Morgen
   kälter, beginnt das Vorheizen früher.
3. **Niemand zu Hause** (alle gewählten Personen sicher weg) → seit 0.49 **nach Entfernung** wie bei Tado: je 10 km
   bis zur nächsten Person (GPS) **„Abwesend: je 10 km weniger“** Prozent unter Komfort (Standard 10 %), nie unter
   der Abwesenheitstemperatur. Bei 21 °C Komfort und 16 °C Abwesenheit: 5 km → 20 °C, 10 km → 19 °C, 14 km → 18 °C,
   ab 24 km 16 °C. Die Entfernung zählt erst nach 2 km Änderung neu – GPS-Schwankung lässt das Soll nicht springen.
   Ohne Entfernung (kein GPS) oder mit 0 % gilt gleich die Abwesenheitstemperatur. Liegt die erwartete Ankunft
   (GPS-Heimfahrt oder Gewohnheit) innerhalb der Aufheizzeit, wird auf Komfort vorgeheizt – seit 0.49 nur so früh, dass
   der Raum zur Ankunft **„Zur Ankunft bis zu … K unter Komfort“** erreicht (Standard 1 K: 20 statt 21 °C). Ein kaum
   ausgekühlter Raum heizt also erst kurz vorher (mindestens 15 min), nur ein stark ausgekühlter braucht die
   Vorhersage wirklich. Dasselbe gilt für die Rückkehr aus dem Urlaub. Ist die Ankunft offen (Gewohnheit lernt noch)
   oder die Anwesenheit unbekannt (Tracker meldet nichts), bleibt es bei Komfort – im Zweifel warm.
4. Sonst **Komfort**.

Feste Grenzen: nie unter der kleineren von Abwesenheits- und Nachttemperatur, nie über der
Komforttemperatur des Raums (außer Boost auf Knopfdruck), immer 5–25 °C im 0,5-°C-Raster.

### Räume, die das Ziel nicht erreichen

Räume mit zu kleinem Heizkörper (Ventil dauernd offen, Temperatur bleibt darunter) werden
akzeptiert: Die Vorheizzeit ist gedeckelt, es wird nichts hochgedreht und der Raum nicht
„aus Angst“ dauerhaft auf Komfort gehalten. Im Heizen-Reiter steht „erreicht ≈ 19,5°“ – die
Temperatur, die der Raum laut Modell bei maximalem Vorlauf schafft. In „Bereit für die Steuerung?“
heißen solche Räume „akzeptiert“ – sie sind kein Hindernis.

### Schreiben, Hand, Pause, Fehler

- Geschrieben wird **nur beim Wechsel** des Wunsch-Solls (Phasenwechsel), nicht alle 5 Minuten,
  und nur über die drei erlaubten Dienste auf die gewählten Räume. Bei „Beobachten“ oder ohne
  „macht mit“ schreibt HeatPilot nie von selbst – das ist im Code hart abgesichert und getestet.
- **Von Hand** gedreht (Soll weicht vom zuletzt geschriebenen ab, egal ob am Ventil, in der
  Tado-App oder im Heizen-Reiter, auch „aus“): HeatPilot lässt es stehen bis zum nächsten
  Phasenwechsel („manuell bis …“) und schreibt dann den Wunschzustand – auch, wenn der sich beim
  Wechsel nicht ändert (Vorheizen → Komfort). Ob ein Soll wirklich angekommen ist, prüft HeatPilot
  nicht nach; meldet Home Assistant danach etwas anderes, gilt das als Handänderung. Seit 0.40 nicht,
  solange Home Assistant bis zu zwei Takte nach dem Schreiben noch den Wert von vorher meldet (die
  Meldung kommt verzögert), und nicht, wenn dort steht, was HeatPilot zuletzt schreiben wollte (etwa
  wenn in einer Gruppe ein Ventil das Soll annahm und ein anderes nicht – nach der Wartezeit bekommen
  beide den Wunschzustand). Als verschieden gelten Sollwerte ab 0,1 K Unterschied.
- **Boost** läuft immer zu Ende, HeatPilot greift währenddessen nicht ein; danach gilt wieder der
  Wunschzustand.
- **Pause** (Heizen → Pause, bis 48 h) hält alle Schreibvorgänge an; der Schalter bleibt dabei
  auf „HeatPilot steuert“. Ein Boost, der in der Pause abläuft, endet mit dem Soll von vorher.
  Ausgenommen ist seit 0.40 das Zurück eines Schritts von [Ventile führen](#ventile-führen): Der Knopf
  „Pause“ nimmt laufende Schritte vorher einmal zurück aufs Wunschsoll, und endet ein Schritt in der
  Pause, geht sein Zurück trotzdem hinaus.
- **Fehler** beim Schreiben werden protokolliert und nach 15 min erneut versucht; ab drei
  Fehlschlägen in Folge steht eine Warnung in Übersicht → Status – seit 0.38 auch als Meldung an die
  [Benachrichtigungsziele](#benachrichtigungen) und im Problem-Sensor (einmal je Ereignis; der erste Erfolg
  danach meldet „wieder in Ordnung“).
- **Protokoll:** jeder Schreibvorgang (Zeit, Raum, Aktion, Wert, Grund; seit 0.38 auch die Zeilen des
  Warmwasser-Solls, seit 0.40 die Schritte der Ventilführung mit „Ventil: …“) wird 90 Tage gespeichert
  – die letzten 20 unter Live → Diagnose → Protokoll, 200 in der Diagnose-Datei, alle im Export der gelernten
  Werte (`steuerung`), ohne Koordinaten.
- Der Zustand der Regelschleife überlebt einen Neustart; beim Zurückschalten auf „Beobachten“
  wird er gelöscht.

### Lernen bei „HeatPilot steuert“

Nachtzeiten und Temperaturen aus Tado zu lernen wäre dann zirkulär – HeatPilot schreibt sie
ja selbst. Deshalb gilt der Wochenplan, „gelernt“ entfällt, und die Übersicht zeigt in der
Zeitplan-Karte den Wochenplan; auch gespeichert wird nichts, und nach dem Zurückschalten auf
„Beobachten“ zählen nur Nächte, in denen wieder Tado gesteuert hat. Raum- und Hausmodell lernen
unverändert weiter; die
Ersparnis-Schätzung rechnet weiter mit Abwesenheits-/Nachttemperatur und dem Mo–Fr-Mittel des
Plans.

### Nachtzeiten aus dem Stromverbrauch

Wann das Haus schlafen geht, verrät der Stromzähler: Abends fällt der Verbrauch auf das Nachtniveau
(Standby), morgens steigt er wieder (Kaffeemaschine, Licht, Bad). Seit 0.36 lernt HeatPilot daraus
den Wochenplan der Nachtabsenkung – als Vorschlag oder, mit dem Häkchen, als wirksame Zeiten.
Die Sensoren wählst du unter Einstellungen → Haus → Gewohnheiten erkennen; das Häkchen steht unter
Einstellungen → Räume, unter dem Wochenplan (dort auch, welche Sensoren gewählt sind).

**Sensor-Eimer** (seit 0.63): Unter „Aktivität (Lampen, Gruppen, Bewegungsmelder …)“ lassen sich beliebige Entitäten
hineinwerfen – Lampen, Schalter, Bewegungs- und Türmelder, Mediaplayer. Jede zählt als „wach“, solange sie an ist (an,
offen, spielt, Bewegung erkannt); HeatPilot speichert je Messpunkt, wie viele an sind. Ein 10-min-Abschnitt gilt als
wach, wenn die Hausleistung über der Schwelle liegt **oder** eine Entität an ist; ohne Leistungssensor zählt der Eimer
allein (dann muss abends etwas an sein, sonst „zu wenig Unterschied“). Die Nacht wird als zusammenhängender Block
gesucht: Eine kurze, einzelne Aktivität mitten in der Nacht (Licht im Bad um 2 Uhr) ist eine Unterbrechung, kein
Aufwachen; Beginn und Ende setzt, was länger anhält (2 von 3 Abschnitten) oder was mehrere bestätigen (zwei Entitäten
bzw. Leistung und Eimer zugleich). So markieren die Lampen grob Beginn (die letzte geht aus) und Ende (die erste geht
an). Werktage und Wochenende lernt HeatPilot getrennt – sogar je Wochentag. Gelernt wird die **Aufwachzeit**; den
Heizstart rechnet HeatPilot je Raum selbst zurück (Vorheizen). Unter dem Wochenplan steht z. B. **„Aufwachen werktags
04:50, sehr sicher · Wochenende 08:10, sicher“**; je Tag „sehr sicher“ (ab 5 Nächten mit höchstens 30 min Streuung),
„sicher“ (belastbar) oder „unsicher“. Der Eimer wirkt ab den Nächten nach dem Speichern (die Historie von Lampen liest
HeatPilot nicht nach).

**Was gelernt ist** (seit 0.67): Übersicht → Gewohnheiten zeigt je Wochentag und Stunde **schlafend** (die Nacht laut
gelernten Zeiten, wo sie für den Tag belastbar sind, sonst laut Wochenplan), **abwesend** (laut Gewohnheit der Personen
ist wahrscheinlich niemand zu Hause) und sonst **wach**; dazu je Tag „gelernt“ oder „eingestellt“ und wie sicher die
Aufwachzeit ist. Gruppen (`group.…`) lassen sich seit 0.67 ebenfalls in den Eimer werfen.

- **Leistungssensoren (Haus, ggf. je Phase):** ein oder mehrere Sensoren in W oder kW; die Summe
  ist die Hausleistung, bei drei Phasen also alle drei eintragen. Gemeint ist der **Verbrauch** des
  Hauses, nicht der Netzbezug einer PV-Anlage (der wird tagsüber negativ und zählt dann nicht).
  Fehlt eine Phase (Sensor „nicht verfügbar“), zählt der Messpunkt nicht – eine halbe Summe wäre
  falsch.
- **Nicht zählen:** Leistungssensoren von Verbrauchern, die nachts laufen (Waschmaschine,
  Spülmaschine, Trockner, Wallbox). Sie werden von der Hausleistung abgezogen. Ohne eigenen Sensor
  stört ein gelegentlicher Nachtlauf wenig, denn der Wochenplan ist der Median von sechs Wochen.
- **Je Nacht** (18:00 bis 12:00 des Folgetags, Ortszeit) bestimmt HeatPilot das Nachtniveau
  (00–04 Uhr), das Abendniveau (19–21 Uhr) und daraus eine Schwelle; sie liegt über dem normalen
  Flackern der Nacht (Kühlschrank, Gefrierschrank). Schlafbeginn ist die erste halbe Stunde unter der
  Schwelle, nach der es bis 3 Uhr überwiegend ruhig bleibt – sonst war noch jemand wach. Aufstehen
  ist der erste Anstieg darüber ab 3:30, frühestens 4 h nach dem Schlafbeginn; ohne Anstieg bis
  11 Uhr zählt die Nacht nur mit ihrem Beginn.
- **Ausgelassen** werden Nächte im Urlaub, wenn alle lange weg sind oder um 22 Uhr niemand zu Hause
  ist („nicht zu Hause“), Nächte mit Lücken in der Hausleistung („keine Daten“) und Nächte ohne
  erkennbaren Abend („zu wenig Unterschied“ – kein Abendverbrauch, oder der Sensor misst nur einen
  Teil des Hauses).
- **Wochenplan:** Beginn aus den Nächten, deren Abend der Wochentag ist, Ende aus denen, deren
  Morgen er ist – wie im Wochenplan selbst (Nacht Fr→Sa: Fr „ab“, Sa „bis“). Median der letzten
  sechs Wochen, auf 5 min gerundet. **Belastbar** ist eine Hälfte ab drei Nächten, wenn die Streuung
  (Quartilsabstand) höchstens 90 min beträgt und die Zeit plausibel liegt (Beginn 19:00–01:30, Ende
  03:30–09:30). Ein Beginn nach Mitternacht (z. B. Sa 00:30) zählt zum Abend davor und gilt am
  Folgetag – bis dahin bleibt es Komfort. Die Tabelle zeigt je Tag „gelernt ab“ / „gelernt bis“ mit
  Zahl der Nächte und Streuung, darunter, wie viele Nächte warum ausgelassen wurden. Ein Messwert gilt
  bis zum nächsten Messpunkt (Abtastintervall bis 15 min geht). Nach einem Wechsel der
  Leistungssensoren verwirft HeatPilot die alten Werte und liest die Historie neu ein.
- **Wann die Werte gelten:** nur mit dem Häkchen **„Gelernte Zeiten verwenden“**, und je Hälfte nur,
  wo etwas Belastbares vorliegt – sonst gilt der eingestellte Eintrag des Wochenplans (Rückfall,
  auch wenn die Sensoren einmal ausfallen). Übersicht und Einstellungen zeigen die wirksame Zeit mit
  Herkunft („gelernt (Strom)“ oder „eingestellt“). Bei „Beobachten“ wird gelernt und angezeigt,
  geändert wird nichts; an den Schreibregeln ändert sich nichts.
- Gerechnet wird stündlich, jede Nacht genau einmal, sobald ihr Fenster vorbei ist. Nach dem ersten
  Start und nach einer Änderung der Sensoren liest HeatPilot die Historie neu ein, damit sofort
  Nächte da sind. Die Hausleistung wird wie die anderen Messwerte gespeichert (`power_w`,
  `power_ex_w`) und steht im Export; „Lernstand neu berechnen“ rechnet die Nächte mit neu.

Später: Soll nach Ventilstellung – jetzt bewusst nicht enthalten.

## Vorschau (Trockenlauf)

Bevor HeatPilot je etwas am Kessel setzt, zeigt es seit 0.37, **was es setzen würde** – für die nächsten 24 h,
je Stunde. **Trockenlauf – die Vorschau schreibt nichts an den Kessel:** EMS-ESP bleibt nur lesend; Vorlauf und
Heizbetrieb schreibt HeatPilot nur mit dem eigenen Schalter „Kessel über HeatPilot“ (seit 0.41, über Home Assistant). Einzige Ausnahme seit 0.38 ist das Warmwasser-Soll mit eigenem
Schalter ([Weg W](#warmwasser-über-heatpilot-weg-w)): Die Vorschau rechnet Startwert und Schreibvorgänge des
Warmwassers nach derselben Regel und nennt sie bei „HeatPilot setzt“ „schreibt“ statt „würde schreiben“. Seit 0.41
ebenso Vorlauf und Heizbetrieb mit dem Schalter [Kessel über HeatPilot](#kessel-über-heatpilot-weg-b): Startwert wie im
Schritt, „schreibt“ bei „HeatPilot setzt“ ohne Pause und Sperre (Regler am Bus) – die Überschrift heißt dann „Vorschau –
Kessel über HeatPilot schreibt“; Abnahme-Untergrenze, Zuschlag und Takt-Boost rechnet HeatPilot live dazu, die Vorschau zeigt den
Plan aus dem Bedarf. Die
Vorschau ist ein Vorschlag zum Vergleichen; wann und ob umgestellt wird, entscheidest
du. Zu finden in der Übersicht, Abschnitt „Vorschau (Trockenlauf)“.

**Nächste 12 Stunden** (seit 0.46.1, seit 0.46.3 als Zeitstrahl): Kurz steht derselbe Plan auch in Übersicht → Warmwasser
(unter den Kacheln) und Übersicht → Kessel (oben) – ab jetzt 12 h in Abschnitten mit Uhrzeit von–bis, Art und Wert.
Warmwasser: Nacht, zu Hause, niemand zu Hause, Urlaub, heizt auf, Zapfung erwartet, Desinfektion mit „Soll 50 °C“ und
klein „Speicher 45–50 °C“ bzw. „darf bis 40 °C fallen“. Kessel: Vorlauf, Vorheizen, Heizung aus, zeitweise aus
(„Vorlauf 38–42 °C“ bzw. „aus“), seit 0.46.5 ohne Bedarf meist „nur Pumpe“ („Pumpe“: Heizbetrieb an, 20 °C). Gleiche Art und Wert (±1 K) ist ein Abschnitt; kürzere als 30 min zählen zum Nachbarn,
An/Aus im Minutentakt wird „zeitweise aus“. Der erste Abschnitt beginnt jetzt, alle Uhrzeiten in Ortszeit von Home
Assistant (auch wenn der Browser in einer anderen Zeitzone läuft) – dieselben wie in Tooltip und 24-h-Vorschau.
Überfahren, Antippen oder Tab zeigt die Einzelheiten mit den Gründen. Breit (ab 600 px) waagrecht mit Stundenmarken,
schmal als Liste. Die Karten nutzen dieselbe Abfrage wie die Vorschau – ist noch keine da, steht dort nur ein kurzer Satz.

Gerechnet wird in 5-min-Schritten von jetzt bis zum Ende der 24. vollen Stunde:

- **Außentemperatur:** der Vorhersagepunkt der Wetter-Entität (höchstens 90 min daneben), an den
  Außenfühler angeglichen: Jetzt gilt der Fühler (gegen ihn sind die Raummodelle gelernt, mit ihm rechnet die
  Steuerung), der Unterschied zur Vorhersage klingt über 6 h ab. Ohne Vorhersagepunkt die aktuelle – je
  Stunde steht dabei, woher sie kommt, und wie weit Fühler und Vorhersage gerade auseinanderliegen.
- **Anwesenheit:** Wer jetzt zu Hause ist, sagt Home Assistant (ein Tracker ohne Wert heißt „unbekannt“, wie
  in der Steuerung). Wer zu Hause ist, bleibt es bis zur nächsten gewohnten Abfahrt – wer gegen die
  Gewohnheit da ist (Homeoffice, Feiertag), also bis zur nächsten regulären; wer weg ist, bis zur
  erwarteten Ankunft (GPS-Heimfahrt bzw. Gewohnheit), danach gilt wieder die Gewohnheit. Wo die Gewohnheit
  noch lernt, ist die Ankunft offen (dann Komfort, wie in der Steuerung); Urlaub bis zur erwarteten Rückkehr.
- **Raum-Soll:** Räume mit „macht mit“ bei „HeatPilot steuert“ bekommen den Wunschzustand der Steuerung
  (Komfort, Nacht, Abwesenheit, Vorheizen – siehe oben). Für alle anderen Räume (und bei „Beobachten“) das,
  was Tado voraussichtlich will, vereinfacht: tagsüber das heutige Soll, nachts die Nachttemperatur, bei
  Abwesenheit die Abwesenheitstemperatur (gelernt bzw. eingestellt). Nachts oder bei Abwesenheit abgefragt,
  gilt als Tages-Soll das tagsüber gemessene der letzten 48 h (Median, ohne Nacht, Abwesenheit und Urlaub),
  ohne Messung der Komfortwert des Raums. Ein Thermostat, das auch tagsüber auf „aus“ steht, bleibt aus.
- **Raumtemperatur:** läuft nach dem Raummodell mit – ohne Wärme kühlt der Raum aus, darunter heizt er mit
  dem Vorlauf auf, am Soll hält ihn das Thermostat (±0,3 K). Daraus folgen die Vorheizdauer und ob ein Raum
  gerade Wärme braucht. Räume, die nicht für den Vorlauf zählen (Fußbodenheizung, Raummodell noch nicht
  plausibel), hält ihr Thermostat am Soll, solange der Heizbetrieb nicht sicher aus ist; ohne Raummodell
  bleibt ihr Messwert stehen.
- **Vorlauf-Soll:** der höchste nötige Vorlauf über die Räume mit plausiblem Raummodell (ohne
  Fußbodenheizung): zum Halten der Vorlauf, mit dem der Raum sein Soll dauerhaft hält; beim **Vorheizen**
  der niedrigste Vorlauf (1 K genau), mit dem der Raum ab dem Beginn des Vorheizens rechtzeitig warm wird.
  Den Beginn bestimmt die Steuerung (seit 0.46.5 sanft: so früh, dass genau dieser niedrigste Vorlauf ab 35 °C im
  Deckel „Vorheizen höchstens“ reicht – vorher die Aufheizzeit bei maximalem Vorlauf, meist also der maximale).
  Sonst beim Aufheizen 5 K mehr. Immer zwischen 35 °C und dem maximalen Vorlauf, auf 1 K gerundet. **35 °C ist
  die Untergrenze, wenn geheizt wird:** Halten alle Räume nur ihr Soll und braucht keiner mindestens 35 °C, lohnt
  Heizen nicht („nötiger Vorlauf unter 35 °C“); **heizt ein Raum auf oder vor** (mehr als 0,3 K unter dem Soll,
  z. B. nach einem Boost), gilt seit 0.46.5 mindestens 35 °C statt „aus“. **Braucht kein Raum Wärme** (alle
  am oder über dem Soll, oder Fremdwärme reicht) – typisch zu Beginn der Nacht –, steht dort seit 0.46.5
  „… – Pumpe läuft, Vorlauf-Soll 20 °C“ (der Heizbetrieb bleibt an, siehe [Kessel über
  HeatPilot](#kessel-über-heatpilot-weg-b)), bei der Einstellung „Heizbetrieb ganz aus“ wie bisher „Heizbetrieb aus“. Das gilt auch für Räume, die den Vorlauf nicht bestimmen: Fußbodenräume mit
  plausiblem Modell zählen wie die übrigen, Räume ohne plausibles Modell nach dem Messwert – mehr als 0,3 K
  unter dem Soll oder ohne Temperatur hält der Heizbetrieb an („Bad unter Soll (ohne Raummodell) –
  Heizbetrieb bleibt an“, mindestens 35 °C). Ohne plausibles Raummodell gibt es kein Vorlauf-Soll, mit Grund.
- **Fußboden-Grenze (seit 0.39.1):** Das Vorlauf-Soll liegt nie über dem höchsten Vorlauf der Fußbodenheizungen,
  – direkt am Kessel (und Anschluss unbekannt) und seit 0.46.5 auch mit RTL-Ventil in jeder Stunde, mit Mischer
  nie. Ob ein RTL-Raum Wärme anfordert, sagt nur noch der Grund: jetzt nach den Messwerten, genau wie „Bereit für
  die Steuerung?“ (0,1 K unter dem Soll, Ventil über 5 %, „heizt“), danach in den Stunden, in denen der Raum laut
  Vorschau Wärme braucht (ohne plausibles Raummodell die von jetzt, solange sein Soll wie jetzt ist). Das gilt
  auch beim Vorheizen und beim Aufheizen. Die
  Stunde sagt dann „… – begrenzt durch Fußboden Bad (RTL-Ventil, fordert nicht an): höchstens 50 °C“; „Womit gerechnet
  wurde“ nennt die Fußboden-Räume mit ihrer Regel. Liegt die Grenze unter 35 °C, gilt sie, sobald ein Raum Wärme
  braucht – sonst käme der Vorlauf nie auf die Untergrenze. Siehe [Fußbodenheizung je Raum](#fußbodenheizung-je-raum).
- **Warmwasser-Soll:** die Warmwasser-Empfehlung für jeden Zeitpunkt (Komfort plus Reserve vor erwarteten
  Zapfungen, sonst das Minimum, niemand zu Hause bzw. Ankunft), mit dem Desinfektionstermin aus den
  Einstellungen; auf 1 K gerundet. Die Stunde des Termins ist markiert.
- **Schreibvorgänge, die es gäbe:** nach den abgesprochenen Regeln – nur bei Änderung (Vorlauf und
  Warmwasser ab 1 K, Heizbetrieb an/aus), danach 5 min Haltezeit. Der erste Vergleich ist mit dem, was
  heute am Kessel steht. Markiert wird je Stunde, wann und was geschrieben würde, dazu die Summe über 24 h.

Je Stunde gilt der Wert, der den größten Teil der Stunde gilt; der Graf zeigt die Änderungen auf 5 min genau.
Gerechnet wird beim Abruf (höchstens alle 5 min neu) und einmal je Stunde fürs Protokoll – nicht im
Messtakt.

**In der Übersicht** (gleich unter den Kacheln): oben der Hinweis und ein Satz, wie oft HeatPilot in 24 h
schreiben würde und was heute am Kessel steht; darunter womit gerechnet wurde (Modus, Vorhersage, welche Räume
den Vorlauf bestimmen und welche nicht dabei sind, mit Grund). Zwei kleine Grafen mit derselben Zeitachse statt
einer zweiten y-Achse: oben Vorlauf-Soll (Stufen) und Außentemperatur (gestrichelt), unten Warmwasser-Soll mit
dem Desinfektionstermin. Hinterlegt sind Nacht und „niemand zu Hause“, ein grauer Streifen unten heißt
„Heizbetrieb aus“, ein Ring „würde schreiben“. Antippen bzw. Zeigen auf eine Stunde markiert sie in beiden
Grafen und zeigt alle Werte der Stunde mit Grund und Schreibvorgängen. „Je Stunde“ klappt dieselben Werte als
Tabelle auf – mit Raum-Soll (nur wo es sich ändert) und am Tag der Zeitumstellung der doppelten Stunde „(2.)“.

**Trockenlauf gegen Regler/Automation (7 Tage):** Zu Beginn jeder Stunde merkt sich HeatPilot den Plan für
diese Stunde – so, wie er damals gerechnet wurde, ohne ihn nachträglich zu verbessern. Ist die Stunde vorbei,
kommt der Ist-Wert dazu: das mittlere Vorlauf-Soll des Reglers (z. B. ES73) für den Heizkreis bei Heizbetrieb –
das Heizkreis-Soll, ohne es das Kessel-Soll `sel_flow`; Speicherladungen (Ventil auf Warmwasser bzw. Kessel-Soll
mehr als 15 K über dem Heizkreis) und Zeiten ohne Anforderung (Kessel-Soll unter 20 °C) zählen nicht –, das
mittlere Speicher-Soll (z. B. aus einer HA-Automation) und wie lange der Heizbetrieb an war (Betriebsart Tag und
„Heizen abschalten“ aus). Die Karte zeigt je Größe Plan und Ist als zwei Linien (Ist grau, Stunden „HeatPilot aus,
Regler heizt“ als Streifen; „Wochenraster“ zeigt Plan minus Ist je Wochentag und Stunde – auf schmalen
Bildschirmen nur die Linien), darunter je Tag zum Aufklappen, und die Kennzahlen in einem
Satz: „HeatPilot läge im Mittel 6 K tiefer beim Vorlauf, 4 K tiefer beim Warmwasser; 9 statt 31
Schreibvorgänge“ – die zweite Zahl zählt, wie oft Regler und Automation ihr Soll geändert haben (ab 1 K bzw.
an/aus; den Vorlauf wie im Plan nur bei Heizbetrieb mit Anforderung, ein Nachtwechsel zählt also einmal). Vorlauf wird nur verglichen, wenn beide heizen; dazu die Stunden, in denen HeatPilot aus wäre, der
Regler aber heizt. Beim Warmwasser gibt es eine grobe Energie-Einschätzung aus dem gelernten
Bereitschaftsverlust des Speichers (nur mit plausiblem Warmwasser-Modell), beim Vorlauf keine kWh. Ohne Daten:
„sammelt – erste Werte nach einer Stunde“. Das Protokoll bleibt 90 Tage, „Lernstand neu berechnen“ lässt es
stehen (es ist Messung, nichts Gelerntes); es steht im Export („trockenlauf“) und im Diagnosepaket.

## Ventilverhalten

Seit 0.39 lernt HeatPilot je Raum, **wie das Thermostat sein Ventil stellt** – Stufe 1 von „Thermostate als
Stellglied“ (Planungspapier): Der eingebaute Regler des Thermostats soll später über fein verschobene Sollwerte das
Ventil führen, damit es moduliert statt nur auf- und zuzugehen. Dafür muss HeatPilot das Ventil nachbilden. Ohne
den Schalter „Ventile führen“ wird das nur **gelernt, gezeigt und im Trockenlauf protokolliert – HeatPilot schreibt
dafür nichts**, weder an die Thermostate noch an den Kessel. Seit 0.40 führt HeatPilot mit diesem Schalter die Ventile
wirklich (Stufe 2, siehe [Ventile führen](#ventile-führen)).

Gelernt wird aus den eigenen Messwerten je Raum (Raumtemperatur, Soll, Ventil in %), ohne Gerätedaten eines
Herstellers. Bei einer Gruppe zählt das Mittel aller Ventile – gezählt werden Räume, nicht Ventile; der Satz sagt dann
„2 Ventile, Mittel: …“ (nur an/aus: „ein mit dem ersten, aus mit dem letzten“). Mischt eine Gruppe Ventile mit und
ohne Sensor, beschreibt das Mittel keines davon – sie wird gekennzeichnet und gilt nicht als belastbar. Der Ventilwert
kommt aus dem Ventil- bzw. Heizleistungssensor (Tado-Cloud); ohne Sensor (z. B. nur HomeKit) aus `hvac_action` als
0 oder 100 % – dann gilt das Ventil als **„nur an/aus“**, ebenso, wenn ein Sensor nur 0 und 100 % meldet (gezählt
werden dafür nur Messpunkte, die etwas sagen: Heizbetrieb an, Ventil offen oder Raum nicht über dem Soll – ein Sommer
mit geschlossenem Ventil zählt nicht; wechselt die Art, beginnen Sprünge und Gütemaß neu). Meldet ein vorhandener
Ventilsensor gerade nichts (Cloud-Ausfall), nimmt der Messpunkt zwar `hvac_action` wie bisher, das Ventilmodell lernt
diesen Ersatzwert aber nicht (gemerkt in der Tabelle `valve_subst`). Seit 0.45 gilt dasselbe für einen veralteten
Sensor: seit 6 Stunden kein neuer Wert, und das Thermostat widerspricht ihm (heizt bei 0 %, ruht bei mehr als 0 %).
Hat ein Raum mindestens einen echten Ventilwert in %, zählen nur die echten – 0/100 % aus `hvac_action` anderer
Geräte verwässern das Raumventil nicht. Meldet ein Raum, der echte %-Werte kannte, nur noch an/aus, bleibt das
gelernte %-Ventilmodell erhalten und **ruht** (nicht belastbar, vergisst nichts), bis wieder %-Werte kommen (seit
0.45.1). Nur „Ventil neu lernen“ bzw. „Anderes Thermostat“ beginnen es neu. Räume ohne je %-Werte: wie bisher 0/100 %.
Der Heizkreis von EMS-ESP bekommt kein Ventilmodell – er ist kein Thermostat mit Ventil.

- **Kennlinie:** Ventil (Median) je Abstand Soll − Raumtemperatur in 0,1-K-Stufen von 1 K über bis 2 K unter dem
  Soll. Nur Beharrung: kein Soll-Wechsel in den letzten 30 min, Heizbetrieb an, kein Fenster offen (die
  Raumtemperatur fällt in 15 min um mindestens 0,5 K – von 15 min davor bis 45 min danach), keine Fremdwärme (Kamin,
  Ofen). Die Kennlinie steigt immer an (geglättet) und vergisst langsam (Halbwertszeit 30 Tage). Daraus: **öffnet
  ab** (über 5 %), **halb offen**, **ganz offen** (ab 90 %) und das Totband. Die Beharrung zählt jeden Messpunkt
  mit seiner Messpause (bis 20 min) – bei 15 min Messpause also so viele Stunden wie bei 5 min.
- **Sprungantwort:** Bei jedem Soll-Sprung ab 0,5 K (Zeitplan, Boost, Hand, HeatPilot) die Totzeit, bis sich das
  Ventil um 5 Prozentpunkte bewegt, und die Zeit bis 63 % der neuen Stellung – Median der letzten Sprünge, getrennt
  für auf und zu. Der Zeitpunkt des Sprungs liegt zwischen zwei Messpunkten, die 63-%-Zeit wird zwischen zwei
  Messpunkten geradlinig eingesetzt: Bei kurzer Messpause stimmen die Zeiten auf etwa eine Messpause; ist die
  Messpause länger als die halbe 63-%-Zeit (z. B. 10 oder 15 min), sind sie nur grob und eher zu lang – dann steht
  „grob – Messpause … min“ dabei.
- **Kleines Reglermodell:** Ventil = Kp · Abstand + Integrator, begrenzt auf 0–100 % (der Integrator läuft an der
  Grenze nicht weiter), mit der Totzeit und dem Stellmotor (Zeitkonstante aus der Sprungantwort). Kp und Ki lernt
  HeatPilot auf halbstündigen Abschnitten ohne Sättigung und ohne Soll-Sprung. Das **Messrauschen** der
  Raumtemperatur schätzt es aus je drei Messpunkten und rechnet es heraus – sonst wäre Kp viel zu klein (Rauschen
  0,1 K drückte Kp sonst auf ein Viertel). Überdeckt das Rauschen die Änderungen fast ganz, steht „Kp unsicher“ dabei.
- **P-Regler oder mit Integrator:** HeatPilot rechnet beide Vorhersagen mit – die Kennlinie entlang (mit Stellmotor)
  und das Reglermodell mit Integrator – und nimmt die, die über 30 min besser trifft (bis beide genug Vergleiche haben:
  Nachstellzeit Kp/Ki ab 3 h wie ein P-Regler). Bei einem **P-Regler** gehört die Kennlinie zum Gerät, der Satz nennt
  ihre Punkte. **Mit Integrator** hängt die Kennlinie von der Last ab (Außentemperatur, Zeitplan – dasselbe
  Thermostat ist mal bei 0,5 K über, mal bei 0,5 K unter dem Soll halb offen): Dann nennt der Satz die Reglerwerte
  („regelt mit Integrator, Ruhepunkt am Soll (Kp 55 %/K, Nachstellzeit 130 min) – die Kennlinie hängt von der Last
  ab“), der Graf ist so gekennzeichnet, und die Vorhersage nutzt die Kennlinie nicht.
- **Gütemaß:** Wie weit lag die Vorhersage der Ventilstellung einen Messschritt und 30 min voraus daneben – gemittelt
  über 14 Tage, in Prozentpunkten, vorhergesagt mit dem Modell, wie es vorher war. Zum Vergleich steht daneben, wie
  weit „Ventil bleibt, wie es ist“ daneben lag.
- **Nur an/aus:** statt Kennlinie und Reglermodell die **Schaltpunkte** (ein ab …, aus ab … – mit Hysterese) und wie
  lange es nach einem Soll-Sprung bis zum Schalten dauert.
- **Belastbar** ab 8 Soll-Sprüngen (mindestens 4 je Richtung, die Reaktionszeiten einer Richtung streuen nicht mehr
  als ihr Median – Abstand der Quartile), 48 h Beharrung und höchstens 20 Prozentpunkten Fehler über 30 min, der
  zudem höchstens das 0,7-Fache von „Ventil bleibt, wie es ist“ sein darf (sonst sagt das Modell nichts, was nicht
  auch ohne es klar wäre). Beim P-Regler darf „halb offen“ in 14 Tagen nicht mehr als 0,3 K wandern; mit Integrator
  muss Kp sicher sein. Sonst steht dort „lernt“ mit dem Grund („erst 5 von 8 Soll-Sprüngen“, „erst 3 Soll-Sprünge
  nach unten“, „Vorhersage kaum besser als „Ventil bleibt, wie es ist““).
- **Schrittweite:** aus Home Assistant (`target_temp_step` des Thermostats; bei Gruppen der gröbste Schritt der
  Thermostate, die beschrieben würden; mit Raumregler dessen). Meldet ein Gerät keine, gilt 0,5 K („angenommen“).

**Aus der Historie und laufend:** Beim ersten Start mit 0.39 lernen die Ventilmodelle aus den gespeicherten
Messwerten der letzten 60 Tage nach – im Takt in Abschnitten von höchstens einem Tag, höchstens 1 s je Takt, damit
Messen und Steuern nicht warten. Bis dahin steht beim Raum „lernt aus der gespeicherten Historie nach“. Danach lernt
jedes Modell je abgeschlossener halber Stunde dazu. Raum-, Haus- und alle anderen Modelle bleiben dabei, wie sie
sind; „Lernstand neu berechnen“ verwirft auch die Ventilmodelle, sie lernen danach wieder aus den Messwerten.

**Anzeige je Raum** (Abfragen `api/heat` und `api/summary`, Feld `valve_model`): die Kennlinie als kleiner Graf und
ein Satz, z. B. „öffnet ab 0,2 K unter Soll, halb offen bei 0,6 K, ganz bei 1,2 K · reagiert auf +0,5 K in ≈ 8 min,
schließt in ≈ 12 min · Schritt 0,5 K · belastbar“ (öffnet es schon über dem Soll, sagt jeder Punkt „über“ bzw.
„unter Soll“ dazu; Minuten wie in der Tabelle, Halbe aufgerundet); mit Integrator „regelt mit Integrator …“; nur
an/aus: „schaltet ein bei 0,3 K unter Soll, aus bei 0,1 K über Soll · …“. Zu finden im Reiter **Heizen → Raum antippen**, Abschnitt „Ventilverhalten“: der Graf zeigt das Ventil in %
über dem Abstand zum Soll (rechts von „Soll“ ist der Raum kälter, links wärmer; der Punkt markiert „halb offen“),
gestrichelt, solange das Modell lernt; mit Integrator steht „hängt von der Last ab“ darüber; bei „nur an/aus“ die
beiden Schaltpunkte als geschlossene Schleife (unten zu bis „ein“, oben offen bis „aus“). Zeiger oder Finger auf dem
Graf zeigen den Wert je 0,1 K und wie viele Stunden dahinterstehen. Unter „Einzelheiten“ stehen Gruppe, Soll-Sprünge
je Richtung, Sprungantwort (mit „grob“ bei langer Messpause), Beharrung, Vorhersagefehler neben „Ventil bleibt“,
Reglermodell (Kp, Ki, Nachstellzeit, P-Regler oder mit Integrator, Messrauschen) bzw. Schaltpunkte, Schrittweite, was
ausgelassen wurde und die Kennlinie als Tabelle. Ohne genug Daten gibt es keinen Graf, nur den Satz („Kennlinie lernt noch“). In der **Übersicht → Räume** steht
unter der Tabelle der Raummodelle die Tabelle „Ventilverhalten“ mit einer Zeile je Raum (Kennlinie in Zahlen, Reaktion
auf/zu, Fehler über 30 min, Schritt, Stand mit Grund); der Raumname öffnet den Dialog mit dem Graf.

### Ventile im Trockenlauf

Was HeatPilot mit den Thermostaten als Stellglied tun würde – **nur Protokoll, es wird nichts geschrieben**, auch
nicht bei „HeatPilot steuert“, solange der Schalter „Ventile führen“ aus ist (mit ihm rechnet der Trockenlauf nur noch
die Räume, die HeatPilot nicht führt). Gezählt wird nach Räumen; der Heizkreis von EMS-ESP ist nie dabei. Zwei Regeln:

- **Früh drosseln:** Nähert sich ein Raum seinem Soll wirklich – gemessen deutlich steigend (mindestens 0,3 K/h und
  dreimal über der Unsicherheit aus dem Messrauschen) und seit einem Soll-Anstieg (Zeitplan, Boost) noch nicht
  erreicht oder noch mindestens 0,2 K bzw. einen halben Schritt darunter –, erreicht er es laut Raum- und Ventilmodell
  (gemeinsam Minute für Minute gerechnet, das Ventil mit Totzeit und Stellmotor) in weniger als 20 min, wäre das
  Ventil dann noch über 30 % offen und sagen die Modelle mindestens 0,2 K Überschwingen voraus, würde HeatPilot das
  Soll jetzt um einen Schritt senken und zurücksetzen, sobald der Raum das eigentliche Soll erreicht – spätestens
  nach 60 min. Danach drosselt es den Raum erst wieder nach einem neuen Soll oder wenn er 0,5 K ausgekühlt ist. So
  löst ein Thermostat mit Integrator, das das Soll mit offenem Ventil hält, nicht alle paar Minuten aus, und ein
  P-Regler, der vor dem Soll schließt, bekommt keinen Vorschlag.
- **Gemeinsam anfordern:** Fordert nur ein Raum Wärme an (Ventil über 5 %) und taktet der Kessel (mindestens 3
  Brennerstarts in der letzten Stunde, Läufe im Mittel unter 10 min – ohne Brennerereignisse aus dem Startzähler),
  würde HeatPilot Räume, die in den nächsten 60 min ohnehin heizen würden, um einen Schritt anheben, damit mehrere
  zugleich anfordern und der Kessel durchläuft. „Würde heizen“ heißt: Das Ventilmodell öffnet das Ventil von jetzt
  aus entlang des Abkühlens (Raummodell, sonst gemessener Abfall) – ein geschlossenes Ventil über dem Soll öffnet
  dabei auch mit Integrator nicht. Zurück, sobald der anfordernde Raum nicht mehr anfordert, spätestens nach 60 min.

Ohne plausibles Raummodell rechnet der Trockenlauf mit dem gemessenen Verlauf der letzten 30 min (bei langer
Messpause der letzten drei Messpunkte), ohne Ventilvorhersage mit der Ventilstellung von jetzt bzw. bis zur
Öffnungsschwelle, mindestens bis zum Soll. Mitgezählt nach den abgesprochenen Regeln: nur bei Änderung, in der vollen
Auflösung des Thermostats (Schrittweite oben), 5 min Haltezeit je Raum. Ändern Zeitplan oder Hand das Soll, endet ein
Eingriff ohne Zurückschreiben.

**Auswertung** (Übersicht → Vorschau, Abschnitt „Ventile (Trockenlauf)“ am Ende der Vorschau; Abfrage `api/preview`,
Feld `valves`): wie oft je Regel in 7 Tagen, die Schreibvorgänge, die es gegeben hätte, je Raum und Tag, Beispiele mit
Grund und die gerade laufenden Eingriffe – oben ein Satz und die Kennzahlen, darunter die Eingriffe je Tag als Balken
(blau früh drosseln, orange gemeinsam anfordern), aufklappbar die Beispiele, je Raum und Tag und die Regeln. Die
**geschätzte Wirkung** – weniger Überschwingen in K (Raum- und Ventilmodell rechnen beide Fälle) und grob ein
Brennerstart weniger je Raum, der sonst später allein angefordert hätte – gibt es nur, wo das Raummodell plausibel und
das Ventilmodell belastbar ist; sonst wird nur gezählt. Eine Stunde nach jedem Eingriff trägt HeatPilot nach, was
wirklich geschah (höchstes Überschwingen über das Soll, Brennerstarts der Stunde aus den Brennerereignissen bzw. dem
Startzähler) – es wurde ja nichts geschrieben. Genau das prüft die Schätzung nach: Beim Eingriff merkt sich HeatPilot
auch das vorhergesagte Überschwingen **ohne** Eingriff; die Schätzung „≈ x K weniger“ zeigt es für einen Raum erst,
wenn diese Vorhersage dreimal nachgemessen im Mittel höchstens 0,2 K daneben lag (über 30 Tage). Bis dahin steht beim
Beispiel „vorhergesagt ohne Eingriff ≈ … K (Schätzung wird nachgeprüft)“.
Ein Ventil, das nur an/aus kennt, gewinnt durch frühes Drosseln kaum etwas: Es schaltet nur früher ab und nach dem
Zurücksetzen wieder voll an – die Schätzung zeigt das so. Das Protokoll bleibt 90 Tage, „Lernstand neu berechnen“
lässt es stehen; es steht im Export („ventile“) und im Diagnosepaket.

### Ventile führen

Stufe 2 von „Thermostate als Stellglied“ (seit 0.40): Mit dem Schalter **„Ventile führen“** unter **Einstellungen →
Steuerung** (Standard aus) schreibt HeatPilot die Regeln des Trockenlaufs wirklich – über denselben Weg wie das
Wunschsoll (`climate.set_temperature` auf die Thermostate des Raums, mit Raumregler nur auf ihn), mit denselben
Fehlerregeln. Ohne den Schalter bleibt alles wie in 0.39: nur Trockenlauf, keine zusätzlichen Schreibvorgänge.

**Wo es wirkt:** nur bei „HeatPilot steuert“, nur in Räumen mit „macht mit“, deren Raummodell plausibel und deren
Ventilmodell belastbar ist (wie im Abschnitt „Ventilverhalten“; nicht beim Nachlernen aus der Historie, nicht bei
gemischten Gruppen) und deren Thermostat in Schritten bis 0,5 K einstellbar ist – gröber wäre ein Schritt schon auf
oder zu. Der Heizkreis von EMS-ESP ist nie dabei. Welche Räume es sind und warum die anderen noch lernen, steht in
„Bereit für die Steuerung?“ (Block „Ventile führen“).

**Was geschrieben wird:** Die Steuerung rechnet wie immer das Wunschsoll (Komfort, Nacht, Abwesenheit, Vorheizen).
Darauf legt die Ventilführung **höchstens einen Schritt** des Thermostats (Tado 0,5 K, auf den Schritt gerundet,
5–25 °C):

- **Früh drosseln** (ein Schritt tiefer): die Regel des Trockenlaufs (echte Annäherung, Soll laut Raum- und
  Ventilmodell in unter 20 min erreicht, Ventil dann noch über 30 % offen, mindestens 0,2 K Überschwingen vorhergesagt),
  dazu strenger: nur in den Phasen Komfort und Vorheizen – nie nachts, bei Abwesenheit oder im Urlaub, dort ist das
  Soll ohnehin tief –, nur wenn der Raum höchstens 0,3 K unter dem Soll ist, und nur mit der gemeinsamen Vorhersage
  beider Modelle. Zurück aufs Wunschsoll, sobald der Raum am Soll ist, spätestens nach 60 min – und **sofort**, auch in
  der Haltezeit, wenn er unter Soll − 0,3 K fällt (Fenster auf, Tür offen): Wer Wärme braucht, bekommt sie. Danach
  drosselt HeatPilot den Raum erst wieder nach einem neuen Soll oder 0,5 K Auskühlen.
- **Gemeinsam anfordern** (ein Schritt höher): Fordert nur ein Raum an und taktet der Kessel, hebt HeatPilot Räume an,
  die laut Raummodell in den nächsten 60 min ohnehin heizen würden (nur mit Außentemperatur) – zurück, sobald der
  Auslöser endet, spätestens nach 60 min. Der anfordernde Raum wartet auf nichts; gezählt wird nach Räumen, auch Räume
  ohne „macht mit“ zählen als anfordernd (angehoben werden sie nie). Keine Kette und kein Pendeln: Auslösen darf kein
  Raum, den HeatPilot gerade drosselt oder in den letzten 60 min angehoben hat – sein Ventil schließt dann gleich bzw.
  steht seinetwegen offen; angehoben wird kein Raum, der nach dem Drosseln gesperrt ist (bis zum neuen Soll bzw. 0,5 K
  Auskühlen) oder in den letzten 60 min schon angehoben war.

**Schreibregeln:** nur bei Änderung, 5 min Haltezeit je Raum nach jedem Schreibvorgang (auch nach einem
Phasenwechsel); ausgenommen das sofortige Zurück. **Phasenwechsel gehen vor:** Wechselt die Steuerung die Phase (z. B.
Nacht), gilt sofort das neue Wunschsoll, die Führung beginnt neu. **Hand vor Automatik:** Dreht jemand am Soll (App,
Ventil, Heizen-Reiter, Boost, aus – auch „Alle aus“ oder die Fenster-offen-Erkennung des Thermostats, auch genau aufs
Wunschsoll), vergisst die Führung ihren Schritt und lässt den Raum bis zum nächsten Phasenwechsel in Ruhe, wie die
Steuerung – „aus“ bleibt aus. Was HeatPilot selbst geschrieben hat, zählt dabei nie als Hand: auch nicht, wenn Home
Assistant es bis zu zwei Takte später meldet, wenn in einer Gruppe ein Ventil es nicht annahm oder wenn die
Einstellungen während des Schreibens gespeichert wurden – spätestens nach der Wartezeit steht dann wieder das
Wunschsoll. **Pause:** In der Pause beginnt HeatPilot keinen Schritt; der Knopf „Pause“ nimmt laufende Schritte vorher
einmal zurück aufs Wunschsoll („Ventil: zurück (Pause)“), und endet ein Schritt in der Pause (Soll erreicht, nach 60 min,
Raum braucht Wärme), geht sein Zurück trotzdem hinaus. Schalter aus oder ein Modell nicht mehr belastbar: sofort zurück
aufs Wunschsoll („Ventil: zurück (Ventile führen aus)“). **„Beobachten“ oder „macht mit“ aus:** Beim Speichern nimmt
HeatPilot einen laufenden Schritt noch unter den alten Einstellungen einmal zurück („Ventil: zurück (Steuerung
„Beobachten“)“ bzw. „… (macht nicht mehr mit)“) – nur wenn er noch am Thermostat steht; danach schreibt es in diesem
Raum nichts mehr. Fällt HeatPilot mitten in einem Schritt aus, bleibt der Schritt stehen (höchstens einer neben dem
Wunschsoll), bis HeatPilot wieder läuft. Schlägt ein Schreibvorgang fehl, gilt der Schritt nicht – erneut nach 15 min,
ab drei Fehlschlägen in Folge die Meldung wie bei jedem Raum.

**Protokoll:** jeder Schritt im Schreibprotokoll (Live → Diagnose → Protokoll, Diagnose-Datei, Export) mit „Ventil: früh drosseln (erreicht
21,0 °C in ≈ 7 min, Ventil dann noch ≈ 64 % offen)“, „Ventil: gemeinsam anfordern (nur Wohnzimmer fordert an, Kessel
taktet (5 Starts/h) – Bad würde in 60 min ohnehin heizen)“ bzw. „Ventil: zurück (Soll erreicht)“, dazu als geschriebener
Eintrag im Ventil-Protokoll. Eine Stunde danach trägt HeatPilot nach, was wirklich geschah – jetzt **mit** dem Eingriff;
vorhergesagt und nachgeprüft wird entsprechend das Überschwingen mit dem Eingriff.

**Anzeige:** Heizen → Raum antippen zeigt während eines Schritts „Ventil geführt: 20,5 statt 21 °C – früh drosseln,
zurück ≈ 15:40“ (beim gemeinsamen Anfordern „zurück spätestens …“), sonst nichts. In der Übersicht → Vorschau heißt der
Abschnitt bei aktivem Schalter **„Ventile – HeatPilot schreibt“**: der Satz („In 7 Tagen hat HeatPilot 2× früh
gedrosselt …“, dazu, was in den übrigen Räumen nur im Trockenlauf gezählt wurde), die Kennzahlen mit „davon …
geschrieben“ und dem Überschwingen mit bzw. ohne Eingriff, Beispiele mit „geschrieben“ bzw. „würde schreiben“, je Raum
und Tag „Schreibvorgänge (davon geschrieben)“, laufende Eingriffe und die Regeln. Ohne Schalter bleibt der Abschnitt
„Ventile (Trockenlauf)“ wie bisher. Abfragen: `api/heat` (je Raum `control.valve`, `control.valve_guided`;
`readiness.valves`), `api/preview` (`valves.live`, `valves.written`), Export „ventile → fuehren“.

## Warmwasser über HeatPilot (Weg W)

Seit 0.38 kann HeatPilot das **Warmwasser-Soll** (Speicher-Soll) des Kessels selbst setzen – statt einer
Automation in Home Assistant. Bis 0.40 war es der einzige Wert, den HeatPilot am Kessel schreibt (seit 0.41 kommen mit
[Kessel über HeatPilot](#kessel-über-heatpilot-weg-b) Vorlauf-Soll und Heizbetrieb dazu), und er geht über Home
Assistant: Dienst `number.set_value` auf die Entität des Speicher-Solls, die EMS-ESP in Home Assistant anlegt
(z. B. `number.boiler_dhw_seltemp`, bei älteren EMS-ESP `number.boiler_wwseltemp`). An EMS-ESP selbst schickt
HeatPilot weiterhin nur Lese-Anfragen; die Schreibregeln für die Thermostate bleiben, wie sie sind.

**Einstellungen → Kessel, Schalter „Warmwasser-Soll“** – unabhängig vom Schalter der Raumsteuerung:

- **Aus** (Standard): wie bisher – nur Empfehlung und Vorschau.
- **Trockenlauf**: HeatPilot rechnet in jedem Takt und schreibt ins Protokoll, was es schreiben würde
  („Trockenlauf: würde 55 °C schreiben (…)“) – nach denselben Regeln wie unten, am Kessel ändert sich nichts.
- **HeatPilot setzt**: HeatPilot schreibt nach den Regeln unten.

**Entität** (seit 0.39.1 unter Einstellungen → Kessel → Warmwasser steuern; unter Warmwasser steht, welche
gilt): vorgeschlagen aus der Erkennung (Rolle „Warmwasser-Soll“, nur `number`-Entitäten); eine andere lässt
sich wählen – die Auswahl bietet nur `number`-Entitäten mit °C an, die nicht zu etwas anderem gehören, den Vorschlag
und Namen mit Warmwasser-Hinweis zuerst. Geschrieben wird nur auf diese eine Entität, nur ganze Grad zwischen Minimum
und Maximum der Warmwasser-Einstellungen, nur innerhalb der Grenzen, die Home Assistant für die Entität meldet, und
nur, wenn sie taugt: verfügbar, Einheit °C (ohne Einheit nicht), kein Heizkreis (`…_hc1_…`, z. B. die Heizkurve des
Reglers), keine andere erkannte Rolle und kein anderer bekannter Kesselwert (Vorlauf-Soll, Heiztemperatur,
Hysterese, Desinfektionstemperatur …). Solche Entitäten lassen sich gar nicht erst speichern; eine früher gespeicherte
macht „Bereit für die Steuerung?“ zum Hindernis, und HeatPilot schreibt nichts. Deutet weder der Name noch der
Anzeigename auf Warmwasser (dhw, ww, Warmwasser, Speicher …), ist das erlaubt – andere Kessel heißen anders –, steht
aber als Einschränkung „prüfen“ da. Alles andere – andere Entitäten, andere Dienste, andere Werte – weist HeatPilot
im Code hart ab. Bei „Aus“ und „Trockenlauf“ gibt es den Schreibweg gar nicht.

**Regeln:**

- **Wunsch** = die Warmwasser-Empfehlung (dieselbe wie in Übersicht und Vorschau), auf 1 K gerundet (halbe
  Grad aufwärts), nie unter dem Minimum, nie über dem Maximum.
- **Nur bei Änderung:** geschrieben wird, wenn der Wunsch mindestens 1 K vom Kessel-Soll abweicht – kein
  „55, 55, 55“; den Wert hält der Kessel dann selbst. Die Schwelle gilt auch für die ungerundete Empfehlung:
  Schwankt sie um x,5 (52,4 ↔ 52,6 °C), schreibt HeatPilot nicht abwechselnd 52 und 53.
- **5 min Haltezeit** nach jedem Schreiben, kein Raster: um 15:03 geschrieben, frühestens um 15:08 die
  nächste Entscheidung – danach sofort, wenn nötig.
- **Sonderladung oder Desinfektion läuft** (Kesselschalter `wwdisinfecting`/Einmalladung oder weit über das
  Soll geladen, samt Nachlauf): kein Schreiben, bis sie vorbei ist.
- **Kessel-Soll:** der Zustand der beschriebenen Entität in Home Assistant; EMS-ESP direkt nur mit frischen Werten
  (höchstens 90 s alt – antwortet EMS-ESP nicht mehr, zählt sein letzter Wert nicht). Zeigen Entität und EMS-ESP
  verschiedene Werte, ist das Kessel-Soll **unklar**: HeatPilot schreibt nicht und zählt nichts als fremd, bis beide
  gleich sind („Kessel-Soll unklar: Home Assistant zeigt 45 °C, EMS-ESP 50 °C“).
- **Nicht bereit heißt: nichts schreiben.** Ist die Entität nicht verfügbar, fehlt sie, taugt sie nicht oder lässt
  EMS-ESP das Warmwasser-Soll nicht schreiben, merkt HeatPilot nichts vor – dieselben Hindernisse wie in „Bereit für
  die Steuerung?“, als Entscheidung sichtbar. Das zählt nicht als Fehlschlag.
- **Prüfung nach 60 s:** Zeigt der Kessel den Wert (±0,5 K)? Geprüft wird im nächsten 30-s-Schritt der Live-Werte
  bzw. im nächsten Takt – der Messtakt wartet darauf nicht. Ist das Kessel-Soll in dem Moment unbekannt oder unklar
  (EMS-ESP startet nach einem Update neu), prüft HeatPilot bis 5 min nach dem Schreiben weiter; erst dann zählt es.
  Zeigt der Kessel einen anderen Wert: Fehlschlag im Protokoll, Hinweis in der Oberfläche und eine Meldung
  („Warmwasser-Soll 50 °C nicht übernommen (Kessel zeigt 55 °C) – 2. Versuch in 5 min“). Kein endloses
  Nachschreiben: der nächste Versuch frühestens nach der Haltezeit; nach **3 Fehlschlägen in Folge pausiert** Weg W,
  mit Meldung, bis du ihn mit **„wieder einschalten“** freigibst. Der erste Erfolg nach einem Fehler meldet „wieder in
  Ordnung“ – auch wenn der Kessel den Wert erst später zeigt, sogar während der Pause. Zeigt er inzwischen schon den
  Wunsch, braucht es keinen neuen Versuch: Hinweis, Meldung und Problem-Sensor gehen aus, die Fehlschläge zählen beim
  nächsten Versuch weiter. Ein Fehler beim Schreiben selbst (Home Assistant antwortet nicht) zählt wie ein
  Fehlschlag. „Wieder einschalten“ wirkt nur bei einer Pause (sonst ändert sich nichts).
- **Fremder Schreiber:** Ändert sich das Kessel-Soll, ohne dass HeatPilot geschrieben hat (meist die alte
  Automation, auch eine Änderung von Hand), stehen „anderer Schreiber aktiv – HA-Automation abschalten“ als
  Hinweis und eine Meldung da; nach **2 fremden Änderungen in 1 h pausiert** Weg W – zwei Schreiber sollen
  nicht gegeneinander schreiben. Übernimmt der Kessel einen Wert von HeatPilot nur verspätet, ist das kein
  fremder Schreiber.
- **Keine Zählsperre:** Ändert sich die Empfehlung um 1 K, wird nach der Haltezeit geschrieben – egal wie oft am
  Tag. Sonst bliebe der Speicher z. B. auf 60 °C stehen, nur weil an dem Tag viel los war. Die Zahl der
  Schreibvorgänge der letzten 24 h steht nur zur Info in der Anzeige.
- **Fällt HeatPilot aus**, bleibt das zuletzt geschriebene Soll stehen – deshalb nie unter dem Minimum. Liegt das
  Minimum unter 50 °C, zeigt „Bereit für die Steuerung?“ das ohne Desinfektionstermin als Einschränkung
  (Legionellen: so tiefe Werte stehen dann wirklich am Kessel), mit Termin als Hinweis. Das Minimum entscheidest du.
- **Zurück auf Aus oder Trockenlauf:** HeatPilot schreibt nichts zurück, der Kessel behält den Wert; ein
  Hinweis erinnert daran, die Automation ggf. wieder einzuschalten.

**Vor dem Umschalten** die Automation in Home Assistant abschalten, die bisher das Speicher-Soll setzt – beim
Speichern mit „HeatPilot setzt“ fragt die Oberfläche einmal danach. Den Stand zeigt „Bereit für die Steuerung?“ im
Block „Warmwasser über HeatPilot“ (siehe dort). Seit 0.42 löst HeatPilot mit „HeatPilot setzt“ auch die Desinfektion
zum Termin aus, wenn kein Regler am Bus ist (siehe [Desinfektion](#desinfektion-legionellen)); solange sie läuft,
schreibt Weg W das Speicher-Soll nicht.

**Anzeige:** Übersicht → Status zeigt eine Statuszeile („HeatPilot setzt · 50 °C seit 15:03 · nächste
Änderung 17:30 → 55 °C“, bei „Beobachten“ „Beobachten · würde 50 °C seit …“, sonst „pausiert – …“), das Protokoll
(Live → Diagnose) auch die Warmwasser-Zeilen (geschrieben, würde schreiben, nicht übernommen, fremde Änderung,
pausiert, wieder eingeschaltet). Bei „Aus“ steht die Zeile nur, solange ein Hinweis gilt (z. B. nach dem
Zurückschalten); ist Weg W pausiert, steht dort der Knopf „wieder einschalten“. Einstellungen → Kessel zeigt
denselben Stand ausführlicher (Kessel-Soll, Empfehlung, letztes Schreiben und Prüfen). Die nächste Änderung kommt
aus der Vorschau, die nach derselben Regel rechnet. Uhrzeiten stehen überall in der Zeitzone deines Browsers (das
Add-on schickt sie als Zeitpunkte, einen fremden Schreiber als Abstand, „vor 3 h“). Abfragen: `GET api/dhw` (Status
und Bereitschaft), `POST api/dhw/resume` („wieder einschalten“ – ohne Pause Antwort 409, es ändert sich nichts).

## Kessel über HeatPilot (Weg B)

Seit 0.41 kann HeatPilot den Kessel selbst führen – **Vorlauf-Soll** live und **Heizbetrieb** an/aus. Das ist nur
ohne Regler am EMS-Bus sinnvoll: Ein Regler (z. B. RC35-artig) überschreibt alle 60 s, was am Kessel gesetzt wird.
Geschrieben wird über Home Assistant, wie beim Warmwasser-Soll – an EMS-ESP selbst schickt HeatPilot weiterhin nur
Lese-Anfragen. Die Antipendelzeit (Taktsperre des Kessels) fasst HeatPilot nur mit „Antipendelzeit anpassen“ an
(seit 0.48, Standard aus, siehe unten), alle anderen Kesselwerte nie.

**Schalter „Kessel über HeatPilot“** (Einstellungen → Kessel über HeatPilot, eigene Karte nach „Haus & Kessel“) –
getrennt von Raumsteuerung und Warmwasser, drei Stellungen:

- **Aus** (Standard): HeatPilot rechnet den Kessel nicht; Regler bzw. Kessel regeln selbst.
- **Trockenlauf**: HeatPilot rechnet alle 30 s und schreibt ins Protokoll, was es schreiben würde („Trockenlauf: würde
  Vorlauf-Soll 42 °C schreiben (Wohnzimmer hält 21,0 °C)“, „… Heizbetrieb aus …“) – am Kessel ändert sich nichts. Geht
  immer, auch mit Regler am Bus: So lässt sich über einige Tage vergleichen, was HeatPilot setzen würde und was der
  Regler tut (Vorschau → Vergleich).
- **HeatPilot setzt**: HeatPilot schreibt nach den Regeln unten – **aber nur, solange kein Regler am Bus ist**. Antwortet
  EMS-ESP auf das Gerät `thermostat` (oder gibt es frische Werte des Reglers in Home Assistant), ist das Schreiben
  gesperrt: „Regler am Bus – er überschreibt alle 60 s. Erst Trockenlauf prüfen, dann Regler abbauen“. Ohne
  EMS-ESP-Adresse bzw. wenn EMS-ESP gerade nicht antwortet, ist es unbekannt – dann schreibt HeatPilot ebenfalls nicht.
  Eine gescheiterte Abfrage des Reglers (Zeitüberlauf, Fehler, leere Antwort) heißt nicht „kein Regler“, sondern
  „unbekannt“ – außer der Kessel antwortet in derselben Abfrage (seit 0.46.5): Dann ist EMS-ESP erreichbar und nur der
  Regler antwortet nicht; das zählt wie „kein Gerät `thermostat`“ (so meldet EMS-ESP einen abgebauten Regler oft). Als
  **nicht vorhanden** gilt der Regler erst, wenn EMS-ESP mindestens 3 Abfragen in Folge über mindestens 5 min
  ausdrücklich meldet, dass es kein Gerät `thermostat` gibt (bzw. nur der Regler nicht antwortet), **und** der Regler
  seit 30 min nicht mehr gemeldet wurde – nach einem Neustart von EMS-ESP wird der Kessel oft vor dem Regler erkannt. Die Werte des Reglers
  in EMS-ESP deshalb nicht unter „Customizations“ für die API ausschließen.

**Entitäten** (Einstellungen → Haus → Experte → Kessel über HeatPilot; leer = Vorschlag aus der Erkennung):

- **Vorlauf-Soll** – die `number`-Entität, die EMS-ESP für `selflowtemp` anlegt (z. B. `number.boiler_selflowtemp`).
  Erlaubt ist nur eine `number`-Entität mit °C, die nicht zu etwas anderem gehört: kein Heizkreis (`…_hc1_…`), keine
  andere erkannte Rolle, nie die Heiztemperatur (Obergrenze des Kessels) oder das Warmwasser-Soll.
- **Heizbetrieb** – die `switch`-Entität für `heatingactivated` (z. B. `switch.boiler_heatingactivated`). Nie
  „Heizen abschalten“ (hat die umgekehrte Bedeutung) und kein Warmwasser- oder Zirkulationsschalter.

Deutet der Name nicht darauf hin, ist das erlaubt (andere Kessel heißen anders), steht aber als Einschränkung „prüfen“
da. Alles andere – andere Entitäten, andere Dienste, andere Werte – weist HeatPilot im Code hart ab; bei „Aus“ und
„Trockenlauf“ gibt es den Schreibweg gar nicht.

**Was geschrieben wird** – nur diese zwei Aufrufe: `number.set_value` auf das Vorlauf-Soll, **ganze Grad von 35 °C
bis zum höchsten Vorlauf** („Maximale Vorlauftemperatur“, Einstellungen → Kessel) und **nie über der Fußboden-Grenze** (mit den Messwerten
von jetzt geprüft, unmittelbar vor jedem Schreiben), innerhalb der Grenzen, die Home Assistant für die Entität meldet
(das Ziel wird schon darauf begrenzt); und `switch.turn_on`/`switch.turn_off` auf den Heizbetrieb – **eingeschaltet nur,
wenn das Vorlauf-Soll am Kessel bekannt ist und höchstens bei der Grenze liegt** (oder HeatPilot es im selben Schritt
darauf schreibt). Vorlauf-Soll und Warmwasser-Soll müssen verschiedene Entitäten sein – sonst lässt sich die Einstellung
nicht speichern, und beide Schreibwege sind gesperrt. Weist der Schreibweg einen Wert doch ab (Schutzregel), steht das
als Hinweis da; hält es 15 min an, kommt eine Meldung.

**Regeln:**

- **Live:** alle 30 s neu gerechnet. Das Vorlauf-Soll nur bei **Änderung ab 1 K** (kein „55, 55, 55“) und höchstens
  **ein Schreiben je 60 s** – der Kessel braucht die Prüfung. **Anheben sofort** (wer Wärme braucht, bekommt sie
  sofort), **senken erst, wenn das Ziel 10 min lang niedriger bleibt** – dann auf den höchsten Wert dieser 10 min; in
  dieser Haltezeit erneuert HeatPilot das gehaltene Soll jede Minute (seit 0.48.6, sonst fällt ein Kessel ohne Regler
  darin auf seinen Ruhewert, z. B. 7 °C). **Seit 0.50 auch nicht mitten im Brennerlauf:** Läuft der Brenner seit
  weniger als 10 min, wartet das Senken, bis er von selbst ausgeht oder 10 min gelaufen ist – vorher schaltete das
  Senken ihn nach wenigen Minuten ab (ein Takt mehr). Der
  **Heizbetrieb** hat **5 min Haltezeit** nach jedem Schalten und geht **erst nach 15 min ohne Bedarf aus**. So pendelt
  nichts, wenn ein Raum um eine Schwelle schwankt (z. B. 20,6 ↔ 20,7 °C). Steht der Kessel dann schon auf seinem
  **Ruhewert** (Vorlauf-Soll bis 20 °C, z. B. 7 °C), heizt er nicht – HeatPilot schaltet den Heizbetrieb dann nicht
  aus (seit 0.49.1; mancher Kessel ohne Regler nimmt „aus“ gar nicht an). Zeigt er bei der Prüfung nach „aus“ noch
  „an“, aber den Ruhewert, ist das kein Fehlschlag.
- **Schutz über der Grenze:** Zeigt der Kessel ein Vorlauf-Soll über der Grenze, die gerade gilt (Fußboden-Grenze, die
  z. B. mit einem RTL-Raum neu greift, höchster Vorlauf, Höchstwert der Entität – auch wenn nur Home Assistant oder nur
  EMS-ESP es zeigt), schreibt HeatPilot sofort die Grenze bzw. das Ziel – ohne 60-s-Abstand und ohne auf eine offene
  Prüfung zu warten (sie wird ersetzt). Braucht gerade kein Raum Wärme, gilt sofort das Vorlauf-Soll „nur Pumpe“
  (20 °C) bzw. bei „Heizbetrieb ganz aus“ geht der Heizbetrieb sofort aus – ebenso, wenn die Fußboden-Grenze unter
  35 °C liegt. Nur eine Warmwasser-Ladung hält das auf.
- **Vorlauf-Soll** – in dieser Reihenfolge:
  1. **Bedarf:** dieselbe Rechnung wie die Vorschau – der nötige Vorlauf des anspruchsvollsten Raums aus den
     Raummodellen, Vorheizen, Aufheizzuschlag. **Untergrenze 35 °C, wenn geheizt wird**: Halten alle Räume nur und
     braucht keiner so viel, ist kein Bedarf; heizt ein Raum auf oder vor (z. B. nach einem Boost), gilt seit 0.46.5
     mindestens 35 °C. Nacht und Abwesenheit folgen aus den Raum-Sollwerten der Steuerung.
     **Kein Bedarf** (seit 0.46.5, Einstellung **„Wenn kein Raum Wärme braucht“**): Standard **„Pumpe läuft weiter“** –
     der Heizbetrieb bleibt an, das Vorlauf-Soll geht auf **20 °C** (mindestens der Mindestwert der Entität; der Brenner
     springt nicht an, die Heizungspumpe läuft weiter; „kein Bedarf – Pumpe läuft, Vorlauf-Soll 20 °C“). Gesenkt wird
     wie immer erst, wenn das Ziel 10 min niedriger bleibt. **„Heizbetrieb ganz aus“** schaltet wie bis 0.46.4 den
     Heizbetrieb aus (nach 15 min ohne Bedarf) – z. B. für den Sommer. Gespeicherte Einstellungen älterer Versionen
     gelten als „Pumpe läuft weiter“. Der Takt-Boost wirkt nur bei laufendem Heizbetrieb.
  2. **Mindest-Vorlauf aus der Abnahme:** Der Kessel kann nicht unter seine kleinste Leistung (kleinste Brennerleistung
     in % × Nennleistung, aus EMS-ESP bzw. Einstellungen → Kessel). HeatPilot rechnet, ab welchem Vorlauf die
     Räume mit offenem Ventil diese Leistung abnehmen (je Raum Heizwirkung × (Vorlauf − Raumtemperatur)^n aus dem
     Raummodell, in kW umgerechnet mit dem Hausmodell: Wärmebedarf des Hauses jetzt, verteilt auf die Räume – eine
     Näherung, die Räume gleich schwer nimmt). Seit 0.50 zählt jedes Ventil nach seiner Öffnung (ein Ventil bei 9 %
     nimmt kaum etwas ab; seit 0.53 mit der Form des Raummodells) – vorher ließ ein Ventil, das gerade über 5 % ging, den Wert von 35 auf 49 °C springen.
     Liegt das über dem Wert aus 1., gilt es. Reicht auch der höchste Vorlauf nicht, gilt nichts: lieber lange Pausen –
     der Kessel taktet dann über seine eigene Sperre.
     **Mindestleistung** (seit 0.42): Meldet der Kessel keine kleinste Brennerleistung (EMS-ESP `burnminpower`) und ist
     unter Einstellungen → Kessel nichts eingetragen, schlägt HeatPilot dort die Untergrenze des einstellbaren
     Bereichs der **maximalen Brennerleistung** vor (`burnmaxpower`, z. B. „Vorschlag 30 % – aus dem Bereich der
     maximalen Brennerleistung (30–100 %)“; Bereich aus der Kesselsteuerung von EMS-ESP bzw. den Grenzen der Entität).
     **„übernehmen“** trägt ihn ins Feld ein, gespeichert wird erst mit „Speichern“ – nie automatisch. Fehlt die
     Mindestleistung (oder die Nennleistung), ist die Regel aus: „Abnahme-Regel aus: Mindestleistung unbekannt – …“
     steht als Einschränkung in „Bereit für die Steuerung?“ und als Grund im Ziel.
  3. **Zuschlag „Raum kommt nicht hoch“** (seit 0.50): Der Bedarf aus den Raummodellen kann zu tief liegen – etwa bei
     zu kleinen Heizkörpern. Steht ein Raum **mindestens 0,3 K unter Soll mit dem Ventil ab 80 %** und ist er **in
     30 min nicht um 0,2 K gestiegen**, hebt HeatPilot den Vorlauf um **2 K je 15 min** (der erste Schritt sofort) bis
     zur Obergrenze. Steigt der Raum wieder, bleibt die Stufe; wartet kein Raum mehr so (am Soll, Ventil zu), geht es in
     2-K-Schritten je 15 min zurück. Einmal erkannt, bleibt ein Raum dabei, solange er 0,2 K unter Soll und sein Ventil
     über 50 % ist (Thermostate pendeln). Das gilt unabhängig vom Takten; andere Räume werden davon nicht überheizt,
     ihre Thermostate machen zu. Räume mit Fußbodenheizung zählen nicht (träge, ihre Grenze gilt ohnehin). Im Ziel
     steht z. B. „Zuschlag +4 K (Flur, Wohnzimmer kommen nicht hoch)“.
  4. **Takt-Boost:** Taktet der Kessel stark (mindestens 3 Brennerstarts in der letzten Stunde, Läufe im Mittel unter
     10 min) und ist ein Ventil offen, hebt HeatPilot den Vorlauf in **2-K-Schritten bis +10 K** über den Wert aus
     1.–3. (der erste Schritt sofort, dann je 10 min) – zurück in 2-K-Schritten 10 min, nachdem das Takten aufgehört
     hat. Seit 0.50 hängt der Boost nur am Takten: Ob ein Raum unter Soll wartet, spielt keine Rolle mehr (das macht
     der Zuschlag) – vorher sprang er bei langen Pausen nicht an (zu wenig Starts) und endete beim Takten nicht, solange
     ein Raum sein Soll nie erreichte.
  5. **Fußboden-Grenze hart:** nie über der Grenze, die gerade gilt – auch bei Zuschlag, Boost und Vorheizen. Liegt sie
     unter 35 °C, ist der Heizbetrieb aus (darunter schreibt HeatPilot keinen Vorlauf).
  6. **Obergrenze:** der höchste Vorlauf der Einstellungen; das Ergebnis in ganzen Grad.
- **Warmwasser lädt** (oder Sonderladung bzw. Desinfektion): Das Vorlauf-Soll bleibt unberührt – der Kessel lädt den
  Speicher über das 3-Wege-Ventil; auch die Prüfung wartet so lange. Nach einer Sonderladung bzw. Desinfektion
  (seit 0.56.5) bleibt das so, bis das Vorlauf-Soll des Kessels wieder niedrig ist (höchstens 4 h) – die Meldung fällt beim Halten der
  Temperatur zwischendurch weg, der Kessel heizt aber weiter. Seit 0.49.3 zählt als Ladung auch, wenn der
  Kessel sein Lade-Soll schon zeigt, bevor „lädt“ gemeldet wird (mindestens 5 K über dem eigenen Vorlauf-Soll, während
  der Speicher unter seinem Soll ist), und noch 3 min nach der Meldung – ein Kessel ohne Regler setzt sein Soll für die
  Ladung selbst (z. B. 75 °C). Das ist dann weder ein Fehlschlag noch ein Anlass für das Schutz-Schreiben noch ein
  fremder Schreiber. Erneuert wird nur das eigene Soll, nie ein Lade-Soll, das noch steht.
- **Kein Ziel rechenbar** (z. B. Außentemperatur fehlt, noch kein Raummodell): HeatPilot schreibt nichts, der Kessel
  bleibt auf dem letzten Wert; hält das 15 min an, kommt eine Meldung („kein Vorlauf-Soll rechenbar“). Ist das Ziel
  wieder rechenbar, geht es normal weiter. Einen Rückfall-Vorlauf gibt es seit 0.59 nicht mehr.
- **Prüfung nach 60 s:** Zeigt der Kessel den Wert (Vorlauf ±0,5 K, Heizbetrieb gleich)? Gelesen wird die Entität in
  Home Assistant bzw. EMS-ESP direkt (höchstens 90 s alt); widersprechen sich beide, ist der Wert unklar – geprüft
  wird bis 5 min nach dem Schreiben weiter. Nicht übernommen: Fehlschlag im Protokoll, Hinweis, Meldung und
  Problem-Sensor; nach **3 Fehlschlägen in Folge pausiert** Weg B, bis du ihn mit **„wieder einschalten“** freigibst.
- **Fremder Schreiber:** Ändert sich Vorlauf-Soll oder Heizbetrieb, ohne dass HeatPilot geschrieben hat, stehen
  Hinweis und Meldung da („Regler noch am Bus oder eine Automation?“); nach **2 fremden Änderungen in 1 h pausiert**
  Weg B – zwei Schreiber sollen nicht gegeneinander schreiben. Ändern sich Vorlauf-Soll und Heizbetrieb zugleich, zählt
  das als eine Änderung.
- **Gesperrt nach dem Schreiben:** Antwortet EMS-ESP länger nicht (oder ist der Regler wieder angeschlossen), schreibt
  HeatPilot nichts; nach 15 min kommt eine Meldung – der Kessel behält solange den letzten Wert.
- **„Heizen abschalten“ an:** EMS-ESP hält die Heizung dann selbst aus und überschreibt das Vorlauf-Soll – HeatPilot
  schreibt nicht (Hindernis), bis du es in EMS-ESP bzw. Home Assistant ausschaltest. HeatPilot schaltet es nicht.
- **Zurückschalten** auf Aus oder Trockenlauf: HeatPilot schreibt nichts mehr (seit 0.59 kein Rückfall-Vorlauf) und
  erinnert daran, den Regler wieder anzuschließen bzw. den Kessel selbst zu stellen.

**Antipendelzeit anpassen** (seit 0.48, Schalter in der Karte, Standard **aus**). Die Antipendelzeit (Taktsperre,
EMS-ESP `burnminperiod`, in Home Assistant z. B. `number.boiler_burner_min_period`) hält den Brenner nach dem Abschalten
aus. Fest auf 25 min blockiert sie Starts auch dann, wenn gerade genug Wärme abgenommen wird. Mit dem Schalter führt
HeatPilot sie im selben 30-s-Schritt wie das Vorlauf-Soll mit:

- **am kürzesten, 5 min** (seit 0.57.1), wenn Räume auf Wärme warten und der Kessel nicht mehr als 6-mal je Stunde
  startet – der Kessel zählt die Sperre ab dem Schreiben neu, mit 5 min springt er nach einer Anforderung bald an,
- **kurz, 10 min**, solange die Räume mit offenem Ventil die kleinste Brennerleistung beim Ziel-Vorlauf abnehmen
  (dieselbe Abnahme-Rechnung wie für den Mindest-Vorlauf) und der Kessel nicht taktet – der Brenner kann dann auf
  kleinster Stufe durchlaufen, und geht er doch aus, wartet ein neuer Start nicht 25 min. 10 min lassen auch im
  ungünstigsten Fall höchstens 6 Starts je Stunde zu,
- **lang = der Grundwert**: der Wert am Kessel beim Einschalten, **mindestens 25 min** (höchstens 60 min), wenn die
  Abnahme nicht reicht oder unbekannt ist (Mindestleistung, Hausmodell) oder der Kessel taktet (ab 3 Starts in 1 h mit
  Läufen unter 10 min),
- **unverändert** ohne Wärmebedarf („nur Pumpe“, Heizbetrieb aus), ohne rechenbares Vorlauf-Soll und während einer
  Warmwasser-Ladung.

Geschrieben wird nur bei „HeatPilot steuert“ (bei „Beobachten“ steht im Protokoll „würde Antipendelzeit … schreiben“),
nur über Home Assistant auf **die eine** `number`-Entität der Antipendelzeit (leer = erkannt; nur `number` in Minuten;
wählbar unter Einstellungen → Haus → Experte → Kessel über HeatPilot → „Antipendelzeit (Entität)“), **ganze Minuten 5–60** und
innerhalb der Grenzen der Entität – alles andere weist `Control` hart ab. Nur bei Änderung ab **5 min**, höchstens
**alle 15 min**; **60 s** danach prüft HeatPilot, ob der Kessel den Wert zeigt (3 Fehlschläge in Folge: Pause nur dieser
Funktion). Ändert jemand anderes den Wert, steht das im Protokoll und als Meldung, der neue Wert gilt als Grundwert und
die 15 min beginnen neu (2 fremde Änderungen in 1 h: Pause). Gesperrt unter denselben Bedingungen wie der Kessel (Regler
am Bus, „Heizen abschalten“ an, Entitäten, Pause). Eine Pause endet, wenn du den Schalter aus- und wieder einschaltest.
**Ausschalten** (Schalter aus oder „Kessel über HeatPilot“ nicht mehr „HeatPilot steuert“) schreibt einmal den Grundwert
zurück – nur, wenn HeatPilot seit dem Einschalten geschrieben hat, und nicht mit Regler am Bus. Fällt HeatPilot aus,
bleibt der zuletzt geschriebene Wert stehen (nie unter 5 min). Die Statusbox zeigt „Antipendelzeit: 10 min (Abnahme
reicht: …)“ bzw. „würde …“ bei „Beobachten“, Übersicht → Status dieselbe Zeile, solange die Funktion an ist; das Protokoll
die Zeilen „Antipendelzeit gesetzt/würde schreiben/nicht übernommen/fremd geändert/pausiert/zurück“. Abfrage: `period`
in `GET api/boiler`.

**Wächter in Home Assistant.** Fällt HeatPilot aus, bleibt der letzte Vorlauf am Kessel stehen; ohne Wärmeabnahme
fällt der Kessel zurück und bekommt keine neue Anforderung. Einen festen Vorlauf stellt nur der Drehschalter am Kessel
ein, nicht Home Assistant – deshalb **meldet** der Wächter seit 0.59 nur noch (bis 0.58 setzte er einen
Rückfall-Vorlauf). HeatPilot setzt jede Minute den Sensor `sensor.heatpilot_heartbeat` (Zeitstempel); eine Automation
in Home Assistant schickt eine Meldung aufs Handy, sobald er sich eine Stunde nicht ändert. Du legst sie selbst an: Der
Knopf **„Anleitung Wächter“** im Kessel-Block zeigt sie als YAML mit deinen Benachrichtigungszielen (ohne Ziel: Meldung
in Home Assistant) – „YAML kopieren“, in Home Assistant eine neue Automation erstellen, ⋮ › „In YAML bearbeiten“, alles
ersetzen, speichern. Wer lieber klickt, findet darunter aufklappbar die Schritte:

1. In Home Assistant „Einstellungen“ › „Automationen & Szenen“ › „Automation erstellen“ › „Neue Automation erstellen“.
2. „Auslöser hinzufügen“ › „Entität“ › „Zustand“: Entität „HeatPilot Heartbeat“ (`sensor.heatpilot_heartbeat`), „Von“
   und „Nach“ leer lassen, „Für“ `01:00:00`.
3. „Bedingung hinzufügen“ › „Entität“ › „Zustand“: Entität „HeatPilot Heartbeat“, Attribut „kessel“, Zustand
   „heatpilot“ – der Wächter meldet nur, solange HeatPilot den Kessel führt.
4. „Aktion hinzufügen“ › „Benachrichtigungen“: an dein Handy, z. B. „HeatPilot antwortet seit 60 min nicht – Kessel
   prüfen; wenn es kalt wird, Drehschalter am Kessel auf Festwert stellen“.
5. Speichern (Name z. B. „HeatPilot-Wächter“). Fehlt der erkannten Automation die Bedingung auf „kessel“, sagt
   „Bereit für die Steuerung?“ das dazu.

**Notfall: HeatPilot fällt aus.** Kommt die Meldung des Wächters (oder wird es kalt, und die HeatPilot-Seite antwortet
nicht):

1. Add-on neu starten (Einstellungen › Add-ons › EMS HeatPilot › Neu starten) bzw. den Raspberry Pi neu starten.
2. Hilft das nicht: am Kessel den **Drehschalter für die Vorlauftemperatur auf einen festen Wert** stellen (z. B. die
   Stellung für 50–55 °C; bei Fußbodenheizung ohne Mischer höchstens deren Grenze). Der Kessel heizt dann selbst, die
   Thermostate regeln die Räume.
3. Läuft HeatPilot wieder, den Drehschalter zurückstellen – HeatPilot übernimmt das Vorlauf-Soll wieder.

HeatPilot liest die Automationsliste alle 10 min (nur lesen) und erkennt den Wächter am Auslöser; bis dahin zeigt
„Bereit für die Steuerung?“ „Wächter nicht erkannt“ als Einschränkung, eine ausgeschaltete Automation ebenso. Nach
einem Neustart von Home Assistant fehlt der Heartbeat, bis HeatPilot ihn wieder setzt (höchstens 1 min). Ausprobieren:
HeatPilot anhalten und nach einer Stunde nachsehen, ob die Automation lief.

**Ablauf zum Regler-Abbau:**

1. **Trockenlauf** einige Tage, den Vergleich in der Vorschau ansehen (Plan gegen das, was der Regler setzt).
2. **Wächter** in Home Assistant anlegen (oben).
3. Schalter auf **HeatPilot setzt** (beim Speichern fragt HeatPilot einmal nach Trockenlauf und Wächter) – gesperrt,
   solange der Regler dran ist („Regler am Bus“).
4. **Regler abbauen** und EMS-ESP neu starten, damit es den Regler nicht mehr meldet. HeatPilot schreibt frühestens
   30 min, nachdem EMS-ESP den Regler zuletzt gemeldet hat (und nach 5 min „kein Gerät thermostat“).
5. HeatPilot **schreibt** – im Protokoll steht jeder Schreibvorgang mit Grund. In den ersten Stunden beobachten, ob das
   Vorlauf-Soll ohne Regler stehen bleibt: Manche Kessel erwarten das Soll regelmäßig vom Regler und fallen sonst auf
   eigene Werte zurück. Prüfungen, die nach einer Weile „nicht übernommen“ melden, oder fremde Änderungen ohne
   Automation deuten darauf hin. Als zusätzlichen Schutz die Heiztemperatur am Kessel (`heatingtemp`, Obergrenze des
   Kessels) höchstens auf die strengste Fußboden-Grenze stellen.

**Anzeige:** Die Karte in den Einstellungen zeigt unter dem Schalter, welche zwei Entitäten gelten (gewählt unter
Einstellungen → Haus → Experte → Kessel über HeatPilot), die Wahl
**„Wenn kein Raum Wärme braucht“** (Pumpe läuft weiter | Heizbetrieb ganz aus, seit 0.46.5), die
Warnhinweise (der Warnkasten „Regler am Bus“ nur, solange laut Regler-Prüfung ein Regler am Bus ist oder es unbekannt
ist – sonst, seit 0.48, nur ein kurzer Hinweis auf den Wächter) und eine Statusbox: was der Kessel gerade zeigt, was HeatPilot will und warum (Bedarf, Abnahme, Zuschlag, Takt-Boost,
Fußboden-Grenze), die letzte Entscheidung, zuletzt geschrieben und geprüft, fremde Änderungen, Regler am Bus, den
Wächter mit dem Knopf „Anleitung Wächter“ und – bei einer Pause – „wieder einschalten“. Heizen und Übersicht zeigen eine
Statuszeile („Kessel: HeatPilot · Vorlauf 42 °C (Wohnzimmer hält 21,0 °C)
· Boost +4 K“, bei „Beobachten“ „Kessel: Beobachten · würde Vorlauf 42 °C setzen (…)“, sonst „Kessel: gesperrt – Regler
am Bus …“ bzw. „Kessel: pausiert – …“), das Protokoll (Live → Diagnose) auch die Kessel-Zeilen (Vorlauf-Soll, Heizbetrieb
an/aus, würde schreiben, nicht übernommen, fremde Änderung, pausiert, wieder eingeschaltet, zurückgeschaltet). Die
Vorschau sagt bei „HeatPilot setzt“ ohne Pause und Sperre „schreibt“ statt „würde schreiben“ – sie zeigt den Plan aus dem
Bedarf; Abnahme-Untergrenze, Zuschlag und Takt-Boost rechnet HeatPilot live dazu. Abfragen: `GET api/boiler` (Status und
Bereitschaft), `POST api/boiler/resume` („wieder einschalten“ – ohne Pause Antwort 409, es ändert sich nichts).

## Benachrichtigungen

Seit 0.38 meldet HeatPilot Probleme an Ziele in Home Assistant – **Einstellungen → Haus → Benachrichtigungen**
(bis 0.39 eine eigene Karte), Liste mit Häkchen:

- **notify-Entitäten** (Home Assistant ab 2024.6, z. B. `notify.handy`) – geschickt mit der Aktion
  `notify.send_message`,
- **Dienste** wie `notify.mobile_app_…` (die Handy-App, ältere Integrationen) – direkt als Dienst, auch bei
  älteren Home-Assistant-Versionen ohne notify-Entitäten.

Seit 0.41 stehen die Dienste der Handy-App (`notify.mobile_app_…`) oben in der Liste und sind als „empfohlen“
markiert – sie kommen am zuverlässigsten an. Scheitert `notify.send_message` auf eine Entität (nicht am Titel, kein
Zeitüberlauf) und gibt es den Dienst `notify.mobile_app_<name>` dazu, schickt HeatPilot die Meldung einmal über ihn –
bei einem Zeitüberlauf oder einer während des Sendens getrennten Verbindung zu Home Assistant nicht (sie könnte schon
angekommen sein), und auch nicht, wenn dieser Dienst selbst als Ziel gewählt ist (er bekommt die Meldung ohnehin). Scheitert die Handy-App, nennt der Fehler die üblichen
Ursachen: in iOS bzw. Android die Mitteilungen der Home-Assistant-App erlauben, in der App die Push-ID zurücksetzen,
im Protokoll von Home Assistant nach „mobile_app“ suchen.

Ziele heißen wie in Home Assistant (`notify.<name>`, Kleinbuchstaben) – ein anders geschriebenes Ziel lässt sich
nicht speichern. Gemeldet wird nur an die gewählten und gespeicherten Ziele; „Testnachricht senden“ schickt eine Nachricht an
genau diese (`POST api/notify/test`) – solange geänderte Häkchen nicht gespeichert sind, ist der Knopf gesperrt.
Gewählte Ziele, die es in Home Assistant nicht mehr gibt, stehen mit „fehlt“ in der Liste zum Abwählen. Ohne Ziele gibt es keine Meldungen – der Problem-Sensor läuft trotzdem.

Was gemeldet wird: Warmwasser-Soll nicht übernommen, Weg W pausiert (3 Fehlschläge oder 2 fremde Änderungen in 1 h),
anderer Schreiber am Speicher-Soll, Thermostat-Schreibfehler der Raumsteuerung ab drei
in Folge und seit 0.41 dasselbe für den Kessel (Weg B: Vorlauf-Soll bzw. Heizbetrieb nicht übernommen, pausiert, fremde
Änderung, seit 15 min gesperrt bzw. abgewiesen, kein Vorlauf-Soll rechenbar). Titel „HeatPilot“, Text kurz und konkret. Je
Ereignis eine Meldung – nicht bei jedem Takt – und einmal „wieder in Ordnung“; höchstens eine Meldung je Art in
6 h.

**Problem-Sensor** `binary_sensor.heatpilot_problem`: an, solange ein Problem offen ist, mit den Gründen im
Attribut `reasons` (Liste) – für eigene Dashboards und Automationen.

## Was das Add-on macht

**Beobachten und lernen.** Im Standard greift es nicht von selbst in die Heizung ein:
- An EMS-ESP sendet es ausschließlich Lese-Anfragen (GET), ein Token wird nicht benötigt –
  auch bei „HeatPilot steuert“, bei „HeatPilot setzt“ und für die Kesselsteuerungen in „Bereit für die Steuerung?“.
- In Home Assistant legt es von selbst nur seine eigenen Sensoren an: `sensor.heatpilot_*` und den
  Problem-Sensor `binary_sensor.heatpilot_problem` (an, solange ein Problem offen ist).
- Ausnahme, nur auf deinen Knopfdruck im Reiter „Heizen“: Tado-Timer, Soll und
  Betriebsart der gewählten Räume (siehe oben). Jeder andere Schreibversuch wird im
  Code hart abgewiesen – auch auf den Heizkreis von EMS-ESP, selbst wenn er als Raum gewählt ist.
- Erst mit dem Schalter **HeatPilot steuert** (Einstellungen → Räume) schreibt es die
  Raum-Solltemperaturen der Räume mit „macht mit“ selbst – mit denselben drei Diensten,
  denselben Räumen und 5–25 °C (siehe „Steuerung“).
- Erst mit dem eigenen Schalter **„HeatPilot setzt“** (Einstellungen → Kessel, seit 0.38) schreibt es am
  Kessel das Speicher-Soll: `number.set_value` über Home Assistant, nur auf die eine eingestellte Entität des
  Warmwasser-Solls, nur ganze Grad zwischen Minimum und Maximum, nur bei Änderung ab 1 K, mit Haltezeit und Prüfung
  nach 60 s (siehe [Weg W](#warmwasser-über-heatpilot-weg-w)). Jede andere `number`-Entität weist der Code hart ab.
- Meldungen schickt es nur an die gewählten Ziele (Einstellungen → Haus → Benachrichtigungen, siehe dort).
- Das Ventilverhalten (seit 0.39) lernt und zeigt es nur; was es mit den Thermostaten als Stellglied tun würde,
  protokolliert es im Trockenlauf – geschrieben wird dafür nichts (siehe [Ventilverhalten](#ventilverhalten)).

1. **Daten übernehmen:** Beim ersten Start holt sich das Add-on selbst die
   vorhandenen Daten aus Home Assistant. Ein Export ist nicht nötig.
   - Langzeitstatistik (stündlich, bleibt in HA dauerhaft): Gaszähler,
     Außenfühler, Tado-Raumtemperaturen → das Hausmodell kann sofort aus
     Monaten an Daten lernen.
   - Recorder-Historie (Standard 10 Tage, siehe `purge_keep_days`): alle
     Einzelwerte → Raummodelle, Brennertakte, Anwesenheitsmuster.
   - Wird der Lernstand nach einem Update neu berechnet, lernen die
     Anwesenheitsgewohnheiten zuerst aus den eigenen gespeicherten Messwerten
     (auch älter als die Recorder-Historie), danach aus der HA-Historie.
2. **Laufend sammeln:** alle 5 Minuten ein kompakter Datensatz in
   `/data/heatpilot.db` (SQLite, wenige MB pro Jahr).
3. **Lernen (maschinelles Lernen, online):** Die Modelle passen sich mit
   jeder Messung an, ältere Daten verlieren langsam an Gewicht. So folgt das
   Modell Jahreszeiten und Änderungen am Haus.
   - *Hausmodell* (stündlich): Wärmeverlust in W/K, Wärmespeicher, **Fremdwärme**
     (Sonne, Personen, Geräte – darunter liegt die Heizgrenze), Heizlast jetzt und bei
     Auslegungstemperatur. Gelernt wird nur in der Heizperiode (24-h-Mittel außen bis
     15 °C) und nur aus Stunden ohne Warmwasserladung und ohne Fremdwärme-Ereignis in einem
     Raum (Kamin, Ofen – siehe unten); ein Tag mit 3 h oder mehr davon zählt nur als Vortag,
     wie ein Urlaubstag. Wärmeverlust und Fremdwärme kommen
     ab 21 Heiztagen aus Tagesmitteln (mit dem Vortag und dem Mittel der Woche davor), weil
     sich Wärmeverlust und Speicher in Stundenwerten bei trägen Häusern (Fußboden, schwere
     Wände) kaum trennen lassen und die Wände über Tage nachwirken. Die
     **Trägheit** misst ein eigenes Modell in gasfreien Nächten (wie schnell das Haus ohne
     Heizung auskühlt); die schnelle Phase direkt nach dem Abschalten lernt es je Anlage.
     Ausführlich mit Formeln: [docs/hausmodell.md](https://github.com/TheInGoF/ems-heatpilot/blob/main/docs/hausmodell.md)
   - *Raummodell* (je Tado-Raum, halbstündlich): wie schnell der Raum bei
     welcher Vorlauftemperatur warm wird und wie schnell er auskühlt. Es startet
     mit der gemessenen Trägheit des Hauses, sonst mit Werten eines typischen Hauses
     (Auskühlen ≈ 55 h), und einer Unsicherheit je
     Parameter, damit ein einzelner auffälliger Schritt nichts umwirft. Das Gedächtnis
     reicht etwa 6 Wochen zurück. Im Herbst, bei kleinem Abstand innen–außen, lässt
     sich das Auskühlen kaum von Fremdwärme (Nachbarräume, Rohre) trennen. Räume, die
     die Leitplanken nicht bestehen, zeigen deshalb „lernt noch“. Wird ein Raum deutlich
     wärmer, ohne dass der Heizkörper es erklären kann (Kamin, Backofen, viele Gäste), gilt
     das als **Fremdwärme-Ereignis**: diese Schritte lernt das Raummodell nicht (siehe
     „Fremdwärme“ unten).
     Seit 0.53 lernt jeder Raum die Form seines Heizkörpers: Wärme ∝ (Vorlauf − Raum)^n × Ventilöffnung^p mit
     n = 1,0 / 1,3 / 1,6 / 2,0 und p = 1 oder 0,5 (p = 0,5: schon ein wenig geöffnetes Ventil bringt viel Wärme). Alle
     8 Formen lernen mit; die mit dem kleinsten Vorhersagefehler in Schritten mit Heizbetrieb gilt – erst ab etwa 48
     solchen Schritten und nur, wenn sie die gültige um 2 % schlägt, bis dahin linear wie früher. An echten Daten
     (7 Tage, 6 Räume) sank der Fehler der 1-h-Vorhersage beim Heizen damit um rund ein Viertel; bei mehr als 50 °C
     Vorlauf war das lineare Modell um 0,5–1 K/h zu langsam. Die Heizwirkung zeigt die Diagnose bei 20 K über Raum und
     offenem Ventil (vergleichbar über alle Formen), dazu Exponent und Ventilkennlinie. Modelle älterer Versionen
     laden als lineare Form; beim ersten Start wird der alte Stand gesichert und der Raum aus den gespeicherten Daten
     neu gelernt – der erste Start dauert dadurch etwas länger.
   - *Ventilmodell* (je Raum, halbstündlich, seit 0.39): wie das Thermostat sein Ventil abhängig vom Abstand
     zum Soll stellt – Kennlinie, Sprungantwort, kleines Reglermodell bzw. Schaltpunkte (siehe
     [Ventilverhalten](#ventilverhalten)). Lernt beim ersten Start aus den gespeicherten Messwerten nach.
   - *Anwesenheit* (je Person): Wochenraster der Gewohnheiten und
     Heimfahrt-Geschwindigkeit. Annäherung und Ankunftszeit berechnet das
     Add-on selbst aus den GPS-Daten der `person.*`-Entitäten.
   - *Zeiten und Temperaturen* (stündlich, letzte 4 Wochen ohne Urlaub): wann
     Tado abends absenkt (gemessen an den zwei Stunden davor – so zählt auch ein
     Zeitplan, der nachmittags aus ist und erst abends heizt; heizt Tado nachmittags
     wirklich, höchstens am Tagesniveau – ein Abendkomfort über dem Tag ist keine
     Absenkung) und morgens wieder weitgehend zu diesem Niveau hochheizt, Nacht- und
     Abwesenheits-Soll. Die Werte unter „Komfort & Strategie“ sind nur Startwerte;
     gelernte Zeiten gelten ab 7 erkannten Nächten (höchstens 3 h vom Startwert
     entfernt), Temperaturen nie kälter als eingestellt und höchstens 3 K wärmer.
     Abschaltbar mit „Weiterlernen“.
4. **Auswerten:** Weboberfläche „HeatPilot“ in der Seitenleiste und Sensoren
   in Home Assistant.

## Sensoren

| Sensor | Bedeutung |
|---|---|
| `sensor.heatpilot_waermeverlust` | gelernter Wärmeverlust des Hauses (W/K) |
| `sensor.heatpilot_heizlast_aktuell` | benötigte Leistung jetzt (kW) |
| `sensor.heatpilot_normheizlast` | Heizlast bei Auslegungs-Außentemperatur (kW) |
| `sensor.heatpilot_haus_traegheit` | Zeitkonstante des Hauses (h) |
| `sensor.heatpilot_vorlauf_empfehlung` | Vorlauf, der die Soll-Temperaturen hält (°C) |
| `sensor.heatpilot_brennerstarts_heute` | Starts, mittlere Lauf- und Pausenzeit |
| `sensor.heatpilot_taktsperre_wartezeit_heute` | Minuten, in denen Räume Wärme wollten, die Taktsperre aber blockierte |
| `sensor.heatpilot_startverzoegerung_heute` | Minuten, die Räume Wärme wollten, bis der Regler auf „Tag“ ging (Wartephasen bis 15 min) |
| `sensor.heatpilot_nachlauf_ohne_bedarf_heute` | Minuten (und kWh), die der Brenner lief, obwohl laut Tado-Soll kein Raum mehr Wärme brauchte |
| `sensor.heatpilot_laufzeit_je_start` | mittlere Laufzeit je Heizstart heute, Attribut: seit Einbau |
| `sensor.heatpilot_gaskosten` | Gaskosten der letzten 12 Monate inkl. anteiligem Grundpreis |
| `sensor.heatpilot_ersparnis_schaetzung` | geschätzte Ersparnis durch Absenken (% des Heizgases, ohne Warmwasser), kWh/€ als Attribute |
| `sensor.heatpilot_ww_empfehlung` | sparsamste Speichertemperatur jetzt (nur Empfehlung) mit Begründung |
| `sensor.heatpilot_ww_bereitschaftsverlust` | was das Warmhalten gerade kostet (W Gas), Attribut je Temperatur |
| `sensor.heatpilot_ww_ersparnis_schaetzung` | Ersparnis der Warmwasser-Strategie (% der Warmwasser-Energie) |
| `sensor.heatpilot_naechste_ankunft` | vorhergesagte Ankunft |
| `sensor.heatpilot_vorheizen_start` | wann das Vorheizen beginnen müsste |
| `binary_sensor.heatpilot_problem` | seit 0.38: an, solange ein Problem offen ist; Gründe im Attribut `reasons` (siehe [Benachrichtigungen](#benachrichtigungen)) |
| `sensor.heatpilot_heartbeat` | seit 0.41: Zeitstempel, jede Minute neu – für den Wächter in Home Assistant (siehe [Kessel über HeatPilot](#kessel-über-heatpilot-weg-b)) |

Hinweis: Diese Sensoren werden vom Add-on gesetzt und sind nach einem
Neustart von Home Assistant bis zum nächsten Takt (max. 5 min) „unbekannt“.

## Räume

Im Reiter Einstellungen → **Räume** stehen alle Thermostate (climate-Entitäten)
als Tabelle. Häkchen = Raum wird gelernt. Je Raum optional: eigener Name,
„macht mit“ und Komforttemperatur (für „HeatPilot steuert“), Raum und Raumregler
(mehrere Thermostate als ein Raum, siehe „Steuerung“), Fußbodenheizung (Anschluss und höchster
Vorlauf, siehe [Fußbodenheizung je Raum](#fußbodenheizung-je-raum)), Voreinstellung und Stufen des
Ventils (siehe „Hydraulischer Abgleich“), Ventil-/Heizleistungs-Sensor (%),
Temperatursensor (für die Langzeitstatistik) und Feuchtesensor. Unter jeder Entität
stehen Bereich und Gerät aus Home Assistant; die Tabelle ist nach Bereich und Name
sortiert. Seit 0.51 je Raum dazu die **Führung**: „Temperatur“ (Standard – HeatPilot schreibt das Soll ans
Thermostat) oder „ventilgeführt“ – dann stellt HeatPilot die **Ventilöffnung in Prozent selbst**, über die
number-Entität im Feld **Ventilstellung** (z. B. Shelly BLU TRV, 0–100 %). Siehe
[Ventilgeführte Räume](#ventilgeführte-räume-seit-051). Ohne Angaben schlägt die automatische Erkennung die Tado-Räume samt
Sensoren vor – für jedes Thermostat mit Gerät in Home Assistant (Tado-Cloud, HomeKit,
andere): Name und Raum aus dem Bereich, Temperatur/Feuchte/Heizleistung aus den
Sensoren desselben Geräts, Raumregler bei Wandthermostaten. Vorschläge füllen nur
leere Felder („Bereiche übernehmen“ trägt den Bereich in leere Raum-Felder ein);
„macht mit“ setzt nie ein Vorschlag, nur du. Was je Raum gilt und was fehlt, zeigt auch Einstellungen → Haus → Experte →
Räume.

Ist der Regler abgebaut („Regler ist abgebaut“ angehakt, oder EMS-ESP meldet kein Gerät „thermostat“), stehen
seine Werte auf Einstellungen → Haus → Experte und in Diagnose → Alle Sensoren als „Regler abgebaut – nicht gebraucht“ und
zählen nicht als Problem (seit 0.52.2).

## Heizen im Lovelace-Dashboard (Gast-Ansicht, seit 0.52)

Die HeatPilot-Seite läuft über Ingress und ist damit nur für Administratoren erreichbar. Damit alle im Haushalt die
Heizung bedienen können, gibt es die **Gast-Ansicht**: derselbe Server, direkt am **Port 8099** – sie zeigt nur den
Reiter „Heizen“ (Boost, Soll, zurück zum Zeitplan), ohne Einstellungen, Übersicht, Live, Diagnose und ohne die
Pause. Alles außer den Heizen-Endpunkten weist der Server für Gäste hart ab (403), unabhängig von der Oberfläche.

Einrichten:

1. In den Einstellungen des Add-ons unter **Netzwerk** dem Port 8099 einen Host-Port geben (z. B. 8099) und speichern.
2. In Lovelace eine **Webpage-Karte** anlegen: `http://<ha-adresse>:8099/`.

Hinweise: Der Port hat **keine eigene Anmeldung** – nur im eigenen, vertrauten Netz freigeben und **nie ins Internet
weiterleiten**. Öffnest du Home Assistant über **https** (z. B. Nabu Casa), blockt der Browser die http-Karte
(gemischte Inhalte) – dann das Dashboard im Heimnetz über http öffnen oder dem Port einen eigenen
https-Reverse-Proxy geben. Über Ingress (HeatPilot-Seitenleiste) bleibt alles wie bisher.

## Ventilgeführte Räume (seit 0.51)

Hat ein Raum ein Stellventil mit eigener Prozent-Entität (z. B. **Shelly BLU TRV** über das BLU Gateway), kann
HeatPilot die Öffnung direkt stellen, statt ein Soll zu schreiben – Einstellungen → Räume → Einzelheiten →
**Führung „Ventil“** plus **Ventilstellung** (number, 0–100 %). Das Wunsch-Soll je Raum (Komfort, Nacht, Abwesenheit,
Vorheizen) kommt weiter aus der Steuerung; neu ist nur das Stellglied:

- **PID mit Vorsteuerung aus dem Raummodell:** Die Vorsteuerung ist die Öffnung, die das Soll beim aktuellen Vorlauf
  dauerhaft hält (seit 0.53 mit der Form des Raummodells – bei Öffnung^0,5 kleinere Öffnungen als linear); der
  PI-Teil korrigiert den Rest. Die **Verstärkung lernt je Raum**: Pendelt der Raum (4
  Vorzeichenwechsel über ±0,15 K in 2 h), sinkt sie um 20 %; bleibt er trotz Luft nach oben 90 min mehr als 0,3 K
  unter Soll, steigt sie um 15 % (0,3–3,0).
- **Schreibregeln:** nur bei Änderung ab 3 Punkten, höchstens ein Schreiben je 5 min – Sprünge ab 15 Punkten sofort
  (schont Motor und Batterie). Geschrieben wird nur über die eine zugewiesene Entität, nur ganze Prozent 0–100,
  innerhalb der Grenzen der Entität; alles andere weist HeatPilot hart ab. 60 s nach dem Schreiben liest HeatPilot
  zurück; nach **3 Fehlschlägen in Folge pausiert der Raum 1 h** (Meldung – Gateway/Batterie prüfen). **Wer von Hand
  dreht, behält das Ventil 1 h.** Die Nutzer-Pause der Steuerung hält auch diese Schiene an. Ohne Raumtemperatur
  stellt HeatPilot nach 30 min einmal den **sicheren Wert 40 %** (kein Frost, kein Überheizen) und wartet.
- **Automatischer hydraulischer Abgleich:** Bleibt ein Raum am Anschlag unter Soll (mindestens 30 min am Tag),
  während ein anderer sein Soll mit höchstens 60 % Öffnung hält (mindestens 1 h), senkt HeatPilot dem leichten Raum
  den **Öffnungs-Deckel** um 5 Punkte je Tag – nie unter 40 %. Bleibt die Unterversorgung 3 Tage aus, gehen die
  Deckel schrittweise zurück auf 100 %. Der Deckel begrenzt nur den PID-Ausgang. Der handgeführte Abgleich
  (Empfehlungen zur Voreinstellung am Ventilunterteil) bleibt für temperaturgeführte Räume.
- Die Soll-Schiene und [Ventile führen](#ventile-führen) fassen ventilgeführte Räume nicht an; **gelernt** wird der
  Raum weiter wie bisher – das gestellte Prozent ist sein Ventilwert. Die Raumkarte zeigt z. B.
  „Ventil · komfort 21° · 45 %“.

## Räume wechseln (z. B. Tado-Cloud → HomeKit)

Messwerte und Modelle eines Raums hängen an seinem Schlüssel – der climate-Entität seines Thermostats. Wechselt die
Integration (Tado-Cloud → HomeKit, Thermostat neu gekoppelt oder getauscht), hat derselbe Raum eine neue Entität: Raum-
und Ventilmodell begännen von vorn, die Messwerte der alten Entität lägen ohne Raum in der Datenbank, und der neue
Temperatursensor hat in Home Assistant erst wenige Tage Langzeitstatistik. Seit 0.42.1 geht dabei nichts mehr
verloren:

- **Raumwechsel speichern** holt nur die Historie der neuen bzw. geänderten Räume aus Home Assistant nach (Temperatur,
  Soll, Ventil, Feuchte – ein schon gespeicherter Messpunkt bleibt, wie er ist) und lernt dann aus den gespeicherten
  Daten neu. Die Langzeitstatistik wird dafür nicht neu gelesen, nichts wird gelöscht. Nur geänderte
  Leistungssensoren lesen die Historie wie bisher ganz neu ein (zusammenführend, siehe nächster Punkt).
- **Die eigene Datenbank ist die Wahrheit (seit 0.44):** Liest HeatPilot aus Home Assistant (erster Start,
  „Lücken aus Home Assistant füllen“, Historie neuer Räume, „Hausdaten wiederherstellen“, Langzeitstatistik), löscht
  und überschreibt das nichts Eigenes. Ein gespeicherter Messpunkt, Raum- und Personenwert bleibt, wie er ist; Home
  Assistant ergänzt nur fehlende Zeitpunkte und leere Werte. Brennerereignisse aus Home Assistant kommen nur in
  Zeiträume, in denen HeatPilot den Brenner nicht selbst verfolgt hat. Eine Stunde behält ihre gespeicherte
  Außentemperatur, ihren Gaswert und ihre Anteile, wenn die neue Quelle dazu nichts hat, und eine gespeicherte
  Innentemperatur immer. Warmwasser- und Personenmodelle bleiben beim Lückenfüllen stehen; Haus-, Raum- und
  Ventilmodelle lernen neu.
- **Thermostat gewechselt (seit 0.44):** Jeder Raum hat eine feste Kennung (Feld `id` am Raum-Eintrag, beim ersten
  Start ergänzt). Bekommt ein Raum ein anderes Schlüsselgerät (Tado-Cloud → HomeKit, neues Thermostat), fragt
  HeatPilot unter Räume: **gleiches Thermostat, neue Anbindung** (alle Daten und Gelerntes ziehen um), **anderes
  Thermostat** (Raumwerte ziehen um, das Ventil lernt ab dem Wechsel neu) oder **alles neu lernen** (nichts zieht um,
  die alten Werte bleiben „ohne Raum“ und lassen sich später übernehmen). Bis zur Wahl misst der Raum normal weiter.
  Benennt Home Assistant nur die Entität um (dasselbe Gerät), zieht alles ohne Rückfrage um. „Ventil neu lernen“
  verlernt jederzeit nur das Ventil eines Raums; gelöscht wird dabei nichts, das Verlernte steht im Protokoll.
- **Innentemperatur aus einem Teil der Räume:** Fehlt einer Stunde ein Raum (sein neuer Sensor hat noch keine
  Statistik, für ihn gibt es keinen alten Sensor, das Thermostat meldete nichts), wäre das Mittel der übrigen um ihren
  Abstand zum Hausmittel verschoben – zwei Räume mit ±1,5 K ergäben 1,5 K zu warm, das Hausmodell läge weit daneben.
  Deshalb ersetzt ein Mittel über weniger Räume nie eine gespeicherte Innentemperatur. Fehlt sie, füllt HeatPilot sie
  mit jeder Quelle um ihren üblichen Abstand zum gespeicherten Hausmittel berichtigt (Median über gemeinsame Stunden);
  eine Quelle ohne genug gemeinsame Stunden, mit zu großer Streuung oder ohne Zusammenhang mit dem Hausmittel bleibt
  weg – lieber eine Stunde ohne Innentemperatur als eine falsche. Ohne gespeichertes Hausmittel (erster Start) gilt
  wie bisher das einfache Mittel. Woher die Innentemperatur einer Stunde kommt, merkt sich HeatPilot (Raumwerte bzw.
  welche Statistik).
- **Gespeicherte Daten ohne Raum:** Über der Raum-Tabelle (Einstellungen → Räume) stehen die alten Entitäten mit
  gespeicherten Messwerten, mit Herkunft und Tagen. Gibt es für einen Raum einen sicheren Treffer (gleicher Name, die
  Tado-Zone heißt wie der Bereich, gleicher Bereich in Home Assistant – und die alte Entität hat deutlich mehr Tage),
  steht dort „Gelerntes von … übernehmen“ mit Knopf; jede alte Entität lässt sich außerdem selbst zuordnen
  („übernehmen in …“). Auch eine Entität, die heute nur Mitläufer eines Raums ist (z. B. beide Integrationen im
  selben Bereich), steht dort mit „Mitläufer im Raum …“ – ihre alten Messwerte zählen sonst für kein Raummodell; sie
  lässt sich nur in ihren Raum übernehmen. Hat der Raum zu denselben Zeitpunkten schon eigene Messwerte (die neue
  Entität lief eine Weile parallel), nennen Liste und Rückfrage diese Überschneidung. Nach der Rückfrage („Die
  Messwerte von … werden Raum … zugeordnet, Raum- und Ventilmodell neu berechnet. … Nicht rückgängig zu machen.“)
  ordnet HeatPilot in einem Schritt die Messwerte der alten Entität dem Raum zu; an Zeitpunkten mit eigenem Messpunkt
  bleiben die Werte des Raums, nur was ihm fehlt, kommt aus der alten Entität – eine gemessene Ventilöffnung geht dabei
  vor einer, die nur aus „heizt/heizt nicht“ abgeleitet ist. Ebenso Fremdwärme-Ereignisse, Ventil-Protokoll und
  Hydraulik-Änderungen; Raum- und Ventilmodell lernen daraus neu (in Tagesabschnitten, die Seite bleibt bedienbar),
  „Bereit für die Steuerung?“
  bewertet den Raum neu. Das geht nur mit gespeicherten Einstellungen (sonst „Erst speichern“). Nichts davon wird an
  Home Assistant geschrieben.
- **Alter Temperatursensor (Alias):** Beim Übernehmen merkt sich HeatPilot den Temperatursensor der alten Entität –
  am alten Gerät, solange die alte Integration noch eingerichtet ist, sonst eine °C-Statistik in Home Assistant, deren
  Name zum alten Raum passt (nur bei genau einem Treffer). Für die Langzeitstatistik nimmt HeatPilot je Raum und
  Stunde den aktuellen Sensor und, wo er (noch) keine Werte hat, den alten – beide um ihren Abstand zum Hausmittel
  berichtigt. Auch ohne Übernahme lässt sich der alte Sensor je Raum wählen; ein Sensor, den ein anderer Raum schon
  nutzt (aktueller oder alter), wird abgewiesen.
- **Alter Temperatursensor je Raum** (Einstellungen → Räume, aufklappbar unter „Gespeicherte Daten ohne Raum“; lädt
  erst beim Aufklappen die Liste der Langzeitstatistiken aus Home Assistant): je Raum der aktuelle Sensor, der gemerkte
  alte mit Herkunft („Sensor am alten Gerät“, „Langzeitstatistik mit passendem Namen“, „selbst gewählt“) und eine
  Auswahl – zuerst die Vorschläge des Raums (ein eindeutiger ist vorgewählt), dann alle °C-Statistiken, die kein
  anderer Raum nutzt; Außenfühler und der aktuelle Sensor stehen nicht darin. „merken“ speichert nur die Zuordnung –
  an den Messwerten ändert sich erst mit „Hausdaten wiederherstellen“ etwas; „– kein alter Sensor –“ entfernt sie.
  Nach dem Wiederherstellen steht in der Zeile, ob der alte Sensor zum gespeicherten Hausmittel passt (mit seinem
  Abstand) und wie viele Stunden er gefüllt hat – oder warum er nicht verwendet wurde. Wird ein alter Sensor, der
  Stunden gefüllt hat, ersetzt oder entfernt, fragt HeatPilot nach und nimmt diese Stunden zurück (sie waren vorher
  ohne Innentemperatur); „Hausdaten wiederherstellen“ füllt sie danach mit den übrigen Quellen neu. Gesperrt, solange
  Raumänderungen nicht gespeichert sind.
- **Hausdaten wiederherstellen** (Einstellungen → Haus → Experte, bei den Hinweisen in Einstellungen → Räume und in der
  Karte „Modelle“; mit Rückfrage, die die alten Sensoren nennt – auch die, die HeatPilot dabei selbst setzt): Räume
  ohne alten Sensor bekommen ihren eindeutigen Vorschlag, dann liest HeatPilot die Langzeitstatistik mit den alten
  Sensoren neu ein – nur zusammenführend: Es ergänzt fehlende Innentemperaturen (berichtigt wie oben), löscht nichts
  und ersetzt nichts durch „unbekannt“. Gibt es eine alte climate-Entität noch in Home Assistant – übernommen oder
  als Herkunft eines alten Sensors –, holt es auch ihre Historie als Raumwerte des neuen Raums nach (nur, wo der Raum
  keinen Messpunkt hat). Das Ergebnis nennt alte Sensoren, die nicht passten, und alte Thermostate, deren Werte nicht
  nachgeholt wurden (mit Grund). Danach lernt es aus den gespeicherten Daten neu. Das läuft im Hintergrund: Unter dem Knopf, in der Karte
  „Modelle“ und unter Einstellungen → Räume steht „Läuft: Hausdaten wiederherstellen – …“ (die Knöpfe sind so lange
  gesperrt), danach das Ergebnis, z. B. „Stunden mit Innentemperatur 260 → 1.440, ohne 1.180 → 0“; Übersicht und
  Zertifikat rechnen dann neu. Nach „Gelerntes übernehmen“ nennt die Meldung den gemerkten alten Sensor und bietet
  „Hausdaten wiederherstellen“ an, wenn vielen Stunden die Innentemperatur fehlt.
- **Räume aus der Stundenstatistik** (seit 0.45, Teil von „Hausdaten wiederherstellen“): Je Raum holt HeatPilot die
  Stundenmittel seines alten Temperatursensors und der alten Heizleistung in % (am Gerät des alten Thermostats bzw. des
  alten Sensors laut Registry, sonst genau eine %-Statistik mit passendem Namen) und des Vorlaufs aus der
  Langzeitstatistik – nur für Stunden, in denen der Raum keine eigenen Raumwerte hat, und nicht weiter zurück als
  Langzeitstatistik-Tage und Aufbewahrung. Die Temperatur wird um den geprüften Versatz des alten Sensors berichtigt
  (gegen die eigenen Raumwerte, sonst über das Hausmittel); passt er nicht, bleibt der Raum leer. Diese Werte stehen
  in einer eigenen Tabelle (`room_stats`): Das Raummodell lernt aus ihnen Aufheizen und Auskühlen in Stundenschritten
  (ohne Heizleistung nur Stunden ohne Heizbetrieb), das Ventilmodell nicht (es braucht Soll-Sprünge in feiner
  Auflösung), Hausmittel und Raum-Verläufe auch nicht. Das Ergebnis nennt je Raum die nachgeholten Stunden (Zeitraum
  im Tooltip), ob es eine Heizleistung gab, und warum ein Raum leer blieb. Seit 0.45.1 ergänzt ein neuer Lauf nur:
  Fehlt die Quelle (alter Sensor, Statistik oder Integration gelöscht, Alias entfernt), bleiben die Stunden von früher
  („… h von früher bleiben“).
- **Aus gespeicherten Daten neu lernen** (Einstellungen → Haus → Experte): Haus-, Raum- und Ventilmodelle, Lernkurve und
  Fremdwärme-Ereignisse aus den gespeicherten Stunden- und Messwerten neu berechnen – ab dem ersten eigenen
  Messpunkt auch die Räume. Holt nichts aus Home Assistant und löscht keine Messwerte; Warmwasser- und
  Personenmodelle bleiben, ebenso die Modelle alter Entitäten ohne Raum (mit ihnen ihr Name für „Gelerntes
  übernehmen“) und seit 0.45.1 ein ruhendes %-Ventilmodell (ohne %-Werte ließe es sich nicht neu lernen). Läuft nach dem Wiederherstellen und nach einem Raumwechsel von selbst; den Stand zeigt die Seite wie
  beim Wiederherstellen. Abläufe laufen nacheinander – ein später gestarteter zeigt „Wartet: …“. Startet HeatPilot
  neu, während es neu lernt, lernt es beim Start fertig.

**Karte „Modelle“** (seit 0.58 unter Live → Diagnose, auch während des Lernens): Hausmodell plausibel oder lernt, Wärmeverlust ±
Unsicherheit, Vorhersagefehler, aus wie vielen Stunden und Tageswerten es wirklich gelernt hat – von wie vielen
Stunden mit Gas, Außen- und Innentemperatur seit wann (ältester Tag mit Innentemperatur); gelernt wird nur in der
Heizperiode, ohne Warmwasser und Fremdwärme; Räume plausibel n von m, Ventile belastbar n von m, Warmwasser. Haben viele Stunden Gas und
Außentemperatur, aber keine Innentemperatur, steht dort ein Hinweis mit „Hausdaten wiederherstellen“ – das
Hausmodell lernt solche Stunden nicht; auch die Grafik „Wärmebedarf des Hauses“ sagt dann, wie viele Stunden ihr
fehlen. Darunter: was zuletzt neu gelernt bzw. übernommen wurde. Ein Klick auf die Karte führt zu „Modelle –
Einzelheiten“ (Live → Diagnose). Die Kacheln mit Wärmeverlust, Heizlast und Trägheit erscheinen wie bisher erst mit
plausiblem Hausmodell – die Karte steht immer da. Plausibel ist ein Raummodell u. a. nur, wenn Heizwirkung/Auskühlen bei
Heizkörpern zwischen 0,3 und 6 liegt (seit 0.46.5, vorher 3 – große Heizkörper bei kleiner Heizlast), bei Räumen mit
Fußbodenheizung (seit 0.45) ebenfalls zwischen 0,3 und 6 – große Heizfläche, niedriger Vorlauf.

**Heizleistung vom alten Thermostat** (seit 0.45): Hat ein Raum an keinem Gerät einen Ventil-/Heizleistungssensor
(z. B. HomeKit), sucht HeatPilot die Heizleistung in % am alten Gerät des Raums – am alten Thermostat aus „Gelerntes
übernehmen“ bzw. Thermostatwechsel, am Gerät des alten Temperatursensors, sonst genau ein Tado-Heizleistungssensor im
gleichen Bereich bzw. mit passendem Namen. Läuft die alte Integration weiter, nutzt HeatPilot ihn zur Laufzeit wie einen
Sensor am eigenen Gerät; die Einstellungen bleiben unverändert, ein eingetragener Sensor hat immer Vorrang. Unter
Einstellungen → Räume → Einzelheiten steht er im Feld „Ventil / Heizleistung“ mit Herkunft („gefunden vom alten
Thermostat …“) – Speichern trägt ihn fest ein. Meldet er nichts oder ist er veraltet, gilt der Wert als Ersatzwert.
Er hilft nur, solange es die alte Integration gibt; was HeatPilot von ihm gelesen hat, bleibt nach dem Löschen (seit
0.45.1 bietet die Oberfläche ihn nur an, solange es ihn gibt – siehe „Alte Thermostate bzw. Integration entfernen“).

### Thermostat wechseln

Seit 0.44 hat jeder Raum eine feste Kennung. Meldet sich ein Raum mit einem anderen Thermostat (andere climate-Entität:
Tado-Cloud → HomeKit, neu gekoppeltes Zigbee-Thermostat, getauschtes Gerät), steht unter Einstellungen → **Räume** oben
ein Hinweis „… : anderes Thermostat erkannt“ mit dem bisherigen und dem neuen Thermostat und drei Knöpfen; Übersicht →
Status zeigt so lange die Zeile „Thermostat gewechselt: … – wartet auf deine Wahl“. Nach dem Speichern der Einstellungen
öffnet ein erkannter Wechsel die Seite von selbst.

- **Gleiches Thermostat, neue Anbindung** – dasselbe Gerät, nur anders eingebunden: alle Messwerte und Gelerntes ziehen
  um, Raum- und Ventilmodell rechnet HeatPilot daraus neu (dauert etwas, die Seite zeigt den Fortschritt).
- **Anderes Thermostat – Ventil lernt neu** – wirklich ein anderes Gerät: Raumwerte (Temperaturen, Soll, Fremdwärme)
  ziehen um, das Raummodell (Aufheizen, Auskühlen) bleibt. Das Ventil lernt ab dem ersten Wert des neuen Thermostats –
  Ventilmodell, hydraulischer Abgleich und Nachprüfung im Beobachten zählen die alte Ventilöffnung nicht.
- **Alles neu lernen** – nichts zieht um; die alten Werte bleiben unter „Gespeicherte Daten ohne Raum“ und lassen sich
  später noch übernehmen.

Jede Wahl kommt erst nach einer Rückfrage; bis dahin misst der Raum normal weiter. Benennt Home Assistant nur die
Entität um (gleiches Gerät laut Registry), zieht alles ohne Rückfrage um. Unter „Einzelheiten“ jedes Raums verlernt
**„Ventil neu lernen“** jederzeit nur das Ventil (z. B. nach Tausch des Ventilkopfs): Messwerte und Raummodell bleiben,
das Ventil lernt ab jetzt. Alle Wechsel und was verlernt wurde stehen unter Räume im aufklappbaren Protokoll.

### Alte Thermostate bzw. Integration entfernen

Seit 0.45.1 braucht HeatPilot eine alte Integration (z. B. Tado-Cloud nach dem Wechsel auf HomeKit) bzw. alte
Thermostate nicht auf Dauer – was es einmal gelesen hat, gehört HeatPilot:

1. **Hausdaten wiederherstellen** (Einstellungen → Haus → Experte): liest Temperatur und Heizleistung der alten Sensoren
   ein (Langzeitstatistik, Historie).
2. **Integration löschen** – nichts weiter nötig. Eingelesene Raum- und Stundenwerte, alte Sensoren (Aliasse) und
   Protokolle bleiben; ein späteres Wiederherstellen ohne die alten Quellen ergänzt nur und löscht nichts.

Live gibt es danach nur, was die neuen Geräte liefern (HomeKit: heizt/heizt nicht, kein Ventil in %); das
%-Ventilmodell ruht dann. Ein eingetragener Ventil-, Feuchte- oder
Temperatur-Sensor, den es in Home Assistant nicht mehr gibt, zählt als nicht vorhanden – kein Fehler, unter
Einstellungen → Räume steht beim Feld „gibt es in Home Assistant nicht mehr – kann leer bleiben“.

### Home Assistant füllt nur Lücken

Die eigene Datenbank ist die Wahrheit. **„Lücken aus Home Assistant füllen“** (Einstellungen → Haus → Experte, bis 0.43
„Historie neu einlesen“) liest Langzeitstatistik und Recorder-Historie und ergänzt nur fehlende Zeitpunkte und leere
Werte; gespeicherte Werte gewinnen, nichts wird gelöscht. Danach lernen Haus-, Raum- und Ventilmodelle neu.

### Daten aufbewahren

Einstellungen → Haus → Experte → Erweitert → **Daten aufbewahren (Tage)** (`keep_days`, Standard 720, erlaubt 365–3650): Einmal am Tag
löscht HeatPilot eigene Rohdaten, die älter sind – Messpunkte, Raum- und Personenwerte, Brennerereignisse,
Ventil-Ersatzwerte, Fremdwärme-Ereignisse, Stundenwerte und Raumwerte aus der Stundenstatistik. Tageswerte und Gelerntes bleiben. Home Assistant hält seine
Historie meist nur wenige Tage; HeatPilot hebt seine eigenen Werte also deutlich länger auf. Die Grenze zählt vom neuesten
eigenen Messpunkt und rückt je Tag höchstens zwei Tage vor – eine falsch vorgehende Uhr löscht so nichts auf einen
Schlag. Die Langzeitstatistik (`statistics_backfill_days`) wird höchstens so weit zurück geholt, wie aufbewahrt wird.

## Wärmebedarf aus dem Tado-Soll

Tado senkt nachts die Solltemperatur sofort ab, die Ventile schließen aber
erst mit Verzögerung – der Brenner läuft dann oft noch ~10 Minuten nach.
Das Add-on bewertet den Bedarf deshalb nach **Soll gegen Ist** (Bedarf ab
0,2 K unter Soll, kein Bedarf wenn alle Räume am Soll sind) und zählt
ereignisgenau, wie lange der Brenner ohne Bedarf lief und wie lange Räume
wegen der Taktsperre warten mussten. In Phase 2 wird genau dieses Signal
den Brenner direkt freigeben bzw. sperren.

## Leitplanken – sparen ja, übertreiben nein

Ein Optimierer, der nur „Gas sparen“ kennt, findet Abkürzungen: gar nicht heizen,
oder Verluste von einem Bereich in den anderen schieben (z. B. den Heizraum
heizen, damit der Speicher langsamer auskühlt). Deshalb gilt fest im Code
(`guards.py`):

1. **Komfort ist eine harte Grenze.** Tado-Soll bei Anwesenheit und Warmwasser
   zur Zapfzeit müssen erreicht werden. Die Ersparnis-Rechnung heizt nur so
   schnell wieder auf, wie die Therme kann, und startet dafür rechtzeitig vor
   der Ankunft. Wo das nicht reicht, verzichtet sie auf die Absenkung. Jede
   Stunde, die trotzdem zu kalt gewesen wäre, und jede zu kalte Zapfung wird
   angezeigt.
   **Je Raum:** Bei einem großen Kessel ist nicht die Therme die Grenze, sondern
   die Heizkörper. Deshalb kühlt und heizt jeder Raum mit plausiblem Modell nach
   seinem eigenen Modell – aufheizen höchstens mit vollem Ventil und maximalem
   Vorlauf, während Warmwasserladung gar nicht. Vorgeheizt wird so früh, wie der
   langsamste Raum braucht; käme ein Raum gar nicht mehr hoch, wird dort nicht
   abgesenkt. Die Übersicht zeigt unter „Leitplanken → Je Raum geprüft“, wie viele
   Räume geprüft wurden und welcher Raum wie viele Stunden zu kalt gewesen wäre.
   Fußbodenheizungen und Räume ohne plausibles Modell zählen nur übers ganze Haus.
2. **Gezählt wird am Gaszähler, fürs ganze Haus** – Verschieben spart nichts.
3. **Feste Untergrenzen:** Abwesenheits-/Nachttemperatur, Warmwasser-Minimum.
   „Nie heizen“ ist keine erlaubte Strategie.
4. **Nur plausible Modelle empfehlen.** Liegen gelernte Werte außerhalb
   physikalischer Grenzen (z. B. Wärmeverlust, Trägheit, Grundlast, Speicher-
   Umgebung) oder ist zu wenig gelernt, gibt es keine Empfehlung.
5. **Kleine, gedeckelte Selbstkorrektur:** Nach einer zu kalten Zapfung +2 K
   Warmwasser-Reserve, höchstens +10 K, Abbau 0,25 K pro Tag. Nur, wenn mehr
   Reserve das Ziel davor angehoben hätte – seit 0.46 immer, wenn jemand zu Hause
   und wach ist (dort gilt Komfort + Reserve). Lag das Ziel schon am Maximum, war
   die Zapfung größer als Maximum minus Minimum (z. B. die volle Wanne), war
   niemand zu Hause oder war Nacht (Minimum), wird sie nur gezählt. Im Urlaub und wenn
   alle lange weg sind, bleibt die Reserve eingefroren.

Der Status steht auf der HeatPilot-Seite im Feld „Leitplanken“.

## Karten für Home Assistant (seit 0.56)

Einrichtung, Stand des Befehls-Helfers und das YAML der Karten zum Kopieren stehen unter Einstellungen → Haus → Experte →
„Karten für Home Assistant“. Aufteilung: **Raumkachel antippen** öffnet das Thermostat-Pop-up von Home Assistant (Soll,
Betriebsart eines Raums); **HeatPilot Steuerung** bedient mehrere Räume auf einmal (Boost, Zeitplan, Aus); Pause und
Einstellungen bleiben der HeatPilot-Seite vorbehalten.

Für eigene Dashboards (auch für Nicht-Admins und unterwegs in der App) stellt HeatPilot bereit – gedacht für die
HeatPilot-Karten (Räume, Boost, Wetter) aus https://github.com/TheInGoF/heatpilot-card (HACS → Benutzerdefinierte
Repositories, Typ „Dashboard“), nutzbar aber auch mit Standard-Karten:

- **`sensor.heatpilot_raum_<raum>`** je Raum (seit 0.56.3 nach dem Raumnamen in HeatPilot, z. B.
  `sensor.heatpilot_raum_kleines_bad`): Zustand = Ist-Temperatur; Attribute wie die Raumkachel – Soll,
  Betriebsart, heizt, Ventil, Luftfeuchte, Phase und Wunsch-Soll der Steuerung, nächster Wechsel, Boost mit Ende.
- **`sensor.heatpilot_boost`**: Zahl der Räume mit Boost; Attribute: laufende Boosts, Grenzen für Soll und Dauer,
  Ergebnis des letzten Befehls (`last`).
- **`input_text.heatpilot_befehl`**: legt HeatPilot beim Start einmal als Helfer an. Die Karten schreiben Befehle als
  JSON hinein – `boost` {rooms, temp, minutes}, `schedule` {rooms}, `mode` {rooms, mode} –,
  HeatPilot führt sie aus wie die Knöpfe im Reiter Heizen, leert das Feld und meldet das Ergebnis. Die Pause bleibt
  der HeatPilot-Seite vorbehalten. Die Sensoren werden alle 30 s und direkt nach einem Befehl aktualisiert.

## Wind (seit 0.54)

Bei Sturm kühlt ein Haus schneller aus – Zugluft durch Fenster und Fugen. Jedes Raummodell lernt dafür einen
eigenen Wind-Anteil: Auskühlen = Grundrate + Anteil je m/s Wind (nie negativ). Ein zugiger Raum lernt viel, ein
geschützter „kaum Wind“. Der Wind kommt aus der Wetter-Integration (Attribut `wind_speed`, jede Einheit) und wird
mit den Messwerten aufgezeichnet; die Historie aus der Zeit davor hat keinen Wind, gelernt wird also erst ab 0.54 –
sicher nach einigen windigen und ruhigen Tagen. Steuerung, Vorheizen, Vorschau und Kessel rechnen mit dem Wind von
jetzt: bei Sturm beginnt das Vorheizen früher und der Vorlauf liegt etwas höher. Übersicht → Modelle zeigt je Raum
„Wind: kühlt bei 10 m/s um … % schneller aus“. Die Wind-Vorhersage nutzt HeatPilot (noch) nicht – bis zum
Zielzeitpunkt gilt der Wind von jetzt.

## Sonne als Wärmequelle

Die Sonne heizt Räume mit Süd-, Ost- oder Westfenstern kostenlos. HeatPilot berechnet
den Sonnenstand aus dem Standort des Zuhauses und der Uhrzeit (ohne Sensor, auch
rückwirkend) und nimmt die Bewölkung aus der Wetter-Integration (`cloud_coverage`,
sonst „halbe Sonne“). Daraus entstehen drei Werte – Einstrahlung auf Ost-, Süd- und
Westfenster –, und jedes Raummodell lernt selbst, wie viel davon bei ihm ankommt. Die
Raumtabelle zeigt das als „Sonne“, z. B. „Süd 0,8 K/h bei voller Sonne“; ein Nordraum
lernt „kaum Sonne“. Für Vorheizpläne rechnet HeatPilot vorsichtig ohne Sonne.

## Fremdwärme: Kamin, Ofen, Gäste

Ein Kaminabend macht das Wohnzimmer 2–3 K wärmer, ohne dass der Heizkörper etwas tut.
Ohne Erkennung lernte das Raummodell solche Abende als „Fremdwärme“ und „Auskühlen“ des
Raums, und das Hausmodell sah „innen wärmer bei weniger Gas“ – beides verfälscht
Vorlauf-Empfehlung, Vorheizplan und Wärmeverlust. Deshalb erkennt HeatPilot je Raum im
30-Minuten-Schritt ein **Fremdwärme-Ereignis**, wenn

- der Raum um mindestens 0,4 K steigt und davon mindestens 0,8 K/h **unerklärt** bleiben –
  unerklärt heißt: mehr, als Heizkörper, Auskühlen, übliche Fremdwärme und Sonne laut
  gelerntem Raummodell zusammen ergeben,
- der Heizkörper praktisch nichts liefert (höchstens 10 % des Möglichen, Ventil in diesem
  und im vorigen Schritt nie über 5 % offen, oder Pumpe/Heizkreis aus) – ein eben noch
  offener Heizkörper gibt nach dem Schließen weiter Wärme ab, und ein Thermostat am
  Heizkörper misst den warmen Körper verzögert; beides sähe sonst wie Fremdwärme aus,
- keine Sonne auf den Fassaden steht oder das Modell die Sonne (bzw. den Fußboden) schon
  gelernt hat (mindestens 200 Lernschritte und die Unsicherheit der angeregten Parameter
  deutlich gesunken). Sonst verwürfe ein junges Modell seine eigene Lernchance als Ereignis
  und lernte Sonne und Heizkörper nie. Fehlen Sonnendaten ganz (kein Standort in `zone.home`),
  wird tagsüber (7–20 Uhr) nicht erkannt.

Ein Abfall (Fenster auf) ist kein Ereignis – und die Erholung danach auch nicht: nach dem
Stoßlüften wärmt sich die Luft aus Wänden und Möbeln in einer halben Stunde um 1–2 K zurück,
bei geschlossenem Ventil (Nachtabsenkung). Deshalb zählt ein Anstieg nur, wenn der Raum
mindestens 0,4 K **über das höchste Niveau der letzten 2 Stunden** steigt; dieses Niveau ist
der Startwert. Das Ereignis endet, wenn der Raum wieder bis auf
0,3 K am Startwert ist, der Heizkörper wieder deutlich öffnet (Ventil im Mittel ≥ 30 %) oder
nach 12 Stunden. Währenddessen lernt das Raummodell nicht (der Estrich wird weiter
nachgeführt), das Hausmodell lernt keine Stunde mit einem Ereignis in irgendeinem Raum, und
ein Tag mit 3 h oder mehr davon zählt nur als Vortag. Ein laufendes Ereignis überlebt einen
Neustart; beim Neuberechnen des Lernstands werden Ereignisse rückwirkend erkannt.

Sichtbar: in der Übersicht als Badge in der Raumtabelle („Fremdwärme seit 19:10, +2,8 K“)
mit Zähler der letzten 7 Tage („3× Fremdwärme“), in der Woche als Ansicht „Fremdwärme“, im
Export als Tabelle `fremdwaerme` (nur Zeiten und Kelvin). Ein Raum mit laufendem Ereignis
gilt im Vorheizplan als schon warm und wird nicht eingeplant („Wohnzimmer: gerade
Fremdwärme, nicht eingeplant“).

## Vorlauf gegen Takten

Ein zu großer Kessel taktet, weil die Heizkörper seine kleinste Leistung nicht abnehmen.
Mehr Raumtemperatur ändert daran nichts – nur ein höherer Vorlauf erhöht, was die
Heizkörper abgeben. Dafür steigt der Rücklauf, und über ~55 °C kondensiert ein
Brennwertkessel kaum noch. Die Karte **„Vorlauf: bringt mehr etwas – oder ist es
Verschwendung?“** misst das an den eigenen Heizstunden der letzten 90 Tage:

- verglichen werden nur Stunden mit ähnlicher Außentemperatur (unter 0, 0–5, 5–10, 10–15 °C),
- nur Beharrungsstunden (innen höchstens 0,3 K Änderung) und ohne Warmwasserladung,
- je 5-K-Vorlaufstufe: Starts pro Stunde, Laufzeit je Start, Rücklauf und Gas je
  Kelvin-Stunde, je Stufe mindestens 20 Stunden. Mit plausiblem Hausmodell ist das
  (Gas − Achsabschnitt) / (innen − außen) – bei Fremdwärme also der Wärmeverlust – und es
  zählen nur Stunden mindestens 2 K über der Heizgrenze; sonst sähe die kältere Stunde (mit
  höherem Vorlauf vom Regler) schon allein deshalb teurer aus. Ohne plausibles Hausmodell:
  Gas / (innen − außen), das Urteil gilt dann als vorläufig.

Urteil: „lohnt sich“, „Verschwendung“ (mit Hinweis, wenn der Rücklauf über 55 °C liegt),
„kein klarer Unterschied“ oder „zu wenig Vielfalt“ – dann mit Vorschlag für einen
Versuch, den du selbst machst (Heizkurve am Regler für ein, zwei Wochen etwa +5 K, nur
wenn du sicher bist). HeatPilot verstellt dabei nichts.

## Ausreißer: was das Modell verwirrt, wird nicht gelernt

Fehler passieren überall – in Tado, in EMS-ESP und in Home Assistant. Drei
Schichten halten sie vom Lernen fern:

1. **Gaszähler je Stunde**
   - *Sprung/Nachtrag:* Zeigt eine Stunde mehr Gas, als der Brenner in seiner
     Laufzeit verbrennen konnte (Laufzeit × Nennleistung + Spielraum) – oder, wo
     es keine Brennerdaten gibt (Langzeitstatistik), ein Vielfaches der Vorwoche –,
     wird sie nicht gelernt. Typisch: der Zählerstand wird von Hand nachgezogen,
     weil der Reed-Kontakt Umdrehungen verpasst hat. Der Nachtrag zählt aber
     weiter in Verbrauch und Kosten, denn das Gas wurde ja verbrannt.
   - *Verpasste Zählung:* Brenner lief kräftig, Zähler zählte (fast) nichts →
     Stunde nicht gelernt.
   - *Zählerstand sinkt* (Korrektur nach unten, Tausch) → Stunde ignoriert.
2. **Messwerte:** außerhalb physikalischer Grenzen (z. B. EMS-Platzhalter wie
   3276,7 °C, Raum unter 0 °C, Ventil über 100 %) → verworfen. Einzelne
   Temperaturspitzen eines Raums (Sprung um mehr als 4 K und gleich wieder
   zurück) → verworfen; bleibt der neue Wert, gilt er. Ab drei Außenfühlern wird
   ein Fühler, der mehr als 8 K unter den anderen liegt, als defekt ignoriert.
3. **Lernen:** Weicht ein Messpunkt weit mehr als üblich vom Modell ab, zählt er
   nur begrenzt (über 4-fachem üblichem Fehler gekappt) oder gar nicht (über
   15-fachem). Ändert sich das Haus wirklich, zieht das Modell trotzdem nach –
   nur langsamer.

Was ignoriert wurde, steht in der Übersicht unter „Leitplanken“, im Selbsttest
(Live → Diagnose) und im Log. Beim Update auf 0.14 wird der Lernstand einmal
mit diesem Schutz aus den gespeicherten und den Home-Assistant-Daten neu
berechnet; „Lücken aus Home Assistant füllen“ (bis 0.43 „Historie neu einlesen“) macht dasselbe auf Knopfdruck. Seit
0.44 gewinnen dabei immer die gespeicherten Werte – Home Assistant ergänzt nur, was fehlt –, und Warmwasser- und
Personenmodelle bleiben; ohne Home Assistant zu fragen, rechnet „Aus gespeicherten Daten neu
lernen“ (siehe [Räume wechseln](#räume-wechseln-z-b-tado-cloud--homekit)).

## Fußbodenheizung je Raum

In Einstellungen → **Räume** steht je Thermostat die Spalte **Fußboden**: ob im Raum eine Fußbodenheizung liegt und
wie sie angeschlossen ist. Davon hängt ab, wie heiß der Vorlauf werden darf:

- **kein Fußboden** (Standard) – nur Heizkörper.
- **mit Mischer** – ein Mischer regelt die Temperatur der Fußbodenheizung selbst nach. Der Vorlauf darf höher sein,
  wie es gerade passt; aus diesem Raum kommt keine Grenze.
- **mit RTL-Ventil** (Rücklauftemperaturbegrenzer) – ist der Boden kalt, fließt am Anfang der volle Vorlauf hinein.
  Der **höchste Vorlauf** gilt seit 0.46.5 **immer**: Das RTL-Ventil öffnet, sobald der Rücklauf kalt ist – auch
  wenn der Heizkörper im Raum gerade nichts anfordert (bis 0.46.4 galt die Grenze nur, solange der Raum Wärme
  anforderte). Ob er anfordert – ein Thermostat des Raums mehr als 0,1 K unter seinem Soll, sein Ventil über 5 %
  offen oder es meldet „heizt“ bzw. „heizt vor“ (`hvac_action: heating` oder `preheating`); bei mehreren Thermostaten
  in einem Raum genügt eines –, steht nur noch im Grund („fordert an“, „fordert nicht an“, „Anforderung
  unbekannt“) – auch in der Vorschau: Ist das Thermostat jetzt nicht verfügbar, gilt sie dort in jeder Stunde.
- **direkt am Kessel** – ohne Mischer und ohne RTL: Der höchste Vorlauf gilt **immer**, auch ohne Raummodell und
  ohne Messwert.

Bei RTL-Ventil und direkt am Kessel erscheint darunter das Feld **höchster Vorlauf (°C)**, 25–70 °C in halben Grad
(die Vorschau rechnet mit ganzen Grad darunter, z. B. 45,5 °C → höchstens 45 °C); leer = 45 °C
(vorsichtig – den richtigen Wert nennt der Heizungsbauer bzw. die Anleitung von Estrich und Rohr). Gibt es mehrere
Fußboden-Räume, gilt die niedrigste Grenze, die gerade gilt. Liegen in einem Raum mehrere Thermostate mit
verschiedenen Angaben, gilt die strengere (direkt vor RTL vor Mischer, die niedrigste Grenze).

**Wo die Grenze gilt:** in der [Vorschau (Trockenlauf)](#vorschau-trockenlauf) für jedes Vorlauf-Soll, auch beim
Vorheizen – die Stunde sagt „begrenzt durch …“ –, in „Bereit für die Steuerung?“ als Hinweis mit der Grenze jetzt,
und seit 0.41 hart vor jedem Schreiben des Vorlaufs ([Kessel über HeatPilot](#kessel-über-heatpilot-weg-b), mit den
Messwerten von jetzt – auch beim Takt-Boost und beim Vorheizen; ein Wert darüber wird nie geschrieben).
Die Regeln der [Ventile im Trockenlauf](#ventile-im-trockenlauf) verschieben nur Raum-Sollwerte, nie den Vorlauf.

**Einstellungen vor 0.39.1:** Ein Raum mit dem früheren Häkchen „Fußboden“ zeigt **„Anschluss wählen“**. Bis du
wählst, gilt er wie direkt am Kessel mit 45 °C (sicher), und „Bereit für die Steuerung?“ erinnert daran. Gespeichert
wird der Anschluss am Raum (`floor_type`: `mischer`, `rtl`, `direkt`; `floor_max_flow` in °C); die Liste
`floor_heating_rooms` enthält weiter alle Räume mit Fußbodenheizung.

**Lernen:** Für jeden Raum mit Fußbodenheizung bekommt das Raummodell einen zweiten Wärmeeingang: Der Fußboden heizt,
wenn warmes Heizwasser fließt – unabhängig von Tado – und wirkt über den Estrich verzögert (≈3 h). Der Tado-Heizkörper
im Raum wird als Boost gelernt. Ohne diese Angabe würde das Modell die Fußbodenwärme als „Fremdwärme“ deuten und das
Auskühlen des Raums falsch einschätzen. Den Vorlauf bestimmen Fußboden-Räume nicht; den Heizbetrieb halten sie an,
solange sie Wärme brauchen. Ein anderer Anschluss bei bleibender Fußbodenheizung lässt das Gelernte stehen.

## Urlaub

In Einstellungen → **Urlaub** Zeiträume mit „+ Urlaub“ anlegen: Beginn und Ende
als Datum, Uhrzeiten optional. Ohne Uhrzeit beim Ende gilt die
**Standard-Rückkehrzeit** (Vorgabe 18:00). Optional ein **Urlaubs-Schalter**
(z. B. `input_boolean.urlaub`, an = Urlaub) – zu wählen unter Einstellungen → Haus → Experte → Personen & Urlaub.

**Rückkehr – das Live-Signal schlägt den Plan:**

1. **GPS – nur bei sicherer Heimfahrt.** Bestätigt wird erst, wenn jemand
   **mindestens 30 Minuten ununterbrochen näher** kommt (kein Schritt weiter weg,
   bis auf 0,5 km GPS-Schwankung), dabei **≥ 15 km und ≥ 10 % der Strecke**
   schafft und **im Schnitt ≥ 20 km/h** fährt. Dann gilt dessen Ankunft
   (Restentfernung / Durchschnittstempo der Heimfahrt) – egal ob 1–2 Tage vor
   oder nach dem geplanten Ende. Eine Rast bis 45 min und kleine Umwege bis 3 km
   heben die Bestätigung nicht auf (die Ankunft verschiebt sich nur);
   **Kehrtwende** (> 3 km wieder weiter weg) oder **45 min ohne Fortschritt**
   schon – dann gilt wieder der Plan. Zickzack und Ausflüge am Urlaubsort lösen
   nichts aus.
2. **Geplantes Ende mit Uhrzeit:** genau diese Zeit.
3. **Nur Datum:** dieser Tag zur Standard-Rückkehrzeit; an dem Tag verfeinert GPS.

**Ende des Urlaubsmodus:** sobald jemand 10 Minuten zu Hause ist – auch vor dem
geplanten Ende. Ist nach dem geplanten Ende noch niemand da, bleibt der
Urlaubsmodus bis zu 3 Tage aktiv. Die Position wird im Urlaub weiter verfolgt,
nur die Gewohnheiten lernen nicht.

Im Urlaub werden **keine Gewohnheiten gelernt** – Anwesenheitsmuster,
Warmwasser-Zapfprofil und Raummodelle bleiben auf dem Stand vor dem Urlaub und
lernen danach normal weiter. Das Hausmodell (Wärmeverlust) und die
Speicher-Physik lernen weiter, weil sich daran im Urlaub nichts ändert.
Urlaubsstunden zählen nicht zur Ersparnis-Rechnung. Die erwartete Rückkehr
gilt als Ankunft: Warmwasser und Vorheizen rechtzeitig davor. Beim Schalter
ohne Zeitraum kennt nur GPS die Rückkehrzeit.

## Vorheizplan: Warmwasser und Heizen nacheinander

Speicher und Heizkreis teilen sich die Therme (3-Wege-Ventil) – beides
gleichzeitig geht nicht. Vor einer Ankunft (z. B. Urlaubsende) plant HeatPilot
deshalb nacheinander und startet um die Summe früher:

1. **Erst Warmwasser** laden (Dauer aus der gelernten Ladegeschwindigkeit,
   Ausgangstemperatur aus dem gelernten Auskühlen),
2. **direkt danach die Räume** heizen, fertig zur Ankunft,
3. plus 10 Minuten Puffer.

Warum diese Reihenfolge: Würde der Speicher zuletzt geladen, kühlten die gerade
geheizten Räume in dieser Zeit wieder ab. Der volle Speicher verliert in der
Heizphase dagegen nur 1–2 K, und der Brenner läuft beim Umschalten ohne neuen
Start durch. Ist es schon zu knapp, startet der Plan sofort und sagt, um wie viele
Minuten es später fertig wird. Auch die Ersparnis-Rechnung berücksichtigt, dass
der Heizkreis während Warmwasserladungen keine Leistung bekommt.

**Mit welcher Außentemperatur der Plan rechnet,** steht seit 0.36.1 unter der Ankunftszeile:
„Gerechnet mit −3 °C außen (Vorhersage für 17:00)“ – der stündliche Vorhersagepunkt der
Wetter-Entität, der der Ankunft am nächsten liegt. Er zählt nur, wenn er höchstens 90 min neben
der Ankunft liegt; sonst rechnet der Plan mit der aktuellen Außentemperatur („aktuelle
Außentemperatur – keine Vorhersage für den Zeitpunkt“). Vorher bekam eine Ankunft in drei Tagen
den letzten Punkt einer 24-h-Vorhersage. Zeiten ohne Zeitzone gelten als Ortszeit. Wird die
Wetter-Entität geleert oder gewechselt, zählt die alte Vorhersage sofort nicht mehr; eine neue
Entität wird gleich abgefragt.

**Kachel „Vorhersage“** (Übersicht, neben „Haus-Trägheit“): „nächste 12 h: min −1 °C um 06:00,
Trend −3 K · Stand vor 20 min“. Der Trend ist die Temperatur in 12 h gegen jetzt (auf 0,5 K
gerundet), abgerufen wird stündlich. Ohne Wetter-Entität bzw. ohne Daten bleibt die Kachel grau.
Seit 0.37 nutzt auch die Steuerung sie (Vorheizdauer mit der Außentemperatur zum Zielzeitpunkt), ebenso
die Vorschau (Trockenlauf).

## Warmwasser

Gelernt wird aus Speichertemperatur, Ladestatus und Brennerleistung:

- **Auskühlen** des Speichers und daraus der Bereitschaftsverlust je Temperatur –
  aus ruhigen Abschnitten ohne Ladung und ohne Zapfung. Die Stufe des Speicher-
  fühlers (0,1 K, 0,5 K oder 1 K) wird aus den Messwerten geschätzt; bei gröberen
  Fühlern liegen die Zapf-Schwellen entsprechend höher, damit eine einzelne Stufe
  nicht als Zapfung gilt und das Auskühlen trotzdem gelernt wird
- **Ladekosten** in Gas-kWh je Kelvin, getrennt nach Ladestart unter/über 47 °C
  (zeigt, ob Laden aus kälterem Speicher wirklich effizienter ist). Die Energie
  wird als Trapez gerechnet: beim Ladebeginn Rampe ab 0 (was der Brenner davor
  für die Heizung leistete, zählt nicht), am Ladeende Rampe auf 0. Der Anstieg
  nach dem Ladesignal (Restwärme) zählt zur Ladung, solange der Speicher in den
  letzten 5 Minuten noch gestiegen ist – bei jeder Abtastrate gleich
- **Zapfprofil:** wann im Tages-/Wochenverlauf Warmwasser gebraucht wird.
  Zapfungen werden über ein 20-Minuten-Fenster erkannt, bei 60 bis 900 s
  Abtastung gleich (sind alle lange weg, zählen auch die leeren Stunden seit dem
  Weggehen nicht, und eine große Zapfung kurz vor der Abfahrt zählt nur einmal)
- **Nachladegeschwindigkeit** in K/h, bis der Speicher nach dem Ladesignal nicht
  mehr steigt (mit Nachlauf, ohne die flachen Schritte danach)

Strategie (läuft im Schatten mit; gesetzt wird sie nur mit dem Schalter aus
[Weg W](#warmwasser-über-heatpilot-weg-w)). Der Kessel lädt erst, wenn der Speicher um seine
Einschalthysterese unter das Soll fällt (EMS-ESP `dhw_hyston`, z. B. −5 K). Seit 0.46.2 gilt dabei wie bei
einer üblichen Automation: **Die Komforttemperatur der Einstellungen ist das Soll** – bei Komfort 50 °C und
5 K Hysterese pendelt der Speicher zwischen 45 und 50 °C („Speicher 45–50 °C“), nicht mehr 50–55 °C. Nur das
**Minimum** bekommt die Hysterese dazu (Soll = Minimum + Hysterese), damit der Speicher beim Fallenlassen nie
darunter fällt. Jedes Soll wird auf 1 K aufgerundet, höchstens das Maximum. Meldet der Kessel keine Hysterese,
rechnet HeatPilot mit 5 K und sagt das im Grund („Hysterese 5 K angenommen“).

- **Urlaub** bzw. **niemand zu Hause** und die nächste Ankunft weiter weg als die Aufheizdauer →
  Minimum („Urlaub – Speicher darf bis … °C fallen“). Ist die Ankunft unbekannt, weil die Gewohnheit noch
  lernt, bleibt das heutige Soll
- **Nacht** laut Nachtplan (eingestellt bzw. gelernt) → Minimum („Nacht – Speicher darf bis … °C fallen“), bis die
  Ladung gerade noch **5 min vor dem frühesten Heizstart der Räume** fertig wird (seit 0.68; ohne Heizstart vor dem
  Nachtende). Die Ladedauer rechnet ab der aktuellen Speichertemperatur: Beginnt das Vorheizen um 4:13 und braucht das
  Wasser 23 min, lädt der Speicher ab 3:45 – Warmwasser und Heizen kommen sich nicht in die Quere
- **sonst** – jemand zu Hause und wach, bzw. innerhalb der Aufheizdauer vor Ankunft oder Aufstehen („vor dem
  Aufstehen aufheizen“) → **Komfort** (+ gelernte Reserve) als Soll. Ist eine große Zapfung üblich, Soll =
  Minimum + Bedarf, wenn das höher ist – ohne Hysterese, denn während des Zapfens lädt der Kessel nach.
  Zapfungen sieht die Strategie mindestens eine Stunde voraus

Die **Aufheizdauer** vor einer Ankunft ist (Komfort-Soll − Minimum) / gelernte Ladegeschwindigkeit + 0,5 h Puffer
(bis 0.46.1: bis Komfort + Hysterese und 1 h Puffer). Beispiel: Minimum 40 °C, Komfort 50 °C, 20 K/h → 1,5 h vor der
Ankunft wird geheizt. Nachts zählt seit 0.68 die Ladedauer ab der aktuellen Speichertemperatur, fertig 5 min vor dem
Heizstart (bis 0.67: ab dem Minimum, fertig eine halbe Stunde vor dem Nachtende – also jede Nacht zur selben Zeit).
**Während einer Ladung** schaltet HeatPilot den Heizbetrieb nicht aus – mancher Kessel zeigt dabei „Heizbetrieb an“
(bis 0.67 wurde das als Fehlschlag gezählt; nach dreien pausierte die Kesselsteuerung). Den früheren Tagesboden „keine Zapfung erwartet → Minimum + Reserve“ gibt es nicht mehr. Die
gelernte Reserve wurde mit 0.46.2 einmal auf 0 gesetzt – sie stammte aus der früheren Strategie. Übersicht → Warmwasser
zeigt in „Empfehlung jetzt“ das Soll und darunter wie der Grund „Speicher 45–50 °C“ bzw. beim Fallenlassen „Speicher
darf bis 40 °C fallen“ („Hysterese 5 K angenommen“ nur, wenn der Kessel keine meldet) und den Grund;
ist die Hysterese am Kessel größer als 5 K, steht darunter ein Hinweis: Eine kleinere Hysterese hält den
Speicher näher am Ziel und spart Bereitschaftsverlust (am Kessel einstellen – HeatPilot ändert sie nicht).
Vorschau, Weg W, Ersparnis-Schätzung und Reserve rechnen mit demselben Ziel; der Vergleichsspeicher lädt wie
der Kessel erst unter Soll − Hysterese und dann bis zum Soll. Die Ersparnis ist der eingesparte
Bereitschaftsverlust bei denselben Zapfungen; Zapfungen, bei denen der
Speicher unter Komfort − Hysterese (seit 0.46.2 die Untergrenze im Komfort; bis 0.46.1 die
Komforttemperatur) gewesen wäre oder die ihn unter das Minimum ziehen, werden als „zu kalt“ gezählt. Fürs Minimum
zählt seit 0.46.2 der tiefste Stand mit dem Nachladen: Fällt der Vergleichsspeicher während der Zapfung unter
Soll − Hysterese, lädt er mit der gelernten Ladegeschwindigkeit für die restliche Zapfdauer nach (die Zapfung eines
Messschritts gilt als gleichmäßig über den Schritt verteilt, höchstens 5 Minuten – länger misst HeatPilot nicht) –
nicht aber solche, die er nur dank der Legionellenschaltung bedient (deren Wärme
bekommt die Strategie genauso): die Wanne danach, die warm beginnt und erst durch
ihre Größe unter das Minimum zieht, höchstens 12 Stunden nach der Sonderladung.
Eine Zapfung, die der Vergleichsspeicher schon unter dem Komfortwert bedient,
zählt auch dann. Die wöchentliche Desinfektion des Reglers bleibt unberührt.

### Desinfektion (Legionellen)

Seit 0.36.3 gehört der Termin der wöchentlichen Desinfektion in die Einstellungen (Karte Warmwasser):
**Tag** (Standard „aus“ – kein Termin), **Uhrzeit** des Beginns (Standard 13:00, nur mit Tag wirksam) und die
**Temperatur**. Die liest HeatPilot vom Kessel (EMS-ESP `wwdisinfectiontemp` bzw. `dhw/disinfectiontemp`,
„70 °C laut Kessel“); meldet EMS-ESP sie nicht, gilt die Einstellung (60–80 °C, Standard 70). Ein Kesselwert
außerhalb von 60–80 °C zählt nicht – unter 60 °C ist es keine Desinfektion („Kessel meldet 55 °C – zu niedrig
für eine Desinfektion, es gilt die Einstellung“).

**Desinfektion auslösen** (seit 0.42): Viele Kessel haben für die Legionellenschaltung keinen eigenen Timer, nur den
Schalter „WWK Desinfizieren“ (EMS-ESP `wwdisinfecting`); bisher macht es der Regler am Bus (z. B. sonntags 13:00) –
solange er das tut, trage seinen Termin ein. Ohne Regler löst HeatPilot die Desinfektion selbst aus, aber nur, wenn
alles zutrifft:

- ein Termin ist eingetragen,
- **Warmwasser-Soll: „HeatPilot setzt“** (Weg W; bei „Trockenlauf“ steht zum Termin nur „Trockenlauf: würde
  Desinfektion … starten“ im Protokoll, bei „Aus“ geschieht nichts),
- **kein Regler am Bus** – dieselbe Erkennung wie bei [Kessel über HeatPilot](#kessel-über-heatpilot-weg-b) (EMS-ESP
  meldet ausdrücklich kein Gerät `thermostat`; ohne Antwort von EMS-ESP ist es unbekannt und gesperrt). Mit Regler am
  Bus macht er die Desinfektion, HeatPilot schaltet nichts. Das gilt nur, solange er frisch gemeldet wird: Wurde er eben
  abgebaut und gilt nur noch die 30 min nach dem letzten Sehen als vorhanden, wartet der Termin und wird nachgeholt,
- Weg W ist nicht pausiert,
- der Schalter taugt: eine `switch`-Entität, deren Name bzw. Anzeigename nach der Desinfektion aussieht, nie die
  Einmalladung, die Zirkulation, der Heizbetrieb oder ein anderer Kesselschalter; verfügbar, an oder aus; EMS-ESP lässt
  ihn schreiben. Vorgeschlagen aus der Erkennung, wählbar unter
  **Einstellungen → Kessel → Warmwasser steuern → „Desinfektion auslösen (Schalter)“** (die Auswahl bietet
  nur passende Schalter an).

Geschrieben wird über Home Assistant – `switch.turn_on`/`turn_off` nur auf diese eine Entität, alles andere weist
HeatPilot hart ab; an EMS-ESP gehen weiterhin nur Lese-Anfragen. **Ablauf zum Termin** (Ortszeit, auch in der Woche der
Zeitumstellung – „So 13:00“ bleibt 13:00):

1. **Einschalten** – einmal je Termin; der Termin gilt damit als erledigt, auch nach einem Neustart und auch, wenn das
   Einschalten scheitert (dann Fehlschlag mit Meldung, kein neuer Versuch vor dem nächsten Termin). Läuft der Aufruf in
   einen Zeitüberlauf, ist unklar, ob er ankam – dann entscheidet die Prüfung nach 60 s.
2. **Nach 60 s prüfen:** Steht der Schalter an bzw. meldet EMS-ESP die Desinfektion (oder eine Sonderladung), läuft sie.
   Fällt der Schalter sofort zurück, ist es ein Fehlschlag – auch wenn der Speicher schon heiß ist; ist der Zustand
   unbekannt, prüft HeatPilot bis 5 min weiter.
3. **Beobachten** (erst nach dieser Bestätigung): Erreicht der Speicher die **Desinfektionstemperatur − 3 K**, ist sie
   gelungen. Lag er schon beim Einschalten darüber, zählt erst die volle Desinfektionstemperatur oder dass der Kessel den
   Schalter selbst zurücksetzt (sein Programm ist fertig). Sonst spätestens nach der Ladedauer laut Modell + 60 min ein
   Fehlschlag: Meldung „Desinfektion So 13:00 nicht gelaufen – Speicher erreichte nur 58 °C, nötig 67 °C“ über die
   Benachrichtigungsziele und `binary_sensor.heatpilot_problem`. Die nächste gelungene meldet „wieder in Ordnung“.
4. **Ausschalten:** Steht der Schalter danach noch an, schaltet HeatPilot ihn einmal aus und prüft nach 60 s – nach
   einer gelungenen Desinfektion erst nach 15 min Nachlauf (meist setzt der Kessel ihn mit der vollen Temperatur selbst
   zurück; war der Speicher schon beim Einschalten heiß, wartet HeatPilot bis zur Frist des Ablaufs), nach einem
   Fehlschlag gleich. Ausgeschaltet wird unter denselben Bedingungen wie eingeschaltet. Sperrt etwas nur kurz (EMS-ESP
   antwortet gerade nicht, Pause des Warmwasser-Solls), ist der Schalter kurz „nicht verfügbar“ oder scheitert der
   Aufruf, versucht HeatPilot es weiter („Ausschalten wartet – Grund“); erst nach 30 min kommt die Meldung „Schalter
   nicht ausgeschaltet (Grund)“. Bleibt er nach dem Ausschalten an, kommt ebenfalls eine Meldung.

Lief HeatPilot zum Termin nicht (Add-on gestoppt) oder war das Auslösen gesperrt, holt es den Termin **innerhalb von
12 h** nach; danach meldet es „Desinfektion So 13:00 verpasst – … ; nächster Termin …“, und es gilt der nächste Termin.
Ein eben eingetragener oder geänderter Termin, der schon vorbei ist, wird nicht ausgelöst. Höchstens eine Desinfektion
je 24 h: Wird der Termin während oder kurz nach einem Ablauf auf später am selben Tag gelegt, gilt er erst in der
nächsten Woche (Protokoll „übersprungen“) – außer der Ablauf schlug fehl. Solange der Ablauf läuft,
schreibt Weg W das Speicher-Soll nicht und Weg B lässt das Vorlauf-Soll in Ruhe (wie bei einer Sonderladung). Wird das
Warmwasser-Soll mitten im Ablauf umgestellt, bricht er ab – der Kessel beendet die Desinfektion selbst.

**Anzeige:** Einstellungen → Kessel zeigt unter dem Termin die Zeile **Auslösen** – „löst HeatPilot aus (So 13:00)“,
„macht der Regler“, „Trockenlauf – würde … auslösen“ oder „aus“ mit Grund –, den Schalter (mit „ändern unter
Entitäten“), was gerade läuft, wartet oder sperrt, und die letzte Desinfektion mit Ergebnis („zuletzt So., 13.10.,
13:00: gelungen – 68 °C erreicht“). Dieselbe Zeile steht im Block „Warmwasser über HeatPilot“ von „Bereit für die
Steuerung?“, die Schritte im Protokoll (Live → Diagnose) (Desinfektion: eingeschaltet, läuft, gelungen bzw. nicht
gelaufen, verpasst, übersprungen, ausgeschaltet). Geschaltet wird nie aus der Oberfläche – nur zum Termin. Abfrage: `GET api/dhw` →
`disinfect` (mit Protokoll).

Eingeplant wird der Termin, wenn HeatPilot ihn auslöst (seit 0.42, siehe oben; ein gescheiterter oder verpasster
Termin zählt nicht, solange das Modell noch mit seiner Wärme rechnen würde – „Desinfektion So 13:00 nicht gelaufen –
ihre Wärme zählt im Warmwasser-Modell nicht“) oder sobald er **bestätigt** ist: Hat der Regler in den letzten 8 Tagen zu dieser Zeit
desinfiziert (erkannt am Kesselschalter `wwdisinfecting` oder daran, dass der Speicher bis 5 K an die
Desinfektionstemperatur kam; endet sie höchstens 3 Stunden nach dem Termin, ist es dieselbe), behandeln
Empfehlung und Vergleichsspeicher die Stunden danach wie nach einer erkannten Sonderladung – ohne auf die
Erkennung dieser Woche zu warten. Heiß ist der Speicher ab Beginn plus Ladedauer (wie beim Vorschlag): Eine
große Zapfung (Wanne) ab dann bis höchstens 12 Stunden danach ist durch diese Wärme gedeckt, die Strategie
lädt dafür vorher nicht höher; ein Termin zur Stunde der Wanne selbst deckt sie nicht. Der Vergleichsspeicher
steht dann auf der Desinfektionstemperatur und kühlt von dort aus; seine Verluste zählen mit, eine Zapfung,
die er nur dank dieser Wärme bedient, zählt nicht als zu kalt. Desinfiziert zu dieser Zeit niemand – kein
Regler, oder er desinfiziert zu einer anderen Zeit (dann zählt seine Zeit) –, zählt der Termin nicht
(„Termin noch nicht bestätigt – kein Regler desinfiziert zu dieser Zeit“): Sonst rechnete die Strategie mit
Wärme, die nicht kommt, und die Ersparnis stiege. Eine Einmalladung ist keine Desinfektion und zählt weder
dafür noch dagegen.

**Vorschlag** (ab 4 Wochen Zapfprofil): HeatPilot sucht die Wochenstunde mit der größten regelmäßigen
Zapfung – zuerst große Zapfungen, die an mindestens der Hälfte der letzten (bis zu 8) Wochen am selben
Wochentag ±1 Stunde kamen (die größte zuerst, bei gleicher Größe die häufigere), sonst die Stunde, in der mindestens jede zweite Woche gezapft wird und Abfall ×
Häufigkeit am größten ist. Der Beginn liegt so, dass der Speicher 15 Minuten vor dieser Stunde fertig ist
(Ladedauer aus der gelernten Ladegeschwindigkeit ab 5 Ladungen, sonst Ladekosten × Anstieg ÷
Kesselleistung), auf 15 Minuten abgerundet, und nur zwischen 06:00 und 21:00 – läge er nachts, gilt die
nächstgrößte regelmäßige Zapfung, sonst gibt es keinen Vorschlag. Beispiel: „So 18 Uhr zapfst du am
meisten (7 von 8 Sonntagen, ≈ 14 K) – Desinfektion So 17:30, dann nutzt du die 70 °C“, dazu grob, wie viel
der Desinfektionswärme bis dahin im Speicher bleibt und ins Wasser geht. „Vorschlag übernehmen“ trägt Tag
und Uhrzeit ein; gespeichert wird erst mit „Speichern“. Ohne genug Daten steht der Grund da.

Die Übersicht (Warmwasser) zeigt die nächste Desinfektion als Wert („So 17:30“, Notiz „eingestellt“, dazu
„Termin noch nicht bestätigt …“, solange er nicht zählt, sonst wer sie auslöst – „HeatPilot löst sie aus“, „Der Regler
macht sie“), ohne Termin die zuletzt erkannte Sonderladung. Mit Termin ist die Desinfektion in „Bereit für die
Steuerung?“ eine nötige Kesselsteuerung.

## Entitätsnamen

EMS-ESP-Entitäten werden in beiden Schreibweisen erkannt: Kurzname
(`binary_sensor.boiler_burngas`) und langer Name (`binary_sensor.boiler_gas`),
auch mit Präfix (`number.ems_esp_thermostat_hc1_remotetemp`). Was gefunden wurde, und eine eigene Wahl je
Rolle: Einstellungen → Haus → Experte ([Entitäten](#entitäten)).
