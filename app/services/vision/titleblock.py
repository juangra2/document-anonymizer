"""Localización del cajetín (title block) en páginas de plano de obra.

El PII de un plano vive en el cajetín — el bloque de metadatos del margen con
promotor, consultora, arquitectos y nº de colegiado, situación, escala, nº de
encargo.  Este módulo lo localiza sin coordenadas hardcodeadas; el cuerpo del
dibujo nunca se procesa.

Dos rutas, ambas validadas por keywords (nunca se acepta un candidato sin
evidencia textual de cajetín):

1. Capa de texto (planos CAD digitales): se agrupan las cajas de palabra que
   contienen keywords de cajetín y se toma el clúster dominante.  Robusto ante
   rotación de página: las palabras de ``get_text`` llegan en espacio SIN rotar
   (mediabox) y se transforman con ``page.rotation_matrix`` al espacio de vista
   (``page.rect``), que es el que espera ``get_pixmap(clip=…)``.

2. Visión (planos escaneados, sin capa de texto): render a DPI reducido →
   detección morfológica de rejilla (líneas H/V largas → clústeres) → candidatos
   por densidad de celdas, más dos candidatos de franja de borde (derecha e
   inferior, prior típico del cajetín).  Cada candidato se re-rasteriza SOLO su
   clip a DPI alto y se valida con EasyOCR: se exige ≥2 keywords.

Valores calibrados con TOMO_04 (plano GRUPOTEC real, A1 apaisado rotado 90°,
cajetín = franja derecha con 11/12 keywords):
  - DPI de localización: 120 (11 MP para A1; suficiente para líneas de rejilla)
  - DPI de validación/OCR del clip: 300
  - Rejilla: kernel líneas = dim/60, dilate 9px, candidatos 0.5–30% de página
  - Franjas de borde: 12% del ancho (derecha) / alto (inferior)
  - Validación: ≥2 keywords distintas en el OCR del clip
  - Clúster texto: ≥3 keywords distintas, radio 25% de la diagonal
"""
from __future__ import annotations

import logging
import unicodedata
from io import BytesIO
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image
from app.services.storage.temp_storage import scratch_dir

logger = logging.getLogger(__name__)

TITLEBLOCK_KEYWORDS = (
    "ESCALA", "PROMOTOR", "ARQUITECT", "COLEGIADO", "PLANO", "FECHA",
    "SITUACION", "EXPEDIENTE", "CONSULTOR", "PROYECTO", "ENCARGO",
    "DENOMINACION", "INGENIER",
)

_LOCATE_DPI = 120
_VALIDATE_DPI = 300
_MIN_TEXT_KEYWORDS = 3     # ruta texto: keywords distintas en el clúster
_MIN_OCR_KEYWORDS = 2      # ruta visión: keywords distintas en el OCR del clip
# Franjas candidatas de borde derecho/inferior.  18%: el cajetín real de
# TOMO_04 ocupa el 13.8% del ancho y las etiquetas empiezan en su borde
# interior; una franja más estrecha recorta el inicio de las keywords.
_EDGE_STRIP_FRAC = 0.18
_MAX_AREA_FRAC = 0.30      # un cajetín nunca supera el 30% de la página


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.upper())
    return "".join(c for c in text if not unicodedata.combining(c))


def _keywords_in(text: str) -> set[str]:
    n = _norm(text)
    return {k for k in TITLEBLOCK_KEYWORDS if k in n}


def detect_titleblock(page: fitz.Page) -> fitz.Rect | None:
    """Rect del cajetín en espacio de vista (``page.rect``), o None."""
    rect = _from_text_layer(page)
    if rect is not None:
        return rect
    return _from_vision(page)


# ---------------------------------------------------------------------------
# Ruta 1: capa de texto (planos CAD digitales)
# ---------------------------------------------------------------------------

def _from_text_layer(page: fitz.Page) -> fitz.Rect | None:
    try:
        words = page.get_text("words")
    except Exception:
        return None
    if not words:
        return None

    matrix = page.rotation_matrix
    hits: list[tuple[fitz.Rect, str]] = []
    for w in words:
        kws = _keywords_in(str(w[4]))
        if kws:
            rect = fitz.Rect(w[0], w[1], w[2], w[3]) * matrix
            for k in kws:
                hits.append((rect, k))
    if len({k for _, k in hits}) < _MIN_TEXT_KEYWORDS:
        return None

    # Clúster dominante: mediana de centros + radio 25% de la diagonal.
    cxs = sorted((r.x0 + r.x1) / 2 for r, _ in hits)
    cys = sorted((r.y0 + r.y1) / 2 for r, _ in hits)
    mx, my = cxs[len(cxs) // 2], cys[len(cys) // 2]
    diag = (page.rect.width ** 2 + page.rect.height ** 2) ** 0.5
    radius = diag * 0.25

    kept = [
        (r, k) for r, k in hits
        if abs((r.x0 + r.x1) / 2 - mx) + abs((r.y0 + r.y1) / 2 - my) <= radius
    ]
    if len({k for _, k in kept}) < _MIN_TEXT_KEYWORDS:
        return None

    bbox = fitz.Rect(kept[0][0])
    for r, _ in kept[1:]:
        bbox |= r

    # Expandir: los valores acompañan a las etiquetas y el cajetín toca el
    # borde de página.  Padding proporcional + extensión al borde cercano.
    pad_x = page.rect.width * 0.03
    pad_y = page.rect.height * 0.03
    bbox = fitz.Rect(bbox.x0 - pad_x, bbox.y0 - pad_y,
                     bbox.x1 + pad_x, bbox.y1 + pad_y)
    edge = min(page.rect.width, page.rect.height) * 0.08
    if page.rect.x1 - bbox.x1 < edge:
        bbox.x1 = page.rect.x1
    if bbox.x0 - page.rect.x0 < edge:
        bbox.x0 = page.rect.x0
    if page.rect.y1 - bbox.y1 < edge:
        bbox.y1 = page.rect.y1
    if bbox.y0 - page.rect.y0 < edge:
        bbox.y0 = page.rect.y0
    bbox &= page.rect

    if bbox.get_area() > page.rect.get_area() * _MAX_AREA_FRAC or bbox.is_empty:
        return None
    return bbox


# ---------------------------------------------------------------------------
# Ruta 2: visión (planos escaneados)
# ---------------------------------------------------------------------------

def _from_vision(page: fitz.Page) -> fitz.Rect | None:
    scale = _LOCATE_DPI / 72.0
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    except Exception:
        return None
    W, H = image.size

    candidates = _grid_candidates(image)
    # Franjas de borde como candidatos adicionales (prior de posición del
    # cajetín; solo se aceptan si la validación OCR encuentra keywords).
    strip = int(W * _EDGE_STRIP_FRAC)
    candidates.append((W - strip, 0, strip, H))          # franja derecha
    strip_h = int(H * _EDGE_STRIP_FRAC)
    candidates.append((0, H - strip_h, W, strip_h))      # franja inferior

    for x, y, w, h in candidates:
        clip = fitz.Rect(x / scale, y / scale, (x + w) / scale, (y + h) / scale)
        clip &= page.rect
        if clip.is_empty:
            continue
        if clip.get_area() > page.rect.get_area() * _MAX_AREA_FRAC:
            continue
        if _validate_by_ocr(page, clip):
            return clip
    return None


def _grid_candidates(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Regiones con estructura de rejilla (líneas H/V largas), por nº de celdas."""
    W, H = image.size
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    # Binarización combinada: la adaptativa capta rejillas sobre fondo variable
    # (escaneos), la OTSU global conserva líneas finas anti-aliased que la
    # adaptativa con ventana pequeña pierde.
    adaptive = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, -2
    )
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    bw = cv2.bitwise_or(adaptive, otsu)
    hk = max(20, W // 60)
    vk = max(20, H // 60)
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (hk, 1)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, vk)))
    grid = cv2.dilate(cv2.add(horiz, vert), np.ones((9, 9), np.uint8))

    n, _, stats, _ = cv2.connectedComponentsWithStats(grid, connectivity=8)
    page_area = float(W * H)
    scored: list[tuple[int, tuple[int, int, int, int]]] = []
    for i in range(1, n):
        x, y, w, h, _area = stats[i]
        frac = (w * h) / page_area
        if not 0.005 <= frac <= _MAX_AREA_FRAC:
            continue
        inter = cv2.bitwise_and(horiz[y:y + h, x:x + w], vert[y:y + h, x:x + w])
        ni, _, _, _ = cv2.connectedComponentsWithStats(inter, connectivity=8)
        cells = ni - 1
        if cells >= 6:
            scored.append((cells, (x, y, w, h)))
    scored.sort(key=lambda s: -s[0])
    return [box for _, box in scored[:3]]


def _validate_by_ocr(page: fitz.Page, clip: fitz.Rect) -> bool:
    """OCR del clip re-rasterizado a DPI alto; exige ≥2 keywords de cajetín."""
    from app.services.ocr.factory import get_word_ocr_engine

    scale = _VALIDATE_DPI / 72.0
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    except Exception:
        return False

    with scratch_dir(prefix="titleblock_") as td:
        path = Path(td) / "clip.png"
        image.save(path)
        try:
            ocr = get_word_ocr_engine(None).analyze_image(image_path=path, page_index=0)
        except Exception:
            return False

    text = " ".join(t.text for t in ocr.tokens)
    found = _keywords_in(text)
    if len(found) >= _MIN_OCR_KEYWORDS:
        logger.info("Cajetín validado por OCR (%s) en %s", sorted(found), clip)
        return True
    return False
