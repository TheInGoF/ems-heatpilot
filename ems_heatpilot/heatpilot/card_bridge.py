"""Brücke zu den HeatPilot-Karten in Home Assistant (seit 0.56, Repository ``heatpilot-card`` über HACS).

Die Karten laufen im Browser in Home Assistant, nicht im Add-on – sie lesen nur Entitäten und schreiben Befehle in
einen Helfer. Darum stellt HeatPilot bereit:

* ``sensor.heatpilot_raum_<raum>`` je Raum (``rooms``): Zustand = Ist-Temperatur, Attribute wie die Raumkachel der
  HeatPilot-Seite – Soll, Betriebsart, heizt, Ventil, Luftfeuchte, Phase und Wunsch-Soll der Steuerung, nächster
  Wechsel, Boost (Soll, Ende, Restzeit), Klima-Entität, Heizkreis von EMS-ESP (nur Anzeige).
* ``sensor.heatpilot_boost`` (``boost``): Zahl der Räume mit Boost; Attribute: die laufenden Boosts, die Grenzen für
  Soll und Dauer, das Ergebnis des letzten Befehls (``last``).
* ``input_text.heatpilot_befehl`` (``ensure_command``): legt HeatPilot einmal als Helfer an (WebSocket
  ``input_text/create``). Die Karte schreibt einen Befehl als JSON hinein, z. B.
  ``{"a": "boost", "rooms": ["climate.wohnzimmer"], "temp": 22, "minutes": 120, "n": 1730000000}`` (``n`` macht
  gleiche Befehle unterscheidbar). HeatPilot führt ihn aus (``heat_actions`` – dieselbe Stelle wie die Seite), leert
  das Feld und meldet das Ergebnis in ``sensor.heatpilot_boost`` → ``last``. Aktionen: boost, schedule, temp, mode.

Geschrieben wird nur an die eigenen Sensoren und den eigenen Helfer; an Tado und Kessel nur über ``control``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from . import control as control_mod
from . import heat_actions
from .values import num

log = logging.getLogger(__name__)

COMMAND = "input_text.heatpilot_befehl"
BOOST = "sensor.heatpilot_boost"
ROOM_PREFIX = "sensor.heatpilot_raum_"
CARD_ACTIONS = ("boost", "schedule", "temp", "mode")  # Pause bleibt der HeatPilot-Seite (Admins) vorbehalten
EVERY_S = 30


def room_entity(climate: str) -> str:
    return ROOM_PREFIX + climate.split(".", 1)[-1]


def rooms(eng, ctl) -> list[tuple[str, object, dict]]:
    """(Entität, Zustand, Attribute) je Raum – wie die Raumkachel der HeatPilot-Seite."""
    targets = {r.climate: (eng.states.get(r.climate) or {}).get("attributes", {}).get("temperature")
               for r in eng.found.rooms}
    boosts = ctl.active(targets) if ctl else {}
    loop = eng.control_loop
    out = []
    for r in eng.found.rooms:
        st = eng.states.get(r.climate) or {}
        a, reading = st.get("attributes", {}), eng.room_reading(r)
        c = (loop.rooms if loop else {}).get(r.climate) or {}
        b = boosts.get(r.climate)
        temp = reading[1] if reading else a.get("current_temperature")
        out.append((room_entity(r.climate), temp, {
            "friendly_name": f"HeatPilot {r.name}", "icon": "mdi:thermostat", "unit_of_measurement": "°C",
            "device_class": "temperature", "room": r.name, "climate": r.climate, "mode": st.get("state"),
            "action": a.get("hvac_action"), "target": a.get("temperature"),
            "valve": reading[3] if reading else eng._val(r.heating_power),
            "humidity": eng._val(r.humidity) or num(a.get("current_humidity")), "modes": a.get("hvac_modes") or [],
            "managed": bool(r.managed), "lead": c.get("lead"), "phase": c.get("phase"), "desired": c.get("desired"),
            "next_change": c.get("next_change"), "next_phase": c.get("next_phase"), "ems": bool(r.ems),
            "boost": None if not b else {"temp": b["temp"], "until": int(b["end"]), "left_min": b["left_min"]},
        }))
    return out


def boost(eng, ctl, last: dict | None) -> tuple[str, int, dict]:
    targets = {r.climate: (eng.states.get(r.climate) or {}).get("attributes", {}).get("temperature")
               for r in eng.found.rooms}
    act = ctl.active(targets) if ctl else {}
    names = {r.climate: r.name for r in eng.found.rooms}
    return BOOST, len(act), {
        "friendly_name": "HeatPilot Boost", "icon": "mdi:fire",
        "rooms": [{"climate": c, "room": names.get(c, c), "temp": b["temp"], "until": int(b["end"]),
                   "left_min": b["left_min"]} for c, b in act.items()],
        "temp_min": control_mod.TEMP_MIN, "temp_max": control_mod.TEMP_MAX,
        "dur_min": control_mod.DUR_MIN, "dur_max": control_mod.DUR_MAX, "command": COMMAND, "last": last}


async def publish(app) -> int:
    """Raum- und Boost-Sensoren setzen; Zahl der gesetzten Sensoren."""
    eng = app.eng
    items = [*rooms(eng, app.control), boost(eng, app.control, getattr(app, "card_last", None))]
    for entity, state, attrs in items:
        await app.ha.set_state(entity, state, attrs)
    return len(items)


async def ensure_command(app) -> bool:
    """Den Befehls-Helfer einmal anlegen, wenn es ihn nicht gibt. True, wenn angelegt."""
    if app.eng.states.get(COMMAND) is not None:
        return False
    await app.ha.call({"type": "input_text/create", "name": "HeatPilot Befehl", "max": 255,
                       "icon": "mdi:console-line"})
    log.info("Befehls-Helfer %s für die HeatPilot-Karten angelegt", COMMAND)
    return True


async def step(app, now: float | None = None) -> bool:
    """Im Takt von ``main.live_loop``: Helfer sicherstellen, alle ``EVERY_S`` die Sensoren setzen."""
    now = time.time() if now is None else now
    if app.eng is None or now - getattr(app, "card_ts", 0) < EVERY_S - 1:
        return False
    if not getattr(app, "card_helper", False):
        app.card_helper = True  # einmal je Start versuchen – schlägt es fehl, bleiben die Karten nur Anzeige
        try:
            await ensure_command(app)
        except Exception as err:  # noqa: BLE001
            log.warning("Befehls-Helfer %s nicht angelegt (%s) – die Karten können dann nicht bedienen", COMMAND, err)
    await publish(app)
    app.card_ts = now
    return True


def parse(value) -> dict | None:
    """Befehl aus dem Helfer – None bei leer (gelöscht) oder unlesbar."""
    if not isinstance(value, str) or not value.strip() or value in ("unknown", "unavailable"):
        return None
    try:
        cmd = json.loads(value)
    except ValueError:
        return None
    return cmd if isinstance(cmd, dict) and cmd.get("a") in CARD_ACTIONS else None


async def handle(app, value) -> dict | None:
    """Befehl ausführen, Feld leeren, Ergebnis melden und die Sensoren gleich neu setzen."""
    cmd = parse(value)
    if cmd is None or app.eng is None or app.control is None:
        return None
    try:
        res = await heat_actions.run(app, cmd["a"], cmd)
        errs = [str(e) for e in res.get("errors") or []]
        last = {"ok": bool(res.get("ok", True)), "action": cmd["a"], "text": "; ".join(errs) or res.get("hint")}
    except control_mod.ControlError as err:
        last = {"ok": False, "action": cmd["a"], "text": str(err)}
    app.card_last = {**last, "n": cmd.get("n"), "ts": int(time.time())}
    log.info("Befehl von der Karte: %s → %s", cmd["a"], "ok" if last["ok"] else last["text"])
    await app.ha.call({"type": "call_service", "domain": "input_text", "service": "set_value",
                       "service_data": {"value": ""}, "target": {"entity_id": COMMAND}})
    await asyncio.sleep(1.5)  # Tado meldet den neuen Zustand mit kurzer Verzögerung
    await app.refresh_rooms()
    await publish(app)
    return app.card_last
