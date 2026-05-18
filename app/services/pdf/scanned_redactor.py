"""Scanned-PDF redactor — layout-first architecture.

Flow per page
─────────────
1. Render page to PNG at configured DPI.
2. EasyOCR → OcrPage  (tokens with pixel-space bboxes + confidence).
3. OcrPageSegmenter.segment(ocr_page, image) → ZonedPage.
4. Per zone:
   • SIGNATURE / QR_CODE → full zone.rect painted black.
   • FORM                 → zone.layout_redact_rects painted black.
   • BODY / HEADER / FOOTER → PII analyzer on zone.text, entity spans
     mapped to token rects via ZoneToken.text_offset.
5. Optional second-pass OCR on the redacted image to check for residuals.
6. Embed redacted images as PDF pages, save clean output.
"""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from PIL import Image, ImageDraw

from app.core.config import settings
from app.schemas.report import OcrInfo, ProcessingLimits, ProcessingReport
from app.services.layout.models import PageZone, ZoneToken, ZoneType
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.layout.zone_config import get_detector_config
from app.services.ocr.base import OcrEngine
from app.services.ocr.factory import get_ocr_engine
from app.services.ocr.models import OcrPage
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity


def redact_pdf_scanned(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    document_type: str = "pdf_scanned",
) -> ProcessingReport:
    inspection = inspect_pdf_for_phase1(input_path)

    ocr_engine = get_ocr_engine()
    segmenter = OcrPageSegmenter()

    source_doc = fitz.open(input_path)
    output_doc = fitz.open()

    all_entities: list[PiiEntity] = []
    all_residual_entities: list[PiiEntity] = []
    redacted_count = 0
    confidence_values: list[float] = []
    low_confidence_pages: list[int] = []
    warnings: list[str] = []

    try:
        with TemporaryDirectory(prefix="pdf_ocr_") as tmp_dir_name:
            tmp_dir = Path(tmp_dir_name)

            # Engines that process the full PDF natively (MinerU) do one pass here.
            # Raster-based engines (EasyOCR) process page-by-page below.
            native_ocr_pages: list[OcrPage] | None = None
            if ocr_engine.supports_pdf_input:
                try:
                    native_ocr_pages = ocr_engine.analyze_pdf(input_path)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Native PDF analysis failed (%s); falling back to raster OCR", exc
                    )

            for page_index, page in enumerate(source_doc):
                image = _render_page_to_image(page, dpi=settings.ocr_pdf_render_dpi)

                if native_ocr_pages is not None and page_index < len(native_ocr_pages):
                    ocr_page = native_ocr_pages[page_index]
                else:
                    page_image_path = tmp_dir / f"page_{page_index + 1}.png"
                    image.save(page_image_path)
                    ocr_page = ocr_engine.analyze_image(
                        image_path=page_image_path,
                        page_index=page_index,
                    )

                if ocr_page.avg_confidence is not None:
                    confidence_values.append(ocr_page.avg_confidence)
                    if ocr_page.avg_confidence < 0.75:
                        low_confidence_pages.append(page_index)

                zoned_page = segmenter.segment(ocr_page, image=image)

                page_rects, page_entities = _collect_redaction_rects(
                    zoned_page=zoned_page,
                    analyzer=analyzer,
                    mode=mode,
                )

                all_entities.extend(page_entities)
                redacted_count += len(page_rects)

                draw = ImageDraw.Draw(image)
                for rect in page_rects:
                    draw.rectangle(
                        [int(rect.x0), int(rect.y0), int(rect.x1), int(rect.y1)],
                        fill="black",
                    )

                if settings.ocr_second_pass_verification:
                    redacted_path = tmp_dir / f"page_{page_index + 1}_redacted.png"
                    image.save(redacted_path)

                    residual_ocr = ocr_engine.analyze_image(
                        image_path=redacted_path,
                        page_index=page_index,
                    )
                    residual_zoned = segmenter.segment(residual_ocr)
                    _, residual_entities = _collect_redaction_rects(
                        zoned_page=residual_zoned,
                        analyzer=analyzer,
                        mode=mode,
                    )
                    all_residual_entities.extend(residual_entities)

                _append_image_as_pdf_page(output_doc, image, page.rect)

        output_doc.set_metadata({})
        try:
            output_doc.del_xml_metadata()
        except Exception:
            pass

        output_doc.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
            incremental=False,
        )

    finally:
        source_doc.close()
        output_doc.close()

    residual_by_type = analyzer.count_by_type(all_residual_entities)
    residual_count = len(all_residual_entities)

    if low_confidence_pages:
        warnings.append("Low OCR confidence detected in one or more pages.")
    if residual_count > 0:
        warnings.append(
            "Residual PII-like entities detected after OCR redaction. Manual review required."
        )

    confidence_avg = (
        sum(confidence_values) / len(confidence_values) if confidence_values else None
    )

    return ProcessingReport(
        job_id=job_id,
        status="completed_with_warnings" if warnings else "completed",
        document_type=document_type,
        pages=inspection.pages,
        selectable_text_chars=inspection.selectable_text_chars,
        entities_detected=len(all_entities),
        entities_redacted=redacted_count,
        entities_by_type=analyzer.count_by_type(all_entities),
        residual_entities=residual_count,
        residual_entities_by_type=residual_by_type,
        manual_review_required=bool(low_confidence_pages) or residual_count > 0,
        warnings=warnings,
        redaction_mode=mode,
        limits=ProcessingLimits(
            max_upload_mb=settings.max_upload_mb,
            max_pdf_pages=settings.max_pdf_pages,
            min_digital_text_chars=settings.min_digital_text_chars,
        ),
        ocr=OcrInfo(
            enabled=True,
            engine=ocr_engine.engine_name,
            pages_ocr_processed=inspection.pages,
            images_processed=0,
            ocr_confidence_avg=confidence_avg,
            low_confidence_pages=low_confidence_pages,
            second_pass_verification=settings.ocr_second_pass_verification,
        ),
    )


# ---------------------------------------------------------------------------
# Per-page redaction logic
# ---------------------------------------------------------------------------

def _collect_redaction_rects(
    zoned_page,
    analyzer: PiiAnalyzer,
    mode: str,
) -> tuple[list[fitz.Rect], list[PiiEntity]]:
    """Return (pixel_rects_to_redact, pii_entities_found)."""
    rects: list[fitz.Rect] = []
    entities: list[PiiEntity] = []

    for zone in zoned_page.zones:

        # SIGNATURE / QR_CODE → full zone rect
        if zone.is_auto_redact:
            rects.append(zone.rect)
            continue

        # FORM / TABLE with pre-computed structural rects (heuristic segmenter).
        # Only skip PII analysis when rects are present; MinerU creates TABLE/FORM
        # zones without pre-computed rects, so those must fall through to PII.
        if zone.zone_type in (ZoneType.FORM, ZoneType.TABLE) and zone.layout_redact_rects:
            rects.extend(zone.layout_redact_rects)
            continue

        # BODY / HEADER / FOOTER → PII analysis
        cfg = get_detector_config(zone.zone_type)
        if not zone.text.strip() or cfg.entity_types is None:
            continue

        local_entities = analyzer.analyze(zone.text, mode=mode)
        allowed = set(cfg.entity_types)
        local_entities = [e for e in local_entities if e.entity_type in allowed]

        for entity in local_entities:
            entity_rects = _entity_rects_from_zone(entity, zone)

            # Skip tokens that are part of existing redaction areas
            for r in entity_rects:
                if not _center_in_any(r, rects):
                    rects.append(r)

            if entity_rects:
                entities.append(entity)

    return rects, entities


def _entity_rects_from_zone(
    entity: PiiEntity,
    zone: PageZone,
) -> list[fitz.Rect]:
    """Map a PII entity span (in zone.text) to pixel-space rects via ZoneTokens."""
    result: list[fitz.Rect] = []

    for token in zone.tokens:
        if token.text_offset is None:
            continue

        t_start = token.text_offset
        t_end = t_start + len(token.text)

        if t_end <= entity.start or t_start >= entity.end:
            continue

        local_start = max(entity.start, t_start) - t_start
        local_end = min(entity.end, t_end) - t_start

        rect = _rect_for_token_range(token, local_start, local_end)
        if rect is not None:
            result.append(rect)

    return result


def _rect_for_token_range(
    token: ZoneToken,
    local_start: int,
    local_end: int,
) -> fitz.Rect | None:
    text = token.text
    if not text:
        return None

    token_len = len(text)
    local_start = max(0, min(local_start, token_len))
    local_end = max(0, min(local_end, token_len))

    if local_start >= local_end:
        return None

    rect = fitz.Rect(token.rect)

    if local_start == 0 and local_end == token_len:
        return _padded(rect)

    width = rect.x1 - rect.x0
    x0 = rect.x0 + width * (local_start / token_len)
    x1 = rect.x0 + width * (local_end / token_len)

    return _padded(fitz.Rect(x0, rect.y0, x1, rect.y1))


def _padded(rect: fitz.Rect, px: float = 2.0, py: float = 2.0) -> fitz.Rect:
    return fitz.Rect(rect.x0 - px, rect.y0 - py, rect.x1 + px, rect.y1 + py)


def _center_in_any(rect: fitz.Rect, containers: list[fitz.Rect]) -> bool:
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    for c in containers:
        if c.x0 - 3 <= cx <= c.x1 + 3 and c.y0 - 3 <= cy <= c.y1 + 3:
            return True
    return False


# ---------------------------------------------------------------------------
# Shared PDF/image utilities (also used by hybrid_redactor)
# ---------------------------------------------------------------------------

def _render_page_to_image(page: fitz.Page, dpi: int) -> Image.Image:
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")


def _append_image_as_pdf_page(
    output_doc: fitz.Document,
    image: Image.Image,
    source_page_rect: fitz.Rect,
) -> None:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    page = output_doc.new_page(
        width=source_page_rect.width,
        height=source_page_rect.height,
    )
    page.insert_image(page.rect, stream=buffer.getvalue())
