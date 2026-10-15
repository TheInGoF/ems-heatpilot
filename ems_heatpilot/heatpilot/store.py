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
CREATE INDEX IF NOT EXISTS idx_room_ts ON room_samples(ts);
CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(kind, ts);
"""

SAMPLE_COLS = (
    "t_out", "flow", "sel_flow", "ret", "burner_pow", "burner_on",
    "heating", "dhw", "gas", "min_period", "home_count",
)


class Store:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
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

    def add_person_samples(self, rows: Iterable[tuple]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO person_samples VALUES(?, ?, ?, ?)", rows
        )

    def add_hourly(self, rows: Iterable[tuple]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO hourly VALUES(?, ?, ?, ?, ?, ?)", rows
        )

    def add_burner_event(self, ts: int, state: bool) -> None:
        self.db.execute("INSERT INTO burner_events VALUES(?, ?)", (ts, int(state)))

    def add_snapshot(self, ts: int, kind: str, key: str, data: dict) -> None:
        self.db.execute(
            "INSERT INTO snapshots VALUES(?, ?, ?, ?)", (ts, kind, key, json.dumps(data))
        )

    def commit(self) -> None:
        self.db.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, params).fetchall()
