"""Prüfung von 0.40 (Ventile führen): die nachgestellten Funde als Tests.

1. Hand „aus“ (Raum-Dialog, „Alle aus“, Fenster-offen-Erkennung) während eines Schritts bleibt bis zum Phasenwechsel:
   Die Führung verwirft ihren Schritt, das Wunschsoll gilt wieder als zuletzt geschrieben – wie 0.39.
2. Ein eigener Schritt zählt nie als Hand: Teilfehler in einer Gruppe, Neuaufbau während des Schreibens, verzögerte
   Rückmeldung – spätestens nach 60 min steht wieder das Wunschsoll. Eine echte Hand bleibt Hand.
3. Pause: Das Zurück aufs Wunschsoll geht trotzdem hinaus (wer Wärme braucht, sofort), neue Schritte nicht; der Knopf
   „Pause“ nimmt laufende Schritte vorher zurück.
4. Gemeinsam anfordern ohne Kette und Pendeln: kein Auslöser, den HeatPilot gerade drosselt oder in den letzten 60 min
   angehoben hat; kein Kandidat, der nach dem Drosseln gesperrt ist oder in den letzten 60 min angehoben war.
5. 0,1-K-Thermostate: Sollwerte mit Toleranz verglichen – Hand um einen Schritt wird erkannt, der Schritt unter 16,5 °C
   geschrieben.
6. „Beobachten“ bzw. „macht mit“ aus mitten im Schritt: Vor dem Neuaufbau (main.build) einmal zurück aufs Wunschsoll,
   noch unter den alten Einstellungen – nur wenn der Schritt noch am Thermostat steht.
"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from heatpilot import controlloop
from heatpilot.config import Config
from heatpilot.controlloop import ControlLoop
from heatpilot.discovery import Room
from heatpilot.main import App
from test_control34 import MON, climate, run, ts_of
from test_valve_control40 import (BAD, KIND, SAMPLE, T0, WZ, EchoHA, readings, setup, temps, throttle_ready, tick,
                                  written)
from test_valve_dry39 import burner, room

ROOT = Path(__file__).resolve().parents[1] / "ems_heatpilot" / "heatpilot"
V2 = "climate.wz2"


def sp_of(eng, c=WZ):
    return eng.states[c]["attributes"]["temperature"]


# ------------------------------------------------------------------ 1. Hand „aus“
def test_off_by_hand_while_throttled_stays_until_phase_change(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert temps(ha) == [(WZ, 20.5)]
    ha.calls.clear()
    climate(eng, WZ, 20.8, 20.5, mode="off", target_temp_step=0.5)  # jemand schaltet den Raum aus
    for k in range(2, 14):
        assert tick(loop, T0 + k * 300, {WZ: (20.8 - 0.02 * k, 0.0), BAD: (21.2, 0.0)}) == []
    assert ha.calls == [] and eng.states[WZ]["state"] == "off"
    assert loop.state[WZ]["last_written"] == 21.0 and WZ in loop.guide.st["hands"] and loop.guide.st["active"] == {}
    # erst der Phasenwechsel beendet „aus“ (wie 0.39)
    out = tick(loop, ts_of(MON, 21, 1), {WZ: (20.0, 0.0), BAD: (21.2, 0.0)})
    assert (WZ, 18.0) in [(c, sp) for c, sp, _ in out]
    assert ("climate", "set_hvac_mode", WZ, {"hvac_mode": "heat"}) in ha.calls


def test_all_off_button_while_raised(tmp_path):
    """Heizen → „Alle aus“ (Control.set_mode off), während Bad gemeinsam anfordert: alle bleiben aus."""
    rooms = [room("Wz", WZ, managed=False), room("Bad", BAD, managed=True), room("Kind", KIND, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    burner(eng, T0)
    tick(loop, T0, {WZ: (20.4, 80.0), BAD: (20.9, 0.0), KIND: (22.4, 0.0)})
    assert temps(ha) == [(BAD, 21.5)]
    run(loop.control.set_mode([WZ, BAD, KIND], "off"))
    ha.calls.clear()
    for k in (2, 3, 6, 12):
        burner(eng, T0 + k * 300)
        assert tick(loop, T0 + k * 300, {WZ: (20.4, 80.0), BAD: (20.9, 0.0), KIND: (22.4, 0.0)}) == []
    assert ha.calls == [] and {eng.states[c]["state"] for c in (WZ, BAD, KIND)} == {"off"}


def test_off_by_hand_baseline_039(tmp_path):
    """Gegenprobe ohne Schalter: „aus“ bleibt bis zum Phasenwechsel."""
    eng, ha, loop = setup(tmp_path, cfg=Config(control_mode="heatpilot"))
    climate(eng, WZ, 20.8, 21.0, mode="off", target_temp_step=0.5)
    assert tick(loop, T0 + 600, {WZ: (20.8, 0.0), BAD: (21.2, 0.0)}) == [] and ha.calls == []


# ------------------------------------------------------------------ 2. eigener Schritt ist keine Hand
def test_lagging_feedback_after_throttle(tmp_path):
    """HA meldet das Geschriebene erst einen bzw. zwei Takte später (Cloud): keine Hand, der Schritt endet normal."""
    for lag in (1, 2):
        eng, ha, loop = setup(tmp_path / str(lag))
        throttle_ready(eng)
        tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
        assert temps(ha) == [(WZ, 20.5)]
        climate(eng, WZ, 20.8, 21.0, target_temp_step=0.5)  # noch das alte Soll
        for k in range(1, lag + 1):
            assert tick(loop, T0 + k * 300, {WZ: (20.85, 90.0), BAD: (21.2, 0.0)}) == []
            assert not loop.rooms[WZ]["manual"] and loop.guide.st["active"][WZ]["sp"] == 20.5
        climate(eng, WZ, 20.9, 20.5, target_temp_step=0.5)  # jetzt kommt das Geschriebene an
        out = [o for k in range(lag + 1, 14) for o in tick(loop, T0 + k * 300, {WZ: (21.1, 0.0), BAD: (21.2, 0.0)})]
        assert out == [(WZ, 21.0, "Ventil: zurück (Soll erreicht)")] and sp_of(eng) == 21.0
        assert not loop.rooms[WZ]["manual"] and loop.guide.st["hands"] == {}


def test_lagging_feedback_baseline_039(tmp_path):
    """Gegenprobe 0.39: Phasenwechsel mit verzögerter Meldung – keine Hand, keine Schreibvorgänge mehr."""
    eng, ha, loop = setup(tmp_path, cfg=Config(control_mode="heatpilot"))
    night = ts_of(MON, 22)
    tick(loop, night, {WZ: (21.0, 0.0), BAD: (21.0, 0.0)})
    assert temps(ha) == [(WZ, 18.0), (BAD, 18.0)]
    climate(eng, WZ, 21.0, 21.0, target_temp_step=0.5)  # verzögert
    assert tick(loop, night + 300, {WZ: (21.0, 0.0), BAD: (21.0, 0.0)}) == []
    climate(eng, WZ, 21.0, 18.0, target_temp_step=0.5)
    assert tick(loop, night + 600, {WZ: (21.0, 0.0), BAD: (21.0, 0.0)}) == [] and not loop.rooms[WZ]["manual"]
    assert temps(ha) == [(WZ, 18.0), (BAD, 18.0)]


def test_partial_group_write_returns_within_60_min(tmp_path):
    """Gruppe ohne Raumregler, das zweite Ventil nimmt das Soll nicht an: Das erste steht auf dem Schritt – das ist
    HeatPilots eigener Wert, keine Hand. Nach der Wartezeit kommen beide aufs Wunschsoll."""
    wz = Room("Wz", WZ, managed=True, comfort=21.0, platform="tado", heating_power="sensor.wz_heat",
              members=[{"climate": V2, "heating_power": "sensor.wz2_heat", "humidity": None, "temp_sensor": None,
                        "platform": "tado"}])
    eng, ha, loop = setup(tmp_path, rooms=[wz, room("Bad", BAD, managed=True)])
    climate(eng, V2, 20.3, 21.0, target_temp_step=0.5)
    ha.calls.clear()
    ha.fail.add(V2)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert sp_of(eng) == 20.5 and sp_of(eng, V2) == 21.0 and loop.state[WZ]["fails"] == 1
    ha.fail.clear()
    for k in range(1, 13):
        tick(loop, T0 + k * 300, {WZ: (21.1, 0.0), BAD: (21.2, 0.0)})
        assert not loop.rooms[WZ]["manual"] and loop.guide.st["hands"] == {}
    assert sp_of(eng) == 21.0 and sp_of(eng, V2) == 21.0 and loop.state[WZ]["fails"] == 0


class StopHA(EchoHA):
    """Während des Aufrufs speichert jemand die Einstellungen: main.build baut eine neue Schleife aus kv und stellt die
    alte außer Dienst – ``early``: die neue entsteht, bevor der Aufruf zurückkommt (wie im echten Ablauf)."""

    loop = new = None
    early = False

    async def call(self, payload, timeout=0):
        hit = self.loop is not None and payload["service_data"].get("temperature") == 20.5
        if hit and self.early:
            self.new = ControlLoop(self.eng, self.loop.control)
        await super().call(payload, timeout)
        if hit:
            self.new = self.new or ControlLoop(self.eng, self.loop.control)
            self.loop.stop()
            self.loop = None


def test_rebuild_during_throttle_write(tmp_path):
    for early in (False, True):
        eng, ha, loop = setup(tmp_path / str(early))
        sha = StopHA(eng)
        sha.loop, sha.early, loop.control.ha = loop, early, sha
        throttle_ready(eng)
        loop.plan(T0, SAMPLE, readings(eng, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}))
        run(loop.apply())
        assert temps(sha) == [(WZ, 20.5)]
        new = sha.new
        new.control.ha = EchoHA(eng)
        eng.control_loop = new
        for k in range(1, 13):
            tick(new, T0 + k * 300, {WZ: (21.1, 0.0), BAD: (21.2, 0.0)})
            assert not new.rooms[WZ]["manual"] and new.guide.st["hands"] == {}, early
        assert sp_of(eng) == 21.0, early


def test_real_hand_on_the_step_value_stays_hand(tmp_path):
    """Hat jemand nach dem Schritt von Hand gedreht, ist der Schritt-Wert danach nicht mehr „eigen“: Dreht er später
    genau auf den Schritt, bleibt das bis zum Phasenwechsel."""
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    climate(eng, WZ, 20.8, 22.0, target_temp_step=0.5)
    assert tick(loop, T0 + 600, {WZ: (20.8, 100.0), BAD: (21.2, 0.0)}) == [] and loop.rooms[WZ]["manual"]
    climate(eng, WZ, 20.8, 20.5, target_temp_step=0.5)
    for k in range(3, 14):
        assert tick(loop, T0 + k * 300, {WZ: (21.1, 0.0), BAD: (21.2, 0.0)}) == []
    assert sp_of(eng) == 20.5 and loop.rooms[WZ]["manual"]


# ------------------------------------------------------------------ 3. Pause
def test_pause_still_returns_when_room_needs_heat(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    loop.pause_until = T0 + 6 * 3600
    out = tick(loop, T0 + 60, {WZ: (20.4, 0.0), BAD: (21.2, 0.0)})  # Fenster auf, 0,6 K unter Soll – in der Haltezeit
    assert out == [(WZ, 21.0, "Ventil: zurück (Raum braucht Wärme)")] and sp_of(eng) == 21.0
    assert loop.guide.st["active"] == {} and loop.rooms[WZ]["valve"] is None


def test_pause_returns_when_reached_but_starts_nothing_new(tmp_path):
    eng, ha, loop = setup(tmp_path)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    loop.pause_until = T0 + 6 * 3600
    assert tick(loop, T0 + 300, {WZ: (21.05, 40.0), BAD: (21.2, 0.0)}) == [(WZ, 21.0, "Ventil: zurück (Soll erreicht)")]
    loop.guide.st["lock"] = {}  # auch ohne Sperre: in der Pause kein neuer Schritt
    n = len(ha.calls)
    for k in range(2, 12):
        throttle_ready(eng, T0 + k * 300, 20.72)
        assert tick(loop, T0 + k * 300, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)}) == []
    assert len(ha.calls) == n and loop.guide.st["active"] == {}


def test_pause_button_takes_running_steps_back(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from heatpilot.web import make_app as web_app
    from test_control34 import make_app

    app, _, loop = make_app(tmp_path)
    eng = app.eng
    loop.control.ha = EchoHA(eng)
    now = int(time.time())
    climate(eng, WZ, 20.8, 20.5)
    loop.state[WZ] = {"last_written": 20.5, "phase": "komfort"}
    loop.guide.st["active"][WZ] = {"rule": "drosseln", "sp": 20.5, "base": 21.0, "since": now - 120, "for": None,
                                   "reason": "erreicht 21,0 °C in ≈ 8 min", "back_at": now + 360}

    async def go():
        client = TestClient(TestServer(web_app(app)))
        await client.start_server()
        try:
            j = await (await client.post("/api/heat/pause", json={"hours": 2})).json()
            assert j["ok"] and j["paused_until"]
        finally:
            await client.close()

    asyncio.run(go())
    assert sp_of(eng) == 21.0 and loop.guide.st["active"] == {} and loop.state[WZ]["last_written"] == 21.0
    assert eng.store.control_log(1)[0]["reason"] == "Ventil: zurück (Pause)"
    assert [r["action"] for r in written(eng)] == ["zurück"]


# ------------------------------------------------------------------ 4. gemeinsam anfordern: keine Kette, kein Pendeln
def test_no_chain_one_tick_after_back(tmp_path):
    """Ein Takt nach dem Zurück ist Bads Ventil wegen des Anhebens noch offen – es löst nichts aus."""
    rooms = [room("Wz", WZ, managed=False), room("Bad", BAD, managed=True), room("Kind", KIND, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    burner(eng, T0)
    tick(loop, T0, {WZ: (20.4, 80.0), BAD: (20.9, 0.0), KIND: (21.6, 0.0)})
    assert tick(loop, T0 + 600, {WZ: (21.0, 0.0), BAD: (21.1, 35.0), KIND: (20.9, 0.0)}) == [
        (BAD, 21.0, "Ventil: zurück (Wz fordert nicht mehr an)")]
    for k in (3, 6, 12):
        burner(eng, T0 + k * 300)
        assert tick(loop, T0 + k * 300, {WZ: (21.0, 0.0), BAD: (21.1, 25.0), KIND: (20.9, 0.0)}) == []
    assert not any(r["room"] == KIND for r in written(eng))


def test_no_raise_right_after_throttle(tmp_path):
    """Pendeln: Wz eben gedrosselt und zurück (Sperre) – kein gemeinsames Anheben."""
    rooms = [room("Wz", WZ, managed=True), room("Bad", BAD, managed=True), room("Kind", KIND, managed=False)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.6, 0.0), KIND: (20.5, 0.0)})
    tick(loop, T0 + 300, {WZ: (21.05, 40.0), BAD: (21.6, 0.0), KIND: (20.5, 0.0)})
    assert loop.guide.st["lock"] == {WZ: 21.0}
    burner(eng, T0 + 900)
    tick(loop, T0 + 900, {WZ: (21.0, 0.0), BAD: (21.6, 0.0), KIND: (20.4, 80.0)})
    assert [r["action"] for r in written(eng) if r["room"] == WZ] == ["senken", "zurück"]


def test_no_join_for_a_throttled_lead(tmp_path):
    """Das Ventil des eben gedrosselten Raums schließt gleich – er löst kein gemeinsames Anfordern aus."""
    rooms = [room("Wz", WZ, managed=True), room("Bad", BAD, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (20.9, 0.0)})
    burner(eng, T0 + 300)
    tick(loop, T0 + 300, {WZ: (20.85, 60.0), BAD: (20.9, 0.0)})
    tick(loop, T0 + 600, {WZ: (21.0, 0.0), BAD: (20.95, 30.0)})
    tick(loop, T0 + 900, {WZ: (21.0, 0.0), BAD: (21.0, 30.0)})
    assert [(r["room"], r["action"]) for r in written(eng)] == [(WZ, "senken"), (WZ, "zurück")]


def test_no_raise_again_within_60_min_after_back(tmp_path):
    rooms = [room("Wz", WZ, managed=False), room("Bad", BAD, managed=True)]
    eng, ha, loop = setup(tmp_path, rooms=rooms)
    for k in range(0, 4 * 12):
        burner(eng, T0 + k * 300)
        tick(loop, T0 + k * 300, {WZ: (20.4, 80.0), BAD: (20.9, 0.0)})  # Wz fordert dauernd allein an
    rows = [(r["ts"], r["action"]) for r in written(eng)]
    assert rows[:2] == [(T0, "anheben"), (T0 + 3600, "zurück")]
    for (t_back, a), (t_up, b) in zip(rows[1::2], rows[2::2]):
        assert (a, b) == ("zurück", "anheben") and t_up - t_back >= 3600


# ------------------------------------------------------------------ 5. 0,1-K-Thermostate
def test_same_with_float_tolerance():
    same = controlloop._same
    assert not same(20.9, 20.8) and not same(21.0, 20.9) and not same(15.9, 16.0) and not same(21.1, 21.2)
    assert same(20.5, 20.5) and same(21.0, 21.04) and not same(None, 21.0) and not same(21.0, None)


def test_hand_one_01_step_is_recognized(tmp_path):
    eng, ha, loop = setup(tmp_path, step=0.1)
    throttle_ready(eng, T0, 20.85)
    tick(loop, T0, {WZ: (20.85, 100.0), BAD: (21.2, 0.0)})
    assert temps(ha) == [(WZ, 20.9)]
    climate(eng, WZ, 20.9, 20.8, target_temp_step=0.1)  # jemand dreht einen Schritt tiefer
    assert tick(loop, T0 + 600, {WZ: (20.95, 40.0), BAD: (21.2, 0.0)}) == []
    assert loop.rooms[WZ]["manual"] and WZ in loop.guide.st["hands"]
    assert tick(loop, T0 + 900, {WZ: (21.05, 20.0), BAD: (21.2, 0.0)}) == [] and sp_of(eng) == 20.8


def test_01_step_written_below_16_5(tmp_path):
    cold = Room("Wz", WZ, managed=True, comfort=16.0, platform="tado", heating_power="sensor.wz_heat")
    eng, ha, loop = setup(tmp_path, rooms=[cold, room("Bad", BAD, managed=True)], step=0.1)
    eng.store.db.execute("DELETE FROM room_samples WHERE room = ?", (WZ,))
    from test_valve_dry39 import approach
    approach(eng, WZ, T0, 15.75, target=16.0, rise_min=None)
    out = tick(loop, T0, {WZ: (15.75, 100.0), BAD: (21.2, 0.0)})
    assert [(c, sp) for c, sp, _ in out] == [(WZ, 15.9)] and temps(ha) == [(WZ, 15.9)]
    assert loop.rooms[WZ]["valve"]["setpoint"] == 15.9


# ------------------------------------------------------------------ 6. „Beobachten“ / „macht mit“ aus
def _throttled(tmp_path):
    eng, ha, loop = setup(tmp_path)
    eng.control_loop = loop
    throttle_ready(eng)
    tick(loop, T0, {WZ: (20.72, 100.0), BAD: (21.2, 0.0)})
    assert sp_of(eng) == 20.5
    return eng, ha, loop


def test_observe_while_throttled_takes_step_back_first(tmp_path):
    eng, ha, loop = _throttled(tmp_path)
    run(App._release_valves(SimpleNamespace(cfg=Config()), eng, eng.found))  # main.build mit „Beobachten“
    assert sp_of(eng) == 21.0 and loop.guide.st["active"] == {}
    assert eng.store.control_log(1)[0]["reason"] == "Ventil: zurück (Steuerung „Beobachten“)"
    eng.cfg.control_mode = "beobachten"
    n = len(ha.calls)
    tick(loop, T0 + 300, {WZ: (20.9, 50.0), BAD: (21.2, 0.0)})
    assert len(ha.calls) == n  # danach schreibt HeatPilot nichts mehr


def test_unmanaged_while_throttled_takes_step_back_first(tmp_path):
    eng, ha, loop = _throttled(tmp_path)
    found = SimpleNamespace(rooms=[room("Wz", WZ, managed=False), room("Bad", BAD, managed=True)])
    run(App._release_valves(SimpleNamespace(cfg=Config(control_mode="heatpilot", valve_control=True)), eng, found))
    assert sp_of(eng) == 21.0 and eng.store.control_log(1)[0]["reason"] == "Ventil: zurück (macht nicht mehr mit)"
    assert [c[2] for c in ha.calls if c[1] == "set_temperature"] == [WZ, WZ]  # nur der Raum mit dem Schritt


def test_release_respects_hand_and_kept_rooms(tmp_path):
    eng, ha, loop = _throttled(tmp_path)
    n = len(ha.calls)
    assert run(loop.release({WZ}, "Pause")) == 0  # Raum wird weiter geführt: nichts
    climate(eng, WZ, 20.8, 22.0, target_temp_step=0.5)  # Hand
    assert run(loop.release(set(), "Pause")) == 0 and len(ha.calls) == n and sp_of(eng) == 22.0
    eng2, ha2, loop2 = _throttled(tmp_path / "b")
    climate(eng2, WZ, 20.8, 20.5, mode="off", target_temp_step=0.5)  # „aus“ von Hand
    assert run(loop2.release(set(), "Pause")) == 0 and eng2.states[WZ]["state"] == "off"


def test_build_releases_before_the_new_loop():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    build = src.split("    async def build(")[1].split("    async def _release_valves(")[0]
    assert build.index("await self._release_valves(old, found)") < build.index("self.eng = eng")
    assert build.index("self.eng = eng") < build.index("self._build_control(eng, found, old)")
