"""Aufbewahrung (seit 0.44): eigene Rohdaten so lange aufheben, wie in den Einstellungen steht (``keep_days``).

Einmal am Tag (``daily``, aus dem Takt) fallen Rohdaten weg, die älter als ``keep_days`` sind (Standard 720 Tage,
erlaubt 365–3650): Messpunkte, Raum- und Personenwerte, Brennerereignisse, Ventil-Ersatzwerte, Fremdwärme-Ereignisse,
Stundenwerte und Raumwerte aus der Stundenstatistik (0.45, ``room_stats``). Die kleinen Tabellen (Tageszähler
``daily``, Modelle und Einstellungen in ``kv``) bleiben. Kein VACUUM (die Datei wird nicht umgebaut; frei gewordener Platz wird wiederverwendet). Protokoll in kv ``keep.log``.

Die Grenze zählt nicht von der Wanduhr, sondern vom neuesten eigenen Messpunkt vor dem Takt – und dieser Bezug rückt
je Prüfung höchstens ``MAX_STEP_DAYS`` weiter (kv ``keep.ref``). Eine falsch vorgehende Uhr löscht so nicht auf einen
Schlag alle Rohdaten.
Home Assistant ist dafür nicht nötig – HeatPilots eigene Datenbank ist die Wahrheit.
"""

from __future__ import annotations

import logging
import time

from .values import local

log = logging.getLogger(__name__)

DEFAULT_DAYS, MIN_DAYS, MAX_DAYS = 720, 365, 3650
LAST_KEY = "keep.last_day"   # Tag (Ortszeit, ISO) der letzten Prüfung
LOG_KEY = "keep.log"         # [{ts, days, before, deleted: {Tabelle: Zeilen}}]
REF_KEY = "keep.ref"         # Bezugszeit der letzten Prüfung (neuester Messpunkt)
LOG_MAX = 30
MAX_STEP_DAYS = 2            # so weit rückt der Bezug je Prüfung höchstens vor
FRESH_S = 3600               # Messpunkte jünger als das (der laufende Takt) zählen nicht als Bezug
TABLES = (("samples", "ts"), ("room_samples", "ts"), ("person_samples", "ts"), ("burner_events", "ts"),
          ("valve_subst", "ts"), ("room_events", "start_ts"), ("hourly", "ts"), ("room_stats", "ts"))


def days_of(value) -> int:
    """Gültige Aufbewahrung in Tagen – Unlesbares gilt als Standard, alles in den erlaubten Bereich."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return min(max(days, MIN_DAYS), MAX_DAYS)


def reference(store, now_ts: int) -> int:
    """Bezugszeit der Aufbewahrung: neuester Messpunkt vor dem laufenden Takt (ohne Messpunkte die Uhr), höchstens
    ``MAX_STEP_DAYS`` nach dem letzten Bezug; gemerkt in kv ``keep.ref``."""
    newest = store.query("SELECT MAX(ts) AS t FROM samples WHERE ts < ?", (now_ts - FRESH_S,))[0]["t"]
    ref = min(int(newest), now_ts) if newest is not None else now_ts
    last = store.get(REF_KEY)
    if isinstance(last, (int, float)) and ref > last + MAX_STEP_DAYS * 86400:
        log.warning("Aufbewahrung: Uhr bzw. Messpunkte springen %d Tage vor – die Grenze rückt nur %d Tage weiter",
                    (ref - int(last)) // 86400, MAX_STEP_DAYS)
        ref = int(last) + MAX_STEP_DAYS * 86400
    if not isinstance(last, (int, float)) or ref > last:
        store.put(REF_KEY, ref)
    return ref


def prune(store, keep_days, now_ts: int | None = None) -> dict[str, int]:
    """Rohdaten älter als ``keep_days`` löschen (eine Transaktion), protokollieren. Gibt je Tabelle die Zahl der
    gelöschten Zeilen zurück."""
    now_ts = int(now_ts or time.time())
    days = days_of(keep_days)
    before = reference(store, now_ts) - days * 86400
    with store.db:
        deleted = {table: store.db.execute(f"DELETE FROM {table} WHERE {col} < ?", (before,)).rowcount
                   for table, col in TABLES}
    if any(deleted.values()):
        entries = store.get(LOG_KEY) or []
        store.put(LOG_KEY, [*entries, {"ts": now_ts, "days": days, "before": before,
                                       "deleted": {k: v for k, v in deleted.items() if v}}][-LOG_MAX:])
        log.info("Aufbewahrung %d Tage: ältere Rohdaten gelöscht (%s)", days,
                 ", ".join(f"{k} {v}" for k, v in deleted.items() if v))
    return deleted


def daily(store, keep_days, now_ts: int | None = None) -> dict[str, int] | None:
    """Höchstens einmal je Tag ``prune`` (gemerkt in kv ``keep.last_day``). None, wenn heute schon geprüft."""
    now_ts = int(now_ts or time.time())
    day = local(now_ts).date().isoformat()
    if getattr(store, "_keep_day", None) == day:
        return None
    store._keep_day = day
    if store.get(LAST_KEY) == day:
        return None
    deleted = prune(store, keep_days, now_ts)
    store.put(LAST_KEY, day)
    return deleted
