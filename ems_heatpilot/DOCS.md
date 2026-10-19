# EMS HeatPilot

Lokale, selbstlernende Heizungsoptimierung für Kessel mit
[EMS-ESP](https://github.com/emsesp/EMS-ESP32) ([Doku](https://emsesp.org)),
Tado und Anwesenheit.

> **Beta (0.x): Raumregelung mit Tado** – über die Tado-Cloud-Integration oder lokal
> über die Tado-Bridge in HomeKit (`homekit_controller`). Andere, direkt mit Home
> Assistant verbundene Thermostate sollen später unterstützt werden.

## Einrichten

Nach der Installation öffnet die HeatPilot-Seite (Seitenleiste) direkt den
Reiter **Einstellungen**. Eingerichtet wird ausschließlich dort – das Add-on hat
keine YAML-Optionen:

- **Steuerung:** der Hauptschalter **Beobachten** (Standard: lernen, empfehlen, Tado
  nur auf Knopfdruck) oder **HeatPilot steuert** (HeatPilot schreibt die
  Raum-Solltemperaturen selbst – siehe [Steuerung](#steuerung-beobachten-oder-heatpilot-steuert)),
  dazu der Wochenplan der Nachtabsenkung, Nacht- und Abwesenheitstemperatur, der
  Vorheizdeckel und die **Nachtzeiten aus dem Stromverbrauch** (Leistungssensoren des Hauses,
  „Nicht zählen“ für Waschmaschine & Co., Häkchen „Gelernte Zeiten verwenden“ – siehe
  [Nachtzeiten aus dem Stromverbrauch](#nachtzeiten-aus-dem-stromverbrauch))
- **Verbindung:** EMS-ESP-Adresse mit „Testen“-Knopf (nur lesend, kein Passwort/Token).
  Gelesen werden Kessel und Regler alle 30 s und alle weiteren Geräte, die EMS-ESP
  kennt (Mischer, Solar, Sensoren …), alle 5 min. Fehlen Werte, die die
  EMS-ESP-Oberfläche zeigt: In EMS-ESP unter „Customizations“ können Werte für
  API/MQTT ausgeschlossen sein – die gibt EMS-ESP dann nirgends heraus. Manche Werte
  meldet der Kessel erst nach dem ersten Brennerstart bzw. der ersten Warmwasserladung.
  Seit 0.36.1 fragt HeatPilot außerdem beim Start und dann stündlich die Kesselsteuerungen ab
  (ebenfalls nur lesen) – siehe [Bereit für die Steuerung?](#bereit-für-die-steuerung)
- **Messwerte:** Außenfühler (einer oder mehrere, Minimum zählt), Gaszähler,
  Wetter, optional eine Heizungsanforderung – Auswahl mit Suche aus allen
  Entitäten in Home Assistant. Die Wetter-Entität liefert die stündliche Vorhersage für den
  Vorheizplan zur Ankunft und die Bewölkung für die Sonnenanteile; ohne Außenfühler auch die
  aktuelle Außentemperatur. Die Steuerung nutzt die Vorhersage ab 0.37
- **Räume:** alle `climate.*` als Tabelle mit Quelle (Integration) – Häkchen, Name,
  **„macht mit“** und **Komforttemperatur** (für „HeatPilot steuert“), **Raum** und
  **Raumregler** (mehrere Thermostate als ein Raum), Fußbodenheizung, **Voreinstellung**
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
  bleibt abgewählt bzw. lässt sich abwählen. Ändern sich die Sensoren eines Raums oder kommt ein Raum
  hinzu, liest HeatPilot die Historie automatisch neu ein (dauert einige Minuten). Beim
  Speichern mehrerer Geräte in einem Raum sagt HeatPilot, welches das Schlüsselgerät ist
  (Raumregler, sonst das erste)
- **Personen:** alle `person.*` mit Häkchen. Automatisch gewählt werden nur Personen
  mit Home-Assistant-Benutzer und Gerät. Fahrzeuge, die als Person angelegt sind
  (Bild, Abwesenheiten, aber kein Benutzer), sind als „Fahrzeug oder Gerät?“
  markiert und nicht gewählt; Dummies ohne Gerät ebenso. Abwählen geht jederzeit. Nach einer
  Änderung zählt HeatPilot die Anwesenheit in den gespeicherten Daten neu;
  abgewählte Räume und Personen fließen in keine Auswertung mehr ein.
- **Gas & Kosten, Komfort & Strategie, Warmwasser, Urlaub**
- **Zuordnungen (Experten):** was die Erkennung gefunden hat, mit eigener Korrektur
- **Erweitert:** Abtastintervall, Historie, „Historie neu einlesen“

Felder sind mit Vorschlägen der automatischen Erkennung vorausgefüllt
(„Vorschläge übernehmen“ setzt sie erneut). **Speichern** prüft alle Eingaben,
erklärt Fehler und übernimmt sofort, ohne Neustart. Die Einstellungen liegen in
`/data/settings.json` und bleiben bei Updates erhalten.

## Alle Sensoren

Reiter **Diagnose → Alle Sensoren** listet, was HeatPilot liest: Bereich, Zweck,
Sensor bzw. EMS-ESP-Wert, Name in Home Assistant, aktueller Wert, Stand und
Zustand (ok, fehlt, veraltet, nicht gefunden). Mit dem Filter findet man schnell
alles zu einem Raum oder alle fehlenden Werte; optional werden auch die von
EMS-ESP gelieferten, aber nicht verwendeten Werte gezeigt.

## Gelernte Werte exportieren

Reiter **Diagnose → Gelernte Werte herunterladen** liefert eine JSON-Datei mit allem,
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

Reiter **Diagnose** auf der HeatPilot-Seite:

- **Selbsttest** – laufend: fehlende oder veraltete Sensoren, unmögliche Werte
  (Vorlauf, Raum, Speicher), Gaszähler zählt nicht obwohl der Brenner lief,
  Aufzeichnungslücken, EMS-ESP nicht erreichbar, Programmfehler der letzten 24 h,
  Modelle ohne Empfehlung (mit Grund).
- **Log** – alle Meldungen mit Zeit und Fehlerdetails, filterbar. Wichtige
  Zustandswechsel (Leitplanken, Urlaub, Ankunft, Vorheizplan, Heizfreigabe,
  Warmwasser-Empfehlung) werden protokolliert. „Ausführliches Log“ (Einstellungen →
  Erweitert) schreibt zusätzlich jeden Takt mit allen Messwerten.
- **Log-Datei** – rotierend (5 × 2 MB) unter `/addon_configs/<slug>/logs/heatpilot.log`,
  erreichbar über Samba oder den File-Editor.
- **Diagnose-Datei herunterladen** – Log, Selbsttest, Einstellungen, erkannte
  Entitäten und deren Zustände, gelernte Modelle, Datenbank-Übersicht und die
  Messwerte der letzten 48 Stunden als JSON. **Ohne GPS-Koordinaten** (weder
  Personen noch Zuhause – nur Entfernungen) und ohne Zugangsdaten. Diese Datei
  kann zur Fehlersuche weitergegeben werden.

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
  Frost gemessen wurden

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
**Einstellungen → Haus & Kessel** (0 = unbekannt bzw. Wert von EMS-ESP). Das
Zertifikat enthält keine Namen, Adressen oder Standortdaten. Es ist kein
Energieausweis nach GEG, die Werte sind nicht witterungsbereinigt.

## Live

Der Reiter **Live** zeigt, was Heizung und Haus gerade tun und in den letzten
3, 6, 12 oder 24 Stunden getan haben; er aktualisiert sich alle 30 Sekunden.

- **Anzeigen:** Brenner (an/aus, Leistung, heizt oder lädt Warmwasser, Taktsperre
  mit Restzeit), Vorlauf mit Soll von Kessel und Regler, Rücklauf mit Spreizung,
  Kessel und Pumpe, Warmwasser, außen, innen Ø, Luftfeuchte Ø, Wärmebedarf laut Tado, wer zu Hause ist
- **Grafen:** Kesseltemperaturen (hinterlegt, wann der Brenner heizt bzw.
  Warmwasser lädt), Brennerleistung und Pumpe, außen/innen, Warmwasser
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
  Ohne GPS-Annäherung gilt das erste Feld ab 50 % als erwartete Ankunft. Die Farbe steht
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
  oder bis morgen früh (06:00) schreibt HeatPilot nichts; „Weiter“ hebt die Pause auf.
- **Raumkacheln** (zwei nebeneinander, wie bei Tado): Feuchte, Ist-Temperatur groß, Name und
  Zustand. Beobachten: „Zeitplan · 21°“, „Eingestellt auf 22°“, „Timer“, „Frostschutz“.
  HeatPilot steuert: „HeatPilot: Nacht 18°“, „Vorheizen → 21°“, „manuell 22° bis 21:00“
  (jemand hat von Hand gedreht), „erreicht ≈ 19,5°“ (Ziel laut Raummodell nicht erreichbar),
  „Pause · 21°“. Heizt der Raum gerade, ist die Kachel orange; aus = grau mit
  Ausschalt-Symbol. Antippen öffnet den Dialog mit Soll (−/+), Betriebsart, Ventil, Restzeit
  eines Timers, den Geräten des Raums und – bei „HeatPilot steuert“ – Phase, Wunschsoll,
  Grund und nächster Änderung.
- **Protokoll** (aufklappbar unter den Kacheln): die letzten 20 Schreibvorgänge mit Zeit,
  Raum, Aktion, Wert und Grund – egal ob Regelschleife oder Knopfdruck.

Erlaubt sind nur drei Aufrufe (`climate.set_temperature`, `climate.set_hvac_mode`,
`tado.set_climate_timer`) und nur auf die Thermostate der in den Einstellungen gewählten
Räume, Temperaturen 5–25 °C. Raumauswahl, Temperatur und Dauer merkt sich das Gerät.

## Steuerung: Beobachten oder HeatPilot steuert

Unter **Einstellungen → Steuerung** steht der Hauptschalter. Es gibt genau zwei Stellungen,
keinen Zwischenmodus und keinen automatischen Rückfall:

- **Beobachten** (Standard): wie bisher – lernen, empfehlen, Tado nur auf Knopfdruck.
- **HeatPilot steuert**: HeatPilot schreibt die Solltemperaturen der Räume mit Häkchen
  **„macht mit“** selbst. Zurück zu Tado geht es nur über diesen Schalter; danach den
  Zeitplan in der Tado-App selbst fortsetzen (HeatPilot stellt ihn nicht wieder her). Fällt
  HeatPilot aus (Add-on gestoppt, Home Assistant neu gestartet), bleibt das zuletzt
  geschriebene Soll stehen.

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

Räume ohne „macht mit“ stehen als „nicht dabei“ in der Liste. Die Übersicht zeigt bei „Beobachten“ die
Stufe als Badge in der Statuszeile, der Heizen-Reiter in seiner Statuszeile.

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
| Desinfektion | `wwdisinfecting` / `dhw/disinfecting` | Info |

„Alle 5 nötigen Steuerungen vorhanden und schreibbar – eine Kesselsteuerung wäre möglich (nicht
gebaut, HeatPilot schreibt nichts)“ bzw. „Es fehlen: Vorlauf-Soll (nicht schreibbar), …“. Ohne
EMS-ESP-Adresse: „EMS-ESP nicht eingerichtet“. Schalter (Heizbetrieb, Einmalladung …) stehen als
„an“/„aus“, gleich in welchem Bool-Format EMS-ESP sie meldet. Antwortet EMS-ESP auf eine dieser
Abfragen nicht in 3 s, bricht die Runde ab und wird nach einer Stunde wiederholt – die Kesselwerte
alle 30 s laufen davon unberührt weiter. Auch diese Abfragen sind reine Lese-Anfragen; einen
Schreibpfad zu EMS-ESP gibt es weiterhin nicht, auch keinen vorbereiteten. Was HeatPilot damit täte,
steht in der Planung ([docs/planung-vorlauf-ems.md](https://github.com/TheInGoF/ems-heatpilot/blob/main/docs/planung-vorlauf-ems.md));
geschrieben wird erst mit einem eigenen Schalter in einer späteren Version. Der Kessel-Block ändert die
Stufe der Raumsteuerung nicht – die Tado-Steuerung braucht ihn nicht.

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

### Wunschzustand (jeder Takt, nur bei „HeatPilot steuert“)

Für jeden Raum mit „macht mit“ bestimmt HeatPilot eine Phase und das dazugehörige Soll:

1. **Urlaub** → Abwesenheitstemperatur; vor der erwarteten Rückkehr (siehe „Urlaub“)
   rechtzeitig vorheizen auf Komfort.
2. **Nacht** laut Wochenplan → Nachttemperatur (Standard 18 °C). Je Wochentag gilt ein Beginn am
   Abend dieses Tages und ein Ende am Morgen dieses Tages; Standard: Beginn So–Do 21:00, Fr und
   Sa 22:00; Ende Mo–Fr 04:30, Sa und So 06:30. Vor dem Nachtende wird so früh **vorgeheizt**,
   wie der Raum laut Raummodell braucht (Aufheizzeit bei maximalem Vorlauf und aktueller
   Außentemperatur), mindestens 15 min, höchstens der **Vorheizdeckel** (Standard 3 h). Ohne
   plausibles Raummodell pauschal 1 h. Folgt nach der Nacht Abwesenheit, wird nicht vorgeheizt.
3. **Niemand zu Hause** (alle gewählten Personen sicher weg) → Abwesenheitstemperatur; liegt die
   erwartete Ankunft (GPS-Heimfahrt oder Gewohnheit) innerhalb der Aufheizzeit, wird auf Komfort
   vorgeheizt. Ist die Ankunft offen (Gewohnheit lernt noch) oder die Anwesenheit unbekannt
   (Tracker meldet nichts), bleibt es bei Komfort – im Zweifel warm.
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
  nicht nach; meldet Home Assistant danach etwas anderes, gilt das als Handänderung.
- **Boost** läuft immer zu Ende, HeatPilot greift währenddessen nicht ein; danach gilt wieder der
  Wunschzustand.
- **Pause** (Heizen → Pause, bis 48 h) hält alle Schreibvorgänge an; der Schalter bleibt dabei
  auf „HeatPilot steuert“. Ein Boost, der in der Pause abläuft, endet mit dem Soll von vorher.
- **Fehler** beim Schreiben werden protokolliert und nach 15 min erneut versucht; ab drei
  Fehlschlägen in Folge steht eine Warnung im Heizen-Reiter.
- **Protokoll:** jeder Schreibvorgang (Zeit, Raum, Aktion, Wert, Grund) wird 90 Tage gespeichert
  – die letzten 20 im Heizen-Reiter, 200 in der Diagnose-Datei, alle im Export der gelernten
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
Einstellungen → Steuerung, unter dem Wochenplan:

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

Später (0.37): Soll nach Ventilstellung – jetzt bewusst nicht enthalten.

## Was das Add-on macht

**Beobachten und lernen.** Im Standard greift es nicht von selbst in die Heizung ein:
- An EMS-ESP sendet es ausschließlich Lese-Anfragen (GET), ein Token wird nicht benötigt –
  auch bei „HeatPilot steuert“ und für die Kesselsteuerungen in „Bereit für die Steuerung?“.
- In Home Assistant schreibt es von selbst nur seine eigenen `sensor.heatpilot_*`.
- Ausnahme, nur auf deinen Knopfdruck im Reiter „Heizen“: Tado-Timer, Soll und
  Betriebsart der gewählten Räume (siehe oben). Jeder andere Schreibversuch wird im
  Code hart abgewiesen.
- Erst mit dem Schalter **HeatPilot steuert** (Einstellungen → Steuerung) schreibt es die
  Raum-Solltemperaturen der Räume mit „macht mit“ selbst – mit denselben drei Diensten,
  denselben Räumen und 5–25 °C (siehe „Steuerung“).

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

Hinweis: Diese Sensoren werden vom Add-on gesetzt und sind nach einem
Neustart von Home Assistant bis zum nächsten Takt (max. 5 min) „unbekannt“.

## Räume

Im Reiter Einstellungen → **Räume** stehen alle Thermostate (climate-Entitäten)
als Tabelle. Häkchen = Raum wird gelernt. Je Raum optional: eigener Name,
„macht mit“ und Komforttemperatur (für „HeatPilot steuert“), Raum und Raumregler
(mehrere Thermostate als ein Raum, siehe „Steuerung“), Voreinstellung und Stufen des
Ventils (siehe „Hydraulischer Abgleich“), Ventil-/Heizleistungs-Sensor (%),
Temperatursensor (für die Langzeitstatistik) und Feuchtesensor. Unter jeder Entität
stehen Bereich und Gerät aus Home Assistant; die Tabelle ist nach Bereich und Name
sortiert. Ohne Angaben schlägt die automatische Erkennung die Tado-Räume samt
Sensoren vor – für jedes Thermostat mit Gerät in Home Assistant (Tado-Cloud, HomeKit,
andere): Name und Raum aus dem Bereich, Temperatur/Feuchte/Heizleistung aus den
Sensoren desselben Geräts, Raumregler bei Wandthermostaten. Vorschläge füllen nur
leere Felder („Bereiche übernehmen“ trägt den Bereich in leere Raum-Felder ein);
„macht mit“ setzt nie ein Vorschlag, nur du.

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
   Reserve das Ziel davor angehoben hätte – das gilt auch für „keine Zapfung
   erwartet“ mit jemandem zu Hause, denn dort ist Minimum + Reserve der Boden
   (für Haushalte ohne feste Zeiten die einzige Korrektur). Lag das Ziel schon am
   Maximum, war die Zapfung größer als Maximum minus Minimum (z. B. die volle
   Wanne) oder war niemand zu Hause, wird sie nur gezählt. Im Urlaub und wenn
   alle lange weg sind, bleibt die Reserve eingefroren.

Der Status steht auf der HeatPilot-Seite im Feld „Leitplanken“.

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
(Reiter Diagnose) und im Log. Beim Update auf 0.14 wird der Lernstand einmal
mit diesem Schutz aus den gespeicherten und den Home-Assistant-Daten neu
berechnet; „Historie neu einlesen“ macht dasselbe auf Knopfdruck.

## Fußbodenheizung ohne Stellventil

Für Räume mit Fußbodenheizung, deren Kreis nicht geregelt wird (z. B. festes
RTL-Ventil), in Einstellungen → Räume das Häkchen **„Fußboden“** setzen.

Das Raummodell bekommt dann einen zweiten Wärmeeingang: Der Fußboden heizt
immer, wenn warmes Heizwasser fließt – unabhängig von Tado – und wirkt über den
Estrich verzögert (≈3 h). Der Tado-Heizkörper im Raum wird als Boost gelernt.
Ohne diese Angabe würde das Modell die Fußbodenwärme als „Fremdwärme“ deuten
und das Auskühlen des Raums falsch einschätzen.

## Urlaub

In Einstellungen → **Urlaub** Zeiträume mit „+ Urlaub“ anlegen: Beginn und Ende
als Datum, Uhrzeiten optional. Ohne Uhrzeit beim Ende gilt die
**Standard-Rückkehrzeit** (Vorgabe 18:00). Optional ein **Urlaubs-Schalter**
(z. B. `input_boolean.urlaub`, an = Urlaub).

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
Die Steuerung nutzt die Vorhersage erst ab 0.37 – bis dahin ist sie Anzeige und Vorheizplan.

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

Strategie (läuft im Schatten mit, setzt nichts): Niemand zu Hause und keine
Ankunft absehbar → Minimum. Zapfung in Kürze erwartet → Komfort
(bei viel Verbrauch so hoch, dass der Speicher danach nicht unter das
Minimum fällt). Sonst → Minimum. Nachgeladen wird rechtzeitig, weil die
Ladegeschwindigkeit bekannt ist. Die Ersparnis ist der eingesparte
Bereitschaftsverlust bei denselben Zapfungen; Zapfungen, bei denen der
Speicher unter der Komforttemperatur gewesen wäre, werden als „zu kalt“ gezählt –
nicht aber solche, die er nur dank der Legionellenschaltung bedient (deren Wärme
bekommt die Strategie genauso): die Wanne danach, die warm beginnt und erst durch
ihre Größe unter das Minimum zieht, höchstens 12 Stunden nach der Sonderladung.
Eine Zapfung, die der Vergleichsspeicher schon unter dem Komfortwert bedient,
zählt auch dann. Die wöchentliche Desinfektion des Reglers bleibt unberührt.

## Entitätsnamen

EMS-ESP-Entitäten werden in beiden Schreibweisen erkannt: Kurzname
(`binary_sensor.boiler_burngas`) und langer Name (`binary_sensor.boiler_gas`),
auch mit Präfix (`number.ems_esp_thermostat_hc1_remotetemp`).
