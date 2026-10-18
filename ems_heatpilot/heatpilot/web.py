"""Weboberfläche (über Ingress direkt in der HA-Seitenleiste)."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path

from aiohttp import web

from .config import Config
from .discovery import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES
from . import certificate, control as control_mod, diagnostics, live
from .emsesp import EmsEsp

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

# Domains, aus denen die Oberfläche Entitäten zur Auswahl anbietet
PICK_DOMAINS = ("sensor", "binary_sensor", "climate", "weather", "input_boolean", "switch",
                "number", "select", "person", "counter")


def clean(value):
    """Für JSON: NaN/unendlich -> null, Zeitpunkte -> ISO-Text (Browser lehnen NaN ab)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def json_response(data, status: int = 200, **kw) -> web.Response:
    body = json.dumps(clean(data), ensure_ascii=False, allow_nan=False, default=str)
    return web.Response(text=body, status=status, content_type="application/json", **kw)


@web.middleware
async def api_errors(request, handler):
    """Fehler einer Abfrage als JSON mit Ursache – statt einer Fehlerseite, die die Oberfläche nicht lesen kann."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        if "/api/" not in request.path:
            raise
        log.exception("Fehler bei %s", request.path)
        return json_response({"error": f"{type(err).__name__}: {err}", "path": request.path}, status=500)


def make_app(app) -> web.Application:
    """``app``: main.App (cfg, eng, status, apply_settings, suggestions, reload_history, ...)."""
    web_app = web.Application(middlewares=[api_errors])

    async def index(_req):
        return web.FileResponse(STATIC / "index.html")

    async def icon(_req):
        return web.FileResponse(STATIC / "icon.svg", headers={"Cache-Control": "max-age=86400"})

    async def summary(_req):
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        return json_response({**app.eng.summary(), "configured": app.configured})

    async def history(req):
        if app.eng is None:
            return json_response({"starting": True})
        days = int(req.query.get("days", "30"))
        return json_response({**app.eng.history(min(max(days, 1), 400)),
                                  "signature": app.eng.house_signature(365)})

    async def get_settings(_req):
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        roles = [{"role": r, "label": ROLE_LABELS.get(r, r), "group": "Kessel"} for r in BOILER_ROLES]
        roles += [{"role": r, "label": ROLE_LABELS.get(r, r), "group": "Regler"} for r in THERMOSTAT_ROLES]
        return json_response({
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
            return json_response([])
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
        return json_response(out)

    async def post_settings(req):
        try:
            raw = await req.json()
        except ValueError:
            return json_response({"ok": False, "errors": ["Ungültige Daten"]}, status=400)
        errors = await app.apply_settings(raw)
        if errors:
            return json_response({"ok": False, "errors": errors}, status=400)
        return json_response({"ok": True})

    async def test_emsesp(req):
        host = (await req.json()).get("host", "").strip()
        if not host:
            return json_response({"ok": False, "message": "Keine Adresse angegeben"})
        ems = EmsEsp(host)
        try:
            values = await asyncio.wait_for(ems.read(), timeout=45)
        except Exception as err:  # noqa: BLE001
            return json_response({"ok": False, "message": str(err) or "keine Antwort"})
        finally:
            await ems.close()
        if not values:
            return json_response({"ok": False, "message": "Keine Antwort von /api/boiler bzw. /api/thermostat"})
        names = {"boiler": "Kessel", "thermostat": "Regler", "mixer": "Mischer", "solar": "Solar",
                 "temperaturesensor": "Temperatursensoren", "analogsensor": "Analogsensoren",
                 "heatpump": "Wärmepumpe", "switch": "Schaltmodul", "custom": "eigene Werte"}
        return json_response({"ok": True, "message": ", ".join(
            f"{names.get(d, d)}: {len(v)} Werte" for d, v in values.items())})

    async def diag(req):
        level = req.query.get("level", "INFO").upper()
        n = int(req.query.get("n", "400"))
        return json_response({
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
            return json_response({"starting": True, "status": app.status})
        hours = min(max(float(req.query.get("hours", "12")), 0.5), 24)
        return json_response({"now": live.now(app.eng), **live.series(app.eng, hours)})

    async def week_data(req):
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        days = min(max(int(req.query.get("days", "56")), 7), 365)
        room = req.query.get("room") or None
        return json_response(app.eng.week(days, room))

    async def presence_data(req):
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        hours = min(max(float(req.query.get("hours", "24")), 1), 168)
        return json_response(app.eng.presence(hours))

    def heat_state() -> dict:
        eng, ctl = app.eng, app.control
        targets = {r.climate: (eng.states.get(r.climate) or {}).get("attributes", {}).get("temperature")
                   for r in eng.found.rooms}
        boosts = ctl.active(targets) if ctl else {}
        rooms = []
        for r in eng.found.rooms:
            st = eng.states.get(r.climate) or {}
            a = st.get("attributes", {})
            rooms.append({
                "climate": r.climate, "name": r.name, "mode": st.get("state"),
                "action": a.get("hvac_action"), "temp": a.get("current_temperature"),
                "target": a.get("temperature"), "valve": eng._val(r.heating_power),
                "humidity": eng._val(r.humidity) or a.get("current_humidity"),
                "modes": a.get("hvac_modes") or [], "boost": boosts.get(r.climate),
            })
        return {"rooms": rooms, "limits": {"temp_min": control_mod.TEMP_MIN, "temp_max": control_mod.TEMP_MAX,
                                           "dur_min": control_mod.DUR_MIN, "dur_max": control_mod.DUR_MAX}}

    async def heat_get(_req):
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        return json_response(heat_state())

    async def heat_post(req):
        """Bedienung auf Knopfdruck – nur Tado, nur die gewählten Räume (siehe control.py)."""
        if app.eng is None or app.control is None:
            return json_response({"error": f"HeatPilot startet noch ({app.status})"}, status=503)
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001
            return json_response({"error": "Ungültige Daten"}, status=400)
        action, ctl = req.match_info["action"], app.control
        try:
            if action == "boost":
                res = await ctl.boost(body.get("rooms"), body.get("temp"), body.get("minutes"))
            elif action == "schedule":
                res = await ctl.schedule(body.get("rooms"))
            elif action == "temp":
                res = await ctl.set_temp(body.get("room"), body.get("temp"))
            elif action == "mode":
                res = await ctl.set_mode(body.get("rooms") or body.get("room"), body.get("mode"))
            else:
                return json_response({"error": "Unbekannte Aktion"}, status=404)
        except control_mod.ControlError as err:
            return json_response({"error": str(err)}, status=400)
        await asyncio.sleep(1.5)  # Tado meldet den neuen Zustand mit kurzer Verzögerung
        await app.refresh_rooms()
        return json_response({**res, **heat_state()})

    async def cert_json(_req):
        if app.eng is None:
            return json_response({"starting": True})
        return json_response(certificate.collect(app.eng))

    async def cert_pdf(req):
        from datetime import datetime

        if app.eng is None:
            return json_response({"error": f"HeatPilot startet noch ({app.status}) – bitte gleich noch einmal."},
                                     status=503)
        try:
            body = certificate.pdf(app.eng)
        except Exception as err:  # noqa: BLE001 – Fehler sichtbar machen statt einer kaputten Datei
            log.exception("Zertifikat konnte nicht erstellt werden")
            return json_response({"error": f"Zertifikat konnte nicht erstellt werden: {err}"}, status=500)
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
            return json_response({"starting": True, "status": app.status})
        return json_response({"rows": diagnostics.sensor_table(app.eng)})

    async def export_size(req):
        if app.eng is None:
            return json_response({"bytes": None})
        days = min(max(int(req.query.get("days", "14")), 1), 60)
        return json_response({"bytes": diagnostics.export_estimate(app, days)})

    async def reload_history(_req):
        asyncio.create_task(app.reload_history())
        return json_response({"ok": True})

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
    web_app.router.add_get("/api/week", week_data)
    web_app.router.add_get("/api/heat", heat_get)
    web_app.router.add_post("/api/heat/{action}", heat_post)
    web_app.router.add_get("/api/presence", presence_data)
    web_app.router.add_get("/api/certificate", cert_json)
    web_app.router.add_get("/api/certificate.pdf", cert_pdf)
    return web_app
