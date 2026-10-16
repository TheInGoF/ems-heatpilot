# EMS HeatPilot

Lokale, selbstlernende Heizungsoptimierung für Kessel mit
[EMS-ESP](https://github.com/emsesp/EMS-ESP32) ([Doku](https://emsesp.org)),
Tado und Anwesenheit.

> **Beta (0.x): Raumregelung nur mit Tado.** Offene, direkt mit Home Assistant
> verbundene Thermostate sollen später unterstützt werden.

## Einrichten

Nach der Installation öffnet die HeatPilot-Seite (Seitenleiste) direkt den
Reiter **Einstellungen**. Eingerichtet wird ausschließlich dort – das Add-on hat
keine YAML-Optionen:

- **Verbindung:** EMS-ESP-Adresse mit „Testen“-Knopf (nur lesend, kein Token)
- **Messwerte:** Außenfühler (einer oder mehrere, Minimum zählt), Gaszähler,
  Wetter, optional eine Heizungsanforderung – Auswahl mit Suche aus allen
  Entitäten in Home Assistant
- **Räume:** alle Thermostate als Tabelle – Häkchen, Name, Fußbodenheizung,
  optionale Ventil-/Temperatur-/Feuchtesensoren
- **Gas & Kosten, Komfort & Strategie, Warmwasser, Urlaub**
- **Zuordnungen (Experten):** was die Erkennung gefunden hat, mit eigener Korrektur
- **Erweitert:** Abtastintervall, Historie, „Historie neu einlesen“

Felder sind mit Vorschlägen der automatischen Erkennung vorausgefüllt
(„Vorschläge übernehmen“ setzt sie erneut). **Speichern** prüft alle Eingaben,
erklärt Fehler und übernimmt sofort, ohne Neustart. Die Einstellungen liegen in
`/data/settings.json` und bleiben bei Updates erhalten.

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
Weitergeben, z. B. an Vermieter oder Heizungsbauer. Es zeigt, gemessen statt
geschätzt:

- **Gebäude:** Wärmeverlust, Heizlast am kältesten Tag, Trägheit; mit Wohnfläche
  auch Heizlast je m² und nach 12 Monaten eine Energieklasse (A+ bis H, Skala wie
  im Energieausweis)
- **Heizkessel:** Nennleistung und kleinste Leistung gegen die Heizlast, Anteil der
  Heizstunden unter der kleinsten Leistung (dort muss der Kessel takten),
  Heizstarts pro Tag und Laufzeit je Start
- **Warmwasser & Verbrauch:** Warmhalteverlust des Speichers, Gasverbrauch und -kosten
- **Urteil** in Klartext und ein Stempel: „GEMESSEN“ oder „VORLÄUFIG“ (mit Grund),
  solange das Hausmodell unsicher ist, weniger als 500 Heizstunden oder noch kein
  Frost gemessen wurden

Wohnfläche, Nennleistung und kleinste Leistung des Kessels stehen unter
**Einstellungen → Haus & Kessel** (0 = unbekannt bzw. Wert von EMS-ESP). Das
Zertifikat enthält keine Namen, Adressen oder Standortdaten. Es ist kein
Energieausweis nach GEG, die Werte sind nicht witterungsbereinigt.

## Live

Der Reiter **Live** zeigt, was Heizung und Haus gerade tun und in den letzten
3, 6, 12 oder 24 Stunden getan haben; er aktualisiert sich alle 30 Sekunden.

- **Anzeigen:** Brenner (an/aus, Leistung, heizt oder lädt Warmwasser, Taktsperre
  mit Restzeit), Vorlauf mit Soll von Kessel und Regler, Rücklauf mit Spreizung,
  Kessel und Pumpe, Warmwasser, außen, innen Ø, Wärmebedarf laut Tado, wer zu Hause ist
- **Grafen:** Kesseltemperaturen (hinterlegt, wann der Brenner heizt bzw.
  Warmwasser lädt), Brennerleistung und Pumpe, außen/innen, Warmwasser und je
  Raum Temperatur, Tado-Soll und Ventilöffnung

Die letzten 12 Stunden hält HeatPilot im Speicher (alle 30 s, mit EMS-ESP direkt
am genauesten); ältere Abschnitte und die Zeit vor einem Neustart kommen aus der
Datenbank (alle 5 min).

## Empfehlungen für Tado

Tado lernt selbst und entscheidet, *wann* ein Raum Wärme zieht. Fängt Tado zu
spät an, kann HeatPilot das über den Kessel nicht retten – ein höherer Vorlauf
hilft nur begrenzt. Deshalb misst HeatPilot je Raum und schlägt im Feld
**„Empfehlungen für Tado“** (und `sensor.heatpilot_tado_empfehlungen`) konkrete
Änderungen vor. Eingestellt wird in der Tado-App – HeatPilot ändert nichts selbst.

| Was gemessen wird | Mögliche Empfehlung |
|---|---|
| Zeitplanblock: wie lange nach dem Soll-Anstieg ist der Raum warm? Öffnet das Ventil schon vorher (Frühstart je Raum erkannt)? | „Frühstart für den Raum einschalten“ · „Frühstart aktiv, reicht nicht: Block X min vorziehen“ · „unnötig früh warm: Block später / Frühstart aus“ |
| Geofencing bei jeder echten Heimkehr: wie lange vorher hebt Tado das Soll an, wie warm ist der Raum bei Ankunft? | „bei Ankunft zu kalt: Vorheiz-Einstellung einen Schritt Richtung Komfort“ · „lange vor Ankunft warm: Richtung Eco“ |
| Soll bei Abwesenheit | „Abwesenheitstemperatur senken“ – nur, wenn der Raum laut Modell danach in ≤ 2 h wieder warm ist |
| Raum über Stunden unter Soll, Ventil voll offen | „Soll senken oder Heizkörper/Ventil prüfen“ (mit Hinweis, ob der Vorlauf am Limit war) |
| Hydraulischer Abgleich (siehe unten) | „bekommt zu wenig Heizwasser: Voreinstellung eine Stufe höher“ · „bekommt mehr als nötig: eine Stufe zurück“ |

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
als Mieter vorher kurz mit dem Vermieter absprechen.

## Was das Add-on in Version 0.13 macht

**Nur beobachten und lernen.** Es greift nicht in die Heizung ein:
- An EMS-ESP sendet es ausschließlich Lese-Anfragen (GET), ein Token wird nicht benötigt.
- In Home Assistant schreibt es nur seine eigenen `sensor.heatpilot_*`. Jeder
  andere Schreibversuch wird im Code hart abgewiesen.

1. **Daten übernehmen:** Beim ersten Start holt sich das Add-on selbst die
   vorhandenen Daten aus Home Assistant. Ein Export ist nicht nötig.
   - Langzeitstatistik (stündlich, bleibt in HA dauerhaft): Gaszähler,
     Außenfühler, Tado-Raumtemperaturen → das Hausmodell kann sofort aus
     Monaten an Daten lernen.
   - Recorder-Historie (Standard 10 Tage, siehe `purge_keep_days`): alle
     Einzelwerte → Raummodelle, Brennertakte, Anwesenheitsmuster.
2. **Laufend sammeln:** alle 5 Minuten ein kompakter Datensatz in
   `/data/heatpilot.db` (SQLite, wenige MB pro Jahr).
3. **Lernen (maschinelles Lernen, online):** Die Modelle passen sich mit
   jeder Messung an, ältere Daten verlieren langsam an Gewicht. So folgt das
   Modell Jahreszeiten und Änderungen am Haus.
   - *Hausmodell* (stündlich): Wärmeverlust in W/K, Wärmespeicher, Trägheit,
     Heizlast jetzt und bei Auslegungstemperatur.
   - *Raummodell* (je Tado-Raum, halbstündlich): wie schnell der Raum bei
     welcher Vorlauftemperatur warm wird und wie schnell er auskühlt.
   - *Anwesenheit* (je Person): Wochenraster der Gewohnheiten und
     Heimfahrt-Geschwindigkeit. Annäherung und Ankunftszeit berechnet das
     Add-on selbst aus den GPS-Daten der `person.*`-Entitäten.
   - *Zeiten und Temperaturen* (stündlich, letzte 4 Wochen ohne Urlaub): wann
     Tado abends absenkt und morgens wieder hochheizt, Nacht- und
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
| `sensor.heatpilot_ersparnis_schaetzung` | geschätzte Ersparnis durch Absenken (%), kWh/€ als Attribute |
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
Ventil-/Heizleistungs-Sensor (%), Temperatursensor (für die Langzeitstatistik)
und Feuchtesensor. Ohne Angaben schlägt die automatische Erkennung die
Tado-Räume samt Sensoren vor.

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
2. **Gezählt wird am Gaszähler, fürs ganze Haus** – Verschieben spart nichts.
3. **Feste Untergrenzen:** Abwesenheits-/Nachttemperatur, Warmwasser-Minimum.
   „Nie heizen“ ist keine erlaubte Strategie.
4. **Nur plausible Modelle empfehlen.** Liegen gelernte Werte außerhalb
   physikalischer Grenzen (z. B. Wärmeverlust, Trägheit, Grundlast, Speicher-
   Umgebung) oder ist zu wenig gelernt, gibt es keine Empfehlung.
5. **Kleine, gedeckelte Selbstkorrektur:** Nach einer zu kalten Zapfung +2 K
   Warmwasser-Reserve, höchstens +10 K, Abbau 0,25 K pro Tag.

Der Status steht auf der HeatPilot-Seite im Feld „Leitplanken“.

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

## Warmwasser

Gelernt wird aus Speichertemperatur, Ladestatus und Brennerleistung:

- **Auskühlen** des Speichers und daraus der Bereitschaftsverlust je Temperatur
- **Ladekosten** in Gas-kWh je Kelvin, getrennt nach Ladestart unter/über 47 °C
  (zeigt, ob Laden aus kälterem Speicher wirklich effizienter ist)
- **Zapfprofil:** wann im Tages-/Wochenverlauf Warmwasser gebraucht wird
- **Nachladegeschwindigkeit** in K/h

Strategie (läuft im Schatten mit, setzt nichts): Niemand zu Hause und keine
Ankunft absehbar → Minimum. Zapfung in Kürze erwartet → Komfort
(bei viel Verbrauch so hoch, dass der Speicher danach nicht unter das
Minimum fällt). Sonst → Minimum. Nachgeladen wird rechtzeitig, weil die
Ladegeschwindigkeit bekannt ist. Die Ersparnis ist der eingesparte
Bereitschaftsverlust bei denselben Zapfungen; Zapfungen, bei denen der
Speicher unter der Komforttemperatur gewesen wäre, werden als „zu kalt“ gezählt.
Die wöchentliche Desinfektion des Reglers bleibt unberührt.

## Entitätsnamen

EMS-ESP-Entitäten werden in beiden Schreibweisen erkannt: Kurzname
(`binary_sensor.boiler_burngas`) und langer Name (`binary_sensor.boiler_gas`),
auch mit Präfix (`number.ems_esp_thermostat_hc1_remotetemp`).
