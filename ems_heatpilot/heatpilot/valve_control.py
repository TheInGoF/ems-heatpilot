"""Ventile führen (seit 0.40): Stufe 2 von „Thermostate als Stellglied“ – HeatPilot verschiebt das Raum-Soll um
höchstens einen Schritt, damit der eingebaute Regler des Thermostats sein Ventil früh drosselt bzw. damit Räume
gemeinsam anfordern.

Eigener Schalter ``valve_control`` (Standard aus; aus = nur der Trockenlauf ``valve_dry`` wie in 0.39). Wirkt nur bei
„HeatPilot steuert“ und nur für Räume mit „macht mit“, plausiblem Raummodell, belastbarem Ventilmodell und einem
Schritt bis 0,5 K (``eligible``); nie für den Heizkreis von EMS-ESP. Diese Datei schreibt nicht selbst:
``ControlLoop.plan`` holt sich je Raum das Soll (``Guide.plan``) und schreibt es über den bestehenden Weg
(``Control.apply`` → ``climate.set_temperature`` auf die Schreibziele des Raums, mit Raumregler nur auf ihn). Die
Regeln sind die des Trockenlaufs (``valve_dry``: echte Annäherung, Raum- und Ventilmodell gemeinsam gerechnet,
Taktung des Kessels, „würde ohnehin heizen“), bezogen auf das Wunschsoll der Steuerung und strenger:

* **früh drosseln** (ein Schritt tiefer): nur in den Phasen komfort und vorheizen – nie nacht, abwesend, urlaub (dort
  ist das Soll ohnehin tief) –, nur zwischen Soll − 0,3 K und dem Soll und nur mit der Vorhersage beider Modelle.
  Zurück, sobald der Raum am Soll ist, spätestens nach 60 min – und sofort, auch in der Haltezeit, wenn er unter
  Soll − 0,3 K fällt: Wer Wärme braucht, bekommt sie. Danach erst wieder nach neuem Soll oder 0,5 K Auskühlen.
* **gemeinsam anfordern** (ein Schritt höher): Räume, die laut Raummodell in den nächsten 60 min ohnehin heizen
  würden, solange nur ein Raum anfordert und der Kessel taktet; zurück, wenn der Auslöser endet, spätestens nach
  60 min. Der anfordernde Raum wartet auf nichts – gezählt wird nach Räumen. Keine Kette und kein Pendeln: Auslösen
  darf kein Raum, den HeatPilot gerade drosselt oder in den letzten 60 min angehoben hat (sein Ventil steht dann
  seinetwegen offen); angehoben wird kein Raum, der nach dem Drosseln gesperrt ist oder in den letzten 60 min schon
  angehoben war (``raised``).

Immer höchstens ein Schritt (``valve_learn.step_of``, Tado 0,5 K) vom Wunschsoll, auf den Schritt gerundet, 5–25 °C;
nur bei Änderung, 5 min Haltezeit je Raum nach jedem Schreibvorgang (``dhw_rule.HOLD_S``). Phasenwechsel der Steuerung
gehen vor (das neue Wunschsoll gilt sofort, die Führung beginnt neu), Hand auch: Ändert jemand das Soll (App, Ventil,
Boost, aus), fasst die Führung den Raum bis zum nächsten Phasenwechsel nicht an. Schalter aus oder Modell nicht mehr
belastbar: sofort zurück aufs Wunschsoll. Fehler wie die übrige Raumsteuerung (erneut nach 15 min, Meldung ab drei).
Das Zurück aufs Wunschsoll geht auch in der Pause hinaus (neue Schritte nicht); vor einer Pause, „Beobachten“ oder
„macht mit“ aus nimmt die Regelschleife laufende Schritte einmal zurück (``give_back``, ``ControlLoop.release``).

Erst ein erfolgreicher Schreibvorgang ändert den Zustand (``Guide.wrote``): Er steht im Schreibprotokoll
(``control_log``, Grund „Ventil: früh drosseln (…)“, „Ventil: gemeinsam anfordern (…)“, „Ventil: zurück (…)“) und als
geschriebener Eintrag im Ventil-Protokoll (``valve_dry_log``, ``written`` = 1); eine Stunde danach trägt
``valve_dry.fill_actuals`` nach, was wirklich geschah. Dazu die Anzeige: Bereitschaft (``readiness``), laufende
Eingriffe (``running``) und die Auswertung (``summary``).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import NamedTuple

from . import guards, valve_learn
from . import valve_dry as vd
from .dhw_rule import HOLD_S
from .ems_lock import LOCK_TEXT
from .values import local
from .valve_text import de_num

KV_STATE = "valve_control.state"
BELOW_K = 0.3                               # früh drosseln nur ab Soll − 0,3 K – darunter braucht der Raum Wärme
MAX_STEP_K = 0.5                            # gröbere Schritte führt HeatPilot nicht (ein Schritt wäre schon auf/zu)
THROTTLE_PHASES = ("komfort", "vorheizen")  # nie nacht, abwesend, urlaub – dort ist das Soll ohnehin tief
RULE_TEXT = {"drosseln": "früh drosseln", "gemeinsam": "gemeinsam anfordern"}
PREFIX = "Ventil: "                         # Grund im Schreibprotokoll
KEYS = ("active", "last", "lock", "hands", "raised")
NOTE_RULES = (f"Höchstens ein Schritt vom Ziel (Schrittweite des Thermostats, höchstens {de_num(MAX_STEP_K)} K), "
              f"nur bei Änderung, {HOLD_S // 60} min Haltezeit – Hand und Phasenwechsel gehen vor, wer Wärme braucht, "
              "bekommt sie sofort")


class Want(NamedTuple):
    """Was die Führung für einen Raum will: das Soll jetzt; Grund und Vorschlag nur, wenn sie schreiben will."""

    sp: float
    reason: str | None = None
    prop: dict | None = None


def _iso(ts) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts).astimezone().isoformat()


def one_step(base: float, step: float, sign: int) -> float | None:
    """Ein Schritt über (``sign`` +1) bzw. unter (−1) dem Wunschsoll, auf den Schritt gerundet, 5–25 °C – None, wenn
    das nicht höchstens einen Schritt weg und nicht gleich dem Wunschsoll ist."""
    sp = vd._grid(base + sign * step, step)
    return sp if 0 < sign * (sp - base) <= step + 1e-6 else None


def eligible(eng, room, ts: int) -> tuple[bool, str]:
    """Kann HeatPilot das Ventil dieses Raums führen? (ja/nein, Grund) – unabhängig vom Schalter."""
    if room is None:
        return False, "unbekannter Raum"
    if room.ems:
        return False, LOCK_TEXT
    if not room.managed:
        return False, "macht nicht mit"
    model = eng.rooms.get(room.climate)
    check = guards.check_room(model) if model is not None else {"ok": False, "reasons": ["noch kein Modell"]}
    if not check["ok"]:
        return False, f"Raummodell lernt noch ({(check['reasons'] or ['unplausibel'])[0]})"
    vm = eng.valves.get(room.climate)
    if vm is None:
        return False, "kein Ventilmodell"
    ok, reasons = vm.usable(local(ts).date().isoformat(), valve_learn.behind(vm, eng._last_room_step),
                            valve_learn.group_of(room))
    if not ok:
        return False, f"Ventilmodell lernt noch ({reasons[0]})" if reasons else "Ventilmodell lernt noch"
    step, _ = valve_learn.step_of(room, eng.states)
    if step > MAX_STEP_K + 1e-9:
        return False, f"Schritt {de_num(step)} K – zu grob zum Führen (höchstens {de_num(MAX_STEP_K)} K)"
    return True, f"Raum- und Ventilmodell belastbar, Schritt {de_num(step)} K"


def _throttle_end(a: dict, r, ts: int) -> tuple[str, bool] | None:
    """Laufendes Drosseln: (Grund, sofort?) fürs Zurück oder None. Sofort – auch in der Haltezeit –, wenn der Raum
    Wärme braucht oder seine Temperatur fehlt."""
    if r is None or r.temp is None:
        return "Raumtemperatur unbekannt", True
    if r.temp < a["base"] - BELOW_K - 1e-9:
        return "Raum braucht Wärme", True
    if r.temp >= a["base"] - 1e-6:
        return "Soll erreicht", False
    if ts - a["since"] >= vd.THROTTLE_MAX_MIN * 60:
        return f"nach {vd.THROTTLE_MAX_MIN} min", False
    return None


class Guide:
    """Zustand der Ventilführung (überlebt Neustarts, kv): laufende Eingriffe (``active``), letzte Schreibzeit je Raum
    (``last``, Haltezeit), Sperre nach dem Drosseln (``lock``), „Hand“ bis zum nächsten Phasenwechsel (``hands``) und
    zuletzt angehoben (``raised``: Zeit, bis 60 min danach – keine Kette, kein Pendeln)."""

    def __init__(self, eng) -> None:
        self.eng = eng
        saved = eng.store.get(KV_STATE) or {}
        self.st: dict[str, dict] = {k: dict(saved.get(k) or {}) for k in KEYS}
        self.props: dict[str, dict] = {}  # Vorschläge des letzten plan() – gültig erst mit wrote()
        self.guided: set[str] = set()     # Räume, die HeatPilot gerade führt (Schalter an, belastbar)
        self.why: dict[str, str] = {}     # je Raum: warum (nicht) geführt
        self.dropped: set[str] = set()    # Eingriffe, die dieser Takt wegen Hand verworfen hat (ohne Zurückschreiben)

    def save(self) -> None:
        self.eng.store.put(KV_STATE, self.st)

    def clear(self) -> None:
        """Steuerung auf „Beobachten“: Zustand vergessen, wie die Regelschleife."""
        self.props, self.guided = {}, set()
        if any(self.st.values()):
            self.st = {k: {} for k in KEYS}
            self.save()

    # ------------------------------------------------------------------ Takt
    def plan(self, ts: int, sample: dict, readings: list[tuple], ctx: dict[str, dict]) -> dict[str, Want]:
        """Soll je Raum mit „macht mit“ – schreibt nichts. ``ctx``: climate → {base (Wunschsoll), phase, reset
        (Phasenwechsel), hands (Hand, Boost, aus), settled (steht auf dem zuletzt Geschriebenen)}."""
        eng, on = self.eng, self.eng.cfg.guides_valves()
        self.props, self.guided, self.dropped = {}, set(), set()
        self.st["last"] = {c: t for c, t in self.st["last"].items() if ts - t < 2 * HOLD_S}
        if not on and not self.st["active"]:  # Schalter aus, nichts zurückzuschreiben: wie 0.39, nichts zu rechnen
            self.why = {}  # die Schreibzeiten bleiben: die Haltezeit gilt auch gleich nach dem Einschalten
            if self.st["lock"] or self.st["hands"] or self.st["raised"]:
                self.st["lock"], self.st["hands"], self.st["raised"] = {}, {}, {}
                self.save()
            return {}
        self._mark_raised(ts)
        by = {r.climate: r for r in vd._rooms(eng, {r[0]: r for r in readings}, ts)}
        for c, x in ctx.items():
            self._settle(c, x)
            ok, self.why[c] = eligible(eng, eng.found.room_of(c), ts)
            if on and ok:
                self.guided.add(c)
            if c in by:  # Bezug ist das Wunschsoll der Steuerung, nicht das Soll am Thermostat
                by[c] = replace(by[c], target=x["base"])
        vd._unlock(self.st["lock"], {c: by[c] for c in self.guided if c in by})
        ends = self._ends(on, ctx, by, ts)
        wishes = {c: {"rule": a["rule"], "sp": a["sp"]} for c, a in self.st["active"].items()
                  if c not in ends and a["rule"] == "drosseln"}
        free = {c for c in self.guided if c in by and c not in self.st["active"] and c not in self.st["hands"]
                and ctx[c]["settled"] and not ctx[c]["reset"]}
        for c in sorted(free):
            w = self._throttle(by[c], ctx[c], sample, ts)
            if w is not None:
                wishes[c] = w
        self._join(by, ends, wishes, free, sample, ts)
        wants = self._gate(ctx, by, wishes, ends, ts)
        self.save()
        return wants

    def _mark_raised(self, ts: int) -> None:
        """Angehobene Räume merken (laufend: jetzt), bis ``JOIN_MAX_MIN`` nach dem letzten Takt mit Anheben – auch wenn
        Hand oder Phasenwechsel das Anheben beenden; das Ventil steht danach noch eine Weile seinetwegen offen."""
        for c, a in self.st["active"].items():
            if a["rule"] == "gemeinsam":
                self.st["raised"][c] = int(ts)
        self.st["raised"] = {c: t for c, t in self.st["raised"].items() if ts - t < vd.JOIN_MAX_MIN * 60}

    def _settle(self, c: str, x: dict) -> None:
        """Phasenwechsel: die Führung beginnt neu. Hand, Boost, aus: Eingriff ohne Schreiben vergessen (``dropped`` –
        die Regelschleife nimmt dann das Wunschsoll als zuletzt geschrieben), den Raum bis zum nächsten Phasenwechsel
        nicht anfassen."""
        if x["reset"]:
            for k in ("active", "lock", "hands"):
                self.st[k].pop(c, None)
        if x["hands"]:
            if self.st["active"].pop(c, None) is not None:
                self.dropped.add(c)
            self.st["hands"][c] = x["phase"]

    def _ends(self, on: bool, ctx: dict, by: dict, ts: int) -> dict[str, tuple[str, bool]]:
        """Laufende Eingriffe, die enden: climate → (Grund, sofort?). Raum macht nicht mehr mit oder hat ein neues
        Wunschsoll: vergessen – die Regelschleife schreibt das Wunschsoll mit ihrem Grund."""
        ends = {}
        for c, a in list(self.st["active"].items()):
            x = ctx.get(c)
            if x is None or abs(a["base"] - x["base"]) >= 0.05:
                del self.st["active"][c]
            elif not on:
                ends[c] = ("Ventile führen aus", True)
            elif c not in self.guided:
                ends[c] = (self.why.get(c) or "nicht mehr belastbar", True)
            elif a["rule"] == "drosseln" and (e := _throttle_end(a, by.get(c), ts)) is not None:
                ends[c] = e
        return ends

    def _throttle(self, r, x: dict, sample: dict, ts: int) -> dict | None:
        """Früh drosseln beginnen? Die Regel des Trockenlaufs mit belastbarer Vorhersage, dazu Phase, Abstand zum Soll
        und genau ein Schritt. Gemerkt wird das vorhergesagte Überschwingen mit dem Eingriff (das wird nachgemessen)."""
        base = x["base"]
        if (x["phase"] not in THROTTLE_PHASES or r.temp is None or r.temp < base - BELOW_K - 1e-9
                or r.temp >= base or r.climate in self.st["lock"]):
            return None
        w = vd._throttle(self.eng, r, sample, None, ts, False)
        sp = one_step(base, r.step, -1)
        if w is None or not w["reliable"] or sp is None or abs(w["sp"] - sp) > 1e-6:
            return None
        sim = vd._sim(r, sample, base - sp)
        w["pred_over_k"] = None if sim is None else round(sim["over60"], 2)
        w["back_at"] = int(ts + max(min(w["eta_min"], vd.THROTTLE_MAX_MIN) * 60, HOLD_S))
        return w

    def _join(self, by: dict, ends: dict, wishes: dict, free: set, sample: dict, ts: int) -> None:
        """Gemeinsam anfordern: die Regel des Trockenlaufs – angehoben werden nur Räume, die HeatPilot gerade frei
        führen darf, nicht nach dem Drosseln gesperrt sind (``lock``) und nicht in den letzten 60 min angehoben waren,
        nur mit Außentemperatur (laut Raummodell). Auslösen darf kein Raum, den HeatPilot gerade drosselt oder in den
        letzten 60 min angehoben hat (sein Ventil steht dann seinetwegen offen bzw. schließt gleich – keine Kette, kein
        Pendeln); laufende ohne Auslöser enden."""
        act = {c: a for c, a in self.st["active"].items() if c not in ends}
        raised = set(self.st["raised"]) | {c for c, a in self.st["active"].items() if a["rule"] == "gemeinsam"}
        throttled = {c for c, a in self.st["active"].items() if a["rule"] == "drosseln"}
        cand = free - raised - set(self.st["lock"]) if sample.get("t_out") is not None else set()
        vd._join(self.eng, list(by.values()), act, wishes, sample, ts, cand, set(by) - raised - throttled)
        for c, w in list(wishes.items()):  # neue: genau ein Schritt über dem Wunschsoll, belastbar
            if w["rule"] == "gemeinsam" and c not in act:
                sp = one_step(by[c].target, by[c].step, +1)
                if sp is None or abs(w["sp"] - sp) > 1e-6 or not w.get("reliable"):
                    del wishes[c]
                else:
                    w["back_at"] = ts + vd.JOIN_MAX_MIN * 60
        for c, a in act.items():  # laufende, die die Regel nicht weiterführt: Zeit um oder Auslöser zu Ende
            if a["rule"] == "gemeinsam" and c not in wishes:
                lead = self.eng.found.room_of(a.get("for") or "")
                ends[c] = (f"nach {vd.JOIN_MAX_MIN} min" if ts - a["since"] >= vd.JOIN_MAX_MIN * 60
                           else f"{lead.name if lead else 'der anfordernde Raum'} fordert nicht mehr an", False)

    def _gate(self, ctx: dict, by: dict, wishes: dict, ends: dict, ts: int) -> dict[str, Want]:
        """Schreibregeln: nur bei Änderung, Haltezeit je Raum – außer beim sofortigen Zurück."""
        wants = {}
        for c, x in ctx.items():
            cur, w, end = self.st["active"].get(c), wishes.get(c), ends.get(c)
            hold = ts - self.st["last"].get(c, ts - HOLD_S) < HOLD_S
            if end is not None:
                if hold and not end[1]:
                    wants[c] = Want(cur["sp"])
                else:
                    back = {"rule": cur["rule"], "sp": x["base"], "reason": f"zurück ({end[0]})"}
                    wants[c] = self._propose(c, by.get(c), back, x["base"], "zurück", ts)
            elif cur is not None:
                wants[c] = Want(cur["sp"])  # läuft unverändert weiter: nichts schreiben
            elif w is not None and not hold:
                wants[c] = self._propose(c, by[c], w, x["base"], "senken" if w["sp"] < x["base"] else "anheben", ts)
        return wants

    def _propose(self, c: str, r, w: dict, base: float, action: str, ts: int) -> Want:
        if r is None:  # ohne Messwert (Thermostat fehlt): Eintrag ohne Temperatur und Ventil
            room = self.eng.found.room_of(c)
            r = vd._Room(c, room.name if room else c, True, None, base, None, valve_learn.DEFAULT_STEP_K,
                         None, None, False)
        text = w["reason"] if action == "zurück" else f"{RULE_TEXT[w['rule']]} ({w['reason']})"
        prop = {"climate": c, "action": action, "rule": w["rule"], "sp": w["sp"], "base": base,
                "step": r.step if action != "zurück" else 0.5, "for": w.get("for"), "reason": w["reason"],
                "back_at": w.get("back_at"), "entry": {**vd._entry(ts, r, w, base, action), "written": 1}}
        self.props[c] = prop
        return Want(w["sp"], PREFIX + text, prop)

    def wrote(self, climate: str, ts: int, prop: dict | None = None, save: bool = True) -> None:
        """Nach jedem erfolgreichen Schreibvorgang auf den Raum (auch Phasenwechsel): die Haltezeit beginnt. Mit
        Vorschlag der Führung wird er gültig und kommt als geschriebener Eintrag ins Ventil-Protokoll. ``save`` False:
        die Schleife ist schon ersetzt – den Zustand der neuen im kv nicht überschreiben."""
        self.st["last"][climate] = int(ts)
        if prop is not None:
            if prop["rule"] == "gemeinsam":
                self.st["raised"][climate] = int(ts)
            if prop["action"] == "zurück":
                a = self.st["active"].pop(climate, None)
                if a is not None and a["rule"] == "drosseln":
                    self.st["lock"][climate] = a["base"]
            else:
                self.st["active"][climate] = {"rule": prop["rule"], "sp": prop["sp"], "base": prop["base"],
                                              "since": int(ts), "for": prop["for"], "reason": prop["reason"],
                                              "back_at": prop["back_at"]}
            self.eng.store.add_valve_dry(prop["entry"])
        if save:
            self.save()

    def give_back(self, climates: set[str], why: str, ts: int) -> dict[str, Want]:
        """Laufende Eingriffe dieser Räume einmal zurück aufs Wunschsoll (vor Pause, „Beobachten“, „macht mit“ aus –
        ``ControlLoop.release``): je Raum der Vorschlag „zurück (…)“; erst ``wrote`` macht ihn gültig."""
        out = {}
        for c in sorted(climates & set(self.st["active"])):
            a = self.st["active"][c]
            out[c] = self._propose(c, None, {"rule": a["rule"], "sp": a["base"], "reason": f"zurück ({why})"},
                                   a["base"], "zurück", ts)
        return out

    # ------------------------------------------------------------------ Anzeige
    def room_info(self, climate: str) -> dict | None:
        """Laufender Eingriff eines Raums für den Raum-Dialog („Ventil geführt: 20,5 statt 21 °C …“), sonst None."""
        a = self.st["active"].get(climate)
        if a is None:
            return None
        return {"rule": a["rule"], "rule_text": RULE_TEXT[a["rule"]], "setpoint": a["sp"], "base": a["base"],
                "since": _iso(a["since"]), "back_at": _iso(a.get("back_at")), "back_latest": a["rule"] == "gemeinsam",
                "reason": a.get("reason")}


def _guide(eng) -> Guide | None:
    return getattr(getattr(eng, "control_loop", None), "guide", None)


def running(eng) -> list[dict]:
    """Laufende echte Eingriffe (geschrieben) – für die Auswertung und „Bereit für die Steuerung?“."""
    g = _guide(eng)
    active = g.st["active"] if g is not None else ((eng.store.get(KV_STATE) or {}).get("active") or {})
    names = {r.climate: r.name for r in eng.found.rooms}
    return [{"climate": c, "name": names.get(c, c), "rule": a["rule"], "setpoint": a["sp"], "base": a["base"],
             "since": _iso(a["since"]), "back_at": _iso(a.get("back_at")), "written": True}
            for c, a in active.items()]


def summary(eng, now_ts: int) -> dict:
    """Auswertung der Ventile (``api/preview`` → ``valves``): ``valve_dry.summary`` mit „schreibt“, wenn die Führung
    wirkt, und den laufenden echten Eingriffen."""
    out = vd.summary(eng, now_ts, live={"on": eng.cfg.guides_valves(), "active": running(eng)})
    out["rules"].update({"below_k": BELOW_K, "max_step_k": MAX_STEP_K, "throttle_phases": list(THROTTLE_PHASES)})
    return out


def readiness(eng, now_ts: int) -> dict:
    """Block „Ventile führen“ in „Bereit für die Steuerung?“: welche Räume HeatPilot führen kann, welche noch lernen
    (mit Grund), Schalter und laufende Eingriffe. Nur Anzeige – ändert die Stufe der Raumsteuerung nicht."""
    cfg = eng.cfg
    switch, managing = cfg.valve_control is True, cfg.is_managing()
    rooms = []
    for room in eng.found.rooms:
        if room.managed and not room.ems:
            ok, why = eligible(eng, room, now_ts)
            rooms.append({"climate": room.climate, "name": room.name, "state": "bereit" if ok else "lernt",
                          "text": why})
    ready = [r["name"] for r in rooms if r["state"] == "bereit"]
    learning = [r["name"] for r in rooms if r["state"] == "lernt"]
    level = "nicht bereit" if not ready else ("eingeschränkt" if learning else "bereit")
    return {"switch": switch, "active": switch and managing, "level": level,
            "headline": _headline(switch, managing, rooms, ready, learning), "rooms": rooms,
            "counts": {"managed": len(rooms), "ready": len(ready), "learning": len(learning)},
            "running": running(eng),
            "notes": [NOTE_RULES]}


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} und {names[-1]}"


def _headline(switch: bool, managing: bool, rooms: list, ready: list[str], learning: list[str]) -> str:
    if not rooms:
        return "Nicht bereit – kein Raum macht mit."
    if not ready:
        return (f"Nicht bereit – noch kein Raum mit belastbarem Raum- und Ventilmodell "
                f"({len(learning)} {'lernt' if len(learning) == 1 else 'lernen'} noch).")
    rest = "" if not learning else f"; {_names(learning)} {'lernt' if len(learning) == 1 else 'lernen'} noch"
    rooms_word = f"{len(ready)} {'Raum' if len(ready) == 1 else 'Räume'}"
    if switch and managing:
        return f"Läuft – führt {rooms_word} ({_names(ready)}){rest}."
    if switch:
        return f"Eingeschaltet, wirkt erst mit „HeatPilot steuert“ – {rooms_word} wären bereit{rest}."
    return (f"Aus – {rooms_word} wären bereit ({_names(ready)}){rest}; bis zum Einschalten nur Beobachten "
            "(Einstellungen → Räume).")
