from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from app.core.config import settings
from app.schemas.report import OcrInfo, ProcessingLimits, ProcessingReport
from app.services.ocr.easyocr_engine import EasyOcrEngine
from app.services.ocr.form_layout import OcrFormLayoutAnalyzer
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pdf.scanned_redactor import (
    _append_image_as_pdf_page,
    _build_text_and_spans,
    _redact_rendered_page_image,
    _render_page_to_image,
)
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity
from app.services.ocr.table_layout import OcrTableLayoutAnalyzer


def redact_pdf_hybrid_secure(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
) -> ProcessingReport:
    """
    PDF híbrido seguro.

    Flujo:
    1. Redacta físicamente texto digital con PyMuPDF sobre un PDF temporal.
    2. Las páginas digitales puras se copian desde ese PDF ya redactado.
    3. Las páginas escaneadas o mixtas se renderizan a imagen.
    4. Sobre esas páginas renderizadas se aplica OCR + redacción por píxel.
    5. Se crea un PDF final nuevo y limpio.
    """
    inspection = inspect_pdf_for_phase1(input_path)

    ocr_engine = EasyOcrEngine()
    form_layout = OcrFormLayoutAnalyzer()
    table_layout = OcrTableLayoutAnalyzer()

    all_ocr_entities: list[PiiEntity] = []
    all_residual_entities: list[PiiEntity] = []
    confidence_values: list[float] = []
    low_confidence_pages: list[int] = []
    warnings: list[str] = []

    ocr_redacted_count = 0
    ocr_pages_processed = 0

    with TemporaryDirectory(prefix="pdf_hybrid_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        digital_redacted_path = tmp_dir / "digital_redacted.pdf"

        digital_report = redact_pdf_digital(
            input_path=input_path,
            output_path=digital_redacted_path,
            job_id=job_id,
            analyzer=analyzer,
            mode=mode,
        )

        warnings.extend(digital_report.warnings)

        redacted_doc = fitz.open(digital_redacted_path)
        output_doc = fitz.open()

        try:
            for page_info in inspection.page_inspections:
                page_index = page_info.page_index
                page = redacted_doc[page_index]

                if page_info.page_type == "digital":
                    output_doc.insert_pdf(
                        redacted_doc,
                        from_page=page_index,
                        to_page=page_index,
                    )
                    continue

                ocr_pages_processed += 1

                image = _render_page_to_image(
                    page=page,
                    dpi=settings.ocr_pdf_render_dpi,
                )

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

                text, token_spans = _build_text_and_spans(ocr_page.tokens)
                entities = analyzer.analyze(text, mode=mode)
                all_ocr_entities.extend(entities)

                page_redacted_count = _redact_rendered_page_image(
                    image=image,
                    ocr_text=text,
                    entities=entities,
                    token_spans=token_spans,
                    form_layout=form_layout,
                    table_layout=table_layout,
                )

                ocr_redacted_count += page_redacted_count

                if settings.ocr_second_pass_verification:
                    redacted_page_path = tmp_dir / f"page_{page_index + 1}_redacted.png"
                    image.save(redacted_page_path)

                    residual_ocr_page = ocr_engine.analyze_image(
                        image_path=redacted_page_path,
                        page_index=page_index,
                    )

                    residual_text, _ = _build_text_and_spans(residual_ocr_page.tokens)
                    residual_entities = analyzer.analyze(residual_text, mode=mode)
                    all_residual_entities.extend(residual_entities)

                _append_image_as_pdf_page(
                    output_doc=output_doc,
                    image=image,
                    source_page_rect=page.rect,
                )

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
                    "PDF scrub cleanup could not be fully completed. Metadata and auxiliary object cleanup may be partial."
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

    residual_by_type = analyzer.count_by_type(all_residual_entities)
    residual_count = len(all_residual_entities)

    if low_confidence_pages:
        warnings.append("Low OCR confidence detected in one or more hybrid/scanned pages.")

    if residual_count > 0:
        warnings.append(
            "Residual PII-like entities detected after OCR redaction. Manual review required."
        )

    confidence_avg = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else None
    )

    manual_review_required = bool(low_confidence_pages) or residual_count > 0

    return ProcessingReport(
        job_id=job_id,
        status="completed_with_warnings" if warnings else "completed",
        document_type="pdf_hybrid",
        pages=inspection.pages,
        selectable_text_chars=inspection.selectable_text_chars,
        entities_detected=digital_report.entities_detected + len(all_ocr_entities),
        entities_redacted=digital_report.entities_redacted + ocr_redacted_count,
        entities_by_type={
            **digital_report.entities_by_type,
            **analyzer.count_by_type(all_ocr_entities),
        },
        residual_entities=residual_count,
        residual_entities_by_type=residual_by_type,
        manual_review_required=manual_review_required,
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
            engine="easyocr",
            pages_ocr_processed=ocr_pages_processed,
            images_processed=0,
            ocr_confidence_avg=confidence_avg,
            low_confidence_pages=low_confidence_pages,
            second_pass_verification=settings.ocr_second_pass_verification,
        ),
    )