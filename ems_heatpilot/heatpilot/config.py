"""Einstellungen: Vorgaben ← Weboberfläche (``/data/settings.json``).

Eingerichtet wird ausschließlich über die HeatPilot-Seite (Reiter Einstellungen);
das Add-on hat keine YAML-Optionen. Eine ``options.json`` älterer Versionen wird
noch gelesen, damit dort gesetzte Werte bei einem Update nicht verloren gehen.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("HEATPILOT_DATA", "/data"))
SETTINGS_NAME = "settings.json"
# Sicherungskopie im Add-on-Konfigurationsordner (/addon_configs/<slug>/, per Samba erreichbar)
BACKUP_DIR = Path(os.environ.get("HEATPILOT_BACKUP_DIR", "/config"))
BACKUP_NAME = "heatpilot-einstellungen.json"

_ENTITY = re.compile(r"^[a-z_]+\.[A-Za-z0-9_]+$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")

# Steuerung (seit 0.34): „beobachten“ = lernen und empfehlen, Tado nur auf Knopfdruck;
# „heatpilot“ = HeatPilot schreibt die Raum-Solltemperaturen der Räume mit „macht mit“ selbst
CONTROL_MODES = ("beobachten", "heatpilot")
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
# Nachtabsenkung nach Wochenplan: Beginn am Abend des Tages, Ende am Morgen desselben Tages
DEFAULT_NIGHT_PLAN = [
    {"start": "21:00", "end": "04:30"},  # Mo
    {"start": "21:00", "end": "04:30"},  # Di
    {"start": "21:00", "end": "04:30"},  # Mi
    {"start": "21:00", "end": "04:30"},  # Do
    {"start": "22:00", "end": "04:30"},  # Fr
    {"start": "22:00", "end": "06:30"},  # Sa
    {"start": "21:00", "end": "06:30"},  # So
]
COMFORT_MIN, COMFORT_MAX, COMFORT_DEFAULT = 5.0, 25.0, 21.0
# Nachtplan (0.36): gelernter Beginn darf nach Mitternacht liegen, höchstens bis hierher (sonst eingestellter Wert)
LATE_START_MAX = "03:00"
NOON = time(12, 0)
# Herkunft einer Nachtzeit im wirksamen Wochenplan (0.36)
SOURCE_SET, SOURCE_POWER = "eingestellt", "gelernt (Strom)"
# Hydraulik (0.35): Voreinstellung am Ventilunterteil – höchstens so viele Stufen
PRESET_MAX = 12
# Warmwasser-Desinfektion (0.36.3): Wochentag des Termins, leer = kein Termin (Standard)
DISINFECT_DAYS = ("", *WEEKDAYS)

# Feld -> (Typ, min, max). Typen: int, float, str, bool, choice, entity, entities, time, strings, rooms,
# overrides, nightplan
FIELDS: dict[str, tuple] = {
    "control_mode": ("choice", CONTROL_MODES),
    "sample_interval_s": ("int", 60, 900),
    "backfill_days": ("int", 0, 365),
    "statistics_backfill_days": ("int", 0, 1095),
    "outdoor_sensors": ("entities",),
    "rooms": ("rooms",),
    "persons": ("entities",),
    "gas_sensor": ("entity",),
    "demand_sensor": ("entity",),
    "weather_entity": ("entity",),
    "emsesp_host": ("str",),
    "gas_calorific_value": ("float", 8, 13),
    "gas_z_number": ("float", 0.8, 1.1),
    "gas_price_eur_per_kwh": ("float", 0, 1),
    "gas_base_fee_eur_per_year": ("float", 0, 2000),
    "dhw_min_temp": ("float", 30, 60),
    "dhw_comfort_temp": ("float", 35, 65),
    "dhw_max_temp": ("float", 40, 70),
    # Desinfektion (0.36.3): Termin je Woche (Tag leer = keiner), Beginn, Temperatur als Rückfall ohne EMS-ESP
    "dhw_disinfect_day": ("choice", DISINFECT_DAYS),
    "dhw_disinfect_time": ("time",),
    "dhw_disinfect_temp": ("float", 60, 80),
    "design_outdoor_temp": ("float", -25, 0),
    "living_area_m2": ("float", 0, 2000),
    "boiler_nominal_kw": ("float", 0, 100),
    "boiler_min_power_pct": ("float", 0, 100),
    "max_flow_temp": ("int", 30, 90),
    "away_temp": ("float", 10, 24),
    "night_temp": ("float", 10, 24),
    "night_start": ("time",),
    "night_end": ("time",),
    "night_plan": ("nightplan",),
    # Schlafenszeit aus dem Hausstromverbrauch (0.36)
    "power_sensors": ("entities",),
    "power_exclude": ("entities",),
    "night_plan_auto": ("bool",),
    "preheat_max_h": ("float", 0.5, 6),
    "learn_schedule": ("bool",),
    "floor_heating_rooms": ("entities",),
    "vacations": ("strings",),
    "vacation_entity": ("entity",),
    "vacation_arrival_time": ("time",),
    "overrides": ("overrides",),
    "debug_logging": ("bool",),
}


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _at(day: datetime, t: time, like: datetime) -> datetime:
    """Wanduhrzeit ``t`` am Tag von ``day`` – mit der Zeitzone von ``like`` (Ortszeit auch über die Umstellung)."""
    naive = datetime.combine(day.date(), t)
    return naive.astimezone() if like.tzinfo is not None else naive


def _wall(dt: datetime) -> datetime:
    """Ortszeit dieses Zeitpunkts (Wochentag und Uhrzeit nach Wanduhr); ohne Zeitzone unverändert."""
    return dt.astimezone() if dt.tzinfo is not None else dt


def _clean_time(value) -> str:
    m = _TIME.match(str(value).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("Uhrzeit als HH:MM")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _clean_room(r: dict) -> dict:
    """Ein Raum-Eintrag aus der Oberfläche; neue Felder (0.34) nur, wenn sie gesetzt sind."""
    climate = str(r.get("climate", "")).strip()
    if not _ENTITY.match(climate):
        raise ValueError(f"Raum ohne gültige climate-Entität: {climate or '(leer)'}")
    room = {"name": str(r.get("name") or "").strip(), "climate": climate}
    for opt in ("temperature", "heating_power", "humidity"):
        v = str(r.get(opt) or "").strip()
        if v:
            if not _ENTITY.match(v):
                raise ValueError(f"{climate}: {opt} ungültig")
            room[opt] = v
    group = str(r.get("group") or "").strip()
    if group:
        room["group"] = group
    for key in ("controller", "managed"):
        if key in r:
            room[key] = r[key] is True or str(r[key]).lower() in ("true", "1", "on", "ja")
    if r.get("comfort") not in (None, ""):
        try:
            comfort = round(float(str(r["comfort"]).replace(",", ".")) * 2) / 2
        except ValueError:
            raise ValueError(f"{climate}: Komforttemperatur keine Zahl") from None
        if not COMFORT_MIN <= comfort <= COMFORT_MAX:
            raise ValueError(f"{climate}: Komfort muss zwischen {COMFORT_MIN:g} und {COMFORT_MAX:g} liegen")
        room["comfort"] = comfort
    # Hydraulik (0.35): preset = aktuelle Voreinstellung, preset_max = Zahl der Stufen.
    # Leer = unbekannt (Schlüssel fehlt); preset_max 0 = kein Voreinstellventil (Rücklaufverschraubung)
    steps: dict[str, int] = {}
    for key, label in (("preset_max", "Stufen"), ("preset", "Voreinstellung")):
        if r.get(key) in (None, ""):
            continue
        try:
            v = int(str(r[key]).strip())
        except ValueError:
            raise ValueError(f"{climate}: {label} keine ganze Zahl") from None
        if v < 0 or v > PRESET_MAX:
            raise ValueError(f"{climate}: {label} muss zwischen 0 und {PRESET_MAX} liegen")
        steps[key] = v
    if steps.get("preset"):
        if "preset_max" not in steps:
            raise ValueError(f"{climate}: Voreinstellung {steps['preset']} braucht die Zahl der Stufen")
        if steps["preset"] > steps["preset_max"]:
            raise ValueError(f"{climate}: Voreinstellung {steps['preset']} liegt über den {steps['preset_max']} Stufen")
    if "preset_max" in steps:
        room["preset_max"] = steps["preset_max"]
    if steps.get("preset"):
        room["preset"] = steps["preset"]
    return room


def _clean_night_plan(value) -> list[dict]:
    """7 Einträge Mo..So {start, end}; Beginn abends (ab 12:00), Ende morgens (vor 12:00)."""
    if not isinstance(value, (list, tuple)) or len(value) != 7:
        raise ValueError("7 Einträge (Mo bis So) mit Beginn und Ende")
    plan = []
    for i, item in enumerate(value):
        start, end = _clean_time((item or {}).get("start", "")), _clean_time((item or {}).get("end", ""))
        if start < "12:00":
            raise ValueError(f"{WEEKDAYS[i]}: Beginn der Nacht liegt am Abend (ab 12:00)")
        if end >= "12:00":
            raise ValueError(f"{WEEKDAYS[i]}: Ende der Nacht liegt am Morgen (vor 12:00)")
        plan.append({"start": start, "end": end})
    return plan


def validate(raw: dict) -> tuple[dict, list[str]]:
    """Eingaben aus der Oberfläche prüfen und bereinigen. Gibt (sauber, Fehler) zurück."""
    from .vacation import _RANGE  # spätes Import, vermeidet Zyklus

    clean: dict = {}
    errors: list[str] = []
    for key, value in raw.items():
        if key not in FIELDS:
            continue
        kind = FIELDS[key][0]
        try:
            if kind in ("int", "float"):
                lo, hi = FIELDS[key][1:]
                v = int(value) if kind == "int" else float(str(value).replace(",", "."))
                if not lo <= v <= hi:
                    raise ValueError(f"muss zwischen {lo} und {hi} liegen")
                clean[key] = v
            elif kind == "str":
                clean[key] = str(value or "").strip()
            elif kind == "bool":
                clean[key] = value is True or str(value).lower() in ("true", "1", "on", "ja")
            elif kind == "choice":
                v = str(value or "").strip()
                if v not in FIELDS[key][1]:
                    raise ValueError(f"erlaubt: {', '.join(c or '(leer)' for c in FIELDS[key][1])}")
                clean[key] = v
            elif kind == "entity":
                v = str(value or "").strip()
                if v and not _ENTITY.match(v):
                    raise ValueError("keine gültige Entität (z. B. sensor.name)")
                clean[key] = v
            elif kind == "entities":
                items = [str(v).strip() for v in (value or []) if str(v).strip()]
                bad = [v for v in items if not _ENTITY.match(v)]
                if bad:
                    raise ValueError(f"ungültig: {', '.join(bad)}")
                clean[key] = list(dict.fromkeys(items))
            elif kind == "time":
                clean[key] = _clean_time(value)
            elif kind == "nightplan":
                clean[key] = _clean_night_plan(value)
            elif kind == "strings":
                items = [str(v).strip() for v in (value or []) if str(v).strip()]
                if key == "vacations":
                    bad = [v for v in items if not _RANGE.match(v)]
                    if bad:
                        raise ValueError(f"Format JJJJ-MM-TT [HH:MM] - JJJJ-MM-TT [HH:MM]: {', '.join(bad)}")
                clean[key] = items
            elif kind == "rooms":
                clean[key] = [_clean_room(r) for r in value or []]
            elif kind == "overrides":
                items = []
                for item in value or []:
                    item = str(item).strip()
                    if not item:
                        continue
                    role, _, ent = item.partition("=")
                    if not role.strip() or not _ENTITY.match(ent.strip()):
                        raise ValueError(f"Format rolle=entity_id: {item}")
                    items.append(f"{role.strip()}={ent.strip()}")
                clean[key] = items
        except (TypeError, ValueError) as err:
            errors.append(f"{key}: {err}")
    if not errors:
        lo = clean.get("dhw_min_temp", raw.get("dhw_min_temp"))
        mid = clean.get("dhw_comfort_temp", raw.get("dhw_comfort_temp"))
        hi = clean.get("dhw_max_temp", raw.get("dhw_max_temp"))
        if None not in (lo, mid, hi) and not (float(lo) <= float(mid) <= float(hi)):
            errors.append("Warmwasser: Minimum ≤ Komfort ≤ Maximum")
    return clean, errors


@dataclass
class Config:
    mode: str = "observe"
    # Steuerung (0.34): beobachten (Standard) oder heatpilot – siehe CONTROL_MODES
    control_mode: str = "beobachten"
    sample_interval_s: int = 300
    backfill_days: int = 30
    statistics_backfill_days: int = 365
    outdoor_sensors: list[str] = field(default_factory=list)
    # Räume: [{name, climate, temperature?, heating_power?, humidity?,
    #          group?, controller?, managed?, comfort?, preset?, preset_max?}], leer = Tado automatisch.
    # group: gleicher Name = derselbe Raum (Mitläufer); controller: Wandthermostat (Raumregler);
    # managed: „macht mit“ (HeatPilot schreibt); comfort: Komforttemperatur des Raums (°C);
    # preset/preset_max (0.35): Voreinstellung am Ventilunterteil und Zahl der Stufen (fehlt = unbekannt,
    # preset_max 0 = kein Voreinstellventil)
    rooms: list[dict] = field(default_factory=list)
    # Personen für die Anwesenheit; leer = automatisch (alle person.* mit Gerät/GPS)
    persons: list[str] = field(default_factory=list)
    gas_sensor: str | None = None
    # Heizungsanforderung der bestehenden Automation (nur aufzeichnen)
    demand_sensor: str | None = None
    weather_entity: str | None = None
    emsesp_host: str | None = None
    # Gasabrechnung: kWh = m³ × Brennwert × Zustandszahl (Werte von der Gasrechnung)
    gas_calorific_value: float = 11.2
    gas_z_number: float = 0.95
    gas_price_eur_per_kwh: float = 0.12
    gas_base_fee_eur_per_year: float = 150.0
    # Warmwasser-Strategie (nur Empfehlung): nie unter min, für Nutzung comfort
    dhw_min_temp: float = 40.0
    dhw_comfort_temp: float = 50.0
    dhw_max_temp: float = 60.0
    # Desinfektion (0.36.3): Termin je Woche – Tag "Mo"…"So", leer = kein Termin (Standard); Beginn (nur mit Tag
    # wirksam) und Temperatur, falls EMS-ESP sie nicht meldet (wwdisinfectiontemp). Nur eingeplant und angezeigt –
    # HeatPilot löst die Desinfektion noch nicht aus (EMS-ESP bleibt nur lesend)
    dhw_disinfect_day: str = ""
    dhw_disinfect_time: time = time(13, 0)
    dhw_disinfect_temp: float = 70.0
    design_outdoor_temp: float = -12.0
    # fürs Zertifikat; 0 = unbekannt bzw. aus EMS-ESP (nompower, burnminpower)
    living_area_m2: float = 0.0
    boiler_nominal_kw: float = 0.0
    boiler_min_power_pct: float = 0.0
    max_flow_temp: int = 75
    away_temp: float = 19.0
    night_temp: float = 18.0
    night_start: time = time(22, 30)
    night_end: time = time(6, 0)
    # Nachtabsenkung nach Wochenplan (Steuerung heatpilot): Mo..So {start, end} als "HH:MM"
    night_plan: list[dict] = field(default_factory=lambda: [dict(d) for d in DEFAULT_NIGHT_PLAN])
    # Schlafenszeit aus dem Hausstromverbrauch (0.36): Leistungssensoren (W oder kW; Summe = Hausleistung,
    # bei drei Phasen alle drei), Verbraucher, die nicht zählen (Waschmaschine, Spülmaschine, Wallbox –
    # werden abgezogen), und ob gelernte Zeiten den Wochenplan ersetzen (sonst nur Vorschlag)
    power_sensors: list[str] = field(default_factory=list)
    power_exclude: list[str] = field(default_factory=list)
    night_plan_auto: bool = False
    # Laufzeit, nicht gespeichert: gelernte Nachtzeiten Mo..So [{start, end}] mit None, wo nichts
    # Belastbares vorliegt – setzt die Engine (update_sleep), wirkt nur mit night_plan_auto
    night_plan_learned: list[dict | None] | None = field(default=None, repr=False, compare=False)
    # so früh höchstens vorheizen (h) – Räume, die es nicht schaffen, werden akzeptiert
    preheat_max_h: float = 3.0
    # Nachtzeiten/-temperatur und Abwesenheit aus Tado weiterlernen (Einstellungen = Startwerte)
    learn_schedule: bool = True
    # Räume mit Fußbodenheizung ohne Stellventil (z. B. festes RTL), climate-Entitäten
    floor_heating_rooms: list[str] = field(default_factory=list)
    # Urlaub: ["2026-12-20 - 2027-01-03", "2027-02-01 08:00 - 2027-02-05 17:30"]
    vacations: list[str] = field(default_factory=list)
    vacation_entity: str | None = None  # z. B. input_boolean.urlaub
    vacation_arrival_time: time = time(18, 0)  # Rückkehr, wenn nur ein Datum angegeben ist
    # "rolle=entity_id", z. B. "flow_temp=sensor.boiler_curflowtemp"
    overrides: dict[str, str] = field(default_factory=dict)
    debug_logging: bool = False  # ausführliches Log (jeder Takt) für die Fehlersuche

    @classmethod
    def load(cls, path: Path | None = None, settings_path: Path | None = None) -> "Config":
        path = path or DATA_DIR / "options.json"
        settings_path = settings_path or path.parent / SETTINGS_NAME
        raw: dict = {}
        if path.exists():
            raw.update(json.loads(path.read_text()))
        if settings_path.exists():  # Weboberfläche hat Vorrang
            raw.update(json.loads(settings_path.read_text()))
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: dict) -> "Config":
        cfg = cls()
        for key, value in raw.items():
            if key == "night_plan_learned":
                continue  # Laufzeitfeld, kommt nie aus einer Datei
            if key in ("night_start", "night_end", "vacation_arrival_time"):
                value = _parse_time(value)
            elif key == "dhw_disinfect_time":
                try:
                    value = _parse_time(_clean_time(value))
                except (TypeError, ValueError, AttributeError):
                    continue  # unlesbar: Standard 13:00
            elif key == "overrides":
                value = dict(item.split("=", 1) for item in value if "=" in item)
                value = {k.strip(): v.strip() for k, v in value.items()}
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        for key in ("gas_sensor", "demand_sensor", "weather_entity", "emsesp_host", "vacation_entity"):
            if getattr(cfg, key) == "":
                setattr(cfg, key, None)
        cfg.rooms = [r for r in cfg.rooms if r.get("climate")]
        if cfg.control_mode not in CONTROL_MODES:
            cfg.control_mode = "beobachten"
        if cfg.dhw_disinfect_day not in DISINFECT_DAYS:
            cfg.dhw_disinfect_day = ""  # unbekannter Tag: kein Termin
        try:
            cfg.night_plan = _clean_night_plan(cfg.night_plan)
        except (TypeError, ValueError, AttributeError):
            cfg.night_plan = [dict(d) for d in DEFAULT_NIGHT_PLAN]
        return cfg

    def to_raw(self) -> dict:
        """Für die Oberfläche: JSON-fähig, im selben Format wie gespeichert."""
        out = {}
        for key in FIELDS:
            v = getattr(self, key)
            if isinstance(v, time):
                v = v.strftime("%H:%M")
            elif key == "overrides":
                v = [f"{k}={e}" for k, e in v.items()]
            elif v is None:
                v = ""
            out[key] = v
        return out

    @property
    def gas_kwh_per_m3(self) -> float:
        return self.gas_calorific_value * self.gas_z_number

    def is_night(self, t: time) -> bool:
        """Nacht nach den festen Zeiten night_start/night_end (ältere Aufrufer, Ersparnis-Schätzung)."""
        if self.night_start <= self.night_end:
            return self.night_start <= t < self.night_end
        return t >= self.night_start or t < self.night_end

    # --- Wochenplan der Nachtabsenkung (0.34) ---
    def night_entry(self, weekday: int) -> dict:
        """Wirksamer Eintrag des Wochentags 0 = Mo … 6 = So: {start, end, source_start, source_end}.

        Seit 0.36 ersetzt mit ``night_plan_auto`` je Hälfte der aus dem Stromverbrauch gelernte Wert
        (``night_plan_learned``) den eingestellten, wo es einen belastbaren gibt; sonst gilt der
        eingestellte (Rückfall). Die Herkunft steht je Hälfte dabei.
        """
        d = self.night_plan[weekday % 7]
        out = {"start": d["start"], "end": d["end"], "source_start": SOURCE_SET, "source_end": SOURCE_SET}
        learned = self.night_plan_learned if self.night_plan_auto else None
        entry = learned[weekday % 7] if learned and len(learned) == 7 else None
        for key in ("start", "end"):
            value = entry.get(key) if entry else None
            if not value:
                continue
            try:
                hhmm = _clean_time(value)
            except ValueError:
                continue
            if key == "start" and LATE_START_MAX < hhmm < "12:00":
                continue  # Beginn mitten in der Nacht/morgens: unplausibel, eingestellter Wert gilt
            if key == "end" and hhmm >= "12:00":
                continue
            out[key], out["source_" + key] = hhmm, SOURCE_POWER
        return out

    def night_times(self, weekday: int) -> tuple[time, time]:
        """(Beginn am Abend, Ende am Morgen) des Wochentags 0 = Mo … 6 = So – wirksam, siehe night_entry."""
        e = self.night_entry(weekday)
        return _parse_time(e["start"]), _parse_time(e["end"])

    def night_span(self, day: datetime) -> tuple[datetime, datetime]:
        """(Beginn, Ende) der Nacht, die zum Abend von ``day`` gehört (Nacht Fr → Sa: ``day`` = Fr).

        Eingestellte Beginne liegen am Abend (ab 12:00). Seit 0.36 kann ein aus dem Stromverbrauch
        gelernter Beginn nach Mitternacht liegen (z. B. 00:30) – er zählt dann zum Folgetag. Läge er nach
        dem Ende, gilt der eingestellte Beginn (sonst wäre die Nacht leer).
        """
        start, _ = self.night_times(day.weekday())
        nxt = day + timedelta(days=1)
        _, end = self.night_times(nxt.weekday())
        s = _at(nxt if start < NOON else day, start, day)
        e = _at(nxt, end, day)
        if s >= e:  # gelernter Beginn nach dem Ende (sehr frühes Ende eingestellt): eingestellter Beginn gilt
            s = _at(day, _parse_time(self.night_plan[day.weekday() % 7]["start"]), day)
        return s, e

    def _spans_around(self, dt: datetime) -> list[tuple[datetime, datetime]]:
        return [self.night_span(dt + timedelta(days=k)) for k in (-1, 0, 1)]

    def is_night_at(self, dt: datetime) -> bool:
        """Nacht laut Wochenplan: zwischen Beginn (abends, gelernt auch nach Mitternacht) und Ende (morgens)."""
        return any(s <= dt < e for s, e in self._spans_around(dt))

    def night_end_at(self, dt: datetime) -> datetime:
        """Ende der Nacht, in der ``dt`` liegt (sonst das nächste Nachtende)."""
        spans = self._spans_around(dt)
        hit = next((e for s, e in spans if s <= dt < e), None)
        return hit or min(e for _, e in spans if e > dt)

    def night_start_after(self, dt: datetime) -> datetime:
        """Nächster Nachtbeginn ab ``dt`` (nur sinnvoll, wenn nicht is_night_at(dt))."""
        spans = self._spans_around(dt) + [self.night_span(dt + timedelta(days=2))]
        return min(s for s, _ in spans if s >= dt)

    def is_managing(self) -> bool:
        return self.control_mode == "heatpilot"

    # --- Desinfektion (0.36.3) ---
    def disinfect_weekday(self) -> int | None:
        """Wochentag des Desinfektionstermins (0 = Mo … 6 = So), None = kein Termin."""
        return WEEKDAYS.index(self.dhw_disinfect_day) if self.dhw_disinfect_day in WEEKDAYS else None

    def disinfections(self, start: datetime, end: datetime) -> list[datetime]:
        """Geplante Beginne der Desinfektion von ``start`` bis ``end`` (beide eingeschlossen), zeitlich sortiert.

        Wanduhrzeit am Termintag in Ortszeit – auch über die Zeitumstellung (wie ``night_span``): „So 13:00“ ist
        nach der Umstellung 13:00 Winterzeit, nicht 12:00. Ohne Termin leer. Ohne Zeitzone (Tests) naiv.
        """
        weekday = self.disinfect_weekday()
        if weekday is None or end < start:
            return []
        first = _wall(start)
        day = first - timedelta(days=(first.weekday() - weekday) % 7)  # letzter Termintag bis ``start``
        out = []
        while (at := _at(day, self.dhw_disinfect_time, start)) <= end:
            if at >= start:
                out.append(at)
            day += timedelta(days=7)
        return out

    def next_disinfection(self, now: datetime) -> datetime | None:
        """Nächster geplanter Beginn ab ``now`` (genau jetzt zählt mit), None ohne Termin."""
        hits = self.disinfections(now, now + timedelta(days=8))
        return hits[0] if hits else None


def save_settings(clean: dict, data_dir: Path | None = None) -> Path:
    """Geprüfte Einstellungen speichern (atomar)."""
    path = (data_dir or DATA_DIR) / SETTINGS_NAME
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update(clean)
    text = json.dumps(current, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    backup(text)
    return path


def backup(text: str, backup_dir: Path | None = None) -> None:
    """Kopie der Einstellungen außerhalb von /data – falls /data einmal verloren geht."""
    folder = backup_dir or BACKUP_DIR
    try:
        if folder.exists() and os.access(folder, os.W_OK):
            tmp = folder / (BACKUP_NAME + ".tmp")
            tmp.write_text(text)
            tmp.replace(folder / BACKUP_NAME)
    except OSError:
        pass


def restore_settings(data_dir: Path | None = None, backup_dir: Path | None = None) -> bool:
    """Fehlt /data/settings.json, aber es gibt eine Sicherung: zurückholen. Gibt True bei Erfolg."""
    path = (data_dir or DATA_DIR) / SETTINGS_NAME
    src = (backup_dir or BACKUP_DIR) / BACKUP_NAME
    if path.exists() or not src.exists():
        return False
    try:
        json.loads(src.read_text())
        path.write_text(src.read_text())
        return True
    except (OSError, ValueError):
        return False
