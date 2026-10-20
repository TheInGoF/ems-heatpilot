"""Daten-Abläufe der App (seit 0.42.1): Gelerntes übernehmen, Hausdaten wiederherstellen, neu lernen, Raumwechsel.

``JobsMixin`` gehört zu ``main.App`` und nutzt von dort ``eng``, ``store``, ``cfg``, ``ha``, ``status``, die Sperre
des Aufbaus (``_lock``), ``registry`` und ``_build``:

* ``adopt_room`` – „Gelerntes übernehmen“ (``room_adopt.run_async``: das Neulernen des Raums gibt nach jedem Tag die
  Ereignisschleife frei) unter der Sperre, danach den alten Temperatursensor als Alias merken (``room_alias.detect``)
  und sagen, ob sich „Hausdaten wiederherstellen“ lohnt;
* ``start_job``/``run_job`` – „Hausdaten wiederherstellen“ (``restore_house``) bzw. „aus gespeicherten Daten neu
  lernen“ (``relearn``) im Hintergrund. Jeder Ablauf hat seinen eigenen Stand (nur den schreibt er fort), ``job``
  zeigt den neuesten; Abläufe laufen nacheinander – ein später gestarteter steht so lange als „wartet“ da;
* ``update_rooms`` – nach einem Raumwechsel nur die Historie der neuen Räume nachholen und neu lernen (als Ablauf
  ``rooms`` über ``start_job``, aus ``App.apply_settings``).

Von Home Assistant wird nur gelesen (Registry, Statistik-Liste, Langzeitstatistik, Historie); geschrieben wird nichts –
weder an Home Assistant noch an den Kessel.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import house_data, house_restore, room_adopt, room_alias
from .backfill import backfill_rooms

log = logging.getLogger("heatpilot")


class JobsMixin:
    """Übernehmen, Wiederherstellen, Neulernen und Raumwechsel für ``main.App``."""

    job: dict | None = None  # laufendes bzw. letztes Wiederherstellen/Neulernen (run_job)

    async def adopt_room(self, old: str, new: str) -> dict:
        """Gelerntes übernehmen (0.42.1, ``room_adopt.run``): Messwerte des verwaisten Schlüssels ``old`` dem Raum
        ``new`` zuordnen, dessen Raum- und Ventilmodell neu lernen – unter der Sperre des Aufbaus, damit kein
        Neuaufbau bzw. Neueinlesen dazwischenkommt (läuft eines, ``AdoptBusy``). Danach den alten Temperatursensor als
        Alias merken (``room_alias``, nur lesen) und sagen, ob „Hausdaten wiederherstellen“ sich lohnt. Schreibt
        nichts an Home Assistant."""
        if self._lock.locked():
            raise room_adopt.AdoptBusy(f"HeatPilot baut gerade neu auf ({self.status}) – bitte gleich noch einmal.")
        async with self._lock:
            if self.eng is None:
                raise room_adopt.AdoptBusy(f"HeatPilot startet noch ({self.status})")
            res = await room_adopt.run_async(self.eng, old, new)
            res["alias"] = await self._adopt_alias(old, new)
            res["restore_suggested"] = house_data.missing_in(house_data.coverage(self.eng.store))
            return res

    async def _adopt_alias(self, old: str, new: str) -> dict | None:
        """Alten Temperatursensor zu ``old`` als Alias von ``new`` merken, wenn der Raum noch keinen hat. Ein Fehler
        (Registry, Statistik-Liste nicht lesbar) hält die Übernahme nicht auf."""
        store = self.eng.store
        if new in room_alias.get(store):
            return None
        try:
            idx = (await self.registry()).lookup()
            found = room_alias.detect(store, self.eng.found, old, new, self.eng.states, idx, await self.stat_list())
        except Exception as err:  # noqa: BLE001
            log.warning("Alter Temperatursensor zu %s nicht gesucht: %s", old, err)
            return None
        if found is None:
            return None
        log.info("Alter Temperatursensor von %s für %s gemerkt: %s (%s)", old, new, found["stat"], found["how"])
        return room_alias.put(store, new, found["stat"], found["from"], found["how"])

    async def stat_list(self) -> list[dict] | None:
        """Liste der Langzeitstatistiken von HA (nur lesen); None, wenn sie nicht lesbar ist."""
        try:
            return await asyncio.wait_for(self.ha.list_statistic_ids(), timeout=60)
        except Exception as err:  # noqa: BLE001 – ältere HA bzw. Fehler: ohne Liste
            log.warning("Liste der Langzeitstatistiken nicht lesbar: %s", err)
            return None

    def job_busy(self) -> str | None:
        """Text, wenn gerade aufgebaut, neu eingelesen oder neu gelernt wird – sonst None."""
        if self.eng is None:
            return f"HeatPilot startet noch ({self.status})"
        if self.job and self.job.get("running"):
            return (f"Wartet schon: {self.job['kind']} – bitte warten, bis das fertig ist." if self.job.get("waiting")
                    else f"Läuft schon: {self.status} – bitte warten, bis das fertig ist.")
        if self._lock.locked():
            return f"HeatPilot baut gerade neu auf ({self.status}) – bitte warten, bis das fertig ist."
        return None

    def _jobs_lock(self) -> asyncio.Lock:
        """Abläufe nacheinander (auch bei einer App ohne ``__init__`` in Tests)."""
        lock = self.__dict__.get("_job_lock")
        if lock is None:
            lock = self._job_lock = asyncio.Lock()
        return lock

    def _new_job(self, kind: str) -> dict:
        job = {"kind": kind, "running": True, "waiting": self._jobs_lock().locked(), "created": int(time.time()),
               "started": None, "done": None, "result": None, "error": None}
        self.job = job  # die Oberfläche folgt dem neuesten Ablauf
        return job

    def start_job(self, kind: str, climates: set[str] | None = None) -> dict:
        """Ablauf anlegen und im Hintergrund starten; gibt seinen Stand zurück (sofort in ``job``)."""
        job = self._new_job(kind)
        asyncio.create_task(self.run_job(kind, climates, job))
        return job

    async def run_job(self, kind: str, climates: set[str] | None = None, job: dict | None = None) -> None:
        """„Hausdaten wiederherstellen“ (``restore``), „aus gespeicherten Daten neu lernen“ (``relearn``) bzw. nach
        einem Raumwechsel die Historie der neuen Räume nachholen (``rooms``) – nach dem vorigen Ablauf. Stand in
        ``job`` (Abfrage ``api/house/job``); geschrieben wird nur der eigene. Ein Fehler lässt die laufende Engine
        weiterarbeiten."""
        job = self._new_job(kind) if job is None else job
        async with self._jobs_lock():
            job.update(waiting=False, started=int(time.time()))
            try:
                if kind == "restore":
                    job["result"] = await self.restore_house()
                elif kind == "rooms":
                    job["result"] = await self.update_rooms(climates or set())
                else:
                    job["result"] = await self.relearn()
            except Exception as err:  # noqa: BLE001
                log.exception("Ablauf „%s“ fehlgeschlagen – HeatPilot arbeitet mit dem bisherigen Stand weiter", kind)
                job["error"] = f"{type(err).__name__}: {err}"
                if self.eng is not None:
                    self.status = "läuft"
            job.update(running=False, done=int(time.time()))

    async def relearn(self) -> dict | None:
        """Aus gespeicherten Daten neu lernen (0.42.1) – nichts aus Home Assistant, keine Messwerte gelöscht."""
        await self.build(backfill=False, relearn=True)
        return house_data.last_restore(self.store)

    async def restore_house(self) -> dict:
        """Hausdaten wiederherstellen (0.42.1, ``house_restore.restore``): Langzeitstatistik mit den Aliassen neu
        einlesen (nur zusammenführen), Historie alter Entitäten nachholen, dann aus den gespeicherten Daten neu lernen.
        Liest von Home Assistant nur."""
        async with self._lock:
            if self.eng is None:
                raise RuntimeError(f"HeatPilot startet noch ({self.status})")
            self.status = "stelle Hausdaten wieder her"
            try:
                idx = (await self.registry()).lookup()
            except Exception as err:  # noqa: BLE001 – ohne Registry: ohne Sensoren der alten Geräte
                log.warning("Registry nicht lesbar: %s", err)
                idx = None
            res = await house_restore.restore(self.ha, self.eng, idx, await self.stat_list())
            await self._build(backfill=False, relearn=True)
        return {**res, "relearn": house_data.last_restore(self.store)}

    async def update_rooms(self, climates: set[str]) -> dict:
        """Raumwechsel (0.42.1): nur die Historie der neuen bzw. geänderten Räume nachholen (``backfill_rooms`` –
        nichts überschrieben, keine Langzeitstatistik), dann aus den gespeicherten Daten neu lernen."""
        async with self._lock:
            if self.eng is None:
                raise RuntimeError(f"HeatPilot startet noch ({self.status})")
            rooms = [r for r in self.eng.found.rooms if r.climate in climates]
            self.status = "hole die Historie der neuen Räume"
            rows = 0
            try:
                rows = await backfill_rooms(self.ha, self.eng, rooms, self.cfg.backfill_days)
            except Exception as err:  # noqa: BLE001 – neu gelernt wird trotzdem
                log.warning("Historie der neuen Räume nicht geholt: %s", err)
            await self._build(backfill=False, relearn=True)
        return {"rooms": sorted(climates), "room_rows": rows, "relearn": house_data.last_restore(self.store)}
