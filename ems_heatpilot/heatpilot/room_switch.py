"""Thermostatwechsel entscheiden (seit 0.44): was mit den Daten eines Raums geschieht, dessen Schlüssel wechselte.

``room_identity`` erkennt den Wechsel (offen in kv ``rooms.switches``); hier wählt der Nutzer (``GET/POST
api/rooms/switch``, ``web_switch``):

* ``same`` – gleiches Thermostat, neue Anbindung (Tado-Cloud → HomeKit, HA-Integration neu): alle Daten und
  Gelerntes ziehen um wie bei „Gelerntes übernehmen“ (``room_adopt.adopt`` in einer Transaktion), Raum- und
  Ventilmodell lernen aus den Daten neu.
* ``other`` – anderes Thermostat: Raumwerte (Temperatur, Soll, Feuchte), Fremdwärme-Ereignisse und Tagesstände
  ziehen um, das Raummodell (Aufheizen/Auskühlen) lernt wie bei ``same`` aus ihnen neu. Das Ventil lernt neu: Die
  alte Ventilöffnung ergänzt keinen Messpunkt des neuen Thermostats, das Ventil-Protokoll bleibt beim alten Schlüssel,
  und erst ab dem ersten Wert des neuen Thermostats (spätestens dem Wechsel; „Ventil lernt ab“, ``valve_since``)
  zählen Ventilwerte fürs Ventilmodell, den Abgleich und die Nachprüfung. Die alten Ventilwerte bleiben gespeichert – das Raummodell braucht sie als Wärmezufuhr.
* ``new`` – alles neu lernen: nichts zieht um, die alten Werte bleiben „ohne Raum“ (später mit „Gelerntes
  übernehmen“ noch übernehmbar).

Dazu ``follow`` (HA hat nur umbenannt: wie ``same`` ohne Rückfrage) und ``relearn_valve`` („Ventil neu lernen“ je Raum:
nur das Ventilmodell von vorn, ab jetzt; nichts gelöscht, das Verlernte im Protokoll). Schreibt nichts an Home
Assistant oder den Kessel.
"""

from __future__ import annotations

import logging
import time

from . import room_adopt, room_ids, room_identity, valve_learn, valve_since
from .config import save_settings
from .discovery import Registry
from .room_adopt import AdoptBusy, AdoptError
from .valve_model import ValveModel
from .values import local

log = logging.getLogger(__name__)

CHOICES = {
    "same": "Gleiches Thermostat, neue Anbindung – alle Daten und Gelerntes ziehen um",
    "other": "Anderes Thermostat – Raumwerte ziehen um, das Ventil lernt ab dem Wechsel neu",
    "new": "Alles neu lernen – nichts zieht um, die alten Werte bleiben ohne Raum",
}


def prepare(app, found, reg) -> list[dict]:
    """Beim Neuaufbau: fehlende Kennungen ergänzen (erster Start von 0.44, gespeichert nur mit eigenen Einstellungen),
    den Räumen ihre Kennung geben und Wechsel erkennen. Gibt die Umbenennungen durch HA zurück (``follow``)."""
    entries = room_ids.migrate(app.cfg.rooms)
    if entries is not None:
        app.cfg.rooms = entries
        try:
            if app.configured:
                save_settings({"rooms": entries})
                log.info("Räume: feste Kennungen ergänzt (%d Einträge)", len(entries))
        except OSError as err:  # nicht gespeichert: die Kennungen ergeben sich beim nächsten Start gleich
            log.warning("Räume: Kennungen nicht gespeichert (%s)", err)
    room_ids.label(found, app.cfg.rooms, app.store.get(room_identity.KEY))
    try:
        return room_identity.observe(app.store, found, Registry.of(reg).lookup())
    except Exception:  # noqa: BLE001 – ein Fehler hier hält den Aufbau nicht auf
        log.exception("Räume: Thermostatwechsel nicht geprüft")
        return []


PENDING = "rooms.relearn_pending"  # [Schlüssel]: nach einem Umzug noch neu zu lernen (ein Neustart setzt fort)


async def _move(eng, old: str, new: str, valve: bool, since: int, close=None) -> dict:
    """Daten von ``old`` nach ``new`` in einer Transaktion – dazu „Ventil lernt ab“, das Schließen des Wechsels
    (``close(write, res)``) und die Marke fürs Neulernen –, danach den Raum aus ihnen neu lernen und speichern. Vor
    ``since`` gewinnen die eigenen Werte des Raums (alter Schlüssel) gegen die HA-Historie des neuen Thermostats; bei
    ``valve=False`` lernt das Ventil ab ``since``."""
    room = eng.found.room_of(new)

    def then(db, res):
        def write(key, value):
            room_adopt._put(db, key, value)
        valve_since.move(eng.store, old, new, write)
        if not valve:  # vor dem Neulernen: das Ventilmodell lernt erst ab dem Wechsel
            valve_since.put(eng.store, new, max(int(since), valve_since.of(eng.store, new)), write)
        if close is not None:
            close(write, res)
        write(PENDING, sorted({*pending(eng.store), new}))

    res = room_adopt.adopt(eng.store, old, new, binary=valve_learn.binary_device(room), valve=valve, cut=int(since),
                           then=then)
    await _relearn(eng, new)
    return res


def pending(store) -> list[str]:
    value = store.get(PENDING) or []
    return [k for k in value if isinstance(k, str)] if isinstance(value, list) else []


async def _relearn(eng, key: str) -> None:
    """Raum aus den Daten neu lernen, speichern, Marke löschen."""
    await eng.relearn_adopted_async(key)
    eng._advice = None
    eng.save()
    eng.store.put(PENDING, [k for k in pending(eng.store) if k != key])


async def resume(eng) -> None:
    """Nach einem Neustart mitten im Neulernen (Marke ``rooms.relearn_pending``): die Räume fertig lernen."""
    keys = {r.climate for r in eng.found.rooms}
    for key in pending(eng.store):
        if key not in keys:
            eng.store.put(PENDING, [k for k in pending(eng.store) if k != key])
            continue
        try:
            log.info("Raum %s: Neulernen nach dem Thermostatwechsel fortsetzen", key)
            await _relearn(eng, key)
        except Exception:  # noqa: BLE001 – bleibt markiert, nächster Start versucht es wieder
            log.exception("Raum %s: Neulernen nicht fortgesetzt", key)


async def follow(eng, renames: list[dict]) -> None:
    """HA hat nur umbenannt: Daten ohne Rückfrage zum neuen Schlüssel (wie ``same``). Ein Fehler bleibt im Log – die
    alten Werte liegen dann „ohne Raum“ und lassen sich übernehmen."""
    keys = {r.climate for r in eng.found.rooms}
    for ch in renames:
        if ch["old_key"] in keys or not room_identity.has_data(eng.store, ch["old_key"]):
            continue
        try:
            res = await _move(eng, ch["old_key"], ch["new_key"], True, int(time.time()))
            log.info("Raum %s: von HA umbenannt %s → %s – %d Messpunkte übernommen", ch["name"], ch["old_key"],
                     ch["new_key"], res["rows"])
        except Exception:  # noqa: BLE001
            log.exception("Raum %s: Übernahme nach Umbenennung fehlgeschlagen", ch["name"])


async def _index(app):
    try:
        return Registry.of(await app.registry()).lookup()
    except Exception:  # noqa: BLE001 – ohne Registry: ohne Gerät im Protokoll
        return Registry().lookup()


def _room(eng, room_id: str):
    return next((r for r in eng.found.rooms if r.room_id == room_id), None)


async def resolve(app, room_id: str, choice: str) -> dict:
    """Wahl zu einem offenen Wechsel ausführen – unter der Sperre des Aufbaus (läuft einer: ``AdoptBusy``). Gründe,
    warum es nicht geht, als ``AdoptError``."""
    if choice not in CHOICES:
        raise AdoptError(f"Unbekannte Wahl „{choice}“ – erlaubt: {', '.join(CHOICES)}.")
    if app._lock.locked():
        raise AdoptBusy(f"HeatPilot baut gerade neu auf ({app.status}) – bitte gleich noch einmal.")
    async with app._lock:
        eng = app.eng
        if eng is None:
            raise AdoptBusy(f"HeatPilot startet noch ({app.status})")
        sw, room = room_identity.switch_of(eng.store, room_id), _room(eng, room_id)
        if sw is None:
            raise AdoptError("Für diesen Raum ist kein Thermostatwechsel offen.")
        if room is None or room.climate != sw["new_key"]:
            raise AdoptError("Der Raum hat inzwischen ein anderes Thermostat – bitte die Seite neu laden.")
        res: dict = {"rows": 0, "skipped": 0, "filled": 0, "from_days": 0}
        idx = await _index(app)

        def close(write=None, moved=None):
            room_identity.close(eng.store, room_id, room, idx, choice, {"rows": (moved or res)["rows"]}, write=write)
        if choice != "new" and room_identity.has_data(eng.store, sw["old_key"]):
            res = await _move(eng, sw["old_key"], room.climate, choice == "same", sw["since"], close)
        else:
            close()
        log.info("Raum %s: Wechsel %s → %s – %s (%d Messpunkte)", room.name, sw["old_key"], room.climate,
                 CHOICES[choice], res["rows"])
        return {**res, "choice": choice, "room_id": room_id, "name": room.name, "old_key": sw["old_key"],
                "new_key": room.climate, "valve_since": _iso(valve_since.of(eng.store, room.climate))}


def new_spans(store, rooms) -> dict[str, tuple[int, int]]:
    """Für ``backfill_rooms``: Räume mit offenem Wechsel holen die HA-Historie ihres neuen Thermostats erst ab dem
    Wechsel – davor gelten die eigenen Werte unter dem alten Schlüssel."""
    since = {(s["room_id"], s.get("new_key")): int(s.get("since") or 0) for s in room_identity.open_switches(store)}
    return {r.climate: (since[(r.room_id, r.climate)], 2**62) for r in rooms if (r.room_id, r.climate) in since}


def _iso(ts: int) -> str | None:
    return local(ts).isoformat() if ts else None


async def relearn_valve(app, room_id: str) -> dict:
    """„Ventil neu lernen“: das Ventilmodell des Raums von vorn, es lernt ab jetzt (``valve_since``). Messwerte und
    Protokolle bleiben; was verlernt wurde, steht im Protokoll (kv ``rooms.identity_log``)."""
    if app._lock.locked():
        raise AdoptBusy(f"HeatPilot baut gerade neu auf ({app.status}) – bitte gleich noch einmal.")
    async with app._lock:
        eng = app.eng
        if eng is None:
            raise AdoptBusy(f"HeatPilot startet noch ({app.status})")
        room = _room(eng, room_id)
        if room is None:
            raise AdoptError(f"„{room_id or '–'}“ ist kein aktueller Raum.")
        old = eng.valves.get(room.climate)
        if old is None:
            raise AdoptError(f"Zu {room.name} lernt HeatPilot kein Ventil (Heizkreis von EMS-ESP).")
        now = int(time.time())
        valve_since.put(eng.store, room.climate, now)
        eng.valves[room.climate] = ValveModel.from_dict(None, valve_learn.binary_device(room))
        eng.store.put(f"model.valve.{room.climate}", eng.valves[room.climate].to_dict())
        room_identity.note(eng.store, {"room_id": room_id, "name": room.name, "what": "ventil neu",
                                       "key": room.climate, "forgot": _valve_brief(old)}, now)
        log.info("Raum %s: Ventil lernt ab jetzt neu (verlernt: %s)", room.name, _valve_brief(old))
        return {"room_id": room_id, "key": room.climate, "name": room.name, "valve_since": _iso(now)}


def _valve_brief(model) -> dict:
    """Was ein Ventilmodell wusste – fürs Protokoll."""
    return {"kind": model.kind, "valve_n": round(model.valve_n), "steady_h": round(model.steady_h, 1),
            "learned_until": model.learned_until}


def _old_stats(store, key: str) -> dict:
    row = store.query(f"SELECT COUNT(*) AS n, COUNT(DISTINCT {room_adopt._day_sql()}) AS d, MIN(ts) AS a, "
                      "MAX(ts) AS b FROM room_samples WHERE room = ?", (key,))[0]
    return {"old_rows": row["n"], "old_days": row["d"], "old_first": _iso(row["a"]), "old_last": _iso(row["b"])}


def overview(eng, idx) -> dict:
    """Für ``GET api/rooms/switch``: offene Wechsel (mit Namen, Herkunft und Umfang der alten Daten), die Wahlen, die
    Räume mit Kennung und „Ventil lernt ab“ und die letzten Einträge des Protokolls."""
    store, states = eng.store, eng.states
    switches = [{**s, "since": _iso(s["since"]), "old_name": room_adopt.name_of(store, states, s["old_key"]),
                 "old_source": room_adopt.source_of(s["old_key"], states, idx),
                 "new_source": room_adopt.source_of(s["new_key"], states, idx), **_old_stats(store, s["old_key"])}
                for s in room_identity.open_switches(store)]
    cut = valve_since.get(store)
    rooms = [{"room_id": r.room_id, "key": r.climate, "name": r.name, "valve": r.climate in eng.valves,
              "valve_since": _iso(cut.get(r.climate, 0))} for r in eng.found.rooms]
    entries = (store.get(room_identity.LOG) or [])[-20:]
    return {"switches": switches, "choices": CHOICES, "rooms": rooms,
            "log": [{**e, "ts": _iso(e.get("ts", 0))} for e in reversed(entries)]}
