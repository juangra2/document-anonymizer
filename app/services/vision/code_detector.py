from __future__ import annotations

import cv2
import fitz
import numpy as np
from PIL import Image

# pyzbar provides higher recall on low-contrast or small QR codes.
# Optional dependency — silently disabled when not installed.
# Install with: pip install pyzbar
try:
    from pyzbar.pyzbar import decode as _pyzbar_decode  # type: ignore
    _PYZBAR_AVAILABLE = True
except ImportError:
    _PYZBAR_AVAILABLE = False


def detect_and_decode_visual_codes(
    image: Image.Image,
    padding: int = 8,
) -> list[tuple[fitz.Rect, str]]:
    """Detect QR codes / barcodes and return (rect, decoded_text) pairs.

    Same detection cascade as detect_visual_code_rects but also captures the
    decoded content so callers can report it as a QR_CODE entity.
    """
    cv_image = cv2.cvtColor(
        np.array(image.convert("RGB")),
        cv2.COLOR_RGB2BGR,
    )

    pairs: list[tuple[fitz.Rect, str]] = []

    # --- Pass 1: QRCodeDetectorAruco ---
    try:
        aruco_detector = cv2.QRCodeDetectorAruco()
        retval, decoded_info, points, _ = aruco_detector.detectAndDecodeMulti(cv_image)
        if retval and points is not None:
            for text, qr_points in zip(decoded_info or [], points):
                rect = _rect_from_points(qr_points, padding=padding)
                if rect is not None and _is_plausible_qr_rect(rect):
                    pairs.append((rect, text or ""))
    except AttributeError:
        pass

    # --- Pass 2: classic QRCodeDetector multi ---
    if not pairs:
        detector = cv2.QRCodeDetector()
        try:
            retval, decoded_info, points, _ = detector.detectAndDecodeMulti(cv_image)
        except Exception:
            retval = False
            points = None
            decoded_info = []
        if retval and points is not None:
            for text, qr_points in zip(decoded_info or [], points):
                rect = _rect_from_points(qr_points, padding=padding)
                if rect is not None and _is_plausible_qr_rect(rect):
                    pairs.append((rect, text or ""))

        # --- Pass 3: single-QR fallback ---
        if not pairs:
            try:
                decoded, single_points, _ = detector.detectAndDecode(cv_image)
            except Exception:
                decoded = ""
                single_points = None
            if single_points is not None:
                rect = _rect_from_points(single_points, padding=padding)
                if rect is not None and _is_plausible_qr_rect(rect):
                    pairs.append((rect, decoded or ""))

    # --- Pass 4: pyzbar ---
    if not pairs and _PYZBAR_AVAILABLE:
        try:
            for obj in _pyzbar_decode(image):
                r = obj.rect
                rect = fitz.Rect(
                    r.left - padding,
                    r.top - padding,
                    r.left + r.width + padding,
                    r.top + r.height + padding,
                )
                if _is_plausible_qr_rect(rect):
                    text = obj.data.decode("utf-8", errors="replace")
                    pairs.append((rect, text))
        except Exception:
            pass

    # Deduplicate by rect proximity
    tolerance = 4.0
    unique: list[tuple[fitz.Rect, str]] = []
    for rect, text in pairs:
        if not any(
            abs(rect.x0 - ex.x0) <= tolerance
            and abs(rect.y0 - ex.y0) <= tolerance
            and abs(rect.x1 - ex.x1) <= tolerance
            and abs(rect.y1 - ex.y1) <= tolerance
            for ex, _ in unique
        ):
            unique.append((rect, text))
    return unique


# A real QR/barcode occupies a small fraction of a document page.  Detections
# larger than this are almost always false positives on diagrams/seals/figures.
_QR_MAX_AREA_FRAC = 0.05


def detect_redactable_codes(
    image: Image.Image,
    padding: int = 8,
    max_area_frac: float = _QR_MAX_AREA_FRAC,
) -> list[tuple[fitz.Rect, str]]:
    """Codes safe to redact: those that decode to a non-empty payload and are
    not implausibly large.  Shared by the digital and OCR redaction paths so the
    same guard (a seal/figure that doesn't decode is not painted black) applies
    everywhere.
    """
    image_area = float(image.width * image.height)
    out: list[tuple[fitz.Rect, str]] = []
    for rect, text in detect_and_decode_visual_codes(image, padding=padding):
        if not text.strip():
            continue
        area = (rect.x1 - rect.x0) * (rect.y1 - rect.y0)
        if image_area > 0 and area > max_area_frac * image_area:
            continue
        out.append((rect, text))
    return out


def detect_visual_code_rects(
    image: Image.Image,
    padding: int = 8,
    max_area_frac: float = _QR_MAX_AREA_FRAC,
) -> list[fitz.Rect]:
    """Detect QR codes / barcodes in *image* and return their bounding rects.

    Used by the image-dominant fast path, which cannot decode payloads; the
    *max_area_frac* size cap is the minimum guard against redacting a large
    diagram/seal misread as a code.
    Detection cascade (first match wins, then deduplication):
      1. cv2.QRCodeDetectorAruco (newer OpenCV, highest recall on modern QR)
      2. cv2.QRCodeDetector.detectAndDecodeMulti  (single-pass multi-QR)
      3. cv2.QRCodeDetector.detect  (single-QR fallback)
      4. pyzbar  (optional; handles 1-D barcodes and many QR variants)
    """
    cv_image = cv2.cvtColor(
        np.array(image.convert("RGB")),
        cv2.COLOR_RGB2BGR,
    )

    rects: list[fitz.Rect] = []

    # --- Pass 1: QRCodeDetectorAruco (OpenCV ≥ 4.8) ---
    try:
        aruco_detector = cv2.QRCodeDetectorAruco()
        retval, _, points, _ = aruco_detector.detectAndDecodeMulti(cv_image)
        if retval and points is not None:
            for qr_points in points:
                rect = _rect_from_points(qr_points, padding=padding)
                if rect is not None:
                    rects.append(rect)
    except AttributeError:
        pass  # QRCodeDetectorAruco not available in this OpenCV build

    # --- Pass 2: classic QRCodeDetector multi ---
    if not rects:
        detector = cv2.QRCodeDetector()
        try:
            retval, _, points, _ = detector.detectAndDecodeMulti(cv_image)
        except Exception:
            retval = False
            points = None

        if retval and points is not None:
            for qr_points in points:
                rect = _rect_from_points(qr_points, padding=padding)
                if rect is not None:
                    rects.append(rect)

        # --- Pass 3: single-QR fallback ---
        if not rects:
            try:
                _, single_points = detector.detect(cv_image)
            except Exception:
                single_points = None

            if single_points is not None:
                rect = _rect_from_points(single_points, padding=padding)
                if rect is not None:
                    rects.append(rect)

    # --- Pass 4: pyzbar (optional) ---
    if not rects and _PYZBAR_AVAILABLE:
        try:
            for obj in _pyzbar_decode(image):
                r = obj.rect
                rects.append(fitz.Rect(
                    r.left - padding,
                    r.top - padding,
                    r.left + r.width + padding,
                    r.top + r.height + padding,
                ))
        except Exception:
            pass

    image_area = float(image.width * image.height)

    def _within_size(r: fitz.Rect) -> bool:
        if image_area <= 0:
            return True
        return (r.x1 - r.x0) * (r.y1 - r.y0) <= max_area_frac * image_area

    return _dedupe_rects(
        [r for r in rects if _is_plausible_qr_rect(r) and _within_size(r)]
    )


def _is_plausible_qr_rect(rect: fitz.Rect) -> bool:
    """Return True when *rect* could plausibly be a QR code.

    Sanity checks applied after the detector returns a bounding box:

    Aspect ratio
        QR codes are always (nearly) square — even when skewed by perspective,
        their axis-aligned bounding box remains approximately square.
        Threshold: shorter side ≥ 80 % of longer side.
        The false-positive case (text column, dense grid) produces significantly
        non-square boxes (e.g. 381 × 485 px → ratio 0.786).

    Minimum size
        A decodable QR code requires at least ~20 × 20 pixels at detection DPI.
        Anything smaller is almost certainly detector noise.
    """
    w = rect.x1 - rect.x0
    h = rect.y1 - rect.y0
    if w <= 0 or h <= 0:
        return False
    if min(w, h) < 20:
        return False
    aspect = min(w, h) / max(w, h)
    return aspect >= 0.80


def _rect_from_points(
    points,
    padding: int,
) -> fitz.Rect | None:
    arr = np.array(points, dtype=float).reshape(-1, 2)

    if arr.size == 0:
        return None

    x0 = float(np.min(arr[:, 0])) - padding
    y0 = float(np.min(arr[:, 1])) - padding
    x1 = float(np.max(arr[:, 0])) + padding
    y1 = float(np.max(arr[:, 1])) + padding

    if x1 <= x0 or y1 <= y0:
        return None

    return fitz.Rect(x0, y0, x1, y1)


def _dedupe_rects(
    rects: list[fitz.Rect],
    tolerance: float = 4.0,
) -> list[fitz.Rect]:
    unique: list[fitz.Rect] = []

    for rect in rects:
        duplicate = False

        for existing in unique:
            if (
                abs(rect.x0 - existing.x0) <= tolerance
                and abs(rect.y0 - existing.y0) <= tolerance
                and abs(rect.x1 - existing.x1) <= tolerance
                and abs(rect.y1 - existing.y1) <= tolerance
            ):
                duplicate = True
                break

        if not duplicate:
            unique.append(rect)

    return unique