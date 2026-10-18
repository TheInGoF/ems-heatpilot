"""0.34 Gegenprüfung: nachgestellte Funde der Regelschleife, des Lernens und der Raumtabelle.

Je Fund ein Test; alle schlagen auf dem Stand vor der Behebung fehl.
"""

import asyncio
import re
from pathlib import Path

from heatpilot import schedule
from heatpilot.config import Config
from heatpilot.control import Control
from heatpilot.controlloop import ControlLoop
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import KV_MANAGED_UNTIL, Engine, local
from heatpilot.store import Store

from test_control import run
from test_control34 import MON, at, climate, make, step, ts_of, wz

TUE = (2026, 10, 13)
ROOT = Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot"


def follow(eng, ha):
    """HA spiegelt die letzten Schreibvorgänge zurück (wie der echte Zustand)."""
    for _, svc, eid, data in ha.calls[-2:]:
        st = eng.states.setdefault(eid, {"state": "heat", "attributes": {}})
        if svc == "set_temperature":
            st["attributes"]["temperature"] = data["temperature"]
        elif svc == "set_hvac_mode":
            st["state"] = data["hvac_mode"]


def temps(ha, start=0):
    return [a[3]["temperature"] for a in ha.calls[start:] if a[1] == "set_temperature"]


# [1] Handänderung/„aus“ gilt bis zum Phasenwechsel – auch wenn das Wunsch-Soll dabei gleich bleibt
def test_phase_change_ends_manual_change_even_with_same_setpoint(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 19.0)
    step(eng, loop, ts_of(MON, 22)); follow(eng, ha)          # nacht 18
    step(eng, loop, ts_of(TUE, 3, 35)); follow(eng, ha)       # vorheizen 21
    climate(eng, "climate.wz", 20.0, 23.0)                     # Nutzer dreht auf 23
    info = step(eng, loop, ts_of(TUE, 4, 0))["climate.wz"]
    assert info["manual"] and info["manual_until"] == at(TUE, 4, 30).isoformat()
    n = len(ha.calls)
    info = step(eng, loop, ts_of(TUE, 4, 35))["climate.wz"]   # komfort, Wunsch weiterhin 21
    assert info["phase"] == "komfort" and not info["manual"]
    assert ha.calls[n:] == [("climate", "set_temperature", "climate.wz", {"temperature": 21.0})]
    follow(eng, ha)
    step(eng, loop, ts_of(TUE, 12))
    assert len(ha.calls) == n + 1 and ctl.target("climate.wz") == 21.0
    # dasselbe mit „aus“ von Hand während des Vorheizens: zum Phasenwechsel wieder ein und auf den Wunsch
    eng, ha, ctl, loop = make(tmp_path / "off")
    climate(eng, "climate.wz", 20.0, 19.0)
    step(eng, loop, ts_of(MON, 22)); follow(eng, ha)
    step(eng, loop, ts_of(TUE, 3, 35)); follow(eng, ha)
    climate(eng, "climate.wz", 20.0, None, mode="off")
    n = len(ha.calls)
    step(eng, loop, ts_of(TUE, 4, 0))
    assert len(ha.calls) == n  # bis zum Phasenwechsel bleibt „aus“
    step(eng, loop, ts_of(TUE, 4, 35))
    assert ha.calls[n:] == [("climate", "set_hvac_mode", "climate.wz", {"hvac_mode": "heat"}),
                            ("climate", "set_temperature", "climate.wz", {"temperature": 21.0})]


# [2] Boost läuft in der Pause ab: Soll von vorher zurück, nicht der Wunschzustand
def test_boost_expiring_during_pause_restores_previous_setpoint(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 19.0)
    step(eng, loop, ts_of(MON, 12)); follow(eng, ha)          # Wunsch 21 geschrieben
    climate(eng, "climate.wz", 20.0, 22.5)                     # von Hand auf 22,5
    step(eng, loop, ts_of(MON, 12, 5))
    run(ctl.boost(["climate.wz"], 24, 30)); follow(eng, ha)
    assert ctl.boosts["climate.wz"]["before"] == 22.5
    loop.pause(4)
    ctl.boosts["climate.wz"]["end"] = ts_of(MON, 12, 30)
    n = len(ha.calls)
    info = step(eng, loop, ts_of(MON, 12, 35))["climate.wz"]
    assert loop.paused(ts_of(MON, 12, 35)) and info["paused"]
    assert temps(ha, n) == [22.5] and "climate.wz" not in ctl.boosts  # Soll von vorher, nicht 21
    assert loop.state["climate.wz"]["last_written"] == 21.0
    follow(eng, ha)
    step(eng, loop, ts_of(MON, 13))
    assert len(ha.calls) == n + 1  # Pause: weiterhin nichts


# [3] Ende eines lokalen Boosts über einen Phasenwechsel hinweg: genau ein Schreibvorgang
def test_local_boost_end_writes_once_over_phase_change(tmp_path):
    eng, ha, ctl, loop = make(tmp_path)
    climate(eng, "climate.wz", 20.0, 19.0)
    step(eng, loop, ts_of(MON, 20, 50)); follow(eng, ha)      # komfort 21
    run(ctl.boost(["climate.wz"], 24, 60)); follow(eng, ha)
    ctl.boosts["climate.wz"]["end"] = ts_of(MON, 21, 50)
    for h, m in ((21, 0), (21, 30)):                          # Phasenwechsel auf nacht während des Boosts
        info = step(eng, loop, ts_of(MON, h, m))["climate.wz"]
        assert info["boost"] and info["phase"] == "nacht"
    n = len(ha.calls)
    info = step(eng, loop, ts_of(MON, 21, 55))["climate.wz"]
    assert ha.calls[n:] == [("climate", "set_temperature", "climate.wz", {"temperature": 18.0})]
    assert eng.store.control_log(1)[0]["reason"] == "Boost zu Ende"
    assert loop.state["climate.wz"]["last_written"] == 18.0 and not info["boost"]
    follow(eng, ha)
    step(eng, loop, ts_of(MON, 22))
    assert len(ha.calls) == n + 1


# [4] Wettlauf: nach dem Neuaufbau (Schalter beobachten) schreibt ein laufendes apply() der alten Schleife nichts mehr
class SlowHA:
    def __init__(self):
        self.calls, self.gate = [], asyncio.Event()

    async def call(self, payload, timeout=0):
        await self.gate.wait()
        self.calls.append((payload["service"], payload["target"]["entity_id"], payload["service_data"]))


def test_stopped_loop_does_not_write_after_switch_to_observe(tmp_path):
    rooms = [wz(), Room("Bad", "climate.bad", managed=True, comfort=22.0, platform="homekit_controller")]

    def fresh(name):
        found = Discovered(rooms=rooms)
        eng = Engine(Config(control_mode="heatpilot"), Store(tmp_path / f"{name}.db"), found)
        ha = SlowHA()
        loop = ControlLoop(eng, Control(ha, eng.store, found.rooms, states=eng.states))
        eng.control_loop = loop
        climate(eng, "climate.wz", 20.0, 19.0)
        climate(eng, "climate.bad", 20.0, 19.0)
        eng.tick(ts_of(MON, 12))
        assert [p[0] for p in loop.pending] == ["climate.wz", "climate.bad"]
        return eng, ha, loop

    def rebuild(eng, ha, loop):
        """Wie main.build: neue Engine/Control/Schleife mit Schalter beobachten, alte Schleife außer Dienst."""
        eng2 = Engine(Config(), eng.store, eng.found)
        eng2.states.update(eng.states)
        eng2.control_loop = ControlLoop(eng2, Control(ha, eng.store, eng.found.rooms, states=eng2.states))
        loop.stop()
        eng2.tick(ts_of(MON, 12, 5))
        assert eng.store.get("control.state") == {}
        return eng2

    async def in_flight():
        eng, ha, loop = fresh("a")
        task = asyncio.create_task(loop.apply())
        await asyncio.sleep(0.01)                               # erster Aufruf wartet auf HA
        rebuild(eng, ha, loop)
        ha.gate.set()
        await task
        return ha.calls, eng.store.get("control.state")

    calls, kv = asyncio.run(in_flight())
    assert calls == [("set_temperature", "climate.wz", {"temperature": 21.0})]  # nur der schon laufende Aufruf
    assert kv == {}  # der gelöschte Zustand wird nicht wieder gefüllt

    async def before_start():
        eng, ha, loop = fresh("b")
        rebuild(eng, ha, loop)
        ha.gate.set()
        await loop.apply()
        return ha.calls

    assert asyncio.run(before_start()) == []
    # auch ohne stop(): steht der eigene Schalter auf beobachten, wird nichts geschrieben
    eng, ha, loop = fresh("c")
    eng.cfg.control_mode = "beobachten"
    ha.gate.set()
    run(loop.apply())
    assert ha.calls == []
    # main.build stellt die alte Schleife außer Dienst
    assert "old.control_loop.stop()" in (ROOT / "main.py").read_text(encoding="utf-8")


# [5] Bei „HeatPilot steuert“ wird nichts aus Tado gelernt – auch nicht rückwirkend nach dem Zurückschalten
def test_no_tado_learning_while_managing_nor_from_managed_period(tmp_path):
    cfg = Config(control_mode="heatpilot")
    found = Discovered(rooms=[wz()], persons=[])
    eng = Engine(cfg, Store(tmp_path / "c.db"), found)
    t0 = ts_of((2026, 10, 1), 0)
    rows = []
    for k in range(12 * 288):  # 12 Tage Raumwerte, wie HeatPilot sie selbst erzeugt (21 tags, 18 nachts)
        t = t0 + k * 300
        rows.append((t, "climate.wz", 20.0, 18.0 if cfg.is_night_at(local(t)) else 21.0, 50.0, None))
        eng.store.add_sample(t, {"home_count": 1})
    eng.store.add_room_samples(rows)
    eng.store.commit()
    now = t0 + 12 * 86400
    assert schedule.learn(eng.store, now, set(), {"climate.wz"}, [])["nights"] >= schedule.MIN_NIGHTS
    assert schedule.learn(eng.store, now, set(), {"climate.wz"}, [], since_ts=now)["nights"] == 0
    eng._schedule_ts = 0
    eng.update_schedule(now)
    assert eng.store.get("schedule.learned") is None and eng.schedule_learned is None
    assert eng.store.get(KV_MANAGED_UNTIL) == now
    assert eng.summary()["schedule"]["learning"] is False
    # zurück auf beobachten (neue Engine wie main.build): HeatPilots eigene Zeiten gelten nicht als Tado-Gewohnheit
    eng2 = Engine(Config(), eng.store, found)
    eng2.update_schedule(now + 300)
    assert eng2.schedule_learned["nights"] == 0
    assert eng2.eff.as_dict()["source"]["night_start"] == "eingestellt"


# [6] Raumtabelle: schon gewählter Raum ohne Komfortwert zeigt 21 (wie das Backend), nicht das Soll der Nacht
def test_room_table_default_comfort_for_existing_rooms_is_21():
    js = re.search(r"<script>(.*)</script>", (ROOT / "static" / "index.html").read_text(encoding="utf-8"), re.S).group(1)
    rows = js.split("function roomRows()")[1].split("function learnedHint")[0]
    assert "r.comfort ?? (chosen[c.id] ? 21 : (CT[c.id] ?? 21))" in rows
    assert "r.comfort ?? (CT[c.id] ?? 21)" not in rows
