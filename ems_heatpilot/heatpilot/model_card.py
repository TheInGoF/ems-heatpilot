"""Karte „Modelle“ (seit 0.42.1): wie sicher die Modelle sind – immer sichtbar, auch während des Lernens.

``collect`` fasst für die Übersicht zusammen (``summary["models"]``, ``GET api/models``):

* **Haus** – plausibel oder lernt (mit Gründen), Wärmeverlust ± Unsicherheit, Vorhersagefehler, aus wie vielen
  Stunden und Tageswerten es wirklich gelernt hat (Lernschritte – nur Heizperiode, ohne Warmwasser und Fremdwärme) von
  wie vielen Stunden mit allen Werten seit wann (ältester Tag mit Innentemperatur), Stunden mit Gas und Außentemperatur,
  aber ohne Innentemperatur als Warnung – mit ``restore`` = Knopf „Hausdaten wiederherstellen“ anbieten
  (``house_data``);
* **Räume** – n plausibel von m, je Raum Zustand und erster Grund;
* **Ventile** – belastbar n von m, je Raum Zustand (lernt aus der Historie nach, lernt, belastbar);
* **Warmwasser** – plausibel oder lernt;
* das letzte Wiederherstellen bzw. Neulernen (``house_restore``, Protokoll ``house.restored``) und die letzte Übernahme (``room_adopt``).

Rechnet nur aus Modellen und einer Zählung der Stundenwerte (gecacht ``COVERAGE_TTL_S``); lernt und schreibt nichts.
"""

from __future__ import annotations

import math
import time

from . import guards, house_data, room_adopt, valve_learn
from .models import ROOM_STEP_H
from .values import local

COVERAGE_TTL_S = 300


def _fmt(v: float | None, digits: int = 0) -> str:
    if v is None:
        return "–"
    return f"{v:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def heat_loss_sd(house) -> float | None:
    """Unsicherheit des Wärmeverlusts (W/K, eine Standardabweichung) aus dem Lernkern: üblicher Vorhersagefehler mal
    Unsicherheit der Parameter (wie ``guards.check_room`` bei der Zeitkonstante) – aus den Tageswerten, wenn sie
    gelten, sonst aus den Stunden."""
    r, n = (house.daily, len(house.daily.theta) - 1) if house.daily_valid else (house.rls, 1)
    if r.err_sq_ewma is None or r.updates == 0:
        return None
    var = sum(r.P[i][j] for i in range(n) for j in range(n)) * r.err_sq_ewma
    return round(math.sqrt(var) * 1000) if var > 0 and math.isfinite(var) else None


def coverage(eng) -> dict:
    """Datenlage des Hauses, höchstens alle ``COVERAGE_TTL_S`` Sekunden neu gezählt (je Engine)."""
    cached = eng.__dict__.get("_coverage")
    if cached is None or time.time() - cached[0] > COVERAGE_TTL_S:
        cached = (time.time(), house_data.coverage(eng.store))
        eng._coverage = cached
    return cached[1]


def house_part(eng) -> dict:
    h, cov = eng.house, coverage(eng)
    chk = guards.check_house(h)
    daily = h.daily_valid
    r = h.daily if daily else h.rls
    sd, loss = heat_loss_sd(h), round(h.h_kw_per_k * 1000)
    err = None if r.rmse is None else round(r.rmse, 2)
    state = "plausibel" if chk["ok"] else "lernt"
    learned = house_data.learned_text(cov, h.rls.updates, h.daily.updates)  # gelernt ≠ nur vorhanden
    text = (f"{state} · Wärmeverlust {_fmt(loss)}{'' if sd is None else ' ± ' + _fmt(sd)} W/K · Vorhersagefehler "
            f"{'–' if err is None else '±' + _fmt(err, 2)} kW je {'Tag' if daily else 'Stunde'} · {learned}")
    return {"state": state, "ok": chk["ok"], "reasons": chk["reasons"], "heat_loss_w_per_k": loss,
            "heat_loss_sd_w_per_k": sd, "error_kw": err, "error_per": "tag" if daily else "stunde",
            "source": "tage" if daily else "stunden", "hours_learned": h.rls.updates, "days_learned": h.daily.updates,
            "data": cov, "learned_text": learned, "warning": house_data.warning(cov),
            "restore": house_data.missing_in(cov), "text": text}


def rooms_part(eng) -> dict:
    items = []
    for r in eng.found.rooms:
        m = eng.rooms.get(r.climate)
        if m is None:
            continue
        c = guards.check_room(m)
        items.append({"climate": r.climate, "name": r.name, "ok": c["ok"], "state": "plausibel" if c["ok"] else "lernt",
                      "reason": c["reasons"][0] if c["reasons"] else None, "updates": m.rls.updates,
                      "hours": round(m.rls.updates * ROOM_STEP_H), "floor": m.floor})
    ok = sum(1 for i in items if i["ok"])
    return {"ok": ok, "total": len(items), "items": items, "text": f"{ok} von {len(items)} plausibel"}


def valves_part(eng, now_ts: int) -> dict:
    day = local(now_ts).date().isoformat()
    items = []
    for r in eng.found.rooms:
        model = eng.valves.get(r.climate)
        if model is None:  # Heizkreis von EMS-ESP: kein Ventil
            continue
        catching = valve_learn.behind(model, eng._last_room_step)
        ok, reasons = model.usable(day, catching, valve_learn.group_of(r))
        state = "belastbar" if ok else "lernt nach" if catching else "lernt"  # nach: aus der gespeicherten Historie
        items.append({"climate": r.climate, "name": r.name, "reliable": ok, "state": state, "catching_up": catching,
                      "reason": reasons[0] if reasons else None})
    n = sum(1 for i in items if i["reliable"])
    return {"reliable": n, "total": len(items), "catching_up": sum(1 for i in items if i["catching_up"]),
            "items": items, "text": f"{n} von {len(items)} belastbar"}


def dhw_part(eng) -> dict:
    c = guards.check_dhw(eng.dhw)
    state = "plausibel" if c["ok"] else "lernt"
    return {"ok": c["ok"], "state": state, "reasons": c["reasons"], "charges": eng.dhw.charges,
            "text": state if c["ok"] else f"lernt – {c['reasons'][0]}"}


def collect(eng, now_ts: int | None = None) -> dict:
    """Alles für die Karte „Modelle“ (siehe Moduldocstring)."""
    now_ts = int(time.time()) if now_ts is None else int(now_ts)
    adopted = eng.store.get(room_adopt.KV_LOG) or []
    return {"house": house_part(eng), "rooms": rooms_part(eng), "valves": valves_part(eng, now_ts),
            "dhw": dhw_part(eng), "last_restore": house_data.last_restore(eng.store),
            "last_adopt": adopted[-1] if adopted else None}
