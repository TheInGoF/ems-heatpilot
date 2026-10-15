"""Beim ersten Start die vorhandene Historie aus Home Assistant übernehmen.

1. Langzeitstatistik (stündlich, bleibt in HA dauerhaft): Gas, Außen- und
   Raumtemperaturen -> Hausmodell lernt sofort aus Monaten an Daten.
2. Recorder-Historie (Standard 10 Tage, je nach ``purge_keep_days``):
   alle Einzelwerte -> Raummodelle, Takten, Anwesenheitsmuster.

Kein Export nötig – das Add-on holt sich alles selbst.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from .engine import Engine, flag, local
from .ha_client import HAClient
from .models import ROOM_STEP_H, HouseModel

log = logging.getLogger(__name__)

CHUNK_DAYS = 3


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    return datetime.fromisoformat(value).timestamp()


async def backfill_statistics(ha: HAClient, eng: Engine, days: int) -> int:
    f = eng.found
    if days <= 0 or not f.gas:
        return 0
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    temp_ids = [r.temp_sensor for r in f.rooms if r.temp_sensor]
    ids = [f.gas, *f.outdoor, *temp_ids]
    stats = await ha.statistics(ids, start, end)

    def by_hour(entity: str, key: str) -> dict[int, float]:
        out = {}
        for e in stats.get(entity, []):
            v = e.get(key)
            if v is not None:
                out[int(_ts(e["start"]))] = v
        return out

    gas_change = by_hour(f.gas, "change")
    if not gas_change:
        sums = sorted(by_hour(f.gas, "sum").items())
        gas_change = {
            h: s2 - s1 for (_, s1), (h, s2) in zip(sums, sums[1:]) if s2 >= s1
        }
    outdoor = [by_hour(e, "mean") for e in f.outdoor]
    rooms = [by_hour(e, "mean") for e in temp_ids]
    rows = []
    for hour in sorted(gas_change):
        t_out = [o[hour] for o in outdoor if hour in o]
        t_in = [r[hour] for r in rooms if hour in r]
        rows.append(
            (
                hour,
                min(t_out) if t_out else None,
                sum(t_in) / len(t_in) if t_in else None,
                gas_change[hour] * eng.gas_factor,
                None,
                None,
            )
        )
    eng.store.add_hourly(rows)
    eng.store.commit()
    log.info("Langzeitstatistik: %d Stunden übernommen", len(rows))
    return len(rows)


def _series(raw: list[dict]) -> list[tuple[float, str, dict]]:
    out = []
    attrs: dict = {}
    for e in raw:
        if "a" in e:
            attrs = e["a"]
        out.append((_ts(e.get("lu", e.get("lc", 0))), e.get("s"), attrs))
    return out


class _Cursor:
    """Letzter bekannter Wert zu einem Zeitpunkt (Werte gelten bis zur nächsten Änderung)."""

    def __init__(self, series: list[tuple[float, str, dict]]) -> None:
        self.s = series
        self.i = -1

    def at(self, ts: float) -> tuple[str, dict] | None:
        while self.i + 1 < len(self.s) and self.s[self.i + 1][0] <= ts:
            self.i += 1
        # HA liefert pro Abfrage den Zustand zum Startzeitpunkt mit, Werte gelten bis zur Änderung
        if self.i < 0 or ts - self.s[self.i][0] > (CHUNK_DAYS + 1) * 86400:
            return None
        return self.s[self.i][1], self.s[self.i][2]


async def backfill_history(ha: HAClient, eng: Engine, days: int) -> int:
    if days <= 0:
        return 0
    f = eng.found
    step = eng.cfg.sample_interval_s
    end_ts = int(time.time())
    end_ts -= end_ts % step
    start_ts = end_ts - days * 86400
    plain = sorted(set(f.boiler.values()) | set(f.thermostat.values()) | set(f.outdoor)
                   | ({f.gas} if f.gas else set())
                   | ({eng.cfg.vacation_entity} if eng.cfg.vacation_entity else set())
                   | {r.heating_power for r in f.rooms if r.heating_power}
                   | {r.humidity for r in f.rooms if r.humidity})
    with_attrs = [r.climate for r in f.rooms] + f.persons
    series: dict[str, list] = {e: [] for e in plain + with_attrs}
    t = start_ts
    while t < end_ts:
        t2 = min(t + CHUNK_DAYS * 86400, end_ts)
        s, e = datetime.fromtimestamp(t, timezone.utc), datetime.fromtimestamp(t2, timezone.utc)
        for ids, attrs in ((plain, False), (with_attrs, True)):
            if not ids:
                continue
            res = await ha.history(ids, s, e, attributes=attrs)
            for ent, raw in (res or {}).items():
                series.setdefault(ent, []).extend(_series(raw))
        t = t2
    log.info("Historie geladen: %d Entitäten", len(series))

    # Brennerereignisse exakt aus der Historie
    burner = f.boiler.get("burner_on")
    prev = None
    for ts, st, _ in series.get(burner, []) if burner else []:
        on = flag(st)
        if on is not None and on != prev:
            eng.store.add_burner_event(int(ts), on)
            prev = on

    cur = {ent: _Cursor(sorted(ser, key=lambda x: x[0])) for ent, ser in series.items()}

    count = 0
    for ts in range(start_ts, end_ts, step):
        # Zustände für diesen Zeitpunkt in die Engine spiegeln und wie live abtasten
        eng.states = {}
        for ent in cur:
            hit = cur[ent].at(ts)
            if hit:
                eng.states[ent] = {"state": hit[0], "attributes": hit[1]}
        eng.update_live(ts, record_events=False)
        sample, rooms, persons = eng.snapshot()
        eng.track_vacation_return(ts, persons)
        sample["vacation"] = int(eng.on_vacation(ts))
        eng.store.add_sample(ts, sample)
        eng.store.add_room_samples((ts, *r) for r in rooms)
        eng.store.add_person_samples((ts, *p) for p in persons)
        now = local(ts)
        for p, is_home, dist in persons:
            eng.persons[p].observe(now, bool(is_home), dist, learn=not sample["vacation"])
        eng.dhw_step(ts, sample)
        count += 1
    eng.store.commit()

    eng.store.put("history_start", start_ts)
    eng._last_room_step = end_ts - end_ts % int(ROOM_STEP_H * 3600)
    eng._last_hour = end_ts - end_ts % 3600
    eng.states = {}
    eng._live_ts, eng._live_prev = None, {}
    log.info("Historie: %d Abtastpunkte übernommen", count)
    return count


def replay(eng: Engine) -> None:
    """Alle Daten chronologisch nachlernen, mit Tagesständen für die Lernkurve.

    Vor dem Fenster der Recorder-Historie gibt es nur Stundenwerte aus der
    Langzeitstatistik (nur Hausmodell), danach lernen auch die Raummodelle.
    """
    eng.house = HouseModel()
    eng.store.db.execute("DELETE FROM snapshots")
    hist_start = eng.store.get("history_start")
    room_step = int(ROOM_STEP_H * 3600)
    hours = [r["ts"] for r in eng.store.query("SELECT ts FROM hourly ORDER BY ts")]
    if hist_start is not None:
        h0 = hist_start - hist_start % 3600 + 3600
        hours = sorted({h for h in hours if h < h0} | set(range(h0, eng._last_hour, 3600)))
    for h in hours:
        if hist_start is not None and h >= hist_start:
            for st in range(h, h + 3600, room_step):
                eng.learn_rooms(st, st + room_step)
            eng.aggregate_hour(h, learn=True)
        else:
            eng.learn_house_hour(h)
        if local(h).hour == 0:
            eng.take_snapshot(h)
    eng.store.commit()


async def run_backfill(ha: HAClient, eng: Engine) -> None:
    if eng.store.get("backfill_done"):
        return
    log.info("Erster Start: übernehme vorhandene Daten aus Home Assistant …")
    try:
        await backfill_statistics(ha, eng, eng.cfg.statistics_backfill_days)
    except Exception as err:  # noqa: BLE001
        log.warning("Langzeitstatistik nicht geladen: %s", err)
    try:
        await backfill_history(ha, eng, eng.cfg.backfill_days)
    except Exception as err:  # noqa: BLE001
        log.warning("Historie nicht geladen: %s", err)
    replay(eng)
    eng.save()
    eng.store.put("backfill_done", True)
    log.info("Datenübernahme fertig: %s", eng.house.summary(eng.cfg.design_outdoor_temp, None, None))

