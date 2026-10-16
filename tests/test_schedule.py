"""Nachtzeiten und Temperaturen aus Tado lernen – mit Leitplanken."""

from datetime import datetime, time

from heatpilot import schedule
from heatpilot.config import Config
from heatpilot.store import Store

START = datetime(2026, 9, 20, 0, 0)
NOW = int(datetime(2026, 10, 16, 0, 0).timestamp())


def fill(tmp_path, night=(22 * 60 + 40, 6 * 60 + 10), night_sp=19.0, away_sp=18.5, days=26, vacation=False):
    store = Store(tmp_path / "s.db")
    t0 = int(START.timestamp())
    for i in range(days * 288):
        ts = t0 + i * 300
        d = datetime.fromtimestamp(ts)
        m = d.hour * 60 + d.minute
        is_night = m >= night[0] or m < night[1]
        away = d.weekday() < 5 and 9 * 60 <= m < 16 * 60
        sp = night_sp if is_night else (away_sp if away else 21.0)
        store.add_room_samples([(ts, f"climate.r{k}", 20.0, sp, 0.0, None) for k in range(3)])
        store.add_person_samples([(ts, "person.a", int(not away), 0.0)])
        store.add_sample(ts, {"vacation": int(vacation)})
    store.commit()
    return store


def test_learns_night_window_and_temperatures(tmp_path):
    learned = schedule.learn(fill(tmp_path), NOW)
    assert learned["night_start"] == "22:40" and learned["night_end"] == "06:10"
    assert learned["nights"] >= 20 and learned["night_temp"] == 19.0 and learned["away_temp"] == 18.5
    eff = schedule.effective(Config(), learned)  # Startwerte 22:30–06:00, 18 °C, 19 °C
    assert eff.night_start == time(22, 40) and eff.night_end == time(6, 10)
    assert eff.night_temp == 19.0 and eff.source["night_temp"] == "gelernt"
    # Abwesenheit: Tado senkt auf 18,5 – HeatPilot bleibt bei der eingestellten Untergrenze 19
    assert eff.away_temp == 19.0 and eff.source["away_temp"] == "eingestellt (Untergrenze)"
    assert eff.is_night(time(23, 0)) and not eff.is_night(time(22, 35))


def test_settings_stay_when_learning_off_or_too_far(tmp_path):
    learned = schedule.learn(fill(tmp_path), NOW)
    off = schedule.effective(Config(learn_schedule=False), learned)
    assert off.night_start == time(22, 30) and set(off.source.values()) == {"eingestellt"}
    far = schedule.effective(Config(night_start=time(18, 0)), learned)  # 4:40 h daneben: nicht übernehmen
    assert far.night_start == time(18, 0) and far.night_end == time(6, 10)


def test_temperatures_capped_and_vacation_ignored(tmp_path):
    learned = {"nights": 20, "night_start": "22:30", "night_end": "06:00", "night_temp": 22.5, "away_temp": 23.0}
    eff = schedule.effective(Config(), learned)
    assert eff.night_temp == 21.0 and eff.away_temp == 22.0  # höchstens 3 K über der Einstellung
    vac = schedule.learn(fill(tmp_path, vacation=True), NOW)
    assert vac["nights"] == 0 and vac["night_start"] is None and vac["away_temp"] is None
