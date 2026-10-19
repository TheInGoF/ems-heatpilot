"""Hydraulischer Abgleich: welcher Raum zu wenig oder zu viel Heizwasser bekommt – und um welche Stufe.

Aus jedem plausiblen Raummodell folgt der nötige Vorlauf am kältesten Tag; wer deutlich über oder unter
dem Median liegt und dabei weit bzw. wenig öffnet, ist unter- oder überversorgt (``balance``). Seit 0.35
mit Stufen der Voreinstellung, grober Erwartung für die anderen Räume (``simulate_preset``) und dem
Merken von Änderungen (``record_preset_changes``). Am Ende stehen die Sätze der Abgleich-Karten
(Handlungssatz mit Stufe, Erwartung, Mitläufer, Wirkung einer Änderung); die Karten selbst setzt
``advice.build`` zusammen. Die Satzbausteine tragen einen Unterstrich, weil nur ``advice.build`` sie nutzt.
"""

from __future__ import annotations

import statistics
import time

from . import guards
from .schedule import OFF_TEMP
from .values import local as _local  # Ortszeit, gleiche Umrechnung wie advice._local

BALANCE_MIN_ROOMS = 3   # Abgleich nur im Vergleich mehrerer plausibler Räume
UNDER_K = 8.0           # so viel mehr Vorlauf als der Median -> unterversorgt
OVER_K = 10.0           # so viel weniger -> überversorgt
# Hydraulik (0.35): Voreinstellung geändert -> Pause, dann Wirkung zeigen, dann vergessen (Tage)
PRESET_PAUSE_DAYS = 10
PRESET_EFFECT_DAYS = 14
PRESET_FORGET_DAYS = 60
PRESET_CHANGES_KEY = "balance.changes"   # kv: [{ts, room, from, to, flow_before}]
PRESET_CHANGES_MAX = 50
# grobe Näherung: Voreinstellventil öffnet etwa quadratisch mit der Stufe, Heizkörperleistung ∝ Durchfluss^0.3
KV_EXP = 2.0
POWER_EXP = 0.3


def sample_hours(ts_list) -> float:
    """Stunden je Messpunkt aus dem typischen Abstand – unabhängig vom eingestellten Abtastintervall."""
    ts = sorted(ts_list)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if 0 < b - a <= 900]
    return (statistics.median(gaps) if gaps else 300) / 3600


def valve_stats(series, home) -> dict | None:
    """Ventilöffnung während der Raum Wärme will (jemand zu Hause, Ventil > 0)."""
    vals = [v for ts, _, _, v in series if v is not None and v > 0 and home.get(ts, True)]
    hours = len(vals) * sample_hours(s[0] for s in series)
    if hours < 12:  # mindestens 12 Stunden Heizbetrieb
        return None
    return {"mean_open": sum(vals) / len(vals), "wide_frac": sum(v >= 90 for v in vals) / len(vals),
            "hours": hours}


def balance(eng, rooms: dict, home: dict) -> dict[str, dict]:
    """Hydraulischer Abgleich: nötiger Vorlauf je Raum am kältesten Tag im Vergleich.

    Je Raum zusätzlich ``preset``/``preset_max`` (Voreinstellung des Schlüsselgeräts, None = unbekannt)."""
    design = eng.cfg.design_outdoor_temp
    floor = set(eng.cfg.floor_heating_rooms)
    data = {}
    for climate, series in rooms.items():
        model = eng.rooms.get(climate)
        if model is None or model.floor or climate in floor or not guards.check_room(model)["ok"]:
            continue
        stats = valve_stats(series, home)
        if stats is None:
            continue
        comfort = [t for ts, _, t, _ in series if t is not None and t > OFF_TEMP + 5 and home.get(ts, True)]
        target = statistics.median(comfort) if comfort else 21.0
        room = eng.found.room_of(climate)
        data[climate] = {**stats, "target": target, "flow": model.required_flow(target, design),
                         "preset": getattr(room, "preset", None), "preset_max": getattr(room, "preset_max", None)}
    if len(data) < BALANCE_MIN_ROOMS:
        return {}
    med = statistics.median(d["flow"] for d in data.values())
    for d in data.values():
        d["median"] = med
        d["diff"] = d["flow"] - med
        under = d["diff"] >= UNDER_K and (d["wide_frac"] >= 0.15 or d["mean_open"] >= 60)
        over = -d["diff"] >= OVER_K and d["mean_open"] <= 40
        d["state"] = "under" if under else "over" if over else "ok"
    if not any(d["state"] == "under" for d in data.values()):
        for d in data.values():  # ohne unterversorgten Raum ist ein Drosseln unnötig
            if d["state"] == "over":
                d["state"] = "ok"
    flows = sorted(d["flow"] for d in data.values())
    for d in data.values():
        d["system_flow"] = flows[-1]
        d["without_worst"] = flows[-2] if len(flows) > 1 else flows[-1]
    return data


# --- Hydraulik (0.35): Stufen, Wirkung, Änderungsverfolgung ---------------------------------------
def flow_shares(presets: dict[str, tuple]) -> dict[str, float]:
    """Durchflussanteil je Raum aus der Voreinstellung: s_i ∝ (Stufe/Stufen)^KV_EXP, Summe 1.

    ``presets``: {climate: (preset, preset_max)}; Räume ohne bekannte Stufe (None/0) zählen nicht."""
    kv = {c: (p / pm) ** KV_EXP for c, (p, pm) in presets.items() if p and pm and p > 0 and pm > 0}
    total = sum(kv.values())
    if total <= 0:
        return {}
    return {c: v / total for c, v in kv.items()}


def simulate_preset(data: dict, presets: dict, room: str, new_preset: int) -> dict[str, float]:
    """Grobe Wirkung einer Stufenänderung in ``room`` auf den nötigen Vorlauf aller Räume (ΔT in K).

    ``data``: Abgleichsdaten (``balance``: flow, target je Raum); ``presets``: {climate: (preset, preset_max)}.
    Heizkörperleistung ∝ Durchfluss^POWER_EXP, also skaliert die nötige Übertemperatur (T_req − T_room)
    mit (s_alt/s_neu)^POWER_EXP. Geänderter Raum sinkt, die anderen steigen leicht. Räume ohne
    Voreinstellung fehlen im Ergebnis (keine Division durch null, kein Raten)."""
    known = {c: presets[c] for c in data if c in presets and presets[c][0] and presets[c][1]}
    if room not in known or not new_preset or new_preset <= 0:
        return {}
    before = flow_shares(known)
    after = flow_shares({**known, room: (new_preset, known[room][1])})
    if not before or not after:
        return {}
    out = {}
    for c in known:
        over = max(float(data[c]["flow"]) - float(data[c]["target"]), 0.0)
        out[c] = over * ((before[c] / after[c]) ** POWER_EXP - 1.0)
    return out


def room_flow(eng, climate: str) -> float | None:
    """Nötiger Vorlauf des Raums am kältesten Tag laut Modell (Komforttemperatur der Einstellungen), wenn plausibel."""
    model = eng.rooms.get(climate)
    room = eng.found.room_of(climate)
    if model is None or room is None or not guards.check_room(model)["ok"]:
        return None
    return round(model.required_flow(room.comfort, eng.cfg.design_outdoor_temp), 1)


def presets_of(cfg) -> dict[str, int | None]:
    """Voreinstellung je climate aus den Raum-Einstellungen (None = unbekannt)."""
    out = {}
    for r in getattr(cfg, "rooms", None) or []:
        try:
            v = int(r.get("preset") or 0)
        except (TypeError, ValueError):
            v = 0
        if r.get("climate"):
            out[r["climate"]] = v or None
    return out


def preset_changes(store, now_ts: int | None = None) -> list[dict]:
    """Gemerkte Änderungen der Voreinstellung, nicht älter als PRESET_FORGET_DAYS, älteste zuerst."""
    now_ts = now_ts or int(time.time())
    rows = store.get(PRESET_CHANGES_KEY, []) or []
    out = []
    for c in rows if isinstance(rows, list) else []:
        try:  # kaputter Eintrag (z. B. von Hand editiert) darf die Empfehlungen nicht lahmlegen
            if isinstance(c, dict) and now_ts - int(c["ts"]) < PRESET_FORGET_DAYS * 86400:
                out.append(c)
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda c: int(c["ts"]))


def record_preset_changes(store, before: dict, after: dict, flows: dict, now_ts: int | None = None) -> list[dict]:
    """Beim Speichern der Einstellungen: geänderte Voreinstellungen in kv ``balance.changes`` merken.

    Gezählt wird nur ein Wechsel von einer bekannten auf eine andere bekannte Stufe – das erste Eintragen
    ist keine Änderung am Ventil. ``flows``: nötiger Vorlauf je climate vor der Änderung (``room_flow``).
    Gibt die neuen Einträge zurück."""
    now_ts = now_ts or int(time.time())
    new = []
    for climate, to in after.items():
        frm = before.get(climate)
        if frm and to and frm != to:
            new.append({"ts": now_ts, "room": climate, "from": int(frm), "to": int(to),
                        "flow_before": flows.get(climate)})
    if new:
        kept = preset_changes(store, now_ts) + new
        store.put(PRESET_CHANGES_KEY, kept[-PRESET_CHANGES_MAX:])
    return new


# --- Sätze für die Abgleich-Karten: advice.build setzt daraus die Karten zusammen ---------------------
def _fmt_k(k: float) -> str:
    """„−3 K“, „+0,5 K“, „±0 K“ – deutsches Komma, echtes Minus; ab 2 K ganze Kelvin (dieselbe Regel wie
    ``kelvin()`` in der Oberfläche)."""
    if abs(k) < 0.05:
        return "±0 K"
    txt = f"{k:+.0f}" if abs(k) >= 2 else f"{k:+.1f}"
    return txt.replace(".", ",").replace("-", "−") + " K"


def _expectation(bal: dict, climate: str, new_preset: int, names: dict) -> tuple[str, list[dict]]:
    """Satz „Erwartung (grob): …“ und die Zahlen dazu für den Beleg."""
    presets = {c: (d.get("preset"), d.get("preset_max")) for c, d in bal.items()}
    deltas = simulate_preset(bal, presets, climate, new_preset)
    if not deltas:
        return "", []
    order = sorted(deltas, key=lambda c: (c != climate, -abs(deltas[c])))
    # ``k``: fertig formatiert wie im Satz – die Chips der Oberfläche zeigen denselben Text (eine Rundungsregel)
    expected = [{"room": c, "room_name": names.get(c, c), "delta_k": round(deltas[c], 2), "k": _fmt_k(deltas[c])}
                for c in order]
    txt = " Erwartung (grob): " + ", ".join(f"{names.get(c, c)} ≈ {_fmt_k(deltas[c])}" for c in order) + \
        " Vorlauf. HeatPilot misst nach der Änderung nach."
    missing = [names.get(c, c) for c in bal if c not in deltas]
    if missing:
        txt += (f" Ohne Voreinstellung in den Einstellungen: {', '.join(missing)} – eintragen, dann rechnet "
                f"HeatPilot auch deren Wirkung.")
    return txt, expected


def _most_over(bal: dict, climate: str, names: dict) -> str:
    """Alternative, wenn das Ventil am Maximum steht: den Raum mit dem größten Überschuss drosseln."""
    cands = [(d["flow"], c) for c, d in bal.items()
             if c != climate and d.get("preset_max") != 0 and (d.get("preset") is None or d["preset"] > 1)]
    if not cands:
        return "einen Raum mit Überschuss eine Stufe zurück (im Vergleich fällt keiner auf)"
    _, other = min(cands)
    p, pm = bal[other].get("preset"), bal[other].get("preset_max")
    step = f" (von {p} auf {p - 1}, Skala 1–{pm})" if p and pm else ""
    return f"{names.get(other, other)} – der Raum mit dem größten Überschuss – eine Stufe zurück{step}"


def _member_note(eng, climate: str) -> str:
    """Mitläufer mit eigener Voreinstellung: die Empfehlung nennt das Gerät."""
    room = eng.found.room_of(climate)
    if room is None or not room.members:
        return ""
    def friendly(c):
        return (eng.states.get(c) or {}).get("attributes", {}).get("friendly_name", c)
    own = [f"{friendly(m['climate'])}: {m['preset']}/{m['preset_max']}" for m in room.members
           if m.get("preset") and m.get("preset_max")]
    if not own:
        return ""
    return (f" Die Stufe gilt für {friendly(room.climate)}; weitere Ventile im Raum: {', '.join(own)} – "
            f"Änderungen dort als eigenes Gerät betrachten.")


def _preset_action(d: dict, up: bool, bal: dict, climate: str, names: dict) -> tuple[str, dict | None, list[dict]]:
    """Handlungssatz mit Stufe. Gibt (Text, Schritt {from, to, max} | None, Erwartung je Raum)."""
    p, pm = d.get("preset"), d.get("preset_max")
    hint = "Voreinstellung und Stufen in den Einstellungen eintragen, dann nennt HeatPilot die Stufe und rechnet die Wirkung auf die anderen Räume."
    if pm is None:
        if up:
            return ("Wahrscheinlich ist die Voreinstellung (Durchflussbegrenzer am Ventilunterteil) zu weit zu: eine "
                    "Stufe höher stellen. " + hint), None, []
        return "Voreinstellung am Ventilunterteil eine Stufe zurück (kleinere Zahl). " + hint, None, []
    if pm == 0:
        if up:
            return ("Der Heizkörper hat kein Voreinstellventil: die Rücklaufverschraubung etwas weiter öffnen "
                    "(Viertelumdrehung, am besten vom Fachbetrieb)."), None, []
        return ("Der Heizkörper hat kein Voreinstellventil: die Rücklaufverschraubung etwas zudrehen "
                "(Viertelumdrehung, am besten vom Fachbetrieb)."), None, []
    if p is None:
        word = "höher stellen" if up else "zurück (kleinere Zahl)"
        return (f"Voreinstellung eine Stufe {word} (Skala 1–{pm}). Die aktuelle Stufe in den Einstellungen "
                f"eintragen, dann rechnet HeatPilot die Wirkung auf die anderen Räume."), None, []
    if up and p >= pm:
        return (f"Die Voreinstellung steht schon auf {pm}/{pm} – mehr geht am Ventil nicht. Alternativen: "
                f"Heizkörper entlüften; {_most_over(bal, climate, names)}; Pumpenstufe oder Vorlauf prüfen."), None, []
    to = p + 1 if up else p - 1
    exp_txt, expected = _expectation(bal, climate, to, names)
    return f"Voreinstellung von {p} auf {to} stellen (Skala 1–{pm}).{exp_txt}", {"from": p, "to": to, "max": pm}, expected


def _effect_text(eng, climate: str, ch: dict) -> tuple[str, dict]:
    """„Wirkung: braucht jetzt X °C statt Y °C Vorlauf“ nach einer Änderung (ab PRESET_EFFECT_DAYS)."""
    now_flow = room_flow(eng, climate)
    when = _local(int(ch["ts"])).strftime("%d.%m.")
    ev = {"Vorlauf vorher (°C)": None if ch.get("flow_before") is None else round(ch["flow_before"]),
          "Vorlauf jetzt (°C)": None if now_flow is None else round(now_flow),
          "Voreinstellung": f"{ch.get('from')} → {ch.get('to')}", "geändert am": when}
    if now_flow is None or ch.get("flow_before") is None:
        return (f"Wirkung der Voreinstellung ({ch.get('from')} → {ch.get('to')} am {when}) noch nicht messbar – "
                f"das Raummodell ist noch nicht plausibel oder der Vorlauf vorher war unbekannt."), ev
    return (f"Wirkung: braucht jetzt {now_flow:.0f} °C statt {ch['flow_before']:.0f} °C Vorlauf am kältesten Tag "
            f"(Voreinstellung am {when} von {ch.get('from')} auf {ch.get('to')})."), ev
