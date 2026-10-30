"""Eigene Betriebsarten der ventilgeführten Räume (Stufe 3, seit 0.71).

Bei einem Ventil, das HeatPilot direkt in Prozent stellt (``room_lead``, z. B. Shelly BLU TRV), darf niemand die
Betriebsart oder das Soll am Thermostat verstellen – das schaltet dessen manuelle Ventilsteuerung ab. ``Control``
sperrt deshalb jeden ``climate``-Aufruf auf diese Thermostate. Die Knöpfe der Oberfläche und der Karten
(``heat_actions``: Boost, Zeitplan, Soll, Betriebsart) wirken bei diesen Räumen stattdessen auf HeatPilots eigene
Führung (``ValvePidControl``, Zustand je Raum ``own``):

* **Aus** (``off``) – der PID hält nur den Frostschutz ``FROST_C``; bleibt, bis jemand etwas anderes wählt.
* **Manuell** (``heat`` bzw. ein Soll) – der PID hält dieses Soll bis zum nächsten Phasenwechsel des Plans.
* **Boost** – der PID hält das Boost-Soll für die gewählte Dauer.
* **Zeitplan** (``auto`` bzw. „Zeitplan“) – zurück zum Plan von HeatPilot.

Das wirkt nur mit „HeatPilot steuert“ (der PID läuft sonst nicht) – sonst eine Fehlermeldung statt eines stillen Nichts.
"""

from __future__ import annotations

import time

FROST_C = 7.0
LABELS = {"aus": "Aus (Frostschutz)", "manuell": "Manuell", "boost": "Boost"}


def own_rooms(app) -> set[str]:
    """Die ventilgeführten Räume, deren Knöpfe hier landen (machen mit, Führung „ventil“)."""
    vp = getattr(app.eng, "valve_pid", None) if app.eng is not None else None
    return {r.climate for r in vp.rooms()} if vp is not None else set()


def _set(app, climate: str, own: dict | None, now: float) -> None:
    vp = app.eng.valve_pid
    st = vp.state.setdefault("rooms", {}).setdefault(climate, {})
    if own is None:
        st.pop("own", None)
    else:
        st["own"] = {**own, "since": int(now)}
    st.pop("hold_until", None)  # wer über HeatPilot bedient, beendet „von Hand am Ventil“


def _desired(app, climate: str) -> tuple[float | None, str | None]:
    info = (app.eng.control_loop.rooms if app.eng.control_loop is not None else {}).get(climate) or {}
    return info.get("desired"), info.get("phase")


def take(app, action: str, body: dict, now: float | None = None) -> tuple[dict | None, list[str]]:
    """Die ventilgeführten Räume aus ``body`` selbst bedienen. Gibt (``body`` für die übrigen Räume bzw. None, wenn
    keiner übrig ist; die selbst bedienten Räume) zurück; ``ValueError`` mit Text, wenn es so nicht geht."""
    now = time.time() if now is None else now
    keys = ("rooms", "room")
    rooms = [r for k in keys for r in ([body.get(k)] if isinstance(body.get(k), str) else body.get(k) or [])]
    mine = [r for r in rooms if r in own_rooms(app)]
    if not mine:
        return body, []
    if not app.eng.cfg.is_managing():
        raise ValueError("Ventilgeführte Räume lassen sich nur bei „HeatPilot steuert“ bedienen – am Thermostat "
                         "selbst bitte nichts umstellen (das beendet die Ventilsteuerung)")
    for c in mine:
        if action == "schedule" or (action == "mode" and body.get("mode") == "auto"):
            _set(app, c, None, now)
        elif action == "mode" and body.get("mode") == "off":
            _set(app, c, {"mode": "aus"}, now)
        elif action == "mode":
            sp, phase = _desired(app, c)
            _set(app, c, {"mode": "manuell", "sp": sp, "phase": phase}, now)
        elif action == "temp":
            _set(app, c, {"mode": "manuell", "sp": float(body.get("temp")), "phase": _desired(app, c)[1]}, now)
        elif action == "boost":
            minutes = int(body.get("minutes"))
            _set(app, c, {"mode": "boost", "sp": float(body.get("temp")), "until": int(now) + minutes * 60}, now)
    rest = [r for r in rooms if r not in mine]
    if not rest:
        return None, mine
    return {**{k: v for k, v in body.items() if k not in keys}, "rooms": rest, "room": rest[0]}, mine


def apply(st: dict, sp: float, phase: str | None, ts: int) -> tuple[float, str | None]:
    """Soll für den PID-Schritt mit der eigenen Betriebsart – (Soll, Text) bzw. (``sp``, None) ohne. Abgelaufenes
    (Boost vorbei, Manuell bis zum Phasenwechsel) fällt dabei weg."""
    own = st.get("own")
    if not own:
        return sp, None
    mode = own.get("mode")
    if (mode == "boost" and ts >= int(own.get("until") or 0)) or (
            mode == "manuell" and own.get("phase") is not None and phase != own.get("phase")):
        st.pop("own", None)
        return sp, None
    if mode == "aus":
        return FROST_C, LABELS["aus"]
    value = own.get("sp")
    return (sp, None) if value is None else (float(value), f"{LABELS.get(mode, mode)} {value:g} °C")
