"""Vorlauf gegen Takten: bringt ein höherer Vorlauf etwas – oder ist er Verschwendung?

Ein zu großer Kessel taktet, weil die Heizkörper seine kleinste Leistung nicht abnehmen.
Mehr Raumtemperatur ändert daran nichts – nur ein höherer Vorlauf erhöht, was die
Heizkörper abgeben. Dafür steigt der Rücklauf, und über ~55 °C kondensiert ein
Brennwertkessel kaum noch. Was überwiegt, misst HeatPilot an deinen eigenen Stunden:

* nur Beharrungsstunden (innen höchstens 0,3 K Änderung) – Aufheizstunden würden
  höheren Vorlauf ungerecht teuer aussehen lassen, weil das Gas dann in die Wände geht,
* Heizstunden (ohne Warmwasserladung) nach Außentemperatur gruppiert – nur Stunden
  bei ähnlicher Außentemperatur werden miteinander verglichen,
* darin nach Vorlauf (Mittel, solange der Brenner fürs Heizen lief) in 5-K-Stufen,
* je Stufe: Starts pro Stunde, Laufzeit je Start, Rücklauf und **Gas je Kelvin-Stunde**
  ((Gas − Achsabschnitt des Hausmodells) / (innen − außen)). Der Achsabschnitt (meist negativ:
  Fremdwärme) wird abgezogen, sonst sähe die kältere Stunde – in der der Regler auch den Vorlauf
  höher fährt – schon allein deshalb teurer aus. Ohne plausibles Hausmodell bleibt er 0 und das
  Urteil gilt als vorläufig.

Nur Beobachtung: HeatPilot stellt nichts um. Gibt es zu wenig Vielfalt, schlägt es einen
Versuch vor, den du selbst machst – und wertet ihn dann aus.
"""

from __future__ import annotations

import time
from collections import defaultdict

OUT_BANDS = ((-30.0, 0.0, "unter 0 °C"), (0.0, 5.0, "0 bis 5 °C"), (5.0, 10.0, "5 bis 10 °C"), (10.0, 15.0, "10 bis 15 °C"))
FLOW_STEP = 5
MIN_HOURS = 20          # so viele Stunden je Stufe, bevor sie zählt
MIN_DIFF = 0.04         # ab 4 % Unterschied beim Gas je K·h gilt ein Ergebnis
CONDENSING_RET = 55.0   # darüber kaum Brennwertnutzen
WINDOW_DAYS = 90
STEADY_K = 0.3          # nur Stunden, in denen sich innen kaum etwas ändert (Beharrung)


def _band(t_out: float):
    for lo, hi, name in OUT_BANDS:
        if lo <= t_out < hi:
            return name
    return None


def hours_table(store, now_ts: int | None = None, offset_kw: float = 0.0) -> list[dict]:
    """Heizstunden mit Vorlauf, Rücklauf, Starts und Gas je K·h."""
    now_ts = now_ts or int(time.time())
    since = now_ts - WINDOW_DAYS * 86400
    hourly = {r["ts"]: r for r in store.query(
        "SELECT ts, t_in, t_out, gas_kwh, dhw_frac FROM hourly WHERE ts >= ? AND gas_kwh IS NOT NULL "
        "AND t_in IS NOT NULL AND t_out IS NOT NULL", (since,))}
    per_hour: dict[int, dict] = defaultdict(lambda: {"flow": [], "ret": [], "on": 0, "n": 0, "dhw": 0})
    for r in store.query("SELECT ts, flow, ret, burner_on, dhw FROM samples WHERE ts >= ?", (since,)):
        h = per_hour[r["ts"] - r["ts"] % 3600]
        h["n"] += 1
        if r["dhw"]:
            h["dhw"] += 1
        elif r["burner_on"]:
            h["on"] += 1
            if r["flow"] is not None:
                h["flow"].append(r["flow"])
            if r["ret"] is not None:
                h["ret"].append(r["ret"])
    starts: dict[int, int] = defaultdict(int)
    prev = None
    for r in store.query("SELECT ts, state FROM burner_events WHERE ts >= ? ORDER BY ts", (since,)):
        if r["state"] == 1 and prev != 1:
            starts[r["ts"] - r["ts"] % 3600] += 1
        prev = r["state"]
    out = []
    for ts, hr in hourly.items():
        s = per_hour.get(ts)
        if not s or not s["n"] or not s["flow"] or hr["t_out"] > 15 or s["dhw"]:
            continue
        if (hr["dhw_frac"] or 0) > 0:  # wie beim Lernen: keine Stunde mit Warmwasserladung
            continue
        dt = hr["t_in"] - hr["t_out"]
        if dt < 3:
            continue
        before = hourly.get(ts - 3600)
        if not before or abs(hr["t_in"] - before["t_in"]) > STEADY_K:
            continue  # Aufheizen/Auskühlen: Gas geht in Wände statt Verluste – verfälscht den Vergleich
        on_frac = s["on"] / s["n"]
        out.append({
            "ts": ts, "t_out": hr["t_out"], "flow": sum(s["flow"]) / len(s["flow"]),
            "ret": sum(s["ret"]) / len(s["ret"]) if s["ret"] else None,
            "starts": starts.get(ts, 0), "on_min": on_frac * 60, "gas_per_kh": (hr["gas_kwh"] - offset_kw) / dt,
        })
    return out


def study(store, now_ts: int | None = None, offset_kw: float | None = None) -> dict:
    """``offset_kw``: Achsabschnitt des Hausmodells – nur übergeben, wenn das Modell plausibel ist."""
    rows = hours_table(store, now_ts, offset_kw or 0.0)
    bands: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        b = _band(r["t_out"])
        if b:
            bands[b][int(r["flow"] // FLOW_STEP * FLOW_STEP)].append(r)
    result = []
    for lo, hi, name in OUT_BANDS:
        cells = []
        for flow, rs in sorted(bands.get(name, {}).items()):
            n = len(rs)
            st = sum(r["starts"] for r in rs)
            rets = [r["ret"] for r in rs if r["ret"] is not None]
            cells.append({
                "flow": f"{flow}–{flow + FLOW_STEP}", "flow_lo": flow, "hours": n,
                "starts_per_h": round(st / n, 2),
                "run_per_start_min": round(sum(r["on_min"] for r in rs) / st, 1) if st else None,
                "ret": round(sum(rets) / len(rets), 1) if rets else None,
                "gas_per_kh": round(sum(r["gas_per_kh"] for r in rs) / n, 4),
                "enough": n >= MIN_HOURS,
            })
        if cells:
            result.append({"band": name, "cells": cells, "finding": _finding(cells)})
    verdict = _verdict(result)
    if offset_kw is None and verdict["kind"] in ("lohnt", "verschwendung", "unklar"):
        verdict["text"] += (" Vorläufig: ohne plausibles Hausmodell ist der Einfluss der Außentemperatur "
                            "innerhalb einer Gruppe nicht ganz herausgerechnet.")
    return {"bands": result, "hours": len(rows), "verdict": verdict, "window_days": WINDOW_DAYS,
            "offset_kw": None if offset_kw is None else round(offset_kw, 3)}


CURVE_EXP = 1 / 1.3        # Heizkörper-Exponent: so krümmen Regler ihre Heizkurve
REF_OUT = 5.0              # Bezugs-Außentemperatur für die Umrechnung


def curve_hint(design: float | None, room: float | None, min_ext: float | None, offset: float | None,
               d_flow: float = 5.0, t_out: float = REF_OUT) -> dict | None:
    """Wie viel mehr Auslegungstemperatur am Regler ergibt d_flow mehr Vorlauf bei t_out?

    Heizkurve wie bei EMS-Reglern (RC35/ES73): Vorlauf = Raum + (Auslegung − Raum) ·
    ((Raum − außen) / (Raum − tiefste Außentemperatur))^(1/1,3). Unbekannte tiefste
    Außentemperatur: −10 °C (Werkseinstellung). Der Offset verschiebt den Vorlauf und ist
    in ``flow_now`` enthalten.
    """
    if design is None:
        return None
    room = 21.0 if room is None else room
    known = min_ext is not None
    min_ext = -10.0 if min_ext is None else min_ext
    if room - min_ext <= 0 or room - t_out <= 0 or design <= room:
        return None
    ratio = ((room - t_out) / (room - min_ext)) ** CURVE_EXP
    return {"design": design, "offset": offset, "min_ext": min_ext, "min_ext_known": known, "room": room,
            "t_out": t_out, "d_flow": d_flow, "flow_now": round(room + (design - room) * ratio + (offset or 0.0), 1),
            "design_new": round(design + d_flow / ratio)}


def _finding(cells: list[dict]) -> dict | None:
    ok = [c for c in cells if c["enough"]]
    if len(ok) < 2:
        return None
    low, high = ok[0], ok[-1]
    diff = high["gas_per_kh"] / low["gas_per_kh"] - 1
    return {"low": low["flow"], "high": high["flow"], "gas_diff": round(diff, 3),
            "starts_low": low["starts_per_h"], "starts_high": high["starts_per_h"],
            "ret_high": high["ret"]}


def _verdict(bands: list[dict]) -> dict:
    found = [b for b in bands if b["finding"]]
    if not found:
        return {"kind": "zu_wenig", "text": (
            "Noch zu wenig Vielfalt: Bei gleicher Außentemperatur lief der Vorlauf bisher kaum unterschiedlich "
            f"(je Stufe mind. {MIN_HOURS} Heizstunden nötig). Ein Versuch würde es klären: die Heizkurve am Regler "
            "für ein, zwei Wochen um etwa 5 K anheben – nur, wenn du sicher bist. HeatPilot vergleicht dann "
            "automatisch Starts und Gasverbrauch.")}
    diffs = [b["finding"]["gas_diff"] for b in found]
    starts_better = all(b["finding"]["starts_high"] < b["finding"]["starts_low"] for b in found)
    ret_hot = any((b["finding"]["ret_high"] or 0) > CONDENSING_RET for b in found)
    names = ", ".join(b["band"] for b in found)
    if all(d <= -MIN_DIFF for d in diffs):
        return {"kind": "lohnt", "text": (
            f"Höherer Vorlauf lohnt sich (gemessen bei {names}): weniger Gas je Grad Unterschied innen–außen "
            f"({min(diffs) * 100:+.0f} bis {max(diffs) * 100:+.0f} %)"
            + (" und weniger Starts." if starts_better else ".") +
            " Die längeren Brennerläufe wiegen den schlechteren Brennwertnutzen auf.")}
    if all(d >= MIN_DIFF for d in diffs):
        return {"kind": "verschwendung", "text": (
            f"Höherer Vorlauf ist hier Verschwendung (gemessen bei {names}): "
            f"{min(diffs) * 100:+.0f} bis {max(diffs) * 100:+.0f} % Gas je Grad Unterschied innen–außen"
            + (", obwohl er seltener startet" if starts_better else "") + "."
            + (" Der Rücklauf liegt dann über 55 °C – der Kessel kondensiert kaum noch." if ret_hot else ""))}
    return {"kind": "unklar", "text": (
        f"Bisher kein klarer Unterschied (gemessen bei {names}): "
        + " · ".join(f"{b['band']}: {b['finding']['gas_diff'] * 100:+.0f} %" for b in found)
        + ". Mehr Vorlauf bringt hier weder deutlich mehr noch deutlich weniger – HeatPilot misst weiter.")}
