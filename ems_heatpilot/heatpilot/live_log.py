"""30-s-Protokoll (seit 0.49.2): die Live-Werte alle 30 s auch in der Datenbank, zum Herunterladen als CSV.

Die Live-Ansicht hält ihre 30-s-Werte nur im Speicher (12 h, nach einem Neustart weg). Zum Nachrechnen – etwa
Wassermenge und Abnahme aus Vor- und Rücklauf, Brennerleistung und Pumpe – braucht es einen ganzen Tag am Stück:

* ``record`` – je Takt eine Zeile in ``live_samples`` (Kesselwerte und je Raum Ist/Soll/Ventil/Feuchte); älter als
  ``KEEP_H`` fällt weg. Übersteht einen Neustart.
* ``csv_text`` – die letzten Stunden als CSV (Komma, Dezimalpunkt): Zeit, Kesselwerte, je Raum vier Spalten.
* ``routes`` – ``GET /api/live/export?hours=24`` (Download, Einstellungen → Diagnose → Dateien).

Nur lesen und speichern, schreibt nichts an Kessel oder Thermostate.
"""

from __future__ import annotations

import csv
import io
import time

from aiohttp import web

from .live import KEYS
from .values import local

KEEP_H = 48
DEFAULT_H = 24
# Zusätzlich zu den Werten der Live-Ansicht: Zähler und Anlagendruck (Starts im Takt genau ablesen)
EXTRA = ("burn_starts", "heat_starts", "heat_min", "min_period", "syspress", "demand_pct")
COLS = KEYS + EXTRA
ROOM_COLS = ("ist", "soll", "ventil", "feuchte")


def record(store, ts: int, sample: dict, rooms: list[tuple]) -> None:
    """Einen 30-s-Punkt speichern – ``rooms`` wie in ``Engine.snapshot`` (Raum, Ist, Soll, Ventil, Feuchte, …)."""
    data = {"s": {k: sample.get(k) for k in COLS},
            "r": {r[0]: [r[1], r[2], r[3], r[4] if len(r) > 4 else None] for r in rooms}}
    store.add_live(ts, data, KEEP_H * 3600)


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    return round(value, 3) if isinstance(value, float) else value


def csv_text(eng, hours: float = DEFAULT_H, now_ts: int | None = None) -> str:
    """Die letzten ``hours`` Stunden (höchstens ``KEEP_H``) als CSV; Räume mit Namen, unbekannte mit ihrer Entität."""
    now_ts = int(now_ts or time.time())
    rows = eng.store.live_rows(now_ts - int(min(hours, KEEP_H) * 3600))
    names = {r.climate: r.name for r in eng.found.rooms}
    seen = {c for _, d in rows for c in d.get("r", {})}
    climates = [c for c in names if c in seen] + sorted(seen - set(names))
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["zeit", "ts", *COLS, *(f"{names.get(c, c)} {col}" for c in climates for col in ROOM_COLS)])
    for ts, d in rows:
        s, r = d.get("s", {}), d.get("r", {})
        w.writerow([local(ts).isoformat(timespec="seconds"), ts, *(_cell(s.get(k)) for k in COLS),
                    *(_cell((r.get(c) or [None] * 4)[i]) for c in climates for i in range(len(ROOM_COLS)))])
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
