"""Zuschlag „Raum kommt nicht hoch“ (seit 0.50): Vorlauf anheben, wenn ein Raum mit offenem Ventil nicht warm wird.

Der Bedarf aus den Raummodellen (``boiler_flow`` 1.) kann zu tief liegen – etwa bei zu kleinen Heizkörpern: Dann steht
das Ventil offen, der Raum bleibt unter Soll und steigt kaum (Fall aus dem 30-s-Protokoll: Küche, Flur und Wohnzimmer
eine Stunde bei 35–37 °C Vorlauf, Ventile 80–100 %). Unabhängig vom Takten (der Takt-Boost ist ein eigener Teil):

* **kommt nicht hoch** – mindestens ``GAP_K`` unter Soll mit dem Ventil ab ``VALVE_PCT`` (einmal erkannt: bis
  ``KEEP_GAP_K`` unter Soll und Ventil ab ``KEEP_VALVE_PCT`` – Thermostate pendeln) und seit ``WINDOW_S`` nicht um
  ``RISE_K`` gestiegen (Bezug: Temperatur beim Erkennen bzw. beim letzten Anstieg). Räume mit Fußbodenheizung zählen
  nicht (träge, die Fußboden-Grenze gilt ohnehin),
* **hoch** – solange ein Raum nicht hochkommt: +``STEP_K`` je ``STEP_S`` (der erste sofort), höchstens bis zur
  Obergrenze (``max_flow_temp``, Fußboden-Grenze, Entität),
* **halten** – steigt der Raum wieder bzw. wartet er noch auf seine ``WINDOW_S``, bleibt die Stufe,
* **zurück** – wartet kein Raum mehr so (am Soll, Ventil zu): −``STEP_K`` je ``STEP_S``.

Andere Räume werden davon nicht überheizt – ihre Thermostate machen zu. Der Stand {k, ts, rooms: {climate: {since,
ref}}} läuft über ``boiler_control`` (kv) weiter; ``step`` selbst hat keinen Zustand.
"""

from __future__ import annotations

GAP_K = 0.3            # kommt nicht hoch: so weit unter dem Soll …
VALVE_PCT = 80.0       # … mit dem Ventil so weit offen …
KEEP_GAP_K = 0.2       # (einmal erkannt, bleibt er dabei, solange er so weit unter Soll …
KEEP_VALVE_PCT = 50.0  # … und das Ventil so weit offen ist)
RISE_K = 0.2           # … und in ``WINDOW_S`` nicht um so viel gestiegen
WINDOW_S = 1800
STEP_K = 2             # Zuschlag: Schritt …
STEP_S = 900           # … je so viele Sekunden (der erste sofort), hoch wie zurück


def _waits(temp, target, valve, known: bool) -> bool:
    if None in (temp, target, valve):
        return False
    gap, pct = (KEEP_GAP_K, KEEP_VALVE_PCT) if known else (GAP_K, VALVE_PCT)
    return temp <= target - gap + 1e-9 and valve >= pct


def step(state: dict | None, rooms: list[tuple], ts: int, headroom: int) -> tuple[dict, list[str]]:
    """Nächster Stand und die Räume, die gerade nicht hochkommen (Namen). ``rooms``: (climate, Name, Ist, Soll, Ventil
    in %) der Räume, die zählen; ``headroom``: Abstand bis zur Obergrenze in K."""
    s = state or {}
    k, last, known = int(s.get("k") or 0), s.get("ts"), s.get("rooms") or {}
    seen, stalled = {}, []
    for climate, name, temp, target, valve in rooms:
        t = known.get(climate)
        if not _waits(temp, target, valve, t is not None):
            continue
        if t is None or ts < t["since"] or temp >= t["ref"] + RISE_K - 1e-9:
            t = {"since": ts, "ref": temp}  # neu erkannt bzw. gestiegen: die Uhr beginnt neu
        seen[climate] = t
        if ts - t["since"] >= WINDOW_S:
            stalled.append(name)
    headroom = max(int(headroom), 0)
    due = last is None or ts >= last + STEP_S or ts < last
    if stalled and k < headroom and due:
        k, last = min(k + STEP_K, headroom), ts
    elif not seen and k > 0 and due:
        k, last = max(k - STEP_K, 0), ts
    if k > headroom:
        k, last = headroom, ts
    return {"k": k, "ts": last, "rooms": seen}, stalled


def text(k: int, stalled: list[str]) -> str:
    """„Zuschlag +4 K (Flur, Wohnzimmer kommen nicht hoch)“ – ohne Räume, solange er nur hält."""
    if not stalled:
        return f"Zuschlag +{k} K"
    verb = "kommt" if len(stalled) == 1 else "kommen"
    return f"Zuschlag +{k} K ({', '.join(stalled)} {verb} nicht hoch)"
