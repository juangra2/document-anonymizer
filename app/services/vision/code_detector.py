from __future__ import annotations

import cv2
import fitz
import numpy as np
from PIL import Image


def detect_visual_code_rects(
    image: Image.Image,
    padding: int = 8,
) -> list[fitz.Rect]:
    """
    Detecta códigos QR visibles en la imagen.

    Política segura:
    - Si detectamos un QR, se redacta completo.
    - No intentamos interpretar si contiene PII o no.
    """
    cv_image = cv2.cvtColor(
        np.array(image.convert("RGB")),
        cv2.COLOR_RGB2BGR,
    )

    rects: list[fitz.Rect] = []

    detector = cv2.QRCodeDetector()

    try:
        retval, decoded_info, points, _ = detector.detectAndDecodeMulti(cv_image)
    except Exception:
        retval = False
        points = None

    if retval and points is not None:
        for qr_points in points:
            rect = _rect_from_points(qr_points, padding=padding)

            if rect is not None:
                rects.append(rect)

    # Fallback para algunos QR donde Multi falla.
    if not rects:
        try:
            _, single_points = detector.detect(cv_image)
        except Exception:
            single_points = None

        if single_points is not None:
            rect = _rect_from_points(single_points, padding=padding)

            if rect is not None:
                rects.append(rect)

    return _dedupe_rects(rects)


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