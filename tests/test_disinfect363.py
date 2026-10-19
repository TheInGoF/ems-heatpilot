"""0.36.3: Warmwasser-Desinfektion in den Einstellungen – Termin, Vorschlag, Einplanen im Modell, Bereitschaft.

HeatPilot löst die Desinfektion nicht aus (EMS-ESP bleibt nur lesend): Der Termin wird gespeichert, im
Warmwassermodell eingeplant (Empfehlung und Schatten wie nach einer erkannten Sonderladung) und angezeigt.
Standard: kein Termin.
"""

import asyncio
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import pytest

from heatpilot import dhw_disinfect as dd, readiness
from heatpilot.config import DISINFECT_DAYS, WEEKDAYS, Config, save_settings, validate
from heatpilot.dhw import BIG_KEEP_DAYS, SLOTS, DhwModel, DhwShadow
from heatpilot.discovery import BOILER_ROLES, Discovered
from heatpilot.emsesp import BOILER_CONTROLS
from heatpilot.engine import Engine
from heatpilot.store import Store

from test_dhw_special import MONDAY, STEP, _cfg, _sim
from test_readiness36 import _controls, house, room

SUNDAY_18 = datetime(2026, 1, 4, 18, 0)  # ein Sonntag


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def plan(day="So", at="17:00", **kw) -> Config:
    return Config.from_raw({"dhw_disinfect_day": day, "dhw_disinfect_time": at, **kw})


# ------------------------------------------------------------------ Einstellungen
def test_defaults_off_and_roundtrip(tmp_path):
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.dhw_disinfect_day == "" and cfg.dhw_disinfect_time == dtime(13, 0) and cfg.dhw_disinfect_temp == 70.0
    assert cfg.disinfect_weekday() is None and cfg.next_disinfection(datetime.now().astimezone()) is None
    raw = cfg.to_raw()
    assert (raw["dhw_disinfect_day"], raw["dhw_disinfect_time"], raw["dhw_disinfect_temp"]) == ("", "13:00", 70.0)
    assert validate(raw)[1] == []
    clean, errors = validate({"dhw_disinfect_day": " So ", "dhw_disinfect_time": "7:30", "dhw_disinfect_temp": "72,5"})
    assert errors == [] and clean == {"dhw_disinfect_day": "So", "dhw_disinfect_time": "07:30", "dhw_disinfect_temp": 72.5}
    save_settings(clean, tmp_path)
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.dhw_disinfect_day == "So" and cfg.dhw_disinfect_time == dtime(7, 30) and cfg.disinfect_weekday() == 6
    assert validate(cfg.to_raw())[1] == []  # was die Oberfläche bekommt, kann sie unverändert speichern
    save_settings(validate({"dhw_disinfect_day": ""})[0], tmp_path)  # „aus“: Termin weg, Uhrzeit bleibt
    cfg = Config.load(tmp_path / "options.json")
    assert cfg.dhw_disinfect_day == "" and cfg.dhw_disinfect_time == dtime(7, 30) and cfg.disinfect_weekday() is None
    assert DISINFECT_DAYS == ("", *WEEKDAYS)


def test_validation_errors_and_broken_file():
    for bad, text in (({"dhw_disinfect_day": "Sonntag"}, "erlaubt: (leer), Mo, Di"),
                      ({"dhw_disinfect_temp": 85}, "zwischen 60 und 80"),
                      ({"dhw_disinfect_temp": "heiß"}, "dhw_disinfect_temp"),
                      ({"dhw_disinfect_time": "24:00"}, "Uhrzeit als HH:MM")):
        _, errors = validate(bad)
        assert errors and text in errors[0], (bad, errors)
    cfg = Config.from_raw({"dhw_disinfect_day": "Sunday", "dhw_disinfect_time": "kaputt"})
    assert cfg.dhw_disinfect_day == "" and cfg.dhw_disinfect_time == dtime(13, 0)


# ------------------------------------------------------------------ nächster Termin (Ortszeit, Umstellung)
def test_next_disinfection_over_week_change():
    cfg = plan("So", "13:00")
    mon = datetime(2026, 10, 12, 9, 0).astimezone()
    assert cfg.next_disinfection(mon) == datetime(2026, 10, 18, 13, 0).astimezone()
    sun = datetime(2026, 10, 18, 12, 59).astimezone()
    assert cfg.next_disinfection(sun) == datetime(2026, 10, 18, 13, 0).astimezone()
    assert cfg.next_disinfection(sun + timedelta(minutes=1)) == sun + timedelta(minutes=1)  # genau jetzt zählt
    assert cfg.next_disinfection(sun + timedelta(minutes=2)) == datetime(2026, 10, 25, 13, 0).astimezone()
    assert cfg.disinfections(sun - timedelta(days=8), sun)[-1] == datetime(2026, 10, 11, 13, 0).astimezone()
    # Mo 00:30: Termin am selben Tag liegt danach; ohne Zeitzone (Tests) bleibt alles naiv
    assert plan("Mo", "00:30").next_disinfection(datetime(2026, 10, 12, 0, 10)) == datetime(2026, 10, 12, 0, 30)
    assert plan("Mo", "00:30").next_disinfection(datetime(2026, 10, 12, 0, 40)) == datetime(2026, 10, 19, 0, 30)
    weeks = cfg.disinfections(mon, mon + timedelta(days=21))
    assert [w.date().isoformat() for w in weeks] == ["2026-10-18", "2026-10-25", "2026-11-01"]


def test_next_disinfection_keeps_wall_clock_over_dst(berlin):
    cfg = plan("So", "13:00")
    sat = datetime(2026, 10, 24, 20, 0).astimezone()  # Sa 20:00 Sommerzeit
    nxt = cfg.next_disinfection(sat)
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (6, 13, 0) and nxt.utcoffset() == timedelta(hours=1)
    assert nxt.timestamp() - sat.timestamp() == 18 * 3600  # 17 h Wanduhr + die gewonnene Stunde
    spring = cfg.next_disinfection(datetime(2026, 3, 28, 20, 0).astimezone())
    assert (spring.day, spring.hour) == (29, 13) and spring.utcoffset() == timedelta(hours=2)
    # über drei Wochen mit Umstellung: immer 13:00 Wanduhr
    span = cfg.disinfections(datetime(2026, 10, 15).astimezone(), datetime(2026, 11, 5).astimezone())
    assert [(d.day, d.hour) for d in span] == [(18, 13), (25, 13), (1, 13)]
    assert [d.utcoffset().total_seconds() / 3600 for d in span] == [2, 1, 1]
    # in UTC gefragt (So 11:30 UTC = 12:30 Ortszeit): Wochentag und Uhrzeit nach Ortszeit
    nxt = cfg.next_disinfection(datetime(2026, 10, 25, 11, 30, tzinfo=timezone.utc))
    assert (nxt.day, nxt.hour) == (25, 13) and nxt.utcoffset() == timedelta(hours=1)


# ------------------------------------------------------------------ Vorschlag
def bath_model(weeks=8, skip=(3,), hour=18, size=14.0, charges=0, rate=20.0):
    """Zapfprofil über ``weeks`` Wochen: sonntags ``hour`` Uhr eine Wanne (≈ ``size`` K), außer in ``skip``."""
    m = DhwModel()
    m.profile_n = [weeks] * SLOTS
    m.kwh_per_k = {"kalt": 0.18, "warm": 0.18}
    m.kwh_per_k_n = {"kalt": 3, "warm": 3}
    m.charges, m.charge_rate_kh = charges, rate
    start = SUNDAY_18.replace(hour=hour)
    for w in range(weeks):
        if w not in skip:
            m.big_draws.append([(start + timedelta(days=7 * w)).isoformat(timespec="minutes"), size, False])
    return m, start + timedelta(days=7 * (weeks - 1), hours=2)


def test_suggestion_sunday_bath():
    m, now = bath_model()
    s = dd.suggestion(m, 18.0, 50.0, 70.0, now)
    # 20 K × 0,18 kWh/K ÷ 18 kW = 12 min Ladung, 15 min Puffer vor 18:00 → 17:33, auf 15 min abgerundet
    assert s["ok"] and (s["day"], s["time"], s["weekday"]) == ("So", "17:30", 6)
    assert (s["hits"], s["weeks"], s["draw_hour"], s["charge_from"]) == (7, 8, 18, "Leistung")
    assert s["text"] == ("So 18 Uhr zapfst du am meisten (7 von 8 Sonntagen, ≈ 14 K) – Desinfektion So 17:30, "
                         "dann nutzt du die 70 °C")
    assert 2.0 <= s["kept_kwh"] <= 14.0 * 0.18 and "kWh der Desinfektionswärme" in s["saving_text"]
    # gelernte Ladegeschwindigkeit (ab 5 Ladungen) geht vor: 20 K bei 26,7 K/h = 45 min → So 17:00
    m.charges, m.charge_rate_kh = 6, 80 / 3
    s = dd.suggestion(m, 18.0, 50.0, 70.0, now)
    assert (s["time"], s["charge_from"], s["charge_h"]) == ("17:00", "gelernt", 0.75)
    assert "Desinfektion So 17:00, dann nutzt du die 70 °C" in s["text"]
    # ohne Leistung und ohne gelernte Ladungen: Standard-Ladegeschwindigkeit (20 K/h = 1 h) → 16:45
    m.charges, m.charge_rate_kh = 0, 20.0
    assert dd.suggestion(m, None, 50.0, 70.0, now)["time"] == "16:45"
    assert BIG_KEEP_DAYS >= 7 * dd.SUGGEST_WEEKS  # das Modell hält die großen Zapfungen lange genug


def test_suggestion_needs_regular_draws_and_enough_weeks():
    m, now = bath_model(weeks=3, skip=())
    s = dd.suggestion(m, 18.0, 50.0, 70.0, now)
    assert not s["ok"] and s["reason"] == "Zapfprofil erst 3 Wochen gelernt – ein Vorschlag braucht mindestens 4 Wochen"
    m, now = bath_model(skip=(0, 1, 2, 3, 4))  # nur 3 von 8 Sonntagen: nicht regelmäßig, Profil leer
    s = dd.suggestion(m, 18.0, 50.0, 70.0, now)
    assert not s["ok"] and s["reason"] == "keine regelmäßige Zapfung erkannt – kein Vorschlag"
    m, now = bath_model(skip=(0, 1, 2, 3))  # 4 von 8: die Hälfte reicht
    assert dd.suggestion(m, 18.0, 50.0, 70.0, now)["hits"] == 4
    assert not dd.suggestion(DhwModel(), 18.0, 50.0, 70.0)["ok"]  # frisches Modell: kein Vorschlag


def test_suggestion_only_between_six_and_nine():
    m, now = bath_model(hour=6)  # Wanne So 6 Uhr: Beginn 05:30 – nachts nicht
    s = dd.suggestion(m, 18.0, 50.0, 70.0, now)
    assert not s["ok"] and s["reason"].startswith("So 6 Uhr zapfst du am meisten (7 von 8 Sonntagen")
    assert s["reason"].endswith("die Desinfektion müsste nachts beginnen (Vorschlag nur 06:00–21:00)")
    m, now = bath_model(hour=22)  # Beginn 21:30 – zu spät
    assert not dd.suggestion(m, 18.0, 50.0, 70.0, now)["ok"]
    m, now = bath_model(hour=21)
    assert dd.suggestion(m, 18.0, 50.0, 70.0, now)["time"] == "20:30"
    m, now = bath_model(hour=7, charges=6, rate=80 / 3)  # 7:00 − 45 − 15 min = 06:00: Grenze gehört dazu
    assert dd.suggestion(m, 18.0, 50.0, 70.0, now)["time"] == "06:00"
    # größte Zapfung nachts, eine kleinere regelmäßige am Abend: die zählt
    m, now = bath_model(hour=6, size=30.0)
    for w in range(8):
        m.big_draws.append([(SUNDAY_18 + timedelta(days=7 * w - 3)).isoformat(timespec="minutes"), 9.0, False])
    s = dd.suggestion(m, 18.0, 50.0, 70.0, now)
    assert s["ok"] and (s["day"], s["time"], s["hits"]) == ("Do", "17:30", 8)


def test_suggestion_from_profile_without_big_draws():
    m = DhwModel()
    m.profile_n = [8] * SLOTS
    m.kwh_per_k = {"kalt": 0.18, "warm": None}
    m.kwh_per_k_n = {"kalt": 3, "warm": 0}
    sat_9 = 5 * 24 + 9
    m.profile[sat_9], m.hit[sat_9], m.daily[9], m.hit_daily[9] = 4.0, 0.9, 0.5, 0.3
    s = dd.suggestion(m, 18.0, 50.0, 70.0)
    assert s["ok"] and s["source"] == "Zapfprofil" and (s["day"], s["time"]) == ("Sa", "08:30")
    assert s["text"].startswith("Sa 9 Uhr zapfst du am meisten (in 72 % der Wochen, ≈ 3 K)")


# ------------------------------------------------------------------ Modell: Termin deckt große Zapfung danach
def confirm(m: DhwModel, at: datetime, minutes: int = 40) -> None:
    """Der Regler hat vor einer Woche zur Zeit ``at`` desinfiziert (erkannt ``minutes`` nach dem Beginn)."""
    m.disinfects = dd.remember(m.disinfects, at - timedelta(days=7) + timedelta(minutes=minutes))


def test_planned_date_covers_bath_like_detected_special():
    m, sh, cfg, log = _sim(3, legionella=False)  # Wanne So 18 Uhr, keine Legionellenschaltung des Reglers
    sunday_17 = MONDAY + timedelta(days=6, hours=17)
    assert m.big_draw_expected(sunday_17, 2.0) >= 30.0 and m.last_special is None
    assert m.recommend(sunday_17, True, None, cfg)["target"] == cfg.dhw_max_temp  # ohne Termin: vorher hoch
    cfg.dhw_disinfect_day, cfg.dhw_disinfect_time = "So", dtime(17, 0)
    # niemand desinfiziert zu dieser Zeit: Der Termin zählt nicht – die Wärme käme ja nicht
    assert not dd.plan_counts(m, cfg, sunday_17) and dd.planned_for(m, cfg, sunday_17, 2.0) == []
    assert m.recommend(sunday_17, True, None, cfg)["target"] == cfg.dhw_max_temp
    # bestätigt (der Regler desinfizierte vorige Woche zu dieser Zeit): deckt ab Beginn + Ladedauer
    confirm(m, sunday_17)
    ready = timedelta(hours=dd.ready_hours(m, cfg))
    assert dd.plan_counts(m, cfg, sunday_17) and timedelta(minutes=15) <= ready <= timedelta(hours=1)
    assert dd.planned_for(m, cfg, sunday_17, 2.0) == [sunday_17 + ready]
    assert m.big_draw_expected(sunday_17, 2.0, dd.planned_for(m, cfg, sunday_17, 2.0)) is None
    rec = m.recommend(sunday_17, True, None, cfg)
    assert rec["target"] <= cfg.dhw_comfort_temp and not rec["reason"].startswith("große Zapfung")
    # Termin erst nach der Wanne (19 Uhr), zur Wanne selbst (18 Uhr) oder mehr als 12 h davor: deckt nichts
    for at in (dtime(19, 0), dtime(18, 0), dtime(5, 0)):
        cfg.dhw_disinfect_time = at
        confirm(m, sunday_17.replace(hour=at.hour))
        assert dd.plan_counts(m, cfg, sunday_17), at
        assert m.recommend(sunday_17, True, None, cfg)["target"] == cfg.dhw_max_temp, at
    # Regler desinfiziert zu anderer Zeit (Sa 10:00, vor 1 Tag erkannt): bestätigt den Termin nicht
    cfg.dhw_disinfect_time = dtime(17, 0)
    m.disinfects = [(sunday_17 - timedelta(days=1, hours=7)).isoformat(timespec="minutes")]
    assert not dd.plan_counts(m, cfg, sunday_17) and dd.planned_for(m, cfg, sunday_17, 2.0) == []
    assert m.recommend(sunday_17, True, None, cfg)["target"] == cfg.dhw_max_temp
    # dieselbe Zeit wie der Regler (endet 40 min nach dem Termin, auch 3 h danach noch): zählt
    confirm(m, sunday_17)
    assert dd.plan_counts(m, cfg, sunday_17)
    m.disinfects = []
    confirm(m, sunday_17, minutes=180)
    assert dd.plan_counts(m, cfg, sunday_17)
    # die letzte Desinfektion zu dieser Zeit liegt mehr als 8 Tage zurück: nicht mehr bestätigt
    m.disinfects = [(sunday_17 - timedelta(days=9)).isoformat(timespec="minutes")]
    assert not dd.plan_counts(m, cfg, sunday_17)
    assert not dd.plan_counts(m, Config(), sunday_17)  # ohne Termin nie


def _sim_plan(weeks: int, cfg: Config, temp_dis: float = 70.0, es73_hour: int | None = None, flag: bool = True,
              onetime_wed: bool = False):
    """Wie test_dhw_special._sim, mit Termin in den Einstellungen und Schatten wie engine.dhw_step: Sonderladung
    des Reglers spiegelt den echten Speicher, sonst ``dhw_disinfect.shadow_mirror`` (bestätigter Termin).

    ``es73_hour``: der Regler desinfiziert sonntags zu dieser Stunde auf 70 °C (``flag``: mit Kesselschalter
    ``wwdisinfecting``, sonst nur am Gipfel erkennbar); ``onetime_wed``: mittwochs 19 Uhr eine Einmalladung auf
    60 °C (Kessel meldet Sonderladung, aber keine Desinfektion). Zählt die letzte Woche: Verluste echt/Schatten.
    """
    m, sh = DhwModel(), DhwShadow()
    for d in range(7):
        m.profile_n[d * 24 + 18] = 2
    t, charging, goal, rec, prev = 55.0, False, 55.0, None, None
    log = {"cold_sunday": 0, "t_sim_17": [], "t_sim_1745": [], "loss": 0.0, "loss_s": 0.0, "mirrors": []}
    for i in range(weeks * 7 * 288):
        now = MONDAY + timedelta(minutes=5 * i)
        t_prev = t
        t -= 0.02 * (t - 18.0) * STEP
        sunday = now.weekday() == 6
        if now.hour == 7 and now.minute < 15:
            t -= 4.0
        if sunday and now.hour == 18 and now.minute < 20:
            t -= 11.25
        if sunday and now.hour == es73_hour and now.minute == 0:
            charging, goal = True, 70.0
        elif onetime_wed and now.weekday() == 2 and now.hour == 19 and now.minute == 0:
            charging, goal = True, 60.0
        elif not charging and t < 45:
            charging, goal = True, 55.0
        heated = charging
        if charging:
            t += 63.5 * STEP
            if t >= goal:
                charging = False
        res = m.step(now, STEP, round(t_prev, 1), round(t, 1), heated, 10.8 if heated else None, dhw_set=55.0,
                     burner_kw_prev=10.8 if heated else 0.0, special=flag and heated and goal > 55.0,
                     disinfect=flag and heated and goal == 70.0, disinfect_temp=temp_dis)
        before = rec or {}
        rec = m.recommend(now, True, None, cfg)
        mirror = round(t, 1) if res["special"] else (
            None if prev is None else dd.shadow_mirror(m, cfg, prev, now, temp_dis))
        if mirror is not None and not res["special"]:
            log["mirrors"].append(now)
        out = sh.step(m, round(t, 1), rec["target"], res["draw_k"], STEP, cfg.dhw_comfort_temp,
                      dhw_min=cfg.dhw_min_temp, margin_bound=bool(before.get("margin_bound")),
                      dhw_max=cfg.dhw_max_temp, mirror=mirror)
        prev = now
        if out["raise_margin"] and m.charges >= 3:
            m.note_cold_draw()
        if out["cold_draw"] and sunday:
            log["cold_sunday"] += 1
        if sunday and now.hour == 17 and now.minute in (5, 45):
            log["t_sim_17" if now.minute == 5 else "t_sim_1745"].append(round(sh.t_sim, 1))
        if i >= 288 * 7 * (weeks - 1):
            log["loss"] += res["loss_kwh"]
            log["loss_s"] += out["loss_kwh"]
    return m, sh, log


def test_unconfirmed_date_changes_nothing_in_shadow():
    """Kein Regler desinfiziert, HeatPilot löst nicht aus: Der Termin bringt keine Wärme – Ersparnis und zu kalte
    Wannen wie ohne Termin (sonst stiege die Ersparnis um Wärme, die es nicht gibt)."""
    cfg = _cfg()
    cfg.dhw_disinfect_day, cfg.dhw_disinfect_time = "So", dtime(17, 0)
    m, sh, log = _sim_plan(3, cfg)
    _, _, log0 = _sim_plan(3, _cfg())
    assert log["mirrors"] == [] and not dd.plan_counts(m, cfg, MONDAY + timedelta(days=20, hours=17))
    assert log["cold_sunday"] == log0["cold_sunday"] >= 2
    assert (round(log["loss_s"], 3), round(log["loss"], 3)) == (round(log0["loss_s"], 3), round(log0["loss"], 3))
    assert all(t < 69.0 for t in log["t_sim_17"]) and m.special_charges == 0 and m.disinfects == []


def test_shadow_gets_heat_of_confirmed_date_and_bath_is_not_cold():
    """Der Regler desinfiziert sonntags 17 Uhr (ohne Kesselschalter, nur am Gipfel erkennbar), der Termin steht
    auf derselben Zeit: ab der zweiten Woche bestätigt, der Schatten steht nach der Ladedauer auf 70 °C."""
    cfg = _cfg()
    cfg.dhw_disinfect_day, cfg.dhw_disinfect_time = "So", dtime(17, 0)
    m, sh, log = _sim_plan(3, cfg, es73_hour=17, flag=False)
    assert all(t >= 65.0 for t in log["t_sim_1745"]), log["t_sim_1745"]
    assert log["cold_sunday"] == 0 and m.comfort_margin == 0.0
    assert m.special_charges == 3 and len(m.disinfects) == 3  # erkannt am Gipfel (≥ 70 − 5 °C)
    assert dd.plan_counts(m, cfg, MONDAY + timedelta(days=20, hours=16))
    # Gegenprobe ohne Termin und ohne Regler: die Wanne ist für die Strategie zu kalt
    assert _sim(3, legionella=False)[3]["cold_sunday"] >= 2


def test_engine_shadow_and_temperature_from_ems(tmp_path):
    cfg = plan("So", "13:00", boiler_nominal_kw=18.0)
    eng = Engine(cfg, Store(tmp_path / "t.db"), Discovered())
    assert eng.disinfect_temp() == (70.0, "eingestellt")
    eng.ems = {"boiler": {"dhw_disinfectiontemp": 65}}  # EMS-ESP ab 3.6: dhw/disinfectiontemp
    assert eng.disinfect_temp() == (65.0, "laut Kessel")
    eng.ems = {"boiler": {"wwdisinfectiontemp": 120}}  # unplausibel: Einstellung gilt
    assert eng.disinfect_temp() == (70.0, "eingestellt")
    eng.ems = {"boiler": {"wwdisinfectiontemp": "72"}}
    assert eng.disinfect_temp() == (72.0, "laut Kessel")
    assert BOILER_ROLES["dhw_disinfect_temp"][:2] == ("dhw_disinfectiontemp", "wwdisinfectiontemp")
    t0 = int(datetime(2026, 1, 11, 12, 0).timestamp())  # Sonntag 12:00
    # vorigen Sonntag hat der Regler um 13 Uhr desinfiziert (erkannt 13:40): der Termin ist bestätigt
    eng.dhw.disinfects = [datetime(2026, 1, 4, 13, 40).astimezone().isoformat(timespec="minutes")]
    # Ladedauer ohne gelernte Ladungen und Ladekosten: Standard 20 K/h von 50 auf 72 °C = 66 min
    assert eng.disinfect_ready_h() == pytest.approx(1.1)
    for i in range(30):
        eng.dhw_step(t0 + i * 300, {"dhw_temp": round(55.0 - 0.05 * i, 1), "dhw": 0, "burner_pow": 0,
                                    "dhw_set": 55.0, "home_count": 1})
        if i in (11, 25):  # vor dem Termin und während seiner Ladedauer: Schatten beim eigenen Ziel
            assert eng.dhw_shadow.t_sim < 60.0, i
    assert eng.dhw_shadow.t_sim > 70.0 and eng.dhw_shadow._ride  # ab 14:06 mit der Desinfektionswärme
    info = eng.disinfect_info(now_ts=t0)
    assert info["configured"] and info["next_text"] == "So 13:00 (eingestellt)" and info["counts"]
    assert (info["temp"], info["temp_source"], info["temp_boiler"]) == (72.0, "laut Kessel", 72.0)
    assert info["triggers"] is False and info["hint"].startswith("HeatPilot löst sie noch nicht aus")
    assert info["last_special"] is None and "suggestion" not in info and "counts_text" not in info
    assert info["last_disinfect"].startswith("2026-01-04T13:40") and info["temp_note"] is None
    eng.dhw._last_special = datetime(2026, 1, 10, 10, 30).astimezone()  # Regler desinfiziert Sa 10:30
    eng.dhw.disinfects = [eng.dhw._last_special.isoformat(timespec="minutes")]
    info = eng.disinfect_info(now_ts=t0)
    assert not info["counts"] and info["last_special_text"] == "Sa 10:30 (erkannt)"
    assert info["counts_text"] == ("Termin noch nicht bestätigt – kein Regler desinfiziert zu dieser Zeit; der Regler "
                                   "desinfiziert zu anderer Zeit (zuletzt Sa 10:30) – es zählt seine Zeit")
    eng.dhw._last_special, eng.dhw.disinfects = None, []
    info = eng.disinfect_info(suggest=True, now_ts=t0)
    assert not info["counts"] and info["counts_text"] == dd.NOT_CONFIRMED
    assert not info["suggestion"]["ok"]  # noch keine Daten


def test_engine_without_plan_changes_nothing(tmp_path):
    eng = Engine(Config(), Store(tmp_path / "t.db"), Discovered())
    t0 = int(datetime(2026, 1, 11, 12, 0).timestamp())
    for i in range(30):
        eng.dhw_step(t0 + i * 300, {"dhw_temp": 55.0, "dhw": 0, "burner_pow": 0, "dhw_set": 55.0, "home_count": 1})
    assert eng.dhw_shadow.t_sim < 60.0 and not eng.dhw_shadow._ride
    info = eng.disinfect_info(now_ts=t0)
    assert not info["configured"] and info["next"] is None and info["next_text"] is None and not info["counts"]
    eng.dhw._last_special = datetime(2026, 1, 4, 13, 40).astimezone()
    assert eng.disinfect_info(now_ts=t0)["last_special_text"] == "So 13:40 (erkannt)"


# ------------------------------------------------------------------ Bereitschaft: wwdisinfecting nötig mit Termin
def test_readiness_needs_disinfecting_only_with_date(tmp_path):
    eng, _, _ = house(tmp_path, [room("Wz", "climate.wz")])
    eng.cfg.emsesp_host = "ems-esp.local"
    eng.ems_controls, eng.ems_controls_ts = _controls(), time.time()
    b = readiness.boiler(eng)
    dis = next(c for c in b["controls"] if c["key"] == "wwdisinfecting")
    assert not dis["required"] and "note" not in dis and b["disinfect_required"] is False
    assert b["text"].startswith("Alle 5 nötigen Steuerungen")
    eng.cfg.dhw_disinfect_day = "So"
    b = readiness.boiler(eng)
    dis = next(c for c in b["controls"] if c["key"] == "wwdisinfecting")
    assert dis["required"] and dis["note"] == "Desinfektion braucht WWK Desinfizieren (schreibbar)"
    assert b["ready"] and b["text"].startswith("Alle 6 nötigen Steuerungen vorhanden und schreibbar")
    eng.ems_controls = _controls(wwdisinfecting={"found": True, "writeable": False, "value": False})
    b = readiness.boiler(eng)
    assert not b["ready"] and b["text"] == "Es fehlen: Desinfektion (nicht schreibbar)"
    assert readiness.assess(eng, int(time.time()))["level"] == "bereit"  # Raumsteuerung braucht den Kessel nicht
    assert not BOILER_CONTROLS["wwdisinfecting"][1]  # ohne Termin bleibt es Info


# ------------------------------------------------------------------ API für die Oberfläche
def test_api_settings_and_summary_carry_disinfection(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from heatpilot import schedule
    from heatpilot.web import make_app as web_app
    from test_control34 import make_app

    app, ha, _ = make_app(tmp_path / "eng")
    (tmp_path / "options.json").write_text("{}")

    async def apply_settings(raw):  # wie main.App.apply_settings, ohne Neuaufbau der Engine
        clean, errors = validate(raw)
        if errors:
            return errors
        save_settings(clean, tmp_path)
        cfg = Config.load(tmp_path / "options.json")
        app.cfg = app.eng.cfg = cfg
        app.eng.eff = schedule.effective(cfg, app.eng.schedule_learned)
        return []

    app.apply_settings = apply_settings

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            j = await (await client.get("/api/settings")).json()
            d = j["disinfect"]
            assert j["settings"]["dhw_disinfect_day"] == "" and j["defaults"]["dhw_disinfect_time"] == "13:00"
            assert d["days"] == ["", "Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"] and d["temp_range"] == [60, 80]
            assert not d["configured"] and d["temp"] == 70.0 and d["temp_source"] == "eingestellt"
            assert d["suggestion"] == {"ok": False, "reason": "Zapfprofil erst 0 Wochen gelernt – ein Vorschlag "
                                                              "braucht mindestens 4 Wochen"}
            r = await client.post("/api/settings", json={"dhw_disinfect_day": "So", "dhw_disinfect_time": "17:30",
                                                         "dhw_disinfect_temp": 70})
            assert r.status == 200 and (await r.json())["ok"]
            j = await (await client.get("/api/settings")).json()
            assert (j["settings"]["dhw_disinfect_day"], j["settings"]["dhw_disinfect_time"]) == ("So", "17:30")
            assert j["disinfect"]["configured"] and j["disinfect"]["next_text"] == "So 17:30 (eingestellt)"
            r = await client.post("/api/settings", json={"dhw_disinfect_day": "Sonntag"})
            assert r.status == 400 and "dhw_disinfect_day" in (await r.json())["errors"][0]
            s = await (await client.get("/api/summary")).json()
            assert s["dhw"]["disinfect_plan"]["next_text"] == "So 17:30 (eingestellt)"
            assert set(s["dhw"]["disinfect_plan"]) >= {"next", "counts", "temp", "temp_source", "last_special",
                                                       "last_special_text", "hint", "triggers"}
        finally:
            await client.close()

    asyncio.run(go())
    assert ha.calls == []  # nichts geschrieben


def test_still_no_write_path_to_emsesp():
    src = (Path(__file__).parent.parent / "ems_heatpilot" / "heatpilot" / "dhw_disinfect.py").read_text()
    assert "emsesp" not in src.replace("EMS-ESP", "") and "call_service" not in src
