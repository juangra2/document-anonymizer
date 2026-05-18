from dataclasses import dataclass
from pathlib import Path

import fitz

from app.core.config import settings
from app.core.errors import (
    EmptyPdfError,
    EncryptedPdfError,
    TooManyPagesError,
)


@dataclass(frozen=True)
class PdfPageInspection:
    page_index: int
    selectable_text_chars: int
    image_count: int
    drawing_count: int
    page_type: str  # "digital", "scanned", "hybrid", "blank"


@dataclass(frozen=True)
class PdfInspection:
    pages: int
    selectable_text_chars: int
    document_type: str  # "pdf_digital", "pdf_scanned", "pdf_hybrid", "pdf_empty"
    page_inspections: list[PdfPageInspection]


def inspect_pdf_for_phase1(input_path: Path) -> PdfInspection:
    doc = fitz.open(input_path)

    try:
        if doc.needs_pass:
            raise EncryptedPdfError("Encrypted PDFs are not supported.")

        pages = len(doc)

        if pages == 0:
            raise EmptyPdfError("PDF has no pages.")

        if pages > settings.max_pdf_pages:
            raise TooManyPagesError(
                f"PDF has {pages} pages. Maximum allowed is {settings.max_pdf_pages}."
            )

        total_selectable_text_chars = 0
        page_inspections: list[PdfPageInspection] = []

        for page_index, page in enumerate(doc):
            page_text_chars = len(page.get_text("text").strip())
            image_count = len(page.get_images(full=True))
            drawing_count = len(page.get_drawings())

            total_selectable_text_chars += page_text_chars

            has_text = page_text_chars >= settings.min_digital_text_chars
            has_images = image_count > 0
            has_drawings = drawing_count > 0

            if has_text and has_images:
                page_type = "hybrid"
            elif has_text:
                page_type = "digital"
            elif has_images or has_drawings:
                page_type = "scanned"
            else:
                page_type = "blank"

            page_inspections.append(
                PdfPageInspection(
                    page_index=page_index,
                    selectable_text_chars=page_text_chars,
                    image_count=image_count,
                    drawing_count=drawing_count,
                    page_type=page_type,
                )
            )

        page_types = {page.page_type for page in page_inspections}

        if page_types == {"blank"}:
            raise EmptyPdfError(
                "PDF has no detectable content or selectable text."
            )

        if page_types == {"digital"}:
            document_type = "pdf_digital"
        elif page_types <= {"scanned", "blank"}:
            document_type = "pdf_scanned"
        else:
            document_type = "pdf_hybrid"

        return PdfInspection(
            pages=pages,
            selectable_text_chars=total_selectable_text_chars,
            document_type=document_type,
            page_inspections=page_inspections,
        )

    finally:
        doc.close()