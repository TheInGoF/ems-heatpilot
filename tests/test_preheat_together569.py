"""0.56.9: Vorheizen gemeinsam beginnen und nie kürzer als das Raummodell – Fall 25.10.: Kessel ab 17:25 auf 55 °C,
die Räume erst ab 18:30 auf ihrem Vorheiz-Soll, bei der Ankunft 18:45 alle zu kalt."""

from datetime import datetime, timedelta

from heatpilot import heatup
from heatpilot.controlloop import Desired

NOW = datetime(2026, 10, 25, 17, 30)
GOAL = datetime(2026, 10, 25, 18, 45)


def _want(phase_at_start):
    return lambda c, at: phase_at_start[c]


def test_rooms_that_would_start_soon_join_the_first():
    ds = {"a": Desired(20.0, "vorheizen", "Ankunft", GOAL),
          "b": Desired(16.0, "abwesend", "niemand da", NOW + timedelta(minutes=30)),   # finge 18:00 selbst an
          "c": Desired(16.0, "abwesend", "niemand da", NOW + timedelta(hours=2))}      # zu weit weg: wartet
    later = {"b": Desired(19.5, "vorheizen", "Ankunft", GOAL), "c": Desired(19.0, "vorheizen", "Ankunft", GOAL)}
    out = heatup.start_together(ds, _want(later), NOW)
    assert out["b"].phase == "vorheizen" and out["b"].setpoint == 19.5 and "gemeinsam" in out["b"].reason
    assert out["c"].phase == "abwesend" and out["a"] is ds["a"]


def test_other_goal_or_nobody_preheating_changes_nothing():
    ds = {"a": Desired(20.0, "komfort", "da", None), "b": Desired(16.0, "abwesend", "x", NOW + timedelta(minutes=10))}
    assert heatup.start_together(ds, _want({"b": Desired(19.5, "vorheizen", "Ankunft", GOAL)}), NOW) == ds
    ds["a"] = Desired(20.0, "vorheizen", "Ankunft", GOAL)
    other = {"b": Desired(19.5, "vorheizen", "Nachtende", GOAL + timedelta(hours=10))}
    assert heatup.start_together(ds, _want(other), NOW)["b"].phase == "abwesend"


def test_learned_speed_never_shortens_the_lead():
    from pathlib import Path
    src = (Path(__file__).parents[1] / "ems_heatpilot/heatpilot/controlloop.py").read_text()
    assert "h / min(speed, 1.0)" in src
