# Changelog

## 0.40.0
**Thermostate als Stellglied, Stufe 2: Ventile führen** (abgesprochen; eigener Schalter, Standard aus – aus schreibt HeatPilot genau wie 0.39, nur Trockenlauf. Geschrieben wird nur über den bestehenden Weg der Raumsteuerung: `climate.set_temperature` auf die Thermostate von Räumen mit „macht mit“ bei „HeatPilot steuert“; Warmwasser-Soll (Weg W), Sperre des EMS-ESP-Heizkreises und Vorlauf unverändert, EMS-ESP bleibt nur lesend)
- **Schalter „Ventile führen (früh drosseln, gemeinsam anfordern)“** unter Einstellungen → Steuerung (`valve_control`, bool, Standard aus) mit Erklärung; der Hinweis darunter folgt dem Hauptschalter („wirkt erst mit „HeatPilot steuert““). Wirkt nur für Räume mit „macht mit“, plausiblem Raummodell, belastbarem Ventilmodell (nicht beim Nachlernen, keine gemischte Gruppe) und einem Schritt des Thermostats bis 0,5 K; nie für den Heizkreis von EMS-ESP
- **Höchstens ein Schritt vom Wunschsoll** (Schrittweite des Thermostats, Tado 0,5 K; auf den Schritt gerundet, 5–25 °C – `Control.apply` rundet dafür in der Schrittweite des Geräts statt immer auf 0,5 K). Die Steuerung rechnet das Wunschsoll wie bisher, die neue Datei `valve_control.py` legt den Schritt darauf, mit den Regeln aus `valve_dry` (nicht kopiert):
  - **früh drosseln** (−1 Schritt): wie im Trockenlauf (Soll laut Raum- und Ventilmodell in unter 20 min erreicht, Ventil dann noch über 30 % offen, Überschwingen vorhergesagt), dazu nur in den Phasen Komfort und Vorheizen (nie Nacht, Abwesenheit, Urlaub), nur ab Soll − 0,3 K und nur mit der Vorhersage beider Modelle. Zurück am Soll, spätestens nach 60 min, und sofort – auch in der Haltezeit –, wenn der Raum unter Soll − 0,3 K fällt
  - **gemeinsam anfordern** (+1 Schritt): Räume, die laut Raummodell in 60 min ohnehin heizen würden, wenn nur ein Raum anfordert und der Kessel taktet (nur mit Außentemperatur); zurück, wenn der Auslöser endet, spätestens nach 60 min. Keine Kette und kein Pendeln: Auslösen darf kein Raum, den HeatPilot gerade drosselt oder in den letzten 60 min angehoben hat (sein Ventil schließt gleich bzw. steht seinetwegen offen); angehoben wird kein Raum, der nach dem Drosseln gesperrt ist oder in den letzten 60 min schon angehoben war
- **Schreibregeln:** nur bei Änderung, 5 min Haltezeit je Raum nach jedem Schreibvorgang (außer dem sofortigen Zurück); Phasenwechsel gehen vor (neues Wunschsoll sofort, die Führung beginnt neu); Hand vor Automatik (App, Ventil, Heizen-Reiter, Boost, aus – auch „Alle aus“ und die Fenster-offen-Erkennung eines Thermostats: der Schritt ist vergessen, „aus“ bleibt aus, bis zum nächsten Phasenwechsel fasst HeatPilot den Raum nicht an); Eigenes zählt nie als Hand (Home Assistant meldet das Geschriebene bis zu zwei Takte später, ein Ventil einer Gruppe nahm es nicht an, die Einstellungen wurden während des Schreibens gespeichert – spätestens nach der Wartezeit steht wieder das Wunschsoll); Pause: kein neuer Schritt, das Zurück aufs Wunschsoll geht aber auch in der Pause hinaus (wer Wärme braucht, bekommt sie), und der Knopf „Pause“ nimmt laufende Schritte vorher einmal zurück; vor „Beobachten“ oder „macht mit“ aus nimmt HeatPilot einen laufenden Schritt noch unter den alten Einstellungen einmal zurück (nur wenn er noch am Thermostat steht); Schalter aus oder Modell nicht mehr belastbar: sofort zurück aufs Wunschsoll. Fehler wie die übrige Raumsteuerung (erneut nach 15 min, Meldung ab drei in Folge); erst ein erfolgreicher Schreibvorgang macht den Schritt gültig
- **Protokoll:** Schreibprotokoll mit „Ventil: früh drosseln (…)“, „Ventil: gemeinsam anfordern (…)“, „Ventil: zurück (…)“; dazu im Ventil-Protokoll als geschriebener Eintrag (neue Spalte `written` in `valve_dry_log`, alte Einträge zählen als Trockenlauf). Eine Stunde danach wird wie im Trockenlauf nachgetragen, was wirklich geschah – jetzt mit dem Eingriff (vorhergesagt und nachgeprüft wird das Überschwingen mit dem Eingriff). Zustand der Führung überlebt Neustarts (`valve_control.state`)
- **Trockenlauf:** Räume, die HeatPilot gerade führt, rechnet er nicht mehr mit; als anfordernde Räume zählen sie weiter
- **Oberfläche:** „Bereit für die Steuerung?“ mit neuem Block „Ventile führen“ (je Raum bereit oder lernt mit Grund, Schalter, laufende Eingriffe, Regeln; ändert die Stufe nicht); Heizen → Raum-Dialog „Ventil geführt: 20,5 statt 21 °C – früh drosseln, zurück ≈ …“ während eines Schritts; Übersicht → Vorschau: bei aktivem Schalter „Ventile – HeatPilot schreibt“ mit „geschrieben“ bzw. „würde schreiben“ je Beispiel, „davon … geschrieben“, Überschwingen mit und ohne Eingriff, Schreibvorgänge je Raum und Tag „(davon geschrieben)“, laufende Eingriffe; ohne Schalter wie 0.39
- **Abfragen:** `api/heat` je Raum `control.valve` (laufender Schritt) und `control.valve_guided`, `readiness.valves`; `api/preview` → `valves` mit `live`, `written`, `interventions_written`; Export „ventile → fuehren“
- **Raumsteuerung allgemein:** Sollwerte gelten mit Toleranz ab 0,1 K als verschieden – bei Thermostaten mit 0,1-K-Schritt kippte der Vergleich an Gleitkommaresten (eine Hand um genau einen Schritt wurde übersehen, unter 16,5 °C ein Schritt nie geschrieben). Meldet Home Assistant bis zu zwei Takte nach einem Schreibvorgang noch den Wert von vorher, zählt das nicht als Hand – der Heizen-Reiter zeigt dann nicht mehr kurz „manuell“; ohne „Ventile führen“ schreibt HeatPilot dabei genau wie in 0.39
- **Texte:** Hat die Nachprüfung des vorhergesagten Überschwingens schon genug Werte, liegt aber daneben, sagt der Satz das („bisher im Mittel 0,25 K daneben, höchstens 0,2 K“) statt „4 von 3 Nachmessungen“
- **Doku:** Abschnitt „Ventile führen“, „Bereit für die Steuerung?“, Trockenlauf und Steuerung ergänzt; Planungspapier (Stufe 2 gebaut), README Phase 5
- **Tests:** `tests/test_valve_control40.py` (alle Schreibregeln, künstliches Haus über einen Tag mit geschlossenem Regelkreis), `tests/test_ui_v40.py`, `tests/test_v40_review.py` (Funde der Prüfung: Hand „aus“ und „Alle aus“ während eines Schritts, eigener Schritt bei Teilfehler, Neuaufbau und verzögerter Meldung, Pause, Kette und Pendeln beim gemeinsamen Anfordern, 0,1-K-Schritt, „Beobachten“ bzw. „macht mit“ aus mitten im Schritt)

## 0.39.1
**Fußbodenheizung je Raum: Anschluss und höchster Vorlauf** (gewünscht; es wird nichts Neues geschrieben – kein zusätzlicher Aufruf an Kessel oder Thermostate, Raumsteuerung, Warmwasser-Soll und die Sperre des EMS-ESP-Heizkreises schreiben bzw. sperren wie bisher, EMS-ESP bleibt nur lesend)
- **Anschluss je Raum** (Einstellungen → Räume, Spalte „Fußboden“, ersetzt das Häkchen): kein Fußboden, **mit Mischer** (er regelt selbst – keine Grenze aus diesem Raum), **mit RTL-Ventil** (am Anfang fließt der volle Vorlauf hinein: die Grenze gilt, solange der Raum Wärme anfordert – ein Thermostat mehr als 0,1 K unter seinem Soll, Ventil über 5 % oder `hvac_action` „heating“ bzw. „preheating“ (heizt vor), bei Gruppen genügt eines; ohne Messwert – Thermostat nicht verfügbar – gilt sie auch) oder **direkt am Kessel** (die Grenze gilt immer, auch ohne Raummodell und Messwert). Dazu das Feld **höchster Vorlauf (°C)**, 25–70 °C in halben Grad, leer = 45 °C. Gespeichert am Raum-Eintrag (`floor_type`, `floor_max_flow`) über die bestehenden Einstellungen; `floor_heating_rooms` enthält weiter alle Räume mit Fußbodenheizung (Raummodelle, Abgleich, Zeitplan und Vorschau unverändert). Mehrere Fußboden-Räume: die niedrigste Grenze, die gerade gilt; verschiedene Angaben in einem Raum: die strengere
- **Neue Datei `floor_limit.py`** (reine Funktionen, schreibt nichts): Fußboden-Räume aus den Einstellungen, Anforderung je Gerät aus Home Assistant, wirksame Grenze mit Grund („Fußboden Bad (RTL-Ventil, fordert an): höchstens 50 °C“)
- **Vorschau (Trockenlauf):** jedes Vorlauf-Soll höchstens die Grenze, die gerade gilt – direkt am Kessel in jeder Stunde, mit RTL-Ventil jetzt nach den Messwerten genau wie „Bereit für die Steuerung?“, danach in den Stunden, in denen der Raum laut Vorschau Wärme braucht (ohne Raummodell bleibt die Anforderung von jetzt, solange das Soll wie jetzt ist; ist ein Thermostat nicht verfügbar, gilt die Grenze in jeder Stunde), auch beim Vorheizen und beim Aufheizen. Die Stunde sagt „… – begrenzt durch …“ (neu je Stunde: `flow_cap`, `flow_limited`), „Womit gerechnet wurde“ nennt die Fußboden-Räume mit ihrer Regel (`floor`). Liegt die Grenze unter 35 °C, gilt sie, sobald ein Raum Wärme braucht (statt „Heizbetrieb aus“)
- **Bereit für die Steuerung?:** Hinweis mit der Grenze jetzt und – für Räume mit dem früheren Häkchen, aber ohne Anschluss – „Anschluss wählen“ (`floor` in der Antwort). Ändert die Stufe nicht
- **Ventile im Trockenlauf:** geprüft – beide Regeln verschieben nur Raum-Sollwerte, nie den Vorlauf; kein Vorschlag kann die Grenze überschreiten
- **Alte Einstellungen:** Räume nur in `floor_heating_rooms` laden ohne Fehler, gelten bis zur Wahl wie direkt am Kessel mit 45 °C und zeigen „Anschluss wählen“; unlesbare Angaben in der Datei zählen als unbekannter Anschluss bzw. Standard-Grenze. Das Gelernte bleibt, solange die Fußbodenheizung bleibt
- **Doku:** Abschnitt „Fußbodenheizung je Raum“ (ersetzt „Fußbodenheizung ohne Stellventil“), Vorschau und „Bereit für die Steuerung?“ ergänzt; Planungspapier: in Weg B harte Grenze vor jedem Schreiben (`floor_limit.flow_cap`), auch für Takt-Boost und Vorheizen

**Einstellungsseite „Entitäten“: alle Entitäten geordnet an einer Stelle** (gewünscht; nur Anzeige und Auswahl – gespeichert wird über die bestehenden Einstellungen, es wird nichts Neues geschrieben, EMS-ESP bleibt nur lesend)
- **Umschalter „Allgemein | Entitäten“** oben in den Einstellungen, am Reiter die Zahl der nötigen, die fehlen, und der nicht verfügbaren. Die Seite **Entitäten** zeigt in aufklappbaren Gruppen mit Zähler („13 von 38 gefunden · 1 nicht verfügbar“) Kessel (EMS-ESP), Regler (EMS-ESP), Warmwasser über HeatPilot, Außen & Wetter, Gas & Bedarf, Strom, Räume, Personen & Urlaub und Benachrichtigungen – je Zeile Name und Zweck (ein Satz), nötig/optional, was gilt (Entität, Zustand mit Einheit, Bereich) und den Status: automatisch gefunden, gewählt, über EMS-ESP (Wert direkt von EMS-ESP), Ersatz (eine andere Rolle genügt), fehlt, nicht verfügbar, nicht gefunden
- **Auswahl mit Suche** je Rolle: passende Entitäten zuerst (Vorschlag der Erkennung, dann nach Bereich und Name; passend = Domäne und Einheit bzw. Geräteklasse der Rolle, beim Warmwasser-Soll nur, was dafür taugt), mit Suchbegriff darunter auch unpassende, markiert; Pfeiltasten und Enter. Was im Feld steht, ist nur die Suche: übernommen wird ein Treffer oder eine Entität aus Home Assistant, genau so eingetragen, eine unbekannte nur als `domain.name` mit Enter; Escape und Verlassen des Felds ohne Wahl lassen den Wert, wie er ist – Speichern liest nur die Wahl. **„Automatik“** zurück zur Erkennung, bei Außenfühlern und Leistungssensoren mehrere mit ×. Filter **„nur fehlende und nicht verfügbare“**; was die Erkennung bei Kessel und Regler nicht fand, steht eingeklappt darunter. Räume zeigen je Raum Thermostat und Sensoren mit Status und springen zur Räume-Tabelle (sie bleibt unter „Allgemein“)
- **Was „nötig“ ist, im Code geprüft:** Vorlauf (Ist); Brenner an oder Brennerleistung (eines genügt); Warmwasser lädt oder Speichertemperatur (eines genügt – Ladungen erkennt HeatPilot auch am Temperaturanstieg); eine Außentemperatur (Außenfühler, sonst der am Kessel, sonst das Wetter); je Raum das Thermostat; die Warmwasser-Soll-Entität nur bei „HeatPilot setzt“. Der Rücklauf ist optional (nur Vorlauf-Auswertung und Live). Liefert EMS-ESP den Wert direkt, fehlt er nicht – außer beim Außenfühler am Kessel: Den Wert direkt von EMS-ESP zeigt die Seite nur an („nur Anzeige“), als Außentemperatur dient nur eine Entität
- **Darin aufgegangen:** die Karte „Zuordnungen (Experten)“, die Karten „Messwerte“, „Personen“ und „Benachrichtigungen“ sowie die Felder Gaszähler, Wetter, Außenfühler, Heizungsanforderung, Urlaubs-Schalter, Warmwasser-Soll-Entität und Leistungssensoren – in den Karten unter „Allgemein“ steht nur noch, was gilt, mit „ändern unter Entitäten“. Keine zweite Speicherstelle: Kessel- und Regler-Rollen weiter als `overrides`, alles andere in den bisherigen Feldern, Speichern wie bisher über `api/settings`. Ist die Seite nicht lesbar, schickt die Oberfläche diese Werte nicht – das Gespeicherte bleibt
- **Alte Einstellungen** laden ohne Fehler: frühere Zuordnungen `gas=…`/`weather=…` wandern beim Laden in Gaszähler bzw. Wetter (sie hatten Vorrang), Zuordnungen ohne bekannte Rolle stehen beim Kessel zum Entfernen; unlesbare Zuordnungen (keine Liste, ohne „=“) werden übersprungen, `floor_heating_rooms` als Text gilt als ein Raum
- **Neue Datei `entity_map.py`** (Rollen, Gruppen, Zweck, Filter, was gilt – nur lesen) und **`GET api/entity_map`**; Personen nur mit Name und Zustand, keine Koordinaten. Hinweise in Diagnose, Live, Kachel „Vorhersage“ und „Bereit für die Steuerung?“ nennen die neue Stelle („Einstellungen → Entitäten“, „→ Außen & Wetter“, „→ Personen & Urlaub“, „Verbindung → Testen“)
- **Doku:** Abschnitt „Entitäten“ (ersetzt „Zuordnungen (Experten)“), Einrichten, Nachtzeiten, Weg W, Benachrichtigungen, Urlaub, README
- **Tests:** `tests/test_floor391.py`, `tests/test_entity_map391.py`, `tests/test_ui_v391.py`, `tests/test_v391_review.py` (Funde der Prüfung); angepasst an die neue Stelle der Felder: `test_ui_v36`, `test_ui_v361`, `test_ui_v363`, `test_ui_v38`, `test_v363_review`

## 0.39.0
**Thermostate als Stellglied, Stufe 1: Ventilverhalten lernen und zeigen, Trockenlauf** (abgesprochen; es wird nichts Neues geschrieben – kein zusätzlicher Aufruf an Thermostate oder Kessel. Raumsteuerung und Warmwasser-Soll schreiben wie bisher, Raum- und Hausmodelle bleiben unverändert. Stufe 2, das Steuern über fein verschobene Sollwerte, kommt mit eigenem Schalter in 0.40)
- **Ventilmodell je Raum** (neue Datei `valve_model.py`, gelernt aus den eigenen Messwerten – Raumtemperatur, Soll, Ventil in %; bei Gruppen das Mittel der Ventile, gezählt wird der Raum): **Kennlinie im Beharren** – Ventil (Median) je Regelabweichung Soll − Raum in 0,1-K-Klassen von −1 bis +2 K, nur ohne Soll-Wechsel in den letzten 30 min, bei Heizbetrieb an, ohne Fenster offen und Fremdwärme; monoton geglättet, mit Vergessen (Halbwertszeit 30 Tage). Daraus Öffnungsbeginn (über 5 %), halb offen, ganz offen (ab 90 %) und Totband. **Sprungantwort** auf Soll-Sprünge ab 0,5 K: Totzeit (bis 5 Prozentpunkte Bewegung) und Zeit bis 63 % der neuen Stellung, Median der letzten Sprünge, getrennt für auf und zu; ist die Messpause länger als die halbe 63-%-Zeit, steht „grob – Messpause … min“ dabei. **PI-Modell mit Sättigung** (Anti-Windup, Totzeit, Stellmotor, Nullpunkt des Integrators), Kp/Ki mit kleinsten Quadraten auf 30-min-Abschnitten ohne Sättigung und ohne Soll-Sprung; das Messrauschen der Raumtemperatur wird aus je drei Messpunkten geschätzt und herausgerechnet (sonst wäre Kp bei 0,1 K Rauschen nur ein Viertel), überdeckt es fast alles, heißt Kp „unsicher“. **P-Regler oder mit Integrator:** Vorhersage entlang der Kennlinie (P-Regler – die Kennlinie gehört zum Gerät) oder mit Integrator (die Kennlinie hängt von der Last ab und wird nur gezeigt, der Satz nennt dann Kp, Nachstellzeit und Ruhepunkt) – beide werden mitgerechnet, es gilt die, die über 30 min besser trifft. Den Integrator schätzt die Vorhersage aus der Stellung (zu: abgebaut, ganz offen: kleinster passender Wert). Neue Dateien `valve_predict.py` (Vorhersage) und `valve_text.py` (der Satz je Raum). **Gütemaß:** mittlerer absoluter Fehler der vorhergesagten Ventilstellung einen Messschritt und 30 min voraus über die letzten 14 Tage – vorhergesagt mit dem Modell von vorher, erst danach gelernt – und zum Vergleich der Fehler von „Ventil bleibt, wie es ist“
- **Nur an/aus:** Thermostate ohne Ventil- bzw. Heizleistungssensor (Ventil aus `hvac_action` nur 0 oder 100 %) – und Sensoren, die nur 0 und 100 % melden – bekommen statt der Kennlinie die Schaltpunkte (ein ab …, aus ab …, Hysterese) und die Zeit bis zum Schalten nach einem Soll-Sprung. Für „meldet nur 0 und 100 %“ zählen nur Messpunkte bei Heizbetrieb mit Ventil im Spiel (ein Sommer mit geschlossenem Ventil macht ein stetiges Thermostat nicht zu „an/aus“), die Zählung vergisst; wechselt die Art, beginnen Sprünge, Schaltpunkte und Gütemaß neu
- **Belastbar** ab 8 Soll-Sprüngen (mindestens 4 je Richtung, Reaktionszeiten ohne große Streuung), 48 h Beharrung und höchstens 20 Prozentpunkten Fehler über 30 min – und höchstens dem 0,7-Fachen von „Ventil bleibt, wie es ist“; beim P-Regler wandert „halb offen“ in 14 Tagen höchstens 0,3 K, mit Integrator muss Kp sicher sein. Sonst „lernt“ mit Grund. Keine Gerätewerte von Herstellern im Modell, nur Daten
- **Gruppen:** der Satz sagt „2 Ventile, Mittel: …“ (an/aus: ein mit dem ersten, aus mit dem letzten); gemischte Gruppen (Ventile mit und ohne Sensor) sind gekennzeichnet und nicht belastbar. Der Heizkreis von EMS-ESP bekommt kein Ventilmodell
- **Ersatzwerte:** Meldet ein vorhandener Ventilsensor nichts (Cloud-Ausfall), bleibt der Messpunkt wie bisher (`hvac_action` als 0/100 %, Raummodelle unverändert), neue Tabelle `valve_subst` merkt ihn – das Ventilmodell lernt diesen Ersatzwert nicht
- **Schrittweite je Gerät** aus Home Assistant (`target_temp_step` der Thermostate, bei Gruppen der gröbste der Schreibziele, mit Raumregler dessen); meldet ein Gerät keine, gilt 0,5 K („angenommen“)
- **Lernen aus der Historie und laufend** (neue Datei `valve_learn.py`, Anbindung in `engine_learn`): Ein neues Modell lernt im Takt aus den gespeicherten Messwerten der letzten 60 Tage nach – in Abschnitten von höchstens einem Tag und höchstens 1 s je Takt, danach je abgeschlossenem Raumschritt (30 min); jeder Messpunkt genau einmal. Fenster offen heißt: die Raumtemperatur fällt in 15 min um mindestens 0,5 K (15 min davor bis 45 min danach nicht gelernt). Die Beharrung zählt jeden Messpunkt mit seiner Messpause (bis 20 min). Gespeichert als `model.valve.<Raum>` mit eigenem Modellstand – beim Update von 0.38 lernen die Ventilmodelle aus den vorhandenen Messwerten, alles andere bleibt, wie es ist, und es wird nichts neu aus Home Assistant geholt; „Lernstand neu berechnen“ verwirft auch die Ventilmodelle
- **Trockenlauf der Ventile** (neue Datei `valve_dry.py`, nur Protokoll): **früh drosseln** – nähert sich ein Raum seinem Soll wirklich (gemessen deutlich steigend, über dem Messrauschen, und seit einem Soll-Anstieg noch nicht erreicht oder noch mindestens 0,2 K bzw. einen halben Schritt darunter), erreicht er es laut Raum- und Ventilmodell (gemeinsam Minute für Minute, Ventil mit Totzeit und Stellmotor) in weniger als 20 min, wäre das Ventil dann noch über 30 % offen und sagen die Modelle mindestens 0,2 K Überschwingen voraus, würde HeatPilot das Soll jetzt um einen Schritt senken und beim Erreichen zurücksetzen (spätestens nach 60 min); danach erst wieder nach einem neuen Soll oder 0,5 K Auskühlen – ein Thermostat mit Integrator, das das Soll hält, löst so nicht dauernd aus, ein P-Regler, der vor dem Soll schließt, gar nicht. **Gemeinsam anfordern** – fordert nur ein Raum Wärme an und taktet der Kessel (mindestens 3 Brennerstarts in der letzten Stunde, Läufe im Mittel unter 10 min), würde HeatPilot Räume, die in den nächsten 60 min ohnehin heizen würden (Ventilvorhersage entlang des Abkühlens; ein geschlossenes Ventil über dem Soll öffnet dabei nicht), um einen Schritt anheben (zurück, sobald der anfordernde Raum fertig ist, spätestens nach 60 min). Ohne plausibles Raummodell zählt der gemessene Verlauf, ohne Ventilvorhersage die Stellung von jetzt bzw. die Öffnungsschwelle, mindestens das Soll. Regeln wie abgesprochen und mitgezählt: nur bei Änderung, volle Auflösung des Thermostats, 5 min Haltezeit je Raum; ändern Zeitplan oder Hand das Soll, endet der Eingriff ohne Zurückschreiben. Der Heizkreis von EMS-ESP ist nie dabei
- **Protokoll und Auswertung:** neue Tabelle `valve_dry_log` (90 Tage, „Lernstand neu berechnen“ lässt sie stehen); eine Stunde danach wird nachgetragen, was wirklich geschah (Überschwingen über das Soll, Brennerstarts – aus den Brennerereignissen oder dem Startzähler). Auswertung über 7 Tage: wie oft je Regel, Schreibvorgänge, je Raum und Tag, Beispiele, laufende Eingriffe; die geschätzte Wirkung (weniger Überschwingen in K aus Raum- und Ventilmodell, grob ein Brennerstart je mit angefordertem Raum) nur mit plausiblem Raummodell und belastbarem Ventilmodell, sonst nur gezählt. Die Schätzung des Überschwingens erst, wenn für den Raum das vorhergesagte Überschwingen ohne Eingriff dreimal nachgemessen im Mittel höchstens 0,2 K daneben lag – bis dahin zeigt das Beispiel die Vorhersage, die nachgeprüft wird. Ein Ventil, das nur an/aus kennt, gewinnt durch frühes Drosseln kaum etwas – die Schätzung sagt das so
- **Abfragen:** `api/heat` und `api/summary` je Raum `valve_model` (Kennlinie, Punkte, Sprungantwort, PI, Schaltpunkte, Güte, Schrittweite, Zustand und ein Satz wie „öffnet ab 0,2 K unter Soll, halb offen bei 0,6 K, ganz bei 1,2 K · reagiert auf +0,5 K in ≈ 8 min, schließt in ≈ 12 min · Schritt 0,5 K · belastbar“), `api/preview` den Trockenlauf der Ventile (`valves`). Export („ventile“: Modelle und Protokoll) und Diagnosepaket
- **Oberfläche:** Heizen → Raum antippen – neuer Abschnitt „Ventilverhalten“: die Kennlinie als kleiner Graf (Ventil in % über K unter Soll, eine Achse, „Soll“ als Bezugslinie, „halb offen“ markiert, gestrichelt solange das Modell lernt; nur an/aus: die Schaltpunkte als Schleife), Hinweis je 0,1 K mit Maus oder Finger (auch über dem Dialog), der Satz und „Einzelheiten“ mit Gruppe, Sprungantwort, Beharrung, Vorhersagefehler neben „Ventil bleibt“, Reglermodell (P-Regler oder mit Integrator, Nachstellzeit, Messrauschen) bzw. Schaltpunkten, Schrittweite und der Kennlinie als Tabelle; ohne Daten nur der Satz. Mit Integrator ist der Graf als lastabhängig gekennzeichnet. Übersicht → Räume: Tabelle „Ventilverhalten“ mit einer Zeile je Raum, der Name öffnet den Dialog. Übersicht → Vorschau: Abschnitt „Ventile (Trockenlauf)“ mit Hinweis, Satz, Kennzahlen (je Regel geschätzt und danach gemessen, Schreibvorgänge), Eingriffen je Tag als Balken mit Legende, laufenden Eingriffen und aufklappbar Beispielen, je Raum und Tag und den Regeln. Ältere Antworten ohne die neuen Felder: die Teile bleiben weg. Die Oberfläche schreibt dafür nichts
- Doku: Abschnitt „Ventilverhalten“, Planungspapier (Stufe 1 gebaut), README
- **Warmwasser über HeatPilot: keine Zählsperre mehr** (Wunsch): Ändert sich die Empfehlung um 1 K, wird nach der Haltezeit von 5 min geschrieben – egal wie oft am Tag. Die Pause nach 24 Schreibvorgängen in 24 h aus 0.38 ist entfernt; sonst bliebe der Speicher z. B. auf 60 °C stehen, nur weil viel los war. Pausiert wird weiter nach 3 Fehlschlägen in Folge oder 2 fremden Änderungen in 1 h
- **Vorschau: Vorlauf mindestens 35 °C** (Wunsch): Braucht kein Raum mindestens 35 °C, steht dort „Heizbetrieb aus“ statt eines Vorlaufs von 25–34 °C – darunter lohnt Heizen nicht. Räume ohne eigenes Vorlauf-Soll (ohne Modell, Fußboden) unter dem Soll halten den Heizbetrieb mit 35 °C an

## 0.38.0
**Weg W: HeatPilot setzt das Warmwasser-Soll, Benachrichtigungen und Problem-Sensor** (abgesprochen; Standard aus – ohne den neuen Schalter ändert sich nichts. Der einzige neue Schreibweg geht über Home Assistant auf die eine Entität des Speicher-Solls; EMS-ESP bleibt nur lesend, die Schreibregeln für die Thermostate und die Sperre gegen den Heizkreis von EMS-ESP bleiben, wie sie sind)
- **Einstellungen → Warmwasser, Schalter „Warmwasser-Soll“** (`dhw_control`): **Aus** (Standard, nur Empfehlung und Vorschau), **Trockenlauf** (rechnet im Takt und protokolliert „würde schreiben X °C (Grund)“, schreibt nichts) oder **HeatPilot setzt**. Unabhängig vom Schalter der Raumsteuerung. Dazu die **Entität** des Speicher-Solls (`dhw_set_entity`, nur `number.*` in Kleinbuchstaben; leer = Vorschlag aus der Erkennung, Rolle „Warmwasser-Soll“ – ältere EMS-ESP melden sie als `wwseltemp`, das wird jetzt auch erkannt). Heizkreis-Entitäten (`…_hc1_…`), andere Rollen und andere bekannte Kesselwerte (Vorlauf-Soll, Heiztemperatur …) lassen sich nicht speichern
- **Schreibweg:** `number.set_value` über Home Assistant, nur auf diese eine Entität, nur ganze Grad zwischen Minimum und Maximum der Warmwasser-Einstellungen, nur innerhalb der Grenzen, die Home Assistant für die Entität meldet, und nur, wenn die Entität taugt (neue Datei `dhw_entity.py`): verfügbar, Einheit °C (ohne Einheit nicht), kein Heizkreis, keine andere erkannte Rolle, kein anderer bekannter Kesselwert. Alles andere – andere `number`-Entitäten, andere Dienste, andere Werte – weist `Control` hart ab; bei „Aus“ und „Trockenlauf“ gibt es den Schreibweg gar nicht. Kein REST an EMS-ESP
- **Regeln** (reine Funktionen `wish`/`decide` in `dhw_rule.py`, Ablauf in `dhw_control.py`, Anzeige in `dhw_status.py`): Wunsch = Warmwasser-Empfehlung auf 1 K gerundet (halbe Grad aufwärts), Minimum…Maximum; geschrieben nur bei Änderung ab 1 K zum Kessel-Soll – beim gerundeten Wunsch und bei der ungerundeten Empfehlung (52,4 ↔ 52,6 °C schreibt nicht abwechselnd 52 und 53); 5 min Haltezeit nach jedem Schreiben (kein Raster); während einer Sonderladung bzw. Desinfektion nicht; **höchstens 24 Schreibvorgänge in 24 h**, beim 25. pausiert Weg W mit Meldung (schwankende Empfehlung)
- **Kessel-Soll und Prüfung nach 60 s:** Kessel-Soll ist der Zustand der beschriebenen Entität; EMS-ESP direkt nur mit frischen Werten (höchstens 90 s alt – antwortet EMS-ESP nicht mehr, zählt sein letzter Wert nicht). Widersprechen sich beide, ist es „unklar“: nicht schreiben, nichts als fremd zählen. Geprüft wird im nächsten 30-s-Schritt bzw. Takt (±0,5 K), ohne den Messtakt aufzuhalten; ist das Kessel-Soll gerade unbekannt oder unklar (EMS-ESP startet neu), bis 5 min nach dem Schreiben weiter. Nicht übernommen: Protokoll, Hinweis, Meldung, nächster Versuch frühestens nach der Haltezeit; nach **3 Fehlschlägen in Folge pausiert** Weg W bis „wieder einschalten“. Der erste Erfolg danach meldet „wieder in Ordnung“ – auch, wenn der Kessel den Wert erst später zeigt, sogar während der Pause; zeigt er inzwischen schon den Wunsch, gehen Hinweis, Meldung und Problem-Sensor aus (die Fehlschläge zählen beim nächsten Versuch weiter). Ein Fehler beim Schreiben selbst zählt genauso
- **Nicht bereit heißt: nichts schreiben.** Fehlt die Entität, ist sie nicht verfügbar, taugt sie nicht oder lässt EMS-ESP das Warmwasser-Soll nicht schreiben (`wwseltemp`), merkt HeatPilot nichts vor – dieselben Hindernisse wie in „Bereit für die Steuerung?“, sichtbar als Entscheidung, ohne als Fehlschlag zu zählen; vor dem Schreiben wird noch einmal geprüft
- **Fremder Schreiber:** Ändert sich das Kessel-Soll ohne HeatPilot (z. B. die alte Automation), Hinweis „anderer Schreiber aktiv – HA-Automation abschalten“ und Meldung; nach **2 fremden Änderungen in 1 h pausiert** Weg W. Ein verspätet übernommener eigener Wert zählt nicht als fremd
- **„Wieder einschalten“** wirkt nur bei einer Pause – ohne Pause ändert sich nichts (`POST api/dhw/resume` antwortet dann 409), ein wiederholter Aufruf kann die Pause nach 3 Fehlschlägen also nicht aushebeln
- **Zurück auf Aus/Trockenlauf:** nichts wird zurückgeschrieben, der Kessel behält den Wert; Hinweis, die Automation ggf. wieder einzuschalten. Fällt HeatPilot aus, bleibt das letzte Soll stehen – deshalb nie unter dem Minimum
- **Protokoll:** Warmwasser-Zeilen im Schreibprotokoll (`control_log`) – geschrieben (`set_value`), würde schreiben (`dry_run`), nicht übernommen (`check_failed`), fremde Änderung (`foreign`), pausiert (`paused`), wieder eingeschaltet (`resumed`); im Heizen-Reiter als „Warmwasser-Soll“
- **Benachrichtigungen** (neue Datei `notify.py`, Einstellung `notify_targets`): notify-Entitäten (ab Home Assistant 2024.6) über `notify.send_message`, Dienste wie `notify.mobile_app_…` direkt – auch bei älteren Versionen ohne notify-Entitäten. Nur an die eingestellten Ziele (in `Control` hart begrenzt; Ziele wie in Home Assistant in Kleinbuchstaben – anders geschriebene lassen sich nicht speichern). Je Ereignis eine Meldung und einmal „wieder in Ordnung“, höchstens eine Meldung je Art in 6 h; Titel „HeatPilot“, Text kurz und konkret. Auswahl aus Entitäten und Diensten (`get_services` neu im Home-Assistant-Client)
- **Problem-Sensor `binary_sensor.heatpilot_problem`:** an, solange ein Problem offen ist (Warmwasser-Soll nicht übernommen, Weg W pausiert, fremder Schreiber, Thermostat-Schreibfehler), Gründe im Attribut `reasons`. Das Add-on darf dafür neben `sensor.heatpilot_*` jetzt auch `binary_sensor.heatpilot_*` anlegen – sonst nichts
- **Raumsteuerung:** Thermostat-Schreibfehler ab drei in Folge gehen über dieselbe Stelle (Meldung, Problem-Sensor), der erste Erfolg danach meldet „wieder in Ordnung“. An den Schreibregeln ändert sich nichts
- **Bereit für die Steuerung?:** neuer Block „Warmwasser über HeatPilot“ – Entität (number mit °C, Grenzen, tauglich; ein Name ohne Warmwasser-Hinweis ist eine Einschränkung „prüfen“), schreibbar laut EMS-ESP (`wwseltemp`), Schalterstand, letzte Prüfung, Pause (seit wann), Kessel-Soll unklar, fremder Schreiber zuletzt gesehen; ein Minimum unter 50 °C ist ohne Desinfektionstermin eine Einschränkung (Legionellen – so tiefe Werte stehen dann wirklich am Kessel), mit Termin ein Hinweis; Hinweis „HA-Automation für das Speicher-Soll abschalten“, solange in den letzten 24 h fremde Änderungen kamen. Ändert die Stufe der Raumsteuerung nicht. Im Kessel-Block heißt es jetzt „am Kessel schreibt HeatPilot nur das Warmwasser-Soll“
- **Vorschau:** Warmwasser-Soll auf ganze Grad wie Weg W; Startwert und Schreibzählung nach derselben Regel (auch die Schwelle für die ungerundete Empfehlung; bei Trockenlauf bzw. „HeatPilot setzt“ ab dem Wert von Weg W mit laufender Haltezeit), bei „HeatPilot setzt“ heißen die Warmwasser-Schreibvorgänge „schreibt“ (`dhw_writes`). Vorlauf und Heizbetrieb bleiben Trockenlauf
- **Oberfläche:** Einstellungen → Warmwasser oben der Schalter „Warmwasser-Soll“ (Aus / Trockenlauf / HeatPilot setzt) mit Hinweis je Stellung, das Feld für die Entität (leer = erkannt; die Auswahl bietet nur passende `number`-Entitäten mit °C an, den Vorschlag und Namen mit Warmwasser-Hinweis zuerst; darunter Wert und Grenzen in Home Assistant), der Warnhinweis (HA-Automation abschalten, Ausfall, Zurückschalten) und eine Statusbox (Kessel-Soll, Empfehlung, letztes Schreiben und Prüfen, Hinweise, Pause mit „wieder einschalten“); beim Speichern mit „HeatPilot setzt“ fragt die Oberfläche einmal nach, ob die Automation abgeschaltet ist. Neue Karte **„Benachrichtigungen“**: Ziele als Liste mit Häkchen (notify-Entitäten und -Dienste; gewählte, die fehlen, zum Abwählen), „Testnachricht senden“ nur mit gespeicherten Häkchen, letzte Sendung und offene Probleme. Heizen und Übersicht: Statuszeile Warmwasser unter dem Status (bei „Aus“ nur, solange ein Hinweis gilt), bei Pause mit „wieder einschalten“; das Protokoll im Heizen-Reiter zeigt die Warmwasser-Zeilen. Uhrzeiten von Weg W stehen überall in der Zeitzone des Browsers (das Add-on schickt Zeitpunkte, die Statuszeile als Vorlage, einen fremden Schreiber als Abstand). Vorschau: bei „HeatPilot setzt“ heißen die Warmwasser-Schreibvorgänge „schreibt“ – gefüllter Ring, eigener Teil im Satz, im Tooltip und in der Tabelle; Vorlauf und Heizbetrieb bleiben „würde schreiben“. „Bereit für die Steuerung?“ zeigt den Block „Warmwasser über HeatPilot“
- **Abfragen:** `GET api/dhw` (Status, Bereitschaft), `POST api/dhw/resume` („wieder einschalten“, nur bei Pause), `GET api/notify` (Ziele zur Auswahl), `POST api/notify/test` (Testnachricht nur an die gespeicherten Ziele); `api/heat`, `api/summary` und `api/settings` enthalten den Status von Weg W, `api/entities` je `number`-Entität, ob sie als Warmwasser-Soll taugt. Diagnose: Selbsttest-Zeile und Status im Diagnosepaket
- **Sperrtext** für den Heizkreis von EMS-ESP jetzt „Heizkreis von EMS-ESP – HeatPilot schreibt nicht an den Heizkreis“ (am Kessel schreibt HeatPilot seit 0.38 das Warmwasser-Soll)
- Doku: Abschnitte „Warmwasser über HeatPilot (Weg W)“ und „Benachrichtigungen“, „Was das Add-on macht“ nennt den neuen Schreibweg, den Problem-Sensor und die Meldungen; Planungspapier (Weg W gebaut), README

## 0.37.0
**Vorschau (Trockenlauf): welchen Vorlauf und welches Warmwasser-Soll HeatPilot je Stunde setzen würde, Vorhersage in der Steuerung** (abgesprochen; nur Anzeige – EMS-ESP bleibt nur lesend, es gibt keinen Schreibpfad an den Kessel, auch keinen vorbereiteten. Die Schreibregeln für Tado bleiben, wie sie sind)
- **Vorschau der nächsten 24 h** in 5-min-Schritten bis zum Ende der 24. vollen Stunde, je Stunde zusammengefasst (es gilt der Wert, der den größten Teil der Stunde gilt; jede Änderung zusätzlich minutengenau): Außentemperatur aus der Vorhersage (höchstens 90 min daneben, sonst die aktuelle, Herkunft je Stunde), an den Außenfühler angeglichen – jetzt gilt der Fühler, gegen den die Raummodelle gelernt sind, der Unterschied zur Vorhersage klingt über 6 h ab und steht dabei –, Raum-Soll je Raum, **Vorlauf-Soll**, **Heizbetrieb an/aus**, **Warmwasser-Soll** und die **Schreibvorgänge, die es gäbe**
- **Raum-Soll:** Räume mit „macht mit“ bei „HeatPilot steuert“ wie die Steuerung (Wunschzustand mit Vorhersage); alle anderen, und alle bei „Beobachten“, was Tado voraussichtlich will – tagsüber das heutige Soll (nachts bzw. bei Abwesenheit abgefragt: das tagsüber gemessene der letzten 48 h, ohne Messung der Komfortwert; „aus“ nur, wenn der Raum auch tagsüber aus war), nachts die Nacht-, bei Abwesenheit die Abwesenheitstemperatur (gelernt bzw. eingestellt). Anwesenheit aus Home Assistant, Gewohnheit und GPS: wer jetzt da ist, sagt der Zustand in Home Assistant (Tracker ohne Wert: unbekannt, wie in der Steuerung); wer da ist, bleibt bis zur nächsten gewohnten Abfahrt (wer gegen die Gewohnheit zu Hause ist, etwa im Homeoffice, bis zur nächsten regulären); wer weg ist, bis zur erwarteten Ankunft; wo die Gewohnheit noch lernt, ist die Ankunft offen (Komfort wie in der Steuerung); Urlaub bis zur Rückkehr. Die Raumtemperatur läuft nach dem Raummodell mit (auskühlen, aufheizen mit dem Vorlauf, am Soll ±0,3 K halten) – daraus Vorheizdauer und Wärmebedarf; Räume, die nicht für den Vorlauf zählen, hält ihr Thermostat am Soll, solange der Heizbetrieb nicht sicher aus ist (ohne Raummodell bleibt der Messwert)
- **Vorlauf-Soll** = höchster nötiger Vorlauf der Räume mit plausiblem Raummodell (ohne Fußbodenheizung), 25 °C bis zum maximalen Vorlauf, auf 1 K gerundet; beim Vorheizen der niedrigste Vorlauf (1 K genau), mit dem der Raum ab dem Beginn laut Steuerung rechtzeitig warm wird – die Steuerung rechnet den Beginn mit der Aufheizzeit bei maximalem Vorlauf, meist ist es deshalb der maximale –, sonst beim Aufheizen +5 K. Braucht kein Raum Wärme, „Heizbetrieb aus“ statt eines Vorlaufs – auch Räume, die den Vorlauf nicht bestimmen, zählen dabei: mit Fußbodenheizung wie die übrigen, ohne plausibles Raummodell nach dem Messwert (mehr als 0,3 K unter dem Soll oder ohne Temperatur: Heizbetrieb bleibt an, mindestens 25 °C, mit Grund). Ohne plausibles Raummodell bzw. ohne Außentemperatur kein Vorlauf-Soll, mit Grund
- **Warmwasser-Soll** je Zeitpunkt aus der Warmwasser-Empfehlung mit derselben Anwesenheit und dem Desinfektionstermin (Stunde markiert, mit Temperatur und ob der Termin zählt); auf 1 K gerundet
- **Schreibvorgänge nach den abgesprochenen Regeln:** nur bei Änderung (Vorlauf und Warmwasser ab 1 K, Heizbetrieb an/aus), danach 5 min Haltezeit – was sich darin ändert, geht danach hinaus, wenn es dann noch anders ist. Der erste Vergleich ist mit dem, was heute am Kessel steht. Je Stunde markiert, Summe über 24 h
- **Trockenlauf-Protokoll:** Zu Beginn jeder Stunde wird der damals gerechnete Plan dieser Stunde gespeichert (neue Tabelle `preview_log`, ältere Datenbanken bekommen sie beim Start), nach der Stunde der Ist-Wert dazu: mittleres Vorlauf-Soll des Reglers für den Heizkreis bei Heizbetrieb (Heizkreis-Soll, ohne es das Kessel-Soll `sel_flow`; Speicherladungen und Zeiten ohne Anforderung – Kessel-Soll unter 20 °C – zählen nicht), mittleres Speicher-Soll (`dhw_set`), Anteil Heizbetrieb an (Betriebsart und „Heizen abschalten“) und wie oft sich diese Werte geändert haben (der Vorlauf wie im Plan nur bei Heizbetrieb mit Anforderung). Die Schreibzählung setzt dort fort, wo der Plan der Vorstunde endete. Aufbewahrt 90 Tage; „Lernstand neu berechnen“ lässt es stehen (Messung, nichts Gelerntes); im Export („trockenlauf“) und im Diagnosepaket
- **Auswertung je Tag und über 7 Tage:** mittlerer Unterschied Plan − Regler beim Vorlauf (nur wenn beide heizen) und Plan − Automation beim Warmwasser, Stunden tiefer bzw. höher, Stunden „HeatPilot aus, Regler heizt“, Schreibvorgänge des Plans gegen Wechsel des Ist-Werts, als Satz („HeatPilot läge im Mittel … tiefer beim Vorlauf, … beim Warmwasser; … statt … Schreibvorgänge“); beim Warmwasser grob die Differenz im Bereitschaftsverlust (nur mit plausiblem Modell), beim Vorlauf keine kWh. Ohne Daten „sammelt – erste Werte nach einer Stunde“
- **Neue Abfrage `GET api/preview`** mit Vorschau und Vergleich – gerechnet beim Abruf (höchstens alle 5 min) und stündlich fürs Protokoll, nicht im Messtakt
- **Oberfläche, Übersicht → „Vorschau (Trockenlauf)“** (gleich unter den Kacheln): Hinweis „Trockenlauf – HeatPilot schreibt nichts an den Kessel“ und ein Satz zu den Schreibvorgängen in 24 h und zum heutigen Stand am Kessel; zwei kleine Grafen der 24 h mit derselben Zeitachse (Vorlauf-Soll als Stufen mit Außentemperatur, darunter Warmwasser-Soll mit Desinfektionstermin – ohne zweite y-Achse), Nacht- und Abwesenheitsbänder, Streifen „Heizbetrieb aus“, Ring „würde schreiben“, Tooltip je Stunde in beiden Grafen; Tabelle je Stunde zum Aufklappen mit Gründen, Raum-Soll (nur Änderungen) und Schreibvorgängen. Karte „Trockenlauf gegen Regler/Automation (7 Tage)“: Satz und Kennzahlen, Plan und Ist als Linien je Größe (Ist grau), „Wochenraster“ mit Plan minus Ist je Wochentag und Stunde (auf schmalen Bildschirmen nur die Linien), Tabelle je Tag. Phase „aus“ (Thermostat aus) hat einen Namen
- **Vorhersage in der Steuerung (Stufe 2):** Die Vorheizdauer vor dem Nachtende, vor einer Ankunft und vor der Rückkehr aus dem Urlaub rechnet mit der Außentemperatur zum Zielzeitpunkt laut Vorhersage (höchstens 90 min daneben), sonst wie bisher mit der aktuellen; wird es bis dahin kälter, beginnt das Vorheizen früher. Der Vorheizdeckel bleibt. `controlloop.desired` bleibt eine reine Funktion (neuer Parameter `t_out_at`)
- Neu und je mit eigener Beschreibung: `preview.py` (Vorschau und Schreibzählung), `preview_log.py` (Protokoll und Auswertung), `presence_plan.py` (Anwesenheit der nächsten Stunden aus Gewohnheit, GPS und Urlaub)
- Doku: Abschnitt „Vorschau (Trockenlauf)“, „Wunschzustand“ und „Vorheizplan“ ergänzt, README auf Stand 0.37

## 0.36.3
**Desinfektion in den Einstellungen mit Zeitvorschlag, Sperre gegen den Heizkreis von EMS-ESP** (abgesprochen; Standard aus – ohne Termin ändert sich nichts. EMS-ESP bleibt nur lesend, HeatPilot löst die Desinfektion nicht aus)
- **Einstellungen → Warmwasser, „Desinfektion (Legionellen)“:** Tag (Standard „aus“ – kein Termin), Uhrzeit des Beginns (Standard 13:00) und Temperatur. Die liest HeatPilot vom Kessel (EMS-ESP `wwdisinfectiontemp` bzw. `dhw/disinfectiontemp`, neue Rolle „Desinfektion: Temperatur (Kessel)“, nur im Bereich der Einstellung 60–80 °C geglaubt – darunter „Kessel meldet 55 °C – zu niedrig für eine Desinfektion“), sonst gilt die Einstellung (60–80 °C, Standard 70). Der nächste Termin gilt nach Wanduhr in Ortszeit, auch über die Zeitumstellung. Hinweis: HeatPilot löst sie noch nicht aus – solange der Regler (z. B. ES73) sie macht, dessen Termin eintragen
- **Vorschlag** ab 4 Wochen Zapfprofil: die Wochenstunde mit der größten regelmäßigen Zapfung – zuerst große Zapfungen, die an mindestens der Hälfte der letzten (bis 8) Wochen am selben Wochentag ±1 h kamen (die größte zuerst, bei gleicher Größe die häufigere), sonst die Stunde, in der mindestens jede zweite Woche gezapft wird, mit dem größten Abfall × Anteil. Beginn = Zapfstunde − Ladedauer − 15 min, auf 15 min abgerundet; Ladedauer aus der gelernten Ladegeschwindigkeit (ab 5 Ladungen), sonst Ladekosten × Anstieg ÷ Kesselleistung. Nur 06:00–21:00 – läge er nachts, gilt die nächstgrößte regelmäßige Zapfung, sonst kein Vorschlag. „So 18 Uhr zapfst du am meisten (7 von 8 Sonntagen, ≈ 14 K) – Desinfektion So 17:30, dann nutzt du die 70 °C“, dazu grob die Wärme, die bis zur Zapfung im Speicher bleibt und ins Wasser geht. Ohne genug Daten kein Vorschlag, mit Grund
- **Im Modell eingeplant, sobald bestätigt:** Hat der Regler in den letzten 8 Tagen zur Zeit des Termins desinfiziert (Kesselschalter `wwdisinfecting` oder Speicher bis 5 K an die Desinfektionstemperatur; endet sie bis 3 h nach dem Termin, ist es dieselbe), behandeln Empfehlung und Schatten der Strategie die Stunden danach wie nach einer erkannten Sonderladung, ohne auf die Erkennung dieser Woche zu warten – ab Beginn + Ladedauer (wie beim Vorschlag) ist eine große Zapfung bis 12 h danach gedeckt (vorher nicht höher laden), der Schatten steht dann auf der Desinfektionstemperatur und kühlt von dort aus. Ein Termin zur Stunde der Wanne selbst deckt sie nicht. Desinfiziert zu dieser Zeit niemand (kein Regler, oder er desinfiziert zu anderer Zeit – dann zählt seine Zeit), zählt der Termin nicht: „Termin noch nicht bestätigt – kein Regler desinfiziert zu dieser Zeit“ – sonst stiege die Ersparnis um Wärme, die nicht kommt. Eine Einmalladung ist keine Desinfektion und zählt weder dafür noch dagegen
- **„Vorschlag übernehmen“** trägt Tag und Uhrzeit ins Formular ein, gespeichert wird mit „Speichern“; ohne Tag ist die Uhrzeit gesperrt (sie bleibt gespeichert, auch wenn das Feld leer ist). Zählt der Termin (noch) nicht, steht der Grund unter dem Termin
- **Übersicht → Warmwasser:** Kachel „Nächste Desinfektion“ (So 17:30, eingestellt, Temperatur), ohne Termin „Letzte Sonderladung“ (erkannt), ohne beides grau „kein Termin“. Die Kachel „Legionellenschaltung“ des Reglers bleibt
- **Bereit für die Steuerung?, Kessel über EMS-ESP:** Mit Termin ist die Desinfektion (`wwdisinfecting`) die sechste nötige Steuerung („Desinfektion braucht WWK Desinfizieren (schreibbar)“); ohne Termin bleibt sie Info
- **Heizkreis von EMS-ESP als Raum wird hart abgewiesen** (Fund aus der Prüfung 0.36.2): erkannt am Gerät in Home Assistant – „EMS-ESP“ in Hersteller, Name oder Modell, Hersteller „proddy“ oder angebunden über das EMS-ESP-Gateway; nur ohne Gerät am Namen und nur bei MQTT. Er wird nicht vorgeschlagen, macht nie mit (ein gespeichertes „macht mit“ gilt nicht, Warnung im Protokoll; liegt er mit Ventilen in einem Raum, macht der ganze Raum nicht mit), jeder Schreibvorgang darauf – Knopfdruck oder Regelschleife – endet in `Control` mit einem Fehler, und „Bereit für die Steuerung?“ zeigt ihn als „nicht dabei“. In der Oberfläche ist „macht mit“ für ihn (und die Geräte im selben Raum – deren gespeichertes „macht mit“ bleibt dabei erhalten) gesperrt, mit Hinweis „Heizkreis von EMS-ESP – HeatPilot schreibt nicht an den Kessel“; im Reiter Heizen steht er nur zur Anzeige (−/+ und Betriebsart gesperrt, Boost, Zeitplan und Ausschalten lassen ihn aus). Ein Boost auf den Heizkreis, den 0.36.2 noch gemerkt hat, wird beim Start verworfen; der Reiter Heizen sagt, auf welchen Wert das Regler-Soll von Hand zurück muss, bis es dort steht. Tado-Zonen und HomeKit-Ventile, die auch „…_thermostat“ heißen, bleiben beschreibbar. Die Tado-Schreibregeln bleiben, wie sie sind (erlaubte Dienste, 5–25 °C, nur „macht mit“, nur mit „HeatPilot steuert“) – die Sperre macht sie nur strenger
- Neu und je mit eigener Beschreibung: `dhw_disinfect.py` (Termin einplanen und bestätigen, Vorschlag, Deckung), `ems_lock.py` (Heizkreis von EMS-ESP erkennen) und `discovery_roles.py` (die EMS-ESP-Rollen und ihre Beschriftungen, aus `discovery.py` herausgelöst – reiner Umbau)
- Doku: Abschnitt „Desinfektion (Legionellen)“ unter Warmwasser, „Räume und Geräte“ und „Bereit für die Steuerung?“ ergänzt, README, Planungspapier (Desinfektion als Teil von Weg B: auslösen, prüfen, bei Fehlschlag Hinweis)

## 0.36.2
**Lesbare Struktur** (abgesprochen; reiner Umbau – an Messwerten, Modellen, Empfehlungen, Steuerung und Schreibregeln ändert sich nichts)
- Die drei großen Dateien sind nach Thema aufgeteilt, jede beginnt mit ein paar Sätzen, wofür sie da ist. `engine.py` ist nur noch der Kern (Aufbau, Zustände, Takt, Speichern); die Arbeit steckt in `engine_sample.py` (Messpunkt bilden), `engine_boiler.py` (Kessel live, Tageszähler), `engine_dhw.py` (Warmwasser in der Engine), `engine_learn.py` (Räume, Fremdwärme, Hausmodell, Zeiten aus Tado und Strom) und `engine_summary.py` (Zusammenfassung, Reiter, Vorhersage); die Wertehelfer (`num`, `flag`, `local` …) stehen in `values.py`. `dhw.py` behält das Warmwassermodell, `dhw_watch.py` erkennt Zapfungen, Nachlauf und Fühlerstufe und führt den Schatten der Strategie. `advice.py` behält die Tado-Empfehlungen und baut die Karten, `balance.py` rechnet den hydraulischen Abgleich und formuliert die Sätze seiner Karten
- Alle Namen, die Module und Tests aus `heatpilot.engine`, `heatpilot.dhw` und `heatpilot.advice` holen, gelten dort weiter; die übrigen Konstanten stehen jetzt beim Thema (`engine_*.py`, `dhw_watch.py`, `balance.py`) und lassen sich nur noch dort importieren. Das Protokoll nennt als Quelle wie bisher `heatpilot.engine`. Geprüft: Jede verschobene Funktion, Methode und Konstante ist im Syntaxbaum gleich – einzige Ausnahme: drei Methoden holen die nächste Ankunft über `heatpilot.engine`, damit Tests sie dort weiter ersetzen können
- Die Uhr bindet jedes Teilmodul selbst: Ein Ersatz von `heatpilot.engine.time` wirkt nur noch im Kern; wer die Uhr für die ganze Engine anhalten will, ersetzt `time.time`. Ein Test hält die Uhr deshalb jetzt so an (eine Codezeile, Prüfungen unverändert); alle übrigen Tests unverändert, alle grün
- README auf Stand 0.36. Genauer als bisher (README, Doku, `control.py`): An EMS-ESP gehen nur Lese-Anfragen, geschrieben wird nur auf die Thermostate, die du als Raum wählst – den Heizkreis von EMS-ESP deshalb nicht als Raum wählen
- Vorgemerkt für einen eigenen Schritt (ändert Funktionsrümpfe oder Verhalten, deshalb nicht in diesem Umbau): `summary()` und `advice.build` in benannte Teilschritte zerlegen, die Satzbausteine des Abgleichs ohne Unterstrich als Teil von `balance` benennen, die unbenutzte Konstante `STALE_S` entfernen, den Heizkreis von EMS-ESP als Raum im Code ablehnen

## 0.36.1
**Vorhersage sichtbar, „Bereit für die Steuerung?“, Kessel über EMS-ESP** (abgesprochen; nur Anzeige – an Modellen, Steuerung und Schreibregeln ändert sich nichts)
- **Vorheizplan zur Ankunft:** sagt, womit er rechnet – „Gerechnet mit −3 °C außen (Vorhersage für 17:00)“ bzw. „(aktuelle Außentemperatur – keine Vorhersage für den Zeitpunkt)“. Ein Vorhersagepunkt zählt nur noch, wenn er höchstens 90 min neben der Ankunft liegt; vorher bekam eine Ankunft in drei Tagen den letzten Punkt einer 24-h-Vorhersage. Vorhersagezeiten ohne Zeitzone gelten als Ortszeit, statt den Vergleich abzubrechen. Nach Leeren oder Wechsel der Wetter-Entität zählt die alte Vorhersage nicht mehr, eine neue wird sofort abgefragt
- **Übersicht, Kachel „Vorhersage“** neben „Haus-Trägheit“: Minimum der nächsten 12 h mit Uhrzeit, Trend (Temperatur in 12 h gegen jetzt, auf 0,5 K) und Stand des Abrufs; ohne Wetter-Entität bzw. ohne Daten grau. Die Steuerung nutzt die Vorhersage erst ab 0.37
- **Einstellungen → Steuerung, „Bereit für die Steuerung?“** über dem Schalter: Ampel und ein Satz, ob HeatPilot steuern könnte – bei „Beobachten“ als „wäre bereit“, bei „HeatPilot steuert“ als „läuft“ mit den Einschränkungen. Aufklappbar mit Hindernissen, Einschränkungen, Hinweisen und dem Zustand je Raum („bereit“, „akzeptiert“, „lernt“, „nicht erreichbar“, „nicht dabei“). Geprüft werden: Thermostate beschreibbar (vorhanden, verfügbar, können heizen, Soll einstellbar – bei Gruppen alle Ventile), Schreibfehler der Regelschleife, Raummodell (sonst Vorheizen pauschal), Soll erreichbar, Anwesenheit, Nachtzeiten, Außentemperatur, Laufzeit. Übersicht und Heizen-Reiter zeigen die Stufe in der Statuszeile
- Räume, die ihr Soll nie erreichen (kleiner Heizkörper, kalter Flur), sind **kein Hindernis**: Sie heißen „akzeptiert“, mit „höchstens 19,5 °C bei 3 °C außen“ und dem Wert bei Auslegungstemperatur. Erst wenn es mindestens zwei und mehr als die Hälfte der beschreibbaren Räume betrifft, ist es eine Einschränkung („Vorlauf/Abgleich prüfen“) – ein einzelner Raum wie der Flur nie, auch nicht, wenn die anderen noch lernen
- **Kessel über EMS-ESP** (zweiter Block, nur Anzeige): HeatPilot fragt beim Start und dann stündlich die Kesselsteuerungen aus dem Planungspapier ab – nötig: Vorlauf-Soll, Warmwasser-Soll, Heizbetrieb, Warmwasser-Betrieb, Einmalladung; Info: Heizen abschalten, Antipendelzeit, Pumpennachlauf, Heiztemperatur, Desinfektion – mit Wert, Bereich und ob EMS-ESP sie schreiben ließe. „Alle 5 nötigen Steuerungen vorhanden und schreibbar“ bzw. „Es fehlen: …“. Schalter stehen als „an“/„aus“, gleich in welchem Bool-Format EMS-ESP sie meldet. Auch das sind reine GET-Anfragen (`/api/boiler/<kurzname>`, bei neueren EMS-ESP `/api/boiler/dhw/…`) mit 3 s Zeitlimit je Abfrage – hängt EMS-ESP, bricht die Runde ab und die Kesselwerte laufen weiter; einen Schreibpfad zu EMS-ESP gibt es weiterhin nicht. Der Kessel-Block ändert die Stufe der Raumsteuerung nicht – Tado braucht ihn nicht
- **Wochenraster** (Woche im Blick, Zapfungen, Gewohnheit „zu Hause“): sieben gleichmäßige Farbstufen, auch im Dunkelmodus (vorher gingen kleine Werte dort in der Fläche unter), eine Legende unter jedem Raster, Felder ohne Daten nur mit Rahmen; die Gewohnheit in festen Stufen (10–85 %)
- Doku: Vorheizplan und Kachel „Vorhersage“, Abschnitt „Bereit für die Steuerung?“ mit dem Kessel-Block, Wochenraster

## 0.36.0
**Nachtzeiten aus dem Stromverbrauch** (abgesprochen; Standard aus – es ändert sich nichts, bis Leistungssensoren gewählt sind)
- **Einstellungen → Steuerung, „Nachtzeiten aus dem Stromverbrauch“:** Leistungssensoren des Hauses (W oder kW; bei drei Phasen alle drei, die Summe ist die Hausleistung) und „Nicht zählen“ für Verbraucher, die nachts laufen (Waschmaschine, Spülmaschine, Wallbox – werden abgezogen). HeatPilot lernt daraus je Wochentag, wann das Haus schlafen geht und aufsteht: abends fällt der Verbrauch aufs Nachtniveau, morgens steigt er wieder (Kaffeemaschine, Licht, Bad). Je Nacht gilt eine eigene Schwelle zwischen Nacht- und Abendniveau, über dem Flackern von Kühlschrank und Gefrierschrank
- Ausgelassen werden Nächte im Urlaub, wenn alle lange weg sind oder um 22 Uhr niemand zu Hause ist („nicht zu Hause“), Nächte mit Lücken in der Hausleistung („keine Daten“) und Nächte ohne erkennbaren Abend („zu wenig Unterschied“). Fehlt eine Phase, zählt der Messpunkt nicht – eine halbe Summe wäre falsch
- **Wochenplan:** je Tag „gelernt ab“/„gelernt bis“ als Median der letzten sechs Wochen mit Zahl der Nächte und Streuung – Beginn aus den Nächten, deren Abend der Tag ist, Ende aus denen, deren Morgen er ist (Nacht Fr→Sa: Fr „ab“, Sa „bis“). Belastbar ab drei Nächten mit höchstens 90 min Streuung und plausibler Lage (Beginn 19:00–01:30, Ende 03:30–09:30). Mit dem Häkchen **„Gelernte Zeiten verwenden“** ersetzen belastbare Werte je Hälfte den eingestellten Eintrag, sonst gilt der Wochenplan (Rückfall); ohne Häkchen nur Vorschlag. Übersicht und Einstellungen zeigen die wirksame Zeit mit Herkunft („gelernt (Strom)“ / „eingestellt“). Bei „Beobachten“ wird gelernt und angezeigt, geändert wird nichts
- Die Hausleistung wird wie die anderen Messwerte gespeichert und mit der Historie eingelesen – nach dem ersten Start und nach geänderten Sensoren sind sofort Nächte da. Gerechnet wird stündlich, jede Nacht genau einmal; „Lernstand neu berechnen“ rechnet die Nächte mit neu. An den Schreibregeln ändert sich nichts (nur Räume mit „macht mit“, 5–25 °C, EMS-ESP nur lesend)
- Ein gelernter Beginn nach Mitternacht (z. B. 00:30 am Wochenende) gilt am Folgetag – die Nacht ist eine Zeitspanne, der Tag davor bleibt Komfort. Messwerte gelten bis zum nächsten Messpunkt, so lernt HeatPilot auch mit Abtastintervall bis 15 min. Nach einem Wechsel der Leistungssensoren zählen alte Werte nicht mehr, angebrochene erste Nächte auch nicht
- Doku: Abschnitt „Nachtzeiten aus dem Stromverbrauch“

## 0.35.1
- Übersicht „Haus-Trägheit“: erklärt, was die Stunden bedeuten (Zeitkonstante der Wände), und rechnet es für heute aus: „von 21,2 auf 18 °C in ≈ 24 h (5 °C außen; Fremdwärme hält ≈ 6 K über außen)“ – mit Nachttemperatur und Außentemperatur aus Einstellungen und Messung

## 0.35.0
**Räume zuordnen, Hydraulik-Helfer** (abgesprochen)
- **Einstellungen → Räume:** HeatPilot liest Bereiche und Geräte aus Home Assistant. Unter jeder Entität steht „Bereich: Wohnzimmer · Gerät: tado Smart Radiator Thermostat VA… (SRT01)“, die Zeilen sind nach Bereich und Name sortiert. Vorschläge füllen nur leere Felder: Bereich als Raum (Knopf „Bereiche übernehmen“), Sensoren des Geräts als Temperatur/Feuchte/Ventil, Raumregler-Vorschlag beim Wandthermostat. HomeKit-Ventile werden auch ohne Tado-Cloud gefunden; dasselbe Ventil wird nie doppelt (Cloud und HomeKit) gewählt. Nach dem Speichern nennt HeatPilot bei mehreren Geräten in einem Raum das Schlüsselgerät
- **Hydraulischer Abgleich mit Stufen:** je Raum Voreinstellung und Stufenzahl des Ventils (leer = unbekannt, 0 = kein Voreinstellventil). Die Empfehlung sagt dann „von 5 auf 6 stellen (Skala 1–6)“, bei 6/6 „mehr geht am Ventil nicht“ mit Alternativen, bei Stufe 1 kein „zurück“. Dazu eine grobe Erwartung je Raum (Durchflussanteil ∝ (Stufe/Max)², Heizkörperleistung ∝ Durchfluss^0,3): „Flur ≈ −5 K Vorlauf, Wohnzimmer ≈ +2 K“. Nach einer Änderung 10 Tage keine neue Empfehlung für den Raum („misst neu“), ab 14 Tagen die Wirkung: „braucht jetzt X statt Y °C Vorlauf“
- Doku: Räume (Bereiche, Gerätesensoren, Raumregler, Voreinstellung) und „Hydraulischer Abgleich“ erweitert; Planungspapier `docs/planung-vorlauf-ems.md` zum Vorlauf über EMS (nichts gebaut)

## 0.34.0
**HeatPilot steuert** (abgesprochen; Standard bleibt „Beobachten“, es ändert sich nichts, bis du den Schalter umlegst)
- **Schalter „Steuerung“** in den Einstellungen: „Beobachten“ (wie bisher: lernen, empfehlen, Tado nur auf Knopfdruck) oder „HeatPilot steuert“. Dann schreibt HeatPilot die Solltemperaturen der Räume mit Häkchen „macht mit“ selbst, je Raum mit eigener Komforttemperatur. Keine Rückfallebene: zurück zu Tado nur über den Schalter, danach in der Tado-App den Zeitplan fortsetzen. Fällt HeatPilot aus, bleibt das zuletzt geschriebene Soll stehen
- **Phasen:** Komfort · Nacht nach Wochenplan (je Wochentag Beginn/Ende; Standard So–Do 21:00, Fr+Sa 22:00, Ende Mo–Fr 04:30, Sa+So 06:30; Nacht 18 °C) · Abwesenheit (niemand zu Hause) · Vorheizen vor Ankunft bzw. Nachtende aus der Aufheizzeit des Raums, gedeckelt (Standard 3 h) · Urlaub. Ankunft unbekannt oder Tracker ausgefallen → Komfort. Räume, die ihr Ziel nicht erreichen, werden akzeptiert („erreicht ≈ 19,5 °C“), nichts wird hochgedreht
- **Sicherheit:** geschrieben wird nur bei Phasenwechsel, nur über die erlaubten Dienste auf gewählte Räume, 5–25 °C, nie über Komfort. Eine Änderung von Hand (Ventil, App) bleibt bis zum nächsten Phasenwechsel. Pause-Kachel im Heizen-Reiter (1 h / 2 h / 4 h / bis morgen früh). Jeder Schreibvorgang im Protokoll (Heizen, Diagnose, Export). Fehler: nach 15 min erneut, ab 3 Fehlschlägen Hinweis. Beim Zurückschalten auf „Beobachten“ schreibt nichts mehr nach
- **Räume als Gruppen:** mehrere Thermostate (z. B. HomeKit-Ventile) zu einem Raum zusammenfassen (gleicher Raumname); ein Wandthermostat als Raumregler wird allein gemessen und beschrieben, sonst alle Ventile mit gemittelter Raumtemperatur. Alte Einstellungen bleiben wie bisher
- **Boost ohne Tado-Cloud:** Für HomeKit-Entitäten läuft der Boost als eigener Timer; danach stellt HeatPilot den Wunschzustand bzw. das vorherige Soll wieder her, nur wenn das Boost-Soll noch steht. „Zeitplan“ schreibt bei „HeatPilot steuert“ den Wunschzustand, sonst Hinweis auf die Tado-App
- Bei „HeatPilot steuert“ gilt der Wochenplan statt der aus Tado gelernten Nachtzeiten; aus den eigenen Schreibvorgängen wird nichts gelernt
- Oberfläche: Karte „Steuerung“ (Schalter, Warnhinweis, Wochenplan, Nacht-/Abwesenheitstemperatur, Vorheizdeckel), Räume-Tabelle mit „macht mit“/Komfort/Raum/Raumregler, Heizen-Statuszeile und Kachelzustände („HeatPilot: Nacht 18°“, „Vorheizen → 21°“, „manuell bis …“), Übersicht zeigt den Wochenplan. Doku: Abschnitt „Steuerung“

## 0.33.0
**Fremdwärme je Raum: Kamin, Backofen, Gäste** (abgesprochen)
- Wird ein Raum deutlich wärmer, ohne dass der Heizkörper es erklären kann (Ventil zu, keine Sonne auf den Fassaden, Anstieg mindestens 0,4 K und 0,8 K/h über dem, was das Raummodell erwartet), gilt das als Fremdwärme-Ereignis. Das Raummodell lernt diese Zeit nicht mehr als Fremdwärme oder Auskühlen des Raums. Heizkörperbetrieb gilt nie als Fremdwärme, Sonne nur, wenn die Sonnenparameter des Raums schon gelernt sind – das Modell verwirft nie seine eigenen Lernchancen
- Das Hausmodell lässt Stunden mit einem Ereignis aus; ein Tag mit 3 h oder mehr zählt nur als Vortag, wie ein Urlaubstag
- Die Wiedererwärmung nach dem Stoßlüften ist kein Ereignis: Ein Anstieg zählt nur über das höchste Niveau der letzten 2 Stunden hinaus. Nachwärme des Heizkörpers direkt nach dem Schließen auch nicht
- Ohne Standort (keine Sonnenwerte) wird tagsüber (7–20 Uhr) nicht erkannt; Räume ohne Ventilwert gar nicht
- Übersicht: laufendes Ereignis als Hinweis in der Raumtabelle („Fremdwärme seit 19:10, +2,8 K“) mit Zähler der letzten 7 Tage; Woche: neue Ansicht „Fremdwärme“; Vorheizplan: ein Raum mit laufender Fremdwärme gilt als warm und wird nicht eingeplant
- Export enthält die Ereignisse (nur Zeiten und Kelvin).
- Beim Update wird der Lernstand einmal neu berechnet, dabei werden Ereignisse rückwirkend erkannt

## 0.32.0
Prüfung des Exports vom 18.10.: Warmwasser, Anwesenheit, Zeitplan und Woche. Dazu ein Hausmodell, das nicht nur zu diesem Haus passt (abgesprochen).

**Hausmodell für jedes Haus**
- **Wärmeverlust und Fremdwärme aus Tageswerten.** Getestet mit fünf künstlichen Häusern (Altbau, Neubau, Fußboden, durchgeheizt, schwere Wände). Aus Stundenwerten lag der Wärmeverlust bei trägen Häusern teils bei der Hälfte oder fast null, weil das Gas einer Stunde erst Stunden später als Raumwärme ankommt. Ab 21 Heiztagen mit genug unterschiedlichem Wetter rechnet HeatPilot mit Tagesmitteln: dem Tag selbst, dem Vortag und dem Mittel der Woche davor, weil die Wände länger als einen Tag nachwirken. In den Testhäusern (auch schwere Wände, Start im Oktober und im Dezember bei stark wechselndem Wetter) liegt der Wärmeverlust innerhalb von 3–7 %.
- Der Tageswert geht nicht mehr verloren, wenn die Stunde 00–01 Uhr fehlt (Neustart um Mitternacht): Der Tag wird mit der nächsten gelernten Stunde abgeschlossen. Ein Urlaubstag wird nicht gelernt, zählt aber als Vortag
- **Auskühlen:** Die Übertemperatur ohne Heizung kommt jetzt aus der Gasbilanz (Heizgrenze), statt aus wenigen Nächten mitgeschätzt zu werden. Gelernt wird erst, wenn die Gasbilanz plausibel ist. Die Dauer der schnellen Phase nach dem Abschalten lernt jede Anlage selbst (0,5–4 h, bei dir 1,5 h)
- Neue Raummodelle starten mit der gemessenen Trägheit des Hauses statt mit einem Pauschalwert
- „Brenner ohne Wärmebedarf“: Eine erkannte Warmwasserladung zählt auch ohne Ladesignal vom Kessel nicht mehr dazu

**Warmwasser**
- **Legionellenwärme bewusst nutzen.** Die Legionellenschaltung macht der Regler, egal was HeatPilot empfiehlt. Der Vergleichsspeicher der Strategie bekommt diese Wärme jetzt genauso; vorher sparte die Strategie auf dem Papier Verluste ein, die sie gar nicht vermeiden kann. Eine große Zapfung, die üblicherweise auf die Sonderladung folgt (die Wanne am Sonntagabend), gilt als dadurch gedeckt: keine Empfehlung mehr, vorher auf 60 °C zu laden, und sie zählt nicht als „zu kalt“. Das gilt nur für Zapfungen, die der Speicher warm beginnt und die erst durch ihre Größe unter das Minimum ziehen, und höchstens 12 Stunden nach der Sonderladung. Fällt die Sonderladung weg, lernt HeatPilot die Zapfung nach zwei Wochen wieder als eigene
- Warmwasser-Ladekosten zählen jetzt auch den Anstieg nach dem Ladeende (Restwärme): Bei dieser Anlage sinken sie von 0,21 auf etwa 0,17–0,18 kWh/K. Der Bereitschaftsverlust bei 55 °C sinkt dadurch von 147 W auf etwa 120 W. Die Ladegeschwindigkeit zählt den Nachlauf mit; der Vorheizplan rechnet die Ladung dadurch realistisch länger (hier etwa 31 statt 47 K/h)
- Zapfungen werden über ein 20-Minuten-Fenster erkannt, auch wenn das Wasser über mehrere Minuten fällt, etwa beim Duschen. In der letzten Woche kommen so drei Zapfungen dazu, die vorher fehlten. Auch während einer Ladung werden Zapfungen bei jeder Abtastrate gleich erkannt, auch im Minutentakt
- Die angezeigten Auskühl-Messpunkte sind sauberer, weil Zapfungen nicht mehr als Auskühlen zählen: bei 50 °C 0,59 statt 0,70 K/h. Kleine Zapfungen unter der Erkennungsschwelle fließen nicht mehr ins gelernte Auskühlen ein
- Speicherfühler mit 0,5- oder 1-K-Stufen: Die Fühlerstufe wird aus den Messwerten geschätzt und hebt die Zapf-Schwellen entsprechend an. Vorher galt jede einzelne Stufe als kleine Zapfung, das Auskühlen wurde als 0 gelernt, und es gab nie eine Empfehlung. Bei 0,1 K (deine Anlage) ändert sich nichts
- Der Nachlauf nach dem Ladesignal wird über ein 5-Minuten-Fenster geprüft statt je Messpunkt: Im Minutentakt beendete jede flache Fühlerstufe die Ladung zu früh, Ladekosten und Bereitschaftsverlust lagen bei trägem Fühler bis zu 20 % zu hoch. Das 20-Minuten-Zapffenster ist auch bei 15-Minuten-Abtastung 20 min lang
- Beim Ladebeginn zählt nur die halbe Brennerleistung des ersten Messpunkts zur Ladung (Rampe ab 0, wie am Ladeende). Unterbricht die Warmwasserladung im Winter den Heizbetrieb, zählte sonst ein halber Schritt Heizleistung mit, und die Ladekosten lagen 10–20 % zu hoch
- Eine große Zapfung kurz vor einer langen Abwesenheit (alle duschen vor der Abfahrt) wurde beim Zurücknehmen der leeren Stunden doppelt gezählt und galt dann 60 Tage als wiederkehrend. Behoben
- Die Warmwasser-Reserve steigt nur noch einmal je Zapfereignis (eine Wanne zählt nicht mehr doppelt) und nur, wenn mehr Reserve das Ziel vorher wirklich angehoben hätte – auch dann, wenn gerade keine Zapfung erwartet wurde und jemand zu Hause ist, denn dann ist Minimum + Reserve die Untergrenze. Für Haushalte ohne feste Duschzeiten ist das die einzige Korrektur; ohne sie bliebe die Empfehlung dauerhaft auf dem Minimum und die Ersparnis würde zu hoch ausgewiesen. Die ausgewiesene Ersparnis sinkt dadurch auf einen realistischen Wert. Eine volle Wanne, für die ohnehin das Maximum empfohlen wird, treibt sie nicht nach oben; ist niemand zu Hause, im Urlaub oder sind alle lange weg, bleibt sie unverändert. Kleine Zapfungen unter 1 K wie Händewaschen zählen nicht als „zu kalt“
- „Nächste Ladung“ kennt das Warmwasser-Zeitprogramm des Reglers (z. B. nachts 45 statt 55 °C) aus den Messwerten. Abends sagt HeatPilot jetzt die Ladung um 04:35 voraus statt fast einen Tag später
- Die Warmwasser-Ersparnis rechnet Ladeenergie und Verluste über denselben Zeitraum. Legionellenladungen aus der Zeit, bevor die Ladekosten gelernt waren, zählen nicht mehr mit

**Anwesenheit**
- Lange Abwesenheit ohne eingetragenen Urlaub (über 3 Tage, oder über 1 Tag bei mehr als 300 km) wird je Person erkannt: Die Gewohnheit lernt die Reise nicht mehr als „nie zu Hause“ und steht nach der Heimkehr wieder so da wie vor der Abfahrt. Regelmäßige lange Abwesenheit – Wochenpendeln, eine weite Wochenendfahrt alle 1–2 Wochen – wird dagegen ab der zweiten Wiederholung zur selben Uhrzeit als Gewohnheit gelernt, mit Gewohnheits-Ankunft; Raummodelle und Zapfprofil pausieren dabei nicht
- Sind alle lange weg, lernen auch Zapfprofil und Raummodelle nicht – das Zapfprofil auch nicht die leeren Stunden seit dem Weggehen. Urlaubsanzeige und Rückkehrplan bleiben davon unberührt
- Meldet Home Assistant für eine Person „unknown“ oder „unavailable“, zählt sie nicht mehr als weg. In dieser Zeit lernt HeatPilot für sie nichts, und ein ausgefallener Tracker löst keine lange Abwesenheit aus. Ist sonst niemand sicher zu Hause, gilt der Haushalt als offen statt als „niemand zu Hause“: Die Warmwasser-Empfehlung hält dann Minimum plus Reserve, statt bei jedem kurzen Ausfall der Companion-App auf das Minimum zu gehen
- Beim Neuberechnen nach einem Update lernt die Anwesenheit zuerst aus den eigenen gespeicherten Messwerten, danach aus der HA-Historie. Gewohnheiten bleiben so erhalten, auch wenn Home Assistant nur 10 Tage Historie hat
- Die Gewohnheit sagt erst dann eine Ankunft voraus, wenn eine Viertelstunde mindestens zweimal gelernt wurde und mehrheitlich „zu Hause“ zeigt. Wer weit weg ist, wird frühestens nach der Fahrzeit erwartet, nicht mehr „in 15 Minuten“ aus 1500 km
- Ist offen, ob jemand heimkommt, weil die Gewohnheit noch lernt, bleibt die Warmwasser-Empfehlung beim heutigen Soll statt auf das Minimum zu gehen
- Die GPS-Ankunft kommt nicht mehr zu spät, wenn es zu Beginn der Fahrt langsam vorangeht oder Stau ist. Eine bestätigte Heimfahrt zieht die Ankunft vor. GPS-Schwankung am Arbeitsplatz ergibt keine Scheinankunft „in 1 h“ mehr: Als Heimfahrt zählt nur, wer seit Beginn der Annäherung mindestens 2 km näher ist oder mindestens halb so schnell wie gewohnt näher kommt
- Das Heimfahrt-Tempo lernt nur noch aus der durchgehenden Annäherung: Standzeiten und GPS-Sprünge am Arbeitsplatz zählen nicht mehr mit, unplausible Werte werden verworfen
- Die Heimkehr-Kontrolle zeigt die Treffsicherheit getrennt nach GPS und Gewohnheit. Kurze Abstecher wie ein Spaziergang zählen nicht mehr in die Kennzahl
- Der Vorheizplan zeigt, woher die erwartete Ankunft kommt und wie sicher sie ist
- Im Export steht die Anwesenheit jetzt wie die anderen Messwerte im 5-Minuten-Raster, weiterhin nur als Entfernung ohne Koordinaten

**Heizen (Bedienung)**
- Der Reiter ist jetzt wie die Tado-App aufgebaut: oben drei kleine Kacheln **Boost**, **Zeitplan** und **Ausschalten** (alle Räume), darunter die Räume als quadratische Kacheln, zwei nebeneinander – Feuchte, Ist-Temperatur groß, Name und Zustand; heizt der Raum, ist die Kachel orange, aus ist grau. Die Bedienung (Boost-Einstellungen, Soll, Betriebsart) liegt in Dialogen, die sich beim Antippen öffnen. Geschaltet wird weiterhin nur auf Knopfdruck

**Zeitplan und Woche**
- **Nachtabsenkung erkannt, auch wenn Tado nachmittags aus ist.** Bezug ist das Soll der zwei Stunden davor, höchstens aber das Tagesniveau, wenn Tado nachmittags wirklich heizt – so wird die Nacht auch erkannt, wenn der Abend wärmer eingestellt ist als der Tag. Nachtende ist, wenn das Soll morgens den Großteil des Wegs zurück geschafft hat.
- **Tado-Frühstart aus „aus“:** Tado setzt das Soll dann an kalten Tagen früher, statt vorher das Ventil zu öffnen. Das wird jetzt als Frühstart erkannt, und die Verspätung wird am Zeitplanblock gemessen, nicht am früheren Sprung. Der Block wird je Wochentag bestimmt, sobald jeder Wochentag mindestens zweimal vorkommt; getrennte Blöcke (Sa 07:30 und So 09:00) werden nicht zusammengelegt
- Ein kurzer Aussetzer (bis 15 min „aus“, danach dasselbe Soll) gilt nicht mehr als neuer Zeitplanblock – nach Zeit geprüft, bei 60-Sekunden-Abtastung genauso wie bei 5 Minuten
- Geofencing: Steht Tado bei der Heimkehr auf „aus“ und heizt in den 15 min danach nicht hoch, ist das keine Geofencing-Heimkehr und zählt nicht als „zu kalt bei Ankunft“
- Woche: Gas gesamt, Heizen und Warmwasser kommen aus denselben Stunden. Stunden ohne Warmwasser-Angabe zählen nur, wenn die Anlage gar kein Warmwasser-Signal hat. Brennerstarts zählen erst ab dem ersten aufgezeichneten Brennerereignis (bei einer Erstinstallation mit 10 Tagen Recorder zeigte die Woche sonst wochenlang zu wenige Starts)
- Woche „Tado-Soll“ zeigt nur eingeschaltete Räume; neu ist „Tado aus“ (Anteil der Räume auf „aus“)
- Woche „Gas wetterbereinigt“: Heizgas im Verhältnis zum Bedarf laut Hausmodell. Durchgehend nach Modell geheizt ergibt das genau den Wärmeverlust; Absenken macht es kleiner, Aufheizen größer
- Beim Update wird der Lernstand einmal neu berechnet, die Empfehlungen werden neu bewertet

## 0.31.1
- Logo: Die linke Kante der Flammenspitze ist jetzt wie die rechte mit einer Linie verbunden (Symbol, Logo, Browser-Symbol und Zertifikat)

## 0.31.0
Hausmodell genauer, nachgerechnet mit dem Export vom 18.10. (abgesprochen):
- **Auskühlen in zwei Phasen.** Direkt nach dem Abschalten geben Luft und Heizkörper ihre Wärme schnell ab, typisch knapp 1 K in den ersten Stunden. Danach kühlen nur noch die Wände langsam aus. Mit einer einzigen Zeitkonstante kühlte das Haus in der Rechnung nach einem Tag ohne Heizung doppelt so schnell aus wie in Wirklichkeit. Mit zwei Phasen ist der Fehler um 30 % kleiner. Die „Haus-Trägheit“ zeigt jetzt die der Wände, die Ersparnis-Schätzung rechnet mit beiden Phasen
- **Warmwasser aus den Altdaten herausgerechnet.** Die Stunden aus der Home-Assistant-Statistik vor HeatPilot enthielten auch das Warmwasser-Gas, zum Beispiel eine wöchentliche Legionellenschaltung. Jetzt wird vor dem Lernen der übliche Sommerbedarf dieser Wochentags-Stunde abgezogen. Der Wärmeverlust wird dadurch etwas kleiner und der Modellfehler sinkt. Der Wärmebedarf-Graf rechnet genauso
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
- **Ergebnis:** Kein Raum kühlt mehr rechnerisch unter die Außentemperatur. Räume ohne genug Daten zeigen weiter „lernt noch“ mit Grund: zu wenig Heizbetrieb, ein für Tados kleine Ventilöffnungen unrealistisch hohes Verhältnis von Heizwirkung zu Auskühlen, oder Fremdwärme unter außen.
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
