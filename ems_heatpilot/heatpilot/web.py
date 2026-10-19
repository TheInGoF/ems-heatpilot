"""Weboberfläche (über Ingress direkt in der HA-Seitenleiste).

Seit 0.39 enthalten ``api/heat`` und ``api/summary`` je Raum das gelernte Ventilverhalten (``valve_model``) und
``api/preview`` den Trockenlauf der Ventile (``valves``) – nur Anzeige.

Seit 0.38 dazu Weg W und Benachrichtigungen: ``GET api/dhw`` (Status und Bereitschaft des Warmwasser-Solls),
``POST api/dhw/resume`` („wieder einschalten“ – nur bei einer Pause, sonst 409), ``GET api/notify`` (Ziele zur
Auswahl) und ``POST api/notify/test`` (Testnachricht nur an die gespeicherten Ziele). ``api/entities`` sagt je
``number``-Entität, ob sie als Warmwasser-Soll taugt (``dhw``: fits, hint – für die Auswahl).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import date, datetime
from pathlib import Path

from aiohttp import web

from .config import CONTROL_MODES, DHW_MODES, DISINFECT_DAYS, FIELDS, PRESET_MAX, WEEKDAYS, Config
from .discovery import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES, Registry, climate_info
from . import advice, certificate, control as control_mod, controlloop, diagnostics, live, preview, preview_log, readiness
from . import dhw_control, dhw_entity, notify, valve_dry
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
        return json_response({**app.eng.summary(), "configured": app.configured,
                              "dhw_control": dhw_state()})  # 0.38: Statuszeile Warmwasser (Weg W)

    def dhw_state() -> dict:
        """Status von Weg W (``DhwControl.as_dict``); ohne Instanz nur der Schalter."""
        ctl = getattr(app.eng, "dhw_control", None)
        if ctl is not None:
            return ctl.as_dict()
        mode = getattr(app.cfg, "dhw_control", "aus")
        return {"mode": mode, "modes": list(DHW_MODES), "active": False, "writes": mode == "heatpilot",
                "paused": False, "warnings": [], "log": [], "status_text": "Warmwasser-Soll: aus – HeatPilot empfiehlt nur"}

    async def notify_info() -> dict:
        """Benachrichtigungsziele zur Auswahl (Entitäten aus den Zuständen, Dienste aus ``get_services``) mit Stand."""
        services, error = {}, None
        try:
            services = await asyncio.wait_for(app.ha.get_services(), timeout=10)
        except Exception as err:  # noqa: BLE001 – ältere HA bzw. Fehler: nur die notify-Entitäten
            error = f"Dienste nicht lesbar ({err or type(err).__name__}) – nur notify-Entitäten zur Auswahl"
        n = getattr(app.eng, "notifier", None)
        targets = list(getattr(app.cfg, "notify_targets", []) or [])
        return {**(n.as_dict() if n is not None else {"targets": targets, "problems": [], "last": None}),
                "available": notify.available(app.eng.states, services, targets), "services_error": error}

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
            # Nachtzeiten aus dem Stromverbrauch (0.36): Wochenplan mit Belastbarkeit, Zahl der Nächte, Gründe
            "sleep": app.eng.sleep_summary(),
            # Steuerung (0.34): erlaubte Schalterstellungen, Wochentage des Nachtplans, aktuelles Soll je
            # Thermostat (Startwert der Komforttemperatur eines neu gewählten Raums)
            "control": {"modes": list(CONTROL_MODES), "weekdays": list(WEEKDAYS),
                        "limits": {"temp_min": control_mod.TEMP_MIN, "temp_max": control_mod.TEMP_MAX,
                                   # Hydraulik (0.35): Voreinstellung/Stufen 0…preset_max
                                   "preset_max": PRESET_MAX}},
            # Hydraulik (0.35): gemerkte Änderungen der Voreinstellung (jüngste zuletzt) und die Fristen
            "balance": {"changes": advice.preset_changes(app.eng.store),
                        "pause_days": advice.PRESET_PAUSE_DAYS, "effect_days": advice.PRESET_EFFECT_DAYS,
                        "forget_days": advice.PRESET_FORGET_DAYS},
            "climate_targets": {eid: st.get("attributes", {}).get("temperature")
                                for eid, st in app.eng.states.items()
                                if eid.startswith("climate.") and st.get("state") != "off"},
            # Desinfektion (0.36.3): Termin, Temperatur (vom Kessel oder eingestellt), Vorschlag, Hinweis
            "disinfect": {**app.eng.disinfect_info(suggest=True), "days": list(DISINFECT_DAYS),
                          "temp_range": list(FIELDS["dhw_disinfect_temp"][1:])},
            # Weg W (0.38): Schalter, Entität (eingestellt bzw. Vorschlag aus der Erkennung), Status, Bereitschaft
            "dhw_control": {"modes": list(DHW_MODES),
                            "suggested": dhw_control.suggest_entity(app.eng.found, app.eng.states),
                            "status": dhw_state(), "readiness": readiness.dhw(app.eng, int(time.time()))},
            "notify": await notify_info(),
        })

    async def entities(_req):
        """Alle wählbaren Entitäten mit Name, Einheit, Klasse, Gerät und Bereich (für Raum-Vorschläge).

        climate-Einträge (0.35) zusätzlich mit ``device_name``, ``device_model``, ``controller`` (Vorschlag
        Wandthermostat) und ``siblings`` (Sensoren desselben Geräts: id, kind, name, unit), seit 0.36.3 ``ems``
        (Heizkreis von EMS-ESP: „macht mit“ gesperrt), seit 0.38 bei ``number`` ``dhw`` (taugt als Warmwasser-Soll,
        Name deutet darauf – ``dhw_entity.picker``)."""
        if app.eng is None:
            return json_response([])
        idx = Registry.of(await app.registry()).lookup()
        reg = idx.entity
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
                "area": idx.area_of(eid),
                # Personen: nur ob Gerät/GPS da ist – nie Koordinaten
                **({"trackers": len(a.get("device_trackers") or []), "gps": "latitude" in a,
                    "user": bool(a.get("user_id"))}
                   if eid.startswith("person.") else {}),
                **({k: v for k, v in climate_info(eid, idx, app.eng.states).items()
                    if k in ("device_name", "device_model", "controller", "siblings", "ems")}
                   if eid.startswith("climate.") else {}),
                **({"dhw": dhw_entity.picker(eid, st, app.eng.found)} if eid.startswith("number.") else {}),
            })
        return json_response(out)

    async def post_settings(req):
        try:
            raw = await req.json()
        except ValueError:
            return json_response({"ok": False, "errors": ["Ungültige Daten"]}, status=400)
        # Hydraulik (0.35): Voreinstellung vorher merken – mit dem nötigen Vorlauf laut Modell vor der Änderung
        presets_before = advice.presets_of(app.cfg)
        flows = {c: advice.room_flow(app.eng, c) for c in presets_before} if app.eng is not None else {}
        errors = await app.apply_settings(raw)
        if errors:
            return json_response({"ok": False, "errors": errors}, status=400)
        changes = []
        if app.eng is not None:
            flows = {c: flows.get(c) if flows.get(c) is not None else
                     advice.room_flow(app.eng, getattr(app.eng.found.room_of(c), "climate", c))
                     for c in presets_before}
            changes = advice.record_preset_changes(app.eng.store, presets_before, advice.presets_of(app.cfg), flows)
            if changes:
                log.info("Voreinstellung geändert: %s – %d Tage keine Abgleich-Empfehlung, dann Wirkung",
                         ", ".join(f"{c['room']} {c['from']}→{c['to']}" for c in changes), advice.PRESET_PAUSE_DAYS)
        # Räume nach dem Speichern: welches Gerät Schlüssel ist (Raumregler, sonst erstes der Gruppe)
        rooms = [{"name": r.name, "climate": r.climate, "controller": r.controller,
                  "members": [m["climate"] for m in r.members], **({"ems": True} if r.ems else {})}
                 for r in (app.eng.found.rooms if app.eng is not None else [])]
        return json_response({"ok": True, "rooms": rooms, "preset_changes": changes})

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

    async def preview_data(_req):
        """Vorschau der nächsten 24 h und Trockenlauf-Vergleich (seit 0.37) – nur Anzeige, schreibt nichts."""
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        now = int(time.time())
        return json_response({"preview": preview.cached(app.eng, now), "compare": preview_log.compare_for(app.eng, now),
                              "valves": valve_dry.summary(app.eng, now)})  # 0.39: Ventile im Trockenlauf

    def heat_state() -> dict:
        eng, ctl, loop = app.eng, app.control, app.eng.control_loop
        targets = {r.climate: (eng.states.get(r.climate) or {}).get("attributes", {}).get("temperature")
                   for r in eng.found.rooms}
        boosts = ctl.active(targets) if ctl else {}
        ctl_rooms = loop.rooms if loop else {}
        rooms = []
        for r in eng.found.rooms:
            st = eng.states.get(r.climate) or {}
            a = st.get("attributes", {})
            reading = eng.room_reading(r)  # Raumtemperatur/Ventil als Mittel der Gruppe (Raumregler: nur er)
            devices = []
            for d in r.devices:
                ds = eng.states.get(d["climate"]) or {}
                da = ds.get("attributes", {})
                devices.append({"climate": d["climate"], "name": da.get("friendly_name", d["climate"]),
                                "mode": ds.get("state"), "action": da.get("hvac_action"),
                                "temp": da.get("current_temperature"), "target": da.get("temperature"),
                                "valve": eng._val(d.get("heating_power")), "platform": d.get("platform"),
                                "controller": bool(r.controller and d["climate"] == r.climate)})
            rooms.append({
                "climate": r.climate, "name": r.name, "mode": st.get("state"),
                "action": a.get("hvac_action"),
                "temp": reading[1] if reading else a.get("current_temperature"),
                "target": a.get("temperature"),
                "valve": reading[3] if reading else eng._val(r.heating_power),
                "humidity": eng._val(r.humidity) or a.get("current_humidity"),
                "modes": a.get("hvac_modes") or [], "boost": boosts.get(r.climate),
                # seit 0.34: Gruppe, Steuerung
                "group": r.group, "controller": r.controller, "managed": r.managed, "comfort": r.comfort,
                "platform": r.platform, "tado_cloud": bool(ctl and ctl.is_tado(r.climate)),
                "ems": r.ems,  # 0.36.3: Heizkreis von EMS-ESP – Bedienung gesperrt
                "climates": r.climates, "devices": devices,
                "control": ctl_rooms.get(r.climate),
                "valve_model": eng.valve_info(r.climate, int(time.time())),  # 0.39: Ventilverhalten (Raum-Dialog)
            })
        return {"rooms": rooms, "limits": {"temp_min": control_mod.TEMP_MIN, "temp_max": control_mod.TEMP_MAX,
                                           "dur_min": control_mod.DUR_MIN, "dur_max": control_mod.DUR_MAX,
                                           "pause_max_h": controlloop.PAUSE_MAX_H},
                "control": loop.as_dict() if loop else {"mode": app.cfg.control_mode, "active": False,
                                                        "paused_until": None, "rooms": {}, "log": [],
                                                        "warnings": [], "status_text": "Beobachten"},
                # seit 0.36.1: „Bereit für die Steuerung?“ – auch bei Beobachten, mit Kessel über EMS-ESP
                "readiness": readiness.assess(eng, int(time.time())),
                # seit 0.38: Warmwasser-Soll über HeatPilot (Weg W) – Statuszeile, Hinweise, Protokoll
                "dhw": dhw_state()}

    async def dhw_get(_req):
        """Weg W (0.38): Status und Bereitschaft des Warmwasser-Solls."""
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        return json_response({"dhw": dhw_state(), "readiness": readiness.dhw(app.eng, int(time.time()))})

    async def dhw_resume(_req):
        """„Wieder einschalten“ nach einer Pause von Weg W – schreibt selbst nichts, der nächste Takt entscheidet. Ohne
        Pause ändert sich nichts (409) – ein wiederholter Aufruf hebt die Fehlschläge nicht auf."""
        ctl = getattr(app.eng, "dhw_control", None)
        if ctl is None:
            return json_response({"error": f"HeatPilot startet noch ({app.status})"}, status=503)
        was = ctl.resume()
        body = {"ok": was, "was_paused": was, "dhw": dhw_state(), "readiness": readiness.dhw(app.eng, int(time.time()))}
        if not was:
            return json_response({**body, "error": "Warmwasser über HeatPilot ist nicht pausiert – nichts geändert"},
                                 status=409)
        return json_response(body)

    async def notify_get(_req):
        if app.eng is None:
            return json_response({"starting": True, "status": app.status})
        return json_response(await notify_info())

    async def notify_test(_req):
        """Testnachricht – nur an die gespeicherten Ziele (``notify_targets``), über ``Control.notify``."""
        n = getattr(app.eng, "notifier", None)
        if n is None:
            return json_response({"ok": False, "errors": [f"HeatPilot startet noch ({app.status})"]}, status=503)
        if not n.targets:
            return json_response({"ok": False, "sent": [], "errors": [notify.NO_TARGETS]}, status=400)
        return json_response(await n.test())

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
        action, ctl, loop = req.match_info["action"], app.control, app.eng.control_loop
        try:
            if action == "boost":
                res = await ctl.boost(body.get("rooms"), body.get("temp"), body.get("minutes"))
            elif action == "schedule":
                # Tado-Cloud: zurück in den Tado-Zeitplan; andere Entitäten: Wunschzustand (heatpilot) bzw. Hinweis
                res = await ctl.schedule(body.get("rooms"), loop.desired_setpoints() if loop else None)
                if loop:
                    for r in res.get("done", []):
                        st = loop.state.get(r)
                        if st is not None and r in loop.desired_setpoints():
                            st["last_written"] = loop.desired_setpoints()[r]
            elif action == "pause":
                # Steuerung anhalten: {"hours": 2} · {"hours": 0} = weiter
                if loop is None:
                    return json_response({"error": "Steuerung noch nicht bereit"}, status=503)
                try:
                    until = loop.pause(body.get("hours", 0))
                except (TypeError, ValueError) as err:
                    return json_response({"error": str(err) or "Stunden fehlen"}, status=400)
                return json_response({"ok": True, "paused_until": until and
                                      datetime.fromtimestamp(until).astimezone().isoformat(), **heat_state()})
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
    web_app.router.add_get("/api/preview", preview_data)
    web_app.router.add_get("/api/dhw", dhw_get)
    web_app.router.add_post("/api/dhw/resume", dhw_resume)
    web_app.router.add_get("/api/notify", notify_get)
    web_app.router.add_post("/api/notify/test", notify_test)
    web_app.router.add_get("/api/certificate", cert_json)
    web_app.router.add_get("/api/certificate.pdf", cert_pdf)
    return web_app
