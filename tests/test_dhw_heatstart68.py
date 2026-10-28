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


def test_write_lost_while_thermostat_was_away_is_written_again_once(tmp_path):
    """28.10.: Flur war 03:30–05:07 nicht erreichbar; das 20 °C von 04:30 kam nie an. Als er wieder 19 °C (den Wert
    von vorher) meldete, galt das als Hand – bis 10:04 blieb er auf 19 °C. Seit 0.68 wird einmal erneut geschrieben."""
    from test_control34 import MON, climate, make, step, ts_of

    eng, ha, _, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 17.0)
    t = ts_of(MON, 12)
    step(eng, loop, t)
    want = ha.calls[-1][3]["temperature"]
    assert want != 17.0
    for i in range(1, 4):  # nicht erreichbar: die Gnadenfrist läuft ab
        eng.states["climate.wz"] = {"state": "unavailable", "attributes": {}}
        step(eng, loop, t + 60 * i)
    climate(eng, "climate.wz", 20.0, 17.0)  # wieder da, mit dem Wert von vorher
    n = len(ha.calls)
    step(eng, loop, t + 300)
    assert len(ha.calls) > n and ha.calls[-1][3] == {"temperature": want}
    n = len(ha.calls)
    for i in range(6, 10):  # steht es danach immer noch dort, gilt es als Hand – kein Dauerschreiben
        step(eng, loop, t + 60 * i)
    assert len(ha.calls) == n


def test_30s_export_has_presence_per_person(tmp_path):
    """Die 30-s-Werte sollen alles enthalten, was ein großes Modell zum Nachtrainieren braucht – auch die Anwesenheit:
    je Person zu Hause, Entfernung, erwartete Ankunft und Quelle; im Haus Anwesende und Urlaub."""
    import csv
    import io

    from heatpilot import live_log
    from heatpilot.presence import PersonLearner
    from test_live_log492 import NOW, _eng

    eng = _eng(tmp_path)
    away = PersonLearner("person.ingo")
    away.home, away.dist_km = False, 20.04
    home = PersonLearner("person.b")
    home.home = True
    live_log.record(eng.store, NOW, {"flow": 40.0, "home_count": 1, "vacation": 0}, [],
                    {"person.ingo": away, "person.b": home})
    rows = list(csv.reader(io.StringIO(live_log.csv_text(eng, 1, NOW + 10))))
    row = dict(zip(rows[0], rows[1]))
    assert row["home_count"] == "1" and row["vacation"] == "0"
    assert row["person.ingo zuhause"] == "0" and row["person.ingo km"] == "20.04"
    assert row["person.b zuhause"] == "1" and row["person.b quelle"] == "zuhause" and row["person.b ankunft"]
    assert rows[0][-8:-4] == ["person.b zuhause", "person.b km", "person.b ankunft", "person.b quelle"]
