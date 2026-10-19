# Planung: Vorlauftemperatur über EMS führen (Diskussionspapier)

Stand 19.10.2026 (Kesselwerte ergänzt, Weg B entschieden, Desinfektion gebaut). Gebaut sind **Weg W** (Warmwasser-Soll über Home
Assistant, 0.38, siehe unten) und seit 0.41 **Weg B** (Kessel über HeatPilot: Vorlauf-Soll live und Heizbetrieb über
Home Assistant, eigener Schalter, siehe „Weg B – gebaut (0.41)“); die Fußboden-Grenze je Raum (0.39.1) gilt seit 0.41
auch hart vor jedem Schreiben. Seit 0.42 löst HeatPilot auch die **Desinfektion** zum Termin aus (siehe „Desinfektion –
gebaut (0.42)“) – damit kann der ES73 nach Weg B und 0.42 entfallen. EMS-ESP bleibt **nur lesend** – geschrieben wird über
Home Assistant.
Dieses Papier sammelt Wege, Risiken und offene Fragen, damit die Entscheidung nebenbei reifen kann.

## Ausgangslage

- Kessel Sieger BK15 (Brennwert, 18 kW, Mindestleistung ≈ 30 %) mit Regler **ES73** am EMS-Bus
  (Sieger-Variante des Buderus RC35: witterungsgeführt, Heizkurve, Tag/Nacht, Warmwasser-Zeitprogramm,
  Legionellenschaltung). EMS-ESP liest den Bus; HeatPilot liest EMS-ESP.
- Die frühere **Tado-Erweiterung am Kessel** gab sich auf dem Bus selbst als Regler aus und setzte den
  Vorlauf direkt. Damit war die Brennwertnutzung dahin (hoher Vorlauf, hoher Rücklauf), deshalb wurde sie
  ausgebaut. Diese Bus-Emulation ist proprietär; EMS-ESP kann keinen Regler nachbilden.
- Heute beeinflusst HA den ES73 nur über `remotetemp` (gemeldete Raumtemperatur → Raumeinfluss der
  Heizkurve). Das ist indirekt und begrenzt.

## Ziel

HeatPilot führt den Vorlauf: schnell aufheizen, wenn ein Raum Wärme braucht; im Beharren so niedrig wie
möglich (Brennwert, wenig Takten); nachts/abwesend ganz zurück. Messbar an: Anteil der Brennerstunden mit
Rücklauf < 55 °C, Brennerstarts je Tag, Heizgas je Gradtag (alles schon in der Übersicht).

## Wege

### A. ES73 bleibt, HeatPilot schreibt Regler-Werte über EMS-ESP
Schreibbare Werte eines RC35-artigen Reglers in EMS-ESP (am eigenen Gerät prüfen, siehe Fragen):
`hc1/mode` (auto/day/night), `hc1/daytemp`, `hc1/nighttemp`, `hc1/remotetemp`, `hc1/offsettemp`,
`hc1/designtemp`, `hc1/roominfluence`, `hc1/summertemp`, `hc1/minflowtemp`/`maxflowtemp`,
Warmwasser `wwmode`/`wwtemp`, Urlaub.

- Vorteile: keine Hardware, jederzeit reversibel (Schalter zurück → ES73 regelt wie heute), der Regler
  bleibt Sicherheitsebene (Frostschutz, Pumpenlogik, Warmwasser-Programm, Legionellen).
- Nachteile: indirekt – der Regler glättet und begrenzt; Kurvenparameter liegen im **EEPROM** des Reglers,
  häufiges Schreiben nutzt ihn ab. Häufig schreiben darf man nur RAM-Werte (`remotetemp`, Betriebsart
  Tag/Nacht). Kurvenparameter höchstens wenige Male am Tag.
- Was HeatPilot damit kann: Nacht/Abwesenheit über `mode` schalten (statt nur an den Ventilen zu sparen,
  fährt auch der Kessel runter); Aufheizphase über Raumeinfluss (`remotetemp` absichtlich niedrig melden)
  anheben; die Heizkurve langsam (wöchentlich) aus dem Hausmodell nachführen.

### B. ES73 abbauen, HeatPilot ist der Regler
Ohne Regler hält der Kessel seinen eigenen Vorlauf-Sollwert (am Kessel einstellbar), EMS-ESP schreibt
`boiler/selflowtemp` (Vorlauf-Soll), `boiler/heatingactivated`, `boiler/wwseltemp` (Speicher-Soll).

- Vorteile: direkte Kontrolle, keine Glättung dazwischen; Brennwert in HeatPilots Hand.
- Nachteile: keine Rückfallebene außer dem Knopf am Kessel. Fällt HeatPilot aus, bleibt der letzte
  Vorlauf stehen (bei Nacht-Vorlauf 30 °C wird es kalt). Warmwasser-Zeitprogramm und Legionellenschaltung
  des ES73 entfallen – HeatPilot müsste `wwseltemp` fahren (die Empfehlung dafür gibt es schon) und die
  Legionellenladung selbst anstoßen. Pumpennachlauf/Frostschutz macht der Kessel selbst. Der
  Außenfühler hängt am Kessel und bleibt verfügbar (EMS-ESP `boiler/outdoortemp`) – nur die Heizkurve
  rechnet heute der ES73; ohne ihn hält der Kessel einen festen Vorlauf, bis HeatPilot die
  Witterungsführung übernimmt. Der Kessel „reagiert zickig auf Eingaben“ (Erfahrung Ingo) – also erst im
  Trockenlauf (nur protokollieren, nichts schreiben) prüfen, was er annimmt.
- Voraussetzung: HeatPilot muss vorher zuverlässig steuern (0.34 im Winter bewährt) und eine eigene
  witterungsgeführte Vorlaufrechnung haben (aus Hausmodell und Raummodellen: welcher Raum braucht gerade
  welchen Vorlauf → Maximum, plus Aufheizzuschlag, plus Taktschutz über Mindestlaufzeit).

### C. EMS-ESP als Regler-Emulation
Gibt es nicht. EMS-ESP kann als „Fernbedienung“ Raumtemperaturen liefern (`remotetemp`), aber keinen
Regler wie den ES73 oder die Tado-Erweiterung nachbilden. Weg C entfällt.

## Empfehlung (Stand 19.10.2026)

Geklärt (Ingo): Der ES73 überschreibt alle 60 s, was am Kessel gesetzt wird. Ein eigener Vorlauf am Kessel
geht also nur ohne ES73. Ingo baut den ES73 ab, sobald HeatPilot den Kessel übernimmt.

1. **Ziel ist Weg B** (ES73 ab, HeatPilot ist der Regler). Weg A bleibt nur als Notlösung, falls Weg B
   im Trockenlauf scheitert.
2. **Vorher fertig und geprüft** (in dieser Reihenfolge), solange der ES73 noch regelt:
   - Vorschau/Trockenlauf je Stunde: Vorlauf-Soll, Heizbetrieb an/aus, Warmwasser-Soll (0.37) – verglichen
     mit dem, was der ES73 tatsächlich setzt (`sel_flow`, Speicher-Soll), mindestens einige Wochen Heizbetrieb.
   - Eigene Witterungsführung (Heizkurve aus Haus- und Raummodellen) als Grundlage der Vorschau.
   - Warmwasser-Soll über HeatPilot statt HA-Automation (Weg W, unten – gebaut (0.38)) und Desinfektion als
     Einstellung in HeatPilot (0.36.3), Termin zunächst wie im ES73.
   - „Bereit für die Steuerung?“ zeigt alle nötigen Kessel-Steuerungen als vorhanden und schreibbar.
3. **Umstieg:** eigener Schalter „Kessel: HeatPilot“, erst Trockenlauf mit Protokoll, dann schreiben.
   ES73 erst danach abbauen. Wächter in HA (HeatPilot-Sensor älter als 1 h → Kessel auf festen Vorlauf
   45 °C und Warmwasser 50 °C).

## Weg B – gebaut (0.41)

**Stand 0.41:** gebaut wie in „Takt der Heizung (Weg B)“ und „Regeln für den Vorlauf“ beschrieben – Schalter „Kessel
über HeatPilot“ (`boiler_control`: aus, Trockenlauf, HeatPilot; Standard aus), getrennt von Raumsteuerung und
Warmwasser. Geschrieben wird über Home Assistant, nie per REST an EMS-ESP:

- `number.set_value` nur auf die eine Entität des Vorlauf-Solls (`selflowtemp`; Einstellung `flow_set_entity`, leer =
  erkannt; strenge Prüfung: number mit °C, kein Heizkreis, keine andere Rolle, nie die Heiztemperatur), ganze Grad,
  35 °C bis `max_flow_temp`, höchstens die Fußboden-Grenze mit den Messwerten von jetzt,
- `switch.turn_on`/`turn_off` nur auf die eine Entität des Heizbetriebs (`heatingactivated`; `heating_switch_entity`;
  nie „Heizen abschalten“ oder ein Warmwasser-Schalter).
- **Solange ein Regler am Bus ist, wird nicht geschrieben** (EMS-ESP antwortet auf das Gerät `thermostat` oder es gibt
  frische Regler-Werte in Home Assistant; ohne Antwort von EMS-ESP ist es unbekannt und ebenfalls gesperrt). Der
  Trockenlauf geht immer. Ebenfalls gesperrt, solange „Heizen abschalten“ an ist (EMS-ESP hält die Heizung dann aus).
- Live alle 30 s: Vorlauf-Soll aus der Vorschau-Rechnung (anspruchsvollster Raum, Vorheizen, Aufheizzuschlag), nur ab
  1 K Änderung, höchstens ein Schreiben je 60 s; Heizbetrieb mit 5 min Haltezeit; Prüfung nach 60 s; 3 Fehlschläge in
  Folge oder 2 fremde Änderungen in 1 h → Pause bis „wieder einschalten“; während einer Warmwasser-Ladung bleibt das
  Vorlauf-Soll unberührt.
- Mindest-Vorlauf aus der Abnahme (Näherung: Wärmebedarf des Hauses laut Hausmodell auf die Räume mit Raummodell
  verteilt, je offenem Raum Heizwirkung × (Vorlauf − Raumtemperatur)); reicht auch der höchste Vorlauf nicht, gilt
  nichts. Takt-Boost: +2 K je 10 min bis +10 K, zurück in 2-K-Schritten. Die Antipendelzeit bleibt unberührt.
- Zurückschalten auf aus/Trockenlauf: einmal der Rückfall-Vorlauf (`boiler_fallback_flow`, Standard 45 °C, höchstens
  die strengste Fußboden-Grenze) und Heizbetrieb an. Wächter in Home Assistant: `sensor.heatpilot_heartbeat` jede
  Minute; die Automation legt der Nutzer in der Oberfläche von Home Assistant an (Anleitung in HeatPilot, kein YAML),
  „Bereit für die Steuerung?“ erkennt sie an der Automationsliste (nur lesen).
- Ablauf zum Regler-Abbau: 1. einige Tage Trockenlauf, Vergleich ansehen; 2. Wächter anlegen; 3. Schalter auf
  HeatPilot – gesperrt, solange der Regler dran ist; 4. Regler abbauen (EMS-ESP danach neu starten, damit es den Regler
  nicht mehr meldet); 5. HeatPilot schreibt.
- Nach der Prüfung von 0.41 zusätzlich abgesichert (die Regeln oben bleiben): Eine gescheiterte Abfrage des Reglers
  heißt „unbekannt“ (gesperrt), nicht „kein Regler“ – als abgebaut gilt er erst nach 3 ausdrücklichen „kein Gerät
  thermostat“ in Folge über 5 min und 30 min nach dem letzten Sehen. Kein Pendeln: Vorlauf anheben sofort, senken erst
  nach 10 min niedrigerem Ziel; Heizbetrieb aus erst nach 15 min ohne Bedarf; Boost zurück sofort nur, wenn der Raum
  warm ist, sonst 10 min nach dem letzten Wunsch. Schutz: Steht das Vorlauf-Soll am Kessel über der Grenze (Fußboden,
  höchster Vorlauf, Entität – auch bei unklarem Wert), sofort zurück auf die Grenze bzw. ohne Bedarf Heizbetrieb aus;
  Heizbetrieb an nur mit bekanntem Vorlauf-Soll bis zur Grenze. Kein Ziel rechenbar (z. B. Außentemperatur fehlt): nach
  15 min Rückfall-Vorlauf und Heizbetrieb an, mit Meldung. Ziel auf die Grenzen der Entität begrenzt, Abweisungen als
  Hinweis und nach 15 min als Meldung. Vorlauf-Soll und Warmwasser-Soll nie dieselbe Entität. Eingriff des Wächters
  wird erkannt (keine Pause), Änderungen eines Schritts zählen als ein Ereignis; die Anleitung nennt die Bedingung
  „kessel ist heatpilot“ und schlägt ohne Rückfallwert (unter 35 °C) keinen Wächter vor.
- Im ersten Betrieb ohne Regler zu beobachten: Bleibt das Vorlauf-Soll über Stunden stehen, oder erwartet der Kessel es
  regelmäßig vom Regler? Wenn nicht stehen bleibt: Auffrischen ohne Änderung (z. B. alle 5–10 min) als eigene Regel
  abstimmen – das widerspricht „nur bei Änderung“. Bis dahin die Heiztemperatur am Kessel höchstens auf die strengste
  Fußboden-Grenze stellen.
- Gebaut mit 0.42: Desinfektion auslösen (siehe „Desinfektion – gebaut (0.42)“) und der Vorschlag der Mindestleistung
  aus dem Bereich der maximalen Brennerleistung. Offen: eigene Witterungsführung über die Vorschau hinaus.
- **ES73 abbauen:** Mit Weg B (Vorlauf, Heizbetrieb), Weg W (Warmwasser-Zeitprogramm) und 0.42 (Desinfektion) fehlt ohne
  den ES73 nichts mehr, was er heute macht – er kann nach dem Trockenlauf von Weg B und 0.42 entfallen.

## Warmwasser-Soll schon vor Weg B (Weg W) – gebaut (0.38)

**Stand 0.38:** gebaut wie unten beschrieben – Schalter „Warmwasser-Soll“ (Aus, Trockenlauf, HeatPilot setzt;
Standard aus), geschrieben über Home Assistant (`number.set_value` auf die Entität des Speicher-Solls, nicht per
REST an EMS-ESP), nur bei Änderung ab 1 K, 5 min Haltezeit, Prüfung nach 60 s, Pause nach 3 Fehlschlägen bzw. 2
fremden Änderungen in 1 h, Meldungen über die Benachrichtigungsziele und `binary_sensor.heatpilot_problem`. Die
Raumsteuerung meldet ihre Schreibfehler über dieselbe Stelle. Das Auslösen der Desinfektion ist seit 0.42 gebaut (unten).
Nach der Prüfung von 0.38 zusätzlich abgesichert (die Regeln oben bleiben): geschrieben wird nur auf eine Entität, die
nach dem Speicher-Soll aussieht (°C, kein Heizkreis, keine andere Rolle wie das Vorlauf-Soll), nur ohne Hindernis in
„Bereit für die Steuerung?“; die 1-K-Schwelle gilt auch für die ungerundete Empfehlung; eine Zählsperre gibt es seit
0.39 nicht mehr (Ingo: nach jeder Haltezeit darf bei 1 K Änderung geschrieben werden); Werte direkt von EMS-ESP zählen nur frisch, widersprechen sie der Entität, wird nicht geschrieben;
ein Minimum unter 50 °C steht als Einschränkung da (Legionellen) – entschieden wird es in den Einstellungen.

Weil HA das Speicher-Soll heute schon schreibt und der ES73 es stehen lässt, kann HeatPilot diese Aufgabe
**vor** dem Abbau des ES73 übernehmen – als kleiner, eigener Schritt. **Entschieden** (Ingo, 19.10.2026):
HeatPilot soll das Warmwasser-Zeitprogramm übernehmen können. Reihenfolge: Vorschau in 0.37, danach
Weg W als eigene Version.

- HeatPilot setzt `wwseltemp` je Stunde aus seiner Warmwasser-Empfehlung (Komfort plus Reserve je
  Ereignis, Minimum sonst, Legionellen-Ladung genutzt statt nachgeheizt, Anwesenheit). Die HA-Automation
  wird dann abgeschaltet – zwei Schreiber gleichzeitig darf es nicht geben (Hinweis in der Oberfläche).
- Eigener Schalter „Warmwasser: HeatPilot“, getrennt von der Raumsteuerung; erst Trockenlauf (Vorschau
  in 0.37 zeigt, was geschrieben würde, neben dem, was die Automation tatsächlich setzt), dann schreiben.
- Grenzen: `dhw_min_temp` bis `dhw_max_temp` aus den Einstellungen. Fällt HeatPilot aus, bleibt das
  letzte Soll stehen – darum nie unter das Minimum.
- **Schreiben nur bei Änderung** (Ingo): kein „55, 55, 55“ – nur wenn sich das Soll um mindestens 1 K
  ändert. Der Kessel hält den Wert dann selbst.
- **Haltezeit 5 min nach jedem Schreiben**, kein Viertelstunden-Raster: Schreibt HeatPilot um 15:03 55 °C
  (jemand kommt bald heim), darf es bis 15:08 nicht umentscheiden; danach sofort, wenn nötig.
- **Nach 60 s prüfen**, ob der Kessel ihn übernommen hat (`wwseltemp` zurücklesen). Wenn nicht: Fehler im
  Protokoll, Hinweis in der Oberfläche und eine Meldung in HA, kein endloses Nachschreiben – nach 3
  Fehlschlägen hintereinander pausiert Weg W, bis jemand nachsieht.
- **Meldung in HA über Benachrichtigungsgeräte** (Ingo): In den Einstellungen wählbare Ziele – `notify.*`-
  Entitäten über die Aktion `notify.send_message`, dazu ältere Dienste wie `notify.mobile_app_…` für die
  Handy-App. Gemeldet wird je Ereignis einmal (nicht bei jedem Takt) und einmal „wieder in Ordnung“.
  Zusätzlich `binary_sensor.heatpilot_problem` mit Grund für eigene Dashboards/Automationen. Dieselben
  Meldungen nutzt auch die Raumsteuerung (Schreibfehler am Thermostat) und später Weg B.
- Jeder Schreibvorgang mit Grund im Protokoll wie `control_log`.
- Die Desinfektion bleibt beim ES73, bis er abgebaut wird; danach löst HeatPilot sie über
  `wwdisinfecting` zum eingestellten Termin aus (0.36.3 legt den Termin an, gebaut mit 0.42, siehe unten).

## Desinfektion – gebaut (0.42)

**Stand 0.42:** gebaut wie unten beschrieben, mit diesen Festlegungen: HeatPilot schaltet `wwdisinfecting` nur, wenn ein
Termin eingestellt ist, das Warmwasser-Soll auf „HeatPilot setzt“ steht (Weg W; im Trockenlauf nur Protokoll „würde
Desinfektion starten“), **kein Regler am Bus** ist (dieselbe Erkennung wie Weg B – statt „keine erkannte Desinfektion zu
anderer Zeit“; mit Regler macht er sie) und Weg W nicht pausiert ist. Geschrieben wird über Home Assistant
(`switch.turn_on`/`turn_off` nur auf die eine Entität der Rolle `dhw_disinfecting`, Einstellung `disinfect_entity`, leer
= erkannt; strenge Prüfung: switch, Name passt, nie Einmalladung, Zirkulation oder ein anderer Kesselschalter).
- Zum Termin (Ortszeit, auch in der Woche der Zeitumstellung) einschalten – höchstens einmal je Termin (Zustand in kv,
  überlebt Neustarts); nach 60 s prüfen (Schalter an bzw. EMS-ESP meldet Desinfektion/Sonderladung); beobachten, bis der
  Speicher die Desinfektionstemperatur − 3 K erreicht (Erfolg) – spätestens **Ladedauer laut Modell + 60 min** (statt
  + 30 min), sonst Fehlschlag mit Meldung und Problem-Sensor.
- Danach: steht der Schalter noch an, einmal ausschalten und prüfen – nach einem Erfolg erst nach 15 min Nachlauf (der
  Kessel setzt ihn meist selbst zurück), nach einem Fehlschlag gleich.
- Verpasst (HeatPilot aus oder gesperrt): innerhalb von 12 h nachholen, sonst Meldung „Desinfektion verpasst“ und der
  nächste Termin. Ein eben eingetragener Termin, der schon vorbei ist, wird nicht ausgelöst.
- Während des Ablaufs schreibt Weg W das Speicher-Soll nicht, Weg B lässt das Vorlauf-Soll in Ruhe. Löst HeatPilot den
  Termin aus, zählt er im Warmwasser-Modell ohne Bestätigung durch einen Regler.
- Anzeige: Einstellungen → Warmwasser, Zeile „Auslösen“ („löst HeatPilot aus (So 17:30)“, „macht der Regler“, „aus“) mit
  der letzten Desinfektion; dieselbe Zeile in „Bereit für die Steuerung?“ (Block Warmwasser); Protokoll im Heizen-Reiter.
- Nach der Prüfung von 0.42 (gleiche Bedingungen fürs Ein- und Ausschalten): Ausschalten bei kurzer Sperre (EMS-ESP
  antwortet nicht, Pause), unbekanntem Schalter oder Fehler bis 30 min weiter versuchen, dann Meldung; „gelungen“ nur
  nach Bestätigung durch den Kessel, bei schon heißem Speicher erst mit voller Temperatur bzw. Rücksetzen durch den Kessel
  (Nachlauf dann bis zur Frist); höchstens eine Desinfektion je 24 h (Termin verlegt); „macht der Regler“ nur mit frischem
  Nachweis (eben abgebaut → warten und nachholen); Zeitüberlauf beim Einschalten → Prüfung nach 60 s entscheidet; ein
  gescheiterter Termin zählt im Warmwasser-Modell nicht. Offen bleibt, ob der Kessel den Schalter nach dem Programm
  selbst zurücksetzt – im ersten Betrieb ohne Regler beobachten.

Ursprünglicher Plan (0.36.3):

Stand 0.36.3 (gebaut, nur lesend): Termin in den Einstellungen (Tag, Uhrzeit; Standard aus), Temperatur
vom Kessel (`wwdisinfectiontemp`, sonst Einstellung 60–80 °C), Vorschlag kurz vor der größten regelmäßigen
Zapfung, im Warmwassermodell eingeplant (Empfehlung und Schatten wie nach einer erkannten Sonderladung, ab
Beginn + Ladedauer) – aber nur bestätigt: Der ES73 hat in den letzten 8 Tagen zur Terminzeit desinfiziert.
Sonst zählt der Termin nicht, denn ohne Auslösen kommt keine Wärme. Als Desinfektion gilt eine Sonderladung
mit `wwdisinfecting` oder bis 5 K an die Desinfektionstemperatur – keine Einmalladung. Mit Termin ist
`wwdisinfecting` in „Bereit für die Steuerung?“ nötig. Ausgelöst wird nichts.

Mit dem Kessel-Schalter (Weg B, eigener Schalter) dazu:
1. **Auslösen:** zum Termin `wwdisinfecting` an – einmal, ins Protokoll wie `control_log`; nur wenn der
   ES73 nicht selbst desinfiziert: keine erkannte Desinfektion zu anderer Zeit, die sich wiederholt (zwei
   Erkennungen am gleichen Wochentag ±1 h in den letzten Wochen). Einmalladungen zählen nicht – sonst setzte
   eine Einmalladung die Legionellenschaltung eine Woche aus. Ab dann zählt der Termin im Modell, weil
   HeatPilot ihn auslöst (nicht mehr erst bestätigt).
2. **Prüfen:** Läuft sie? `dhw_special` (EMS-ESP meldet `wwdisinfecting`/Sonderladung) und die
   Speichertemperatur: innerhalb von Ladedauer + 30 min mindestens Desinfektionstemperatur − 3 K.
3. **Fehlschlag:** kein Anstieg, Schalter fällt sofort zurück oder EMS-ESP lehnt ab → Hinweis über die
   Benachrichtigungsgeräte („Desinfektion So 17:30 nicht gelaufen“) und `binary_sensor.heatpilot_problem`;
   kein erneuter Versuch vor dem nächsten Termin, außer von Hand.

## Takt der Heizung (Weg B)

Der Vorlauf braucht mehr als das Warmwasser (Ingo): **live** – HeatPilot rechnet bei jedem EMS-ESP-Abruf
(30 s) neu und schreibt, sobald sich das Vorlauf-Soll ändert (Vorschlag: ab 1 K Unterschied, damit
Nachkommastellen nicht jedes Mal schreiben). Wie beim Warmwasser: nur bei Änderung, 60 s danach
zurücklesen, Fehler über die Benachrichtigungsgeräte melden. Heizbetrieb an/aus bekommt dieselbe
5-min-Haltezeit wie das Warmwasser, damit der Kessel nicht hin und her schaltet.

**Gebaut (0.41):** genau so – live alle 30 s, ab 1 K, Prüfung nach 60 s, Heizbetrieb mit 5 min Haltezeit; dazu
höchstens ein Vorlauf-Schreiben je 60 s (siehe „Weg B – gebaut (0.41)“). Gegen Pendeln an einer Schwelle (nach der
Prüfung von 0.41): Vorlauf anheben sofort, senken erst nach 10 min niedrigerem Ziel; Heizbetrieb aus erst nach 15 min
ohne Bedarf.

**Auflösung fürs Schreiben (Ingo):** Warmwasser-Soll ab 1 K Änderung, Vorlauf-Soll ab 1 K, Raum-Soll in
der **vollen Auflösung des Thermostats** – bei Tado 0,5 K, siehe nächster Abschnitt.

## Thermostate als Stellglied: Ventilverhalten lernen – Stufe 1 gebaut (0.39), Stufe 2 gebaut (0.40)

Idee Ingo: HeatPilot „missbraucht“ den eingebauten PID-Regler der Thermostate. Tado lässt das Ventil nicht
direkt stellen – aber über ein fein verschobenes Raum-Soll lässt sich die Ventilöffnung führen. Dafür
braucht es die **volle Auflösung des Thermostats** – Tado kann 0,5 K, feiner geht nicht. Mit groben
Schritten (1 K und mehr) springt der Regler nur zwischen auf und zu, das Ventil kennt dann praktisch nur
an und aus. Jeder 0,5-K-Schritt zählt, damit es so fein wie möglich moduliert.

Dafür muss HeatPilot das Verhalten jedes Ventils nachbilden (reverse engineering aus den eigenen Daten,
`room_samples`: Raumtemperatur, Soll, Ventil in %):
- Wie öffnet das Ventil abhängig vom Abstand Soll − Raumtemperatur und von der Zeit (Verstärkung,
  Nachstellzeit, Totzeit), Sättigung 0–100 %, Totband um das Soll.
- Gelernt aus den vorhandenen Soll-Wechseln und dem normalen Verlauf, je Ventil, mit Vorhersagefehler;
  erst wenn die Vorhersage trägt, nutzt die Steuerung es.
- Anzeige je Raum: „Ventil öffnet bei 0,5 K unter Soll auf ≈ 40 %, schließt ab Soll“.
- Umgekehrt genutzt: Welches Soll (in 0,5-K-Schritten) bringt die gewünschte Ventilstellung – z. B. das Ventil
  früh drosseln, wenn ein Raum schnell heizt, oder mehrere Räume gleichzeitig öffnen (weniger Takten).
- Schrittweite je Thermostat aus HA (`target_temp_step`, bei Tado 0,5 K); andere Geräte mit feinerem
  Schritt nutzen ihren Schritt. Geschrieben wird immer auf den Schritt gerundet.
- Nur bei Änderung schreiben, 5 min Haltezeit, Protokoll.

**Stand 0.39 – Stufe 1 gebaut: lernen, anzeigen, Trockenlauf; es wird nichts Neues geschrieben.**
- Ventilmodell je Raum (`valve_model.py`, Lernen in `valve_learn.py`, angebunden in `engine_learn`): Kennlinie im
  Beharren (Median je 0,1-K-Klasse von −1 bis +2 K, monoton geglättet; Öffnungsbeginn, halb, ganz offen, Totband),
  Sprungantwort (Totzeit, 63 %-Zeit, auf/zu getrennt, „grob“ bei langer Messpause), PI mit Sättigung, Totzeit,
  Stellmotor und Nullpunkt des Integrators (kleinste Quadrate auf 30-min-Abschnitten ohne Sättigung und Soll-Sprung,
  Messrauschen herausgerechnet), Gütemaß über 14 Tage (ein Messschritt und 30 min voraus, neben „Ventil bleibt, wie es
  ist“). Vorhersage (`valve_predict.py`) als P-Regler entlang der Kennlinie oder mit Integrator – es gilt, was besser
  trifft; mit Integrator hängt die Kennlinie von der Last ab und wird nur gezeigt. Nur an/aus: Schaltpunkte. Belastbar
  ab 8 Sprüngen (4 je Richtung, wenig Streuung), 48 h Beharrung, ≤ 20 Prozentpunkten Fehler und deutlich besser als
  „Ventil bleibt“.
- Lernt beim ersten Start aus den gespeicherten Messwerten der letzten 60 Tage in Abschnitten nach (eigener
  Modellstand statt Neuberechnung), danach je Raumschritt. Fenster, Fremdwärme, Heizbetrieb aus werden ausgelassen.
- Schrittweite je Gerät aus `target_temp_step` (Gruppe: gröbster Schritt der Schreibziele, sonst 0,5 K angenommen).
- Trockenlauf (`valve_dry.py`, Tabelle `valve_dry_log`): **früh drosseln** (Raum nähert sich gemessen dem Soll, nach
  einem Soll-Anstieg oder deutlich darunter; Soll laut Raum- und Ventilmodell in < 20 min erreicht, Ventil dann noch
  > 30 % offen, ≥ 0,2 K Überschwingen vorhergesagt → einen Schritt tiefer, beim Erreichen zurück, danach erst nach
  neuem Soll wieder) und **gemeinsam anfordern** (nur ein Raum fordert an, Kessel taktet → Räume, deren Ventil beim
  Abkühlen in 60 min ohnehin öffnen würde, einen Schritt höher). Nur bei Änderung, volle Auflösung, 5 min Haltezeit –
  mitgezählt. Auswertung mit geschätzter Wirkung nur bei belastbaren Modellen, dazu nach einer Stunde gemessen, was
  wirklich geschah; die Schätzung erst, wenn das vorhergesagte Überschwingen ohne Eingriff dreimal stimmte.
- Erkenntnis aus den Testthermostaten: Ein Ventil, das nur an/aus kennt, gewinnt durch frühes Drosseln kaum etwas;
  der Hebel liegt bei stetigen Ventilen mit Integrator, die am Soll noch offen sind – und auch dort ist er klein
  (im Simulator ≈ 0,05 K weniger Überschwingen je Eingriff bei 0,8 K Überschwingen). Ein P-Regler schließt vor dem
  Soll und braucht kein Drosseln. Ob sich Stufe 2 lohnt, zeigen die nachgemessenen Daten.
- **Stufe 2 gebaut (0.40): Ventile führen** (`valve_control.py`, eigener Schalter `valve_control`, Standard aus – aus
  bleibt es beim Trockenlauf). Wirkt nur bei „HeatPilot steuert“ in Räumen mit „macht mit“, plausiblem Raummodell,
  belastbarem Ventilmodell und einem Schritt bis 0,5 K; geschrieben wird über den bestehenden Weg (`Control.apply`,
  `climate.set_temperature` auf die Schreibziele), höchstens **ein Schritt** vom Wunschsoll der Steuerung, auf den
  Schritt gerundet, 5–25 °C. Regeln aus `valve_dry`, dazu strenger: früh drosseln nur in Komfort/Vorheizen, nie unter
  Soll − 0,3 K, sofort zurück (auch in der Haltezeit), wenn der Raum darunter fällt – **wer Wärme braucht, bekommt sie
  sofort**; gemeinsam anfordern nur für Räume, die laut Raummodell in 60 min ohnehin heizen würden, ohne Kette und
  Pendeln (kein Auslöser, der gerade gedrosselt wird oder in den letzten 60 min angehoben war; kein Kandidat, der
  nach dem Drosseln gesperrt ist oder in den letzten 60 min angehoben war). Nur bei Änderung, 5 min Haltezeit,
  Phasenwechsel und Hand gehen vor (auch „aus“ bleibt aus; Eigenes – verzögert gemeldet, Teilfehler einer Gruppe –
  zählt nie als Hand), Fehler wie die übrige Raumsteuerung. Pause: kein neuer Schritt, das Zurück aufs Wunschsoll geht
  trotzdem hinaus; vor Pause, „Beobachten“ und „macht mit“ aus nimmt HeatPilot laufende Schritte einmal zurück. Protokoll im Schreibprotokoll („Ventil: …“) und als geschriebener Eintrag im
  Ventil-Protokoll; nach einer Stunde wird nachgemessen, was wirklich geschah. Anzeige: Block „Ventile führen“ in
  „Bereit für die Steuerung?“, Zeile im Raum-Dialog, „schreibt“ in der Vorschau. Den Vorlauf fasst die Ventilführung
  nicht an – die Fußboden-Grenze bleibt unberührt; der Takt-Boost kommt mit Weg B.

## Regeln für den Vorlauf (Ideen, Reihenfolge nach Nutzen)

**Festgelegt (Ingo, 19.10.2026):**
- **Untergrenze 35 °C**, wenn geheizt wird. Braucht kein Raum so viel, ist der Heizbetrieb aus – darunter lohnt
  Heizen nicht (gilt seit 0.39 in Vorschau und Trockenlauf).
- **Mindest-Vorlauf aus der Abnahme:** Der Kessel kann nicht unter seine Mindestleistung (BK15 ≈ 30 % von 18 kW).
  Aus den Raummodellen rechnen, ab welchem Vorlauf die offenen Räume diese Leistung abnehmen; liegt das über 35 °C,
  gilt der höhere Wert. Reicht auch das nicht, lieber lange Pausen als viele kurze Läufe. (Live-Test Ingo: der BK15
  meldet keine Mindestleistung. Seit 0.42 schlägt HeatPilot die Untergrenze des Bereichs von „Maximale Brennerleistung“
  vor – 32 % –, übernommen wird sie nur von Hand unter Haus & Kessel; ohne Wert ist die Abnahme-Regel aus.)
- **Wer Wärme braucht, bekommt sie sofort.** Braucht nur ein Raum Wärme, wird DIESER Raum warm – der Takt-Boost hebt
  den Vorlauf dafür an. Kein Warten, bis andere Räume auskühlen und mitanfordern. „Gemeinsam anfordern“ zieht nur
  Räume vor, die ohnehin bald heizen würden, und lässt nie einen Raum warten.
- **Fußbodenheizung je Raum – gebaut (0.39.1, `floor_limit.py`):** Anschluss Mischer (keine Grenze aus diesem
  Raum), RTL-Ventil (Grenze gilt, solange der Raum Wärme anfordert: ein Thermostat mehr als 0,1 K unter Soll, Ventil
  über 5 % oder „heizt“ bzw. „heizt vor“, bei Gruppen genügt eines; unbekannt = gilt) oder direkt am Kessel (Grenze gilt immer, auch
  ohne Modell und Messwert); Räume mit dem alten Häkchen ohne Anschluss gelten wie direkt („Anschluss wählen“). Die
  Grenze legt der Nutzer fest, ohne Angabe 45 °C (Ingo: RTL, 55 °C). Heute hält die Vorschau sie ein (jede Stunde,
  RTL jetzt nach den Messwerten wie `flow_cap`, danach in den Stunden mit Wärmebedarf, bei nicht verfügbarem
  Thermostat in jeder Stunde; auch beim Vorheizen; unter 35 °C gilt die Grenze statt „Heizbetrieb aus“),
  „Bereit für die Steuerung?“ zeigt sie. **Weg B: harte Grenze vor jedem Schreiben** – jedes Vorlauf-Soll geht
  unmittelbar vor dem Schreiben durch `floor_limit.flow_cap` (mit den Messwerten von jetzt) und wird abgewiesen bzw.
  auf die Grenze gesenkt, auch beim Takt-Boost und beim Vorheizen; ein Wert darüber wird nie geschrieben.

1. **Takt-Boost** (Idee Ingo, gebaut 0.41: ab 3 Starts je Stunde mit Läufen im Mittel unter 10 min): Taktet der Kessel stark (z. B. mehr als 3 Starts je Stunde, Brennerläufe
   unter 5 min) und wartet dabei ein Raum mit offenem Ventil unter Soll, den Vorlauf in 2-K-Schritten um
   bis zu +10 K anheben. Mehr Energie je Brennerlauf, längere Läufe, wartende Räume bekommen Leistung.
   Zurück, sobald der Raum warm ist oder das Takten aufhört. Preis: etwas weniger Brennwert, deshalb nur
   bei Bedarf. Die Taktsperre selbst wird nie umgangen, die Fußboden-Grenze auch nicht (höchstens bis zu ihr).
2. **Beharrung tief:** Sind alle Räume am Soll und die Ventile wenig offen, Vorlauf senken, bis der
   erste Raum sein Soll nicht mehr hält (Raummodelle: nötiger Vorlauf je Raum, Maximum plus Reserve).
3. **Aufheizen hoch:** Vor dem Nachtende bzw. der Ankunft Vorlauf anheben, damit die Aufheizzeit kurz
   wird (aus dem Vorheizplan), danach wieder auf Beharrung.
4. **Nacht/Abwesenheit:** Heizkreis über den Regler in Nacht/Absenkbetrieb, nicht nur die Ventile zu.

Hebel ohne Vorlauf-Zugriff, schon über die Sollwerte möglich (0.37 „Soll nach Ventilstellung“): Räume
gemeinsam anfordern lassen statt nacheinander – gezählt werden Räume (Raumgruppen aus 0.34), nicht
Ventile; das Wohnzimmer mit zwei Ventilen ist ein Raum – fordert nur der Flur Wärme an, taktet der Kessel immer;
fordern drei Räume zusammen an, läuft er durch.

## Anzeigen, die mit Weg A/B entfallen

- „Startverzögerung“ und „Tag/Nacht-Wechsel“ (Übersicht): messen das Warten auf den Nachtbetrieb des
  ES73. Schaltet HeatPilot Tag/Nacht selbst oder ist der ES73 weg, ausblenden.
- Vorlauf-Studie „Vorlauf gegen Takten“: wird vom Takt-Boost abgelöst bzw. zur Erfolgskontrolle.

## Schutzregeln, falls geschrieben wird

- Weiße Liste der erlaubten Befehle und Werte (wie heute bei Tado in `control.py`), Grenzen 35 °C bis
  `max_flow_temp` (Ingo: braucht kein Raum mindestens 35 °C, Heizbetrieb aus), Fußboden-Grenze je Raum als harte
  Grenze vor jedem Schreiben (`floor_limit.flow_cap`, siehe „Regeln für den Vorlauf“), Kurvenparameter höchstens 3× am
  Tag. Gebaut (0.41): Vorlauf-Soll höchstens einmal je 60 s (live, Ingo – statt der früheren Idee „einmal je 10 min“),
  Heizbetrieb mit 5 min Haltezeit; über der Grenze sofort zurück (Schutz), Heizbetrieb an nur mit Vorlauf-Soll bis zur
  Grenze, gesperrt, solange nicht sicher ist, dass kein Regler am Bus ist.
- Trockenlauf-Modus: HeatPilot rechnet und protokolliert, was es schreiben würde, schreibt aber nicht.
- Jeder Schreibvorgang im Protokoll (wie `control_log`), Anzeige im Heizen-Reiter.
- Ausfall-Verhalten dokumentiert: Weg A → ES73 regelt weiter; Weg B → Kesselknopf bzw. HA-Wächter.

## Schreibbare Kesselwerte (Export aus EMS-ESP, 18.10.2026)

Antwort auf einen Teil der Fragen unten: EMS-ESP zeigt am **Kessel** (nicht am ES73) diese schreibbaren Werte.
Beim Export stand „Heizen abschalten: an“ und Vorlauf-Soll 7 °C bei 11 °C außen – der Kessel war im Aus-/Sommerbetrieb.

| Steuerung (HA-Name) | EMS-ESP | Wert beim Export | Bereich | Wofür HeatPilot sie bräuchte |
|---|---|---|---|---|
| Gewählte Vorlauftemperatur | `selflowtemp` | 7 °C | 0–90 | Weg B: Vorlauf-Soll je Stunde |
| Heizbetrieb aktiviert | `heatingactivated` | an | aus/an | Heizkreis nachts/abwesend ganz aus |
| Heizen abschalten | `heatingoff` | an | aus/an | Sommer/Aus (heute setzt es der ES73) |
| Heiztemperatur | `heatingtemp` | 90 °C | 0–90 | Obergrenze – nur einmal auf `max_flow_temp` |
| Antipendelzeit | `burnminperiod` | 25 min | 0–120 | Taktsperre: **lesen**, nicht ändern (Regel bleibt) |
| Pumpennachlaufzeit | `pumpdelay` | 8 min | 0–60 | nur lesen (Nachlauf in der Bilanz) |
| Maximale Brennerleistung | `burnmaxpower` | 100 % | 32–100 | nicht ändern; Untergrenze = Vorschlag Mindestleistung (0.42) |
| WWK gewählte Temperatur | `wwseltemp` | 45 °C | 0–254 | Speicher-Soll aus der Warmwasser-Empfehlung |
| WWK Komfort | `wwcomfort` | Heiß | Heiß/Eco/Intelligent | nicht ändern |
| WWK aktiviert | `wwactivated` | an | aus/an | Warmwasser aus bei langer Abwesenheit |
| WWK Einmalladung | `wwonetime` | aus | aus/an | Sonderladung vor Ankunft/Wanne |
| WWK Desinfizieren | `wwdisinfecting` | aus | aus/an | Legionellenladung ohne ES73 – seit 0.42 zum Termin |
| WWK Desinfektionstemperatur | `wwdisinfectiontemp` | 70 °C | 60–80 | nur lesen |
| WWK Zirkulation | `wwcirc`/`wwcircmode` | aus / 1×3 min | – | nicht ändern |

Folgerungen:
- Für Weg B reichen **zwei Werte im Takt** (`selflowtemp`, `wwseltemp`) und **drei Schalter** (`heatingactivated`,
  `wwactivated`, `wwonetime`); alles andere bleibt Nutzer-Einstellung. Die Antipendelzeit (25 min) ist der Grund für
  die „Wartezeit durch Taktsperre“ in der Übersicht – sie bleibt, wie sie ist; der Takt-Boost arbeitet innerhalb davon.
- **Vor dem Aktivieren muss HeatPilot prüfen und anzeigen, dass es alle nötigen Steuerungen hat** (Wunsch Ingo): je
  Steuerung gefunden/schreibbar (EMS-ESP meldet `writeable` je Entität), erst wenn alle fünf da sind, lässt sich der Kessel-
  Schalter stellen. Kommt als Abschnitt „Kessel“ in „Bereit für die Steuerung?“ (0.36.1, nur Anzeige).
- **Vorschau vor dem Schreiben** (Wunsch Ingo): HeatPilot zeigt für die nächsten 24 h je Stunde, welchen Vorlauf und welches
  Warmwasser-Soll es setzen würde (Trockenlauf) – aus Raummodellen (nötiger Vorlauf des anspruchsvollsten Raums mit Soll laut
  Steuerungsplan, Aufheizzuschlag vor Nachtende/Ankunft, Takt-Boost), Nachtplan (Heizbetrieb aus bzw. Absenkvorlauf),
  Warmwasser-Plan (Komfort + Reserve je Stunde, Sonderladungen). Geschrieben wird nichts; die Vorschau ist der Maßstab, ob die
  Rechnung taugt, bevor ein Schalter kommt.
- Offen bleibt Frage 2: nimmt der BK15 `selflowtemp` an, solange der ES73 am Bus hängt? Prüfen im Trockenlauf durch Vergleich
  der Vorschau mit dem, was der ES73 tatsächlich setzt (`sel_flow` wird schon gespeichert).

## Offene Fragen an Ingo / zu prüfen

1. Welche Thermostat-Werte zeigt EMS-ESP für den ES73 als schreibbar (`api/thermostat`, Spalte
   „writeable“)? Besonders `mode`, `remotetemp`, `offsettemp`, `designtemp`, `wwmode`.
2. ~~Nimmt der BK15 `boiler/selflowtemp` an, solange der ES73 am Bus hängt?~~ **Nein** – der ES73
   überschreibt alle 60 s (Ingo, 19.10.2026). Daraus folgt Weg B.
3. ~~Liegt das Warmwasser-Zeitprogramm im ES73 oder im Kessel?~~ **Weder noch** – eine HA-Automation setzt
   das Speicher-Soll (`wwseltemp`): 55 °C, Nacht, Abwesenheit (Ingo, 19.10.2026). Der ES73 hat dafür kein
   richtiges Zeitprogramm und überschreibt diesen Wert nicht. Die Desinfektion (So 13:00) kommt dagegen aus
   dem ES73 – der Kessel hat dafür keinen Timer, nur `wwdisinfecting` an/aus.
4. Moduliert der BK15 die Heizkreispumpe selbst, oder gibt der ES73 die Pumpe vor?
5. EEPROM-Grenzen des ES73 (Buderus nennt für RC35 begrenzte Schreibzyklen) – wie oft darf die Kurve
   geändert werden?
6. Setzt der Kessel `wwdisinfecting` nach dem Desinfektionsprogramm selbst zurück? (Im ersten Betrieb ohne ES73 im
   Protokoll der Desinfektion ablesen: „Schalter aus (vom Kessel)“ bzw. „Schalter aus“ nach dem Ausschalten.)

## Hinweis zu den ES73-Entitäten in HA

Unsichtbar schalten ändert für HeatPilot nichts: Es liest Zustände über die API, nicht die Oberfläche.
Die Modelle lernen aus Kesselwerten (Vorlauf, Brenner, Gas), Tado-Raumtemperaturen und Außenfühlern –
nicht aus dem ES73. Vom ES73 nutzt HeatPilot nur Regler-Soll/Betriebsart als Hilfsgrößen (Warmwasser-
erkennung, Vorlauf-Studie). **Den ES73 erst abbauen**, wenn Weg B gebaut und im Trockenlauf geprüft ist: Der
Außenfühler bleibt zwar am Kessel, aber Heizkurve, Warmwasser-Zeitprogramm und Desinfektion macht heute der
ES73 – ohne ihn läuft der Kessel mit festem Vorlauf und ohne Legionellenschaltung, solange HeatPilot sie nicht übernimmt
(Weg B, Weg W und seit 0.42 die Desinfektion).
