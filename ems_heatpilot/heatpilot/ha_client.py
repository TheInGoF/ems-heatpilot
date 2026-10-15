"""Verbindung zu Home Assistant über die WebSocket- und REST-API.

Als Add-on bekommt der Container automatisch ein Token (SUPERVISOR_TOKEN)
und erreicht Home Assistant unter http://supervisor/core. Darüber liest das
Add-on Zustände, die komplette Historie (Recorder) und die Langzeitstatistik –
ein manueller Export ist nicht nötig.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from datetime import datetime
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)

StateCallback = Callable[[str, dict | None], Awaitable[None] | None]


class HAError(RuntimeError):
    pass


class HAClient:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url or os.environ.get("HA_URL", "http://supervisor/core")
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._state_cb: StateCallback | None = None
        self._reader: asyncio.Task | None = None
        self.connected = asyncio.Event()

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def start(self, on_state: StateCallback) -> None:
        self._state_cb = on_state
        self._session = aiohttp.ClientSession()
        self._reader = asyncio.create_task(self._run())
        await asyncio.wait_for(self.connected.wait(), timeout=60)

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def _run(self) -> None:
        ws_url = self.base_url.replace("http", "ws", 1) + "/websocket"
        delay = 2
        while True:
            try:
                async with self._session.ws_connect(ws_url, heartbeat=30, max_msg_size=0) as ws:
                    self._ws = ws
                    await self._auth(ws)
                    delay = 2
                    await self._send_raw(
                        {"type": "subscribe_events", "event_type": "state_changed"}
                    )
                    self.connected.set()
                    log.info("Mit Home Assistant verbunden")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._dispatch(msg.json())
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 – Verbindung immer wieder aufbauen
                log.warning("Verbindung zu Home Assistant verloren: %s", err)
            self.connected.clear()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(HAError("Verbindung getrennt"))
            self._pending.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _auth(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        msg = await ws.receive_json()
        if msg.get("type") != "auth_required":
            raise HAError(f"Unerwartete Antwort: {msg}")
        await ws.send_json({"type": "auth", "access_token": self.token})
        msg = await ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise HAError(f"Anmeldung fehlgeschlagen: {msg}")

    async def _send_raw(self, payload: dict) -> asyncio.Future:
        msg_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        await self._ws.send_json({"id": msg_id, **payload})
        return fut

    async def _dispatch(self, msg: dict) -> None:
        if msg.get("type") == "event":
            data = msg["event"].get("data", {})
            if self._state_cb and "entity_id" in data:
                res = self._state_cb(data["entity_id"], data.get("new_state"))
                if asyncio.iscoroutine(res):
                    await res
            return
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        if msg.get("success", True):
            fut.set_result(msg.get("result"))
        else:
            fut.set_exception(HAError(str(msg.get("error"))))

    async def call(self, payload: dict, timeout: float = 120) -> Any:
        await self.connected.wait()
        fut = await self._send_raw(payload)
        return await asyncio.wait_for(fut, timeout)

    # --- bequeme Abfragen ---
    async def get_states(self) -> list[dict]:
        return await self.call({"type": "get_states"})

    async def entity_registry(self) -> list[dict]:
        return await self.call({"type": "config/entity_registry/list"})

    async def history(
        self,
        entity_ids: list[str],
        start: datetime,
        end: datetime,
        attributes: bool = False,
    ) -> dict[str, list[dict]]:
        """Recorder-Historie. Kompaktes Format: {"s": Zustand, "a": Attribute, "lu": Zeit}."""
        return await self.call(
            {
                "type": "history/history_during_period",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "entity_ids": entity_ids,
                "minimal_response": not attributes,
                "no_attributes": not attributes,
                "significant_changes_only": False,
            },
            timeout=300,
        )

    async def statistics(
        self, statistic_ids: list[str], start: datetime, end: datetime, period: str = "hour"
    ) -> dict[str, list[dict]]:
        """Langzeitstatistik (bleibt in HA dauerhaft erhalten, stündliche Werte)."""
        return await self.call(
            {
                "type": "recorder/statistics_during_period",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "statistic_ids": statistic_ids,
                "period": period,
                "types": ["mean", "sum", "state", "change"],
            },
            timeout=300,
        )

    async def weather_forecast(self, entity_id: str) -> list[dict]:
        res = await self.call(
            {
                "type": "call_service",
                "domain": "weather",
                "service": "get_forecasts",
                "service_data": {"type": "hourly"},
                "target": {"entity_id": entity_id},
                "return_response": True,
            }
        )
        resp = (res or {}).get("response", {})
        return resp.get(entity_id, {}).get("forecast", [])

    async def set_state(self, entity_id: str, state: Any, attributes: dict) -> None:
        """Eigene Sensoren in HA anlegen/aktualisieren (REST /api/states)."""
        url = f"{self.base_url}/api/states/{entity_id}"
        async with self._session.post(
            url,
            headers=self._headers,
            json={"state": state, "attributes": attributes},
        ) as resp:
            if resp.status >= 400:
                log.warning("Sensor %s nicht gesetzt: HTTP %s", entity_id, resp.status)
