"""0.66: zweite Ebene der Einstellungen – Einstellungen → Räume (1. Klick) → Wohnzimmer (2. Klick). Jede Seite zeigt nur
Kacheln (je Abschnitt eine, Räume zusätzlich je Raum), ein Klick öffnet eine Unterseite mit nur diesem Abschnitt;
„Experte“ ist kein Aufklapper mehr, seine Teile sind schlichtere Kacheln am Ende; „Gewohnheiten erkennen“ (früher Strom & Aktivität) steht vorn."""

from test_ui_v361 import html, script, source


def test_sections_become_tiles_with_subpages():
    js, page = script(), html()
    sec = source(js, "setSections")
    assert 'pg.querySelectorAll(":scope > .card:not(.glance)")' in sec and "el.dataset.exp" in sec
    assert 'Experte <span class="muted">– selten nötig</span>' in sec
    assert 'onclick="setSub(' in sec and "showSub();" in sec
    show = source(js, "showSub")
    assert 'el.dataset.sec === sub || (sub === "room" && el === rooms)' in show and '`‹ ${sub ? page : "Einstellungen"}`' in show
    assert 'if (SET_SUB) { SET_SUB = null; ROOM_SEL = null; roomTiles(); showSub(); } else setPage("menu");' in source(js, "setBack")
    assert 'ROOM_SEL = c; SET_SUB = "room";' in source(js, "roomPick")
    assert "if (was !== p) SET_SUB = null;" in source(js, "setPage") and "setSections();" in source(js, "setPage")
    assert "details.expert" not in js and '<div class="exp-parts" id="exp-${k}">' in source(js, "expert")
    assert ".subpage > [hidden] { display: none !important; }" in page
