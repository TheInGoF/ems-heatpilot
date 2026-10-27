"""0.62: Override „nur jetzt“ (Konzeptpapier, Abschnitt 7) – Nacht, Anwesend, Abwesend für das ganze Haus; endet am
nächsten Schaltpunkt, wenn der Plan von selbst so weit ist oder die Lage vorbei ist (jemand kommt heim, alle weg)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from heatpilot import card_bridge, heat_actions, override
from heatpilot.controlloop import Desired

T0 = 1_793_000_000


class Store(dict):
    def put(self, k, v):
        self[k] = v


def loop(store=None):
    cfg = SimpleNamespace(night_temp=18.0, away_temp=17.0)
    return SimpleNamespace(store=store if store is not None else Store(), eng=SimpleNamespace(cfg=cfg))


ROOMS = [SimpleNamespace(climate="climate.a", comfort=21), SimpleNamespace(climate="climate.b", comfort=20)]


def natural(phase="komfort", until_h=3):
    until = datetime.fromtimestamp(T0).astimezone() + timedelta(hours=until_h)
    return {"climate.a": Desired(21.0, phase, "jemand zu Hause", until),
            "climate.b": Desired(20.0, phase, "jemand zu Hause", until + timedelta(hours=1))}


def test_night_until_next_switchpoint():
    lp = loop()
    override.set_mode(lp.store, "nacht", T0)
    out = override.apply(lp, T0, natural(), SimpleNamespace(home_count=2), ROOMS)
    assert [(d.setpoint, d.phase, d.reason) for d in out.values()] == [(18.0, "nacht", "nur jetzt: Nacht")] * 2
    ov = override.get(lp.store)
    assert ov["until"] == T0 + 3 * 3600 and ov["home"] == 2  # frühester Wechsel des normalen Plans
    assert override.apply(lp, T0 + 3 * 3600, natural(), SimpleNamespace(home_count=2), ROOMS) == natural()
    assert override.get(lp.store) is None


def test_ends_when_plan_gets_there_or_situation_is_over():
    lp = loop()
    override.set_mode(lp.store, "nacht", T0)
    assert override.apply(lp, T0, natural("nacht"), SimpleNamespace(home_count=2), ROOMS) == natural("nacht")
    assert override.get(lp.store) is None  # der Plan ist von selbst Nacht
    override.set_mode(lp.store, "abwesend", T0)
    out = override.apply(lp, T0, natural(), SimpleNamespace(home_count=0), ROOMS)
    assert out["climate.a"].setpoint == 17.0 and out["climate.a"].phase == "abwesend"
    assert override.apply(lp, T0 + 60, natural(), SimpleNamespace(home_count=1), ROOMS) == natural()  # heimgekommen
    override.set_mode(lp.store, "anwesend", T0)
    out = override.apply(lp, T0, natural("nacht"), SimpleNamespace(home_count=1), ROOMS)
    assert out["climate.b"].setpoint == 20.0 and out["climate.b"].phase == "komfort"
    assert override.apply(lp, T0 + 60, natural("nacht"), SimpleNamespace(home_count=0), ROOMS) == natural("nacht")


def test_set_off_unknown_and_sensor():
    lp = loop()
    assert override.sensor(lp.store)[1] == "auto"
    override.set_mode(lp.store, "abwesend", T0)
    eid, state, attrs = override.sensor(lp.store)
    assert (eid, state, attrs["since"]) == ("sensor.heatpilot_modus", "abwesend", T0)
    assert override.set_mode(lp.store, "aus") is None and override.get(lp.store) is None
    with pytest.raises(ValueError):
        override.set_mode(lp.store, "party")
    assert "override" in heat_actions.ACTIONS and "override" in card_bridge.CARD_ACTIONS


async def _run(app, body):
    return await heat_actions.run(app, "override", body)


def test_action_sets_mode():
    import asyncio
    lp = loop()
    app = SimpleNamespace(control=None, eng=SimpleNamespace(control_loop=lp))
    res = asyncio.run(_run(app, {"mode": "nacht"}))
    assert res["ok"] and override.get(lp.store)["mode"] == "nacht"
