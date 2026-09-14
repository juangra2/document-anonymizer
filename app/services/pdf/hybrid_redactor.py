"""Hybrid-secure PDF redactor.

Delegates to the two specialised redactors:
  - redact_pdf_digital  → handles digital pages (text-layer redaction)
  - OcrPageSegmenter + pixel painting → handles scanned/hybrid pages

Flow
────
1. Run redact_pdf_digital on the full input → intermediate redacted PDF.
2. Build the final output PDF:
   • Digital pages  → copy directly from the intermediate PDF.
   • Scanned/hybrid → render page from intermediate, run OcrPageSegmenter,
     paint redaction rects, embed as image page.
3. Scrub metadata, save.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from app.core.config import settings
from app.schemas.report import DetectedEntityInfo, OcrInfo, ProcessingLimits, ProcessingReport
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.ocr.factory import get_ocr_engine
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pdf.plan_pages import is_plan_page
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pdf.scanned_redactor import (
    _append_image_as_pdf_page,
    _collect_redaction_rects,
    _render_page_to_image,
)
from app.services.pii.analyzer import PiiAnalyzer
from app.services import progress
from app.services.pii.models import PiiEntity
from PIL import ImageDraw


def redact_pdf_hybrid_secure(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    collect_entities: bool = False,
) -> ProcessingReport:
    inspection = inspect_pdf_for_phase1(input_path)

    # Se reclama el aviso de avance ANTES del pase digital: ese pase recorre las
    # mismas paginas, y si informara el, el avance llegaria al 100 % justo antes
    # de empezar el OCR, que es la parte lenta.
    on_page = progress.claim(len(inspection.page_inspections))

    ocr_engine = get_ocr_engine()
    segmenter = OcrPageSegmenter()

    all_ocr_entities: list[PiiEntity] = []
    all_residual_entities: list[PiiEntity] = []
    confidence_values: list[float] = []
    low_confidence_pages: list[int] = []
    warnings: list[str] = []

    ocr_redacted_count = 0
    ocr_pages_processed = 0
    plan_pages_carried: list[int] = []

    with TemporaryDirectory(prefix="pdf_hybrid_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        digital_redacted_path = tmp_dir / "digital_redacted.pdf"

        digital_report = redact_pdf_digital(
            input_path=input_path,
            output_path=digital_redacted_path,
            job_id=job_id,
            analyzer=analyzer,
            mode=mode,
            collect_entities=collect_entities,
        )
        warnings.extend(digital_report.warnings)

        redacted_doc = fitz.open(digital_redacted_path)
        output_doc = fitz.open()

        try:
            for page_info in inspection.page_inspections:
                page_index = page_info.page_index
                on_page(page_index)
                page = redacted_doc[page_index]

                # Plan pages (A1/A0 drawings): the digital pass already located
                # and redacted the title block (or flagged the page — its
                # warnings propagate).  Carried through without OCR: the page
                # budget cannot cover rasterising an A1/A0 drawing.
                if is_plan_page(page):
                    plan_pages_carried.append(page_index)
                    output_doc.insert_pdf(
                        redacted_doc, from_page=page_index, to_page=page_index,
                    )
                    continue

                if page_info.page_type == "digital":
                    output_doc.insert_pdf(
                        redacted_doc,
                        from_page=page_index,
                        to_page=page_index,
                    )
                    continue

                ocr_pages_processed += 1
                image = _render_page_to_image(page, dpi=settings.ocr_pdf_render_dpi)
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
                    collect_entities=collect_entities,
                )

                all_ocr_entities.extend(page_entities)
                ocr_redacted_count += len(page_rects)

                from PIL import ImageDraw as _ID
                draw = _ID.Draw(image)
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

            try:
                output_doc.scrub(
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
            redacted_doc.close()
            output_doc.close()

    residual_count = len(all_residual_entities)
    residual_by_type = analyzer.count_by_type(all_residual_entities)

    if low_confidence_pages:
        warnings.append("Low OCR confidence detected in one or more hybrid/scanned pages.")
    if residual_count > 0:
        warnings.append(
            "Residual PII-like entities detected after OCR redaction. Manual review required."
        )

    confidence_avg = (
        sum(confidence_values) / len(confidence_values) if confidence_values else None
    )

    all_detected = (
        list(digital_report.detected_entities) + [
            DetectedEntityInfo(text=e.text, type=e.entity_type, score=e.score, source=e.source)
            for e in all_ocr_entities
            if e.text
        ]
        if collect_entities
        else []
    )

    return ProcessingReport(
        job_id=job_id,
        status="completed_with_warnings" if warnings else "completed",
        document_type="pdf_hybrid",
        pages=inspection.pages,
        selectable_text_chars=inspection.selectable_text_chars,
        entities_detected=digital_report.entities_detected + len(all_ocr_entities),
        entities_redacted=digital_report.entities_redacted + ocr_redacted_count,
        # Se suman: con {**a, **b} el recuento OCR pisaba al digital del mismo tipo.
        entities_by_type=dict(
            Counter(digital_report.entities_by_type)
            + Counter(analyzer.count_by_type(all_ocr_entities))
        ),
        structural_redactions_untyped=digital_report.structural_redactions_untyped,
        residual_entities=residual_count,
        residual_entities_by_type=residual_by_type,
        manual_review_required=(
            bool(low_confidence_pages)
            or residual_count > 0
            or digital_report.manual_review_required
        ),
        warnings=warnings,
        redaction_strategy="hybrid_secure_pdf_redaction",
        redaction_mode=mode,
        limits=ProcessingLimits(
            max_upload_mb=settings.max_upload_mb,
            max_pdf_pages=settings.max_pdf_pages,
            min_digital_text_chars=settings.min_digital_text_chars,
        ),
        ocr=OcrInfo(
            enabled=ocr_pages_processed > 0,
            engine=ocr_engine.engine_name,
            pages_ocr_processed=ocr_pages_processed,
            images_processed=0,
            ocr_confidence_avg=confidence_avg,
            low_confidence_pages=low_confidence_pages,
            second_pass_verification=settings.ocr_second_pass_verification,
        ),
        detected_entities=all_detected,
    )
