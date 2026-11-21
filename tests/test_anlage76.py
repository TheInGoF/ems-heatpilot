"""0.76: Anlage im Blick – Kesselstatus und Fehler mitschreiben, Anlagendruck, Nachfüllen, Druckverlust, Kessel verwirft
das Vorlauf-Soll (Anlass: 21.11.2026, Druck 0,1 bar, Kessel gestört, 6 h ohne Heizen und ohne Hinweis)."""

import types

from heatpilot import boiler_health as bh
from heatpilot.boiler_control import BoilerControl
from heatpilot.boiler_watch import IGNORE_NOTE_S, K_IGNORED, BoilerWatch
from heatpilot.notify import Notifier
from heatpilot.store import Store

T0 = 1_795_000_000


class FakeEng:
    def __init__(self, store, **values):
        self.store, self.values = store, dict(values)
        self.cfg = types.SimpleNamespace(floor_heating_rooms=["climate.bad"])

    def _boiler(self, role):
        return self.values.get(role)


def make(tmp_path, **values):
    store = Store(tmp_path / "hp.db")
    eng = FakeEng(store, **{"service_code": "-H", "sys_press": 1.7, "pump_mod": 99, **values})
    notifier = Notifier(store, None, ["notify.mobile_app_test"])
    health = bh.BoilerHealth(eng, notifier)
    eng.boiler_health = health
    return eng, store, notifier, health


def events(store, kind=None):
    rows = [dict(r) for r in store.query("SELECT * FROM boiler_events ORDER BY ts")]
    return [r for r in rows if kind is None or r["kind"] == kind]


def test_normal_codes():
    for code in ("-H", "=H", "0A", "0H", "0Y", "0U", "0E", "00"):
        assert bh.normal(code), code
    for code in ("CE", "6A", "A8", "9Y", "8Y", "EA", "H07"):
        assert not bh.normal(code), code


def test_fault_open_and_close(tmp_path):
    eng, store, notifier, health = make(tmp_path, service_num="200")
    health.step(T0)
    eng.values.update(service_code="0H")
    health.step(T0 + 30)
    assert [e["text"] for e in events(store, "code")] == ["-H → 0H (200)"]
    eng.values.update(service_code="CE", service_num="207", sys_press=0.1, pump_mod=0)
    health.step(T0 + 60)
    assert bh.K_FAULT not in notifier.open_keys()  # erst nach FAULT_HOLD_S
    health.step(T0 + 60 + bh.FAULT_HOLD_S)
    text = notifier.open_keys()[bh.K_FAULT]
    assert "Störung CE (207)" in text and "0,1 bar" in text
    assert events(store, "fault")[0]["code"] == "CE"
    eng.values.update(service_code="-H", service_num="200", sys_press=1.6, pump_mod=99)
    health.step(T0 + 3660)
    assert bh.K_FAULT not in notifier.open_keys()
    assert "dauerte 60 min" in events(store, "fault_end")[0]["text"]
    assert any("wieder in Betrieb" in t for _, t in notifier.queue)


def test_last_code_once(tmp_path):
    eng, store, notifier, health = make(tmp_path, last_code="0Y(277) 27.09.2026 13:58 (0 min)")
    health.step(T0)
    assert events(store, "error") == [] and notifier.queue == []  # erstes Lesen: kein neuer Fehler
    eng.values["last_code"] = "CE(207) 21.11.2026 01:10 (0 min)"
    health.step(T0 + 30)
    assert events(store, "error")[0]["code"].startswith("CE(207)")
    assert len(notifier.queue) == 1 and bh.K_ERROR not in notifier.open_keys()


def test_refill_same_pump_state(tmp_path):
    eng, store, _, health = make(tmp_path, sys_press=0.4, pump_mod=99)
    health.step(T0)                                   # Pumpe an: 0,4 bar
    eng.values.update(sys_press=0.1, pump_mod=0)
    health.step(T0 + 3600)                            # Pumpe aus: 0,1 bar – kein Nachfüllen
    eng.values.update(sys_press=0.4, pump_mod=99)
    health.step(T0 + 7200)                            # Pumpe wieder an: +0,3 nur durch die Pumpe
    assert events(store, "refill") == []
    eng.values.update(sys_press=1.5)
    health.step(T0 + 7500)
    assert [e["text"] for e in events(store, "refill")] == ["Anlage nachgefüllt: 0,4 bar → 1,5 bar"]
    eng.values.update(sys_press=1.4, pump_mod=0)
    health.step(T0 + 9000)                            # Pumpe aus: 0,1 → 1,4, aber dasselbe Nachfüllen
    assert len(events(store, "refill")) == 1
    assert health.state["refills"][-1]["to"] == 1.5


def test_low_pressure(tmp_path):
    eng, store, notifier, health = make(tmp_path, sys_press=0.9)
    health.step(T0)
    assert bh.K_LOW not in notifier.open_keys()
    health.step(T0 + bh.LOW_HOLD_S)
    assert "0,9 bar" in notifier.open_keys()[bh.K_LOW]
    eng.values["sys_press"] = 0.6
    health.step(T0 + bh.LOW_HOLD_S + 30)
    assert "schaltet bald auf Störung" in notifier.open_keys()[bh.K_LOW]
    eng.values["sys_press"] = 1.1
    health.step(T0 + 2000)
    assert bh.K_LOW in notifier.open_keys()           # erst ab 1,2 bar erledigt
    eng.values["sys_press"] = 1.5
    health.step(T0 + 2030)
    assert bh.K_LOW not in notifier.open_keys() and len(events(store, "low")) == 1


def test_loss_rate_from_rest_pressure(tmp_path):
    eng, store, notifier, health = make(tmp_path, sys_press=1.4)
    now = T0 + 5 * 86400
    for i in range(0, 5 * 24 * 12):                   # 5 Tage alle 5 min, Pumpe nachts aus
        ts = T0 + i * 300
        night = (i // 12) % 24 < 6
        press = round(1.7 - 0.06 * (ts - T0) / 86400 + (0 if night else 0.25), 1)
        store.add_sample(ts, {"syspress": press, "pump_on": 0 if night else 1, "pump_mod": 0 if night else 99})
    health.step(now)
    loss = health.state["loss"]
    assert 0.04 <= loss["per_day"] <= 0.08 and loss["warn"] and loss["days_to_low"] > 0
    assert "verliert Wasser" in notifier.open_keys()[bh.K_LOSS]
    report = bh.report(eng, days=30)
    assert report["stunden"]["spalten"][0] == "ts" and len(report["stunden"]["zeilen"]) >= 100
    rows = []
    bh.checks(eng, lambda level, area, text: rows.append((level, area, text)))
    assert ("warn", "Druckverlust") in [(r[0], r[1]) for r in rows]


class Watch(BoilerWatch):
    def __init__(self, store, notifier, eng):
        self.state, self.store, self.notifier, self.eng = {}, store, notifier, eng


def test_boiler_drops_flow_setpoint(tmp_path):
    eng, store, notifier, _ = make(tmp_path, sys_press=0.1, service_code="CE", service_num="207")
    w = Watch(store, notifier, eng)
    for i in range(0, IGNORE_NOTE_S // 60 + 1):       # jede Minute neu geschrieben, jedes Mal verworfen
        w._dropped(T0 + i * 60, 55)
    text = notifier.open_keys()[K_IGNORED]
    assert "seit 15 min nicht an" in text and "Anlagendruck 0,1 bar" in text and "Kesselstatus CE (207)" in text
    assert len(events(store, "ignored")) == 1
    w._dropped_clear(T0 + 1200, 55)
    assert K_IGNORED not in notifier.open_keys() and "dropped" not in w.state


def test_dropped_sequence_restarts_after_gap(tmp_path):
    eng, store, notifier, _ = make(tmp_path)
    w = Watch(store, notifier, eng)
    w._dropped(T0, 55)
    w._dropped(T0 + 600, 55)                          # 10 min Lücke: neue Folge
    w._dropped(T0 + 1200, 55)
    assert K_IGNORED not in notifier.open_keys()


def test_keepalive_lines_only_once_per_hour(monkeypatch):
    ctl = object.__new__(BoilerControl)
    clock = [T0]
    monkeypatch.setattr("heatpilot.boiler_control.time.time", lambda: clock[0])
    assert ctl._say("flow", "set", (55, "Wohnzimmer hält 21 °C"))
    clock[0] += 60
    assert not ctl._say("flow", "set", (55, "Wohnzimmer hält 21 °C"))
    assert ctl._say("flow", "set", (48, "Wohnzimmer hält 21 °C"))
    clock[0] += 3600
    assert ctl._say("flow", "set", (48, "Wohnzimmer hält 21 °C"))
