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
    img = _blank()
    d = ImageDraw.Draw(img)
    cx, cy, r = 300, 430, 70
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=4)
    d.ellipse([cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12], outline="black", width=2)
    d.text((cx - 45, cy - 6), "SELLO 0247235145", fill="black")
    rects = detect_stamp_rects(img)
    assert rects, "stamp circle should be detected"
    r0 = rects[0]
    assert r0.x0 <= cx <= r0.x1 and r0.y0 <= cy <= r0.y1


def test_blank_page_no_stamp():
    assert detect_stamp_rects(_blank()) == []
