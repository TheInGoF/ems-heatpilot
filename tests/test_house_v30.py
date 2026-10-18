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
