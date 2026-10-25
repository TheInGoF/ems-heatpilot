"""Auswertung für Oberfläche und API: Zusammenfassung, Reiter-Daten und Wettervorhersage.

``SummaryMixin`` rechnet aus Messwerten und Modellen, was die Weboberfläche zeigt: die große
Zusammenfassung (``summary``) mit Vorheizplan, Ersparnis und Prüfungen, dazu Woche, Anwesenheit,
Vorlauf-Studie, Empfehlungen, Historie und die Kachel „Vorhersage“, seit 0.42.1 die Karte „Modelle“
(``model_card``). Es lernt nichts. In die Datenbank
schreibt es nur den Stand der Tado-Empfehlungen (``advice.track``: neu oder erledigt); sonst merkt es
sich Zwischenergebnisse im Speicher (Cache) und den letzten Vorheizplan (``last_plan``).
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import datetime, timedelta

from . import advice, analysis, flowstudy, guards, model_card, readiness, weekly
from .presence import arrival_unknown, short_trip
from .values import aware, flag, local, midnight, num, parse_time

log = logging.getLogger(f"{__package__}.engine")  # im Protokoll wie vor der Aufteilung: heatpilot.engine

EXT_COUNT_DAYS = 7       # Zähler je Raum in der Übersicht

# Wettervorhersage (seit 0.36.1): ein Vorhersagepunkt gilt nur so nah am gefragten Zeitpunkt – sonst keiner
# (eine Ankunft in drei Tagen bekommt nicht den letzten Punkt einer 24-h-Vorhersage)
FORECAST_MAX_GAP_MIN = 90
FORECAST_AHEAD_H = 12  # Kachel „Vorhersage“: Minimum, Maximum und Trend der nächsten so vielen Stunden


class SummaryMixin:
    """Auswertungen der ``Engine``.

    Nutzt von der Engine fast alles: Zustände, Modelle, ``store``, ``forecast``, und die Methoden der
    anderen Mixins (``snapshot``, ``counter_days``, ``dhw_summary``, ``heating_gas`` …).
    """

    # ------------------------------------------------------------------ Zusammenfassung
    def summary(self) -> dict:
        now_ts = int(time.time())
        now = local(now_ts)
        t_out = self.outdoor_now()
        _, rooms, _ = self.snapshot()
        temps = [r[1] for r in rooms if r[1] is not None]
        t_in = sum(temps) / len(temps) if temps else None

        day_start = midnight(now)
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
        if "avg_run_min" not in (counters or {}):  # ohne Heizlaufzeit-Zähler: Läufe ohne Warmwasser
            avg, _ = analysis.heat_runs(self.store, day_start, now_ts + 1)
            cycling["avg_run_min"] = None if avg is None else round(avg, 1)
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
        nom = self.nominal_kw()
        room_checks = {c: guards.check_room(m) for c, m in self.rooms.items()}
        check_rooms = {c: m for c, m in self.rooms.items()
                       if room_checks[c]["ok"] and not m.floor and c not in self.cfg.floor_heating_rooms}
        room_hourly: dict[int, dict[str, float]] = {}
        if check_rooms:
            for r in self.store.query(
                "SELECT ts - ts % 3600 AS h, room, AVG(temp) AS t FROM room_samples "
                "WHERE temp IS NOT NULL GROUP BY h, room"
            ):
                if r["room"] in check_rooms:
                    room_hourly.setdefault(r["h"], {})[r["room"]] = r["t"]
        savings = analysis.estimate_savings(
            hourly, self.house, self.eff.away_temp, self.eff.night_temp, self.eff.is_night,
            p_max_kw=(nom or 18.0) * 0.95, rooms=check_rooms, room_hourly=room_hourly,
            max_flow=self.cfg.max_flow_temp,
        )
        house_check = guards.check_house(self.house)
        names_by_climate = {r.climate: r.name for r in self.found.rooms}
        gas_total = sum((h["gas_kwh"] or 0) + (h["gas_spike_kwh"] or 0) for h in hourly)
        span_days = savings.hours / 24  # nur Stunden, in denen sich Heizgas bestimmen ließ
        year_days = savings.span_hours / 24  # Hochrechnung: Urlaub zählt mit (dort wird nichts gespart)
        price, fee = self.cfg.gas_price_eur_per_kwh, self.cfg.gas_base_fee_eur_per_year
        last_year = [h for h in hourly if h["ts"] >= now_ts - 365 * 86400]
        year_kwh = sum((h["gas_kwh"] or 0) + (h["gas_spike_kwh"] or 0) for h in last_year)
        year_span = (now_ts - last_year[0]["ts"]) / 86400 if last_year else 0
        costs = {
            "kwh": round(year_kwh, 0),
            "days": round(year_span, 1),
            "eur": round(year_kwh * price + fee * year_span / 365, 0),
            # Jahreswert nur mit vollen 12 Monaten – Hochrechnen aus Teilen verzerrt (Sommer/Winter)
            "eur_per_year": round(year_kwh * price + fee, 0) if year_span >= 360 else None,
            "full_year": year_span >= 360,
        }

        persons = [{**p.eta(now), "status": p.status(now)["text"], "long_away": p.long_away}
                   for p in self.persons.values()]
        vacation = self.on_vacation(now_ts)
        if vacation:  # Rückkehr: schnelle GPS-Annäherung vor geplantem Ende vor Datum
            arrival = self.vacation.arrival(now, list(self.persons.values()))
        else:
            arrival = _e.next_arrival(list(self.persons.values()), now)
        plan = None
        if arrival and arrival["eta"] and t_out is not None and house_check["ok"]:
            room_inputs, skipped = self.plan_rooms(rooms, room_checks)
            # Außentemperatur zur Ankunft: Vorhersagepunkt in der Nähe, sonst die aktuelle (seit 0.36.1 sichtbar)
            point = self.forecast_point_at(arrival["eta"])
            t_used = t_out if point is None else point[1]
            plan = analysis.preheat_plan(
                room_inputs, self.house, arrival["eta"], now, t_used, self.cfg.max_flow_temp,
                dhw=self.dhw_for_arrival(arrival["eta"], now),
            )
            if plan is not None:  # worauf der Plan sich verlässt
                plan = {**plan, "arrival_source": arrival.get("source"), "skipped_warm": skipped,
                        "arrival_confidence": arrival.get("confidence"), "arrival_person": arrival.get("person"),
                        "t_out_used": t_used, "t_out_source": "aktuell" if point is None else "vorhersage",
                        "t_out_at": None if point is None else point[0].astimezone().isoformat()}
        self.last_plan = plan
        ext = self.ext_summary(now_ts)

        flow_needed = None
        if t_out is not None:
            needs = [
                self.rooms[c].required_flow(target, t_out)
                for c, temp, target, _, _ in rooms
                if target is not None and c in self.rooms and room_checks[c]["ok"]
            ]
            if needs:
                flow_needed = round(min(max(max(needs), 25), self.cfg.max_flow_temp), 1)

        ready = readiness.assess(self, now_ts)
        return {
            "now": now.isoformat(),
            "mode": self.cfg.mode,
            "t_out": t_out, "wind": getattr(self, "wind_now", None),  # Wind m/s, letzter Messwert (0.55, setzt tick)
            "t_in": None if t_in is None else round(t_in, 2),
            "house": self.house.summary(self.cfg.design_outdoor_temp, t_out, t_in),
            "rooms": [
                {**self.rooms[c].summary(t_out if t_out is not None else 5.0, self.cfg.max_flow_temp),
                 "temp": temp, "target": target, "valve": valve, "humidity": hum,
                 "check": room_checks[c], **ext[c],
                 # seit 0.39: Schlüssel und Ventilverhalten (Kennlinie, Sprungantwort, Güte, Text)
                 "climate": c, "valve_model": self.valve_info(c, now_ts)}
                for c, temp, target, valve, hum in rooms if c in self.rooms
            ],
            "cycling": cycling,
            "costs": costs,
            "dhw": self.dhw_summary(),
            "vacation": {
                "active": vacation,
                "until": (self.vacation.end(now).isoformat() if vacation and self.vacation.end(now) else None),
                "until_exact": bool(vacation and self.vacation.trip(now) and self.vacation.trip(now).end_exact),
                "ranges": [[t.start.isoformat(), t.end.isoformat(), t.end_exact] for t in self.vacation.trips],
                "returned": sorted(self.vacation.returned),
            },
            "advice": self.tado_advice(),
            "ignored": self.ignored(),
            "flow_study": self.flow_study(),
            "schedule": {"learned": self.schedule_learned, "effective": self.eff.as_dict(),
                         # bei Steuerung heatpilot wäre Lernen aus Tado zirkulär: der Wochenplan gilt
                         "learning": self.cfg.learn_schedule and not self.cfg.is_managing()},
            "sleep": self.sleep_summary(),  # Nachtzeiten aus dem Stromverbrauch (0.36)
            # seit 0.36.1: Wettervorhersage (Kachel) und „Bereit für die Steuerung?“ (Stufe und ein Satz)
            "forecast": self.forecast_summary(now_ts),
            "readiness": {k: ready[k] for k in ("level", "headline")},
            "control_mode": self.cfg.control_mode,
            "limits": {
                "away_temp": self.eff.away_temp,      # damit rechnet die Ersparnis (gelernt, nie darunter)
                "night_temp": self.eff.night_temp,
                "away_floor": self.cfg.away_temp,     # feste Untergrenzen aus den Einstellungen
                "night_floor": self.cfg.night_temp,
                "dhw_min_temp": self.cfg.dhw_min_temp,
                "max_flow_temp": self.cfg.max_flow_temp,
            },
            "models": model_card.collect(self, now_ts),  # 0.42.1: Karte „Modelle“ – Güte, Lernzeitraum, Warnung
            "guards": {
                "house": house_check,
                "dhw": guards.check_dhw(self.dhw),
                "rooms_ok": sum(1 for r in room_checks.values() if r["ok"]),
                "rooms_total": len(room_checks),
            },
            "savings": {
                "hours": savings.hours,
                "cold_hours": savings.cold_hours,
                "rooms_checked": len(check_rooms),
                "rooms_total": len(self.rooms),
                # plausible Fußbodenräume werden nicht je Raum simuliert (zu träge für Stundenschritte)
                "rooms_ok_floor": sum(1 for c, m in self.rooms.items() if room_checks[c]["ok"] and c not in check_rooms),
                "room_hours": savings.room_hours,
                "room_cold": [{"room": c, "name": names_by_climate.get(c, c), "hours": n}
                              for c, n in (savings.room_cold or {}).items() if n],
                "lead_by_rooms": savings.lead_by_rooms,
                "lead_by_house": savings.lead_by_house,
                "reliable": house_check["ok"],
                "actual_kwh": savings.actual_kwh,
                "saved_kwh": savings.saved_kwh,
                "saved_pct": savings.saved_pct,
                "saved_eur": round(savings.saved_kwh * self.cfg.gas_price_eur_per_kwh, 2),
                # Jahreswert nur mit vollem Jahr, sonst bleibt der Prozentwert die Aussage
                "year_kwh": round(savings.saved_kwh * 365 / year_days, 0) if year_days >= 360 else None,
                "year_eur": round(savings.saved_kwh * 365 / year_days * self.cfg.gas_price_eur_per_kwh, 0)
                if year_days >= 360 else None,
                "dhw_separated": savings.dhw_separated,
                "gas_total_kwh": round(gas_total, 1),
                "span_days": round(span_days, 1),
                "daily": savings.daily[-60:],
            },
            "persons": [{**p, "eta": p["eta"].isoformat() if p["eta"] else None} for p in persons],
            "arrival": None if not arrival else {**arrival, "eta": arrival["eta"].isoformat()},
            "plan": None if not plan else {
                **plan,
                "best": None if not plan.get("best") else {
                    **plan["best"],
                    "start": plan["best"]["start"].isoformat(),
                    "steps": [{**st, "start": st["start"].isoformat(), "end": st["end"].isoformat()}
                              for st in plan["best"].get("steps", [])],
                },
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
            "discovered": self.found.as_dict(private=False),
        }

    def plan_rooms(self, rooms: list[tuple], room_checks: dict) -> tuple[list[tuple], list[str]]:
        """Räume für den Vorheizplan: plausible Modelle mit Ist und Soll – ein Raum mit laufender
        Fremdwärme (Kamin) gilt als schon warm und wird nicht eingeplant (zweiter Wert: seine Namen)."""
        names = {r.climate: r.name for r in self.found.rooms}
        inputs, skipped = [], []
        for climate, temp, target, _, _ in rooms:
            if climate not in self.rooms or temp is None or target is None or not room_checks[climate]["ok"]:
                continue
            if climate in self.room_ext:
                skipped.append(names.get(climate, climate))
                continue
            inputs.append((self.rooms[climate], temp, target))
        return inputs, skipped

    def ext_summary(self, now_ts: int) -> dict[str, dict]:
        """Je Raum: laufendes Fremdwärme-Ereignis und Zahl der Ereignisse der letzten EXT_COUNT_DAYS Tage."""
        counts = Counter(r["room"] for r in self.store.query(
            "SELECT room FROM room_events WHERE start_ts >= ?", (now_ts - EXT_COUNT_DAYS * 86400,)))
        out = {}
        for c in self.rooms:
            ev = self.room_ext.get(c)
            out[c] = {
                "ext_event": None if ev is None else {"since": local(ev["start_ts"]).isoformat(), "rise_k": ev["rise"]},
                "ext_events_7d": counts.get(c, 0) + (1 if ev else 0),
            }
        return out

    def ignored(self, days: int = 7) -> dict:
        """Was in den letzten Tagen nicht gelernt wurde – für Selbsttest und Übersicht."""
        since = local(time.time() - days * 86400).date().isoformat()
        tot: dict[str, float] = {}
        for vals in self.store.daily(since).values():
            for k, v in vals.items():
                if k.startswith("ignored_") or k == "gas_spike_kwh":
                    tot[k] = tot.get(k, 0) + v
        models = [self.house.rls, self.dhw.loss, *(m.rls for m in self.rooms.values())]
        return {
            "days": days,
            "gas_spikes": int(tot.get("ignored_gas_spike", 0)),
            "gas_spike_kwh": round(tot.get("gas_spike_kwh", 0.0), 1),
            "gas_missed_hours": int(tot.get("ignored_gas_missed", 0)),
            "gas_negative": int(tot.get("ignored_gas_negative", 0)),
            "room_spikes": int(tot.get("ignored_room_spike", 0)),
            "values": int(tot.get("ignored_values", 0)),
            "learn_clipped": sum(m.clipped for m in models),
            "learn_rejected": sum(m.rejected for m in models),
        }

    def note_changes(self, s: dict) -> None:
        """Wichtige Zustandswechsel ins Log schreiben – nachvollziehbar, was das Add-on denkt."""
        plan = (s.get("plan") or {}).get("best") or {}
        current = {
            "Hausmodell plausibel": s["guards"]["house"]["ok"],
            "Warmwasser-Modell plausibel": s["guards"]["dhw"]["ok"],
            "plausible Räume": s["guards"]["rooms_ok"],
            "Urlaub": s["vacation"]["active"],
            "Ankunft-Quelle": (s.get("arrival") or {}).get("source"),
            "Vorheizen ab": (plan.get("start") or "")[:16] or None,
            "Warmwasser-Empfehlung": (s["dhw"].get("recommendation") or {}).get("target"),
            "Heizfreigabe": s.get("hc_mode"),
            "Bedarf laut Tado-Soll": s.get("setpoint_demand"),
        }
        prev = getattr(self, "_noted", None)
        if prev is not None:
            for key, value in current.items():
                if prev.get(key) != value:
                    log.info("Änderung: %s %s → %s", key, prev.get(key), value)
        self._noted = current

    # ------------------------------------------------------------------ Reiter
    def flow_study(self, max_age_s: int = 3600) -> dict:
        """Vorlauf gegen Takten (stündlich neu – wertet 90 Tage aus)."""
        now = time.time()
        cached = getattr(self, "_flow_study", None)
        if cached is None or now - cached[0] > max_age_s:
            ok = guards.check_house(self.house)["ok"]
            self._flow_study = (now, flowstudy.study(self.store, offset_kw=self.house.offset_kw if ok else None,
                                                     heating_limit_k=self.house.heating_limit_k if ok else None))
        curve = flowstudy.curve_hint(num(self._thermo("hc_designtemp")), num(self._thermo("hc_seltemp")),
                                     num(self._thermo("hc_minexttemp")), num(self._thermo("hc_offsettemp")))
        return {**self._flow_study[1], "curve": curve}

    def week(self, days: int = weekly.WINDOW_DAYS, room: str | None = None, max_age_s: int = 600) -> dict:
        """Wochenraster Mo–So × Stunde (alle 10 min neu)."""
        now = time.time()
        cache = self.__dict__.setdefault("_week", {})
        key = (days, room)
        if key not in cache or now - cache[key][0] > max_age_s:
            rooms = [r.climate for r in self.found.rooms]
            house_ok = guards.check_house(self.house)["ok"]
            data = weekly.collect(self.store, int(now), days, self.house, rooms, room, house_ok)
            data["rooms"] = [{"climate": r.climate, "name": r.name} for r in self.found.rooms]
            data["room"] = room
            data["house_ok"] = house_ok
            if len(cache) > 8:
                cache.clear()
            cache[key] = (now, data)
        return cache[key][1]

    def presence(self, hours: float = 24) -> dict:
        """Wie HeatPilot Anwesenheit und Heimkehr deutet – je Person, nur Entfernungen."""
        now_ts = int(time.time())
        now = local(now_ts)
        since = now_ts - int(hours * 3600)
        rows: dict[str, dict] = {p: {"ts": [], "dist_km": [], "home": []} for p in self.persons}
        for r in self.store.query("SELECT ts, person, home, dist_km FROM person_samples WHERE ts >= ? ORDER BY ts",
                                  (since,)):
            if r["person"] in rows:
                d = rows[r["person"]]
                d["ts"].append(r["ts"])
                d["dist_km"].append(None if r["dist_km"] is None else round(r["dist_km"], 2))
                d["home"].append(r["home"])
        out = []
        for p, learner in self.persons.items():
            e = learner.eta(now)
            out.append({
                "person": p, "name": (self.states.get(p) or {}).get("attributes", {}).get("friendly_name")
                or p.replace("person.", ""),
                "home": learner.home, "dist_km": e["dist_km"], "speed_kmh": e["speed_kmh"],
                "eta": e["eta"].isoformat() if e["eta"] else None, "source": e["source"],
                "confidence": e["confidence"], "status": learner.status(now),
                "approach_speed_kmh": round(learner.approach_speed_kmh, 1),
                "arrivals_learned": learner.arrivals_learned, "long_away": learner.long_away,
                "habit": learner.habit_hours(),
                "arrivals": [{**a, "short": short_trip(a)} for a in learner.arrivals[-10:][::-1]],
                "quality": learner.arrival_quality(), "series": rows[p],
            })
        if self.on_vacation(now_ts):
            arrival = self.vacation.arrival(now, list(self.persons.values()))
        else:
            arrival = _e.next_arrival(list(self.persons.values()), now)
        unknown = None if self.on_vacation(now_ts) else arrival_unknown(list(self.persons.values()), now)
        return {"now": now.isoformat(), "hours": hours, "persons": out,
                "anyone_home": any(p.home for p in self.persons.values()),
                "unknown_from": unknown.isoformat() if unknown else None,
                "vacation": self.on_vacation(now_ts),
                "arrival": None if not arrival else {k: (v.isoformat() if isinstance(v, datetime) else v)
                                                      for k, v in arrival.items()}}

    def tado_advice(self, max_age_s: int = 900) -> dict:
        """Empfehlungen an Tado (höchstens alle 15 min neu berechnet – wertet 3 Wochen aus)."""
        now = time.time()
        if self._advice is None or now - self._advice[0] > max_age_s:
            items = advice.build(self)
            resolved = advice.track(self.store, items)
            self._advice = (now, items, resolved)
        return {"items": self._advice[1], "resolved": self._advice[2]}

    def house_signature(self, days: int = 365) -> list[dict]:
        """Tagesmittel: Gasleistung gegen Temperaturdifferenz (Energiesignatur).

        Einzelne Stunden streuen stark (Kessel taktet, Warmwasser lädt) – Tagesmittel zeigen,
        ob die gelernte Linie passt. Stunden mit Warmwasserladung (bekannt) zählen nicht.
        """
        since = int(time.time()) - days * 86400
        by_day: dict[str, list[tuple[float, float]]] = {}
        for r in self.store.query(
            "SELECT ts, t_in, t_out, gas_kwh, dhw_frac, dhw_energy_frac FROM hourly WHERE ts >= ? "
            "AND gas_kwh IS NOT NULL AND t_in IS NOT NULL AND t_out IS NOT NULL", (since,)
        ):
            if r["t_out"] > 15 or (r["dhw_frac"] or 0) > 0:  # wie beim Lernen
                continue
            by_day.setdefault(local(r["ts"]).date().isoformat(), []).append(
                (r["t_in"] - r["t_out"], self.heating_gas(r)))
        out = []
        for day, vals in sorted(by_day.items()):
            if len(vals) >= 12:
                out.append({"day": day, "dt": round(sum(v[0] for v in vals) / len(vals), 2),
                            "kw": round(sum(v[1] for v in vals) / len(vals), 3), "hours": len(vals)})
        return out

    def history(self, days: int = 30) -> dict:
        since = int(time.time()) - days * 86400
        hourly = [dict(r) for r in self.store.query("SELECT * FROM hourly WHERE ts >= ? ORDER BY ts", (since,))]
        snaps = [
            {"ts": r["ts"], "kind": r["kind"], "key": r["key"], "data": json.loads(r["data"])}
            for r in self.store.query("SELECT * FROM snapshots ORDER BY ts")
        ]
        daily_cycles = []
        today = local(time.time())
        for d in range(min(days, 30)):
            day = today - timedelta(days=d)
            s, e = midnight(day), midnight(day + timedelta(days=1))
            ev = [(r["ts"], r["state"]) for r in self.store.query(
                "SELECT ts, state FROM burner_events WHERE ts >= ? AND ts < ? ORDER BY ts", (s - 86400, e))]
            st = analysis.cycling_stats(ev, s, e)
            daily_cycles.append({"day": day.date().isoformat(), **st})
        first = daily_cycles[-1]["day"] if daily_cycles else local(time.time()).date().isoformat()
        counters = self.counter_days(first)
        live = self.store.daily(first)
        for d in daily_cycles:
            cnt = counters.get(d["day"], {})
            d.update(cnt)
            if "avg_run_min" not in cnt:  # ohne Heizlaufzeit-Zähler: nur Läufe ohne Warmwasser
                day = datetime.fromisoformat(d["day"])
                avg, _ = analysis.heat_runs(self.store, int(day.timestamp()), int((day + timedelta(days=1)).timestamp()))
                d["avg_run_min"] = None if avg is None else round(avg, 1)
            for k, v in live.get(d["day"], {}).items():
                d[k] = round(v, 2)
        return {"hourly": hourly, "snapshots": snaps, "cycles": list(reversed(daily_cycles))}

    def sleep_summary(self) -> dict:
        """Zusammenfassung der gelernten Nachtzeiten (gecacht aus update_sleep) für Oberfläche und API."""
        return self._sleep

    # ------------------------------------------------------------------ Vorhersage
    def forecast_points(self) -> list[tuple[datetime, float]]:
        """Stündliche Vorhersage als (Zeitpunkt mit Zone, Außentemperatur), zeitlich sortiert.

        Punkte ohne lesbare Zeit oder Temperatur fallen weg. Zeiten ohne Zone gelten als Ortszeit
        (HA liefert ISO mit Offset; seit 0.36.1 defensiv, vorher brach der Vergleich ab).
        """
        out = []
        for f in self.forecast or []:
            if not isinstance(f, dict):
                continue
            when, temp = parse_time(f.get("datetime")), num(f.get("temperature"))
            if when is not None and temp is not None:
                out.append((when, temp))
        return sorted(out, key=lambda p: p[0])

    def forecast_point_at(self, when: datetime) -> tuple[datetime, float] | None:
        """Der Vorhersagepunkt am nächsten zu ``when`` – nur innerhalb ``FORECAST_MAX_GAP_MIN``, sonst None.

        Für die Anzeige, welcher Zeitpunkt genommen wurde (Vorheizplan: „Vorhersage für 17:00“).
        Ohne Wetter-Entität None – eine alte Vorhersage zählt dann nicht mehr (wie bei der Kachel).
        """
        if not self.found.weather:
            return None
        when = aware(when)
        best = min(self.forecast_points(), key=lambda p: abs((p[0] - when).total_seconds()), default=None)
        if best is None or abs((best[0] - when).total_seconds()) > FORECAST_MAX_GAP_MIN * 60:
            return None
        return best

    def outdoor_forecast_at(self, when: datetime) -> float | None:
        """Außentemperatur laut Vorhersage zum Zeitpunkt ``when`` – None ohne Punkt in der Nähe (seit 0.36.1)."""
        point = self.forecast_point_at(when)
        return point[1] if point else None

    def forecast_summary(self, now_ts: float) -> dict | None:
        """Kachel „Vorhersage“ (seit 0.36.1): Alter, Reichweite, nächste 12 h und Trend.

        None ohne Wetter-Entität oder ohne Punkte. ``trend_k`` = Temperatur in 12 h − jetzt (je nächster
        Punkt, auf 0,5 K gerundet); fehlt einer der beiden Punkte, None.
        """
        if not self.found.weather:
            return None
        points = self.forecast_points()
        if not points:
            return None
        now = local(now_ts)
        ahead = now + timedelta(hours=FORECAST_AHEAD_H)
        # der Punkt der laufenden Stunde zählt mit (HA beginnt meist zur vollen Stunde)
        window = [p for p in points if now - timedelta(minutes=30) <= p[0] <= ahead]
        next12 = None
        if window:
            low = min(window, key=lambda p: p[1])
            next12 = {"min": low[1], "min_at": low[0].astimezone().isoformat(), "max": max(p[1] for p in window)}
        cur, later = self.forecast_point_at(now), self.forecast_point_at(ahead)
        return {
            "age_min": None if self.forecast_ts is None else max(0, round((now_ts - self.forecast_ts) / 60)),
            "points": len(points),
            "hours": round(max((points[-1][0] - now).total_seconds() / 3600, 0.0), 1),
            "next12": next12,
            "trend_k": (round((later[1] - cur[1]) * 2) / 2 or 0.0) if cur and later else None,  # ohne −0,0
        }


# Am Ende geladen, weil engine dieses Modul importiert (Engine erbt SummaryMixin): summary und presence
# rufen next_arrival über heatpilot.engine auf – dort ersetzen Tests die Ankunft (monkeypatch).
from . import engine as _e
