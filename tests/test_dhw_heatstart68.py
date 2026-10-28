"""0.68: Warmwasser nachts nicht mehr zu einer festen Zeit – fertig 5 min vor dem frühesten Heizstart der Räume, die
Ladedauer ab der aktuellen Speichertemperatur (Beispiel: Vorheizen ab 4:13, das Wasser braucht 23 min → ab 3:45)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from heatpilot import dhw_target as dt
from heatpilot.dhw import DhwModel

from test_dhw_v462 import NIGHT_END, _cfg

HEAT = datetime(2026, 10, 19, 4, 13)


def _rec(at, temp=38.5, start=HEAT):
    m = DhwModel()
    m.charge_rate_kh = 30.0  # 50 − 38,5 = 11,5 K → 23 min
    return m.recommend(at, True, None, _cfg(), hyst=-5, temp=temp, heat_start=start)


def test_ready_5_min_before_the_rooms_start_heating():
    assert _rec(datetime(2026, 10, 19, 3, 44))["hold"] == 40.0  # darf noch fallen
    assert _rec(datetime(2026, 10, 19, 3, 45))["target"] == 50   # 4:13 − 5 min − 23 min
    assert _rec(datetime(2026, 10, 19, 3, 0), temp=45.0)["hold"] == 40.0  # wärmerer Speicher: später


def test_without_heat_start_or_after_night_end_the_night_end_counts():
    at = NIGHT_END - timedelta(minutes=29)  # 4:01: 23 min + 5 min vor 4:30 reicht noch
    assert _rec(at, start=None)["hold"] == 40.0
    assert _rec(at, start=NIGHT_END + timedelta(hours=1))["hold"] == 40.0
    assert _rec(NIGHT_END - timedelta(minutes=28), start=None)["target"] == 50


def test_heat_start_from_the_rooms():
    now = datetime(2026, 10, 19, 2, 0)
    d = lambda phase, until: SimpleNamespace(phase=phase, until=until)  # noqa: E731
    assert dt.heat_start([d("nacht", HEAT), d("nacht", NIGHT_END), d("komfort", now)], now) == HEAT
    assert dt.heat_start([d("vorheizen", NIGHT_END), d("nacht", HEAT)], now) == now  # einer heizt schon
    assert dt.heat_start([d("komfort", None)], now) is None


def test_heating_stays_on_while_hot_water_charges():
    """27.10. 03:05: Der Kessel zeigt während der Ladung „Heizbetrieb an“; HeatPilot schaltete dreimal aus, wertete das
    als Fehlschlag und pausierte – das Vorheizen ab 03:30 fiel aus. Seit 0.68 bleibt der Heizbetrieb während der Ladung."""
    from heatpilot.boiler_rule import decide_heating
    d = decide_heating(False, True, 1000.0, charging=True)
    assert not d.write and "Warmwasser lädt" in d.why
    assert decide_heating(False, True, 1000.0, charging=True, protect="Grenze unter 35 °C").write  # Schutz geht vor
    assert decide_heating(False, True, 1000.0).write
