"""Zertifikat über Haus und Heizung – aus Messdaten, als PDF zum Weitergeben.

Rein sachlich: zeigt schwarz auf weiß, was das Haus braucht und was der Kessel
liefert – bezogen auf die letzten 12 Monate Messung. Enthält keine
Namen, Adressen oder Standortdaten. Kein Energieausweis nach GEG.

Seit 0.42.1 sagt es, über welchen Zeitraum es rechnet, woraus das Hausmodell gelernt hat und welche Stunden fehlen
(mit Grund, ``house_data``); fehlt vielen Stunden die Innentemperatur, ist es vorläufig.
"""

from __future__ import annotations

import math
import time
from datetime import datetime

from . import __version__, guards, house_data
from .analysis import heat_runs
from .engine import local, num
from .pdf import Page, document, text_width

HEATING_MAX_OUT = 15.0       # darüber zählt eine Stunde nicht als Heizstunde
MIN_HEATING_HOURS = 500      # darunter ist das Zertifikat vorläufig
MIN_COLD_HOURS = 24          # Stunden unter 0 °C – sonst fehlt der Winter
FULL_YEAR_DAYS = 360

# Energieeffizienzklassen wie im Energieausweis (Endenergie kWh/(m²·a), GEG Anlage 10)
ENERGY_CLASSES = [("A+", 30), ("A", 50), ("B", 75), ("C", 100), ("D", 130),
                  ("E", 160), ("F", 200), ("G", 250), ("H", None)]
CLASS_COLORS = ["#1a9641", "#4dac26", "#7fbc41", "#b8e186", "#ffffbf",
                "#fee08b", "#fdae61", "#f46d43", "#d73027"]

GRADE_COLORS = {"good": "#2e7d32", "mid": "#b26a00", "bad": "#c62828", "none": "#757575"}
NAVY, NAVY_DARK, ORANGE, GREY, LIGHT = "#1f3a5f", "#0f1f33", "#eb6834", "#5f6b7a", "#fff4ea"


def fmt(v: float | None, digits: int = 0) -> str:
    """Deutsche Schreibweise: Komma, Tausenderpunkt ab 10.000, echtes Minus."""
    if v is None:
        return "–"
    s = f"{abs(v):,.{digits}f}".replace(",", "_").replace(".", ",")
    s = s.replace("_", ".") if abs(v) >= 10000 else s.replace("_", "")
    return "−" + s if v < 0 and s.strip("0,") else s


def energy_class(kwh_m2: float | None) -> int | None:
    if kwh_m2 is None:
        return None
    for i, (_, upper) in enumerate(ENERGY_CLASSES):
        if upper is None or kwh_m2 < upper:
            return i
    return len(ENERGY_CLASSES) - 1


# Gesamtbewertung: je Bereich 0–100 Punkte (linear zwischen „sehr gut“ und „schlecht“), gewichtet
OVERALL_WEIGHTS = {"Gebäude": 0.40, "Kesselgröße": 0.25, "Taktverhalten": 0.20, "Warmwasser": 0.15}
OVERALL_CLASSES = [("A+", 90), ("A", 80), ("B", 70), ("C", 60), ("D", 50), ("E", 40), ("F", 30), ("G", 20), ("H", 0)]


def _points(value: float | None, best: float, worst: float) -> float | None:
    if value is None:
        return None
    t = (value - worst) / (best - worst)
    return round(100 * min(max(t, 0.0), 1.0))


def overall(parts: dict[str, float | None]) -> dict | None:
    """Ein Nenner für alles: gewichtetes Mittel der vorhandenen Bereiche.

    Nur mit Gebäude (größtes Gewicht) und mindestens zwei weiteren Bereichen – sonst wäre
    die Note eher Zufall als Bewertung.
    """
    have = {k: v for k, v in parts.items() if v is not None}
    if "Gebäude" not in have or len(have) < 3:
        return None
    w = sum(OVERALL_WEIGHTS[k] for k in have)
    score = round(sum(OVERALL_WEIGHTS[k] * v for k, v in have.items()) / w)
    cls = next(i for i, (_, lo) in enumerate(OVERALL_CLASSES) if score >= lo)
    return {"score": score, "class": OVERALL_CLASSES[cls][0], "class_index": cls,
            "parts": [{"area": k, "points": parts[k], "weight": OVERALL_WEIGHTS[k]} for k in OVERALL_WEIGHTS]}


# Mehrverbrauch durch häufiges Takten: Erfahrungswerte aus Fachliteratur und Herstellerangaben
# (Vor-/Nachspülen kühlt den Wärmetauscher, Startphase verbrennt schlechter) – Schätzung, nicht gemessen
CYCLING_LOSS = (0.05, 0.15)
CYCLING_RATIO = 2.5         # ab dieser Überdimensionierung …
CYCLING_RUN_MIN = 10.0      # … oder so kurzen Läufen je Start wird der Hinweis gezeigt
burner_runs = heat_runs     # Laufzeit je Heizstart ohne Heizlaufzeit-Zähler (siehe analysis)


def _dhw_share(h: dict) -> float | None:
    """Warmwasser-Anteil am Gas der Stunde: nach Brennerleistung, ältere Stunden nach Zeit.

    None: nicht trennbar (Langzeitstatistik aus der Zeit vor HeatPilot).
    """
    v = h.get("dhw_energy_frac")
    return h.get("dhw_frac") if v is None else v


def collect(eng, now_ts: int | None = None) -> dict:
    """Alle Zahlen und Urteile fürs Zertifikat."""
    now_ts = now_ts or int(time.time())
    cfg = eng.cfg
    hourly = [dict(r) for r in eng.store.query("SELECT * FROM hourly ORDER BY ts")]
    heating = [h for h in hourly if h["t_out"] is not None and h["t_out"] <= HEATING_MAX_OUT
               and (h["gas_kwh"] or 0) > 0.05]
    cold = [h for h in heating if h["t_out"] < 0]
    temps = [h["t_out"] for h in hourly if h["t_out"] is not None]
    house = eng.house
    house_ok = guards.check_house(house)

    design_t = cfg.design_outdoor_temp
    design_kw = house.demand_kw(20.0, design_t) if house_ok["ok"] else None
    area = cfg.living_area_m2 or None

    year = [h for h in hourly if h["ts"] >= now_ts - 365 * 86400]
    year_days = (now_ts - year[0]["ts"]) / 86400 if year else 0.0
    full_year = year_days >= FULL_YEAR_DAYS
    year_kwh = sum((h["gas_kwh"] or 0) + (h["gas_spike_kwh"] or 0) for h in year)
    kwh_m2 = year_kwh / area if full_year and area else None

    nom = cfg.boiler_nominal_kw or num(eng._boiler("nom_power"))
    min_pct = cfg.boiler_min_power_pct or num(eng._boiler("min_power"))
    min_kw = nom * min_pct / 100 if nom and min_pct else None
    # Heizstunden, in denen der Bedarf im Mittel unter der Kleinstleistung lag: dort muss er takten
    pure = [h for h in heating if (h.get("dhw_frac") or 0) < 0.2]
    below = (round(100 * sum(1 for h in pure if h["gas_kwh"] < min_kw) / len(pure))
             if min_kw and len(pure) >= 24 else None)

    since = datetime.fromtimestamp(now_ts - 30 * 86400).date().isoformat()
    today = local(now_ts).date().isoformat()  # angefangener Tag drückt das Mittel
    days = [d for k, d in eng.counter_days(since).items() if k != today]
    # ohne Heizstart-Zähler zählen alle Brennerstarts (auch Warmwasser)
    starts_heat = bool(days) and all("heat_starts" in d for d in days)
    starts_day = (sum(d.get("heat_starts", d["starts"]) for d in days) / len(days)) if len(days) >= 3 else None
    weighted = [(d["avg_run_min"], d["heat_starts"]) for d in days if d.get("avg_run_min") and d.get("heat_starts")]
    run_min = (sum(a * n for a, n in weighted) / sum(n for _, n in weighted)) if weighted else None
    run_src = "zaehler"
    if run_min is None:  # kein Heizlaufzeit-Zähler: aus den aufgezeichneten Brennerläufen
        run_min, n_runs = burner_runs(eng.store, now_ts - 30 * 86400)
        run_src = "ereignisse" if n_runs >= 20 else None
        if run_src is None:
            run_min = None

    dhw_ok = guards.check_dhw(eng.dhw)["ok"]
    dhw_loss_w = eng.dhw.loss_w(55.0) if dhw_ok else None

    price, fee = cfg.gas_price_eur_per_kwh, cfg.gas_base_fee_eur_per_year
    eur_year = year_kwh * price + fee if full_year else None

    prelim: list[str] = []
    if not house_ok["ok"]:
        prelim.append("Hausmodell noch unsicher (" + "; ".join(house_ok["reasons"]) + ")")
    if len(heating) < MIN_HEATING_HOURS:
        prelim.append(f"erst {len(heating)} von {MIN_HEATING_HOURS} Heizstunden gemessen")
    if len(cold) < MIN_COLD_HOURS:
        prelim.append("noch kein Frost gemessen")
    cov = house_data.coverage(eng.store)
    if house_data.missing_in(cov):  # 0.42.1: nicht still weniger nehmen
        prelim.append(f"{fmt(cov['no_t_in'])} Stunden ohne Innentemperatur – das Hausmodell lernt sie nicht "
                      "(„Hausdaten wiederherstellen“)")

    verdicts = []
    spec = design_kw * 1000 / area if design_kw and area else None
    cls = energy_class(kwh_m2)
    if cls is not None:
        g = "good" if cls <= 3 else "mid" if cls <= 5 else "bad"
        verdicts.append(("Gebäude", g, f"Klasse {ENERGY_CLASSES[cls][0]}",
                         f"Das Haus braucht {fmt(kwh_m2)} kWh je m² und Jahr (Energieklasse {ENERGY_CLASSES[cls][0]})."))
    elif spec is not None:
        g, word = ((("good", "sehr gut") if spec <= 50 else ("good", "gut") if spec <= 70 else
                    ("mid", "mittel") if spec <= 100 else ("mid", "mäßig") if spec <= 130 else ("bad", "schwach")))
        verdicts.append(("Gebäude", g, word, f"Wärmeschutz {word}: {fmt(spec)} W je m² am kältesten Tag."))
    elif design_kw is not None:
        verdicts.append(("Gebäude", "none", "ohne Fläche",
                         f"Das Haus braucht am kältesten Tag ({fmt(design_t)} °C) rund {fmt(design_kw, 1)} kW."))

    ratio = nom / design_kw if nom and design_kw else None
    if ratio is not None:
        if ratio <= 1.5:
            g, word = "good", "passend"
        elif ratio <= 2.5:
            g, word = "mid", "etwas groß"
        else:
            g, word = "bad", f"{fmt(ratio, 1)}-fach zu groß"
        text = f"Der Kessel ({fmt(nom)} kW) ist {word} für ein Haus mit {fmt(design_kw, 1)} kW Heizlast."
        if min_kw and min_kw > design_kw:
            text += (f" Schon seine kleinste Leistung ({fmt(min_kw, 1)} kW) liegt über dem Bedarf am "
                     f"kältesten Tag – er kann nie durchlaufen, nur takten.")
        elif below is not None and below >= 50:
            text += f" In {below} % der Heizstunden brauchte das Haus weniger als seine kleinste Leistung."
        verdicts.append(("Kesselgröße", g, word, text))

    # Hinweis Ineffizienz: zu großer Kessel taktet – das kostet Gas (Schätzung über Erfahrungswerte)
    # Kein Hochrechnen aus Teilen des Jahres (Sommer/Winter verzerrt) – dann gilt der Messzeitraum
    cycling = None
    if (ratio and ratio > CYCLING_RATIO) or (run_min is not None and run_min < CYCLING_RUN_MIN):
        # Stunden ohne Warmwasser-Angabe zählen nicht (wie bei der Ersparnis) – außer es gibt gar keine
        track = any(_dhw_share(h) is not None for h in year)
        used = [h for h in year if _dhw_share(h) is not None or not track]
        heat_kwh = sum((h["gas_kwh"] or 0) * (1 - min(max(_dhw_share(h) or 0.0, 0.0), 1.0)) for h in used)
        days = (now_ts - used[0]["ts"]) / 86400 if used else 0.0
        if heat_kwh > 0 and days >= 30:
            lo, hi = (heat_kwh * f for f in CYCLING_LOSS)
            cycling = {"pct_lo": round(CYCLING_LOSS[0] * 100), "pct_hi": round(CYCLING_LOSS[1] * 100),
                       "kwh_lo": round(lo), "kwh_hi": round(hi), "eur_lo": round(lo * price), "eur_hi": round(hi * price),
                       "full_year": days >= FULL_YEAR_DAYS, "days": round(days)}
    cycling_text = None
    if cycling:
        period = "im Jahr" if cycling["full_year"] else f"in den bisher gemessenen {cycling['days']} Tagen"
        cycling_text = (f" Häufiges Takten kostet erfahrungsgemäß {cycling['pct_lo']}–{cycling['pct_hi']} % mehr Heizgas – "
                        f"hier etwa {fmt(cycling['eur_lo'])}–{fmt(cycling['eur_hi'])} € {period} (Schätzung).")
        for i, v in enumerate(verdicts):
            if v[0] == "Kesselgröße":
                verdicts[i] = (v[0], v[1], v[2], v[3] + cycling_text)
                cycling_text = None

    if run_min is not None:
        g = "good" if run_min >= 20 else "mid" if run_min >= 10 else "bad"
        word = "ruhig" if g == "good" else "unruhig" if g == "mid" else "taktet stark"
        what = "Heizstarts" if starts_heat else "Brennerstarts (mit Warmwasser)"
        text = (f"Im Schnitt {fmt(starts_day)} {what} am Tag, je Heizstart {fmt(run_min)} min Laufzeit"
                if starts_day is not None else f"Im Schnitt {fmt(run_min)} min Laufzeit je Heizstart")
        text += " – gut wären 20 min und mehr." if g != "good" else "."
        verdicts.append(("Taktverhalten", g, word, text + (cycling_text or "")))

    if dhw_loss_w is not None:
        g = "good" if dhw_loss_w < 60 else "mid" if dhw_loss_w < 100 else "bad"
        word = {"good": "sparsam", "mid": "mittel", "bad": "verlustreich"}[g]
        verdicts.append(("Warmwasser", g, word,
                         f"Warmhalten bei 55 °C kostet {fmt(dhw_loss_w)} W, das sind {fmt(dhw_loss_w * 24 / 1000, 1)} kWh am Tag."))

    # Punkte je Bereich: Gebäude nach Energiekennwert (sonst Heizlast je m²), Kessel nach Verhältnis
    # Nennleistung/Heizlast, Takten nach Laufzeit je Start, Warmwasser nach Warmhalteverlust
    building = (_points(kwh_m2, 30, 250) if kwh_m2 is not None else _points(spec, 40, 150))
    score = overall({
        "Gebäude": building,
        "Kesselgröße": _points(ratio, 1.3, 4.0),
        "Taktverhalten": _points(run_min, 30, 5),
        "Warmwasser": _points(dhw_loss_w, 40, 200),
    })

    return {
        "overall": score,
        "created": local(now_ts).isoformat(),
        "version": __version__,
        "preliminary": prelim,
        "basis": {
            "from": local(hourly[0]["ts"]).date().isoformat() if hourly else None,
            "to": local(hourly[-1]["ts"]).date().isoformat() if hourly else None,
            "hours": len(hourly),
            "heating_hours": len(heating),
            "cold_hours": len(cold),
            "coldest": round(min(temps), 1) if temps else None,
            # 0.42.1: Zeitraum der Rechnung, woraus das Hausmodell lernte, was fehlt (mit Grund)
            "year_from": local(year[0]["ts"]).date().isoformat() if year else None,
            "house_hours": cov["usable"], "house_days": cov["usable_days"], "house_from": cov["usable_from"],
            "house_learned_hours": eng.house.rls.updates, "house_learned_days": eng.house.daily.updates,
            "missing": house_data.missing(cov),
            "note": house_data.basis_text(cov, eng.house.rls.updates, eng.house.daily.updates),
        },
        "house": {
            "ok": house_ok["ok"],
            "h_w_per_k": round(house.h_kw_per_k * 1000) if house_ok["ok"] else None,
            "design_temp": design_t,
            "design_kw": None if design_kw is None else round(design_kw, 2),
            "tau_h": None if not house_ok["ok"] or house.tau_h is None else round(house.tau_h, 1),
            "area_m2": area,
            "spec_w_m2": None if spec is None else round(spec),
            "kwh_m2": None if kwh_m2 is None else round(kwh_m2),
            "energy_class": None if cls is None else ENERGY_CLASSES[cls][0],
            "class_index": cls,
        },
        "boiler": {
            "nominal_kw": nom,
            "min_pct": min_pct,
            "min_kw": None if min_kw is None else round(min_kw, 2),
            "ratio": None if ratio is None else round(ratio, 2),
            "below_min_pct": below,
            "starts_per_day": None if starts_day is None else round(starts_day, 1),
            "starts_heat": starts_heat,
            "run_per_start_min": None if run_min is None else round(run_min, 1),
            "run_source": run_src,
            "cycling_extra": cycling,
        },
        "dhw": {"loss_w_55": None if dhw_loss_w is None else round(dhw_loss_w)},
        "consumption": {"full_year": full_year, "days": round(year_days), "kwh": round(year_kwh),
                        "eur": None if eur_year is None else round(eur_year)},
        "verdicts": [{"area": a, "grade": g, "label": w, "text": t} for a, g, w, t in verdicts],
    }


# ---------------------------------------------------------------------- Zeichnen
FLAME = [("M", 66, 14), ("C", 70, 26, 80, 32, 86, 44), ("C", 94, 58, 96, 72, 94, 82),
         ("C", 92, 96, 80, 108, 64, 110), ("C", 48, 108, 36, 98, 34, 82), ("C", 32, 70, 34, 62, 38, 58),
         ("C", 42, 54, 46, 50, 50, 46), ("C", 50, 54, 53, 60, 58, 62), ("C", 58, 50, 60, 30, 66, 14), ("Z",)]
NODES = [(66, 14), (80, 34), (92, 58), (94, 82), (82, 102), (64, 110), (46, 102), (34, 82),
         (38, 60), (50, 46), (58, 62), (74, 64)]
SPOKES = [(94, 82), (82, 102), (64, 110), (46, 102), (34, 82), (58, 62), (74, 64)]


def draw_logo(p: Page, x: float, y: float, size: float) -> None:
    s = size / 128
    p.rect(x, y, size, size, fill=NAVY, radius=28 * s)
    p.path(FLAME, transform=(x, y, s), clip_gradient=("#e34948", "#eb6834", "#eda100"))
    pt = [(x + a * s, y + b * s) for a, b in
          [(66, 14), (80, 34), (92, 58), (94, 82), (82, 102), (64, 110), (46, 102), (34, 82),
           (38, 60), (50, 46), (58, 62), (74, 64), (66, 14)]]
    p.polyline(pt, color="#fff3d6", lw=2.6 * s)
    hub = (x + 64 * s, y + 84 * s)
    for a, b in SPOKES:
        p.line(*hub, x + a * s, y + b * s, color="#fff3d6", lw=2.6 * s)
    for a, b, c, d in [(74, 64, 92, 58), (74, 64, 80, 34), (38, 60, 58, 62), (58, 62, 66, 14)]:
        p.line(x + a * s, y + b * s, x + c * s, y + d * s, color="#fff3d6", lw=2.6 * s)
    for a, b in NODES:
        p.circle(x + a * s, y + b * s, 5 * s, fill="#ffd27a", stroke=NAVY_DARK, lw=2.5 * s)
    p.circle(*hub, 8 * s, fill="#5fd3ff", stroke=NAVY_DARK, lw=3 * s)


def _date(iso: str | None) -> str:
    return "–" if not iso else datetime.fromisoformat(iso).strftime("%d.%m.%Y")


def _finite(obj):
    """Nicht-endliche Zahlen (NaN aus einem entgleisten Modell) als „unbekannt“ behandeln."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finite(v) for v in obj]
    return obj


FOOT_TOP = 60          # Fußzeile beginnt so weit über dem Seitenende – sicher innerhalb des Rahmens
MIN_STEP = 12.0


def render(data: dict, step: float = 15.0) -> bytes:
    """Eine A4-Seite. Passt der Inhalt nicht über die Fußzeile, wird enger gesetzt."""
    data = _finite(data)
    p = Page()
    W = p.w
    L, R = 56, W - 56

    # Rahmen wie bei einer Urkunde
    p.rect(22, 22, W - 44, p.h - 44, stroke=NAVY, lw=2.2)
    p.rect(28, 28, W - 56, p.h - 56, stroke=ORANGE, lw=0.8)

    draw_logo(p, L, 46, 58)
    p.text(L + 72, 76, "Zertifikat", 30, bold=True, color=NAVY_DARK)
    p.text(L + 72, 96, "Haus & Heizung – gemessen, nicht geschätzt", 12, color=GREY)
    p.text(R, 60, "Ausgestellt am", 8, color=GREY, align="right")
    p.text(R, 73, _date(data["created"]), 11, bold=True, color=NAVY_DARK, align="right")
    p.text(R, 86, "von EMS HeatPilot " + data["version"], 8, color=GREY, align="right")

    b = data["basis"]
    basis = (f"Grundlage: Messdaten vom {_date(b['from'])} bis {_date(b['to'])} · {fmt(b['heating_hours'])} "
             f"Heizstunden, {fmt(b['cold_hours'])} davon unter 0 °C · kälteste Stunde {fmt(b['coldest'], 1)} °C")
    if b.get("note"):  # 0.42.1: woraus das Hausmodell lernte, was fehlt
        basis += f". {b['note']}"
    y = p.paragraph(L, 124, basis, R - L, 9, color=GREY)
    p.line(L, y - 4, R, y - 4, color=ORANGE, lw=1.2)
    y += 14

    o = data.get("overall")
    if o:
        idx = o["class_index"]
        p.rect(L, y - 6, 54, 44, fill=CLASS_COLORS[idx], radius=6)
        p.text(L + 27, y + 24, o["class"], 24, bold=True, color="#333333" if idx in (3, 4, 5) else "#ffffff",
               align="center")
        p.text(L + 66, y + 8, "HeatPilot-Gesamtbewertung", 12, bold=True, color=NAVY)
        p.text(L + 66, y + 24, f"{o['score']} von 100 Punkten", 11, color=NAVY_DARK)
        parts = " · ".join(f"{x['area']} {x['points']}" if x["points"] is not None else f"{x['area']} –"
                           for x in o["parts"])
        p.text(L + 66, y + 36, parts + " (gewichtet 40/25/20/15 %)", 8, color=GREY)
        y += 64

    grades = {v["area"]: v for v in data["verdicts"]}

    def badge(v: dict, y: float, prefix: str = "") -> None:
        label = prefix + v["label"]
        w = text_width(label, 9, True) + 16
        p.rect(R - w, y - 12, w, 16, fill=GRADE_COLORS[v["grade"]], radius=8)
        p.text(R - w / 2, y - 1, label, 9, bold=True, color="#ffffff", align="center")

    def heading(title: str, area: str | None, y: float) -> float:
        p.text(L, y, title, 13, bold=True, color=NAVY)
        if area and area in grades:
            badge(grades[area], y)
        return y + 18

    def row(label: str, value: str, y: float, note: str = "") -> float:
        p.text(L + 4, y, label, 10, color="#222222")
        p.text(L + 250, y, value, 10, bold=True, color=NAVY_DARK)
        if note:
            p.text(L + 250 + text_width(value, 10, True) + 6, y, note, 8, color=GREY)
        return y + step

    h, bo = data["house"], data["boiler"]
    # --- Gebäude
    y = heading("Gebäude", "Gebäude", y)
    y = row("Wärmeverlust", f"{fmt(h['h_w_per_k'])} W/K", y, "Leistung je Grad Unterschied innen/außen")
    y = row(f"Heizlast am kältesten Tag ({fmt(h['design_temp'])} °C)", f"{fmt(h['design_kw'], 1)} kW", y, "Gasleistung")
    y = row("Trägheit (Auskühlen)", f"{fmt(h['tau_h'])} h", y)
    if h["area_m2"]:
        y = row("Wohnfläche", f"{fmt(h['area_m2'])} m²", y)
        y = row("Heizlast je m²", f"{fmt(h['spec_w_m2'])} W/m²", y)
    y = row("Energiekennwert", f"{fmt(h['kwh_m2'])} kWh/(m²·a)" if h["kwh_m2"] is not None else "–", y,
            "" if h["kwh_m2"] is not None else
            ("nach 12 Monaten Messung" if h["area_m2"] else "Wohnfläche in den Einstellungen angeben"))
    # Skala wie im Energieausweis
    y += 4
    seg = (R - L) / len(ENERGY_CLASSES)
    idx = h["class_index"]
    for i, (name, _) in enumerate(ENERGY_CLASSES):
        x = L + i * seg
        p.rect(x, y, seg - 2, 16, fill=CLASS_COLORS[i] if idx is not None else "#e3e6ea", radius=3)
        dark = idx is None or i in (3, 4, 5)
        p.text(x + seg / 2 - 1, y + 11.5, name, 9, bold=True, color="#333333" if dark else "#ffffff", align="center")
    if idx is not None:
        cx = L + idx * seg + seg / 2 - 1
        p.polygon([(cx - 6, y + 26), (cx + 6, y + 26), (cx, y + 18)], fill=NAVY_DARK)
    y += 44

    # --- Kessel
    y = heading("Heizkessel", "Kesselgröße", y)
    y = row("Nennleistung", f"{fmt(bo['nominal_kw'])} kW" if bo["nominal_kw"] else "–", y,
            "" if bo["nominal_kw"] else "in den Einstellungen angeben")
    y = row("Kleinste Leistung", f"{fmt(bo['min_kw'], 1)} kW" if bo["min_kw"] else "–", y,
            f"{fmt(bo['min_pct'])} % der Nennleistung" if bo["min_kw"] else "in den Einstellungen angeben")
    y = row("Nennleistung im Verhältnis zur Heizlast", f"{fmt(bo['ratio'], 1)}-fach" if bo["ratio"] else "–", y,
            "ideal: 1,0 bis 1,5")
    y = row("Heizstunden unter der kleinsten Leistung", f"{bo['below_min_pct']} %" if bo["below_min_pct"] is not None else "–", y,
            "dann muss der Kessel takten")
    y = row("Heizstarts pro Tag" if bo.get("starts_heat", True) else "Brennerstarts pro Tag", fmt(bo["starts_per_day"]), y,
            "Mittel der letzten 30 Tage" + ("" if bo.get("starts_heat", True) else ", mit Warmwasser"))
    if "Taktverhalten" in grades:
        badge(grades["Taktverhalten"], y, "Takten: ")
    y = row("Laufzeit je Heizstart", f"{fmt(bo['run_per_start_min'])} min" if bo["run_per_start_min"] else "–", y,
            "Mittel der letzten 30 Tage" if bo["run_per_start_min"] else "")
    ce = bo.get("cycling_extra")
    if ce:
        full = ce.get("full_year", True)
        y = row("Mehrkosten durch Takten (Schätzung)",
                f"{fmt(ce['eur_lo'])}–{fmt(ce['eur_hi'])} € " + ("/ Jahr" if full else f"in {ce['days']} Tagen"), y,
                f"{ce['pct_lo']}–{ce['pct_hi']} % des Heizgases, Erfahrungswerte"
                + ("" if full else "; kein volles Jahr gemessen"))
    # Balken: Bedarf gegen Kessel
    bars = [("Heizlast am kältesten Tag", h["design_kw"], ORANGE),
            ("Kleinste Kesselleistung", bo["min_kw"], "#8a9bb0"),
            ("Nennleistung", bo["nominal_kw"], NAVY)]
    top = max((v for _, v, _ in bars if v), default=0)
    if top and h["design_kw"] and bo["nominal_kw"]:
        y += 4
        full = R - L - 190
        for label, v, col in bars:
            p.text(L + 4, y + 9, label, 9, color="#222222")
            if v:
                w = max(full * v / top, 2)
                p.rect(L + 150, y, w, 12, fill=col, radius=2)
                p.text(L + 150 + w + 5, y + 9.5, f"{fmt(v, 1)} kW", 9, bold=True, color=NAVY_DARK)
            y += step + 2
        y += 6
    else:
        y += 6
    # --- Warmwasser und Verbrauch
    y = heading("Warmwasser & Verbrauch", "Warmwasser", y + 8)
    d, c = data["dhw"], data["consumption"]
    y = row("Warmhalten des Speichers bei 55 °C", f"{fmt(d['loss_w_55'])} W" if d["loss_w_55"] else "–", y,
            "" if d["loss_w_55"] else "noch zu wenig Ladungen gemessen")
    y = row("Gasverbrauch letzte 12 Monate" if c["full_year"] else f"Gasverbrauch ({fmt(c['days'])} Tage)",
            f"{fmt(c['kwh'])} kWh", y, "Heizung und Warmwasser")
    y = row("Gaskosten pro Jahr", f"{fmt(c['eur'])} €" if c["eur"] is not None else "–", y,
            "inkl. Grundpreis" if c["eur"] is not None else "nach 12 Monaten Messung")

    # --- Urteil
    y += 10
    texts = [v["text"] for v in data["verdicts"]] or ["Für ein Urteil fehlen noch Messdaten."]
    lines_h = sum(len(_wrap_lines(t, R - L - 150)) for t in texts) * 13 + len(texts) * 4
    box_h = max(lines_h + 34, 96)
    p.rect(L - 8, y, R - L + 16, box_h, fill=LIGHT, radius=8)
    p.text(L + 4, y + 20, "Urteil", 13, bold=True, color=NAVY)
    ty = y + 38
    for t in texts:
        p.circle(L + 8, ty - 3.5, 2.2, fill=ORANGE)
        ty = p.paragraph(L + 16, ty, t, R - L - 150, 10, color="#222222", leading=13) + 4

    # Stempel
    prelim = bool(data["preliminary"])
    col = GRADE_COLORS["bad"] if prelim else GRADE_COLORS["good"]
    cx, cy = R - 58, y + box_h / 2
    p.circle(cx, cy, 46, stroke=col, lw=2.5)
    p.circle(cx, cy, 40, stroke=col, lw=0.8)
    tilt = math.radians(12)
    for d, label, size in ((-13, "HeatPilot", 8.5), (4, "VORLÄUFIG" if prelim else "GEMESSEN", 12),
                           (18, data["created"][:4], 8.5)):
        p.text(cx + d * math.sin(tilt), cy + d * math.cos(tilt), label, size, bold=True,
               color=col, align="center", angle=12)
    y += box_h + 12

    if prelim:
        y = p.paragraph(L, y, "Vorläufig, weil: " + "; ".join(data["preliminary"]) + ".", R - L, 8.5,
                        color=GRADE_COLORS["bad"])
    foot = ("Automatisch erstellt aus den Messdaten von EMS HeatPilot. Ohne Gewähr. Kein Energieausweis "
            "nach GEG; Werte nicht witterungsbereinigt. Leistungen und Verbrauch sind Gas (einschließlich "
            "Kesselwirkungsgrad). Enthält keine Namen, Adressen oder Standortdaten.")
    if y + 6 > p.h - FOOT_TOP and step > MIN_STEP:
        return render(data, step - 1)
    p.paragraph(L, max(y + 6, p.h - FOOT_TOP), foot, R - L, 7.5, color=GREY, leading=9.5)
    return document([p], title="Zertifikat Haus & Heizung", author="EMS HeatPilot")


def _wrap_lines(text: str, width: float) -> list[str]:
    from .pdf import wrap
    return wrap(text, width, 10)


def pdf(eng, now_ts: int | None = None) -> bytes:
    return render(collect(eng, now_ts))
