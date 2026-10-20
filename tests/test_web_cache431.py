"""0.43.1: Die Oberfläche kommt ohne Zwischenspeicher-Freigabe, damit nach einem Update die neue Seite erscheint."""
import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from heatpilot import __version__, web
from test_adopt421 import make_app
from heatpilot.discovery import Registry

ROOT = Path(__file__).resolve().parents[1]


async def _get(path, headers=None):
    client = TestClient(TestServer(web.make_app(make_app(None, Registry()))))
    await client.start_server()
    try:
        r = await client.get(path, headers=headers or {})
        return r.status, dict(r.headers), await r.read()
    finally:
        await client.close()


def test_index_must_be_revalidated_and_answers_304_when_unchanged():
    status, headers, body = asyncio.run(_get("/"))
    assert status == 200 and headers["Cache-Control"] == "no-cache" and b"<html" in body.lower()
    status2, _, _ = asyncio.run(_get("/", {"If-Modified-Since": headers["Last-Modified"]}))
    assert status2 == 304


def test_version_and_changelog():
    assert __version__ >= "0.43.1"  # seit 0.44 prüft test_ui_v44 die aktuelle Version
    assert f'version: "{__version__}"' in (ROOT / "ems_heatpilot" / "config.yaml").read_text(encoding="utf-8")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = log.split("\n## 0.43.1\n")[1].split("\n## 0.43.0")[0]
    assert log.startswith("# Changelog\n\n## ") and "tests/test_web_cache431.py" in head
