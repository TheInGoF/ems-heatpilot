"""Simuliertes Home Assistant zum Testen ohne echte Anlage.

Simuliert 30 Tage: Haus mit 3 Tado-Räumen, EMS-ESP-Therme mit 30 min
Taktsperre, Gaszähler, Außenfühler, zwei Personen mit Arbeitszeiten.
Stellt WebSocket- und REST-API so bereit, wie das Add-on sie nutzt.

    python tools/fake_ha.py  # lauscht auf :8123
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone

from aiohttp import web

HOME = (53.55, 10.0)
WORK = {"person.ingo": (53.60, 10.25), "person.partner": (53.48, 9.90)}
ROOMS = {
    "wohnzimmer": dict(a=0.05, b=0.035, c=0.10),
    "buero": dict(a=0.04, b=0.05, c=0.05),
    "schlafzimmer": dict(a=0.035, b=0.04, c=0.02),
}
STEP = 300
DAYS = 30


def simulate():
    rng = random.Random(7)
    now = int(time.time())
    now -= now % STEP
    start = now - DAYS * 86400
    hist: dict[str, list] = {}
    temps = {r: 20.5 for r in ROOMS}
    gas = 4321.0
    burner = False
    last_off = -10**9
    starts = 1000
    heat_starts, burn_min, heat_min = 900, 4000.0, 3000.0
    t_boiler = 30.0
    mode = "Nacht"
    targets_lag = [21.0, 21.0]  # Tado-Ventile reagieren ~10 min verzögert
    states = {}

    def rec(eid, ts, s, a=None):
        lst = hist.setdefault(eid, [])
        if lst and lst[-1]["s"] == str(s) and a is None:
            return
        e = {"s": str(s), "lu": float(ts)}
        if a is not None:
            e["a"] = a
        lst.append(e)

    for ts in range(start, now + 1, STEP):
        dt = datetime.fromtimestamp(ts)
        hour = dt.hour + dt.minute / 60
        day = (ts - start) / 86400
        t_out = 6 - day * 0.15 + 4 * math.sin(2 * math.pi * (hour - 9) / 24) + rng.gauss(0, 0.3)
        sun = max(0, math.sin(math.pi * (hour - 8) / 9)) if 8 <= hour <= 17 else 0

        home = {}
        for p in WORK:
            weekday = dt.weekday() < 5
            away = weekday and (8 <= hour < 17 if p == "person.ingo" else 7.5 <= hour < 16.5)
            commute = weekday and (16.9 <= hour < 17.4 if p == "person.ingo" else 16.4 <= hour < 16.9)
            if commute:
                frac = (hour - (16.9 if p == "person.ingo" else 16.4)) / 0.5
                lat = WORK[p][0] + (HOME[0] - WORK[p][0]) * frac
                lon = WORK[p][1] + (HOME[1] - WORK[p][1]) * frac
                st = "not_home"
            elif away:
                lat, lon, st = *WORK[p], "not_home"
            else:
                lat, lon, st = *HOME, "home"
            home[p] = st == "home"
            rec(p, ts, st, {"latitude": lat, "longitude": lon, "friendly_name": p[7:].title()})
            states[p] = (st, {"latitude": lat, "longitude": lon, "friendly_name": p[7:].title()})

        night = hour >= 22.5 or hour < 6
        target = 18.0 if night else 21.0
        targets_lag.append(target)
        valve_target = targets_lag.pop(0)
        valves = {r: max(0, min(100, (valve_target - temps[r] + 0.1) * 80)) for r in ROOMS}
        demand = max(valves.values())
        sel_flow = round(34 + (14 - t_out) * 0.8)  # Heizkurve des Reglers
        dhw = 6.5 <= hour < 6.75 or 18.0 <= hour < 18.25
        pow_pct = 0
        for minute in range(5):  # minütlich: Kesselwasser, Brenner mit Hysterese -2/+10 K
            heat_demand = demand > 0
            # wie Ingos Automation: alle 10 min Tag/Nacht, "Flamme hält Tag"
            if (ts // 60 + minute) % 10 == 0:
                mode = "Tag" if (heat_demand or burner) and not night else "Nacht"
            heating_ok = mode == "Tag"
            if not burner and (dhw or (heating_ok and t_boiler < sel_flow - 2)) \
                    and ts - last_off >= 25 * 60:
                burner, starts = True, starts + 1
                heat_starts += 0 if dhw else 1
            elif burner and not dhw and (not heating_ok or t_boiler > sel_flow + 10):
                burner, last_off = False, ts
            load = sum(0.06 * max(t_boiler - temps[r], 0) * valves[r] / 100 for r in ROOMS)
            pow_pct = (100 if dhw else max(30, min(100, load / 18 * 100))) if burner else 0
            p_kw = pow_pct / 100 * 18
            t_boiler += ((0 if dhw else p_kw) - load - 0.05 * (t_boiler - 20)) / 0.06 / 60
            if burner:
                burn_min += 1
                heat_min += 0 if dhw else 1
                gas += p_kw / 0.95 / 60 / 10.64
            for r, p in ROOMS.items():
                u = max(t_boiler - temps[r], 0) * valves[r] / 100
                temps[r] += (p["a"] * u + p["b"] * (t_out - temps[r]) + p["c"] + 0.15 * sun) / 60
        flow = t_boiler
        for r in ROOMS:
            attrs = {"current_temperature": round(temps[r], 1), "temperature": target,
                     "hvac_action": "heating" if valves[r] > 0 else "idle",
                     "current_humidity": 50, "friendly_name": r.title()}
            rec(f"climate.{r}", ts, "heat", attrs)
            states[f"climate.{r}"] = ("heat", attrs)
            for eid, val in ((f"sensor.{r}_heating", round(valves[r])),
                             (f"sensor.{r}_temperature", round(temps[r], 1)),
                             (f"sensor.{r}_humidity", 50)):
                rec(eid, ts, val)
                states[eid] = (str(val), None)
        vals = {
            "sensor.boiler_curflowtemp": round(flow, 1),
            "number.boiler_selflowtemp": sel_flow,
            "sensor.boiler_rettemp": round(flow - 8, 1),
            "sensor.boiler_curburnpow": round(pow_pct),
            "binary_sensor.boiler_burngas": "on" if burner else "off",
            "sensor.boiler_burnstarts": starts,
            "binary_sensor.boiler_heatingpump": "on" if (burner and not dhw) else "off",
            "binary_sensor.boiler_dhw_charging": "on" if dhw else "off",
            "sensor.boiler_outdoortemp": round(t_out + 1, 1),
            "number.boiler_burnminperiod": 25,
            "number.boiler_nompower": 18,
            "sensor.boiler_burnworkmin": round(burn_min),
            "sensor.boiler_heatstarts": heat_starts,
            "sensor.boiler_heatworkmin": round(heat_min),
            "switch.boiler_heatingoff": "off",
            "select.thermostat_hc1_mode": mode,
            "sensor.thermostat_hc1_targetflowtemp": round(34 + (14 - t_out) * 0.8),
            "sensor.aussen_nord": round(t_out, 1),
            "sensor.aussen_sued": round(t_out + 2 * sun, 1),
            "sensor.aussen_temperatur": round(t_out, 1),
            "sensor.gaszaehler": round(gas, 3),
        }
        for eid, val in vals.items():
            rec(eid, ts, val)
            states[eid] = (str(val), None)
    return hist, states, start, now


HIST, STATES, START, END = simulate()
UNITS = {"sensor.gaszaehler": {"unit_of_measurement": "m³", "device_class": "gas", "state_class": "total_increasing"}}
for r in ROOMS:
    UNITS[f"sensor.{r}_heating"] = {"unit_of_measurement": "%"}
    UNITS[f"sensor.{r}_humidity"] = {"device_class": "humidity", "unit_of_measurement": "%"}
    UNITS[f"sensor.{r}_temperature"] = {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"}
PUBLISHED: dict[str, dict] = {}


def state_objs():
    out = []
    for eid, (s, a) in STATES.items():
        out.append({"entity_id": eid, "state": s, "attributes": a or UNITS.get(eid, {})})
    out.append({"entity_id": "zone.home", "state": "2", "attributes": {"latitude": HOME[0], "longitude": HOME[1]}})
    out.append({"entity_id": "weather.forecast_home", "state": "cloudy", "attributes": {"temperature": 3.0}})
    return out


def registry():
    reg = []
    for r in ROOMS:
        for eid in (f"climate.{r}", f"sensor.{r}_heating", f"sensor.{r}_humidity", f"sensor.{r}_temperature"):
            reg.append({"entity_id": eid, "platform": "tado", "device_id": f"dev_{r}"})
    return reg


def history(msg):
    s = datetime.fromisoformat(msg["start_time"]).timestamp()
    e = datetime.fromisoformat(msg["end_time"]).timestamp()
    out = {}
    for eid in msg["entity_ids"]:
        allrows = HIST.get(eid, [])
        before = [x for x in allrows if x["lu"] < s]
        rows = ([{**before[-1], "lu": s}] if before else []) + [dict(x) for x in allrows if s <= x["lu"] < e]
        if msg.get("no_attributes"):
            for x in rows:
                x.pop("a", None)
        if rows:
            out[eid] = rows
    return out


def statistics(msg):
    s = datetime.fromisoformat(msg["start_time"]).timestamp()
    e = datetime.fromisoformat(msg["end_time"]).timestamp()
    out = {}
    for eid in msg["statistic_ids"]:
        rows = HIST.get(eid, [])
        buckets: dict[int, list[float]] = {}
        for x in rows:
            h = int(x["lu"] - x["lu"] % 3600)
            if s <= h < e:
                buckets.setdefault(h, []).append(float(x["s"]))
        res = []
        prev_last = None
        for h in sorted(buckets):
            vals = buckets[h]
            entry = {"start": h * 1000.0, "end": (h + 3600) * 1000.0, "mean": sum(vals) / len(vals)}
            if eid == "sensor.gaszaehler":
                entry["sum"] = vals[-1]
                entry["change"] = vals[-1] - (prev_last if prev_last is not None else vals[0])
                prev_last = vals[-1]
            res.append(entry)
        out[eid] = res
    return out


async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    await ws.send_json({"type": "auth_required"})
    await ws.receive_json()
    await ws.send_json({"type": "auth_ok"})
    async for msg in ws:
        m = msg.json()
        t, mid = m["type"], m["id"]
        result = None
        if t == "get_states":
            result = state_objs()
        elif t == "config/entity_registry/list":
            result = registry()
        elif t == "history/history_during_period":
            result = history(m)
        elif t == "recorder/statistics_during_period":
            result = statistics(m)
        elif t == "call_service":
            fc = [{"datetime": datetime.fromtimestamp(END + i * 3600, timezone.utc).isoformat(), "temperature": 2.0 - 0.1 * i} for i in range(48)]
            result = {"response": {"weather.forecast_home": {"forecast": fc}}}
        await ws.send_json({"id": mid, "type": "result", "success": True, "result": result})
    return ws


def ems_api(device):
    """Nachbildung von GET /api/boiler bzw. /api/thermostat (Kurznamen, verschachtelt)."""
    out: dict = {}
    for eid, (val, _a) in STATES.items():
        prefix = f"{device}_"
        name = eid.split(".", 1)[1]
        if not name.startswith(prefix):
            continue
        key = name[len(prefix):]
        try:
            v = float(val)
        except ValueError:
            v = val
        if key.startswith(("dhw_", "hc1_")):
            group, sub = key.split("_", 1)
            out.setdefault(group, {})[sub] = v
        else:
            out[key] = v
    return out


async def ems_handler(request):
    return web.json_response(ems_api(request.match_info["device"]))


async def post_state(request):
    PUBLISHED[request.match_info["eid"]] = await request.json()
    return web.json_response({})


async def get_published(_request):
    return web.json_response(PUBLISHED)


app = web.Application()
app.router.add_get("/websocket", ws_handler)
app.router.add_post("/api/states/{eid}", post_state)
app.router.add_get("/published", get_published)
app.router.add_get("/api/{device}", ems_handler)

if __name__ == "__main__":
    web.run_app(app, port=8123)
