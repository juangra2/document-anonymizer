from __future__ import annotations

from io import BytesIO
from pathlib import Path
from time import perf_counter

import fitz
from PIL import Image, ImageDraw

from app.core.config import settings
from app.schemas.report import OcrInfo, ProcessingLimits, ProcessingReport
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.ocr.factory import get_ocr_engine
from app.services.pdf.scanned_redactor import _collect_redaction_rects
from app.services.pii.analyzer import PiiAnalyzer


class ImageAnonymizationPipeline:
    def __init__(self) -> None:
        self._analyzer = PiiAnalyzer()
        # Use EasyOCR for standalone images: it returns per-word tokens with
        # precise bounding boxes, which the form layout analyzer needs to place
        # redaction boxes accurately.  MinerU returns the form as a single HTML
        # table token, causing alignment drift in the character-to-pixel mapping.
        # Design decision: MinerU is the primary engine for PDF pages (where its
        # structured understanding adds value); EasyOCR handles images.
        from app.services.ocr.easyocr_engine import EasyOcrEngine
        self._ocr_engine = EasyOcrEngine(use_gpu=settings.ocr_use_gpu)
        self._segmenter = OcrPageSegmenter()

    def process(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str = "strict",
    ) -> ProcessingReport:
        started = perf_counter()

        image = Image.open(input_path).convert("RGB")

        ocr_page = self._ocr_engine.analyze_image(
            image_path=input_path,
            page_index=0,
        )

        zoned_page = self._segmenter.segment(ocr_page, image=image)

        rects, entities = _collect_redaction_rects(
            zoned_page=zoned_page,
            analyzer=self._analyzer,
            mode=mode,
        )

        draw = ImageDraw.Draw(image)
        for rect in rects:
            draw.rectangle(
                [int(rect.x0), int(rect.y0), int(rect.x1), int(rect.y1)],
                fill="black",
            )

        self._save_image_as_pdf(image, output_path)

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
            entities_redacted=len(rects),
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
                engine=self._ocr_engine.engine_name,
                pages_ocr_processed=0,
                images_processed=1,
                ocr_confidence_avg=confidence_avg,
                low_confidence_pages=(
                    [0] if confidence_avg is not None and confidence_avg < 0.75 else []
                ),
                second_pass_verification=False,
            ),
        )

        report.processing_ms = int((perf_counter() - started) * 1000)
        return report

    @staticmethod
    def _save_image_as_pdf(image: Image.Image, output_pdf_path: Path) -> None:
        width, height = image.size
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=95)

        doc = fitz.open()
        page = doc.new_page(width=width, height=height)
        page.insert_image(fitz.Rect(0, 0, width, height), stream=buffer.getvalue())
        doc.set_metadata({})
        doc.save(
            output_pdf_path,
            garbage=4,
            deflate=True,
            clean=True,
            incremental=False,
        )
        doc.close()
