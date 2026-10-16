"""Optional: alle Werte direkt von EMS-ESP lesen (REST-API, ohne Token).

``GET http://<host>/api/<gerät>`` liefert alle Werte eines Geräts auf einmal –
auch die, die in Home Assistant nicht aktiviert sind. Kessel und Regler werden
bei jeder Abfrage gelesen; welche weiteren Geräte (Mischer, Solar, Sensoren …)
es gibt, wird beim ersten Mal ermittelt, sie werden seltener gelesen. Lesen
braucht weder Passwort noch Token. Was in EMS-ESP unter „Customizations“ für
API/MQTT ausgeschlossen ist, liefert EMS-ESP auch hier nicht. Verschachtelte Werte (``dhw``, ``hc1``) werden zu ``dhw_charging``,
``hc1_seltemp`` usw. flachgeklopft – passend zu den Kurznamen in
``discovery.BOILER_ROLES``/``THERMOSTAT_ROLES``.

Diese Klasse kann ausschließlich lesen (GET). Es gibt bewusst keinen Code,
der Werte an EMS-ESP sendet – Einstellungen der Therme bleiben unangetastet.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)

DEVICES = ("boiler", "thermostat")
# weitere Gerätetypen von EMS-ESP; nur die, die antworten, werden danach gelesen
EXTRA_DEVICES = ("mixer", "solar", "heatpump", "heatsource", "switch", "controller", "gateway", "connect",
                 "extension", "alert", "ventilation", "water", "pool", "temperaturesensor", "analogsensor",
                 "custom")
EXTRA_EVERY = 10  # weitere Geräte nur bei jeder 10. Abfrage (alle 5 min)


def flatten(data: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{name}_"))
        else:
            out[name] = value
    return out


class EmsEsp:
    def __init__(self, host: str, session: aiohttp.ClientSession | None = None) -> None:
        host = host.strip().rstrip("/")
        self.base = host if host.startswith("http") else f"http://{host}"
        self._session = session
        self.ok = False
        self.extra: list[str] | None = None  # gefundene weitere Geräte (None = noch nicht gesucht)
        self._extra_values: dict[str, dict] = {}
        self._calls = 0

    async def _get(self, device: str) -> dict | None:
        try:
            async with self._session.get(f"{self.base}/api/{device}") as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            if self.ok and device in DEVICES:
                log.warning("EMS-ESP %s nicht erreichbar: %s", device, err)
            return None
        if not isinstance(data, dict) or not data or set(data) <= {"message", "error"}:
            return None
        return flatten(data)

    async def read(self) -> dict[str, dict]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        out: dict[str, dict] = {}
        for device in DEVICES:
            values = await self._get(device)
            if values:
                out[device] = values
        self.ok = bool(out)
        if self.ok and (self.extra is None or self._calls % EXTRA_EVERY == 0):
            found = []
            for device in (EXTRA_DEVICES if self.extra is None else self.extra):
                values = await self._get(device)
                if values:
                    self._extra_values[device] = values
                    found.append(device)
            if self.extra is None:
                self.extra = found
                log.info("EMS-ESP: Kessel %d, Regler %d Werte; weitere Geräte: %s",
                         len(out.get("boiler", {})), len(out.get("thermostat", {})), ", ".join(found) or "keine")
        self._calls += 1
        out.update(self._extra_values)
        return out

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
