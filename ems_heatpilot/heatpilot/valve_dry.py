"""Ventile im Trockenlauf (seit 0.39): was HeatPilot mit den Thermostaten als Stellglied tun würde – nur Protokoll.

Stufe 1 von „Thermostate als Stellglied“ (Planungspapier): Es wird **nichts geschrieben** – kein Aufruf an Thermostate
oder Kessel, auch nicht bei „HeatPilot steuert“. Je Takt prüft ``step`` zwei Regeln, gezählt nach Räumen (eine Gruppe
mit mehreren Ventilen ist ein Raum; der Heizkreis von EMS-ESP ist nie dabei):

* **früh drosseln:** Der Raum nähert sich seinem Soll wirklich – gemessen deutlich steigend (über dem Rauschen) und
  seit einem Soll-Anstieg noch nicht erreicht oder noch mindestens 0,2 K bzw. einen halben Schritt darunter –, er
  erreicht es laut Raum- und Ventilmodell (gemeinsam gerechnet; ohne plausibles Raummodell nach dem gemessenen
  Anstieg) in weniger als 20 min, das Ventil wäre dann noch über 30 % offen und die Modelle sagen ein spürbares
  Überschwingen voraus (ab 0,2 K). HeatPilot würde das Soll jetzt um einen Schritt senken und zurücksetzen, sobald
  der Raum das eigentliche Soll erreicht (spätestens nach 60 min). Danach drosselt es den Raum erst wieder nach einem
  neuen Soll oder wenn er deutlich (0,5 K) ausgekühlt ist – ein Thermostat mit Integrator, das das Soll hält, löst
  so nicht dauernd aus.
* **gemeinsam anfordern:** Nur ein Raum fordert Wärme an (Ventil über 5 %) und der Kessel taktet (mindestens 3
  Brennerstarts in der letzten Stunde, Läufe im Mittel unter 10 min). Räume, die in den nächsten 60 min ohnehin heizen
  würden (Ventilmodell entlang des Abkühlens aus dem Raummodell bzw. dem gemessenen Abfall; ohne Vorhersage: bis zur
  Öffnungsschwelle, mindestens bis zum Soll abgekühlt), würde HeatPilot um einen Schritt anheben, damit mehrere
  zugleich anfordern – zurück, sobald der anfordernde Raum nicht mehr anfordert, spätestens nach 60 min.

Regeln wie abgesprochen, im Trockenlauf mitgezählt: nur bei Änderung, in der vollen Auflösung des Thermostats
(Schrittweite je Gerät, ``valve_learn.step_of``), 5 min Haltezeit je Raum nach jedem Schreibvorgang (wie Weg W).
Ändern Zeitplan oder Hand das Soll, endet ein Eingriff ohne Zurückschreiben. Beide Regeln verschieben nur Raum-Sollwerte,
nie den Vorlauf – die Fußboden-Grenze (``floor_limit``, 0.39.1) kann kein Vorschlag überschreiten; ein späterer
Takt-Boost am Vorlauf muss sie vor jedem Schreiben einhalten (Planungspapier, Weg B).

Seit 0.40 schreibt HeatPilot mit dem Schalter „Ventile führen“ wirklich (``valve_control`` – dieselben Regeln, die
Funktionen hier): Räume, die es führt (``skip``), rechnet der Trockenlauf dann nicht mit; ihre echten Eingriffe stehen
mit ``written`` = 1 in derselben Tabelle, und die Auswertung zählt beide – „schreibt“ statt „würde schreiben“.

Protokoll: Tabelle ``valve_dry_log`` (90 Tage; „Lernstand neu berechnen“ lässt sie stehen). Eine Stunde danach trägt
``fill_actuals`` nach, was wirklich geschah (Überschwingen über das Soll, Brennerstarts – aus den Brennerereignissen
oder dem Startzähler). ``summary`` wertet aus: wie oft, Schreibvorgänge, je Raum und Tag, Beispiele; die geschätzte
Wirkung (weniger Überschwingen in K, Brennerstarts) nur mit plausiblem Raummodell und belastbarem Ventilmodell, sonst
wird nur gezählt. Die Schätzung des Überschwingens zeigt es erst, wenn für den Raum das vorhergesagte Überschwingen
ohne Eingriff mehrmals mit dem gemessenen übereinstimmte (es wurde ja nichts geschrieben – gemessen wird genau das).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from . import guards, valve_learn, valve_since
from . import valve_predict as vp
from .analysis import cycling_stats
from .control import TEMP_MAX, TEMP_MIN
from .dhw_rule import HOLD_S
from .values import local, midnight
from .valve_model import OPEN_PCT

THROTTLE_ETA_MIN = 20       # früh drosseln, wenn der Raum sein Soll in weniger Minuten erreicht …
THROTTLE_VALVE_PCT = 30.0   # … und das Ventil dann noch weiter offen wäre …
OVERSHOOT_K = 0.2           # … und die Modelle (wenn es sie gibt) ohne Eingriff so viel Überschwingen vorhersagen
THROTTLE_MAX_MIN = 60       # spätestens dann zurück
APPROACH_K = 0.2            # Annäherung: so weit (oder einen halben Schritt) unter dem Soll …
APPROACH_H = 6              # … oder seit einem Soll-Anstieg in so vielen Stunden noch nicht erreicht …
RISE_KH = 0.3               # … und gemessen mindestens so schnell steigend (K/h) …
RISE_SIGMA = 3.0            # … und so viele Standardfehler über null (Messrauschen)
NOISE_FLOOR_K = 0.03        # Messrauschen der Raumtemperatur mindestens so viel annehmen
REARM_K = 0.5               # nach dem Drosseln erst wieder nach neuem Soll oder so weit ausgekühlt
JOIN_AHEAD_MIN = 60         # gemeinsam anfordern: Räume, die in so vielen Minuten ohnehin heizen würden
JOIN_MAX_MIN = 60           # spätestens dann zurück
CYCLE_STARTS_H = 3          # Kessel taktet: so viele Brennerstarts in der letzten Stunde …
CYCLE_RUN_MIN = 10.0        # … und Läufe im Mittel kürzer
SLOPE_MIN = 30              # gemessener Anstieg/Abfall über so viele Minuten
SIM_MIN = 90                # Wirkung des Drosselns über so viele Minuten schätzen
CHECK_S = 3600              # so lange danach nachtragen, was wirklich geschah
CAL_DAYS, CAL_MIN_N, CAL_MAX_K = 30, 3, 0.2  # Schätzung zeigen: so oft nachgemessen, im Mittel so nah dran
DAYS = 7
EXAMPLES = 6
KV_STATE = "valve_dry.state"
NOTE = "Beobachten – HeatPilot schreibt nichts an die Thermostate"
NOTE_LIVE = ("Ventile führen ist an – HeatPilot schreibt an die Thermostate der Räume, die es führt; die übrigen nur "
             "beobachtet")
RULES = ("drosseln", "gemeinsam")


@dataclass
class _Room:
    climate: str
    name: str
    managed: bool
    temp: float | None
    target: float | None
    valve: float | None
    step: float
    model: object | None    # plausibles Raummodell, sonst None
    vm: object              # Ventilmodell
    vm_ok: bool             # Ventilmodell belastbar


def _grid(value: float, step: float) -> float:
    """Auf den Schritt des Geräts runden, in den Grenzen der Steuerung."""
    return round(min(max(round(value / step) * step, TEMP_MIN), TEMP_MAX), 2)


def _rooms(eng, readings: dict, ts: int) -> list[_Room]:
    out = []
    day = local(ts).date().isoformat()
    for room in eng.found.rooms:
        vm = eng.valves.get(room.climate)
        if room.ems or vm is None:
            continue
        model = eng.rooms.get(room.climate)
        ok = model is not None and guards.check_room(model)["ok"]
        behind = valve_learn.behind(vm, eng._last_room_step)
        _, temp, target, valve, _ = readings.get(room.climate, (None,) * 5)
        out.append(_Room(room.climate, room.name, bool(room.managed), temp, target, valve,
                         valve_learn.step_of(room, eng.states)[0], model if ok else None, vm,
                         not behind and vm.reliable(day)[0]))
    return out


# ------------------------------------------------------------------ Messwerte
def _trend(store, r: _Room, ts: int) -> tuple[float, float] | None:
    """Gemessener Anstieg (K/h) und sein Standardfehler: Ausgleichsgerade der letzten ``SLOPE_MIN`` Minuten (bei
    langer Messpause die letzten drei Messpunkte bis doppelt so weit zurück). Rauschen: Streuung um die Gerade,
    mindestens das gelernte Messrauschen bzw. ``NOISE_FLOOR_K``."""
    rows = [(x["ts"], x["temp"]) for x in store.query(
        "SELECT ts, temp FROM room_samples WHERE room = ? AND ts > ? AND ts <= ? AND temp IS NOT NULL ORDER BY ts",
        (r.climate, ts - 2 * SLOPE_MIN * 60, ts))]
    pts = [p for p in rows if p[0] > ts - SLOPE_MIN * 60]
    pts = pts if len(pts) >= 3 else rows[-3:]
    if len(pts) < 3:
        return None
    n = len(pts)
    mt, mv = sum(t for t, _ in pts) / n, sum(v for _, v in pts) / n
    sxx = sum((t - mt) ** 2 for t, _ in pts)
    if sxx <= 0:
        return None
    slope = sum((t - mt) * (v - mv) for t, v in pts) / sxx
    resid = (sum((v - mv - slope * (t - mt)) ** 2 for t, v in pts) / (n - 2)) ** 0.5 if n > 3 else 0.0
    sigma = max(resid, r.vm.noise_k() or 0.0, NOISE_FLOOR_K)
    return slope * 3600, sigma / sxx**0.5 * 3600


def _unreached_rise(store, r: _Room, ts: int) -> bool:
    """Stieg das Soll in den letzten ``APPROACH_H`` Stunden (Zeitplan, Boost, Hand), ohne dass der Raum es seitdem
    erreicht hat?"""
    rows = store.query("SELECT ts, temp, target FROM room_samples WHERE room = ? AND ts > ? AND ts <= ? ORDER BY ts",
                       (r.climate, ts - APPROACH_H * 3600, ts))
    for k in range(len(rows) - 1, 0, -1):
        prev, cur = rows[k - 1]["target"], rows[k]["target"]
        if cur is not None and (prev is None or abs(cur - prev) >= 0.05):
            rose = prev is None or cur > prev
            return rose and all(x["temp"] is None or x["temp"] < r.target for x in rows[k:])
    return False


def _approaching(eng, r: _Room, ts: int) -> bool:
    """Nähert sich der Raum dem Soll wirklich (nicht nur Rauschen eines Thermostats, das das Soll hält)?"""
    tr = _trend(eng.store, r, ts)
    if tr is None or tr[0] < max(RISE_KH, RISE_SIGMA * tr[1]):
        return False
    return r.target - r.temp >= max(APPROACH_K, r.step / 2) - 1e-9 or _unreached_rise(eng.store, r, ts)


def _heating(sample: dict) -> bool:
    """Kommt Wärme an? Vorlauf bekannt und Heizungspumpe bzw. Heizbetrieb an."""
    return sample.get("flow") is not None and bool(sample.get("pump_on") or sample.get("heating"))


def _starts(store, a: int, b: int) -> tuple[int | None, float | None]:
    """Brennerstarts und mittlere Laufzeit (min) in a ≤ ts ≤ b: aus den Brennerereignissen, ohne sie aus dem
    Startzähler der Therme (dann ohne Laufzeit); (None, None), wenn es beides nicht gibt."""
    events = [(r["ts"], r["state"]) for r in store.query(
        "SELECT ts, state FROM burner_events WHERE ts >= ? AND ts <= ? ORDER BY ts", (a - 3600, b))]
    if events:
        st = cycling_stats(events, a, b + 1)
        return st["starts"], st["avg_run_min"]
    r = store.query("SELECT MIN(burn_starts) AS a, MAX(burn_starts) AS b FROM samples WHERE ts >= ? AND ts <= ?",
                    (a, b))
    return (int(r[0]["b"] - r[0]["a"]), None) if r and r[0]["a"] is not None else (None, None)


def cycling(store, ts: int) -> dict:
    """Taktet der Kessel? Brennerstarts und mittlere Laufzeit der letzten Stunde (``_starts``)."""
    starts, run = _starts(store, ts - 3600, ts)
    starts = starts or 0
    return {"starts_h": starts, "run_min": run,
            "cycling": starts >= CYCLE_STARTS_H and (run is None or run < CYCLE_RUN_MIN)}


# ------------------------------------------------------------------ Vorhersage
def _sim(r: _Room, sample: dict, lowered: float) -> dict | None:
    """Raum- und Ventilmodell gemeinsam in 1-min-Schritten über ``SIM_MIN`` Minuten (Ventil mit Totzeit und
    Stellmotor): wann der Raum sein Soll erreicht, wie weit das Ventil dann offen ist, das höchste Überschwingen in
    der ersten Stunde und insgesamt. ``lowered``: Soll so viel tiefer, bis der Raum das eigentliche Soll erreicht
    (spätestens ``THROTTLE_MAX_MIN``). None ohne plausibles Raummodell oder ohne Ventilvorhersage."""
    p, flow, t_out = r.vm.predictor(), sample.get("flow"), sample.get("t_out")
    if p is None or r.model is None or flow is None or t_out is None:
        return None
    t, sp = r.temp, r.target - lowered
    s = p.start(r.valve, r.target - t)  # bis jetzt regelt das Thermostat auf das eigentliche Soll
    delay = vp.Delay(r.vm.dead_s() / 60, r.target - t)
    eta = v_reach = None
    peak = peak60 = t
    for minute in range(1, SIM_MIN + 1):
        if t >= r.target or minute > THROTTLE_MAX_MIN:
            sp = r.target
        v = p.step(s, delay.push(sp - t), 1.0)
        t += r.model.rate(t, flow, t_out, v / 100) / 60
        if eta is None and t >= r.target:
            eta, v_reach = minute, v
        peak = max(peak, t)
        peak60 = peak if minute <= CHECK_S // 60 else peak60
    return {"eta": eta, "v_reach": v_reach, "over": max(peak - r.target, 0.0), "over60": max(peak60 - r.target, 0.0)}


def _eta_min(eng, r: _Room, sample: dict, ts: int) -> float | None:
    """Ohne Ventilvorhersage: Minuten bis zum Soll aus dem Raummodell mit dem Ventil von jetzt, ohne plausibles
    Raummodell aus dem gemessenen Anstieg."""
    flow, t_out = sample.get("flow"), sample.get("t_out")
    if r.model is not None and t_out is not None:
        h = r.model.time_to_reach(r.temp, r.target, flow, t_out, r.valve / 100)
        return None if h is None else h * 60
    tr = _trend(eng.store, r, ts)
    return (r.target - r.temp) / tr[0] * 60 if tr is not None and tr[0] > 0.05 else None


def _cooling(eng, r: _Room, sample: dict, ts: int) -> list[float]:
    """Raumtemperatur je Minute über ``JOIN_AHEAD_MIN`` Minuten ohne Heizen (Raummodell, sonst gemessener Abfall)."""
    t_out = sample.get("t_out")
    if r.model is not None and t_out is not None:
        return [r.model.cool_down(r.temp, t_out, m / 60) for m in range(JOIN_AHEAD_MIN + 1)]
    tr = _trend(eng.store, r, ts)
    fall = min(tr[0], 0.0) if tr is not None else 0.0
    return [r.temp + fall * m / 60 for m in range(JOIN_AHEAD_MIN + 1)]


def _would_heat(eng, r: _Room, sample: dict, ts: int) -> bool:
    """Würde der Raum in ``JOIN_AHEAD_MIN`` Minuten ohnehin heizen? Ventilmodell ab dem Ventil von jetzt entlang des
    Abkühlens; ohne Vorhersage: abgekühlt bis zur Öffnungsschwelle, mindestens bis zum Soll – ein geschlossenes
    Ventil öffnet über dem Soll nicht, auch mit Integrator (der ist dann abgebaut)."""
    temps = _cooling(eng, r, sample, ts)
    p = r.vm.predictor()
    if p is not None:
        vs = vp.path(p, r.valve or 0.0, [r.target - t for t in temps], r.vm.dead_s() / 60)
        return max(vs) > OPEN_PCT
    return r.target - temps[-1] >= max(r.vm.open_at() or 0.0, 0.0)


# ------------------------------------------------------------------ Regeln
def _throttle(eng, r: _Room, sample: dict, cur: dict | None, ts: int, locked: bool) -> dict | None:
    """Regel „früh drosseln“ – Wunsch (Soll, Grund, Schätzung) oder None."""
    if cur is not None:  # läuft schon: bis das eigentliche Soll erreicht ist, höchstens THROTTLE_MAX_MIN
        if cur["rule"] != "drosseln" or (r.temp is not None and r.temp >= cur["base"] - 1e-6) \
                or ts - cur["since"] >= THROTTLE_MAX_MIN * 60:
            return None
        return {"rule": "drosseln", "sp": cur["sp"]}
    if (locked or not _heating(sample) or r.temp is None or r.target is None or r.valve is None
            or r.temp >= r.target or r.valve <= THROTTLE_VALVE_PCT or not _approaching(eng, r, ts)):
        return None
    sim = _sim(r, sample, 0.0)
    if sim is not None:
        eta, v_reach = sim["eta"], sim["v_reach"]
    else:
        eta = _eta_min(eng, r, sample, ts)
        pred = None if eta is None else r.vm.predict_path(r.valve, r.target - r.temp, eta)
        v_reach = r.valve if pred is None else pred
    sp = _grid(r.target - r.step, r.step)
    if (eta is None or eta >= THROTTLE_ETA_MIN or v_reach <= THROTTLE_VALVE_PCT or sp >= r.target
            or (sim is not None and sim["over"] < OVERSHOOT_K)):
        return None
    reliable = sim is not None and r.vm_ok
    est = None
    if reliable:
        est = round(max(sim["over"] - _sim(r, sample, r.target - sp)["over"], 0.0), 2)
    when = f"in ≈ {int(eta)} min" if eta >= 1 else "in unter 1 min"  # abgerundet: die Regel heißt „unter 20 min“
    return {"rule": "drosseln", "sp": sp, "eta_min": round(eta, 1), "valve_pred": round(v_reach, 1),
            "reliable": reliable, "est_k": est, "pred_over_k": round(sim["over60"], 2) if reliable else None,
            "reason": f"erreicht {_de(r.target)} °C {when}, Ventil dann noch ≈ {math.ceil(v_reach)} % offen"}


def _join(eng, infos: list[_Room], act: dict, wishes: dict, sample: dict, ts: int,
          candidates: set | None = None, leads: set | None = None) -> dict | None:
    """Regel „gemeinsam anfordern“: laufende weiterführen, neue vormerken – angehoben werden nur ``candidates`` (None:
    alle Räume; angefordert zählen immer alle), auslösen darf nur ein Raum aus ``leads`` (None: jeder). Gibt die
    Taktung zurück (oder None)."""
    demanding = [r for r in infos if r.target is not None and (r.valve or 0) > OPEN_PCT]
    running = {c: a for c, a in act.items() if a["rule"] == "gemeinsam"}
    if not running and len(demanding) != 1:
        return None
    cyc = cycling(eng.store, ts)
    for c, a in running.items():
        if any(d.climate == a.get("for") for d in demanding) and ts - a["since"] < JOIN_MAX_MIN * 60:
            wishes[c] = {"rule": "gemeinsam", "sp": a["sp"]}
    if len(demanding) != 1 or not cyc["cycling"] or (leads is not None and demanding[0].climate not in leads):
        return cyc
    lead = demanding[0]
    for r in infos:
        if (r is lead or r.climate in act or r.climate in wishes or r.temp is None or r.target is None
                or (r.valve or 0) > OPEN_PCT or (candidates is not None and r.climate not in candidates)):
            continue
        sp = _grid(r.target + r.step, r.step)
        if sp <= r.target or not _would_heat(eng, r, sample, ts):
            continue
        reliable = r.model is not None and r.vm_ok
        wishes[r.climate] = {
            "rule": "gemeinsam", "sp": sp, "for": lead.climate, "reliable": reliable,
            "est_starts": 1.0 if reliable else None, "starts_h": cyc["starts_h"],
            "reason": (f"nur {lead.name} fordert an, Kessel taktet ({cyc['starts_h']} Starts/h) – "
                       f"{r.name} würde in {JOIN_AHEAD_MIN} min ohnehin heizen")}
    return cyc


def _de(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


# ------------------------------------------------------------------ Takt
def _state(eng) -> dict:
    st = eng.__dict__.get("_valve_dry")
    if st is None:
        saved = eng.store.get(KV_STATE) or {}
        st = {"active": dict(saved.get("active") or {}), "last": dict(saved.get("last") or {}),
              "lock": dict(saved.get("lock") or {})}
        eng.__dict__["_valve_dry"] = st
    return st


def _unlock(lock: dict, by: dict) -> None:
    """Sperre nach dem Drosseln aufheben: neues Soll (Zeitplan, Hand) oder der Raum ist deutlich ausgekühlt."""
    for c, base in list(lock.items()):
        r = by.get(c)
        if (r is None or r.target is None or abs(r.target - base) >= 0.05
                or (r.temp is not None and r.temp <= r.target - REARM_K)):
            del lock[c]


def step(eng, ts: int, sample: dict, rooms: list[tuple], skip=()) -> list[dict]:
    """Im Takt der Engine: Wünsche nach beiden Regeln bestimmen, nach den Schreibregeln zählen und protokollieren.
    Schreibt nur in die eigene Datenbank. ``skip`` (0.40): Räume, die HeatPilot gerade wirklich führt
    (``valve_control``) – für sie nichts im Trockenlauf; als anfordernder Raum zählen sie weiter. Gibt die neuen
    Protokolleinträge zurück."""
    st = _state(eng)
    act, last, lock = st["active"], st["last"], st["lock"]
    infos = _rooms(eng, {r[0]: r for r in rooms}, ts)
    by = {r.climate: r for r in infos}
    for c in list(act):  # Soll von außen geändert (Zeitplan, Hand) oder Raum weg: Eingriff endet ohne Schreiben
        r = by.get(c)
        if r is None or c in skip or r.target is None or abs(r.target - act[c]["base"]) >= 0.05:
            del act[c]
    _unlock(lock, by)
    wishes: dict[str, dict] = {}
    for r in infos:
        w = None if r.climate in skip else _throttle(eng, r, sample, act.get(r.climate), ts, r.climate in lock)
        if w is not None:
            wishes[r.climate] = w
    _join(eng, infos, act, wishes, sample, ts, {r.climate for r in infos} - set(skip))
    entries = _apply(st, by, wishes, ts)
    for e in entries:
        eng.store.add_valve_dry(e)
    fill_actuals(eng.store, ts)
    eng.store.put(KV_STATE, {"active": act, "last": {c: t for c, t in last.items() if ts - t < HOLD_S * 2},
                             "lock": lock})
    return entries


def _apply(st: dict, by: dict, wishes: dict, ts: int) -> list[dict]:
    """Wünsche nach den Schreibregeln zählen: nur bei Änderung, Haltezeit je Raum; ein beendetes Drosseln sperrt den
    Raum bis zum nächsten Soll (``_unlock``)."""
    act, last, lock = st["active"], st["last"], st["lock"]
    entries = []
    for c in sorted(set(act) | set(wishes)):
        cur, w, r = act.get(c), wishes.get(c), by[c]
        if cur is not None and w is not None and w["rule"] == cur["rule"] and abs(w["sp"] - cur["sp"]) < r.step / 2:
            continue  # unverändert: nichts schreiben
        if ts - last.get(c, -HOLD_S) < HOLD_S:
            continue  # Haltezeit: danach, wenn es dann noch anders ist
        base = cur["base"] if cur is not None else r.target
        if w is not None:
            entries.append(_entry(ts, r, w, base, "senken" if w["sp"] < base else "anheben"))
            act[c] = {"rule": w["rule"], "sp": w["sp"], "base": base, "since": ts, "for": w.get("for")}
        else:
            entries.append(_entry(ts, r, {"rule": cur["rule"], "sp": base, "reason": "zurück auf das Soll"}, base,
                                  "zurück"))
            if cur["rule"] == "drosseln":
                lock[c] = base
            del act[c]
        last[c] = ts
    return entries


def _entry(ts: int, r: _Room, w: dict, base: float, action: str) -> dict:
    return {"ts": int(ts), "room": r.climate, "rule": w["rule"], "action": action, "setpoint": w["sp"],
            "base": base, "temp": r.temp, "valve": r.valve, "eta_min": w.get("eta_min"),
            "valve_pred": w.get("valve_pred"), "est_k": w.get("est_k"), "est_starts": w.get("est_starts"),
            "pred_over_k": w.get("pred_over_k"),
            "reliable": None if action == "zurück" else int(bool(w.get("reliable"))), "managed": int(r.managed),
            "reason": w.get("reason"), "starts_h": w.get("starts_h"), "written": 0}


def fill_actuals(store, now_ts: int) -> int:
    """Eine Stunde nach jedem Eingriff nachtragen, was wirklich geschah (im Trockenlauf ohne Eingriff, bei Ventile führen
    mit ihm): beim Drosseln das höchste Überschwingen über das Soll, beim gemeinsamen Anfordern die Brennerstarts der
    Stunde (Ereignisse oder Startzähler, wie ``cycling``)."""
    open_rows = store.valve_dry_open(now_ts - CHECK_S)
    for e in open_rows:
        over = starts = None
        if e["rule"] == "drosseln":
            t = store.query("SELECT MAX(temp) AS t FROM room_samples WHERE room = ? AND ts > ? AND ts <= ?",
                            (e["room"], e["ts"], e["ts"] + CHECK_S))[0]["t"]
            over = None if t is None or e["base"] is None else round(max(t - e["base"], 0.0), 2)
        else:
            starts = _starts(store, e["ts"], e["ts"] + CHECK_S)[0]
        store.set_valve_dry_actual(e["id"], over, starts)
    if open_rows:
        store.commit()
    return len(open_rows)


# ------------------------------------------------------------------ Auswertung
def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def checked_rooms(rows: list[dict]) -> dict[str, dict]:
    """Je Raum: Wie gut war das Überschwingen vorhergesagt? (vorhergesagt beim Eingriff – im Trockenlauf ohne, bei
    Ventile führen mit ihm –, eine Stunde später gemessen). ``ok``: oft genug und im Mittel nah genug dran."""
    devs: dict[str, list[float]] = {}
    for r in rows:
        if r["rule"] == "drosseln" and r.get("pred_over_k") is not None and r.get("act_over_k") is not None:
            devs.setdefault(r["room"], []).append(abs(r["pred_over_k"] - r["act_over_k"]))
    return {c: {"n": len(d), "mae_k": _mean(d), "ok": len(d) >= CAL_MIN_N and sum(d) / len(d) <= CAL_MAX_K}
            for c, d in devs.items()}


def _effect(starts: list[dict], checked: dict) -> dict:
    d = [r for r in starts if r["rule"] == "drosseln"]
    g = [r for r in starts if r["rule"] == "gemeinsam"]
    d_rel = [r for r in d if r["reliable"] and r["est_k"] is not None]
    d_ok = [r for r in d_rel if checked.get(r["room"], {}).get("ok")]
    g_rel = [r for r in g if r["reliable"] and r["est_starts"] is not None]
    pairs = sum(v["n"] for v in checked.values())
    mae = round(sum(v["mae_k"] * v["n"] for v in checked.values()) / pairs, 2) if pairs else None
    return {
        "drosseln": {"n": len(d), "reliable": len(d_rel), "checked": len(d_ok),
                     "est_k_mean": _mean([r["est_k"] for r in d_ok]),
                     "est_k_sum": round(sum(r["est_k"] for r in d_ok), 2) if d_ok else None,
                     # gemessen ohne Eingriff (Trockenlauf) bzw. mit ihm (geschrieben, 0.40)
                     "act_over_k_mean": _mean([r["act_over_k"] for r in d
                                               if r["act_over_k"] is not None and not r.get("written")]),
                     "act_over_k_written_mean": _mean([r["act_over_k"] for r in d
                                                       if r["act_over_k"] is not None and r.get("written")]),
                     "check": {"n": pairs, "mae_k": mae, "min_n": CAL_MIN_N, "max_k": CAL_MAX_K}},
        "gemeinsam": {"n": len(g), "reliable": len(g_rel),
                      "starts_saved_est": round(sum(r["est_starts"] for r in g_rel), 1) if g_rel else None,
                      "starts_h_mean": _mean([r["starts_h"] for r in g if r["starts_h"] is not None])},
    }


def _head(s: dict) -> str:
    """Anfang des Satzes: wie oft, Schreibvorgänge – „hätte“ im Trockenlauf, „hat“ für Geschriebenes (0.40)."""
    n, days, written = s["interventions"], s["window_days"], s.get("written", 0)
    if not s["writes"]:
        verb = "hat" if s.get("live") else "hätte"
        return f"Noch kein Eingriff – in {days} Tagen {verb} HeatPilot an den Thermostaten nichts verstellt"
    if not written:
        return (f"In {days} Tagen hätte HeatPilot {n['drosseln']}× früh gedrosselt und {n['gemeinsam']}× Räume "
                f"gemeinsam anfordern lassen – {s['writes']} Schreibvorgänge, nichts davon geschrieben")
    w = s["interventions_written"]
    out = (f"In {days} Tagen hat HeatPilot {w['drosseln']}× früh gedrosselt und {w['gemeinsam']}× Räume gemeinsam "
           f"anfordern lassen – {written} Schreibvorgänge")
    if s["writes"] > written:
        out += (f"; beobachtet dazu {n['drosseln'] - w['drosseln']}× früh drosseln und "
                f"{n['gemeinsam'] - w['gemeinsam']}× gemeinsam anfordern – {s['writes'] - written} Schreibvorgänge, "
                "nicht geschrieben")
    return out


def text(s: dict) -> str:
    """Ein Satz: „In 7 Tagen hätte HeatPilot 12× früh gedrosselt und 5× Räume gemeinsam anfordern lassen – 34
    Schreibvorgänge, nichts davon geschrieben; geschätzt …“ – mit Ventile führen (0.40) „hat HeatPilot …“."""
    out = _head(s)
    if not s["writes"]:
        return out
    d, g = s["effect"]["drosseln"], s["effect"]["gemeinsam"]
    if d["est_k_mean"] is not None:
        out += f"; geschätzt ≈ {_de(d['est_k_mean'])} K weniger Überschwingen je Drosseln ({d['checked']} belastbar)"
    elif d["reliable"]:
        ck = d["check"]
        so_far = (f"{ck['n']} von {CAL_MIN_N} Nachmessungen" if ck["n"] < CAL_MIN_N or ck["mae_k"] is None
                  else f"bisher im Mittel {ck['mae_k']:.2f} K daneben, höchstens {_de(CAL_MAX_K)} K".replace(".", ","))
        out += (f"; Schätzung des Überschwingens erst, wenn die Vorhersage{'' if s.get('written') else ' ohne Eingriff'} "
                f"nachgemessen stimmt ({so_far})")
    if g["starts_saved_est"]:
        out += f"; ≈ {g['starts_saved_est']:.0f} Brennerstarts weniger"
    if s["counted_only"]:
        out += f"; {s['counted_only']} nur gezählt (Modelle lernen noch)"
    return out


def summary(eng, now_ts: int, days: int = DAYS, live: dict | None = None) -> dict:
    """Auswertung für ``GET api/preview`` (Schlüssel ``valves``): Zahl der Eingriffe und Schreibvorgänge, je Raum und
    Tag, Wirkung (geschätzt nur mit belastbaren und nachgeprüften Modellen, dazu das gemessene Überschwingen),
    Beispiele, laufende. ``live`` (0.40, von ``valve_control.summary``): {"on": Ventile führen wirkt, "active":
    laufende echte Eingriffe} – dann „schreibt“ statt „würde schreiben“."""
    names = {r.climate: r.name for r in eng.found.rooms}
    since = midnight(local(now_ts) - timedelta(days=days - 1))
    rows = eng.store.valve_dry_log(since)
    cut = valve_since.get(eng.store)  # Nachprüfung nur fürs heutige Ventil (0.44: Ventil neu lernen)
    checked = checked_rooms([r for r in eng.store.valve_dry_log(min(since, now_ts - CAL_DAYS * 86400))
                             if r["ts"] >= cut.get(r["room"], 0)])
    starts = [r for r in rows if r["action"] != "zurück"]
    on = bool(live and live.get("on"))
    out = {
        "dry_run": not on, "live": on, "note": NOTE_LIVE if on else NOTE, "window_days": days, "writes": len(rows),
        "written": sum(1 for r in rows if r.get("written")),
        "interventions": {k: sum(1 for r in starts if r["rule"] == k) for k in RULES},
        "interventions_written": {k: sum(1 for r in starts if r["rule"] == k and r.get("written")) for k in RULES},
        "writes_by": {k: sum(1 for r in rows if r["rule"] == k) for k in RULES},
        "counted_only": sum(1 for r in starts if not r["reliable"]),
        "managed": sum(1 for r in starts if r["managed"]),
        "effect": _effect(starts, checked), **_tables(rows, names),
        "examples": [_example(r, names, checked) for r in reversed(starts[-EXAMPLES:])],
        "active": [{"climate": c, "name": names.get(c, c), "rule": a["rule"], "setpoint": a["sp"], "base": a["base"],
                    "since": local(a["since"]).isoformat(), "written": False}
                   for c, a in _state(eng)["active"].items()] + list((live or {}).get("active") or []),
        "rules": {"throttle_eta_min": THROTTLE_ETA_MIN, "throttle_valve_pct": THROTTLE_VALVE_PCT,
                  "throttle_max_min": THROTTLE_MAX_MIN, "overshoot_k": OVERSHOOT_K, "approach_k": APPROACH_K,
                  "rise_kh": RISE_KH, "rearm_k": REARM_K, "join_ahead_min": JOIN_AHEAD_MIN,
                  "join_max_min": JOIN_MAX_MIN, "cycle_starts_h": CYCLE_STARTS_H, "cycle_run_min": CYCLE_RUN_MIN,
                  "hold_min": HOLD_S // 60, "check_n": CAL_MIN_N, "check_k": CAL_MAX_K},
    }
    out["text"] = text(out)
    return out


def _tables(rows: list[dict], names: dict) -> dict:
    """Je Raum und je Tag: Eingriffe je Regel und Schreibvorgänge (mit dem Zurücksetzen; ``written``: davon wirklich
    geschrieben, 0.40)."""
    rooms: dict[str, dict] = {}
    days_out: dict[str, dict] = {}
    for r in rows:
        room = rooms.setdefault(r["room"], {"climate": r["room"], "name": names.get(r["room"], r["room"]),
                                            "drosseln": 0, "gemeinsam": 0, "writes": 0, "written": 0})
        day = days_out.setdefault(local(r["ts"]).date().isoformat(),
                                  {"drosseln": 0, "gemeinsam": 0, "writes": 0, "written": 0})
        for x in (room, day):
            x["writes"] += 1
            x["written"] += bool(r.get("written"))
        if r["action"] != "zurück":
            room[r["rule"]] += 1
            day[r["rule"]] += 1
    return {"rooms": sorted(rooms.values(), key=lambda x: (-x["writes"], x["name"])),
            "days": [{"day": d, **v} for d, v in sorted(days_out.items())]}


def _example(r: dict, names: dict, checked: dict) -> dict:
    return {"t": local(r["ts"]).isoformat(), "climate": r["room"], "name": names.get(r["room"], r["room"]),
            "rule": r["rule"], "action": r["action"], "setpoint": r["setpoint"], "base": r["base"],
            "temp": r["temp"], "valve": r["valve"], "reason": r["reason"], "reliable": bool(r["reliable"]),
            "managed": bool(r["managed"]), "est_k": r["est_k"], "est_starts": r["est_starts"],
            "pred_over_k": r["pred_over_k"], "est_checked": bool(checked.get(r["room"], {}).get("ok")),
            "act_over_k": r["act_over_k"], "act_starts": r["act_starts"], "written": bool(r.get("written"))}
