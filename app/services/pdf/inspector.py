from dataclasses import dataclass
from pathlib import Path

import fitz

from app.core.config import settings
from app.core.errors import (
    EmptyPdfError,
    EncryptedPdfError,
    FileValidationError,
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


def cobertura_imagenes(page: fitz.Page) -> float:
    """Fraccion de la pagina cubierta por imagenes que cuentan.

    Suma, no maximo: cuatro miniaturas de un anexo fotografico de 3 % son 12 %
    y hay que mirarlas, aunque ninguna llegue sola.  Solo entran las imagenes
    que cubren al menos `ocr_imagen_minima`, para que los adornos (logos de
    0,38 %) no se acumulen hasta disparar el OCR.  El razonamiento y las
    mediciones estan junto a `ocr_imagen_pagina` en la config.
    """
    area = page.rect.width * page.rect.height
    if area <= 0:
        return 0.0
    total = 0.0
    try:
        imagenes = page.get_images(full=True)
    except Exception:
        return 0.0
    for imagen in imagenes:
        try:
            rects = page.get_image_rects(imagen[0])
        except Exception:
            continue
        for rect in rects:
            visible = rect & page.rect
            if visible.is_empty:
                continue
            fraccion = visible.get_area() / area
            if fraccion >= settings.ocr_imagen_minima:
                total += fraccion
    return min(1.0, total)


def pagina_necesita_ocr(page: fitz.Page) -> bool:
    """¿Hay que mirar los pixeles de esta pagina, o basta con su capa de texto?

    Una pagina sin texto se mira siempre: no tiene nada que conservar.  Una con
    texto solo si sus imagenes cubren una parte suficiente de la pagina como
    para llevar texto que la capa no tiene.  Pasar al OCR una pagina que no lo
    necesita no es gratis: pierde su texto seleccionable y cambia una redaccion
    exacta por otra que no lo es.
    """
    try:
        caracteres = len(page.get_text("text").strip())
    except Exception:
        caracteres = 0
    if caracteres < settings.min_digital_text_chars:
        return True
    return cobertura_imagenes(page) >= settings.ocr_imagen_pagina


def assert_pdf_within_limits(input_path: Path) -> int:
    """Comprueba PÁGINAS y legibilidad antes de encolar.  Devuelve las páginas.

    Abrir el PDF cuesta milisegundos.  Hacerlo al recibirlo evita que un
    documento con contraseña, vacío, dañado o de más páginas de la cuenta se
    descubra cuando le llega el turno, que con la cola por delante puede ser
    horas después.  El tamaño en MB ya se comprueba al escribirlo en disco.
    """
    try:
        doc = fitz.open(input_path)
    except Exception as exc:  # fitz.FileDataError y parientes
        raise FileValidationError(
            "The PDF could not be opened; the file looks damaged."
        ) from exc

    try:
        if doc.needs_pass:
            raise EncryptedPdfError("Encrypted PDFs are not supported.")
        pages = doc.page_count
        if pages == 0:
            raise EmptyPdfError("PDF has no pages.")
        if pages > settings.max_pdf_pages:
            raise TooManyPagesError(
                f"PDF has {pages} pages. Maximum allowed is "
                f"{settings.max_pdf_pages}."
            )
        return pages
    finally:
        doc.close()


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