# EMS HeatPilot

## Was das Add-on in Version 0.3 macht

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
