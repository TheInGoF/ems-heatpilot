"""Abfragen zu Hausdaten und Modellen (seit 0.42.1).

* ``GET api/models`` – Karte „Modelle“ (``model_card.collect``, dasselbe wie ``summary.models``): Haus, Räume,
  Ventile, Warmwasser mit Güte, Lernzeitraum und Warnung bei fehlender Innentemperatur; dazu ``job`` (siehe unten).
* ``POST api/house/restore`` – „Hausdaten wiederherstellen“ im Hintergrund starten (``App.run_job("restore")``):
  Langzeitstatistik mit den Aliassen neu einlesen (nur zusammenführen), Historie alter Entitäten nachholen, dann aus
  den gespeicherten Daten neu lernen. 202 mit ``job``; läuft schon etwas bzw. startet HeatPilot noch: 409.
* ``POST api/house/relearn`` – „aus gespeicherten Daten neu lernen“ im Hintergrund (``App.run_job("relearn")``):
  holt nichts aus Home Assistant und löscht keine Messwerte. 202 bzw. 409 wie oben.
* ``GET api/house/job`` – Stand des neuesten Ablaufs {kind, running, waiting, created, started, done, result, error}
  und der Status von HeatPilot; ``kind`` auch ``rooms`` (Historie der neuen Räume nach dem Speichern eines
  Raumwechsels). Abläufe laufen nacheinander; ``waiting``: wartet, bis der vorige fertig ist.

Von Home Assistant wird nur gelesen; geschrieben wird nichts – weder an Home Assistant noch an den Kessel.
"""

from __future__ import annotations

from typing import Callable

from . import model_card

JOBS = {"restore": "Hausdaten wiederherstellen", "relearn": "Aus gespeicherten Daten neu lernen",
        "rooms": "Historie der neuen Räume nachholen"}  # „rooms“ startet nur das Speichern eines Raumwechsels


def routes(app, json_response: Callable) -> list[tuple[str, str, Callable]]:
    """(Methode, Pfad, Handler) für ``web.make_app``; ``app``: main.App (eng, status, job, job_busy, run_job)."""

    def job_state() -> dict:
        return {"job": app.job, "status": app.status, "busy": app.job_busy()}

    async def models(_req):
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        return json_response({**model_card.collect(app.eng), **job_state()})

    def starter(kind: str) -> Callable:
        async def start(_req):
            busy = app.job_busy()
            if busy:
                return json_response({"ok": False, "error": busy, **job_state()}, status=409)
            app.start_job(kind)
            return json_response({"ok": True, "started": JOBS[kind], **job_state()}, status=202)
        return start

    async def job(_req):
        return json_response({**job_state(), "kinds": JOBS})

    return [("GET", "/api/models", models), ("POST", "/api/house/restore", starter("restore")),
            ("POST", "/api/house/relearn", starter("relearn")), ("GET", "/api/house/job", job)]
