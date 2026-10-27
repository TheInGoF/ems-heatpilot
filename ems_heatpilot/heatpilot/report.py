"""Fehlerbericht (seit 0.59.2): ein anonymisierter Export für ein GitHub-Issue (Live → Diagnose → Dateien).

Eine ZIP-Datei mit drei Teilen:

* ``info.json`` – Version, Kesselmodell (Gerät der Kessel-Entitäten in Home Assistant), Thermostatstufe je Raum
  (``stage``), die drei Schalter, Selbsttest;
* ``langzeit_30_tage.json`` – das Gelernte mit den Stundenwerten der letzten 30 Tage (``diagnostics.export``);
* ``kurzzeit_<h>h.csv`` – die 30-s-Werte der letzten 12 oder 24 h (``live_log.csv_text``).

Anonymisiert (``Anonymizer``): Raum-, Bereichs-, Personen- und Ortsnamen, Gerätenamen und Entitäten werden durch
„Raum 1“, „Person 1“, „Ort 1“, „Gerät 1“, ``climate.raum_1``, ``sensor.e1`` … ersetzt, Koordinaten fallen weg. Die
Entitäten von EMS-ESP (``boiler_…``, ``thermostat_…``) und von HeatPilot bleiben lesbar – sie sagen nichts über den
Haushalt, helfen aber bei der Fehlersuche. Auch anonymisiert zeigt der Bericht, wann jemand zu Hause ist und schläft;
deshalb fragt die Oberfläche vor dem Melden auf GitHub nach.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime

from . import __version__, diagnostics, live_log
from .discovery import ROOM_PLATFORMS, RegistryIndex

ISSUE_URL = "https://github.com/TheInGoF/ems-heatpilot/issues/new?template=fehler.yml"
LONG_DAYS = 30
SHORT_HOURS = (12, 24)
KEEP_PREFIXES = ("boiler_", "thermostat_", "ems_esp", "heatpilot", "dhw_", "mixer_", "solar_")
DROP_KEYS = {"latitude", "longitude", "gps_accuracy", "entity_picture", "emsesp_host", "notify_targets"}
STAGES = {1: "Stufe 1 – indirekt, träge (Soll, Tado)", 2: "Stufe 2 – Soll direkt",
          3: "Stufe 3 – Ventil direkt (PID)"}
_EDGE = "A-Za-zÄÖÜäöüß0-9"


def stage(room: dict) -> int:
    """Thermostatstufe eines Raums (Konzeptpapier, Abschnitt 3): 3 = Ventil direkt geführt, 1 = Tado bzw. die
    HomeKit-Bridge von Tado (indirekt über das Soll), sonst 2 (Soll direkt, z. B. Zigbee)."""
    if room.get("lead") == "ventil" and room.get("valve_pos"):
        return 3
    return 1 if room.get("platform") in ROOM_PLATFORMS else 2


def boiler_model(eng, reg) -> str | None:
    """Kesselmodell aus der Geräteregistry von Home Assistant: das Gerät, zu dem die Kessel-Entitäten von EMS-ESP
    gehören (Hersteller und Modell), sonst None."""
    idx = RegistryIndex(reg)
    for eid in sorted(eng.states):
        if not eid.split(".")[-1].startswith("boiler_"):
            continue
        dev = idx.device.get(idx.entity.get(eid, {}).get("device_id") or "", {})
        text = " ".join(str(dev.get(k)) for k in ("manufacturer", "model") if dev.get(k))
        if text:
            return text
    return None


class Anonymizer:
    """Ersetzt bekannte Namen und Entitäten in Texten, Schlüsseln und Werten; längste zuerst, nur ganze Wörter."""

    def __init__(self, eng) -> None:
        self.pairs: dict[str, str] = {}
        rooms = [r.__dict__ for r in eng.found.rooms]
        for i, r in enumerate(rooms, 1):
            self._add(r.get("climate"), f"climate.raum_{i}")
            self._add(r.get("room_id"), f"raum_{i}")
            for k in ("name", "area", "group"):
                self._add(r.get(k), f"Raum {i}")
            self._add(r.get("device_name"), f"Gerät {i}")
        for room in eng.cfg.rooms or []:
            idx = next((i for i, r in enumerate(rooms, 1) if r.get("climate") == room.get("climate")), None)
            if idx:
                for k in ("name", "group"):
                    self._add(room.get(k), f"Raum {idx}")
                self._add(room.get("id"), f"raum_{idx}")
        for i, p in enumerate(eng.found.persons, 1):
            self._add(p, f"person.p{i}")
            self._add(self._friendly(eng, p), f"Person {i}")
        zones = sorted(e for e in eng.states if e.startswith("zone."))
        for i, z in enumerate(zones, 1):
            self._add(z, f"zone.ort_{i}")
            self._add(self._friendly(eng, z), f"Ort {i}")
        n = 0
        for eid in sorted(eng.states):
            if eid in self.pairs or eid.split(".")[-1].startswith(KEEP_PREFIXES):
                continue
            n += 1
            self._add(eid, f"{eid.split('.')[0]}.e{n}")
        keys = sorted(self.pairs, key=len, reverse=True)
        self._re = re.compile("|".join(f"(?<![{_EDGE}]){re.escape(k)}(?![{_EDGE}])" for k in keys)) if keys else None

    @staticmethod
    def _friendly(eng, eid: str) -> str | None:
        return (eng.states.get(eid) or {}).get("attributes", {}).get("friendly_name")

    def _add(self, real, fake: str) -> None:
        if isinstance(real, str) and len(real.strip()) >= 2 and real not in self.pairs:
            self.pairs[real] = fake

    def text(self, value: str) -> str:
        return self._re.sub(lambda m: self.pairs[m.group(0)], value) if self._re else value

    def data(self, value):
        """JSON-Daten rekursiv: Strings ersetzen, Koordinaten und Zugangsdaten weglassen."""
        if isinstance(value, dict):
            return {self.text(str(k)): self.data(v) for k, v in value.items() if k not in DROP_KEYS}
        if isinstance(value, list):
            return [self.data(v) for v in value]
        return self.text(value) if isinstance(value, str) else value


def info(app, reg) -> dict:
    """Kopf des Berichts: Version, Kesselmodell, Stufen je Raum, Schalter, Selbsttest."""
    eng, cfg = app.eng, app.cfg
    stages = []
    for r in eng.found.rooms:
        saved = next((x for x in cfg.rooms or [] if x.get("climate") == r.climate), {})
        s = stage({"platform": r.platform, **saved})
        stages.append({"climate": r.climate, "platform": r.platform, "stage": s, "text": STAGES[s],
                       "external_sensor": bool(saved.get("temperature") or r.temp_sensor)})
    return {"created": datetime.now().astimezone().isoformat(timespec="minutes"), "version": __version__,
            "boiler_model": boiler_model(eng, reg) or "unbekannt", "thermostats": stages,
            "switches": {"control_mode": cfg.control_mode, "boiler_control": cfg.boiler_control,
                         "dhw_control": cfg.dhw_control},
            "self_check": diagnostics.self_check(app)}


def build(app, reg, hours: int = 24) -> bytes:
    """Die ZIP-Datei des Fehlerberichts (anonymisiert)."""
    hours = hours if hours in SHORT_HOURS else SHORT_HOURS[-1]
    anon = Anonymizer(app.eng)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(anon.data(info(app, reg)), ensure_ascii=False, indent=1, default=str))
        z.writestr(f"langzeit_{LONG_DAYS}_tage.json", json.dumps(anon.data(diagnostics.export(app, LONG_DAYS)),
                                                                 ensure_ascii=False, default=str, separators=(",", ":")))
        z.writestr(f"kurzzeit_{hours}h.csv", anon.text(live_log.csv_text(app.eng, hours)))
    return out.getvalue()


def routes(app, json_response) -> list[tuple]:
    from aiohttp import web

    async def download(req):
        if app.eng is None:
            return web.Response(status=503, text="Add-on startet noch")
        try:
            hours = int(req.query.get("hours", SHORT_HOURS[-1]))
        except ValueError:
            hours = SHORT_HOURS[-1]
        body = build(app, await app.registry(), hours)
        name = f"heatpilot-fehlerbericht-{datetime.now():%Y%m%d-%H%M}.zip"
        return web.Response(body=body, content_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return [("GET", "/api/report", download)]
