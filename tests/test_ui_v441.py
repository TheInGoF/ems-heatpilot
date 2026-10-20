"""0.44.1: Diagnose-Datei und gelernte Werte holt die Seite selbst – ein direkter Link öffnet sich in der
Home-Assistant-App außerhalb der Anmeldung („401: Unauthorized“). Danach Speichern, Teilen und Kopieren."""
import json
import re
import subprocess
from pathlib import Path

import pytest
from test_ui_v361 import NODE, html, script, source

ROOT = Path(__file__).resolve().parents[1]


def test_both_download_links_go_through_the_page():
    page = html()
    for href, box in (("api/diagnostics/bundle", "dl-diag"), ("api/export?days=14", "dl-export")):
        tag = re.search(rf'<a [^>]*href="{re.escape(href)}"[^>]*>', page).group(0)
        assert f"return dlClick(event, this, '{box}')" in tag
        assert f'id="{box}"' in page
    js = source(script(), "dlClick")
    assert "ev.preventDefault()" in js and 'fetch(link.getAttribute("href")' in js


@pytest.mark.skipif(NODE is None, reason="node fehlt")
def test_fetched_file_offers_save_share_copy_and_reports_errors(tmp_path):
    js = script()
    names = ("fmt", "esc", "dlClick", "dlFile")
    body = "\n".join(source(js, n) for n in names)
    probe = tmp_path / "probe.js"
    probe.write_text(f"""
const el = {{ textContent: "", innerHTML: "" }};
const $ = () => el;
let DL = null;
const navigator = {{ canShare: () => true }};  // Node hat ein eigenes navigator
globalThis.File = class {{ constructor(parts, name) {{ this.name = name; }} }};
{body}
const out = [];
const ev = {{ preventDefault() {{ out.push("prevented"); }} }};
const link = {{ getAttribute: () => "api/diagnostics/bundle" }};
(async () => {{
  globalThis.fetch = async () => ({{ ok: true, headers: {{ get: () => 'attachment; filename="heatpilot-diagnose-1.json"' }},
                                     blob: async () => ({{ size: 2048, type: "application/json" }}) }});
  await dlClick(ev, link, "dl-diag");
  out.push(el.innerHTML);
  globalThis.fetch = async () => ({{ ok: false, status: 401 }});
  await dlClick(ev, link, "dl-diag");
  out.push(el.textContent);
  console.log(JSON.stringify(out));
}})();
""", encoding="utf-8")
    res = subprocess.run([NODE, str(probe)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    prevented, ok_html, _, err = json.loads(res.stdout)
    assert prevented == "prevented"
    assert "heatpilot-diagnose-1.json" in ok_html and "2 kB" in ok_html
    for word in ("dlSave()", "dlShare()", "dlCopy(this)"):
        assert word in ok_html, word
    assert err.startswith("Fehler 401")


def test_version_and_changelog():
    from heatpilot import __version__
    assert __version__ >= "0.44.1"
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = log.split("\n## 0.44.1\n")[1].split("\n## ")[0]
    assert "401: Unauthorized" in head and "tests/test_ui_v441.py" in head
