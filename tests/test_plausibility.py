"""Was das Modell verwirrt, wird ignoriert: Zählernachträge, Platzhalter, Spitzen, Ausreißer."""

from heatpilot import plausibility
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.rls import RLS
from heatpilot.store import Store

HOUR = 1_790_002_800 - 1_790_002_800 % 3600


def rows(on_share, pow_=50.0, n=12):
    k = round(on_share * n)
    return [{"burner_on": int(i < k), "burner_pow": pow_ if i < k else 0.0} for i in range(n)]


def test_gas_hour_checks_against_burner_runtime():
    assert plausibility.gas_hour(3.0, rows(0.4), 18.0) == ("ok", 3.0)
    # Zählerstand von Hand nachgezogen: 20 kWh, Brenner lief nur 10 min -> Sprung
    assert plausibility.gas_hour(20.0, rows(1 / 6), 18.0)[0] == "spike"
    # Reed-Kontakt hat nicht gezählt: Brenner 45 min mit 60 %, Zähler 0
    assert plausibility.gas_hour(0.0, rows(0.75, 60.0), 18.0)[0] == "missed"
    # Brenner aus, Zähler steht: alles in Ordnung
    assert plausibility.gas_hour(0.0, rows(0.0), 18.0) == ("ok", 0.0)


def test_gas_series_without_burner_data():
    hours = {i * 3600: 1.5 + (i % 24) * 0.05 for i in range(24 * 10)}
    hours[24 * 8 * 3600] = 25.0  # Nachtrag
    clean, spikes = plausibility.gas_series(hours, None)
    assert list(spikes) == [24 * 8 * 3600] and clean[24 * 8 * 3600] is None
    assert sum(v is None for v in clean.values()) == 1


def engine(tmp_path):
    cfg = Config(boiler_nominal_kw=18.0)
    eng = Engine(cfg, Store(tmp_path / "p.db"), Discovered(rooms=[Room("Wz", "climate.wz")], gas="sensor.gas",
                                                            gas_unit="kWh"))
    return eng


def fill_hour(eng, start, gas0, step_kwh, burner_on, jump_at=None, jump=0.0):
    gas = gas0
    for i in range(13):  # inkl. erstem Wert der nächsten Stunde
        ts = start + i * 300
        if jump_at is not None and i == jump_at:
            gas += jump
        eng.store.add_sample(ts, {"gas": gas, "burner_on": burner_on, "burner_pow": 40.0 if burner_on else 0.0,
                                  "t_out": 2.0, "dhw": 0})
        eng.store.add_room_samples([(ts, "climate.wz", 20.5, 21.0, 50.0, None)])
        gas += step_kwh
    eng.store.commit()
    return gas


def test_hour_includes_last_five_minutes(tmp_path):
    eng = engine(tmp_path)
    fill_hour(eng, HOUR, 1000.0, 0.25, 1)
    eng.aggregate_hour(HOUR, learn=False)
    row = eng.store.query("SELECT gas_kwh FROM hourly WHERE ts=?", (HOUR,))[0]
    assert row["gas_kwh"] == 3.0  # 12 × 0,25 kWh – früher fehlte das letzte Stück (2,75)


def test_manual_counter_correction_is_not_learned_but_counted(tmp_path):
    eng = engine(tmp_path)
    before = eng.house.rls.updates
    fill_hour(eng, HOUR, 1000.0, 0.0, 0, jump_at=6, jump=55.0)  # Brenner aus, Zähler +55 kWh
    eng.aggregate_hour(HOUR, learn=True)
    row = eng.store.query("SELECT gas_kwh, gas_spike_kwh FROM hourly WHERE ts=?", (HOUR,))[0]
    assert row["gas_kwh"] is None and row["gas_spike_kwh"] == 55.0
    assert eng.house.rls.updates == before
    assert eng.ignored(days=10000)["gas_spikes"] == 1


def test_values_out_of_range_and_spikes_are_dropped():
    assert plausibility.in_range("flow", 3276.7) is None and plausibility.in_range("flow", 45.0) == 45.0
    assert plausibility.robust_outdoor([5.0, 5.5, -30.0]) == [5.0, 5.5]
    assert plausibility.robust_outdoor([5.0, -30.0]) == [5.0, -30.0]  # zwei Fühler: nicht entscheidbar
    f = plausibility.SpikeFilter()
    assert [f("r", t * 300, v) for t, v in enumerate([21.0, 21.1, 35.0, 21.2])] == [21.0, 21.1, None, 21.2]
    g = plausibility.SpikeFilter()  # echter Sprung (z. B. Sensor getauscht): nach Bestätigung gültig
    assert [g("r", t * 300, v) for t, v in enumerate([21.0, 25.5, 25.6, 25.7])] == [21.0, None, 25.6, 25.7]


def test_rls_clips_and_rejects_outliers():
    rls = RLS(n=2, lam=0.999, delta=10.0, theta=[0.0, 0.0], min_sigma=0.1)
    for i in range(400):
        x = [1.0, (i % 10) / 10]
        rls.update(x, 2.0 + 3.0 * x[1] + (0.05 if i % 2 else -0.05))
    theta = list(rls.theta)
    rls.update([1.0, 0.5], 1000.0)  # 1000-%-Spitze
    assert rls.rejected == 1 and rls.theta == theta
    sigma = rls.rmse
    rls.update([1.0, 0.5], 3.5 + 8 * sigma)  # deutlich daneben, aber nicht absurd: nur gekappt
    assert rls.clipped == 1 and abs(rls.theta[0] - theta[0]) < 4 * sigma
