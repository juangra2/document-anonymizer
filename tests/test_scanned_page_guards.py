"""Per-page OCR timeout and image-dominant fast-path guards (scanned path)."""
import io
import time

import fitz
import pytest
from PIL import Image

from app.core.config import settings
from app.services.pdf.scanned_redactor import (
    _is_image_dominant_page,
    _run_with_timeout,
)


def _png(w, h, color="gray"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _page_with_image(img_rect, px=(120, 120), page_size=(595, 842)):
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    page.insert_image(img_rect, stream=_png(*px))
    return doc, page


# --- image-dominant detection ---------------------------------------------

def test_full_page_large_image_is_dominant(monkeypatch):
    monkeypatch.setattr(settings, "ocr_image_dominant_megapixels", 0.0)
    doc, page = _page_with_image(fitz.Rect(0, 0, 595, 842))
    try:
        assert _is_image_dominant_page(page) is True
    finally:
        doc.close()


def test_small_image_not_dominant(monkeypatch):
    monkeypatch.setattr(settings, "ocr_image_dominant_megapixels", 0.0)
    doc, page = _page_with_image(fitz.Rect(40, 40, 200, 160))  # small area
    try:
        assert _is_image_dominant_page(page) is False
    finally:
        doc.close()


def test_low_resolution_image_not_dominant():
    # Full-page but only 0.014 MP → below the default 25 MP floor → not dominant.
    doc, page = _page_with_image(fitz.Rect(0, 0, 595, 842))
    try:
        assert _is_image_dominant_page(page) is False
    finally:
        doc.close()


def test_page_without_images_not_dominant():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "solo texto", fontsize=12)
    try:
        assert _is_image_dominant_page(page) is False
    finally:
        doc.close()


# --- timeout wrapper ------------------------------------------------------

def test_run_with_timeout_returns_result():
    assert _run_with_timeout(lambda: 21 * 2, timeout_s=5) == 42


def test_run_with_timeout_raises_on_slow():
    from concurrent.futures import TimeoutError as FuturesTimeout

    with pytest.raises(FuturesTimeout):
        _run_with_timeout(lambda: time.sleep(3), timeout_s=1)
