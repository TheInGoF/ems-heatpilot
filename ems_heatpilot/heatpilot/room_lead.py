"""Führung je Raum (seit 0.51): temperaturgeführt (Soll ans Thermostat, wie bisher) oder ventilgeführt.

Ventilgeführt heißt: Der Raum hat ein Stellventil mit eigener ``number``-Entität für die Öffnung in Prozent
(z. B. Shelly BLU TRV), und HeatPilot stellt die Öffnung direkt (``valve_pid``) statt ein Soll zu schreiben. Je Raum
zwei neue Einstellungen (``cfg.rooms``-Eintrag, Prüfung hier in ``clean_entry`` – Muster ``floor_limit.clean_entry``):

* ``lead`` – ``temp`` (Standard) | ``ventil``,
* ``valve_pos`` – die ``number``-Entität der Ventilöffnung (0–100 %), Pflicht bei ``ventil``.

``check_valve_call`` sind die harten Schutzregeln des Schreibwegs (``control.Control._call``): nur eine zugewiesene
Entität, nur ``value``, ganze Prozent 0–100, innerhalb der Grenzen der Entität, Entität verfügbar. Alles andere wird
mit ``PermissionError`` abgewiesen – wie beim Vorlauf-Soll (Weg B).
"""

from __future__ import annotations

import re

NUMBER = re.compile(r"^number\.[a-z0-9_]+$")
LEADS = ("temp", "ventil")


def clean_entry(r: dict, climate: str) -> dict:
    """Die Felder ``lead`` und ``valve_pos`` eines Raum-Eintrags prüfen (``room_entry.clean_room``)."""
    out: dict = {}
    valve = str(r.get("valve_pos") or "").strip().lower()
    if valve:
        if not NUMBER.match(valve):
            raise ValueError(f"{climate}: Ventilstellung muss eine number-Entität sein, nicht {valve!r}")
        out["valve_pos"] = valve
    lead = str(r.get("lead") or "").strip().lower()
    if lead and lead not in LEADS:
        raise ValueError(f"{climate}: Führung muss „temp“ oder „ventil“ sein, nicht {lead!r}")
    if lead == "ventil":
        if not valve:
            raise ValueError(f"{climate}: ventilgeführt braucht die number-Entität der Ventilstellung")
        out["lead"] = lead
    return out


def valve_led(room) -> bool:
    """Führt HeatPilot diesen Raum über die Ventilöffnung (statt über das Soll)?"""
    return getattr(room, "lead", "temp") == "ventil" and bool(getattr(room, "valve_pos", None))


def valve_rooms(rooms) -> dict[str, str]:
    """{Ventil-Entität: Raumname} der ventilgeführten Räume, die mitmachen – die erlaubten Schreibziele."""
    return {room.valve_pos: room.name for room in rooms if room.managed and valve_led(room)}


def check_valve_call(valves: dict[str, str], entity_id: str, data: dict, state: dict | None) -> None:
    """Harte Schutzregeln für ``number.set_value`` auf eine Ventilöffnung (siehe Modulbeschreibung)."""
    if entity_id not in valves:
        raise PermissionError(f"number.set_value auf {entity_id} ist gesperrt – keine Ventilstellung eines "
                              "ventilgeführten Raums")
    value = data.get("value")
    if set(data) != {"value"} or isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise PermissionError(f"Ventilstellung {value!r} gesperrt – nur ganze Prozent von 0 bis 100")
    st = state or {}
    if not st or str(st.get("state")) in ("unknown", "unavailable"):
        raise PermissionError(f"{entity_id} ist nicht verfügbar – gesperrt")
    attrs = st.get("attributes") or {}
    lo, hi = _num(attrs.get("min")), _num(attrs.get("max"))
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise PermissionError(f"Ventilstellung {value} % außerhalb der Grenzen von {entity_id}")


def _num(value) -> float | None:
    try:
        return None if isinstance(value, bool) else float(value)
    except (TypeError, ValueError):
        return None
