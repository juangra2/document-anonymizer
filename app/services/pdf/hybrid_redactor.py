"""Camino hibrido: documentos con paginas digitales y paginas que hay que mirar.

Ya no tiene bucle de OCR propio
-------------------------------
Antes lo tenia, y era una segunda copia del tratamiento de una pagina escaneada
que se habia quedado atras: sin enderezado, sin clasificacion de redaccion total
—una copia de DNI dentro de un expediente no se tachaba entera— y con una
geometria de barras distinta, que las pintaba desplazadas.  Cada mejora del
camino escaneado dejaba al hibrido un paso mas lejos.

Ahora el hibrido es solo el orden de dos pasos:

1. El pase digital sobre el documento entero: redacta la capa de texto de todas
   las paginas, que es exacto.
2. El MISMO bucle que el camino escaneado, sobre el intermedio que deja el paso
   anterior, pasando por OCR solo las paginas que lo necesitan
   (`pagina_necesita_ocr`, criterio de superficie).  Las demas se copian tal cual
   y conservan su texto seleccionable.

Una pagina se trata del mismo modo venga en un documento escaneado o en uno
mixto, y la clasificacion de redaccion total, la parada parcial, los avisos en
vivo y el avance funcionan sobre el documento entero porque es un solo recorrido.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import fitz

from app.schemas.report import ProcessingReport
from app.services import cancellation, progress
from app.services.pdf.full_redaction import classify_pages
from app.services.pdf.inspector import pagina_necesita_ocr
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pdf.scanned_redactor import redact_pdf_scanned
from app.services.pii.analyzer import PiiAnalyzer
from app.services.storage.temp_storage import scratch_dir


def paginas_para_ocr(input_path: Path) -> set[int]:
    """Indices de las paginas que hay que mirar, segun el criterio de superficie."""
    with fitz.open(input_path) as doc:
        return {i for i, page in enumerate(doc) if pagina_necesita_ocr(page)}


def redact_pdf_hybrid_secure(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    collect_entities: bool = False,
    paginas_ocr: set[int] | None = None,
) -> ProcessingReport:
    if paginas_ocr is None:
        paginas_ocr = paginas_para_ocr(input_path)

    # Texto ORIGINAL de cada pagina, para que la clasificacion de redaccion
    # total pueda propagar entre tipos de pagina: el pase digital borra el
    # justificante de una autoliquidacion digital del intermedio.
    with fitz.open(input_path) as doc:
        textos_originales = {i: page.get_text("text") for i, page in enumerate(doc)}
    # Lo que el pase digital va a tachar entero: la misma clasificacion sobre
    # los mismos textos, para que la pasada global no lo cuente dos veces.
    redactadas_previas = classify_pages(
        [textos_originales[i] for i in range(len(textos_originales))]
    )

    with scratch_dir(prefix="pdf_hybrid_") as tmp:
        intermedio = Path(tmp) / "digital_redacted.pdf"

        # El pase digital es rapido y recorre el documento entero: si se
        # cortara a medias, el bucle de despues leeria paginas que no existen.
        # Corre con un receptor de avance desechable: el aviso de avance lo
        # reclama el PRIMERO que llega, y tiene que ser el bucle que va a pasar
        # el OCR, que es la parte lenta.  Si lo reclamara este pase, la barra se
        # quedaria quieta durante todo el OCR.
        with cancellation.without_partial(), progress.track(lambda *_: None):
            digital = redact_pdf_digital(
                input_path=input_path,
                output_path=intermedio,
                job_id=job_id,
                analyzer=analyzer,
                mode=mode,
                collect_entities=collect_entities,
            )

        escaneado = redact_pdf_scanned(
            input_path=intermedio,
            output_path=output_path,
            job_id=job_id,
            analyzer=analyzer,
            mode=mode,
            document_type="pdf_hybrid",
            collect_entities=collect_entities,
            paginas_ocr=paginas_ocr,
            textos_previos=textos_originales,
            redactadas_previas=redactadas_previas,
        )

    # Sin doble recuento: el pase digital cuenta lo que hay en la capa de texto,
    # y cuando el OCR lee una pagina mixta lo hace sobre el intermedio, donde
    # esa capa ya esta tachada.  Cada dato se cuenta en un solo lado.
    avisos = list(digital.warnings)
    for aviso in escaneado.warnings:
        if aviso not in avisos:
            avisos.append(aviso)

    return escaneado.model_copy(update={
        "document_type": "pdf_hybrid",
        "status": "completed_with_warnings" if avisos else "completed",
        "entities_detected": digital.entities_detected + escaneado.entities_detected,
        "entities_redacted": digital.entities_redacted + escaneado.entities_redacted,
        # Se suman: con {**a, **b} el recuento OCR pisaba al digital del mismo tipo.
        "entities_by_type": dict(
            Counter(digital.entities_by_type) + Counter(escaneado.entities_by_type)
        ),
        "structural_redactions_untyped": digital.structural_redactions_untyped,
        "manual_review_required": (
            digital.manual_review_required or escaneado.manual_review_required
        ),
        "warnings": avisos,
        "redaction_strategy": "hybrid_secure_pdf_redaction",
        "detected_entities": (
            list(digital.detected_entities) + list(escaneado.detected_entities)
        ),
    })
