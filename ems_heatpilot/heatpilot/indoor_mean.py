"""Innentemperatur des Hauses aus den Räumen, die gerade Werte haben (seit 0.42.1).

Die Stundenwerte speichern als Innentemperatur das Mittel der Räume. Fehlt einer Stunde ein Raum – sein neuer Sensor
hat noch keine Langzeitstatistik, für ihn gibt es keinen alten Sensor, sein Thermostat meldete nichts –, läge das
Mittel der übrigen um deren Abstand zum Hausmittel daneben: Zwei Räume mit ±1,5 K ergäben aus einem allein 1,5 K zu
warm, und das Hausmodell läge um die Hälfte daneben. Deshalb:

* ``fit`` – je Quelle (Langzeitstatistik eines Sensors bzw. Raumwerte eines Schlüssels) ihr üblicher Versatz zum
  gespeicherten Hausmittel aus gemeinsamen Stunden (Median), dazu Streuung und Korrelation. Passt eine Quelle nicht
  (zu wenige gemeinsame Stunden, Streuung bzw. Versatz zu groß, kaum Zusammenhang), nimmt HeatPilot sie nicht – lieber
  eine Stunde ohne Innentemperatur als eine falsche.
* ``mean`` – Mittel einer Stunde über die Quellen mit Wert, jede um ihren Versatz berichtigt.
* ``room_fits`` – dasselbe für die Raumwerte einzelner Schlüssel (``aggregate_hour``: Stunde mit unvollständigen
  Räumen ohne gespeicherte Innentemperatur); ohne jeden Bezug in den Tagen davor (ein Raum fehlt von Anfang an) gilt
  das einfache Mittel wie bisher.

Ohne gespeichertes Hausmittel (erster Start) gilt der Versatz 0 – wie bisher das einfache Mittel der Räume. Nur
Rechnen und Lesen; geschrieben wird hier nichts.
"""

from __future__ import annotations

import math
from statistics import median

MIN_HOURS = 48       # so viele gemeinsame Stunden mit gespeicherter Innentemperatur braucht eine Quelle
MIN_REF_HOURS = 48   # weniger gespeicherte Innentemperatur: kein Bezug – Versatz 0 (erster Start)
MAX_SPREAD_K = 2.0   # Streuung (Standardabweichung) um den Versatz – darüber passt die Quelle nicht
MAX_OFFSET_K = 6.0   # so weit darf ein Raum im Mittel vom Hausmittel abweichen (kühler Flur, warmes Bad)
MIN_CORR = 0.3       # Zusammenhang mit dem gespeicherten Hausmittel – nur geprüft, wenn es genug schwankt
MIN_REF_SD_K = 0.3
ROOM_DAYS = 14       # Raumwerte: Versatz aus so vielen Tagen vor der Stunde
ROOM_MIN_HOURS = 24


def _sd(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _corr(a: list[float], b: list[float]) -> float | None:
    sa, sb = _sd(a), _sd(b)
    if sa < 1e-9 or sb < 1e-9:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) * sa * sb)


def plain(series: dict[int, float]) -> dict:
    """Ohne Bezug (noch keine gespeicherte Innentemperatur): Versatz 0 – das einfache Mittel wie bisher."""
    return {"ok": bool(series), "offset": 0.0, "hours": 0, "spread": None, "corr": None,
            "why": None if series else "keine Werte"}


def fit(series: dict[int, float], ref: dict[int, float], min_hours: int = MIN_HOURS) -> dict:
    """Versatz einer Quelle zum gespeicherten Hausmittel: {ok, offset, hours, spread, corr, why}. ``why`` sagt, warum
    sie nicht passt (für Protokoll und Oberfläche)."""
    common = sorted(series.keys() & ref.keys())
    out = {"ok": False, "offset": None, "hours": len(common), "spread": None, "corr": None, "why": None}
    if len(common) < min_hours:
        out["why"] = (f"nur {len(common)} gemeinsame Stunden mit gespeicherter Innentemperatur (nötig {min_hours}) – "
                      "Versatz zum Hausmittel unbekannt")
        return out
    vals, refs = [series[h] for h in common], [ref[h] for h in common]
    diffs = [v - r for v, r in zip(vals, refs)]
    off = median(diffs)
    spread, corr = _sd(diffs), _corr(vals, refs)
    out.update(offset=round(off, 3), spread=round(spread, 2), corr=None if corr is None else round(corr, 2))
    if abs(off) > MAX_OFFSET_K:
        out["why"] = f"liegt im Mittel {off:+.1f} K neben dem Hausmittel – wohl kein Raumsensor"
    elif spread > MAX_SPREAD_K:
        out["why"] = f"schwankt um {spread:.1f} K gegen das Hausmittel – passt nicht"
    elif _sd(refs) >= MIN_REF_SD_K and (corr is None or corr < MIN_CORR):
        out["why"] = "folgt dem Hausmittel nicht" + ("" if corr is None else f" (Korrelation {corr:.2f})")
    else:
        out["ok"] = True
    return out


def fits(sources: dict[str, dict[int, float]], ref: dict[int, float]) -> dict[str, dict]:
    """``fit`` je Quelle – ohne nennenswerten Bezug (``MIN_REF_HOURS``, erster Start) ``plain``."""
    if len(ref) < MIN_REF_HOURS:
        return {sid: plain(ser) for sid, ser in sources.items()}
    return {sid: fit(ser, ref) for sid, ser in sources.items()}


def mean(values: list[tuple[str, float]], fitted: dict[str, dict]) -> tuple[float | None, list[str]]:
    """Mittel einer Stunde über die passenden Quellen, jede um ihren Versatz berichtigt: (Wert, genutzte Quellen)."""
    used = [(sid, v - fitted[sid]["offset"]) for sid, v in values if (fitted.get(sid) or {}).get("ok")]
    if not used:
        return None, []
    return sum(v for _, v in used) / len(used), [sid for sid, _ in used]


PLAIN_SRC = "rooms-"   # Herkunft: einfaches Mittel unvollständiger Räume (kein Bezug zum Berichtigen)
_REF = f"h.t_in IS NOT NULL AND (h.t_in_src IS NULL OR h.t_in_src != '{PLAIN_SRC}')"


def room_fits(store, keys: list[str], hour: int) -> dict[str, dict] | None:
    """Versatz der Raumwerte je Schlüssel zum gespeicherten Hausmittel aus den ``ROOM_DAYS`` Tagen vor ``hour``. Als
    Bezug zählt jede gespeicherte Innentemperatur außer einem einfachen Mittel unvollständiger Räume (``PLAIN_SRC``).
    Gibt es in dieser Zeit keinen Bezug (fehlt ein Raum von Anfang an, war er noch nicht gewählt): None – dann gilt das
    einfache Mittel wie bisher. Ein Schlüssel ohne genug gemeinsame Stunden passt nicht (lieber keine Innentemperatur
    als eine verschobene)."""
    if not keys:
        return {}
    since = hour - ROOM_DAYS * 86400
    rows = store.query(
        "SELECT k.room AS room, k.hh AS hh, k.t AS t, h.t_in AS ref FROM (SELECT room, ts - ts % 3600 AS hh, "
        "AVG(temp) AS t FROM room_samples WHERE ts >= ? AND ts < ? AND temp IS NOT NULL AND room IN "
        f"({','.join('?' * len(keys))}) GROUP BY room, hh) k LEFT JOIN hourly h ON h.ts = k.hh AND {_REF}",
        (since, hour, *keys))
    series: dict[str, dict[int, float]] = {}
    for r in rows:
        series.setdefault(r["room"], {})[r["hh"]] = r["t"]
    ref = {r["hh"]: r["ref"] for r in rows if r["ref"] is not None}
    if not ref and not store.query(f"SELECT 1 FROM hourly h WHERE h.ts >= ? AND h.ts < ? AND {_REF} LIMIT 1",
                                   (since, hour)):
        return None
    return {k: fit(series.get(k, {}), ref, ROOM_MIN_HOURS) for k in keys}
