"""0.44: Aufbewahrung der eigenen Rohdaten (``keep_data``, Einstellung ``keep_days``).

Geprüft: Rohdaten älter als ``keep_days`` fallen weg (Messpunkte, Raum- und Personenwerte, Brennerereignisse,
Ventil-Ersatzwerte, Fremdwärme-Ereignisse, Stundenwerte), Tageszähler und kv bleiben, Protokoll; höchstens einmal am
Tag, auch aus dem Takt der Engine; das Einstellungsfeld (Standard 720, Bereich 365–3650, gespeichert wie die anderen).
"""

import time

from heatpilot import keep_data
from heatpilot.config import FIELDS, Config, validate
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

DAY = 86400
NOW = int(time.time()) // DAY * DAY + 12 * 3600


def stocked(folder) -> Store:
    folder.mkdir(exist_ok=True)
    st = Store(folder / "k.db")
    for ts in (NOW - 800 * DAY, NOW - 10 * DAY):
        st.add_sample(ts, {"flow": 40.0})
        st.add_room_samples([(ts, "climate.a", 20.0, 21.0, 10.0, None)])
        st.add_person_samples([(ts, "person.a", 1, None)])
        st.add_burner_event(ts, True)
        st.db.execute("INSERT INTO valve_subst VALUES(?, ?)", (ts, "climate.a"))  # add_valve_subst kürzt selbst
        st.add_room_event(ts, ts + 3600, "climate.a", 1.0)
        st.add_hourly([(ts - ts % 3600, 5.0, 20.0, 1.0)])
        st.add_daily("2020-01-01" if ts < NOW - DAY * 400 else "2026-01-01", "burner_h", 1.0)
    st.put("model.room.climate.a", {"room": "A"})
    st.commit()
    return st


def counts(st) -> dict[str, int]:
    return {t: st.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
            for t in ("samples", "room_samples", "person_samples", "burner_events", "valve_subst", "room_events",
                      "hourly", "daily")}


def test_prune_deletes_only_old_raw_data_and_logs(tmp_path):
    st = stocked(tmp_path)
    deleted = keep_data.prune(st, 720, NOW)
    assert set(deleted.values()) == {1} and len(deleted) == 7
    assert counts(st) == {"samples": 1, "room_samples": 1, "person_samples": 1, "burner_events": 1, "valve_subst": 1,
                          "room_events": 1, "hourly": 1, "daily": 2}             # Tageszähler bleiben
    assert st.get("model.room.climate.a") == {"room": "A"}
    (entry,) = st.get(keep_data.LOG_KEY)
    assert entry["days"] == 720 and entry["before"] == NOW - 10 * DAY - 720 * DAY and entry["deleted"]["samples"] == 1
    assert keep_data.prune(st, 720, NOW) == dict.fromkeys(deleted, 0) and len(st.get(keep_data.LOG_KEY)) == 1
    assert set(keep_data.prune(stocked(tmp_path / "x"), 3650, NOW).values()) == {0}  # 3650 Tage: nichts zu löschen


def test_daily_runs_once_per_day_and_from_engine_tick(tmp_path):
    st = stocked(tmp_path)
    assert keep_data.daily(st, 720, NOW)["samples"] == 1
    assert keep_data.daily(st, 720, NOW + 3600) is None
    st.add_sample(NOW - 900 * DAY, {"flow": 1.0})
    st.commit()
    assert keep_data.daily(Store(tmp_path / "k.db"), 720, NOW) is None    # gemerkt in kv, auch nach Neustart
    assert keep_data.daily(st, 720, NOW + DAY)["samples"] == 1
    st2 = stocked(tmp_path / "e")
    eng = Engine(Config(keep_days=365), st2, Discovered())
    eng.tick(NOW)
    assert st2.query("SELECT COUNT(*) AS n FROM samples WHERE ts < ?", (NOW - 365 * DAY,))[0]["n"] == 0
    assert st2.get(keep_data.LAST_KEY)


def test_keep_days_setting_range_default_and_round_trip():
    assert Config().keep_days == 720 and FIELDS["keep_days"] == ("int", 365, 3650)
    for value, ok in ((364, False), (365, True), (720, True), (3650, True), (3651, False), ("x", False)):
        clean, errors = validate({"keep_days": value})
        assert (not errors) == ok and (clean.get("keep_days") == value if ok else "keep_days" not in clean)
    assert Config.from_raw(Config(keep_days=1000).to_raw()).keep_days == 1000
    assert Config.from_raw({}).keep_days == 720                             # alte Einstellungen ohne Feld
    assert [keep_data.days_of(v) for v in (None, "x", 10, 800, 99999)] == [720, 720, 365, 800, 3650]
