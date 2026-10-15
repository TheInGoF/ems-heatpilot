"""Optional: alle Werte direkt von EMS-ESP lesen (REST-API, ohne Token).

``GET http://<host>/api/boiler`` und ``/api/thermostat`` liefern alle Werte
eines Geräts auf einmal – auch die, die in Home Assistant nicht aktiviert
sind. Verschachtelte Werte (``dhw``, ``hc1``) werden zu ``dhw_charging``,
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

    async def read(self) -> dict[str, dict]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        out: dict[str, dict] = {}
        for device in DEVICES:
            try:
                async with self._session.get(f"{self.base}/api/{device}") as resp:
                    if resp.status != 200:
                        continue
                    out[device] = flatten(await resp.json(content_type=None))
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                if self.ok:
                    log.warning("EMS-ESP %s nicht erreichbar: %s", device, err)
        self.ok = bool(out)
        return out
