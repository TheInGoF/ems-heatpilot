"""0.54: Wind im Raummodell – Auskühlen = b + w · Wind, je Raum gelernt, nie negativ.

Regeln (jede hat einen Test): Ein zugiger Raum lernt w > 0, ein geschützter w ≈ 0; Modelle von vor 0.54 laden mit
w = 0 und behalten alles Gelernte; ohne Windwert lernt w nicht; die Steuerung rechnet mit dem Wind von jetzt
(``wind_now``) – bei Sturm braucht der Raum mehr Vorlauf und kühlt schneller aus."""

import random

from heatpilot.engine_sample import wind_ms
from heatpilot.room_model import ROOM_STEP_H, RoomModel


def _learn(model, w_true, steps=1500, seed=1):
    """Künstlicher Raum: Heizung an/aus, Wind 0–15 m/s; echtes Auskühlen 0,02 + w_true · Wind."""
    rnd, t = random.Random(seed), 20.0
    for i in range(steps):
        wind = rnd.uniform(0, 15)
        t_out = rnd.uniform(-5, 10)
        on = (i // 6) % 2 == 0
        warm = max(50.0 - t, 0.0) if on else 0.0
        rate = 0.02 * warm + (0.02 + w_true * wind) * (t_out - t) + 0.1
        t1 = t + rate * ROOM_STEP_H + rnd.gauss(0, 0.02)
        model.learn(t, t1, warm, t_out, ROOM_STEP_H, parts=[(warm, 1.0)], wind=wind)
        t = min(max(t1, 15.0), 24.0)
    return model


def test_drafty_room_learns_wind_sheltered_room_does_not():
    drafty, sheltered = _learn(RoomModel("Flur"), 0.002), _learn(RoomModel("Bad"), 0.0, seed=2)
    assert 0.0012 < drafty.wind_gain < 0.003
    assert sheltered.wind_gain < 0.0004


def test_b_and_predictions_use_the_wind_now():
    m = _learn(RoomModel("Flur"), 0.002)
    calm_b, calm_flow = m.b, m.required_flow(21.0, 0.0)
    m.wind_now = 15.0
    assert m.b > calm_b * 1.5                       # Sturm: kühlt deutlich schneller aus
    assert m.required_flow(21.0, 0.0) > calm_flow + 3  # braucht mehr Vorlauf zum Halten
    assert m.cool_down(21.0, 0.0, 3.0) < RoomModel.from_dict(m.to_dict()).cool_down(21.0, 0.0, 3.0)  # wind_now nie gespeichert


def test_without_wind_values_w_stays_put():
    m = RoomModel("Kü")
    for i in range(300):
        m.learn(20.0, 20.0 - 0.1, 0.0, 5.0, ROOM_STEP_H, parts=[(0.0, 0.0)], wind=None)
    assert m.wind_gain == 0.0


def test_old_models_load_with_zero_wind_and_keep_what_they_learned():
    m = _learn(RoomModel("Wz"), 0.0, steps=400)
    d = m.to_dict()
    for r in d["rlss"]:  # Stand von 0.53: ein Parameter weniger
        r["theta"] = r["theta"][:-1]
        r["P"] = [row[:-1] for row in r["P"][:-1]]
        r["n"] -= 1
    old = RoomModel.from_dict(d)
    assert old.wind_gain == 0.0 and old.rls.n == m.rls.n
    assert abs(old.a - m.a) < 1e-9 and abs(old.rls.theta[1] - m.rls.theta[1]) < 1e-9


def test_wind_unit_conversion():
    assert wind_ms(36, "km/h") == 10.0 and wind_ms(10, "m/s") == 10.0 and wind_ms(36, None) == 10.0
    assert wind_ms(10, "kn") == 5.14 and wind_ms(None, "km/h") is None and wind_ms(-1, "m/s") is None
