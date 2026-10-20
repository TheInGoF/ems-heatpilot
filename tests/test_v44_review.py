"""Prüfung von 0.44: nachgestellte Funde als Tests.

Thermostatwechsel: HA-Werte des neuen Thermostats vor dem Wechsel verdrängen keine eigenen Werte, „Ventil lernt ab“ ist
der Wechsel; ein nur umgehängtes Schlüsselgerät nimmt die Kennung seines alten Raums nicht mit; Auto-Modus: Kennung
hängt nicht an der Reihenfolge, zwei Zonen nebeneinander sind kein Wechsel; Aufbewahrung übersteht eine vorgehende Uhr;
geänderte Leistungssensoren löschen nur das Fenster, das HA neu füllt; Ventil-Ersatzwerte bleiben so lange wie die
übrigen Rohdaten; Abbruch beim Neulernen: Wechsel geschlossen, Neulernen setzt fort; Langzeitstatistik höchstens so weit
zurück wie die Aufbewahrung; „Hausdaten wiederherstellen“ holt ein altes Thermostat nur bis zum Wechsel.
"""

import asyncio

import pytest
from heatpilot import house_restore, keep_data, room_identity, room_ids, room_switch, sleep, valve_since
from heatpilot.config import Config, validate
from heatpilot.discovery import Registry, Room, group_rooms
from heatpilot.store import POWER_SINCE_KEY, Store
from test_room_identity44 import DAY, NEW, OLD, STEP, T0, FakeApp, count, fill, found_of, switched


# ------------------------------------------------------------------ 1: HA-Werte vor dem Wechsel
@pytest.mark.parametrize("choice", ["same", "other"])
def test_ha_values_of_new_thermostat_before_switch_do_not_replace_own(tmp_path, choice):
    st, eng = switched(tmp_path, old_days=(0, 12), since_day=12)
    fill(st, NEW, 5, 12, temp=24.0, valve=0.0)  # HA-Historie: neues Thermostat lag gekoppelt im Regal
    st.db.execute("UPDATE room_samples SET target = 5.0 WHERE room = ? AND ts < ?", (NEW, T0 + 12 * DAY))
    st.commit()
    res = asyncio.run(room_switch.resolve(FakeApp(eng), "r1", choice))
    t = T0 + 10 * DAY
    row = st.query("SELECT temp, target, valve FROM room_samples WHERE room = ? AND ts = ?", (NEW, t))[0]
    assert (row["temp"], row["target"], row["valve"]) == (19.0, 21.0, 55.0)       # eigene Werte bleiben
    assert count(st, NEW) == 14 * DAY // STEP and res["skipped"] == 7 * DAY // STEP
    if choice == "other":
        assert valve_since.of(st, NEW) == T0 + 12 * DAY                          # der Wechsel, nicht Tag 5


def test_backfill_of_room_with_open_switch_starts_at_switch(tmp_path):
    st, eng = switched(tmp_path)
    assert room_switch.new_spans(st, eng.found.rooms) == {NEW: (T0 + 12 * DAY, 2**62)}
    assert room_switch.new_spans(st, [Room("Bad", "climate.bad", room_id="r2")]) == {}


# ------------------------------------------------------------------ 2: Schlüsselgerät umgehängt
def test_key_device_moved_to_other_room_keeps_ids_with_rooms():
    before = [Room("Wohnzimmer", "climate.esszimmer_va", group="Wohnzimmer", room_id="r-wz",
                   members=[{"climate": "climate.wohnzimmer_va"}]),
              Room("Esszimmer", "climate.esszimmer_wand", group="Esszimmer", room_id="r-ez")]
    entries = [{"climate": "climate.wohnzimmer_va", "group": "Wohnzimmer", "id": "r-wz"},
               {"climate": "climate.esszimmer_wand", "group": "Esszimmer", "id": "r-ez"},
               {"climate": "climate.esszimmer_va", "group": "Esszimmer", "id": "r-wz"}]
    out = room_ids.assign(entries, before)
    rooms = {r.name: r for r in group_rooms(out, {}, {})}
    ids = {e["climate"]: e["id"] for e in out}
    assert rooms["Esszimmer"].climate == "climate.esszimmer_wand" and ids["climate.esszimmer_wand"] == "r-ez"
    assert ids["climate.wohnzimmer_va"] == "r-wz" and ids["climate.esszimmer_va"] == "r-ez"
    # ohne mitgeschickte Kennungen ebenso
    bare = room_ids.assign([{k: v for k, v in e.items() if k != "id"} for e in entries], before)
    assert {e["climate"]: e["id"] for e in bare} == ids


def test_moved_key_device_reports_switch_for_old_room(tmp_path):
    st = Store(tmp_path / "m.db")
    fill(st, "climate.esszimmer_va", 0, 5)
    st.put(room_identity.KEY, {"r-wz": {"key": "climate.esszimmer_va", "name": "Wohnzimmer"}})
    found = found_of(Room("Wohnzimmer", "climate.wohnzimmer_va", room_id="r-wz"),
                     Room("Esszimmer", "climate.esszimmer_wand", room_id="r-ez",
                          members=[{"climate": "climate.esszimmer_va"}]))
    room_identity.observe(st, found, Registry().lookup(), T0 + 6 * DAY)
    (sw,) = room_identity.open_switches(st)
    assert (sw["room_id"], sw["old_key"], sw["new_key"]) == ("r-wz", "climate.esszimmer_va", "climate.wohnzimmer_va")


# ------------------------------------------------------------------ 3: Auto-Modus
def test_auto_mode_reuses_remembered_id_and_parallel_zones_are_no_switch(tmp_path):
    a, b = "climate.wohnzimmer", "climate.wohnzimmer_erker"
    zones = [Room("Wohnzimmer", a, area="Wohnzimmer"), Room("Wohnzimmer Erker", b, area="Wohnzimmer")]
    found = found_of(*zones)
    room_ids.label(found, [])
    assert [r.room_id for r in found.rooms] == ["auto:wohnzimmer", "auto:wohnzimmer-erker"]
    st = Store(tmp_path / "a.db")
    fill(st, a, 0, 5, temp=21.0)
    fill(st, b, 0, 5, temp=18.0)
    room_identity.observe(st, found, Registry().lookup(), T0 + 5 * DAY)
    only_b = found_of(Room("Wohnzimmer Erker", b, area="Wohnzimmer"))
    room_ids.label(only_b, [], st.get(room_identity.KEY))                       # Zone A fehlt (HA-Start)
    assert only_b.rooms[0].room_id == "auto:wohnzimmer-erker"
    room_identity.observe(st, only_b, Registry().lookup(), T0 + 5 * DAY)
    assert room_identity.open_switches(st) == []
    # ohne gemerkte Kennung rutscht sie weiter – nebeneinander gemessen: kein Wechsel, nichts vermischt
    slid = found_of(Room("Wohnzimmer Erker", b, area="Wohnzimmer"))
    room_ids.label(slid, [])
    assert slid.rooms[0].room_id == "auto:wohnzimmer"
    room_identity.observe(st, slid, Registry().lookup(), T0 + 5 * DAY)
    assert room_identity.open_switches(st) == [] and st.get(room_identity.LOG)[-1]["what"] == "nebeneinander"
    assert count(st, a) == count(st, b) == 5 * DAY // STEP


# ------------------------------------------------------------------ 4: Aufbewahrung und Uhr
def test_keep_survives_clock_jump(tmp_path):
    st = Store(tmp_path / "k.db")
    now = T0 + 20 * DAY
    for ts in range(now - 400 * DAY, now, 3600):
        st.add_sample(ts, {"flow": 40.0})
    st.commit()
    n = count_samples(st)
    assert sum(keep_data.daily(st, 365, now).values()) > 0
    kept = count_samples(st)
    assert kept >= 365 * 24 and n - kept < 40 * 24
    assert keep_data.daily(st, 365, now + 3 * 365 * DAY)["samples"] <= 3 * 24  # Bezug rückt höchstens 2 Tage vor
    assert count_samples(st) >= 362 * 24


def count_samples(st) -> int:
    return st.query("SELECT COUNT(*) AS n FROM samples")[0]["n"]


# ------------------------------------------------------------------ 5: Leistungssensoren
def test_clear_power_only_clears_the_window_ha_refills(tmp_path):
    st = Store(tmp_path / "p.db")
    now = T0 + 20 * DAY
    for ts in range(now - 60 * DAY, now, 3600):
        st.add_sample(ts, {"power_w": 300.0})
    st.commit()
    st.clear_power(now - 30 * DAY)
    left = st.query("SELECT COUNT(*) AS n FROM samples WHERE power_w IS NOT NULL")[0]["n"]
    assert left == 30 * 24 and st.get(POWER_SINCE_KEY) == now - 30 * DAY
    assert sleep.learn(st, now) == {}                       # alte Sensoren zählen nicht als Nächte


# ------------------------------------------------------------------ 6: Ventil-Ersatzwerte
def test_valve_subst_kept_beyond_90_days(tmp_path):
    st = Store(tmp_path / "v.db")
    for age in (200, 100):
        st.add_valve_subst(T0 - age * DAY, ["climate.a"])
    st.add_valve_subst(T0, ["climate.a"])
    st.commit()
    assert st.query("SELECT COUNT(*) AS n FROM valve_subst")[0]["n"] == 3


# ------------------------------------------------------------------ 7: Abbruch beim Neulernen
def test_abort_during_relearn_closes_switch_atomically_and_resumes(tmp_path, monkeypatch):
    st, eng = switched(tmp_path)

    async def boom(_key):
        raise RuntimeError("Neustart")
    monkeypatch.setattr(eng, "relearn_adopted_async", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(room_switch.resolve(FakeApp(eng), "r1", "other"))
    assert room_identity.open_switches(st) == [] and st.get(room_identity.LOG)[-1]["what"] == "other"
    assert valve_since.of(st, NEW) == T0 + 12 * DAY and room_switch.pending(st) == [NEW]
    monkeypatch.undo()
    asyncio.run(room_switch.resume(eng))
    assert room_switch.pending(st) == [] and st.get(f"model.room.{NEW}") is not None


# ------------------------------------------------------------------ 8: Langzeitstatistik ≤ Aufbewahrung
def test_statistics_days_not_beyond_keep_days():
    assert validate({"keep_days": 365, "statistics_backfill_days": 1095})[1]
    assert not validate({"keep_days": 1095, "statistics_backfill_days": 1095})[1]
    assert Config.from_raw({"keep_days": 365, "statistics_backfill_days": 1095}).statistics_backfill_days == 365
    assert Config.from_raw({}).statistics_backfill_days == 365                 # Standard unverändert


# ------------------------------------------------------------------ 9: Wiederherstellen nach „anderes Thermostat“
def test_restore_fetches_old_thermostat_only_until_switch(tmp_path):
    _, eng = switched(tmp_path)
    asyncio.run(room_switch.resolve(FakeApp(eng), "r1", "other"))
    assert house_restore.old_until(eng) == {OLD: (0, T0 + 12 * DAY)}
    (tmp_path / "s").mkdir()
    _, eng2 = switched(tmp_path / "s")
    asyncio.run(room_switch.resolve(FakeApp(eng2), "r1", "same"))
    assert house_restore.old_until(eng2) == {}
