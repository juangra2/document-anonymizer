"""Detección de páginas de plano de obra (pre-render, solo metadatos fitz).

Una página de plano (A1/A0, dibujo técnico) hace inviable el pipeline normal:
rasterizarla supera los 30 MP y el OCR/VLM se dispara.  El PII real vive en el
cajetín (bloque de metadatos del margen); el cuerpo del dibujo no se procesa.

La detección usa exclusivamente metadatos baratos de fitz — dimensiones de
página, dimensiones nativas de imágenes embebidas (sin decodificar), conteo de
paths vectoriales y bloques de texto — por lo que cuesta O(ms) por página y se
ejecuta ANTES de cualquier get_pixmap u OCR.

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


_TITLEBLOCK_OCR_DPI = 300

import re as _re

# "Situación/Emplazamiento:" del cajetín → dirección del proyecto (PII del
# plano).  Local a este módulo: fuera del cajetín "situación" es prosa común.
# Numero de colegiado: por requisito del ayuntamiento NO se anonimiza.  Se
# localiza para excluirlo de la redaccion aunque el NER desborde el nombre del
# arquitecto hacia su etiqueta ("Luis Ortenbach Alonso Arquitecto Colegiado").
# Entre la etiqueta y el numero cabe cualquier marca corta ("nº", "núm.:",
# "ns" cuando el OCR degrada la "º"), en la misma linea o en la siguiente.
_COLEGIADO_RE = _re.compile(
    r"(?i)(?:col[·.]?legiat|colegiad[oa])"
    r"[^\d\r\n]{0,12}"
    r"(?:\r?\n[^\S\r\n]*)?"
    r"[\d][\d.]*"
)

_TB_ADDRESS_RE = _re.compile(
    r"(?im)^[^\S\r\n]*(?:situaci[oó]n?|emplazamiento|localizaci[oó]n)"
    r"[^\S\r\n]*:[^\S\r\n]*\n?(?P<value>[^\n]+(?:\n[^\n:]+)?)"
)


def redact_plan_page(page: fitz.Page, analyzer, mode: str = "strict"):
    """Redacta el PII del cajetín de una página-plano, in place.

    Flujo: localizar cajetín (titleblock.detect_titleblock) → re-rasterizar
    SOLO ese clip a 300 DPI → EasyOCR + análisis PII (pipeline de zonas del
    path escaneado) → mapear los rects a puntos de página (des-rotando: los
    annots de redacción esperan coordenadas sin rotar) → apply_redactions,
    que tacha también el contenido de imágenes embebidas (planos escaneados).

    El cuerpo del dibujo no se rasteriza ni se analiza nunca.

    Returns:
        (found, entities, n_rects): found=False si no hay cajetín (la página
        queda intacta; el caller debe marcarla para revisión manual).
    """
    # Imports perezosos: scanned_redactor importa este módulo (evita el ciclo)
    # y los motores OCR no deben cargarse si no hay páginas-plano.
    from app.services.layout.models import PageZone, ZoneType
    from app.services.layout.segmenters import _build_ocr_zone_text_and_tokens
    from app.services.ocr.factory import get_word_ocr_engine
    from app.services.vision.titleblock import detect_titleblock

    clip = detect_titleblock(page)
    if clip is None:
        return False, [], 0

    scale = _TITLEBLOCK_OCR_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    from io import BytesIO
    from PIL import Image
    image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")

    import tempfile
    from pathlib import Path as _Path
    with tempfile.TemporaryDirectory(prefix="plan_tb_") as td:
        img_path = _Path(td) / "titleblock.png"
        image.save(img_path)
        ocr_page = get_word_ocr_engine(None).analyze_image(
            image_path=img_path, page_index=page.number or 0,
        )
    if not ocr_page.tokens:
        return True, [], 0

    # El cajetín es un bloque pequeño y estructurado: se analiza como UNA sola
    # zona (partirlo en header/footer separa "Promotor:" de su valor) y con
    # heurísticas propias, porque TODO su contenido es metadato del proyecto:
    #   - LOCATION del NER con tokens de nombre/apellido INE → PERSON (los
    #     nombres de arquitecto en el cajetín se etiquetan a menudo LOCATION).
    #   - "Situación/Emplazamiento:" → ADDRESS (dirección del proyecto).
    zone_text, zone_tokens = _build_ocr_zone_text_and_tokens(ocr_page.tokens)
    zone = PageZone(
        zone_type=ZoneType.BODY,
        rect=fitz.Rect(0, 0, image.width, image.height),
        text=zone_text,
        tokens=zone_tokens,
        source="ocr",
    )

    from app.services.layout.zone_config import ALL_PII_TYPES
    from app.services.pii import gazetteer
    from app.services.pii.analyzer import _name_tokens
    from app.services.pii.models import PiiEntity
    from app.services.pdf.scanned_redactor import (
        _center_in_any, _entity_rects_from_zone,
    )

    raw = analyzer.analyze(zone_text, mode=mode, zone_type=None)
    allowed = set(ALL_PII_TYPES)
    entities = []
    for e in raw:
        if e.entity_type == "LOCATION" and any(
            gazetteer.is_person_token(t) for t in _name_tokens(zone_text[e.start:e.end])
        ):
            e = PiiEntity("PERSON", e.start, e.end, e.score, e.source,
                          zone_text[e.start:e.end])
        if e.entity_type in allowed:
            entities.append(PiiEntity(
                e.entity_type, e.start, e.end, e.score, e.source,
                zone_text[e.start:e.end],
            ))
    for m in _TB_ADDRESS_RE.finditer(zone_text):
        entities.append(PiiEntity(
            "ADDRESS", m.start("value"), m.end("value"), 0.9,
            "labeled_field", m.group("value"),
        ))

    # El numero de colegiado se preserva: se recorta todo span que lo invada y
    # se descarta el que quede vacio.  Sin esto, un span de PERSON desbordado
    # ("... Arquitecto Colegiado n 6.402") se llevaria el numero por delante.
    protected = [(m.start(), m.end()) for m in _COLEGIADO_RE.finditer(zone_text)]
    if protected:
        trimmed: list[PiiEntity] = []
        for e in entities:
            start, end = e.start, e.end
            for p_start, p_end in protected:
                if start < p_end and p_start < end:      # solapan
                    if start < p_start:
                        end = min(end, p_start)
                    else:
                        start = max(start, p_end)
            if end - start >= 3:
                trimmed.append(PiiEntity(
                    e.entity_type, start, end, e.score, e.source,
                    zone_text[start:end],
                ))
        entities = trimmed

    px_rects: list[fitz.Rect] = []
    for e in entities:
        for r in _entity_rects_from_zone(e, zone):
            if not _center_in_any(r, px_rects):
                px_rects.append(r)

    derot = page.derotation_matrix
    for r in px_rects:
        view = fitz.Rect(
            clip.x0 + r.x0 / scale, clip.y0 + r.y0 / scale,
            clip.x0 + r.x1 / scale, clip.y0 + r.y1 / scale,
        ) & clip
        if view.is_empty:
            continue
        page.add_redact_annot(view * derot, fill=(0, 0, 0))

    if px_rects:
        try:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
        except Exception:
            # Colorspace exótico en imagen embebida: eliminar la imagen que
            # solapa la redacción en lugar de tachar sus píxeles.
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
    return True, entities, len(px_rects)


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
