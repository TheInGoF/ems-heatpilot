"""0.70: Ventile – Meldung „schließt nicht“, Reihenfolge Deckel → Abgleich → Vorlauf, gelernte Wirkungsgrenze."""

from types import SimpleNamespace

from heatpilot import balance_auto as ba
from heatpilot import valve_leak, valve_range
from heatpilot.store import Store

T0 = 1_790_000_000


def _fill(store, minutes, valve, rate_hot, rate_cold, room="climate.wz", target=21.0, start=20.0):
    """Je Minute ein Punkt; Heizung 30 min an, 30 min aus; der Raum steigt bzw. fällt mit der jeweiligen Rate."""
    temp = start
    for i in range(minutes):
        ts = T0 + i * 60
        hot = (i // 30) % 2 == 0
        v = valve(i) if callable(valve) else valve
        store.add_sample(ts, {"flow": 55.0 if hot else 30.0, "heating": int(hot), "dhw": 0})
        store.add_room_samples([(ts, room, round(temp, 3), target, v, None)])
        temp += (rate_hot(v) if callable(rate_hot) else rate_hot if hot else rate_cold) / 60
    store.commit()


class _Notes:
    def __init__(self):
        self.open = {}

    def problem(self, key, text, ts=None):
        self.open[key] = text

    def settle(self, prefix, keep, ts=None):
        for k in [k for k in self.open if k.startswith(prefix) and k not in keep]:
            self.open.pop(k)


# ------------------------------------------------------------------ Ventil schließt nicht
def test_closed_valve_that_warms_the_room_while_heating_runs_is_reported(tmp_path):
    s = Store(tmp_path / "a.db")
    _fill(s, 8 * 60, 0.0, 0.6, -0.2)
    r = valve_leak.rates(s, "climate.wz", T0)
    assert abs(r["hot"][0] - 0.6) < 0.05 and abs(r["cold"][0] + 0.2) < 0.05
    assert "schließt wohl nicht ganz" in valve_leak.finding(r)


def test_tight_valve_and_sun_are_no_leak(tmp_path):
    s = Store(tmp_path / "b.db")
    _fill(s, 8 * 60, 0.0, -0.2, -0.2)  # dicht: mit und ohne Heizung gleich
    assert valve_leak.finding(valve_leak.rates(s, "climate.wz", T0)) is None
    s2 = Store(tmp_path / "c.db")
    _fill(s2, 8 * 60, 0.0, 0.0, 0.5)  # Sonne, während die Heizung aus ist
    assert valve_leak.finding(valve_leak.rates(s2, "climate.wz", T0)) is None


def test_check_opens_and_quietly_closes_the_problem(tmp_path):
    s = Store(tmp_path / "d.db")
    _fill(s, 8 * 60, 0.0, 0.6, -0.2)
    notes = _Notes()
    from heatpilot.config import Config
    from heatpilot.discovery import Room

    eng = SimpleNamespace(store=s, notifier=notes, cfg=Config(), found=SimpleNamespace(rooms=[Room("Küche", "climate.wz")]))
    now = T0 + 8 * 3600
    assert set(valve_leak.check(eng, now)) == {"climate.wz"} and "Küche:" in notes.open["valve_leak:climate.wz"]
    assert valve_leak.check(eng, now + 60) == {}  # höchstens stündlich
    eng.store = Store(tmp_path / "e.db")  # repariert: keine Befunde mehr
    valve_leak.check(eng, now + 3600)
    assert notes.open == {}


# ------------------------------------------------------------------ Reihenfolge Deckel → Abgleich → Vorlauf
def test_starving_room_loses_its_cap_at_once_and_the_limit_rests_until_warm():
    st = {"caps": {"climate.wz": 60.0}, "limit": {"climate.wz": 80.0}}
    assert ba.cap(st, "climate.wz") == 60.0
    for i in range(31):  # 15 min am Deckel, 0,5 K unter Soll
        ba.observe(st, [("climate.wz", 20.5, 21.0, 60.0, ba.cap(st, "climate.wz"))], T0 + i * 30)
    assert ba.cap(st, "climate.wz") == 100.0 and "climate.wz" not in st["caps"]
    ba.observe(st, [("climate.wz", 21.0, 21.0, 70.0, 100.0)], T0 + 1000)  # warm
    assert ba.cap(st, "climate.wz") == 80.0  # die Wirkungsgrenze gilt wieder, der Abgleich-Deckel bleibt weg


def test_capped_rooms_do_not_raise_the_flow():
    bal = {"caps": {"climate.a": 70.0}, "limit": {"climate.b": 80.0}, "released": {"climate.b": T0}}
    eng = SimpleNamespace(valve_pid=SimpleNamespace(state={"bal": bal}))
    assert ba.capped(eng) == {"climate.a"}  # b ist freigegeben: zählt wieder für den Vorlauf
    assert ba.capped(SimpleNamespace(valve_pid=None)) == set()


# ------------------------------------------------------------------ Wirkungsgrenze
def _range_store(tmp_path, name, gain):
    """Raum unter Soll, Heizung an, Ventil wandert stündlich durch 10 … 100 %; Anstieg je Klasse laut ``gain``."""
    s = Store(tmp_path / name)
    opens = [10, 30, 50, 70, 90, 100]
    for i in range(6 * 60 * 2):
        ts = T0 + i * 60
        v = opens[(i // 60) % 6]
        s.add_sample(ts, {"flow": 55.0, "heating": 1, "dhw": 0})
        s.add_room_samples([(ts, "climate.wz", 18.0 + 0.0001 * i * gain(v), 21.0, float(v), None)])
    s.commit()
    return s


def test_full_effect_is_learned_and_used_only_when_plausible(tmp_path):
    sat = _range_store(tmp_path, "r1.db", lambda v: min(v, 70) / 70)  # ab 70 % nicht mehr schneller
    r = valve_range.rates(sat, "climate.wz", T0)
    assert valve_range.full_at(r) == 80  # Klasse 61–80 % bringt schon (fast) alles
    bal, notes = {}, _Notes()
    room = SimpleNamespace(climate="climate.wz", name="Küche")
    valve_range.update(bal, notes, [room], sat, T0 + 13 * 3600)
    assert bal["limit"] == {"climate.wz": 80.0} and notes.open == {}
    early = _range_store(tmp_path, "r2.db", lambda v: min(v, 30) / 30)  # „voll“ schon ab 30 %: unplausibel
    valve_range.update(bal, notes, [room], early, T0 + 13 * 3600)
    assert bal["limit"] == {} and "falsch kalibriert" in notes.open["valve_range:climate.wz"]
    lin = _range_store(tmp_path, "r3.db", lambda v: v / 100)  # wirkt bis 100 %
    valve_range.update(bal, notes, [room], lin, T0 + 13 * 3600)
    assert bal["limit"] == {} and notes.open == {}
