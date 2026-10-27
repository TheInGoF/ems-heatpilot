"""Ersparnis gegenüber festen Zeitplänen und gegenüber früher (seit 0.64, Konzeptpapier Abschnitt 8).

Zwei Zahlen neben der Modell-Schätzung der Übersicht (``analysis.estimate_savings``):

* **Zeitersparnis** (``time_saving``, immer): Stunden je Woche, in denen HeatPilot weniger heizt als ein fester
  Zeitplan – die gelernten Nachtzeiten gegen den eingestellten Wochenplan (Nacht länger oder kürzer) und die Stunden,
  in denen niemand zu Hause war (außerhalb der festen Nacht; ein fester Zeitplan hätte geheizt). Ohne „Gelernte
  Zeiten verwenden“ steht der Unterschied der Nächte als „möglich“ da.
* **Vergleich mit früher** (``compare``, mit Gasdaten): Heizgas je Gradtag (G 20/15: an Tagen unter 15 °C mittlerer
  Außentemperatur 20 − Tagesmittel) in den letzten ``RECENT_DAYS`` Tagen gegen einen Vergleichszeitraum, den der
  Nutzer wählt – so lassen sich vorgeprägte Zeiten (frühere Automationen, Tado-Zeitpläne) ausschließen.
  Witterungsbereinigt, ohne Warmwasser-Anteil. Der Zeitraum steht im kv ``savings.ref``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time

KV_REF = "savings.ref"
RECENT_DAYS = 30
TIME_DAYS = 28
HDD_BASE, HDD_LIMIT = 20.0, 15.0
MIN_DAYS = 7          # so viele Heiztage braucht ein Zeitraum für den Vergleich


def _min(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _night_len(start: str, end: str) -> int:
    """Länge einer Nacht in Minuten (Beginn abends, Ende am Folgemorgen)."""
    return 1440 - _min(start) + _min(end)


def _in_fixed_night(cfg, dt: datetime) -> bool:
    m, wd = dt.hour * 60 + dt.minute, dt.weekday()
    if m < 12 * 60:
        return m < _min(cfg.night_plan[wd]["end"])
    return m >= _min(cfg.night_plan[wd]["start"])


def time_saving(store, cfg, now_ts: int, days: int = TIME_DAYS) -> dict:
    """Stunden je Woche weniger geheizt als mit festem Zeitplan: Nächte (gelernt gegen Wochenplan) und Abwesenheit."""
    nights_min, possible_min = 0, 0
    learned = cfg.night_plan_learned or []
    for i in range(7):
        j = (i + 1) % 7
        fixed = _night_len(cfg.night_plan[i]["start"], cfg.night_plan[j]["end"])
        eff = _night_len(cfg.night_entry(i)["start"], cfg.night_entry(j)["end"])
        nights_min += eff - fixed
        if len(learned) == 7:
            ls, le = (learned[i] or {}).get("start"), (learned[j] or {}).get("end")
            possible_min += _night_len(ls or cfg.night_plan[i]["start"], le or cfg.night_plan[j]["end"]) - fixed
    since = now_ts - days * 86400
    rows = store.query("SELECT ts, home_frac, vacation_frac FROM hourly WHERE ts >= ? AND ts < ?", (since, now_ts))
    away = sum(1 for r in rows if ((r["home_frac"] == 0) or (r["vacation_frac"] or 0) >= 1)
               and not _in_fixed_night(cfg, datetime.fromtimestamp(r["ts"]).astimezone()))
    weeks = max(len(rows) / 168, 1 / 7) if rows else None
    away_h = round(away / weeks, 1) if weeks else None
    used = bool(cfg.night_plan_auto)
    night_h = round(nights_min / 60, 1)
    return {"night_h_week": night_h if used else 0.0, "night_h_possible": round(possible_min / 60, 1),
            "away_h_week": away_h, "total_h_week": round((night_h if used else 0.0) + (away_h or 0.0), 1),
            "learned_used": used, "days": days}


def period(store, start: date, end: date) -> dict:
    """Heizgas und Gradtage eines Zeitraums (Tage ``start`` bis ``end`` einschließlich), nur volle Heiztage."""
    a = int(datetime.combine(start, time()).timestamp())
    b = int(datetime.combine(end, time()).timestamp()) + 86400
    rows = store.query("SELECT ts, t_out, gas_kwh, dhw_frac, dhw_energy_frac FROM hourly WHERE ts >= ? AND ts < ?",
                       (a, b))
    days: dict[date, dict] = defaultdict(lambda: {"t": [], "kwh": 0.0, "n": 0})
    for r in rows:
        d = days[datetime.fromtimestamp(r["ts"]).date()]
        if r["t_out"] is not None:
            d["t"].append(r["t_out"])
        if r["gas_kwh"] is not None:
            frac = r["dhw_energy_frac"] if r["dhw_energy_frac"] is not None else (r["dhw_frac"] or 0.0)
            d["kwh"] += r["gas_kwh"] * (1 - min(max(frac, 0.0), 1.0))
            d["n"] += 1
    kwh, kd, n = 0.0, 0.0, 0
    for d in days.values():
        if d["n"] < 20 or len(d["t"]) < 20:
            continue  # unvollständiger Tag
        t = sum(d["t"]) / len(d["t"])
        if t >= HDD_LIMIT:
            continue  # kein Heiztag
        kwh, kd, n = kwh + d["kwh"], kd + (HDD_BASE - t), n + 1
    return {"from": start.isoformat(), "to": end.isoformat(), "days": n, "kwh": round(kwh, 1), "kd": round(kd, 1),
            "kwh_per_kd": round(kwh / kd, 3) if kd > 0 else None}


def compare(store, now_ts: int) -> dict:
    """Heizgas je Gradtag: letzte ``RECENT_DAYS`` Tage gegen den gewählten Vergleichszeitraum."""
    today = datetime.fromtimestamp(now_ts).date()
    recent = period(store, date.fromordinal(today.toordinal() - RECENT_DAYS), date.fromordinal(today.toordinal() - 1))
    ref = store.get(KV_REF) or {}
    out = {"recent": recent, "ref": None, "saved_pct": None, "note": None}
    if not ref.get("from") or not ref.get("to"):
        out["note"] = "Vergleichszeitraum wählen – z. B. derselbe Monat im Vorjahr, bevor HeatPilot steuerte"
        return out
    out["ref"] = period(store, date.fromisoformat(ref["from"]), date.fromisoformat(ref["to"]))
    if min(recent["days"], out["ref"]["days"]) < MIN_DAYS or not recent["kwh_per_kd"] or not out["ref"]["kwh_per_kd"]:
        out["note"] = f"zu wenig Heiztage mit Gas und Außentemperatur (mindestens {MIN_DAYS} je Zeitraum)"
        return out
    out["saved_pct"] = round((1 - recent["kwh_per_kd"] / out["ref"]["kwh_per_kd"]) * 100, 1)
    return out


def set_ref(store, start: str | None, end: str | None) -> dict | None:
    """Vergleichszeitraum speichern (ISO-Daten) bzw. mit leeren Werten löschen; ValueError bei Unsinn."""
    if not start and not end:
        store.put(KV_REF, None)
        return None
    a, b = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if b < a or (b - a).days > 366:
        raise ValueError("Vergleichszeitraum: Ende nach dem Beginn, höchstens ein Jahr")
    ref = {"from": a.isoformat(), "to": b.isoformat()}
    store.put(KV_REF, ref)
    return ref


def routes(app, json_response) -> list[tuple]:
    import time as _time

    async def get(_req):
        if app.eng is None:
            return json_response({"starting": True})
        now = int(_time.time())
        return json_response({"time": time_saving(app.eng.store, app.eng.cfg, now),
                              "history": compare(app.eng.store, now)})

    async def post(req):
        if app.eng is None:
            return json_response({"error": "Add-on startet noch"}, status=503)
        body = await req.json()
        try:
            ref = set_ref(app.eng.store, body.get("from"), body.get("to"))
        except (TypeError, ValueError) as err:
            return json_response({"error": str(err)}, status=400)
        return json_response({"ok": True, "ref": ref})

    return [("GET", "/api/savings", get), ("POST", "/api/savings/ref", post)]
