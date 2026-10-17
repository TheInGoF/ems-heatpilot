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
                           "warm_anyway": not heated,
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
        events.append({"ts": ts, "late_min": late, "early_open": early_open,
                       "reached": warm_at is not None, "warm_ahead_min": 0.0})
    return events


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
    """Hydraulischer Abgleich: nötiger Vorlauf je Raum am kältesten Tag im Vergleich."""
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
        data[climate] = {**stats, "target": target, "flow": model.required_flow(target, design)}
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


def build(eng, now_ts: int | None = None) -> list[dict]:
    """Empfehlungen je Raum, mit Beleg. ``eng``: Engine (store, rooms, found, cfg)."""
    now_ts = now_ts or int(time.time())
    since = now_ts - WINDOW_DAYS * 86400
    rooms, home, vacation, flow = _load(eng.store, since, {r.climate for r in eng.found.rooms},
                                        eng.found.persons)
    arrivals = _arrivals(home, vacation)
    names = {r.climate: r.name for r in eng.found.rooms}
    out: list[dict] = []

    def add(room, kind, impact, title, text, evidence):
        out.append({"room": room, "room_name": names.get(room, room), "kind": kind,
                    "impact": impact, "title": title, "text": text, "evidence": evidence})

    bal = balance(eng, rooms, home)
    design = eng.cfg.design_outdoor_temp
    for climate, d in bal.items():
        name = names.get(climate, climate)
        ev = {"nötiger Vorlauf am kältesten Tag (°C)": round(d["flow"]),
              "Median aller Räume (°C)": round(d["median"]),
              "Ventil im Mittel offen (%)": round(d["mean_open"]),
              "Heizzeit mit Ventil über 90 % (%)": round(100 * d["wide_frac"]),
              "Heizstunden ausgewertet": round(d["hours"])}
        if d["state"] == "under":
            gain = ""
            if d["flow"] >= d["system_flow"] - 0.5 and d["system_flow"] - d["without_worst"] >= 3:
                gain = (f" Er bestimmt den Vorlauf fürs ganze Haus: besser versorgt, reichen am kältesten "
                        f"Tag ca. {d['without_worst']:.0f} °C statt {d['system_flow']:.0f} °C.")
            add(climate, "abgleich", "Abgleich", f"{name}: bekommt zu wenig Heizwasser",
                f"Um {d['target']:.0f} °C bei {design:.0f} °C außen zu halten, braucht der Raum laut Modell "
                f"{d['flow']:.0f} °C Vorlauf – {d['diff']:.0f} K mehr als die anderen Räume (Median "
                f"{d['median']:.0f} °C). Das Ventil steht dabei oft weit offen. Wahrscheinlich ist die "
                f"Voreinstellung (Durchflussbegrenzer am Ventilunterteil) zu weit zu: eine Stufe höher "
                f"stellen. Vorher prüfen, ob der Heizkörper entlüftet ist und nicht zugestellt ist.{gain} "
                f"Danach misst HeatPilot 1–2 Wochen neu.", ev)
        elif d["state"] == "over":
            add(climate, "abgleich", "Abgleich", f"{name}: bekommt mehr Heizwasser als nötig",
                f"Der Raum braucht laut Modell nur {d['flow']:.0f} °C Vorlauf – {-d['diff']:.0f} K weniger als "
                f"die anderen (Median {d['median']:.0f} °C) – und öffnet im Mittel nur {d['mean_open']:.0f} %. "
                f"Wenn er öffnet, zieht er viel Wasser, das unterversorgten Räumen fehlt. Voreinstellung am "
                f"Ventilunterteil eine Stufe zurück (kleinere Zahl). Danach misst HeatPilot 1–2 Wochen neu.", ev)

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
            d = _local(ev["ts"])
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


ADVICE_VERSION = 2  # 2 (0.25.4): „unnötig früh warm“ nur noch bei echtem Vorheizen


def track(store, advice: list[dict], now_ts: int | None = None) -> list[dict]:
    """Merkt sich, seit wann eine Empfehlung besteht, und was inzwischen behoben ist."""
    now_ts = now_ts or int(time.time())
    state: dict = store.get("advice_state", {})
    if store.get("advice_version", 1) != ADVICE_VERSION:
        # geänderte Regeln: alte Empfehlungen sind zurückgezogen, nicht „behoben“
        state = {}
        store.put("advice_version", ADVICE_VERSION)
    current = {f"{a['room']}|{a['kind']}|{a['title']}": a for a in advice}
    for key, a in current.items():
        a["since"] = state.get(key, {}).get("since", now_ts)
        a["key"] = key
    resolved = []
    for key, old in state.items():
        if key not in current and not old.get("resolved"):
            old["resolved"] = now_ts
        if old.get("resolved") and now_ts - old["resolved"] < 14 * 86400:
            resolved.append({"title": old["title"], "since": old["since"], "resolved": old["resolved"]})
    new_state = {k: {"since": a["since"], "title": a["title"]} for k, a in current.items()}
    for key, old in state.items():
        if key not in current and old.get("resolved") and now_ts - old["resolved"] < 14 * 86400:
            new_state[key] = old
    store.put("advice_state", new_state)
    return resolved
