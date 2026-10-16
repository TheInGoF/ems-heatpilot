"""Diagnose: vollständiges Log, Selbsttest und eine Diagnose-Datei zum Weitergeben.

* Log: jede Meldung landet in einem Ringpuffer (für die Oberfläche) und in einer
  rotierenden Datei. Mit ``addon_config``-Freigabe liegt sie unter
  ``/addon_configs/<slug>/logs/heatpilot.log`` (Samba/File-Editor), sonst in ``/data/logs``.
* Selbsttest: prüft laufend auf fehlende/veraltete Sensoren, unmögliche Werte,
  einen Gaszähler, der trotz Brenner nicht zählt, Aufzeichnungslücken und Programmfehler.
* Diagnose-Datei: alles Nötige zur Fehlersuche in einer JSON-Datei – ohne
  GPS-Koordinaten (nur Entfernungen) und ohne Zugangsdaten.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .engine import num

LOG_RING_SIZE = 3000
CORE_ROLES = ("flow_temp", "burner_on", "burner_power", "dhw_temp", "dhw_active")
STALE_HOURS = 6

log = logging.getLogger(__name__)


class RingHandler(logging.Handler):
    """Hält die letzten Meldungen im Speicher (für Oberfläche und Diagnose-Datei)."""

    def __init__(self, size: int = LOG_RING_SIZE) -> None:
        super().__init__(logging.DEBUG)
        self.records: deque[dict] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                entry["exception"] = logging.Formatter().formatException(record.exc_info)
            self.records.append(entry)
        except Exception:  # noqa: BLE001 – Logging darf nie selbst abstürzen
            self.handleError(record)

    def tail(self, n: int = 500, min_level: str = "DEBUG") -> list[dict]:
        level = logging.getLevelName(min_level)
        items = [r for r in self.records if logging.getLevelName(r["level"]) >= level]
        return items[-n:]


RING = RingHandler()


def log_dir() -> Path:
    """Bevorzugt /config (addon_config, per Samba erreichbar), sonst /data/logs."""
    base = Path(os.environ.get("HEATPILOT_LOG_DIR", "/config"))
    if not base.exists() or not os.access(base, os.W_OK):
        base = Path(os.environ.get("HEATPILOT_DATA", "/data"))
    path = base / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(debug: bool = False) -> Path | None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(RING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    try:
        path = log_dir() / "heatpilot.log"
        file = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        file.setFormatter(fmt)
        root.addHandler(file)
        return path
    except OSError as err:
        root.warning("Log-Datei nicht möglich: %s", err)
        return None


def set_debug(debug: bool) -> None:
    logging.getLogger().setLevel(logging.DEBUG if debug else logging.INFO)


# ---------------------------------------------------------------- Selbsttest
def _age_hours(state: dict | None) -> float | None:
    if not state:
        return None
    stamp = state.get("last_updated") or state.get("last_changed")
    if not stamp:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() / 3600
    except ValueError:
        return None


def self_check(app) -> list[dict]:
    """Auffälligkeiten im Klartext: level = ok | info | warn | error."""
    out: list[dict] = []

    def add(level: str, area: str, message: str) -> None:
        out.append({"level": level, "area": area, "message": message})

    eng = app.eng
    if eng is None:
        add("info", "Start", f"Add-on startet noch: {app.status}")
        return out
    if not app.ha.connected.is_set():
        add("error", "Home Assistant", "Keine Verbindung zu Home Assistant")
    if not app.configured:
        add("warn", "Einrichtung", "Einstellungen wurden noch nie gespeichert – bitte im Reiter Einstellungen prüfen")

    f = eng.found
    for role in CORE_ROLES:
        if role not in f.boiler and not eng.ems.get("boiler"):
            add("warn", "Kessel", f"Wert „{role}“ nicht gefunden – ggf. unter Zuordnungen festlegen")
    if not f.rooms:
        add("error", "Räume", "Keine Räume gefunden – im Reiter Einstellungen Thermostate auswählen")
    if not f.gas:
        add("warn", "Gas", "Kein Gaszähler – Wärmebedarf und Kosten lassen sich nicht lernen")
    elif (f.gas_unit or "").lower() == "kwh" and f.gas in (f.boiler.get("nrg_total"), f.boiler.get("nrg_heat")):
        add("info", "Gas", "Nur Energie-Schätzung von EMS-ESP statt echtem Gaszähler")
    if not f.outdoor and not f.weather:
        add("error", "Außentemperatur", "Weder Außenfühler noch Wetter gefunden")
    if app.cfg.emsesp_host and not eng.ems:
        add("warn", "EMS-ESP", f"Keine Daten von {app.cfg.emsesp_host} – Adresse prüfen (Einstellungen → Testen)")

    critical = [f.gas, *f.outdoor, f.boiler.get("flow_temp"), *(r.climate for r in f.rooms)]
    for eid in filter(None, critical):
        st = eng.states.get(eid)
        if st is None:
            add("error", "Sensoren", f"{eid} existiert nicht (mehr) in Home Assistant")
            continue
        if st.get("state") in ("unavailable", "unknown"):
            add("warn", "Sensoren", f"{eid} ist „{st.get('state')}“")
        age = _age_hours(st)
        if age is not None and age > STALE_HOURS and eid != f.gas:
            add("warn", "Sensoren", f"{eid} seit {age:.0f} h nicht aktualisiert")

    for climate, temp, target, valve, _ in eng.snapshot()[1]:
        if temp is not None and not 5 <= temp <= 35:
            add("warn", "Räume", f"{climate}: Raumtemperatur {temp} °C unplausibel")
    flow = num(eng._boiler("flow_temp"))
    if flow is not None and not 5 <= flow <= 100:
        add("warn", "Kessel", f"Vorlauftemperatur {flow} °C unplausibel")
    dhw = num(eng._boiler("dhw_temp"))
    if dhw is not None and not 5 <= dhw <= 90:
        add("warn", "Warmwasser", f"Speichertemperatur {dhw} °C unplausibel")

    now = int(time.time())
    last = eng.store.query("SELECT MAX(ts) AS t FROM samples")[0]["t"]
    if last is None:
        add("info", "Aufzeichnung", "Noch keine Messwerte aufgezeichnet")
    elif now - last > 3 * app.cfg.sample_interval_s:
        add("warn", "Aufzeichnung", f"Letzter Messwert vor {(now - last) / 60:.0f} min – Takt hängt?")

    # Gaszähler zählt nicht, obwohl der Brenner lief?
    rows = eng.store.query(
        "SELECT MIN(gas) AS g0, MAX(gas) AS g1, SUM(burner_on) AS on_n, COUNT(*) AS n "
        "FROM samples WHERE ts >= ?", (now - 6 * 3600,))
    r = rows[0]
    if r["n"] and r["on_n"] and r["on_n"] >= 6 and r["g0"] is not None and r["g1"] == r["g0"]:
        add("warn", "Gas", f"Brenner lief in den letzten 6 h ({r['on_n']} Messpunkte), Gaszähler blieb bei {r['g1']}")
    drops = eng.store.query(
        "SELECT COUNT(*) AS n FROM (SELECT gas, LAG(gas) OVER (ORDER BY ts) AS prev FROM samples "
        "WHERE ts >= ? AND gas IS NOT NULL) WHERE gas < prev", (now - 86400,))[0]["n"]
    if drops:
        add("info", "Gas", f"Gaszähler ist in 24 h {drops}× gesunken (Neustart/Zurücksetzen?) – diese Stunden werden ignoriert")

    s = eng.summary()
    for name, chk in (("Hausmodell", s["guards"]["house"]), ("Warmwasser-Modell", s["guards"]["dhw"])):
        if not chk["ok"]:
            add("info", "Modelle", f"{name}: keine Empfehlung – " + "; ".join(chk["reasons"]))
    if s["savings"]["cold_hours"]:
        add("info", "Komfort", f"Strategie wäre {s['savings']['cold_hours']} h zu kalt gewesen")

    errors = [x for x in RING.records if x["level"] in ("ERROR", "CRITICAL")
              and x["ts"] >= datetime.fromtimestamp(now - 86400).astimezone().isoformat(timespec="seconds")]
    if errors:
        add("error", "Programm", f"{len(errors)} Fehler in 24 h – letzter: {errors[-1]['message']}")

    if not any(x["level"] in ("warn", "error") for x in out):
        add("ok", "Gesamt", "Keine Auffälligkeiten")
    return out


# ---------------------------------------------------------------- Diagnose-Datei
def _rows(eng, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in eng.store.query(sql, params)]


def bundle(app, hours: int = 48) -> dict:
    """Alles zur Fehlersuche – ohne GPS-Koordinaten und Zugangsdaten."""
    eng = app.eng
    data: dict = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": __version__,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "status": app.status,
        "configured": app.configured,
        "settings": app.cfg.to_raw(),
        "self_check": self_check(app),
        "log": RING.tail(LOG_RING_SIZE),
    }
    if eng is None:
        return data
    since = int(time.time()) - hours * 3600
    data["discovered"] = eng.found.as_dict(private=False)
    data["emsesp_values"] = eng.ems
    data["entity_states"] = {
        eid: {"state": st.get("state"), "last_updated": st.get("last_updated"),
              "attributes": {k: v for k, v in st.get("attributes", {}).items()
                             if k not in ("latitude", "longitude", "gps_accuracy", "entity_picture")}}
        for eid, st in eng.states.items()
        if eid in set(eng.found.all_sensor_ids()) | {r.climate for r in eng.found.rooms}
        | set(eng.found.persons) | {eng.found.weather}
    }
    data["summary"] = eng.summary()
    data["models"] = {
        "house": eng.house.to_dict(),
        "rooms": {c: m.to_dict() for c, m in eng.rooms.items()},
        "dhw": {k: v for k, v in eng.dhw.to_dict().items() if k not in ("profile", "profile_n")},
        "persons": {p: {k: v for k, v in l.to_dict().items() if k != "week"} for p, l in eng.persons.items()},
    }
    data["db"] = {
        t: _rows(eng, f"SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM {t}")[0]
        for t in ("samples", "room_samples", "person_samples", "hourly", "burner_events")
    }
    data["samples"] = _rows(eng, "SELECT * FROM samples WHERE ts >= ? ORDER BY ts", (since,))
    data["room_samples"] = _rows(eng, "SELECT * FROM room_samples WHERE ts >= ? ORDER BY ts", (since,))
    data["person_samples"] = _rows(eng, "SELECT * FROM person_samples WHERE ts >= ? ORDER BY ts", (since,))
    data["hourly"] = _rows(eng, "SELECT * FROM hourly WHERE ts >= ? ORDER BY ts", (since - 5 * 86400,))
    data["burner_events"] = _rows(eng, "SELECT * FROM burner_events WHERE ts >= ? ORDER BY ts", (since,))
    data["daily"] = _rows(eng, "SELECT * FROM daily ORDER BY day DESC LIMIT 400")
    data["kv"] = {r["key"]: r["value"] for r in _rows(eng, "SELECT key, value FROM kv")
                  if not r["key"].startswith("model.")}
    return data
