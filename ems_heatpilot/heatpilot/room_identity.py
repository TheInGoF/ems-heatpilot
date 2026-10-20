"""Thermostatwechsel erkennen (seit 0.44): Welcher Schlüssel gehörte zuletzt zu welchem Raum?

kv ``rooms.identity`` merkt je fester Raumkennung (``room_ids``) den zuletzt gesehenen Schlüssel (climate-Entität)
mit Name, Bereich, Gerät und Registry-Eintrag. ``observe`` vergleicht bei jedem Neuaufbau (Start, Speichern,
Erkennung) die aktuellen Räume damit:

* Erster Start von 0.44 (noch nichts gemerkt): nur säen, kein Wechsel.
* Gleicher Schlüssel: Name, Bereich und Gerät auffrischen; ein offener Wechsel des Raums fällt weg (der Raum ist
  zurück auf seinem Schlüssel).
* Anderer Schlüssel, HA hat nur umbenannt (derselbe Registry-Eintrag bzw. dieselbe ``unique_id`` der Integration,
  oder dasselbe Gerät und die alte Entität gibt es nicht mehr): ohne Rückfrage übernehmen – ``observe`` gibt die
  Umbenennung zurück, ``room_switch.follow`` zieht alles um (Protokoll).
* Anderer Schlüssel, zum alten ist nichts gespeichert bzw. er ist Schlüssel eines anderen Raums: nur merken. Ebenso
  im Auto-Modus (Kennung ``auto:…``), wenn alter und neuer Schlüssel bis zuletzt gleichzeitig gemessen haben – zwei
  Zonen nebeneinander, kein Tausch (die alten Werte bleiben „ohne Raum“ übernehmbar).
* Sonst ein offener Wechsel {room_id, name, old_key, new_key, since} in kv ``rooms.switches`` – bis zur Wahl des
  Nutzers (``room_switch.resolve``) misst der Raum unter dem neuen Schlüssel normal weiter, der gemerkte bleibt.

Protokoll in kv ``rooms.identity_log``. Liest die Registry nur, schreibt nichts an Home Assistant.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

KEY = "rooms.identity"        # {Kennung: {key, name, area, device, platform, reg, uid, device_id, seen}}
OPEN = "rooms.switches"       # [{room_id, name, old_key, new_key, since}]
LOG = "rooms.identity_log"    # [{ts, room_id, name, what, old_key, new_key, …}]
LOG_MAX = 100
WHAT = {"rename": "umbenannt", "quiet": "ohne Daten", "parallel": "nebeneinander"}
WHY = {"rename": "HA hat umbenannt – Daten ziehen um", "quiet": "zum alten nichts umzuziehen",
       "parallel": "beide maßen nebeneinander – kein Tausch, alte Werte bleiben übernehmbar"}
PARALLEL_DAYS = 7   # Auto-Modus: so weit zurück zählen gleichzeitige Messpunkte von altem und neuem Schlüssel
PARALLEL_MIN = 12   # … ab so vielen (eine Stunde bei 5 min) maßen beide nebeneinander


def _reg(idx, key: str) -> dict:
    return (getattr(idx, "entity", None) or {}).get(key) or {}


def info(room, idx, now_ts: int) -> dict:
    """Was zur Kennung gemerkt wird: Schlüssel, Name, Bereich, Gerät und – zum Erkennen einer Umbenennung – der
    Registry-Eintrag (``id``), die ``unique_id`` der Integration und das Gerät."""
    reg = _reg(idx, room.climate)
    uid = f"{reg.get('platform')}:{reg['unique_id']}" if reg.get("unique_id") else None
    return {"key": room.climate, "name": room.name, "area": room.area, "device": room.device_name,
            "platform": room.platform, "reg": reg.get("id"), "uid": uid, "device_id": reg.get("device_id"),
            "seen": int(now_ts)}


def is_rename(prev: dict, now: dict, idx) -> bool:
    """Hat HA nur die entity_id geändert? Derselbe Registry-Eintrag bzw. dieselbe ``unique_id``, oder dasselbe Gerät
    und die alte Entität steht nicht mehr in der Registry."""
    if prev.get("reg") and prev["reg"] == now.get("reg"):
        return True
    if prev.get("uid") and prev["uid"] == now.get("uid"):
        return True
    gone = prev.get("key") not in (getattr(idx, "entity", None) or {})
    return bool(prev.get("device_id")) and prev["device_id"] == now.get("device_id") and gone


def has_data(store, key: str) -> bool:
    """Liegt unter dem Schlüssel etwas, das umziehen könnte (Messwerte oder Modelle)?"""
    return bool(store.query("SELECT 1 FROM room_samples WHERE room = ? LIMIT 1", (key,))
                or store.query("SELECT 1 FROM kv WHERE key IN (?, ?) LIMIT 1", (f"model.room.{key}",
                                                                              f"model.valve.{key}")))


def open_switches(store) -> list[dict]:
    value = store.get(OPEN) or []
    return [s for s in value if isinstance(s, dict) and s.get("room_id")] if isinstance(value, list) else []


def switch_of(store, room_id: str) -> dict | None:
    return next((s for s in open_switches(store) if s["room_id"] == room_id), None)


def note(store, entry: dict, now_ts: int | None = None, write=None) -> None:
    """Eintrag ins Protokoll (kv ``rooms.identity_log``, die letzten ``LOG_MAX``). ``write(kv, Wert)`` statt
    ``store.put`` (ohne Commit, in einer laufenden Transaktion)."""
    entries = store.get(LOG) or []
    (write or store.put)(LOG, [*entries, {"ts": int(now_ts or time.time()), **entry}][-LOG_MAX:])


def parallel(store, old: str, new: str, now_ts: int) -> bool:
    """Haben ``old`` und ``new`` in den letzten ``PARALLEL_DAYS`` Tagen zu denselben Zeitpunkten gemessen?"""
    row = store.query("SELECT COUNT(*) AS n FROM room_samples n JOIN room_samples o ON o.ts = n.ts AND o.room = ? "
                      "WHERE n.room = ? AND n.ts >= ?", (old, new, int(now_ts) - PARALLEL_DAYS * 86400))[0]
    return row["n"] >= PARALLEL_MIN


def _kind(store, found, prev: dict, now: dict, idx, room_id: str = "", now_ts: int = 0) -> str:
    """„rename“ (ohne Rückfrage übernehmen), „quiet“ bzw. „parallel“ (nur merken) oder „ask“ (offener Wechsel)."""
    if is_rename(prev, now, idx):
        return "rename"
    old = prev["key"]
    if any(r.climate == old for r in found.rooms) or not has_data(store, old):
        return "quiet"
    if room_id.startswith("auto:") and parallel(store, old, now["key"], now_ts or int(time.time())):
        return "parallel"  # Auto-Modus: zwei Zonen nebeneinander, die Kennung ist nur weitergerutscht
    return "ask"


def observe(store, found, idx, now_ts: int | None = None) -> list[dict]:
    """Aktuelle Räume mit den gemerkten Schlüsseln vergleichen (siehe oben). Gibt die Umbenennungen durch HA zurück
    [{room_id, name, old_key, new_key}] – ``room_switch.follow`` zieht ihre Daten um."""
    now_ts = int(now_ts or time.time())
    rooms = [r for r in found.rooms if r.room_id]
    known = store.get(KEY)
    if not isinstance(known, dict):  # erster Start von 0.44: nur säen
        store.put(KEY, {r.room_id: info(r, idx, now_ts) for r in rooms})
        return []
    switches = {s["room_id"]: s for s in open_switches(store)}
    renames = []
    for r in rooms:
        prev, now = known.get(r.room_id), info(r, idx, now_ts)
        if not isinstance(prev, dict) or prev.get("key") == r.climate:
            known[r.room_id] = now
            switches.pop(r.room_id, None)
            continue
        kind = _kind(store, found, prev, now, idx, r.room_id, now_ts)
        change = {"room_id": r.room_id, "name": r.name, "old_key": prev["key"], "new_key": r.climate}
        if kind == "ask":
            if switches.get(r.room_id, {}).get("new_key") != r.climate:
                switches[r.room_id] = {**change, "since": now_ts}
                log.info("Raum %s: neues Thermostat %s statt %s – unter Räume wählen, was mit den Daten geschieht",
                         r.name, r.climate, prev["key"])
            continue
        known[r.room_id] = now
        switches.pop(r.room_id, None)
        note(store, {**change, "what": WHAT[kind]}, now_ts)
        log.info("Raum %s: Schlüssel %s → %s (%s)", r.name, prev["key"], r.climate, WHY[kind])
        if kind == "rename":
            renames.append(change)
    current = {r.room_id for r in rooms}
    store.put(OPEN, [s for rid, s in switches.items() if rid in current])
    store.put(KEY, known)
    return renames


def close(store, room_id: str, room, idx, choice: str, extra: dict | None = None, now_ts: int | None = None,
          write=None) -> None:
    """Wahl getroffen: der neue Schlüssel gilt, der Wechsel ist erledigt, Protokoll. ``write`` wie bei ``note``."""
    now_ts = int(now_ts or time.time())
    write = write or store.put
    sw = switch_of(store, room_id) or {}
    known = store.get(KEY) or {}
    known[room_id] = info(room, idx, now_ts)
    write(KEY, known)
    write(OPEN, [s for s in open_switches(store) if s["room_id"] != room_id])
    note(store, {"room_id": room_id, "name": room.name, "what": choice, "old_key": sw.get("old_key"),
                 "new_key": room.climate, **(extra or {})}, now_ts, write)
