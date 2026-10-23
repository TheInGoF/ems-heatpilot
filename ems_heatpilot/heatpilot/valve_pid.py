"""PID je Raum über die Ventilöffnung (seit 0.51) – reine Funktionen, der Ablauf steht in ``valve_pid_control``.

Für ventilgeführte Räume (``room_lead``, z. B. Shelly BLU TRV) stellt HeatPilot die Öffnung in Prozent selbst,
statt ein Soll zu schreiben – das Thermostat kann dann nicht mehr zu spät schließen (Überschwingen beim Vorheizen).

* **Vorsteuerung** aus dem Raummodell (``feedforward``): die Öffnung, die das Soll beim aktuellen Vorlauf dauerhaft
  hält – ``(b·(Soll − außen) − c) / (a·(Vorlauf − Soll))``. Der PI-Teil korrigiert nur noch den Rest.
* **PI mit Anti-Windup** (``step``): P = ``KP``·Verstärkung·Fehler, I wächst mit ``KP/TI_S``; läuft die Summe aus
  0…Deckel hinaus, wird der I-Anteil zurückgerechnet (kein Aufziehen in der Sättigung).
* **Lernen je Raum** (in ``step``): Pendelt der Raum (mindestens ``OSC_N`` Vorzeichenwechsel des Fehlers über
  ±``OSC_BAND`` in 2 h), sinkt die Verstärkung um 20 % (bis ``GAIN_MIN``); bleibt er trotz Luft nach oben
  ``SLOW_S`` lang mehr als ``SLOW_K`` unter dem Soll, steigt sie um 15 % (bis ``GAIN_MAX``).
* **Schreibregeln** (``decide``): nur bei Änderung ab ``DEAD_PCT``, höchstens ein Schreiben je ``GAP_S`` – ein großer
  Sprung ab ``JUMP_PCT`` sofort (wer Wärme braucht, bekommt sie sofort); eine fremde Hand hält bis ``hold_until``.

Der Zustand je Raum ist ein JSON-fähiges dict {i, ts, out, gain, xs, sign, slow_since} und läuft über
``valve_pid_control`` (kv) weiter.
"""

from __future__ import annotations

from .dhw_rule import Decision

KP = 25.0           # P-Anteil: Prozent Öffnung je K Fehler (× gelernte Verstärkung)
TI_S = 5400.0       # Nachstellzeit des I-Anteils (90 min)
DEAD_PCT = 3        # schreiben nur bei Änderung ab so vielen Prozentpunkten (schont Motor und Batterie)
JUMP_PCT = 15       # ab so viel Änderung sofort schreiben, ohne den Abstand abzuwarten
GAP_S = 300         # sonst höchstens ein Schreiben je so viele Sekunden
OSC_BAND = 0.15     # Pendeln: Fehler wechselt das Vorzeichen über ±so viel K hinaus …
OSC_N = 4           # … so oft …
OSC_WINDOW_S = 7200  # … innerhalb von 2 h → Verstärkung runter
SLOW_K = 0.3        # träge: so weit unter Soll ohne Sättigung …
SLOW_S = 5400       # … so lange → Verstärkung hoch
GAIN_MIN, GAIN_MAX = 0.3, 3.0
GAIN_DOWN, GAIN_UP = 0.8, 1.15
FALLBACK_PCT = 40   # Raumtemperatur länger unbekannt: einmal auf diesen sicheren Wert (kein Frost, kein Überheizen)
BLIND_S = 1800      # … nach so langer Zeit ohne Messwert


def feedforward(model, sp: float, flow: float | None, t_out: float | None) -> float | None:
    """Öffnung in %, die das Soll beim Vorlauf ``flow`` dauerhaft hält – None, wenn nicht rechenbar (kein Vorlauf
    über dem Soll); 0, wenn Fremdwärme reicht."""
    if model is None or flow is None or t_out is None or flow <= sp + 1.0:
        return None
    need = model.b * (sp - t_out) - model.c  # nötige Heizleistung in K/h (wie ``preview._need``)
    if need <= 0:
        return 0.0
    return max(0.0, min(need / (model.a * (flow - sp)) * 100.0, 100.0))


def step(st: dict, sp: float, temp: float, flow: float | None, t_out: float | None, model, ts: int,
         hi: float = 100.0) -> float:
    """Ein PID-Schritt: neuer Wunsch in % (0…``hi``). ``st`` wird fortgeschrieben (I-Anteil, Lernen)."""
    err = sp - temp
    gain = float(st.get("gain", 1.0))
    dt = min(max(ts - st["ts"], 0), 300) if st.get("ts") is not None else 0
    ff = feedforward(model, sp, flow, t_out)
    st["i"] = float(st.get("i", 0.0)) + KP / TI_S * gain * err * dt
    raw = (ff or 0.0) + KP * gain * err + st["i"]
    out = max(0.0, min(raw, hi))
    st["i"] -= raw - out  # Anti-Windup: der I-Anteil wächst nicht über die Sättigung hinaus
    st["ts"], st["out"] = ts, round(out, 1)
    _learn(st, err, out, hi, ts)
    return out


def _learn(st: dict, err: float, out: float, hi: float, ts: int) -> None:
    """Verstärkung je Raum anpassen (siehe Modulbeschreibung) – Teil von ``step``."""
    gain = float(st.get("gain", 1.0))
    sign = 1 if err > OSC_BAND else -1 if err < -OSC_BAND else 0
    if sign and sign != st.get("sign", 0):
        if st.get("sign", 0):
            xs = [t for t in st.get("xs", []) if t > ts - OSC_WINDOW_S] + [ts]
            if len(xs) >= OSC_N:
                gain, xs = max(gain * GAIN_DOWN, GAIN_MIN), []
            st["xs"] = xs
        st["sign"] = sign
    if err > SLOW_K and out < hi - 1.0:
        since = st.setdefault("slow_since", ts)
        if ts - since >= SLOW_S:
            gain = min(gain * GAIN_UP, GAIN_MAX)
            st["slow_since"] = ts
    else:
        st.pop("slow_since", None)
    st["gain"] = round(gain, 3)


def decide(target: float, current: float | None, ts: int, last_ts: int | None,
           hold_until: int | None = None) -> Decision:
    """Schreiben? Nur bei Änderung ab ``DEAD_PCT``, höchstens eins je ``GAP_S`` – Sprünge ab ``JUMP_PCT`` sofort;
    eine fremde Hand (``hold_until``) hält auf."""
    value = int(round(target))
    if hold_until is not None and ts < hold_until:
        return Decision(False, value, f"von Hand verstellt – HeatPilot wartet noch "
                                      f"{max(1, int(hold_until - ts) // 60)} min", until=int(hold_until))
    if current is None:
        return Decision(True, value, f"Ventil unbekannt → {value} %")
    diff = abs(value - current)
    if diff < DEAD_PCT:
        return Decision(False, value, f"Ventil {current:.0f} % passt")
    if diff < JUMP_PCT and last_ts is not None and ts < last_ts + GAP_S:
        return Decision(False, value, f"höchstens ein Schreiben je {GAP_S // 60} min", until=int(last_ts + GAP_S))
    return Decision(True, value, f"{current:.0f} → {value} %")
