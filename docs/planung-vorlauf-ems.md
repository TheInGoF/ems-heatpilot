# Planung: Vorlauftemperatur über EMS führen (Diskussionspapier)

Stand 18.10.2026. Nichts davon ist gebaut; EMS-ESP bleibt bis auf Weiteres **nur lesend**.
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
  Legionellenladung selbst anstoßen. Pumpennachlauf/Frostschutz macht der Kessel selbst, aber ohne
  Außentemperaturführung. Der Kessel „reagiert zickig auf Eingaben“ (Erfahrung Ingo) – also erst im
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

## Schutzregeln, falls geschrieben wird

- Weiße Liste der erlaubten Befehle und Werte (wie heute bei Tado in `control.py`), Grenzen 30 °C bis
  `max_flow_temp`, höchstens ein Schreibvorgang je 10 min je Wert, Kurvenparameter höchstens 3× am Tag.
- Trockenlauf-Modus: HeatPilot rechnet und protokolliert, was es schreiben würde, schreibt aber nicht.
- Jeder Schreibvorgang im Protokoll (wie `control_log`), Anzeige im Heizen-Reiter.
- Ausfall-Verhalten dokumentiert: Weg A → ES73 regelt weiter; Weg B → Kesselknopf bzw. HA-Wächter.

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
erkennung, Vorlauf-Studie). **Den ES73 nicht abbauen**, bevor Weg B gebaut und geprüft ist – sonst fährt
der Kessel ohne Außentemperaturführung.
