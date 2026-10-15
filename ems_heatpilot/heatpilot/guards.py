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

# (min, max) physikalisch sinnvoller Bereiche
LIMITS = {
    "house_h_w_per_k": (20.0, 1500.0),       # Wärmeverlust Einfamilienhaus
    "house_tau_h": (2.0, 300.0),             # Trägheit
    # Grundlast (Warmwasser-Mittel, Bereitschaft): erklärt das Modell den Verbrauch
    # überwiegend damit, hat es das Haus nicht verstanden
    "house_base_kw": (0.0, 1.0),
    "house_min_updates": 72,                 # mind. 3 Tage Heizbetrieb gelernt
    "room_tau_h": (2.0, 300.0),
    "room_heat_gain": (1e-4, 1.0),
    "room_min_updates": 96,                  # mind. 2 Tage à 30 min
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
    if value is None or not (lo <= value <= hi):
        shown = "–" if value is None else f"{value:.3g}"
        reasons.append(f"{label} {shown} außerhalb {lo:g}–{hi:g}")


def check_house(house) -> dict:
    reasons: list[str] = []
    if house.rls.updates < LIMITS["house_min_updates"]:
        reasons.append(f"erst {house.rls.updates} Stunden gelernt")
    _within("house_h_w_per_k", house.h_kw_per_k * 1000, reasons, "Wärmeverlust W/K")
    _within("house_tau_h", house.tau_h, reasons, "Trägheit h")
    _within("house_base_kw", house.base_kw, reasons, "Grundlast kW")
    return {"ok": not reasons, "reasons": reasons}


def check_room(room) -> dict:
    reasons: list[str] = []
    if room.rls.updates < LIMITS["room_min_updates"]:
        reasons.append(f"erst {room.rls.updates} Lernschritte")
    _within("room_tau_h", room.tau_h, reasons, "Zeitkonstante h")
    _within("room_heat_gain", room.rls.theta[0], reasons, "Heizwirkung")
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
