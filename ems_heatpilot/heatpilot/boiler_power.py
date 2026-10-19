"""Mindestleistung des Kessels (seit 0.42): woher sie kommt, ein Vorschlag, wenn der Kessel keine meldet.

Die Abnahme-Regel von Weg B (``boiler_flow._load``) braucht die kleinste Leistung des Kessels: kleinste Brennerleistung
(%) × Nennleistung. Die Prozent kommen aus Einstellungen → Haus & Kessel (``boiler_min_power_pct``), sonst von EMS-ESP
(``burnminpower`` – als Entität bzw. direkt gelesen, ``pct``). Manche Kessel melden keine und kennen nur die
„Maximale Brennerleistung“ (``burnmaxpower``), einstellbar in einem Bereich (z. B. 30–100 %). Dessen Untergrenze ist die
kleinste Leistung, auf die man den Brenner stellen kann – ein guter Vorschlag (``suggestion``, Bereich aus der
Kesselsteuerung von EMS-ESP, sonst aus den Grenzen der Entität in Home Assistant).

Der Vorschlag wird nur angezeigt: „übernehmen“ füllt das Feld, gespeichert wird erst mit „Speichern“ – nie automatisch.
Fehlt die Mindestleistung (oder die Nennleistung), ist die Abnahme-Regel aus und steht als Einschränkung in „Bereit für
die Steuerung?“ (``missing`` – derselbe Text überall). Nur lesen, schreibt nichts.
"""

from __future__ import annotations

from .values import num

SOURCE_SET, SOURCE_BOILER = "eingestellt", "laut Kessel"
MAX_KEY, MIN_KEY = "burnmaxpower", "burnminpower"   # Kesselsteuerungen von EMS-ESP (``emsesp.POWER_CONTROLS``)
LOAD_OFF = "Abnahme-Regel aus: Mindestleistung unbekannt"
WHERE = "unter Einstellungen → Haus & Kessel eintragen"


def _pct(value) -> float | None:
    """Prozent, nur plausibel (über 0 bis 100)."""
    v = None if isinstance(value, bool) else num(value)
    return v if v is not None and 0.0 < v <= 100.0 else None


def _control(eng, key: str) -> dict:
    """Kesselsteuerung ``key`` laut EMS-ESP (stündlich gelesen) – leer, solange nicht abgefragt bzw. nicht gefunden."""
    if getattr(eng, "ems_controls_ts", None) is None:
        return {}
    c = (getattr(eng, "ems_controls", None) or {}).get(key) or {}
    return c if c.get("found") else {}


def pct(eng) -> tuple[float | None, str | None]:
    """(kleinste Brennerleistung in %, Herkunft): Einstellung, sonst EMS-ESP ``burnminpower``; sonst (None, None)."""
    own = _pct(eng.cfg.boiler_min_power_pct)
    if own is not None:
        return own, SOURCE_SET
    boiler = _pct(eng._boiler("min_power"))
    if boiler is None:
        boiler = _pct(_control(eng, MIN_KEY).get("value"))
    return (boiler, SOURCE_BOILER) if boiler is not None else (None, None)


def max_range(eng) -> tuple[float, float, str] | None:
    """Bereich der maximalen Brennerleistung (min, max, Herkunft): Kesselsteuerung von EMS-ESP, sonst die Grenzen der
    ``number``-Entität in Home Assistant. None, wenn keiner bekannt ist oder er nicht passt (0 < min < max ≤ 100)."""
    c = _control(eng, MAX_KEY)
    cands = [(c.get("min"), c.get("max"), "EMS-ESP")]
    entity = (getattr(eng.found, "boiler", None) or {}).get("max_power")
    if entity and entity.startswith("number."):
        attrs = (eng.states.get(entity) or {}).get("attributes") or {}
        cands.append((attrs.get("min"), attrs.get("max"), entity))
    for lo, hi, source in cands:
        lo, hi = _pct(lo), _pct(hi)
        if lo is not None and hi is not None and lo < hi:
            return lo, hi, source
    return None


def _de(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def suggestion(eng) -> dict | None:
    """Vorschlag für die kleinste Brennerleistung: Untergrenze des Bereichs der maximalen Brennerleistung – nur, wenn
    weder eingestellt noch vom Kessel gemeldet. None sonst bzw. ohne Bereich."""
    if pct(eng)[0] is not None:
        return None
    r = max_range(eng)
    if r is None:
        return None
    lo, hi, source = r
    return {"pct": lo, "min": lo, "max": hi, "source": source,
            "text": f"aus dem Bereich der maximalen Brennerleistung ({_de(lo)}–{_de(hi)} %)"}


def missing(eng) -> str | None:
    """Warum die Abnahme-Regel aus ist (Mindestleistung oder Nennleistung fehlt) – None, wenn beides da ist."""
    have_pct, have_nom = pct(eng)[0] is not None, bool(eng.nominal_kw())
    if have_pct and have_nom:
        return None
    if not have_pct and not have_nom:
        return f"{LOAD_OFF} – kleinste Brennerleistung (%) und Nennleistung des Kessels {WHERE}"
    if have_pct:
        return f"{LOAD_OFF} – Nennleistung des Kessels {WHERE}"
    sg = suggestion(eng)
    hint = f" (Vorschlag {_de(sg['pct'])} % {sg['text']})" if sg else ""
    return f"{LOAD_OFF} – der Kessel meldet keine kleinste Brennerleistung; sie {WHERE}{hint}"


def info(eng) -> dict:
    """Für Einstellungen → Haus & Kessel (``boiler_power`` in ``api/settings``): wirksamer Wert, Herkunft, Vorschlag."""
    value, source = pct(eng)
    nom = eng.nominal_kw()
    return {"pct": value, "source": source, "nominal_kw": nom, "suggestion": suggestion(eng), "missing": missing(eng),
            "kw": None if value is None or not nom else round(value / 100.0 * nom, 1)}
