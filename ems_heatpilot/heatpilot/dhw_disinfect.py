"""Warmwasser-Desinfektion (seit 0.36.3): Termin einplanen, Zeit vorschlagen, große Zapfungen danach decken.

Die Legionellenschaltung kam bisher vom Regler (z. B. ES73 sonntags 13:00); der Kessel selbst hat dafür keinen
Timer. Den Termin stellt man in HeatPilot ein (``Config.dhw_disinfect_day``/``_time``). Seit 0.42 löst HeatPilot ihn
selbst aus, wenn kein Regler am Bus ist und das Warmwasser-Soll auf „HeatPilot setzt“ steht (``disinfect_control``,
geschrieben über Home Assistant – EMS-ESP bleibt nur lesend). Im Modell zählt der Termin (``plan_counts``), wenn
HeatPilot ihn auslöst (``Config.disinfect_triggered``, gesetzt von ``disinfect_control``) – oder wenn er bestätigt ist:
In den letzten ``DETECTED_DAYS`` Tagen hat der Regler zu dieser Zeit desinfiziert (``is_disinfection`` – eine
Einmalladung zählt nicht). Dann behandeln Empfehlung und Schatten der Strategie die Stunden danach wie nach einer
erkannten Sonderladung (große Zapfungen höchstens ``SPECIAL_COVER_H`` nach der Ladung sind gedeckt), ohne auf die
Erkennung dieser Woche zu warten. Desinfiziert zu dieser Zeit niemand, zählt der Termin nicht – sonst rechnete die
Strategie mit Wärme, die nicht kommt.

``suggestion`` schlägt einen Termin vor: kurz vor der größten regelmäßigen Zapfung der Woche, damit deren
Wärme ins Wasser geht statt auszukühlen. Alles hier rechnet nur und merkt sich nichts.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .config import FIELDS, WEEKDAYS
from .dhw_watch import HIT_K, SPECIAL_COVER_H
from .guards import LIMITS
from .values import aware, num

if TYPE_CHECKING:
    from .dhw import DhwModel

REGULAR_SHARE = 0.5      # an mindestens so vielen der letzten Wochen (bzw. so oft) gezapft: regelmäßig
SUGGEST_WEEKS = 8        # so viele Wochen zurück zählen (große Zapfungen hält das Modell 60 Tage)
MIN_WEEKS = 4            # Zapfprofil mindestens so lange gelernt, sonst kein Vorschlag
BUFFER_MIN = 15          # Desinfektion so lange vor der Zapfung fertig (und mindestens so lange Ladung)
ROUND_MIN = 15           # Beginn auf so viele Minuten abgerundet
DAY_FROM_MIN, DAY_TO_MIN = 6 * 60, 21 * 60  # Beginn nur 06:00–21:00 (nachts nicht)
DETECTED_DAYS = 8        # so lange bestätigt eine erkannte Desinfektion den Termin zur selben Zeit
SAME_BEFORE_H, SAME_AFTER_H = 3.0, 1.0  # endet sie so kurz nach (vor) einem Termin, ist es dieselbe
# Desinfektionstemperatur vom Kessel nur im Bereich der Einstellung glauben (60–80 °C) – darunter ist es keine
TEMP_RANGE = tuple(float(v) for v in FIELDS["dhw_disinfect_temp"][1:])
DEFAULT_TEMP = 70.0      # wie der Standard von Config.dhw_disinfect_temp – wenn keine Temperatur bekannt ist
PEAK_BELOW_K = 5.0       # Sonderladung bis so nah an die Desinfektionstemperatur: war eine Desinfektion
KEEP = 10                # so viele erkannte Desinfektionen merkt sich das Modell (``DhwModel.disinfects``)
SOURCE_BOILER, SOURCE_SET = "laut Kessel", "eingestellt"
# „7 von 8 Sonntagen“
DAYS_PLURAL = ("Montagen", "Dienstagen", "Mittwochen", "Donnerstagen", "Freitagen", "Samstagen", "Sonntagen")
# Hinweis, solange HeatPilot nicht auslöst (Warmwasser-Soll nicht „HeatPilot setzt“); die Kachel zeigt den Teil vor
# „ – “
NOT_YET = ("HeatPilot löst sie nicht aus – nur bei „Warmwasser-Soll: HeatPilot setzt“ ohne Regler am Bus; solange der "
           "Regler (z. B. ES73) sie macht, dessen Termin eintragen")
NOT_CONFIRMED = "Termin noch nicht bestätigt – kein Regler desinfiziert zu dieser Zeit"


def _de(value: float, digits: int = 0) -> str:
    """Zahl für Texte: deutsches Komma."""
    return f"{value:.{digits}f}".replace(".", ",")


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _planned(cfg) -> bool:
    return getattr(cfg, "disinfect_weekday", lambda: None)() is not None


# ------------------------------------------------------------------ Temperatur
def temperature(boiler_value, cfg) -> tuple[float, str]:
    """(Desinfektionstemperatur, Herkunft): vom Kessel (EMS-ESP ``wwdisinfectiontemp``), sonst die Einstellung.

    Der Kesselwert zählt nur im Bereich der Einstellung (``TEMP_RANGE``); ``temp_note`` sagt sonst, warum.
    """
    v = num(boiler_value)
    if v is not None and TEMP_RANGE[0] <= v <= TEMP_RANGE[1]:
        return v, SOURCE_BOILER
    return float(cfg.dhw_disinfect_temp), SOURCE_SET


def temp_note(boiler_value) -> str | None:
    """Hinweis, wenn der Kessel eine Temperatur außerhalb von ``TEMP_RANGE`` meldet – sonst None."""
    v = None if isinstance(boiler_value, bool) else num(boiler_value)
    if v is None or TEMP_RANGE[0] <= v <= TEMP_RANGE[1]:
        return None
    what = "zu niedrig" if v < TEMP_RANGE[0] else "zu hoch"
    return f"Kessel meldet {_de(v, 0 if v == int(v) else 1)} °C – {what} für eine Desinfektion, es gilt die Einstellung"


# ------------------------------------------------------------------ erkannte Desinfektion bestätigt den Termin
def is_disinfection(flagged: bool, peak: float | None, temp: float | None) -> bool:
    """War eine Sonderladung eine Desinfektion? Der Kessel meldete sie (``wwdisinfecting``), oder der Speicher kam
    bis ``PEAK_BELOW_K`` an die Desinfektionstemperatur heran. Eine Einmalladung oder eine Ladung, die nur weit
    über das Soll ging, ist keine – sie bestätigt keinen Termin."""
    if flagged:
        return True
    return peak is not None and peak >= (DEFAULT_TEMP if temp is None else temp) - PEAK_BELOW_K


def remember(seen: list[str], ended: datetime) -> list[str]:
    """Erkannte Desinfektion (Ende) an die Liste des Modells anhängen, die ältesten über ``KEEP`` fallen weg."""
    return [*seen, ended.isoformat(timespec="minutes")][-KEEP:]


def recent(model: DhwModel, now: datetime) -> list[datetime]:
    """Erkannte Desinfektionen (Ende) der letzten ``DETECTED_DAYS`` Tage, zeitlich sortiert."""
    now, out = aware(now), []
    for text in getattr(model, "disinfects", None) or []:
        try:
            ended = aware(datetime.fromisoformat(text))
        except (TypeError, ValueError):
            continue
        if timedelta(0) <= now - ended <= timedelta(days=DETECTED_DAYS):
            out.append(ended)
    return sorted(out)


def _same_time(cfg, ended: datetime) -> bool:
    """Endete diese Desinfektion zur Zeit eines Termins (höchstens ``SAME_BEFORE_H`` danach, ``SAME_AFTER_H`` davor)?"""
    return bool(cfg.disinfections(ended - timedelta(hours=SAME_BEFORE_H), ended + timedelta(hours=SAME_AFTER_H)))


def plan_counts(model: DhwModel, cfg, now: datetime) -> bool:
    """Zählt der eingestellte Termin im Modell? Löst HeatPilot ihn aus (seit 0.42, ``cfg.disinfect_triggered``), immer;
    sonst nur bestätigt.

    Bestätigt: In den letzten ``DETECTED_DAYS`` Tagen hat der Regler zur Zeit des Termins desinfiziert – dann gilt
    er diese Woche schon vor der Erkennung. Ohne Desinfektion zu dieser Zeit (kein Regler, oder er desinfiziert
    zu anderer Zeit – dann zählt seine Zeit über die erkannte Sonderladung) zählt der Termin nicht. Einmalladungen
    zählen weder dafür noch dagegen (``is_disinfection``).
    """
    if not _planned(cfg):
        return False
    return bool(getattr(cfg, "disinfect_triggered", False)) or any(_same_time(cfg, d) for d in recent(model, now))


def counts_text(model: DhwModel, cfg, now: datetime) -> str | None:
    """Warum der Termin (noch) nicht zählt – None ohne Termin oder wenn er zählt."""
    if not _planned(cfg) or plan_counts(model, cfg, now):
        return None
    other = recent(model, now)
    if not other:
        return NOT_CONFIRMED
    last = other[-1]
    return (f"{NOT_CONFIRMED}; der Regler desinfiziert zu anderer Zeit (zuletzt {WEEKDAYS[last.weekday()]} "
            f"{last:%H:%M}) – es zählt seine Zeit")


# ------------------------------------------------------------------ Termin im Modell
def ready_hours(model: DhwModel, cfg, charge_kw: float | None = None, temp_typ: float | None = None,
                temp_dis: float | None = None) -> float:
    """So lange nach dem Beginn ist der Speicher heiß: Ladedauer wie im Vorschlag (``charge_hours``), mindestens
    ``BUFFER_MIN``. Ohne Angaben vom Komfortwert auf die eingestellte Desinfektionstemperatur."""
    typ = cfg.dhw_comfort_temp if temp_typ is None else temp_typ
    dis = cfg.dhw_disinfect_temp if temp_dis is None else temp_dis
    return max(charge_hours(model, charge_kw, typ, dis)[0], BUFFER_MIN / 60)


def planned_for(model: DhwModel, cfg, now: datetime, hours: float, ready_h: float | None = None) -> list[datetime]:
    """Zeitpunkte, ab denen ein Termin den Speicher heiß hat (Beginn + ``ready_h``, sonst ``ready_hours``) und der
    Zapfungen von ``now`` bis ``now + hours`` decken kann (bis ``SPECIAL_COVER_H`` vorher).

    Leer ohne Termin oder wenn er nicht zählt (``plan_counts``).
    """
    if not _planned(cfg) or not plan_counts(model, cfg, now):
        return []
    ready = timedelta(hours=ready_hours(model, cfg) if ready_h is None else ready_h)
    starts = cfg.disinfections(now - ready - timedelta(hours=SPECIAL_COVER_H + 1), now + timedelta(hours=hours + 1))
    return [p + ready for p in starts]


def covers(ready: list[datetime], slot_start: datetime) -> bool:
    """Deckt ein geplanter Termin eine Zapfung in der Stunde ab ``slot_start``?

    Der Speicher ist spätestens zu Beginn der Stunde heiß (``ready`` aus ``planned_for``: Beginn + Ladedauer), und
    ihr Ende liegt höchstens ``SPECIAL_COVER_H`` danach – wie bei der erkannten Sonderladung, die ab ihrem Ende
    zählt (``DhwModel._learn_slot``). Ein Termin zur Zapfstunde selbst deckt sie also nicht.
    """
    end = slot_start + timedelta(hours=1)
    return any(r <= slot_start and (end - r).total_seconds() <= SPECIAL_COVER_H * 3600 for r in ready)


def shadow_mirror(model: DhwModel, cfg, prev: datetime, now: datetime, temp: float,
                  ready_h: float | None = None) -> float | None:
    """Ist nach ``prev`` bis ``now`` ein geplanter Termin fertig geladen, bekommt der Schatten dessen Wärme.

    Die Strategie desinfiziert zum Termin – ihr Speicher steht nach der Ladedauer (``ready_h``, sonst
    ``ready_hours``) auf ``temp`` und kühlt von dort aus (wie bei einer Sonderladung des Reglers). None ohne
    Termin in diesem Schritt oder wenn er nicht zählt (``plan_counts``).
    """
    if not _planned(cfg):
        return None
    ready = timedelta(hours=ready_hours(model, cfg) if ready_h is None else ready_h)
    hits = [p for p in cfg.disinfections(prev - ready, now - ready) if p > prev - ready]
    return temp if hits and plan_counts(model, cfg, now) else None


# ------------------------------------------------------------------ Vorschlag
def charge_hours(model: DhwModel, charge_kw: float | None, temp_typ: float, temp_dis: float) -> tuple[float, str]:
    """(Ladedauer in h, Herkunft) von ``temp_typ`` auf ``temp_dis``: gelernte Ladegeschwindigkeit, sonst
    Ladekosten × Anstieg / Leistung, sonst die Standard-Ladegeschwindigkeit des Modells."""
    if model.charges >= LIMITS["dhw_min_charges"]:
        return model.charge_hours(temp_typ, temp_dis), "gelernt"
    per_k = model.kwh_per_kelvin()
    if per_k and charge_kw:
        return max(temp_dis - temp_typ, 0.0) * per_k / charge_kw, "Leistung"
    return model.charge_hours(temp_typ, temp_dis), "geschätzt"


def _big_candidates(model: DhwModel, weeks: int, now: datetime | None) -> list[dict]:
    """Große Zapfungen, die an mindestens der Hälfte der letzten ``weeks`` Wochen am selben Wochentag ±1 h kamen."""
    draws = [(datetime.fromisoformat(b[0]), float(b[1])) for b in model.big_draws]
    if not draws:
        return []
    ref = aware(now) if now is not None else max(aware(d) for d, _ in draws)
    draws = [(d, k) for d, k in draws if aware(d) >= ref - timedelta(days=7 * weeks)]
    need = max(2, math.ceil(REGULAR_SHARE * weeks))
    out = []
    for wd, hour in sorted({(d.weekday(), d.hour) for d, _ in draws}):
        same = [(d, k) for d, k in draws if d.weekday() == wd and abs(d.hour - hour) <= 1]
        hits = min(len({d.date() for d, _ in same}), weeks)
        if hits >= need:
            sizes = sorted(k for _, k in same)
            out.append({"weekday": wd, "hour": hour, "draw_k": sizes[len(sizes) // 2], "hits": hits,
                        "weeks": weeks, "share": None, "source": "große Zapfung"})
    # größte zuerst – „regelmäßig“ ist schon das Kriterium oben; bei gleicher Größe die häufigere
    out.sort(key=lambda c: (-c["draw_k"], -c["hits"], c["weekday"], c["hour"]))
    return out


def _slot_candidates(model: DhwModel) -> list[dict]:
    """Ohne regelmäßige große Zapfung: Wochenstunden, in denen meist gezapft wird, nach Abfall × Anteil."""
    out = []
    for wd in range(7):
        for hour in range(24):
            share, draw = model.hit_at(wd, hour), model.slot_draw_at(wd, hour)
            if share >= REGULAR_SHARE and draw >= HIT_K:
                out.append({"weekday": wd, "hour": hour, "draw_k": draw, "hits": None, "weeks": None,
                            "share": share, "source": "Zapfprofil"})
    out.sort(key=lambda c: (-c["draw_k"] * c["share"], c["weekday"], c["hour"]))
    return out


def _start_min(hour: int, charge_h: float) -> int:
    """Beginn in Minuten nach Mitternacht: fertig ``BUFFER_MIN`` vor der Zapfstunde, auf ``ROUND_MIN`` abgerundet."""
    minutes = hour * 60 - charge_h * 60 - BUFFER_MIN
    return math.floor(minutes / ROUND_MIN + 1e-9) * ROUND_MIN


def _why(c: dict) -> str:
    day = WEEKDAYS[c["weekday"]]
    how = (f"{c['hits']} von {c['weeks']} {DAYS_PLURAL[c['weekday']]}" if c["hits"] is not None
           else f"in {c['share'] * 100:.0f} % der Wochen")
    return f"{day} {c['hour']} Uhr zapfst du am meisten ({how}, ≈ {_de(c['draw_k'])} K)"


def suggestion(model: DhwModel, charge_kw: float | None, temp_typ: float, temp_dis: float,
               now: datetime | None = None) -> dict:
    """Terminvorschlag: {"ok": True, "day", "time", "text", …} oder {"ok": False, "reason"}.

    Wochenstunde mit der größten regelmäßigen Zapfung – zuerst große Zapfungen (``_big_candidates``), sonst das
    Zapfprofil (``_slot_candidates``). Beginn = Zapfstunde − Ladedauer (``charge_hours``) − ``BUFFER_MIN``, auf
    ``ROUND_MIN`` abgerundet; nur zwischen 06:00 und 21:00 – liegt er bei der größten Zapfung außerhalb, gilt die
    nächstgrößte, sonst kein Vorschlag. ``kept_kwh``: grob die Wärme der Desinfektion, die bis zur Zapfung im
    Speicher bleibt und ins Wasser geht (``cool`` über die Wartezeit, höchstens so viel, wie die Zapfung zieht).
    """
    weeks = sum(model.profile_n) / max(len(model.profile_n), 1)
    if weeks < MIN_WEEKS:
        return {"ok": False, "reason": f"Zapfprofil erst {int(weeks)} Wochen gelernt – ein Vorschlag braucht "
                                       f"mindestens {MIN_WEEKS} Wochen"}
    charge_h, charge_from = charge_hours(model, charge_kw, temp_typ, temp_dis)
    cands = _big_candidates(model, min(int(weeks), SUGGEST_WEEKS), now) or _slot_candidates(model)
    if not cands:
        return {"ok": False, "reason": "keine regelmäßige Zapfung erkannt – kein Vorschlag"}
    chosen = next(((c, s) for c in cands if DAY_FROM_MIN <= (s := _start_min(c["hour"], charge_h)) <= DAY_TO_MIN),
                  None)
    if chosen is None:
        return {"ok": False, "reason": f"{_why(cands[0])} – die Desinfektion müsste nachts beginnen "
                                       f"(Vorschlag nur {_hhmm(DAY_FROM_MIN)}–{_hhmm(DAY_TO_MIN)})"}
    c, start = chosen
    wait_h = max(c["hour"] - start / 60 - charge_h, 0.0)
    t_draw = model.cool(temp_dis, wait_h)
    per_k = model.kwh_per_kelvin()
    kept = None if per_k is None else round(min(max(t_draw - temp_typ, 0.0), c["draw_k"]) * per_k, 1)
    day, at = WEEKDAYS[c["weekday"]], _hhmm(start)
    return {
        "ok": True, "day": day, "weekday": c["weekday"], "time": at,
        "draw_hour": c["hour"], "draw_k": round(c["draw_k"], 1), "source": c["source"], "hits": c["hits"],
        "weeks": c["weeks"], "share": None if c["share"] is None else round(c["share"], 2),
        "charge_h": round(charge_h, 2), "charge_from": charge_from, "temp": temp_dis,
        "temp_at_draw": round(t_draw, 1), "kept_kwh": kept,
        "text": f"{_why(c)} – Desinfektion {day} {at}, dann nutzt du die {_de(temp_dis)} °C",
        "saving_text": None if kept is None else (
            f"Zur Zapfung hat der Speicher noch ≈ {_de(t_draw)} °C – grob {_de(kept, 1)} kWh der "
            "Desinfektionswärme gehen ins Wasser statt verloren"),
    }
