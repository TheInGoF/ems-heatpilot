"""Abfragen zu „Gelerntes übernehmen“ (seit 0.42.1): ``GET api/rooms/adopt`` und ``POST api/rooms/adopt``.

* ``GET`` – nur lesen: verwaiste Raumdaten (auch heutige Mitläufer) mit der Überschneidung je Raum, Vorschläge je
  Raum und die aktuellen Räume mit ihren Tagen (``room_adopt.overview``). Das Zählen der Messwerte ist ein Durchlauf
  über alle Raumwerte – in einem Thread mit eigener, nur lesender Verbindung (``room_adopt.tally_file``); das Ergebnis
  gilt ``STATS_TTL_S`` lang bzw. bis zur nächsten Übernahme oder einer neuen Engine (Einstellungen gespeichert, neu
  eingelesen).
* ``POST {from, to}`` – Quelle nur ein verwaister Schlüssel mit Messwerten (ein heutiger Mitläufer nur in seinen
  Raum), Ziel nur ein aktueller Raum, sonst 400 mit Grund (``room_adopt.check``); ``main.App.adopt_room`` übernimmt
  unter der Sperre des Aufbaus und lernt Raum- und Ventilmodell neu (in Tagesabschnitten). Während des Neueinlesens
  409. Die Antwort nennt die Überschneidung (``skipped``: Zeitpunkte, die der Raum schon hatte, ``filled``: davon aus
  der alten Zeile ergänzt), den gemerkten alten Temperatursensor (``alias``) und ob „Hausdaten wiederherstellen“ sich
  lohnt (``restore_suggested``).
* ``GET api/rooms/aliases`` – je Raum der aktuelle Temperatursensor, der Alias (alter Sensor für die
  Langzeitstatistik, ``room_alias``) und Vorschläge, dazu alle °C-Statistiken zur Wahl (liest die Statistik-Liste
  von Home Assistant).
* ``POST api/rooms/aliases {room, stat}`` – Alias setzen (``stat`` leer: entfernen); nur aktuelle Räume, nur
  °C-Statistiken mit Mittelwert, kein Außenfühler, kein Sensor, den ein anderer Raum schon nutzt – sonst 400 mit
  Grund. Ein neuer Alias ändert keine Messwerte (wirksam mit „Hausdaten wiederherstellen“); ein ersetzter bzw.
  entfernter nimmt die Innentemperatur der Stunden zurück, die er gefüllt hat (``reverted``: so viele). Die alte
  Entität (``from``) kommt aus dem Protokoll der Übernahmen.

Liest von Home Assistant nur; schreibt nichts an Home Assistant oder den Kessel.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from . import room_adopt, room_alias
from .discovery import Registry

STATS_TTL_S = 300


def routes(app, json_response: Callable) -> list[tuple[str, str, Callable]]:
    """(Methode, Pfad, Handler) für ``web.make_app``; ``app``: main.App (eng, status, registry, adopt_room)."""
    cache: dict = {"ts": 0.0, "eng": None, "stats": None}

    async def counted(eng) -> dict:
        """Zählung (``room_adopt.tally``) – in einem Thread mit eigener, nur lesender Verbindung, damit die
        Ereignisschleife frei bleibt; geht das nicht (z. B. Datenbank ohne Datei), hier."""
        if cache["eng"] is not eng or cache["stats"] is None or time.time() - cache["ts"] > STATS_TTL_S:
            rooms = [r.climate for r in eng.found.rooms]
            try:
                res = await asyncio.to_thread(room_adopt.tally_file, eng.store.path, rooms)
            except Exception:  # noqa: BLE001
                res = room_adopt.tally(eng.store, rooms)
            cache.update(ts=time.time(), eng=eng, stats=res)
        return cache["stats"]

    async def index():
        try:
            return Registry.of(await app.registry()).lookup()
        except Exception:  # noqa: BLE001 – ohne Registry: ohne Bereich und Herkunft
            return Registry().lookup()

    async def get(_req):
        eng = app.eng
        if eng is None:
            return json_response({"starting": True, "status": app.status})
        t = await counted(eng)
        return json_response(room_adopt.overview(eng.store, eng.found, eng.states, await index(), t["stats"],
                                                 t["overlaps"]))

    async def post(req):
        if app.eng is None:
            return json_response({"ok": False, "error": f"HeatPilot startet noch ({app.status})"}, status=503)
        try:
            body = await req.json()
        except ValueError:
            return json_response({"ok": False, "error": "Ungültige Daten"}, status=400)
        if not isinstance(body, dict):
            return json_response({"ok": False, "error": "Ungültige Daten"}, status=400)
        old, new = str(body.get("from") or "").strip(), str(body.get("to") or "").strip()
        try:
            res = await app.adopt_room(old, new)
        except room_adopt.AdoptBusy as err:
            return json_response({"ok": False, "error": str(err)}, status=409)
        except room_adopt.AdoptError as err:
            return json_response({"ok": False, "error": str(err)}, status=400)
        cache["stats"] = None
        return json_response({"ok": True, **res})

    async def body_of(req) -> dict | None:
        try:
            body = await req.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    async def aliases_get(_req):
        eng = app.eng
        if eng is None:
            return json_response({"starting": True, "status": app.status})
        stat_list = await app.stat_list()
        error = None if stat_list is not None else "Liste der Langzeitstatistiken nicht lesbar – nur gemerkte Aliasse"
        return json_response(room_alias.overview(eng.store, eng.found, eng.states, await index(), stat_list, error))

    async def aliases_post(req):
        eng = app.eng
        if eng is None:
            return json_response({"ok": False, "error": f"HeatPilot startet noch ({app.status})"}, status=503)
        body = await body_of(req)
        if body is None:
            return json_response({"ok": False, "error": "Ungültige Daten"}, status=400)
        room, stat = str(body.get("room") or "").strip(), str(body.get("stat") or "").strip() or None
        why = room_alias.check(eng.store, eng.found, room, stat, await app.stat_list() if stat else None)
        if why:
            return json_response({"ok": False, "error": why}, status=400)
        before = (room_alias.get(eng.store).get(room) or {}).get("stat")
        reverted = eng.store.t_in_hours(before) if before and before != stat else 0
        adopted = room_alias.adopted_from(eng.store, room)  # selbst gewählt: alte Entität aus dem Protokoll
        alias = room_alias.put(eng.store, room, stat, adopted[-1] if adopted else None, "user", found=eng.found)
        return json_response({"ok": True, "room": room, "alias": alias, "reverted": reverted})

    return [("GET", "/api/rooms/adopt", get), ("POST", "/api/rooms/adopt", post),
            ("GET", "/api/rooms/aliases", aliases_get), ("POST", "/api/rooms/aliases", aliases_post)]
