"""0.70: Ventile – Meldung „schließt nicht“, Reihenfolge Deckel → Abgleich → Vorlauf, gelernte Wirkungsgrenze."""

from datetime import datetime
from types import SimpleNamespace

from heatpilot import balance_auto as ba
from heatpilot import valve_leak, valve_range
from heatpilot.store import Store

T0 = 1_790_000_000
D0 = int(datetime(2026, 10, 26).astimezone().timestamp())  # Mitternacht Ortszeit: Tage der Erkennung sind Kalendertage


def _house(store, days, hot, start=20.0):
    """Je Minute ein Punkt über ``days`` Tage, Ventile zu; Heizung 30 min an, 30 min aus. ``hot``: {Raum: K/h bei
    laufender Heizung – Zahl oder Funktion des Tages}; ohne Heizung kühlt jeder Raum ebenso schnell aus."""
    temps = dict.fromkeys(hot, start)
    for i in range(days * 1440):
        ts, heat = D0 + i * 60, (i // 30) % 2 == 0
        store.add_sample(ts, {"flow": 55.0 if heat else 30.0, "heating": int(heat), "dhw": 0})
        store.add_room_samples([(ts, r, round(t, 3), 21.0, 0.0, None) for r, t in temps.items()])
        for r in temps:
            rate = hot[r](i // 1440) if callable(hot[r]) else hot[r]
            temps[r] += (rate if heat else -rate) / 60
    store.commit()


class _Notes:
    def __init__(self):
        self.open = {}

    def problem(self, key, text, ts=None):
        self.open[key] = text

    def settle(self, prefix, keep, ts=None):
        for k in [k for k in self.open if k.startswith(prefix) and k not in keep]:
            self.open.pop(k)


# ------------------------------------------------------------------ Ventil schließt nicht (0.75.1: gegen die Nachbarn)
def test_room_warming_faster_than_the_other_closed_rooms_is_reported(tmp_path):
    s = Store(tmp_path / "a.db")
    _house(s, 4, {"climate.wz": 0.9, "climate.a": 0.1, "climate.b": 0.1})  # wz: 0,8 K/h mehr als die Nachbarn
    by_room = valve_leak.excess(s, D0)
    assert len(by_room["climate.wz"]) == 4 and all(abs(x - 0.8) < 0.05 for x, _ in by_room["climate.wz"].values())
    text = valve_leak.finding(by_room["climate.wz"])
    assert text.startswith("Ventil schließt wohl nicht ganz – bei 0 % wird der Raum an 4 von 4 Tagen schneller warm "
                           "als die anderen Räume mit geschlossenem Ventil") and "(im Mittel +0,8 K/h)" in text
    assert valve_leak.finding(by_room["climate.a"]) is None


def test_neighbour_heat_a_baking_day_and_a_lone_room_are_no_leak(tmp_path):
    s = Store(tmp_path / "b.db")
    _house(s, 4, {"climate.wz": 0.4, "climate.a": 0.4, "climate.b": 0.4})  # alle werden warm: Nachbarwärme, Anwesenheit
    assert all(valve_leak.finding(d) is None for d in valve_leak.excess(s, D0).values())
    s2 = Store(tmp_path / "c.db")  # 30.10.–02.11.: Küche, Flur – bis 0.75 gemeldet
    _house(s2, 4, {"climate.kueche": lambda day: 1.0 if day == 1 else 0.2, "climate.a": 0.1, "climate.b": 0.1})
    days = valve_leak.excess(s2, D0)["climate.kueche"]
    assert sum(1 for x, _ in days.values() if x >= valve_leak.LEAK_KH) == 1 and valve_leak.finding(days) is None
    s3 = Store(tmp_path / "d.db")
    _house(s3, 4, {"climate.wz": 0.9})  # ohne Vergleichsraum kein Urteil
    assert valve_leak.excess(s3, D0) == {}


def test_check_opens_and_quietly_closes_the_problem(tmp_path):
    s = Store(tmp_path / "e.db")
    _house(s, 4, {"climate.wz": 0.9, "climate.a": 0.1, "climate.b": 0.1})
    notes = _Notes()
    from heatpilot.config import Config
    from heatpilot.discovery import Room

    rooms = [Room("Küche", "climate.wz"), Room("A", "climate.a"), Room("B", "climate.b")]
    eng = SimpleNamespace(store=s, notifier=notes, cfg=Config(), found=SimpleNamespace(rooms=rooms))
    now = D0 + 4 * 86400
    assert set(valve_leak.check(eng, now)) == {"climate.wz"} and "Küche:" in notes.open["valve_leak:climate.wz"]
    assert valve_leak.check(eng, now + 60) == {}  # höchstens stündlich
    eng.store = Store(tmp_path / "f.db")  # repariert: keine Befunde mehr
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
