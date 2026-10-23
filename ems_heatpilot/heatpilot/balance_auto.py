"""Automatischer hydraulischer Abgleich (seit 0.51): lernende Öffnungs-Deckel für ventilgeführte Räume.

Ohne Durchflussmessung je Raum bleibt nur die Beobachtung übers Haus: Ein Raum ist **unterversorgt**, wenn er trotz
Ventil am Anschlag (an seinem Deckel) spürbar unter Soll bleibt; ein Raum hat es **leicht**, wenn er sein Soll mit
wenig Öffnung hält. Kommt beides am selben Tag zusammen, nehmen die leichten Räume den unterversorgten Wasser weg –
dann senkt HeatPilot die Deckel der leichten Räume in kleinen Schritten (wie eine Voreinstellung am Ventilunterteil,
nur automatisch und umkehrbar). Bleibt die Unterversorgung ``RAISE_DAYS`` Tage aus, gehen die Deckel schrittweise
zurück Richtung 100 %.

Der Deckel begrenzt nur den PID-Ausgang (``valve_pid_control``); Räume über das Soll hinaus drosselt der PID ohnehin.
Beobachtet wird je 30-s-Schritt (``observe``), entschieden einmal je Tageswechsel (``_rebalance``). Zustand
{caps, day, obs, calm_days, last} läuft über ``valve_pid_control`` (kv) weiter. Der handgeführte Abgleich
(``balance``: Empfehlungen für die Voreinstellung) bleibt für temperaturgeführte Räume unverändert.
"""

from __future__ import annotations

from .values import local

STARVE_K = 0.3      # unterversorgt: so weit unter Soll …
STARVE_MIN_S = 1800  # … mit Ventil am Deckel, mindestens so lange am Tag
EASY_PCT = 60.0     # leicht: hält das Soll mit höchstens so viel Öffnung …
EASY_MIN_S = 3600   # … mindestens so lange am Tag
STEP_PCT = 5        # Deckel je Tag höchstens um so viel senken bzw. anheben
CAP_MIN = 40        # nie weiter drosseln als bis hierhin
RAISE_DAYS = 3      # so viele ruhige Tage in Folge, dann Deckel wieder anheben


def cap(st: dict, climate: str) -> float:
    """Öffnungs-Deckel eines Raums in % (100 = ungedrosselt)."""
    return float((st.get("caps") or {}).get(climate, 100.0))


def observe(st: dict, rooms: list[tuple], ts: int) -> None:
    """Einen 30-s-Schritt verbuchen; beim Tageswechsel vorher den Tag abrechnen (``_rebalance``).

    ``rooms``: (climate, Ist, Soll, PID-Ausgang in %, Deckel) der ventilgeführten Räume mit Messwert."""
    day = local(ts).date().isoformat()
    if st.get("day") not in (None, day):
        _rebalance(st)
        st["obs"] = {}
    st["day"] = day
    dt = min(max(ts - st.get("ts", ts), 0), 120)
    st["ts"] = ts
    obs = st.setdefault("obs", {})
    for climate, temp, sp, out, limit in rooms:
        o = obs.setdefault(climate, {"starve_s": 0, "easy_s": 0})
        if temp < sp - STARVE_K and out >= min(limit, 100.0) - 1.0:
            o["starve_s"] += dt
        elif temp >= sp - 0.1 and out <= EASY_PCT:
            o["easy_s"] += dt


def _rebalance(st: dict) -> None:
    """Tagesabschluss: Deckel der leichten Räume senken, solange ein Raum unterversorgt war – sonst nach
    ``RAISE_DAYS`` ruhigen Tagen wieder anheben (siehe Modulbeschreibung)."""
    obs, caps = st.get("obs") or {}, st.setdefault("caps", {})
    starved = sorted(c for c, o in obs.items() if o["starve_s"] >= STARVE_MIN_S)
    easy = sorted(c for c, o in obs.items() if o["easy_s"] >= EASY_MIN_S and c not in starved)
    if starved and easy:
        lowered = []
        for c in easy:
            new = max(cap(st, c) - STEP_PCT, CAP_MIN)
            if new < cap(st, c):
                caps[c] = new
                lowered.append(c)
        st["calm_days"] = 0
        st["last"] = {"day": st.get("day"), "starved": starved, "lowered": lowered}
        return
    if starved:
        st["calm_days"] = 0
        st["last"] = {"day": st.get("day"), "starved": starved, "lowered": []}
        return
    st["calm_days"] = int(st.get("calm_days", 0)) + 1
    if st["calm_days"] >= RAISE_DAYS and caps:
        for c in list(caps):
            caps[c] = min(caps[c] + STEP_PCT, 100.0)
            if caps[c] >= 100.0:
                caps.pop(c)
        st["calm_days"] = 0
        st["last"] = {"day": st.get("day"), "starved": [], "raised": True}


def text(st: dict, names: dict[str, str]) -> str | None:
    """Eine Zeile für die Oberfläche – None, solange alle Deckel auf 100 % stehen."""
    caps = st.get("caps") or {}
    if not caps:
        return None
    parts = ", ".join(f"{names.get(c, c)} {v:.0f} %" for c, v in sorted(caps.items(), key=lambda x: x[1]))
    last = st.get("last") or {}
    why = f" – {', '.join(names.get(c, c) for c in last['starved'])} kam nicht mit" if last.get("starved") else ""
    return f"Abgleich: {parts}{why}"
