"""„Bereit für die Steuerung?“ (seit 0.36.1): Könnte HeatPilot steuern, und was hindert es?

Reine Auswertung, schreibt nichts. Gilt im Modus Beobachten („wäre bereit“) und bei „HeatPilot steuert“
(„läuft; Einschränkungen: …“). Drei Stufen:

* ``bereit`` – alles da,
* ``eingeschränkt`` – steuern geht, aber ohne Teile der Intelligenz (Raum lernt noch, keine Außentemperatur …),
* ``nicht bereit`` – Schreiben ginge ins Leere (kein Raum macht mit, kein Thermostat beschreibbar,
  wiederholte Schreibfehler).

Prüfungen nach Gewicht: 1. beschreibbar (Zustand in Home Assistant, Betriebsart heizen/auto, Soll
einstellbar), 2. Schreibfehler der Regelschleife, 3. Raummodell plausibel, 4. Soll erreichbar,
5. Anwesenheit, 6. Nachtplan, 7. Außentemperatur, 8. Laufzeit. Räume, die ihr Soll nie erreichen
(kleiner Heizkörper, kalter Flur), sind kein Hindernis: Sie werden akzeptiert und benannt.

Der Abschnitt ``boiler`` sagt, ob EMS-ESP alle Kesselsteuerungen hätte, die eine spätere Kesselsteuerung
bräuchte – nur Anzeige. Er ändert die Stufe der Raumsteuerung nicht: Tado braucht keinen Kessel. Seit 0.36.3
ist die Desinfektion (``wwdisinfecting``) dort nötig, sobald ein Desinfektionstermin eingestellt ist. Der
Heizkreis von EMS-ESP als Raum (``Room.ems``) ist immer „nicht dabei“ – HeatPilot schreibt nicht an den Kessel.
"""

from __future__ import annotations

from datetime import datetime

from . import guards
from .controlloop import MAX_FAILS, preheat_hours, reach_c
from .emsesp import BOILER_CONTROLS
from .ems_lock import LOCK_TEXT

LEVELS = ("bereit", "eingeschränkt", "nicht bereit")
ROOM_STATES = ("bereit", "akzeptiert", "lernt", "nicht erreichbar", "nicht dabei")
UNREACHED_K = 0.5          # erreicht ein Raum sein Soll um mehr als so viel nicht: „akzeptiert“
ACCEPTED_MIN = 2           # so viele „akzeptiert“ mindestens, bevor es (über die Hälfte) eine Einschränkung ist
T_OUT_ASSUMED = 5.0        # ohne Außentemperatur rechnet die Regelschleife damit (controlloop.desired)
PRESENCE_WINDOW_S = 3600   # Anwesenheit „unklar“, wenn in dieser Zeit meist unbekannt
TARGET_TEMPERATURE = 1     # Home Assistant: ClimateEntityFeature.TARGET_TEMPERATURE
# Desinfektion (0.36.3): mit eingestelltem Termin ist diese Kesselsteuerung nötig
DISINFECT_CONTROL = "wwdisinfecting"
DISINFECT_NOTE = "Desinfektion braucht WWK Desinfizieren (schreibbar)"


def _num(value: float) -> str:
    """Zahl für Texte: eine Nachkommastelle höchstens, Komma, echtes Minus."""
    return f"{round(value, 1):g}".replace(".", ",").replace("-", "−")


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} und {names[-1]}"


def _rooms_word(n: int) -> str:
    return f"{n} Raum" if n == 1 else f"{n} Räume"


def write_problem(state: dict | None) -> str | None:
    """Warum ein Thermostat nicht beschreibbar ist – None = beschreibbar.

    Fehlt ``hvac_modes`` oder ``supported_features``, wird durchgelassen (nicht jede Plattform meldet sie).
    """
    if not state:
        return "fehlt in Home Assistant"
    if state.get("state") == "unavailable" or state.get("state") is None:
        return "nicht verfügbar"
    if state.get("state") == "unknown":
        return "Zustand unbekannt"
    attrs = state.get("attributes") or {}
    modes = attrs.get("hvac_modes")
    if isinstance(modes, (list, tuple)):
        plain = {str(m).lower().rsplit(".", 1)[-1] for m in modes}  # auch „HVACMode.HEAT“
        if not plain & {"heat", "auto"}:
            return f"kann nicht heizen (Betriebsarten: {', '.join(sorted(plain)) or 'keine'})"
    features = attrs.get("supported_features")
    if features is not None:
        try:
            if not int(features) & TARGET_TEMPERATURE:
                return "Soll nicht einstellbar"
        except (TypeError, ValueError):
            pass
    return None


def _label(eng, entity_id: str) -> str:
    return ((eng.states.get(entity_id) or {}).get("attributes") or {}).get("friendly_name") or entity_id


def _presence(eng, now_ts: int) -> tuple[bool, list[str]]:
    """(Anwesenheit unklar?, Personen ohne Zustand jetzt). Unklar: in der letzten Stunde meist keine bekannte
    Anwesenheit (``home_count`` leer); ohne Messpunkte zählt der Zustand jetzt."""
    from .engine import PERSON_UNKNOWN  # spät: engine importiert dieses Modul

    unknown = [p for p in eng.found.persons if (eng.states.get(p) or {}).get("state") in PERSON_UNKNOWN]
    rows = eng.store.query("SELECT home_count FROM samples WHERE ts >= ?", (now_ts - PRESENCE_WINDOW_S,))
    if rows:
        unclear = sum(1 for r in rows if r["home_count"] is None) * 2 > len(rows)
    else:
        home = any((eng.states.get(p) or {}).get("state") == "home" for p in eng.found.persons)
        unclear = bool(unknown) and not home
    return unclear, [_label(eng, p) for p in unknown]


def _night_note(eng) -> str:
    """Eine Zeile zu den Nachtzeiten (0.36): gelernt aktiv, nur Vorschlag oder ohne Stromsensoren."""
    s = eng.sleep_summary() or {}
    n = int(s.get("nights") or 0)
    if not s.get("sensors"):
        return "Nachtzeiten nach dem Wochenplan – ohne Leistungssensoren lernt HeatPilot keine Schlafenszeiten"
    if s.get("active"):
        return f"Nachtzeiten aus dem Stromverbrauch gelernt und wirksam ({n} Nächte), sonst gilt der Wochenplan"
    if s.get("auto"):
        return f"Nachtzeiten aus dem Stromverbrauch noch nicht belastbar ({n} Nächte) – es gilt der Wochenplan"
    return (f"Nachtzeiten aus dem Stromverbrauch nur Vorschlag ({n} Nächte) – wirksam mit „Gelernte Zeiten "
            "verwenden“, bis dahin gilt der Wochenplan")


def boiler(eng) -> dict:
    """Kessel über EMS-ESP: Sind alle nötigen Steuerungen da und schreibbar? Nur Anzeige, nichts schreiben.

    ``found``/``writeable`` sind None, solange nicht abgefragt wurde. Mit Desinfektionstermin (0.36.3) ist
    ``wwdisinfecting`` die sechste nötige Steuerung (``note`` sagt, wofür).
    """
    configured = bool(eng.cfg.emsesp_host)
    disinfect = bool(getattr(eng.cfg, "dhw_disinfect_day", ""))
    ts = getattr(eng, "ems_controls_ts", None)
    polled = configured and ts is not None
    got = (getattr(eng, "ems_controls", None) or {}) if polled else {}
    controls, missing = [], []
    for key, (name, required, _) in BOILER_CONTROLS.items():
        by_term = disinfect and key == DISINFECT_CONTROL
        required = required or by_term
        c = got.get(key) or {}
        found = bool(c.get("found")) if polled else None
        writeable = c.get("writeable") if found else (False if polled else None)
        controls.append({"key": key, "name": name, "required": required, "found": found, "writeable": writeable,
                         "value": c.get("value") if found else None, "min": c.get("min") if found else None,
                         "max": c.get("max") if found else None, "uom": c.get("uom") if found else None,
                         **({"note": DISINFECT_NOTE} if by_term else {})})
        if polled and required and writeable is not True:
            why = "nicht gefunden" if not found else ("nicht schreibbar" if writeable is False
                                                      else "schreibbar unbekannt")
            missing.append(f"{name} ({why})")
    n_required = sum(1 for c in controls if c["required"])
    if not configured:
        text = "EMS-ESP nicht eingerichtet – ohne EMS-ESP keine Kesselsteuerung"
    elif not polled:
        text = "noch nicht abgefragt" + ("" if eng.ems else " – EMS-ESP antwortet bisher nicht")
    elif not missing:
        text = (f"Alle {n_required} nötigen Steuerungen vorhanden und schreibbar – eine Kesselsteuerung wäre "
                "möglich (nicht gebaut, HeatPilot schreibt nichts)")
    else:
        text = "Es fehlen: " + ", ".join(missing)
    return {
        "configured": configured,
        "polled": polled,
        "polled_at": datetime.fromtimestamp(ts).astimezone().isoformat() if polled else None,
        "ready": polled and not missing,
        "controls": controls,
        "text": text,
        "disinfect_required": disinfect,
    }


def assess(eng, now_ts: int) -> dict:
    """Bereitschaft der Raumsteuerung – siehe Modulbeschreibung. Reine Funktion der Engine (schreibt nichts)."""
    cfg = eng.cfg
    managing = cfg.is_managing()
    loop = getattr(eng, "control_loop", None)
    fails = {c: int(s.get("fails", 0)) for c, s in (loop.state if loop is not None else {}).items()}
    t_out = eng.outdoor_now()
    t_calc = T_OUT_ASSUMED if t_out is None else t_out
    design = cfg.design_outdoor_temp
    flat_h = preheat_hours(None, None, 0.0, cfg.max_flow_temp, t_calc, cfg.preheat_max_h)  # ohne Modell pauschal

    blockers: list[str] = []
    limits: list[str] = []
    notes: list[str] = []
    short_blocks: list[str] = []
    short_limits: list[str] = []
    rooms: list[dict] = []
    by_state: dict[str, list[str]] = {s: [] for s in ROOM_STATES}
    not_writable: list[tuple[str, str]] = []  # (Raum, Grund) – im Zustand von Home Assistant nicht beschreibbar

    for room in eng.found.rooms:
        entry = {"climate": room.climate, "name": room.name, "managed": bool(room.managed),
                 "comfort": float(room.comfort)}
        if getattr(room, "ems", False):  # Heizkreis von EMS-ESP: nie dabei, auch mit „macht mit“ (0.36.3)
            rooms.append({**entry, "managed": False, "state": "nicht dabei", "text": LOCK_TEXT})
            notes.append(f"{room.name}: {LOCK_TEXT} – macht nicht mit")  # eigener Hinweis, nicht noch einmal unten
            continue
        if not room.managed:
            rooms.append({**entry, "state": "nicht dabei", "text": "macht nicht mit (Einstellungen → Räume)"})
            by_state["nicht dabei"].append(room.name)
            continue
        comfort = float(room.comfort)
        targets = room.write_targets
        # 1. beschreibbar: alle Thermostate, auf die geschrieben würde (Raumregler bzw. alle Ventile der Gruppe –
        #    die Regelschleife schreibt sie nacheinander, ein totes Ventil hält die anderen auf)
        bad = [(e, p) for e, p in ((e, write_problem(eng.states.get(e))) for e in targets) if p]
        bad_text = (bad[0][1] if len(targets) == 1 and bad
                    else "; ".join(f"{_label(eng, e)}: {p}" for e, p in bad))
        n_fail = fails.get(room.climate, 0)
        if bad or n_fail >= MAX_FAILS:
            # 2. Schreibfehler der Regelschleife – Blocker je Raum (wie warnings() im Heizen-Reiter)
            parts = [bad_text] if bad else []
            if n_fail >= MAX_FAILS:
                parts.append(f"{n_fail} Schreibfehler in Folge – Thermostat prüfen")
                blockers.append(f"{room.name}: {n_fail} Schreibfehler in Folge – Thermostat prüfen")
                short_blocks.append(f"{room.name}: {n_fail} Schreibfehler in Folge")
            else:
                not_writable.append((room.name, bad_text))
            rooms.append({**entry, "state": "nicht erreichbar", "text": "; ".join(parts)})
            by_state["nicht erreichbar"].append(room.name)
            continue
        # 3. Raummodell: unplausibel → Vorheizen pauschal statt nach Aufheizzeit
        model = eng.rooms.get(room.climate)
        check = guards.check_room(model) if model is not None else {"ok": False, "reasons": ["noch kein Modell"]}
        if not check["ok"]:
            reason = check["reasons"][0] if check["reasons"] else "Modell unplausibel"
            rooms.append({**entry, "state": "lernt",
                          "text": f"lernt noch ({reason}) – Vorheizen pauschal {_num(flat_h)} h"})
            by_state["lernt"].append(room.name)
            limits.append(f"{room.name} lernt noch ({reason}) – Vorheizen pauschal {_num(flat_h)} h statt nach "
                          "der Aufheizzeit des Raums")
            continue
        # 4. Soll erreichbar? Nein → akzeptiert, kein Hindernis (nichts wird hochgedreht)
        now_c = reach_c(model, comfort, cfg.max_flow_temp, t_calc)
        design_c = reach_c(model, comfort, cfg.max_flow_temp, design)
        at_design = (f", {_num(design_c)} °C bei {_num(design)} °C"
                     if design_c is not None and design_c < comfort - UNREACHED_K else "")
        if now_c is not None and now_c < comfort - UNREACHED_K:
            what = f"höchstens {_num(now_c)} °C bei {_num(t_calc)} °C außen{at_design}"
            rooms.append({**entry, "state": "akzeptiert",
                          "text": f"erreicht sein Soll nicht ({what}) – wird akzeptiert, kein Hindernis"})
            by_state["akzeptiert"].append(room.name)
            notes.append(f"{room.name} erreicht sein Soll nicht ({what}) – wird akzeptiert, kein Hindernis")
            continue
        text = "beschreibbar, Modell belastbar"
        if at_design:
            text += f" · bei {_num(design)} °C außen höchstens {_num(design_c)} °C"
        rooms.append({**entry, "state": "bereit", "text": text})
        by_state["bereit"].append(room.name)

    managed = sum(1 for r in rooms if r["managed"])
    unreachable = len(by_state["nicht erreichbar"])
    writable = managed - unreachable
    models_ok = len(by_state["bereit"]) + len(by_state["akzeptiert"])

    if managed == 0:
        blockers.insert(0, "Kein Raum macht mit – in den Einstellungen → Räume ankreuzen")
        short_blocks.insert(0, "kein Raum macht mit")
    if not_writable:
        detail = ", ".join(f"{n} ({t})" for n, t in not_writable)
        if writable == 0:  # wiegt schwerer als Schreibfehler einzelner Räume: zuerst
            blockers.insert(0, f"Kein Raum mit „macht mit“ ist beschreibbar: {detail} – Thermostate in Home "
                               "Assistant prüfen")
            short_blocks.insert(0, "kein Thermostat beschreibbar")
        else:
            limits.insert(0, f"Nicht beschreibbar: {detail} – diese Räume folgen nicht")
            short_limits.append(f"{_names([n for n, _ in not_writable])} nicht beschreibbar")
    learning = by_state["lernt"]
    if len(learning) == 1:
        short_limits.append(f"{learning[0]} lernt noch (Vorheizen nach Pauschale)")
    elif learning:
        short_limits.append(f"Vorheizen für {len(learning)} Räume noch nach Pauschale")
    accepted = by_state["akzeptiert"]
    # erst ab zwei Räumen und gemessen an allen beschreibbaren (auch denen, die noch lernen): ein einzelner
    # Raum ohne erreichbares Soll ist nie eine Einschränkung
    if len(accepted) >= ACCEPTED_MIN and len(accepted) * 2 > writable:
        limits.append(f"Mehr als die Hälfte der Räume kommt nicht auf Soll ({_names(accepted)}) – "
                      "Vorlauf/Abgleich prüfen (Empfehlungen)")
        short_limits.append("mehr als die Hälfte der Räume kommt nicht auf Soll")

    # 5. Anwesenheit
    if not eng.found.persons:
        notes.append("Ohne Personen gibt es keine Abwesenheits-Absenkung (Einstellungen → Personen)")
    else:
        unclear, unknown = _presence(eng, now_ts)
        if unclear:
            who = f"Tracker ohne Zustand: {', '.join(unknown)}" if unknown else "Tracker ohne Zustand"
            limits.append(f"Anwesenheit unklar ({who}) – im Zweifel bleibt es bei Komfort, keine "
                          "Abwesenheits-Absenkung")
            short_limits.append("Anwesenheit unklar")
    # 6. Nachtplan
    notes.append(_night_note(eng))
    # 7. Außentemperatur
    if t_out is None:
        limits.append(f"Keine Außentemperatur – die Vorheizrechnung nimmt {_num(T_OUT_ASSUMED)} °C an")
        short_limits.append("keine Außentemperatur")
    # 8. Laufzeit
    hours = int(eng.house.rls.updates)
    if hours < guards.LIMITS["house_min_updates"]:
        notes.append(f"Modelle sind jung ({hours} h gelernt)")
    if managed and by_state["nicht dabei"]:
        notes.append(f"Machen nicht mit: {_names(by_state['nicht dabei'])}")
    if managing and loop is not None and loop.paused(now_ts):
        notes.append(f"Pausiert bis {datetime.fromtimestamp(loop.pause_until).astimezone():%H:%M} – "
                     "solange schreibt HeatPilot nichts")

    level = "nicht bereit" if blockers else ("eingeschränkt" if limits else "bereit")
    if blockers:
        headline = f"Nicht bereit – {short_blocks[0] if short_blocks else blockers[0]}."
    elif limits:
        lead = "Läuft – eingeschränkt: " if managing else "Eingeschränkt bereit – "
        headline = lead + "; ".join(short_limits[:3] or [limits[0]]) + "."
    else:
        headline = (f"{'Läuft' if managing else 'Bereit'} – {_rooms_word(writable)} schreibbar, "
                    f"{models_ok} mit belastbarem Modell.")
    if accepted and not blockers:
        headline += (f" {accepted[0]} erreicht sein Soll nicht und wird akzeptiert." if len(accepted) == 1 else
                     f" {_names(accepted)} erreichen ihr Soll nicht und werden akzeptiert.")
    return {
        "level": level,
        "headline": headline,
        "managing": managing,
        "blockers": blockers,
        "limits": limits,
        "notes": notes,
        "rooms": rooms,
        "counts": {"managed": managed, "writable": writable, "models_ok": models_ok, "unreachable": unreachable,
                   "accepted": len(accepted), "learning": len(learning)},
        "boiler": boiler(eng),
    }
