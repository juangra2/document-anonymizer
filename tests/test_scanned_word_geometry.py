"""Scanned redaction geometry: word-level boxes + A1 layout-text detection."""
import fitz

from app.services.layout.models import PageZone, ZoneToken, ZoneType
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.ocr.models import OcrPage, OcrToken
from app.services.pii.models import PiiEntity
from app.services.pdf.scanned_redactor import (
    _collect_redaction_rects,
    _entity_rects_via_easy,
)


def _tok(text, x0, y0, x1, y1, hint=None):
    return OcrToken(text=text, confidence=1.0, rect=fitz.Rect(x0, y0, x1, y1), zone_hint=hint)


# --- EasyOCR-only fallback path: a PII span maps to its own line -----------

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
    for r in rects:
        assert (r.y1 - r.y0) <= 35, f"redaction spans multiple lines: {r}"
    assert any(128 <= (r.y0 + r.y1) / 2 <= 152 for r in rects), "DNI line not covered"


# --- A1: detect on clean (MinerU) text, map to word-level OCR boxes --------

def _zone(text, rect):
    zt = ZoneToken(text=text, rect=fitz.Rect(*rect), text_offset=0)
    return PageZone(zone_type=ZoneType.TABLE, rect=fitz.Rect(*rect), text=text,
                    tokens=[zt], source="ocr")


def test_iban_maps_to_fragmented_word_boxes():
    # MinerU keeps the IBAN contiguous in one block; EasyOCR fragments it across
    # words and a colliding reference shares short digit groups.
    text = "IBAN ES18 0049 5114 9121 1615 8451"
    zone = _zone(text, (50, 100, 400, 120))
    ent = PiiEntity("IBAN_CODE", text.index("ES18"), len(text), 0.9)
    easy = [
        _tok("ES18", 90, 102, 120, 118),
        _tok("0049 5114 9121", 125, 102, 230, 118),
        _tok("1615 8451", 235, 102, 300, 118),
        _tok("0049 5114 632 BBGRX", 60, 300, 200, 316),   # reference, other line
    ]
    rects = _entity_rects_via_easy(ent, zone, easy)
    # Three tight IBAN word boxes, all on the IBAN line; reference not included.
    assert len(rects) == 3
    assert all(r.y0 < 130 for r in rects)


def test_csv_value_maps_to_single_word_box():
    text = "(*) C.S.V.: 12806538135277910"
    zone = _zone(text, (50, 600, 400, 620))
    v = "12806538135277910"
    ent = PiiEntity("CSV", text.index(v), text.index(v) + len(v), 0.9)
    easy = [
        _tok("C.S.V", 60, 602, 110, 618),
        _tok("12806538135277910", 120, 602, 320, 618),
        _tok("Servicio", 60, 640, 130, 656),
    ]
    rects = _entity_rects_via_easy(ent, zone, easy)
    assert len(rects) == 1
    assert rects[0].x0 <= 122 and rects[0].x1 >= 318


def _body_zone(text, rect):
    zt = ZoneToken(text=text, rect=fitz.Rect(*rect), text_offset=0)
    return PageZone(zone_type=ZoneType.BODY, rect=fitz.Rect(*rect), text=text,
                    tokens=[zt], source="ocr")


def test_large_block_incomplete_coverage_not_fully_painted():
    # Tall multi-line block (e.g. an NER false positive on a paragraph) with
    # only partial word coverage → must NOT paint the whole block.
    text = "frase larga que el sistema marca como persona por error en el bloque"
    zone = _body_zone(text, (50, 200, 1000, 380))   # 180 px tall
    ent = PiiEntity("PERSON", 0, len(text), 0.9)
    easy = [_tok("frase", 55, 205, 95, 223), _tok("larga", 100, 205, 150, 223)]
    rects = _entity_rects_via_easy(ent, zone, easy, line_h=20.0)
    for r in rects:
        assert (r.y1 - r.y0) <= 30, "must not paint the multi-line block"


def test_no_match_large_region_skipped():
    zone = _body_zone("texto", (50, 200, 1000, 380))
    ent = PiiEntity("PERSON", 0, 5, 0.9)
    easy = [_tok("otracosa", 55, 205, 120, 223)]   # no shared token
    assert _entity_rects_via_easy(ent, zone, easy, line_h=20.0) == []


def test_incomplete_word_coverage_falls_back_to_region():
    # Entity tokens partly OCR-misread in the word layer → cannot cover all
    # tokens → redact the whole region rather than leak the misread part.
    text = "Dulce María Calvo"
    zone = _zone(text, (50, 200, 300, 218))
    ent = PiiEntity("PERSON", 0, len(text), 0.9)
    easy = [
        _tok("Dulce", 55, 202, 95, 216),
        _tok("Maríe", 100, 202, 140, 216),   # misread
        _tok("Calvo", 145, 202, 190, 216),
    ]
    rects = _entity_rects_via_easy(ent, zone, easy)
    assert len(rects) == 1                    # the region (safe fallback)
    assert rects[0].x0 <= 52 and rects[0].x1 >= 298
