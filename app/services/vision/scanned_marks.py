"""Visual detection of handwritten signatures and stamps in scanned pages.

Operates in the rendered-image pixel space used by the scanned redactor, so the
returned rects can be drawn directly.  Two detectors:

  * detect_signature_rects(image, tokens) — ink blobs near a signature cue
    ("Fdo.", "Firma", "Signatura", "Signat", "Rúbrica"), generalising the old
    label-anchored detector to any cue and to signatures above the cue line.
  * detect_stamp_rects(image) — circular/elliptical seal graphics (e.g. notarial
    stamps whose ID number OCR cannot reliably read).

Both are conservative (anchored / size-bounded / header-excluded) to avoid
redacting logos, photos or diagrams.
"""
from __future__ import annotations

import re
import unicodedata

import cv2
import fitz
import numpy as np
from PIL import Image

from app.services.ocr.models import OcrToken

_HEADER_BAND = 0.12

_CUE_RE = re.compile(r"^(?:fdo|firma|firmado|firmat|signatura|signat|signature|rubrica)$")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().strip())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip(".:")


def _to_bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

def detect_signature_rects(
    image: Image.Image,
    tokens: list[OcrToken],
) -> list[fitz.Rect]:
    """Handwritten-signature ink blobs anchored to a signature cue token."""
    anchors = [t.rect for t in tokens if _CUE_RE.match(_norm(t.text))]
    if not anchors:
        return []

    bgr = _to_bgr(image)
    h, w = bgr.shape[:2]
    header_cut = h * _HEADER_BAND

    rects: list[fitz.Rect] = []
    for a in anchors:
        # Search to the right of and just above the cue (signatures sit beside or
        # above "Fdo./Firma").  Bounded so it can never cover half the page.
        band = (
            int(max(0, a.x0 - w * 0.02)),
            int(max(header_cut, a.y0 - h * 0.10)),
            int(min(w, a.x0 + w * 0.42)),
            int(min(h, a.y1 + h * 0.05)),
        )
        ink = _ink_bbox(bgr, band, max_w=w * 0.35, max_h=h * 0.12)
        if ink is not None:
            rects.append(ink)
    return _dedupe(rects)


def _ink_bbox(
    bgr: np.ndarray,
    band: tuple[int, int, int, int],
    max_w: float,
    max_h: float,
) -> fitz.Rect | None:
    x0, y0, x1, y1 = band
    if x1 - x0 < 12 or y1 - y0 < 8:
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
        if cw > rw * 0.92 and ch <= max(6, rh * 0.30):   # form line / separator
            continue
        if ch > rh * 0.85:
            continue
        comps.append((x, y, cw, ch))

    if len(comps) < 2:    # a single blob is usually printed text or noise
        return None

    min_x = min(c[0] for c in comps)
    min_y = min(c[1] for c in comps)
    max_x = max(c[0] + c[2] for c in comps)
    max_y = max(c[1] + c[3] for c in comps)

    pad = 6
    rect = fitz.Rect(
        x0 + min_x - pad, y0 + min_y - pad,
        x0 + max_x + pad, y0 + max_y + pad,
    )
    if rect.width > max_w:
        rect.x1 = rect.x0 + max_w
    if rect.height > max_h:
        rect.y1 = rect.y0 + max_h
    return rect


# ---------------------------------------------------------------------------
# Stamps / seals
# ---------------------------------------------------------------------------

def detect_stamp_rects(image: Image.Image) -> list[fitz.Rect]:
    """Circular/elliptical stamp graphics (seals), returned as bounding rects.

    Uses Hough circle detection with a plausible seal-radius range and an ink-
    density check inside the circle (a seal carries text/marks, unlike a blank
    circle), then excludes the header logo band.
    """
    bgr = _to_bgr(image)
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)

    min_r = int(min(w, h) * 0.03)
    max_r = int(min(w, h) * 0.18)
    if max_r <= min_r:
        return []

    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=int(min(w, h) * 0.10),
        param1=120, param2=60, minRadius=min_r, maxRadius=max_r,
    )
    if circles is None:
        return []

    header_cut = h * _HEADER_BAND
    rects: list[fitz.Rect] = []
    for cx, cy, r in np.round(circles[0]).astype(int):
        if cy - r < header_cut:        # header logo band
            continue
        if not _has_ink_inside(gray, cx, cy, r):
            continue
        rects.append(fitz.Rect(
            max(0, cx - r), max(0, cy - r),
            min(w, cx + r), min(h, cy + r),
        ))
    return _dedupe(rects)


def _has_ink_inside(gray: np.ndarray, cx: int, cy: int, r: int) -> bool:
    """True when the disc has a moderate amount of dark pixels (text/marks)."""
    h, w = gray.shape[:2]
    x0, y0, x1, y1 = max(0, cx - r), max(0, cy - r), min(w, cx + r), min(h, cy + r)
    patch = gray[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    dark_frac = float(np.mean(patch < 128))
    # A seal has rings + text: enough ink, but not a solid black blob.
    return 0.04 <= dark_frac <= 0.60


def _dedupe(rects: list[fitz.Rect], tol: float = 4.0) -> list[fitz.Rect]:
    out: list[fitz.Rect] = []
    for r in rects:
        if not any(
            abs(r.x0 - e.x0) <= tol and abs(r.y0 - e.y0) <= tol
            and abs(r.x1 - e.x1) <= tol and abs(r.y1 - e.y1) <= tol
            for e in out
        ):
            out.append(r)
    return out
