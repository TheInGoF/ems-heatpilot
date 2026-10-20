"""Ventilmodell lernen (seit 0.39): Messwerte aufbereiten, Beharrung und Soll-Sprünge finden, in Abschnitten lernen.

Eingang sind die gespeicherten Messpunkte je logischem Raum (``room_samples``: Temperatur, Soll, Ventil – bei
Gruppen das Mittel der Ventile), die Betriebsart des Heizkreises aus ``samples`` (Heizbetrieb an) und die
Fremdwärme-Ereignisse (``room_events`` und das laufende aus ``Engine.room_ext``). Nicht gelernt werden:

* **Fenster offen:** Die Raumtemperatur fällt in 15 min um mindestens 0,5 K – von 15 min davor bis 45 min danach.
* **Fremdwärme** (Kamin, Ofen, Gäste) und **Heizbetrieb aus** (nur für Kennlinie und Gütemaß: ohne Heizwasser
  sagt die Ventilstellung nichts über die Wärme).
* **Ersatzwerte:** Meldet der Ventilsensor eines Raums nichts, nimmt der Messpunkt ``hvac_action`` (0/100 % bzw. seit
  0.45.1 die typische Öffnung, ``valve_typical``) – solche Ventilwerte (Tabelle ``valve_subst``) lernt ein stetiges
  Ventilmodell nicht. Ein Modell, das nur an/aus kennt (``binary_dev``), lernt aus ihnen an/aus (offen = an).
* **Ruht** (``no_pct``, seit 0.45.1): ein %-Modell, dessen Raum nur noch an/aus meldet – es vergisst nichts
  (``decay`` hält an), bis wieder %-Werte kommen.
* **Beharrung** heißt: kein Soll-Wechsel in den letzten 30 min.

Ob ein Ventil nur an/aus kennt, zählt nur aus Messpunkten, die etwas sagen: Heizbetrieb an, kein Fenster, keine
Fremdwärme und das Ventil im Spiel (offen oder Raum nicht über dem Soll) – ein Sommer mit geschlossenem Ventil zählt
nicht.
Das Messrauschen der Raumtemperatur schätzt ``_noise`` aus drei aufeinanderfolgenden Messpunkten in Beharrung.

``learn_chunk`` lernt höchstens einen Tag auf einmal – beim ersten Start (oder nach einem neuen Modellstand) aus der
gespeicherten Historie der letzten ``HISTORY_DAYS`` Tage, danach je abgeschlossenem Raumschritt. Was einen Blick in
die Zukunft braucht (Sprungantwort 90 min, 30-min-Vorhersage), wird ``LAG_S`` später gelernt, jeder Messpunkt genau
einmal. Dazu die Schrittweite je Gerät (``step_of``). Schreibt nur in das Modell, nichts an die Thermostate.
"""

from __future__ import annotations

import time
from itertools import pairwise

from . import valve_model as vm
from . import valve_since
from .values import local

HISTORY_DAYS = 60          # beim ersten Mal so weit zurück lernen
CHUNK_S = 86400            # höchstens so viel auf einmal
RESP_S = 5400              # Sprungantwort so lange verfolgen …
RESP_MIN_S = 1800          # … mindestens so lange ohne neuen Soll-Wechsel, sonst zählt der Sprung nicht
LAG_S = RESP_S             # so viel später lernen (Blick nach vorn)
CTX_S = 3600               # so viel davor zusätzlich lesen (Blick zurück)
MAX_GAP_S = 1200           # größere Lücke zwischen Messpunkten: nicht über sie hinweg rechnen
NOISE_MAX_K = 0.5          # Rausch-Stichprobe: größere Abweichung ist kein Rauschen (Fenster, Sprung)
STEADY_S = 1800            # Beharrung: so lange kein Soll-Wechsel
PRED_S = 1800              # Vorhersage über so viele Sekunden (Gütemaß)
WINDOW_DROP_K, WINDOW_SPAN_S, WINDOW_HOLD_S = 0.5, 900, 2700
DEFAULT_STEP_K = 0.5       # Schrittweite, wenn das Gerät keine meldet
CATCH_UP_S = 3600          # mehr als so weit zurück: „lernt aus der Historie nach“


class Row:
    """Ein Messpunkt eines Raums, aufbereitet."""

    __slots__ = ("e", "ed", "ext", "heat", "since_jump", "target", "temp", "ts", "valve", "window")

    def __init__(self, ts, temp, target, valve, heat) -> None:
        self.ts, self.temp, self.target, self.valve, self.heat = ts, temp, target, valve, heat
        self.e = None if temp is None or target is None else target - temp
        self.ed = self.e
        self.ext = self.window = False
        self.since_jump = float("inf")

    @property
    def clean(self) -> bool:
        """Weder Fenster noch Fremdwärme."""
        return not self.window and not self.ext


# ------------------------------------------------------------------ Eingänge aus Home Assistant
def binary_device(room) -> bool:
    """Kein Gerät des Raums hat einen Ventil-/Heizleistungssensor: Ventil nur 0/100 % aus ``hvac_action``."""
    return not any(d.get("heating_power") for d in room.devices)


def resting(store, rooms) -> list[str]:
    """Räume, deren gespeichertes %-Ventilmodell ruht (der Raum meldet nur an/aus, ``ValveModel.no_pct``) – es bleibt
    beim Neulernen aus gespeicherten Daten (0.45.1: ohne %-Werte ließe es sich nicht neu lernen)."""
    out = []
    for r in rooms:
        d = store.get(f"model.valve.{r.climate}")
        if d and vm.ValveModel.from_dict(d, binary_device(r)).no_pct:
            out.append(r.climate)
    return out


def group_of(room) -> dict | None:
    """Gruppe mit mehreren Geräten: wie viele Ventile gemittelt werden und ob stetige (mit Ventilsensor) und an/aus
    (ohne) gemischt sind – das Mittel beschreibt dann keines davon. Ein Raumregler als Schlüssel zählt nicht mit."""
    devs = room.devices
    if len(devs) < 2:
        return None
    valves = devs[1:] if room.controller else devs
    sensors = [bool(d.get("heating_power")) for d in valves]
    return {"n": len(devs), "mixed": any(sensors) and not all(sensors)}


def step_of(room, states: dict) -> tuple[float, str]:
    """(Schrittweite in K, Herkunft) – Attribut ``target_temp_step`` der Schreibziele, bei Gruppen der gröbste;
    meldet keines eine, 0,5 K („angenommen“)."""
    steps = []
    for climate in room.write_targets:
        raw = ((states.get(climate) or {}).get("attributes") or {}).get("target_temp_step")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if 0 < v <= 5:
            steps.append(v)
    return (max(steps), "gerät") if steps else (DEFAULT_STEP_K, "angenommen")


# ------------------------------------------------------------------ Aufbereiten
def _heat(row) -> bool | None:
    day, off = row["hc_day"], row["heating_off"]
    if day is None and off is None:
        return None
    return bool(day != 0 and off != 1)


def _subst(valve, binary: bool):
    """Ersatzwert: für ein Modell, das nur an/aus kennt, an/aus (offen = 100 %), sonst nicht lernen (None)."""
    return (100.0 if (valve or 0) > vm.OPEN_PCT else 0.0) if binary and valve is not None else None


def fetch(store, climate: str, start: int, end: int, running_ext: int | None = None,
          binary: bool = False) -> list[Row]:
    """Messpunkte ``start`` ≤ ts < ``end`` aufbereitet (Regelabweichung, Heizbetrieb, Fremdwärme, Fenster, Sprünge);
    Ersatzwerte siehe ``_subst``."""
    heat = {r["ts"]: _heat(r) for r in store.query(
        "SELECT ts, hc_day, heating_off FROM samples WHERE ts >= ? AND ts < ?", (start, end))}
    subst = {r["ts"] for r in store.query(
        "SELECT ts FROM valve_subst WHERE room = ? AND ts >= ? AND ts < ?", (climate, start, end))}
    rows = [Row(r["ts"], r["temp"], r["target"], _subst(r["valve"], binary) if r["ts"] in subst else r["valve"],
                heat.get(r["ts"]))
            for r in store.query(
                "SELECT ts, temp, target, valve FROM room_samples WHERE room = ? AND ts >= ? AND ts < ? ORDER BY ts",
                (climate, start, end))]
    spans = [(r["start_ts"], r["end_ts"]) for r in store.query(
        "SELECT start_ts, end_ts FROM room_events WHERE room = ? AND end_ts > ? AND start_ts < ?",
        (climate, start, end))]
    if running_ext is not None:
        spans.append((running_ext, end + 1))
    for r in rows:
        r.ext = any(a <= r.ts < b for a, b in spans)
    _mark_windows(rows)
    _mark_jumps(rows)
    return rows


def _mark_windows(rows: list[Row]) -> None:
    """Fenster offen: Temperatur fällt in ``WINDOW_SPAN_S`` um ``WINDOW_DROP_K`` – davor und danach ausblenden."""
    spans, recent = [], []
    for r in rows:
        if r.temp is None:
            continue
        recent = [(t, v) for t, v in recent if r.ts - t <= WINDOW_SPAN_S] + [(r.ts, r.temp)]
        if max(v for _, v in recent) - r.temp >= WINDOW_DROP_K:
            spans.append((r.ts - WINDOW_SPAN_S, r.ts + WINDOW_HOLD_S))
    if spans:
        for r in rows:
            r.window = any(a <= r.ts <= b for a, b in spans)


def _mark_jumps(rows: list[Row]) -> None:
    """Sekunden seit dem letzten Soll-Wechsel (auch aus/an) – am Anfang ohne Vorgeschichte: unbegrenzt."""
    last = None
    for p, r in zip([None, *rows], rows):
        if p is not None and (p.target is None) != (r.target is None) or (
                p is not None and p.target is not None and r.target is not None and abs(r.target - p.target) >= 0.05):
            last = r.ts
        r.since_jump = float("inf") if last is None else r.ts - last


def _delay(rows: list[Row], delay_s: float) -> None:
    """Verzögerte Regelabweichung ``ed``: der Messpunkt, der der Zeit ts − Totzeit am nächsten liegt (nicht später)."""
    j = 0
    for k, r in enumerate(rows):
        want = r.ts - delay_s
        while j < k and abs(rows[j + 1].ts - want) <= abs(rows[j].ts - want):
            j += 1
        r.ed = rows[j].e if r.ts - rows[j].ts <= delay_s + MAX_GAP_S else None


def _pi(model: vm.ValveModel, rows: list[Row], a: int, b: int) -> None:
    """PI-Regression: je Anker 30 min ohne Sättigung, Lücke, Fenster oder Fremdwärme (siehe ``ValveModel.gains``)."""
    for k, r in enumerate(rows):
        if not (a <= r.ts < b and r.clean and r.ed is not None and r.valve is not None
                and vm.SAT_LO < r.valve < vm.SAT_HI and r.since_jump >= STEADY_S):
            continue
        prev, e_dt = r, 0.0
        for q in rows[k + 1:]:
            if (q.ts - prev.ts > MAX_GAP_S or not q.clean or q.ed is None or q.valve is None
                    or not vm.SAT_LO < q.valve < vm.SAT_HI or q.ts - r.ts > PRED_S + MAX_GAP_S
                    or q.since_jump < q.ts - r.ts + STEADY_S):
                break  # Soll-Sprung: Stellmotor und Sättigung des Reglers verfälschen Kp
            e_dt += q.ed * (q.ts - prev.ts) / 60
            prev = q
            if q.ts - r.ts >= PRED_S - 60:
                model.add_pi(q.ed - r.ed, e_dt, (q.ts - r.ts) / 60, q.valve - r.valve)
                break


def _pairs(rows: list[Row], a: int, b: int):
    """(vorher, Messpunkt) ohne Lücke dazwischen, Messpunkt im Ankerbereich a ≤ ts < b."""
    for p, r in pairwise(rows):
        if a <= r.ts < b and r.ts - p.ts <= MAX_GAP_S:
            yield p, r


# ------------------------------------------------------------------ Lernen
def learn_span(model: vm.ValveModel, rows: list[Row], lo: int, hi: int) -> None:
    """Messpunkte des Abschnitts lernen: Anker a = lo − LAG_S ≤ ts < b = hi − LAG_S. Zuerst das Gütemaß mit dem
    bisherigen Modell (ehrlich: vorhergesagt, bevor gelernt), dann Kennlinie, Sprünge, Schaltpunkte und PI."""
    a, b = lo - LAG_S, hi - LAG_S
    if model.no_pct and model.decay_ts is not None:  # ruht (0.45.1): nichts vergessen
        model.decay_ts = max(model.decay_ts, hi)
    model.decay(hi)
    model.note_kind()
    _delay(rows, model.dead_s())
    _errors(model, rows, a, b)
    _steady(model, rows, a, b)
    _noise(model, rows, a, b)
    _jumps(model, rows, a, b)
    for p, r in _pairs(rows, a, b):
        if (p.clean and r.clean and p.valve is not None and r.valve is not None and r.since_jump >= STEADY_S
                and p.ed is not None and r.ed is not None and (p.valve > vm.OPEN_PCT) != (r.valve > vm.OPEN_PCT)):
            model.add_switch(r.valve > vm.OPEN_PCT, (p.ed + r.ed) / 2)
    _pi(model, rows, a, b)
    model.learned_until = hi
    if model.err:
        model.prune_err(max(model.err))
    model.note_e50(local(hi - 1).date().isoformat())


def _steady(model: vm.ValveModel, rows: list[Row], a: int, b: int) -> None:
    """Kennlinie in Beharrung (je Messpunkt die Zeit seit dem vorigen, höchstens ``MAX_GAP_S``), Ausgelassenes, und
    die Zählung für „nur an/aus“ – nur Messpunkte, die etwas sagen (siehe oben)."""
    prev = None
    for r in rows:
        dt = min(r.ts - prev, MAX_GAP_S) if prev is not None else 300
        prev = r.ts
        if not a <= r.ts < b or r.valve is None:
            continue
        if r.heat is not False and r.clean and r.e is not None and (r.valve > vm.OPEN_PCT or r.e >= 0):
            model.count_valve(r.valve)
        hours = dt / 3600
        if r.heat is False:
            model.skipped_h["heizung_aus"] += hours
        elif r.window:
            model.skipped_h["fenster"] += hours
        elif r.ext:
            model.skipped_h["fremdwaerme"] += hours
        elif r.e is not None and r.since_jump >= STEADY_S:
            model.add_steady(r.e, r.valve, hours)


def _noise(model: vm.ValveModel, rows: list[Row], a: int, b: int) -> None:
    """Messrauschen der Raumtemperatur: Abweichung des mittleren von drei Messpunkten von der Geraden durch die
    beiden anderen, in Beharrung ohne Fenster und Fremdwärme; auf das Rauschen eines Messpunkts umgerechnet."""
    for p, r, n in zip(rows, rows[1:], rows[2:]):
        if not (a <= r.ts < b and p.temp is not None and r.temp is not None and n.temp is not None
                and p.clean and r.clean and n.clean and r.since_jump >= STEADY_S
                and r.ts - p.ts <= MAX_GAP_S and n.ts - r.ts <= MAX_GAP_S):
            continue
        w = (n.ts - r.ts) / (n.ts - p.ts)
        dev = abs(r.temp - (w * p.temp + (1 - w) * n.temp))
        if dev < NOISE_MAX_K:
            model.add_noise(dev / (1 + w * w + (1 - w) ** 2) ** 0.5)


def _crossing(points: list[tuple[float, float]], level: float) -> tuple[float, float] | None:
    """(Erste Zeit in s, zu der ``points`` [(t, Bewegung)] ``level`` erreicht – linear zwischen den Messpunkten;
    Messpause dort in s)."""
    for (t0, m0), (t1, m1) in pairwise(points):
        if m1 >= level:
            t = t1 if m1 == m0 or m0 >= level else t0 + (level - m0) / (m1 - m0) * (t1 - t0)
            return t, t1 - t0
    return None


def _jumps(model: vm.ValveModel, rows: list[Row], a: int, b: int) -> None:
    """Sprungantworten: Soll ändert sich um ≥ ``JUMP_K`` (Zeitpunkt: Mitte zwischen den beiden Messpunkten)."""
    binary = model.kind == "binär"
    for k in range(1, len(rows)):
        p, r = rows[k - 1], rows[k]
        if not (a <= r.ts < b and p.target is not None and r.target is not None and p.valve is not None
                and abs(r.target - p.target) >= vm.JUMP_K - 1e-6 and r.ts - p.ts <= MAX_GAP_S and r.clean):
            continue
        up, t_j = r.target > p.target, (p.ts + r.ts) / 2
        pts, last = [(0.0, p.valve)], p.ts  # bis zur Sprungmitte stand das Ventil wie vorher
        for q in rows[k:]:
            if (q.ts - t_j > RESP_S or q.ts - last > MAX_GAP_S or q.target is None
                    or abs(q.target - r.target) >= 0.05 or q.valve is None or not q.clean):
                break
            pts.append((q.ts - t_j, q.valve))
            last = q.ts
        if pts[-1][0] < RESP_MIN_S:
            continue
        sign = 1 if up else -1
        move = [(t, sign * (v - p.valve)) for t, v in pts]
        if binary:
            if (p.valve > vm.OPEN_PCT) == up:
                continue  # schon im Zielzustand
            hit = next((i for i, (_, v) in enumerate(pts) if (v > vm.OPEN_PCT) == up), None)
            if hit is None:
                continue
            dead = (pts[hit - 1][0] + pts[hit][0]) / 2  # geschaltet zwischen den beiden Messpunkten
            model.add_jump("up" if up else "down", dead / 60, dead / 60, (pts[hit][0] - pts[hit - 1][0]) / 60)
            continue
        full = max(m for _, m in move)
        if full < vm.RESP_MIN_PCT:
            continue  # Ventil schon am Anschlag oder reagiert nicht
        dead, t63 = _crossing(move, vm.MOVE_PCT), _crossing(move, 0.63 * full)
        if dead is not None and t63 is not None:
            model.add_jump("up" if up else "down", dead[0] / 60, t63[0] / 60, t63[1] / 60)


def _errors(model: vm.ValveModel, rows: list[Row], a: int, b: int) -> None:
    """Gütemaß: Ventil einen Messschritt und 30 min voraus aus der gemessenen Regelabweichung (nur Heizbetrieb an) –
    mit dem gewählten Vorhersager, dazu jeder Vorhersager für sich (``regler`` wählt danach) und zum Vergleich
    „Ventil bleibt, wie es ist“."""
    binary, cands, chosen = model.kind == "binär", model.candidates(), model.regler()
    if not cands:
        return

    def actual(v: float) -> float:
        return (100.0 if v > vm.OPEN_PCT else 0.0) if binary else v

    for k, r in enumerate(rows):
        if not (a <= r.ts < b and r.heat is not False and r.clean and r.valve is not None and r.ed is not None):
            continue
        states, prev, day = {n: p.start(r.valve, r.ed) for n, p in cands.items()}, r, local(r.ts).date().isoformat()
        for q in rows[k + 1:]:
            if (q.ts - prev.ts > MAX_GAP_S or q.ed is None or q.valve is None or not q.clean
                    or q.ts - r.ts > PRED_S + MAX_GAP_S):
                break
            vs = {n: p.step(states[n], q.ed, (q.ts - prev.ts) / 60) for n, p in cands.items()}
            if prev is r and chosen in vs:
                model.add_err(day, 1, vs[chosen] - actual(q.valve))
            prev = q
            if q.ts - r.ts >= PRED_S - 60:
                errs = {n: v - actual(q.valve) for n, v in vs.items()}
                model.add_err(day, 30, errs.get(chosen), actual(r.valve) - actual(q.valve), errs)
                break


def learn_chunk(store, model: vm.ValveModel, climate: str, until: int, running_ext: int | None = None) -> bool:
    """Einen Abschnitt (höchstens ``CHUNK_S``) bis ``until`` lernen. True, wenn danach noch etwas offen ist."""
    if model.learned_until is None:  # nicht vor „Ventil lernt ab“ (0.44: Ventil neu lernen, anderes Thermostat)
        since = max(until - HISTORY_DAYS * 86400, valve_since.of(store, climate))
        first = store.query("SELECT MIN(ts) AS t FROM room_samples WHERE ts >= ? AND room = ?", (since, climate))
        # ohne Messwerte gibt es nichts nachzulernen: ab jetzt laufend (nicht jeden Takt erneut suchen)
        model.learned_until = until if not first or first[0]["t"] is None else int(first[0]["t"])
    lo = model.learned_until
    if lo >= until:
        return False
    hi = min(lo + CHUNK_S, until)
    rows = fetch(store, climate, lo - LAG_S - CTX_S, hi, running_ext, model.binary_dev)
    learn_span(model, rows, lo, hi)
    return hi < until


def behind(model: vm.ValveModel, until: int | None) -> bool:
    """Hat das Modell noch nichts gelernt oder lernt es noch aus der Historie nach (mehr als ``CATCH_UP_S`` zurück)?"""
    return model.learned_until is None or (until is not None and until - model.learned_until > CATCH_UP_S)


def learn_all(store, models: dict, until: int, running_ext: dict, budget_s: float | None = None) -> bool:
    """Alle Räume reihum abschnittweise bis ``until`` lernen, höchstens ``budget_s`` Sekunden (None: bis fertig).
    True, wenn noch etwas offen ist (dann im nächsten Takt weiter)."""
    deadline = None if budget_s is None else time.monotonic() + budget_s
    pending = list(models)
    while pending:
        for climate in list(pending):
            if not learn_chunk(store, models[climate], climate, until, running_ext.get(climate)):
                pending.remove(climate)
            if deadline is not None and time.monotonic() > deadline:
                return bool(pending)
    return False
