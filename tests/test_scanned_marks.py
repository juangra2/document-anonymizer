"""Scanned-path visual detection: handwritten signatures and stamps."""
import fitz
from PIL import Image, ImageDraw

from app.services.ocr.models import OcrToken
from app.services.vision.scanned_marks import (
    detect_signature_rects,
    detect_stamp_rects,
)


def _blank(w=600, h=800):
    return Image.new("RGB", (w, h), "white")


def _cue(text, x0, y0):
    return OcrToken(text=text, confidence=0.9, rect=fitz.Rect(x0, y0, x0 + 40, y0 + 15))


def _draw_ink(img, cx, cy):
    d = ImageDraw.Draw(img)
    d.line([(cx, cy), (cx + 35, cy - 25), (cx + 70, cy + 10)], fill="black", width=3)
    d.line([(cx + 60, cy - 5), (cx + 120, cy + 5)], fill="black", width=2)
    d.arc([cx + 90, cy - 20, cx + 140, cy + 25], 0, 300, fill="black", width=2)


# --- signatures -----------------------------------------------------------

def test_signature_detected_near_fdo_cue():
    img = _blank()
    _draw_ink(img, 170, 395)
    tokens = [_cue("Fdo.", 100, 400)]
    rects = detect_signature_rects(img, tokens)
    assert rects, "ink near 'Fdo.' must be detected as a signature"


def test_no_signature_without_cue():
    img = _blank()
    _draw_ink(img, 170, 395)
    assert detect_signature_rects(img, tokens=[]) == []


def test_signature_cue_in_header_excluded():
    img = _blank()
    _draw_ink(img, 170, 40)
    tokens = [_cue("Firma", 100, 30)]   # header band
    assert detect_signature_rects(img, tokens) == []


# --- stamps ---------------------------------------------------------------

def test_stamp_circle_detected():
    # A realistic seal: thick concentric rings plus several text lines / marks
    # inside (the tightened detector requires this internal structure).
    img = _blank()
    d = ImageDraw.Draw(img)
    cx, cy, r = 300, 430, 70
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=8)
    d.ellipse([cx - r + 10, cy - r + 10, cx + r - 10, cy + r - 10], outline="black", width=4)
    d.text((cx - 55, cy - 30), "AYUNTAMIENTO DE", fill="black")
    d.text((cx - 35, cy - 12), "VALENCIA", fill="black")
    d.text((cx - 50, cy + 6), "SELLO 0247235145", fill="black")
    d.text((cx - 30, cy + 24), "REGISTRO", fill="black")
    rects = detect_stamp_rects(img)
    assert rects, "stamp circle should be detected"
    r0 = rects[0]
    assert r0.x0 <= cx <= r0.x1 and r0.y0 <= cy <= r0.y1


def test_blank_page_no_stamp():
    assert detect_stamp_rects(_blank()) == []


# --- default-off gating at the segmenter level ----------------------------

def test_visual_detectors_off_by_default():
    from app.core.config import settings
    assert settings.detect_visual_signatures is False
    assert settings.detect_visual_stamps is False


def test_segmenter_skips_visual_marks_when_disabled(monkeypatch):
    import fitz as _fitz
    from app.core.config import settings
    from app.services.layout.models import ZoneType
    from app.services.layout.segmenters import OcrPageSegmenter
    from app.services.ocr.models import OcrPage, OcrToken

    img = _blank()
    _draw_ink(img, 170, 395)
    page = OcrPage(0, 600, 800, [
        OcrToken("Firma", 0.9, _fitz.Rect(100, 400, 140, 415)),
        OcrToken("cuerpo", 0.9, _fitz.Rect(100, 200, 200, 215)),
    ])

    monkeypatch.setattr(settings, "detect_visual_signatures", False)
    monkeypatch.setattr(settings, "detect_visual_stamps", False)
    zoned = OcrPageSegmenter().segment(page, image=img)
    assert not any(z.zone_type == ZoneType.SIGNATURE for z in zoned.zones)

    monkeypatch.setattr(settings, "detect_visual_signatures", True)
    zoned_on = OcrPageSegmenter().segment(page, image=img)
    assert any(z.zone_type == ZoneType.SIGNATURE for z in zoned_on.zones)
