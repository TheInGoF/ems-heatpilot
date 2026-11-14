"""Bedienung der Räume auf Knopfdruck – eine Stelle für die HeatPilot-Seite (``POST api/heat/{action}``) und die
Karten in Home Assistant (``card_bridge``, seit 0.56).

Aktionen: ``boost`` {rooms, temp, minutes} · ``schedule`` {rooms} · ``temp`` {room, temp} · ``mode`` {rooms|room,
mode} · ``pause`` {hours} · ``override`` {mode: nacht|anwesend|abwesend|aus} (0.62, ``override``). Fehler als ``control.ControlError`` (Text für die Oberfläche).
Räume, die HeatPilot führt („macht mit“ bei „HeatPilot steuert“), bedient seit 0.72 ``room_modes`` – ihr eigenes
Climate; das Thermostat bekommt keine Betriebsart (Tado bleibt auf manuell, ein Ventil bekommt nur die Öffnung). Die
übrigen Räume gehen wie bisher an ``Control``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from . import control as control_mod
from . import override as override_mod
from . import room_modes

log = logging.getLogger(__name__)

ACTIONS = ("boost", "schedule", "temp", "mode", "pause", "override")


async def run(app, action: str, body: dict) -> dict:
    """Aktion ausführen; Ergebnis wie ``control`` ({done, failed, …}) bzw. bei ``pause`` {paused_until}."""
    if action in ("boost", "schedule", "temp", "mode"):  # 0.72: geführte Räume – eigenes Climate
        try:
            body, own = room_modes.take(app, action, body)
        except (TypeError, ValueError) as err:
            raise control_mod.ControlError(str(err) or "Wert fehlt") from None
        if own:
            await _write_now(app)
        if body is None:
            return {"ok": True, "done": own, "errors": []}
        res = await _run(app, action, body)
        return {**res, "done": own + list(res.get("done") or [])} if own else res
    return await _run(app, action, body)


async def _write_now(app) -> None:
    """Geführte Räume (0.75.4): das neue Soll gleich ans Thermostat schreiben statt erst im nächsten Takt (bis 5 min)."""
    now = getattr(app.eng, "control_now", None)  # Testhäuser ohne Regelschleife
    if now is None:
        return
    try:
        now()
        await app.eng.control_loop.apply()
    except Exception:  # noqa: BLE001 – der nächste Takt schreibt es
        log.exception("Soll nach dem Knopf nicht sofort geschrieben")


async def _run(app, action: str, body: dict) -> dict:
    ctl, loop = app.control, app.eng.control_loop
    if action == "boost":
        return await ctl.boost(body.get("rooms"), body.get("temp"), body.get("minutes"))
    if action == "schedule":  # nicht geführt: Tado-Cloud zurück in den Tado-Zeitplan, sonst der Hinweis
        return await ctl.schedule(body.get("rooms"))
    if action == "pause":  # Steuerung anhalten: {"hours": 2} · {"hours": 0} = weiter
        if loop is None:
            raise control_mod.ControlError("Steuerung noch nicht bereit")
        try:
            until = loop.pause(body.get("hours", 0))
        except (TypeError, ValueError) as err:
            raise control_mod.ControlError(str(err) or "Stunden fehlen") from None
        if until:  # Ventile führen (0.40): laufende Schritte vorher einmal zurück aufs Wunschsoll
            await loop.release(set(), "Pause")
        return {"ok": True, "paused_until": until and datetime.fromtimestamp(until).astimezone().isoformat()}
    if action == "override":  # 0.62: „nur jetzt“ – wirkt beim nächsten Takt des Regelkreises
        if loop is None:
            raise control_mod.ControlError("Steuerung noch nicht bereit")
        try:
            ov = override_mod.set_mode(loop.store, body.get("mode"))
        except ValueError as err:
            raise control_mod.ControlError(str(err)) from None
        return {"ok": True, "override": ov}
    if action == "temp":
        return await ctl.set_temp(body.get("room"), body.get("temp"))
    if action == "mode":
        return await ctl.set_mode(body.get("rooms") or body.get("room"), body.get("mode"))
    raise KeyError(action)
