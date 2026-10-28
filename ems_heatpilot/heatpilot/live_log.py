"""30-s-Protokoll (seit 0.49.2): die Live-Werte alle 30 s auch in der Datenbank, zum Herunterladen als CSV.

Die Live-Ansicht hält ihre 30-s-Werte nur im Speicher (12 h, nach einem Neustart weg). Zum Nachrechnen – etwa
Wassermenge und Abnahme aus Vor- und Rücklauf, Brennerleistung und Pumpe – braucht es einen ganzen Tag am Stück:

* ``record`` – je Takt eine Zeile in ``live_samples`` (Kesselwerte und je Raum Ist/Soll/Ventil/Feuchte); älter als
  ``KEEP_H`` fällt weg. Übersteht einen Neustart.
* ``note_boiler`` (seit 0.50) – gleich danach HeatPilots Entscheidung für den Kessel in dieselbe Zeile (``BOILER_COLS``:
  Ziel, Bedarf, Abnahme, Zuschlag, Boost, Takten, wartende und nicht hochkommende Räume, Grund, Entscheidung) – damit
  eine Auswertung nicht rekonstruieren muss, warum das Vorlauf-Soll so stand.
* ``csv_text`` – die letzten Stunden als CSV (Komma, Dezimalpunkt): Zeit, Kesselwerte, HeatPilot, seit 0.57 die
  Spalte ``hp_geschrieben`` (alles aus dem Protokoll seit dem vorigen Punkt: geschrieben, nicht übernommen, fremde
  Änderung, Pause …), je Raum vier Spalten, seit 0.68 je Person vier (zu Hause, Entfernung, erwartete Ankunft und
  ihre Quelle – gps/gewohnheit) und im Haus ``home_count``/``vacation``: Die Datei soll alles enthalten, was ein großes
  Modell zum Nachtrainieren der kleinen Modelle im Add-on braucht.
* ``routes`` – ``GET /api/live/export?hours=24`` (Download, Live → Diagnose → Dateien).

Nur lesen und speichern, schreibt nichts an Kessel oder Thermostate.
"""

from __future__ import annotations

import bisect
import csv
import io
import time

from aiohttp import web

from .live import KEYS
from .values import local

KEEP_H = 48
DEFAULT_H = 24
# Zusätzlich zu den Werten der Live-Ansicht: Zähler und Anlagendruck (Starts im Takt genau ablesen)
EXTRA = ("burn_starts", "heat_starts", "heat_min", "min_period", "syspress", "demand_pct", "home_count", "vacation")
COLS = KEYS + EXTRA
ROOM_COLS = ("ist", "soll", "ventil", "feuchte")
PERSON_COLS = ("zuhause", "km", "ankunft", "quelle")  # 0.68
# 0.50: HeatPilots Entscheidung für den Kessel (Weg B, auch im Trockenlauf) – leer bei „aus“
BOILER_COLS = ("hp_heizbetrieb", "hp_ziel", "hp_bedarf", "hp_abnahme", "hp_zuschlag_k", "hp_boost_k", "hp_starts_h",
               "hp_lauf_min", "hp_wartet", "hp_kommt_nicht_hoch", "hp_grund", "hp_vorlauf_entscheidung")


def _person(p, now) -> list:
    e = p.eta(now)
    return [p.home, None if p.dist_km is None else round(p.dist_km, 2),
            e["eta"].isoformat(timespec="minutes") if e.get("eta") else None, e.get("source")]


def record(store, ts: int, sample: dict, rooms: list[tuple], persons: dict | None = None) -> None:
    """Einen 30-s-Punkt speichern – ``rooms`` wie in ``Engine.snapshot`` (Raum, Ist, Soll, Ventil, Feuchte, …),
    ``persons`` die Lerner je Person (``Engine.persons``)."""
    data = {"s": {k: sample.get(k) for k in COLS},
            "r": {r[0]: [r[1], r[2], r[3], r[4] if len(r) > 4 else None] for r in rooms},
            "p": {eid: _person(p, local(ts)) for eid, p in (persons or {}).items()}}
    store.add_live(ts, data, KEEP_H * 3600)


def note_boiler(store, view: dict) -> None:
    """HeatPilots Entscheidung (``BoilerControl.view`` nach ``plan``) in den 30-s-Punkt dieses Takts schreiben – der
    jüngste Punkt, höchstens 45 s alt (``record`` läuft im selben Takt kurz davor)."""
    t, ts = view.get("target"), view.get("ts")
    rows = store.live_rows(ts - 45) if t is not None and ts is not None else []
    if not rows:
        return
    cyc = t.cycling or {}
    b = dict(zip(BOILER_COLS, (t.heating, t.flow, t.base, t.load, t.stall_k, t.boost_k, cyc.get("starts_h"),
                               cyc.get("run_min"), "; ".join(t.waiting), "; ".join(t.stalled), t.reason,
                               (view.get("decision") or {}).get("flow"))))
    row_ts, data = rows[-1]
    store.add_live(row_ts, {**data, "b": b}, KEEP_H * 3600)


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    return round(value, 3) if isinstance(value, float) else value


def _log_by_row(stamps: list[int], store, names: dict) -> dict[int, str]:
    """Protokoll-Einträge (``control_log``: Kessel, Warmwasser, Räume) dem 30-s-Punkt zuordnen, in dessen Zeitraum
    (seit dem vorigen Punkt) sie fielen – je Punkt ein Text, Einträge mit „ · “ getrennt."""
    if not stamps:
        return {}
    out: dict[int, list[str]] = {}
    for e in reversed(store.control_log(5000, since=stamps[0] - 30)):
        i = bisect.bisect_left(stamps, e["ts"])
        if i == len(stamps):
            continue
        row = stamps[i]
        value = "" if e["value"] is None else f" {e['value']:g}" if isinstance(e["value"], float) else f" {e['value']}"
        out.setdefault(row, []).append(f"{names.get(e['room'], e['room'])}: {e['action']}{value} ({e['reason']})")
    return {t: " · ".join(x) for t, x in out.items()}


def csv_text(eng, hours: float = DEFAULT_H, now_ts: int | None = None) -> str:
    """Die letzten ``hours`` Stunden (höchstens ``KEEP_H``) als CSV; Räume mit Namen, unbekannte mit ihrer Entität."""
    now_ts = int(now_ts or time.time())
    rows = eng.store.live_rows(now_ts - int(min(hours, KEEP_H) * 3600))
    names = {r.climate: r.name for r in eng.found.rooms}
    seen = {c for _, d in rows for c in d.get("r", {})}
    climates = [c for c in names if c in seen] + sorted(seen - set(names))
    people = sorted({e for _, d in rows for e in d.get("p", {})})
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["zeit", "ts", *COLS, *BOILER_COLS, "hp_geschrieben",
                *(f"{names.get(c, c)} {col}" for c in climates for col in ROOM_COLS),
                *(f"{e} {col}" for e in people for col in PERSON_COLS)])  # Entität: der Fehlerbericht ersetzt sie
    logs = _log_by_row([ts for ts, _ in rows], eng.store, names)
    for ts, d in rows:
        s, r, b, p = d.get("s", {}), d.get("r", {}), d.get("b", {}), d.get("p", {})
        w.writerow([local(ts).isoformat(timespec="seconds"), ts, *(_cell(s.get(k)) for k in COLS),
                    *(_cell(b.get(k)) for k in BOILER_COLS), logs.get(ts, ""),
                    *(_cell((r.get(c) or [None] * 4)[i]) for c in climates for i in range(len(ROOM_COLS))),
                    *(_cell((p.get(e) or [None] * 4)[i]) for e in people for i in range(len(PERSON_COLS)))])
    return out.getvalue()


def routes(app, json_response) -> list[tuple]:
    async def export(req):
        if app.eng is None:
            return web.Response(status=503, text="Add-on startet noch")
        try:
            hours = min(max(float(req.query.get("hours", DEFAULT_H)), 0.5), KEEP_H)
        except ValueError:
            hours = DEFAULT_H
        name = f"heatpilot-30s-{local(time.time()):%Y%m%d-%H%M}.csv"
        return web.Response(text=csv_text(app.eng, hours), content_type="text/csv", charset="utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return [("GET", "/api/live/export", export)]
