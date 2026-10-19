"""Trockenlauf-Protokoll (seit 0.37): der stündliche Plan der Vorschau neben dem, was Regler und Automation setzten.

Zu Beginn jeder Stunde speichert ``record`` den Plan, den die Vorschau (``preview``) für diese Stunde rechnet –
Vorlauf-Soll, Heizbetrieb, Warmwasser-Soll, Außentemperatur und die Schreibvorgänge, die es gäbe. Ehrlich mit dem
damals gerechneten Plan: Er wird später nicht neu gerechnet. Ist die Stunde vorbei, trägt ``fill_actuals`` den
Ist-Wert aus den Messpunkten nach: Mittel des Vorlauf-Solls des Reglers (z. B. ES73) bei Heizbetrieb – das
Heizkreis-Soll ``calc_flow``, ohne es das Kessel-Soll ``sel_flow``; Speicherladungen (Ventil auf Warmwasser bzw.
Kessel-Soll weit über dem Heizkreis, wie in ``advice``) und Messpunkte ohne Anforderung (Kessel-Soll unter 20 °C)
zählen nicht –, Mittel des Speicher-Solls ``dhw_set`` (z. B. HA-Automation), Anteil Heizbetrieb an (Betriebsart
``hc_day`` und „Heizen abschalten“ ``heating_off``) und wie oft sich diese Werte geändert haben (ab 1 K bzw.
an/aus; der Vorlauf wie im Plan nur zwischen Messpunkten mit Heizbetrieb und Anforderung).

``compare`` wertet je Tag und über 7 Tage aus: mittlerer Unterschied Plan − Ist, Stunden tiefer/höher, Schreibvorgänge
des Plans gegen Wechsel des Ist-Werts und – nur wo das Warmwasser-Modell es hergibt – den Unterschied im
Bereitschaftsverlust (``DhwModel.loss_w``). Für den Vorlauf gibt es keine kWh. Aufbewahrt wird 90 Tage (wie
``control_log``), „neu lernen“ lässt das Protokoll stehen – es ist Messung, nichts Gelerntes. Geschrieben wird nur
in die eigene Datenbank, nie an den Kessel.
"""

from __future__ import annotations

from datetime import timedelta

from . import guards, preview
from .values import flag, local, midnight

LATE_MIN = 15          # später in der Stunde gerechnet, wäre es kein Plan für die ganze Stunde mehr
DAYS = 7               # Auswertung über so viele Tage (heute eingeschlossen)
DIFF_K = 1.0           # ab so viel Unterschied liegt eine Stunde tiefer bzw. höher
KV_WRITTEN = "preview.written"  # was HeatPilot nach der letzten protokollierten Stunde am Kessel stehen hätte
EMPTY = "sammelt – erste Werte nach einer Stunde"
KINDS = ("flow", "heating", "dhw")
DEMAND_MIN = 20.0      # Kessel-Soll darunter: keine Wärmeanforderung (z. B. 7 °C im Aus-/Sommerbetrieb)
CHARGE_GAP_K = 15.0    # Kessel-Soll so weit über dem Heizkreis: nicht markierte Speicherladung (wie advice._load)


# ------------------------------------------------------------------ Plan speichern
def record(eng, now_ts: int) -> bool:
    """Im Takt der Engine: einmal je Stunde Ist-Werte nachtragen und den Plan der begonnenen Stunde speichern.

    Nur in den ersten ``LATE_MIN`` Minuten der Stunde (nach einem Neustart später nicht – das wäre kein Plan für die
    Stunde). Die Schreibzählung beginnt dort, wo der Plan der Vorstunde endete; ohne Vorstunde bei dem, was heute am
    Kessel steht. True, wenn ein Plan gespeichert wurde.
    """
    hour = preview.hour_start(now_ts)
    if eng.__dict__.get("_preview_logged") == hour:
        return False
    eng.__dict__["_preview_logged"] = hour
    store = eng.store
    fill_actuals(store, now_ts)
    if now_ts - hour > LATE_MIN * 60 or store.query("SELECT 1 FROM preview_log WHERE ts = ?", (hour,)):
        return False
    prev = store.get(KV_WRITTEN) or {}
    written = {k: prev.get(k) for k in KINDS} if prev.get("ts") == hour - 3600 else None
    plan = preview.preview(eng, now_ts, written)
    first = plan["hours"][0]
    counts = {k: sum(1 for w in first["writes"] if w["what"] == k) for k in KINDS}
    saved = store.add_preview_plan(hour, {
        "flow": first["flow"], "heating": None if first["heating"] is None else int(first["heating"]),
        "dhw": first["dhw"], "t_out": first["t_out"],
        "w_flow": counts["flow"], "w_heating": counts["heating"], "w_dhw": counts["dhw"]})
    store.put(KV_WRITTEN, {"ts": hour, **plan["written_next"]})
    return saved


# ------------------------------------------------------------------ Ist-Werte
def _heating(row) -> bool | None:
    """Heizbetrieb an laut Messpunkt: Betriebsart Tag und Heizen nicht abgeschaltet (unbekannt: None)."""
    day, off = row["hc_day"], flag(row["heating_off"])
    if day is None and off is None:
        return None
    return bool(day != 0 and off != 1)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _flow(row) -> float | None:
    """Vorlauf-Soll des Reglers für den Heizkreis in diesem Messpunkt – None, wenn der Messpunkt keins zeigt:
    Heizbetrieb aus, Ventil auf Warmwasser, nicht markierte Speicherladung (Kessel-Soll weit über dem Heizkreis)
    oder keine Anforderung (Kessel-Soll unter ``DEMAND_MIN``). Mit Heizkreis-Soll (``calc_flow``) zählt das, sonst
    das Kessel-Soll."""
    sel, calc = row["sel_flow"], row["calc_flow"]
    if _heating(row) is False or row["dhw"]:
        return None
    if sel is not None and calc is not None and sel > calc + CHARGE_GAP_K:
        return None
    if sel is not None and sel < DEMAND_MIN:
        return None
    value = sel if calc is None else calc
    return value if value is not None and value >= DEMAND_MIN else None


def actuals(store, ts: int) -> dict:
    """Ist-Werte der Stunde ab ``ts`` aus den Messpunkten (dazu die Stunde davor als Bezug für die Wechsel)."""
    rows = store.query("SELECT ts, sel_flow, calc_flow, dhw, dhw_set, hc_day, heating_off FROM samples "
                       "WHERE ts >= ? AND ts < ? ORDER BY ts", (ts - 3600, ts + 3600))
    before = [r for r in rows if r["ts"] < ts]
    inside = [r for r in rows if r["ts"] >= ts]
    states = [_heating(r) for r in inside]
    known = [s for s in states if s is not None]

    def changes(value, step_k: float | None) -> int:
        """Wechsel ab 1 K bzw. an/aus – gegen den letzten Wert davor, ohne ihn gegen den ersten bekannten. Werte
        None (beim Vorlauf: kein Heizkreis-Soll) überspringt die Zählung wie im Plan."""
        series = [(local(r["ts"]), value(r)) for r in inside]
        ref = next((v for v in (value(r) for r in reversed(before)) if v is not None), None) if before else None
        if ref is None:
            ref = next((v for _, v in series if v is not None), None)
        return len(preview.count_writes(series, ref, step_k, hold_min=0))

    flows = [f for f in (_flow(r) for r in inside) if f is not None]
    return {
        "act_flow": _mean(flows),
        "act_heating": round(sum(known) / len(known), 2) if known else None,
        "act_dhw": _mean([r["dhw_set"] for r in inside if r["dhw_set"] is not None]),
        "ch_flow": changes(_flow, preview.FLOW_STEP_K),
        "ch_dhw": changes(lambda r: r["dhw_set"], preview.DHW_STEP_K),
        "ch_heating": changes(_heating, None),
        "act_n": len(inside),
    }


def fill_actuals(store, now_ts: int) -> int:
    """Alle abgelaufenen Stunden ohne Ist-Wert nachtragen; gibt die Zahl der Stunden zurück."""
    open_hours = store.preview_open(now_ts)
    for ts in open_hours:
        store.set_preview_actual(ts, actuals(store, ts))
    return len(open_hours)


# ------------------------------------------------------------------ Auswertung
def _flow_pair(r: dict) -> bool:
    """Vorlauf vergleichen nur, wenn beide heizen: Plan Heizbetrieb an, Regler mindestens die halbe Stunde an."""
    return (r["heating"] == 1 and r["flow"] is not None and r["act_flow"] is not None
            and (r["act_heating"] is None or r["act_heating"] >= 0.5))


def stats(rows: list[dict], dhw=None) -> dict:
    """Kennzahlen über Stunden mit Ist-Wert. ``dhw``: plausibles ``DhwModel`` für die Bereitschaftsverluste, sonst None."""
    flow = [r["flow"] - r["act_flow"] for r in rows if _flow_pair(r)]
    pairs = [(r["dhw"], r["act_dhw"]) for r in rows if r["dhw"] is not None and r["act_dhw"] is not None]
    dhw_d = [p - a for p, a in pairs]
    kwh = None
    if dhw is not None and pairs and dhw.loss_w(50.0) is not None:
        kwh = round(sum(dhw.loss_w(p) - dhw.loss_w(a) for p, a in pairs) / 1000, 2)  # je Stunde W·h → kWh
    plan = {k: int(sum(r[f"w_{k}"] or 0 for r in rows)) for k in KINDS}
    act = {k: int(sum(r[f"ch_{k}"] or 0 for r in rows)) for k in KINDS}
    return {
        "hours": len(rows),
        "flow_hours": len(flow), "flow_diff_k": _mean(flow),
        "flow_lower_h": sum(1 for d in flow if d <= -DIFF_K), "flow_higher_h": sum(1 for d in flow if d >= DIFF_K),
        # Heizbetrieb: HeatPilot aus, Regler an – und umgekehrt
        "heating_off_h": sum(1 for r in rows if r["heating"] == 0 and (r["act_heating"] or 0) >= 0.5),
        "heating_on_h": sum(1 for r in rows if r["heating"] == 1 and r["act_heating"] is not None
                            and r["act_heating"] < 0.5),
        "dhw_hours": len(dhw_d), "dhw_diff_k": _mean(dhw_d),
        "dhw_lower_h": sum(1 for d in dhw_d if d <= -DIFF_K), "dhw_higher_h": sum(1 for d in dhw_d if d >= DIFF_K),
        "writes_plan": sum(plan.values()), "changes_act": sum(act.values()),
        "writes_plan_by": plan, "changes_act_by": act,
        "dhw_kwh": kwh,
    }


def _k(diff: float) -> str:
    if abs(diff) < 0.5:
        return "etwa gleich"
    return f"{abs(diff):.0f} K {'tiefer' if diff < 0 else 'höher'}"


def text(s: dict) -> str:
    """Kennzahlen in einem Satz: „HeatPilot läge im Mittel 6 K tiefer beim Vorlauf, 4 K tiefer beim Warmwasser; 9 statt
    31 Schreibvorgänge“ (der zweite Wert: so oft haben Regler und Automation ihr Soll geändert)."""
    parts = [f"{_k(s[key])} beim {name}" for key, name in (("flow_diff_k", "Vorlauf"), ("dhw_diff_k", "Warmwasser"))
             if s[key] is not None]
    head = ("HeatPilot läge im Mittel " + ", ".join(parts)) if parts else "Noch keine Stunde zum Vergleichen"
    out = f"{head}; {s['writes_plan']} statt {s['changes_act']} Schreibvorgänge"
    if s["dhw_kwh"] is not None and abs(s["dhw_kwh"]) >= 0.05:
        out += (f"; Bereitschaftsverlust Warmwasser ≈ {abs(s['dhw_kwh']):.1f} kWh "
                f"{'weniger' if s['dhw_kwh'] < 0 else 'mehr'}").replace(".", ",")
    return out


def compare(store, now_ts: int, dhw=None, days: int = DAYS) -> dict:
    """Trockenlauf gegen Regler/Automation: je Tag (``days``, älteste zuerst), gesamt und die Stundenreihe dazu.

    Ohne abgeschlossene Stunde ``collecting`` mit ``text`` „sammelt – erste Werte nach einer Stunde“.
    """
    since = midnight(local(now_ts) - timedelta(days=days - 1))
    rows = store.preview_log(since)
    series = [{"t": local(r["ts"]).isoformat(), "flow": r["flow"], "heating": r["heating"], "dhw": r["dhw"],
               "t_out": r["t_out"], "act_flow": r["act_flow"], "act_heating": r["act_heating"],
               "act_dhw": r["act_dhw"], "writes": sum(int(r[f"w_{k}"] or 0) for k in KINDS),
               "changes": None if r["act_n"] is None else sum(int(r[f"ch_{k}"] or 0) for k in KINDS)}
              for r in rows]
    done = [r for r in rows if r["act_n"]]
    if not done:
        return {"collecting": True, "text": EMPTY, "days": [], "total": None, "series": series, "window_days": days}
    by_day: dict[str, list[dict]] = {}
    for r in done:
        by_day.setdefault(local(r["ts"]).date().isoformat(), []).append(r)
    per_day = [(d, stats(rs, dhw)) for d, rs in sorted(by_day.items())]
    total = stats(done, dhw)
    return {"collecting": False, "text": text(total), "total": total, "series": series, "window_days": days,
            "days": [{"day": d, **s, "text": text(s)} for d, s in per_day]}


def compare_for(eng, now_ts: int) -> dict:
    """``compare`` für die Oberfläche – Bereitschaftsverluste nur mit plausiblem Warmwasser-Modell."""
    return compare(eng.store, now_ts, eng.dhw if guards.check_dhw(eng.dhw)["ok"] else None)
