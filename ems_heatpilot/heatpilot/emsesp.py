"""Optional: alle Werte direkt von EMS-ESP lesen (REST-API, ohne Token).

``GET http://<host>/api/<gerät>`` liefert alle Werte eines Geräts auf einmal –
auch die, die in Home Assistant nicht aktiviert sind. Kessel und Regler werden
bei jeder Abfrage gelesen; welche weiteren Geräte (Mischer, Solar, Sensoren …)
es gibt, wird beim ersten Mal ermittelt, sie werden seltener gelesen. Lesen
braucht weder Passwort noch Token. Was in EMS-ESP unter „Customizations“ für
API/MQTT ausgeschlossen ist, liefert EMS-ESP auch hier nicht. Verschachtelte Werte (``dhw``, ``hc1``) werden zu ``dhw_charging``,
``hc1_seltemp`` usw. flachgeklopft – passend zu den Kurznamen in
``discovery.BOILER_ROLES``/``THERMOSTAT_ROLES``.

Seit 0.36.1 fragt ``controls()`` zusätzlich einzelne Kesselsteuerungen ab
(``GET /api/boiler/<kurzname>``): Wert, Bereich, Einheit und ob EMS-ESP sie schreiben ließe
(``writeable``). Das ist nur Anzeige für „Bereit für die Steuerung?“ – was eine spätere
Kesselsteuerung bräuchte (``BOILER_CONTROLS``, siehe docs/planung-vorlauf-ems.md).

Diese Klasse kann ausschließlich lesen (GET) – auch diese Abfragen. Es gibt bewusst keinen Code,
der Werte an EMS-ESP sendet, auch keinen vorbereiteten – Einstellungen der Therme bleiben unangetastet.
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

# Kesselsteuerungen, die eine spätere Kesselsteuerung bräuchte (seit 0.36.1, nur Anzeige, nichts schreiben):
# Schlüssel (EMS-ESP-Kurzname) → (Anzeige, nötig?, Pfade unter /api/boiler/). Neuere EMS-ESP (ab 3.6) führen
# die Warmwasserwerte unter „dhw“ (``dhw/seltemp`` statt ``wwseltemp``) – es zählt der erste Pfad, der antwortet.
BOILER_CONTROLS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "selflowtemp": ("Vorlauf-Soll", True, ("selflowtemp",)),
    "wwseltemp": ("Warmwasser-Soll", True, ("wwseltemp", "dhw/seltemp")),
    "heatingactivated": ("Heizbetrieb", True, ("heatingactivated",)),
    "wwactivated": ("Warmwasser-Betrieb", True, ("wwactivated", "dhw/activated")),
    "wwonetime": ("Warmwasser-Einmalladung", True, ("wwonetime", "dhw/onetime")),
    "heatingoff": ("Heizen abschalten", False, ("heatingoff", "forceheatingoff")),
    "burnminperiod": ("Antipendelzeit", False, ("burnminperiod",)),
    "pumpdelay": ("Pumpennachlauf", False, ("pumpdelay",)),
    "heatingtemp": ("Heiztemperatur (Obergrenze)", False, ("heatingtemp",)),
    "wwdisinfecting": ("Desinfektion", False, ("wwdisinfecting", "dhw/disinfecting")),
}
# daran erkennt man eine einzelne Entität (und nicht eine Meldung oder ein ganzes Unterverzeichnis)
ENTITY_KEYS = {"value", "writeable", "writable", "fullname", "uom", "readable"}
TRUE_TEXT = {"true", "1", "yes", "ja", "on", "an"}
FALSE_TEXT = {"false", "0", "no", "nein", "off", "aus"}
# Zeitlimit je Einzelabfrage: hängt EMS-ESP, wartet poll_emsesp nicht 15 s je Pfad (seit 0.36.1)
ENTITY_TIMEOUT_S = 3


def _scalar(value):
    """Nur einfache Werte durchreichen (Zahl, Text, Wahrheitswert) – alles andere None."""
    return value if isinstance(value, (int, float, str, bool)) or value is None else None


def _truth(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in TRUE_TEXT
    return bool(value)


def _switch(value):
    """Wert eines Schalters (EMS-ESP ``type`` boolean) als Wahrheitswert – je nach Bool-Format kommt
    „on“/„off“, „an“/„aus“, „true“/„false“ oder 1/0. Unbekanntes bleibt, wie es ist."""
    if isinstance(value, bool) or value is None:
        return value
    text = str(value).strip().lower()
    return True if text in TRUE_TEXT else False if text in FALSE_TEXT else _scalar(value)


def control_info(data: dict | None) -> dict:
    """Antwort von ``entity()`` in einheitlicher Form: {found, writeable, value, min, max, uom, fullname}.

    ``writeable`` None = EMS-ESP sagt es nicht (ältere Versionen liefern teils nur den Wert). Schalter
    (``type`` boolean) kommen als True/False, damit die Oberfläche „an“/„aus“ zeigt."""
    if data is None:
        return {"found": False}
    value = _scalar(data.get("value"))
    if str(data.get("type") or "").lower() == "boolean":
        value = _switch(value)
    return {
        "found": True,
        "writeable": _truth(data.get("writeable", data.get("writable"))),
        "value": value,
        "min": _scalar(data.get("min")),
        "max": _scalar(data.get("max")),
        "uom": _scalar(data.get("uom")) or None,
        "fullname": _scalar(data.get("fullname")),
    }


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

    def _open(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session

    async def read(self) -> dict[str, dict]:
        self._open()
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

    async def entity(self, device: str, name: str) -> dict | None:
        """Eine Entität mit Beschreibung lesen: ``GET /api/<gerät>/<name>`` (seit 0.36.1, nur GET).

        EMS-ESP ab 3.5 liefert {"name", "fullname", "value", "type", "min", "max", "uom", "readable",
        "writeable", "visible"}; andere Versionen weniger (z. B. nur {"value": …}) oder nur den Wert.
        Fehler, 404, eine Meldung statt einer Entität oder ein ganzes Unterverzeichnis → None.
        """
        try:
            return await self._entity(device, name)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            log.debug("EMS-ESP %s/%s nicht lesbar: %s", device, name, err)
            return None

    async def _entity(self, device: str, name: str) -> dict | None:
        """Wie ``entity()``, aber Zeitüberlauf und Verbindungsfehler gehen an den Aufrufer (nur GET)."""
        timeout = aiohttp.ClientTimeout(total=ENTITY_TIMEOUT_S)
        async with self._open().get(f"{self.base}/api/{device}/{name}", timeout=timeout) as resp:
            if resp.status != 200:
                return None
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                return None
        if data is None:
            return None
        if not isinstance(data, dict):
            return {"value": data} if _scalar(data) is not None else None  # nur der Wert
        if not set(data) & ENTITY_KEYS or any(isinstance(v, dict) for v in data.values()):
            return None
        return data

    async def controls(self) -> dict[str, dict]:
        """Kesselsteuerungen aus ``BOILER_CONTROLS`` (seit 0.36.1, nur Anzeige, nur GET).

        Je Steuerung {found, writeable, value, min, max, uom, fullname, path}; nicht gefunden →
        {"found": False}. Geschrieben wird nichts – es gibt hier keinen Schreibpfad.
        Antwortet EMS-ESP auf eine Abfrage nicht in ``ENTITY_TIMEOUT_S``, bricht die Runde ab
        (TimeoutError): poll_emsesp liest weiter Kesselwerte und fragt in einer Stunde erneut.
        """
        out: dict[str, dict] = {}
        for key, (_, _, paths) in BOILER_CONTROLS.items():
            found = None
            for path in paths:
                try:
                    data = await self._entity("boiler", path)
                except TimeoutError as err:  # hängt EMS-ESP, nicht alle Pfade nacheinander abwarten
                    raise TimeoutError(f"keine Antwort auf boiler/{path} in {ENTITY_TIMEOUT_S} s") from err
                except aiohttp.ClientError as err:
                    log.debug("EMS-ESP boiler/%s nicht lesbar: %s", path, err)
                    data = None
                if data is not None:
                    found = {**control_info(data), "path": path}
                    break
            out[key] = found or control_info(None)
        return out

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
