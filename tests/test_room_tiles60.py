"""0.60: Einstellungen → Räume als Kacheln (Konzeptpapier, Abschnitt 5) – Qualität je Raum (Stufe, Raumfühler,
Lernfortschritt), ein Klick öffnet darunter die Raumansicht mit Gerätekarten; tado mit „indirekte Regelung, träge“."""

from test_ui_v361 import between, html, script, source


def test_tiles_and_room_view_are_wired():
    js, page = script(), html()
    rs = between(js, "function renderSettings()", "\nfunction ")
    assert '<div class="rt-grid" id="room-tiles"></div>' in rs
    assert 'oninput="roomsDirty(); roomTiles()" onchange="roomsDirty(); roomTiles()"' in rs
    assert "roomTiles();" in source(js, "renderSettings") and "roomTiles();" in source(js, "loadHeat")
    tiles = source(js, "roomTiles")
    for part in ("STAGE_NAME[st], st > 1", '"kein Raumfühler"', "m.text, m.ok", '"lernt nicht"', "roomDevices(tr)"):
        assert part in tiles, part
    assert 'return ["tado", "homekit_controller"].includes(plat) ? 1 : 2;' in source(js, "roomStage")
    assert '"Modell plausibel"' in source(js, "roomModel") and '"Modell lernt"' in source(js, "roomModel")
    dev = source(js, "roomDevices")
    assert "indirekte Regelung, träge" in dev and "dringend empfohlen" in dev and "nicht erreichbar" in dev
    assert "#room-table .room { display: none; }" in page and "#room-table .room.sel { display: block;" in page
    assert "roomStates" not in js  # 0.60: die Gerätekarten ersetzen die Zeilen „automatisch gefunden“
