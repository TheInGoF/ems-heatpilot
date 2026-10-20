"""Räume aus der Langzeitstatistik nachholen (seit 0.45): stündliche Raumwerte für die Zeit ohne eigene Raumwerte.

Nach einem Thermostatwechsel (Tado-Cloud → HomeKit) bzw. dem Datenverlust vor 0.42.1 hat HeatPilot je Raum oft nur
wenige Tage eigene Raumwerte, Home Assistant aber in der Langzeitstatistik Monate an Stundenmitteln der alten Sensoren:
Temperatur (der Alias des Raums, ``room_alias``) und Heizleistung in % (``heat_stat``: am Gerät des alten Thermostats
bzw. des Alias laut Registry, sonst genau eine %-Statistik mit passendem Namen). ``restore`` – Teil von „Hausdaten
wiederherstellen“ (``house_restore``) – liest sie (nur lesen, wie ``backfill.backfill_statistics``) und speichert je
Raum und Stunde ohne eigenen Raumwert eine Zeile in der eigenen Tabelle ``room_stats`` (ts = Stundenbeginn, room,
temp, valve, flow):

* ``temp`` – Stundenmittel des alten Sensors, um seinen geprüften Versatz berichtigt (``offset``): direkt gegen die
  eigenen Raumwerte des Raums (gemeinsame Stunden, ``indoor_mean.fit``), sonst über das Hausmittel (Versatz des Alias
  zum Hausmittel aus „Hausdaten wiederherstellen“ minus Versatz der eigenen Raumwerte dazu). Passt der Sensor nicht
  oder fehlt jeder Bezug, bleibt der Raum leer – lieber keine Werte als verschobene;
* ``valve`` – mittlere Heizleistung der Stunde in % (None ohne Heizleistung); ``flow`` – Stundenmittel des Vorlaufs aus
  der Langzeitstatistik (für die Wärmezufuhr, wo es keine eigenen Messpunkte gibt). Ein Soll gibt es nicht.

Die Zeilen sind keine Messpunkte (``room_samples``): Ventilmodell, Hausmittel und Raum-Verläufe sehen sie nicht. Nur
das Raummodell lernt aus ihnen in Stundenschritten (``room_stats_learn``). Ein neuer Lauf ersetzt die Zeilen im
Zeitraum – hat ein Raum keinen passenden alten Sensor mehr (Alias entfernt, passt nicht), fallen sie dort weg; älter
als die Aufbewahrung (``keep_days``) bzw. die Langzeitstatistik-Tage wird nichts geholt.

Liest von Home Assistant nur (Langzeitstatistik, Statistik-Liste, Registry, Zustände); schreibt nichts an HA.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from statistics import median

from . import indoor_mean, keep_data, plausibility, room_alias, room_heating
from .discovery import HEATING_WORDS

log = logging.getLogger(__name__)

MIN_HOURS = indoor_mean.ROOM_MIN_HOURS   # gemeinsame Stunden für den Versatz
MAX_OFFSET_K = 3.0                       # derselbe Raum: so weit darf der alte Sensor im Mittel danebenliegen
NOT_HEAT = ("humid", "feucht", "batter", "signal")
HOW_TEXT = {"room": "Sensor des Raums", **room_heating.HOW_TEXT}


def _ts(value) -> int:
    if isinstance(value, (int, float)):
        return int(value / 1000 if value > 1e11 else value)
    return int(datetime.fromisoformat(value).timestamp())


def by_hour(stats: dict, sid: str | None) -> dict[int, float]:
    """Stundenmittel einer Statistik {Stundenbeginn: Wert}."""
    out = {}
    for e in (stats or {}).get(sid, []) if sid else []:
        if e.get("mean") is not None:
            out[_ts(e["start"]) // 3600 * 3600] = e["mean"]
    return out


def pct_stats(stat_list) -> dict[str, dict] | None:
    """%-Statistiken mit Mittelwert aus ``list_statistic_ids`` (None: Liste unbekannt)."""
    if stat_list is None:
        return None
    out = {}
    for m in stat_list:
        if not isinstance(m, dict) or not m.get("statistic_id"):
            continue
        unit = m.get("statistics_unit_of_measurement") or m.get("display_unit_of_measurement")
        if unit == "%" and (m.get("has_mean") is True or m.get("mean_type") in (1, "arithmetic")):
            out[m["statistic_id"]] = m
    return out


def _heat_named(sid: str, meta: dict) -> bool:
    text = f"{sid} {meta.get('name') or ''}".lower()
    return any(w in text for w in HEATING_WORDS) and not any(w in text for w in NOT_HEAT)


# ------------------------------------------------------------------ Quellen finden
def heat_stat(store, room, idx, states: dict, stats: dict | None, exclude: set[str], hit: dict | None = None) -> tuple:
    """Statistik der Heizleistung eines Raums: (ID, wie, warum nicht). Zuerst der Ventilsensor des Raums (eingetragen
    bzw. vom alten Thermostat gefunden – ``hit`` aus ``room_heating``), dann der Sensor am Gerät eines alten Thermostats
    bzw. des Alias, sonst genau eine %-Statistik mit passendem Namen."""
    ok = (lambda s: s not in exclude) if stats is None else (lambda s: s in stats and s not in exclude)
    for d in room.devices:
        if d.get("heating_power") and ok(d["heating_power"]):
            found = hit is not None and hit.get("sensor") == d["heating_power"]
            return d["heating_power"], hit["how"] if found else "room", None
    for ent, how in room_heating.old_sources(store, room):
        sid = room_heating.on_device(ent, idx, states)
        if sid and ok(sid):
            return sid, how, None
    if stats is None:
        return None, None, "Liste der Langzeitstatistiken nicht lesbar"
    want = room_heating.labels(store, states, room)
    hits = sorted(s for s, m in stats.items() if s not in exclude and _heat_named(s, m)
                  and {room_heating.hnorm(s), room_heating.hnorm(m.get("name"))} & want)
    if len(hits) == 1:
        return hits[0], "name", None
    if hits:
        return None, None, f"mehrere passende Heizleistungen ({', '.join(hits)}) – keine gewählt"
    return None, None, "keine Heizleistung (%) in der Langzeitstatistik gefunden"


def own_hours(store, key: str) -> dict[int, float]:
    """Eigene Raumwerte eines Raums als Stundenmittel {Stunde: Temperatur} (auch Stunden ohne Temperatur, Wert None)."""
    rows = store.query("SELECT ts - ts % 3600 AS h, AVG(temp) AS t FROM room_samples WHERE room = ? GROUP BY h", (key,))
    return {r["h"]: r["t"] for r in rows}


def _room_vs_house(store, own: dict[int, float]) -> float | None:
    """Versatz der eigenen Raumwerte zum gespeicherten Hausmittel (Median) – None ohne genug gemeinsame Stunden."""
    ref = {r["ts"]: r["t_in"] for r in store.query("SELECT ts, t_in FROM hourly WHERE t_in IS NOT NULL")}
    diffs = [t - ref[h] for h, t in own.items() if t is not None and h in ref]
    return median(diffs) if len(diffs) >= MIN_HOURS else None


def offset(store, own: dict[int, float], series: dict[int, float], alias: dict) -> tuple:
    """Geprüfter Versatz des alten Sensors zu den eigenen Raumwerten: (Versatz, wie, warum nicht)."""
    temps = {h: t for h, t in own.items() if t is not None}
    f = indoor_mean.fit(series, temps, MIN_HOURS)
    if f["offset"] is not None:
        if f["ok"] and abs(f["offset"]) <= MAX_OFFSET_K:
            return f["offset"], "room", None
        return None, None, (f"passt nicht zu den eigenen Raumwerten (Versatz {f['offset']:+.1f} K, Streuung "
                            f"{f['spread'] or 0:.1f} K)")
    check, room_off = alias.get("check") or {}, _room_vs_house(store, own)
    if check.get("ok") and check.get("offset") is not None and room_off is not None:
        return round(check["offset"] - room_off, 3), "house", None
    return None, None, (f"zu wenig gemeinsame Stunden mit eigenen Raumwerten ({f['hours']}, nötig {MIN_HOURS}) und "
                        "kein Bezug über das Hausmittel")


# ------------------------------------------------------------------ Einlesen
def window(eng, now: int) -> tuple[int, int]:
    """[Beginn, Ende) in Stunden: höchstens so viele Tage wie Langzeitstatistik und Aufbewahrung, bis zur laufenden
    Stunde (ausschließlich)."""
    end = now - now % 3600
    days = min(int(eng.cfg.statistics_backfill_days), keep_data.days_of(eng.cfg.keep_days))
    return end - max(days, 0) * 86400, end


def plan(eng, idx, stat_list) -> list[dict]:
    """Je aktuellem Raum: alter Temperatursensor (Alias) und Heizleistung – {room, name, temp_stat, heat_stat,
    heat_how, heat_why}. Eine Heizleistung zählt nur für einen Raum."""
    stats = pct_stats(stat_list)
    aliases = room_alias.get(eng.store)
    exclude = {d["heating_power"] for r in eng.found.rooms for d in r.devices if d.get("heating_power")}
    out = []
    for r in eng.found.rooms:
        mine = {d["heating_power"] for d in r.devices if d.get("heating_power")}
        hit = (getattr(eng.found, "valve_found", None) or {}).get(r.climate)
        heat, how, why = heat_stat(eng.store, r, idx, eng.states, stats, exclude - mine, hit)
        if heat:
            exclude.add(heat)
        out.append({"room": r.climate, "name": r.name, "temp_stat": (aliases.get(r.climate) or {}).get("stat"),
                    "heat_stat": heat, "heat_how": how, "heat_how_text": HOW_TEXT.get(how), "heat_why": why})
    return out


def rows_for(store, p: dict, stats: dict, flow: dict[int, float], span: tuple[int, int]) -> tuple[list, dict]:
    """Zeilen eines Raums für ``room_stats`` und was dazu ins Ergebnis gehört (Versatz, warum nicht)."""
    temp, heat = by_hour(stats, p["temp_stat"]), by_hour(stats, p["heat_stat"])
    own = own_hours(store, p["room"])
    off, how, why = offset(store, own, temp, room_alias.get(store).get(p["room"]) or {})
    info = {"offset": off, "offset_how": how}
    if off is None:
        return [], {**info, "why": why or "keine Werte des alten Sensors"}
    rows = []
    for h in sorted(temp):
        t = plausibility.in_range("room_temp", round(temp[h] - off, 2))
        if span[0] <= h < span[1] and h not in own and t is not None:
            v = heat.get(h)
            rows.append((h, p["room"], t, None if v is None else plausibility.in_range("valve", round(v, 1)),
                         flow.get(h)))
    return rows, {**info, "why": None if rows else "keine Stunden ohne eigene Raumwerte in der Langzeitstatistik"}


def save(store, key: str, rows: list, span: tuple[int, int]) -> None:
    """Zeilen eines Raums im Zeitraum ersetzen (ein neuer Lauf rechnet sie neu)."""
    store.db.execute("DELETE FROM room_stats WHERE room = ? AND ts >= ? AND ts < ?", (key, *span))
    store.db.executemany("INSERT OR REPLACE INTO room_stats VALUES(?, ?, ?, ?, ?)", rows)
    store.commit()


def _report(p: dict, rows: list, info: dict) -> dict:
    valve = [r for r in rows if r[3] is not None]
    return {**p, **info, "hours": len(rows), "valve_hours": len(valve), "heating": bool(valve),
            "first": rows[0][0] if rows else None, "last": rows[-1][0] if rows else None}


async def _fetch(ha, ids: list[str], span: tuple[int, int]) -> dict:
    if not ids:
        return {}
    a, b = (datetime.fromtimestamp(x, timezone.utc) for x in span)
    return await ha.statistics(ids, a, b) or {}


async def restore(ha, eng, idx, stat_list, now: int | None = None) -> dict:
    """Raumwerte aus der Stundenstatistik einlesen (siehe Moduldocstring). Ergebnis {hours, flow_stat, rooms: [je Raum
    {room, name, temp_stat, heat_stat, heat_how, hours, valve_hours, heating, first, last, offset, why, heat_why}]}."""
    span = window(eng, int(time.time() if now is None else now))
    if span[0] >= span[1]:  # Langzeitstatistik abgeschaltet (0 Tage)
        return {"hours": 0, "flow_stat": None, "rooms": []}
    flow_id = eng.found.boiler.get("flow_temp")
    flow = by_hour(await _fetch(ha, [flow_id] if flow_id else [], span), flow_id)
    out = []
    for p in plan(eng, idx, stat_list):
        if p["temp_stat"]:
            stats = await _fetch(ha, [p["temp_stat"], *([p["heat_stat"]] if p["heat_stat"] else [])], span)
            rows, info = rows_for(eng.store, p, stats, flow, span)
        else:
            rows, info = [], {"offset": None, "why": "kein alter Temperatursensor (Alias) – "
                                                     "unter Einstellungen → Räume wählen"}
        save(eng.store, p["room"], rows, span)  # ohne passende Quelle fallen frühere Zeilen weg (wie beim Alias)
        out.append(_report(p, rows, info))
        log.info("Raum %s aus der Stundenstatistik: %d Stunden (%d mit Heizleistung)%s", p["room"], len(rows),
                 out[-1]["valve_hours"], f" – {info['why']}" if info.get("why") else "")
    return {"hours": sum(r["hours"] for r in out), "flow_stat": flow_id if flow else None, "rooms": out}
