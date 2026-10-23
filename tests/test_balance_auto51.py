"""0.51: Automatischer hydraulischer Abgleich – lernende Öffnungs-Deckel für ventilgeführte Räume.

Regeln (jede hat einen Test): Gedrosselt wird nur, wenn am selben Tag ein Raum unterversorgt war (Ventil am Deckel,
unter Soll) UND ein anderer es leicht hatte; höchstens 5 Punkte je Tag, nie unter 40 %. Ohne Unterversorgung gehen die
Deckel nach 3 ruhigen Tagen schrittweise zurück auf 100 %."""

from heatpilot import balance_auto as ba

from test_control34 import MON, ts_of

T0 = ts_of(MON, 12)
DAY = 86400


def _day(st, rooms, start, hours=8):
    """Einen Tag beobachten: alle 30 s dieselben Raumwerte, dann der Tageswechsel (rechnet ab)."""
    for i in range(int(hours * 120)):
        ba.observe(st, rooms, start + i * 30)
    ba.observe(st, [], start + DAY)  # Tageswechsel: _rebalance läuft
    return st


def test_starved_plus_easy_room_lowers_the_easy_cap_step_by_step():
    st = {}
    rooms = [("climate.flur", 20.4, 21.0, 100.0, 100.0),   # unterversorgt: am Deckel, 0,6 K unter Soll
             ("climate.bad", 21.0, 21.0, 40.0, 100.0)]     # leicht: am Soll mit 40 %
    _day(st, rooms, T0)
    assert st["caps"] == {"climate.bad": 95.0}
    assert st["last"]["starved"] == ["climate.flur"] and st["last"]["lowered"] == ["climate.bad"]
    for k in range(1, 15):
        _day(st, rooms, T0 + k * DAY)
    assert st["caps"]["climate.bad"] == ba.CAP_MIN  # nie unter 40 %


def test_no_easy_room_or_no_starved_room_changes_nothing():
    st = {}
    _day(st, [("climate.flur", 20.4, 21.0, 100.0, 100.0)], T0)       # alle kämpfen: nichts drosseln
    assert not st.get("caps")
    st2 = {}
    _day(st2, [("climate.bad", 21.0, 21.0, 40.0, 100.0)], T0)        # niemand unterversorgt: nichts drosseln
    assert not st2.get("caps")


def test_short_episodes_do_not_count():
    st = {}
    rooms = [("climate.flur", 20.4, 21.0, 100.0, 100.0), ("climate.bad", 21.0, 21.0, 40.0, 100.0)]
    _day(st, rooms, T0, hours=0.3)  # nur 18 min unterversorgt bzw. leicht: unter den Mindestzeiten
    assert not st.get("caps")


def test_calm_days_raise_the_caps_back_to_100():
    st = {"caps": {"climate.bad": 85.0}}
    quiet = [("climate.flur", 21.0, 21.0, 50.0, 100.0), ("climate.bad", 21.0, 21.0, 40.0, 85.0)]
    for k in range(ba.RAISE_DAYS):
        _day(st, quiet, T0 + k * DAY)
    assert st["caps"]["climate.bad"] == 90.0
    for k in range(ba.RAISE_DAYS, 10 * ba.RAISE_DAYS):
        _day(st, quiet, T0 + k * DAY)
    assert not st["caps"]  # bei 100 %: Deckel weg


def test_starved_at_its_own_cap_counts():
    """Ein schon gedeckelter Raum zählt als unterversorgt, sobald er an SEINEM Deckel hängt."""
    st = {"caps": {"climate.flur": 60.0}}
    rooms = [("climate.flur", 20.4, 21.0, 60.0, 60.0), ("climate.bad", 21.0, 21.0, 40.0, 100.0)]
    _day(st, rooms, T0)
    assert st["caps"]["climate.bad"] == 95.0


def test_cap_and_text():
    st = {"caps": {"climate.bad": 70.0}, "last": {"starved": ["climate.flur"], "lowered": ["climate.bad"]}}
    assert ba.cap(st, "climate.bad") == 70.0 and ba.cap(st, "climate.flur") == 100.0
    text = ba.text(st, {"climate.bad": "Bad", "climate.flur": "Flur"})
    assert text == "Abgleich: Bad 70 % – Flur kam nicht mit"
    assert ba.text({}, {}) is None
