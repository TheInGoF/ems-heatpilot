"""Leitplanken: Das System darf sparen, aber nicht übertreiben.

Grundsätze (gelten für Empfehlungen jetzt und für jede spätere Steuerung):

1. **Komfort ist eine harte Grenze, kein Teil der Rechnung.** Tado-Sollwerte bei
   Anwesenheit und Warmwasser zur Zapfzeit müssen erreicht werden. Eine Ersparnis,
   die das verletzt, wird als „zu kalt“ ausgewiesen statt als Erfolg.
2. **Gezählt wird am Gaszähler, für das ganze Haus.** Verluste von einem Bereich in
   den anderen zu schieben (z. B. den Heizraum heizen, damit der Speicher langsamer
   auskühlt) spart nichts und wird nicht als Ersparnis gewertet.
3. **Feste Untergrenzen.** Nie unter Abwesenheits-/Nachttemperatur, Warmwasser nie
   unter dem Minimum – „gar nicht heizen“ ist keine erlaubte Strategie.
4. **Nur plausible Modelle dürfen empfehlen.** Liegt ein gelernter Wert außerhalb
   physikalisch sinnvoller Grenzen oder ist noch zu wenig gelernt, gibt es keine
   Empfehlung – dann bleibt alles wie es ist.
5. **Kleine Schritte.** Selbstkorrekturen (z. B. mehr Warmwasser-Reserve nach einer
   zu kalten Zapfung) sind gedeckelt und bilden sich langsam zurück.
"""

from __future__ import annotations

import math

# (min, max) physikalisch sinnvoller Bereiche
LIMITS = {
    "house_h_w_per_k": (20.0, 1500.0),       # Wärmeverlust Einfamilienhaus
    "house_tau_h": (2.0, 300.0),             # Trägheit
    # Achsabschnitt der Gasbilanz: negativ = Fremdwärme (Sonne, Personen, Geräte), positiv =
    # Grundlast (Warmwasser-Mittel, Bereitschaft). Erklärt das Modell den Verbrauch überwiegend
    # damit, hat es das Haus nicht verstanden
    "house_offset_kw": (-2.0, 1.0),
    "house_min_updates": 72,                 # mind. 3 Tage Heizbetrieb gelernt
    "room_tau_h": (2.0, 300.0),
    "room_heat_gain": (1e-4, 1.0),
    "room_min_updates": 96,                  # mind. 2 Tage à 30 min
    # Vorlauf-Verhältnis (a+f)/b: 0,3 ≈ 130 °C, 3 ≈ 32 °C, 6 ≈ 26 °C Vorlauf für 21 °C bei −12 °C
    "room_static_gain": (0.3, 6.0),  # 0.46.5: große Heizkörper bei kleiner Heizlast (vorher 3)
    # Fußbodenheizung (seit 0.45): große Heizfläche, niedriger Vorlauf – (a+f)/b bis 6 ≈ 26 °C Vorlauf
    "room_static_gain_floor": (0.3, 6.0),
    "room_min_excitation": 15.0,             # K·h Heizbetrieb gesehen
    "room_min_free_k": -1.0,                 # Endtemperatur ohne Heizung mind. außen − 1 K
    "room_tau_uncertainty": 0.5,             # relative Unsicherheit der Auskühlrate
    "dhw_ambient": (5.0, 35.0),              # Aufstellraum des Speichers
    "dhw_cooling_kh_at_55": (0.05, 5.0),
    "dhw_kwh_per_k": (0.03, 1.5),            # 50–1000 l Speicher inkl. Wirkungsgrad
    "dhw_charge_rate_kh": (3.0, 300.0),
    "dhw_min_charges": 5,
}

DHW_MARGIN_STEP = 2.0      # K mehr Reserve nach einer zu kalten Zapfung
DHW_MARGIN_MAX = 10.0      # nie mehr als 10 K Reserve aufschlagen
DHW_MARGIN_DECAY = 0.25    # K pro Tag ohne Problem wieder abbauen


def _within(name: str, value: float | None, reasons: list[str], label: str) -> None:
    lo, hi = LIMITS[name]
    if value is None:
        reasons.append(f"{label} noch nicht gemessen")
    elif not (lo <= value <= hi):
        reasons.append(f"{label} {value:.3g} außerhalb {lo:g}–{hi:g}")


def check_house(house) -> dict:
    reasons: list[str] = []
    if house.rls.updates < LIMITS["house_min_updates"]:
        reasons.append(f"erst {house.rls.updates} Stunden gelernt")
    _within("house_h_w_per_k", house.h_kw_per_k * 1000, reasons, "Wärmeverlust W/K")
    _within("house_tau_h", house.tau_h, reasons, "Trägheit h")
    _within("house_offset_kw", house.offset_kw, reasons, "Grundlast/Fremdwärme kW")
    return {"ok": not reasons, "reasons": reasons}


def check_room(room) -> dict:
    reasons: list[str] = []
    if room.rls.updates < LIMITS["room_min_updates"]:
        reasons.append(f"erst {room.rls.updates} Lernschritte")
    _within("room_tau_h", room.tau_h, reasons, "Zeitkonstante h")
    _within("room_heat_gain", room.rls.theta[0], reasons, "Heizwirkung")
    gain = "room_static_gain_floor" if getattr(room, "floor", False) else "room_static_gain"
    _within(gain, (room.a + room.f) / room.b, reasons, "Heizwirkung/Auskühlen")
    if getattr(room, "excitation", None) is not None and room.excitation < LIMITS["room_min_excitation"]:
        reasons.append(f"zu wenig Heizbetrieb gelernt ({room.excitation:.0f} K·h)")
    if room.c / room.b < LIMITS["room_min_free_k"]:
        reasons.append(f"ohne Heizung rechnerisch {room.c / room.b:.1f} K unter außen – unplausibel")
    bi = 2 if room.floor else 1
    err = room.rls.err_sq_ewma
    if err is not None and room.rls.P[bi][bi] > 0:
        rel = math.sqrt(room.rls.P[bi][bi] * err) / room.b
        if rel > LIMITS["room_tau_uncertainty"]:
            reasons.append(f"Zeitkonstante noch unsicher (±{rel * 100:.0f} %)")
    return {"ok": not reasons, "reasons": reasons}


def check_dhw(dhw) -> dict:
    reasons: list[str] = []
    if dhw.charges < LIMITS["dhw_min_charges"]:
        reasons.append(f"erst {dhw.charges} Ladungen gelernt")
    _within("dhw_ambient", dhw.ambient, reasons, "Umgebung °C")
    _within("dhw_cooling_kh_at_55", dhw.cooling_kh(55.0), reasons, "Auskühlen K/h")
    _within("dhw_kwh_per_k", dhw.kwh_per_kelvin(), reasons, "Ladekosten kWh/K")
    _within("dhw_charge_rate_kh", dhw.charge_rate_kh, reasons, "Nachladen K/h")
    return {"ok": not reasons, "reasons": reasons}
