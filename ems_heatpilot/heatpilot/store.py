"""Kompakte lokale Datenbank (SQLite in /data).

Seit 0.42.1 ersetzt nichts mehr vorhandene Stundenwerte durch „unbekannt“: ``add_hourly`` führt zusammen (eine Spalte
ohne neuen Wert behält den gespeicherten; nur ausdrücklich genannte Spalten werden auch geleert – der Gaswert einer
Stunde, deren Zählerstände bekannt sind; ``keep``: eine gespeicherte Innentemperatur bleibt auch gegen einen neuen
Wert), ``fill_hourly`` ergänzt nur fehlende Innen- und Außentemperaturen. Woher die Innentemperatur einer Stunde kommt,
steht in ``t_in_src`` („rooms“: Raumwerte aller Räume, „rooms~“: unvollständige, je Raum um seinen Versatz berichtigt,
„rooms-“: unvollständige ohne Bezug zum Berichtigen, „stat:…“: Langzeitstatistik dieser Sensoren) – ``revert_t_in`` nimmt zurück, was ein falscher alter Sensor gefüllt hat.
``reset_models`` verwirft für „aus gespeicherten Daten neu lernen“ nur, was sich aus den gespeicherten Daten neu
lernen lässt – Messwerte, Warmwasser- und Personenmodelle, die Tageszähler aus der Abtastung und die Modelle von
Schlüsseln ohne aktuellen Raum bleiben. Die Namen der Räume merkt sich ``save_room_names`` (kv ``rooms.names``), bevor
Modelle verworfen werden.

Seit 0.44 ist die eigene Datenbank die Wahrheit: Lesen aus Home Assistant (Lücken füllen, erster Start) löscht und
überschreibt nichts Eigenes – ``add_sample(fill=True)``, ``add_room_samples(fill=True)`` und
``add_person_samples(merge=True)`` ergänzen nur fehlende Zeitpunkte und Spalten, ein gespeicherter Wert bleibt.
"""

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
CREATE TABLE IF NOT EXISTS preview_log(
    ts INTEGER PRIMARY KEY, flow REAL, heating INTEGER, dhw REAL, t_out REAL
);
CREATE TABLE IF NOT EXISTS valve_dry_log(
    ts INTEGER, room TEXT, rule TEXT, action TEXT, setpoint REAL, base REAL, temp REAL, valve REAL,
    eta_min REAL, valve_pred REAL, est_k REAL, est_starts REAL, reliable INTEGER, managed INTEGER, reason TEXT,
    starts_h REAL, act_over_k REAL, act_starts REAL, checked INTEGER, pred_over_k REAL
);
CREATE TABLE IF NOT EXISTS valve_subst(ts INTEGER, room TEXT, PRIMARY KEY(ts, room));
CREATE INDEX IF NOT EXISTS idx_room_ts ON room_samples(ts);
CREATE INDEX IF NOT EXISTS idx_valve_dry_ts ON valve_dry_log(ts);
CREATE INDEX IF NOT EXISTS idx_control_ts ON control_log(ts);
CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(kind, ts);
"""

CONTROL_LOG_KEEP_DAYS = 90  # Schreibprotokoll (0.34) so lange aufheben
PREVIEW_LOG_KEEP_DAYS = 90  # Trockenlauf-Protokoll (0.37) so lange aufheben
VALVE_DRY_KEEP_DAYS = 90    # Ventil-Trockenlauf (0.39) so lange aufheben
POWER_SINCE_KEY = "power.since"  # 0.44: ab hier gehört die Hausleistung zu den heutigen Leistungssensoren
# Ventil-Trockenlauf (0.39) je Eintrag: Regel (drosseln/gemeinsam), Aktion (senken/anheben/zurück), was HeatPilot
# geschrieben hätte und warum, die Schätzung (nur mit belastbaren Modellen; pred_over_k: vorhergesagtes Überschwingen
# ohne Eingriff, zum Nachprüfen) und später, was wirklich geschah. Seit 0.40 ``written`` = 1: wirklich geschrieben
# (Ventile führen, ``valve_control``) – dann ist pred_over_k das vorhergesagte Überschwingen mit dem Eingriff
VALVE_DRY_COLS = ("ts", "room", "rule", "action", "setpoint", "base", "temp", "valve", "eta_min", "valve_pred",
                  "est_k", "est_starts", "reliable", "managed", "reason", "starts_h", "act_over_k", "act_starts",
                  "checked", "pred_over_k", "written")
# Trockenlauf (0.37) je Stunde: Plan (flow, heating, dhw, t_out aus SCHEMA) und dazu Schreibvorgänge des Plans,
# nach der Stunde der Ist-Wert (Mittel der Messpunkte, Anteil Heizbetrieb an), dessen Wechsel und die Zahl der Messpunkte
PREVIEW_COLS = ("w_flow", "w_heating", "w_dhw", "act_flow", "act_heating", "act_dhw",
                "ch_flow", "ch_heating", "ch_dhw", "act_n")

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
    # seit 0.36: Hausleistung in W (Summe der Leistungssensoren; NULL, sobald einer fehlt) und davon
    # die Verbraucher, die nicht zählen (Summe der vorhandenen Ausschluss-Sensoren)
    "power_w", "power_ex_w",
)
# gas_spike_kwh: Zählersprung/Nachtrag – zählt im Verbrauch, wird nicht gelernt (seit 0.14)
HOURLY_COLS = ("ts", "t_out", "t_in", "gas_kwh", "dhw_frac", "home_frac", "vacation_frac", "gas_spike_kwh",
               # seit 0.26: Anteil des Gases fürs Warmwasser (nach Brennerleistung, nicht nach Zeit)
               "dhw_energy_frac",
               # seit 0.33: Anteil der Stunde, in dem ein Raum Fremdwärme hatte (Kamin, Ofen) – nicht gelernt
               "ext_frac",
               # seit 0.42.1: Herkunft der Innentemperatur (Text, siehe oben)
               "t_in_src")
TEXT_COLS = {"t_in_src"}
ROOM_NAMES_KEY = "rooms.names"   # {Schlüssel: Raumname} – Namen alter Schlüssel, auch wenn ihr Modell verworfen ist


# Herkunft „stat:a,b,c“ enthält genau diese Statistik
_SRC_HAS = "t_in_src LIKE 'stat:%' AND instr(',' || substr(t_in_src, 6) || ',', ',' || ? || ',') > 0"


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
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
                self.db.execute(f"ALTER TABLE hourly ADD COLUMN {col} {'TEXT' if col in TEXT_COLS else 'REAL'}")
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(preview_log)")}
        for col in PREVIEW_COLS:
            if col not in have:
                self.db.execute(f"ALTER TABLE preview_log ADD COLUMN {col} REAL")
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(valve_dry_log)")}
        if "written" not in have:  # 0.40: Ventile führen – alte Einträge sind Trockenlauf (leer = nicht geschrieben)
            self.db.execute("ALTER TABLE valve_dry_log ADD COLUMN written INTEGER")
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

    def save_room_names(self) -> None:
        """Namen aus den Raummodellen in kv ``rooms.names`` merken (vor dem Verwerfen der Modelle) – für eine alte
        Entität mit nichtssagender ID ist das der einzige Name (``room_adopt.name_of``)."""
        names = self.get(ROOM_NAMES_KEY) or {}
        for r in self.db.execute("SELECT key, value FROM kv WHERE key LIKE 'model.room.%'"):
            model = json.loads(r["value"])
            name = model.get("room") if isinstance(model, dict) else None
            if name:
                names[r["key"][len("model.room."):]] = name
        self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (ROOM_NAMES_KEY, json.dumps(names)))

    def reset_learning(self, since_day: str) -> None:
        """Gelerntes verwerfen, damit es aus den Rohdaten neu berechnet werden kann.

        Bleibt: Messwerte, Einstellungen, Empfehlungs-Verlauf, Schreib- und Trockenlauf-Protokolle (auch das der
        Ventile, 0.39 – Messung, nichts Gelerntes). Ventilmodelle (``model.valve.*``) fallen mit weg und lernen danach
        aus den Messwerten nach. Tageszähler ab ``since_day``
        werden beim Neueinlesen der Historie neu gebildet und hier gelöscht (sonst doppelt). Seit 0.42.1 merkt es vorher
        die Raumnamen (``save_room_names``).
        """
        self.save_room_names()
        self.db.execute("DELETE FROM kv WHERE key LIKE 'model.%' OR key IN "
                        "('last_room_step', 'last_hour', 'schedule.learned', 'sleep.nights', 'backfill_done')")
        self.db.execute("DELETE FROM daily WHERE day >= ?", (since_day,))
        self.db.execute("DELETE FROM snapshots")
        self.db.execute("DELETE FROM room_events")  # Fremdwärme-Ereignisse werden beim Nachlernen neu erkannt
        self.db.commit()

    def reset_models(self, since_day: str | None = None, rooms: Iterable[str] | None = None) -> None:
        """„Aus gespeicherten Daten neu lernen“ (seit 0.42.1): Haus-, Raum- und Ventilmodelle, laufende Fremdwärme,
        Lernkurve, Fremdwärme-Ereignisse und gelernte Zeiten verwerfen – die Neuberechnung bildet sie aus den
        gespeicherten Messwerten und Stundenwerten neu. Bleiben: alle Messwerte und Stundenwerte, Warmwasser- und
        Personenmodelle (ein Raumwechsel ändert sie nicht, und ohne die HA-Historie ließen sie sich nicht neu lernen),
        Nächte aus dem Stromverbrauch, Einstellungen, Protokolle und die Tageszähler aus der Abtastung. Die Zähler
        verworfener Gaswerte ab ``since_day`` bildet die Neuberechnung neu (sonst doppelt). ``rooms``: nur die Raum- und
        Ventilmodelle dieser Schlüssel (der aktuellen Räume) – die alter Schlüssel ohne Raum bleiben mit ihrem Namen."""
        self.save_room_names()
        if rooms is None:
            self.db.execute("DELETE FROM kv WHERE key LIKE 'model.room.%' OR key LIKE 'model.valve.%'")
        else:
            self.db.executemany("DELETE FROM kv WHERE key = ?",
                                [(f"model.{kind}.{k}",) for k in rooms for kind in ("room", "valve")])
        self.db.execute("DELETE FROM kv WHERE key IN "
                        "('model.house', 'model.room_ext', 'last_room_step', 'last_hour', 'schedule.learned')")
        if since_day is not None:
            self.db.execute("DELETE FROM daily WHERE day >= ? AND key IN ('ignored_gas_negative', "
                            "'ignored_gas_spike', 'ignored_gas_missed', 'gas_spike_kwh')", (since_day,))
        self.db.execute("DELETE FROM snapshots")
        self.db.execute("DELETE FROM room_events")
        self.db.commit()

    def clear_power(self, since: int | None = None) -> None:
        """Andere Leistungssensoren gewählt (0.36): Die Hausleistung gehört zu den alten Sensoren. Seit 0.44 nur ab
        ``since`` verwerfen (das Fenster, das „Lücken füllen“ aus HA neu holt); ältere Werte bleiben gespeichert und
        zählen ab kv ``power.since`` nicht mehr als Nächte (``sleep.learn``). Ohne ``since``: alle verwerfen."""
        if since is None:
            self.db.execute("UPDATE samples SET power_w = NULL, power_ex_w = NULL")
        else:
            self.db.execute("UPDATE samples SET power_w = NULL, power_ex_w = NULL WHERE ts >= ?", (int(since),))
            self.put(POWER_SINCE_KEY, int(since))
        self.db.commit()

    # --- Messwerte ---
    def add_sample(self, ts: int, values: dict, fill: bool = False) -> None:
        """Messpunkt speichern. ``fill`` (seit 0.44, Lesen aus Home Assistant): ein gespeicherter Messpunkt bleibt –
        nur seine leeren Spalten bekommen den neuen Wert."""
        cols = ", ".join(("ts",) + SAMPLE_COLS)
        marks = ", ".join("?" * (len(SAMPLE_COLS) + 1))
        if fill:
            sets = ", ".join(f"{c} = COALESCE(samples.{c}, excluded.{c})" for c in SAMPLE_COLS)
            sql = f"INSERT INTO samples({cols}) VALUES({marks}) ON CONFLICT(ts) DO UPDATE SET {sets}"
        else:
            sql = f"INSERT OR REPLACE INTO samples({cols}) VALUES({marks})"
        self.db.execute(sql, (ts, *(values.get(c) for c in SAMPLE_COLS)))

    def add_room_samples(self, rows: Iterable[tuple], replace: bool = True, fill: bool = False) -> None:
        """Raumwerte (ts, Raum, Temperatur, Soll, Ventil, Feuchte). ``replace=False`` (seit 0.42.1, Nachholen der
        Historie eines neuen Raums): ein schon gespeicherter Messpunkt bleibt, wie er ist. ``fill`` (seit 0.44, Lesen
        aus Home Assistant): ein gespeicherter Messpunkt bleibt, nur seine leeren Spalten werden ergänzt."""
        if fill:
            sets = ", ".join(f"{c} = COALESCE(room_samples.{c}, excluded.{c})"
                             for c in ("temp", "target", "valve", "humidity"))
            self.db.executemany("INSERT INTO room_samples VALUES(?, ?, ?, ?, ?, ?) "
                                f"ON CONFLICT(ts, room) DO UPDATE SET {sets}", rows)
            return
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        self.db.executemany(f"{verb} INTO room_samples VALUES(?, ?, ?, ?, ?, ?)", rows)

    def add_valve_subst(self, ts: int, rooms: Iterable[str]) -> None:
        """Messpunkt, in dem der Ventilwert dieser Räume ein Ersatzwert ist (Sensor ohne Wert, Ventil aus
        ``hvac_action``) – das Ventilmodell lernt ihn nicht (seit 0.39)."""
        rooms = list(rooms)
        if rooms:
            # seit 0.44 ohne eigenes Löschen nach 90 Tagen – die Aufbewahrung (``keep_data``, keep_days) räumt auf
            self.db.executemany("INSERT OR REPLACE INTO valve_subst VALUES(?, ?)", ((int(ts), r) for r in rooms))

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

    def add_person_samples(self, rows: Iterable[tuple], merge: bool = False) -> None:
        """(ts, Person, zu Hause, Entfernung). ``merge`` (Lesen aus Home Assistant): ein gespeicherter Wert bleibt –
        seit 0.44 auch gegen einen neuen, nur Lücken werden gefüllt."""
        if not merge:
            self.db.executemany("INSERT OR REPLACE INTO person_samples VALUES(?, ?, ?, ?)", rows)
            return
        self.db.executemany(
            "INSERT INTO person_samples VALUES(?, ?, ?, ?) ON CONFLICT(ts, person) DO UPDATE SET "
            "home = COALESCE(person_samples.home, excluded.home), "
            "dist_km = COALESCE(person_samples.dist_km, excluded.dist_km)", rows)

    def add_hourly(self, rows: Iterable[tuple], replace: Iterable[str] = (), keep: Iterable[str] = ()) -> None:
        """Zeilen in der Reihenfolge von HOURLY_COLS; fehlende Werte am Ende = NULL.

        Seit 0.42.1 zusammenführen statt ersetzen: Gibt es die Stunde schon, behält jede Spalte ohne neuen Wert (NULL)
        den gespeicherten – ein Neuaufbau mit anderen Sensoren löscht keine Innentemperatur mehr. Nur die Spalten in
        ``replace`` übernehmen den neuen Wert auch, wenn er NULL ist (``aggregate_hour``: Gaswert einer Stunde mit
        bekannten Zählerständen, die als Sprung bzw. unplausibel nicht gelernt wird). Die Spalten in ``keep`` behalten
        einen gespeicherten Wert auch gegen einen neuen (Innentemperatur aus der Langzeitstatistik bzw. aus
        unvollständigen Räumen: nur Lücken füllen). ``t_in_src`` folgt der Innentemperatur."""
        forced, kept = set(replace), set(keep)
        cols = ", ".join(HOURLY_COLS)
        marks = ", ".join("?" * len(HOURLY_COLS))

        def assign(c: str) -> str:
            if c == "t_in_src":  # Herkunft gehört zum Wert, der stehen bleibt
                won = "hourly.t_in IS NULL" if "t_in" in kept else "excluded.t_in IS NOT NULL"
                return f"t_in_src = CASE WHEN {won} THEN excluded.t_in_src ELSE hourly.t_in_src END"
            if c in forced:
                return f"{c} = excluded.{c}"
            if c in kept:
                return f"{c} = COALESCE(hourly.{c}, excluded.{c})"
            return f"{c} = COALESCE(excluded.{c}, hourly.{c})"
        sets = ", ".join(assign(c) for c in HOURLY_COLS[1:])
        self.db.executemany(
            f"INSERT INTO hourly({cols}) VALUES({marks}) ON CONFLICT(ts) DO UPDATE SET {sets}",
            (tuple(r) + (None,) * (len(HOURLY_COLS) - len(r)) for r in rows),
        )

    def fill_hourly(self, rows: Iterable[tuple]) -> int:
        """(ts, t_out, t_in[, Herkunft]) in vorhandene Stunden eintragen, nur wo der Wert fehlt (seit 0.42.1: Stunden mit
        eigenen Messwerten bekommen aus der Langzeitstatistik nur die fehlende Innen- bzw. Außentemperatur). Gibt die
        Zahl der Stunden zurück, in denen etwas ergänzt wurde."""
        n = 0
        for ts, t_out, t_in, *src in rows:
            n += self.db.execute(
                "UPDATE hourly SET t_in_src = CASE WHEN t_in IS NULL AND ? IS NOT NULL THEN ? ELSE t_in_src END, "
                "t_out = COALESCE(t_out, ?), t_in = COALESCE(t_in, ?) WHERE ts = ? "
                "AND ((t_out IS NULL AND ? IS NOT NULL) OR (t_in IS NULL AND ? IS NOT NULL))",
                (t_in, src[0] if src else None, t_out, t_in, int(ts), t_out, t_in)).rowcount
        return n

    def t_in_hours(self, stat: str) -> int:
        """Stunden, deren Innentemperatur (auch) aus der Langzeitstatistik von ``stat`` kommt."""
        return self.db.execute(f"SELECT COUNT(*) FROM hourly WHERE {_SRC_HAS}", (stat,)).fetchone()[0]

    def revert_t_in(self, stat: str) -> int:
        """Innentemperatur der Stunden zurücknehmen, die (auch) aus ``stat`` gefüllt wurden – gefüllt wurde nur, wo
        sie fehlte, der vorige Wert ist also „unbekannt“ (ein falscher alter Sensor, seit 0.42.1). Gibt die Zahl der
        Stunden zurück."""
        n = self.db.execute(f"UPDATE hourly SET t_in = NULL, t_in_src = NULL WHERE {_SRC_HAS}", (stat,)).rowcount
        self.db.commit()
        return n

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

    # --- Trockenlauf (0.37) ---
    def add_preview_plan(self, ts: int, plan: dict) -> bool:
        """Plan der Vorschau für die Stunde ab ``ts`` – nur der erste je Stunde zählt (der damals gerechnete).

        ``plan``: flow, heating, dhw, t_out und die Schreibvorgänge w_flow, w_heating, w_dhw. False, wenn es die
        Stunde schon gab. Ältere Stunden als ``PREVIEW_LOG_KEEP_DAYS`` fallen weg.
        """
        cols = ("flow", "heating", "dhw", "t_out", "w_flow", "w_heating", "w_dhw")
        cur = self.db.execute(f"INSERT OR IGNORE INTO preview_log(ts, {', '.join(cols)}) VALUES({', '.join('?' * 8)})",
                              (int(ts), *(plan.get(c) for c in cols)))
        self.db.execute("DELETE FROM preview_log WHERE ts < ?", (int(ts) - PREVIEW_LOG_KEEP_DAYS * 86400,))
        self.db.commit()
        return cur.rowcount > 0

    def preview_open(self, now_ts: int) -> list[int]:
        """Stunden mit Plan, die vorbei sind und noch keinen Ist-Wert haben."""
        return [r["ts"] for r in self.db.execute(
            "SELECT ts FROM preview_log WHERE act_n IS NULL AND ts + 3600 <= ? ORDER BY ts", (int(now_ts),))]

    def set_preview_actual(self, ts: int, values: dict) -> None:
        """Ist-Werte einer abgelaufenen Stunde nachtragen (Spalten ``act_*``, ``ch_*``)."""
        cols = [c for c in PREVIEW_COLS if c.startswith(("act_", "ch_"))]
        self.db.execute(f"UPDATE preview_log SET {', '.join(f'{c} = ?' for c in cols)} WHERE ts = ?",
                        (*(values.get(c) for c in cols), int(ts)))
        self.db.commit()

    def preview_log(self, since: int) -> list[dict]:
        """Protokoll ab ``since``, älteste Stunde zuerst."""
        return [dict(r) for r in self.db.execute("SELECT * FROM preview_log WHERE ts >= ? ORDER BY ts", (int(since),))]

    # --- Ventil-Trockenlauf (0.39) ---
    def add_valve_dry(self, entry: dict) -> None:
        """Ein Eintrag „würde schreiben“ bzw. (0.40, ``written``) „geschrieben“ an ein Thermostat (Spalten
        ``VALVE_DRY_COLS``, fehlende leer)."""
        self.db.execute(f"INSERT INTO valve_dry_log({', '.join(VALVE_DRY_COLS)}) "
                        f"VALUES({', '.join('?' * len(VALVE_DRY_COLS))})", tuple(entry.get(c) for c in VALVE_DRY_COLS))
        self.db.execute("DELETE FROM valve_dry_log WHERE ts < ?", (int(entry["ts"]) - VALVE_DRY_KEEP_DAYS * 86400,))
        self.db.commit()

    def valve_dry_log(self, since: int) -> list[dict]:
        """Einträge ab ``since``, älteste zuerst (mit ``id``)."""
        return [dict(r) for r in self.db.execute(
            "SELECT rowid AS id, * FROM valve_dry_log WHERE ts >= ? ORDER BY ts, rowid", (int(since),))]

    def valve_dry_open(self, before: int) -> list[dict]:
        """Einträge vor ``before``, bei denen noch nachgetragen werden muss, was wirklich geschah."""
        return [dict(r) for r in self.db.execute(
            "SELECT rowid AS id, * FROM valve_dry_log WHERE ts <= ? AND action != 'zurück' AND checked IS NULL "
            "ORDER BY ts", (int(before),))]

    def set_valve_dry_actual(self, entry_id: int, over_k: float | None, starts: float | None) -> None:
        self.db.execute("UPDATE valve_dry_log SET act_over_k = ?, act_starts = ?, checked = 1 WHERE rowid = ?",
                        (over_k, starts, int(entry_id)))

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
