# EMS HeatPilot

## Was das Add-on in Version 0.7 macht

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

## Optionen

| Option | Bedeutung |
|---|---|
| `outdoor_sensors` | ein oder mehrere Außenfühler, es zählt das Minimum. Vorgabe: `sensor.aussen_temperatur`. Leer = EMS-ESP-Außenfühler |
| `rooms` | Räume einzeln festlegen (siehe unten). Leer = alle Tado-Räume automatisch |
| `gas_sensor` | Gaszähler-Entität (m³ oder kWh). Leer = automatisch suchen, notfalls Energiezähler von EMS-ESP |
| `weather_entity` | Wetter-Entität für die Vorhersage. Leer = automatisch |
| `emsesp_host` | IP/Name von EMS-ESP (Vorgabe `192.168.188.102`): alle Werte direkt lesen. Leer = nur über HA |
| `gas_calorific_value`, `gas_z_number` | Brennwert und Zustandszahl laut Gasrechnung: kWh = m³ × Brennwert × Zustandszahl |
| `gas_price_eur_per_kwh`, `gas_base_fee_eur_per_year` | Arbeitspreis und Grundpreis für die Euro-Angaben |
| `design_outdoor_temp` | Auslegungs-Außentemperatur (Hamburg: −12 °C) |
| `max_flow_temp` | maximale Vorlauftemperatur |
| `away_temp`, `night_temp`, `night_start`, `night_end` | Strategie für die Ersparnis-Schätzung |
| `backfill_days`, `statistics_backfill_days` | wie viel Historie beim ersten Start übernommen wird |
| `overrides` | Zuordnung erzwingen, z. B. `flow_temp=sensor.boiler_curflowtemp` |

## Räume konfigurieren

```yaml
rooms:
  - name: Wohnzimmer
    climate: climate.wohnzimmer          # Tado-Thermostat (Soll + Ist)
    heating_power: sensor.wohnzimmer_heating   # optional: Ventil/Heizleistung %
    temperature: sensor.wohnzimmer_temperature # optional: für die Langzeitstatistik
    humidity: sensor.wohnzimmer_humidity       # optional
  - name: Bad
    climate: climate.bad
```

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
RTL-Ventil), die climate-Entität unter `floor_heating_rooms` eintragen:

```yaml
floor_heating_rooms:
  - climate.badezimmer
```

Das Raummodell bekommt dann einen zweiten Wärmeeingang: Der Fußboden heizt
immer, wenn warmes Heizwasser fließt – unabhängig von Tado – und wirkt über den
Estrich verzögert (≈3 h). Der Tado-Heizkörper im Raum wird als Boost gelernt.
Ohne diese Angabe würde das Modell die Fußbodenwärme als „Fremdwärme“ deuten
und das Auskühlen des Raums falsch einschätzen.

## Urlaub

```yaml
vacations:
  - 2026-12-20 - 2027-01-03            # Rückkehr am 03.01. um 18:00
  - 2027-02-01 08:00 - 2027-02-05 17:30
vacation_entity: input_boolean.urlaub  # optional: an = Urlaub
```

Im Urlaub werden **keine Gewohnheiten gelernt** – Anwesenheitsmuster,
Warmwasser-Zapfprofil und Raummodelle bleiben auf dem Stand vor dem Urlaub und
lernen danach normal weiter. Das Hausmodell (Wärmeverlust) und die
Speicher-Physik lernen weiter, weil sich daran im Urlaub nichts ändert.
Urlaubsstunden zählen nicht zur Ersparnis-Rechnung. Ist das Ende bekannt
(Zeitraum), gilt es als Ankunft: Vorheizen und Warmwasser rechtzeitig davor.
Beim Schalter ist das Ende unbekannt – dann wird erst nach dem Ausschalten
reagiert.

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
Ankunft absehbar → `dhw_min_temp`. Zapfung in Kürze erwartet → `dhw_comfort_temp`
(bei viel Verbrauch so hoch, dass der Speicher danach nicht unter das
Minimum fällt). Sonst → Minimum. Nachgeladen wird rechtzeitig, weil die
Ladegeschwindigkeit bekannt ist. Die Ersparnis ist der eingesparte
Bereitschaftsverlust bei denselben Zapfungen; Zapfungen, bei denen der
Speicher unter `dhw_comfort_temp` gewesen wäre, werden als „zu kalt“ gezählt.
Die wöchentliche Desinfektion des Reglers bleibt unberührt.

## Entitätsnamen

EMS-ESP-Entitäten werden in beiden Schreibweisen erkannt: Kurzname
(`binary_sensor.boiler_burngas`) und langer Name (`binary_sensor.boiler_gas`),
auch mit Präfix (`number.ems_esp_thermostat_hc1_remotetemp`).

## Privates GitHub-Repo als Add-on-Quelle

Home Assistant kann private Repos nur mit Zugangsdaten in der URL laden:
`https://<benutzer>:<token>@github.com/TheInGoF/ems-heatpilot`
(Token mit nur Lese-Rechten auf dieses Repo). Alternativ das Verzeichnis
`ems_heatpilot` per Samba/SSH nach `/addons/` kopieren → erscheint als
„Lokales Add-on“.
