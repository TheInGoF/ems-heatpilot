"""Warmwasser-Ziel (seit 0.46): welche Speichertemperatur jetzt gehalten werden soll – und welches Soll dafür.

Der Kessel lädt erst, wenn der Speicher um die Einschalthysterese unter das Soll fällt (EMS-ESP ``dhw_hyston``,
Rolle ``dhw_hyst_on``, z. B. −5 K). Seit 0.46.2: Die Komforttemperatur der Einstellungen IST das Soll (Speicher
zwischen Komfort − Hysterese und Komfort, wie bei einer üblichen Automation); nur das Minimum bekommt die Hysterese
dazu, damit der Speicher beim Fallenlassen nie darunter fällt (Soll = Minimum + |Hysterese|). Erwartete große
Zapfung: Soll = max(Komfort, Minimum + Bedarf) – während des Zapfens lädt der Kessel ohnehin nach. Auf 1 K
aufgerundet, höchstens das Maximum. Meldet der Kessel keine Hysterese, gilt ``HYST_ASSUMED_K`` – im Grund genannt.

Zustände (wie eine übliche Automation: fallen lassen, wenn niemand Wasser braucht):

* Urlaub bzw. niemand zu Hause und die Ankunft weiter weg als Aufheizdauer + ``BUFFER_H`` → Minimum
  (Ankunft unbekannt, weil die Gewohnheit noch lernt: das heutige Soll bleibt),
* Nacht laut Nachtplan (eingestellt bzw. gelernt, ``Config.is_night_at``) → Minimum, bis die Ladung gerade noch
  ``HEAT_GAP_H`` vor dem frühesten Heizstart der Räume fertig wird (``heat_start``, seit 0.68; ohne Heizstart vor
  dem Nachtende). Die Ladedauer rechnet ab der aktuellen Speichertemperatur (``temp``), sonst ab dem Minimum: Startet
  das Vorheizen um 4:13 und braucht das Wasser 23 min, lädt der Speicher ab 3:45 – nicht mehr zu einer festen Zeit,
* sonst (jemand zu Hause und wach, kurz vor Ankunft bzw. Aufstehen) → Komfort + gelernte Reserve; erwartete große
  Zapfung → max(Komfort, Minimum + Bedarf).

Aufheizdauer = (Komfort-Soll − Minimum) / gelernte Laderate + ``BUFFER_H``; Zapfungen sieht die Strategie mindestens
``DRAW_AHEAD_H`` voraus. Dasselbe Ergebnis nutzen Übersicht, Vorschau, Weg W
(``dhw_control`` schreibt ``target``), der Schatten der Ersparnis (lädt unter ``hold`` bis ``target``) und die
Selbstkorrektur (``margin_bound``) – die Hysterese wird nur hier berücksichtigt (der Schatten nimmt sie als
``target − hold`` für „zu kalt“: unter Komfort − Hysterese).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .dhw import HIT_THRESHOLD, slot_of
from .dhw_disinfect import planned_for
from .values import num

HYST_ASSUMED_K = 5.0  # Einschalthysterese, wenn der Kessel keine meldet
HYST_HINT_K = 5.0     # größer: Hinweis, dass eine kleinere Hysterese Bereitschaftsverlust spart
BUFFER_H = 0.5        # Puffer zur Aufheizdauer vor Ankunft (0.46.2: 1 h war zu früh)
HEAT_GAP_H = 5 / 60   # nachts: Warmwasser so lange vor dem Heizstart fertig (0.68)
DRAW_AHEAD_H = 1.0    # Zapfungen mindestens so weit voraus sehen (0.46.2)
AWAY, VACATION, NIGHT, OPEN = "niemand zu Hause", "Urlaub", "Nacht", "offen"
HOME, UNKNOWN = "jemand zu Hause", "Anwesenheit unbekannt"


def hysteresis(value) -> tuple[float, bool]:
    """(Einschalthysterese als Betrag in K, angenommen?) – aus dem Kesselwert (z. B. −10), sonst ``HYST_ASSUMED_K``."""
    v = num(value)
    return (HYST_ASSUMED_K, True) if v is None else (abs(v), False)


def setpoint(hold: float, hyst: float, cfg) -> int:
    """Soll, damit der Speicher über ``hold`` bleibt: hold + Hysterese, auf 1 K aufgerundet, höchstens Maximum."""
    return capped(hold + hyst, cfg)


def capped(value: float, cfg) -> int:
    """Soll auf 1 K aufgerundet, höchstens das Maximum der Einstellungen."""
    return min(math.ceil(value - 1e-6), math.floor(cfg.dhw_max_temp + 1e-9))


def hyst_hint(hyst: float, assumed: bool) -> str | None:
    """Kurzer Hinweis, wenn die Hysterese am Kessel größer als ``HYST_HINT_K`` ist (HeatPilot schreibt sie nicht)."""
    if assumed or hyst <= HYST_HINT_K + 1e-9:
        return None
    return (f"Hysterese am Kessel {_n(hyst)} K: Mit {_n(HYST_HINT_K)} K schwankt der Speicher weniger und verliert "
            "weniger Wärme – am Kessel einstellen, HeatPilot ändert sie nicht.")


def _n(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}".replace(".", ",")


def _hours(now: datetime, then: datetime) -> float:
    return (then - now).total_seconds() / 3600


def _night(cfg, now: datetime) -> bool:
    is_night = getattr(cfg, "is_night_at", None)
    return bool(is_night and is_night(now))


def heat_start(desireds, now: datetime) -> datetime | None:
    """Frühester Heizstart der Räume (Beginn des Vorheizens) aus ihren Wunschzuständen – ``now``, wenn einer schon
    vorheizt. Nur nachts von Belang (``_drop`` nimmt ihn, wenn er vor dem Nachtende liegt)."""
    return min((now if d.phase == "vorheizen" else d.until for d in desireds
                if d.phase in ("nacht", "vorheizen") and d.until is not None), default=None)


def _ready_by(cfg, now: datetime, start: datetime | None) -> datetime:
    """Bis wann der Speicher nachts geladen sein soll: ``HEAT_GAP_H`` vor dem Heizstart bzw. dem Nachtende."""
    end = cfg.night_end_at(now)
    return (start if start is not None and start <= end else end) - timedelta(hours=HEAT_GAP_H)


def _drop(now, someone_home, next_arrival, cfg, horizon: float, unknown_from, vacation: bool,
          charge_h: float, start: datetime | None) -> str | None:
    """Warum der Speicher bis zum Minimum fallen darf (Urlaub, niemand zu Hause, Nacht) – ``OPEN``, wenn die
    Ankunft unbekannt ist; None: Warmwasser kann innerhalb der Aufheizdauer gebraucht werden. Nachts reicht die
    Ladedauer ``charge_h`` bis kurz vor den Heizstart ``start``."""
    if someone_home is False and (next_arrival is None or _hours(now, next_arrival) > horizon):
        if unknown_from is not None and _hours(now, unknown_from) <= horizon:
            return OPEN
        return VACATION if vacation else AWAY
    if _night(cfg, now) and _hours(now, _ready_by(cfg, now, start)) > charge_h:
        return NIGHT
    return None


def _base_why(now, someone_home, cfg) -> str:
    if someone_home is False:
        return "vor der Ankunft aufheizen"
    if _night(cfg, now):
        return "vor dem Aufstehen aufheizen"
    return HOME if someone_home else UNKNOWN


def _draws(model, now: datetime, cfg, horizon: float, disinfect_h) -> tuple[float, float, float | None]:
    """(erwarteter Abfall, höchste Zapf-Wahrscheinlichkeit, große Zapfung) im Horizont."""
    draw = model.expected_draw(now, horizon)
    chance = max(model.hit_at(slot // 24, slot % 24)
                 for slot in (slot_of(now + timedelta(hours=h)) for h in range(int(horizon) + 1)))
    # geplanter Desinfektionstermin (0.36.3): große Zapfungen danach deckt dessen Wärme
    big = model.big_draw_expected(now, horizon, planned_for(model, cfg, now, horizon, disinfect_h))
    return draw, chance, big


def _draw_why(draw: float, chance: float, big: float | None) -> str:
    return (f"große Zapfung üblich (≈{big:.0f} K)" if big else
            f"Zapfung erwartet (≈{draw:.0f} K)" if draw >= 1.0 else
            f"hier wird oft gezapft (in {chance * 100:.0f} % der Fälle)")


def _out(target: int, h: float, assumed: bool, why: str, drop: bool, *, expects: bool = False,
         bound: bool = False) -> dict:
    hold = round(target - h, 1)
    temp = f"Speicher darf bis {_n(hold)} °C fallen" if drop else f"Speicher {_n(hold)}–{target} °C"
    note = f" (Hysterese {_n(h)} K angenommen)" if assumed else ""
    return {"target": target, "hold": hold, "hyst": h, "hyst_assumed": assumed, "why": why, "drop": drop,
            "reason": f"{why} – {temp}{note}", "expects_draw": expects, "margin_bound": bound}


def recommend(model, now: datetime, someone_home: bool | None, next_arrival: datetime | None, cfg, *,
              unknown_from: datetime | None = None, current_set: float | None = None,
              disinfect_h: float | None = None, hyst=None, vacation: bool = False, temp: float | None = None,
              heat_start: datetime | None = None) -> dict:
    """Empfohlenes Warmwasser-Soll jetzt (``target``) und die Temperatur, über der der Speicher bleibt (``hold``).

    ``hyst``: Einschalthysterese des Kessels (``dhw_hyst_on``, Vorzeichen egal), None = unbekannt.
    ``unknown_from``/``current_set``: Ist offen, ob jemand heimkommt, bleibt das heutige Soll.
    ``disinfect_h``: Ladedauer der Desinfektion zum Termin (``dhw_disinfect.ready_hours``).
    ``temp``/``heat_start`` (0.68): Speichertemperatur jetzt und frühester Heizstart – nachts fertig kurz davor.
    """
    h, assumed = hysteresis(hyst)
    comfort = cfg.dhw_comfort_temp + model.comfort_margin
    rate = max(model.charge_rate_kh, 5.0)
    horizon = (capped(comfort, cfg) - cfg.dhw_min_temp) / rate + BUFFER_H
    charge_h = max(capped(comfort, cfg) - (cfg.dhw_min_temp if temp is None else temp), 0.0) / rate
    drop = _drop(now, someone_home, next_arrival, cfg, horizon, unknown_from, vacation, charge_h, heat_start)
    if drop == OPEN:
        keep = round(current_set) if current_set is not None else capped(cfg.dhw_comfort_temp, cfg)
        return _out(keep, h, assumed, "Ankunft unbekannt (Gewohnheit lernt noch), Soll bleibt", False)
    if drop:
        return _out(setpoint(cfg.dhw_min_temp, h, cfg), h, assumed, drop, True)
    # Zapfungen mindestens die nächste Stunde voraus sehen (wie bis 0.46.1, als der Puffer 1 h war) – sonst kennt
    # die Empfehlung um 6:55 die Dusche um 7:00 nicht, wenn der Speicher schnell lädt
    draw, chance, big = _draws(model, now, cfg, max(horizon, DRAW_AHEAD_H), disinfect_h)
    expects = draw >= 1.0 or chance >= HIT_THRESHOLD or bool(big)
    # bei viel Verbrauch so hoch, dass der Speicher danach nicht unter das Minimum fällt
    need = max(draw, big or 0.0) if expects else 0.0
    target = capped(max(comfort, cfg.dhw_min_temp + need), cfg)  # Komfort ist das Soll (0.46.2)
    # margin_bound: mehr Reserve hebt dieses Ziel an – nicht, wenn Minimum + Bedarf es bestimmt oder am Maximum
    bound = comfort >= cfg.dhw_min_temp + need and target < math.floor(cfg.dhw_max_temp + 1e-9)
    base = _base_why(now, someone_home, cfg)  # vor Ankunft/Aufstehen sagt das Aufheizen mehr als die Zapfung
    why = _draw_why(draw, chance, big) if expects and (base in (HOME, UNKNOWN) or not bound) else base
    return _out(target, h, assumed, why, False, expects=expects, bound=bound)
