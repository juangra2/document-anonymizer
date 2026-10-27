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

# zxing-cpp decodifica QR escaneados que zbar y OpenCV no leen
# (tests/test_qr_escaneado.py).
try:
    import zxingcpp  # type: ignore
    _ZXING_AVAILABLE = True
except ImportError:
    _ZXING_AVAILABLE = False


def _recover_linear_extent(
    image: Image.Image,
    anchor_x: int,
    y0: int,
    y1: int,
) -> tuple[int, int, int, int] | None:
    """Extensión real (x, y, w, h) de un código de barras lineal degenerado.

    zbar decodifica algunos Code128 pero reporta un bbox/polygon colapsado a un
    solo borde vertical (width=0).  Se recupera la extensión con la receta
    clásica de localización: en la banda vertical del ancla, el patrón de
    barras tiene gradiente-x fuerte y gradiente-y débil; el componente conexo
    de esa máscara que contiene el ancla es el código completo.
    """
    gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    b0 = max(0, y0 - 8)
    b1 = min(gray.shape[0], y1 + 8)
    if b1 - b0 < 10:
        return None
    band = gray[b0:b1, :]
    gx = cv2.Sobel(band, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(band, cv2.CV_32F, 0, 1)
    grad = cv2.convertScaleAbs(np.abs(gx) - np.abs(gy))
    _, thr = cv2.threshold(grad, 60, 255, cv2.THRESH_BINARY)
    closed = cv2.morphologyEx(
        thr, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 7))
    )
    n, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    best: tuple[int, int, int, int] | None = None
    for i in range(1, n):
        x, y, w, h, _area = stats[i]
        if w < 30 or h < 0.4 * (y1 - y0):
            continue
        if not (x - 15 <= anchor_x <= x + w + 15):
            continue
        if best is None or w > best[2]:
            best = (x, y + b0, w, h)
    return best


def _pyzbar_pairs(image: Image.Image, padding: int = 8) -> list[tuple[fitz.Rect, str]]:
    """(rect, payload) de pyzbar: QR y códigos de barras lineales decodificados.

    Sanidad mínima (lado corto ≥12 px) en lugar del filtro cuadrado: los
    lineales son anchos por naturaleza y solo se emiten si decodifican.  Un
    bbox degenerado de zbar (width=0 en Code128 reales) se reconstruye desde
    el polygon o, si también está colapsado, con OpenCV desde el ancla.
    """
    if not _PYZBAR_AVAILABLE:
        return []
    out: list[tuple[fitz.Rect, str]] = []
    try:
        for obj in _pyzbar_decode(image):
            r = obj.rect
            x, y, w, h = r.left, r.top, r.width, r.height

            if min(w, h) < 12:
                # 1º intento: bbox del polygon (a veces trae los 4 vértices).
                pts = getattr(obj, "polygon", None) or []
                if pts:
                    xs = [p.x for p in pts]
                    ys = [p.y for p in pts]
                    x, y = min(xs), min(ys)
                    w, h = max(xs) - x, max(ys) - y
                # 2º intento: recuperación OpenCV desde el borde reportado.
                if min(w, h) < 12:
                    recovered = _recover_linear_extent(
                        image, anchor_x=r.left, y0=r.top, y1=r.top + max(r.height, h),
                    )
                    if recovered is None:
                        continue
                    x, y, w, h = recovered
            if min(w, h) < 12:
                continue

            text = obj.data.decode("utf-8", errors="replace")
            out.append((
                fitz.Rect(x - padding, y - padding, x + w + padding, y + h + padding),
                text,
            ))
    except Exception:
        pass
    return out


def _zxing_pairs(image: Image.Image, padding: int = 8) -> list[tuple[fitz.Rect, str]]:
    """(rect, payload) de zxing-cpp.  Solo lo que decodifica."""
    if not _ZXING_AVAILABLE:
        return []
    out: list[tuple[fitz.Rect, str]] = []
    try:
        for r in zxingcpp.read_barcodes(image.convert("RGB")):
            if not r.text:
                continue
            p = r.position
            xs = [p.top_left.x, p.top_right.x, p.bottom_right.x, p.bottom_left.x]
            ys = [p.top_left.y, p.top_right.y, p.bottom_right.y, p.bottom_left.y]
            if min(max(xs) - min(xs), max(ys) - min(ys)) < 12:
                continue
            out.append((
                fitz.Rect(min(xs) - padding, min(ys) - padding,
                          max(xs) + padding, max(ys) + padding),
                r.text,
            ))
    except Exception:
        pass
    return out


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

    # --- Pass 4: pyzbar — QR + códigos de barras lineales (Code128/39/93,
    # EAN/UPC, I25, DATABAR, PDF417…).  Corre SIEMPRE, no solo como fallback:
    # una página puede llevar un QR y un código de barras a la vez.  A los
    # resultados pyzbar no se les exige forma cuadrada (un lineal es ancho por
    # naturaleza y el hecho de decodificar ya descarta el falso positivo); la
    # guardia compartida de payload+tamaño (detect_redactable_codes) aplica igual.
    pairs.extend(_pyzbar_pairs(image, padding))

    # --- Pass 5: zxing-cpp, tambien siempre.  Lo que otro paso ya encontro no
    # se repite: las cajas de dos motores difieren en mas de la tolerancia de
    # abajo, asi que se compara por centro dentro de una caja ya hallada.  Si
    # esa caja la LOCALIZO OpenCV sin decodificarla (texto vacio), zxing le pone
    # el contenido: sin el, el filtro de decodificacion la descartaria.
    for rect, text in _zxing_pairs(image, padding):
        centro = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        previo = next((i for i, (ex, _) in enumerate(pairs) if ex.contains(centro)), None)
        if previo is None:
            pairs.append((rect, text))
        elif not pairs[previo][1].strip():
            pairs[previo] = (pairs[previo][0], text)

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
# Un codigo que SE DESCODIFICA es un codigo, no un sello ni un diagrama: puede
# ocupar mas.  En un carnet o una tarjeta el QR es el 8-15 % de la imagen.  El
# tope de arriba (pensado para un QR en un A4) sigue para lo que no descodifica.
_QR_DECODIFICADO_MAX_AREA_FRAC = 0.35
# Un código de barras lineal (aspect ≥3) es ancho por naturaleza y puede
# superar con facilidad el tope pensado para QR compactos.
_LINEAR_MAX_AREA_FRAC = 0.20


def _area_cap(rect: fitz.Rect, default: float) -> float:
    """Tope de área según la forma: los lineales anchos admiten más superficie."""
    w, h = rect.x1 - rect.x0, rect.y1 - rect.y0
    if min(w, h) <= 0:
        return default
    return max(default, _LINEAR_MAX_AREA_FRAC) if max(w, h) / min(w, h) >= 3.0 else default


def detect_redactable_codes(
    image: Image.Image,
    padding: int = 8,
    max_area_frac: float = _QR_DECODIFICADO_MAX_AREA_FRAC,
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
        if image_area > 0 and area > _area_cap(rect, max_area_frac) * image_area:
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

    image_area = float(image.width * image.height)

    def _within_size(r: fitz.Rect) -> bool:
        if image_area <= 0:
            return True
        return (r.x1 - r.x0) * (r.y1 - r.y0) <= max_area_frac * image_area

    # cv2 QR hits (detect-only) need the square-shape filter; pyzbar hits are
    # decoded (QR + linear barcodes) so only the shape-aware size cap applies.
    kept = [r for r in rects if _is_plausible_qr_rect(r) and _within_size(r)]
    kept.extend(
        r for r, _ in _pyzbar_pairs(image, padding)
        if (r.x1 - r.x0) * (r.y1 - r.y0) <= _area_cap(r, max_area_frac) * image_area
        or image_area <= 0
    )

    return _dedupe_rects(kept)


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