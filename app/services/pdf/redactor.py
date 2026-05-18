from collections import defaultdict
from pathlib import Path

import fitz

from app.schemas.report import ProcessingLimits, ProcessingReport
from app.core.config import settings
from app.services.pdf.digital_layout import (
    analyze_digital_layout,
    filter_entity_rects_for_layout,
    free_text_segments_for_analysis,
)
from app.services.pdf.extractor import extract_page_text, rects_for_span
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity


def redact_pdf_digital(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
) -> ProcessingReport:
    inspection = inspect_pdf_for_phase1(input_path)

    doc = fitz.open(input_path)

    try:
        page_texts = [
            extract_page_text(page, page_index)
            for page_index, page in enumerate(doc)
        ]

        all_entities: list[PiiEntity] = []
        entities_by_page: dict[int, list[PiiEntity]] = defaultdict(list)
        layout_by_page = {}

        layout_detected_count = 0

        for page_text in page_texts:
            layout = None

            if mode == "strict":
                layout = analyze_digital_layout(page_text)
                layout_by_page[page_text.page_index] = layout
                layout_detected_count += len(layout.redaction_rects)

            if layout is not None:
                entities = _analyze_free_text_segments(
                    page_text=page_text,
                    layout=layout,
                    analyzer=analyzer,
                    mode=mode,
                )
            else:
                entities = analyzer.analyze(page_text.text, mode=mode)

            entities_by_page[page_text.page_index].extend(entities)
            all_entities.extend(entities)

        redacted_count = 0

        for page_text in page_texts:
            page = doc[page_text.page_index]
            layout = layout_by_page.get(page_text.page_index)

            if layout is not None:
                for rect in layout.redaction_rects:
                    page.add_redact_annot(
                        rect,
                        fill=(0, 0, 0),
                    )
                    redacted_count += 1

            for entity in entities_by_page.get(page_text.page_index, []):
                if mode == "strict" and entity.entity_type == "CASE_TABLE_ROW":
                    continue

                rects = rects_for_span(page_text, entity.start, entity.end)

                if layout is not None:
                    rects = filter_entity_rects_for_layout(
                        rects=rects,
                        layout=layout,
                    )

                for rect in rects:
                    page.add_redact_annot(
                        rect,
                        fill=(0, 0, 0),
                    )
                    redacted_count += 1

            page.apply_redactions()

        warnings: list[str] = []

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
                "PDF scrub cleanup could not be fully completed. Metadata and auxiliary object cleanup may be partial."
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

    if layout_detected_count:
        entities_by_type["LAYOUT_VALUE"] = layout_detected_count

    return ProcessingReport(
        job_id=job_id,
        status="completed" if not warnings else "completed_with_warnings",
        document_type="pdf_digital",
        pages=inspection.pages,
        selectable_text_chars=inspection.selectable_text_chars,
        entities_detected=len(all_entities) + layout_detected_count,
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
    )

def _analyze_free_text_segments(
    page_text,
    layout,
    analyzer: PiiAnalyzer,
    mode: str,
) -> list[PiiEntity]:
    entities: list[PiiEntity] = []

    segments = free_text_segments_for_analysis(
        page_text=page_text,
        layout=layout,
    )

    for segment in segments:
        local_entities = analyzer.analyze(
            segment.text,
            mode=mode,
        )

        for entity in local_entities:
            entities.append(
                PiiEntity(
                    entity_type=entity.entity_type,
                    start=segment.start + entity.start,
                    end=segment.start + entity.end,
                    score=entity.score,
                    source=entity.source,
                )
            )

    return entities