"""0.48.1: Ein Kessel ohne Regler verwirft das Vorlauf-Soll nach einigen Minuten (zeigt dann 7 °C). HeatPilot erneuert
es jede Minute und schreibt nach einem Rückfall sofort neu – das ist keine fremde Änderung."""
from heatpilot import boiler_rule as br
from heatpilot.boiler_watch import same

T = 1_800_000_000


def test_keepalive_rewrites_same_value_every_minute():
    assert br.decide_flow(40, 40.0, T, T - 30, keepalive=True).write is False          # noch keine Minute
    d = br.decide_flow(40, 40.0, T, T - 61, keepalive=True)
    assert d.write and d.value == 40 and "erneuert" in d.why
    assert br.decide_flow(40, 40.0, T, T - 61).write is False                           # ohne Erneuern wie bisher
    assert br.decide_flow(20, 20.0, T, T - 600, keepalive=True).write is False          # „nur Pumpe“: nicht nötig
    assert br.decide_flow(40, 40.0, T, T - 61, checking=True, keepalive=True).write is False
    assert br.decide_flow(40, 40.0, T, T - 61, paused=True, keepalive=True).write is False
    assert br.decide_flow(40, 40.0, T, T - 61, charging=True, keepalive=True).write is False


def test_dropped_setpoint_is_rewritten_at_once_and_not_foreign():
    d = br.decide_flow(40, 7.0, T, T - 5, checking=True, keepalive=True)               # sofort, trotz Prüfung/Abstand
    assert d.write and d.value == 40 and "verworfen" in d.why
    assert same("flow", 7, 20)                                                          # Ruhewert = kein Bedarf


def test_waiting_rooms_get_the_short_anti_cycle_time():
    """0.48.2: Warten Räume auf Wärme, wird die Antipendelzeit kurz – auch wenn die Abnahme nicht reicht."""
    from types import SimpleNamespace

    from heatpilot import boiler_period as bp
    t = SimpleNamespace(charging=False, heating=True, idle=False, flow=40, load=None, load_text="Abnahme: 2 Räume …",
                        cycling={"cycling": True, "starts_h": 3}, waiting=["Flur", "Küche"])
    assert bp.want(t, 25)[0] == bp.WAIT_MIN == 5  # 0.57.1: kürzeste Sperre, der Kessel zählt ab dem Schreiben neu
    t.waiting = []
    assert bp.want(t, 25)[0] == 25
    t.waiting, t.cycling = ["Flur"], {"cycling": True, "starts_h": 8}
    assert bp.want(t, 25)[0] == 25
