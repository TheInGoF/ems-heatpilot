# Planung: Vorlauftemperatur über EMS führen (Diskussionspapier)

Stand 18.10.2026 (Kesselwerte ergänzt). Nichts davon ist gebaut; EMS-ESP bleibt bis auf Weiteres **nur lesend**.
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

## Empfehlung (Stand heute)

1. **Weg A zuerst**, in Stufen und hinter eigenem Schalter („Vorlauf am Regler: aus / Tag-Nacht /
   Heizkurve nachführen“): erst Trockenlauf mit Protokoll, dann nur `mode` Tag/Nacht und `remotetemp`
   (RAM-Werte), zuletzt die Heizkurve selten nachführen. Jederzeit zurück.
2. **Weg B** erst nach einer Heizsaison mit A, wenn klar ist, dass der ES73 im Weg steht, und mit
   Wächter in HA (HeatPilot-Sensor älter als 1 h → Kessel auf festen Vorlauf 45 °C).

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
2. Nimmt der BK15 `boiler/selflowtemp` an, solange der ES73 am Bus hängt? (Vermutlich überschreibt der
   Regler es im Minutentakt – dann nur Weg B.)
3. Liegt das Warmwasser-Zeitprogramm (04:35 / 21:05, Legionellen So 13:00) im ES73 oder im Kessel?
4. Moduliert der BK15 die Heizkreispumpe selbst, oder gibt der ES73 die Pumpe vor?
5. EEPROM-Grenzen des ES73 (Buderus nennt für RC35 begrenzte Schreibzyklen) – wie oft darf die Kurve
   geändert werden?

## Hinweis zu den ES73-Entitäten in HA

Unsichtbar schalten ändert für HeatPilot nichts: Es liest Zustände über die API, nicht die Oberfläche.
Die Modelle lernen aus Kesselwerten (Vorlauf, Brenner, Gas), Tado-Raumtemperaturen und Außenfühlern –
nicht aus dem ES73. Vom ES73 nutzt HeatPilot nur Regler-Soll/Betriebsart als Hilfsgrößen (Warmwasser-
erkennung, Vorlauf-Studie). **Den ES73 nicht abbauen**, bevor Weg B gebaut und geprüft ist: Der Außenfühler
bleibt zwar am Kessel, aber die Heizkurve rechnet der ES73 – ohne ihn läuft der Kessel mit festem Vorlauf.
