"""Empfehlungen an Tado: was du einstellen kannst, wenn HeatPilot über den Kessel nicht weiterkommt.

Tado entscheidet, *wann* ein Raum Wärme zieht. Fängt Tado zu spät an, hilft auch
ein höherer Vorlauf nur begrenzt. HeatPilot misst deshalb je Raum und schlägt
konkrete Änderungen in Tado vor – es stellt selbst nichts ein.

1. **Zeitplan / Frühstart:** Bei jedem Soll-Anstieg (Zeitplanblock) – wie lange,
   bis der Raum warm ist? Öffnet das Ventil schon *vor* dem Soll-Wechsel, ist der
   Frühstart für den Raum aktiv.
2. **Geofencing:** Bei jeder echten Heimkehr – wie viele Minuten vorher hob Tado
   das Soll an, wie warm war der Raum bei Ankunft, wie hoch ist das Soll bei
   Abwesenheit?
3. **Soll nicht erreichbar:** Raum bleibt über Stunden unter Soll, obwohl das
   Ventil voll offen ist.
4. **Hydraulischer Abgleich:** In einem abgeglichenen Haus brauchen alle Räume
   etwa denselben Vorlauf für ihre Wunschtemperatur. Aus jedem Raummodell folgt,
   welchen Vorlauf der Raum am kältesten Tag bräuchte. Braucht einer deutlich mehr
   als die anderen und steht sein Ventil oft weit offen, bekommt er zu wenig
   Heizwasser (Voreinstellung eine Stufe auf); braucht einer viel weniger und
   öffnet nur wenig, nimmt er den anderen Wasser weg (eine Stufe zu).
   Seit 0.35 mit konkreten Stufen (Voreinstellung/Stufen aus den Einstellungen),
   grober Erwartung für die anderen Räume (``simulate_preset``) und Nachmessen:
   nach einer Änderung ``PRESET_PAUSE_DAYS`` keine neue Empfehlung, danach „Wirkung“.

Leitplanken: erst ab ``MIN_EVENTS`` Beobachtungen (letzte ``WINDOW_DAYS`` Tage,
ohne Urlaub), kleine Schritte (max. 30 min bzw. ein Reglerschritt), keine
widersprüchlichen Ratschläge, jede Empfehlung mit Beleg.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from datetime import datetime

from . import guards
from .schedule import OFF_TEMP

WINDOW_DAYS = 21
MIN_EVENTS = 4
RISE_K = 1.0            # Soll-Anstieg, der als Zeitplan-/Geofencing-Wechsel zählt
WARM_MARGIN = 0.3       # "warm" = höchstens so weit unter Soll
LATE_MIN = 20           # ab so viel Verspätung empfehlen
EARLY_MIN = 45          # ab so viel "zu früh warm" empfehlen
ARRIVAL_COLD_K = 0.7    # bei Ankunft so viel zu kalt -> Richtung Komfort
ARRIVAL_WARM_MIN = 60   # so lange vor Ankunft schon warm -> Richtung Eco
AWAY_GAP_MIN = 120      # vorher so lange niemand da = echte Heimkehr
MAX_SHIFT_MIN = 30
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


def _local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _clock(ts: int) -> str:
    return _local(ts).strftime("%H:%M")


def _round5(minutes: float) -> int:
    return int(min(max(5 * round(minutes / 5), 10), MAX_SHIFT_MIN))


def _load(store, since: int, rooms_sel: set[str] | None = None, persons: list[str] | None = None):
    rooms: dict[str, list[tuple]] = defaultdict(list)
    for r in store.query(
        "SELECT ts, room, temp, target, valve FROM room_samples WHERE ts >= ? ORDER BY ts", (since,)
    ):
        if rooms_sel is None or r["room"] in rooms_sel:  # abgewählte Räume zählen nicht
            # Tado „aus“ (Zeitplanblock oder Geofencing): kein Soll, aber Raumtemperatur -> Frostschutz
            target = r["target"] if r["target"] is not None or r["temp"] is None else OFF_TEMP
            rooms[r["room"]].append((r["ts"], r["temp"], target, r["valve"]))
    home = store.home_by_ts(since, persons)
    vacation = {r["ts"] for r in store.query(
        "SELECT ts FROM samples WHERE ts >= ? AND vacation = 1", (since,))}
    flow = {}
    for r in store.query("SELECT ts, flow, dhw, sel_flow, calc_flow FROM samples WHERE ts >= ?", (since,)):
        if r["flow"] is None or r["dhw"]:
            continue  # Ventil auf Warmwasser: dieser Vorlauf erreicht die Heizkörper nicht
        if r["sel_flow"] is not None and r["calc_flow"] is not None and r["sel_flow"] > r["calc_flow"] + 15:
            continue  # nicht markierte Speicherladung (Kessel-Soll weit über dem Heizkreis)
        flow[r["ts"]] = r["flow"]
    return rooms, home, vacation, flow


def _arrivals(home: dict[int, bool], vacation: set[int]) -> list[int]:
    """Echte Heimkehr: jemand kommt, nachdem mindestens AWAY_GAP_MIN niemand da war."""
    out, away_since = [], None
    for ts in sorted(home):
        if ts in vacation:
            away_since = None
            continue
        if not home[ts]:
            away_since = away_since or ts
        else:
            if away_since is not None and ts - away_since >= AWAY_GAP_MIN * 60:
                out.append(ts)
            away_since = None
    return out


PREHEAT_VALVE = 20  # % Ventil: darunter heizt Tado praktisch nicht
DROPOUT_MIN = 15    # so kurz „aus“ ist ein Aussetzer, kein Zeitplanblock – und so lange darf Tado bei der
#                     Heimkehr noch „aus“ sein, bevor es das Soll anhebt (Geofencing). Nach Zeit, nicht Messpunkten


def _first_warm(series, i0: int, target: float, horizon_s: int = 4 * 3600) -> int | None:
    t0 = series[i0][0]
    for ts, temp, _, _ in series[i0:]:
        if ts - t0 > horizon_s:
            return None
        if temp is not None and temp >= target - WARM_MARGIN:
            return ts
    return None


def schedule_events(series, home, vacation) -> list[dict]:
    """Soll-Anstiege, während jemand zu Hause ist (Zeitplanblöcke)."""
    events = []
    for i in range(1, len(series)):
        ts, temp, target, _ = series[i]
        prev_target = series[i - 1][2]
        if None in (target, prev_target, temp) or ts in vacation:
            continue
        if target - prev_target < RISE_K or not home.get(ts, True):
            continue
        from_off = prev_target <= OFF_TEMP
        if from_off:  # kurzer Aussetzer (≤ DROPOUT_MIN „aus“, danach dasselbe Soll): kein neuer Zeitplanblock
            back = None
            for tj, _, tg, _ in reversed(series[:i]):  # nach Zeit zurück, nicht nach Messpunkten (Abtastrate)
                if ts - tj > DROPOUT_MIN * 60:
                    break
                if tg is not None and tg > OFF_TEMP:
                    back = tg
                    break
            if back is not None and abs(back - target) < 0.1:
                continue
        if target - temp < 0.8:  # war schon (fast) warm
            warm_since = None
            for j in range(i - 1, -1, -1):
                tj, tempj, _, _ = series[j]
                if tempj is None or tempj < target - WARM_MARGIN or ts - tj > 4 * 3600:
                    break
                warm_since = tj
            # Nur Verschwendung, wenn Tado vorher wirklich geheizt hat (Ventil offen, obwohl das
            # bisherige Soll erreicht war = Frühstart). Hält der Raum die Wärme von selbst oder
            # hat ihn die Sonne gewärmt, ist der Zeitplanblock nur ein Soll – es fließt keine Wärme.
            preheat = [tj for tj, tempj, tgtj, vj in series[max(0, i - 48):i]
                       if ts - tj <= 4 * 3600 and vj is not None and vj >= PREHEAT_VALVE
                       and tempj is not None and tgtj is not None and tempj >= tgtj - WARM_MARGIN]
            heated = bool(preheat) and warm_since is not None
            events.append({"ts": ts, "late_min": 0.0, "early_open": heated, "reached": True,
                           "warm_anyway": not heated, "from_off": from_off, "warm_at": ts,
                           "warm_ahead_min": (ts - max(warm_since, min(preheat))) / 60 if heated else 0.0})
            continue
        warm_at = _first_warm(series, i, target)
        late = (warm_at - ts) / 60 if warm_at else 240.0
        # Frühstart: Ventil öffnet in der Stunde davor, obwohl das bisherige Soll erreicht ist
        # (sonst zieht der Raum nur Wärme für das alte Soll)
        early_open = any(
            v is not None and v >= 20 and tp is not None and tgt is not None and tp >= tgt - WARM_MARGIN
            for t, tp, tgt, v in series[max(0, i - 12):i] if ts - t <= 3600
        )
        events.append({"ts": ts, "late_min": late, "early_open": early_open, "from_off": from_off,
                       "warm_at": warm_at, "reached": warm_at is not None, "warm_ahead_min": 0.0})
    _early_start_from_off(events)
    return events


def _early_start_from_off(events: list[dict]) -> None:
    """Tado-Frühstart aus „aus“: Tado setzt dann das neue Soll früher, statt vorher das Ventil zu öffnen.

    Der Zeitplanblock liegt deshalb beim spätesten Anstieg innerhalb von 90 min – je kälter der Raum,
    desto früher springt das Soll. Verspätung und Frühstart werden an diesem Block gemessen, sonst
    sähe der Frühstart wie ein zu spät beginnender Block aus.

    Zusammengefasst wird je Wochentag, sobald jeder vorkommende Wochentag der Art (Werktag bzw.
    Wochenende) mindestens zwei Anstiege hat – Tado erlaubt je Tag eigene Blöcke (Sa 07:30, So 09:00;
    Fr später als Mo–Do), die sonst zu einem falschen Block verschmölzen. Mit weniger Daten wie
    bisher Werktage bzw. Wochenende gemeinsam.
    """
    per_day: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        if e["from_off"]:
            per_day[_local(e["ts"]).weekday()].append(e)
    groups: list[list[int]] = []
    for weekday in (True, False):
        days = [d for d in range(7) if (d < 5) == weekday]
        if all(len(per_day[d]) >= 2 for d in days if per_day[d]):
            groups += [[d] for d in days]
        else:
            groups.append(days)
    for days in groups:
        offs = sorted((e for d in days for e in per_day[d]), key=lambda e: _minute(e["ts"]))
        cluster: list[dict] = []
        for e in offs + [None]:
            if e is not None and (not cluster or _minute(e["ts"]) - _minute(cluster[0]["ts"]) <= 90):
                cluster.append(e)
                continue
            if len(cluster) >= 2:
                nominal = max(_minute(c["ts"]) for c in cluster)
                for c in cluster:
                    shift = nominal - _minute(c["ts"])
                    c["block_ts"] = c["ts"] + shift * 60
                    if shift >= 10:
                        c["early_open"] = True
                    if c["warm_at"] is not None:
                        c["late_min"] = max((c["warm_at"] - c["block_ts"]) / 60, 0.0)
            cluster = [e] if e is not None else []


def _minute(ts: int) -> int:
    d = _local(ts)
    return d.hour * 60 + d.minute


def arrival_events(series, arrivals) -> list[dict]:
    idx = {s[0]: k for k, s in enumerate(series)}
    out = []
    for a in arrivals:
        k = idx.get(a)
        if k is None:
            continue
        _, temp, target, _ = series[k]
        if temp is None or target is None:
            continue
        if target <= OFF_TEMP:
            # Tado bei der Ankunft (noch) aus: kurz nachschauen, sonst ist das keine Geofencing-Heimkehr
            later = None
            for s in series[k:]:
                if s[0] - a > DROPOUT_MIN * 60:
                    break
                if s[2] is not None and s[2] > OFF_TEMP:
                    later = s[2]
                    break
            if later is None:
                continue
            target = later
        lead = None
        for j in range(k, 0, -1):
            tj, _, tgt, _ = series[j]
            if a - tj > 3 * 3600:
                break
            prev = series[j - 1][2]
            if tgt is not None and prev is not None and tgt - prev >= RISE_K:
                lead = (a - tj) / 60
                break
        warm_ahead = 0.0
        for j in range(k, -1, -1):
            tj, tempj, _, _ = series[j]
            if tempj is None or tempj < target - WARM_MARGIN or a - tj > 4 * 3600:
                break
            warm_ahead = (a - tj) / 60
        # "zu früh warm" zählt nur nach Tados Soll-Anhebung – war der Raum gar nicht
        # abgesenkt, ist das kein zu frühes Vorheizen
        warm_ahead = min(warm_ahead, lead) if lead is not None else 0.0
        out.append({"ts": a, "deficit": target - temp, "lead_min": lead, "warm_ahead_min": warm_ahead})
    return out


def away_setpoints(series, home, vacation) -> list[float]:
    """Soll, während seit mindestens 2 h niemand zu Hause ist (ohne Urlaub)."""
    vals, away_since = [], None
    for ts, _, target, _ in series:
        h = home.get(ts)
        if h is None or ts in vacation:
            away_since = None
            continue
        if h:
            away_since = None
            continue
        away_since = away_since or ts
        if ts - away_since >= AWAY_GAP_MIN * 60 and target is not None:
            vals.append(target)
    return vals


def unreachable_episodes(series, home, flow) -> list[dict]:
    """Über 2 h unter Soll, Ventil voll offen, jemand zu Hause."""
    eps, start, worst = [], None, 0.0
    for ts, temp, target, valve in series:
        bad = (temp is not None and target is not None and valve is not None
               and target - temp >= 0.5 and valve >= 90 and home.get(ts, True))
        if bad:
            start = start or ts
            worst = max(worst, target - temp)
        else:
            if start and ts - start >= 2 * 3600:
                fl = [v for t, v in flow.items() if start <= t < ts and v is not None]
                eps.append({"ts": start, "hours": (ts - start) / 3600, "deficit": worst,
                            "flow_max": max(fl) if fl else None})
            start, worst = None, 0.0
    return eps


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


def _fmt_k(k: float) -> str:
    """„−3 K“, „+0,5 K“, „±0 K“ – deutsches Komma, echtes Minus; ab 2 K ganze Kelvin (dieselbe Regel wie
    ``kelvin()`` in der Oberfläche)."""
    if abs(k) < 0.05:
        return "±0 K"
    txt = f"{k:+.0f}" if abs(k) >= 2 else f"{k:+.1f}"
    return txt.replace(".", ",").replace("-", "−") + " K"


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


def build(eng, now_ts: int | None = None) -> list[dict]:
    """Empfehlungen je Raum, mit Beleg. ``eng``: Engine (store, rooms, found, cfg).

    Einträge: {room, room_name, kind, impact, title, text, evidence} – Abgleich (0.35) zusätzlich
    ``preset`` {from, to, max} | None und ``expected`` [{room, room_name, delta_k}]; Info-Karten
    (impact „Info“, kind abgleich_misst/abgleich_wirkung) mit ``since`` = Zeitpunkt der Änderung."""
    now_ts = now_ts or int(time.time())
    since = now_ts - WINDOW_DAYS * 86400
    rooms, home, vacation, flow = _load(eng.store, since, {r.climate for r in eng.found.rooms},
                                        eng.found.persons)
    arrivals = _arrivals(home, vacation)
    names = {r.climate: r.name for r in eng.found.rooms}
    out: list[dict] = []

    def add(room, kind, impact, title, text, evidence, **extra):
        out.append({"room": room, "room_name": names.get(room, room), "kind": kind,
                    "impact": impact, "title": title, "text": text, "evidence": evidence, **extra})

    bal = balance(eng, rooms, home)
    design = eng.cfg.design_outdoor_temp
    # Hydraulik (0.35): jüngste gemerkte Änderung der Voreinstellung je Raum (Schlüssel-climate)
    changes: dict[str, dict] = {}
    for ch in preset_changes(eng.store, now_ts):
        r = eng.found.room_of(ch.get("room", ""))
        key = r.climate if r else ch.get("room")
        if key in names and (key not in changes or ch["ts"] > changes[key]["ts"]):
            changes[key] = ch
    advised: set[str] = set()
    for climate, d in bal.items():
        name = names.get(climate, climate)
        ch = changes.get(climate)
        age = now_ts - int(ch["ts"]) if ch else None
        if age is not None and age < PRESET_PAUSE_DAYS * 86400:
            continue  # Voreinstellung gerade geändert: HeatPilot misst neu (Info-Karte unten)
        effect = ""
        if age is not None and PRESET_EFFECT_DAYS * 86400 <= age < PRESET_FORGET_DAYS * 86400:
            effect = " " + _effect_text(eng, climate, ch)[0]
        ev = {"nötiger Vorlauf am kältesten Tag (°C)": round(d["flow"]),
              "Median aller Räume (°C)": round(d["median"]),
              "Ventil im Mittel offen (%)": round(d["mean_open"]),
              "Heizzeit mit Ventil über 90 % (%)": round(100 * d["wide_frac"]),
              "Heizstunden ausgewertet": round(d["hours"])}
        if d.get("preset_max"):
            ev["Voreinstellung (Stufe/Stufen)"] = f"{d['preset'] or '?'}/{d['preset_max']}"
        if d["state"] == "under":
            gain = ""
            if d["flow"] >= d["system_flow"] - 0.5 and d["system_flow"] - d["without_worst"] >= 3:
                gain = (f" Er bestimmt den Vorlauf fürs ganze Haus: besser versorgt, reichen am kältesten "
                        f"Tag ca. {d['without_worst']:.0f} °C statt {d['system_flow']:.0f} °C.")
            action, step, expected = _preset_action(d, True, bal, climate, names)
            advised.add(climate)
            add(climate, "abgleich", "Abgleich", f"{name}: bekommt zu wenig Heizwasser",
                f"Um {d['target']:.0f} °C bei {design:.0f} °C außen zu halten, braucht der Raum laut Modell "
                f"{d['flow']:.0f} °C Vorlauf – {d['diff']:.0f} K mehr als die anderen Räume (Median "
                f"{d['median']:.0f} °C). Das Ventil steht dabei oft weit offen. {action} "
                f"Vorher prüfen, ob der Heizkörper entlüftet ist und nicht zugestellt ist.{gain}"
                f"{_member_note(eng, climate)}{effect}"
                + ("" if expected else " Danach misst HeatPilot 1–2 Wochen neu."),
                ev, preset=step, expected=expected)
        elif d["state"] == "over":
            if d.get("preset") == 1:
                continue  # steht schon auf Stufe 1: „zurück“ nicht empfehlen
            action, step, expected = _preset_action(d, False, bal, climate, names)
            advised.add(climate)
            add(climate, "abgleich", "Abgleich", f"{name}: bekommt mehr Heizwasser als nötig",
                f"Der Raum braucht laut Modell nur {d['flow']:.0f} °C Vorlauf – {-d['diff']:.0f} K weniger als "
                f"die anderen (Median {d['median']:.0f} °C) – und öffnet im Mittel nur {d['mean_open']:.0f} %. "
                f"Wenn er öffnet, zieht er viel Wasser, das unterversorgten Räumen fehlt. {action}"
                f"{_member_note(eng, climate)}{effect}"
                + ("" if expected else " Danach misst HeatPilot 1–2 Wochen neu."),
                ev, preset=step, expected=expected)

    # Hydraulik (0.35): Info-Karten – „misst neu“ in der Pause, „Wirkung“ ab PRESET_EFFECT_DAYS
    for climate, ch in changes.items():
        age = now_ts - int(ch["ts"])
        name = names.get(climate, climate)
        when = _local(int(ch["ts"])).strftime("%d.%m.")
        if age < PRESET_PAUSE_DAYS * 86400:
            add(climate, "abgleich_misst", "Info", f"{name}: Voreinstellung geändert – HeatPilot misst neu",
                f"Am {when} von {ch.get('from')} auf {ch.get('to')} gestellt. {PRESET_PAUSE_DAYS} Tage lang keine "
                f"neue Abgleich-Empfehlung für diesen Raum; ab {PRESET_EFFECT_DAYS} Tagen zeigt HeatPilot die Wirkung "
                f"auf den nötigen Vorlauf.",
                {"Voreinstellung": f"{ch.get('from')} → {ch.get('to')}", "geändert am": when,
                 "Vorlauf vorher (°C)": None if ch.get("flow_before") is None else round(ch["flow_before"])},
                since=int(ch["ts"]), preset={"from": ch.get("from"), "to": ch.get("to"), "max": None})
        elif PRESET_EFFECT_DAYS * 86400 <= age < PRESET_FORGET_DAYS * 86400 and climate not in advised:
            text, ev = _effect_text(eng, climate, ch)
            add(climate, "abgleich_wirkung", "Info", f"{name}: Wirkung der Voreinstellung", text, ev,
                since=int(ch["ts"]), preset={"from": ch.get("from"), "to": ch.get("to"), "max": None})

    for climate, series in rooms.items():
        name = names.get(climate, climate)

        # 0. Soll nicht erreichbar – geht vor: Tado-Einstellungen helfen dann nicht
        eps = [e for e in unreachable_episodes(series, home, flow) if e["ts"] >= now_ts - 7 * 86400]
        if len(eps) >= 3:
            flow_max = max((e["flow_max"] for e in eps if e["flow_max"] is not None), default=None)
            limit = flow_max is not None and flow_max >= eng.cfg.max_flow_temp - 5
            low_curve = flow_max is not None and flow_max < eng.cfg.max_flow_temp - 15
            if limit:
                cause = "Der Vorlauf war dabei am Limit – Soll senken oder Heizkörperleistung prüfen."
            elif low_curve:
                cause = (f"Der Vorlauf lag dabei nur bei höchstens {flow_max:.0f} °C (möglich: "
                         f"{eng.cfg.max_flow_temp} °C) – wahrscheinlich liefert die Heizkurve des Reglers zu "
                         f"wenig. Das wäre eine Einstellung am Regler (Heizkurve/Auslegungstemperatur) – nur "
                         f"ändern, wenn du sicher bist. Sonst Heizkörper entlüften und Ventil prüfen.")
            elif bal.get(climate, {}).get("state") == "under":
                cause = ("Der Raum bekommt im Vergleich zu wenig Heizwasser – siehe Empfehlung zum "
                         "hydraulischen Abgleich (Voreinstellung am Ventil).")
            else:
                cause = "Heizkörper entlüften, Ventil/Thermostat prüfen – oder Soll senken."
            add(climate, "nicht_erreichbar", "Komfort", f"{name}: Soll wird nicht erreicht",
                f"{len(eps)}× in 7 Tagen über Stunden bis {max(e['deficit'] for e in eps):.1f} K unter Soll, "
                f"obwohl das Ventil voll offen war. " + cause
                + " Zeitplan- oder Geofencing-Änderungen helfen hier nicht, deshalb gibt es dafür keine "
                  "weiteren Empfehlungen für diesen Raum.",
                {"Episoden (7 Tage)": len(eps), "höchster Vorlauf (°C)": flow_max})
            continue

        # 1. Zeitplan / Frühstart – nach Uhrzeit-Block gruppiert (Werktag/Wochenende, halbe Stunde)
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for ev in schedule_events(series, home, vacation):
            d = _local(ev.get("block_ts", ev["ts"]))
            groups[(d.weekday() < 5, d.hour * 2 + d.minute // 30)].append(ev)
        for (weekday, slot), evs in groups.items():
            if len(evs) < MIN_EVENTS:
                continue
            when = f"{'Mo–Fr' if weekday else 'Sa–So'} {slot // 2:02d}:{30 * (slot % 2):02d}"
            # Raum war ohnehin warm und Tado hat nicht geheizt: kein Komfort-, kein Sparthema
            needy = [e for e in evs if not e.get("warm_anyway")]
            if len(needy) < MIN_EVENTS:
                continue
            evs = needy
            late = statistics.median(e["late_min"] for e in evs)
            ahead = statistics.median(e["warm_ahead_min"] for e in evs)
            early_frac = sum(e["early_open"] for e in evs) / len(evs)
            reached = sum(e["reached"] for e in evs) / len(evs)
            ev_txt = {"Beobachtungen": len(evs), "Verspätung Median (min)": round(late),
                      "Frühstart erkannt (%)": round(100 * early_frac)}
            if reached < 0.5:
                continue  # wird meist gar nicht warm: Leistungsfrage, kein Zeitplan-Problem
            if late >= LATE_MIN:
                shift = _round5(late)
                step = " (erster Schritt – danach prüft HeatPilot erneut)" if late > MAX_SHIFT_MIN else ""
                if early_frac < 0.3:
                    add(climate, "frühstart", "Komfort", f"{name}: zum Block {when} zu spät warm",
                        f"Im Mittel {late:.0f} min nach dem Soll-Wechsel warm. Der Frühstart scheint "
                        f"für diesen Raum aus zu sein – in Tado einschalten, oder den Block um {shift} min "
                        f"vorziehen{step}.", ev_txt)
                else:
                    add(climate, "zeitplan", "Komfort", f"{name}: zum Block {when} zu spät warm",
                        f"Frühstart ist aktiv, reicht aber nicht: im Mittel {late:.0f} min zu spät. "
                        f"Block in Tado um {shift} min vorziehen{step}.", ev_txt)
            elif ahead >= EARLY_MIN:
                shift = _round5(ahead - 15)
                add(climate, "zeitplan", "Sparen", f"{name}: zum Block {when} unnötig früh geheizt",
                    f"Tado heizt vor dem Block schon mit offenem Ventil, der Raum ist im Mittel {ahead:.0f} min "
                    f"vor dem Block warm. Block um {shift} min später legen oder den Frühstart für diesen Raum "
                    f"ausschalten.",
                    {**ev_txt, "früher warm Median (min)": round(ahead)})

        # 2. Geofencing
        arr = arrival_events(series, arrivals)
        cold = False
        if len(arr) >= MIN_EVENTS:
            deficit = statistics.median(a["deficit"] for a in arr)
            ahead = statistics.median(a["warm_ahead_min"] for a in arr)
            leads = [a["lead_min"] for a in arr if a["lead_min"] is not None]
            lead_txt = f"Tado hebt das Soll im Mittel {statistics.median(leads):.0f} min vor Ankunft an" \
                if leads else "Tado hebt das Soll vor Ankunft nicht erkennbar an"
            ev_txt = {"Heimkehren": len(arr), "zu kalt bei Ankunft Median (K)": round(deficit, 1),
                      "Soll-Anhebung vor Ankunft Median (min)": round(statistics.median(leads)) if leads else None}
            if deficit >= ARRIVAL_COLD_K:
                cold = True
                add(climate, "geofencing", "Komfort", f"{name}: bei Ankunft zu kalt",
                    f"Bei Heimkehr im Mittel {deficit:.1f} K unter Soll. {lead_txt}. In Tado die "
                    f"Vorheiz-Einstellung bei Ankunft für diesen Raum einen Schritt Richtung „Komfort“ stellen.",
                    ev_txt)
            elif deficit <= 0.2 and ahead >= ARRIVAL_WARM_MIN:
                add(climate, "geofencing", "Sparen", f"{name}: lange vor Ankunft warm",
                    f"Bei Heimkehr im Mittel schon {ahead:.0f} min warm. {lead_txt}. In Tado die "
                    f"Vorheiz-Einstellung einen Schritt Richtung „Eco“ stellen.",
                    {**ev_txt, "vor Ankunft warm Median (min)": round(ahead)})

        away = away_setpoints(series, home, vacation)
        if len(away) * sample_hours(x[0] for x in series) >= MIN_EVENTS and not cold:
            med = statistics.median(away)
            target = eng.cfg.away_temp
            comfort = [s[2] for s in series if s[2] is not None and s[2] > OFF_TEMP + 5 and home.get(s[0])]
            comfort_t = statistics.median(comfort) if comfort else med
            model = eng.rooms.get(climate)
            t_back = None
            if model is not None and guards.check_room(model)["ok"]:
                t_back = model.time_to_reach(target, comfort_t, eng.cfg.max_flow_temp, 5.0)
            # nur empfehlen, wenn der Raum das Wiederaufheizen laut Modell in 2 h schafft
            if med >= target + 0.9 and t_back is not None and t_back <= 2.0:
                add(climate, "abwesenheit", "Sparen", f"{name}: bei Abwesenheit warm gehalten",
                    f"Während niemand zu Hause ist, hält Tado {med:.1f} °C. Abwesenheitstemperatur in Tado "
                    f"auf {target:.0f} °C senken. Laut Modell ist der Raum danach in ca. "
                    f"{t_back * 60:.0f} min wieder auf {comfort_t:.0f} °C.",
                    {"Messpunkte bei Abwesenheit": len(away), "Soll bei Abwesenheit Median (°C)": round(med, 1),
                     "Wiederaufheizen laut Modell (min)": round(t_back * 60)})

    return out


ADVICE_VERSION = 3  # 2 (0.25.4): „unnötig früh warm“ nur noch bei echtem Vorheizen · 3 (0.32): Frühstart aus „aus“


def track(store, advice: list[dict], now_ts: int | None = None) -> list[dict]:
    """Merkt sich, seit wann eine Empfehlung besteht, und was inzwischen behoben ist."""
    now_ts = now_ts or int(time.time())
    state: dict = store.get("advice_state", {})
    if store.get("advice_version", 1) != ADVICE_VERSION:
        # geänderte Regeln: alte Empfehlungen sind zurückgezogen, nicht „behoben“
        state = {}
        store.put("advice_version", ADVICE_VERSION)
    for a in advice:
        a["key"] = f"{a['room']}|{a['kind']}|{a['title']}"
    # Info-Karten (0.35, „misst neu“/„Wirkung“) tragen ihr ``since`` selbst und gelten nie als „behoben“
    current = {a["key"]: a for a in advice if a.get("impact") != "Info"}
    for key, a in current.items():
        a["since"] = state.get(key, {}).get("since", now_ts)
    for a in advice:
        a.setdefault("since", now_ts)
    # Abgleich-Empfehlung eines Raums in der Messpause nach einer Voreinstellungs-Änderung (Info-Karte „misst
    # neu“): nichts wurde gemessen – weder aktuell noch „behoben“, der Eintrag bleibt, wie er ist
    paused = {a["room"] for a in advice if a.get("kind") == "abgleich_misst"}

    def on_hold(key: str) -> bool:
        room, kind = key.split("|")[:2]
        return kind == "abgleich" and room in paused

    resolved = []
    for key, old in state.items():
        if key not in current and not old.get("resolved") and not on_hold(key):
            old["resolved"] = now_ts
        if old.get("resolved") and now_ts - old["resolved"] < 14 * 86400:
            resolved.append({"title": old["title"], "since": old["since"], "resolved": old["resolved"]})
    new_state = {k: {"since": a["since"], "title": a["title"]} for k, a in current.items()}
    for key, old in state.items():
        if key not in current and (on_hold(key) or old.get("resolved") and now_ts - old["resolved"] < 14 * 86400):
            new_state[key] = old
    store.put("advice_state", new_state)
    return resolved
