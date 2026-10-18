# Das Hausmodell – wie HeatPilot Wärmeverlust und Heizlast berechnet

HeatPilot misst das Haus nicht mit Bauplänen oder Norm-Annahmen, sondern lernt es aus
dem, was tatsächlich passiert: wie viel Gas der Kessel verbraucht und wie warm es innen
und außen ist. Diese Seite beschreibt, was dabei gerechnet wird – und wo die Grenzen sind.

## Die Gleichung

Für jede Stunde gilt die Energiebilanz des Hauses:

```
Gas  =  H · (T_innen − T_außen)  +  C · ΔT_innen/Δt  +  b
```

| Größe | Bedeutung | Einheit |
|---|---|---|
| **Gas** | Gasverbrauch der Stunde (= mittlere Gasleistung) | kWh/h = kW |
| **H** | **Wärmeverlust**: wie viel Leistung je Grad Unterschied innen–außen nach draußen geht (Wände, Fenster, Dach, Lüftung) | kW/K (angezeigt in W/K) |
| **C** | **Wärmespeicher**: wie viel Energie nötig ist, um das Haus (vor allem Heizkörper, Luft, Einrichtung) um 1 K zu erwärmen | kWh/K |
| **b** | **Achsabschnitt**: negativ = **Fremdwärme** (Sonne, Personen, Geräte decken einen Teil der Verluste), positiv = Grundlast | kW |

Alle drei Werte (H, C, b) lernt HeatPilot gleichzeitig aus den Stundenwerten.

### Woher die Zahlen kommen

- **Gas:** Zählerstand des gewählten Gaszählers. In m³ gemessen, rechnet HeatPilot mit
  `m³ × Brennwert × Zustandszahl` (beides aus der Gasrechnung, Einstellungen) in kWh um.
  Gas ist **Brennstoff**, nicht Nutzwärme – der Kesselwirkungsgrad steckt in allen Werten mit drin.
- **T_innen:** Mittel aller gewählten Räume (Tado-Ist-Temperaturen) über die Stunde.
  Nicht nach Fläche gewichtet.
- **T_außen:** Mittel über die Stunde; je Zeitpunkt das Minimum der Außenfühler (ein Fühler,
  der weit unter den anderen liegt, gilt als defekt und zählt nicht), ersatzweise die Wetter-Integration.
- **ΔT_innen/Δt:** mittig gebildet – `(T_innen der Folgestunde − T_innen der Vorstunde) / 2`.
  So passt die Temperaturänderung zeitlich zum Gas *dieser* Stunde.

## Wie gelernt wird

Rekursive kleinste Quadrate (RLS) – jede neue Stunde verschiebt die Werte ein Stück, ältere
Stunden verlieren langsam an Gewicht (Vergessensfaktor 0,999: nach rund 700 gelernten
Heizstunden, also etwa 4 Wochen Heizbetrieb, zählt eine Stunde nur noch halb).
So folgt das Modell Jahreszeiten und Änderungen am Haus.

Gelernt wird eine Stunde **nur**, wenn

- die **Heizperiode** läuft: das 24-Stunden-Mittel draußen liegt bei höchstens 15 °C und die
  Stunde selbst auch (warme Sommertage mit kühler Nacht zählen nicht),
- **kein Warmwasser** geladen wurde (sonst steckt Speicherwärme im Gas). Für Stunden aus der
  Langzeitstatistik von Home Assistant (vor HeatPilot) ist das unbekannt. Dort zieht HeatPilot
  den üblichen Warmwasserbedarf dieser Wochentags-Stunde ab, gemessen in Sommerstunden
  (24-h-Mittel draußen ab 17 °C, dann ist alles Gas Warmwasser – inklusive einer
  Legionellenschaltung zur festen Stunde). Ohne mindestens 14 Tage Sommerdaten wird nichts abgezogen.
- der **Gaszähler plausibel** ist – keine Nachträge/Sprünge, keine verpassten Zählungen,
  kein gesunkener Zählerstand (siehe „Ausreißer“ in der Dokumentation),
- Vor- und Folgestunde Messwerte haben (für die mittige Temperaturänderung).

Robust gegen Ausreißer: nach einer Einlernphase von 50 Stunden zählt ein Fehler über dem
4-Fachen des erwarteten Fehlers nur gedeckelt, über dem 15-Fachen gar nicht. Der erwartete Fehler
ist der übliche, erweitert um die Unsicherheit der Vorhersage für diese Stunde.

## Fremdwärme und Heizgrenze

Ist **b** negativ, deckt Fremdwärme einen Teil der Verluste. Daraus folgt die **Heizgrenze**:

```
Heizgrenze (K innen–außen)  =  −b / H
```

Beispiel: H = 150 W/K, b = −1,0 kW → Heizgrenze 6,7 K. Bei 20 °C innen braucht das Haus
also erst unter etwa 13 °C außen überhaupt Gas.

## Heizlast

Die Leistung, die das Haus bei einer Außentemperatur braucht, um eine Innentemperatur zu halten:

```
Heizlast(T_innen, T_außen)  =  max( H · (T_innen − T_außen) + b ,  0 )
```

- **Heizlast jetzt:** mit der aktuellen Innen- und Außentemperatur.
- **Normheizlast / Heizlast am kältesten Tag:** mit 20 °C innen und der
  **Auslegungs-Außentemperatur** aus den Einstellungen (Standard −12 °C).

Beispiel: H = 150 W/K, b = −1,0 kW, −12 °C: `0,150 × 32 − 1,0 = 3,8 kW`.

Der Speicher **C** spielt für die Heizlast keine Rolle (dauerhaft gleiche Temperatur heißt
ΔT_innen = 0). Er bestimmt, wie lange Aufheizen und Auskühlen dauern.

### Unterschied zur Norm-Heizlast (DIN EN 12831)

| | HeatPilot | Norm-Berechnung |
|---|---|---|
| Grundlage | gemessener Gasverbrauch | Bauteile, U-Werte, Luftwechsel |
| Fremdwärme | wird abgezogen (so wie gemessen) | wird nicht abgezogen (Sicherheitsreserve) |
| Leistung | Gasleistung, inkl. Kesselwirkungsgrad | Nutzwärme |
| Innentemperatur | gemessenes Mittel aller Räume | je Raum nach Nutzung |

Die gemessene Heizlast ist deshalb meist **kleiner** als eine Norm-Heizlast. Sie beschreibt,
was das Haus im Betrieb wirklich braucht – genau das, woran sich die Kesselgröße messen lassen muss.

## Trägheit (Auskühlen)

Wie schnell das Haus ohne Heizung auskühlt, lernt ein **eigenes kleines Modell** – nur aus
gasfreien Nachtstunden (22–6 Uhr, in der Stunde und den Nachbarstunden kein Gas, kein
Warmwasser, mindestens 3 K Unterschied innen–außen). Ein Haus kühlt in **zwei Phasen** aus:
Direkt nach dem Abschalten geben Raumluft und Heizkörper ihre Wärme schnell ab, danach
kühlen nur noch die Wände langsam aus. Deshalb gilt mit t = Stunden seit der letzten Stunde mit Gas:

```
ΔT_innen/Δt  =  −k · (T_innen − T_außen)  +  g  −  A · e^(−t / 1,5 h)
Trägheit τ = 1/k (Wände)    Übertemperatur ohne Heizung = g/k    schneller Abfall = A · 1,5 h
```

An echten Daten (Herbst 2026) passte eine einzige Zeitkonstante schlecht: In den ersten
Stunden kühlte das Haus schneller, nach einem Tag ohne Heizung nur noch halb so schnell, wie
sie vorhersagte. Mit zwei Phasen ist der Fehler um 30 % kleiner. Die Übertemperatur ohne Heizung
passt dann auch zur Heizgrenze aus der Gasbilanz.

Gültig ab 30 solchen Stunden. Bis dahin zeigt HeatPilot vorläufig `C/H` aus der Gasbilanz –
das beschreibt aber eher die schnell reagierende Wärme der Heizkörper und der Luft und ist
meist deutlich kürzer als die echte Auskühlzeit der Wände.

## Wofür das Modell verwendet wird

- **Übersicht:** Wärmeverlust, Heizlast jetzt, Normheizlast, Trägheit, Fremdwärme;
  der Graf „Wärmebedarf des Hauses“ (Punkte = Messung, Linie = Modell)
- **Zertifikat:** Heizlast am kältesten Tag, Verhältnis Kessel-Nennleistung zu Heizlast, Trägheit
- **Ersparnis-Schätzung:** Absenken lässt das Haus nach τ auskühlen (nie unter außen plus
  Fremdwärme-Übertemperatur); Wiederaufheizen mit der Kesselleistung abzüglich Verlust, geteilt durch C.
  In Stunden, in denen die Heizung in Wirklichkeit ohnehin aus war, wird die Absenkung nicht
  tiefer – der Rückstand klingt mit der Trägheit ab. Bezug der Prozentangabe ist das Heizgas
  (ohne Warmwasser).
- **Vorlauf-Studie** und **Wochenansicht „Gas wetterbereinigt“:** ziehen den Achsabschnitt b ab,
  damit kältere Stunden nicht allein wegen der Fremdwärme teurer aussehen.
- **Vorheizplan** (Gas fürs Heizen mit Fremdwärme) und **Wochenansicht „Abweichung vom Hausmodell“**

## Leitplanken

Das Modell darf erst etwas empfehlen, wenn es plausibel ist:

| Wert | erlaubter Bereich |
|---|---|
| gelernte Heizstunden | mindestens 72 |
| Wärmeverlust H | 20–1500 W/K |
| Trägheit τ | 2–300 h |
| Achsabschnitt b | −2 bis +1 kW |

Sonst steht in der Übersicht unter „Leitplanken“, was noch fehlt.

## Grenzen

- **Ein Mittelwert für das ganze Haus:** Räume ohne Tado-Thermostat fehlen im Innenmittel;
  kalte Nebenräume ziehen es nach unten.
- **Gas, nicht Wärme:** ein besserer oder schlechterer Kesselwirkungsgrad verschiebt H.
- **Nicht witterungsbereinigt:** Sonne und Wind wirken nur im Mittel (über b), nicht je Stunde.
- **Warmwasser vor Beginn der Messung:** Für Stunden aus der Langzeitstatistik von Home
  Assistant (vor HeatPilot) ist nicht bekannt, wann Warmwasser geladen wurde. Abgezogen wird
  der Sommer-Durchschnitt dieser Wochentags-Stunde. Im Winter braucht Warmwasser etwas mehr
  (kälteres Leitungswasser), ein kleiner Rest bleibt also im Wärmeverlust.
- **Taktender Kessel:** einzelne Stunden streuen stark (mal läuft der Brenner 15 min, mal gar
  nicht). Erst über viele Stunden mittelt sich das heraus – deshalb zeigt der Graf auch Tageswerte.
