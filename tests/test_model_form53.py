"""0.53: Raummodell mit gelernter Form des Heizkörpers – Exponent n (Vorlauf − Raum) und Ventilkennlinie p.

Synthetischer Raum mit der Physik dT/dt = A·(Vorlauf − T)^n·Öffnung^p + B·(außen − T) + C: Das bis 0.52 lineare
Modell (Vorlauf − Raum)·Öffnung liegt nach dem Lernen bei hohem Vorlauf und kleiner Öffnung nachweislich daneben,
das neue nicht; die Form wird aus den Kandidaten richtig gewählt, ein linearer Raum bleibt linear. Dazu: alte Modelle
laden, umrechnen, neu lernen.
"""

import random

import pytest

from heatpilot.models import RoomModel
from heatpilot.rls import RLS
from heatpilot.room_model import FLOW_REF_K, FORMS, MIGRATE_VALVE, drive

T_OUT, B, C = 5.0, 0.03, 0.1


def physics(n: float, p: float = 0.5, a20: float = 1.5):
    """Raum: bei 20 K über Raum und offenem Ventil a20 K/h Heizkörperwirkung."""
    big_a = a20 / 20.0 ** n
    return lambda t, flow, v: big_a * max(flow - t, 0.0) ** n * v ** p + B * (T_OUT - t) + C


def steps(n: float, p: float = 0.5, count: int = 1500, seed: int = 1):
    """30-min-Schritte mit Minutenwerten: Vorlauf 30–65 °C, Öffnung 0–100 %, wie ein Thermostat um 20–22 °C."""
    rnd, f, t, out = random.Random(seed), physics(n, p), 20.0, []
    for _ in range(count):
        flow = rnd.uniform(30.0, 65.0)
        v = 0.0 if t > 22.0 else rnd.choice([0.0, 0.1, 0.25, 0.5, 1.0]) if t > 20.0 else rnd.choice([0.25, 0.5, 1.0])
        t0, parts = t, []
        for _ in range(30):
            parts.append((max(flow - t, 0.0), v))
            t += f(t, flow, v) / 60
        out.append((t0, t, parts))
    return out


def learn_new(n: float, p: float = 0.5) -> RoomModel:
    m = RoomModel("Raum")
    for t0, t1, parts in steps(n, p):
        lin = sum(w * v for w, v in parts) / len(parts)
        m.learn(t0, t1, lin, T_OUT, 0.5, parts=parts)
    return m


def learn_linear(n: float) -> RLS:
    """Das Modell bis 0.52: Eingang (Vorlauf − Raum)·Öffnung, sonst gleich."""
    r = RLS(n=3, lam=0.9995, theta=[0.02, 0.018, 0.0], P=[[0.05 ** 2, 0, 0], [0, 0.03 ** 2, 0], [0, 0, 0.3 ** 2]],
            min_sigma=0.03)
    for t0, t1, parts in steps(n):
        u = sum(w * v for w, v in parts) / len(parts)
        r.update([u * 0.5, (T_OUT - (t0 + t1) / 2) * 0.5, 0.5], t1 - t0)
    return r


@pytest.mark.parametrize("n_true", [1.3, 2.0])
def test_new_model_predicts_high_flow_and_small_opening_old_one_does_not(n_true):
    f, new, old = physics(n_true), learn_new(n_true), learn_linear(n_true)
    worst_new = worst_old = 0.0
    for flow, v, t in ((55.0, 0.2, 20.0), (60.0, 1.0, 20.0), (38.0, 1.0, 20.5), (45.0, 0.5, 21.0)):
        truth = f(t, flow, v)
        a, b, c = old.theta
        worst_old = max(worst_old, abs(a * (flow - t) * v + b * (T_OUT - t) + c - truth))
        worst_new = max(worst_new, abs(new.rate(t, flow, T_OUT, v) - truth))
    assert worst_old > 0.4    # linear: bei hohem Vorlauf bzw. kleiner Öffnung deutlich daneben (K/h)
    assert worst_new < 0.1    # neue Form: trifft überall


@pytest.mark.parametrize("n_true", [1.3, 2.0])
def test_form_is_chosen_from_the_data(n_true):
    m = learn_new(n_true)
    assert (m.n, m.p) == (n_true, 0.5)
    assert m.a_ref == pytest.approx(1.5 / 20.0, rel=0.1)  # Heizwirkung bei 20 K vergleichbar wie bisher


def test_linear_physics_keeps_the_old_linear_form():
    """Ein Ventil, das wirklich linear wirkt: die Form von bis 0.52 bleibt – keine Verzerrung durch Öffnung^0,5."""
    m = learn_new(1.0, p=1.0)
    assert (m.n, m.p) == (1.0, 1.0)
    f = physics(1.0, 1.0)
    for flow, v, t in ((55.0, 0.2, 20.0), (60.0, 1.0, 20.0), (38.0, 1.0, 20.5)):
        assert abs(m.rate(t, flow, T_OUT, v) - f(t, flow, v)) < 0.1


def test_closed_forms_agree_with_the_rate():
    m = learn_new(2.0)
    eq = m.equilibrium(45.0, T_OUT, 0.5)
    assert abs(m.rate(eq, 45.0, T_OUT, 0.5)) < 1e-6
    sp = 21.0
    flow = m.required_flow(sp, T_OUT, 0.8)
    assert abs(m.rate(sp, flow, T_OUT, 0.8)) < 1e-6
    # Zeit bis zum Ziel: mit der Rate in kleinen Schritten nachgerechnet
    t, h = 19.0, 0.0
    while t < 20.5:
        t += m.rate(t, 50.0, T_OUT, 0.5) * 0.001
        h += 0.001
    assert m.time_to_reach(19.0, 20.5, 50.0, T_OUT, 0.5) == pytest.approx(h, abs=0.01)
    # Öffnung für eine Heizleistung: die Umkehrung von heating
    v = m.valve_for(0.6, 45.0, sp)
    assert m.heating(45.0, sp, v) == pytest.approx(0.6)


def legacy(a=0.03, floor=False):
    keys = 7 if floor else 6
    theta = [a, *( [0.01] if floor else []), 0.02, 0.05, 0.0, 0.0, 0.0]
    r = RLS(n=keys, theta=theta, P=[[1e-3 if i == j else 0.0 for j in range(keys)] for i in range(keys)], updates=900)
    return {"room": "Wz", "floor": floor, "floor_lag_h": 3.0, "floor_u": 0.0, "excitation": 80.0, "rls": r.to_dict()}


@pytest.mark.parametrize("floor", [False, True])
def test_old_model_loads_and_keeps_its_effect(floor):
    m = RoomModel.from_dict(legacy(floor=floor))
    assert m.migrated and m.floor == floor and m.rls.updates == 900
    assert (m.n, m.p) == (1.0, 1.0) and m.rls.theta[:-1] == legacy(floor=floor)["rls"]["theta"] and m.wind_gain == 0.0  # gilt wie bisher (0.54: + Wind)
    for i, (n, p) in enumerate(FORMS):  # jede Form wirkt bei 20 K und halber Öffnung wie das alte Modell
        r = m.rlss[i]
        assert r.theta[0] * drive(FLOW_REF_K, MIGRATE_VALVE, n, p) == pytest.approx(0.03 * FLOW_REF_K * MIGRATE_VALVE)
        assert r.theta[1:-1] == legacy(floor=floor)["rls"]["theta"][1:] and r.theta[-1] == 0.0
    back = RoomModel.from_dict(m.to_dict())
    assert not back.migrated and back.rlss[5].theta == m.rlss[5].theta and (back.n, back.p) == (m.n, m.p)


def test_engine_relearns_old_models_from_history_and_keeps_a_backup(tmp_path):
    from heatpilot.store import Store
    from test_control34 import make, ts_of, MON

    t0 = ts_of(MON, 0)
    store = Store(tmp_path / "c.db")
    store.put("model.room.climate.wz", legacy())
    for k in range(48 * 12):  # zwei Tage, alle 5 min: heizt am Vormittag
        ts = t0 + k * 300
        on = 6 <= (k // 12) % 24 < 12
        store.add_sample(ts, {"flow": 50.0 if on else 30.0, "t_out": T_OUT, "pump_mod": 100.0 if on else 0.0,
                              "heating": int(on), "dhw": 0})
        store.add_room_samples([(ts, "climate.wz", 20.0 + (0.002 * k) % 1.5, 21.0, 60.0 if on else 0.0, None)])
    store.put("last_room_step", t0 + 47 * 3600)
    store.commit()
    store.db.close()
    eng, *_ = make(tmp_path)
    m = eng.rooms["climate.wz"]
    assert not m.migrated and m.rls.updates > 50             # aus der Historie neu gelernt
    assert eng.store.get("model.room.pre053.climate.wz") == legacy()  # alter Stand gesichert, nicht verworfen
