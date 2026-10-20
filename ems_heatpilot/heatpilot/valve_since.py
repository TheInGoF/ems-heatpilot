"""Ventil lernt ab (seit 0.44): je Raumschlüssel der Zeitpunkt, ab dem seine Ventilwerte zum heutigen Ventil gehören.

„Ventil neu lernen“ und ein Thermostatwechsel mit „anderes Thermostat“ (``room_switch``) setzen ihn; davor gemessene
Ventilöffnungen bleiben gespeichert (das Raummodell braucht sie als Wärmezufuhr), zählen aber nicht mehr fürs Ventil:
Das Ventilmodell lernt erst ab hier (``valve_learn.learn_chunk``), der Abgleich nimmt ältere Öffnungen nicht
(``advice``), die Nachprüfung des Ventil-Trockenlaufs ältere Einträge nicht (``valve_dry.summary``).

kv ``rooms.valve_since`` {Schlüssel: ts}. Ohne Abhängigkeiten, damit jedes Modul es lesen kann.
"""

from __future__ import annotations

KEY = "rooms.valve_since"


def get(store) -> dict[str, int]:
    value = store.get(KEY) or {}
    return {k: int(v) for k, v in value.items() if isinstance(v, (int, float))} if isinstance(value, dict) else {}


def of(store, key: str) -> int:
    """Ab hier zählen Ventilwerte des Schlüssels (0: alle)."""
    return get(store).get(key, 0)


def put(store, key: str, ts: int, write=None) -> None:
    """``write(kv, Wert)``: anders schreiben als ``store.put`` (ohne Commit, mitten in einer Transaktion)."""
    (write or store.put)(KEY, {**get(store), key: int(ts)})


def move(store, old: str, new: str, write=None) -> None:
    """Werte von ``old`` gehören jetzt ``new`` (Übernahme): seine Grenze geht mit, die spätere gilt."""
    cut = get(store)
    if old in cut:
        cut[new] = max(cut.pop(old), cut.get(new, 0))
        (write or store.put)(KEY, cut)
