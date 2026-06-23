"""Visual detection of handwritten signatures in digital PDF pages.

Handwritten signatures are images/vector ink, not text, so the PII analyzer
never sees them.  This module finds them so the redactor can cover them, much
like the QR-code detector.

Strategy (conservative, anchored to a signature cue for precision):
  1. Locate signature cues in the page text ("Fdo.", "Firma", "Signatura",
     "Signat", "Firmado", "Rúbrica").
  2. Primary: embedded raster images near a cue (excluding the header band where
     the institutional logo lives, and full-page backgrounds).
  3. Fallback: when no embedded image sits near a cue, render the band around
     the cue and detect a handwriting-like ink region with OpenCV.

Anchoring to a cue keeps body photos/diagrams and header logos from being
redacted.
"""
from __future__ import annotations

import re
import unicodedata
from io import BytesIO

import cv2
import fitz
import numpy as np
from PIL import Image

from app.services.pdf.extractor import PageText

_RENDER_DPI = 300
_HEADER_BAND = 0.12      # top fraction of the page reserved for the logo
_MAX_AREA_FRAC = 0.45    # images larger than this are backgrounds
_V_BAND = 0.12           # vertical proximity (fraction of page height) to a cue
_H_BAND = 0.30           # horizontal proximity (fraction of page width) to a cue

_CUE_RE = re.compile(r"^(?:fdo|firma|firmado|firmat|signatura|signat|signature|rubrica)$")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().strip())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip(".:")


def _signature_anchors(page_text: PageText) -> list[fitz.Rect]:
    return [w.rect for w in page_text.words if _CUE_RE.match(_norm(w.text))]


def _near_anchor(rect: fitz.Rect, anchors: list[fitz.Rect], w: float, h: float) -> bool:
    vband, hband = h * _V_BAND, w * _H_BAND
    for a in anchors:
        acx, acy = (a.x0 + a.x1) / 2, (a.y0 + a.y1) / 2
        if rect.y0 - vband <= acy <= rect.y1 + vband and \
           rect.x0 - hband <= acx <= rect.x1 + hband:
            return True
    return False


def detect_signature_rects(page: fitz.Page, page_text: PageText) -> list[fitz.Rect]:
    anchors = _signature_anchors(page_text)
    if not anchors:
        return []

    page_rect = page.rect
    w, h = page_rect.width, page_rect.height
    if w <= 0 or h <= 0:
        return []
    header_cut = page_rect.y0 + h * _HEADER_BAND
    page_area = w * h

    rects: list[fitz.Rect] = []

    # --- Primary: embedded raster images near a signature cue ---------------
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            img_rects = page.get_image_rects(xref)
        except Exception:
            continue
        for r in img_rects:
            if r.is_empty or r.width <= 0 or r.height <= 0:
                continue
            if (r.width * r.height) / page_area > _MAX_AREA_FRAC:
                continue          # background / full-page scan
            if r.y1 <= header_cut:
                continue          # header logo band
            if r.width < 12 or r.height < 6:
                continue          # detector noise
            if _near_anchor(r, anchors, w, h):
                rects.append(fitz.Rect(r))

    if rects:
        return _dedupe(rects)

    # --- Fallback: pixel ink detection in TEXT-FREE bands around each cue ----
    # A genuine signature region carries no extractable text; if the band holds
    # printed words they are the analyzer's job, so the band is skipped (this is
    # what keeps printed text near a "Fdo." label from being redacted).
    word_rects = [
        wb.rect for wb in page_text.words if not _CUE_RE.match(_norm(wb.text))
    ]
    image = None
    scale = _RENDER_DPI / 72.0
    for a in anchors:
        band = fitz.Rect(
            a.x0,
            max(page_rect.y0, a.y0 - h * 0.10),
            min(page_rect.x1, a.x0 + w * 0.45),
            min(page_rect.y1, a.y1 + h * 0.04),
        )
        if any((band & wr).get_area() > 0 for wr in word_rects):
            continue
        if image is None:
            image = _render(page)
        ink = _ink_region(image, band, scale)
        if ink is not None:
            rects.append(ink)

    return _dedupe(rects)


def _render(page: fitz.Page) -> Image.Image:
    matrix = fitz.Matrix(_RENDER_DPI / 72.0, _RENDER_DPI / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")


def _ink_region(image: Image.Image, band: fitz.Rect, scale: float) -> fitz.Rect | None:
    """Detect a handwriting-like ink blob inside *band* (PDF coords)."""
    x0, y0 = int(band.x0 * scale), int(band.y0 * scale)
    x1, y1 = int(band.x1 * scale), int(band.y1 * scale)
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    h_img, w_img = bgr.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w_img, x1), min(h_img, y1)
    if x1 - x0 < 10 or y1 - y0 < 8:
        return None

    roi = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    thr = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    num, _, stats, _ = cv2.connectedComponentsWithStats(thr, connectivity=8)
    rh, rw = thr.shape[:2]
    comps = []
    for i in range(1, num):
        x, y, cw, ch, area = stats[i]
        if area < 25 or cw < 8 or ch < 3:
            continue
        # Drop long thin separators (form lines) but keep wide stylised strokes.
        if cw > rw * 0.92 and ch <= max(6, rh * 0.30):
            continue
        if ch > rh * 0.85:
            continue
        comps.append((x, y, cw, ch))

    # A handwritten signature is a few connected strokes: <2 is noise/a printed
    # word, >12 is a block of printed text — neither is a signature.
    if not 2 <= len(comps) <= 12:
        return None

    min_x = min(c[0] for c in comps)
    min_y = min(c[1] for c in comps)
    max_x = max(c[0] + c[2] for c in comps)
    max_y = max(c[1] + c[3] for c in comps)
    pad = 6
    return fitz.Rect(
        x0 / scale + (min_x - pad) / scale,
        y0 / scale + (min_y - pad) / scale,
        x0 / scale + (max_x + pad) / scale,
        y0 / scale + (max_y + pad) / scale,
    )


def _dedupe(rects: list[fitz.Rect], tol: float = 3.0) -> list[fitz.Rect]:
    out: list[fitz.Rect] = []
    for r in rects:
        if not any(
            abs(r.x0 - e.x0) <= tol and abs(r.y0 - e.y0) <= tol
            and abs(r.x1 - e.x1) <= tol and abs(r.y1 - e.y1) <= tol
            for e in out
        ):
            out.append(r)
    return out
