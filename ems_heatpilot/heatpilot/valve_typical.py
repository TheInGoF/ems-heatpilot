"""Typische Ventilöffnung beim Heizen je Raum (seit 0.45.1): Ersatz, wenn das Thermostat nur noch an/aus meldet.

Das Raummodell rechnet die Wärmezufuhr als Ventil × (Vorlauf − Raum). Kannte ein Raum echte Ventilwerte in % (etwa die
Heizleistung der Tado-Cloud) und meldet sein Thermostat danach nur noch ``hvac_action`` (HomeKit: heizt / heizt nicht),
wäre das Ventil beim Heizen 100 % – das Raummodell sähe mehr Wärme, als es gelernt hat. ``learn`` merkt sich deshalb je
Raum die typische Öffnung beim Heizen (kv ``rooms.valve_typical``): das Mittel der echten Ventilwerte über 5 % aus den
eigenen Messpunkten (``room_samples`` ohne Ersatzwerte aus ``valve_subst``) – das Mittel, weil die Wärmezufuhr linear im
Ventil ist. Es zählt erst ab ``MIN_SAMPLES`` Werten und nur, wenn das Ventil wirklich Zwischenwerte kennt
(mindestens ``MIN_SHARE`` der Werte zwischen 5 und 95 % – sonst meldet es nur an/aus). Reichen die eigenen Messpunkte
nicht, zählen die Stunden der Stundenstatistik mit Heizleistung (``room_stats``, ab ``MIN_HOURS``; Stundenmittel, eher
etwas niedriger). Reichen die Daten bei einem neuen Lauf nicht mehr (Aufbewahrung, Quelle gelöscht), bleibt der gemerkte
Wert – was HeatPilot einmal gelernt hat, bleibt.

``table`` sagt dem Messpunkt (``engine_sample.room_reading``), welche Räume %-Werte kannten: {Schlüssel: typische
Öffnung oder None}. Dazu zählt auch ein Raum, dessen Ventilmodell stetig gelernt hat (``ValveModel``), wenn die typische
Öffnung noch unbekannt ist (None: beim Heizen 100 %). Meldet ein solcher Raum gerade keinen echten Ventilwert, nimmt er
beim Heizen die typische Öffnung und markiert den Wert als Ersatzwert – das %-Ventilmodell lernt ihn nicht. Räume ohne
je %-Werte bleiben, wie sie waren (0/100 % aus ``hvac_action``).

Rechnet nur aus der eigenen Datenbank (``refresh``: beim Aufbau und täglich im Takt, höchstens etwa einmal am Tag;
``learn`` nach „Hausdaten wiederherstellen“); liest und schreibt nichts an Home Assistant.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

KV = "rooms.valve_typical"   # {Schlüssel: {pct, n, src, ts, checked}}
OPEN_PCT = 5.0               # ab hier ist das Ventil offen (wie ``valve_model.OPEN_PCT``)
MIN_SAMPLES = 144            # so viele eigene Messpunkte mit offenem Ventil (≈ 12 h Heizen bei 5 min) …
MIN_HOURS = 24               # … sonst so viele Stunden der Stundenstatistik mit Heizleistung
MIN_SHARE = 0.1              # so viel davon zwischen 5 und 95 % – sonst kennt das Ventil nur an/aus
REFRESH_S = 20 * 3600        # beim Aufbau bzw. täglich im Takt höchstens so oft neu rechnen


def get(store) -> dict[str, dict]:
    v = store.get(KV)
    return v if isinstance(v, dict) else {}


def pct(store, key: str) -> float | None:
    """Gemerkte typische Öffnung eines Raums in % (None: unbekannt)."""
    return (get(store).get(key) or {}).get("pct")


def _counts(store, table: str, keys: list[str], own: bool) -> dict[str, tuple]:
    """Je Raum (Zahl, Mittel, Zwischenwerte) der offenen Ventilwerte; ``own``: ohne Ersatzwerte (``valve_subst``)."""
    marks = ",".join("?" * len(keys))
    skip = (" AND NOT EXISTS (SELECT 1 FROM valve_subst v WHERE v.ts = s.ts AND v.room = s.room)" if own else "")
    rows = store.query(f"SELECT room, COUNT(*) AS n, AVG(valve) AS m, SUM(valve < ?) AS i FROM {table} s "
                       f"WHERE room IN ({marks}) AND valve > ?{skip} GROUP BY room", (100 - OPEN_PCT, *keys, OPEN_PCT))
    return {r["room"]: (r["n"], r["m"], r["i"] or 0) for r in rows}


def measure(n: int, mean: float | None, inter: int, need: int) -> float | None:
    """Typische Öffnung aus den Zählungen – None bei zu wenig Werten oder nur an/aus."""
    if not n or n < need or mean is None or inter < MIN_SHARE * n:
        return None
    return round(min(max(mean, OPEN_PCT), 100.0), 1)


def learn(store, keys: list[str], now: int | None = None) -> dict[str, dict]:
    """Neu rechnen (siehe Moduldocstring); ein Raum ohne genug Daten behält seinen gemerkten Wert. Gibt kv zurück."""
    ts = int(time.time() if now is None else now)
    old = get(store)
    out = dict(old)
    if keys:
        own, hours = _counts(store, "room_samples", keys, True), _counts(store, "room_stats", keys, False)
        for k in keys:
            n = own.get(k, (0, None, 0))
            v, src = measure(*n, MIN_SAMPLES), "samples"
            if v is None:
                n = hours.get(k, (0, None, 0))
                v, src = measure(*n, MIN_HOURS), "stats"
            if v is not None:
                out[k] = {"pct": v, "n": n[0], "src": src, "ts": ts, "checked": ts}
                if v != (old.get(k) or {}).get("pct"):
                    log.info("Raum %s: typische Ventilöffnung beim Heizen %.1f %% (%d Werte, %s)", k, v, n[0], src)
            else:
                out[k] = {**(old.get(k) or {"pct": None}), "checked": ts}
    store.put(KV, out)
    return out


def refresh(store, keys: list[str], now: int | None = None) -> dict[str, dict]:
    """Beim Aufbau: Räume neu rechnen, die länger als ``REFRESH_S`` nicht geprüft wurden."""
    ts = int(time.time() if now is None else now)
    cur = get(store)
    due = [k for k in keys if ts - int((cur.get(k) or {}).get("checked") or 0) >= REFRESH_S]
    return learn(store, due, ts) if due else cur


def table(store, rooms, valves: dict) -> dict[str, float | None]:
    """Räume, die %-Werte kannten: {Schlüssel: typische Öffnung oder None (unbekannt, beim Heizen 100 %)}."""
    known = get(store)
    out = {}
    for r in rooms:
        p, m = (known.get(r.climate) or {}).get("pct"), valves.get(r.climate)
        if p is not None or (m is not None and not m.binary_dev and m.valve_n > 0 and m.kind == "stetig"):
            out[r.climate] = p
    return out
