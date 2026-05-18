from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import re

import fitz
from PIL import Image, ImageDraw

from app.core.config import settings
from app.schemas.report import OcrInfo, ProcessingLimits, ProcessingReport
from app.services.ocr.easyocr_engine import EasyOcrEngine
from app.services.ocr.form_layout import (
    OcrFormLayoutAnalyzer,
    normalize_text,
    padded_rect,
    rect_for_token_range,
)
from app.services.ocr.models import OcrToken
from app.services.ocr.signature_detector import detect_signature_rect
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity
from app.services.ocr.table_layout import OcrTableLayoutAnalyzer
from app.services.vision.code_detector import detect_visual_code_rects


class TokenSpan:
    def __init__(self, start: int, end: int, token: OcrToken) -> None:
        self.start = start
        self.end = end
        self.token = token

OCR_SENSITIVE_PATTERN_RE = re.compile(
    r"("
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b|"
    r"\b\d{8}[A-Z]\b|"
    r"\b[XYZ]\d{7}[A-Z]\b|"
    r"\b[A-HJNPQRSUVW]\d{7}[0-9A-J]\b|"
    r"\bES\d{2}(?:[ \t\-]?\d{4}){5}\b|"
    r"\b(?:\+34[ \t.\-]*)?(?:6|7|8|9)(?:[ \t.\-]?\d){8}\b|"
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b|"
    r"\b(?:EXP|REF|PAC|POLIZA|FAKE|USR|COACV)[-/_.]?[A-Z0-9]+"
    r")",
    re.IGNORECASE,
)


OCR_TITLE_KEYWORDS = {
    "tabla",
    "formulario",
    "documento",
    "imagen",
    "escaneada",
    "escaneado",
    "incrustada",
    "incrustado",
    "datos ficticios",
    "contactos ficticios",
    "no valida",
    "no válida",
    "ficticio",
    "ficticios",
    "sintetico",
    "sintético",
}


def redact_pdf_scanned(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    document_type: str = "pdf_scanned",
) -> ProcessingReport:
    """
    Redacción segura para PDF escaneado.

    No pinta encima del PDF original.
    Renderiza cada página a imagen, redacta píxeles y crea un PDF nuevo.
    """
    inspection = inspect_pdf_for_phase1(input_path)

    ocr_engine = EasyOcrEngine()
    form_layout = OcrFormLayoutAnalyzer()
    table_layout = OcrTableLayoutAnalyzer()

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

            for page_index, page in enumerate(source_doc):
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

                all_entities.extend(entities)

                page_redacted_count = _redact_rendered_page_image(
                    image=image,
                    ocr_text=text,
                    entities=entities,
                    token_spans=token_spans,
                    form_layout=form_layout,
                    table_layout=table_layout,
                )

                redacted_count += page_redacted_count

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
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else None
    )

    manual_review_required = bool(low_confidence_pages) or residual_count > 0

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
        manual_review_required=manual_review_required,
        warnings=warnings,
        redaction_mode=mode,
        limits=ProcessingLimits(
            max_upload_mb=settings.max_upload_mb,
            max_pdf_pages=settings.max_pdf_pages,
            min_digital_text_chars=settings.min_digital_text_chars,
        ),
        ocr=OcrInfo(
            enabled=True,
            engine="easyocr",
            pages_ocr_processed=inspection.pages,
            images_processed=0,
            ocr_confidence_avg=confidence_avg,
            low_confidence_pages=low_confidence_pages,
            second_pass_verification=settings.ocr_second_pass_verification,
        ),
    )


def _render_page_to_image(
    page: fitz.Page,
    dpi: int,
) -> Image.Image:
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    pixmap = page.get_pixmap(
        matrix=matrix,
        alpha=False,
    )

    image_bytes = pixmap.tobytes("png")

    return Image.open(BytesIO(image_bytes)).convert("RGB")


def _build_text_and_spans(
    tokens: list[OcrToken],
) -> tuple[str, list[TokenSpan]]:
    ordered_tokens = sorted(
        tokens,
        key=lambda token: (
            token.rect.y0,
            token.rect.x0,
        ),
    )

    parts: list[str] = []
    spans: list[TokenSpan] = []
    cursor = 0

    for index, token in enumerate(ordered_tokens):
        if index > 0:
            parts.append("\n")
            cursor += 1

        start = cursor
        parts.append(token.text)
        cursor += len(token.text)
        end = cursor

        spans.append(
            TokenSpan(
                start=start,
                end=end,
                token=token,
            )
        )

    return "".join(parts), spans


def _redact_rendered_page_image(
    image: Image.Image,
    ocr_text: str,
    entities: list[PiiEntity],
    token_spans: list[TokenSpan],
    form_layout: OcrFormLayoutAnalyzer,
    table_layout: OcrTableLayoutAnalyzer | None = None,
) -> int:
    draw = ImageDraw.Draw(image)

    tokens = [span.token for span in token_spans]

    if table_layout is None:
        table_layout = OcrTableLayoutAnalyzer()

    rects: list[fitz.Rect] = []

    # 1. Formularios etiqueta → valor.
    form_rects = form_layout.rects_for_form_values(tokens)
    rects.extend(form_rects)

    # 2. Tablas OCR.
    table_rects = table_layout.rects_for_tables(tokens)
    rects.extend(table_rects)

    covered_rects = form_rects + table_rects

    # 3. PII genérica en texto libre.
    # Ya no la apagamos en páginas form-like; solo evitamos duplicados y etiquetas.
    for entity in entities:
        entity_text = ocr_text[entity.start : entity.end]

        if "\n" in entity_text:
            continue

        rects.extend(
            _entity_rects_from_token_spans(
                entity=entity,
                token_spans=token_spans,
                form_layout=form_layout,
                existing_rects=covered_rects,
            )
        )

    merged_rects = form_layout.merge_close_rects(rects)

    for rect in merged_rects:
        draw.rectangle(
            [
                int(rect.x0),
                int(rect.y0),
                int(rect.x1),
                int(rect.y1),
            ],
            fill="black",
        )

    signature_rect = detect_signature_rect(
        image=image,
        tokens=tokens,
    )

    if signature_rect is not None:
        draw.rectangle(
            [
                int(signature_rect.x0),
                int(signature_rect.y0),
                int(signature_rect.x1),
                int(signature_rect.y1),
            ],
            fill="black",
        )

    visual_code_rects = detect_visual_code_rects(image)

    for rect in visual_code_rects:
        draw.rectangle(
            [
                int(rect.x0),
                int(rect.y0),
                int(rect.x1),
                int(rect.y1),
            ],
            fill="black",
        )

    return (
        len(merged_rects)
        + (1 if signature_rect is not None else 0)
        + len(visual_code_rects)
    )


def _entity_rects_from_token_spans(
    entity: PiiEntity,
    token_spans: list[TokenSpan],
    form_layout: OcrFormLayoutAnalyzer,
    existing_rects: list[fitz.Rect],
) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []

    for span in token_spans:
        token = span.token

        if span.end <= entity.start or span.start >= entity.end:
            continue

        if _should_skip_entity_token(
            text=token.text,
            form_layout=form_layout,
        ):
            continue

        overlap_start = max(entity.start, span.start)
        overlap_end = min(entity.end, span.end)

        local_start = overlap_start - span.start
        local_end = overlap_end - span.start

        rect = rect_for_token_range(
            token=token,
            local_start=local_start,
            local_end=local_end,
        )

        if rect is None:
            continue

        if _rect_center_is_inside_any(rect, existing_rects):
            continue

        rects.append(rect)

    return rects


def _should_skip_entity_token(
    text: str,
    form_layout: OcrFormLayoutAnalyzer,
) -> bool:
    normalized = normalize_text(text).strip(":")

    if not normalized:
        return True

    if form_layout.is_label_only(text):
        return True

    if form_layout.is_safe_static_text(text):
        return True
    
    if _looks_like_safe_ocr_title_or_boilerplate(text):
        return True

    skip_exact_values = {
        "firma",
        "fecha",
        "formulario",
        "formulario de alta",
        "datos ficticios",
        "documento sintetico para pruebas",
        "documento sintético para pruebas",
    }

    if normalized in skip_exact_values:
        return True

    skip_prefixes = (
        "fecha ",
        "documento sintetico",
        "documento sintético",
        "ocumiento",
        "sinterico",
        "sintetico",
    )

    return normalized.startswith(skip_prefixes)


def _rect_center_is_inside_any(
    rect: fitz.Rect,
    containers: list[fitz.Rect],
) -> bool:
    if not containers:
        return False

    center_x = (rect.x0 + rect.x1) / 2
    center_y = (rect.y0 + rect.y1) / 2

    for container in containers:
        expanded = padded_rect(container, padding_x=3, padding_y=3)

        if (
            expanded.x0 <= center_x <= expanded.x1
            and expanded.y0 <= center_y <= expanded.y1
        ):
            return True

    return False


def _append_image_as_pdf_page(
    output_doc: fitz.Document,
    image: Image.Image,
    source_page_rect: fitz.Rect,
) -> None:
    buffer = BytesIO()

    # PNG evita artefactos de compresión sobre las cajas negras.
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    page = output_doc.new_page(
        width=source_page_rect.width,
        height=source_page_rect.height,
    )

    page.insert_image(
        page.rect,
        stream=image_bytes,
    )

def _looks_like_safe_ocr_title_or_boilerplate(text: str) -> bool:
    normalized = normalize_text(text).strip(":;,. ")

    if not normalized:
        return True

    # Si contiene un patrón sensible claro, no se protege.
    if OCR_SENSITIVE_PATTERN_RE.search(text):
        return False

    words = normalized.split()

    if len(words) < 2:
        return False

    if any(keyword in normalized for keyword in OCR_TITLE_KEYWORDS):
        return True

    # Títulos OCR típicos en mayúsculas sin números ni PII.
    letters = [char for char in normalized if char.isalpha()]
    digits = [char for char in normalized if char.isdigit()]

    if len(words) >= 3 and letters and not digits:
        uppercase_ratio = sum(1 for char in text if char.isupper()) / max(
            1,
            sum(1 for char in text if char.isalpha()),
        )

        if uppercase_ratio >= 0.65:
            return True

    return False