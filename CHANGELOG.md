# Changelog

## 0.31.0
Hausmodell genauer, nachgerechnet mit dem Export vom 18.10. (abgesprochen):
- **Auskühlen in zwei Phasen.** Direkt nach dem Abschalten geben Luft und Heizkörper ihre Wärme schnell ab, mit deinen Daten zusätzlich ≈ 0,8 K in den ersten Stunden. Danach kühlen nur noch die Wände langsam aus. Mit einer einzigen Zeitkonstante kühlte das Haus in der Rechnung nach einem Tag ohne Heizung doppelt so schnell aus wie in Wirklichkeit. Mit zwei Phasen ist der Fehler um 30 % kleiner. Die „Haus-Trägheit“ zeigt jetzt die der Wände, die Ersparnis-Schätzung rechnet mit beiden Phasen
- **Warmwasser aus den Altdaten herausgerechnet.** Die Stunden aus der Home-Assistant-Statistik vor HeatPilot enthielten auch das Warmwasser-Gas, zum Beispiel die Legionellenschaltung sonntags um 13 Uhr. Jetzt wird vor dem Lernen der übliche Sommerbedarf dieser Wochentags-Stunde abgezogen. Mit deinen Daten: Wärmeverlust 145 statt 153 W/K, Normheizlast 3,7 statt 3,9 kW, kleinerer Modellfehler. Der Wärmebedarf-Graf rechnet genauso
- Beim Update wird der Lernstand einmal neu berechnet

## 0.30.2
Hausmodell aus der Prüfung des Exports vom 18.10. Das Modell selbst passt zu den Daten: Normheizlast 3,9 kW, an den Tagesmitteln 2–5 % Abweichung. Korrigiert wurden:
- **Ersparnis-Schätzung zeigte 71 % statt 2 %.** Während der 19 Tage Abwesenheit im September war die Heizung aus. Wärmte die Sonne das echte Haus, blieb das simulierte abgesenkte Haus kalt, und der Unterschied wurde als Ersparnis gutgeschrieben. Jetzt bekommen beide Häuser in gasfreien Stunden dieselbe Sonne. Außerdem kann aufsummiert nie mehr gespart werden, als wirklich Heizgas verbraucht wurde
- **Neuberechnung beim Update:** Ältere Stunden, die HeatPilot schon aus eigenen Messwerten gebildet hatte, wurden mit der Langzeitstatistik überschrieben. Damit gingen Warmwasser-Anteil, Anwesenheit und Urlaub verloren. Diese Stunden bleiben jetzt erhalten
- Woche „Gas wetterbereinigt“: Erklärtext präzisiert (je Stunde nachts niedriger, morgens höher)

## 0.30.1
Zeitumstellung (25.10.) geprüft. Gelernt und gespeichert wird in absoluter Zeit (Unix-Sekunden), das Lernen bleibt also unberührt. Drei Anzeigen lagen am Umstellungstag bzw. über die Umstellung hinweg eine Stunde daneben:
- **Tagesgrenzen** (Brennerstarts heute, Tagesbalken): Mitternacht wird jetzt je Kalendertag bestimmt. Der 25-Stunden-Tag zählt also vollständig
- **Warmwasser:** Zapfprofil, Trefferquote und „Nächste Ladung“ lesen die richtige Stunde, auch wenn die Vorhersage über die Umstellung reicht
- **Anwesenheit:** Eine gewohnte Heimkehr (z. B. sonntags 17:00) bleibt 17:00 nach Wanduhr und rutscht nicht auf 16:00

## 0.30.0
Raummodelle aus der Prüfung des Exports vom 18.10. Jeder Punkt wurde mit den echten Daten nachgerechnet und gegengeprüft:
- **Ein einzelner Schritt warf ein Raummodell um.** Alle Parameter starteten mit derselben, viel zu großen Unsicherheit. Im Arbeitszimmer genügte ein Schritt mit kurz offenem Ventil (+0,4 K beim Start des Tagbetriebs): Die Heizwirkung stieg dadurch von 0,02 auf 0,24 und blieb dort. Jetzt hat jeder Parameter eine eigene Startunsicherheit. Das Auskühlen startet beim Wert eines typischen Hauses (≈ 55 h).
- **Seltene Heizschritte galten als Ausreißer.** In selten beheizten Räumen wurde ausgerechnet der informative Heizschritt gekappt. Im Bad lag die Heizwirkung deshalb etwa beim Drittel. Die Ausreißer-Schwelle berücksichtigt jetzt, wie unsicher die Vorhersage für diesen Schritt ist. Auf das Hausmodell wirkt sich das nicht aus, das wurde an den echten Daten nachgerechnet.
- **Gedächtnis der Raummodelle von ~10 Tagen auf ~6 Wochen.** Die Auskühlrate ist eine Gebäudegröße und lässt sich im Herbst nur langsam von der Fremdwärme trennen.
- **Ergebnis mit deinen Daten:** Kein Raum kühlt mehr rechnerisch unter die Außentemperatur (vorher bis −14 K). Die Zeitkonstanten liegen bei 47–139 h (Haus 56 h). Flur und Wohnzimmer bestehen die Leitplanken. Die übrigen Räume zeigen weiter „lernt noch“ mit Grund: zu wenig Heizbetrieb, ein für Tados kleine Ventilöffnungen unrealistisch hohes Verhältnis von Heizwirkung zu Auskühlen, oder Fremdwärme unter außen.
- Beim Update wird der Lernstand einmal neu berechnet.

## 0.29.1
Nachbesserungen aus der Gegenprüfung von 0.29.0:
- **Vorlauf-Studie:** Mit Hausmodell zählen nur Stunden mindestens 2 K über der Heizgrenze. Darunter sagt das Modell kein Gas voraus, und das Herausrechnen der Fremdwärme hätte das Urteil zu „lohnt“ gekippt
- **Balkengrafen mit lauter Nullen** (Brenner ohne Wärmebedarf, Taktsperre, Ersparnis pro Tag) sind jetzt wirklich sichtbar, mit einem Satz. Bisher wurde die Karte trotzdem ausgeblendet
- **Zertifikat:** Stunden aus der Langzeitstatistik ohne Warmwasser-Angabe zählen bei den Takt-Mehrkosten nicht mehr als Heizgas
- **Ersparnis:** In der Jahres-Hochrechnung zählen Urlaubstage als Tage ohne Ersparnis. Ohne Warmwasser-Signal steht „vom gesamten Gas“ statt „vom Heizgas“
- **Vorheizplan:** Wird ein Raum selbst mit dem höchsten Vorlauf nicht warm, wird er genannt, statt „Modell lernt noch“ anzuzeigen
- „Empfehlung jetzt: lernt noch“ erscheint nur noch mit Speicherfühler
- Texte zur Fremdwärme richtiggestellt: Sie wird wieder hinzugezählt, der Wert entspricht dem Wärmeverlust

## 0.29.0
Die Anzeigen der Übersicht passen jetzt zum Modell. Grundlage war eine Prüfung jeder Kachel, jedes Grafen und des Zertifikats gegen den Code und im Simulator:
- **Ersparnis-Schätzung:** Die Prozentangabe bezog sich auf das gesamte Gas mit Warmwasser. Sie bezieht sich jetzt nur noch auf das Heizgas. Stunden aus der Langzeitstatistik ohne Warmwasser-Angabe zählen nicht mit. In gasfreien Stunden klingt die Absenkung mindestens mit der Trägheit ab: Sonne wärmt das abgesenkte Haus genauso wie das echte. Ist das Hausmodell unsicher, steht der Hinweis „vorläufig“ beim Text.
- **Zertifikat:**
  - Die Mehrkosten durch Takten wurden aus wenigen Wochen aufs Jahr hochgerechnet. Jetzt stehen dort die Kosten im gemessenen Zeitraum („in 40 Tagen“), bis ein volles Jahr vorliegt. Der Warmwasser-Anteil wird nach Energie gerechnet.
  - Steht kein Urteil zur Kesselgröße da, erscheint der Kostenhinweis beim Taktverhalten.
  - „Im Schnitt – Heizstarts“ ist behoben. Ohne Heizstart-Zähler heißt es „Brennerstarts (mit Warmwasser)“. Der angefangene Tag zählt nicht mehr ins Mittel.
  - Fehlt die Wohnfläche, steht der Hinweis darauf bei der Gesamtbewertung. Lädt das Zertifikat nicht, erscheint eine Fehlermeldung.
- **Räume:** Für Räume, die die Leitplanken nicht bestehen, steht „lernt noch“ (mit Grund) statt Zeitkonstante, Aufheizzeit, Vorlauf und Sonne.
  - **Fußbodenräume** zeigten „nicht erreichbar“, obwohl der Vorlauf für 21 °C niedrig war. Ursache: Der Estrich wurde beim Aufheizen als kalt angenommen. Jetzt heizt er im Dauerbetrieb mit, und die Aufheizzeit wird mit der Estrich-Verzögerung berechnet. Das korrigiert auch den Vorheizplan.
  - Liegt der Vorlauf über dem eingestellten Maximum, steht das so da. Ohne Außenfühler steht dabei, dass 5 °C angenommen sind.
- **Warmwasser:** Bei unsicherem Modell sind Bereitschaftsverlust, nächste Ladung und Ersparnis als „vorläufig“ gekennzeichnet und die Modelllinie ist gestrichelt. „Empfehlung jetzt“ zeigt dann „lernt noch“ mit Grund, statt zu verschwinden.
  - Die Texte zum Zapfprofil wurden korrigiert: Trefferquote, „nach etwa 6 Wochen“, große Zapfungen „im Profil nur gedeckelt“.
  - Die Legionellenschaltung erscheint auch, wenn der Regler nur die Uhrzeit liefert.
- **Wärmebedarf des Hauses:** Wie beim Lernen zählen keine Stunden mit Warmwasserladung. Vorher waren Stunden mit bis zu 10 % Ladezeit dabei. Ist das Modell unsicher, ist die Linie gestrichelt.
- **Vorlauf-Studie:** In „Gas je K·h“ ist der Achsabschnitt des Hausmodells herausgerechnet. Bei Fremdwärme wird sie also wieder hinzugezählt, der Wert entspricht dem Wärmeverlust. Sonst sah der höhere Vorlauf schon deshalb teurer aus, weil der Regler ihn bei Kälte fährt. Stunden mit Warmwasser zählen nicht mehr. Im Heizkurven-Hinweis sind der Offset und das Raum-Soll eingerechnet.
- **Woche:**
  - „Gas wetterbereinigt“ rechnet mit plausiblem Hausmodell den Achsabschnitt heraus (Wärmeverlust je Kelvin).
  - Brennerstarts stehen in „Starts je Tag“ statt in „je h je Tag“.
- **Vorheizplan:** Das Gas fürs Heizen berücksichtigt die Fremdwärme.
- **Laufzeit je Heizstart:** Ohne Heizlaufzeit-Zähler wird sie aus Brennerläufen ohne Warmwasser berechnet, in der Kachel und im Grafen.
- **Leitplanken:** Als feste Untergrenzen stehen die eingestellten Temperaturen da. Daneben steht, mit welchen aus Tado gelernten Werten die Ersparnis rechnet. Für Fußbodenräume gibt es einen eigenen Text bei der Prüfung je Raum.
- **Kleinigkeiten:**
  - Belege der Empfehlungen in Prozent mit Komma.
  - Der Komfort-Hinweis fehlte beim Jahreswert der Ersparnis.
  - Die Balkengrafen zeigen bei lauter Nullen einen Satz statt „Noch keine Daten“.
  - Anwesenheit: Bei einer Rast auf der Heimfahrt bleibt die GPS-Ankunftszeit stehen.
  - Raum- und Personennamen werden sicher dargestellt.

## 0.28.0
Korrekturen am Warmwasser aus der Export-Prüfung (jeder Punkt gegengeprüft):
- **Ladekosten wurden nie gelernt**, wenn der Kessel seine Nennleistung nicht als Zahl meldet – die Nennleistung aus den Einstellungen wurde beim Warmwasser nicht verwendet. Folge waren „Modell unplausibel“, keine Empfehlung, Bereitschaftsverlust „–“ und Ersparnis 0. Betraf auch „Brenner ohne Wärmebedarf“ (kWh) und die Ersparnis-Simulation
- **Legionellenschaltung** wird jetzt auch ohne Kesselschalter erkannt: eine Ladung weit über das Soll (mehr als 8 K) zählt als Sonderladung und wird nicht gelernt
- **Gleichzeitig gezapft** (z. B. Wanne beim Ladestart): die Ladung zerfiel bisher in Stücke und lieferte falsche Ladekosten. Nach dem Ladezähler bleibt „lädt“ bestehen, bis der Brenner aus ist; Ladungen mit Zapfung zählen, lernen aber keine Kosten
- Ladung aus dem Temperaturanstieg nur, wenn das Kesselwasser heißer ist als der Speicher (der erste Heizschritt nach einer Ladung galt sonst als Warmwasser)
- **Empfehlung:** seltene, aber regelmäßige Nutzung (z. B. Wanne am Sonntag) verschwand im Mittelwert – jetzt zählt auch, *wie oft* in einer Stunde gezapft wird, und wiederkehrende große Zapfungen. Die Reserve nach zu kalten Zapfungen hebt auch das Minimum an. Die Empfehlung gibt es direkt nach dem Start
- Ladekosten-Mittel nach Zahl der Messungen gewichtet; „noch nicht gemessen“ statt „außerhalb –“ in den Leitplanken
- Beim Update wird der Lernstand einmal neu berechnet

## 0.27.0
Korrekturen an den Raummodellen aus der Export-Prüfung (jeder Punkt gegengeprüft):
- **Wärme kommt über die Pumpe:** bisher zählte Wärme nur, solange der Brenner brannte. Bei taktenden Kesseln läuft die Heizungspumpe aber 3- bis 4-mal so lange und liefert dabei Wärme. Dadurch war die Heizwirkung je Raum zu hoch gelernt – „Vorlauf für 21 °C“ und Aufheizzeiten waren unrealistisch niedrig. Live zeigt die Pumpe jetzt nach ihrer Drehzahl
- **Kein Aufblähen in Aus-Phasen:** lange Zeiten ohne Heizbetrieb machten das Lernen instabil, ein einzelner Heizschritt danach konnte das Modell umwerfen (gerichtetes Vergessen)
- **Sonne kann nicht kühlen:** negative Sonnenwerte werden nicht mehr gelernt; ein Raum kühlt rechnerisch nie unter die Außentemperatur
- **Strengere Leitplanken je Raum:** unplausibles Verhältnis Heizwirkung/Auskühlen, zu wenig gesehener Heizbetrieb, unsichere Zeitkonstante oder Endtemperatur unter außen → keine Empfehlungen und keine Vorlauf-/Komfortaussagen für diesen Raum, bis er genug gelernt hat
- **„Soll nicht erreichbar – Vorlauf am Limit“:** der Höchstwert stammte aus Warmwasser- bzw. Legionellenladungen; jetzt zählt nur der Vorlauf, der wirklich zu den Heizkörpern geht
- **Ersparnis-Schätzung:** Stunden, in denen die Heizung ohnehin aus war, bringen keine Ersparnis mehr; nie negative Ersparnis
- Beim Update wird der Lernstand einmal neu berechnet

## 0.26.0
Korrekturen am Hausmodell aus der Prüfung eines echten Exports (jeder Punkt gegengeprüft):
- **Fremdwärme statt gekappter Grundlast:** Sonne, Personen und Geräte decken einen Teil der Verluste – der gelernte Achsabschnitt ist negativ und wurde bisher auf 0 gesetzt. Heizlast, „Heizlast jetzt“, Zertifikat, Wochenansicht „Abweichung vom Hausmodell“ und die Linie im Wärmebedarf-Grafen lagen dadurch rund 0,8 kW zu hoch. Neue Kachel „Fremdwärme“ mit Heizgrenze
- **Trägheit gemessen:** ein eigenes Modell lernt in gasfreien Nächten, wie schnell das Haus auskühlt. Vorher kam die Trägheit aus der Gasbilanz (Speicher/Wärmeverlust) und war 3- bis 4-mal zu kurz – die Ersparnis-Schätzung hat damit das Auskühlen bei Abwesenheit überschätzt
- **Warmwasser raus aus dem Hausmodell:** jede Stunde mit Ladung bleibt draußen (vorher erst ab 30 % der Stunde – eine typische 15-min-Ladung mit voller Leistung zählte als Heizen)
- **Wochenansicht:** Gas fürs Warmwasser nach Brennerleistung statt nach Zeitanteil aufgeteilt (war rund 3× zu niedrig)
- **Mittige Ableitung:** die Temperaturänderung passt jetzt zeitlich zum Gas der Stunde (vorher eine halbe Stunde versetzt)
- **Heizperiode** über das 24-h-Mittel außen: warme Sommertage mit kühlen Nächten werden nicht mehr gelernt
- Langzeitstatistik: Legionellen- und Aufheizstunden nach ruhigen Tagen galten als Zählersprung, gesunkene Zählerstände wurden mitgezählt
- Beim Update wird der Lernstand einmal neu berechnet. Mit den geprüften Daten: Modellfehler 0,83 → 0,55 kW

## 0.25.4
- **Empfehlungen:** „unnötig früh warm“ kam auch, wenn der Raum einfach noch warm war und Tado das Ventil gar nicht geöffnet hatte (der Zeitplanblock ist nur ein Soll, es floss keine Wärme). Jetzt zählt das nur noch, wenn Tado vor dem Block mit offenem Ventil vorgeheizt hat. Räume, die ohnehin warm sind, bekommen weder Spar- noch Komfort-Hinweise zum Zeitplan. Die alten Hinweise werden zurückgezogen, nicht als „behoben“ gemeldet
- Zertifikat: die Fußzeile lag auf dem Rahmen – sie steht jetzt innerhalb; reicht der Platz nicht, wird der Inhalt enger gesetzt

## 0.25.3
- Zertifikat rein sachlich: bezieht sich nur auf die letzten 12 Monate Messung (keine Werte „seit Einbau“ mehr) und nicht mehr auf Vermietung

## 0.25.2
- Zertifikat: bei zu großem Kessel bzw. sehr kurzen Läufen ein Hinweis auf die **Ineffizienz** – „Mehrkosten durch Takten (Schätzung)“ in € pro Jahr, gerechnet mit Erfahrungswerten von 5–15 % des Heizgases und deutlich als Schätzung gekennzeichnet
- Zertifikat: „Laufzeit je Heizstart“ auch ohne Heizlaufzeit-Zähler des Kessels – aus den aufgezeichneten Brennerläufen der letzten 30 Tage (ohne Warmwasserladungen); „seit Einbau“ notfalls aus Gesamtlaufzeit und allen Starts

## 0.25.1
- Diagnose: die langen Listen „Alle Sensoren“ und „Log“ stehen jetzt am Ende der Seite
- Behoben: der Abschnitt „Anwesenheit“ war seit 0.22 versehentlich in die Log-Karte der Diagnose gerutscht – er steht wieder am Ende des Live-Reiters

## 0.25.0
- **Mehr EMS-ESP-Daten im Modell** (wenn der Kessel sie liefert):
  - Warmwasser-**Ladezähler**: jede Speicherladung wird exakt erkannt statt nur aus dem Temperaturanstieg
  - **Legionellenschaltung** und **Einmalladung** laufen mit, werden aber nicht gelernt (weder Ladekosten noch Zapfprofil); Tag/Uhrzeit der Legionellenschaltung als Kachel
  - **Nächste Ladung**: aus Soll und Einschalt-Hysterese des Kessels (z. B. 55 − 10 = 45 °C) plus gelerntem Auskühlen und Zapfprofil
  - **Heizkurve des Reglers** in „Vorlauf gegen Takten“: +5 K Vorlauf als konkrete Auslegungstemperatur am ES73 (z. B. 60 → 68 °C)
  - Live: **Kesselstatus** (Servicecode mit Klartext, z. B. „0Y – Kessel wärmer als Soll“), **Anlagendruck**, Pumpennachlauf
  - Diagnose: Anlagendruck (Warnung unter 1,0 bar), Wartungsmeldung des Kessels, Fehlercodes der letzten 14 Tage
- **Export „Gelernte Werte“** enthält jetzt auch Selbsttest, rohe EMS-ESP-Werte, gelernte Zeiten, Warmwasser-Auswertung (inkl. nächster Ladung), Vorlauf-Studie, Wochenraster und je Person Gewohnheit, Heimkehr-Kontrolle und Vorhersagegüte – alles zum Gegenprüfen in einer Datei

## 0.24.5
- **EMS-ESP auf Deutsch:** „an“/„aus“ werden jetzt erkannt – vorher galten Brenner, Pumpe usw. beim direkten Lesen als „aus“, sobald EMS-ESP deutsche Werte lieferte
- Warmwasser lädt: wenn der Kessel kein Ladesignal hat, zählt auch die Speicherladepumpe (`dhw_chargepump`) bzw. das 3-Wege-Ventil; die Erkennung über den Temperaturanstieg bleibt zusätzlich aktiv
- Diagnose → Alle Sensoren: Werte, die der Kessel laut EMS-ESP gar nicht kennt, heißen jetzt „Kessel liefert nicht“ statt „fehlt“ – da lässt sich nichts freischalten

## 0.24.4
- „Gas und Außentemperatur“: Marker verschiebbar wie in den anderen Grafen – zeigt Uhrzeit, Außentemperatur und das Gas der Stunde (Balken wird hervorgehoben)

## 0.24.3
- Grafen am Handy: Hinweisfeld steht etwa einen halben Zentimeter höher über dem Finger

## 0.24.2
- Grafen am Handy: Marker mit dem Finger **verschieben** (waagerecht wischen; senkrecht scrollt weiter die Seite). Das Hinweisfeld erscheint links über dem Finger mit etwas Abstand und verschwindet nach 3 s – auch Marker und Punkt; es bleibt nicht mehr hängen

## 0.24.1
- Live: Warmwasser-Graf steht jetzt vor „Außen & innen“

## 0.24.0
- **Heizen** ist jetzt der erste Reiter und öffnet beim Start
- Räume umbenennen: Spalte **Anzeigename** unter Einstellungen → Räume – gilt nur in HeatPilot, in Home Assistant bleibt der Name (z. B. „Wohnzimmer Heizung“ für Siri). Der neue Name gilt überall, auch für bereits gelernte Raummodelle

## 0.23.1
- Handy: Seite scrollt nicht mehr seitlich – Raum-Legende und Legenden brechen um, Grafen und Kacheln passen auch auf schmale Bildschirme (ab 320 px)
- Raumnamen kürzer: „Katzenzimmer Heizung“ erscheint in Legende, Boost und Kacheln als „Katzenzimmer“

## 0.23.0
- Neuer Reiter **Heizen** – Tado bedienen, nur auf Knopfdruck (HeatPilot schaltet weiterhin nie von selbst):
  - **Heiz-Boost:** Räume wählen, Temperatur und Dauer einstellen, Starten – nutzt den **Tado-Timer** (`tado.set_climate_timer`), nach Ablauf gilt in Tado wieder der Zeitplan. „Zeitplan“ beendet ihn sofort
  - **Raumkacheln** farbig nach Soll: Ist, Ventil, Feuchte, Soll −/+ und Zeitplan / Manuell / Aus, Restzeit eines laufenden Timers
  - Erlaubt sind nur Tado-Timer, Soll und Betriebsart auf die gewählten Räume; alles andere (auch EMS-ESP) bleibt im Code gesperrt
- Handy: Reiterleiste wischbar statt über den Rand

## 0.22.0
- Live: **alle Räume gemeinsam** in drei Grafen – Raumtemperatur, Luftfeuchte und Heizbedarf (Ventile), eine Farbe je Raum; Räume per Antippen aus- und einblenden (bleibt gemerkt). Die Einzelgrafen je Raum stehen aufklappbar darunter
- Live: neuer Graf **Gas und Außentemperatur** – Gas je Stunde als Balken, Außentemperatur als Linie
- Kurven ruhiger und schärfer: Messwerte werden auf die Bildschirmbreite gemittelt und weich gezeichnet (keine Treppen aus 0,1-K-Sprüngen mehr), dünnere Linien, höhere Grafen, Lücken (z. B. Neustart) werden nicht überbrückt

## 0.21.1
- Live und alle anderen Ansichten: statt „The string did not match the expected pattern“ (HA-App und Browser auf iPhone/iPad, wenn die Antwort kein gültiges JSON war) steht jetzt die Ursache da – z. B. „Add-on antwortet nicht (HTTP 502) – startet es gerade neu?“ oder der Fehler aus dem Add-on. Live versucht es nach 15 s selbst erneut
- Add-on: Fehler einer Abfrage kommen als lesbare Meldung zurück und stehen mit Ursache im Protokoll; ungültige Zahlen (NaN, unendlich) werden zu „kein Wert“, statt die ganze Antwort unlesbar zu machen

## 0.21.0
- **Die Woche im Blick** (Übersicht): Wärmekarte Montag bis Sonntag × Stunde für das ganze Haus, nicht nur fürs Warmwasser – umschaltbar: Gas gesamt, Gas fürs Heizen, Gas für Warmwasser, **Gas wetterbereinigt** (W je K innen–außen, damit eine kalte Woche nicht wie ein „teurer Wochentag“ aussieht), **Abweichung vom Hausmodell** (zeigt, welche wiederkehrenden Zeiten das Modell noch nicht erklärt), Brennerstarts, jemand zu Hause, Tado-Soll und Ventile (auch je Raum). Summe bzw. Mittel je Tag, Klartext zu Spitzen, stärkstem Wochentag und Wochenende; Zeitraum 4 Wochen bis 1 Jahr, Urlaub zählt nicht
- **Anwesenheit im Live-Tab:** je Person die Entfernung im Verlauf (12 h bis 7 Tage, zu Hause hinterlegt), wie HeatPilot die Lage gerade deutet (zu Hause · Heimfahrt erkannt · kommt näher · entfernt sich · steht), erwartete Ankunft mit Quelle, die gelernte Gewohnheit als Wochenraster und eine **Heimkehr-Kontrolle**: was 60, 30 und 15 min vor jeder tatsächlichen Ankunft erwartet wurde und wie weit das daneben lag. Nur Entfernungen, keine Koordinaten
- Übersicht „Anwesenheit“: Status zeigt die Deutung (z. B. „kommt näher (35 km/h)“) statt nur „unterwegs“

## 0.20.2
- Warmwasser „Wann gezapft wird“: Umschalter **Typischer Tag / Woche**. Die Woche zeigt als Wärmekarte die Vorhersage je Wochentag und Stunde, so wie HeatPilot sie zum Planen nutzt, und wie stark der Wochentag schon zählt (wächst mit jeder gelernten Woche, höchstens 70 %)

## 0.20.1
- Live: Luftfeuchte je Raum – im Kopf jeder Raumkarte, als gepunktete Linie im Raum-Grafen (rechte Skala) und als Kachel „Luftfeuchte Ø“ mit trockenstem und feuchtestem Raum (ab 65 % Hinweis „lüften“)

## 0.20.0
- **Sonne als Wärmequelle je Raum:** Sonnenstand wird aus Standort und Uhrzeit berechnet, die Bewölkung kommt aus der Wetter-Integration. Jeder Raum lernt selbst, wie stark und aus welcher Richtung (Ost/Süd/West) die Sonne ihn heizt – Räume ohne Sonnenfenster lernen „kaum Sonne“. Neue Spalte „Sonne“ in der Raumtabelle; bestehende Raummodelle werden erweitert, nicht verworfen
- **Vorlauf gegen Takten:** HeatPilot misst an den eigenen Heizstunden (gleiche Außentemperatur, nur Beharrung, ohne Warmwasser), ob ein höherer Vorlauf weniger Starts und weniger Gas je Grad bringt – oder durch warmen Rücklauf Verschwendung ist. Mit klarem Urteil, Tabelle je Außentemperatur und Versuchsvorschlag, wenn die Daten nicht reichen. Nur Beobachtung
- Beim Update wird der Lernstand einmal neu berechnet (Sonne in der Historie)

## 0.19.0
- Zertifikat: **HeatPilot-Gesamtbewertung** – alles auf einen Nenner: 0–100 Punkte und Klasse A+ bis H aus Gebäude (40 %), Kesselgröße (25 %), Taktverhalten (20 %) und Warmwasser (15 %). Erscheint erst, wenn das Gebäude und mindestens zwei weitere Bereiche bewertet sind. Im PDF, in der Druckansicht und in der Übersicht

## 0.18.4
- Zertifikat: neue **Druckansicht** in derselben Seite (auch in der Home-Assistant-App). Über „Drucken / als PDF sichern“ erzeugt das Gerät selbst ein PDF – z. B. unter iOS zum Weitergeben an WhatsApp, wo die direkt erzeugte Datei beim Teilen leer ankam (die Datei selbst ist laut qpdf-Prüfung fehlerfrei)

## 0.18.3
- Zertifikat: Die Seite lädt das PDF erst und prüft es, bevor es gespeichert oder angezeigt wird. Eine Fehlerantwort landet nicht mehr als „leeres PDF“ auf dem Gerät, sondern erscheint als Meldung unter den Knöpfen; Fehler beim Erstellen stehen mit Details im Log. Zusätzlich ein direkter Link als Ausweichweg

## 0.18.2
- Oberfläche: Kacheln ohne Wert und Karten ohne Daten (z. B. leere Diagramme, Empfehlungen, Vorheizplan, Personen) werden ausgeblendet und erscheinen von selbst, sobald Daten da sind – auch im Reiter Live
- Hauswerte (Wärmeverlust, Heizlast, Trägheit) erscheinen erst, wenn das Hausmodell plausibel ist

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
