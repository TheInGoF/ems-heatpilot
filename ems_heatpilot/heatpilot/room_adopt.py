"""Gelerntes übernehmen (seit 0.42.1): Raumdaten von alten Entitäten auf neue.

Messwerte und Modelle eines Raums hängen an seinem Schlüssel – der climate-Entität seines Schlüsselgeräts. Nach einem
Wechsel der Integration (Tado-Cloud → HomeKit, Zigbee-Thermostat neu gekoppelt, Thermostat getauscht) hat derselbe
Raum einen neuen Schlüssel: Raum- und Ventilmodell beginnen von vorn, die Messwerte des alten Schlüssels liegen ohne
Raum in der Datenbank („verwaist“), und die Historie der neuen Entität in Home Assistant ist kurz.

* ``orphans`` – Schlüssel mit gespeicherten Messwerten (``room_samples``) bzw. Modellen (``model.room.*``,
  ``model.valve.*``), die kein aktueller Raumschlüssel sind – mit Name, Tagen, Zeitraum, Herkunft und je Raum der
  Überschneidung (Zeitpunkte, an denen der Raum selbst Messwerte hat). Auch ein Schlüssel, der heute nur Mitläufer
  eines Raums ist (``member_of``), zählt: seine alten Messwerte gehören zu keinem Raummodell.
* ``suggest`` – je aktuellem Raum höchstens ein sicherer Vorschlag: gleicher Name, Tado-Zone (bzw. Name) wie der
  Bereich, gleicher HA-Bereich – und nur, wenn der alte Schlüssel deutlich mehr Daten hat als der Raum. Jeder alte
  Schlüssel höchstens für einen Raum; ist ein Treffer nicht eindeutig, gibt es keinen Vorschlag (manuell wählbar).
* ``check`` – Grund, warum eine Übernahme nicht geht (Quelle nur ein verwaister Schlüssel mit Messwerten – ein
  Mitläufer nur in seinen Raum –, Ziel nur ein aktueller Raum).
* ``adopt`` – in **einer** Transaktion: Messwerte des alten Schlüssels dem Raum zuordnen. Hat der Raum zum selben
  Zeitpunkt schon einen Messpunkt, bleiben seine Werte; was ihm fehlt (Temperatur, Soll, Feuchte), kommt aus der
  alten Zeile, und eine gemessene Ventilöffnung geht vor einer aus ``hvac_action`` abgeleiteten bzw. als Ersatzwert
  markierten – erst danach fällt die alte Zeile weg. Ebenso Ventil-Ersatzwerte, Fremdwärme-Ereignisse,
  Ventil-Protokoll und Tagesstände der Lernkurve. Hydraulik-Änderungen (``balance.changes``) und der Stand der
  Empfehlungen werden umgeschrieben; Raum- und Ventilmodell beider Schlüssel verworfen – ``run``/``run_async`` lassen
  die Engine sie danach aus den Messwerten neu lernen (``Engine.relearn_adopted``, im Ablauf in Tagesabschnitten).
  Protokoll in kv ``rooms.adopted``.
* ``tally_file`` – Zählung für die Übersicht über eine eigene, nur lesende Verbindung (``web_adopt`` im Hintergrund-
  Thread – die Ereignisschleife bleibt frei).

Seit 0.44 nutzt ``room_switch`` ``adopt`` auch beim erkannten Thermostatwechsel (``valve=False``: anderes
Thermostat – die alte Ventilöffnung ergänzt das neue Ventil nicht, das Ventil-Protokoll bleibt beim alten Schlüssel).

Schreibt nichts an Home Assistant oder den Kessel und liest die HA-Historie nicht neu ein (Lesen aus HA füllt seit
0.44 nur Lücken – übernommene und eigene Werte bleiben).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time

from . import valve_learn
from .balance import PRESET_CHANGES_KEY
from .discovery import short_name
from .store import ROOM_NAMES_KEY

log = logging.getLogger(__name__)

KV_LOG = "rooms.adopted"   # Protokoll: [{ts, from, to, rows}]
LOG_MAX = 50
MORE_FACTOR = 2.0          # Vorschlag nur, wenn der alte Schlüssel mindestens doppelt so viele Tage hat …
MORE_DAYS = 3              # … und mindestens so viele Tage mehr als der Raum
ADVICE_KEY = "advice_state"  # Stand der Empfehlungen: Schlüssel „climate|Art|Titel“
OPEN_SHUT = (0.0, 100.0)   # Ventil aus hvac_action (heizt/heizt nicht) – gröber als eine gemessene Öffnung
# Integrationen, wie die Oberfläche sie nennt – sonst der Name der Integration
PLATFORMS = {"tado": "Tado-Cloud", "homekit_controller": "HomeKit", "zha": "Zigbee (ZHA)", "mqtt": "MQTT",
             "deconz": "deCONZ", "zwave_js": "Z-Wave", "matter": "Matter", "netatmo": "Netatmo",
             "fritzbox": "FRITZ!Box", "shelly": "Shelly", "evohome": "evohome"}
GONE = "nicht mehr in Home Assistant"
# Wörter, die keinen Raum beschreiben (nach dem Vereinheitlichen der Umlaute)
NOISE = {"thermostat", "thermostate", "heizung", "heizkorper", "radiator", "smart", "tado", "ventil", "valve",
         "climate", "regler", "raumregler", "wandthermostat", "heating", "heizkreis", "zone", "wireless", "sensor"}
_SERIAL = re.compile(r"[a-z]{2}\d{4,}")


class AdoptError(ValueError):
    """Übernahme nicht möglich – der Text sagt warum (für die Oberfläche)."""


class AdoptBusy(AdoptError):
    """Gerade nicht: HeatPilot baut neu auf bzw. liest die Historie neu ein."""


# ------------------------------------------------------------------ Namen
def norm(text) -> str:
    """Vergleichsform eines Namens: klein, Umlaute vereinheitlicht (ä/ae → a …), ohne Gerätewörter und Seriennummern
    („Küche Thermostat“, „kueche“, „climate.kuche“ → „kuche“). Leer, wenn nichts übrig bleibt."""
    t = str(text or "").lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"), ("ae", "a"), ("oe", "o"), ("ue", "u")):
        t = t.replace(a, b)
    words = [w for w in re.findall(r"[a-z0-9]+", t) if w not in NOISE and not _SERIAL.fullmatch(w)]
    return " ".join(words)


def key_name(key: str) -> str:
    """Name aus dem Schlüssel: ``climate.gaeste_zimmer`` → „Gaeste Zimmer“."""
    return key.split(".", 1)[-1].replace("_", " ").strip().title() or key


def _friendly(states: dict, key: str) -> str | None:
    return ((states.get(key) or {}).get("attributes") or {}).get("friendly_name")


def _model_name(store, key: str) -> str | None:
    return (store.get(f"model.room.{key}") or {}).get("room")


def name_of(store, states: dict, key: str) -> str:
    """Name eines alten Schlüssels: wie HeatPilot den Raum nannte (gespeichertes Raummodell bzw. – ist es verworfen –
    der gemerkte Name, kv ``rooms.names``), sonst der Name in Home Assistant, sonst aus dem Schlüssel."""
    return (_model_name(store, key) or (store.get(ROOM_NAMES_KEY) or {}).get(key) or _friendly(states, key)
            or key_name(key))


def source_of(key: str, states: dict, idx=None) -> str:
    """Herkunft für die Anzeige: Integration laut Registry, dazu „nicht mehr in Home Assistant“, wenn es die Entität
    nicht mehr gibt („Tado-Cloud“, „HomeKit, nicht mehr in Home Assistant“, „“)."""
    platform = (idx.entity.get(key, {}) if idx is not None else {}).get("platform")
    parts = [PLATFORMS.get(platform, platform)] if platform else []
    if key not in states:
        parts.append(GONE)
    return ", ".join(parts)


# ------------------------------------------------------------------ Bestand
def _day_sql(col: str = "ts") -> str:
    """Kalendertag eines Messpunkts in Ortszeit (Versatz von jetzt – für das Zählen der Tage genau genug)."""
    return f"({col} + {int(time.localtime().tm_gmtoff)}) / 86400"


def key_stats(store) -> dict[str, dict]:
    """Je Schlüssel in ``room_samples``: Zahl der Messpunkte, Tage mit Messwerten, erster und letzter Zeitpunkt.
    Ein Durchlauf über alle Raumwerte (bei Jahren an Daten einige Zehntelsekunden)."""
    return {r["room"]: {"rows": r["n"], "days": r["d"], "first_ts": r["a"], "last_ts": r["b"]}
            for r in store.query(f"SELECT room, COUNT(*) AS n, COUNT(DISTINCT {_day_sql()}) AS d, MIN(ts) AS a, "
                                 "MAX(ts) AS b FROM room_samples GROUP BY room")}


def overlaps(store, olds: list[str], rooms: list[str]) -> dict[str, dict[str, dict]]:
    """Je alter Schlüssel und Raum: an wie vielen Zeitpunkten (und Tagen) hat der Raum selbst schon Messwerte –
    {alt: {Raum: {rows, days}}}."""
    if not olds or not rooms:
        return {}
    out: dict[str, dict[str, dict]] = {}
    # von den Räumen aus (meist wenige Tage) je Zeitpunkt nachschlagen – CROSS JOIN legt die Reihenfolge fest
    for r in store.query(
            f"SELECT o.room AS old, n.room AS new, COUNT(*) AS n, COUNT(DISTINCT {_day_sql('n.ts')}) AS d "
            f"FROM room_samples n CROSS JOIN room_samples o ON o.ts = n.ts AND o.room IN ({','.join('?' * len(olds))}) "
            f"WHERE n.room IN ({','.join('?' * len(rooms))}) GROUP BY o.room, n.room", (*olds, *rooms)):
        out.setdefault(r["old"], {})[r["new"]] = {"rows": r["n"], "days": r["d"]}
    return out


def tally(store, rooms: list[str]) -> dict:
    """Zählung für die Übersicht: ``key_stats`` und die Überschneidungen der Schlüssel, die keine Raumschlüssel sind."""
    stats = key_stats(store)
    olds = sorted(k for k in stats if k not in set(rooms))
    return {"stats": stats, "overlaps": overlaps(store, olds, rooms)}


class _ReadOnly:
    """Eigene, nur lesende Verbindung zur Datenbank (für einen Hintergrund-Thread)."""

    def __init__(self, path) -> None:
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def query(self, sql: str, params: tuple = ()) -> list:
        return self.db.execute(sql, params).fetchall()


def tally_file(path, rooms: list[str]) -> dict:
    """``tally`` über eine eigene, nur lesende Verbindung – läuft in einem Thread, die Ereignisschleife bleibt frei."""
    ro = _ReadOnly(path)
    try:
        return tally(ro, rooms)
    finally:
        ro.db.close()


def orphans(store, found, states: dict, idx=None, stats: dict | None = None, over: dict | None = None) -> list[dict]:
    """Verwaiste Schlüssel: Messwerte bzw. Raum-/Ventilmodell, aber kein aktueller Raumschlüssel – auch ein heutiger
    Mitläufer (``member_of``: sein Raum). Je Eintrag {key, name, days, rows, first_ts, last_ts, still_exists, source,
    area, models, member_of, overlap}; ``overlap`` je Raum aus ``overlaps``. Meiste Tage zuerst."""
    stats = key_stats(store) if stats is None else stats
    keys = {r.climate for r in found.rooms}
    member = {c: r.climate for r in found.rooms for c in r.climates[1:]}
    models: dict[str, list[str]] = {}
    for r in store.query("SELECT key FROM kv WHERE key LIKE 'model.room.%' OR key LIKE 'model.valve.%'"):
        kind, key = r["key"][len("model."):].split(".", 1)
        models.setdefault(key, []).append(kind)
    out = []
    for key in sorted((set(stats) | set(models)) - keys):
        s = stats.get(key) or {"rows": 0, "days": 0, "first_ts": None, "last_ts": None}
        out.append({"key": key, "name": name_of(store, states, key), **s, "still_exists": key in states,
                    "source": source_of(key, states, idx), "area": idx.area_of(key) if idx is not None else None,
                    "models": sorted(models.get(key, [])), "member_of": member.get(key),
                    "overlap": (over or {}).get(key, {})})
    return sorted(out, key=lambda o: (-o["days"], -o["rows"], o["key"]))


# ------------------------------------------------------------------ Vorschläge
def _room_labels(room, idx, states: dict) -> tuple[set[str], set[str]]:
    """(Namen, Bereiche) eines aktuellen Raums in Vergleichsform: Anzeigename, Raum der Einstellungen, Namen seiner
    Geräte in Home Assistant; Bereich des Schlüssels und jedes Geräts."""
    names = {room.name, room.group}
    areas = {room.area}
    for dev in room.devices:
        c = dev["climate"]
        names.add(short_name(_friendly(states, c) or ""))
        if idx is not None:
            d = idx.device_of(c)
            names.add(short_name(d.get("name_by_user") or d.get("name") or ""))
            areas.add(idx.area_of(c))
    return {norm(x) for x in names} - {""}, {norm(x) for x in areas} - {""}


def _orphan_labels(o: dict, states: dict) -> tuple[set[str], set[str]]:
    names = {o["name"], short_name(_friendly(states, o["key"]) or ""), key_name(o["key"])}
    return {norm(x) for x in names} - {""}, {norm(o.get("area"))} - {""}


def _score(o: dict, ol: tuple, rl: tuple, idx) -> tuple[int, list[str]]:
    """Wie sicher gehört der alte Schlüssel zum Raum: 2 Name (gleich bzw. wie der Bereich), +1 gleicher Bereich."""
    o_names, o_areas = ol
    r_names, r_areas = rl
    score, why = 0, []
    if o_names & r_names:
        score, why = 2, ["gleicher Name"]
    elif o_names & r_areas:
        tado = (idx.entity.get(o["key"], {}) if idx is not None else {}).get("platform") == "tado"
        score, why = 2, ["Tado-Zone heißt wie der Bereich" if tado else "Name wie der Bereich"]
    if o_areas & r_areas:
        score, why = score + 1, why + ["gleicher Bereich"]
    elif o_areas & r_names:
        score, why = score + 1, why + ["Bereich wie der Raumname"]
    return score, why


MEMBER_SCORE = 4  # heutiger Mitläufer des Raums: sicherer als jeder Name


def _scores(cands: list[dict], found, idx, states: dict) -> dict[tuple[str, str], tuple[int, list[str]]]:
    """Treffer je (Raum, alter Schlüssel): Name und Bereich (``_score``); ein heutiger Mitläufer nur zu seinem Raum."""
    o_lab = {o["key"]: _orphan_labels(o, states) for o in cands}
    scores: dict[tuple[str, str], tuple[int, list[str]]] = {}
    for room in found.rooms:
        rl = _room_labels(room, idx, states)
        for o in cands:
            if o.get("member_of"):
                if o["member_of"] == room.climate:
                    scores[(room.climate, o["key"])] = (MEMBER_SCORE, [f"Mitläufer im Raum {room.name}"])
                continue
            sc = _score(o, o_lab[o["key"]], rl, idx)
            if sc[0] > 0:
                scores[(room.climate, o["key"])] = sc
    return scores


def suggest(orphan_list: list[dict], found, idx, states: dict, stats: dict) -> list[dict]:
    """Je Raum höchstens ein Vorschlag {to, to_name, from, from_name, source, days, new_days, reason, overlap}: das Paar
    muss für den Raum und für den alten Schlüssel der eindeutig beste Treffer sein, und der alte Schlüssel deutlich mehr
    Tage haben (``MORE_FACTOR``, ``MORE_DAYS``). Kein sicherer Treffer: kein Vorschlag."""
    cands = [o for o in orphan_list if o["rows"] > 0]
    scores = _scores(cands, found, idx, states)

    def unique_best(pair) -> bool:
        s = scores[pair][0]
        rivals = [v[0] for p, v in scores.items() if p != pair and (p[0] == pair[0] or p[1] == pair[1])]
        return all(r < s for r in rivals)

    by_key, names = {o["key"]: o for o in cands}, {r.climate: r.name for r in found.rooms}
    out = []
    for (to, frm), (_, why) in scores.items():
        new_days, o = (stats.get(to) or {}).get("days", 0), by_key[frm]
        if not unique_best((to, frm)) or o["days"] < max(new_days * MORE_FACTOR, new_days + MORE_DAYS):
            continue
        out.append({"to": to, "to_name": names[to], "from": frm, "from_name": o["name"], "source": o["source"],
                    "days": o["days"], "new_days": new_days, "reason": " · ".join(why),
                    "overlap": (o.get("overlap") or {}).get(to) or {"rows": 0, "days": 0}})
    order = {r.climate: i for i, r in enumerate(found.rooms)}
    return sorted(out, key=lambda s: order[s["to"]])


def overview(store, found, states: dict, idx=None, stats: dict | None = None, over: dict | None = None) -> dict:
    """Für ``GET api/rooms/adopt``: verwaiste Schlüssel (mit Überschneidung je Raum), Vorschläge, aktuelle Räume mit
    ihren Tagen, letzte Übernahmen. ``stats``/``over`` aus ``tally`` (sonst hier gezählt)."""
    rooms = [r.climate for r in found.rooms]
    stats = key_stats(store) if stats is None else stats
    over = overlaps(store, sorted(k for k in stats if k not in set(rooms)), rooms) if over is None else over
    orph = orphans(store, found, states, idx, stats, over)
    rooms = [{"key": r.climate, "name": r.name, "days": (stats.get(r.climate) or {}).get("days", 0),
              "rows": (stats.get(r.climate) or {}).get("rows", 0)} for r in found.rooms]
    return {"orphans": orph, "suggestions": suggest(orph, found, idx, states, stats), "rooms": rooms,
            "log": (store.get(KV_LOG) or [])[-5:]}


# ------------------------------------------------------------------ Übernehmen
def check(store, found, old: str, new: str) -> str | None:
    """Grund, warum ``old`` nicht in ``new`` übernommen werden kann – None, wenn es geht. Ein heutiger Mitläufer geht
    nur in seinen eigenen Raum."""
    if new not in {r.climate for r in found.rooms}:
        return f"„{new or '–'}“ ist kein aktueller Raum – übernommen wird nur in einen Raum der Einstellungen."
    if not old or old in {r.climate for r in found.rooms}:
        return f"„{old or '–'}“ gehört zu einem aktuellen Raum – übernommen werden nur gespeicherte Daten ohne Raum."
    home = found.room_of(old)
    if home is not None and home.climate != new:
        return (f"„{old}“ gehört als Mitläufer zum Raum {home.name} – seine Messwerte lassen sich nur dorthin "
                "übernehmen.")
    if not store.query("SELECT 1 FROM room_samples WHERE room = ? LIMIT 1", (old,)):
        return f"Zu „{old}“ sind keine Messwerte gespeichert – nichts zu übernehmen."
    return None


def _put(db, key: str, value) -> None:
    """kv schreiben ohne Commit (``Store.put`` committet – mitten in der Übernahme nicht)."""
    db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value)))


def _merge_overlap(db, p: dict, binary: bool) -> tuple[int, int]:
    """Zeitpunkte, an denen der Raum selbst einen Messpunkt hat: seine Werte bleiben, was ihm fehlt, kommt aus der alten
    Zeile; eine gemessene Ventilöffnung der alten Zeile geht vor einer aus ``hvac_action`` abgeleiteten (``binary``:
    der Raum hat keinen Ventilsensor; sonst jede 0 bzw. 100 % gegen eine Öffnung dazwischen) bzw. als Ersatzwert
    markierten. Vor ``cut`` (0.44, Thermostatwechsel: vor dem Wechsel) gewinnt dagegen die alte Zeile – sie ist der
    eigene Messwert des Raums, die neue dort nur aus der HA-Historie des neuen Thermostats; die neue füllt nur, was der
    alten fehlt. Gibt (Zeitpunkte, ergänzte) zurück."""
    cut = int(p.get("cut") or 0)
    rows = db.execute(
        "SELECT n.ts AS ts, n.temp AS t, n.target AS g, n.valve AS v, n.humidity AS h, o.temp AS ot, "
        "o.target AS og, o.valve AS ov, o.humidity AS oh, "
        "EXISTS(SELECT 1 FROM valve_subst s WHERE s.ts = n.ts AND s.room = :new) AS ns, "
        "EXISTS(SELECT 1 FROM valve_subst s WHERE s.ts = n.ts AND s.room = :old) AS os "
        "FROM room_samples n JOIN room_samples o ON o.ts = n.ts AND o.room = :old WHERE n.room = :new", p).fetchall()
    upd, mark, unmark = [], [], []
    for r in rows:
        if r["ts"] < cut:
            upd, mark, unmark = _old_wins(r, p["new"], upd, mark, unmark)
            continue
        valve, subst = r["v"], r["ns"]
        measured = r["ov"] is not None and not r["os"]  # alte Zeile: Ventilöffnung gemessen, kein Ersatzwert
        if not p.get("valve", True):
            pass  # anderes Thermostat (0.44): die alte Ventilöffnung ergänzt das neue Ventil nicht
        elif r["ov"] is not None and valve is None:
            valve, subst = r["ov"], r["os"]
        elif measured and (subst or binary or (valve in OPEN_SHUT and r["ov"] not in OPEN_SHUT)):
            valve, subst = r["ov"], False  # 0/100 % aus hvac_action gegen gemessene Öffnung
        new = (r["t"] if r["t"] is not None else r["ot"], r["g"] if r["g"] is not None else r["og"], valve,
               r["h"] if r["h"] is not None else r["oh"])
        if new != (r["t"], r["g"], r["v"], r["h"]):
            upd.append((*new, r["ts"], p["new"]))
        if subst != r["ns"]:
            (mark if subst else unmark).append((r["ts"], p["new"]))
    db.executemany("UPDATE room_samples SET temp = ?, target = ?, valve = ?, humidity = ? WHERE ts = ? AND room = ?",
                   upd)
    db.executemany("INSERT OR IGNORE INTO valve_subst VALUES(?, ?)", mark)
    db.executemany("DELETE FROM valve_subst WHERE ts = ? AND room = ?", unmark)
    return len(rows), len(upd)


def _old_wins(r, new_key: str, upd: list, mark: list, unmark: list) -> tuple[list, list, list]:
    """Ein Zeitpunkt vor dem Wechsel: die alte (eigene) Zeile gilt, die neue füllt nur ihre leeren Spalten."""
    def pick(o, n):
        return o if o is not None else n
    valve, subst = (r["ov"], r["os"]) if r["ov"] is not None else (r["v"], r["ns"])
    new = (pick(r["ot"], r["t"]), pick(r["og"], r["g"]), valve, pick(r["oh"], r["h"]))
    if new != (r["t"], r["g"], r["v"], r["h"]):
        upd.append((*new, r["ts"], new_key))
    if bool(subst) != bool(r["ns"]):
        (mark if subst else unmark).append((r["ts"], new_key))
    return upd, mark, unmark


def _move_rows(db, p: dict, binary: bool = False) -> dict:
    """Messwerte und was an ihren Zeitpunkten hängt: Zeitpunkte ohne eigenen Messpunkt des Raums wandern, an den
    übrigen ergänzt die alte Zeile den Raum (``_merge_overlap``) und fällt dann weg."""
    own = "SELECT ts FROM room_samples WHERE room = :new"
    info = db.execute(f"SELECT COUNT(DISTINCT {_day_sql()}) AS d, MIN(ts) AS a, MAX(ts) AS b FROM room_samples "
                      f"WHERE room = :old AND ts NOT IN ({own})", p).fetchone()
    _, filled = _merge_overlap(db, p, binary)
    db.execute(f"UPDATE OR IGNORE valve_subst SET room = :new WHERE room = :old AND ts NOT IN ({own})", p)
    db.execute("DELETE FROM valve_subst WHERE room = :old", p)
    if p.get("valve", True):  # anderes Thermostat (0.44): das Ventil-Protokoll bleibt beim alten Schlüssel
        db.execute(f"UPDATE valve_dry_log SET room = :new WHERE room = :old AND ts NOT IN ({own})", p)
        db.execute("DELETE FROM valve_dry_log WHERE room = :old", p)
    moved = db.execute("UPDATE OR IGNORE room_samples SET room = :new WHERE room = :old", p).rowcount
    skipped = db.execute("DELETE FROM room_samples WHERE room = :old", p).rowcount
    db.execute("UPDATE OR IGNORE room_stats SET room = :new WHERE room = :old", p)  # Stundenstatistik (0.45)
    db.execute("DELETE FROM room_stats WHERE room = :old", p)
    db.execute("UPDATE OR IGNORE room_events SET room = :new WHERE room = :old", p)
    db.execute("DELETE FROM room_events WHERE room = :old", p)
    db.execute("UPDATE snapshots SET key = :new WHERE kind = 'room' AND key = :old AND ts NOT IN "
               "(SELECT ts FROM snapshots WHERE kind = 'room' AND key = :new)", p)
    db.execute("DELETE FROM snapshots WHERE kind = 'room' AND key = :old", p)
    return {"rows": moved, "skipped": skipped, "filled": filled, "from_days": info["d"] if moved else 0,
            "first_ts": info["a"], "last_ts": info["b"]}


def _rewrite_kv(store, old: str, new: str) -> None:
    """Modelle beider Schlüssel verwerfen (sie lernen neu), Raumschlüssel in Hydraulik und Empfehlungen umschreiben."""
    db = store.db
    for kind in ("room", "valve"):
        for key in (old, new):
            db.execute("DELETE FROM kv WHERE key = ?", (f"model.{kind}.{key}",))
    ext = store.get("model.room_ext") or {}
    if old in ext or new in ext:  # laufendes Fremdwärme-Ereignis: erkennt das Nachlernen neu
        _put(db, "model.room_ext", {k: v for k, v in ext.items() if k not in (old, new)})
    changes = store.get(PRESET_CHANGES_KEY)
    if isinstance(changes, list) and any(isinstance(c, dict) and c.get("room") == old for c in changes):
        _put(db, PRESET_CHANGES_KEY, [{**c, "room": new} if isinstance(c, dict) and c.get("room") == old else c
                                      for c in changes])
    advice = store.get(ADVICE_KEY)
    if isinstance(advice, dict) and any(k.startswith(old + "|") for k in advice):
        moved = {new + k[len(old):]: v for k, v in advice.items() if k.startswith(old + "|")}
        rest = {k: v for k, v in advice.items() if not k.startswith(old + "|")}
        _put(db, ADVICE_KEY, {**moved, **rest})  # Einträge des Raums gehen vor


def adopt(store, old: str, new: str, now_ts: int | None = None, binary: bool = False, valve: bool = True,
          cut: int | None = None, then=None) -> dict:
    """Messwerte von ``old`` dem Raum ``new`` zuordnen – alles in einer Transaktion (bei einem Fehler bleibt alles,
    wie es war). ``binary``: der Raum hat keinen Ventilsensor (Ventil nur aus ``hvac_action``). ``valve=False``
    (0.44, anderes Thermostat): die alte Ventilöffnung ergänzt keinen Messpunkt des Raums und das Ventil-Protokoll
    bleibt beim alten Schlüssel (``room_switch`` lässt das Ventil ab dem Wechsel neu lernen). ``cut`` (0.44): vor
    diesem Zeitpunkt gewinnt an gleichen Zeitpunkten die alte Zeile. ``then(db, res)``: weitere Schreibvorgänge in
    derselben Transaktion (``room_switch``: Wechsel schließen, „Ventil lernt ab“). Gibt {rows, skipped
    (Zeitpunkte, die der Raum schon hatte), filled (davon aus der alten Zeile ergänzt), from_days, first_ts, last_ts}
    zurück. Schreibt nichts an Home Assistant."""
    if old == new:
        raise AdoptError("Quelle und Ziel sind derselbe Schlüssel.")
    now_ts = int(time.time()) if now_ts is None else int(now_ts)
    db = store.db
    db.commit()  # Offenes des Takts abschließen – die Übernahme ist eine eigene Transaktion
    with db:     # alles oder nichts: Commit am Ende, Rollback bei einem Fehler
        res = _move_rows(db, {"old": old, "new": new, "valve": valve, "cut": cut}, binary)
        _rewrite_kv(store, old, new)
        entries = store.get(KV_LOG) or []
        entry = {"ts": now_ts, "from": old, "to": new, "rows": res["rows"], **({} if valve else {"valve": False})}
        _put(db, KV_LOG, [*entries, entry][-LOG_MAX:])
        if then is not None:
            then(db, res)
    return res


def _start(eng, old: str, new: str) -> dict:
    """Prüfen und übernehmen (die Transaktion) – ``AdoptError`` mit Grund, wenn es nicht geht."""
    why = check(eng.store, eng.found, old, new)
    if why:
        raise AdoptError(why)
    from_name, room = name_of(eng.store, eng.states, old), eng.found.room_of(new)
    res = adopt(eng.store, old, new, binary=valve_learn.binary_device(room))
    return {**res, "from": old, "to": new, "from_name": from_name, "to_name": room.name}


def _finish(eng, res: dict) -> dict:
    eng._advice = None  # Empfehlungen mit dem neuen Modell rechnen
    eng.save()
    log.info("Gelerntes übernommen: %s → %s (%d Messpunkte, %d Tage; %d Zeitpunkte hatte der Raum schon, %d davon aus "
             "der alten Zeile ergänzt) – Raum- und Ventilmodell lernen neu", res["from"], res["to"], res["rows"],
             res["from_days"], res["skipped"], res["filled"])
    return res


def run(eng, old: str, new: str) -> dict:
    """Übernahme für die laufende Engine: prüfen, übernehmen, dann Raum- und Ventilmodell des Raums aus den
    Messwerten neu lernen und speichern. Gibt das Ergebnis mit den Namen zurück; ``AdoptError`` mit Grund, wenn es
    nicht geht."""
    res = _start(eng, old, new)
    eng.relearn_adopted(new)
    return _finish(eng, res)


async def run_async(eng, old: str, new: str) -> dict:
    """Wie ``run``, das Neulernen des Raums gibt aber nach jedem Tag die Ereignisschleife frei (``main.App.adopt_room``
    – bei Jahren an Daten bleiben Oberfläche und HA-Verbindung bedienbar)."""
    res = _start(eng, old, new)
    await eng.relearn_adopted_async(new)
    return _finish(eng, res)
