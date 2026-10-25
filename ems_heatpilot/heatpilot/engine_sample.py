"""Messpunkt bilden: aus den Zuständen von Home Assistant und EMS-ESP einen Datensatz machen.

``SampleMixin`` liest Außentemperatur, Kessel, Personen, Hausleistung und Räume (auch Gruppen mit
Raumregler), verwirft Werte außerhalb physikalischer Grenzen und einzelne Temperaturspitzen und
ergänzt die Sonne auf den Fassaden. Verworfene Werte zählt es als Tageszähler in der Datenbank
(``ignored_values``, ``ignored_room_spike``); den Messpunkt selbst speichert es nicht, und es lernt
nichts – das macht der Takt (``Engine.tick``) mit dem Ergebnis.
"""

from __future__ import annotations

import logging
import time

from . import plausibility, room_heating, solar
from .presence import haversine_km
from .values import flag, local, num

log = logging.getLogger(f"{__package__}.engine")  # im Protokoll wie vor der Aufteilung: heatpilot.engine

PERSON_UNKNOWN = {None, "", "unknown", "unavailable"}  # Tracker ohne Wert: weder zu Hause noch weg


class SampleMixin:
    """Messpunkt und Raumwerte der ``Engine``.

    Nutzt von der Engine: ``states``, ``found``, ``store``, ``room_spikes``, ``_val``/``_boiler``/``_thermo``,
    die Kesselzustände aus ``BoilerMixin``, ``on_vacation`` und (0.45.1) ``_gone``; setzt
    ``_rejected``.
    """

    # ------------------------------------------------------------------ Messpunkt
    def outdoor_now(self) -> float | None:
        vals = [plausibility.in_range("t_out", num(self._val(e))) for e in self.found.outdoor]
        vals = plausibility.robust_outdoor([v for v in vals if v is not None])
        if vals:
            return min(vals)  # wie bisher: Minimum der Außenfühler
        if self.found.weather and self.found.weather in self.states:
            return num(self.states[self.found.weather]["attributes"].get("temperature"))
        return None

    def power_sum(self, entities: list[str], strict: bool) -> float | None:
        """Summe der Leistungssensoren in W (kW ×1000; Einheit aus dem Zustand, sonst die der Erkennung).

        ``strict``: fehlt ein Sensor oder hat er keinen Wert, ist die Summe unbekannt (None) – eine
        Phasensumme ohne eine Phase wäre falsch. Sonst zählt ein fehlender Sensor 0 (Ausschlüsse).
        Ohne Sensoren None.
        """
        if not entities:
            return None
        total = 0.0
        for e in entities:
            st = self.states.get(e)
            v = num(st.get("state")) if st else None
            if v is None:
                if strict:
                    return None
                continue
            unit = st.get("attributes", {}).get("unit_of_measurement") or self.found.power_units.get(e) or "W"
            total += v * 1000 if str(unit).strip().lower() == "kw" else v
        return total

    def snapshot(self) -> tuple[dict, list[tuple], list[tuple]]:
        burner_pow = num(self._boiler("burner_power"))
        burner_on = self.burner_on()
        dhw = flag(self._boiler("dhw_active"))
        heating = flag(self._boiler("heating_pump"))
        if heating is None and burner_on is not None:
            heating = int(burner_on and not dhw)
        home, unknown = 0, False  # bekannte Anwesende; irgendeine Person ohne Zustand?
        persons = []
        for p in self.found.persons:
            st = self.states.get(p)
            if not st:
                unknown = True
                continue
            state = st.get("state")
            # Tracker ohne Wert: nicht „weg“, sondern unbekannt (gespeichert als leer, nicht gelernt)
            is_home = None if state in PERSON_UNKNOWN else int(state == "home")
            if is_home is None:
                unknown = True
            else:
                home += is_home
            attrs = st.get("attributes", {})
            dist = None
            if self.found.home_zone and "latitude" in attrs:
                dist = haversine_km(*self.found.home_zone, attrs["latitude"], attrs["longitude"])
            persons.append((p, is_home, dist))
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
            # Zahl der bekannten Anwesenden; ist niemand bekannt zu Hause und eine Person unbekannt,
            # bleibt offen, ob jemand da ist (None: Warmwasser hält den Boden mit Reserve, nicht das Minimum)
            "home_count": (None if home == 0 and unknown else home) if self.found.persons else None,
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
            "vacation": int(self.on_vacation()),
            # seit 0.25: Warmwasser-Ladezähler, Sonderladung (Legionellen/Einmalladung), Anlagendruck
            "dhw_starts": num(self._boiler("dhw_starts")),
            "dhw_special": self.dhw_special(),
            "syspress": num(self._boiler("sys_press")),
            # seit 0.36: Hausleistung (Summe der Leistungssensoren; fehlt einer, bleibt sie offen) und
            # die Verbraucher, die nicht zählen (Waschmaschine, Wallbox …)
            "power_w": self.power_sum(self.found.power, strict=True),
            "power_ex_w": self.power_sum(self.found.power_exclude, strict=False),
            # nur fürs Warmwasser-Modell (nicht in der Datenbank, 0.36.3): meldet der Kessel die Desinfektion?
            # Unterscheidet sie von der Einmalladung – nur sie bestätigt den Desinfektionstermin
            "dhw_disinfecting": flag(self._boiler("dhw_disinfecting")),
            # nur für die Live-Ansicht (nicht in der Datenbank): läuft die Heizungspumpe?
            "pump_on": (int(num(self._boiler("pump_mod")) > 0) if num(self._boiler("pump_mod")) is not None
                        else flag(self._boiler("heating_pump"))),
        }
        self.valve_subst = set()  # Räume mit Ersatz-Ventilwert in diesem Messpunkt (room_reading)
        rooms = [row for row in (self.room_reading(r) for r in self.found.rooms) if row is not None]
        return self._check_ranges(sample, rooms, persons)

    # ------------------------------------------------------------------ Räume
    def _device_reading(self, dev: dict) -> tuple | None:
        """(Temperatur, Soll, Ventil, Feuchte, Ersatzwert, echt) eines Thermostats; None, wenn es keinen Zustand hat.
        Ersatzwert: Das Gerät hat einen Ventilsensor, der gerade nichts meldet oder veraltet ist (seit 0.45,
        ``room_heating.stale``) – das Ventil kommt dann aus ``hvac_action``. Echt: das Ventil kommt vom Sensor (%).
        Ein Sensor, den Home Assistant im Betrieb entfernt hat (``Engine.on_state``), zählt wie keiner (0.45.1)."""
        st = self.states.get(dev["climate"])
        if not st:
            return None
        a = st.get("attributes", {})
        pos = num(self._val(dev.get("valve_pos")))  # 0.51: ventilgeführt – die gestellte Öffnung ist der Ventilwert
        if pos is not None and 0 <= pos <= 100:
            target = num(a.get("temperature")) if st.get("state") != "off" else None
            hum = num(self._val(dev.get("humidity")))
            hum = num(a.get("current_humidity")) if hum is None else hum
            return num(a.get("current_temperature")), target, pos, hum, False, True
        sensor = dev.get("heating_power")
        if sensor in self.__dict__.get("_gone", ()):
            sensor = None
        valve = num(self._val(sensor))
        if valve is not None and room_heating.stale((self.states or {}).get(sensor), valve, a.get("hvac_action"),
                                                    time.time()):
            valve = None
        subst = valve is None and bool(sensor)
        real = valve is not None
        if valve is None:
            valve = 100.0 if a.get("hvac_action") == "heating" else 0.0
        hum = num(self._val(dev.get("humidity")))
        if hum is None:
            hum = num(a.get("current_humidity"))
        target = num(a.get("temperature")) if st.get("state") != "off" else None
        return num(a.get("current_temperature")), target, valve, hum, subst, real

    def room_reading(self, r) -> tuple | None:
        """(climate, Temperatur, Soll, Ventil, Feuchte) eines logischen Raums.

        Mit Raumregler zählen dessen Temperatur und Soll; sonst ist die Raumtemperatur das Mittel
        aller Geräte der Gruppe, das Soll das des Schlüssels. Das Ventil ist das Mittel aller
        Geräte – seit 0.45 nur der echten (Sensor in %), sobald es mindestens einen gibt: 0/100 % aus ``hvac_action``
        anderer Geräte verwässern es nicht. (Fehlt der Schlüssel, fehlt der Raum – wie bisher.) Ist ein Ventilwert ein
        Ersatzwert, merkt sich der Raum das in ``valve_subst`` (das Ventilmodell lernt ihn nicht, seit 0.39).
        """
        head = self._device_reading(r.devices[0])
        if head is None:
            return None
        others = [x for x in (self._device_reading(d) for d in r.members) if x is not None]
        if any(x[4] for x in (head, *others)):
            self.__dict__.setdefault("valve_subst", set()).add(r.climate)
        temps = [head[0]] if r.controller else [x[0] for x in (head, *others) if x[0] is not None]
        real = [x[2] for x in (head, *others) if x[5]]
        valves = real or [x[2] for x in (head, *others) if x[2] is not None]
        hums = [x[3] for x in (head, *others) if x[3] is not None]
        return (r.climate, sum(temps) / len(temps) if temps else None, head[1],
                sum(valves) / len(valves) if valves else None, hums[0] if hums else None)

    # ------------------------------------------------------------------ Prüfen und ergänzen
    def _check_ranges(self, sample: dict, rooms: list[tuple], persons: list[tuple]):
        """Werte außerhalb physikalischer Grenzen verwerfen (z. B. EMS-Platzhalter 3276,7)."""
        rejected = set()
        for key, value in sample.items():
            if key in plausibility.RANGES and value is not None:
                sample[key] = plausibility.in_range(key, value)
                if sample[key] is None:
                    rejected.add(key)
        clean = []
        for climate, temp, target, valve, hum in rooms:
            row = (climate, plausibility.in_range("room_temp", temp), plausibility.in_range("room_target", target),
                   plausibility.in_range("valve", valve), plausibility.in_range("humidity", hum))
            if (temp is not None and row[1] is None) or (valve is not None and row[3] is None):
                rejected.add(f"{climate}")
            clean.append(row)
        self._rejected = rejected
        return sample, clean, persons

    def add_solar(self, ts: int, sample: dict) -> None:
        """Sonneneinstrahlung auf Ost/Süd/West zum Zeitpunkt – aus Standort, Uhrzeit und Bewölkung; seit 0.54 dazu
        der Wind in m/s (``wind``, aus der Wetter-Integration)."""
        attrs = (self.states.get(self.found.weather) or {}).get("attributes", {}) if self.found.weather else {}
        sample["wind"] = wind_ms(attrs.get("wind_speed"), attrs.get("wind_speed_unit"))
        if not self.found.home_zone:
            return
        cloud = num(attrs.get("cloud_coverage"))
        lat, lon = self.found.home_zone
        sample["sun_e"], sample["sun_s"], sample["sun_w"] = solar.facade_inputs(ts, lat, lon, cloud)

    def filter_room_spikes(self, ts: int, rooms: list[tuple]) -> list[tuple]:
        """Einzelne Temperatur-Spitzen je Raum verwerfen (vor dem Speichern)."""
        out = []
        for climate, temp, target, valve, hum in rooms:
            kept = self.room_spikes(climate, ts, temp)
            if temp is not None and kept is None:
                self.store.add_daily(local(ts).date().isoformat(), "ignored_room_spike", 1)
                log.info("Raum %s: Temperatursprung auf %.1f °C ignoriert (Einzelspitze)", climate, temp)
            out.append((climate, kept, target, valve, hum))
        return out

    def count_rejected(self, ts: int) -> None:
        if self._rejected:
            self.store.add_daily(local(ts).date().isoformat(), "ignored_values", len(self._rejected))
            log.debug("Unplausible Werte verworfen: %s", sorted(self._rejected))


WIND_UNITS = {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 0.514444, "ft/s": 0.3048}


def wind_ms(value, unit) -> float | None:
    """Windgeschwindigkeit in m/s (0.54) – Home Assistant meldet sie in der Einheit des Nutzers (Standard km/h)."""
    v = num(value)
    if v is None or v < 0 or v > 100 * 3.6:
        return None
    return round(v * WIND_UNITS.get(str(unit or "km/h").strip(), 1 / 3.6), 2)
