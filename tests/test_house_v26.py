"""0.26: Hausmodell mit Fremdwärme, gemessener Auskühlzeit, mittiger Ableitung, Heizperiode."""

import math
import tempfile
from datetime import datetime
from pathlib import Path

from heatpilot import guards, plausibility, weekly
from heatpilot.config import Config
from heatpilot.discovery import Discovered
from heatpilot.engine import Engine
from heatpilot.models import HouseModel
from heatpilot.store import HOURLY_COLS, Store


def _engine(rows) -> Engine:
    st = Store(Path(tempfile.mkdtemp()) / "h.db")
    st.add_hourly([tuple(r.get(c) for c in HOURLY_COLS) for r in rows])
    st.commit()
    return Engine(Config(), st, Discovered())


def _synthetic(days=40, H=0.15, free=0.9, tau=60.0, C=4.0):
    """Tagsüber heizen (Gas = H·ΔT − Fremdwärme), nachts 22–06 Uhr gasfrei auskühlen."""
    t0 = int(datetime(2026, 1, 5).timestamp())
    rows, t_in = [], 21.0
    for i in range(days * 24):
        ts = t0 + i * 3600
        hr = datetime.fromtimestamp(ts).hour
        t_out = 2.0 + 3.0 * math.sin(2 * math.pi * hr / 24)
        night = hr >= 22 or hr < 6
        if night:
            excess = free / H
            t_new = t_out + excess + (t_in - t_out - excess) * math.exp(-1 / tau)
            gas = 0.0
        else:
            t_new = 21.0
            gas = max(H * (t_new - t_out) - free + C * (t_new - t_in), 0.0)
        rows.append({"ts": ts, "t_out": t_out, "t_in": (t_in + t_new) / 2, "gas_kwh": gas, "dhw_frac": 0.0})
        t_in = t_new
    return rows


def test_free_heat_is_kept_not_clamped():
    h = HouseModel()
    h.rls.theta, h.rls.updates = [0.15, 3.0, -0.9], 500
    assert h.offset_kw == -0.9 and h.base_kw == 0.0 and abs(h.free_heat_kw - 0.9) < 1e-9
    assert abs(h.demand_kw(20, -12) - (0.15 * 32 - 0.9)) < 1e-9
    assert h.demand_kw(20, 16) == 0.0  # unter der Heizgrenze (6 K) kein Gas
    assert abs(h.heating_limit_k - 6.0) < 1e-9
    assert guards.check_house(h)["ok"]


def test_cooling_time_constant_is_learned_from_gas_free_nights():
    eng = _engine(_synthetic())
    for r in eng.store.query("SELECT ts FROM hourly ORDER BY ts"):
        eng.learn_house_hour(r["ts"])
    h = eng.house
    assert h.tau_cool_h is not None and 45 <= h.tau_h <= 80, h.tau_h
    # H/Offset nur grob: die stufige Morgen-Aufheizung der Testdaten ist gröber als echte Stundenmittel
    assert 0.1 <= h.h_kw_per_k <= 0.3 and h.offset_kw < 0


def test_summer_hours_are_not_learned():
    rows = []
    t0 = int(datetime(2026, 7, 1).timestamp())
    for i in range(72):  # warme Tage (24-h-Mittel 20 °C), kühle Nachtstunden mit 13 °C
        hr = (i % 24)
        rows.append({"ts": t0 + i * 3600, "t_out": 13.0 if hr < 4 else 21.5, "t_in": 23.0, "gas_kwh": 0.2, "dhw_frac": 0.0})
    eng = _engine(rows)
    for r in rows:
        eng.learn_house_hour(r["ts"])
    assert eng.house.rls.updates == 0


def test_dhw_hours_never_teach_heating():
    rows = [{"ts": 3600 * i, "t_out": 0.0, "t_in": 21.0, "gas_kwh": 9.0 if i % 3 == 1 else 3.0,
             "dhw_frac": 0.25 if i % 3 == 1 else 0.0} for i in range(1, 200)]
    eng = _engine(rows)
    for r in rows:
        eng.learn_house_hour(r["ts"])
    assert abs(eng.house.demand_kw(21.0, 0.0) - 3.0) < 0.3  # die 9-kWh-Ladestunden zählen nicht


def test_weekly_uses_energy_share_of_dhw():
    t0 = int(datetime(2026, 1, 5).timestamp())
    rows = [{"ts": t0 + i * 3600, "t_out": 0.0, "t_in": 21.0, "gas_kwh": 4.0, "dhw_frac": 0.25,
             "dhw_energy_frac": 1.0 if i % 24 == 4 else 0.0, "home_frac": 1.0} for i in range(14 * 24)]
    eng = _engine(rows)
    out = weekly.collect(eng.store, t0 + 14 * 86400, 14)
    assert out["metrics"]["dhw"]["grid"][0][4] == 4.0 and out["metrics"]["heat"]["grid"][0][4] == 0.0


def test_gas_series_keeps_real_peaks_after_quiet_days():
    quiet = {i * 3600: 0.3 for i in range(200)}
    clean, spikes = plausibility.gas_series({**quiet, 200 * 3600: 9.0}, 18.0)
    assert clean[200 * 3600] == 9.0 and not spikes  # Legionellen-/Aufheizstunde ist echt
    clean, spikes = plausibility.gas_series({**quiet, 200 * 3600: 25.0}, 18.0)
    assert 200 * 3600 in spikes
    clean, spikes = plausibility.gas_series({**quiet, 200 * 3600: -5.0}, 18.0)
    assert clean[200 * 3600] is None and not spikes  # gesunkener Zähler zählt nicht
