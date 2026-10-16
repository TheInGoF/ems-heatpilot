"""Sonne als Wärmequelle: Sonnenstand aus Ort und Uhrzeit, Einstrahlung auf Fassaden.

Ohne Sensoren: Der Sonnenstand wird astronomisch berechnet (genau genug fürs Heizen,
etwa ±1°), die Bewölkung kommt – falls vorhanden – aus der Wetter-Integration.
Je Messpunkt entstehen drei Werte 0…1: wie stark die Sonne gerade auf eine Ost-,
Süd- und Westfassade scheint. Jedes Raummodell lernt daraus selbst, wie viel Wärme
bei ihm ankommt und aus welcher Richtung – ein Nordraum lernt „kaum Sonne“.
"""

from __future__ import annotations

import math

FACADES = (("Ost", 90.0), ("Süd", 180.0), ("West", 270.0))


def sun_position(ts: float, lat: float, lon: float) -> tuple[float, float]:
    """Sonnenhöhe und Azimut (Grad, Azimut von Nord im Uhrzeigersinn) zum Unix-Zeitpunkt."""
    d = ts / 86400.0 + 2440587.5 - 2451545.0  # Tage seit J2000
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians((gmst * 15 + lon) % 360) - ra
    phi = math.radians(lat)
    elev = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(ha))
    az = math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(phi) - math.tan(dec) * math.cos(phi)))
    return math.degrees(elev), (az + 180.0) % 360


def facade_inputs(ts: float, lat: float, lon: float, cloud_pct: float | None) -> tuple[float, float, float]:
    """Einstrahlung (0…1) auf senkrechte Ost-, Süd- und Westfenster."""
    elev, az = sun_position(ts, lat, lon)
    if elev <= 0.5:
        return 0.0, 0.0, 0.0
    clear = math.sin(math.radians(elev)) ** 0.4  # tief stehende Sonne: lange Luftstrecke
    # Bewölkung: dichte Wolken halten das meiste ab (Kasten/Czeplak); unbekannt -> halbe Sonne
    cc = 0.5 if cloud_pct is None else min(max(cloud_pct, 0.0), 100.0) / 100
    sky = 1 - 0.75 * cc ** 3.4
    out = []
    for _, facing in FACADES:
        inc = math.cos(math.radians(elev)) * math.cos(math.radians(az - facing))
        out.append(round(max(inc, 0.0) * clear * sky, 4))
    return tuple(out)


def describe(gains: tuple[float, float, float], threshold: float = 0.05) -> str:
    """Klartext: woher die Sonne den Raum heizt – „Süd 0,6 K/h“ oder „kaum Sonne“."""
    best = max(range(3), key=lambda i: gains[i])
    if gains[best] < threshold:
        return "kaum Sonne"
    parts = [f"{FACADES[i][0]} {gains[i]:.1f}".replace(".", ",") for i in range(3) if gains[i] >= threshold]
    return " · ".join(parts) + " K/h bei voller Sonne"
