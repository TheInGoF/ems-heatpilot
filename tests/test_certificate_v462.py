"""0.46.2: Kein Text des Zertifikats läuft über den Rand – lange Hinweise in den Zeilen werden unter dem Wert
umbrochen, lange Bezeichnungen kleiner gesetzt."""
from heatpilot import certificate, pdf
from test_certificate import NOW, year_engine

MARGIN = 56 - 0.5


def _texts(monkeypatch, data) -> list[tuple[float, float, str]]:
    cls = next(c for c in vars(pdf).values() if isinstance(c, type) and hasattr(c, "paragraph"))
    orig, seen = cls.text, []

    def text(self, x, y, text, size=10.0, bold=False, color="#000000", align="left", angle=0.0):
        w = pdf.text_width(text, size, bold)
        left = x - (w / 2 if align == "center" else w if align == "right" else 0)
        if not angle:
            seen.append((left, left + w, self.w, text))
        return orig(self, x, y, text, size, bold, color, align, angle)

    monkeypatch.setattr(cls, "text", text)
    certificate.render(data)
    return seen


def test_no_text_beyond_the_margins_even_with_long_notes(tmp_path, monkeypatch):
    for kw in ({}, {"days": 40, "area": 0.0, "frost": False}):
        sub = tmp_path / f"h{len(kw)}"
        sub.mkdir()
        data = certificate.collect(year_engine(sub, **kw), NOW)
        data["basis"]["note"] = "Hausmodell aus 7.853 Stunden; nicht berücksichtigt: 312 Stunden ohne Innentemperatur " * 2
        bad = [t for left, right, w, t in _texts(monkeypatch, data) if left < MARGIN or right > w - MARGIN]
        assert bad == []
