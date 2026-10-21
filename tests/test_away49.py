"""0.49: Niemand zu Hause – Soll nach Entfernung der nächsten Person (wie Tado), Vorheizen zur Ankunft nur bis
Komfort − ``arrival_margin_k`` (nur stark ausgekühlte Räume brauchen die Vorhersage)."""

from datetime import timedelta
from types import SimpleNamespace

from heatpilot.away import AWAY_KM_STEP, away_setpoint, nearest_km
from heatpilot.config import Config, validate
from heatpilot.controlloop import PREHEAT_MIN_H, Presence, desired
from heatpilot.discovery import Room
from heatpilot.presence_plan import Household

from test_control34 import MON, at, slow_room, wz

CFG = Config(control_mode="heatpilot", away_temp=16.0)


def test_setpoint_falls_ten_percent_per_ten_km_down_to_the_away_temperature():
    assert away_setpoint(21.0, CFG, 0.0) == 21.0
    assert round(away_setpoint(21.0, CFG, 10.0), 2) == 18.9
    assert round(away_setpoint(21.0, CFG, 14.3), 1) == 18.0
    assert away_setpoint(21.0, CFG, 45.0) == 16.0          # weit weg: Abwesenheitstemperatur
    assert away_setpoint(21.0, CFG, None) == 16.0          # ohne GPS wie bisher
    off = Config(control_mode="heatpilot", away_temp=16.0, away_distance_pct=0.0)
    assert away_setpoint(21.0, off, 3.0) == 16.0           # 0 % = gleich die Abwesenheitstemperatur


def test_nobody_home_follows_the_nearest_distance():
    """Fall aus dem Haus: Maria 14 km, Ingo 45 km weg, Ankunft laut Gewohnheit 17 Uhr – um 7 Uhr 18 °C statt 21."""
    pres = Presence(home_count=0, arrival=at(MON, 17), away_km=14.3)
    d = desired(wz(), at(MON, 7), CFG, pres, slow_room(), 20.8, 5.0)
    assert d.phase == "abwesend" and d.setpoint == 18.0 and "nächste Person 14 km" in d.reason
    kitchen = Room("Kü", "climate.kue", managed=True, comfort=20.0, platform="homekit_controller")
    assert desired(kitchen, at(MON, 7), CFG, pres, None, 20.0, 5.0).setpoint == 17.0  # je Raum vom eigenen Komfort
    near = Presence(home_count=0, away_km=1.0)
    assert desired(wz(), at(MON, 7), CFG, near, None, 20.8, 5.0).setpoint == 21.0  # Spaziergang: 20,79 → 21 °C
    far = Presence(home_count=0, away_km=80.0)
    d = desired(wz(), at(MON, 7), CFG, far, None, 20.8, 5.0)
    assert d.setpoint == 16.0 and "km" not in d.reason  # ganz unten: Entfernung spielt keine Rolle mehr


def test_preheat_only_when_the_room_is_well_below_comfort():
    """Zur Ankunft reicht Komfort − 1 K: ein kaum ausgekühlter Raum heizt erst kurz vorher, ein kalter früh."""
    m, eta = slow_room(), at(MON, 17)
    pres = Presence(home_count=0, arrival=eta, away_km=14.3)
    warm = desired(wz(), at(MON, 12), CFG, pres, m, 20.3, 5.0)  # schon über 20 °C
    assert warm.phase == "abwesend" and eta - warm.until == timedelta(hours=PREHEAT_MIN_H)
    cold = desired(wz(), at(MON, 12), CFG, pres, m, 17.0, 5.0)
    assert cold.until < warm.until - timedelta(hours=1)
    full = Config(control_mode="heatpilot", away_temp=16.0, arrival_margin_k=0.0)
    assert desired(wz(), at(MON, 12), full, pres, m, 20.3, 5.0).until < warm.until  # bis Komfort: früher
    assert desired(wz(), eta - timedelta(minutes=10), CFG, pres, m, 20.3, 5.0).phase == "vorheizen"


def test_night_and_vacation_stay_as_before():
    pres = Presence(home_count=0, away_km=3.0)
    assert desired(wz(), at(MON, 23), CFG, pres, None, 20.0, 5.0).phase == "nacht"
    vac = Presence(home_count=0, vacation=True, away_km=3.0)
    assert desired(wz(), at(MON, 12), CFG, vac, None, 20.0, 5.0).setpoint == 16.0


def test_distance_changes_only_after_two_km():
    """GPS-Schwankung an einer Stufe: das Soll springt nicht hin und her."""
    def p(km, home=False):
        return SimpleNamespace(home=home, dist_km=km)

    assert nearest_km([p(9.6), p(45.0), p(0.0, home=None)], None) == 9.6
    assert nearest_km([p(10.4), p(45.0)], 9.6) == 9.6 and nearest_km([p(9.1)], 9.6) == 9.6
    assert nearest_km([p(9.6 + AWAY_KM_STEP)], 9.6) == 9.6 + AWAY_KM_STEP
    assert nearest_km([p(None), p(None)], 9.6) is None  # niemand mit GPS


def test_preview_uses_the_distance_until_the_first_arrival():
    hh = Household(points=[[(at(MON, 7), False), (at(MON, 17), True)]], unknown=[[]], away_km=14.3,
                   away_until=at(MON, 17))
    assert hh.at(at(MON, 12)).away_km == 14.3
    hh.points[0].append((at(MON, 19), False))
    assert hh.at(at(MON, 20)).away_km is None  # spätere Abwesenheit: Entfernung unbekannt


def test_settings_validate():
    ok, errors = validate({"away_distance_pct": "12,5", "arrival_margin_k": "0,5"})
    assert errors == [] and ok["away_distance_pct"] == 12.5 and ok["arrival_margin_k"] == 0.5
    assert validate({"away_distance_pct": 80})[1] and validate({"arrival_margin_k": 5})[1]
    assert Config().away_distance_pct == 10.0 and Config().arrival_margin_k == 1.0


def test_control_loop_writes_the_distance_setpoint(tmp_path):
    """Im Takt: Person 14 km weg (GPS), Gewohnheit gelernt „weg“ – das Thermostat bekommt 18 °C statt 16 °C."""
    from heatpilot.presence import haversine_km

    from test_control34 import climate, make, step, ts_of

    eng, ha, _, loop = make(tmp_path, cfg=Config(control_mode="heatpilot", away_temp=16.0), persons=["person.a"],
                            home_zone=(53.55, 10.0))
    p = eng.persons["person.a"]
    p.week, p.visits = [0.2] * len(p.week), [5] * len(p.visits)  # gelernt: meist weg, keine Heimkehr in Sicht
    lat = 53.676
    assert 13.5 < haversine_km(53.55, 10.0, lat, 10.0) < 14.5
    eng.states["person.a"] = {"state": "not_home", "attributes": {"latitude": lat, "longitude": 10.0,
                                                                   "friendly_name": "A"}}
    climate(eng, "climate.wz", 20.5, 21.0)
    rooms = step(eng, loop, ts_of(MON, 10))
    assert rooms["climate.wz"]["phase"] == "abwesend" and rooms["climate.wz"]["desired"] == 18.0
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": 18.0})
    assert loop.away_km is not None and 13.5 < loop.away_km < 14.5
