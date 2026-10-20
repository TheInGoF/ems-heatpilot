"""0.48 Oberfläche: „Antipendelzeit anpassen“ unter Einstellungen → Kessel (Schalter, Entität, Regel in zwei Sätzen +
„mehr“), Zeile in Statusbox und Übersicht → Status, Protokoll in Minuten – und der Warnkasten „Regler am Bus“ nur noch,
solange laut Regler-Prüfung ein Regler am Bus ist oder es unbekannt ist."""
import json
import re

from test_boiler41 import FLOW, HEAT, T0
from test_boiler_period48 import PER, setup, step
from test_ui_v39 import as_json
from test_ui_v41 import js_run
from test_ui_v361 import plain, script, source


def test_regulator_warnbox_only_with_regulator_or_unknown(tmp_path):
    r = js_run(tmp_path, """
      const S = (rm) => ({ boiler_control: "heatpilot", regulator_removed: rm });
      const X = (present) => present === undefined ? {} : { status: { regulator: { present, text: "Prüfung" } } };
      out({ yes: boilerSectionHtml(S(false), X(true)), unknown: boilerSectionHtml(S(false), X(null)),
            seen: boilerSectionHtml(S(true), X(true)), nostatus: boilerSectionHtml(S(false), X(undefined)),
            no: boilerSectionHtml(S(false), X(false)), removed: boilerSectionHtml(S(true), X(undefined)) });""")
    for k in ("yes", "unknown", "seen", "nostatus"):  # Regler am Bus bzw. unbekannt: Warnkasten wie bisher
        assert 'id="boiler-warn"' in r[k] and 'id="boiler-failsafe"' not in r[k], k
        assert "Regler am Bus: Ein Regler am EMS-Bus überschreibt alle 60 s" in plain(r[k]), k
    for k in ("no", "removed"):  # kein Regler bzw. „Regler ist abgebaut“: nur der kurze, neutrale Hinweis
        sec = r[k]
        assert 'id="boiler-warn"' not in sec and 'class="warnbox"' not in sec, k
        hint = re.search(r'<div class="hint" id="boiler-failsafe"><details class="more"><summary>(.*?)</summary>', sec)
        assert plain(hint.group(1)) == "Fällt HeatPilot aus, bleibt der letzte Vorlauf stehen – dafür den Wächter anlegen.", k
        body = sec.split('id="boiler-failsafe"')[1].split('id="boiler-box"')[0]
        assert '<span class="m"></span>' in hint.group(1) and "Regler am Bus:" not in plain(body)
        assert "bleibt der letzte Vorlauf stehen – nachts vielleicht 35 °C" in plain(body)  # unter „mehr“


def test_period_switch_entity_rule_and_lines(tmp_path):
    _, _, bc = setup(tmp_path)
    step(bc, T0)
    b = as_json(bc.as_dict(T0))
    x = {"status": b, "suggested": {"flow": FLOW, "heating": HEAT, "period": PER}}
    r = js_run(tmp_path, f"""const B = {json.dumps(b)}, X = {json.dumps(x)};
      const S = {{ boiler_control: "heatpilot", boiler_period: true }};
      out({{ sec: boilerSectionHtml(S, X), off: boilerSectionHtml({{ ...S, boiler_period: false }}, X),
            line: boilerLine(B), facts: boilerFacts(B), quiet: boilerLine({{ ...B, period: {{ status_text: null }} }}),
            val: boilerLogValue({{ action: "boiler_period", value: 10 }}), lab: BOILER_ACTION.boiler_period_back }});""")
    sec = r["sec"]
    assert '<input id="f-boiler_period" type="checkbox" checked> Antipendelzeit anpassen</label>' in sec
    assert '<input id="f-boiler_period" type="checkbox"> Antipendelzeit anpassen</label>' in r["off"]  # Standard aus
    field = sec.split('id="f-boiler_period"')[1]
    rule = plain(re.search(r"<summary>(.*?)<span class=\"m\">", field).group(1))
    assert rule == ("Kurz (10 min), solange die Räume mit offenem Ventil die kleinste Brennerleistung abnehmen; sonst der "
                    "Wert vom Einschalten, mindestens 25 min. Wirkt nur bei „HeatPilot steuert“ – bei „Beobachten“ steht "
                    "im Protokoll, was HeatPilot schreiben würde.")
    assert rule.count(". ") + 1 == 2  # höchstens zwei Sätze, der Rest unter „mehr“
    ref = plain(re.search(r'<p class="em-ref" id="boiler-period-ref">(.*?)</p>', sec).group(1))
    assert ref == f"Antipendelzeit: {PER} (automatisch erkannt). jetzt 25 min · Grundwert 25 min"
    want = "Antipendelzeit: 10 min (Abnahme reicht: 1 Raum nimmt die Mindestleistung 5,4 kW ab 52 °C ab)"
    assert b["period"]["status_text"] == want + " · Kessel hat noch 25 min – wird jetzt geschrieben"
    assert want in plain(r["facts"]) and "<b>Antipendelzeit:</b> 10 min" in r["facts"]
    assert 'id="ov-period"' in r["line"] and want in plain(r["line"])  # Übersicht → Status, solange sie an ist
    assert 'id="ov-period"' not in r["quiet"]
    assert r["val"] == "10 min" and r["lab"] == "Antipendelzeit zurück"


def test_period_saved_and_picked_only_if_fitting():
    js = script()
    assert 'if ($("f-boiler_period")) out.boiler_period = $("f-boiler_period").checked;' in js
    assert "period: { filter: (e) => !!(e.period && e.period.fits)" in source(js, "PICKERS")
    for name in ("boilerPeriodRule", "boilerPeriodRef", "boilerPeriodFact"):
        assert source(js, name).count("\n") <= 20, name
