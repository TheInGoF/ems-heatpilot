"""Kompakte lokale Datenbank (SQLite in /data)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples(
    ts INTEGER PRIMARY KEY,
    t_out REAL, flow REAL, sel_flow REAL, ret REAL,
    burner_pow REAL, burner_on INTEGER, heating INTEGER, dhw INTEGER,
    gas REAL, min_period REAL, home_count INTEGER
);
CREATE TABLE IF NOT EXISTS room_samples(
    ts INTEGER, room TEXT, temp REAL, target REAL, valve REAL, humidity REAL,
    PRIMARY KEY(ts, room)
);
CREATE TABLE IF NOT EXISTS person_samples(
    ts INTEGER, person TEXT, home INTEGER, dist_km REAL,
    PRIMARY KEY(ts, person)
);
CREATE TABLE IF NOT EXISTS hourly(
    ts INTEGER PRIMARY KEY,
    t_out REAL, t_in REAL, gas_kwh REAL, dhw_frac REAL, home_frac REAL
);

CREATE TABLE IF NOT EXISTS burner_events(ts INTEGER, state INTEGER);
CREATE TABLE IF NOT EXISTS snapshots(ts INTEGER, kind TEXT, key TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS daily(day TEXT, key TEXT, value REAL, PRIMARY KEY(day, key));
CREATE TABLE IF NOT EXISTS room_events(
    start_ts INTEGER, end_ts INTEGER, room TEXT, rise_k REAL,
    PRIMARY KEY(start_ts, room)
);
CREATE TABLE IF NOT EXISTS control_log(
    ts INTEGER, room TEXT, action TEXT, value REAL, reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_room_ts ON room_samples(ts);
CREATE INDEX IF NOT EXISTS idx_control_ts ON control_log(ts);
CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(kind, ts);
"""

CONTROL_LOG_KEEP_DAYS = 90  # Schreibprotokoll (0.34) so lange aufheben

SAMPLE_COLS = (
    "t_out", "flow", "sel_flow", "ret", "burner_pow", "burner_on",
    "heating", "dhw", "gas", "min_period", "home_count",
    # seit 0.2
    "boiler_temp", "calc_flow", "heating_off", "burn_starts", "heat_starts", "heat_min",
    # seit 0.3
    "hc_day", "hc_seltemp", "remote_temp", "pump_mod", "dhw_temp", "flame",
    "demand_pct",
    # seit 0.4
    "dhw_set",
    # seit 0.6
    "vacation",
    # seit 0.20: Sonne auf Ost-, Süd-, Westfassade (0…1)
    "sun_e", "sun_s", "sun_w",
    # seit 0.25: Warmwasser-Ladezähler, Sonderladung, Anlagendruck
    "dhw_starts", "dhw_special", "syspress",
    # seit 0.32: alle Personen lange weg (Reise ohne Urlaubseintrag) – kein Urlaub, nur nicht lernen
    "away_long",
)
# gas_spike_kwh: Zählersprung/Nachtrag – zählt im Verbrauch, wird nicht gelernt (seit 0.14)
HOURLY_COLS = ("ts", "t_out", "t_in", "gas_kwh", "dhw_frac", "home_frac", "vacation_frac", "gas_spike_kwh",
               # seit 0.26: Anteil des Gases fürs Warmwasser (nach Brennerleistung, nicht nach Zeit)
               "dhw_energy_frac",
               # seit 0.33: Anteil der Stunde, in dem ein Raum Fremdwärme hatte (Kamin, Ofen) – nicht gelernt
               "ext_frac")


class Store:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(samples)")}
        for col in SAMPLE_COLS:
            if col not in have:
                self.db.execute(f"ALTER TABLE samples ADD COLUMN {col} REAL")
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(hourly)")}
        for col in HOURLY_COLS:
            if col not in have:
                self.db.execute(f"ALTER TABLE hourly ADD COLUMN {col} REAL")
        self.db.commit()

    # --- Schlüssel/Wert ---
    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value))
        )
        self.db.commit()

    def delete_range(self, start: int, end: int) -> None:
        """Messwerte eines Zeitraums löschen (wird danach aus der HA-Historie neu geschrieben)."""
        for table in ("samples", "room_samples", "person_samples", "burner_events"):
            self.db.execute(f"DELETE FROM {table} WHERE ts >= ? AND ts <= ?", (start, end))
        self.db.commit()

    def reset_learning(self, since_day: str) -> None:
        """Gelerntes verwerfen, damit es aus den Rohdaten neu berechnet werden kann.

        Bleibt: Messwerte, Einstellungen, Empfehlungs-Verlauf. Tageszähler ab ``since_day``
        werden beim Neueinlesen der Historie neu gebildet und hier gelöscht (sonst doppelt).
        """
        self.db.execute("DELETE FROM kv WHERE key LIKE 'model.%' OR key IN "
                        "('last_room_step', 'last_hour', 'schedule.learned', 'backfill_done')")
        self.db.execute("DELETE FROM daily WHERE day >= ?", (since_day,))
        self.db.execute("DELETE FROM snapshots")
        self.db.execute("DELETE FROM room_events")  # Fremdwärme-Ereignisse werden beim Nachlernen neu erkannt
        self.db.commit()

    # --- Messwerte ---
    def add_sample(self, ts: int, values: dict) -> None:
        cols = ", ".join(("ts",) + SAMPLE_COLS)
        marks = ", ".join("?" * (len(SAMPLE_COLS) + 1))
        self.db.execute(
            f"INSERT OR REPLACE INTO samples({cols}) VALUES({marks})",
            (ts, *(values.get(c) for c in SAMPLE_COLS)),
        )

    def add_room_samples(self, rows: Iterable[tuple]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO room_samples VALUES(?, ?, ?, ?, ?, ?)", rows
        )

    def home_by_ts(self, since: int, persons: list[str] | None) -> dict[int, bool]:
        """Ist jemand zu Hause? Nur die gewählten Personen (None = alle gespeicherten)."""
        sql, params = "SELECT ts, MAX(home) AS h FROM person_samples WHERE ts >= ?", [since]
        if persons is not None:
            if not persons:
                return {}
            sql += f" AND person IN ({','.join('?' * len(persons))})"
            params += list(persons)
        return {r["ts"]: bool(r["h"]) for r in self.db.execute(sql + " GROUP BY ts", params)}

    def recount_home(self, persons: list[str]) -> None:
        """Nach geänderter Personenauswahl: Anwesenheit in Messwerten und Stundenwerten neu zählen.

        Gleiche Regel wie ``Engine.snapshot``: Zahl der bekannten Anwesenden; ist niemand bekannt
        zu Hause und eine Person unbekannt (home NULL), bleibt home_count NULL statt 0."""
        marks = ",".join("?" * len(persons)) or "NULL"
        self.db.execute(
            f"UPDATE samples SET home_count = (SELECT CASE WHEN COALESCE(SUM(home), 0) > 0 THEN SUM(home) "
            f"WHEN SUM(home IS NULL) > 0 THEN NULL ELSE 0 END FROM person_samples p "
            f"WHERE p.ts = samples.ts AND p.person IN ({marks})) "
            "WHERE ts IN (SELECT DISTINCT ts FROM person_samples)", list(persons))
        self.db.execute(
            "UPDATE hourly SET home_frac = (SELECT AVG(CASE WHEN home_count > 0 THEN 1.0 ELSE 0.0 END) "
            "FROM samples s WHERE s.ts >= hourly.ts AND s.ts < hourly.ts + 3600 AND s.home_count IS NOT NULL) "
            "WHERE EXISTS (SELECT 1 FROM samples s WHERE s.ts >= hourly.ts AND s.ts < hourly.ts + 3600 "
            "AND s.home_count IS NOT NULL)")
        self.db.commit()

    def add_person_samples(self, rows: Iterable[tuple]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO person_samples VALUES(?, ?, ?, ?)", rows
        )

    def add_hourly(self, rows: Iterable[tuple]) -> None:
        """Zeilen in der Reihenfolge von HOURLY_COLS; fehlende Werte am Ende = NULL."""
        cols = ", ".join(HOURLY_COLS)
        marks = ", ".join("?" * len(HOURLY_COLS))
        self.db.executemany(
            f"INSERT OR REPLACE INTO hourly({cols}) VALUES({marks})",
            (tuple(r) + (None,) * (len(HOURLY_COLS) - len(r)) for r in rows),
        )

    def add_room_event(self, start_ts: int, end_ts: int, room: str, rise_k: float) -> None:
        """Abgeschlossenes Fremdwärme-Ereignis eines Raums (nur Zeiten und Kelvin)."""
        self.db.execute("INSERT OR REPLACE INTO room_events VALUES(?, ?, ?, ?)",
                        (int(start_ts), int(end_ts), room, round(rise_k, 2)))

    def add_control_log(self, ts: int, room: str, action: str, value: float | None, reason: str) -> None:
        """Ein Schreibvorgang an ein Thermostat (seit 0.34) – nur Raum, Dienst, Wert, Grund."""
        self.db.execute("INSERT INTO control_log VALUES(?, ?, ?, ?, ?)",
                        (int(ts), room, action, None if value is None else float(value), reason))
        self.db.execute("DELETE FROM control_log WHERE ts < ?", (int(ts) - CONTROL_LOG_KEEP_DAYS * 86400,))
        self.db.commit()

    def control_log(self, limit: int = 50, since: int | None = None) -> list[dict]:
        """Die letzten Schreibvorgänge, neueste zuerst."""
        sql, params = "SELECT ts, room, action, value, reason FROM control_log", []
        if since is not None:
            sql, params = sql + " WHERE ts >= ?", [since]
        rows = self.db.execute(sql + " ORDER BY ts DESC, rowid DESC LIMIT ?", (*params, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def add_burner_event(self, ts: int, state: bool) -> None:
        self.db.execute("INSERT INTO burner_events VALUES(?, ?)", (ts, int(state)))

    def add_snapshot(self, ts: int, kind: str, key: str, data: dict) -> None:
        self.db.execute(
            "INSERT INTO snapshots VALUES(?, ?, ?, ?)", (ts, kind, key, json.dumps(data))
        )

    def add_daily(self, day: str, key: str, delta: float) -> None:
        self.db.execute(
            "INSERT INTO daily VALUES(?, ?, ?) "
            "ON CONFLICT(day, key) DO UPDATE SET value = value + excluded.value",
            (day, key, delta),
        )

    def daily(self, since_day: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for r in self.db.execute("SELECT * FROM daily WHERE day >= ? ORDER BY day", (since_day,)):
            out.setdefault(r["day"], {})[r["key"]] = r["value"]
        return out

    def commit(self) -> None:
        self.db.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, params).fetchall()
