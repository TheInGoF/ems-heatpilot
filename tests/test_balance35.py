"""0.35 Hydraulik-Helfer: Stufen in den Texten, Wirkung auf die anderen Räume, Änderungsverfolgung."""

import json
from types import SimpleNamespace

from heatpilot import advice, diagnostics
from heatpilot.config import Config
from test_advice import balance_engine, by_kind

WEAK, STRONG, A, B, C = "climate.schwach", "climate.stark", "climate.a", "climate.b", "climate.c"


def with_presets(eng, presets: dict):
    """Voreinstellung (Stufe, Stufen) an den Räumen der Engine setzen; fehlende Räume bleiben unbekannt."""
    for r in eng.found.rooms:
        p = presets.get(r.climate)
        if p is not None:
            r.preset, r.preset_max = p
    return eng


def abgleich(eng, now):
    return {k[0]: a for k, a in by_kind(advice.build(eng, now)).items() if k[1] == "abgleich"}


# --- Texte mit Stufen -----------------------------------------------------------------------------
def test_texts_name_the_step_and_the_expected_effect(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (5, 6), STRONG: (4, 6), A: (4, 6), B: (4, 6)})  # C: unbekannt
    items = abgleich(eng, now)
    under = items[WEAK]
    assert "Voreinstellung von 5 auf 6 stellen (Skala 1–6)" in under["text"]
    assert under["preset"] == {"from": 5, "to": 6, "max": 6}
    assert "Erwartung (grob): Schwach ≈ −" in under["text"] and "HeatPilot misst nach der Änderung nach" in under["text"]
    assert "Ohne Voreinstellung in den Einstellungen: C" in under["text"]
    exp = {e["room"]: e["delta_k"] for e in under["expected"]}
    assert under["expected"][0]["room"] == WEAK and exp[WEAK] < 0  # geänderter Raum sinkt
    assert all(exp[c] > 0 for c in (STRONG, A, B)) and C not in exp
    assert under["evidence"]["Voreinstellung (Stufe/Stufen)"] == "5/6"
    assert "eine Stufe höher" not in under["text"]  # alte vage Formulierung ersetzt
    over = items[STRONG]
    assert "Voreinstellung von 4 auf 3 stellen (Skala 1–6)" in over["text"] and over["preset"] == {"from": 4, "to": 3, "max": 6}
    assert "Erwartung (grob): Stark ≈ +" in over["text"]
    assert {e["room"]: e["delta_k"] for e in over["expected"]}[STRONG] > 0  # gedrosselt: braucht mehr Vorlauf


def test_maximum_reached_names_alternatives_and_the_room_with_most_surplus(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (6, 6), STRONG: (5, 6), A: (4, 6)})
    under = abgleich(eng, now)[WEAK]
    assert "steht schon auf 6/6 – mehr geht am Ventil nicht" in under["text"]
    assert "Heizkörper entlüften" in under["text"] and "Pumpenstufe oder Vorlauf prüfen" in under["text"]
    assert "Stark – der Raum mit dem größten Überschuss – eine Stufe zurück (von 5 auf 4, Skala 1–6)" in under["text"]
    assert under["preset"] is None and under["expected"] == []


def test_no_step_back_at_level_one_and_no_scale_without_preset_valve(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {STRONG: (1, 6)})
    items = abgleich(eng, now)
    assert STRONG not in items and WEAK in items  # Stufe 1: „zurück“ nicht empfehlen
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (None, 0), STRONG: (None, 0)})  # kein Voreinstellventil
    items = abgleich(eng, now)
    assert "Rücklaufverschraubung etwas weiter öffnen" in items[WEAK]["text"] and "Skala" not in items[WEAK]["text"]
    assert "Rücklaufverschraubung etwas zudrehen" in items[STRONG]["text"] and "Stufe" not in items[STRONG]["text"]
    assert items[WEAK]["preset"] is None and "Voreinstellung (Stufe/Stufen)" not in items[WEAK]["evidence"]


def test_unknown_preset_keeps_old_advice_with_hint(tmp_path):
    eng, now = balance_engine(tmp_path)
    items = abgleich(eng, now)  # nichts eingetragen
    assert "eine Stufe höher stellen" in items[WEAK]["text"] and "in den Einstellungen eintragen" in items[WEAK]["text"]
    assert "eine Stufe zurück (kleinere Zahl)" in items[STRONG]["text"] and items[STRONG]["expected"] == []
    with_presets(eng, {WEAK: (None, 8)})  # Stufen bekannt, Stufe nicht
    text = abgleich(eng, now)[WEAK]["text"]
    assert "eine Stufe höher stellen (Skala 1–8)" in text and "aktuelle Stufe in den Einstellungen eintragen" in text


def test_member_with_own_preset_is_named(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (5, 6)})
    weak = eng.found.room_of(WEAK)
    weak.members.append({"climate": "climate.schwach_2", "heating_power": None, "humidity": None,
                         "temp_sensor": None, "platform": "tado", "preset": 3, "preset_max": 6})
    eng.states["climate.schwach_2"] = {"state": "heat", "attributes": {"friendly_name": "Schwach Fenster"}}
    text = abgleich(eng, now)[WEAK]["text"]
    assert "weitere Ventile im Raum: Schwach Fenster: 3/6" in text


# --- Simulation -----------------------------------------------------------------------------------
def test_simulate_preset_shares_monotony_and_no_division_by_zero():
    data = {c: {"flow": f, "target": 21.0} for c, f in ((WEAK, 65.0), (A, 50.0), (B, 48.0), (C, 47.0))}
    presets = {WEAK: (3, 6), A: (4, 6), B: (4, 6), C: (None, 6)}
    shares = advice.flow_shares(presets)
    assert C not in shares and abs(sum(shares.values()) - 1) < 1e-9
    assert shares[WEAK] < shares[A] == shares[B]
    d4 = advice.simulate_preset(data, presets, WEAK, 4)
    d6 = advice.simulate_preset(data, presets, WEAK, 6)
    assert set(d4) == {WEAK, A, B}  # C ohne Stufe: nicht simuliert
    assert d4[WEAK] < 0 and d4[A] > 0 and d4[B] > 0 and d6[WEAK] < d4[WEAK] and d6[A] > d4[A]
    # größere Übertemperatur = größere Wirkung; Schritt zurück hebt den geänderten Raum
    assert advice.simulate_preset(data, presets, WEAK, 2)[WEAK] > 0
    # Räume ohne Übertemperatur (flow <= target) ändern sich nicht
    cold = {**data, A: {"flow": 20.0, "target": 21.0}}
    assert advice.simulate_preset(cold, presets, WEAK, 4)[A] == 0
    # keine Division durch null: unbekannte oder ungültige Stufen
    assert advice.simulate_preset(data, presets, C, 5) == {}
    assert advice.simulate_preset(data, presets, WEAK, 0) == {}
    assert advice.simulate_preset(data, {WEAK: (0, 0), A: (0, 6)}, WEAK, 3) == {}
    assert advice.simulate_preset({}, presets, WEAK, 4) == {}
    assert advice.flow_shares({}) == {}
    # Kelvin-Format: deutsches Komma, echtes Minus
    assert advice._fmt_k(-3.2) == "−3 K" and advice._fmt_k(0.46) == "+0,5 K" and advice._fmt_k(0.01) == "±0 K"


# --- Änderungsverfolgung --------------------------------------------------------------------------
def test_record_preset_changes_only_between_known_steps(tmp_path):
    eng, now = balance_engine(tmp_path)
    store = eng.store
    assert advice.record_preset_changes(store, {WEAK: None}, {WEAK: 5}, {WEAK: 61.0}, now) == []  # erstes Eintragen
    assert advice.record_preset_changes(store, {WEAK: 5}, {WEAK: 5}, {}, now) == []
    new = advice.record_preset_changes(store, {WEAK: 5, A: 4}, {WEAK: 6, A: None}, {WEAK: 61.0}, now)
    assert new == [{"ts": now, "room": WEAK, "from": 5, "to": 6, "flow_before": 61.0}]
    assert store.get(advice.PRESET_CHANGES_KEY) == new
    # alte Einträge (> 60 Tage) fallen beim nächsten Schreiben heraus
    store.put(advice.PRESET_CHANGES_KEY, [{"ts": now - 61 * 86400, "room": A, "from": 3, "to": 4, "flow_before": 50.0}])
    advice.record_preset_changes(store, {WEAK: 6}, {WEAK: 5}, {WEAK: None}, now)
    kept = advice.preset_changes(store, now)
    assert [c["room"] for c in kept] == [WEAK] and kept[0]["flow_before"] is None
    assert advice.presets_of(Config.from_raw({"rooms": [{"climate": WEAK, "preset": 5}, {"climate": A}]})) == {WEAK: 5, A: None}
    assert advice.room_flow(eng, WEAK) > advice.room_flow(eng, A) > 21


def test_pause_effect_and_forgetting_after_a_change(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (6, 6), STRONG: (4, 6)})
    before = advice.room_flow(eng, WEAK) + 6.0  # vorher brauchte der Raum 6 K mehr (Modell seither nachgelernt)

    def change(days_ago, room=WEAK, flow_before=before):
        eng.store.put(advice.PRESET_CHANGES_KEY, [{"ts": now - days_ago * 86400, "room": room, "from": 5, "to": 6,
                                                   "flow_before": flow_before}])
        return by_kind(advice.build(eng, now))

    # Tag 3: keine Abgleich-Empfehlung für den Raum, Info-Karte „misst neu“; andere Räume unberührt
    items = change(3)
    assert (WEAK, "abgleich") not in items and (STRONG, "abgleich") in items
    info = items[(WEAK, "abgleich_misst")]
    assert info["impact"] == "Info" and "HeatPilot misst neu" in info["title"] and "10 Tage" in info["text"]
    assert info["since"] == now - 3 * 86400 and info["preset"] == {"from": 5, "to": 6, "max": None}
    # Tag 12: Pause vorbei, Empfehlung wieder da (noch ohne Wirkungszeile), keine Info-Karte
    items = change(12)
    under = items[(WEAK, "abgleich")]
    assert "Wirkung" not in under["text"] and not any(k[1].startswith("abgleich_") for k in items)
    # Tag 20: Wirkungszeile in der Empfehlung, keine doppelte Info-Karte
    items = change(20)
    text = items[(WEAK, "abgleich")]["text"]
    assert f"Wirkung: braucht jetzt {advice.room_flow(eng, WEAK):.0f} °C statt {before:.0f} °C Vorlauf" in text
    assert (WEAK, "abgleich_wirkung") not in items
    # Tag 20 für einen Raum ohne Empfehlung (A): eigene Info-Karte mit Beleg
    items = change(20, room=A, flow_before=advice.room_flow(eng, A) + 2)
    card = items[(A, "abgleich_wirkung")]
    assert card["impact"] == "Info" and "statt" in card["text"] and card["evidence"]["Vorlauf vorher (°C)"] is not None
    assert card["evidence"]["Vorlauf jetzt (°C)"] == round(advice.room_flow(eng, A))
    # unbekannter Vorlauf vorher: „noch nicht messbar“
    assert "noch nicht messbar" in change(20, room=A, flow_before=None)[(A, "abgleich_wirkung")]["text"]
    # Tag 70: vergessen
    items = change(70)
    assert "Wirkung" not in items[(WEAK, "abgleich")]["text"] and not any(k[1].startswith("abgleich_") for k in items)
    # Änderung an einem Mitläufer zählt für den Raum des Schlüssels
    eng.found.room_of(WEAK).members.append({"climate": "climate.schwach_2", "heating_power": None, "humidity": None,
                                            "temp_sensor": None, "platform": "tado", "preset": None, "preset_max": None})
    assert (WEAK, "abgleich_misst") in change(2, room="climate.schwach_2")


def test_info_cards_are_not_tracked_as_resolved(tmp_path):
    eng, now = balance_engine(tmp_path)
    with_presets(eng, {WEAK: (5, 6)})
    eng.store.put(advice.PRESET_CHANGES_KEY, [{"ts": now - 2 * 86400, "room": A, "from": 3, "to": 4, "flow_before": 50.0}])
    items = advice.build(eng, now)
    assert advice.track(eng.store, items, now) == []
    assert all("key" in a and "since" in a for a in items)
    assert "climate.a|abgleich_misst" not in str(eng.store.get("advice_state"))
    eng.store.put(advice.PRESET_CHANGES_KEY, [])
    resolved = advice.track(eng.store, advice.build(eng, now + 60), now + 60)
    assert not any("misst neu" in r["title"] for r in resolved)


def test_export_lists_changes_without_coordinates(tmp_path):
    eng, now = balance_engine(tmp_path)
    eng.found.home_zone = (53.55, 10.0)
    eng.store.put(advice.PRESET_CHANGES_KEY, [{"ts": now, "room": WEAK, "from": 5, "to": 6, "flow_before": 61.0}])
    data = diagnostics.export(SimpleNamespace(eng=eng, cfg=eng.cfg), days=1)
    assert data["abgleich_aenderungen"][0]["room"] == WEAK
    text = json.dumps(data, default=str)
    assert "53.55" not in text and "latitude" not in text
