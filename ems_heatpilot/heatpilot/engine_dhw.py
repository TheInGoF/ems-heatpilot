"""Warmwasser in der Engine: Ladung erkennen, Modell füttern, Empfehlung und Anzeige.

``DhwMixin`` erkennt Speicherladungen auch ohne Ladesignal (Ladezähler, Temperaturanstieg), gibt
jeden Messpunkt an ``DhwModel`` und den Schatten der Spar-Strategie weiter (deren Tageswerte – Zapfung,
Verluste, Ladung – schreibt es in die Datenbank), empfiehlt die Speichertemperatur und rechnet die
Werte für den Reiter Warmwasser (Ersparnis, Auskühlkurve, nächste Ladung, Soll-Zeitprogramm des
Reglers). Seit 0.36.3 gibt es den Desinfektionstermin aus den Einstellungen an den Schatten weiter und zeigt
ihn mit Vorschlag an (``disinfect_info``, gerechnet in ``dhw_disinfect``), seit 0.42 mit dem Stand des Auslösens
(``disinfect_status``). Das Modell selbst steht in ``dhw.py``.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta

from . import dhw_disinfect, dhw_target, guards
from .config import WEEKDAYS
from .dhw_watch import AFTER_CHARGE_H, DrawWatch, tail_rising
from .presence import arrival_unknown
from .values import flag, local, num

DHW_RISE_KH = 6.0  # so schnell steigt der Speicher nur beim Laden (Auskühlen: < 2 K/h)
DHW_HOLD_MAX_S = 90 * 60  # nach dem Ladezähler höchstens so lange „lädt“ annehmen
# Warmwasser-Soll-Zeitprogramm des Reglers (z. B. nachts 45 statt 55 °C) aus den Messwerten
DHW_SET_DAYS = 14         # so weit zurück
DHW_SET_SLOT_MIN = 5      # Raster nach Wanduhr
DHW_SET_SHARE = 0.7       # so viele Tage müssen im Slot denselben Wert zeigen
DHW_SET_MIN_DAYS = 3
DHW_SET_CACHE_S = 3600


class DhwMixin:
    """Warmwasser der ``Engine``.

    Nutzt von der Engine: ``cfg``, ``store``, ``dhw``, ``dhw_shadow``, ``persons``, ``vacation``,
    ``_boiler``/``_thermo`` und ``nominal_kw``; setzt ``dhw_rec`` und die Ladeerkennung (``_dhw_prev`` …).
    """

    # ------------------------------------------------------------------ Ladung erkennen und lernen
    def infer_dhw(self, ts: int, sample: dict) -> None:
        """Warmwasserladung erkennen, wenn das Ladesignal fehlt oder nie 1 wird.

        Steigt die Speichertemperatur bei laufendem Brenner um mindestens DHW_RISE_KH,
        lädt der Kessel den Speicher (3-Wege-Ventil auf Warmwasser). Dann bekommt der
        Heizkreis in dieser Zeit nichts.
        """
        flag, t = sample.get("dhw"), sample.get("dhw_temp")
        # Ladezähler des Kessels: jede Erhöhung ist der Beginn einer Speicherladung (exakt)
        starts, prev_starts = sample.get("dhw_starts"), getattr(self, "_dhw_starts_prev", None)
        if starts is not None:
            self._dhw_starts_prev = starts
            if prev_starts is not None and 0 < starts - prev_starts <= 5 and not flag:
                sample["dhw"] = flag = 1
                self.dhw_counted = getattr(self, "dhw_counted", 0) + 1
                self._dhw_hold = {"since": ts, "hot": False}
        # Ladung läuft weiter, auch wenn der Speicher gerade fällt (gleichzeitig gezapft) – bis der
        # Brenner aus ist, der Vorlauf nicht mehr über dem Speicher liegt oder 90 min um sind
        hold = getattr(self, "_dhw_hold", None)
        if hold is not None and not flag:
            flow = sample.get("flow")
            if flow is not None and t is not None and flow > t:
                hold["hot"] = True
            done = (not sample.get("burner_on") or ts - hold["since"] > DHW_HOLD_MAX_S
                    or (hold["hot"] and flow is not None and t is not None and flow <= t + 2.0))
            if done:
                self._dhw_hold = None
            else:
                sample["dhw"] = flag = 1
        if flag:
            self.dhw_flag_seen = True
        prev = self._dhw_prev
        if t is not None and (prev is None or ts - prev[0] >= 100):
            self._dhw_prev = (ts, t)
        if flag or t is None or prev is None or not sample.get("burner_on"):
            if flag is None and t is not None:
                sample["dhw"] = 0
            return
        dt_h = (ts - prev[0]) / 3600
        flow = sample.get("flow")
        valve_dhw = flow is None or flow >= t + 5.0  # Speicher wird nur warm, wenn das Kesselwasser heißer ist
        if valve_dhw and 0 < dt_h <= 0.25 and t - prev[1] >= 0.5 and (t - prev[1]) / dt_h >= DHW_RISE_KH:
            sample["dhw"] = 1
            self.dhw_inferred += 1
        elif flag is None:
            sample["dhw"] = 0
        if sample["dhw"] and self._boiler("heating_pump") is None:
            sample["heating"] = 0  # Ventil auf Warmwasser: Heizkreis bekommt nichts

    def dhw_recommend(self, ts: int, sample: dict) -> tuple[dict, tuple[float, float]]:
        """Empfehlung für den Speicher jetzt; gibt (Prüfung, (lädt unter, lädt bis) für den Schatten) zurück.

        Seit 0.46 mit der Einschalthysterese des Kessels: Der Schatten lädt wie der Kessel erst unter Soll − Hysterese
        (``hold``) und dann bis zum Soll (``target``)."""
        now = local(ts)
        home = sample.get("home_count")
        arrival = unknown = None
        if sample.get("vacation"):
            ret = self.vacation.arrival(now, list(self.persons.values()))
            home, arrival = 0, ret["eta"] if ret else None
        elif home == 0:
            learners = list(self.persons.values())
            nxt = _e.next_arrival(learners, now)
            arrival = nxt["eta"] if nxt else None
            unknown = arrival_unknown(learners, now)  # Gewohnheit lernt noch: nicht auf Minimum
        check = guards.check_dhw(self.dhw)
        hyst = self._boiler("dhw_hyst_on")
        if check["ok"]:
            self.dhw_rec = self.dhw.recommend(now, None if home is None else home > 0, arrival, self.cfg,
                                              unknown_from=unknown, current_set=num(sample.get("dhw_set")),
                                              disinfect_h=self.disinfect_ready_h(), hyst=hyst,
                                              vacation=bool(sample.get("vacation")), temp=num(sample.get("dhw_temp")),
                                              heat_start=getattr(self, "heat_start", None))  # 0.68: aus dem Regelkreis
            return check, (self.dhw_rec["hold"], self.dhw_rec["target"])
        # unsicheres Modell: keine Empfehlung, Schatten folgt dem heutigen Soll (mit derselben Hysterese)
        self.dhw_rec = {"target": None, "reason": "lernt noch: " + "; ".join(check["reasons"])}
        top = num(sample.get("dhw_set")) or self.cfg.dhw_comfort_temp
        return check, (top - dhw_target.hysteresis(hyst)[0], top)

    def dhw_step(self, ts: int, sample: dict) -> None:
        """Warmwasser lernen und die Spar-Strategie im Schatten mitlaufen lassen."""
        self.dhw_now = bool(sample.get("dhw"))  # 0.68: Ladung laut Messpunkt (auch erschlossen) für die Kesselsteuerung
        t_cur = sample.get("dhw_temp")
        nom = self.nominal_kw()  # Einstellung vor Kessel-Sensor (der liefert oft keine Zahl)
        kw = None
        if sample.get("burner_pow") is not None and nom:
            kw = sample["burner_pow"] / 100 * nom
        prev, self._prev_dhw = self._prev_dhw, (ts, t_cur, kw)
        # Haushalt leer: Zapfprofil sichern; wird daraus „alle lange weg“, die leeren Stunden davor
        # zurücknehmen (wie bei den Personen)
        self.dhw.note_household(sample.get("home_count"), bool(sample.get("away_long")))
        if prev is None:
            # erst ab dem zweiten Messpunkt lernen – die Empfehlung gibt es aber sofort
            self.dhw_recommend(ts, sample)
            return
        dt_h = (ts - prev[0]) / 3600
        now = local(ts)
        vacation = bool(sample.get("vacation"))
        frozen = vacation or bool(sample.get("away_long"))  # Urlaub oder alle lange weg: Zapfprofil nicht lernen
        # hätte mehr Reserve das Ziel der Strategie bis eben angehoben? Nur dann hilft sie bei zu
        # kaltem Wasser (Stand vor dem Schritt; seit 0.46 immer, wenn Komfort + Reserve gilt)
        bound = bool((self.dhw_rec or {}).get("margin_bound"))
        res = self.dhw.step(now, dt_h, prev[1], t_cur, bool(sample.get("dhw")), kw,
                            learn_usage=not frozen, special=bool(sample.get("dhw_special")),
                            dhw_set=num(sample.get("dhw_set")), burner_kw_prev=prev[2] if len(prev) > 2 else None,
                            disinfect=bool(sample.get("dhw_disinfecting")), disinfect_temp=self.disinfect_temp()[0])
        check, (shadow_hold, shadow_top) = self.dhw_recommend(ts, sample)
        # Sonderladung des Reglers trifft beide Speicher; ein bestätigter Desinfektionstermin (0.36.3) nur den
        # Schatten, nach der Ladedauer – die Strategie desinfiziert zum Termin
        mirror = t_cur if res.get("special") else dhw_disinfect.shadow_mirror(
            self.dhw, self.cfg, local(prev[0]), now, self.disinfect_temp()[0], self.disinfect_ready_h())
        sh = self.dhw_shadow.step(
            self.dhw, t_cur, shadow_hold, res["draw_k"], dt_h, self.cfg.dhw_comfort_temp,
            dhw_min=self.cfg.dhw_min_temp, margin_bound=bound, dhw_max=self.cfg.dhw_max_temp, mirror=mirror,
            top=shadow_top,
        )
        if sh["raise_margin"] and check["ok"] and not frozen:  # Urlaub / lange weg: Reserve bleibt eingefroren
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
        if self.dhw.kwh_per_kelvin() is not None:
            # Ladeenergie nur, solange auch Verluste berechnet werden (Nenner der Ersparnis) –
            # auch 0 schreiben: markiert den Tag als so gezählt
            self.store.add_daily(day, "dhw_charge_cmp_kwh", res["charge_kwh"])

    # ------------------------------------------------------------------ Anzeige
    def dhw_points(self, days: int = 14, now_ts: int | None = None) -> list[dict]:
        """Gemessenes Auskühlen des Speichers (ohne Ladung, ohne Zapfung), je 1 K gemittelt.

        Dieselbe Auswahl wie beim Lernen (DrawWatch, Nachlauf nach der Ladung) – so lässt sich
        prüfen, ob die gelernte Kurve passt.
        """
        now_ts = now_ts or int(time.time())
        rows = self.store.query(
            "SELECT ts, dhw_temp, dhw FROM samples WHERE ts >= ? AND dhw_temp IS NOT NULL ORDER BY ts",
            (now_ts - days * 86400,))
        bins: dict[int, list[float]] = {}
        watch = DrawWatch(self.dhw.sensor_step)  # dieselbe Fühlerstufe wie beim Lernen
        last_charge = None  # Ende der letzten Ladung (nach dem Nachlauf)
        tail = tail_ts = None  # Nachlauf nach dem Ladesignal: [(Stunden seit Ladeende, Temperatur)], Ladeende
        for a, b in zip(rows, rows[1:]):
            dt_h = (b["ts"] - a["ts"]) / 3600
            if not 0 < dt_h <= 0.5:
                watch.reset()
                continue
            if b["dhw"]:
                watch.reset()
                tail, tail_ts = [(0.0, b["dhw_temp"])], b["ts"]
                continue
            if tail is not None:
                tail.append(((b["ts"] - tail_ts) / 3600, b["dhw_temp"]))
                if tail[-1][0] <= AFTER_CHARGE_H + 1e-6 and tail_rising(tail):
                    continue  # Nachlauf zählt noch zur Ladung
                tail, last_charge = None, a["ts"]
            since = 99.0 if last_charge is None else (b["ts"] - last_charge) / 3600
            seg = watch.step(local(b["ts"]), dt_h, a["dhw_temp"], b["dhw_temp"], self.dhw.cooling_kh, since)["segment"]
            if seg:
                t0, hours, t1 = seg
                bins.setdefault(round((t0 + t1) / 2), []).append((t0 - t1) / hours)
        return [{"t": t, "kh": round(sum(v) / len(v), 3), "n": len(v)}
                for t, v in sorted(bins.items()) if len(v) >= 3]

    def dhw_summary(self) -> dict:
        t_now = num(self._boiler("dhw_temp"))
        rows = self.store.query(
            "SELECT key, SUM(value) AS v, COUNT(DISTINCT day) AS days FROM daily "
            "WHERE key LIKE 'dhw_%' GROUP BY key"
        )
        tot = {r["key"]: r["v"] for r in rows}
        days = next((r["days"] for r in rows if r["key"] == "dhw_loss_kwh"), 0)  # Zeitraum der Verluste
        loss, loss_s = tot.get("dhw_loss_kwh", 0.0), tot.get("dhw_loss_strategy_kwh", 0.0)
        charge = self.dhw_charge_compared()
        today = local(time.time()).date().isoformat()
        return {
            **self.dhw.summary(t_now),
            "temp": t_now,
            "set": num(self._boiler("dhw_seltemp")),
            "recommendation": self.dhw_rec,
            "hyst_hint": dhw_target.hyst_hint(*dhw_target.hysteresis(self._boiler("dhw_hyst_on"))),  # 0.46
            "today": {k: round(v, 2) for k, v in self.store.daily(today).get(today, {}).items()
                      if k.startswith("dhw_")},
            "period_days": days,
            "charge_kwh": round(charge, 1),
            "charge_total_kwh": round(tot.get("dhw_charge_kwh", 0.0), 1),
            "loss_kwh": round(loss, 1),
            "loss_strategy_kwh": round(loss_s, 1),
            "saved_kwh": round(loss - loss_s, 1),
            "saved_pct": round(100 * (loss - loss_s) / charge, 1) if charge > 1 else None,
            "saved_eur": round((loss - loss_s) * self.cfg.gas_price_eur_per_kwh, 2),
            "cold_draws": int(tot.get("dhw_cold_draws", 0)),
            "points": self.dhw_points(),
            "curve_kh": {t: round(self.dhw.cooling_kh(t), 3) for t in range(30, 71)},
            "model_ok": guards.check_dhw(self.dhw)["ok"],
            "kwh_per_k_avg": None if self.dhw.kwh_per_kelvin() is None else round(self.dhw.kwh_per_kelvin(), 3),
            "next_charge": self.dhw_next_charge(t_now),
            "disinfect": self.disinfect_schedule(),
            "disinfect_plan": self.disinfect_info(),  # 0.36.3: eingestellter Termin, letzte erkannte Sonderladung
        }

    def dhw_charge_compared(self) -> float:
        """Ladeenergie über denselben Zeitraum wie die Verluste (Nenner der Warmwasser-Ersparnis).

        Tage mit ``dhw_charge_cmp_kwh`` (zählt erst ab dem Schritt, in dem die Ladekosten gelernt
        sind) nehmen diesen Wert; ältere Tage ohne ihn zählen nur, wenn an ihnen Verluste berechnet wurden.
        """
        rows = self.store.query("SELECT day, key, SUM(value) AS v FROM daily WHERE key IN "
                                "('dhw_charge_kwh', 'dhw_charge_cmp_kwh', 'dhw_loss_kwh') GROUP BY day, key")
        by_day: dict[str, dict] = {}
        for r in rows:
            by_day.setdefault(r["day"], {})[r["key"]] = r["v"]
        total = 0.0
        for v in by_day.values():
            if "dhw_charge_cmp_kwh" in v:
                total += v["dhw_charge_cmp_kwh"]
            elif "dhw_loss_kwh" in v:
                total += v.get("dhw_charge_kwh", 0.0)
        return total

    # ------------------------------------------------------------------ Soll-Zeitprogramm und nächste Ladung
    def dhw_set_profile(self) -> list[float | None] | None:
        """Warmwasser-Soll-Zeitprogramm des Reglers, gelernt aus den Messwerten (je 5 min nach Wanduhr).

        Je Slot der häufigste Wert der letzten 14 Tage, nur wenn ≥ 70 % der Tage ihn zeigen –
        sonst None (dann gilt das aktuelle Soll). dhw_switchtime von EMS-ESP enthält nur einen
        Eintrag und passt nicht zu den gemessenen Umschaltzeiten, deshalb nicht verwendet.
        """
        cached = self.__dict__.get("_dhw_set_prof")
        if cached and time.time() - cached[0] < DHW_SET_CACHE_S:
            return cached[1]
        rows = self.store.query("SELECT ts, dhw_set FROM samples WHERE ts >= ? AND dhw_set IS NOT NULL",
                                (int(time.time()) - DHW_SET_DAYS * 86400,))
        per_day: dict[tuple[str, int], float] = {}
        for r in rows:
            w = local(r["ts"])
            per_day[(w.date().isoformat(), self._set_slot(w))] = round(r["dhw_set"] * 2) / 2
        counts: dict[int, Counter] = {}
        for (_, slot), v in per_day.items():
            counts.setdefault(slot, Counter())[v] += 1
        prof: list[float | None] = [None] * (24 * 60 // DHW_SET_SLOT_MIN)
        for slot, c in counts.items():
            v, n = c.most_common(1)[0]
            total = sum(c.values())
            if total >= DHW_SET_MIN_DAYS and n >= DHW_SET_SHARE * total:
                prof[slot] = v
        prof = prof if any(v is not None for v in prof) else None
        self._dhw_set_prof = (time.time(), prof)
        return prof

    @staticmethod
    def _set_slot(when: datetime) -> int:
        w = when.astimezone() if when.tzinfo else when  # Wanduhr, auch über die Zeitumstellung
        return (w.hour * 60 + w.minute) // DHW_SET_SLOT_MIN

    def dhw_next_charge(self, t_now: float | None) -> dict | None:
        """Nächste Speicherladung aus Soll und Einschalt-Hysterese des Kessels (z. B. 55 − 10 = 45 °C).

        Mit gelerntem Soll-Zeitprogramm gilt je Uhrzeit dessen Grenze (nachts z. B. 45 − 10 = 35 °C);
        springt das Soll morgens über den Speicher, ist das die Ladung.
        """
        set_t, hyst = num(self._boiler("dhw_seltemp")), num(self._boiler("dhw_hyst_on"))
        if t_now is None or set_t is None or hyst is None:
            return None
        hyst = abs(hyst)
        trigger = set_t - hyst
        if flag(self._boiler("dhw_active")) or t_now <= trigger:
            return {"trigger": trigger, "eta": None, "now": True}
        now = local(time.time())
        prof = self.dhw_set_profile()
        v_now = None if prof is None else prof[self._set_slot(now)]
        if v_now is not None and abs(v_now - set_t) > 0.5:
            prof = None  # Programm passt nicht zum heutigen Soll (umgestellt?): wie bisher fest rechnen

        def trig(when: datetime) -> float:
            v = prof[self._set_slot(when)] if prof else None
            return (set_t if v is None else v) - hyst

        eta = self.dhw.next_charge(now, t_now, trig if prof else trigger)
        out = {"trigger": trigger, "eta": eta.isoformat() if eta else None,
               "hours": None if eta is None else round((eta - now).total_seconds() / 3600, 1), "now": False}
        if eta is not None and prof:
            out["trigger_at_eta"] = trig(eta)
            out["by_schedule"] = trig(eta) > trig(eta - timedelta(minutes=DHW_SET_SLOT_MIN)) + 0.1
        return out

    def disinfect_schedule(self) -> dict | None:
        """Legionellenschaltung laut Regler (Tag/Uhrzeit) – diese Ladungen lernt das Modell nicht."""
        day, hour = self._thermo("disinfect_day"), num(self._thermo("disinfect_hour"))
        if day is None and hour is None:
            return None
        return {"day": day, "hour": None if hour is None else int(hour),
                "special_charges": self.dhw.special_charges}

    # ------------------------------------------------------------------ Desinfektion (0.36.3)
    def disinfect_temp(self) -> tuple[float, str]:
        """(Desinfektionstemperatur, Herkunft): EMS-ESP ``wwdisinfectiontemp``, sonst die Einstellung."""
        return dhw_disinfect.temperature(self._boiler("dhw_disinfect_temp"), self.cfg)

    def _disinfect_from(self) -> float:
        """Speichertemperatur, von der die Desinfektion lädt: heutiges Soll, sonst der Komfortwert."""
        return num(self._boiler("dhw_seltemp")) or self.cfg.dhw_comfort_temp

    def disinfect_ready_h(self) -> float:
        """Ladedauer der Desinfektion wie im Vorschlag: erst so lange nach dem Termin ist der Speicher heiß."""
        return dhw_disinfect.ready_hours(self.dhw, self.cfg, self.nominal_kw(), self._disinfect_from(),
                                         self.disinfect_temp()[0])

    def disinfect_info(self, suggest: bool = False, now_ts: float | None = None) -> dict:
        """Termin aus den Einstellungen für Übersicht und Einstellungen; ``suggest``: mit Terminvorschlag.

        ``next_text`` „So 17:00 (eingestellt)“; dazu die letzte erkannte Sonderladung des Reglers
        (``last_special_text`` „So 13:40 (erkannt)“). ``counts``: Der Termin zählt im Modell – nur bestätigt durch
        eine Desinfektion des Reglers zu dieser Zeit bzw. – seit 0.42 – weil HeatPilot ihn auslöst
        (``dhw_disinfect.plan_counts``), sonst sagt ``counts_text`` warum nicht. ``trigger``: wer die Desinfektion
        auslöst, Stand und letztes Ergebnis (``disinfect_status.info``); ``triggers``/``hint`` folgen daraus.
        """
        from .disinfect_status import info as trigger_info  # spät: disinfect_status nutzt dhw_disinfect und config
        now = local(time.time() if now_ts is None else now_ts)
        temp, source = self.disinfect_temp()
        nxt, last = self.cfg.next_disinfection(now), self.dhw.last_special
        last = None if last is None else last.astimezone() if last.tzinfo else last
        out = {
            "configured": nxt is not None, "day": self.cfg.dhw_disinfect_day or None,
            "time": self.cfg.dhw_disinfect_time.strftime("%H:%M"),
            "next": None if nxt is None else nxt.isoformat(),
            "next_text": None if nxt is None else f"{WEEKDAYS[nxt.weekday()]} {nxt:%H:%M} (eingestellt)",
            "counts": nxt is not None and dhw_disinfect.plan_counts(self.dhw, self.cfg, now),
            "temp": temp, "temp_source": source, "temp_boiler": num(self._boiler("dhw_disinfect_temp")),
            "temp_note": dhw_disinfect.temp_note(self._boiler("dhw_disinfect_temp")),
            "last_disinfect": (self.dhw.disinfects or [None])[-1],  # zuletzt erkannte Desinfektion (ohne Einmalladung)
            "last_special": None if last is None else last.isoformat(),
            "last_special_text": None if last is None else f"{WEEKDAYS[last.weekday()]} {last:%H:%M} (erkannt)",
            "special_charges": self.dhw.special_charges, "regler": self.disinfect_schedule(),
        }
        trig = trigger_info(self, now_ts=now.timestamp())
        out.update(trigger=trig, triggers=trig["triggers"], hint=trig["hint"])
        if out["configured"] and not out["counts"]:  # 0.42: ein gescheiterter Termin zählt eine Weile nicht
            out["counts_text"] = trig.get("uncounted") or dhw_disinfect.counts_text(self.dhw, self.cfg, now)
        if suggest:
            out["suggestion"] = dhw_disinfect.suggestion(self.dhw, self.nominal_kw(), self._disinfect_from(), temp, now)
        return out

    def dhw_for_arrival(self, arrival: datetime, now: datetime) -> dict | None:
        """Wie lange muss der Speicher vor der Ankunft laden? (teilt sich die Therme mit dem Heizen)"""
        t_now = num(self._boiler("dhw_temp"))
        if t_now is None:
            return None
        # seit 0.46.2: lädt (wie die Empfehlung) bis Komfort, sobald der Speicher unter Komfort − Hysterese fällt
        hyst = dhw_target.hysteresis(self._boiler("dhw_hyst_on"))[0]
        target = dhw_target.capped(self.cfg.dhw_comfort_temp + self.dhw.comfort_margin, self.cfg)
        hours_to = max((arrival - now).total_seconds() / 3600, 0.0)
        # bis kurz vor Ankunft kühlt er weiter aus (Ladung ist erst gegen Ende geplant)
        t_start = self.dhw.cool(t_now, max(hours_to - 2.0, 0.0))
        if t_start >= target - hyst - 1.0:
            return None
        if guards.check_dhw(self.dhw)["ok"]:
            hours = self.dhw.charge_hours(t_start, target)
        else:  # Modell noch unsicher: vorsichtig mit 40 K/h rechnen
            hours = max(target - t_start, 0.0) / 40.0
        return {"hours": round(hours, 2), "from": round(t_start, 1), "to": round(target, 1)}


# Am Ende geladen, weil engine dieses Modul importiert (Engine erbt DhwMixin): dhw_recommend ruft
# next_arrival über heatpilot.engine auf – dort ersetzen Tests die Ankunft (monkeypatch).
from . import engine as _e
