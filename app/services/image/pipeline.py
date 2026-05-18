from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import perf_counter

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
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity
from app.services.ocr.signature_detector import detect_signature_rect


@dataclass
class TokenSpan:
    start: int
    end: int
    token: OcrToken


class ImageAnonymizationPipeline:
    def __init__(self) -> None:
        self._analyzer = PiiAnalyzer()
        self._ocr_engine = EasyOcrEngine()
        self._form_layout = OcrFormLayoutAnalyzer()

    def process(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str = "strict",
    ) -> ProcessingReport:
        started = perf_counter()

        ocr_page = self._ocr_engine.analyze_image(
            image_path=input_path,
            page_index=0,
        )

        text, token_spans = self._build_text_and_spans(ocr_page.tokens)
        entities = self._analyzer.analyze(text, mode=mode)

        redacted_count = self._redact_image(
            input_path=input_path,
            output_pdf_path=output_path,
            ocr_text=text,
            entities=entities,
            token_spans=token_spans,
        )

        confidence_avg = ocr_page.avg_confidence
        warnings: list[str] = []
        manual_review_required = False

        if confidence_avg is None:
            warnings.append("OCR did not detect any text.")
            manual_review_required = True
        elif confidence_avg < 0.75:
            warnings.append("Low OCR confidence. Manual review recommended.")
            manual_review_required = True

        report = ProcessingReport(
            job_id=job_id,
            status="completed_with_warnings" if warnings else "completed",
            document_type="image",
            pages=1,
            selectable_text_chars=0,
            entities_detected=len(entities),
            entities_redacted=redacted_count,
            entities_by_type=self._analyzer.count_by_type(entities),
            residual_entities=0,
            residual_entities_by_type={},
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
                pages_ocr_processed=0,
                images_processed=1,
                ocr_confidence_avg=confidence_avg,
                low_confidence_pages=[0]
                if confidence_avg is not None and confidence_avg < 0.75
                else [],
                second_pass_verification=False,
            ),
        )

        report.processing_ms = int((perf_counter() - started) * 1000)

        return report

    @staticmethod
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

    def _redact_image(
        self,
        input_path: Path,
        output_pdf_path: Path,
        ocr_text: str,
        entities: list[PiiEntity],
        token_spans: list[TokenSpan],
    ) -> int:
        image = Image.open(input_path).convert("RGB")
        draw = ImageDraw.Draw(image)

        tokens = [span.token for span in token_spans]
        signature_rect = detect_signature_rect(
            image=image,
            tokens=tokens,
        )
        rects: list[fitz.Rect] = []

        # 1. Capa principal para formularios OCR:
        #    etiqueta izquierda → valor derecha.
        form_rects = self._form_layout.rects_for_form_values(tokens)
        rects.extend(form_rects)

        is_form_like = self._form_layout.has_form_layout(tokens)

        # 2. Capa genérica para PII en texto libre.
        #    En formularios etiquetados la desactivamos para evitar cajas raras
        #    en "Fecha", pie de página, títulos o texto residual OCR.
        if not is_form_like:
            for entity in entities:
                entity_text = ocr_text[entity.start : entity.end]

                if "\n" in entity_text:
                    continue

                entity_rects = self._entity_rects_from_token_spans(
                    entity=entity,
                    token_spans=token_spans,
                    existing_rects=form_rects,
                )

                rects.extend(entity_rects)

        merged_rects = self._form_layout.merge_close_rects(rects)

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

        self._save_image_as_pdf(
            image=image,
            output_pdf_path=output_pdf_path,
        )

        return len(merged_rects) + (1 if signature_rect is not None else 0)

    def _entity_rects_from_token_spans(
        self,
        entity: PiiEntity,
        token_spans: list[TokenSpan],
        existing_rects: list[fitz.Rect],
    ) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []

        for span in token_spans:
            token = span.token

            if span.end <= entity.start or span.start >= entity.end:
                continue

            if self._should_skip_entity_token(token.text):
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

            if self._rect_center_is_inside_any(rect, existing_rects):
                continue

            rects.append(rect)

        return rects

    def _should_skip_entity_token(
        self,
        text: str,
    ) -> bool:
        normalized = normalize_text(text).strip(":")

        if not normalized:
            return True

        if self._form_layout.is_label_only(text):
            return True

        if self._form_layout.is_safe_static_text(text):
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

        # Evita que la capa PII genérica tape la fecha del formulario,
        # pie de página o textos residuales de baja calidad.
        skip_prefixes = (
            "fecha ",
            "documento sintetico",
            "documento sintético",
            "ocumiento",
            "sinterico",
            "sintetico",
        )

        if normalized.startswith(skip_prefixes):
            return True

        return False

    @staticmethod
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

    @staticmethod
    def _save_image_as_pdf(
        image: Image.Image,
        output_pdf_path: Path,
    ) -> None:
        width, height = image.size

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        image_bytes = buffer.getvalue()

        doc = fitz.open()
        page = doc.new_page(
            width=width,
            height=height,
        )

        page.insert_image(
            fitz.Rect(0, 0, width, height),
            stream=image_bytes,
        )

        doc.set_metadata({})

        doc.save(
            output_pdf_path,
            garbage=4,
            deflate=True,
            clean=True,
            incremental=False,
        )

        doc.close()