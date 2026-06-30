"""Digital-PDF redactor — layout-first architecture.

Flow per page
─────────────
1. extract_page_text  → PageText  (word boxes + char offsets)
2. DigitalPageSegmenter.segment → ZonedPage  (HEADER / FOOTER / FORM / BODY)
3. Per zone:
   • FORM  → apply zone.layout_redact_rects directly (structural detection)
   • BODY / HEADER / FOOTER → run PII analyzer on zone.text, translate
     local entity offsets to absolute PageText offsets, get word-level rects
4. Visual QR detection: render page, detect codes, add QR_CODE zones.
5. apply_redactions, scrub metadata, save.
"""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image

from app.core.config import settings
from app.schemas.report import DetectedEntityInfo, ProcessingLimits, ProcessingReport
from app.services.layout.models import PageZone, ZoneType
from app.services.layout.segmenters import DigitalPageSegmenter
from app.services.layout.zone_config import get_detector_config
from app.services.pdf.extractor import extract_page_text, rects_for_span
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.institution_filter import filter_institutional
from app.services.pii.models import PiiEntity
from app.services.vision.code_detector import detect_redactable_codes, detect_visual_code_rects
from app.services.vision.signature_region import detect_signature_rects

# Render DPI used only for visual QR detection — not full-page OCR.
# 300 DPI gives ~5–6 px/module for small QR codes (contrato_laboral, notificacion)
# which improves decode rate significantly vs 200 DPI (3–4 px/module).
_QR_DETECT_DPI = 300


def redact_pdf_digital(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    collect_entities: bool = False,
) -> ProcessingReport:
    inspection = inspect_pdf_for_phase1(input_path)
    segmenter = DigitalPageSegmenter()

    doc = fitz.open(input_path)

    try:
        all_entities: list[PiiEntity] = []
        layout_rect_count = 0
        redacted_count = 0
        warnings: list[str] = []

        for page_index, page in enumerate(doc):
            page_text = extract_page_text(page, page_index)
            zoned_page = segmenter.segment(page_text, page.rect)

            # Add QR_CODE zones from visual detection.
            # DigitalPageSegmenter works on text only and cannot detect visual codes;
            # we render the page at low DPI and scan for QR codes separately.
            _add_visual_qr_zones(page, zoned_page)

            # Add SIGNATURE zones for handwritten signatures (images / ink).
            if settings.detect_visual_signatures:
                _add_signature_zones(page, zoned_page, page_text)

            page_rects, page_entities, page_layout_count = _collect_redaction_rects(
                zoned_page=zoned_page,
                page_text=page_text,
                analyzer=analyzer,
                mode=mode,
                collect_entities=collect_entities,
            )

            all_entities.extend(page_entities)
            layout_rect_count += page_layout_count
            redacted_count += len(page_rects)

            for rect in page_rects:
                page.add_redact_annot(rect, fill=(0, 0, 0))

            page.apply_redactions()

        try:
            doc.scrub(
                attached_files=True,
                clean_pages=True,
                embedded_files=True,
                hidden_text=True,
                javascript=True,
                metadata=True,
                redactions=True,
                remove_links=True,
                reset_fields=True,
                reset_responses=True,
                thumbnails=True,
                xml_metadata=True,
            )
        except Exception:
            warnings.append(
                "PDF scrub cleanup could not be fully completed. "
                "Metadata and auxiliary object cleanup may be partial."
            )

        doc.set_metadata({})

        try:
            doc.del_xml_metadata()
        except Exception:
            pass

        doc.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
            incremental=False,
        )

    finally:
        doc.close()

    entities_by_type = analyzer.count_by_type(all_entities)

    if layout_rect_count:
        entities_by_type["LAYOUT_VALUE"] = layout_rect_count

    return ProcessingReport(
        job_id=job_id,
        status="completed" if not warnings else "completed_with_warnings",
        document_type="pdf_digital",
        pages=inspection.pages,
        selectable_text_chars=inspection.selectable_text_chars,
        entities_detected=len(all_entities) + layout_rect_count,
        entities_redacted=redacted_count,
        entities_by_type=entities_by_type,
        residual_entities=0,
        manual_review_required=False,
        warnings=warnings,
        redaction_mode=mode,
        limits=ProcessingLimits(
            max_upload_mb=settings.max_upload_mb,
            max_pdf_pages=settings.max_pdf_pages,
            min_digital_text_chars=settings.min_digital_text_chars,
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
    page_text,
    analyzer: PiiAnalyzer,
    mode: str,
    collect_entities: bool = False,
) -> tuple[list[fitz.Rect], list[PiiEntity], int]:
    """Return (rects_to_redact, pii_entities_found, layout_rect_count)."""
    rects: list[fitz.Rect] = []
    entities: list[PiiEntity] = []
    layout_count = 0

    # BODY/HEADER/FOOTER entity rects are deferred so institutional contact data
    # (issuer phone/address/CP) can be filtered out at page level before emission.
    pii_entity_rects: dict[int, list[fitz.Rect]] = {}

    # Collect all protected rects across zones to filter entity-based rects
    all_protected: list[fitz.Rect] = []
    for zone in zoned_page.zones:
        all_protected.extend(zone.layout_protected_rects)

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
            layout_count += len(zone.layout_redact_rects)
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
                            start=zone.page_text_offset + entity.start,
                            end=zone.page_text_offset + entity.end,
                            score=entity.score,
                            source=entity.source,
                            text=zone.text[entity.start:entity.end],
                        ))
            continue

        # BODY / HEADER / FOOTER → PII analysis with zone-specific types
        cfg = get_detector_config(zone.zone_type)
        if not zone.text.strip() or cfg.entity_types is None:
            continue

        local_entities = analyzer.analyze(
            zone.text,
            mode=mode,
            zone_type=zone.zone_type.value,
        )

        # Filter to types relevant for this zone
        allowed = set(cfg.entity_types)
        local_entities = [e for e in local_entities if e.entity_type in allowed]

        for entity in local_entities:
            abs_start = zone.page_text_offset + entity.start
            abs_end = zone.page_text_offset + entity.end

            entity_rects = [
                r
                for r in rects_for_span(page_text, abs_start, abs_end)
                if not _center_in_any(r, all_protected)
            ]

            idx = len(entities)
            entities.append(PiiEntity(
                entity_type=entity.entity_type,
                start=abs_start,
                end=abs_end,
                score=entity.score,
                source=entity.source,
                text=zone.text[entity.start:entity.end],
            ))
            pii_entity_rects[idx] = entity_rects

    # Suppress institutional contact data (issuer's own phone/address/CP) before
    # emitting BODY/HEADER/FOOTER entity rects: it is not personal data under RGPD.
    suppressed = filter_institutional(page_text.text, zoned_page, entities)

    for idx, entity_rects in pii_entity_rects.items():
        if idx in suppressed:
            continue
        rects.extend(entity_rects)

    if suppressed:
        entities = [e for i, e in enumerate(entities) if i not in suppressed]

    # Deduplicate by (start, end, entity_type): the FORM zone text is a sparse
    # reconstruction of form-word characters, but as a safety net remove any exact
    # duplicates that might arise from overlapping zone text ranges.
    if collect_entities and entities:
        seen: set[tuple[int, int, str]] = set()
        deduped: list[PiiEntity] = []
        for e in entities:
            key = (e.start, e.end, e.entity_type)
            if key not in seen:
                seen.add(key)
                deduped.append(e)
        entities = deduped

    return rects, entities, layout_count


def _center_in_any(rect: fitz.Rect, containers: list[fitz.Rect]) -> bool:
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    for c in containers:
        if c.x0 - 1.5 <= cx <= c.x1 + 1.5 and c.y0 - 1.5 <= cy <= c.y1 + 1.5:
            return True
    return False


def _add_signature_zones(page: fitz.Page, zoned_page, page_text) -> None:
    """Append SIGNATURE zones for handwritten signatures (in-place).

    SIGNATURE zones are auto-redacted (whole rect), like QR_CODE zones.
    """
    for rect in detect_signature_rects(page, page_text):
        zoned_page.zones.append(PageZone(
            zone_type=ZoneType.SIGNATURE,
            rect=rect,
            text="",
            source="digital",
        ))


def _add_visual_qr_zones(page: fitz.Page, zoned_page) -> None:
    """Render *page* at low DPI, detect QR codes, append QR_CODE zones (in-place).

    Coordinate conversion: image pixels → PDF pts via 1/scale (scale = DPI/72).
    """
    scale = _QR_DETECT_DPI / 72.0
    matrix = fitz.Matrix(scale, scale)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")

    # Shared guard (see code_detector.detect_redactable_codes): only codes that
    # decode to a non-empty payload and are not implausibly large are redacted.
    for qr_rect, decoded_text in detect_redactable_codes(image):
        pdf_rect = fitz.Rect(
            qr_rect.x0 / scale,
            qr_rect.y0 / scale,
            qr_rect.x1 / scale,
            qr_rect.y1 / scale,
        )
        zoned_page.zones.append(PageZone(
            zone_type=ZoneType.QR_CODE,
            rect=pdf_rect,
            text=decoded_text,
            source="digital",
        ))
