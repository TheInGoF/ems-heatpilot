"""0.50: Zuschlag „Raum kommt nicht hoch“, Takt-Boost nur am Takten, nicht mitten im Brennerlauf senken, Abnahme nach
Ventilöffnung, HeatPilots Entscheidung im 30-s-Export.

Fall aus dem 30-s-Protokoll vom 21.10.: Küche, Flur und Wohnzimmer eine Stunde mit offenem Ventil unter Soll bei
35–37 °C Vorlauf; der Boost blieb aus (zu wenig Starts), die Abnahme-Regel sprang mit einem 9-%-Ventil auf 49 °C, und das Senken
auf 37 °C schaltete den Brenner nach 4,5 min ab."""

import csv
import io

from heatpilot import boiler_flow, boiler_stall as bs, live_log
from heatpilot import boiler_rule as br
from heatpilot.boiler_flow import compute
from heatpilot.discovery import Room

from test_boiler41 import FLOW, T0, calls, cycle, house, shows, step
from test_control34 import climate, wz

ROOM = ("climate.wz", "Wz")


def _s(state, temp, ts, target=21.0, valve=100.0, headroom=30):
    return bs.step(state, [(*ROOM, temp, target, valve)], ts, headroom)


# ------------------------------------------------------------------ Zuschlag: reine Regel
def test_room_that_does_not_rise_gets_two_kelvin_every_quarter_hour():
    s, stalled = _s(None, 20.5, T0)
    assert s["k"] == 0 and stalled == [] and s["rooms"]["climate.wz"] == {"since": T0, "ref": 20.5}
    s, stalled = _s(s, 20.6, T0 + 1799)  # 0,1 K in 30 min: noch nicht
    assert s["k"] == 0 and stalled == []
    s, stalled = _s(s, 20.6, T0 + 1800)
    assert s["k"] == 2 and stalled == ["Wz"]
    assert _s(s, 20.6, T0 + 2699)[0]["k"] == 2      # je 15 min
    s, _ = _s(s, 20.6, T0 + 2700)
    assert s["k"] == 4
    s, stalled = _s(s, 20.7, T0 + 3000)            # steigt um 0,2 K: Stufe bleibt, die Uhr beginnt neu
    assert s["k"] == 4 and stalled == [] and s["rooms"]["climate.wz"] == {"since": T0 + 3000, "ref": 20.7}
    s, _ = _s(s, 20.8, T0 + 4500)                  # noch keine 30 min seit dem Anstieg
    assert s["k"] == 4
    s, _ = _s(s, 20.9, T0 + 4600)                  # warm (0,1 K unter Soll): zurück in 2-K-Schritten
    assert s["k"] == 2 and s["rooms"] == {}
    assert _s(s, 20.9, T0 + 5499)[0]["k"] == 2
    assert _s(s, 20.9, T0 + 5500)[0]["k"] == 0


def test_thresholds_with_hysteresis_and_limit():
    assert _s(None, 20.75, T0)[0]["rooms"] == {}                    # nur 0,25 K unter Soll
    assert _s(None, 20.5, T0, valve=79.0)[0]["rooms"] == {}         # Ventil unter 80 %
    s, _ = _s(None, 20.6, T0)
    s, _ = _s(s, 20.75, T0 + 600, valve=55.0)                        # einmal erkannt: Thermostat pendelt, bleibt dabei
    assert s["rooms"]["climate.wz"] == {"since": T0, "ref": 20.6}
    assert _s(s, 20.6, T0 + 900, valve=40.0)[0]["rooms"] == {}       # Ventil zu: nicht mehr
    assert _s(s, 20.85, T0 + 900)[0]["rooms"] == {}                  # 0,15 K unter Soll: am Soll
    s, _ = _s(s, 20.6, T0 + 1800, headroom=1)
    assert s["k"] == 1                                               # höchstens bis zur Obergrenze
    assert bs.text(4, ["Flur", "Wohnzimmer"]) == "Zuschlag +4 K (Flur, Wohnzimmer kommen nicht hoch)"
    assert bs.text(2, ["Flur"]) == "Zuschlag +2 K (Flur kommt nicht hoch)" and bs.text(2, []) == "Zuschlag +2 K"


# ------------------------------------------------------------------ Zuschlag im Ziel
def test_target_rises_when_the_room_stays_below_and_floor_rooms_do_not_count(tmp_path):
    eng, ha, bc = house(tmp_path)  # Wz 20,5 / 21 °C, Ventil offen: braucht 46 °C (41 + 5 aufheizen)
    t, _ = compute(eng, T0)
    assert t.flow == 46 and t.stall_k == 0
    t, _ = compute(eng, T0 + 1800, None, None, t.stall)
    assert t.stall_k == 2 and t.stalled == ["Wz"] and t.flow == 48
    assert "Zuschlag +2 K (Wz kommt nicht hoch)" in t.reason and not t.boost_k  # Kessel taktet nicht
    eng.cfg.rooms = [{"climate": "climate.wz", "name": "Wz", "floor_type": "mischer", "floor_max_flow": 60}]
    eng.cfg.floor_heating_rooms = ["climate.wz"]
    t, _ = compute(eng, T0)
    t, _ = compute(eng, T0 + 1800, None, None, t.stall)
    assert t.stall_k == 0 and t.stalled == []  # Fußbodenheizung: träge, zählt nicht


def test_control_loop_keeps_the_surcharge_and_resets_it_on_switch(tmp_path):
    eng, ha, bc = house(tmp_path)
    for i in range(0, 61):
        step(bc, T0 + i * 30)
        if calls(ha):
            shows(eng, flow=calls(ha)[-1][3]["value"])
    assert [c[3]["value"] for c in calls(ha) if c[2] == FLOW] == [46, 48]
    assert bc.state["stall"]["k"] == 2 and bc.as_dict(T0)["target"]["stalled"] == ["Wz"]
    assert "Zuschlag +2 K" in bc.as_dict(T0)["status_text"]
    eng.cfg.boiler_control = "trocken"
    bc.plan(T0 + 1900)
    assert bc.state["stall"]["k"] == 0


# ------------------------------------------------------------------ Takt-Boost nur am Takten
def test_boost_when_cycling_even_if_no_room_waits(tmp_path):
    eng, ha, bc = house(tmp_path, temp=21.0)  # am Soll, Ventil offen
    cycle(eng, T0)
    t, _ = compute(eng, T0)
    assert t.waiting == [] and t.boost_wanted and t.boost_k == 2
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="idle")  # kein Ventil offen: niemand nimmt die Wärme
    t, _ = compute(eng, T0)
    assert not t.boost_wanted and t.boost_k == 0


def test_room_waits_but_boiler_does_not_cycle_no_boost(tmp_path):
    eng, ha, bc = house(tmp_path)
    cycle(eng, T0, starts=2)
    t, _ = compute(eng, T0)
    assert t.waiting == ["Wz"] and not t.boost_wanted and t.boost_k == 0


# ------------------------------------------------------------------ Abnahme nach Ventilöffnung
def test_load_weighs_the_valve_opening(tmp_path, monkeypatch):
    eng, ha, bc = house(tmp_path, rooms=[wz(), Room("Kz", "climate.kz", platform="homekit_controller")],
                        boiler_min_power_pct=30.0, boiler_nominal_kw=18.0)
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="heating")

    def readings(valve):
        return lambda _: {"climate.wz": ("climate.wz", 21.0, 21.0, valve, None)}

    monkeypatch.setattr(boiler_flow, "_readings", readings(100.0))
    assert compute(eng, T0)[0].load == 52
    monkeypatch.setattr(boiler_flow, "_readings", readings(9.0))  # knapp offen: nimmt kaum etwas ab
    t, _ = compute(eng, T0)
    assert t.load is None and "lange Pausen" in t.load_text


# ------------------------------------------------------------------ nicht mitten im Brennerlauf senken
def test_decide_flow_waits_for_the_burner():
    d = br.decide_flow(41, 46.0, T0, T0 - 700, down_since=T0 - 700, burning_until=T0 + 300)
    assert not d.write and d.until == T0 + 300 and "Brenner" in d.why
    d = br.decide_flow(41, 46.0, T0, T0 - 700, down_since=T0 - 700, burning_until=T0 + 300, keepalive=True)
    assert d.write and d.value == 46 and "erneuert" in d.why  # gehaltenes Soll erneuern
    assert br.decide_flow(41, 46.0, T0 + 300, T0 - 700, down_since=T0 - 700, burning_until=T0 + 300).value == 41
    assert br.decide_flow(50, 46.0, T0, T0 - 700, burning_until=T0 + 300).value == 50  # anheben sofort
    d = br.decide_flow(41, 46.0, T0, T0 - 700, down_since=T0 - 120, burning_until=T0 + 60)
    assert "10 min niedriger" in d.why and d.until == T0 + 480  # das spätere gilt


def test_lowering_waits_until_the_burner_ran_ten_minutes_or_stopped(tmp_path):
    eng, ha, bc = house(tmp_path)
    assert step(bc, T0) == 1 and calls(ha)[-1][3] == {"value": 46}
    shows(eng, flow=46)
    climate(eng, "climate.wz", 21.0, 21.0, hvac_action="heating")  # warm: Ziel 41 °C (Ventil offen)
    eng.ems["boiler"]["burngas"] = 1
    eng.store.add_burner_event(T0 + 400, True)  # Brenner springt kurz vor Ende der 10 min an
    for i in range(1, 25):
        step(bc, T0 + 60 + i * 30)
    assert [c[3]["value"] for c in calls(ha) if c[2] == FLOW] == [46]  # nicht nach 10 min (T0 + 660) gesenkt
    assert "Brenner" in bc.view["decision"]["flow"]
    eng.ems["boiler"]["burngas"] = 0  # Brenner aus (von selbst): jetzt senken
    step(bc, T0 + 900)
    assert calls(ha)[-1][3] == {"value": 41}


# ------------------------------------------------------------------ 30-s-Export
def test_export_has_heatpilots_decision(tmp_path):
    from heatpilot import main as main_mod
    from types import SimpleNamespace

    eng, ha, bc = house(tmp_path)
    cycle(eng, T0)
    eng.record_live(T0)
    import asyncio
    asyncio.run(main_mod.boiler_step(SimpleNamespace(eng=eng, ha=ha, cfg=eng.cfg), T0))
    rows = list(csv.reader(io.StringIO(live_log.csv_text(eng, 1, T0 + 10))))
    row = dict(zip(rows[0], rows[1]))
    assert row["hp_ziel"] == "48" and row["hp_bedarf"] == "46" and row["hp_boost_k"] == "2"
    assert row["hp_zuschlag_k"] == "0" and row["hp_wartet"] == "Wz" and row["hp_heizbetrieb"] == "1"
    assert row["hp_starts_h"] == "4" and "Boost +2 K" in row["hp_grund"]
    assert row["hp_vorlauf_entscheidung"].startswith("30 → 48")
    assert rows[0].index("hp_ziel") < rows[0].index("Wz ist")


# ------------------------------------------------------------------ Oberfläche
def test_ui_shows_the_surcharge_and_the_cycling_boost(tmp_path):
    from test_ui_v41 import js_run, plain

    t = {"heating": True, "flow": 50, "reason": "Wz hält 21,0 °C · Zuschlag +4 K (Flur, Wz kommen nicht hoch)",
         "base": 44, "load": None, "stall_k": 4, "stalled": ["Flur", "Wz"], "boost_k": 2, "waiting": ["Wz"],
         "cycling": {"cycling": True, "starts_h": 4, "run_min": 3}}
    r = js_run(tmp_path, f"out(boilerTarget({__import__('json').dumps(t)}, 'heatpilot'));")
    assert plain(r).endswith("Bedarf 44 °C · Zuschlag +4 K (kommt nicht hoch: Flur, Wz) · "
                             "Takt-Boost +2 K (Kessel taktet: 4 Starts/h, Läufe Ø 3 min)")


def test_tado_back_on_its_schedule_after_a_boost_is_no_hand_change(tmp_path):
    """0.50.2: Nach einem Tado-Boost (Timer) folgt Tado wieder seinem Zeitplan („auto“) – HeatPilot schreibt sofort
    seinen Wunsch zurück, statt es als Handänderung bis zum Phasenwechsel stehen zu lassen."""
    from test_control34 import MON, make, step as cstep, ts_of

    eng, ha, _, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 19.0)
    cstep(eng, loop, ts_of(MON, 12))
    want = ha.calls[-1][3]["temperature"]
    climate(eng, "climate.wz", 20.0, 17.0, mode="heat")  # von Hand: bleibt bis zum Phasenwechsel
    n = len(ha.calls)
    cstep(eng, loop, ts_of(MON, 12) + 60)
    assert len(ha.calls) == n
    climate(eng, "climate.wz", 20.0, 18.5, mode="auto")  # Timer vorbei: Tado-Zeitplan
    cstep(eng, loop, ts_of(MON, 12) + 120)
    assert ha.calls[-1] == ("climate", "set_temperature", "climate.wz", {"temperature": want})
