"""Datenlage des Hauses (seit 0.42.1): aus welchen Stunden das Hausmodell lernen kann – und welche fehlen.

Das Hausmodell lernt aus Stundenwerten mit Gas, Außen- und Innentemperatur. Fehlt die Innentemperatur (nach einem
Wechsel der Thermostate hat der neue Sensor keine Langzeitstatistik, die alten Raumwerte zählten nicht mehr), lernt es
diese Stunden nicht – still, bis 0.42.1. ``coverage`` zählt nach: Zeitraum, nutzbare Stunden und Tage, ältester Tag mit
Innentemperatur, Stunden mit Gas und Außentemperatur, aber ohne Innentemperatur (mit Zeitraum), Stunden ohne Gas bzw.
ohne Außentemperatur und Lücken ohne Stundenwert. ``warning`` macht daraus den Hinweis (Modelle-Karte, Zertifikat),
der zu „Hausdaten wiederherstellen“ führt (``house_restore``). ``learned_text`` trennt, woraus das Hausmodell wirklich
gelernt hat (seine Lernschritte – nur Heizperiode, ohne Warmwasser und Fremdwärme), von den Stunden mit allen Werten.

Nur lesen: ein Durchlauf über die Stundenwerte (ein Jahr sind gut 8.700 Zeilen).
"""

from __future__ import annotations

import time

from .values import local

KV_RESTORED = "house.restored"   # Protokoll von „Hausdaten wiederherstellen“ und „neu lernen“ (house_restore)
MISSING_MIN_HOURS = 24      # so viele Stunden ohne Innentemperatur (mit Gas und außen) …
MISSING_MIN_SHARE = 0.05    # … und so viel Anteil an den Stunden mit Gas und außen: Hinweis und Knopf
HEAT_MAX_OUT = 15.0         # Heizperiode wie beim Lernen (darüber lernt das Hausmodell nicht)


def _day_sql() -> str:
    return f"(ts + {int(time.localtime().tm_gmtoff)}) / 86400"


def _iso(ts) -> str | None:
    return None if ts is None else local(ts).date().isoformat()


def coverage(store) -> dict:
    """Zählung über alle Stundenwerte (siehe Moduldocstring). Zeitpunkte als ISO-Datum (Ortszeit)."""
    r = store.query(
        "SELECT COUNT(*) AS n, MIN(ts) AS a, MAX(ts) AS b, "
        "SUM(gas_kwh IS NOT NULL AND t_in IS NOT NULL AND t_out IS NOT NULL) AS usable, "
        "SUM(gas_kwh IS NOT NULL AND t_in IS NOT NULL AND t_out IS NOT NULL AND t_out <= ?) AS heat, "
        "SUM(gas_kwh IS NOT NULL AND t_out IS NOT NULL AND t_in IS NULL) AS no_in, "
        "SUM(gas_kwh IS NULL) AS no_gas, SUM(t_out IS NULL) AS no_out FROM hourly", (HEAT_MAX_OUT,))[0]
    good = store.query(f"SELECT MIN(ts) AS a, MAX(ts) AS b, COUNT(DISTINCT {_day_sql()}) AS d FROM hourly "
                       "WHERE gas_kwh IS NOT NULL AND t_in IS NOT NULL AND t_out IS NOT NULL")[0]
    miss = store.query("SELECT MIN(ts) AS a, MAX(ts) AS b FROM hourly WHERE gas_kwh IS NOT NULL "
                       "AND t_out IS NOT NULL AND t_in IS NULL")[0]
    n = r["n"] or 0
    span = 0 if not n else (r["b"] - r["a"]) // 3600 + 1
    return {
        "hours": n, "from": _iso(r["a"]), "to": _iso(r["b"]), "gaps": max(span - n, 0),
        "usable": r["usable"] or 0, "usable_heat": r["heat"] or 0, "usable_days": good["d"] or 0,
        "usable_from": _iso(good["a"]), "usable_to": _iso(good["b"]),
        "no_t_in": r["no_in"] or 0, "no_t_in_from": _iso(miss["a"]), "no_t_in_to": _iso(miss["b"]),
        "no_gas": r["no_gas"] or 0, "no_t_out": r["no_out"] or 0,
    }


def _date(iso: str | None) -> str:
    return "–" if not iso else f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def _n(v: int) -> str:
    """Ganzzahl mit Tausenderpunkt."""
    return f"{int(v):,}".replace(",", ".")


def missing_in(cov: dict) -> bool:
    """Viele Stunden mit Gas und Außentemperatur, aber ohne Innentemperatur: Hausdaten wiederherstellen anbieten."""
    n = cov["no_t_in"]
    return n >= MISSING_MIN_HOURS and n >= MISSING_MIN_SHARE * (n + cov["usable"])


def warning(cov: dict) -> str | None:
    """Hinweis auf fehlende Innentemperatur (für Modelle-Karte und Zertifikat) – None, wenn nichts Nennenswertes fehlt."""
    if not missing_in(cov):
        return None
    return (f"{_n(cov['no_t_in'])} Stunden ({_date(cov['no_t_in_from'])}–{_date(cov['no_t_in_to'])}) haben Gas und "
            "Außentemperatur, aber keine Innentemperatur – das Hausmodell lernt sie nicht. Meist fehlt nach einem "
            "Wechsel der Thermostate die Statistik des alten Sensors: „Hausdaten wiederherstellen“ liest sie mit dem "
            "alten Sensor neu ein.")


def learned_text(cov: dict, hours: int | None = None, days: int | None = None) -> str:
    """Woraus das Hausmodell gelernt hat – getrennt nach Stunden mit allen Werten (Gas, außen, innen) und den davon
    wirklich gelernten (``hours``/``days``: Lernschritte des Modells; gelernt wird nur in der Heizperiode, ohne
    Warmwasser, Fremdwärme und Urlaub, mit beiden Nachbarstunden): „aus 710 Stunden und 30 Tageswerten gelernt – von
    1.440 Stunden mit Gas, Außen- und Innentemperatur seit 21.08.2026“. Ohne Lernschritte nur die Stunden."""
    if not cov["usable"]:
        return "noch keine Stunden mit Gas, Außen- und Innentemperatur"
    have = f"{_n(cov['usable'])} Stunden mit Gas, Außen- und Innentemperatur seit {_date(cov['usable_from'])}"
    if hours is None:
        return have
    if not hours:
        return f"noch nichts gelernt – {have}"
    return f"aus {_n(hours)} Stunden und {_n(days or 0)} Tageswerten gelernt – von {have}"


def last_restore(store) -> dict | None:
    """Letzter Eintrag des Protokolls ``house.restored`` (Wiederherstellen bzw. Neu lernen)."""
    entries = store.get(KV_RESTORED) or []
    return entries[-1] if entries else None


def missing(cov: dict) -> list[dict]:
    """Was in den Stundenwerten fehlt, mit Grund – fürs Zertifikat (statt still weniger zu nehmen)."""
    out = []
    for key, reason in (("no_t_in", "ohne Innentemperatur – das Hausmodell lernt sie nicht"),
                        ("no_gas", "ohne verwertbaren Gaswert (Zähler fehlte, stand still oder sprang)"),
                        ("no_t_out", "ohne Außentemperatur"),
                        ("gaps", "ohne Stundenwert (HeatPilot bzw. Home Assistant lief nicht, keine Statistik)")):
        if cov[key]:
            out.append({"key": key, "hours": cov[key], "reason": reason,
                        **({"from": cov["no_t_in_from"], "to": cov["no_t_in_to"]} if key == "no_t_in" else {})})
    return out


def basis_text(cov: dict, hours: int | None = None, days: int | None = None) -> str:
    """Ein Satz für das Zertifikat: woraus das Hausmodell gelernt hat und welche Stunden fehlen."""
    text = "Hausmodell " + learned_text(cov, hours, days)
    parts = []
    for m in missing(cov):
        span = f" ({_date(m['from'])}–{_date(m['to'])})" if m.get("from") else ""
        parts.append(f"{_n(m['hours'])} Stunden {m['reason'].split(' – ')[0]}{span}")
    return text + (" · nicht berücksichtigt: " + ", ".join(parts) if parts else "")
