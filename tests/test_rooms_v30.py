"""0.30: Raumlernen – Startunsicherheit je Parameter, langsames Vergessen, Ausreißer-Schwelle."""

from heatpilot import guards
from heatpilot.models import EXCITATION_DECAY, ROOM_GATE, ROOM_LAM, RoomModel
from heatpilot.rls import RLS


def test_single_warm_step_does_not_throw_heat_gain():
    """Arbeitszimmer im Export: ein Schritt +0,4 K bei kurz offenem Ventil hob a von 0,02 auf 0,24."""
    m = RoomModel("Arbeitszimmer")
    for _ in range(200):  # lange ohne Heizbetrieb, Raum hält sich
        m.learn(22.0, 21.98, 0.0, 10.0)
    m.learn(21.5, 21.9, 1.34, 10.0)
    assert m.a < 0.03
    assert not guards.check_room(m)["ok"]  # zu wenig Heizbetrieb – zu Recht


def test_room_rls_settings_survive_save_and_load():
    m = RoomModel("Wz")
    assert m.rls.lam == ROOM_LAM and m.rls.gate == ROOM_GATE
    back = RoomModel.from_dict(m.to_dict())
    assert back.rls.lam == ROOM_LAM and back.rls.gate == ROOM_GATE


def test_excitation_decays_independent_of_learning_memory():
    m = RoomModel("Wz")
    m.learn(20.0, 20.5, 10.0, 5.0)
    first = m.excitation
    m.learn(20.5, 20.4, 0.0, 5.0)
    assert abs(m.excitation - first * EXCITATION_DECAY) < 1e-9


def test_gate_widens_for_uncertain_direction():
    """Ein Schritt, der einen kaum gelernten Parameter stark anregt, ist kein Ausreißer."""
    r = RLS(n=2, lam=0.999, theta=[0.0, 0.0], P=[[1e-4, 0.0], [0.0, 100.0]], min_sigma=0.03)
    for _ in range(100):
        r.update([1.0, 0.0], 0.0, idle=[1])
    clipped = r.clipped
    r.update([0.0, 3.0], 0.9, idle=[0])  # erster Schritt mit dem zweiten Eingang
    assert r.clipped == clipped and r.rejected == 0
    assert r.theta[1] > 0.2
    r.update([1.0, 0.0], 5.0, idle=[1])  # grober Fehler in einer gut gelernten Richtung
    assert r.rejected == 1
