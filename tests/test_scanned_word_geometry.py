"""Word-level geometry for scanned redaction (alignment + MinerU zone hints)."""
import fitz

from app.services.layout.segmenters import OcrPageSegmenter
from app.services.ocr.models import OcrPage, OcrToken
from app.services.pdf.scanned_redactor import (
    _assign_hints_from_layout,
    _collect_redaction_rects,
)


def _tok(text, x0, y0, x1, y1, hint=None):
    return OcrToken(text=text, confidence=1.0, rect=fitz.Rect(x0, y0, x1, y1), zone_hint=hint)


# --- MinerU-zone hint assignment onto word tokens -------------------------

def test_hints_assigned_by_containment():
    words = OcrPage(0, 500, 700, [
        _tok("Cabecera", 20, 10, 120, 30),
        _tok("cuerpo", 20, 300, 120, 320),
        _tok("pie", 20, 660, 120, 680),
    ])
    layout = OcrPage(0, 500, 700, [
        _tok("h", 0, 0, 500, 80, hint="header"),
        _tok("b", 0, 80, 500, 620, hint="body"),
        _tok("f", 0, 620, 500, 700, hint="footer"),
    ])
    _assign_hints_from_layout(words, layout)
    assert [t.zone_hint for t in words.tokens] == ["header", "body", "footer"]


# --- alignment: a PII span maps to its own line, not the whole block -------

def test_entity_rect_is_line_aligned_not_block_height():
    # Two separate line tokens; the DNI is on the second line.  Each redaction
    # must stay within a single line's height — never span both lines, which was
    # the old block-level mapping bug (full-paragraph-height bars).
    line1 = _tok("Resumen general del documento adjunto", 50, 100, 450, 120)
    line2 = _tok("DNI 12345678Z", 50, 130, 200, 150)
    page = OcrPage(0, 600, 800, [line1, line2])

    from app.services.pii.analyzer import PiiAnalyzer
    zoned = OcrPageSegmenter().segment(page)
    rects, ents = _collect_redaction_rects(zoned, PiiAnalyzer(), mode="strict")

    assert rects, "DNI should be redacted"
    # No box spans both lines (the block-height bug would give height ~50+).
    for r in rects:
        assert (r.y1 - r.y0) <= 35, f"redaction spans multiple lines: {r}"
    # The DNI is redacted on its own line.
    assert any(128 <= (r.y0 + r.y1) / 2 <= 152 for r in rects), "DNI line not covered"
