"""0.30.2: Ersparnis in gasfreien, sonnigen Stunden; Neuberechnung behält eigene Stunden."""

import asyncio
from datetime import datetime, timezone

from heatpilot.analysis import estimate_savings
from heatpilot.backfill import backfill_statistics
from heatpilot.config import Config
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.store import Store

from test_guards import house


def test_no_savings_when_the_house_was_unheated_and_sun_warmed_it():
    """Export 18.10.: 19 Tage weg, Heizung aus, Sonne wärmt nachmittags – vorher 70 % „gespart“."""
    start = int(datetime(2026, 9, 21, 0, 0).timestamp())
    rows, t_in = [], 20.0
    for h in range(7 * 24):
        hour = h % 24
        t_out = 12.0 + 6.0 * (10 <= hour <= 17)
        t_in += 0.25 if 11 <= hour <= 16 else -0.1           # Sonne nachmittags, sonst Auskühlen
        rows.append({"ts": start + h * 3600, "t_in": t_in, "t_out": t_out, "gas_kwh": 0.0,
                     "home_frac": 0.0, "dhw_frac": 0.0})
    res = estimate_savings(rows, house(), 18.0, 18.0, Config().is_night, p_max_kw=17)
    assert res.saved_kwh <= res.actual_kwh + 1e-9 and res.saved_kwh < 0.5


class StatsHA:
    def __init__(self, hours):
        self.hours = hours

    async def statistics(self, ids, start, end):
        iso = lambda h: datetime.fromtimestamp(h, timezone.utc).isoformat()
        return {"sensor.gas": [{"start": iso(h), "change": 0.2} for h in self.hours],
                "sensor.out": [{"start": iso(h), "mean": 5.0} for h in self.hours]}


def test_statistics_backfill_keeps_hours_built_from_own_samples(tmp_path):
    eng = Engine(Config(), Store(tmp_path / "b.db"), Discovered(gas="sensor.gas", outdoor=["sensor.out"]))
    now = int(datetime.now(timezone.utc).timestamp())
    own, other = now - now % 3600 - 40 * 86400, now - now % 3600 - 41 * 86400
    eng.store.add_sample(own + 60, {"t_out": 5.0})
    eng.store.add_sample(own + 180, {"t_out": 5.0})
    eng.store.add_hourly([(own, 5.0, 21.0, 1.5, 0.3, 1.0, 0.0, None, 0.4)])
    eng.store.commit()
    asyncio.run(backfill_statistics(StatsHA([own, other]), eng, 365))
    rows = {r["ts"]: r for r in eng.store.query("SELECT * FROM hourly")}
    assert rows[own]["dhw_frac"] == 0.3 and rows[own]["gas_kwh"] == 1.5   # eigene Stunde bleibt
    assert rows[other]["dhw_frac"] is None and rows[other]["gas_kwh"] > 0  # Statistik-Stunde kommt dazu


def test_long_term_hours_learn_without_summer_hot_water(tmp_path):
    """Langzeitstatistik kennt kein Warmwasser: das Sommerprofil (z. B. Legionellen So 13 Uhr) wird abgezogen."""
    eng = Engine(Config(), Store(tmp_path / "w.db"), Discovered())
    start = int(datetime(2026, 6, 1, 0, 0).timestamp())
    rows = []
    for i in range(5 * 7 * 24):  # fünf Sommerwochen
        ts = start + i * 3600
        d = datetime.fromtimestamp(ts)
        gas = 5.0 if (d.weekday(), d.hour) == (6, 13) else 0.1
        rows.append((ts, 20.0, 22.0, gas, None, None, None, None, None))
    eng.store.add_hourly(rows)
    eng.store.commit()
    sunday_13 = int(datetime(2026, 12, 6, 13, 0).timestamp())
    monday_9 = int(datetime(2026, 12, 7, 9, 0).timestamp())
    winter = lambda ts, frac=None: {"ts": ts, "gas_kwh": 6.0, "dhw_frac": frac, "dhw_energy_frac": None}
    assert eng.heating_gas(winter(sunday_13)) == 1.0
    assert abs(eng.heating_gas(winter(monday_9)) - 5.9) < 1e-9
    assert eng.heating_gas(winter(sunday_13, frac=0.0)) == 6.0  # eigene Messung: nichts abziehen


def test_no_hot_water_profile_without_enough_summer():
    eng = Engine(Config(), Store(":memory:"), Discovered())
    assert eng.dhw_base() is None


def test_cooling_learns_fast_and_slow_phase():
    """Abschalten um 22 Uhr: erst schnell (Luft, Heizkörper), dann langsam (Wände)."""
    import random
    from heatpilot.models import FAST_TAU_H, HouseModel, fast_decay
    rng = random.Random(3)
    m = HouseModel()
    k, g, a = 1 / 120, 0.05, 0.6
    for night in range(60):
        t_in, t_out = 21.0, 2.0 + rng.uniform(-3, 3)
        for n in range(1, 9):  # 8 gasfreie Stunden nach dem Abschalten
            rate = -k * (t_in - t_out) + g - a * fast_decay(n) + rng.gauss(0, 0.01)
            m.learn_cooling(t_in, t_out, rate, n)
            t_in += rate
    assert 90 < m.tau_cool_h < 160
    assert abs(m.cool_fast_k - a * FAST_TAU_H) < 0.25
    assert m.fast_drop(0) > m.fast_drop(3) > 0


def test_old_cooling_model_is_extended():
    from heatpilot.models import HouseModel
    old = HouseModel()
    d = old.to_dict()
    d["cool"]["n"], d["cool"]["theta"] = 2, [0.02, 0.1]
    d["cool"]["P"] = [[1.0, 0.0], [0.0, 1.0]]
    m = HouseModel.from_dict(d)
    assert m.cool.n == 3 and m.cool.theta[:2] == [0.02, 0.1] and m.cool_fast_k == 0.0
