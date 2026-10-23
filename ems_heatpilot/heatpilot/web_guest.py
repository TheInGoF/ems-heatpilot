"""Gast-Ansicht (seit 0.52): der Reiter „Heizen“ fürs Lovelace-Dashboard – ohne Einstellungen.

Die HeatPilot-Seite läuft normal über Ingress und ist damit nur für Administratoren von Home Assistant erreichbar.
Damit alle im Haushalt die Heizung bedienen können (Boost, Soll, zurück zum Zeitplan), gibt das Add-on denselben
Server zusätzlich **direkt am Port 8099** frei – der Port ist standardmäßig **aus** und wird in den Einstellungen des Add-ons unter
„Netzwerk“ eingeschaltet; eingebunden wird er in Lovelace als Webpage-Karte
(``http://<ha-adresse>:8099/``).

Wer über den Port kommt (statt über Ingress), ist **Gast**: Nur die Startseite und die Endpunkte des Reiters
„Heizen“ sind erlaubt (``ALLOWED_GET``/``ALLOWED_POST``) – Bedienen ja, Einstellen nein. Alles andere wird mit 403
beantwortet, und die Oberfläche blendet die übrigen Reiter samt Pause-Knopf aus (``api/heat`` liefert ``guest``).

Erkannt wird Ingress am Kopf ``X-Ingress-Path``, den der Supervisor-Proxy jeder Ingress-Anfrage mitgibt. Der Schutz
ist nur im Add-on aktiv (``SUPERVISOR_TOKEN`` gesetzt) – lokale Tests und Entwicklung laufen unverändert; Tests
schalten ihn über ``web_app["guarded"] = True`` gezielt ein. Der Port hat keine eigene Anmeldung: nur im eigenen,
vertrauten Netz freigeben (nie ins Internet weiterleiten).
"""

from __future__ import annotations

import os

from aiohttp import web

ALLOWED_GET = ("/", "/icon.svg", "/api/heat", "/api/summary")  # summary: die Startzeile der Seite (nur lesen)
# Bedienen wie im Reiter „Heizen“: Boost, zurück zum Zeitplan, Soll, Betriebsart – nie die Pause (hält die Steuerung
# an) und nie Einstellungen, Diagnose oder Export
ALLOWED_POST = ("/api/heat/boost", "/api/heat/schedule", "/api/heat/temp", "/api/heat/mode")
DENIED = ("Nur Bedienen: Diese Ansicht ist die Gast-Ansicht (Lovelace). Einstellungen gehen über die "
          "HeatPilot-Seite in Home Assistant")


def is_guest(req) -> bool:
    """Gast: Anfrage kam direkt über den Port, nicht über Ingress – nur wenn der Schutz aktiv ist."""
    guarded = req.app.get("guarded")
    if guarded is None:
        guarded = bool(os.environ.get("SUPERVISOR_TOKEN"))
    return bool(guarded) and not req.headers.get("X-Ingress-Path")


@web.middleware
async def guard(req, handler):
    """Gästen nur die Heizen-Ansicht erlauben (siehe Modulbeschreibung)."""
    if is_guest(req):
        ok = req.path in ALLOWED_GET if req.method == "GET" else req.path in ALLOWED_POST
        if not ok:
            return web.json_response({"error": DENIED}, status=403)
    return await handler(req)
