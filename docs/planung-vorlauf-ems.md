# Planung: Vorlauftemperatur über EMS führen (Diskussionspapier)

Stand 19.10.2026 (Kesselwerte ergänzt, Weg B entschieden). Nichts davon ist gebaut; EMS-ESP bleibt bis auf Weiteres **nur lesend**.
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
   - Warmwasser-Soll über HeatPilot statt HA-Automation (Weg W, unten) und Desinfektion als Einstellung
     in HeatPilot (0.36.3), Termin zunächst wie im ES73.
   - „Bereit für die Steuerung?“ zeigt alle nötigen Kessel-Steuerungen als vorhanden und schreibbar.
3. **Umstieg:** eigener Schalter „Kessel: HeatPilot“, erst Trockenlauf mit Protokoll, dann schreiben.
   ES73 erst danach abbauen. Wächter in HA (HeatPilot-Sensor älter als 1 h → Kessel auf festen Vorlauf
   45 °C und Warmwasser 50 °C).

## Warmwasser-Soll schon vor Weg B (Weg W)

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
- **Schreiben nur bei Änderung** (Ingo): kein „55, 55, 55“ – nur wenn sich das Soll ändert. Der Kessel
  hält den Wert dann selbst.
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
  `wwdisinfecting` zum eingestellten Termin aus (0.36.3 legt den Termin an).

## Takt der Heizung (Weg B)

Der Vorlauf braucht mehr als das Warmwasser (Ingo): **live** – HeatPilot rechnet bei jedem EMS-ESP-Abruf
(30 s) neu und schreibt, sobald sich das Vorlauf-Soll ändert (Vorschlag: ab 1 K Unterschied, damit
Nachkommastellen nicht jedes Mal schreiben). Wie beim Warmwasser: nur bei Änderung, 60 s danach
zurücklesen, Fehler über die Benachrichtigungsgeräte melden. Heizbetrieb an/aus bekommt dieselbe
5-min-Haltezeit wie das Warmwasser, damit der Kessel nicht hin und her schaltet.

## Regeln für den Vorlauf (Ideen, Reihenfolge nach Nutzen)

1. **Takt-Boost** (Idee Ingo): Taktet der Kessel stark (z. B. mehr als 3 Starts je Stunde, Brennerläufe
   unter 5 min) und wartet dabei ein Raum mit offenem Ventil unter Soll, den Vorlauf in 2-K-Schritten um
   bis zu +10 K anheben. Mehr Energie je Brennerlauf, längere Läufe, wartende Räume bekommen Leistung.
   Zurück, sobald der Raum warm ist oder das Takten aufhört. Preis: etwas weniger Brennwert, deshalb nur
   bei Bedarf. Die Taktsperre selbst wird nie umgangen.
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

- Weiße Liste der erlaubten Befehle und Werte (wie heute bei Tado in `control.py`), Grenzen 30 °C bis
  `max_flow_temp`, höchstens ein Schreibvorgang je 10 min je Wert, Kurvenparameter höchstens 3× am Tag.
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
| Maximale Brennerleistung | `burnmaxpower` | 100 % | 32–100 | nicht ändern |
| WWK gewählte Temperatur | `wwseltemp` | 45 °C | 0–254 | Speicher-Soll aus der Warmwasser-Empfehlung |
| WWK Komfort | `wwcomfort` | Heiß | Heiß/Eco/Intelligent | nicht ändern |
| WWK aktiviert | `wwactivated` | an | aus/an | Warmwasser aus bei langer Abwesenheit |
| WWK Einmalladung | `wwonetime` | aus | aus/an | Sonderladung vor Ankunft/Wanne |
| WWK Desinfizieren | `wwdisinfecting` | aus | aus/an | Legionellenladung, falls der ES73 wegfällt |
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

## Hinweis zu den ES73-Entitäten in HA

Unsichtbar schalten ändert für HeatPilot nichts: Es liest Zustände über die API, nicht die Oberfläche.
Die Modelle lernen aus Kesselwerten (Vorlauf, Brenner, Gas), Tado-Raumtemperaturen und Außenfühlern –
nicht aus dem ES73. Vom ES73 nutzt HeatPilot nur Regler-Soll/Betriebsart als Hilfsgrößen (Warmwasser-
erkennung, Vorlauf-Studie). **Den ES73 erst abbauen**, wenn Weg B gebaut und im Trockenlauf geprüft ist: Der
Außenfühler bleibt zwar am Kessel, aber Heizkurve, Warmwasser-Zeitprogramm und Desinfektion macht heute der
ES73 – ohne ihn läuft der Kessel mit festem Vorlauf und ohne Legionellenschaltung.
