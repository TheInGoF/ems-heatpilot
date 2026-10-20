"""Feste Raumkennung (seit 0.44): Jeder Raum hat eine eigene Kennung, die bleibt, wenn sein Thermostat wechselt.

Messwerte und Modelle eines Raums liegen weiter unter seinem Schlüssel (der climate-Entität des Schlüsselgeräts). Die
Kennung sagt, welcher Raum es ist – auch wenn der Schlüssel wechselt (Tado-Cloud → HomeKit, neues Thermostat, HA
benennt um). ``room_identity`` merkt sich je Kennung den zuletzt gesehenen Schlüssel und erkennt so den Wechsel.

* ``assign`` – beim Speichern: Jeder Raum-Eintrag der Einstellungen bekommt die Kennung seines Raums (alle Geräte
  eines Raums – gleicher „Raum“ – dieselbe). Woher, in dieser Reihenfolge: der Raum enthält das bisherige
  Schlüsselgerät einer Kennung und ist noch derselbe Raum (gleicher Name bzw. Raum, oder die Mehrheit der bisherigen
  Geräte – ein nur in einen anderen Raum umgehängtes Schlüsselgerät nimmt die Kennung nicht mit); die mitgeschickte Kennung (Oberfläche); die des Raums, zu dem eines seiner Geräte
  bisher gehörte; die eines bisherigen Raums gleichen Namens, dessen Geräte alle nicht mehr gewählt sind (neues
  Thermostat für denselben Raum – ``room_identity`` meldet das als Wechsel); sonst neu. Dazu bleibt das bisherige
  Schlüsselgerät eines Raums ohne Raumregler vorn: Ein weiteres Gerät im Raum, in der Liste davor (Tado-Cloud und
  HomeKit im selben Bereich), machte sich bis 0.43 zum Schlüssel – die Werte des Raums lagen danach „ohne Raum“.
* ``migrate`` – erster Start von 0.44: Einträge ohne Kennung ergänzen; sonst bleiben die Einstellungen, wie sie sind.
* ``label`` – nach der Erkennung ``Room.room_id`` setzen; im Auto-Modus (ohne Raum-Einträge) die schon gemerkte
  Kennung des Schlüssels (kv ``rooms.identity``), sonst aus Bereich bzw. Name – so hängt sie nicht an der Reihenfolge.

Liest und schreibt nichts an Home Assistant.
"""

from __future__ import annotations

import hashlib

from .room_adopt import norm
from .room_entry import ID  # gültige Kennung (auch „auto:wohnzimmer“ aus dem Auto-Modus)


def valid(rid) -> bool:
    return isinstance(rid, str) and bool(ID.match(rid))


def new_id(seed: str, used: set[str]) -> str:
    """Neue Kennung aus dem Schlüssel zur Zeit der Vergabe (gleiche Eingabe → gleiche Kennung), nie eine benutzte."""
    base = "r" + hashlib.sha1(seed.encode()).hexdigest()[:10]
    rid, n = base, 2
    while rid in used:
        rid, n = f"{base}-{n}", n + 1
    return rid


def _slug(text) -> str:
    return norm(text).replace(" ", "-")


def groups(entries: list[dict]) -> list[list[dict]]:
    """Raum-Einträge je Raum wie ``discovery.group_rooms``: gleicher „Raum“ = ein Raum, ohne: eigener Raum."""
    out: dict[str, list[dict]] = {}
    for i, e in enumerate(entries):
        out.setdefault(e.get("group") or f"\0{i}", []).append(e)
    return list(out.values())


def _head(group: list[dict]) -> dict:
    return next((e for e in group if e.get("controller")), group[0])


def _previous(before) -> tuple[dict, dict, dict, dict]:
    """Aus den bisherigen Räumen (``Room`` mit ``room_id``): Gerät → Kennung, Kennung → Schlüssel, Name → (Kennung,
    Geräte) – Namen nur, wenn sie eindeutig sind – und Kennung → (Namen, Geräte)."""
    by_climate, heads, names, twice, rooms = {}, {}, {}, set(), {}
    for r in before or []:
        rid = getattr(r, "room_id", None)
        if not rid:
            continue
        heads[rid] = r.climate
        rooms[rid] = ({_slug(r.name), _slug(r.group)} - {""}, set(r.climates))
        for c in r.climates:
            by_climate.setdefault(c, rid)
        for label in {_slug(r.name), _slug(r.group)} - {""}:
            if label in names:
                twice.add(label)
            names[label] = (rid, set(r.climates))
    return by_climate, heads, {k: v for k, v in names.items() if k not in twice}, rooms


def _candidates(group: list[dict], prev: tuple, chosen: set[str]) -> list[list[str]]:
    """Kennungen für einen Raum, nach Rang (siehe oben)."""
    by_climate, heads, names, rooms = prev
    climates = [e["climate"] for e in [_head(group), *group]]
    label = _slug(group[0].get("group") or _head(group).get("name"))
    named = names.get(label)
    return [
        [rid for rid, key in heads.items() if key in climates and _same_room(rooms[rid], label, climates)],
        [e["id"] for e in [_head(group), *group] if valid(e.get("id"))],
        [by_climate[c] for c in climates if c in by_climate],
        [named[0]] if named and not named[1] & chosen else [],
    ]


def _same_room(before: tuple[set, set], label: str, climates: list[str]) -> bool:
    """Ist der Raum mit dem bisherigen Schlüsselgerät noch derselbe? Gleicher Name bzw. Raum, oder er enthält die
    Mehrheit der bisherigen Geräte. Sonst wurde nur das Gerät umgehängt – die Kennung bleibt beim alten Raum."""
    labels, devices = before
    return label in labels or 2 * len(devices & set(climates)) > len(devices)


def _keep_head(group: list[dict], key: str | None) -> list[dict]:
    """Ohne Raumregler bleibt das bisherige Schlüsselgerät vorn (``group_rooms`` nimmt das erste)."""
    if key is None or any(e.get("controller") for e in group) or group[0]["climate"] == key:
        return group
    first = [e for e in group if e["climate"] == key]
    return first + [e for e in group if e["climate"] != key] if first else group


def assign(entries: list[dict], before=None) -> list[dict]:
    """Kennungen vergeben (beim Speichern bzw. Migrieren). ``before``: die bisherigen Räume (``Room`` mit
    ``room_id``) oder None. Gibt neue Einträge zurück (je Raum zusammenhängend, das Schlüsselgerät vorn)."""
    prev = _previous(before)
    chosen = {e["climate"] for e in entries}
    rooms = groups(entries)
    cands = [_candidates(g, prev, chosen) for g in rooms]
    ids: list[str | None] = [None] * len(rooms)
    ranks = [4] * len(rooms)
    used: set[str] = set()
    for rank in range(4):
        for i, c in enumerate(cands):
            if ids[i] is None:
                ids[i] = next((rid for rid in c[rank] if rid not in used), None)
                if ids[i]:
                    used.add(ids[i])
                    ranks[i] = rank
    known = used | set(prev[1])
    out = []
    for i, group in enumerate(rooms):
        if ids[i] is None:
            ids[i] = new_id(_head(group)["climate"], known)
            known.add(ids[i])
        head = prev[1].get(ids[i]) if ranks[i] == 0 else None  # nur, wenn das Schlüsselgerät im Raum blieb
        out += [{**e, "id": ids[i]} for e in _keep_head(group, head)]
    return out


def migrate(entries: list[dict]) -> list[dict] | None:
    """Erster Start von 0.44: Einträge ohne (gültige, je Raum gleiche) Kennung ergänzen. None, wenn nichts fehlt."""
    if not entries:
        return None
    ids = {e["climate"]: e["id"] for e in assign(entries)}
    if all(e.get("id") == ids[e["climate"]] for e in entries):
        return None
    return [{**e, "id": ids[e["climate"]]} for e in entries]  # Reihenfolge wie gespeichert


def label(found, entries: list[dict], known: dict | None = None) -> None:
    """``Room.room_id`` setzen: aus dem Raum-Eintrag des Schlüssels, im Auto-Modus die zum Schlüssel gemerkte Kennung
    (``known`` = kv ``rooms.identity``), sonst aus Bereich bzw. Name (bei Gleichheit der Name, dann der Schlüssel)."""
    if entries:
        by_climate = {e.get("climate"): e.get("id") for e in entries}
        for r in found.rooms:
            rid = by_climate.get(r.climate)
            r.room_id = rid if valid(rid) else new_id(r.climate, set())
        return
    remembered = {v.get("key"): rid for rid, v in (known or {}).items()
                  if isinstance(v, dict) and isinstance(rid, str) and rid.startswith("auto:")}
    seen: set[str] = set()
    for r in found.rooms:  # zuerst die gemerkten: eine fehlende Zone gibt ihre Kennung nicht an die nächste weiter
        rid = remembered.get(r.climate)
        if rid and rid not in seen:
            r.room_id = rid
            seen.add(rid)
    for r in found.rooms:
        if r.room_id in seen and remembered.get(r.climate) == r.room_id:
            continue
        options = [f"auto:{c}" for c in (_slug(r.area), _slug(r.name)) if c] + [f"auto:{r.climate}"]
        r.room_id = next((o for o in options if o not in seen), options[-1])
        seen.add(r.room_id)
