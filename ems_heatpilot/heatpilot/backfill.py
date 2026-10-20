"""Beim ersten Start die vorhandene Historie aus Home Assistant übernehmen.

1. Langzeitstatistik (stündlich, bleibt in HA dauerhaft): Gas, Außen- und
   Raumtemperaturen -> Hausmodell lernt sofort aus Monaten an Daten.
2. Recorder-Historie (Standard 10 Tage, je nach ``purge_keep_days``):
   alle Einzelwerte -> Raummodelle, Takten, Anwesenheitsmuster. Beim Neuberechnen
   lernen die Personen davor aus den eigenen gespeicherten Messwerten (oft länger als 10 Tage).

Kein Export nötig – das Add-on holt sich alles selbst. Was HA liefert, ersetzt die gespeicherten Werte desselben
Zeitraums – seit 0.42.1 zusammenführend: Ein Messpunkt behält, wozu HA nichts hat (``merge_stored``, z. B. Kesselwerte
direkt von EMS-ESP), Brennerereignisse werden nur mit Brenner-Entität in HA ersetzt, Raumwerte je Raum erst ab dem
ersten Zeitpunkt seiner HA-Historie (``room_starts``) – ältere, z. B. von einer alten Entität übernommene
(``room_adopt``), und Schlüssel ohne Raum bleiben.

Seit 0.42.1 außerdem:

* Die Langzeitstatistik führt zusammen statt zu überschreiben (``Store.add_hourly``): Eine Stunde ohne neue
  Außentemperatur behält die gespeicherte, eine gespeicherte Innentemperatur bleibt immer. Fehlende Innentemperatur
  kommt je Raum aus dem aktuellen Sensor und, wo er (noch) keine Statistik hat, aus dem Alias des Raums (``room_alias``
  – der Sensor vor einem Wechsel der Integration), jede Quelle um ihren Versatz zum gespeicherten Hausmittel
  berichtigt (``indoor_mean``) – ein Mittel über weniger Räume verschöbe sie sonst um deren Abstand zum Hausmittel.
  Stunden mit eigenen Messwerten bekommen nur, was ihnen fehlt (``Store.fill_hourly``).
* ``backfill_rooms`` holt nach einem Raumwechsel nur die Historie der Geräte dieser Räume nach – ohne etwas zu
  löschen oder zu überschreiben, ohne Langzeitstatistik.
* ``replay`` kann ab einem frei gewählten Zeitpunkt nachlernen (``since``: „aus gespeicherten Daten neu lernen“ ab dem
  ersten eigenen Messpunkt) und gibt dabei nach jedem Tag die Ereignisschleife frei (``replay_async``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from itertools import groupby
from datetime import datetime, timedelta, timezone

from . import indoor_mean, plausibility, room_alias
from .engine import Engine, flag, local
from .engine_sample import SampleMixin
from .ha_client import HAClient
from .models import ROOM_STEP_H, HouseModel
from .store import SAMPLE_COLS

log = logging.getLogger(__name__)

CHUNK_DAYS = 3


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    return datetime.fromisoformat(value).timestamp()


def _by_hour(stats: dict, entity: str | None, key: str = "mean") -> dict[int, float]:
    out: dict[int, float] = {}
    for e in stats.get(entity, []) if entity else []:
        v = e.get(key)
        if v is not None:
            out[int(_ts(e["start"]))] = v
    return out


def _indoor(stats: dict, sources: list, ref: dict[int, float]):
    """Innentemperatur je Stunde aus der Langzeitstatistik (seit 0.42.1): je Raum der aktuelle Sensor, wo er nichts hat
    sein Alias – jede Quelle um ihren Versatz zum gespeicherten Hausmittel berichtigt (``indoor_mean``), Quellen, die
    nicht passen, bleiben weg. Gibt (Funktion Stunde → (Wert, Herkunft), Stunden mit Werten, Prüfung je Quelle) zurück."""
    ids = sorted({i for _, cur, alias in sources for i in (cur, alias) if i})
    series = {sid: _by_hour(stats, sid) for sid in ids}
    fitted = indoor_mean.fits(series, ref)
    rooms = [[sid for sid in (cur, alias) if sid and fitted[sid]["ok"]] for _, cur, alias in sources]

    def at(hour: int) -> tuple[float | None, str | None]:
        vals = []
        for room in rooms:
            hit = next((sid for sid in room if hour in series[sid]), None)
            if hit:
                vals.append((hit, series[hit][hour]))
        value, used = indoor_mean.mean(vals, fitted)
        return value, "stat:" + ",".join(sorted(used)) if used else None

    hours = set().union(*(set(series[sid]) for room in rooms for sid in room))
    return at, hours, fitted


async def backfill_statistics(ha: HAClient, eng: Engine, days: int) -> int:
    """Langzeitstatistik (Gas, außen, innen) in die Stundenwerte – zusammenführend (seit 0.42.1): Stunden ohne eigene
    Messwerte bekommen Gas aus der Statistik und eine Außentemperatur, wo es eine gibt; eine Innentemperatur nur, wo
    keine gespeichert ist (``_indoor``: je Quelle um ihren Versatz zum Hausmittel berichtigt – ein Mittel über weniger
    Räume verschöbe sie). Stunden mit eigenen Messwerten bekommen nur die fehlende Innen- bzw. Außentemperatur. Die
    Prüfung je alter Quelle steht danach beim Alias (``room_alias.note_checks``). Gibt die Zahl der Stunden ohne
    eigene Messwerte zurück, die aus der Statistik geschrieben wurden."""
    f = eng.found
    if days <= 0 or not f.gas:
        return 0
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    sources = room_alias.sources(eng.store, f)
    temp_ids = sorted({i for _, cur, alias in sources for i in (cur, alias) if i})
    stats = await ha.statistics([f.gas, *f.outdoor, *temp_ids], start, end) or {}

    gas_change = _by_hour(stats, f.gas, "change")
    if not gas_change:
        sums = sorted(_by_hour(stats, f.gas, "sum").items())
        gas_change = {
            h: s2 - s1 for (_, s1), (h, s2) in zip(sums, sums[1:]) if s2 >= s1
        }
    kwh = {h: v * eng.gas_factor for h, v in gas_change.items()}
    clean, spikes = plausibility.gas_series(kwh, eng.nominal_kw())
    for hour, value in spikes.items():
        log.info("Langzeitstatistik %s: Gaszähler-Sprung um %.1f kWh (Nachtrag?) – nicht gelernt",
                 datetime.fromtimestamp(hour).strftime("%d.%m.%Y %H:00"), value)
    outdoor = [_by_hour(stats, e) for e in f.outdoor]
    ref = {r["ts"]: r["t_in"] for r in eng.store.query("SELECT ts, t_in FROM hourly WHERE t_in IS NOT NULL")}
    indoor, in_hours, fitted = _indoor(stats, sources, ref)

    def t_out(hour: int) -> float | None:
        vals = [o[hour] for o in outdoor if hour in o]
        return min(vals) if vals else None

    # Stunden mit eigenen Messwerten nicht überschreiben – dort kennt HeatPilot Warmwasser,
    # Anwesenheit und Urlaub (die Langzeitstatistik nicht); sie bildet replay/aggregate_hour
    own = {r["h"] for r in eng.store.query(
        "SELECT ts - ts % 3600 AS h, COUNT(*) AS n FROM samples GROUP BY h HAVING n >= 2")}
    rows = []
    for hour in sorted(set(gas_change) - own):
        t_in, src = indoor(hour)  # Spalten wie HOURLY_COLS; Anteile bleiben, wie sie gespeichert sind
        rows.append((hour, t_out(hour), t_in, clean[hour], None, None, None, spikes.get(hour), None, None, src))
    eng.store.add_hourly(rows, replace=("gas_kwh", "gas_spike_kwh"), keep=("t_in",))
    temp_hours = set().union(*outdoor, in_hours)
    filled = eng.store.fill_hourly((h, t_out(h), *indoor(h)) for h in sorted(temp_hours & own))
    eng.store.commit()
    room_alias.note_checks(eng.store, fitted)
    unused = sorted(sid for sid, fit in fitted.items() if not fit["ok"])
    log.info("Langzeitstatistik: %d Stunden übernommen, in %d Stunden mit eigenen Messwerten Temperaturen ergänzt "
             "(Innentemperatur von %d Räumen, %d davon mit altem Sensor%s)", len(rows), filled, len(sources),
             sum(1 for _, _, a in sources if a), f"; nicht verwendet: {', '.join(unused)}" if unused else "")
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


async def _fetch(ha: HAClient, plain: list[str], with_attrs: list[str], start_ts: int, end_ts: int) -> dict[str, list]:
    """Recorder-Historie in Abschnitten von ``CHUNK_DAYS`` Tagen: {Entität: [(ts, Zustand, Attribute)]}."""
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
    return series


class _RoomReader(SampleMixin):
    """Raumwerte aus einem Satz Zustände – wie live (``SampleMixin.room_reading``), ohne Engine."""

    def __init__(self) -> None:
        self.states: dict[str, dict] = {}

    def _val(self, entity_id: str | None):
        return (self.states.get(entity_id) or {}).get("state") if entity_id else None


def _room_row(reader: _RoomReader, room, ts: int, spikes, key: str) -> tuple | None:
    """Ein Messpunkt eines Raums (ts, Schlüssel, Temperatur, Soll, Ventil, Feuchte) mit den Grenzen und dem
    Spitzenfilter wie live; None, wenn das Schlüsselgerät keinen Zustand hat."""
    row = reader.room_reading(room)
    if row is None:
        return None
    _, temp, target, valve, hum = row
    temp = spikes(key, ts, plausibility.in_range("room_temp", temp))
    return (ts, key, temp, plausibility.in_range("room_target", target), plausibility.in_range("valve", valve),
            plausibility.in_range("humidity", hum))


async def backfill_rooms(ha: HAClient, eng: Engine, rooms: list, days: int, key_of: dict | None = None) -> int:
    """Raumwechsel (seit 0.42.1): nur die HA-Historie der Geräte dieser Räume nachholen – Temperatur, Soll, Ventil
    und Feuchte im Raster der Messpunkte. Ein schon gespeicherter Messpunkt des Raums bleibt, wie er ist; Kessel-,
    Personen- und Stundenwerte, Langzeitstatistik und andere Räume bleiben unberührt, es wird nichts gelöscht.
    ``key_of``: Werte unter einem anderen Schlüssel speichern („Hausdaten wiederherstellen“: alte Entität → Raum).
    Gibt die Zahl der neu gespeicherten Messpunkte zurück."""
    if days <= 0 or not rooms:
        return 0
    key_of = key_of or {}
    step = eng.cfg.sample_interval_s
    end_ts = int(time.time())
    end_ts -= end_ts % step
    start_ts = end_ts - days * 86400
    plain = sorted({d[k] for r in rooms for d in r.devices for k in ("heating_power", "humidity") if d.get(k)})
    series = await _fetch(ha, plain, [c for r in rooms for c in r.climates], start_ts, end_ts)
    cur = {ent: _Cursor(sorted(ser, key=lambda x: x[0])) for ent, ser in series.items() if ser}
    keys = sorted({key_of.get(r.climate, r.climate) for r in rooms})
    have = {(r["room"], r["ts"]) for r in eng.store.query(
        f"SELECT room, ts FROM room_samples WHERE ts >= ? AND room IN ({','.join('?' * len(keys))})",
        (start_ts, *keys))}  # schon gespeichert: bleibt, wie es ist
    reader, spikes = _RoomReader(), plausibility.SpikeFilter()
    rows, subst, kept = [], [], 0
    for ts in range(start_ts, end_ts, step):
        reader.states = {e: {"state": hit[0], "attributes": hit[1]} for e, c in cur.items() if (hit := c.at(ts))}
        reader.valve_subst = set()
        for r in rooms:
            row = _room_row(reader, r, ts, spikes, key_of.get(r.climate, r.climate)) if reader.states else None
            if row is None:
                continue
            if (row[1], ts) in have:
                kept += 1
                continue
            rows.append(row)
            if r.climate in reader.valve_subst:
                subst.append((ts, row[1]))
    eng.store.add_room_samples(rows, replace=False)
    eng.store.db.executemany("INSERT OR IGNORE INTO valve_subst VALUES(?, ?)", subst)
    eng.store.commit()
    log.info("Historie der Räume %s nachgeholt: %d neue Messpunkte (%d schon gespeichert, bleiben)",
             ", ".join(keys), len(rows), kept)
    return len(rows)


def room_starts(series: dict[str, list], rooms, start_ts: int) -> dict[str, int]:
    """Je Raum der erste Zeitpunkt der HA-Historie seines Schlüsselgeräts (nicht vor ``start_ts``) – erst ab dort
    schreibt das Neueinlesen Raumwerte (``room_reading`` braucht den Schlüssel), also werden auch erst ab dort die
    gespeicherten ersetzt (0.42.1). Räume ohne HA-Historie fehlen: ihre Werte bleiben."""
    out = {}
    for r in rooms:
        ser = series.get(r.climate)
        if ser:
            out[r.climate] = max(int(min(t for t, _, _ in ser)), start_ts)
    return out


def observe_stored_persons(eng: Engine, until: int | None = None) -> int:
    """Neuberechnung: Personen aus den eigenen Messwerten vor ``until`` nachlernen (Gewohnheit, Reisen).

    Gleicher Weg wie live (``observe_persons`` je Zeitpunkt, Personen in derselben Reihenfolge,
    Urlaub wie damals gespeichert). Ab ``until`` liefert die HA-Historie die Werte – nichts doppelt.
    """
    eng._persons_from_store = True
    where, params = ("WHERE ts < ?", (until,)) if until is not None else ("", ())
    rows = eng.store.query(f"SELECT ts, person, home, dist_km FROM person_samples {where} ORDER BY ts", params)
    if not rows:
        return 0
    vacation = {r["ts"] for r in eng.store.query(f"SELECT ts FROM samples {where} {'AND' if where else 'WHERE'} "
                                                  "vacation = 1", params)}
    order = {p: i for i, p in enumerate(eng.found.persons)}
    count = 0
    for ts, group in groupby(rows, key=lambda r: r["ts"]):
        persons = sorted(((r["person"], r["home"], r["dist_km"]) for r in group if r["person"] in order),
                         key=lambda x: order[x[0]])
        if persons:
            eng.observe_persons(ts, persons, ts in vacation)
            count += 1
    log.info("Anwesenheit: %d eigene Messpunkte vor der HA-Historie nachgelernt", count)
    return count


def merge_stored(sample: dict, row) -> None:
    """Neueinlesen (seit 0.42.1): Werte, zu denen die HA-Historie nichts hat – etwa Kesselwerte direkt von EMS-ESP –,
    aus dem gespeicherten Messpunkt übernehmen, bevor Ladeerkennung und Warmwasser ihn sehen. Ein gespeicherter Wert
    wird so nie durch „unbekannt“ ersetzt."""
    if row is None:
        return
    for col in SAMPLE_COLS:
        if sample.get(col) is None and row[col] is not None:
            sample[col] = row[col]


def _prepare_reload(eng: Engine, series: dict, start_ts: int, end_ts: int) -> tuple[int | None, dict]:
    """Vor dem Neueinlesen: Was HA liefert, ersetzt die gespeicherten Werte desselben Zeitraums. Seit 0.42.1
    zusammenführend – Messpunkte und Personen behalten, wozu HA nichts hat (``merge_stored``); Raumwerte werden je Raum
    erst ab seiner HA-Historie ersetzt (``room_starts`` – übernommene ältere Werte und Schlüssel ohne Raum bleiben),
    Brennerereignisse nur mit Brenner-Entität in HA (sonst doppelt bzw. – kamen sie von EMS-ESP – verloren).
    Gibt (Beginn der HA-Daten, gespeicherte Messpunkte des Zeitraums je Zeitpunkt) zurück."""
    firsts = [ser[0][0] for ser in series.values() if ser]
    data_start = None
    if firsts:
        data_start = max(int(min(firsts)), start_ts)
        burner = eng.found.boiler.get("burner_on")
        burner_ser = series.get(burner) if burner else None
        burner_from = max(int(min(t for t, _, _ in burner_ser)), start_ts) if burner_ser else None
        eng.store.clear_reload(end_ts, room_starts(series, eng.found.rooms, data_start), burner_from)
    stored = {r["ts"]: r for r in eng.store.query("SELECT * FROM samples WHERE ts >= ? AND ts < ?", (start_ts, end_ts))}
    return data_start, stored


async def backfill_history(ha: HAClient, eng: Engine, days: int) -> int:
    """Recorder-Historie in Messpunkte, Raum- und Personenwerte – Zustände je Zeitpunkt in die Engine gespiegelt und
    wie live abgetastet; Gespeichertes wird zusammengeführt (siehe oben)."""
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
                   | {d["heating_power"] for r in f.rooms for d in r.devices if d.get("heating_power")}
                   | {d["humidity"] for r in f.rooms for d in r.devices if d.get("humidity")}
                   # seit 0.36: Hausleistung – damit nach dem ersten Start sofort Nächte da sind
                   | set(f.power) | set(f.power_exclude))
    with_attrs = f.climate_ids() + f.persons + ([f.weather] if f.weather else [])  # Bewölkung
    series = await _fetch(ha, plain, with_attrs, start_ts, end_ts)
    log.info("Historie geladen: %d Entitäten", len(series))

    data_start, stored = _prepare_reload(eng, series, start_ts, end_ts)
    observe_stored_persons(eng, data_start)  # ältere eigene Werte zuerst, wie live der Reihe nach

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
        if not eng.states:
            continue  # vor Beginn der Historie: keine leeren Messpunkte anlegen
        sample, rooms, persons = eng.snapshot()
        merge_stored(sample, stored.get(ts))
        eng.count_rejected(ts)
        eng.infer_dhw(ts, sample)
        eng._dhw_now = int(bool(sample.get("dhw")))
        eng.update_live(ts, record_events=False)  # wie live: nach der Ladeerkennung
        eng.add_solar(ts, sample)
        rooms = eng.filter_room_spikes(ts, rooms)
        eng.track_vacation_return(ts, persons)
        sample["vacation"] = int(eng.on_vacation(ts))
        sample["away_long"] = int(eng.observe_persons(ts, persons, bool(sample["vacation"])))  # wie live
        eng.store.add_sample(ts, sample)
        eng.store.add_room_samples((ts, *r) for r in rooms)
        eng.store.add_person_samples(((ts, *p) for p in persons), merge=True)
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


def replay(eng: Engine, since: int | None = None) -> None:
    """Alle Daten chronologisch nachlernen, mit Tagesständen für die Lernkurve (siehe ``replay_steps``)."""
    for _ in replay_steps(eng, since):
        pass


async def replay_async(eng: Engine, since: int | None = None) -> None:
    """Wie ``replay``, gibt aber nach jedem Tag die Ereignisschleife frei (Oberfläche und HA-Verbindung bleiben
    bedienbar, auch wenn über Monate an Daten neu gelernt wird)."""
    for _ in replay_steps(eng, since):
        await asyncio.sleep(0)


def replay_steps(eng: Engine, since: int | None = None):
    """Alle Daten chronologisch nachlernen; nach jedem Tag ein ``yield``.

    Vor ``since`` (Standard: Beginn der Recorder-Historie ``history_start``) gibt es nur Stundenwerte – aus der
    Langzeitstatistik bzw. gespeichert (nur Hausmodell); ab ``since`` werden die Stunden aus den gespeicherten
    Messwerten neu gebildet (zusammenführend) und auch die Raummodelle gelernt.
    """
    eng.house = HouseModel()
    eng.__dict__.pop("_dhw_base", None)  # Warmwasser-Profil aus der frisch geladenen Langzeitstatistik
    eng.__dict__.pop("_house_day_open", None)  # Tageswerte von vorn
    eng.store.db.execute("DELETE FROM snapshots")
    hist_start = eng.store.get("history_start") if since is None else since
    # Fremdwärme-Ereignisse werden beim Nachlernen der Raumschritte neu erkannt (vor dem Stundenwert)
    eng.room_ext = {}
    eng._room_temp_hist = {}  # Bezugsniveau der Fremdwärme-Erkennung nicht aus der Gegenwart
    eng.store.db.execute("DELETE FROM room_events WHERE start_ts >= ?", (hist_start if hist_start is not None else 0,))
    room_step = int(ROOM_STEP_H * 3600)
    hours = [r["ts"] for r in eng.store.query("SELECT ts FROM hourly ORDER BY ts")]
    if hist_start is not None:
        h0 = hist_start - hist_start % 3600 + 3600
        hours = sorted({h for h in hours if h < h0} | set(range(h0, eng._last_hour, 3600)))
    seeded = False
    for h in hours:
        if hist_start is not None and h >= hist_start:
            if not seeded:  # Hausmodell aus der Langzeitstatistik steht: Raum-Startwerte daraus
                eng.seed_room_priors()
                seeded = True
            for st in range(h, h + 3600, room_step):
                eng.learn_rooms(st, st + room_step)
            eng.aggregate_hour(h, learn=True)
        else:
            eng.learn_house_hour(h)
        if local(h).hour == 0:
            eng.take_snapshot(h)
            yield h
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
    if not eng.__dict__.get("_persons_from_store"):  # ohne HA-Historie: alle eigenen Werte
        observe_stored_persons(eng)
    await replay_async(eng)
    eng.save()
    eng.store.put("backfill_done", True)
    log.info("Datenübernahme fertig: %s", eng.house.summary(eng.cfg.design_outdoor_temp, None, None))

