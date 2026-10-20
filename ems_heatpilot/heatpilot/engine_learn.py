"""Lernen: Raummodelle, Fremdwärme, Hausmodell aus Stunden und Tagen, Zeiten aus Tado und Strom.

``LearnMixin`` bekommt vom Takt (``Engine.tick``) abgeschlossene Raumschritte und Stunden: Es lernt
die Raummodelle, erkennt Fremdwärme (Kamin, Ofen), bildet die Stundenwerte und lernt daraus das
Hausmodell (Stunde und Tag, ohne Warmwasser). Stündlich lernt es Nachtzeiten und Temperaturen aus
Tado und die Schlafenszeiten aus dem Stromverbrauch. In die Datenbank schreibt es die Stundenwerte,
die Fremdwärme-Ereignisse, die Zähler verworfener Gaswerte, die Tagesschnappschüsse der Modelle und
die gelernten Zeiten (``schedule.learned``, ``KV_MANAGED_UNTIL``, die Nächte aus dem Stromverbrauch über
``sleep.learn``). Seit 0.39 lernt es das Ventilverhalten je Raum (``learn_valves`` über ``valve_learn``). Seit 0.42.1
lernt es nach „Gelerntes übernehmen“ (``room_adopt``) Raum- und Ventilmodell des Raums von vorn (``relearn_adopted``),
und ``aggregate_hour`` führt Stundenwerte zusammen: Was in der Stunde unbekannt ist (keine Raumwerte, kein
Gaszählerstand …), behält den gespeicherten Wert; fehlen Räume, ersetzt das Mittel der übrigen keine gespeicherte
Innentemperatur und füllt eine Lücke nur um ihren Versatz berichtigt (``indoor_mean``). Es steuert nichts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta

from . import indoor_mean, plausibility, schedule, sleep, valve_learn
from .models import DAILY_MIN_HOURS, ROOM_STEP_H, RoomModel
from .valve_model import ValveModel
from .values import local, midnight

log = logging.getLogger(f"{__package__}.engine")  # im Protokoll wie vor der Aufteilung: heatpilot.engine

# Fremdwärme je Raum (Kamin, Ofen, viele Gäste): der Raum wird deutlich wärmer, ohne dass der
# Heizkörper es erklären kann – solche Schritte lernt das Raummodell nicht, solche Stunden das Haus nicht
EXT_RATE_KH = 0.8        # unerklärter Anstieg ab so viel K/h …
EXT_MIN_K = 0.4          # … und mindestens so viel K im Schritt
EXT_HEAT_SHARE = 0.10    # Heizkörper liefert praktisch nichts: Eingang ≤ 10 % des möglichen (Vorlauf − Raum) …
EXT_VALVE_MAX = 5.0      # … und das Ventil war in diesem und im vorigen Schritt nie weiter offen (%): ein eben
#                          noch offener Heizkörper gibt nach dem Schließen weiter Wärme ab, und ein Thermostat
#                          am Heizkörper misst den warmen Körper erst verzögert – beides sähe sonst wie Fremdwärme aus
EXT_SUN_MAX = 0.1        # darunter keine Sonne auf den Fassaden – dann darf auch ein junges Modell erkennen
EXT_DAY_HOURS = range(7, 20)  # ohne Sonnendaten (kein Standort) wird in diesen Stunden nicht erkannt: könnte Sonne sein
EXT_REF_H = 2.0          # Bezug ist die höchste Temperatur der letzten so vielen Stunden: nach Stoßlüften muss der Raum
#                          erst wieder über sein altes Niveau steigen – die Erholung aus Wänden und Möbeln bei
#                          geschlossenem Ventil (Nachtabsenkung) ist keine Fremdwärme
EXT_END_K = 0.3          # Ereignis endet, wenn der Raum wieder bis auf so viel K am Start ist …
EXT_END_VALVE = 30.0     # … oder der Heizkörper wieder deutlich öffnet (Ventil-Mittel eines Schritts, %) …
EXT_MAX_H = 12           # … spätestens nach so vielen Stunden
EXT_DAY_SKIP_H = 3.0     # Tag mit so vielen Stunden Fremdwärme lernt das Tagesmodell nicht (zählt nur als Vortag)

HEAT_SEASON_24H = 15.0          # darüber (24-h-Mittel außen) lernt das Hausmodell nicht
HOUSE_DAYS_CATCH_UP = 31        # so viele offene Tage holt das Tagesmodell höchstens nach
GAS_FREE_KWH = 0.05             # Stunde gilt als gasfrei (Auskühlen lernen)
NIGHT_HOURS = (22, 23, 0, 1, 2, 3, 4, 5)  # ohne Sonne
# Warmwasser-Grundbedarf aus Sommerstunden (24-h-Mittel draußen so warm: sicher keine Heizung,
# das Gas ist Warmwasser) – für Langzeitstunden ohne Warmwasser-Angabe vor dem Hauslernen abgezogen
DHW_BASE_SUMMER_24H = 17.0
DHW_BASE_MIN_HOURS = 14 * 24
DHW_BASE_MIN_CELL = 4         # so viele Sommerstunden je Wochentag/Stunde, sonst Mittel der Stunde

KV_MANAGED_UNTIL = "schedule.managed_until"  # bis dahin hat HeatPilot selbst gesteuert: nicht aus Tado lernen
VALVE_BUDGET_S = 1.0  # Ventilmodelle: höchstens so lange je Takt aus der Historie nachlernen (0.39)


class LearnMixin:
    """Lernen der ``Engine``.

    Nutzt von der Engine: ``cfg``, ``found``, ``store``, ``house``, ``rooms``, ``room_ext``,
    ``schedule_learned``/``eff``, die Raum-Historie (``_room_valve_prev``, ``_room_temp_hist``,
    ``_last_room_step``) und ``nominal_kw``/``outdoor_now`` aus den anderen Mixins.
    """

    # ------------------------------------------------------------------ Räume
    def seed_room_priors(self) -> None:
        """Noch ungelernte Raummodelle starten mit der gemessenen Auskühlzeit dieses Hauses statt einem Pauschalwert."""
        if self.house.balance_plausible:
            for m in self.rooms.values():
                m.adopt_house_tau(self.house.tau_cool_h)

    def relearn_rooms(self, climates: set[str]) -> None:
        """Neue/umgestellte Raummodelle sofort aus der gespeicherten Historie lernen."""
        log.info("Lerne Räume %s aus der gespeicherten Historie neu", sorted(climates))
        for _ in self.relearn_steps(climates):
            pass

    def relearn_steps(self, climates: set[str]):
        """Raumschritte der Räume ``climates`` von den ältesten Raumwerten bis zum letzten Raumschritt des Takts lernen;
        nach jedem Tag ein ``yield``. Das Ende wird jedes Mal neu gelesen – was der Takt inzwischen weiterzählt, lernt
        die Schleife noch mit (der Takt lässt Räume in ``_room_busy`` aus)."""
        first = self.store.query("SELECT MIN(ts) AS t FROM room_samples")[0]["t"]
        if first is None:
            return
        step = int(ROOM_STEP_H * 3600)
        st, n = first - first % step + step, 0
        while self._last_room_step is not None and st < self._last_room_step:
            self.learn_rooms(st, st + step, only=climates)
            st, n = st + step, n + 1
            if n % max(int(24 / ROOM_STEP_H), 1) == 0:
                yield st

    def _reset_adopted(self, climate: str) -> bool:
        """Raum- und Ventilmodell des Raums von vorn (siehe ``relearn_adopted``); True, wenn jetzt nachzulernen ist."""
        room = self.found.room_of(climate)
        if room is None or room.climate != climate:
            return False
        model = RoomModel(room.name, floor=climate in self.cfg.floor_heating_rooms)
        if self.house.balance_plausible:
            model.adopt_house_tau(self.house.tau_cool_h)
        self.rooms[climate] = model
        if climate in self.valves:  # nicht für den Heizkreis von EMS-ESP
            self.valves[climate] = ValveModel.from_dict(None, valve_learn.binary_device(room))
        for state in (self.room_ext, self._room_temp_hist, self._room_valve_prev):
            state.pop(climate, None)
        if self._last_room_step is None:
            return False
        self.store.db.execute("DELETE FROM room_events WHERE room = ?", (climate,))
        return True

    def relearn_adopted(self, climate: str) -> None:
        """Gelerntes übernommen (0.42.1, ``room_adopt``): Raum- und Ventilmodell des Raums von vorn aus den gespeicherten
        Messwerten – das Raummodell gleich (``relearn_rooms``, mit Startwert aus dem Haus; seine Fremdwärme-Ereignisse
        werden dabei neu erkannt), das Ventilmodell im Takt in Abschnitten (``learn_valves``). Andere Räume und Modelle
        bleiben, wie sie sind; es wird nichts geschrieben."""
        if self._reset_adopted(climate):
            self.relearn_rooms({climate})
        self.store.commit()

    async def relearn_adopted_async(self, climate: str) -> None:
        """Wie ``relearn_adopted``, gibt aber nach jedem Tag die Ereignisschleife frei. Solange lässt der Takt den Raum
        aus (``_room_busy``) und speichert sein halbes Modell nicht."""
        if self._reset_adopted(climate):
            busy = self.__dict__.setdefault("_room_busy", set())
            busy.add(climate)
            try:
                log.info("Lerne Raum %s aus der gespeicherten Historie neu", climate)
                for _ in self.relearn_steps({climate}):
                    await asyncio.sleep(0)
            finally:
                busy.discard(climate)
        self.store.commit()

    def learn_rooms(self, start: int, end: int, only: set[str] | None = None) -> None:
        sys_rows = {
            r["ts"]: r
            for r in self.store.query(
                "SELECT ts, flow, heating, burner_on, dhw, pump_mod, t_out, vacation, away_long, sun_e, sun_s, sun_w "
                "FROM samples WHERE ts BETWEEN ? AND ?",
                (start, end),
            )
        }
        busy = (self.__dict__.get("_room_busy") or set()) if only is None else set()  # lernt gerade neu (0.42.1)
        for climate, model in self.rooms.items():
            if (only is not None and climate not in only) or climate in busy:
                continue
            rows = self.store.query(
                "SELECT ts, temp, valve FROM room_samples WHERE room=? AND ts BETWEEN ? AND ? ORDER BY ts",
                (climate, start, end),
            )
            rows = [r for r in rows if r["temp"] is not None]
            if len(rows) < 3 or rows[-1]["ts"] - rows[0]["ts"] < 0.8 * ROOM_STEP_H * 3600:
                continue
            inputs, floor_inputs, outs, suns, overs, valves = [], [], [], [], [], []
            vacation = False
            for r in rows:
                s = sys_rows.get(r["ts"])
                if not s or s["flow"] is None or s["t_out"] is None:
                    continue
                vacation = vacation or bool(s["vacation"]) or bool(s["away_long"])  # Urlaub oder alle lange weg
                # Wärme kommt an, solange die Heizungspumpe Wasser umwälzt – auch nach dem
                # Brennerlauf (Nachlauf, Taktpausen). Fehlt die Pumpendrehzahl: Pumpen-, sonst Brennersignal.
                if s["pump_mod"] is not None:
                    active = int(s["pump_mod"] > 0)
                else:
                    active = s["heating"] if s["heating"] is not None else (s["burner_on"] or 0)
                if s["dhw"]:
                    active = 0  # Ventil auf Warmwasser
                warm = max(s["flow"] - r["temp"], 0) * (active or 0)
                inputs.append(warm * (r["valve"] or 0) / 100)
                floor_inputs.append(warm)  # Fußboden: kein Ventil, heizt immer mit
                overs.append(max(s["flow"] - r["temp"], 0))  # was der Heizkörper höchstens liefern könnte
                valves.append(r["valve"])  # None = kein Ventilwert
                outs.append(s["t_out"])
                if s["sun_s"] is not None:
                    suns.append((s["sun_e"], s["sun_s"], s["sun_w"]))
            if len(inputs) < 3:
                continue
            t0, t1 = rows[0]["temp"], rows[-1]["temp"]
            dt_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600
            heat, floor_in = sum(inputs) / len(inputs), sum(floor_inputs) / len(floor_inputs)
            t_out = sum(outs) / len(outs)
            solar = tuple(sum(v[i] for v in suns) / len(suns) for i in range(3)) if suns else None
            known = [v for v in valves if v is not None]
            if known:
                ext = self.ext_heat_step(climate, model, start, end, t0, t1, dt_h, heat, floor_in, t_out, solar,
                                         sum(overs) / len(overs), sum(known) / len(known), max(known))
            else:  # ohne Ventilwert ist Heizkörperwärme nicht von Fremdwärme zu trennen: nicht erkennen
                self._room_valve_prev.pop(climate, None)
                ext = climate in self.room_ext
            # Fenster offen o. ä., Urlaub oder alle lange weg, Fremdwärme: Estrich nachführen, aber nicht lernen
            update = abs(t1 - t0) <= 2.0 and not vacation and not ext
            model.learn(t0, t1, heat, t_out, dt_h, floor_input=floor_in, update=update, solar=solar)

    def ext_heat_step(self, climate: str, model: RoomModel, start: int, end: int, t0: float, t1: float,
                      dt_h: float, heat_input: float, floor_input: float, t_out: float,
                      solar: tuple[float, float, float] | None, flow_over: float, valve: float,
                      valve_max: float) -> bool:
        """Fremdwärme-Ereignis des Raums verfolgen; True = dieser Schritt gehört zu einem Ereignis.

        Beginn: unerklärter Anstieg (gemessen minus Modell) ≥ EXT_RATE_KH und ≥ EXT_MIN_K im Schritt,
        der Heizkörper liefert praktisch nichts (auch im Schritt davor, EXT_VALVE_MAX), und Sonne bzw.
        Fußboden sind entweder nicht im Spiel oder schon gelernt – sonst würde ein junges Modell seine
        eigene Lernchance als Ereignis verwerfen (Rückkopplung). Ein Abfall (Fenster auf) ist kein
        Ereignis, die Erholung danach auch nicht: der Raum muss über das höchste Niveau der letzten
        EXT_REF_H Stunden steigen, das ist dann der Startwert. Ende: siehe EXT_END_* / EXT_MAX_H.
        """
        valve_prev = self._room_valve_prev.get(climate)
        self._room_valve_prev[climate] = valve_max
        hist = [(ts, t) for ts, t in self._room_temp_hist.get(climate, ()) if start - EXT_REF_H * 3600 <= ts <= start]
        self._room_temp_hist[climate] = hist + [(end, t1)]
        ref = max([t0, *(t for _, t in hist)])  # Niveau vor einem Abfall (Lüften), sonst t0
        ev = self.room_ext.get(climate)
        if ev is not None:
            ev["peak"] = max(ev["peak"], t1)
            ev["rise"] = round(ev["peak"] - ev["t_start"], 2)
            limit = ev["start_ts"] + EXT_MAX_H * 3600
            if t1 <= ev["t_start"] + EXT_END_K or valve >= EXT_END_VALVE or end >= limit:
                self.store.add_room_event(ev["start_ts"], min(end, limit), climate, ev["rise"])
                del self.room_ext[climate]
                log.info("Raum %s: Fremdwärme vorbei (%s–%s, +%.1f K)", climate,
                         local(ev["start_ts"]).strftime("%d.%m. %H:%M"), local(min(end, limit)).strftime("%H:%M"), ev["rise"])
            return True
        if dt_h <= 0 or t1 - t0 < EXT_MIN_K or t1 - ref < EXT_MIN_K:
            return False
        unexplained = (t1 - t0) / dt_h - model.explained_rate(heat_input, t_out, (t0 + t1) / 2, solar)
        if unexplained < EXT_RATE_KH:
            return False
        if heat_input > EXT_HEAT_SHARE * flow_over or valve_prev is None or max(valve_max, valve_prev) > EXT_VALVE_MAX:
            return False  # der Heizkörper liefert(e) – das muss lernbar bleiben
        if solar is None and local(start).hour in EXT_DAY_HOURS:
            return False  # ohne Sonnendaten (kein Standort) kann es tagsüber Sonne sein – nicht erkennen, lernen
        sunny = solar is not None and max(solar) >= EXT_SUN_MAX
        floor_on = model.floor and floor_input > EXT_HEAT_SHARE * flow_over
        if (sunny or floor_on) and not model.inputs_learned(solar, floor=floor_on):
            return False  # Sonne/Fußboden noch nicht gelernt: erst lernen, dann erkennen
        self.room_ext[climate] = {"start_ts": start, "t_start": ref, "peak": t1, "rise": round(t1 - ref, 2)}
        log.info("Raum %s: Fremdwärme ab %s (+%.1f K, %.1f K/h unerklärt, Ventil %.0f %%)", climate,
                 local(start).strftime("%d.%m. %H:%M"), t1 - ref, unexplained, valve)
        return True

    def ext_frac(self, hour: int) -> float:
        """Anteil der Stunde, in dem irgendein gewählter Raum Fremdwärme hatte (abgeschlossen oder laufend)."""
        spans = [(r["start_ts"], r["end_ts"]) for r in self.store.query(
            "SELECT start_ts, end_ts, room FROM room_events WHERE end_ts > ? AND start_ts < ?", (hour, hour + 3600))
            if r["room"] in self.rooms]
        spans += [(ev["start_ts"], ev["start_ts"] + EXT_MAX_H * 3600) for ev in self.room_ext.values()]
        covered, cur = 0, None  # Vereinigung der Abschnitte innerhalb der Stunde
        for a, b in sorted((max(a, hour), min(b, hour + 3600)) for a, b in spans):
            if b <= a:
                continue
            if cur is not None and a <= cur[1]:
                cur[1] = max(cur[1], b)
            else:
                covered += cur[1] - cur[0] if cur else 0
                cur = [a, b]
        covered += cur[1] - cur[0] if cur else 0
        return covered / 3600

    # ------------------------------------------------------------------ Ventile (0.39)
    def learn_valves(self, until: int | None = None, budget_s: float | None = VALVE_BUDGET_S) -> bool:
        """Ventilmodelle bis zum letzten abgeschlossenen Raumschritt lernen (``valve_learn``) – beim ersten Mal bzw.
        nach einem neuen Modellstand aus der gespeicherten Historie, je Abschnitt höchstens ein Tag und je Takt
        höchstens ``budget_s`` Sekunden (None: bis fertig). True, solange noch nachgelernt wird."""
        until = self._last_room_step if until is None else until
        if until is None or not self.valves:
            return False
        running = {c: int(ev["start_ts"]) for c, ev in self.room_ext.items()}
        return valve_learn.learn_all(self.store, self.valves, until, running, budget_s)

    def valve_info(self, climate: str, now_ts: int | None = None) -> dict | None:
        """Ventilmodell eines Raums für die Anzeige (``ValveModel.summary``) mit der Schrittweite seiner Thermostate."""
        model, room = self.valves.get(climate), self.found.room_of(climate)
        if model is None or room is None:
            return None
        step, source = valve_learn.step_of(room, self.states)
        return model.summary(step, source, now_ts, catching_up=valve_learn.behind(model, self._last_room_step),
                             group=valve_learn.group_of(room))

    # ------------------------------------------------------------------ Haus
    def aggregate_hour(self, hour: int, learn: bool = True) -> None:
        rows = self.store.query(
            "SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (hour, hour + 3600)
        )
        if len(rows) < 2:
            if learn:  # Stunde fehlt (Neustart): offene Tage abschließen und, gibt es Stundenwerte aus der
                self.learn_house_hour(hour)  # Langzeitstatistik, die Stunde davor daraus lernen (seit 0.42.1)
            return
        # Gas bis zum ersten Wert der nächsten Stunde – sonst fehlen die letzten 5 min jeder Stunde
        gas_rows = self.store.query(
            "SELECT ts, gas FROM samples WHERE ts >= ? AND ts <= ? AND gas IS NOT NULL ORDER BY ts",
            (hour, hour + 3600),
        )
        gas = [r["gas"] for r in gas_rows]
        raw_kwh = None
        if len(gas) >= 2:
            raw_kwh = (gas[-1] - gas[0]) * self.gas_factor
        recent = [r["gas_kwh"] for r in self.store.query(
            "SELECT gas_kwh FROM hourly WHERE ts >= ? AND ts < ? AND gas_kwh IS NOT NULL", (hour - 7 * 86400, hour))]
        day = local(hour).date().isoformat()
        gas_kwh, spike = None, None
        if raw_kwh is not None and raw_kwh < 0:
            self.store.add_daily(day, "ignored_gas_negative", 1)
            log.info("Gaszähler %s: Stand gesunken (%.1f kWh) – Stunde nicht gelernt", local(hour).strftime("%d.%m. %H:00"), raw_kwh)
        elif raw_kwh is not None:
            status, gas_kwh = plausibility.gas_hour(raw_kwh, rows, self.nominal_kw(), recent)
            if status == "spike":
                spike = raw_kwh
                self.store.add_daily(day, "ignored_gas_spike", 1)
                self.store.add_daily(day, "gas_spike_kwh", raw_kwh)
                log.info("Gaszähler %s: Sprung um %.1f kWh (Nachtrag?) – nicht gelernt, zählt nur im Verbrauch",
                         local(hour).strftime("%d.%m. %H:00"), raw_kwh)
            elif status == "missed":
                self.store.add_daily(day, "ignored_gas_missed", 1)
                log.info("Gaszähler %s: Brenner lief, Zähler zählte nur %.2f kWh – Stunde nicht gelernt",
                         local(hour).strftime("%d.%m. %H:00"), raw_kwh)
        t_out = [r["t_out"] for r in rows if r["t_out"] is not None]
        dhw = [r["dhw"] for r in rows if r["dhw"] is not None]
        home = [r["home_count"] for r in rows if r["home_count"] is not None]
        vac = [r["vacation"] for r in rows if r["vacation"] is not None]
        t_in, t_src, complete = self._mean_room_temp(hour, hour + 3600)
        # Anteil des Gases fürs Warmwasser: nach Brennerleistung gewichtet (eine 15-min-Ladung mit
        # voller Leistung ist mehr als ein Viertel der Stunde, wenn der Kessel sonst taktet)
        pw_all = pw_dhw = 0.0
        for r in rows:
            if r["burner_on"]:
                pw = r["burner_pow"] if r["burner_pow"] is not None else 100.0
                pw_all += pw
                if r["dhw"]:
                    pw_dhw += pw
        dhw_energy = (pw_dhw / pw_all) if pw_all > 0 and dhw else (0.0 if dhw else None)
        row = (
            hour,
            sum(t_out) / len(t_out) if t_out else None,
            t_in,
            gas_kwh,
            sum(dhw) / len(dhw) if dhw else None,
            sum(1 for h in home if h > 0) / len(home) if home else None,
            sum(vac) / len(vac) if vac else None,
            spike,
            dhw_energy,
            self.ext_frac(hour),
            t_src,
        )
        # zusammenführen (0.42.1): was hier unbekannt ist (keine Raumwerte, kein Gaszählerstand …), behält den
        # gespeicherten Wert; der Gaswert einer Stunde mit bekannten Zählerständen gilt auch, wenn er verworfen ist.
        # Innentemperatur aus unvollständigen Räumen füllt nur eine Lücke
        self.store.add_hourly([row], replace=("gas_kwh", "gas_spike_kwh") if raw_kwh is not None else (),
                              keep=() if complete else ("t_in",))
        self.store.commit()
        if learn:
            self.learn_house_hour(hour)

    def _mean_room_temp(self, start: int, end: int) -> tuple[float | None, str | None, bool]:
        """Innentemperatur der Stunde aus den Raumwerten: (Wert, Herkunft, vollständig). Vollständig – mindestens so
        viele Schlüssel mit Werten wie Räume gewählt sind –: das Mittel aller Raumwerte wie bisher („rooms“).
        Unvollständig (seit 0.42.1: ein Raum fehlt, etwa nach einem Wechsel der Thermostate nur ein Raum nachgeholt):
        jeder Schlüssel um seinen Versatz zum Hausmittel der Tage davor berichtigt („rooms~“, ``indoor_mean``) –
        sonst wäre die Stunde um den Abstand der übrigen Räume verschoben; ohne jeden Bezug davor das einfache Mittel
        („rooms-“). ``aggregate_hour`` füllt damit nur Lücken."""
        rows = self.store.query(
            "SELECT room, AVG(temp) AS t, COUNT(*) AS n FROM room_samples WHERE ts >= ? AND ts < ? AND temp IS NOT NULL "
            "GROUP BY room", (start, end))
        if not rows:
            return None, None, False
        if len(rows) >= len(self.found.rooms):
            return sum(r["t"] * r["n"] for r in rows) / sum(r["n"] for r in rows), "rooms", True
        if self.store.query("SELECT 1 FROM hourly WHERE ts = ? AND t_in IS NOT NULL", (start - start % 3600,)):
            return None, None, False  # gespeicherte Innentemperatur bleibt – nichts zu berichtigen
        fitted = indoor_mean.room_fits(self.store, [r["room"] for r in rows], start)
        if fitted is None:  # kein Bezug (ein Raum fehlt von Anfang an): einfaches Mittel wie bisher
            return sum(r["t"] * r["n"] for r in rows) / sum(r["n"] for r in rows), indoor_mean.PLAIN_SRC, False
        value, _ = indoor_mean.mean([(r["room"], r["t"]) for r in rows], fitted)
        return value, "rooms~" if value is not None else None, False

    def learn_house_hour(self, hour: int) -> None:
        """Lernt die Stunde *vor* ``hour`` – mit beiden Nachbarn für eine mittige Ableitung.

        Gas der Stunde h passt zur Temperaturänderung über h (Mitte h−1 bis Mitte h+1),
        nicht zur Differenz der Stundenmittel h und h−1 (die läge eine halbe Stunde früher).
        """
        h = hour - 3600
        self.learn_house_days(h)
        rows = self.store.query("SELECT * FROM hourly WHERE ts IN (?, ?, ?)", (h - 3600, h, hour))
        by = {r["ts"]: r for r in rows}
        prev, cur, nxt = by.get(h - 3600), by.get(h), by.get(hour)
        if not cur or cur["gas_kwh"] is None or cur["t_in"] is None or cur["t_out"] is None:
            return
        if not prev or not nxt or prev["t_in"] is None or nxt["t_in"] is None:
            return
        d_in = (nxt["t_in"] - prev["t_in"]) / 2
        # Heizperiode: das gleitende 24-h-Mittel draußen, nicht die einzelne kühle Sommernacht
        r24 = self.store.query("SELECT AVG(t_out) AS t, COUNT(*) AS n FROM hourly "
                               "WHERE ts > ? AND ts <= ? AND t_out IS NOT NULL", (h - 86400, h))
        if not r24 or r24[0]["n"] < 12 or r24[0]["t"] > HEAT_SEASON_24H:
            return
        dhw = cur["dhw_frac"]
        ext = cur["ext_frac"] or 0  # Fremdwärme (Kamin, Ofen) in einem Raum: weder Auskühlen noch Bilanz lernen
        gas_free = all(r["gas_kwh"] is not None and r["gas_kwh"] <= GAS_FREE_KWH for r in (prev, cur, nxt))
        if gas_free and not dhw and not ext and local(h).hour in NIGHT_HOURS:
            last = self.store.query("SELECT MAX(ts) AS t FROM hourly WHERE ts < ? AND (gas_kwh IS NULL OR gas_kwh > ?)",
                                    (h, GAS_FREE_KWH))[0]["t"]
            self.house.learn_cooling(cur["t_in"], cur["t_out"], d_in, 99.0 if last is None else (h - last) / 3600)
        if (dhw is not None and dhw > 0) or ext > 0:
            return  # Warmwasserladung verfälscht die Heizbilanz (auch eine kurze verschiebt kWh), Fremdwärme auch
        self.house.learn(self.heating_gas(cur), cur["t_in"], cur["t_out"], d_in)

    def learn_house_days(self, h: int) -> None:
        """Alle Kalendertage vor dem der Stunde ``h`` abschließen (Tageswerte lernen), die noch offen sind.

        Nicht an die Stunde 00 gebunden: Fehlt sie (Add-on oder HA um Mitternacht neu gestartet),
        schließt die nächste gelernte Stunde den Tag ab. Ist ``h`` die Stunde 23, ist ihr Tag schon
        vollständig. Ein schon gelernter Tag wird vom Hausmodell nicht noch einmal gezählt, deshalb
        beginnt es nach einem Neustart gefahrlos beim Vortag.
        """
        d = local(h)
        end = d.date() + timedelta(days=1 if d.hour == 23 else 0)  # erster noch nicht abgeschlossener Tag
        open_day = self.__dict__.get("_house_day_open") or end - timedelta(days=1)
        open_day = max(open_day, end - timedelta(days=HOUSE_DAYS_CATCH_UP))
        while open_day < end:
            self.learn_house_day(midnight(datetime.combine(open_day, datetime.min.time())))
            open_day += timedelta(days=1)
        self._house_day_open = end

    def learn_house_day(self, ts: int) -> None:
        """Tagesmittel (Heizgas, innen–außen) des Kalendertags von ``ts`` lernen – siehe HouseModel.learn_day.

        Ein Urlaubstag zählt nur als Zustand für die Folgetage (sein Unterschied innen–außen), nicht als
        Lerntag – ebenso ein Tag mit mindestens EXT_DAY_SKIP_H Stunden Fremdwärme (Kaminabend).
        """
        day = local(ts)
        span = (midnight(day), midnight(day + timedelta(days=1)))
        rows = self.store.query("SELECT * FROM hourly WHERE ts >= ? AND ts < ? AND gas_kwh IS NOT NULL "
                                "AND t_in IS NOT NULL AND t_out IS NOT NULL", span)
        if len(rows) < DAILY_MIN_HOURS:
            return
        vacation = any((r["vacation_frac"] or 0) > 0.5 for r in rows)
        ext_h = self.store.query("SELECT COALESCE(SUM(ext_frac), 0) AS h FROM hourly WHERE ts >= ? AND ts < ?", span)[0]["h"]
        gas = []
        for r in rows:
            frac = r["dhw_energy_frac"] if r["dhw_energy_frac"] is not None else r["dhw_frac"]
            gas.append(self.heating_gas(r) if frac is None else r["gas_kwh"] * (1 - min(max(frac, 0.0), 1.0)))
        n = len(rows)
        self.house.learn_day(day.date().isoformat(), sum(r["t_in"] - r["t_out"] for r in rows) / n,
                             sum(r["t_out"] for r in rows) / n, sum(gas) / n,
                             learn=not vacation and ext_h < EXT_DAY_SKIP_H)

    def heating_gas(self, row) -> float:
        """Heizgas einer Stunde: bei Langzeitstunden (Warmwasser unbekannt) ohne den üblichen Warmwasserbedarf."""
        gas = row["gas_kwh"]
        if row["dhw_frac"] is None and row["dhw_energy_frac"] is None:
            base = self.dhw_base()
            if base:
                d = local(row["ts"])
                gas = max(gas - base[(d.weekday(), d.hour)], 0.0)
        return gas

    def dhw_base(self) -> dict[tuple[int, int], float] | None:
        """Warmwasser-Gas je Wochentag und Stunde (kWh) aus den Sommerstunden – täglich neu berechnet.

        Vor HeatPilot weiß die Langzeitstatistik nicht, wann Warmwasser geladen wurde. Im Sommer
        ist alles Gas Warmwasser (inkl. Legionellenschaltung zur festen Stunde) – dieses Profil
        wird im Winter von den Langzeitstunden abgezogen, sonst lernt das Haus das Warmwasser mit.
        """
        cached = self.__dict__.get("_dhw_base")
        if cached is not None and time.time() - cached[0] < 86400:
            return cached[1]
        rows = self.store.query("SELECT ts, t_out, gas_kwh FROM hourly ORDER BY ts")
        cells: dict[tuple[int, int], list[float]] = {}
        window: deque[tuple[int, float]] = deque()
        total, n = 0.0, 0
        for r in rows:
            if r["t_out"] is not None:
                window.append((r["ts"], r["t_out"]))
                total += r["t_out"]
            while window and window[0][0] <= r["ts"] - 86400:
                total -= window.popleft()[1]
            if r["gas_kwh"] is None or len(window) < 12 or total / len(window) < DHW_BASE_SUMMER_24H:
                continue
            d = local(r["ts"])
            cells.setdefault((d.weekday(), d.hour), []).append(r["gas_kwh"])
            n += 1
        base = None
        if n >= DHW_BASE_MIN_HOURS:
            by_hour = {hr: [v for (wd, h2), vals in cells.items() if h2 == hr for v in vals] for hr in range(24)}
            base = {}
            for wd in range(7):
                for hr in range(24):
                    v = cells.get((wd, hr), [])
                    if len(v) < DHW_BASE_MIN_CELL:
                        v = by_hour[hr]
                    base[(wd, hr)] = sum(v) / len(v) if v else 0.0
        self._dhw_base = (time.time(), base)
        return base

    def take_snapshot(self, ts: int) -> None:
        t_out = self.outdoor_now()
        self.store.add_snapshot(
            ts, "house", "", self.house.summary(self.cfg.design_outdoor_temp, t_out, 20.0)
        )
        for climate, m in self.rooms.items():
            self.store.add_snapshot(ts, "room", climate, m.summary(t_out or 5.0, self.cfg.max_flow_temp))
        self.store.commit()

    # ------------------------------------------------------------------ Zeiten
    def update_schedule(self, now_ts: int | None = None, max_age_s: int = 3600) -> None:
        """Nachtzeiten und Temperaturen aus Tado neu lernen (stündlich).

        Bei Steuerung ``heatpilot`` schreibt HeatPilot die Zeiten selbst – daraus zu lernen wäre
        zirkulär: nichts lernen, nur merken, bis wann gesteuert wurde (``KV_MANAGED_UNTIL``); nach dem
        Zurückschalten zählen erst Nächte danach.
        """
        now_ts = now_ts or int(time.time())
        if now_ts - self._schedule_ts < max_age_s:
            return
        self._schedule_ts = now_ts
        if self.cfg.is_managing():
            self.store.put(KV_MANAGED_UNTIL, now_ts)
            return
        learned = schedule.learn(self.store, now_ts, set(self.cfg.floor_heating_rooms),
                                 {r.climate for r in self.found.rooms}, self.found.persons,
                                 since_ts=self.store.get(KV_MANAGED_UNTIL))
        eff = schedule.effective(self.cfg, learned)
        if eff.as_dict() != self.eff.as_dict():
            log.info("Zeiten/Temperaturen: %s (gelernt aus %d Nächten)", eff.as_dict(), learned["nights"])
        self.schedule_learned, self.eff = learned, eff
        self.store.put("schedule.learned", learned)

    def update_sleep(self, now_ts: int | None = None, max_age_s: int = 3600) -> None:
        """Schlafenszeiten aus dem Hausstromverbrauch (0.36) nachrechnen – stündlich, nur mit Leistungssensoren.

        Gerechnet werden nur fertige Nächte, die noch nicht gespeichert sind (nach der Datenübernahme beim
        ersten Takt alle). Daraus der Wochenplan in ``cfg.night_plan_learned`` – unabhängig von der
        Steuerung; wirksam nur mit ``night_plan_auto``, sonst Anzeige.
        """
        now_ts = now_ts or int(time.time())
        if not self.found.power or now_ts - self._sleep_ts < max_age_s:
            return
        self._sleep_ts = now_ts
        self.sleep_nights = sleep.learn(self.store, now_ts)
        self._sleep = sleep.summary(self.sleep_nights, now_ts, self.cfg, self.found.power, self.found.power_exclude)
        learned = sleep.learned_plan(self._sleep["plan"])
        if learned != self.cfg.night_plan_learned:
            log.info("Nachtzeiten aus dem Stromverbrauch (%d Nächte, %s): %s", self._sleep["nights"],
                     "wirksam" if self._sleep["active"] else "nur Anzeige",
                     " ".join(f"{d['day']} {d['start'] or '–'}/{d['end'] or '–'}" for d in self._sleep["plan"]))
            self.cfg.night_plan_learned = learned
            self.eff = schedule.effective(self.cfg, self.schedule_learned)
