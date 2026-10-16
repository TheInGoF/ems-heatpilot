"""Vorlauf gegen Takten: HeatPilot findet selbst heraus, ob höherer Vorlauf etwas bringt."""

from heatpilot import flowstudy
from heatpilot.store import Store

NOW = 1_790_000_000 - 1_790_000_000 % 3600


def fill(tmp_path, gas_low, gas_high, starts_low=3, starts_high=1, ret_high=48.0, variety=True):
    store = Store(tmp_path / "f.db")
    t0 = NOW - 60 * 86400
    for h in range(60 * 24):
        hour = t0 + h * 3600
        high = variety and h % 2 == 0
        flow, ret = (60.0, ret_high) if high else (45.0, 38.0)
        starts = starts_high if high else starts_low
        store.add_hourly([(hour, 2.0, 21.0, (gas_high if high else gas_low) * 19.0, 0.0, 1.0)])
        for k in range(12):  # 5-min-Werte: Brenner die Hälfte der Zeit an
            store.add_sample(hour + k * 300, {"flow": flow, "ret": ret, "burner_on": int(k < 6), "dhw": 0})
        for k in range(starts):  # Starts als Ereignisse
            store.add_burner_event(hour + k * 600, True)
            store.add_burner_event(hour + k * 600 + 300, False)
    store.commit()
    return store


def test_higher_flow_pays_off_when_it_saves_gas(tmp_path):
    res = flowstudy.study(fill(tmp_path, gas_low=0.20, gas_high=0.18), NOW)
    band = next(b for b in res["bands"] if b["band"] == "0 bis 5 °C")
    f = band["finding"]
    assert f["low"] == "45–50" and f["high"] == "60–65" and f["gas_diff"] < -0.04
    assert f["starts_high"] < f["starts_low"]
    assert res["verdict"]["kind"] == "lohnt"


def test_higher_flow_is_waste_when_it_costs_more(tmp_path):
    res = flowstudy.study(fill(tmp_path, gas_low=0.18, gas_high=0.20, ret_high=58.0), NOW)
    assert res["verdict"]["kind"] == "verschwendung"
    assert "kondensiert kaum" in res["verdict"]["text"]


def test_too_little_variety_suggests_a_test(tmp_path):
    res = flowstudy.study(fill(tmp_path, 0.2, 0.2, variety=False), NOW)
    assert res["verdict"]["kind"] == "zu_wenig" and "nur, wenn du sicher bist" in res["verdict"]["text"]
