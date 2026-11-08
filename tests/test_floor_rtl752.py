"""Fußboden am RTL-Ventil (0.75.2): Am Soll macht das RTL-Ventil selbst fast dicht – das Bad hält den Heizbetrieb
nicht an. Unter dem Soll bleibt er an; die Grenze (``floor_max_flow``) gilt weiter."""

import pytest

from heatpilot import preview as pv
from heatpilot.config import Config
from heatpilot.discovery import Room
from heatpilot.values import local

from test_control34 import MON, climate, slow_room, ts_of, wz
from test_preview37 import house


def _bad():
    return Room("Bad", "climate.bad", managed=True, comfort=22.0, platform="homekit_controller")


def _house(tmp_path, kind: str, bad_temp: float):
    cfg = Config(control_mode="heatpilot", floor_heating_rooms=["climate.bad"],
                 rooms=[{"climate": "climate.bad", "floor_type": kind, "floor_max_flow": 55.0}])
    eng, _, _ = house(tmp_path, cfg=cfg, temp=22.5, rooms=[wz(), _bad()])  # Wz über dem Soll
    climate(eng, "climate.bad", bad_temp, 22.0)
    eng.rooms["climate.bad"] = slow_room()
    ts = ts_of(MON, 12)
    row = pv._Run(eng, ts).step(local(ts))  # jetzt: der erste Schritt
    return {"heating": row["heating"], "flow_reason": row["why"]}


def test_rtl_floor_at_setpoint_does_not_keep_heating_on(tmp_path):
    h0 = _house(tmp_path, "rtl", 22.0)
    assert h0["heating"] is False and h0["flow_reason"].startswith("kein Raum braucht Wärme")


@pytest.mark.parametrize("kind", ["direkt", "mischer"])
def test_other_floor_at_setpoint_keeps_heating_on(tmp_path, kind):
    h0 = _house(tmp_path, kind, 22.0)
    assert h0["heating"] is True and h0["flow_reason"] == "Bad hält 22,0 °C (Fußbodenheizung) – Heizbetrieb bleibt an"


def test_rtl_floor_below_setpoint_keeps_heating_on(tmp_path):
    h0 = _house(tmp_path, "rtl", 20.0)
    assert h0["heating"] is True and h0["flow_reason"].startswith("Bad unter Soll (Fußbodenheizung)")
