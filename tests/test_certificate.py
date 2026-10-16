"""Zertifikat über Haus und Heizung: Zahlen, Urteile, gültige PDF-Datei."""

import math
import re
import zlib
from datetime import datetime

from heatpilot import certificate
from heatpilot.config import Config
from heatpilot.discovery import Discovered, Room
from heatpilot.engine import Engine
from heatpilot.store import Store

NOW = int(datetime(2026, 10, 16, 12, 0).timestamp())


def year_engine(tmp_path, days=370, area=85.0, nominal=18.0, min_pct=30.0, frost=True):
    cfg = Config(living_area_m2=area, boiler_nominal_kw=nominal, boiler_min_power_pct=min_pct)
    eng = Engine(cfg, Store(tmp_path / "z.db"), Discovered(rooms=[Room("Wz", "climate.wz")]))
    eng.house.rls.theta, eng.house.rls.updates = [0.16, 2.7, 0.05], 4650
    rows = []
    start = NOW - days * 86400
    for i in range(days * 24):
        ts = start + i * 3600
        season = math.cos(2 * math.pi * (ts - NOW + 270 * 86400) / (365 * 86400))
        t_out = 9 - 10 * season - 3 * math.cos(2 * math.pi * (i % 24) / 24)
        if not frost:
            t_out = max(t_out, 2.0)
        gas = max(0.16 * (20 - t_out) + 0.05, 0.0) if t_out < 15 else 0.3
        rows.append((ts, t_out, 20.5, gas, 0.05, 0.6))
    eng.store.add_hourly(rows)
    midnight = int(datetime(2026, 10, 16).timestamp())
    for d in range(1, 30):  # Zähler der Therme: 24 Heizstarts à 8 min pro Tag
        day = midnight - d * 86400
        for k, ts in enumerate((day + 600, day + 86400 - 600)):
            eng.store.add_sample(ts, {"burn_starts": 1000 - d * 26 + k * 26,
                                      "heat_starts": 900 - d * 24 + k * 24,
                                      "heat_min": 5000 - d * 192 + k * 192})
    eng.store.commit()
    return eng


def test_numbers_and_verdicts(tmp_path):
    data = certificate.collect(year_engine(tmp_path), NOW)
    h, b = data["house"], data["boiler"]
    assert h["h_w_per_k"] == 160 and h["design_kw"] == 5.17  # 0,16 × 32 K + 0,05
    assert h["spec_w_m2"] == round(5170 / 85)
    assert data["consumption"]["full_year"] and h["energy_class"] is not None
    assert b["min_kw"] == 5.4 and b["ratio"] == round(18 / 5.17, 2)
    assert b["below_min_pct"] > 90 and b["run_per_start_min"] == 8.0 and b["starts_per_day"] == 24.0
    v = {x["area"]: x for x in data["verdicts"]}
    assert v["Kesselgröße"]["grade"] == "bad" and "nie durchlaufen" in v["Kesselgröße"]["text"]
    assert v["Taktverhalten"]["grade"] == "bad"
    assert data["preliminary"] == []


def test_preliminary_without_winter_and_without_area(tmp_path):
    data = certificate.collect(year_engine(tmp_path, days=40, area=0.0, frost=False), NOW)
    assert any("Frost" in r for r in data["preliminary"])
    assert data["house"]["energy_class"] is None and data["house"]["spec_w_m2"] is None
    assert data["consumption"]["eur"] is None  # kein volles Jahr: keine Jahreskosten


def test_pdf_is_valid_and_contains_no_personal_data(tmp_path):
    eng = year_engine(tmp_path)
    eng.found.persons = ["person.max_mustermann"]
    pdf = certificate.pdf(eng, NOW)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    # xref-Verweis zeigt auf die Tabelle
    start = int(re.search(rb"startxref\n(\d+)", pdf).group(1))
    assert pdf[start:start + 4] == b"xref"
    text = b"".join(zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", pdf, re.S))
    assert "Zertifikat".encode("cp1252") in text and "Gebäude".encode("cp1252") in text
    assert b"mustermann" not in text.lower() and b"person." not in text
