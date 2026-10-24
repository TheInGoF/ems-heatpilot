"""Raummodelle aus den Raumwerten der Stundenstatistik lernen (seit 0.45, Tabelle ``room_stats``).

Die Zeilen aus ``room_stats`` sind Stundenmittel (Temperatur, Heizleistung, Vorlauf) aus der Zeit ohne eigene
Raumwerte. Das Raummodell (``models.RoomModel``: Aufheizen und Auskühlen) lernt aus ihnen in Schritten von einer
Stunde statt der halben Stunde der Messpunkte – die Gleichung ist linear in den Parametern, Stundenmittel ergeben denselben Schritt mit
gemittelten Eingängen (``dt_h`` = 1). Ein Schritt geht vom Mittel der Stunde h zum Mittel der Stunde h+1, seine
Eingänge sind das Mittel beider Stunden (wie die mittige Ableitung des Hausmodells):

* Wärmezufuhr wie in ``engine_learn.learn_rooms``: (Vorlauf − Raum) × Heizbetrieb × Heizleistung/100 (seit 0.53
  je Stunde an das Modell übergeben, das daraus seine Form rechnet: Exponent, Ventil^0,5). Vorlauf und
  Heizbetrieb kommen aus eigenen Messpunkten der Stunde, wo es sie gibt, sonst aus dem Stundenmittel des Vorlaufs der
  Langzeitstatistik und dem Heizgas der Stundenwerte (Heizbetrieb, wenn mehr als ``HEAT_GAS_KWH`` geheizt wurde);
* ohne Heizleistung lernt das Modell nur Stunden ohne Heizbetrieb (Auskühlen, Fremdwärme) – eine Stunde mit
  Heizbetrieb, aber unbekanntem Ventil oder Vorlauf, bleibt weg;
* nicht gelernt (nur der Estrich nachgeführt) wie live: Sprung über 2 K, Urlaub, Fremdwärme. Keine Sonne (die
  Stundenwerte kennen keine Bewölkung).

Gelernt wird nur, wo der Raum in beiden Stunden keine eigenen Raumwerte hat – sonst lernt er aus ihnen (Messpunkte).
Das Ventilmodell sieht diese Zeilen nie (es liest nur ``room_samples``). Nur lesen und Modelle rechnen; geschrieben
wird hier nichts.
"""

from __future__ import annotations

STEP_H = 1.0
HEAT_GAS_KWH = 0.05            # darüber gilt eine Stunde der Statistik als Heizbetrieb (wie GAS_FREE_KWH beim Lernen)
MAX_JUMP_K = 2.0               # wie live: größerer Sprung (Fenster, Messfehler) wird nicht gelernt


class StatLearner:
    """Stundenschritte aus ``room_stats`` für die Räume der Engine (bzw. nur ``rooms``), in zeitlicher Reihenfolge
    über ``until`` – so lassen sie sich zwischen die Messpunkt-Schritte einordnen (``backfill.replay_steps``,
    ``LearnMixin.relearn_steps``)."""

    def __init__(self, eng, rooms: set[str] | None = None) -> None:
        self.eng = eng
        keys = [k for k in eng.rooms if rooms is None or k in rooms]
        self.data: dict[str, dict[int, tuple]] = {k: {} for k in keys}
        self.own: dict[str, set[int]] = {k: set() for k in keys}
        if keys:
            marks = ",".join("?" * len(keys))
            for r in eng.store.query(f"SELECT ts, room, temp, valve, flow FROM room_stats WHERE room IN ({marks}) "
                                     "ORDER BY ts", tuple(keys)):
                self.data[r["room"]][r["ts"]] = (r["temp"], r["valve"], r["flow"])
            for r in eng.store.query(f"SELECT DISTINCT room, ts - ts % 3600 AS h FROM room_samples WHERE room IN "
                                     f"({marks})", tuple(keys)):
                self.own[r["room"]].add(r["h"])
        self.hours = sorted({h for d in self.data.values() for h in d})
        self.i = 0
        self.hourly = self._hourly()
        first = eng.store.query("SELECT MIN(ts) AS t FROM samples")[0]["t"]
        self.first_sample = first if first is not None else float("inf")
        self.steps: dict[str, int] = {k: 0 for k in keys}
        self._srows: dict[int, list] = {}

    def _hourly(self) -> dict[int, dict]:
        if not self.hours:
            return {}
        rows = self.eng.store.query("SELECT * FROM hourly WHERE ts >= ? AND ts <= ?",
                                    (self.hours[0], self.hours[-1] + 3600))
        return {r["ts"]: r for r in rows}

    def until(self, ts: float) -> None:
        """Alle noch offenen Stunden h mit h + 1 h ≤ ``ts`` lernen."""
        while self.i < len(self.hours) and self.hours[self.i] + 3600 <= ts:
            self.hour(self.hours[self.i])
            self.i += 1

    def days(self, ts: float):
        """Wie ``until(ts)``, aber nach jedem Tag ein ``yield`` (die Ereignisschleife bleibt bedienbar)."""
        while self.i < len(self.hours) and self.hours[self.i] + 3600 <= ts:
            self.until(min(ts, self.hours[self.i] + 86400))
            yield self.hours[self.i - 1]

    def hour(self, h: int) -> None:
        """Schritt von der Stunde ``h`` zur nächsten für jeden Raum mit Werten in beiden Stunden."""
        for key, series in self.data.items():
            model = self.eng.rooms.get(key)
            if model is None or h not in series or h + 3600 not in series:
                continue
            if h in self.own[key] or h + 3600 in self.own[key]:
                continue
            if self._step(model, h, series[h], series[h + 3600]):
                self.steps[key] += 1

    def _samples_drive(self, h: int, temp: float) -> float | None:
        """Mittlere Wärmezufuhr-Basis (Vorlauf − Raum) × Heizbetrieb aus eigenen Messpunkten der Stunde."""
        if h not in self._srows:  # je Stunde einmal lesen (alle Räume)
            self._srows[h] = self.eng.store.query("SELECT flow, heating, burner_on, dhw, pump_mod FROM samples WHERE "
                                                  "ts >= ? AND ts < ? AND flow IS NOT NULL", (h, h + 3600))
        rows = self._srows[h]
        if len(rows) < 2:
            return None
        vals = []
        for s in rows:
            if s["pump_mod"] is not None:
                active = int(s["pump_mod"] > 0)
            else:
                active = s["heating"] if s["heating"] is not None else (s["burner_on"] or 0)
            vals.append(0.0 if s["dhw"] else max(s["flow"] - temp, 0.0) * (active or 0))
        return sum(vals) / len(vals)

    def drive(self, h: int, temp: float, flow: float | None) -> float | None:
        """(Vorlauf − Raum) × Heizbetrieb der Stunde ``h``: aus Messpunkten, sonst Statistik-Vorlauf und Heizgas.
        None, wenn es sich nicht sagen lässt."""
        if h + 3600 > self.first_sample:
            got = self._samples_drive(h, temp)
            if got is not None:
                return got
        row = self.hourly.get(h)
        if not row or row["gas_kwh"] is None:
            return None
        if self.eng.heating_gas(row) <= HEAT_GAS_KWH:
            return 0.0
        return None if flow is None else max(flow - temp, 0.0)

    def _step(self, model, h: int, a: tuple, b: tuple) -> bool:
        """Ein Stundenschritt; True, wenn das Modell ihn gelernt hat."""
        (t0, v0, f0), (t1, v1, f1) = a, b
        r0, r1 = self.hourly.get(h), self.hourly.get(h + 3600)
        outs = [r["t_out"] for r in (r0, r1) if r and r["t_out"] is not None]
        if t0 is None or t1 is None or not outs:
            return False
        w0, w1 = self.drive(h, t0, f0), self.drive(h + 3600, t1, f1)
        if w0 is None or w1 is None:
            return False
        if v0 is None or v1 is None:
            if w0 or w1:  # Heizbetrieb, aber Ventil unbekannt: Heizkörperwärme nicht von Fremdwärme zu trennen
                return False
            heat, parts = 0.0, None
        else:
            heat, parts = (w0 * v0 + w1 * v1) / 200, [(w0, v0 / 100), (w1, v1 / 100)]
        rows = [r for r in (r0, r1) if r]
        quiet = not any((r["vacation_frac"] or 0) > 0.5 or (r["ext_frac"] or 0) > 0 for r in rows)
        update = abs(t1 - t0) <= MAX_JUMP_K and quiet
        if update and model.rls.updates == 0 and self.eng.house.balance_plausible:
            model.adopt_house_tau(self.eng.house.tau_cool_h)
        model.learn(t0, t1, heat, sum(outs) / len(outs), STEP_H, floor_input=(w0 + w1) / 2, update=update,
                    parts=parts)
        return update
