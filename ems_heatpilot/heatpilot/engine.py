"""Herzstück: Zustände sammeln, Modelle lernen, Auswertungen berechnen.

Phase 1 = Beobachten: Das Add-on liest nur und schreibt nichts an die Therme.
Es veröffentlicht eigene ``sensor.heatpilot_*``-Sensoren und eine Weboberfläche.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from . import analysis, guards
from .config import Config
from .dhw import DhwModel, DhwShadow
from .discovery import BOILER_ROLES, HEAT_MODES, THERMOSTAT_ROLES, Discovered
from .models import ROOM_STEP_H, HouseModel, RoomModel
from .presence import PersonLearner, haversine_km, next_arrival
from .store import Store

log = logging.getLogger(__name__)

ENABLE_DELAY_MAX_MIN = 15
ON_STATES = {"on", "true", "1", "heat", "heating"}
STALE_S = 12 * 3600


def num(value) -> float | None:
    if isinstance(value, str):
        value = value.strip().replace(",", ".")  # z. B. EMS-ESP-Zähler "4321,57"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN raus


def flag(value) -> int | None:
    if value is None or value in ("unknown", "unavailable"):
        return None
    if isinstance(value, bool):
        return int(value)
    if num(value) is not None:
        return int(num(value) > 0)
    return int(str(value).lower() in ON_STATES)


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


class Engine:
    def __init__(self, cfg: Config, store: Store, found: Discovered) -> None:
        self.cfg = cfg
        self.store = store
        self.found = found
        self.states: dict[str, dict] = {}
        self.ems: dict[str, dict] = {}  # direkt von EMS-ESP gelesen (optional)
        self.gas_factor = 1.0 if (found.gas_unit or "").lower() == "kwh" else cfg.gas_kwh_per_m3
        self.house = HouseModel.from_dict(store.get("model.house")) if store.get("model.house") else HouseModel()
        self.rooms: dict[str, RoomModel] = {}
        for room in found.rooms:
            saved = store.get(f"model.room.{room.climate}")
            self.rooms[room.climate] = RoomModel.from_dict(saved) if saved else RoomModel(room.name)
        self.persons: dict[str, PersonLearner] = {}
        for p in found.persons:
            saved = store.get(f"model.person.{p}")
            self.persons[p] = PersonLearner.from_dict(saved) if saved else PersonLearner(p)
        saved = store.get("model.dhw")
        self.dhw = DhwModel.from_dict(saved) if saved else DhwModel()
        self.dhw_shadow = DhwShadow()
        self._prev_dhw: tuple[int, float | None] | None = None
        self.dhw_rec: dict | None = None
        self.forecast: list[dict] = []
        self.last_plan: dict | None = None
        self._last_burner: int | None = None
        # Live-Zähler (Nachlauf, Taktsperre) – ereignisgenau statt 5-min-Raster
        self._live_ts: float | None = None
        self._live_prev: dict = {}
        self._demand: bool | None = None
        self._mode_wait_start: float | None = None  # Beginn der aktuellen Wartephase
        self._last_enabled: int | None = None
        off = store.query("SELECT MAX(ts) AS t FROM burner_events WHERE state = 0")
        self._last_off_ts: float | None = off[0]["t"] if off else None
        self._live_entities = {
            *(r.climate for r in found.rooms),
            *(found.boiler.get(k) for k in ("burner_on", "burner_power", "dhw_active", "min_period")),
            found.thermostat.get("hc_mode"),
        }
        self._last_room_step: int | None = store.get("last_room_step")
        self._last_hour: int | None = store.get("last_hour")

    # ------------------------------------------------------------------ Zustände
    def on_state(self, entity_id: str, state: dict | None) -> None:
        if state is None:
            self.states.pop(entity_id, None)
            return
        self.states[entity_id] = state
        if entity_id in self._live_entities:
            self.update_live()

    def set_ems(self, values: dict[str, dict]) -> None:
        self.ems = values
        self.update_live()

    def _val(self, entity_id: str | None):
        if not entity_id or entity_id not in self.states:
            return None
        return self.states[entity_id].get("state")

    def _boiler(self, role: str):
        ems = self.ems.get("boiler")
        if ems:
            for key in BOILER_ROLES.get(role, ()):
                if key in ems:
                    return ems[key]
        return self._val(self.found.boiler.get(role))

    def _thermo(self, role: str):
        ems = self.ems.get("thermostat")
        if ems:
            for key in THERMOSTAT_ROLES.get(role, ()):
                if key in ems:
                    return ems[key]
        return self._val(self.found.thermostat.get(role))

    def burner_on(self) -> int | None:
        on = flag(self._boiler("burner_on"))
        if on is None:
            p = num(self._boiler("burner_power"))
            on = None if p is None else int(p > 0)
        return on

    def heat_enabled(self) -> int | None:
        """Heizfreigabe am Regler: Betriebsart Tag = heizen, Nacht = aus (Absenkmodus Frostschutz)."""
        mode = self._thermo("hc_mode")
        if mode is None or mode in ("unknown", "unavailable"):
            return None
        return int(str(mode).strip().lower() in HEAT_MODES)

    def setpoint_demand(self) -> bool | None:
        """Wärmebedarf aus Tado-Soll vs. Ist – reagiert sofort, ohne Ventil-Verzögerung.

        Bedarf ab 0,2 K unter Soll, kein Bedarf wenn alle Räume am Soll sind,
        dazwischen bleibt der letzte Zustand (Hysterese).
        """
        gaps = []
        for r in self.found.rooms:
            st = self.states.get(r.climate)
            if not st:
                continue
            a = st.get("attributes", {})
            temp, target = num(a.get("current_temperature")), num(a.get("temperature"))
            if st.get("state") == "off" or target is None:
                gaps.append(-99.0)
            elif temp is not None:
                gaps.append(target - temp)
        if not gaps:
            return None
        if max(gaps) >= 0.2:
            self._demand = True
        elif max(gaps) <= 0.0:
            self._demand = False
        return self._demand

    def update_live(self, now: float | None = None, record_events: bool = True) -> None:
        """Zeit seit dem letzten Aufruf den passenden Tageszählern gutschreiben."""
        now = time.time() if now is None else now
        burner = self.burner_on()
        if burner is not None and burner != self._last_burner:
            if record_events:
                self.store.add_burner_event(int(now), burner)
            if burner == 0 and self._last_burner == 1:
                self._last_off_ts = now
            self._last_burner = burner

        prev = self._live_prev
        if self._live_ts is not None and prev:
            dt = min(now - self._live_ts, 2 * self.cfg.sample_interval_s)
            day = local(self._live_ts).date().isoformat()
            if dt > 0:
                if prev["overrun"]:
                    self.store.add_daily(day, "overrun_min", dt / 60)
                    if prev["kw"] is not None:
                        self.store.add_daily(day, "overrun_kwh", prev["kw"] * dt / 3600)
                if prev["blocked"]:
                    self.store.add_daily(day, "blocked_min", dt / 60)
                if prev["waiting"]:
                    self.store.add_daily(day, "waiting_min", dt / 60)
                if prev["mode_wait"]:
                    self.store.add_daily(day, "mode_wait_min", dt / 60)

        dhw = flag(self._boiler("dhw_active")) or 0
        demand = self.setpoint_demand()
        enabled = self.heat_enabled()
        period = num(self._boiler("min_period"))
        power, nom = num(self._boiler("burner_power")), num(self._boiler("nom_power"))
        locked = bool(
            period and self._last_off_ts is not None and now - self._last_off_ts < period * 60
        )
        if enabled is not None:
            if self._last_enabled is not None and enabled != self._last_enabled:
                # viele Wechsel am Tag = Automationen schalten gegeneinander
                self.store.add_daily(local(now).date().isoformat(), "mode_switch_count", 1)
            self._last_enabled = enabled
        mode_wait = burner == 0 and demand is True and enabled == 0 and not locked
        if mode_wait and self._mode_wait_start is None:
            self._mode_wait_start = now
        elif not mode_wait and self._mode_wait_start is not None:
            # Kurze Wartephasen = Verzögerung der Automation, lange = gewollte Sperre (Nacht/weg)
            minutes = (now - self._mode_wait_start) / 60
            day_ = local(self._mode_wait_start).date().isoformat()
            if minutes <= ENABLE_DELAY_MAX_MIN:
                self.store.add_daily(day_, "enable_delay_min", minutes)
                self.store.add_daily(day_, "enable_delay_count", 1)
            self._mode_wait_start = None
        self._live_prev = {
            # Brenner heizt, obwohl laut Tado-Soll kein Raum mehr Wärme braucht
            "overrun": burner == 1 and not dhw and demand is False,
            "kw": None if power is None or nom is None else power / 100 * nom,
            # Räume wollen Wärme, Brenner darf wegen Taktsperre nicht
            "blocked": burner == 0 and not dhw and demand is True and locked,
            # Räume wollen Wärme, Brenner aus (egal warum)
            "waiting": burner == 0 and demand is True,
            # Räume wollen Wärme, aber der Regler steht (noch) auf Nacht – z. B. weil
            # die Automation nur alle 10 min prüft
            "mode_wait": mode_wait,
        }
        self._live_ts = now

    def outdoor_now(self) -> float | None:
        vals = [num(self._val(e)) for e in self.found.outdoor]
        vals = [v for v in vals if v is not None]
        if vals:
            return min(vals)  # wie bisher: Minimum der Außenfühler
        if self.found.weather and self.found.weather in self.states:
            return num(self.states[self.found.weather]["attributes"].get("temperature"))
        return None

    def snapshot(self) -> tuple[dict, list[tuple], list[tuple]]:
        burner_pow = num(self._boiler("burner_power"))
        burner_on = self.burner_on()
        dhw = flag(self._boiler("dhw_active"))
        heating = flag(self._boiler("heating_pump"))
        if heating is None and burner_on is not None:
            heating = int(burner_on and not dhw)
        home = 0
        persons = []
        for p in self.found.persons:
            st = self.states.get(p)
            if not st:
                continue
            is_home = st.get("state") == "home"
            home += is_home
            attrs = st.get("attributes", {})
            dist = None
            if self.found.home_zone and "latitude" in attrs:
                dist = haversine_km(*self.found.home_zone, attrs["latitude"], attrs["longitude"])
            persons.append((p, int(is_home), dist))
        sample = {
            "t_out": self.outdoor_now(),
            "flow": num(self._boiler("flow_temp")),
            "sel_flow": num(self._boiler("sel_flow_temp")),
            "ret": num(self._boiler("return_temp")),
            "burner_pow": burner_pow,
            "burner_on": burner_on,
            "heating": heating,
            "dhw": dhw,
            "gas": num(self._val(self.found.gas)),
            "min_period": num(self._boiler("min_period")),
            "home_count": home if self.found.persons else None,
            "boiler_temp": num(self._boiler("boiler_temp")),
            "calc_flow": num(self._thermo("hc_calc_flow")),
            "heating_off": flag(self._boiler("heating_off")),
            "burn_starts": num(self._boiler("burner_starts")),
            "heat_starts": num(self._boiler("heat_starts")),
            "heat_min": num(self._boiler("heat_runtime")),
            "hc_day": self.heat_enabled(),
            "hc_seltemp": num(self._thermo("hc_seltemp")),
            "remote_temp": num(self._thermo("hc_remotetemp")),
            "pump_mod": num(self._boiler("pump_mod")),
            "dhw_temp": num(self._boiler("dhw_temp")),
            "flame": num(self._boiler("flame_current")),
            "demand_pct": num(self._val(self.found.demand_sensor)),
            "dhw_set": num(self._boiler("dhw_seltemp")),
        }
        rooms = []
        for r in self.found.rooms:
            st = self.states.get(r.climate)
            if not st:
                continue
            a = st.get("attributes", {})
            valve = num(self._val(r.heating_power))
            if valve is None:
                valve = 100.0 if a.get("hvac_action") == "heating" else 0.0
            hum = num(self._val(r.humidity))
            if hum is None:
                hum = num(a.get("current_humidity"))
            target = num(a.get("temperature")) if st.get("state") != "off" else None
            rooms.append((r.climate, num(a.get("current_temperature")), target, valve, hum))
        return sample, rooms, persons

    # ------------------------------------------------------------------ Takt
    def tick(self, now_ts: int | None = None) -> None:
        now_ts = now_ts or int(time.time())
        ts = now_ts - now_ts % self.cfg.sample_interval_s
        self.update_live(now_ts)
        sample, rooms, persons = self.snapshot()
        self.store.add_sample(ts, sample)
        self.store.add_room_samples((ts, *r) for r in rooms)
        self.store.add_person_samples((ts, *p) for p in persons)
        now = local(ts)
        for p, is_home, dist in persons:
            self.persons[p].observe(now, bool(is_home), dist)
        self.dhw_step(ts, sample)
        self.store.commit()

        step = ts - ts % int(ROOM_STEP_H * 3600)
        if self._last_room_step is None:
            self._last_room_step = step
        while self._last_room_step + int(ROOM_STEP_H * 3600) <= step:
            self.learn_rooms(self._last_room_step, self._last_room_step + int(ROOM_STEP_H * 3600))
            self._last_room_step += int(ROOM_STEP_H * 3600)

        hour = ts - ts % 3600
        if self._last_hour is None:
            self._last_hour = hour
        while self._last_hour + 3600 <= hour:
            self.aggregate_hour(self._last_hour)
            self._last_hour += 3600
            if local(self._last_hour).hour == 0:
                self.take_snapshot(self._last_hour)
        self.save()

    # ------------------------------------------------------------------ Lernen
    def learn_rooms(self, start: int, end: int) -> None:
        sys_rows = {
            r["ts"]: r
            for r in self.store.query(
                "SELECT ts, flow, heating, burner_on, dhw, t_out FROM samples WHERE ts BETWEEN ? AND ?",
                (start, end),
            )
        }
        for climate, model in self.rooms.items():
            rows = self.store.query(
                "SELECT ts, temp, valve FROM room_samples WHERE room=? AND ts BETWEEN ? AND ? ORDER BY ts",
                (climate, start, end),
            )
            rows = [r for r in rows if r["temp"] is not None]
            if len(rows) < 3 or rows[-1]["ts"] - rows[0]["ts"] < 0.8 * ROOM_STEP_H * 3600:
                continue
            inputs, outs = [], []
            for r in rows:
                s = sys_rows.get(r["ts"])
                if not s or s["flow"] is None or s["t_out"] is None:
                    continue
                active = s["heating"] if s["heating"] is not None else (s["burner_on"] or 0)
                if s["dhw"]:
                    active = 0
                inputs.append(max(s["flow"] - r["temp"], 0) * (r["valve"] or 0) / 100 * (active or 0))
                outs.append(s["t_out"])
            if len(inputs) < 3:
                continue
            t0, t1 = rows[0]["temp"], rows[-1]["temp"]
            dt_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600
            if abs(t1 - t0) > 2.0:  # Fenster offen o. ä. – nicht lernen
                continue
            model.learn(t0, t1, sum(inputs) / len(inputs), sum(outs) / len(outs), dt_h)

    def aggregate_hour(self, hour: int, learn: bool = True) -> None:
        rows = self.store.query(
            "SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (hour, hour + 3600)
        )
        if len(rows) < 2:
            return
        gas = [r["gas"] for r in rows if r["gas"] is not None]
        gas_kwh = None
        if len(gas) >= 2 and gas[-1] >= gas[0]:
            gas_kwh = (gas[-1] - gas[0]) * self.gas_factor
        t_out = [r["t_out"] for r in rows if r["t_out"] is not None]
        dhw = [r["dhw"] for r in rows if r["dhw"] is not None]
        home = [r["home_count"] for r in rows if r["home_count"] is not None]
        t_in = self._mean_room_temp(hour, hour + 3600)
        row = (
            hour,
            sum(t_out) / len(t_out) if t_out else None,
            t_in,
            gas_kwh,
            sum(dhw) / len(dhw) if dhw else None,
            sum(1 for h in home if h > 0) / len(home) if home else None,
        )
        self.store.add_hourly([row])
        self.store.commit()
        if learn:
            self.learn_house_hour(hour)

    def _mean_room_temp(self, start: int, end: int) -> float | None:
        r = self.store.query(
            "SELECT AVG(temp) AS t FROM room_samples WHERE ts >= ? AND ts < ? AND temp IS NOT NULL",
            (start, end),
        )
        return r[0]["t"] if r and r[0]["t"] is not None else None

    def learn_house_hour(self, hour: int) -> None:
        rows = self.store.query("SELECT * FROM hourly WHERE ts IN (?, ?)", (hour - 3600, hour))
        by = {r["ts"]: r for r in rows}
        cur, prev = by.get(hour), by.get(hour - 3600)
        if not cur or cur["gas_kwh"] is None or cur["t_in"] is None or cur["t_out"] is None:
            return
        if cur["dhw_frac"] is not None and cur["dhw_frac"] > 0.3:
            return  # Warmwasserladung verfälscht die Heizbilanz
        d_in = cur["t_in"] - prev["t_in"] if prev and prev["t_in"] is not None else 0.0
        self.house.learn(cur["gas_kwh"], cur["t_in"], cur["t_out"], d_in)

    def take_snapshot(self, ts: int) -> None:
        t_out = self.outdoor_now()
        self.store.add_snapshot(
            ts, "house", "", self.house.summary(self.cfg.design_outdoor_temp, t_out, 20.0)
        )
        for climate, m in self.rooms.items():
            self.store.add_snapshot(ts, "room", climate, m.summary(t_out or 5.0, self.cfg.max_flow_temp))
        self.store.commit()

    def dhw_step(self, ts: int, sample: dict) -> None:
        """Warmwasser lernen und die Spar-Strategie im Schatten mitlaufen lassen."""
        t_cur = sample.get("dhw_temp")
        prev, self._prev_dhw = self._prev_dhw, (ts, t_cur)
        if prev is None:
            return
        dt_h = (ts - prev[0]) / 3600
        nom = num(self._boiler("nom_power"))
        kw = None
        if sample.get("burner_pow") is not None and nom:
            kw = sample["burner_pow"] / 100 * nom
        now = local(ts)
        res = self.dhw.step(now, dt_h, prev[1], t_cur, bool(sample.get("dhw")), kw)
        home = sample.get("home_count")
        arrival = None
        if home == 0:
            nxt = next_arrival(list(self.persons.values()), now)
            arrival = nxt["eta"] if nxt else None
        check = guards.check_dhw(self.dhw)
        if check["ok"]:
            self.dhw_rec = self.dhw.recommend(now, None if home is None else home > 0, arrival, self.cfg)
            shadow_target = self.dhw_rec["target"]
        else:
            # unsicheres Modell: keine Empfehlung, Schatten folgt dem heutigen Soll
            self.dhw_rec = {"target": None, "reason": "lernt noch: " + "; ".join(check["reasons"])}
            shadow_target = sample.get("dhw_set") or self.cfg.dhw_comfort_temp
        sh = self.dhw_shadow.step(
            self.dhw, t_cur, shadow_target, res["draw_k"], dt_h, self.cfg.dhw_comfort_temp
        )
        if sh["cold_draw"] and check["ok"]:
            self.dhw.note_cold_draw()
        day = now.date().isoformat()
        for key, val in (
            ("dhw_draw_k", res["draw_k"]),
            ("dhw_loss_kwh", res["loss_kwh"]),
            ("dhw_charge_kwh", res["charge_kwh"]),
            ("dhw_loss_strategy_kwh", sh["loss_kwh"]),
            ("dhw_cold_draws", sh["cold_draw"]),
        ):
            if val:
                self.store.add_daily(day, key, val)

    def save(self) -> None:
        self.store.put("model.dhw", self.dhw.to_dict())
        self.store.put("model.house", self.house.to_dict())
        for climate, m in self.rooms.items():
            self.store.put(f"model.room.{climate}", m.to_dict())
        for p, learner in self.persons.items():
            self.store.put(f"model.person.{p}", learner.to_dict())
        self.store.put("last_room_step", self._last_room_step)
        self.store.put("last_hour", self._last_hour)

    # ------------------------------------------------------------------ Auswertung
    def summary(self) -> dict:
        now_ts = int(time.time())
        now = local(now_ts)
        t_out = self.outdoor_now()
        _, rooms, _ = self.snapshot()
        temps = [r[1] for r in rooms if r[1] is not None]
        t_in = sum(temps) / len(temps) if temps else None

        day_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        events = [
            (r["ts"], r["state"])
            for r in self.store.query(
                "SELECT ts, state FROM burner_events WHERE ts >= ? ORDER BY ts", (day_start - 86400,)
            )
        ]
        cycling = analysis.cycling_stats(events, day_start, now_ts + 1)
        today = now.date().isoformat()
        counters = self.counter_days(today).get(today)
        if counters:  # Zähler der Therme sind genauer als Ein/Aus-Ereignisse
            cycling.update(counters)
        live = self.store.daily(today).get(today, {})
        cycling["blocked_min"] = round(live.get("blocked_min", 0.0), 1)
        cycling["waiting_min"] = round(live.get("waiting_min", 0.0), 1)
        cycling["mode_wait_min"] = round(live.get("mode_wait_min", 0.0), 1)
        cycling["enable_delay_min"] = round(live.get("enable_delay_min", 0.0), 1)
        cycling["enable_delay_count"] = int(live.get("enable_delay_count", 0))
        cycling["mode_switch_count"] = int(live.get("mode_switch_count", 0))
        cycling["overrun_min"] = round(live.get("overrun_min", 0.0), 1)
        cycling["overrun_kwh"] = round(live.get("overrun_kwh", 0.0), 2)
        starts, runtime = num(self._boiler("heat_starts")), num(self._boiler("heat_runtime"))
        cycling["lifetime_run_per_start_min"] = (
            round(runtime / starts, 1) if starts and runtime else None
        )

        hourly = [dict(r) for r in self.store.query("SELECT * FROM hourly ORDER BY ts")]
        nom = num(self._boiler("nom_power"))
        savings = analysis.estimate_savings(
            hourly, self.house, self.cfg.away_temp, self.cfg.night_temp, self.cfg.is_night,
            p_max_kw=(nom or 18.0) * 0.95,
        )
        house_check = guards.check_house(self.house)
        room_checks = {c: guards.check_room(m) for c, m in self.rooms.items()}
        gas_total = sum(h["gas_kwh"] or 0 for h in hourly)
        span_days = (hourly[-1]["ts"] - hourly[0]["ts"]) / 86400 if len(hourly) > 1 else 0
        price, fee = self.cfg.gas_price_eur_per_kwh, self.cfg.gas_base_fee_eur_per_year
        last_year = [h for h in hourly if h["ts"] >= now_ts - 365 * 86400]
        year_kwh = sum(h["gas_kwh"] or 0 for h in last_year)
        year_span = (now_ts - last_year[0]["ts"]) / 86400 if last_year else 0
        costs = {
            "kwh": round(year_kwh, 0),
            "days": round(year_span, 1),
            "eur": round(year_kwh * price + fee * year_span / 365, 0),
            # Jahreswert nur mit vollen 12 Monaten – Hochrechnen aus Teilen verzerrt (Sommer/Winter)
            "eur_per_year": round(year_kwh * price + fee, 0) if year_span >= 360 else None,
            "full_year": year_span >= 360,
        }

        persons = [p.eta(now) for p in self.persons.values()]
        arrival = next_arrival(list(self.persons.values()), now)
        plan = None
        if arrival and arrival["eta"] and t_out is not None and house_check["ok"]:
            room_inputs = []
            for climate, temp, target, _, _ in rooms:
                if (temp is not None and target is not None and climate in self.rooms
                        and room_checks[climate]["ok"]):
                    room_inputs.append((self.rooms[climate], temp, target))
            t_out_arrival = self.outdoor_forecast_at(arrival["eta"])
            plan = analysis.preheat_plan(
                room_inputs, self.house, arrival["eta"], now,
                t_out if t_out_arrival is None else t_out_arrival, self.cfg.max_flow_temp,
            )
        self.last_plan = plan

        flow_needed = None
        if t_out is not None:
            needs = [
                self.rooms[c].required_flow(target, t_out)
                for c, temp, target, _, _ in rooms
                if target is not None and c in self.rooms and room_checks[c]["ok"]
            ]
            if needs:
                flow_needed = round(min(max(max(needs), 25), self.cfg.max_flow_temp), 1)

        return {
            "now": now.isoformat(),
            "mode": self.cfg.mode,
            "t_out": t_out,
            "t_in": None if t_in is None else round(t_in, 2),
            "house": self.house.summary(self.cfg.design_outdoor_temp, t_out, t_in),
            "rooms": [
                {**self.rooms[c].summary(t_out if t_out is not None else 5.0, self.cfg.max_flow_temp),
                 "temp": temp, "target": target, "valve": valve, "humidity": hum,
                 "check": room_checks[c]}
                for c, temp, target, valve, hum in rooms if c in self.rooms
            ],
            "cycling": cycling,
            "costs": costs,
            "dhw": self.dhw_summary(),
            "limits": {
                "away_temp": self.cfg.away_temp,
                "night_temp": self.cfg.night_temp,
                "dhw_min_temp": self.cfg.dhw_min_temp,
            },
            "guards": {
                "house": house_check,
                "dhw": guards.check_dhw(self.dhw),
                "rooms_ok": sum(1 for r in room_checks.values() if r["ok"]),
                "rooms_total": len(room_checks),
            },
            "savings": {
                "hours": savings.hours,
                "cold_hours": savings.cold_hours,
                "reliable": house_check["ok"],
                "actual_kwh": savings.actual_kwh,
                "saved_kwh": savings.saved_kwh,
                "saved_pct": savings.saved_pct,
                "saved_eur": round(savings.saved_kwh * self.cfg.gas_price_eur_per_kwh, 2),
                # Jahreswert nur mit vollem Jahr, sonst bleibt der Prozentwert die Aussage
                "year_kwh": round(savings.saved_kwh * 365 / span_days, 0) if span_days >= 360 else None,
                "year_eur": round(savings.saved_kwh * 365 / span_days * self.cfg.gas_price_eur_per_kwh, 0)
                if span_days >= 360 else None,
                "gas_total_kwh": round(gas_total, 1),
                "span_days": round(span_days, 1),
                "daily": savings.daily[-60:],
            },
            "persons": [{**p, "eta": p["eta"].isoformat() if p["eta"] else None} for p in persons],
            "arrival": None if not arrival else {**arrival, "eta": arrival["eta"].isoformat()},
            "plan": None if not plan else {
                **plan,
                "best": None if not plan.get("best") else {**plan["best"], "start": plan["best"]["start"].isoformat()},
                "options": [{**o, "start": o["start"].isoformat()} for o in plan.get("options", [])],
            },
            "flow_recommendation": flow_needed,
            "flow_current_setpoint": num(self._boiler("sel_flow_temp")),
            "flow_calculated": num(self._thermo("hc_calc_flow")),
            "min_period": num(self._boiler("min_period")),
            "hyst_on": num(self._boiler("hyst_on")),
            "hyst_off": num(self._boiler("hyst_off")),
            "heating_off": flag(self._boiler("heating_off")),
            "heat_enabled": self.heat_enabled(),
            "hc_mode": self._thermo("hc_mode"),
            "hc_seltemp": num(self._thermo("hc_seltemp")),
            "remote_temp": num(self._thermo("hc_remotetemp")),
            "pump_mod": num(self._boiler("pump_mod")),
            "demand_pct": num(self._val(self.found.demand_sensor)),
            "setpoint_demand": self._demand,
            "emsesp": bool(self.ems),
            "discovered": self.found.as_dict(),
        }

    def dhw_summary(self) -> dict:
        t_now = num(self._boiler("dhw_temp"))
        rows = self.store.query(
            "SELECT key, SUM(value) AS v, COUNT(DISTINCT day) AS days FROM daily "
            "WHERE key LIKE 'dhw_%' GROUP BY key"
        )
        tot = {r["key"]: r["v"] for r in rows}
        days = max((r["days"] for r in rows), default=0)
        loss, loss_s = tot.get("dhw_loss_kwh", 0.0), tot.get("dhw_loss_strategy_kwh", 0.0)
        charge = tot.get("dhw_charge_kwh", 0.0)
        today = local(time.time()).date().isoformat()
        return {
            **self.dhw.summary(t_now),
            "temp": t_now,
            "set": num(self._boiler("dhw_seltemp")),
            "recommendation": self.dhw_rec,
            "today": {k: round(v, 2) for k, v in self.store.daily(today).get(today, {}).items()
                      if k.startswith("dhw_")},
            "period_days": days,
            "charge_kwh": round(charge, 1),
            "loss_kwh": round(loss, 1),
            "loss_strategy_kwh": round(loss_s, 1),
            "saved_kwh": round(loss - loss_s, 1),
            "saved_pct": round(100 * (loss - loss_s) / charge, 1) if charge > 1 else None,
            "saved_eur": round((loss - loss_s) * self.cfg.gas_price_eur_per_kwh, 2),
            "cold_draws": int(tot.get("dhw_cold_draws", 0)),
        }

    def counter_days(self, since_day: str) -> dict[str, dict]:
        """Starts und Laufzeit je Tag aus den Zählern der Therme (Differenz Max-Min)."""
        rows = self.store.query(
            "SELECT date(ts, 'unixepoch', 'localtime') AS day, "
            "MAX(burn_starts) - MIN(burn_starts) AS starts, "
            "MAX(heat_starts) - MIN(heat_starts) AS heat_starts, "
            "MAX(heat_min) - MIN(heat_min) AS heat_min "
            "FROM samples WHERE burn_starts IS NOT NULL AND ts >= ? GROUP BY day",
            (int(datetime.fromisoformat(since_day).timestamp()),),
        )
        out = {}
        for r in rows:
            d = {"starts": int(r["starts"]), "source": "zaehler"}
            if r["heat_starts"] is not None:
                d["heat_starts"] = int(r["heat_starts"])
                if r["heat_starts"] and r["heat_min"] is not None:
                    d["avg_run_min"] = round(r["heat_min"] / r["heat_starts"], 1)
            out[r["day"]] = d
        return out

    def history(self, days: int = 30) -> dict:
        since = int(time.time()) - days * 86400
        hourly = [dict(r) for r in self.store.query("SELECT * FROM hourly WHERE ts >= ? ORDER BY ts", (since,))]
        snaps = [
            {"ts": r["ts"], "kind": r["kind"], "key": r["key"], "data": json.loads(r["data"])}
            for r in self.store.query("SELECT * FROM snapshots ORDER BY ts")
        ]
        daily_cycles = []
        for d in range(min(days, 30)):
            day = local(time.time()).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=d)
            s, e = int(day.timestamp()), int((day + timedelta(days=1)).timestamp())
            ev = [(r["ts"], r["state"]) for r in self.store.query(
                "SELECT ts, state FROM burner_events WHERE ts >= ? AND ts < ? ORDER BY ts", (s - 86400, e))]
            st = analysis.cycling_stats(ev, s, e)
            daily_cycles.append({"day": day.date().isoformat(), **st})
        first = daily_cycles[-1]["day"] if daily_cycles else local(time.time()).date().isoformat()
        counters = self.counter_days(first)
        live = self.store.daily(first)
        for d in daily_cycles:
            d.update(counters.get(d["day"], {}))
            for k, v in live.get(d["day"], {}).items():
                d[k] = round(v, 2)
        return {"hourly": hourly, "snapshots": snaps, "cycles": list(reversed(daily_cycles))}

    # ------------------------------------------------------------------ Vorhersage
    def outdoor_forecast_at(self, when: datetime) -> float | None:
        best = None
        for f in self.forecast:
            try:
                t = datetime.fromisoformat(f["datetime"])
            except (KeyError, ValueError):
                continue
            if best is None or abs((t - when).total_seconds()) < abs((best[0] - when).total_seconds()):
                best = (t, num(f.get("temperature")))
        return best[1] if best else None
