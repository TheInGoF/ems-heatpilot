"""Anlage im Blick (seit 0.76): Kesselstatus und Fehler mitschreiben, Anlagendruck überwachen, Nachfüllen erkennen.

Anlass (21.11.2026): Der Druck fiel über Tage auf 0,1 bar, der Kessel ging nachts auf Störung und heizte von 01:10 bis
07:20 nicht – HeatPilot zeichnete weder den Störungscode noch die Uhrzeit auf, gemeldet wurde nichts.

``BoilerHealth.step`` läuft im 30-s-Takt der Live-Werte (``main.live_loop``) und schreibt nur ins eigene Protokoll
(Tabelle ``boiler_events``) und an den ``Notifier`` – am Kessel stellt es nichts:

* **Kesselstatus** (``service_code``, Nummer ``service_num``): jede Änderung steht mit Zeit, Druck und Nummer im
  Protokoll. Betriebsmeldungen („-H“ Heizen, „=H“ Warmwasser, „0A“ Taktsperre, „0H“ bereit … – ``normal``) nur dort;
  alles andere gilt nach ``FAULT_HOLD_S`` als **Störung bzw. Sperre**: Warnung im Log, Problem ``plant_fault`` mit
  Code, Nummer und Druck; vorbei, sobald wieder eine Betriebsmeldung kommt (mit Dauer).
* **Letzter Fehler** (``last_code``): ein neuer Eintrag des Kessels steht im Protokoll und – ohne offene Störung – als
  einmalige Meldung.
* **Anlagendruck:** Verglichen wird nur bei gleichem Pumpenzustand (die Pumpe hebt die Anzeige um 0,2–0,3 bar). Steigt
  er gegenüber dem letzten Wert der letzten 24 h um ``REFILL_K``, ist **nachgefüllt** worden (Protokoll, Log). Unter
  ``PRESS_LOW`` seit ``LOW_HOLD_S``: Problem ``plant_pressure`` (unter ``PRESS_CRIT`` mit „Kessel schaltet bald ab“);
  erledigt ab ``PRESS_LOW`` + ``CLEAR_K``. **Verlust:** stündlich die Gerade durch den Ruhedruck (Pumpe aus) seit dem
  letzten Nachfüllen – ab ``LOSS_WARN`` bar/Tag über mindestens ``LOSS_MIN_DAYS`` Tage und ``LOSS_MIN_DROP`` bar
  Problem ``plant_pressure_loss`` mit der Zeit bis ``PRESS_LOW``.

``report`` liefert für die Diagnose das Protokoll, den Verlust und je Stunde Druck neben Pumpe, Brenner, Warmwasser,
Zapfung und Außentemperatur (30 Tage) – damit sich zeigt, wobei der Druck fällt. ``checks`` sind die Zeilen im
Selbsttest. Der Zustand überlebt Neustarts (kv ``boiler_health.state``).
"""

from __future__ import annotations

import logging
import re
import time

from .values import flag, num

log = logging.getLogger(__name__)

KV = "boiler_health.state"
K_FAULT, K_ERROR, K_LOW, K_LOSS = "plant_fault", "plant_error", "plant_pressure", "plant_pressure_loss"
PRESS_LOW, PRESS_CRIT, PRESS_HIGH = 1.0, 0.8, 2.5   # bar
CLEAR_K = 0.2           # Druck wieder in Ordnung ab PRESS_LOW + so viel
LOW_HOLD_S = 600        # so lange unter PRESS_LOW, bevor gemeldet wird
FAULT_HOLD_S = 60       # so lange muss eine Störungsmeldung stehen
REFILL_K = 0.4          # Anstieg bei gleichem Pumpenzustand, der als Nachfüllen gilt
REFILL_WINDOW_S = 86400  # Vergleichswert höchstens so alt
REFILL_GAP_S = 12 * 3600  # zwei Nachfüllungen frühestens in diesem Abstand
LOSS_EVERY_S = 3600     # Verlust so oft neu rechnen
LOSS_WARN = 0.05        # bar/Tag
LOSS_MIN_DAYS = 3.0
LOSS_MIN_DROP = 0.2     # bar
LOSS_MIN_POINTS = 30
NO_VALUE = ("", "unknown", "unavailable", "none", "-", "--")
# Betriebsmeldungen der EMS-Kessel: -H/=H (Heizen/Warmwasser), 0x (0A Taktsperre, 0H bereit, 0Y, 0U, 0E …)
_NORMAL = re.compile(r"^(?:[-=][A-Z]|0[A-Z0-9]|00)$")


def normal(code: str) -> bool:
    """Betriebsmeldung des Kessels (keine Störung, keine Sperre)?"""
    return bool(_NORMAL.match(str(code or "").strip().upper()))


def _text(value) -> str | None:
    text = str(value if value is not None else "").strip()
    return None if text.lower() in NO_VALUE else text


def _bar(v: float | None) -> str:
    return "–" if v is None else f"{v:.1f}".replace(".", ",") + " bar"


def plant_text(eng) -> str:
    """„Anlagendruck 0,1 bar · Kesselstatus 6A (227)“ – für Meldungen anderer Teile (``boiler_watch``)."""
    code, nr = _text(eng._boiler("service_code")), _text(eng._boiler("service_num"))
    parts = [f"Anlagendruck {_bar(num(eng._boiler('sys_press')))}"]
    if code:
        parts.append(f"Kesselstatus {code}" + (f" ({nr})" if nr else ""))
    return " · ".join(parts)


_PUMP = "COALESCE(pump_on, CASE WHEN pump_mod > 0 THEN 1 WHEN pump_mod = 0 THEN 0 END)"  # vor 0.76 ohne pump_on
HOUR_COLS = ("ts", "druck_min", "druck_max", "ruhedruck_min", "pumpe_anteil", "brenner_5min", "warmwasser_5min",
             "zapfung_5min", "vorlauf", "ruecklauf", "aussen", "fussboden_ventil", "fussboden_temp")


def report(eng, days: int = 30) -> dict:
    """Für die Diagnose: Zustand, Protokoll und je Stunde Druck neben Pumpe, Brenner, Warmwasser, Zapfung,
    Vor-/Rücklauf, Außentemperatur und Fußboden (Ventil und Temperatur der Fußboden-Räume) – ``days`` Tage."""
    since = int(time.time()) - days * 86400
    q = eng.store.query
    rows = q(f"SELECT ts - ts % 3600 AS h, MIN(syspress) AS lo, MAX(syspress) AS hi, "
             f"MIN(CASE WHEN {_PUMP} = 0 THEN syspress END) AS rest, ROUND(AVG({_PUMP}), 2) AS pump, "
             "SUM(burner_on) AS burner, SUM(dhw) AS dhw, SUM(tap) AS tap, ROUND(AVG(flow), 1) AS flow, "
             "ROUND(AVG(ret), 1) AS ret, ROUND(AVG(t_out), 1) AS t_out FROM samples "
             "WHERE ts >= ? AND syspress IS NOT NULL GROUP BY h ORDER BY h", (since,))
    floors = list(getattr(eng.cfg, "floor_heating_rooms", None) or [])
    floor = {}
    if floors:
        marks = ",".join("?" * len(floors))
        floor = {int(r["h"]): (r["v"], r["t"]) for r in q(
            f"SELECT ts - ts % 3600 AS h, ROUND(AVG(valve), 0) AS v, ROUND(AVG(temp), 1) AS t FROM room_samples "
            f"WHERE ts >= ? AND room IN ({marks}) GROUP BY h", (since, *floors))}
    table = [[int(r["h"]), r["lo"], r["hi"], r["rest"], r["pump"], r["burner"], r["dhw"], r["tap"], r["flow"],
              r["ret"], r["t_out"], *floor.get(int(r["h"]), (None, None))] for r in rows]
    health = getattr(eng, "boiler_health", None)
    return {
        "hinweis": "Anlagendruck je Stunde (Messwerte alle 5 min: *_5min = Zahl der Messpunkte mit Brenner, "
                   "Warmwasser bzw. Zapfung an; ruhedruck_min = kleinster Druck mit Pumpe aus) und das Protokoll "
                   "(Kesselstatus, Fehler, Störung, Nachfüllen, Druck)",
        "zustand": health.as_dict() if health is not None else None,
        "protokoll": [dict(r) for r in q("SELECT * FROM boiler_events WHERE ts >= ? ORDER BY ts", (since,))],
        "stunden": {"spalten": list(HOUR_COLS), "zeilen": table},
    }


def checks(eng, add) -> None:
    """Selbsttest: Anlagendruck (jetzt, 7-Tage-Minimum), Störung, Druckverlust, letztes Nachfüllen."""
    press = num(eng._boiler("sys_press"))
    if press is not None:
        rows = eng.store.query("SELECT MIN(syspress) AS lo FROM samples WHERE ts >= ? AND syspress IS NOT NULL",
                               (int(time.time()) - 7 * 86400,))
        low = rows[0]["lo"] if rows and rows[0]["lo"] is not None else press
        if press < PRESS_CRIT:
            add("error", "Anlagendruck", f"{_bar(press)} – zu wenig Wasser in der Heizung, bitte nachfüllen")
        elif press < PRESS_LOW or low < PRESS_LOW:
            add("warn", "Anlagendruck", f"{_bar(press)} (7-Tage-Minimum {_bar(low)}) – unter {_bar(PRESS_LOW)}, "
                                        "bitte nachfüllen")
        elif press > PRESS_HIGH:
            add("warn", "Anlagendruck", f"{_bar(press)} – ungewöhnlich hoch (Ausdehnungsgefäß prüfen lassen)")
        else:
            add("ok", "Anlagendruck", f"{_bar(press)} (7-Tage-Minimum {_bar(low)})")
    health = getattr(eng, "boiler_health", None)
    st = health.state if health is not None else {}
    if st.get("fault"):
        f = st["fault"]
        add("error", "Kessel", f"Störung {f['code']}" + (f" ({f['nr']})" if f.get("nr") else "") +
            f" seit {time.strftime('%d.%m. %H:%M', time.localtime(f['ts']))} – der Kessel heizt nicht")
    loss = st.get("loss")
    if loss and loss.get("per_day") is not None and loss["per_day"] > 0:
        add("warn" if loss.get("warn") else "info", "Druckverlust",
            f"{loss['per_day']:.2f} bar/Tag über {loss['days']:.1f} Tage (Ruhedruck, Pumpe aus)".replace(".", ","))
    if st.get("refills"):
        r = st["refills"][-1]
        add("info", "Nachfüllen", f"zuletzt {time.strftime('%d.%m.%Y %H:%M', time.localtime(r['ts']))}: "
                                  f"{_bar(r['from'])} → {_bar(r['to'])}")


def loss_fit(points: list[tuple[int, float]]) -> dict | None:
    """Gerade durch (ts, bar): {per_day, days, drop, n, last} – None bei zu wenigen Punkten oder ohne Zeitspanne."""
    if len(points) < LOSS_MIN_POINTS:
        return None
    t0 = points[0][0]
    xs = [(t - t0) / 86400 for t, _ in points]
    ys = [p for _, p in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    last = my + slope * (xs[-1] - mx)
    return {"per_day": round(-slope, 3), "days": round(xs[-1], 1), "drop": round(-slope * xs[-1], 2),
            "n": len(points), "last": round(last, 2)}


class BoilerHealth:
    """Kesselstatus, Fehler und Anlagendruck (siehe Modulbeschreibung)."""

    def __init__(self, eng, notifier=None) -> None:
        self.eng, self.notifier, self.store = eng, notifier, eng.store
        self.state: dict = dict(self.store.get(KV) or {})
        self._saved = repr(self.state)

    def step(self, now: float | None = None) -> None:
        """Im 30-s-Takt: Codes, Fehler und Druck lesen und auswerten."""
        ts = int(time.time() if now is None else now)
        eng = self.eng
        press = num(eng._boiler("sys_press"))
        pump = num(eng._boiler("pump_mod"))
        pump_on = int(pump > 0) if pump is not None else flag(eng._boiler("heating_pump"))
        self._code(ts, _text(eng._boiler("service_code")), _text(eng._boiler("service_num")), press)
        self._last_code(ts, _text(eng._boiler("last_code")), press)
        if press is not None:
            self._refill(ts, press, pump_on)
            self._low(ts, press)
        if ts - self.state.get("loss_ts", 0) >= LOSS_EVERY_S:
            self.state["loss_ts"] = ts
            self._loss(ts)
        self._save()

    # -------------------------------------------------------------- Kesselstatus, Fehler
    def _code(self, ts: int, code: str | None, nr: str | None, press: float | None) -> None:
        st = self.state
        if code is None:
            return
        if code != st.get("code"):
            prev = st.get("code")
            st["code"], st["code_ts"] = code, ts
            if prev is not None:
                self._event(ts, "code", code, press, f"{prev} → {code}" + (f" ({nr})" if nr else ""))
        fault = st.get("fault")
        if not normal(code) and fault is None and ts - st["code_ts"] >= FAULT_HOLD_S:
            st["fault"] = {"ts": st["code_ts"], "code": code, "nr": nr, "press": press}
            text = (f"Kessel meldet Störung {code}" + (f" ({nr})" if nr else "") + f" und heizt nicht – Anlagendruck "
                    f"{_bar(press)}. Am Kessel ablesen, entstören bzw. Wasser nachfüllen")
            self._event(ts, "fault", code, press, text)
            log.warning("Kessel: %s", text)
            self._problem(K_FAULT, text, ts)
        elif normal(code) and fault is not None:
            st.pop("fault")
            mins = round((ts - fault["ts"]) / 60)
            text = f"Kessel wieder in Betrieb ({code}) – Störung {fault['code']} dauerte {mins} min"
            self._event(ts, "fault_end", fault["code"], press, text)
            log.info("Kessel: %s", text)
            if self.notifier is not None:
                self.notifier.solved(K_FAULT, text, ts)

    def _last_code(self, ts: int, last: str | None, press: float | None) -> None:
        st = self.state
        if last is None or last == st.get("last_code"):
            return
        first = "last_code" not in st
        st["last_code"] = last
        if first:
            return  # beim ersten Lesen nur merken – das ist kein neuer Fehler
        text = f"Kessel hat einen neuen Fehler gespeichert: {last} (Anlagendruck {_bar(press)})"
        self._event(ts, "error", last, press, text)
        log.warning("Kessel: %s", text)
        if st.get("fault") is None and self.notifier is not None:
            self.notifier.problem(K_ERROR, text, ts)
            self.notifier.solved(K_ERROR, ts=ts, quiet=True)  # einmalige Meldung, nichts bleibt offen

    # -------------------------------------------------------------- Druck
    def _refill(self, ts: int, press: float, pump_on: int | None) -> None:
        """Nachfüllen: Anstieg um ``REFILL_K`` gegenüber dem letzten Wert mit gleichem Pumpenzustand (≤ 24 h alt)."""
        if pump_on is None:
            return
        refs = self.state.setdefault("press", {})
        key = str(pump_on)
        prev = refs.get(key)
        refs[key] = [ts, press]
        last = (self.state.get("refills") or [{}])[-1].get("ts", 0)
        if (prev and ts - prev[0] <= REFILL_WINDOW_S and press - prev[1] >= REFILL_K
                and ts - last >= REFILL_GAP_S):
            text = f"Anlage nachgefüllt: {_bar(prev[1])} → {_bar(press)}"
            self.state["refills"] = (self.state.get("refills") or [])[-19:] + [
                {"ts": ts, "from": prev[1], "to": press}]
            self.state["loss_ts"] = 0  # Verlust ab jetzt neu
            self._event(ts, "refill", None, press, text)
            log.info("Kessel: %s", text)

    def _low(self, ts: int, press: float) -> None:
        st = self.state
        if press < PRESS_LOW:
            since = st.setdefault("low_since", ts)
            if ts - since >= LOW_HOLD_S:
                if "low_event" not in st:
                    st["low_event"] = ts
                    self._event(ts, "low", None, press, f"Anlagendruck unter {_bar(PRESS_LOW)}")
                self._problem(K_LOW, self._low_text(press), ts)
        elif press >= PRESS_LOW + CLEAR_K:
            st.pop("low_since", None)
            st.pop("low_event", None)
            if self.notifier is not None:
                self.notifier.solved(K_LOW, f"Anlagendruck wieder in Ordnung ({_bar(press)})", ts)

    def _low_text(self, press: float) -> str:
        if press < PRESS_CRIT:
            return (f"Anlagendruck nur {_bar(press)} – zu wenig Wasser in der Heizung, der Kessel schaltet bald auf "
                    "Störung. Bitte nachfüllen")
        loss = self.state.get("loss") or {}
        rate = f" (sinkt um {loss['per_day']:.2f} bar/Tag)".replace(".", ",") if loss.get("warn") else ""
        return f"Anlagendruck {_bar(press)}{rate} – unter {_bar(PRESS_LOW)}, bitte nachfüllen"

    def rest_points(self, since: int) -> list[tuple[int, float]]:
        """Ruhedruck (Pumpe aus) seit ``since`` aus den Messwerten – vor 0.76 ohne ``pump_on``: Pumpenmodulation 0."""
        rows = self.store.query(
            "SELECT ts, syspress FROM samples WHERE ts >= ? AND syspress IS NOT NULL "
            "AND COALESCE(pump_on, CASE WHEN pump_mod > 0 THEN 1 WHEN pump_mod = 0 THEN 0 END) = 0 ORDER BY ts",
            (int(since),))
        return [(int(r["ts"]), float(r["syspress"])) for r in rows]

    def _loss(self, ts: int) -> None:
        """Druckverlust seit dem letzten Nachfüllen (höchstens 30 Tage) – Problem ab ``LOSS_WARN``."""
        refills = self.state.get("refills") or []
        since = max(ts - 30 * 86400, refills[-1]["ts"] + 3600 if refills else 0)
        fit = loss_fit(self.rest_points(since))
        if fit is not None:
            fit["warn"] = (fit["per_day"] >= LOSS_WARN and fit["days"] >= LOSS_MIN_DAYS
                           and fit["drop"] >= LOSS_MIN_DROP)
            fit["days_to_low"] = (round((fit["last"] - PRESS_LOW) / fit["per_day"], 1)
                                  if fit["per_day"] > 0 and fit["last"] > PRESS_LOW else None)
        self.state["loss"] = fit
        if fit and fit["warn"]:
            left = (f" Bei {_bar(PRESS_LOW)} in ca. {fit['days_to_low']:.0f} Tagen nachfüllen." if fit["days_to_low"]
                    else "")
            self._problem(K_LOSS, f"Anlagendruck sinkt stetig um {fit['per_day']:.2f} bar/Tag ".replace(".", ",") +
                          f"({fit['days']:.0f} Tage) – die Heizung verliert Wasser.{left} Heizungsbauer: "
                          "Ausdehnungsgefäß, Sicherheitsventil, Entlüfter prüfen", ts)
        elif self.notifier is not None:
            self.notifier.solved(K_LOSS, ts=ts, quiet=True)

    # -------------------------------------------------------------- Hilfen
    def _event(self, ts: int, kind: str, code: str | None, value: float | None, text: str) -> None:
        self.store.add_boiler_event(ts, kind, code, value, text)

    def _problem(self, key: str, text: str, ts: int) -> None:
        if self.notifier is not None:
            self.notifier.problem(key, text, ts)

    def _save(self) -> None:
        text = repr(self.state)
        if text != self._saved:
            self.store.put(KV, self.state)
            self._saved = text

    def as_dict(self) -> dict:
        return {k: v for k, v in self.state.items() if k not in ("loss_ts",)}
