"""Schimmelschutz (seit 0.75): zu feuchte Räume nicht tief absenken – und als Hinweis zeigen, nicht aufs Handy melden.

Schimmel wächst, wo die Luft an der kältesten Wandstelle (Außenecke, hinter Schränken) über 80 % Feuchte hat. Wie viel
Raumfeuchte das ist, hängt von draußen ab – je kälter, desto kälter die Ecke. Faustregel (DIN 4108-2 nachgerechnet,
Raum um 20 °C): Grenze = 50 % + 1 % je Grad Außentemperatur, zwischen ``LO`` und ``HI`` (``limit``) – bei +10 °C
60 %, bei 0 °C 50 %, bei −10 °C 40 %.

* **Feuchte je Raum** (``step``): aus dem eigenen Fühler (Thermostat bzw. „Feuchte“ unter Einstellungen → Räume),
  geglättet mit der Zeitkonstante ``TAU_S`` – Kochen und Duschen sind kurze Spitzen. Ohne eigenen Fühler gilt der
  Hauswert, der Median der Räume mit Fühler (``house``; Shelly-Thermostate messen keine Feuchte). Ohne jeden Fühler
  gibt es keinen Schutz – dann gelten die festen Werte.
* **Zu feucht**: geglättet über der Grenze; zurück erst ``HYST`` darunter (kein Hin und Her). Steht im Zustand der
  Regelschleife (``damp``) – nur bei „HeatPilot steuert“.
* **Absenken** (``guard``): Nacht und Abwesenheit senken einen zu feuchten Raum höchstens ``SETBACK_K`` unter Komfort
  – wärmere Luft hat weniger relative Feuchte, die Wände bleiben wärmer. Im Urlaub nicht (dann entsteht keine
  Feuchte). Eigene Betriebsarten (Manuell, Aus, Boost) gehen vor. Die Vorschau rechnet ebenso (``room_modes.planned``).
* **Hinweis** (``hints``): „Wohnzimmer 62 % Luftfeuchte – bitte lüften (Grenze heute 58 %)“ – nur in
  ``sensor.heatpilot_hinweise`` (``card_bridge``) und auf der HeatPilot-Karte „Hinweise“, keine Meldung aufs Handy.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime

LO, HI = 40.0, 60.0      # Grenze der Raumfeuchte in %, je nach Außentemperatur dazwischen
BASE, PER_K = 50.0, 1.0  # … 50 % bei 0 °C, je Grad Außentemperatur 1 % mehr bzw. weniger
HYST = 3.0               # zurück erst so viele Prozentpunkte unter der Grenze
TAU_S = 3600             # Glättung: Zeitkonstante (Kochen, Duschen sind Spitzen)
GAP_S = 1800             # längere Lücke: neu beginnen statt glätten
SETBACK_K = 1.0          # zu feucht: Nacht und Abwesenheit höchstens so weit unter Komfort
PHASES = ("nacht", "abwesend")


def limit(t_out: float | None) -> float:
    """Höchste Raumfeuchte in %, die bei dieser Außentemperatur unkritisch ist (ohne Außenfühler: 0 °C angenommen)."""
    return min(max(BASE + PER_K * (0.0 if t_out is None else t_out), LO), HI)


def house(rooms) -> float | None:
    """Hauswert: Median der Feuchte aller Räume mit Fühler (``rooms`` wie ``Engine.snapshot``: Raum, Ist, Soll, Ventil,
    Feuchte, …) – None ohne Fühler."""
    values = [r[4] for r in rooms if len(r) > 4 and r[4] is not None and 0 < r[4] <= 100]
    return statistics.median(values) if values else None


def step(st: dict, comfort: float, d, reading, house_rh: float | None, t_out: float | None, ts: int):
    """Im Takt der Regelschleife: Feuchte des Raums glätten (eigener Fühler, sonst Hauswert), „zu feucht“ merken
    (``st["damp"]``) und den Wunschzustand ``d`` schützen (``guard``)."""
    own = reading[4] if reading is not None and len(reading) > 4 and reading[4] is not None else None
    value = own if own is not None else house_rh
    if value is None:
        st.pop("rh", None)
        st.pop("damp", None)
        return d
    prev = st.get("rh")  # [Wert, Zeit, eigener Fühler]
    if prev and 0 < ts - prev[1] <= GAP_S and bool(prev[2]) == (own is not None):
        value = prev[0] + (1 - math.exp(-(ts - prev[1]) / TAU_S)) * (value - prev[0])
    st["rh"] = [round(value, 2), ts, own is not None]
    lim, was = limit(t_out), st.get("damp")
    if value > lim or (was and value > lim - HYST):
        st["damp"] = {"rh": round(value), "limit": round(lim), "own": own is not None,
                      "since": (was or {}).get("since", ts)}
    else:
        st.pop("damp", None)
    return guard(st, comfort, d)


def guard(st: dict, comfort: float | None, d):
    """Wunschzustand mit Schimmelschutz: zu feucht → Nacht und Abwesend höchstens ``SETBACK_K`` unter Komfort."""
    damp = st.get("damp")
    if not damp or comfort is None or d.phase not in PHASES or d.setpoint >= comfort - SETBACK_K:
        return d
    return d._replace(setpoint=comfort - SETBACK_K, reason=f"{d.reason} · Feuchte {damp['rh']} % (Grenze "
                                                           f"{damp['limit']} %): nur {SETBACK_K:g} K abgesenkt")


def hints(loop, rooms) -> list[dict]:
    """Lüften-Hinweise für ``sensor.heatpilot_hinweise``: je zu feuchtem Raum mit eigenem Fühler einer, die Räume mit
    dem Hauswert zusammen in einem."""
    state = getattr(loop, "state", None) or {}
    out, by_house = [], []
    for room in rooms:
        damp = (state.get(room.climate) or {}).get("damp")
        if not damp:
            continue
        if not damp.get("own"):
            by_house.append((room, damp))
            continue
        out.append(_hint(room.name, damp, f"{room.name}: {damp['rh']} % Luftfeuchte", room.managed))
    if by_house:
        d, names = by_house[0][1], ", ".join(r.name for r, _ in by_house)
        out.append(_hint(None, d, f"Luftfeuchte im Haus {d['rh']} % ({names} ohne eigenen Fühler)",
                         any(r.managed for r, _ in by_house)))
    return out


def _hint(room: str | None, damp: dict, what: str, guarded: bool) -> dict:
    """Ein Lüften-Hinweis; ``note`` sagt, was HeatPilot dagegen tut (nur bei Räumen, die mitmachen)."""
    return {"kind": "lueften", "group": "lueften", "room": room, "rh": damp["rh"], "limit": damp["limit"],
            "since": _iso(damp["since"]),
            "text": f"{what} – bitte lüften (Grenze heute {damp['limit']} %)",
            "note": f"nachts und bei Abwesenheit nur {SETBACK_K:g} K abgesenkt" if guarded else None}


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes") if ts else None
