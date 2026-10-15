# EMS HeatPilot

## Was das Add-on in Version 0.1 macht

**Nur beobachten und lernen.** Es greift nicht in die Heizung ein.

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
| `sensor.heatpilot_ersparnis_prognose_jahr` | geschätzte Ersparnis durch Absenken (kWh/Jahr) |
| `sensor.heatpilot_naechste_ankunft` | vorhergesagte Ankunft |
| `sensor.heatpilot_vorheizen_start` | wann das Vorheizen beginnen müsste |

Hinweis: Diese Sensoren werden vom Add-on gesetzt und sind nach einem
Neustart von Home Assistant bis zum nächsten Takt (max. 5 min) „unbekannt“.

## Optionen

| Option | Bedeutung |
|---|---|
| `outdoor_sensors` | Außenfühler; es wird – wie in deiner Automation – das Minimum genommen. Leer = EMS-ESP-Außenfühler |
| `gas_sensor` | Gaszähler-Entität (m³ oder kWh). Leer = automatisch suchen |
| `weather_entity` | Wetter-Entität für die Vorhersage. Leer = automatisch |
| `gas_kwh_per_m3` | Brennwert × Zustandszahl laut Gasrechnung |
| `gas_price_eur_per_kwh` | für die Euro-Angaben |
| `design_outdoor_temp` | Auslegungs-Außentemperatur (Hamburg: −12 °C) |
| `max_flow_temp` | maximale Vorlauftemperatur |
| `away_temp`, `night_temp`, `night_start`, `night_end` | Strategie für die Ersparnis-Schätzung |
| `backfill_days`, `statistics_backfill_days` | wie viel Historie beim ersten Start übernommen wird |
| `overrides` | Zuordnung erzwingen, z. B. `flow_temp=sensor.boiler_curflowtemp` |

## Privates GitHub-Repo als Add-on-Quelle

Home Assistant kann private Repos nur mit Zugangsdaten in der URL laden:
`https://<benutzer>:<token>@github.com/TheInGoF/ems-heatpilot`
(Token mit nur Lese-Rechten auf dieses Repo). Alternativ das Verzeichnis
`ems_heatpilot` per Samba/SSH nach `/addons/` kopieren → erscheint als
„Lokales Add-on“.
