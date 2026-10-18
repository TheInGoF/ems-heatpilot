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
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, guards
from .discovery import BOILER_ROLES, ROLE_LABELS, THERMOSTAT_ROLES
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
    ha = app.ha.info()
    if not ha["token_present"]:
        add("error", "Home Assistant", "Kein Schlüssel für Home Assistant (SUPERVISOR_TOKEN fehlt) – Add-on neu starten")
    elif ha["last_error"].startswith("Anmeldung") and not ha["connected"]:
        add("error", "Home Assistant", f"{ha['last_error']} – Add-on neu starten; hilft das nicht, Diagnose-Datei weitergeben")
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
    elif eng.ems:
        add("ok", "EMS-ESP", "Direkt gelesen: " + ", ".join(f"{d} {len(v)} Werte" for d, v in eng.ems.items()))
        for role in ("nom_power", "min_power"):
            if not any(k in eng.ems.get("boiler", {}) for k in BOILER_ROLES.get(role, ())):
                add("info", "EMS-ESP", f"{ROLE_LABELS.get(role, role)} liefert EMS-ESP nicht – fürs Zertifikat "
                                       "unter Einstellungen → Haus & Kessel eintragen")

    plant_checks(eng, add)

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

    if eng.dhw_inferred and not eng.dhw_flag_seen:
        add("info", "Warmwasser", "Kein Ladesignal vom Kessel („Warmwasser lädt“) – Ladungen werden aus dem "
                                  f"Temperaturanstieg des Speichers erkannt ({eng.dhw_inferred} Messpunkte)")
    if eng._rejected:
        add("warn", "Messwerte", "Liefert gerade unmögliche Werte, werden ignoriert: " + ", ".join(sorted(eng._rejected)))
    ig = eng.ignored()
    parts = []
    if ig["gas_spikes"]:
        parts.append(f"{ig['gas_spikes']} Gaszähler-Sprung/Nachtrag ({ig['gas_spike_kwh']:.0f} kWh, zählt nur im Verbrauch)")
    if ig["gas_missed_hours"]:
        parts.append(f"{ig['gas_missed_hours']} Stunden, in denen der Zähler trotz Brenner nicht zählte")
    if ig["gas_negative"]:
        parts.append(f"{ig['gas_negative']} Stunden mit sinkendem Zählerstand")
    if ig["room_spikes"]:
        parts.append(f"{ig['room_spikes']} Temperaturspitzen in Räumen")
    if ig["values"]:
        parts.append(f"{ig['values']} unmögliche Messwerte")
    if parts:
        add("info", "Ausreißer", f"Nicht gelernt (letzte {ig['days']} Tage): " + "; ".join(parts))
    if ig["learn_rejected"] or ig["learn_clipped"]:
        add("info", "Ausreißer", f"Lernen: {ig['learn_rejected']} grobe Ausreißer verworfen, "
                                 f"{ig['learn_clipped']} auffällige Punkte nur begrenzt gezählt (seit Beginn)")

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
PRESS_LOW, PRESS_CRIT, PRESS_HIGH = 1.0, 0.8, 2.5
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _code_date(text) -> datetime | None:
    m = _DATE.search(str(text or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def plant_checks(eng, add) -> None:
    """Anlagendruck, Wartungsmeldung und letzter Fehler des Kessels/Reglers (nur Hinweise)."""
    press = num(eng._boiler("sys_press"))
    if press is not None:
        rows = eng.store.query("SELECT MIN(syspress) AS lo FROM samples WHERE ts >= ? AND syspress IS NOT NULL",
                               (int(time.time()) - 7 * 86400,))
        low = rows[0]["lo"] if rows and rows[0]["lo"] is not None else press
        if press < PRESS_CRIT:
            add("error", "Anlagendruck", f"{press:.1f} bar – zu wenig Wasser in der Heizung, Heizungsbauer bzw. nachfüllen")
        elif press < PRESS_LOW or low < PRESS_LOW:
            add("warn", "Anlagendruck", f"{press:.1f} bar (7-Tage-Minimum {low:.1f} bar) – unter {PRESS_LOW:.1f} bar, "
                                        "bitte nachfüllen lassen")
        elif press > PRESS_HIGH:
            add("warn", "Anlagendruck", f"{press:.1f} bar – ungewöhnlich hoch (Ausdehnungsgefäß prüfen lassen)")
        else:
            add("ok", "Anlagendruck", f"{press:.1f} bar (7-Tage-Minimum {low:.1f} bar)")
    msg, date = eng._boiler("maint_msg"), eng._boiler("maint_date")
    if msg not in (None, "", "-", "0", "keine", "none", "unknown", "unavailable"):
        add("info", "Wartung", f"Kessel meldet „{msg}“" + (f" (Termin {date})" if date else "") +
            " – ist die Wartung erledigt, im Kessel bzw. in EMS-ESP zurücksetzen, sonst bleibt die Meldung stehen")
    for device, getter in (("Kessel", eng._boiler), ("Regler", eng._thermo)):
        code = getter("last_code")
        when = _code_date(code)
        if code and when and (datetime.now() - when).days <= 14:
            add("info", "Störung", f"{device}: letzter Fehlercode {code} – in den letzten 14 Tagen")


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
        "ha_connection": app.ha.info(),
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


def explain_models(eng) -> dict:
    """Gelernte Werte in Klartext – was das Modell glaubt, mit Einheit und Gütemaß."""
    def rls(m):
        return {"lernschritte": m.updates, "fehler_rmse": m.rmse, "gekappt": m.clipped, "verworfen": m.rejected,
                "parameter": m.theta}

    h = eng.house
    d = eng.dhw
    rooms = {}
    for c, m in eng.rooms.items():
        rooms[c] = {
            "fussboden": m.floor,
            "zeitkonstante_h": round(m.tau_h, 1),
            "heizwirkung_k_h_je_k": round(m.a, 5),
            "fussboden_wirkung": round(m.f, 5) if m.floor else None,
            "fremdwaerme_k_h": round(m.c, 3),
            "vorlauf_fuer_21_bei_0_grad": round(m.required_flow(21.0, 0.0), 1),
            "plausibel": guards.check_room(m),
            "rls": rls(m.rls),
        }
    return {
        "haus": {
            "waermeverlust_w_je_k": round(h.h_kw_per_k * 1000, 1),
            "speicher_kwh_je_k": round(h.c_kwh_per_k, 2),
            "grundlast_kw": round(h.base_kw, 3),
            "achsabschnitt_kw": round(h.offset_kw, 3),
            "fremdwaerme_kw": round(h.free_heat_kw, 3),
            "heizgrenze_k": None if h.heating_limit_k is None else round(h.heating_limit_k, 1),
            "traegheit_h": None if h.tau_h is None else round(h.tau_h, 1),
            "traegheit_quelle": "gasfreie Nächte" if h.tau_cool_h is not None else "Gasbilanz C/H",
            "traegheit_bilanz_h": None if h.tau_balance_h is None else round(h.tau_balance_h, 1),
            "auskuehl_lernschritte": h.cool.updates,
            "auskuehlen_schnell_k": round(h.cool_fast_k, 2),
            "fremdwaerme_uebertemperatur_k": round(h.cool_excess_k, 1),
            "rls_auskuehlen": rls(h.cool),
            "plausibel": guards.check_house(h),
            "rls": rls(h.rls),
        },
        "warmwasser": {
            "umgebung_c": round(d.ambient, 1),
            "auskuehlen_k_h": {t: round(d.cooling_kh(t), 3) for t in (40, 45, 50, 55, 60)},
            "ladekosten_kwh_je_k": d.kwh_per_k,
            "ladekosten_messungen": d.kwh_per_k_n,
            "nachladen_k_h": round(d.charge_rate_kh, 1),
            "ladungen": d.charges,
            "reserve_k": round(d.comfort_margin, 2),
            "zapfprofil_k_je_stunde": [round(v, 2) for v in d.daily],
            "plausibel": guards.check_dhw(d),
            "rls_auskuehlen": rls(d.loss),
            "messpunkte_auskuehlen": eng.dhw_points(),
        },
        "raeume": rooms,
        "zeiten": {"gelernt": eng.schedule_learned, "verwendet": eng.eff.as_dict()},
        "personen": {p: {"gewohnheiten_woche": l.week, "heimfahrt_kmh": l.approach_speed_kmh,
                         "ankuenfte_gelernt": l.arrivals_learned} for p, l in eng.persons.items()},
        "ignoriert": eng.ignored(days=30),
    }


EXPORT_STEP_S = 300  # Messwerte im Export auf 5 min verdichtet – egal welches Abtastintervall


def _table(rows: list, step: int | None = EXPORT_STEP_S, key: str | None = None) -> dict:
    """Spaltenformat: Namen einmal, Werte gerundet; ``step``: je Zeitraster (und ``key``) nur der erste Wert."""
    rows = [dict(r) for r in rows]
    if step:
        seen, kept = set(), []
        for r in rows:
            k = (r["ts"] - r["ts"] % step, r.get(key) if key else None)
            if k not in seen:
                seen.add(k)
                kept.append(r)
        rows = kept
    cols = list(rows[0]) if rows else []
    # Spalten ohne einen einzigen Wert weglassen
    cols = [c for c in cols if any(r.get(c) is not None for r in rows)]

    def val(v):
        return round(v, 2) if isinstance(v, float) else v
    return {"spalten": cols, "zeilen": [[val(r.get(c)) for c in cols] for r in rows]}


def export(app, days: int = 14) -> dict:
    """Gelernte Werte plus die Daten, aus denen sie gelernt wurden – zum Nachrechnen.

    Ohne GPS-Koordinaten (nur Entfernungen), ohne Zugangsdaten. Tabellen im Spaltenformat;
    Messwerte, Räume und Anwesenheit auf 5 min verdichtet, damit die Datei klein bleibt.
    """
    eng = app.eng
    since = int(time.time()) - days * 86400
    q = eng.store.query
    return {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": __version__,
        "hinweis": "Gelernte Werte von EMS HeatPilot mit Lerndaten der letzten Tage. Ohne GPS-Koordinaten. "
                   "Tabellen: 'spalten' + 'zeilen'; messwerte, raeume_messwerte und anwesenheit (nur Entfernungen) "
                   "auf 5 min verdichtet, stundenwerte, tageswerte, lernkurve, brenner und fremdwaerme "
                   "(Ereignisse je Raum: Zeiten und Kelvin, nicht gelernt) unverdichtet.",
        "tage": days,
        "settings": app.cfg.to_raw(),
        "discovered": eng.found.as_dict(private=False),
        "gelernt": explain_models(eng),
        "modelle_roh": {
            "house": eng.house.to_dict(),
            "rooms": {c: m.to_dict() for c, m in eng.rooms.items()},
            "dhw": eng.dhw.to_dict(),
        },
        "lernkurve": _table(q("SELECT ts, kind, key, data FROM snapshots WHERE kind = 'house' ORDER BY ts"), None),
        "stundenwerte": _table(q("SELECT * FROM hourly ORDER BY ts"), None),
        "tageswerte": _table(q("SELECT * FROM daily ORDER BY day"), None),
        "messwerte": _table(q("SELECT * FROM samples WHERE ts >= ? ORDER BY ts", (since,))),
        "raeume_messwerte": _table(q("SELECT * FROM room_samples WHERE ts >= ? ORDER BY ts", (since,)), key="room"),
        "anwesenheit": _table(q("SELECT ts, person, home, dist_km FROM person_samples WHERE ts >= ? ORDER BY ts",
                                (since,)), key="person"),
        "brenner": _table(q("SELECT * FROM burner_events WHERE ts >= ? ORDER BY ts", (since,)), None),
        # seit 0.33: Fremdwärme-Ereignisse je Raum (Kamin, Ofen) – alle, sie sind klein
        "fremdwaerme": _table(q("SELECT * FROM room_events ORDER BY start_ts"), None),
        # seit 0.25: alles, was die Oberfläche aus den Modellen ableitet – zum Gegenprüfen
        "selbsttest": _safe(lambda: self_check(app)),
        "ems_werte": getattr(eng, "ems", None),
        "zeiten": _safe(lambda: {"gelernt": eng.schedule_learned, "verwendet": eng.eff.as_dict()}),
        "warmwasser": _safe(lambda: {k: v for k, v in eng.dhw_summary().items() if k not in ("points", "curve_kh")}),
        "vorlauf_studie": _safe(eng.flow_study),
        "woche": _safe(eng.week),
        "personen": _safe(lambda: {p: {"heimfahrt_kmh": round(l.approach_speed_kmh, 1), "heimfahrten": l.arrivals_learned,
                                       "gewohnheit_7x24": l.habit_hours(), "lange_weg": l.long_away,
                                       "heimkehr_kontrolle": l.arrivals,
                                       "vorhersage_guete": l.arrival_quality()}
                                   for p, l in eng.persons.items()}),
    }


def _safe(fn):
    """Ein Teil des Exports darf scheitern, ohne dass die ganze Datei fehlt."""
    try:
        return fn()
    except Exception as err:  # noqa: BLE001
        return {"fehler": f"{type(err).__name__}: {err}"}


def export_estimate(app, days: int) -> int:
    """Ungefähre Größe des Exports in Bytes (aus der Zahl der Zeilen, ohne ihn zu erzeugen)."""
    eng = app.eng
    since = int(time.time()) - days * 86400
    n = lambda sql, *p: eng.store.query(sql, p)[0]["n"]  # noqa: E731
    samples = n("SELECT COUNT(DISTINCT ts - ts % 300) AS n FROM samples WHERE ts >= ?", since)
    rooms = n("SELECT COUNT(DISTINCT (ts - ts % 300) || room) AS n FROM room_samples WHERE ts >= ?", since)
    persons = n("SELECT COUNT(DISTINCT (ts - ts % 300) || person) AS n FROM person_samples WHERE ts >= ?", since)
    hourly = n("SELECT COUNT(*) AS n FROM hourly")
    return 60_000 + samples * 125 + rooms * 55 + persons * 40 + hourly * 50


def sensor_table(eng) -> list[dict]:
    """Alle Sensoren auf einen Blick: wofür, woher, Name in HA, aktueller Wert, Stand, Zustand."""
    rows: list[dict] = []
    f = eng.found
    now = time.time()

    def ha(eid: str | None) -> dict:
        st = eng.states.get(eid or "")
        if not st:
            return {}
        a = st.get("attributes", {})
        age = _age_hours(st)
        return {"name": a.get("friendly_name"), "unit": a.get("unit_of_measurement"),
                "value": st.get("state"), "age_min": None if age is None else round(age * 60)}

    def add(group, label, source, info, status=None):
        value = info.get("value")
        if status is None:
            status = "fehlt" if value in (None, "unknown", "unavailable") else "ok"
        rows.append({"group": group, "label": label, "source": source, "name": info.get("name"),
                     "value": value, "unit": info.get("unit"), "age_min": info.get("age_min"), "status": status})

    used_ems = set()
    for group, roles, device, found_map in (("Kessel", BOILER_ROLES, "boiler", f.boiler),
                                            ("Regler", THERMOSTAT_ROLES, "thermostat", f.thermostat)):
        ems = eng.ems.get(device) or {}
        for role, keys in roles.items():
            key = next((k for k in keys if k in ems), None)
            if key is not None:
                used_ems.add((device, key))
                add(group, ROLE_LABELS.get(role, role), f"EMS-ESP {device}/{key}", {"value": ems[key]})
            elif found_map.get(role):
                info = ha(found_map[role])
                # EMS-ESP direkt verbunden, aber der Wert kommt dort gar nicht vor: der Kessel kennt ihn nicht
                no_value = ems and info.get("value") in (None, "unknown", "unavailable")
                add(group, ROLE_LABELS.get(role, role), found_map[role], info, "Kessel liefert nicht" if no_value else None)
            else:
                add(group, ROLE_LABELS.get(role, role), "–", {}, "Kessel liefert nicht" if ems else "nicht gefunden")
    for e in f.outdoor:
        add("Außen", "Außentemperatur", e, ha(e))
    if f.weather:
        add("Außen", "Wetter (Vorhersage)", f.weather, ha(f.weather))
    add("Gas", "Gaszähler", f.gas or "–", ha(f.gas) if f.gas else {}, None if f.gas else "nicht gewählt")
    if f.demand_sensor:
        add("Gas", "Heizungsanforderung", f.demand_sensor, ha(f.demand_sensor))
    floor = set(eng.cfg.floor_heating_rooms)
    for r in f.rooms:
        g = f"Raum {r.name}" + (" (Fußboden)" if r.climate in floor else "")
        info = ha(r.climate)
        st = eng.states.get(r.climate, {})
        temp = st.get("attributes", {}).get("current_temperature")
        target = st.get("attributes", {}).get("temperature") if st.get("state") != "off" else "aus"
        add(g, "Thermostat (Ist / Soll)", r.climate, {**info, "value": f"{temp} / {target}", "unit": "°C"},
            "fehlt" if temp is None else "ok")
        for label, eid in (("Ventil / Heizleistung", r.heating_power), ("Temperatursensor", r.temp_sensor),
                           ("Luftfeuchte", r.humidity)):
            if eid:
                add(g, label, eid, ha(eid))
            else:
                add(g, label, "–", {}, "nicht gewählt")
    for p in f.persons:
        info = ha(p)
        add("Personen", "Anwesenheit", p, {k: v for k, v in info.items() if k != "unit"})
    for device, values in eng.ems.items():
        for key, value in sorted(values.items()):
            if (device, key) not in used_ems:
                add(f"EMS-ESP {device} (nicht verwendet)", key, f"EMS-ESP {device}/{key}", {"value": value},
                    "nicht verwendet")
    for r in rows:
        if r["status"] == "ok" and r["age_min"] is not None and r["age_min"] > STALE_HOURS * 60 and r["group"] != "Gas":
            r["status"] = "veraltet"
    return rows
