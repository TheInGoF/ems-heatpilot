"""0.75: Schimmelschutz – Grenze nach Außentemperatur, geglättete Feuchte (eigener Fühler, sonst Hauswert), zu feuchte
Räume nachts und bei Abwesenheit nur 1 K unter Komfort, Hinweise für die Karte statt Meldungen aufs Handy."""

from types import SimpleNamespace

from heatpilot import card_bridge as cb
from heatpilot import mold, room_modes
from heatpilot.controlloop import Desired

from test_control34 import MON, climate, make, step, ts_of

T0 = ts_of(MON, 12)
NIGHT = Desired(18.0, "nacht", "Nacht", None)


def test_limit_follows_the_outdoor_temperature():
    assert [mold.limit(t) for t in (15, 10, 8, 0, -10, -20)] == [60, 60, 58, 50, 40, 40]
    assert mold.limit(None) == 50


def test_house_value_is_the_median_of_rooms_with_a_sensor():
    rooms = [("a", 20, 21, 0, 62.0), ("b", 20, 21, 0, 50.0), ("c", 20, 21, 0, None), ("d", 20, 21, 0),
             ("e", 20, 21, 0, 55.0)]
    assert mold.house(rooms) == 55.0 and mold.house([("x", 20, 21, 0, None)]) is None


def _feed(st, values, start=0, own=True, t_out=8.0):
    """Je Wert ein Takt von 5 min ab ``start``; gibt (Wunschzustand, nächster Takt)."""
    out = NIGHT
    for i, v in enumerate(values, start):
        reading = ("climate.wz", 20.0, 18.0, 0.0, v if own else None)
        out = mold.step(st, 21.0, NIGHT, reading, None if own else v, t_out, T0 + i * 300)
    return out, start + len(values)


def test_short_peaks_do_not_count():
    st = {}
    _feed(st, [50.0] * 12 + [90.0] * 2 + [50.0] * 2)  # 10 min Duschen mit 90 %
    assert "damp" not in st and st["rh"][0] < 56


def test_damp_room_gets_one_kelvin_and_a_way_back():
    st = {}
    out, n = _feed(st, [62.0] * 24)  # 2 h über der Grenze (58 % bei 8 °C draußen)
    assert st["damp"]["rh"] == 62 and st["damp"]["limit"] == 58 and st["damp"]["own"]
    assert out.setpoint == 20.0 and out.reason == "Nacht · Feuchte 62 % (Grenze 58 %): nur 1 K abgesenkt"
    out, n = _feed(st, [56.0] * 72, n)  # unter der Grenze, aber noch nicht 3 % darunter
    assert st.get("damp") and out.setpoint == 20.0
    out, n = _feed(st, [54.0] * 72, n)
    assert "damp" not in st and out == NIGHT
    _feed(st, [70.0] * 24, n, own=False)  # Raum ohne eigenen Fühler: der Hauswert, neu geglättet
    assert st["damp"]["own"] is False and st["damp"]["rh"] == 70


def test_guard_only_at_night_and_away():
    st = {"damp": {"rh": 62, "limit": 58, "own": True, "since": T0}}
    assert mold.guard(st, 21.0, NIGHT).setpoint == 20.0
    assert mold.guard(st, 21.0, NIGHT._replace(phase="abwesend")).setpoint == 20.0
    for phase in ("komfort", "urlaub", "vorheizen"):  # im Urlaub entsteht keine Feuchte
        assert mold.guard(st, 21.0, NIGHT._replace(phase=phase)) == NIGHT._replace(phase=phase)
    assert mold.guard(st, 21.0, NIGHT._replace(setpoint=20.5)).setpoint == 20.5  # schon wärmer
    assert mold.guard({}, 21.0, NIGHT) == NIGHT and mold.guard(st, None, NIGHT) == NIGHT


def test_control_loop_sets_a_damp_room_back_only_one_kelvin(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)  # ohne Außenfühler: Grenze 50 %
    climate(eng, "climate.wz", 20.5, 19.0, current_humidity=62)
    step(eng, loop, ts_of(MON, 12))
    climate(eng, "climate.wz", 20.5, 21.0, current_humidity=62)
    info = step(eng, loop, ts_of(MON, 22))["climate.wz"]  # Nacht: 20 statt 18 °C
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": 20.0})
    assert info["desired"] == 20.0 and "Feuchte 62 % (Grenze 50 %)" in info["reason"]
    assert all(c[0] == "climate" for c in ha.calls)  # keine Meldung aufs Handy


def test_preview_uses_the_same_guard():
    loop = SimpleNamespace(state={"climate.wz": {"damp": {"rh": 62, "limit": 58, "own": True, "since": T0}}})
    eng = SimpleNamespace(control_loop=loop, found=SimpleNamespace(room_of=lambda c: SimpleNamespace(comfort=21)))
    assert room_modes.planned(eng, "climate.wz", NIGHT, T0) == 20.0
    loop.state["climate.wz"]["own"] = {"mode": "aus"}  # eigene Betriebsart geht vor
    assert room_modes.planned(eng, "climate.wz", NIGHT, T0) == room_modes.FROST_C


def test_hints_for_the_card_not_the_phone():
    rooms = [SimpleNamespace(climate="climate.wz", name="Wohnzimmer", managed=True),
             SimpleNamespace(climate="climate.flur", name="Flur", managed=False),
             SimpleNamespace(climate="climate.bad", name="Bad", managed=True)]
    loop = SimpleNamespace(state={"climate.wz": {"damp": {"rh": 62, "limit": 58, "own": True, "since": T0}},
                                  "climate.flur": {"damp": {"rh": 61, "limit": 58, "own": False, "since": T0}},
                                  "climate.bad": {}})
    assert [i["text"] for i in mold.hints(loop, rooms)] == [
        "Wohnzimmer: 62 % Luftfeuchte – bitte lüften (Grenze heute 58 %)",
        "Luftfeuchte im Haus 61 % (Flur ohne eigenen Fühler) – bitte lüften (Grenze heute 58 %)"]
    assert [i["note"] for i in mold.hints(loop, rooms)] == ["nachts und bei Abwesenheit nur 1 K abgesenkt", None]
    notifier = SimpleNamespace(open_keys=lambda: {"disinfect_failed": "Desinfektion So 13:00 nicht gelaufen",
                                                  "valve_leak:climate.flur": "Flur: Ventil schließt wohl nicht ganz",
                                                  "ha_unbekannt": "x"})
    eng = SimpleNamespace(control_loop=loop, found=SimpleNamespace(rooms=rooms), notifier=notifier)
    entity, n, attrs = cb.hints(eng)
    assert entity == cb.HINTS == "sensor.heatpilot_hinweise" and n == 5
    assert [i["kind"] for i in attrs["items"]] == ["lueften", "lueften", "problem", "problem", "problem"]
    assert [i["group"] for i in attrs["items"]] == ["lueften", "lueften", "warmwasser", "ventile", "sonstiges"]
    assert attrs["items"][2]["text"] == "Desinfektion So 13:00 nicht gelaufen"
    assert {cb.group_of(k) for k in ("boiler_period_failed", "boiler_write_failed")} == {"kessel"}
    assert cb.group_of("room_write_failed:climate.wz") == "thermostate" and cb.group_of("valve_pid_fail") == "ventile"
    assert attrs["icon"] == "mdi:bell-badge-outline"
    empty = cb.hints(SimpleNamespace(control_loop=None, found=SimpleNamespace(rooms=rooms), notifier=None))
    assert empty[1] == 0 and empty[2]["items"] == [] and empty[2]["icon"] == "mdi:bell-outline"
