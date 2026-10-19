"""Kessel live: Brenner, Heizfreigabe, Wärmebedarf und die Tageszähler, die daraus folgen.

``BoilerMixin`` deutet die Kesselwerte (Brenner an, Sonderladung, Betriebsart Tag/Nacht), erkennt den
Wärmebedarf aus Tado-Soll und Ist, speichert jeden Brennerwechsel und schreibt ereignisgenau Nachlauf,
Taktsperre und Wartezeiten in die Tageszähler der Datenbank. Dazu Starts und Laufzeit je Tag aus den
Zählern der Therme. Warmwasser steht in ``engine_dhw``.
"""

from __future__ import annotations

import time
from datetime import datetime

from .discovery import HEAT_MODES
from .values import flag, local, num

# Räume wollen Wärme, der Regler steht noch auf Nacht: so lange ist das die Verzögerung der Automation
# (Tageszähler enable_delay_*), eine längere Wartephase ist eine gewollte Sperre (Nacht, niemand da)
ENABLE_DELAY_MAX_MIN = 15


class BoilerMixin:
    """Kesselzustand und Live-Zähler der ``Engine``.

    Nutzt von der Engine: ``cfg``, ``found``, ``states``, ``store``, ``_boiler``/``_thermo`` und die
    Live-Felder aus ``__init__`` (``_last_burner``, ``_live_prev``, ``_demand``, ``_dhw_now`` …).
    """

    # ------------------------------------------------------------------ Kesselzustand
    def burner_on(self) -> int | None:
        on = flag(self._boiler("burner_on"))
        if on is None:
            p = num(self._boiler("burner_power"))
            on = None if p is None else int(p > 0)
        return on

    def dhw_special(self) -> int | None:
        """Legionellenschaltung oder Einmalladung läuft – solche Ladungen lernt das Modell nicht."""
        vals = [flag(self._boiler(r)) for r in ("dhw_disinfecting", "dhw_onetime")]
        if all(v is None for v in vals):
            return None
        return int(any(vals))

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

    def nominal_kw(self) -> float | None:
        return self.cfg.boiler_nominal_kw or num(self._boiler("nom_power"))

    # ------------------------------------------------------------------ Live-Zähler
    def update_live(self, now: float | None = None, record_events: bool = True) -> None:
        """Zeit seit dem letzten Aufruf den passenden Tageszählern gutschreiben.

        Warmwasser gilt nach der erkannten Ladung (infer_dhw), nicht nur nach dem Ladesignal – viele
        Kessel melden keins, dann zählte jede Speicherladung als „Brenner ohne Wärmebedarf“.
        """
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

        dhw = flag(self._boiler("dhw_active")) or self._dhw_now or 0
        demand = self.setpoint_demand()
        enabled = self.heat_enabled()
        period = num(self._boiler("min_period"))
        power, nom = num(self._boiler("burner_power")), self.nominal_kw()
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
