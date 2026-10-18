"""Bedienung im Reiter „Heizen“: Tado-Timer, Soll ändern, zurück zum Zeitplan.

Nur auf Knopfdruck – HeatPilot schaltet nie von selbst. Erlaubt sind ausschließlich diese
Aufrufe auf die Thermostate (``climate.*``) der gewählten Räume:

* ``tado.set_climate_timer`` – Tado hält die Temperatur für die Dauer und kehrt danach
  selbst zum Zeitplan zurück (läuft also auch weiter, wenn das Add-on neu startet),
* ``climate.set_temperature`` – Soll eines Raums ändern (wie am Tado-Thermostat),
* ``climate.set_hvac_mode`` – Zeitplan (``auto``), Aus (``off``) oder Manuell (``heat``).

Alles andere – auch jeder Schreibzugriff auf EMS-ESP – wird hart abgewiesen.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

ALLOWED = {("tado", "set_climate_timer"), ("climate", "set_temperature"), ("climate", "set_hvac_mode")}
MODES = ("auto", "off", "heat")
TEMP_MIN, TEMP_MAX = 5.0, 25.0
DUR_MIN, DUR_MAX = 15, 12 * 60
KV = "control.boosts"


class ControlError(ValueError):
    pass


def _hhmmss(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


class Control:
    def __init__(self, ha, store, rooms: list[str]) -> None:
        self.ha = ha
        self.store = store
        self.rooms = set(rooms)
        # nur zur Anzeige der Restzeit – zurückgestellt wird von Tado selbst
        self.boosts: dict[str, dict] = dict(store.get(KV) or {})

    def _check_rooms(self, rooms) -> list[str]:
        rooms = [r for r in (rooms or []) if isinstance(r, str)]
        if not rooms:
            raise ControlError("Kein Raum gewählt")
        bad = [r for r in rooms if r not in self.rooms or not r.startswith("climate.")]
        if bad:
            raise ControlError(f"Unbekannter Raum: {', '.join(bad)}")
        return rooms

    @staticmethod
    def _temp(value) -> float:
        try:
            t = round(float(value) * 2) / 2
        except (TypeError, ValueError):
            raise ControlError("Temperatur fehlt") from None
        if not TEMP_MIN <= t <= TEMP_MAX:
            raise ControlError(f"Temperatur muss zwischen {TEMP_MIN:g} und {TEMP_MAX:g} °C liegen")
        return t

    async def _call(self, domain: str, service: str, entity_id: str, data: dict) -> None:
        if (domain, service) not in ALLOWED or entity_id not in self.rooms:
            raise PermissionError(f"{domain}.{service} auf {entity_id} ist gesperrt")
        await self.ha.call({"type": "call_service", "domain": domain, "service": service,
                            "service_data": data, "target": {"entity_id": entity_id}}, timeout=30)

    def _save(self) -> None:
        now = time.time()
        self.boosts = {r: b for r, b in self.boosts.items() if b["end"] > now}
        self.store.put(KV, self.boosts)
        self.store.commit()

    async def _each(self, rooms: list[str], what: str, fn) -> dict:
        done, errors = [], []
        for r in rooms:
            try:
                await fn(r)
                done.append(r)
            except Exception as err:  # noqa: BLE001 – je Raum melden, die anderen trotzdem schalten
                errors.append(f"{r}: {err}")
        self._save()
        log.info("%s: %s%s", what, ", ".join(done) or "–", f" · Fehler: {'; '.join(errors)}" if errors else "")
        return {"ok": not errors, "done": done, "errors": errors}

    async def boost(self, rooms, temp, minutes) -> dict:
        rooms, t = self._check_rooms(rooms), self._temp(temp)
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ControlError("Dauer fehlt") from None
        if not DUR_MIN <= minutes <= DUR_MAX:
            raise ControlError(f"Dauer muss zwischen {DUR_MIN} min und {DUR_MAX // 60} h liegen")
        end = int(time.time()) + minutes * 60

        async def one(r):
            await self._call("tado", "set_climate_timer", r, {"temperature": t, "time_period": _hhmmss(minutes)})
            self.boosts[r] = {"temp": t, "end": end}

        return await self._each(rooms, f"Tado-Timer {t:g} °C für {minutes} min", one)

    async def schedule(self, rooms) -> dict:
        """Zurück zum Tado-Zeitplan (beendet auch einen laufenden Timer)."""
        async def one(r):
            await self._call("climate", "set_hvac_mode", r, {"hvac_mode": "auto"})
            self.boosts.pop(r, None)

        return await self._each(self._check_rooms(rooms), "Zeitplan", one)

    async def set_temp(self, room, temp) -> dict:
        r, t = self._check_rooms([room])[0], self._temp(temp)

        async def one(r):
            await self._call("climate", "set_temperature", r, {"temperature": t})
            self.boosts.pop(r, None)

        return await self._each([r], f"Soll {t:g} °C", one)

    async def set_mode(self, rooms, mode) -> dict:
        """Betriebsart eines Raums oder mehrerer („alle aus“ im Dialog)."""
        rs = self._check_rooms([rooms] if isinstance(rooms, str) else rooms)
        if mode not in MODES:
            raise ControlError("Unbekannte Betriebsart")

        async def one(r):
            await self._call("climate", "set_hvac_mode", r, {"hvac_mode": mode})
            self.boosts.pop(r, None)

        return await self._each(rs, f"Betriebsart {mode}", one)

    def active(self, targets: dict | None = None, now: float | None = None) -> dict:
        """Laufende Timer mit Restzeit – nur solange das Soll noch dem Timer entspricht."""
        now = now or time.time()
        out = {}
        for r, b in self.boosts.items():
            target = (targets or {}).get(r, b["temp"])
            if b["end"] > now and target is not None and abs(float(target) - b["temp"]) < 0.1:
                out[r] = {**b, "left_min": max(1, round((b["end"] - now) / 60))}
        return out
