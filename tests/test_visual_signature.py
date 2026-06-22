"""Visual handwritten-signature detection (digital path)."""
import io

import fitz
from PIL import Image, ImageDraw

from app.services.pdf.extractor import extract_page_text
from app.services.vision.signature_region import detect_signature_rects


def _ink_png(w=160, h=60):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    # a few cursive-like strokes (multiple components)
    d.line([(5, 40), (40, 10), (70, 45), (100, 15)], fill="black", width=3)
    d.line([(90, 30), (150, 35)], fill="black", width=2)
    d.arc([110, 20, 150, 55], 0, 300, fill="black", width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _detect(page):
    return detect_signature_rects(page, extract_page_text(page, 0))


def test_embedded_signature_near_cue_detected():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((80, 700), "Fdo.: el arquitecto municipal", fontsize=11)
    page.insert_image(fitz.Rect(80, 715, 240, 775), stream=_ink_png())
    rects = _detect(page)
    doc.close()
    assert rects, "signature image near 'Fdo.' must be detected"
    assert any(r.y0 > 700 for r in rects)


def test_header_logo_not_detected():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # logo image in the header band, no signature cue anywhere
    page.insert_image(fitz.Rect(40, 20, 200, 80), stream=_ink_png())
    page.insert_text((80, 400), "Texto del cuerpo del documento", fontsize=11)
    rects = _detect(page)
    doc.close()
    assert rects == [], "header logo without a signature cue must not be redacted"


def test_no_cue_no_detection():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(80, 600, 240, 660), stream=_ink_png())
    page.insert_text((80, 400), "Cuerpo del documento", fontsize=11)
    rects = _detect(page)
    doc.close()
    assert rects == [], "image with no nearby signature cue must not be redacted"
