"""Fußbodenheizung je Raum (seit 0.39.1): Anschluss und höchster Vorlauf – eine harte Grenze für jedes Vorlauf-Soll.

Ein Raum-Eintrag der Einstellungen (Räume) sagt mit ``floor_type``, wie seine Fußbodenheizung angeschlossen ist, und mit
``floor_max_flow`` den höchsten Vorlauf, den sie verträgt (25–70 °C, ohne Angabe ``DEFAULT_MAX_FLOW`` – vorsichtig):

* **mischer** – ein Mischer regelt die Fußboden-Temperatur selbst nach: Der Vorlauf darf höher sein, wie es gerade
  passt; aus diesem Raum kommt keine Grenze.
* **rtl** – Rücklauftemperaturbegrenzer (RTL-Ventil): Ist der Boden kalt, fließt zuerst der volle Vorlauf hinein. Die
  Grenze gilt seit 0.46.5 **immer** (vorher nur, solange der Raum Wärme anforderte): Das RTL-Ventil öffnet, sobald der
  Rücklauf kalt ist – auch wenn der Heizkörper im Raum gerade nichts anfordert. Die Anforderung (``demand``) – ein Gerät
  des Raums (bei Gruppen genügt eines) mehr als 0,1 K unter seinem Soll, Ventil über 5 % oder ``hvac_action``
  „heating“ bzw. „preheating“ (Home Assistant: heizt vor); unbekannt ohne Zustand bzw. nicht verfügbar, ausgeschaltet
  ohne ``hvac_action`` – steht nur noch im Grund („fordert an“, „fordert nicht an“, „Anforderung unbekannt“).
* **direkt** – direkt am Kessel: Die Grenze gilt immer, auch ohne Raummodell und ohne Messwert.

Räume aus ``floor_heating_rooms`` (Einstellungen vor 0.39.1) ohne Anschluss heißen „unbekannt“ und gelten wie „direkt“;
„Bereit für die Steuerung?“ sagt dann „Anschluss wählen“. ``floor_heating_rooms`` bleibt die Liste aller Räume mit
Fußbodenheizung (Raummodelle, Abgleich, Zeitplan, Vorschau fragen dort): ``migrate`` ergänzt sie um die Räume mit
Anschluss. Bei einer Gruppe zählt jedes Gerät mit Fußboden-Angabe, es gilt die strengere (direkt vor RTL vor Mischer,
die niedrigste Grenze).

``flow_cap`` gibt die wirksame Grenze jetzt: das Minimum über die Fußboden-Räume ohne Mischer, mit Grund
(„Fußboden Bad (RTL-Ventil, fordert an): höchstens 50 °C“); ``cap_of`` dasselbe für eine bekannte Anforderung je Raum.
Die Vorschau nimmt jetzt (erster Schritt) dieselbe Anforderung aus den Messwerten wie ``flow_cap``, danach die Schritte,
in denen der Raum laut Vorschau Wärme braucht; ist ein Gerät ohne Zustand (``blind``), bleibt sie offen – für den Grund.
Reine Funktionen, schreibt nichts.
"""

from __future__ import annotations

from dataclasses import dataclass

from .values import num

KINDS = ("mischer", "rtl", "direkt")
FLOOR_TYPES = ("", *KINDS)    # gespeichert; "" = keine Fußbodenheizung (in floor_heating_rooms: Anschluss unbekannt)
UNKNOWN = "unbekannt"
LABELS = {"mischer": "Mischer", "rtl": "RTL-Ventil", "direkt": "direkt am Kessel", UNKNOWN: "Anschluss unbekannt"}
DEFAULT_MAX_FLOW = 45.0       # ohne Angabe – vorsichtig, für jede Anlage
MAX_FLOW_MIN, MAX_FLOW_MAX = 25.0, 70.0
DEMAND_K = 0.1                # fordert an: Raum mehr als so viel unter dem Soll …
VALVE_PCT = 5.0               # … oder Ventil weiter offen …
HEATING_ACTIONS = ("heating", "preheating")  # … oder das Thermostat meldet „heizt“ bzw. „heizt vor“ (HVACAction)
_STRICT = {"mischer": 0, "rtl": 1, UNKNOWN: 2, "direkt": 3}  # Gruppe mit verschiedenen Angaben: die strengere gilt


@dataclass(frozen=True)
class FloorRoom:
    """Ein Raum mit Fußbodenheizung: Anschluss, Grenze und seine Geräte (climate, Ventilsensor) für die Anforderung."""

    name: str
    climate: str              # Schlüssel des Raums (wie Raummodell und Vorschau)
    kind: str                 # mischer | rtl | direkt | unbekannt
    max_flow: float           # höchster Vorlauf (°C)
    devices: tuple = ()       # ((climate, heating_power | None), …)


def _deg(v: float) -> str:
    return f"{round(v, 1):g}".replace(".", ",")


def _valid_max(value) -> float | None:
    v = num(value)
    return v if v is not None and MAX_FLOW_MIN <= v <= MAX_FLOW_MAX else None


# ------------------------------------------------------------------ Einstellungen
def clean_entry(r: dict, climate: str) -> dict:
    """Fußboden-Felder eines Raum-Eintrags aus der Oberfläche (``config._clean_room``): Anschluss nur, wenn gesetzt;
    höchster Vorlauf nur, wenn angegeben – auf halbe Grad. Fehler als ValueError mit der Entität."""
    out: dict = {}
    kind = str(r.get("floor_type") or "").strip().lower()
    if kind not in FLOOR_TYPES:
        raise ValueError(f"{climate}: Anschluss der Fußbodenheizung – erlaubt: {', '.join(KINDS)} oder leer")
    if kind:
        out["floor_type"] = kind
    if r.get("floor_max_flow") not in (None, ""):
        try:
            v = round(float(str(r["floor_max_flow"]).replace(",", ".")) * 2) / 2
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{climate}: höchster Vorlauf der Fußbodenheizung keine Zahl") from None
        if not MAX_FLOW_MIN <= v <= MAX_FLOW_MAX:
            raise ValueError(f"{climate}: höchster Vorlauf der Fußbodenheizung muss zwischen {MAX_FLOW_MIN:g} und "
                             f"{MAX_FLOW_MAX:g} °C liegen")
        out["floor_max_flow"] = v
    return out


def migrate(rooms: list[dict], floor_rooms: list[str] | None) -> list[str]:
    """Gespeicherte Einstellungen lesen (``Config.from_raw``) bzw. speichern (``validate``): unlesbare Fußboden-Angaben
    verwerfen – ein unlesbarer Anschluss zählt als Fußboden mit unbekanntem Anschluss (sicher), eine unlesbare Grenze
    als Standard – und ``floor_heating_rooms`` um die Räume mit Anschluss ergänzen. Ändert die Einträge an Ort und
    Stelle, gibt die neue Liste zurück (alte Einstellungen bleiben, wie sie sind)."""
    if isinstance(floor_rooms, str):  # von Hand als Text statt Liste
        floor_rooms = [floor_rooms]
    out = [c for c in floor_rooms if isinstance(c, str)] if isinstance(floor_rooms, (list, tuple)) else []
    for r in rooms:
        kind = r.get("floor_type")
        if kind not in (None, ""):
            if kind not in KINDS:
                del r["floor_type"]
            if r.get("climate") and r["climate"] not in out:
                out.append(r["climate"])
        if r.get("floor_max_flow") is not None and _valid_max(r["floor_max_flow"]) is None:
            del r["floor_max_flow"]
    return out


def _kind(entry: dict | None, legacy: bool) -> str:
    v = (entry or {}).get("floor_type")
    if v in KINDS:
        return v
    return UNKNOWN if legacy or v not in (None, "") else ""


def floor_rooms(cfg, rooms) -> list[FloorRoom]:
    """Die Räume (``discovery.Room``) mit Fußbodenheizung laut Einstellungen – Anschluss und Grenze je Raum."""
    entries = {r.get("climate"): r for r in getattr(cfg, "rooms", None) or [] if isinstance(r, dict)}
    legacy = set(getattr(cfg, "floor_heating_rooms", None) or [])
    out = []
    for room in rooms:
        found = [(k, entries.get(c) or {}) for c in room.climates
                 if (k := _kind(entries.get(c), c in legacy))]
        if not found:
            continue
        kind = max((k for k, _ in found), key=_STRICT.__getitem__)
        limits = [_valid_max(e.get("floor_max_flow")) or DEFAULT_MAX_FLOW for k, e in found if k != "mischer"]
        out.append(FloorRoom(room.name, room.climate, kind, min(limits) if limits else DEFAULT_MAX_FLOW,
                             tuple((d["climate"], d.get("heating_power")) for d in room.devices)))
    return out


# ------------------------------------------------------------------ Anforderung
def read(rooms: list[FloorRoom], states: dict) -> dict[str, dict | None]:
    """Messwerte je Gerät der Fußboden-Räume aus Home Assistant: {climate: {temp, target, valve, action}}; None, wenn
    das Gerät keinen Zustand hat (fehlt, „unavailable“, „unknown“). Ausgeschaltet: kein Soll."""
    out: dict[str, dict | None] = {}
    for fr in rooms:
        for climate, sensor in fr.devices:
            st = states.get(climate)
            if not st or st.get("state") in (None, "unavailable", "unknown"):
                out[climate] = None
                continue
            a = st.get("attributes") or {}
            out[climate] = {"temp": num(a.get("current_temperature")),
                            "target": num(a.get("temperature")) if st.get("state") != "off" else None,
                            "valve": num((states.get(sensor) or {}).get("state")) if sensor else None,
                            "action": a.get("hvac_action")}
    return out


def _device_demand(d: dict | None) -> bool | None:
    """Fordert ein Gerät Wärme an? None: nichts bekannt."""
    if not d:
        return None
    known = False
    if d.get("temp") is not None and d.get("target") is not None:
        known = True
        if d["temp"] < d["target"] - DEMAND_K - 1e-9:
            return True
    if d.get("valve") is not None:
        known = True
        if d["valve"] > VALVE_PCT:
            return True
    if d.get("action"):
        known = True
        if str(d["action"]).lower() in HEATING_ACTIONS:
            return True
    return False if known else None


def demand(room: FloorRoom, readings: dict) -> bool | None:
    """Fordert der Raum Wärme an? Ein Gerät genügt (True); weiß man von einem Gerät nichts, ist es offen (None)."""
    each = [_device_demand(readings.get(c)) for c, _ in room.devices]
    if any(x is True for x in each):
        return True
    return None if not each or any(x is None for x in each) else False


def blind(room: FloorRoom, readings: dict) -> bool:
    """Weiß HeatPilot von einem Gerät des Raums gar nichts (kein Zustand, „unavailable“, „unknown“)? Dann lässt sich
    auch für die Zukunft nichts sagen – die Vorschau lässt die Anforderung offen, die Grenze gilt (sicher)."""
    return any(readings.get(c) is None for c, _ in room.devices)


# ------------------------------------------------------------------ Grenze
def applies(room: FloorRoom, need: bool | None) -> bool:
    """Gilt die Grenze des Raums? Mischer nie, sonst immer – seit 0.46.5 auch beim RTL-Ventil: Es öffnet, sobald der
    Rücklauf kalt ist, unabhängig davon, ob der Raum Wärme anfordert (``need`` bleibt für die Anzeige)."""
    return room.kind != "mischer"


def why(room: FloorRoom, need: bool | None) -> str:
    """Grund-Text eines Raums: „Fußboden Bad (RTL-Ventil, fordert an): höchstens 50 °C“."""
    label = LABELS[room.kind]
    if room.kind == "mischer":
        return f"Fußboden {room.name} ({label}): keine Grenze"
    if room.kind == UNKNOWN:
        label += " – wie direkt am Kessel"
    elif room.kind == "rtl":
        label += {True: ", fordert an", False: ", fordert nicht an", None: ", Anforderung unbekannt"}[need]
    if not applies(room, need):
        return f"Fußboden {room.name} ({label}): Grenze {_deg(room.max_flow)} °C gilt gerade nicht"
    return f"Fußboden {room.name} ({label}): höchstens {_deg(room.max_flow)} °C"


def cap_of(rooms: list[FloorRoom], needs: dict) -> tuple[float | None, str | None]:
    """(Grenze °C, Grund) bei bekannter Anforderung je Raum (``needs``: Schlüssel → True/False/None; fehlt = None) –
    das Minimum über die Räume, für die sie gilt; (None, None), wenn keine gilt."""
    hits = [(r.max_flow, i, r) for i, r in enumerate(rooms) if applies(r, needs.get(r.climate))]
    if not hits:
        return None, None
    _, _, r = min(hits)
    return r.max_flow, why(r, needs.get(r.climate))


def flow_cap(rooms: list[FloorRoom], readings: dict) -> tuple[float | None, str | None]:
    """Wirksame Grenze jetzt aus den Messwerten je Gerät (``read``) – siehe Modulbeschreibung."""
    return cap_of(rooms, {r.climate: demand(r, readings) for r in rooms})


# ------------------------------------------------------------------ Anzeige
def rule(room: FloorRoom) -> str:
    """Wann die Grenze des Raums gilt – für Vorschau und Doku."""
    if room.kind == "mischer":
        return "ohne Grenze (Mischer)"
    when = "immer"
    return f"höchstens {_deg(room.max_flow)} °C, {when} ({LABELS[room.kind]})"


def describe(rooms: list[FloorRoom]) -> dict | None:
    """Fußboden-Räume der Einstellungen für die Vorschau: je Raum Anschluss, Grenze und Regel, dazu ein Satz."""
    if not rooms:
        return None
    return {"rooms": [{"name": r.name, "climate": r.climate, "kind": r.kind, "label": LABELS[r.kind],
                       "max_flow": r.max_flow, "rule": rule(r)} for r in rooms],
            "text": "Fußboden-Grenze für den Vorlauf: " + "; ".join(f"{r.name} {rule(r)}" for r in rooms) + "."}


def status(cfg, rooms, states: dict) -> dict:
    """Stand jetzt für „Bereit für die Steuerung?“: wirksame Grenze, je Raum Anschluss, Anforderung und Grund, Hinweise
    (wirksame Grenze; „Anschluss wählen“ bei unbekanntem Anschluss)."""
    floors = floor_rooms(cfg, rooms)
    readings = read(floors, states)
    needs = {r.climate: demand(r, readings) for r in floors}
    cap, cap_why = cap_of(floors, needs)
    notes = []
    if floors:
        notes.append(f"Vorlauf-Grenze der Fußbodenheizung jetzt: {cap_why}" if cap is not None else
                     "Fußbodenheizung: gerade keine Vorlauf-Grenze (" + "; ".join(why(r, needs[r.climate])
                                                                         for r in floors) + ")")
    notes += [f"Fußboden {r.name}: Anschluss wählen (Einstellungen → Räume: Mischer, RTL-Ventil oder direkt am Kessel) – "
              f"bis dahin gilt höchstens {_deg(r.max_flow)} °C immer, wie direkt am Kessel"
              for r in floors if r.kind == UNKNOWN]
    return {"cap": cap, "why": cap_why, "unknown": [r.name for r in floors if r.kind == UNKNOWN], "notes": notes,
            "rooms": [{"name": r.name, "climate": r.climate, "kind": r.kind, "label": LABELS[r.kind],
                       "max_flow": r.max_flow, "demand": needs[r.climate], "applies": applies(r, needs[r.climate]),
                       "text": why(r, needs[r.climate])} for r in floors]}
