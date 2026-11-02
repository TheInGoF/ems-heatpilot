"""0.74.2: Takten am 02.11. (12 h, 28 Starts) und Tado „aus“.

* A – kürzeste Sperre (5 min) nur, wenn die Abnahme reicht oder das Soll eines wartenden Raums eben stieg (heimkommen,
  hochdrehen, Nachtende) – dann ohne Haltezeit. Morgens lief der Kessel für die Küche allein alle 5 min für 2 min an.
* B – laufen die Brenner im Mittel nur wenige Minuten (die Räume nehmen 0,6 kW von 5,8 kW ab), 45 min Sperre.
* C – HeatPilots eigene Betriebsart zählt für Vorschau und Vorlauf: „Aus“ braucht keinen Vorlauf.
* Tado schaltet sich selbst aus (Arbeitszimmer, Katzenzimmer ab 08:01): HeatPilot stellt den Wunsch nach einer Karenz
  still wieder her – ohne Meldung, höchstens einmal je Karenz.
"""

from types import SimpleNamespace

from heatpilot import boiler_period as bp
from heatpilot import preview as pv
from heatpilot import room_modes
from heatpilot.controlloop import OFF_GRACE_S, Desired

from test_boiler41 import T0, climate as boiler_climate
from test_boiler_period48 import per_calls, setup, step as period_step
from test_control34 import MON, climate, make, step, ts_of
from test_preview37 import house as pv_house


def _target(**kw):
    base = dict(charging=False, heating=True, idle=False, flow=39, load=None, load_text="Abnahme: 1 Raum nimmt …",
                cycling={"cycling": False, "starts_h": 2, "run_min": 1.8}, waiting=["Küche"], stalled=[])
    return SimpleNamespace(**{**base, **kw})


# ------------------------------------------------------------------ A: kürzeste Sperre nur, wenn sie hilft
def test_single_waiting_room_without_take_off_keeps_a_long_sperre():
    t = _target(cycling={"cycling": True, "starts_h": 5, "run_min": 3.0})  # 02.11., 06:46–07:19
    value, reason = bp.want(t, 25)
    assert value == 45 and reason.startswith("Läufe im Mittel nur 3,0 min")  # bisher 5 min: 5 Starts in 30 min
    assert bp.want(_target(load=38), 25) == (bp.WAIT_MIN, "Küche warten auf Wärme – kürzeste Sperre")  # Abnahme reicht


def test_raised_setpoint_reacts_fast():
    seen, since = {}, {}
    assert bp.raised(seen, {"Wohnzimmer": 18.0, "Küche": 20.0}, since, T0) == {}  # erster Blick: kein Sprung
    now = bp.raised(seen, {"Wohnzimmer": 21.0, "Küche": 20.0, "Bad": None}, since, T0 + 60)  # heimkommen
    assert now == {"Wohnzimmer": T0 + 60} and "Bad" not in seen
    assert bp.raised(seen, {"Wohnzimmer": 21.0}, now, T0 + 60 + bp.RAISE_S) == {}  # danach wieder normal
    t = _target(waiting=["Wohnzimmer", "Küche"])
    fast = bp.quick(t, {"Wohnzimmer": T0})
    assert fast == ["Wohnzimmer"] and bp.want(t, 25, fast) == (5, "Wohnzimmer: Soll angehoben – kürzeste Sperre")
    assert bp.decide(5, 45, T0, last_ts=T0 - 60, urgent=True).write       # ohne Haltezeit …
    assert not bp.decide(5, 45, T0, last_ts=T0 - 60).write
    assert not bp.decide(45, 5, T0, last_ts=T0 - 60, urgent=True).write    # … aber nur zum Kürzeren


def test_raise_through_the_period_control(tmp_path):
    eng, ha, bc = setup(tmp_path, enough=False)  # Abnahme unbekannt, Wz wartet (20,5 °C bei 21 °C)
    period_step(bc, T0)
    assert per_calls(ha) == [] and bc.period.view["value"] == 25
    boiler_climate(eng, "climate.wz", 20.5, 22.0, hvac_action="heating")  # hochgedreht
    period_step(bc, T0 + 30)
    assert per_calls(ha) == [5] and bc.period.view["reason"] == "Wz: Soll angehoben – kürzeste Sperre"


# ------------------------------------------------------------------ B: längere Sperre bei kurzen Läufen
def test_short_runs_get_the_extra_long_sperre():
    assert bp.want(_target(waiting=[]), 25) == (45, "Läufe im Mittel nur 1,8 min – die Räume nehmen kaum Wärme ab: "
                                                     "längere Sperre")
    assert bp.want(_target(waiting=[]), 50)[0] == 50  # nie unter dem Grundwert
    assert bp.want(_target(waiting=[], stalled=["Küche"]), 25)[0] == 25  # ein Raum hängt am Soll: nicht länger
    assert bp.want(_target(waiting=[], cycling={"cycling": False, "starts_h": 1, "run_min": 12.0}), 25)[0] == 25
    assert bp.want(_target(waiting=[], load=38), 25)[0] == bp.SHORT_MIN  # Abnahme reicht: kurz wie bisher


# ------------------------------------------------------------------ C: eigene Betriebsart im Vorlauf
def test_own_mode_counts_for_the_flow():
    d = Desired(20.0, "komfort", "x", None)
    loop = SimpleNamespace(state={"climate.az": {"own": {"mode": "aus"}}})
    eng = SimpleNamespace(control_loop=loop)
    assert room_modes.planned(eng, "climate.az", d, T0) == room_modes.FROST_C
    loop.state["climate.az"]["own"] = {"mode": "boost", "sp": 24.0, "until": T0 + 600}
    assert room_modes.planned(eng, "climate.az", d, T0) == 24.0
    assert room_modes.planned(eng, "climate.az", d, T0 + 600) == 20.0  # Boost vorbei
    loop.state["climate.az"]["own"] = {"mode": "manuell", "sp": 22.0, "phase": "komfort"}
    assert room_modes.planned(eng, "climate.az", d, T0) == 22.0
    assert room_modes.planned(eng, "climate.az", d._replace(setpoint=18.0, phase="nacht"), T0) == 18.0
    assert loop.state["climate.az"]["own"]["mode"] == "manuell"  # die Vorschau ändert nichts
    assert room_modes.planned(SimpleNamespace(), "climate.az", d, T0) == 20.0


def test_room_off_through_heatpilot_needs_no_flow(tmp_path):
    eng, _, loop = pv_house(tmp_path, temp=19.0)  # 2 K unter Komfort: heizt auf
    now = ts_of(MON, 12)
    row = pv._Run(eng, now).step(pv._Run(eng, now).now)
    assert row["heating"] and row["sps"]["Wz"] == 21.0
    loop.state.setdefault("climate.wz", {})["own"] = {"mode": "aus"}  # über HeatPilot ausgeschaltet
    run_ = pv._Run(eng, now)
    row = run_.step(run_.now)
    assert row["sps"]["Wz"] == room_modes.FROST_C and not row["heating"]


# ------------------------------------------------------------------ Tado schaltet sich aus
def test_tado_off_is_switched_back_quietly_after_the_grace(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 19.0)
    step(eng, loop, ts_of(MON, 12))
    climate(eng, "climate.wz", 20.0, 21.0)
    step(eng, loop, ts_of(MON, 12, 5))
    n = len(ha.calls)
    climate(eng, "climate.wz", 20.0, None, mode="off")  # Tado steht von selbst auf „aus“
    step(eng, loop, ts_of(MON, 12, 10))
    info = step(eng, loop, ts_of(MON, 12, 10) + OFF_GRACE_S - 60)["climate.wz"]  # Karenz (Fenster lüften)
    assert len(ha.calls) == n and not info["manual"]
    step(eng, loop, ts_of(MON, 12, 10) + OFF_GRACE_S)
    assert ha.calls[n:] == [("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "heat"}),
                            ("climate", "set_temperature", "climate.wz", {"temperature": 21.0})]
    assert any("Thermostat stand auf aus" in r["reason"] for r in eng.store.control_log(2))
    step(eng, loop, ts_of(MON, 12, 10) + OFF_GRACE_S + 300)  # HA meldet noch „aus“: nicht gleich noch einmal
    assert len(ha.calls) == n + 2
    climate(eng, "climate.wz", 20.0, 21.0)  # wieder an: kein Rest
    step(eng, loop, ts_of(MON, 13))
    assert "off_since" not in loop.state["climate.wz"] and len(ha.calls) == n + 2
