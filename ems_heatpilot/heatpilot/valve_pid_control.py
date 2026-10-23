"""Ventilgeführte Räume (seit 0.51): HeatPilot stellt die Ventilöffnung selbst – Ablauf im 30-s-Takt.

Für Räume mit Führung „ventilgeführt“ (``room_lead``, z. B. Shelly BLU TRV) läuft je 30-s-Schritt der Live-Werte
(``main.valve_pid_step``, wie Weg B):

1. **Fremde Hand** erkennen: Zeigt das Ventil einen anderen Wert als zuletzt geschrieben (ab ``HAND_PCT``), ohne dass
   HeatPilot gerade geschrieben hat, wartet der Raum ``HAND_HOLD_S`` – wer von Hand dreht, behält das Ventil so lange.
2. **Prüfung** 60 s nach dem Schreiben: Zeigt die Entität den Wert (±1 %)? Nach ``MAX_FAILS`` Fehlschlägen in Folge
   pausiert der Raum ``FAIL_HOLD_S`` (Meldung), dann geht es von selbst weiter.
3. **PID-Schritt** (``valve_pid.step``): Wunsch-Soll und Phase kommen aus der Regelschleife (``controlloop``), die
   Raumtemperatur aus dem Raum (Zusatzsensor bzw. Thermostat), der Vorlauf vom Kessel. Der Öffnungs-Deckel kommt vom
   automatischen Abgleich (``balance_auto``). Ohne Raumtemperatur stellt HeatPilot nach ``valve_pid.BLIND_S`` einmal
   den sicheren Wert ``FALLBACK_PCT`` (kein Frost, kein Überheizen) und wartet.
4. **Schreiben** nur über ``Control.set_valve`` (harte Schutzregeln in ``room_lead.check_valve_call``), nach den
   Schreibregeln ``valve_pid.decide``; die Nutzer-Pause der Steuerung hält auch diese Schiene an.

Die Soll-Schiene fasst solche Räume nicht an (``controlloop`` überspringt sie); gelernt wird der Raum weiter wie
bisher – das gestellte Prozent kommt als Ventilwert in ``room_samples`` an (``engine_sample``). Zustand je Raum
{pid, last, check, fails, hold_until, pause_until, why, want} und der Abgleich (``bal``) im kv ``valve_pid.state``.
"""

from __future__ import annotations

import json
import logging
import time

from . import balance_auto, guards, room_lead, valve_pid
from .dhw_rule import CHECK_MAX_S, CHECK_S, MAX_FAILS
from .values import num

log = logging.getLogger(__name__)

KV = "valve_pid.state"
HAND_PCT = 5        # fremde Hand: Ventil weicht ab so vielen Prozentpunkten vom zuletzt Geschriebenen ab …
HAND_HOLD_S = 3600  # … dann wartet der Raum so lange
HAND_GRACE_S = 120  # so lange nach dem eigenen Schreiben zählt eine Abweichung nicht als fremd
FAIL_HOLD_S = 3600  # nach ``MAX_FAILS`` Fehlschlägen in Folge: Raum so lange pausieren, dann von selbst weiter
K_FAIL, K_HAND = "valve_pid_fail", "valve_pid_hand"
ACT_SET, ACT_FAIL = "valve_set", "valve_check_failed"


class ValvePidControl:
    """Zustand und Schritt der ventilgeführten Räume (siehe Modulbeschreibung)."""

    def __init__(self, eng, control=None, notifier=None, carry: "ValvePidControl | None" = None) -> None:
        self.eng, self.control, self.notifier = eng, control, notifier
        self.store = eng.store
        self.state: dict = carry.state if carry is not None else dict(self.store.get(KV) or {})
        self.pending: list[tuple] = []
        self.view: dict = {}
        self.stopped = False
        self._saved: str | None = None

    def stop(self) -> None:
        """Außer Dienst (main.build): vorgemerkte Schreibvorgänge werden verworfen."""
        self.stopped = True

    def rooms(self) -> list:
        """Die Räume, die HeatPilot über das Ventil führt (machen mit, Führung „ventil“, Entität gesetzt)."""
        return [r for r in self.eng.found.rooms if r.managed and room_lead.valve_led(r)]

    def current(self, room) -> float | None:
        """Ventilöffnung laut der number-Entität – None, wenn unbekannt."""
        v = num((self.eng.states.get(room.valve_pos) or {}).get("state"))
        return v if v is not None and 0 <= v <= 100 else None

    # -------------------------------------------------------------- Schritt
    def plan(self, now: float | None = None) -> None:
        """Im 30-s-Takt: je Raum Hand, Prüfung, PID, Entscheidung – schreibt nichts, merkt für ``apply`` vor."""
        now = time.time() if now is None else now
        ts, rooms = int(now), self.rooms()
        self.pending = []
        rst = self.state.setdefault("rooms", {})
        for c in set(rst) - {r.climate for r in rooms}:
            rst.pop(c)  # nicht mehr ventilgeführt: Zustand weg, das Ventil bleibt, wie es steht
        if not rooms:
            self.view = {"ts": ts, "rooms": {}}
            self._save()
            return
        loop = self.eng.control_loop
        paused = loop is not None and loop.paused()
        managing = self.eng.cfg.is_managing()
        flow, t_out = num(self.eng._boiler("flow_temp")), self.eng.outdoor_now()
        obs = []
        for room in rooms:
            st = rst.setdefault(room.climate, {})
            self._step_room(room, st, ts, loop, paused, managing, flow, t_out, obs)
        balance_auto.observe(self.state.setdefault("bal", {}), obs, ts)
        self.view = {"ts": ts, "rooms": {r.climate: dict(rst.get(r.climate) or {}) for r in rooms}}
        self._save()

    def _step_room(self, room, st: dict, ts: int, loop, paused: bool, managing: bool, flow, t_out,
                   obs: list) -> None:
        """Ein Raum in ``plan`` – hängt höchstens einen Schreibvorgang an ``pending``."""
        current = self.current(room)
        self._watch(room, st, current, ts)
        self._verify(room, st, current, ts)
        info = (loop.rooms if loop is not None else {}).get(room.climate) or {}
        sp = info.get("desired")
        reading = self.eng.room_reading(room)
        temp = reading[1] if reading else None
        if sp is None or paused or not managing:
            st["why"] = "Pause" if paused else "Steuerung nicht auf „HeatPilot steuert“ bzw. noch kein Wunsch-Soll"
            return
        if temp is None:
            self._blind(room, st, ts, current)
            return
        st.pop("blind", None)
        cap = balance_auto.cap(self.state.get("bal") or {}, room.climate)
        model = self.eng.rooms.get(room.climate)
        ok = model is not None and guards.check_room(model)["ok"]
        target = valve_pid.step(st.setdefault("pid", {}), float(sp), temp, flow, t_out,
                                model if ok else None, ts, hi=cap)
        hold = max(int(st.get("hold_until") or 0), int(st.get("pause_until") or 0)) or None
        d = valve_pid.decide(target, current, ts, (st.get("last") or {}).get("ts"), hold)
        st["why"], st["want"], st["cap"] = d.why, d.value, cap
        obs.append((room.climate, temp, float(sp), target, cap))
        if d.write and not st.get("check"):
            self.pending.append((room, d.value, f"{d.why} · Soll {sp:g} °C ({info.get('phase') or '–'})"))

    def _watch(self, room, st: dict, current: float | None, ts: int) -> None:
        """Fremde Hand: Ventil weicht vom zuletzt Geschriebenen ab, ohne dass HeatPilot gerade geschrieben hat."""
        last = st.get("last")
        if last is None or current is None or st.get("check"):
            return
        fresh = ts - last["ts"] <= HAND_GRACE_S
        if not fresh and abs(current - last["value"]) >= HAND_PCT:
            st["hold_until"] = ts + HAND_HOLD_S
            st.pop("last", None)  # der fremde Wert ist jetzt der Stand – kein zweites Mal melden
            log.info("Ventil %s: von Hand auf %.0f %% gestellt – HeatPilot wartet %d min", room.name, current,
                     HAND_HOLD_S // 60)
            if self.notifier is not None:
                self.notifier.problem(K_HAND, f"{room.name}: Ventil von Hand auf {current:.0f} % gestellt – "
                                              f"HeatPilot wartet {HAND_HOLD_S // 60} min", ts)

    def _verify(self, room, st: dict, current: float | None, ts: int) -> None:
        """Prüfung 60 s nach dem Schreiben: Zeigt die Entität den Wert (±1 %)? Fehlschläge zählen je Raum."""
        chk = st.get("check")
        if not chk or ts < chk["due"]:
            return
        if current is None and ts < chk["until"]:
            return
        st.pop("check")
        if current is not None and abs(current - chk["value"]) <= 1.0:
            st["fails"] = 0
            if self.notifier is not None:
                self.notifier.solved(K_FAIL, f"{room.name}: Ventil wieder in Ordnung – {chk['value']} % übernommen",
                                     ts)
            return
        st["fails"] = int(st.get("fails", 0)) + 1
        shown = "unbekannt" if current is None else f"{current:.0f} %"
        msg = f"{room.name}: Ventil {chk['value']} % nicht übernommen (zeigt {shown})"
        self.store.add_control_log(ts, room.valve_pos, ACT_FAIL, chk["value"],
                                   f"{msg} – Fehlschlag {st['fails']} von {MAX_FAILS}")
        log.warning("Ventil %s – Fehlschlag %d von %d", msg, st["fails"], MAX_FAILS)
        if st["fails"] >= MAX_FAILS:
            st["pause_until"], st["fails"] = ts + FAIL_HOLD_S, 0
            if self.notifier is not None:
                self.notifier.problem(K_FAIL, f"{msg} – {MAX_FAILS}. Fehlschlag in Folge, der Raum pausiert "
                                              f"{FAIL_HOLD_S // 60} min (Gateway/Batterie prüfen)", ts)

    def _blind(self, room, st: dict, ts: int, current: float | None) -> None:
        """Keine Raumtemperatur: nach ``valve_pid.BLIND_S`` einmal den sicheren Wert stellen und warten."""
        since = st.setdefault("blind", ts)
        st["why"] = "keine Raumtemperatur – ohne Messwert stellt HeatPilot nicht"
        if ts - since < valve_pid.BLIND_S or st.get("check"):
            return
        if current is not None and abs(current - valve_pid.FALLBACK_PCT) <= 1.0:
            return
        st["why"] = f"keine Raumtemperatur seit {(ts - since) // 60} min – sicherer Wert {valve_pid.FALLBACK_PCT} %"
        self.pending.append((room, valve_pid.FALLBACK_PCT, st["why"]))

    # -------------------------------------------------------------- Schreiben
    async def apply(self, now: float | None = None) -> int:
        """Vorgemerktes schreiben – nur über ``Control.set_valve``, Zahl der Schreibvorgänge."""
        pending, self.pending = self.pending, []
        if not pending or self.stopped or self.control is None:
            return 0
        now = time.time() if now is None else now
        n, rst = 0, self.state.setdefault("rooms", {})
        for room, value, why in pending:
            st = rst.setdefault(room.climate, {})
            try:
                await self.control.set_valve(room.valve_pos, int(value), f"{room.name}: {why}")
            except PermissionError as err:
                st["why"] = f"abgewiesen: {err}"
                log.info("Ventil %s: %s", room.name, err)
                continue
            except Exception as err:  # noqa: BLE001 – zählt wie „nicht übernommen“, nächster Versuch im Takt
                st["fails"] = int(st.get("fails", 0)) + 1
                st["why"] = f"nicht geschrieben ({err})"
                log.warning("Ventil %s: nicht geschrieben (%s)", room.name, err)
                continue
            st["last"] = {"ts": int(now), "value": int(value)}
            st["check"] = {"due": now + CHECK_S, "until": now + CHECK_MAX_S, "value": int(value)}
            if st.pop("hold_until", None) and self.notifier is not None:
                self.notifier.solved(K_HAND, quiet=True)
            n += 1
        self._save()
        return n

    # -------------------------------------------------------------- Anzeige
    def as_dict(self) -> dict:
        """Je Raum {want, current, why, gain, cap, hold} plus die Abgleich-Zeile – für Oberfläche und Export."""
        names = {r.climate: r.name for r in self.eng.found.rooms}
        out = {}
        for climate, st in (self.state.get("rooms") or {}).items():
            room = next((r for r in self.rooms() if r.climate == climate), None)
            out[climate] = {"want": st.get("want"), "why": st.get("why"), "cap": st.get("cap"),
                            "gain": (st.get("pid") or {}).get("gain", 1.0),
                            "current": self.current(room) if room is not None else None,
                            "hold": bool(max(int(st.get("hold_until") or 0), int(st.get("pause_until") or 0))
                                         > (self.view.get("ts") or 0))}
        return {"rooms": out, "balance": balance_auto.text(self.state.get("bal") or {}, names)}

    def _save(self) -> None:
        """Zustand speichern – nur bei Änderung (der Schritt läuft alle 30 s)."""
        text = json.dumps(self.state, sort_keys=True, default=str)
        if text != self._saved:
            self.store.put(KV, self.state)
            self._saved = text
