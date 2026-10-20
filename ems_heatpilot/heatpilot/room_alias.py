"""Alte Temperatur-Quellen eines Raums merken (seit 0.42.1): Aliasse für die Langzeitstatistik.

Die Innentemperatur der Stunden vor HeatPilot kommt aus der Langzeitstatistik von Home Assistant – aus dem
Temperatursensor jedes Raums. Nach einem Wechsel der Integration (Tado-Cloud → HomeKit, Thermostat neu gekoppelt) hat
der neue Sensor nur wenige Tage Statistik; die Jahre davor stehen unter dem alten Sensor. Ein **Alias** merkt sich je
Raum diesen alten Sensor (kv ``rooms.aliases``): ``backfill.backfill_statistics`` nimmt für jede Stunde den aktuellen
Sensor des Raums und, wo er nichts hat, den Alias.

Gefunden wird der alte Sensor (nur lesen, nichts an Home Assistant):

* ``device`` – der Temperatursensor am Gerät der alten climate-Entität (Registry), solange die alte Integration noch
  eingerichtet ist;
* ``statistics`` – sonst eine °C-Statistik aus ``recorder/list_statistic_ids``, deren ID bzw. Name zum alten Raum
  passt (Name der Tado-Zone, des Raums oder Bereichs) – nur bei genau einem Treffer;
* ``user`` – vom Nutzer gewählt (``POST api/rooms/aliases``), auch für Räume ohne Übernahme.

Ein Alias ändert keine Messwerte; erst „Hausdaten wiederherstellen“ (``house_restore``) liest mit ihm die
Langzeitstatistik neu ein – zusammenführend, ohne etwas zu überschreiben, und nur, wenn der alte Sensor zum
gespeicherten Hausmittel passt (``indoor_mean``: Versatz, Streuung, Zusammenhang; das Ergebnis steht danach beim Alias
unter ``check`` – liefert die Quelle später nichts mehr, etwa nach dem Löschen der alten Integration, bleibt es, seit
0.45.1). Ein Alias ist eine Statistik-ID und braucht die alte Entität nicht. Ein Sensor, den ein anderer Raum schon
nutzt (aktueller Sensor oder Alias), wird abgewiesen (``check``). Wird ein Alias entfernt oder ersetzt, nimmt ``put`` die Innentemperatur der Stunden zurück, die er
gefüllt hat (``Store.revert_t_in`` – gefüllt wurde nur, wo sie fehlte); „Hausdaten wiederherstellen“ füllt sie danach
mit den übrigen Quellen neu.
"""

from __future__ import annotations

import logging
import re
import time

from . import room_adopt
from .discovery import device_sensors

log = logging.getLogger(__name__)

KV = "rooms.aliases"   # {Raumschlüssel: {stat, from, how, ts, check}}
HOW_TEXT = {"device": "Sensor am alten Gerät", "statistics": "Langzeitstatistik mit passendem Namen",
            "user": "selbst gewählt"}
# Wörter in Sensor-IDs und -Namen, die keinen Raum beschreiben (zusätzlich zu ``room_adopt.NOISE``)
TEMP_WORDS = {"temperature", "temperatur", "temp", "current", "aktuell", "aktuelle", "ist", "raumtemperatur",
              "measured", "gemessen", "air", "luft"}
UNITS = ("°C",)


def snorm(text) -> str:
    """Vergleichsform eines Sensor- oder Raumnamens: wie ``room_adopt.norm``, ohne Temperatur-Wörter
    („sensor.wohnzimmer_temperature“, „Wohnzimmer Temperatur“ → „wohnzimmer“)."""
    t = str(text or "")
    if t.startswith("sensor."):
        t = t[len("sensor."):]
    return " ".join(w for w in room_adopt.norm(t.replace("_", " ")).split() if w not in TEMP_WORDS)


def is_temp(meta: dict) -> bool:
    """°C-Statistik mit Mittelwert (alte HA: ``has_mean``, neuere: ``mean_type``)."""
    unit = meta.get("statistics_unit_of_measurement") or meta.get("display_unit_of_measurement")
    mean = meta.get("has_mean") is True or meta.get("mean_type") in (1, "arithmetic")
    return bool(meta.get("statistic_id")) and unit in UNITS and mean


def temp_stats(stat_list) -> dict[str, dict]:
    """°C-Statistiken aus ``list_statistic_ids``: {statistic_id: Eintrag}."""
    return {m["statistic_id"]: m for m in (stat_list or []) if isinstance(m, dict) and is_temp(m)}


# ------------------------------------------------------------------ Speicher
def get(store) -> dict[str, dict]:
    v = store.get(KV)
    return v if isinstance(v, dict) else {}


def put(store, room: str, stat: str | None, frm: str | None = None, how: str = "user",
        now_ts: int | None = None, found=None) -> dict | None:
    """Alias eines Raums setzen (``stat``) bzw. entfernen (``stat`` leer). Ersetzt bzw. entfernt das einen anderen
    alten Sensor, verlieren die Stunden, die er gefüllt hat, ihre Innentemperatur wieder (``Store.revert_t_in``) –
    außer er ist der aktuelle Sensor eines Raums (``found``). Gibt den neuen Eintrag zurück."""
    aliases = get(store)
    before = (aliases.get(room) or {}).get("stat")
    if stat:
        aliases[room] = {"stat": stat, "from": frm, "how": how, "ts": int(time.time() if now_ts is None else now_ts)}
    else:
        aliases.pop(room, None)
    store.put(KV, aliases)
    current = {d.get("temp_sensor") for r in found.rooms for d in r.devices} if found is not None else set()
    if before and before != stat and before not in current:
        n = store.revert_t_in(before)
        if n:
            log.info("Alter Sensor %s von %s entfernt: %d Stunden ohne Innentemperatur (bis zum Wiederherstellen)",
                     before, room, n)
    return aliases.get(room)


def note_checks(store, fitted: dict[str, dict], now_ts: int | None = None) -> None:
    """Prüfung je alter Quelle (``indoor_mean.fit`` beim Einlesen der Langzeitstatistik) beim Alias merken – die
    Oberfläche zeigt, ob er passt und warum nicht. Seit 0.45.1 bleibt eine gemerkte Prüfung, wenn die Quelle diesmal
    nichts zum Vergleichen lieferte (keine gemeinsamen Stunden – etwa weil Sensor bzw. Statistik gelöscht sind): Was
    einmal geprüft eingelesen wurde, wird dadurch nicht „passt nicht“."""
    aliases = get(store)
    ts = int(time.time() if now_ts is None else now_ts)
    changed = False
    for a in aliases.values():
        f = fitted.get(a.get("stat"))
        if f is not None and a.get("check") and not f.get("ok") and not f.get("hours"):
            continue
        if f is not None:
            a["check"] = {**{k: f.get(k) for k in ("ok", "offset", "spread", "corr", "hours", "why")}, "ts": ts}
            changed = True
    if changed:
        store.put(KV, aliases)


def sources(store, found) -> list[tuple[str, str | None, str | None]]:
    """Je aktuellem Raum (Schlüssel, aktueller Temperatursensor, Alias) für ``backfill_statistics`` – der Alias nur,
    wenn er nicht der aktuelle Sensor ist."""
    aliases = get(store)
    out = []
    for r in found.rooms:
        alias = (aliases.get(r.climate) or {}).get("stat")
        out.append((r.climate, r.temp_sensor, alias if alias and alias != r.temp_sensor else None))
    return out


def taken(store, found, room: str | None = None) -> set[str]:
    """Sensoren, die kein Alias von ``room`` sein können: aktuelle Temperatursensoren der Räume, Außenfühler,
    Aliasse anderer Räume, HeatPilots eigene Sensoren."""
    ids = {d.get("temp_sensor") for r in found.rooms for d in r.devices} | set(found.outdoor)
    ids |= {a.get("stat") for k, a in get(store).items() if k != room}
    return {i for i in ids if i}


# ------------------------------------------------------------------ Finden
def from_device(old: str, idx, states: dict, stats: dict | None) -> str | None:
    """Temperatursensor am Gerät der alten climate-Entität (Registry) – mit Langzeitstatistik, wenn die Liste da ist."""
    if idx is None or old not in idx.entity:
        return None
    sid = device_sensors(old, idx, states)["temp_sensor"]
    if sid and (stats is None or sid in stats):
        return sid
    return None


def match(labels: set[str], stats: dict, exclude: set[str]) -> list[str]:
    """°C-Statistiken, deren ID oder Name in Vergleichsform einem der ``labels`` gleicht."""
    want = {snorm(x) for x in labels} - {""}
    hits = []
    for sid, meta in stats.items():
        if sid in exclude or sid.startswith(("sensor.heatpilot_", "binary_sensor.")):
            continue
        if {snorm(sid), snorm(meta.get("name"))} & want:
            hits.append(sid)
    return sorted(hits)


def old_labels(store, states: dict, old: str) -> set[str]:
    """Namen eines alten Schlüssels: wie HeatPilot ihn nannte, Name in HA, aus dem Schlüssel."""
    friendly = ((states.get(old) or {}).get("attributes") or {}).get("friendly_name")
    return {room_adopt.name_of(store, states, old), friendly or "", room_adopt.key_name(old)}


def detect(store, found, old: str, room: str, states: dict, idx, stat_list) -> dict | None:
    """Alter Temperatursensor zu ``old`` für den Raum ``room`` (nach „Gelerntes übernehmen“): erst am alten Gerät,
    sonst genau ein passender Name in der Langzeitstatistik. {stat, from, how} oder None."""
    stats = temp_stats(stat_list) if stat_list is not None else None
    exclude = taken(store, found, room)
    sid = from_device(old, idx, states, stats)
    if sid and sid not in exclude:
        return {"stat": sid, "from": old, "how": "device"}
    if stats:
        hits = match(old_labels(store, states, old), stats, exclude)
        if len(hits) == 1:
            return {"stat": hits[0], "from": old, "how": "statistics"}
    return None


def adopted_from(store, room: str) -> list[str]:
    """Alte Schlüssel, die in den Raum übernommen wurden (Protokoll ``rooms.adopted``)."""
    return [e["from"] for e in (store.get(room_adopt.KV_LOG) or []) if isinstance(e, dict) and e.get("to") == room]


def room_candidates(store, found, room, states: dict, idx, stats: dict) -> list[dict]:
    """Mögliche Aliasse eines Raums: Sensor am Gerät übernommener alter Schlüssel, °C-Statistiken mit dem Namen
    eines übernommenen Schlüssels, des Raums, seiner Gruppe oder seines Bereichs. [{stat, name, how, from}]."""
    exclude = taken(store, found, room.climate)
    out: dict[str, dict] = {}
    for old in adopted_from(store, room.climate):
        sid = from_device(old, idx, states, stats)
        if sid and sid not in exclude:
            out.setdefault(sid, {"stat": sid, "how": "device", "from": old})
        for sid in match(old_labels(store, states, old), stats, exclude):
            out.setdefault(sid, {"stat": sid, "how": "statistics", "from": old})
    for sid in match({room.name, room.group or "", room.area or ""}, stats, exclude):
        out.setdefault(sid, {"stat": sid, "how": "statistics", "from": None})
    for c in out.values():
        c["name"] = stat_name(c["stat"], stats, states)
        c["how_text"] = HOW_TEXT[c["how"]]
    return sorted(out.values(), key=lambda c: (c["how"] != "device", c["stat"]))


def stat_name(sid: str, stats: dict, states: dict) -> str:
    """Anzeigename einer Statistik: Name in HA, sonst Name der Statistik, sonst die ID."""
    friendly = ((states.get(sid) or {}).get("attributes") or {}).get("friendly_name")
    return friendly or (stats.get(sid) or {}).get("name") or sid


def overview(store, found, states: dict, idx, stat_list, error: str | None = None) -> dict:
    """Für ``GET api/rooms/aliases``: je Raum aktueller Temperatursensor, Alias und Vorschläge; dazu alle °C-Statistiken
    zur freien Wahl (ohne die schon vergebenen)."""
    stats = temp_stats(stat_list)
    aliases = get(store)
    rooms = []
    for r in found.rooms:
        a = aliases.get(r.climate)
        cands = room_candidates(store, found, r, states, idx, stats)
        rooms.append({"key": r.climate, "name": r.name, "temp_sensor": r.temp_sensor,
                      "alias": None if not a else {**a, "name": stat_name(a["stat"], stats, states),
                                                   "how_text": HOW_TEXT.get(a.get("how"), a.get("how")),
                                                   "hours": store.t_in_hours(a["stat"])},
                      "candidates": cands, "suggested": cands[0]["stat"] if len(cands) == 1 and not a else None})
    free = sorted(set(stats) - taken(store, found))
    return {"rooms": rooms, "stats": [{"stat": s, "name": stat_name(s, stats, states)} for s in free],
            "error": error}


def owner(store, found, stat: str, room: str) -> str | None:
    """Wer ``stat`` schon nutzt (außer als Alias von ``room``): „der aktuelle Temperatursensor von Küche“ …"""
    for r in found.rooms:
        if stat in {d.get("temp_sensor") for d in r.devices}:
            return f"der aktuelle Temperatursensor von {r.name}" if r.climate != room else \
                "schon der aktuelle Temperatursensor dieses Raums"
    for key, a in get(store).items():
        if key != room and a.get("stat") == stat:
            other = found.room_of(key)
            return f"schon der alte Sensor von {other.name if other else key}"
    return None


def check(store, found, room: str, stat: str | None, stat_list) -> str | None:
    """Grund, warum ``stat`` kein Alias von ``room`` sein kann – None, wenn es geht (leer = Alias entfernen). Auch
    abgewiesen: ein Sensor, den ein anderer Raum schon nutzt (``owner``) – wie in der Auswahl der Oberfläche."""
    if room not in {r.climate for r in found.rooms}:
        return f"„{room or '–'}“ ist kein aktueller Raum."
    if not stat:
        return None
    if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", stat):
        return f"„{stat}“ ist keine Statistik-ID."
    if stat in set(found.outdoor):
        return f"„{stat}“ ist ein Außenfühler."
    used = owner(store, found, stat, room)
    if used:
        return f"„{stat}“ ist {used} – je Sensor nur ein Raum."
    if stat_list is not None and stat not in temp_stats(stat_list):
        return f"„{stat}“ hat keine Langzeitstatistik in °C mit Mittelwert."
    return None


def auto_assign(store, found, states: dict, idx, stat_list) -> list[dict]:
    """Für „Hausdaten wiederherstellen“: Räume ohne Alias bekommen ihren Vorschlag, wenn es genau einen gibt.
    Gibt die neu gesetzten zurück [{room, name, stat, how}]."""
    stats = temp_stats(stat_list)
    done = []
    for r in found.rooms:
        if r.climate in get(store):
            continue
        cands = room_candidates(store, found, r, states, idx, stats)
        if len(cands) == 1 and cands[0]["stat"] != r.temp_sensor:
            c = cands[0]
            put(store, r.climate, c["stat"], c["from"], c["how"])
            done.append({"room": r.climate, "name": r.name, "stat": c["stat"], "how": c["how"]})
    return done
