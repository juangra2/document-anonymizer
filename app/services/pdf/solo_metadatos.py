"""Solo quitar metadatos: el documento sale con su contenido intacto y sin nada oculto.

No se analiza ni se tacha nada.  Se aplica la misma limpieza que al entregar un
documento anonimizado (`limpieza.limpiar_para_entregar`) y, ademas:

* los comentarios (cualquier anotacion que no sea un campo) se eliminan: no se
  integran en la pagina porque aqui no hay analisis que revise lo que traigan;
* los campos de formulario se integran en la pagina, como al anonimizar: lo que
  se ve es contenido y no se toca, solo deja de ser rellenable;
* el texto que no se ve (en blanco sobre blanco, invisible o tapado por una
  imagen) se quita: viaja con el fichero aunque no se vea.  Incluye la capa de
  texto de un escaneado con OCR, que deja de poder buscarse.
"""
from __future__ import annotations

from pathlib import Path
from time import perf_counter

import fitz

from app.schemas.report import (
    CONTENT_NOT_REVIEWED,
    DIGITAL_SIGNATURE_INVALIDATED,
    CodedWarning,
    ProcessingReport,
)
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pdf.limpieza import inventario_metadatos, limpiar_para_entregar
from app.services.pdf.redactor import _es_blanco, cajas_de_letra_ajustadas


def _fondo_blanco(page: fitz.Page, caja: fitz.Rect) -> bool:
    """La zona se pinta toda blanca: el texto blanco de encima no se ve."""
    zona = caja & page.rect
    if zona.is_empty:
        return True
    pix = page.get_pixmap(clip=zona, dpi=72, colorspace=fitz.csGRAY, annots=False)
    return min(pix.samples) >= 242 if pix.samples else True


def texto_oculto(page: fitz.Page) -> list[fitz.Rect]:
    """Rects del texto que no se ve, de cualquier tamaño.

    Invisible (modo 3) o transparente; tapado por una imagen dibujada despues
    (el orden de dibujo es el seqno); o blanco sobre una zona que se pinta
    blanca.  Un trozo que pisa texto visible no se toca: quitarlo se llevaria
    tambien las letras de encima.
    """
    trazas = [t for t in page.get_texttrace() if t["chars"]]
    if not trazas:
        return []
    imagenes = [(i, fitz.Rect(c)) for i, (tipo, c) in enumerate(page.get_bboxlog())
                if tipo == "fill-image"]
    ocultas, visibles = [], []
    for t in trazas:
        caja = fitz.Rect(t["bbox"])
        tapado = any(i > t["seqno"] and abs(caja & img) >= 0.5 * abs(caja)
                     for i, img in imagenes)
        if (t["type"] == 3 or t["opacity"] == 0 or tapado
                or (_es_blanco(t["color"]) and _fondo_blanco(page, caja))):
            ocultas.append(caja)
        else:
            visibles.append(caja)
    return [c for c in ocultas
            if not any(abs(c & v) > 0.2 * min(abs(c), abs(v)) for v in visibles)]


def quitar_texto_oculto(doc: fitz.Document) -> int:
    quitados = 0
    with cajas_de_letra_ajustadas():
        for page in doc:
            rects = texto_oculto(page)
            for r in rects:
                page.add_redact_annot(r, fill=None)
            if rects:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                      graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                      text=fitz.PDF_REDACT_TEXT_REMOVE)
                quitados += len(rects)
    return quitados


def quitar_metadatos(input_path: Path, output_path: Path, job_id: str) -> ProcessingReport:
    empezado = perf_counter()
    inspeccion = inspect_pdf_for_phase1(Path(input_path))
    doc = fitz.open(input_path)
    try:
        quitado = inventario_metadatos(doc)
        firmas = quitado.pop("digital_signatures", 0)
        for page in doc:
            for annot in list(page.annots()):
                page.delete_annot(annot)
        if any(True for page in doc for _ in page.widgets()):
            doc.bake(annots=False, widgets=True)
        oculto = quitar_texto_oculto(doc)
        if oculto:
            quitado["hidden_text"] = oculto
        doc.scrub(attached_files=True, clean_pages=True, embedded_files=True,
                  hidden_text=True, javascript=True, metadata=True, redactions=False,
                  remove_links=True, reset_fields=False, reset_responses=True,
                  thumbnails=True, xml_metadata=True)
        limpiar_para_entregar(doc)
        doc.save(output_path, garbage=4, deflate=True, clean=True, incremental=False)
        paginas = len(doc)
    finally:
        doc.close()

    avisos = [CodedWarning(code=CONTENT_NOT_REVIEWED)]
    if firmas:
        avisos.append(CodedWarning(code=DIGITAL_SIGNATURE_INVALIDATED,
                                   counts={"signatures": firmas}))
    return ProcessingReport(
        job_id=job_id,
        status="completed_with_warnings",
        document_type=inspeccion.document_type,
        treatment="metadata_only",
        pages=paginas,
        selectable_text_chars=inspeccion.selectable_text_chars,
        warnings=["Metadata removed only: the content has NOT been reviewed or anonymized."],
        coded_warnings=avisos,
        metadata_removed={k: v for k, v in quitado.items() if v},
        redaction_strategy="metadata_only",
        processing_ms=int((perf_counter() - empezado) * 1000),
    )
