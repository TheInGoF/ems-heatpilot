"""Eigenes Climate je Raum (seit 0.72, für ventilgeführte Räume seit 0.71): HeatPilots Betriebsarten statt der des
Thermostats.

Für jeden Raum, den HeatPilot führt („macht mit“ bei „HeatPilot steuert“), gibt es ein internes Climate mit eigenen
Betriebsarten – das Thermostat-Climate dient nur noch der Einrichtung. Tado bekommt daraus nur noch Temperaturen (nie
seine Automatik), ein ventilgeführter Raum nur die Öffnung (``Control`` sperrt dort jedes ``climate``). Die Knöpfe der
Seite und der Karten (``heat_actions``: Boost, Zeitplan, Soll, Betriebsart) wirken bei diesen Räumen nur hier:

* **Zeitplan** (``auto`` bzw. „Zeitplan“) – HeatPilots Plan (Nacht, Komfort, Abwesend, Vorheizen …),
* **Manuell** (``heat`` bzw. ein Soll) – dieses Soll bis zum nächsten Phasenwechsel des Plans,
* **Aus** (``off``) – nur der Frostschutz ``FROST_C``, bis jemand etwas anderes wählt,
* **Boost** – das Boost-Soll für die gewählte Dauer.

Der Stand je Raum steht im Zustand der Regelschleife (``ControlLoop.state[climate]["own"]``, kv); ``desired`` legt
ihn im Takt über den Wunschzustand – daraus schreibt die Soll-Schiene das Soll bzw. stellt der PID die Öffnung. Andere
Climate-Karten im Dashboard bedienen HeatPilot nicht; dafür gibt es die HeatPilot-Karte.
"""

from __future__ import annotations

import time

FROST_C = 7.0
LABELS = {"aus": "Aus (Frostschutz)", "manuell": "Manuell", "boost": "Boost"}


def own_rooms(app) -> set[str]:
    """Die Räume, die HeatPilot führt – nur bei „HeatPilot steuert“ und „macht mit“ (ventil- wie temperaturgeführt)."""
    eng = app.eng
    if eng is None or not eng.cfg.is_managing():
        return set()
    return {r.climate for r in eng.found.rooms if r.managed and not r.ems}


def _set(app, climate: str, own: dict | None, now: float) -> None:
    loop = app.eng.control_loop
    st = loop.state.setdefault(climate, {})
    if own is None:
        st.pop("own", None)
    else:
        st["own"] = {**own, "since": int(now)}
    st.pop("last_written", None)  # neu schreiben, auch nach einer Hand am Thermostat
    info = loop.rooms.get(climate)
    if info is not None:  # Anzeige sofort (Knopf, Farbe), nicht erst nach dem nächsten Takt
        info["own"] = st.get("own")
        info["desired"] = apply(dict(st), st.get("plan_sp", info.get("desired")), info.get("phase"), int(now))[0]
    ctl = getattr(app, "control", None)  # 0.73.3: ein alter Boost (Tado-Timer, lokal) hielt die Regelschleife an
    if ctl is not None and ctl.boosts.pop(climate, None) is not None:
        ctl._save()  # HeatPilots nächstes Soll löst auch den Timer am Thermostat ab
    vp = getattr(app.eng, "valve_pid", None)
    if vp is not None:
        (vp.state.get("rooms") or {}).get(climate, {}).pop("hold_until", None)  # beendet „von Hand am Ventil“
    from .controlloop import KV_STATE  # spät: controlloop nutzt dieses Modul
    loop.store.put(KV_STATE, loop.state)


def _desired(app, climate: str) -> tuple[float | None, str | None]:
    info = (app.eng.control_loop.rooms if app.eng.control_loop is not None else {}).get(climate) or {}
    return info.get("desired"), info.get("phase")


def take(app, action: str, body: dict, now: float | None = None) -> tuple[dict | None, list[str]]:
    """Die von HeatPilot geführten Räume aus ``body`` selbst bedienen. Gibt (``body`` für die übrigen Räume bzw. None,
    wenn keiner übrig ist; die selbst bedienten Räume) zurück; ``ValueError`` mit Text bei Unsinn."""
    now = time.time() if now is None else now
    keys = ("rooms", "room")
    rooms = [r for k in keys for r in ([body.get(k)] if isinstance(body.get(k), str) else body.get(k) or [])]
    mine = [r for r in rooms if r in own_rooms(app)]
    if not mine:
        return body, []
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
    """Soll mit der eigenen Betriebsart – (Soll, Text) bzw. (``sp``, None) ohne. Abgelaufenes (Boost vorbei, Manuell
    bis zum Phasenwechsel) fällt dabei weg."""
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


def boosts(loop, now: float | None = None) -> dict[str, dict]:
    """Laufende eigene Boosts je Raum – im Format von ``Control.active`` (Boost-Sensor, Karte, Raum-Anzeige)."""
    now = time.time() if now is None else now
    out = {}
    for c, st in (getattr(loop, "state", None) or {}).items():
        own = st.get("own") or {}
        until = int(own.get("until") or 0)
        if own.get("mode") == "boost" and until > now:
            out[c] = {"temp": float(own["sp"]), "end": until, "left_min": max(0, round((until - now) / 60)),
                      "minutes": round((until - int(own.get("since") or now)) / 60), "local": True}
    return out


def desired(st: dict, d, ts: int):
    """Wunschzustand eines Raums (``controlloop.Desired``) mit der eigenen Betriebsart darüber; das Soll des Plans steht
    in ``plan_sp`` (für die Anzeige gleich nach „Zeitplan“)."""
    st["plan_sp"] = d.setpoint
    sp, label = apply(st, d.setpoint, d.phase, ts)
    return d if label is None else d._replace(setpoint=sp, reason=label)
