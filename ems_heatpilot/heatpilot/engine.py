"""Herzstück: Zustände sammeln, Modelle lernen, Auswertungen berechnen.

Phase 1 = Beobachten: Das Add-on liest nur und schreibt nichts an die Therme.
Es veröffentlicht eigene ``sensor.heatpilot_*``-Sensoren und eine Weboberfläche.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from . import analysis
from .config import Config
from .discovery import Discovered
from .models import ROOM_STEP_H, HouseModel, RoomModel
from .presence import PersonLearner, haversine_km, next_arrival
from .store import Store

log = logging.getLogger(__name__)

ON_STATES = {"on", "true", "1", "heat", "heating"}
STALE_S = 12 * 3600


def num(value) -> float | None:
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
        self.forecast: list[dict] = []
        self.last_plan: dict | None = None
        self._last_burner: int | None = None
        self._last_room_step: int | None = store.get("last_room_step")
        self._last_hour: int | None = store.get("last_hour")

    # ------------------------------------------------------------------ Zustände
    def on_state(self, entity_id: str, state: dict | None) -> None:
        if state is None:
            self.states.pop(entity_id, None)
            return
        self.states[entity_id] = state
        if entity_id == self.found.boiler.get("burner_on"):
            on = flag(state.get("state"))
            if on is not None and on != self._last_burner:
                self.store.add_burner_event(int(time.time()), on)
                self.store.commit()
                self._last_burner = on

    def _val(self, entity_id: str | None):
        if not entity_id or entity_id not in self.states:
            return None
        return self.states[entity_id].get("state")

    def _boiler(self, role: str):
        return self._val(self.found.boiler.get(role))

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
        burner_on = flag(self._boiler("burner_on"))
        if burner_on is None and burner_pow is not None:
            burner_on = int(burner_pow > 0)
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
        sample, rooms, persons = self.snapshot()
        self.store.add_sample(ts, sample)
        self.store.add_room_samples((ts, *r) for r in rooms)
        self.store.add_person_samples((ts, *p) for p in persons)
        now = local(ts)
        for p, is_home, dist in persons:
            self.persons[p].observe(now, bool(is_home), dist)
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

    def save(self) -> None:
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
        cycling["blocked_min"] = self._blocked_today(day_start, now_ts, events)

        hourly = [dict(r) for r in self.store.query("SELECT * FROM hourly ORDER BY ts")]
        savings = analysis.estimate_savings(
            hourly, self.house, self.cfg.away_temp, self.cfg.night_temp, self.cfg.is_night
        )
        gas_total = sum(h["gas_kwh"] or 0 for h in hourly)
        span_days = (hourly[-1]["ts"] - hourly[0]["ts"]) / 86400 if len(hourly) > 1 else 0

        persons = [p.eta(now) for p in self.persons.values()]
        arrival = next_arrival(list(self.persons.values()), now)
        plan = None
        if arrival and arrival["eta"] and t_out is not None:
            room_inputs = []
            for climate, temp, target, _, _ in rooms:
                if temp is not None and target is not None and climate in self.rooms:
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
                if target is not None and c in self.rooms and self.rooms[c].rls.updates > 20
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
                 "temp": temp, "target": target, "valve": valve, "humidity": hum}
                for c, temp, target, valve, hum in rooms if c in self.rooms
            ],
            "cycling": cycling,
            "savings": {
                "hours": savings.hours,
                "actual_kwh": savings.actual_kwh,
                "saved_kwh": savings.saved_kwh,
                "saved_pct": savings.saved_pct,
                "saved_eur": round(savings.saved_kwh * self.cfg.gas_price_eur_per_kwh, 2),
                "year_kwh": round(savings.saved_kwh * 365 / span_days, 0) if span_days >= 14 else None,
                "year_eur": round(savings.saved_kwh * 365 / span_days * self.cfg.gas_price_eur_per_kwh, 0)
                if span_days >= 14 else None,
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
            "min_period": num(self._boiler("min_period")),
            "discovered": self.found.as_dict(),
        }

    def _blocked_today(self, day_start: int, now_ts: int, events: list[tuple[int, int]]) -> float:
        rows = self.store.query(
            "SELECT ts, burner_on, min_period, dhw FROM samples WHERE ts >= ? ORDER BY ts", (day_start,)
        )
        room_rows = self.store.query(
            "SELECT ts, temp, target, valve FROM room_samples WHERE ts >= ?", (day_start,)
        )
        demand_ts = {
            r["ts"] for r in room_rows
            if r["temp"] is not None and r["target"] is not None
            and r["target"] - r["temp"] >= 0.3 and (r["valve"] or 0) >= 40
        }
        offs = [ts for ts, st in events if st == 0]
        last_off: dict[int, int | None] = {}
        i = 0
        cur = None
        for r in rows:
            while i < len(offs) and offs[i] <= r["ts"]:
                cur = offs[i]
                i += 1
            last_off[r["ts"]] = cur
        samples = [
            {"ts": r["ts"], "burner_on": r["burner_on"], "min_period": r["min_period"],
             "dhw": r["dhw"], "demand": r["ts"] in demand_ts}
            for r in rows
        ]
        return analysis.blocked_minutes(samples, last_off, self.cfg.sample_interval_s)

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
