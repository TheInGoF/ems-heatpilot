"""0.67: Gelernte Gewohnheiten je Wochentag und Stunde (schlafend / wach / abwesend), Gruppen im Sensor-Eimer,
„Strom & Aktivität“ heißt „Gewohnheiten erkennen“."""

import json
import subprocess
from types import SimpleNamespace

from heatpilot import entity_map, habits, web
from heatpilot.config import Config

from test_ui_v361 import script, source


def person(p_home):
    return SimpleNamespace(habit_hours=lambda: [[p_home(d, h) for h in range(24)] for d in range(7)])


def test_learned_night_wins_where_reliable_else_week_plan():
    cfg = Config()
    plan = [{"start": "23:30", "end": "05:00", "reliable_start": True, "reliable_end": True, "sure_end": "sicher"}] + \
        [{"start": None, "end": None, "reliable_start": False, "reliable_end": False}] * 6
    w = habits.week(cfg, plan, [])
    mo, di = w["days"][0], w["days"][1]
    assert mo["source"] == "gelernt" and mo["sure"] == "sicher" and mo["hours"][4] == "schlafend"
    assert mo["hours"][5] == "wach" and mo["hours"][23] == "schlafend" and mo["hours"][22] == "wach"
    assert di["source"] == "eingestellt" and di["bed"] == cfg.night_plan[1]["start"]
    assert w["persons"] is False and "abwesend" not in sum((d["hours"] for d in w["days"]), [])


def test_away_only_when_nobody_is_likely_home():
    work = lambda d, h: 0.05 if d < 5 and 8 <= h < 16 else 0.9  # noqa: E731
    w = habits.week(Config(), [], [person(work), person(lambda d, h: 0.1 if d < 5 and 9 <= h < 15 else 0.9)])
    mo, sa = w["days"][0]["hours"], w["days"][5]["hours"]
    assert mo[8] == "wach" and mo[10] == "abwesend" and mo[15] == "wach"  # 8 Uhr: einer ist noch da
    assert "abwesend" not in sa and w["persons"]


def test_groups_in_bucket_and_new_name():
    assert "group" in web.PICK_DOMAINS and "group" in entity_map.KINDS["activity"][0]
    assert entity_map.GROUPS["strom"][0] == "Gewohnheiten erkennen"


def test_overview_draws_one_row_per_day():
    js = script()
    assert '["anwesenheit", "Gewohnheiten"]' in js and "loadHabits()" in source(js, "drawOverview")
    j = {"days": [{"day": "Mo", "hours": ["schlafend"] * 6 + ["wach"] * 18, "source": "gelernt", "sure": "sicher"}],
         "persons": False, "nights": 3}
    code = "const esc = (s) => String(s);\n" + source(js, "HABIT_COLORS") + "\n" + source(js, "habitsHtml") + \
        f"\nprocess.stdout.write(habitsHtml({json.dumps(j)}));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    assert out.count('title="schlafend"') == 6 and out.count('title="wach"') == 18
    assert "gelernt, sicher" in out and "aus 3 Nächten" in out and "ohne Personen" in out
