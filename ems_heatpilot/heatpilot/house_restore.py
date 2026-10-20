"""Hausdaten wiederherstellen und aus gespeicherten Daten neu lernen (seit 0.42.1).

* ``prepare`` + ``relearn`` – **aus gespeicherten Daten neu lernen**: Haus-, Raum- und Ventilmodelle, Lernkurve und
  Fremdwärme-Ereignisse verwerfen (``Store.reset_models``) und aus den gespeicherten Stunden- und Messwerten neu lernen
  – ab dem ersten eigenen Messpunkt auch die Räume (``backfill.replay_async``). Holt nichts aus Home Assistant und
  löscht keine Messwerte; Warmwasser- und Personenmodelle bleiben. ``main.App`` baut dafür eine neue Engine auf
  (``App.relearn``), nach „Gelerntes übernehmen“, einem Raumwechsel und dem Wiederherstellen automatisch.
* ``restore`` – **Hausdaten wiederherstellen**: Räume ohne Alias bekommen ihren eindeutigen alten Temperatursensor
  (``room_alias.auto_assign``), dann liest HeatPilot die Langzeitstatistik mit den Aliassen neu ein – nur
  zusammenführend: Es ergänzt fehlende Innentemperaturen (je Quelle um ihren Versatz zum Hausmittel berichtigt, eine
  unpassende bleibt weg), nichts wird gelöscht oder durch „unbekannt“ ersetzt. Gibt es eine alte climate-Entität
  noch in Home Assistant – aus dem Protokoll der Übernahmen (``rooms.adopted``) oder als ``from`` eines Alias –, holt
  es ihre Recorder-Historie als Raumwerte des neuen Raums nach – nur zu Zeitpunkten, an denen der Raum keinen
  Messpunkt hat. Das Ergebnis sagt je alter Entität, ob und wie viel nachgeholt wurde (und warum nicht), und je Alias,
  ob er passt. Danach lernt ``App`` neu.
* Seit der Prüfung von 0.42.1: ``prepare`` verwirft nur die Raum- und Ventilmodelle der aktuellen Räume (die alter
  Schlüssel bleiben – mit ihnen der Name) und merkt sich in kv ``relearn_pending``, dass neu gelernt wird – bricht
  ein Neustart das Nachlernen ab, setzt ``main.App`` es beim Start fort (``pending``). ``keep_models`` und
  ``put_back`` behalten Warmwasser- und Personenmodelle über „Historie neu einlesen“ hinweg.

Liest von Home Assistant nur (Statistik, Statistik-Liste, Historie, Registry); schreibt nichts an HA oder den Kessel.
Protokoll in kv ``house.restored``.
"""

from __future__ import annotations

import logging
import time

from . import backfill, house_data, room_adopt, room_alias
from .dhw import DhwModel
from .discovery import Room, device_sensors
from .models import ROOM_STEP_H
from .presence import PersonLearner
from .values import local

log = logging.getLogger(__name__)

KV_LOG = house_data.KV_RESTORED   # [{ts, kind, …}]
LOG_MAX = 20
PENDING = "relearn_pending"       # {ts, since}: Modelle verworfen, Nachlernen noch nicht fertig
KEEP = ("model.dhw", "model.person.")  # bleiben über „Historie neu einlesen“ (keep_models)
last = house_data.last_restore


def _log(store, entry: dict) -> None:
    store.put(KV_LOG, [*(store.get(KV_LOG) or []), entry][-LOG_MAX:])


def relearn_since(store) -> int | None:
    """Erster eigener Messpunkt – ab dort bildet die Neuberechnung die Stunden neu und lernt die Räume; davor zählen
    die gespeicherten Stundenwerte (Langzeitstatistik). Ohne Messpunkte der Beginn der HA-Historie."""
    first = store.query("SELECT MIN(ts) AS t FROM samples")[0]["t"]
    return first if first is not None else store.get("history_start")


def prepare(store, rooms: list[str] | None = None) -> int | None:
    """Vor dem Aufbau der neuen Engine: Modelle verwerfen, die neu gelernt werden – Raum- und Ventilmodelle nur der
    aktuellen Räume ``rooms``. Merkt vorher ``relearn_pending`` (Neustart mittendrin: ``pending``). Gibt den Beginn
    (``since``) zurück."""
    since = relearn_since(store)
    store.put(PENDING, {"ts": int(time.time()), "since": since})
    store.reset_models(None if since is None else local(since).date().isoformat(), rooms)
    return since


def pending(store) -> bool:
    """Wurden Modelle verworfen, ohne dass das Nachlernen fertig wurde (Neustart mittendrin)?"""
    return bool(store.get(PENDING))


def keep_models(store) -> dict:
    """Vor „Historie neu einlesen“: Warmwasser- und Personenmodelle sichern – sie haben über Monate gelernt, das
    Neueinlesen sähe nur das Recorder-Fenster. {kv-Schlüssel: Wert}."""
    rows = store.query("SELECT key FROM kv WHERE key = ? OR key LIKE ?", (KEEP[0], KEEP[1] + "%"))
    return {r["key"]: store.get(r["key"]) for r in rows}


def put_back(eng, kept: dict) -> None:
    """Nach dem Neueinlesen: die gesicherten Warmwasser- und Personenmodelle wieder einsetzen (in der Engine und
    gespeichert) – statt der aus dem Recorder-Fenster neu gelernten."""
    for key, value in kept.items():
        eng.store.put(key, value)
        if key == KEEP[0]:
            eng.dhw = DhwModel.from_dict(value)
        elif key[len(KEEP[1]):] in eng.persons:
            eng.persons[key[len(KEEP[1]):]] = PersonLearner.from_dict(value)
    if kept:
        log.info("Warmwasser- und Personenmodelle behalten (%d), nicht aus dem Recorder-Fenster neu gelernt", len(kept))


async def relearn(eng, since: int | None) -> dict:
    """Auf der frischen Engine (nach ``prepare``): bis jetzt aus den gespeicherten Daten nachlernen und speichern."""
    t0 = time.monotonic()
    now = int(time.time())
    eng._last_room_step = now - now % int(ROOM_STEP_H * 3600)
    eng._last_hour = now - now % 3600
    await backfill.replay_async(eng, since)
    eng.save()
    eng.store.put("backfill_done", True)  # sonst holte der nächste Start alles aus HA neu
    eng.store.put(PENDING, None)
    cov = house_data.coverage(eng.store)
    entry = {"ts": now, "kind": "relearn", "since": since, "hours": cov["usable"], "no_t_in": cov["no_t_in"],
             "house_updates": eng.house.rls.updates, "seconds": round(time.monotonic() - t0, 1)}
    _log(eng.store, entry)
    log.info("Aus gespeicherten Daten neu gelernt (Räume ab %s): Hausmodell aus %d von %d Stunden mit allen Werten "
             "gelernt, %d Räume (%.1f s)", "–" if since is None else local(since).strftime("%d.%m.%Y"),
             eng.house.rls.updates, cov["usable"], len(eng.rooms), entry["seconds"])
    return entry


def old_entities(eng) -> list[tuple[str, str]]:
    """Alte climate-Entitäten und ihr Raum: aus dem Protokoll der Übernahmen (``rooms.adopted``) und als ``from``
    eines Alias – je alter Entität der zuletzt genannte Raum. [(alt, Raum)]."""
    pairs: dict[str, str] = {}
    for e in eng.store.get(room_adopt.KV_LOG) or []:
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            pairs[e["from"]] = e["to"]
    for room, a in room_alias.get(eng.store).items():
        if a.get("from"):
            pairs.setdefault(a["from"], room)
    return sorted(pairs.items())


def old_rooms(eng, idx) -> tuple[list[Room], dict[str, str], list[dict]]:
    """Alte climate-Entitäten (``old_entities``), die es in Home Assistant noch gibt, als Räume zum Nachholen ihrer
    Historie – mit ihren Sensoren laut Registry; dazu, unter welchem Raum ihre Werte gespeichert werden, und je alter
    Entität, warum nicht ({from, to, why})."""
    rooms, key_of, skipped = [], {}, []
    current = set(eng.found.climate_ids())
    for old, room in old_entities(eng):
        why = ("gehört wieder zu einem aktuellen Raum" if old in current
               else "nicht mehr in Home Assistant" if old not in eng.states
               else "ihr Raum ist nicht mehr gewählt" if eng.found.room_of(room) is None else None)
        if why:
            skipped.append({"from": old, "to": room, "why": why})
            continue
        sensors = device_sensors(old, idx, eng.states) if idx is not None else {}
        rooms.append(Room(name=old, climate=old, heating_power=sensors.get("heating_power"),
                          humidity=sensors.get("humidity")))
        key_of[old] = room
    return rooms, key_of, skipped


async def _fetch_old(ha, eng, idx) -> tuple[int, list[dict]]:
    """Historie alter Entitäten nachholen (je Entität für sich, damit das Ergebnis sie einzeln nennt)."""
    rooms, key_of, report = old_rooms(eng, idx)
    total = 0
    for r in rooms:
        n = await backfill.backfill_rooms(ha, eng, [r], eng.cfg.backfill_days, key_of)
        total += n
        report.append({"from": r.climate, "to": key_of[r.climate], "rows": n,
                       "why": None if n else "keine Zeitpunkte ohne eigenen Messpunkt im Recorder-Fenster"})
    return total, sorted(report, key=lambda e: e["from"])


def alias_report(store, found) -> list[dict]:
    """Je Raum mit Alias: welcher alte Sensor und ob er zum gespeicherten Hausmittel passt (``check``)."""
    out = []
    for room, a in sorted(room_alias.get(store).items()):
        r, c = found.room_of(room), a.get("check") or {}
        out.append({"room": room, "name": r.name if r else room, "stat": a.get("stat"), "ok": c.get("ok"),
                    "offset": c.get("offset"), "why": c.get("why")})
    return out


async def restore(ha, eng, idx, stat_list) -> dict:
    """Hausdaten wiederherstellen (siehe Moduldocstring) – ohne das Neulernen, das macht danach ``App``."""
    t0 = time.monotonic()
    before = house_data.coverage(eng.store)
    assigned = room_alias.auto_assign(eng.store, eng.found, eng.states, idx, stat_list) if stat_list else []
    hours = await backfill.backfill_statistics(ha, eng, eng.cfg.statistics_backfill_days)
    room_rows, old = await _fetch_old(ha, eng, idx)
    after = house_data.coverage(eng.store)
    entry = {"ts": int(time.time()), "kind": "restore", "assigned": assigned,
             "aliases": len(room_alias.get(eng.store)), "statistics_hours": hours, "room_rows": room_rows,
             "old": old, "checks": alias_report(eng.store, eng.found),
             "usable_before": before["usable"], "usable_after": after["usable"],
             "no_t_in_before": before["no_t_in"], "no_t_in_after": after["no_t_in"],
             "seconds": round(time.monotonic() - t0, 1)}
    _log(eng.store, entry)
    log.info("Hausdaten wiederhergestellt: Stunden mit Innentemperatur %d → %d, ohne %d → %d, %d Raumwerte alter "
             "Entitäten nachgeholt, %d Aliasse neu gesetzt", before["usable"], after["usable"], before["no_t_in"],
             after["no_t_in"], room_rows, len(assigned))
    return entry
