"""Kleiner PDF-Schreiber ohne Abhängigkeiten: A4-Seiten, Helvetica, Linien, Flächen, Kurven.

Koordinaten wie auf dem Bildschirm: (0, 0) ist oben links, y wächst nach unten,
Einheit Punkt (1/72 Zoll). Text wird in Windows-1252 kodiert – das deckt Umlaute,
ß, °, €, „“ und – ab.
"""

from __future__ import annotations

import math
import zlib
from datetime import datetime

A4 = (595.28, 841.89)

# Zeichenbreiten der Standardschriften (Adobe AFM, 1/1000 em) für ASCII 32–126
_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
        1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
        333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
        556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
         975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
         333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
         611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
_EXTRA = {  # Zeichen außerhalb ASCII: (normal, fett)
    "ä": (556, 556), "ö": (556, 611), "ü": (556, 611), "Ä": (667, 722), "Ö": (778, 778),
    "Ü": (722, 722), "ß": (611, 611), "°": (400, 400), "€": (556, 556), "–": (556, 556),
    "—": (1000, 1000), "„": (333, 500), "“": (333, 500), "”": (333, 500), "·": (278, 278),
    "×": (584, 584), "²": (333, 333), "³": (333, 333), "…": (1000, 1000), "é": (556, 556),
}
_REPLACE = {"−": "–", "→": "->", "≈": "ca.", " ": " ", " ": " "}


def clean(text: str) -> str:
    for a, b in _REPLACE.items():
        text = text.replace(a, b)
    return text


def text_width(text: str, size: float, bold: bool = False) -> float:
    table = _BOLD if bold else _REG
    total = 0
    for ch in clean(text):
        o = ord(ch)
        if 32 <= o <= 126:
            total += table[o - 32]
        else:
            total += _EXTRA.get(ch, (556, 556))[1 if bold else 0]
    return total * size / 1000


def wrap(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for para in clean(text).split("\n"):
        line = ""
        for word in para.split(" "):
            test = f"{line} {word}" if line else word
            if line and text_width(test, size, bold) > width:
                lines.append(line)
                line = word
            else:
                line = test
        lines.append(line)
    return lines


def _str(text: str) -> bytes:
    raw = clean(text).encode("cp1252", "replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _n(v: float) -> str:
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        v = 0.0  # NaN/unendlich wären ungültige PDF-Befehle – manche Programme zeigen dann gar nichts
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def hex_rgb(color: str | tuple) -> tuple[float, float, float]:
    if isinstance(color, tuple):
        return color
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


class Page:
    def __init__(self, size: tuple[float, float] = A4) -> None:
        self.w, self.h = size
        self.ops: list[bytes] = []

    def _y(self, y: float) -> float:
        return self.h - y

    def _op(self, *parts) -> None:
        self.ops.append(b" ".join(p if isinstance(p, bytes) else str(p).encode() for p in parts))

    def _paint(self, fill, stroke, lw: float, close: bool = True) -> None:
        if stroke is not None:
            self._op(*map(_n, hex_rgb(stroke)), "RG", _n(lw), "w")
        if fill is not None:
            self._op(*map(_n, hex_rgb(fill)), "rg")
        if fill is not None and stroke is not None:
            self._op("b" if close else "B")
        elif fill is not None:
            self._op("f")
        elif stroke is not None:
            self._op("s" if close else "S")
        else:
            self._op("n")

    # ------------------------------------------------------------------ Formen
    def line(self, x1, y1, x2, y2, color="#000000", lw=1.0, dash: tuple | None = None) -> None:
        self._op("q")
        if dash:
            self._op("[" + " ".join(_n(d) for d in dash) + "]", "0", "d")
        self._op("1 J")
        self._op(_n(x1), _n(self._y(y1)), "m", _n(x2), _n(self._y(y2)), "l")
        self._paint(None, color, lw, close=False)
        self._op("Q")

    def polyline(self, points, color="#000000", lw=1.0, closed=False) -> None:
        self._op("q 1 J 1 j")
        x, y = points[0]
        self._op(_n(x), _n(self._y(y)), "m")
        for x, y in points[1:]:
            self._op(_n(x), _n(self._y(y)), "l")
        if closed:
            self._op("h")
        self._paint(None, color, lw, close=False)
        self._op("Q")

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=1.0, radius=0.0) -> None:
        self._op("q")
        self._rect_path(x, y, w, h, radius)
        self._paint(fill, stroke, lw)
        self._op("Q")

    def _rect_path(self, x, y, w, h, r) -> None:
        if r <= 0:
            self._op(_n(x), _n(self._y(y + h)), _n(w), _n(h), "re")
            return
        k = 0.5523 * r
        y0, y1 = self._y(y), self._y(y + h)
        self._op(_n(x + r), _n(y0), "m", _n(x + w - r), _n(y0), "l")
        self._op(_n(x + w - r + k), _n(y0), _n(x + w), _n(y0 - r + k), _n(x + w), _n(y0 - r), "c")
        self._op(_n(x + w), _n(y1 + r), "l")
        self._op(_n(x + w), _n(y1 + r - k), _n(x + w - r + k), _n(y1), _n(x + w - r), _n(y1), "c")
        self._op(_n(x + r), _n(y1), "l")
        self._op(_n(x + r - k), _n(y1), _n(x), _n(y1 + r - k), _n(x), _n(y1 + r), "c")
        self._op(_n(x), _n(y0 - r), "l")
        self._op(_n(x), _n(y0 - r + k), _n(x + r - k), _n(y0), _n(x + r), _n(y0), "c")
        self._op("h")

    def circle(self, cx, cy, r, fill=None, stroke=None, lw=1.0) -> None:
        k = 0.5523 * r
        y = self._y(cy)
        self._op("q")
        self._op(_n(cx + r), _n(y), "m")
        self._op(_n(cx + r), _n(y + k), _n(cx + k), _n(y + r), _n(cx), _n(y + r), "c")
        self._op(_n(cx - k), _n(y + r), _n(cx - r), _n(y + k), _n(cx - r), _n(y), "c")
        self._op(_n(cx - r), _n(y - k), _n(cx - k), _n(y - r), _n(cx), _n(y - r), "c")
        self._op(_n(cx + k), _n(y - r), _n(cx + r), _n(y - k), _n(cx + r), _n(y), "c")
        self._paint(fill, stroke, lw)
        self._op("Q")

    def polygon(self, points, fill=None, stroke=None, lw=1.0) -> None:
        self._op("q 1 j")
        x, y = points[0]
        self._op(_n(x), _n(self._y(y)), "m")
        for x, y in points[1:]:
            self._op(_n(x), _n(self._y(y)), "l")
        self._op("h")
        self._paint(fill, stroke, lw)
        self._op("Q")

    def path(self, segments, fill=None, stroke=None, lw=1.0, clip_gradient: tuple | None = None,
             transform: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> None:
        """Pfad aus ("M", x, y), ("L", x, y), ("C", x1, y1, x2, y2, x, y), ("Z",).

        ``transform`` = (dx, dy, Maßstab). ``clip_gradient`` = (Farbe unten, Mitte, oben):
        Fläche als senkrechter Verlauf (in Streifen) statt einfarbig.
        """
        dx, dy, s = transform

        def pt(x, y):
            return _n(dx + x * s), _n(self._y(dy + y * s))

        def emit():
            for seg in segments:
                if seg[0] == "M":
                    self._op(*pt(*seg[1:3]), "m")
                elif seg[0] == "L":
                    self._op(*pt(*seg[1:3]), "l")
                elif seg[0] == "C":
                    self._op(*pt(*seg[1:3]), *pt(*seg[3:5]), *pt(*seg[5:7]), "c")
                elif seg[0] == "Z":
                    self._op("h")

        self._op("q")
        emit()
        if clip_gradient:
            ys = [v for seg in segments for v in seg[2::2] if isinstance(v, (int, float))]
            top, bottom = dy + min(ys) * s, dy + max(ys) * s
            xs = [v for seg in segments for v in seg[1::2] if isinstance(v, (int, float))]
            left, right = dx + min(xs) * s, dx + max(xs) * s
            self._op("W n")
            stops = [hex_rgb(c) for c in clip_gradient]
            n = 24
            band = (bottom - top) / n
            for i in range(n):  # von unten nach oben
                t = i / (n - 1)
                if t < 0.55:
                    a, b, f = stops[0], stops[1], t / 0.55
                else:
                    a, b, f = stops[1], stops[2], (t - 0.55) / 0.45
                col = tuple(a[j] + (b[j] - a[j]) * f for j in range(3))
                self._op(*map(_n, col), "rg")
                self._op(_n(left - 1), _n(self._y(bottom - i * band) - 0.6),
                         _n(right - left + 2), _n(band + 1.2), "re f")
        else:
            self._paint(fill, stroke, lw)
        self._op("Q")

    # ------------------------------------------------------------------ Text
    def text(self, x, y, text, size=10.0, bold=False, color="#000000", align="left", angle=0.0) -> float:
        """Grundlinie bei y. Gibt die Textbreite zurück."""
        w = text_width(text, size, bold)
        if align == "center":
            x -= w / 2 * math.cos(math.radians(angle))
            y += w / 2 * math.sin(math.radians(angle))
        elif align == "right":
            x -= w
        a = math.radians(angle)
        c, s = math.cos(a), math.sin(a)
        self._op("BT", "/F2" if bold else "/F1", _n(size), "Tf", *map(_n, hex_rgb(color)), "rg",
                 _n(c), _n(s), _n(-s), _n(c), _n(x), _n(self._y(y)), "Tm", _str(text), "Tj", "ET")
        return w

    def paragraph(self, x, y, text, width, size=10.0, bold=False, color="#000000", leading=None) -> float:
        """Umbrochener Text ab Grundlinie y. Gibt die y-Position nach der letzten Zeile zurück."""
        leading = leading or size * 1.3
        for line in wrap(text, width, size, bold):
            self.text(x, y, line, size, bold, color)
            y += leading
        return y


def _pdf_text(text: str) -> bytes:
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


def document(pages: list[Page], title: str = "", author: str = "") -> bytes:
    """Seiten zu einer PDF-Datei zusammensetzen."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog = add(b"")
    pages_id = add(b"")
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for page in pages:
        data = zlib.compress(b"\n".join(page.ops))
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
            % (pages_id, _n(page.w).encode(), _n(page.h).encode(), content, f1, f2)
        ))
    objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    objs[pages_id - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                          + b"] /Count %d >>" % len(kids))
    stamp = datetime.now().astimezone()
    tz = stamp.strftime("%z")
    created = f"D:{stamp:%Y%m%d%H%M%S}{tz[:3]}'{tz[3:]}'".encode()
    info = add(b"<< /Title " + _pdf_text(title) + b" /Author " + _pdf_text(author)
               + b" /Producer " + _pdf_text("EMS HeatPilot") + b" /CreationDate (" + created + b") >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, catalog, info, xref))
    return bytes(out)
