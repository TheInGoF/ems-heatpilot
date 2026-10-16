"""Weboberfläche (über Ingress direkt in der HA-Seitenleiste)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from .config import Config
from .discovery import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES
from . import certificate, diagnostics, live
from .emsesp import EmsEsp

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

# Domains, aus denen die Oberfläche Entitäten zur Auswahl anbietet
PICK_DOMAINS = ("sensor", "binary_sensor", "climate", "weather", "input_boolean", "switch",
                "number", "select", "person", "counter")


def make_app(app) -> web.Application:
    """``app``: main.App (cfg, eng, status, apply_settings, suggestions, reload_history, ...)."""
    web_app = web.Application()

    async def index(_req):
        return web.FileResponse(STATIC / "index.html")

    async def icon(_req):
        return web.FileResponse(STATIC / "icon.svg", headers={"Cache-Control": "max-age=86400"})

    async def summary(_req):
        if app.eng is None:
            return web.json_response({"starting": True, "status": app.status})
        return web.json_response({**app.eng.summary(), "configured": app.configured})

    async def history(req):
        if app.eng is None:
            return web.json_response({"starting": True})
        days = int(req.query.get("days", "30"))
        return web.json_response({**app.eng.history(min(max(days, 1), 400)),
                                  "signature": app.eng.house_signature(365)})

    async def get_settings(_req):
        if app.eng is None:
            return web.json_response({"starting": True, "status": app.status})
        roles = [{"role": r, "label": ROLE_LABELS.get(r, r), "group": "Kessel"} for r in BOILER_ROLES]
        roles += [{"role": r, "label": ROLE_LABELS.get(r, r), "group": "Regler"} for r in THERMOSTAT_ROLES]
        return web.json_response({
            "settings": app.cfg.to_raw(),
            "defaults": Config().to_raw(),
            "configured": app.configured,
            "discovered": app.eng.found.as_dict(private=False),
            "suggested": await app.suggestions(),
            "roles": roles,
            "schedule": {"learned": app.eng.schedule_learned, "effective": app.eng.eff.as_dict()},
        })

    async def entities(_req):
        """Alle wählbaren Entitäten mit Name, Einheit, Klasse und Gerät (für Raum-Vorschläge)."""
        if app.eng is None:
            return web.json_response([])
        reg = {r["entity_id"]: r for r in await app.registry()}
        out = []
        for eid, st in sorted(app.eng.states.items()):
            if eid.split(".", 1)[0] not in PICK_DOMAINS:
                continue
            a = st.get("attributes", {})
            out.append({
                "id": eid,
                "name": a.get("friendly_name", eid),
                "unit": a.get("unit_of_measurement"),
                "device_class": a.get("device_class"),
                "state": st.get("state"),
                "device_id": reg.get(eid, {}).get("device_id"),
                "platform": reg.get(eid, {}).get("platform"),
                # Personen: nur ob Gerät/GPS da ist – nie Koordinaten
                **({"trackers": len(a.get("device_trackers") or []), "gps": "latitude" in a,
                    "user": bool(a.get("user_id"))}
                   if eid.startswith("person.") else {}),
            })
        return web.json_response(out)

    async def post_settings(req):
        try:
            raw = await req.json()
        except ValueError:
            return web.json_response({"ok": False, "errors": ["Ungültige Daten"]}, status=400)
        errors = await app.apply_settings(raw)
        if errors:
            return web.json_response({"ok": False, "errors": errors}, status=400)
        return web.json_response({"ok": True})

    async def test_emsesp(req):
        host = (await req.json()).get("host", "").strip()
        if not host:
            return web.json_response({"ok": False, "message": "Keine Adresse angegeben"})
        ems = EmsEsp(host)
        try:
            values = await asyncio.wait_for(ems.read(), timeout=45)
        except Exception as err:  # noqa: BLE001
            return web.json_response({"ok": False, "message": str(err) or "keine Antwort"})
        finally:
            await ems.close()
        if not values:
            return web.json_response({"ok": False, "message": "Keine Antwort von /api/boiler bzw. /api/thermostat"})
        names = {"boiler": "Kessel", "thermostat": "Regler", "mixer": "Mischer", "solar": "Solar",
                 "temperaturesensor": "Temperatursensoren", "analogsensor": "Analogsensoren",
                 "heatpump": "Wärmepumpe", "switch": "Schaltmodul", "custom": "eigene Werte"}
        return web.json_response({"ok": True, "message": ", ".join(
            f"{names.get(d, d)}: {len(v)} Werte" for d, v in values.items())})

    async def diag(req):
        level = req.query.get("level", "INFO").upper()
        n = int(req.query.get("n", "400"))
        return web.json_response({
            "checks": diagnostics.self_check(app),
            "log": diagnostics.RING.tail(min(max(n, 10), diagnostics.LOG_RING_SIZE), level),
            "log_dir": str(diagnostics.log_dir()),
            "debug": app.cfg.debug_logging,
        })

    async def diag_bundle(_req):
        import json
        from datetime import datetime

        data = diagnostics.bundle(app)  # im Hauptthread: SQLite-Verbindung ist threadgebunden
        name = f"heatpilot-diagnose-{datetime.now():%Y%m%d-%H%M}.json"
        return web.Response(
            body=json.dumps(data, ensure_ascii=False, default=str, indent=1).encode(),
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    async def live_data(req):
        if app.eng is None:
            return web.json_response({"starting": True, "status": app.status})
        hours = min(max(float(req.query.get("hours", "12")), 0.5), 24)
        return web.json_response({"now": live.now(app.eng), **live.series(app.eng, hours)})

    async def cert_json(_req):
        if app.eng is None:
            return web.json_response({"starting": True})
        return web.json_response(certificate.collect(app.eng))

    async def cert_pdf(req):
        from datetime import datetime

        if app.eng is None:
            return web.json_response({"error": f"HeatPilot startet noch ({app.status}) – bitte gleich noch einmal."},
                                     status=503)
        try:
            body = certificate.pdf(app.eng)
        except Exception as err:  # noqa: BLE001 – Fehler sichtbar machen statt einer kaputten Datei
            log.exception("Zertifikat konnte nicht erstellt werden")
            return web.json_response({"error": f"Zertifikat konnte nicht erstellt werden: {err}"}, status=500)
        name = f"heatpilot-zertifikat-{datetime.now():%Y%m%d}.pdf"
        mode = "inline" if req.query.get("inline") else "attachment"
        return web.Response(body=body, content_type="application/pdf",
                            headers={"Content-Disposition": f'{mode}; filename="{name}"',
                                     "Cache-Control": "no-store"})

    async def export_learned(req):
        import json
        from datetime import datetime

        if app.eng is None:
            return web.Response(status=503, text="Add-on startet noch")
        days = min(max(int(req.query.get("days", "14")), 1), 60)
        data = diagnostics.export(app, days)
        name = f"heatpilot-gelernt-{datetime.now():%Y%m%d-%H%M}.json"
        return web.Response(
            body=json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")).encode(),
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    async def sensors(_req):
        if app.eng is None:
            return web.json_response({"starting": True, "status": app.status})
        return web.json_response({"rows": diagnostics.sensor_table(app.eng)})

    async def export_size(req):
        if app.eng is None:
            return web.json_response({"bytes": None})
        days = min(max(int(req.query.get("days", "14")), 1), 60)
        return web.json_response({"bytes": diagnostics.export_estimate(app, days)})

    async def reload_history(_req):
        asyncio.create_task(app.reload_history())
        return web.json_response({"ok": True})

    web_app.router.add_get("/", index)
    web_app.router.add_get("/icon.svg", icon)
    web_app.router.add_get("/api/summary", summary)
    web_app.router.add_get("/api/history", history)
    web_app.router.add_get("/api/settings", get_settings)
    web_app.router.add_post("/api/settings", post_settings)
    web_app.router.add_get("/api/entities", entities)
    web_app.router.add_post("/api/test_emsesp", test_emsesp)
    web_app.router.add_post("/api/reload_history", reload_history)
    web_app.router.add_get("/api/diagnostics", diag)
    web_app.router.add_get("/api/diagnostics/bundle", diag_bundle)
    web_app.router.add_get("/api/export", export_learned)
    web_app.router.add_get("/api/export/size", export_size)
    web_app.router.add_get("/api/sensors", sensors)
    web_app.router.add_get("/api/live", live_data)
    web_app.router.add_get("/api/certificate", cert_json)
    web_app.router.add_get("/api/certificate.pdf", cert_pdf)
    return web_app
