"""Detección de páginas de plano de obra (pre-render, solo metadatos fitz).

Una página de plano (A1/A0, dibujo técnico) hace inviable el pipeline normal:
rasterizarla supera los 30 MP y el OCR/VLM se dispara.  El PII real vive en el
cajetín (bloque de metadatos del margen); el cuerpo del dibujo no se procesa.

La detección usa exclusivamente metadatos baratos de fitz — dimensiones de
página, dimensiones nativas de imágenes embebidas (sin decodificar), conteo de
paths vectoriales y bloques de texto — por lo que cuesta O(ms) por página y se
ejecuta ANTES de cualquier get_pixmap u OCR.

Umbrales calibrados con TOMO_04 (plano vectorial real del ayuntamiento,
A1 apaisado 2384×1684 pt): 60.494 drawings, 3.645 bloques de texto con mediana
de 18 chars y solo 1 bloque-párrafo (≥150 chars).  Un administrativo A4 tiene
<200 paths y decenas de bloques-párrafo.
"""
from __future__ import annotations

import fitz

from app.core.config import settings

# Longitud mínima de un bloque de texto para considerarlo "párrafo".
_PARAGRAPH_CHARS = 150


def is_image_dominant_page(page: fitz.Page) -> bool:
    """True para páginas plan/foto: una imagen enorme cubriendo casi todo.

    Solo metadatos (get_images da dimensiones nativas sin decodificar;
    get_image_rects es geometría) — no rasteriza.
    """
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height
    if page_area <= 0:
        return False

    try:
        images = page.get_images(full=True)
    except Exception:
        return False

    for im in images:
        xref = im[0]
        w_px = im[2] if len(im) > 2 else 0
        h_px = im[3] if len(im) > 3 else 0
        if (w_px * h_px) / 1_000_000.0 < settings.ocr_image_dominant_megapixels:
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for r in rects:
            if (r.width * r.height) / page_area >= settings.ocr_image_dominant_area_frac:
                return True
    return False


def _paragraph_block_count(page: fitz.Page) -> int:
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return 0
    return sum(
        1 for b in blocks
        if len(b) > 6 and b[6] == 0 and len((b[4] or "").strip()) >= _PARAGRAPH_CHARS
    )


def _drawing_count(page: fitz.Page) -> int:
    try:
        return len(page.get_cdrawings())   # variante rápida (dicts crudos)
    except Exception:
        try:
            return len(page.get_drawings())
        except Exception:
            return 0


def is_plan_page(page: fitz.Page) -> bool:
    """True cuando la página es un plano de obra (todas las señales a la vez):

    1. Gran formato (≥A2 en ambas dimensiones).
    2. Sin párrafos de texto (≤ plan_max_paragraph_blocks bloques ≥150 chars):
       las anotaciones de un plano son fragmentos sueltos, no prosa.
    3. Dominancia gráfica: imagen embebida dominante (plano escaneado) O
       volumen masivo de paths vectoriales (plano CAD digital).
    """
    if not settings.plan_page_enabled:
        return False

    w, h = page.rect.width, page.rect.height
    if min(w, h) < settings.plan_min_short_side_pts:
        return False
    if max(w, h) < settings.plan_min_long_side_pts:
        return False

    if _paragraph_block_count(page) > settings.plan_max_paragraph_blocks:
        return False

    if is_image_dominant_page(page):
        return True
    return _drawing_count(page) >= settings.plan_min_drawings
