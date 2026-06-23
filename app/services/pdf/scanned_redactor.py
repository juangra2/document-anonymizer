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

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from PIL import Image, ImageDraw

from app.core.config import settings
from app.schemas.report import DetectedEntityInfo, OcrInfo, ProcessingLimits, ProcessingReport
from app.services.layout.models import PageZone, ZoneToken, ZoneType
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.layout.zone_config import get_detector_config
from app.services.ocr.base import OcrEngine
from app.services.ocr.factory import get_ocr_engine, get_word_ocr_engine
from app.services.ocr.models import OcrPage
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity
from app.services.vision.code_detector import detect_visual_code_rects

logger = logging.getLogger(__name__)


def _run_with_timeout(fn, timeout_s: int):
    """Run *fn* in a worker thread, returning its result or raising on timeout.

    On timeout the worker thread is abandoned (Python cannot force-kill it); the
    caller skips the page so the overall job still completes.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    finally:
        executor.shutdown(wait=False)


def _assign_hints_from_layout(word_page: OcrPage, layout_page: OcrPage) -> None:
    """Copy each layout block's zone_hint onto the word tokens it contains.

    Geometry comes from word-level OCR (tight boxes); zone classification comes
    from the layout engine (MinerU).  Both run on the same rendered image, so a
    word's centre falling inside a block inherits that block's hint.
    """
    blocks = [b for b in layout_page.tokens if b.zone_hint]
    if not blocks:
        return
    for word in word_page.tokens:
        cx = (word.rect.x0 + word.rect.x1) / 2
        cy = (word.rect.y0 + word.rect.y1) / 2
        for block in blocks:
            r = block.rect
            if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1:
                word.zone_hint = block.zone_hint
                break


def _is_image_dominant_page(page: fitz.Page) -> bool:
    """True for plan/photo pages: a very large image covering most of the page.

    Distinguishes high-resolution blueprints (which stall the OCR engine) from
    ordinary scanned text pages by requiring a high pixel count, not just area.
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


def redact_pdf_scanned(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    document_type: str = "pdf_scanned",
    collect_entities: bool = False,
) -> ProcessingReport:
    inspection = inspect_pdf_for_phase1(input_path)

    ocr_engine = get_ocr_engine()
    # Redaction geometry uses word/line-level OCR for tight, aligned boxes; the
    # primary engine (e.g. MinerU) is used only for zone classification when it
    # provides layout.  When the primary already is EasyOCR, the same instance is
    # reused (single OCR pass).
    word_engine = get_word_ocr_engine(ocr_engine)
    provides_layout = ocr_engine.provides_layout
    segmenter = OcrPageSegmenter()

    source_doc = fitz.open(input_path)
    output_doc = fitz.open()

    all_entities: list[PiiEntity] = []
    all_residual_entities: list[PiiEntity] = []
    redacted_count = 0
    confidence_values: list[float] = []
    low_confidence_pages: list[int] = []
    skipped_pages: list[int] = []
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
                    logger.warning(
                        "Native PDF analysis failed (%s); falling back to raster OCR", exc
                    )

            def _process_page_heavy(page_index: int, image: Image.Image) -> dict:
                """OCR + segment + collect + draw + second pass for one page.

                Runs under a per-page timeout; draws on a copy so the caller can
                fall back to the un-redacted original if it times out.
                """
                work_image = image.copy()

                # Zone classification from the layout engine (MinerU), if any.
                layout_page: OcrPage | None = None
                if provides_layout:
                    if native_ocr_pages is not None and page_index < len(native_ocr_pages):
                        layout_page = native_ocr_pages[page_index]
                    else:
                        layout_path = tmp_dir / f"page_{page_index + 1}_layout.png"
                        image.save(layout_path)
                        layout_page = ocr_engine.analyze_image(
                            image_path=layout_path, page_index=page_index,
                        )

                # Geometry + text from the word/line-level engine.
                page_image_path = tmp_dir / f"page_{page_index + 1}.png"
                image.save(page_image_path)
                ocr_page = word_engine.analyze_image(
                    image_path=page_image_path, page_index=page_index,
                )
                if layout_page is not None:
                    _assign_hints_from_layout(ocr_page, layout_page)

                zoned_page = segmenter.segment(ocr_page, image=work_image)
                page_rects, page_entities = _collect_redaction_rects(
                    zoned_page=zoned_page,
                    analyzer=analyzer,
                    mode=mode,
                    collect_entities=collect_entities,
                )

                draw = ImageDraw.Draw(work_image)
                for rect in page_rects:
                    draw.rectangle(
                        [int(rect.x0), int(rect.y0), int(rect.x1), int(rect.y1)],
                        fill="black",
                    )

                residuals: list[PiiEntity] = []
                if settings.ocr_second_pass_verification:
                    redacted_path = tmp_dir / f"page_{page_index + 1}_redacted.png"
                    work_image.save(redacted_path)
                    residual_ocr = word_engine.analyze_image(
                        image_path=redacted_path, page_index=page_index,
                    )
                    residual_zoned = segmenter.segment(residual_ocr)
                    _, residuals = _collect_redaction_rects(
                        zoned_page=residual_zoned, analyzer=analyzer, mode=mode,
                    )

                return {
                    "image": work_image,
                    "entities": page_entities,
                    "residuals": residuals,
                    "rect_count": len(page_rects),
                    "confidence": ocr_page.avg_confidence,
                }

            for page_index, page in enumerate(source_doc):
                image = _render_page_to_image(page, dpi=settings.ocr_pdf_render_dpi)

                # Fast path: oversized plan/photo page → skip heavy text OCR and
                # redact only visual codes (and, later, signatures/stamps).
                if _is_image_dominant_page(page):
                    logger.warning(
                        "Page %d is image-dominant (plan/photo); skipping full OCR.",
                        page_index,
                    )
                    code_rects = detect_visual_code_rects(image)
                    draw = ImageDraw.Draw(image)
                    for rect in code_rects:
                        draw.rectangle(
                            [int(rect.x0), int(rect.y0), int(rect.x1), int(rect.y1)],
                            fill="black",
                        )
                    redacted_count += len(code_rects)
                    skipped_pages.append(page_index)
                    _append_image_as_pdf_page(output_doc, image, page.rect)
                    continue

                # Per-page timeout: a stalled page is skipped (un-redacted) and
                # flagged for manual review rather than hanging the whole job.
                try:
                    result = _run_with_timeout(
                        lambda: _process_page_heavy(page_index, image),
                        settings.ocr_page_timeout_s,
                    )
                except FuturesTimeout:
                    logger.warning(
                        "Page %d exceeded the %ds OCR budget; skipping (manual review).",
                        page_index, settings.ocr_page_timeout_s,
                    )
                    skipped_pages.append(page_index)
                    _append_image_as_pdf_page(output_doc, image, page.rect)
                    continue

                all_entities.extend(result["entities"])
                all_residual_entities.extend(result["residuals"])
                redacted_count += result["rect_count"]
                if result["confidence"] is not None:
                    confidence_values.append(result["confidence"])
                    if result["confidence"] < 0.75:
                        low_confidence_pages.append(page_index)

                _append_image_as_pdf_page(output_doc, result["image"], page.rect)

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
    if skipped_pages:
        warnings.append(
            "Pages skipped (image-dominant or OCR timeout) and not fully redacted: "
            f"{sorted(skipped_pages)}. Manual review required."
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
        manual_review_required=(
            bool(low_confidence_pages) or residual_count > 0 or bool(skipped_pages)
        ),
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
        detected_entities=(
            [
                DetectedEntityInfo(text=e.text, type=e.entity_type, score=e.score, source=e.source)
                for e in all_entities
                if e.text
            ]
            if collect_entities
            else []
        ),
    )


# ---------------------------------------------------------------------------
# Per-page redaction logic
# ---------------------------------------------------------------------------

def _collect_redaction_rects(
    zoned_page,
    analyzer: PiiAnalyzer,
    mode: str,
    collect_entities: bool = False,
) -> tuple[list[fitz.Rect], list[PiiEntity]]:
    """Return (pixel_rects_to_redact, pii_entities_found)."""
    rects: list[fitz.Rect] = []
    entities: list[PiiEntity] = []

    for zone in zoned_page.zones:

        # SIGNATURE / QR_CODE → full zone rect
        if zone.is_auto_redact:
            rects.append(zone.rect)
            if collect_entities and zone.zone_type == ZoneType.QR_CODE and zone.text:
                entities.append(PiiEntity(
                    entity_type="QR_CODE",
                    start=0,
                    end=len(zone.text),
                    score=0.99,
                    source="visual_qr",
                    text=zone.text,
                ))
            continue

        # FORM / TABLE with pre-computed structural rects (heuristic segmenter).
        # Only skip PII analysis when rects are present; MinerU creates TABLE/FORM
        # zones without pre-computed rects, so those must fall through to PII.
        if zone.zone_type in (ZoneType.FORM, ZoneType.TABLE) and zone.layout_redact_rects:
            rects.extend(zone.layout_redact_rects)
            if collect_entities and zone.text.strip():
                cfg = get_detector_config(zone.zone_type)
                if cfg.entity_types is not None:
                    form_entities = analyzer.analyze(
                        zone.text, mode=mode, zone_type=zone.zone_type.value
                    )
                    allowed = set(cfg.entity_types)
                    for entity in form_entities:
                        if entity.entity_type not in allowed:
                            continue
                        entities.append(PiiEntity(
                            entity_type=entity.entity_type,
                            start=entity.start,
                            end=entity.end,
                            score=entity.score,
                            source=entity.source,
                            text=zone.text[entity.start:entity.end],
                        ))
            continue

        # BODY / HEADER / FOOTER → PII analysis
        cfg = get_detector_config(zone.zone_type)
        if not zone.text.strip() or cfg.entity_types is None:
            continue

        local_entities = analyzer.analyze(
            zone.text,
            mode=mode,
            zone_type=zone.zone_type.value,
        )
        allowed = set(cfg.entity_types)
        local_entities = [e for e in local_entities if e.entity_type in allowed]

        for entity in local_entities:
            entity_rects = _entity_rects_from_zone(entity, zone)

            # Skip tokens that are part of existing redaction areas
            for r in entity_rects:
                if not _center_in_any(r, rects):
                    rects.append(r)

            if entity_rects:
                entities.append(PiiEntity(
                    entity_type=entity.entity_type,
                    start=entity.start,
                    end=entity.end,
                    score=entity.score,
                    source=entity.source,
                    text=zone.text[entity.start:entity.end],
                ))

    return rects, entities


def _entity_rects_from_zone(
    entity: PiiEntity,
    zone: PageZone,
) -> list[fitz.Rect]:
    """Map a PII entity span (in zone.text) to pixel-space rects via ZoneTokens.

    For MinerU form-row tokens that carry a value_start offset: when the entity
    falls in the value portion (local_start >= value_start), we map the entity to
    the full token rect instead of using character-proportion interpolation.
    This corrects the systematic rightward drift caused by long label prefixes
    in "Label: Value" row tokens.
    """
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

        # Form-row remapping: entity in value portion → map to full token rect.
        # Heuristic: token.value_start marks the boundary between label chars and
        # value chars. Characters < value_start are the label (outside token rect);
        # characters ≥ value_start are the value (fill the full token rect).
        # LEARNABLE: value_start is estimated from the row's HTML structure; exact
        # cell-level bboxes from the MinerU API would make this unnecessary.
        if (
            token.value_start is not None
            and local_start >= token.value_start
        ):
            value_len = len(token.text) - token.value_start
            if value_len > 0:
                vs = local_start - token.value_start
                ve = local_end - token.value_start
                rect = _rect_for_value_range(token.rect, vs, ve, value_len)
                if rect is not None:
                    result.append(rect)
            continue

        rect = _rect_for_token_range(token, local_start, local_end)
        if rect is not None:
            result.append(rect)

    return result


def _rect_for_value_range(
    token_rect: fitz.Rect,
    vs: int,
    ve: int,
    value_len: int,
) -> fitz.Rect | None:
    """Map a value-portion entity to pixel coords within the token rect.

    The token rect covers only the value column; vs/ve are offsets within
    the value text (0 = start of value, value_len = end of value).
    """
    vs = max(0, min(vs, value_len))
    ve = max(0, min(ve, value_len))
    if vs >= ve:
        return None
    rect = fitz.Rect(token_rect)
    if vs == 0 and ve == value_len:
        return _padded(rect)
    width = rect.x1 - rect.x0
    x0 = rect.x0 + width * (vs / value_len)
    x1 = rect.x0 + width * (ve / value_len)
    return _padded(fitz.Rect(x0, rect.y0, x1, rect.y1))


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
