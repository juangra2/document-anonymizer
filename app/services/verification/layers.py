"""The four post-redaction verification layers.

Each function returns a LayerResult for the given PDF and page (or the whole
document for the Metadata and Binary layers, which are page-agnostic).

Layer summary
─────────────
1. Textual  : PyMuPDF text extraction → PII analyzer. Fast, reliable.
2. OCR      : Render page to image → OCR engine → PII analyzer. Catches
              visually-present PII that is not selectable text. Slow.
3. Metadata : Inspect PDF Info dict, XMP blob, AcroForm field values,
              annotation contents, embedded files, and JavaScript presence.
4. Binary   : Scan raw PDF bytes with high-confidence PII regex patterns.
              Catches residue in uncompressed streams not visible via text
              extraction. Higher false-positive rate than other layers.
"""
from __future__ import annotations

import re
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import fitz

from app.services.layout.zone_config import ALL_PII_TYPES
from app.services.pii.analyzer import DIRECT_REGEX_PATTERNS, PiiAnalyzer
from app.services.pii.models import PiiEntity
from app.services.pdf.limpieza import jpeg_con_metadatos
from app.services.verification.models import LayerResult, VerificationLayer
from app.services.verification.scorer import compute_layer_score
from app.services.storage.temp_storage import scratch_dir

# Types the system redacts.  Verification must only flag residual PII of these
# types; LOCATION/ORGANIZATION are detected but never redacted, so counting them
# would inflate the residual risk on every document.
_REDACTABLE_TYPES = set(ALL_PII_TYPES)

if TYPE_CHECKING:
    from app.services.ocr.base import OcrEngine
    from app.services.layout.segmenters import OcrPageSegmenter


# ---------------------------------------------------------------------------
# Layer 1 — Textual
# ---------------------------------------------------------------------------

_ESPACIOS_RE = re.compile(r"[ \t]{2,}")

# spaCy rechaza mas de un millon de caracteres; se analiza por debajo, en trozos.
_MAX_TEXTO_ANALISIS = 500_000


def analizar_por_trozos(analyzer: PiiAnalyzer, texto: str, mode: str,
                        maximo: int = _MAX_TEXTO_ANALISIS) -> list[PiiEntity]:
    """analyzer.analyze en trozos de como mucho `maximo` caracteres, cortados en
    un salto de linea, con las posiciones devueltas sobre el texto entero."""
    if len(texto) <= maximo:
        return analyzer.analyze(texto, mode=mode)
    entidades: list[PiiEntity] = []
    inicio = 0
    while inicio < len(texto):
        fin = min(len(texto), inicio + maximo)
        if fin < len(texto):
            corte = texto.rfind("\n", inicio, fin)
            if corte > inicio:
                fin = corte + 1
        for e in analyzer.analyze(texto[inicio:fin], mode=mode):
            entidades.append(replace(e, start=e.start + inicio, end=e.end + inicio))
        inicio = fin
    return entidades


def _entidades_de_la_pagina(page: fitz.Page, analyzer: PiiAnalyzer, mode: str) -> list[PiiEntity]:
    """La pagina ENTERA, con todos los tipos, y los mismos filtros que el tachado.

    Sin limitar tipos por zona: las fugas tipicas (telefonos y direcciones en
    las franjas de cabecera y pie, nombres en el pie, el texto del QR) estan
    justo en esas limitaciones, y el verificador las ve porque no las tiene.
    Pero con el MISMO texto que el tachado (las lineas en su orden): ordenada
    con sort=True, la pagina junta palabras de celdas distintas que el detector
    de nombres tomaria por un nombre.  Y con el mismo filtro institucional: el contacto del membrete no
    es un residuo.  Los filtros de etiquetas y de nombres estan dentro del
    analizador, que es el mismo.
    """
    from app.services.layout.segmenters import DigitalPageSegmenter
    from app.services.pdf.extractor import extract_page_text
    from app.services.pii.institution_filter import filter_institutional

    page_text = extract_page_text(page, page.number or 0)
    texto = page_text.text
    if not texto.strip():
        return []
    entidades = analizar_por_trozos(analyzer, texto, mode)
    sin_girar = page.rect * page.derotation_matrix
    sin_girar.normalize()
    zonas = DigitalPageSegmenter().segment(page_text, sin_girar)
    # Como en el tachado: donde hay un nombre de persona, el contacto es suyo.
    personas = [(e.start, e.end) for e in entidades if e.entity_type == "PERSON"]
    apartadas = filter_institutional(texto, zonas, entidades, person_spans=personas)
    return [e for i, e in enumerate(entidades) if i not in apartadas]


def check_textual(
    pdf_path: Path,
    page_index: int,
    analyzer: PiiAnalyzer,
    mode: str,
    clip: fitz.Rect | None = None,
) -> LayerResult:
    """Run PII analysis on selectable text extracted from one page (or only
    from `clip`, in page.rect coordinates: the title block of a plan page)."""
    started = perf_counter()

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        if clip is not None:
            # Cajetin de un plano: solo ese recorte.  sort=True rellena con
            # espacios los huecos entre fragmentos (69.000 caracteres reales de
            # un plano se convertian en 20 millones): se colapsan.
            recorte = clip * page.derotation_matrix
            text = _ESPACIOS_RE.sub(" ", page.get_text("text", sort=True, clip=recorte))
            entities = analizar_por_trozos(analyzer, text, mode) if text.strip() else []
        else:
            entities = _entidades_de_la_pagina(page, analyzer, mode)
    finally:
        doc.close()

    entities = [e for e in entities if e.entity_type in _REDACTABLE_TYPES]

    return LayerResult(
        layer=VerificationLayer.TEXTUAL,
        entities_found=entities,
        risk_score=compute_layer_score(VerificationLayer.TEXTUAL, entities),
        processing_ms=int((perf_counter() - started) * 1000),
    )


# ---------------------------------------------------------------------------
# Layer 2 — OCR
# ---------------------------------------------------------------------------

def check_ocr(
    pdf_path: Path,
    page_index: int,
    ocr_engine: "OcrEngine",
    segmenter: "OcrPageSegmenter",
    analyzer: PiiAnalyzer,
    mode: str,
    dpi: int = 200,
    clip: fitz.Rect | None = None,
) -> LayerResult:
    """Render the page (or only `clip`), run OCR, then PII-analyse each zone's text."""
    from app.services.layout.zone_config import get_detector_config
    from app.services.layout.models import ZoneType

    started = perf_counter()
    warnings: list[str] = []

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, clip=clip)
        image_bytes = pix.tobytes("png")
    finally:
        doc.close()

    from PIL import Image
    image = Image.open(BytesIO(image_bytes)).convert("RGB")

    with scratch_dir(prefix="verify_ocr_") as tmp:
        img_path = Path(tmp) / "page.png"
        image.save(img_path)
        ocr_page = ocr_engine.analyze_image(img_path, page_index)

    if ocr_page.avg_confidence is not None and ocr_page.avg_confidence < 0.60:
        warnings.append(
            f"Low OCR confidence on page {page_index + 1} "
            f"({ocr_page.avg_confidence:.2f}); OCR layer result may be unreliable."
        )

    # Segment without image (no SIGNATURE/QR needed for verification)
    zoned_page = segmenter.segment(ocr_page)

    entities: list[PiiEntity] = []
    for zone in zoned_page.zones:
        if not zone.text.strip():
            continue
        cfg = get_detector_config(zone.zone_type)
        if cfg.entity_types is None:
            continue  # auto-redact zone — should already be gone
        local = analyzer.analyze(zone.text, mode=mode)
        allowed = set(cfg.entity_types)
        entities.extend(e for e in local if e.entity_type in allowed)

    return LayerResult(
        layer=VerificationLayer.OCR,
        entities_found=entities,
        risk_score=compute_layer_score(VerificationLayer.OCR, entities),
        processing_ms=int((perf_counter() - started) * 1000),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Layer 3 — Metadata  (document-level)
# ---------------------------------------------------------------------------

# PDF Info dict keys that may contain personal data
_SENSITIVE_META_KEYS = {"author", "title", "subject", "keywords", "creator"}

# Minimum length for a metadata value to bother analysing
_META_MIN_LEN = 3


def check_metadata(
    pdf_path: Path,
    analyzer: PiiAnalyzer,
) -> LayerResult:
    """Lo que no es contenido de pagina y no deberia estar en la salida.

    El PDF entregado no lleva nada oculto ni interactivo (ver pdf/limpieza.py).
    Esta capa lo comprueba de forma independiente del tratamiento: diccionario de
    informacion, XMP (documento, pagina, imagen), anotaciones (tipo y autor),
    campos de formulario, marcadores, JavaScript, adjuntos, metadatos dentro de
    las imagenes JPEG y versiones anteriores por guardado incremental.  El texto
    que encuentra se analiza; ningun valor se copia a los avisos.

    "format" no se mira: no es un metadato guardado, es la version del PDF que
    PyMuPDF deduce de la cabecera ("PDF 1.7").
    """
    started = perf_counter()
    warnings: list[str] = []
    entities: list[PiiEntity] = []

    def analizar(texto: str) -> None:
        if texto and len(texto) >= _META_MIN_LEN:
            entities.extend(analyzer.analyze(texto, mode="strict"))

    doc = fitz.open(pdf_path)
    try:
        # 1. Diccionario de informacion
        for key, value in (doc.metadata or {}).items():
            if key in ("format", "encryption") or not value or not str(value).strip():
                continue
            if key.lower() in _SENSITIVE_META_KEYS:
                analizar(value)
            warnings.append(f"Metadata field '{key}' is not empty ({len(value)} characters).")

        # 2. XMP del documento
        xmp = doc.get_xml_metadata() or ""
        if xmp.strip():
            warnings.append(f"XMP metadata blob present ({len(xmp)} bytes)")
            analizar(xmp)

        # 3. Anotaciones y campos
        for page in doc:
            for annot in page.annots() or []:
                info = annot.info or {}
                autor = "with author" if (info.get("title") or "").strip() else "without author"
                warnings.append(
                    f"Annotation '{annot.type[1]}' ({autor}) on page {page.number + 1}.")
                analizar(info.get("content", "") or "")
                analizar(info.get("title", "") or "")
            for widget in page.widgets() or []:
                warnings.append(f"Form field '{widget.field_name}' on page {page.number + 1}.")
                analizar(widget.field_value or "")

        # 4. Marcadores
        toc = doc.get_toc()
        if toc:
            warnings.append(f"{len(toc)} bookmark(s) remain in the document.")
            for _nivel, titulo, *_ in toc:
                analizar(titulo)

        # 5. Adjuntos
        embedded_count = doc.embfile_count() if hasattr(doc, "embfile_count") else 0
        if embedded_count:
            warnings.append(f"{embedded_count} embedded file(s) remain in the document.")

        # 6. Objetos: JavaScript, XMP de pagina o imagen, metadatos dentro de los JPEG
        js = xmp_objetos = imagenes_con_metadatos = copias_texto = 0
        contenidos = {x for page in doc for x in page.get_contents()}
        for xref in range(1, doc.xref_length()):
            try:
                objeto = doc.xref_object(xref)
            except Exception:
                continue
            if "/ActualText" in objeto or "/StructTreeRoot" in objeto:
                copias_texto += 1
            elif doc.xref_is_stream(xref) and (xref in contenidos or "/Subtype /Form" in objeto):
                try:
                    if b"/ActualText" in (doc.xref_stream(xref) or b""):
                        copias_texto += 1
                except Exception:
                    pass
            if "/JS" in objeto or "/JavaScript" in objeto:
                js += 1
            if "/Metadata" in objeto and xref != doc.pdf_catalog():
                xmp_objetos += 1
            if "/Subtype /Image" in objeto and "/DCTDecode" in objeto:
                try:
                    if jpeg_con_metadatos(doc.xref_stream_raw(xref)):
                        imagenes_con_metadatos += 1
                except Exception:
                    pass
        if js:
            warnings.append(f"JavaScript present in {js} object(s).")
        if xmp_objetos:
            warnings.append(f"XMP metadata in {xmp_objetos} page or image object(s).")
        if copias_texto:
            warnings.append(
                f"Text copies (ActualText or structure tags) in {copias_texto} object(s).")
        if imagenes_con_metadatos:
            warnings.append(
                f"{imagenes_con_metadatos} image(s) carry EXIF/XMP/IPTC or encoder metadata.")

    finally:
        doc.close()

    # 7. Versiones anteriores dentro del fichero
    revisiones = Path(pdf_path).read_bytes().count(b"%%EOF")
    if revisiones > 1:
        warnings.append(f"{revisiones} revisions in the file (incremental save).")

    # Deduplicate entities (same span may appear across multiple checks)
    entities = [e for e in entities if e.entity_type in _REDACTABLE_TYPES]
    entities = _dedupe(entities)

    return LayerResult(
        layer=VerificationLayer.METADATA,
        entities_found=entities,
        risk_score=compute_layer_score(VerificationLayer.METADATA, entities),
        processing_ms=int((perf_counter() - started) * 1000),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Layer 4 — Binary  (document-level)
# ---------------------------------------------------------------------------

# Only high-confidence patterns; lower-confidence ones have too many FP in binary
_BINARY_MIN_SCORE = 0.85

# Minimum printable-character ratio in a 60-byte window around a match
_MIN_PRINTABLE_RATIO = 0.80


# Cadena hexadecimal de PDF, "<8A0D...>", sin confundirla con un diccionario "<<".
_HEX_PDF_RE = re.compile(r"(?<!<)<[0-9A-Fa-f\s]+>(?!>)")
_INICIO_FLUJO_RE = re.compile(r"(?<!end)stream\r?\n")
# Array solo de numeros ("/Widths [278 667 722 611]", "/W [1 [667 722]]"): tres
# anchos seguidos tienen forma de telefono.  Un dato va en una cadena, no suelto.
_ARRAY_NUMERICO_RE = re.compile(r"\[[\d.\s\-\[\]]+\]")


def _apartar_codificado(texto: str) -> str:
    """Sustituye por espacios (mismas posiciones) lo que son bytes codificados:
    las cadenas hexadecimales (el /ID del trailer: 64 cifras aleatorias que a
    veces forman un "telefono"), los arrays solo de numeros (anchos de fuente)
    y el contenido de los flujos con /Filter (ruido comprimido; su texto lo leen
    ya las capas textual y de OCR)."""
    texto = _HEX_PDF_RE.sub(lambda m: " " * len(m.group(0)), texto)
    texto = _ARRAY_NUMERICO_RE.sub(lambda m: " " * len(m.group(0)), texto)
    trozos = []
    pos = 0
    for m in _INICIO_FLUJO_RE.finditer(texto):
        if m.start() < pos:
            continue
        diccionario = texto[texto.rfind("obj", 0, m.start()) + 1:m.start()]
        fin = texto.find("endstream", m.end())
        if fin < 0 or "/Filter" not in diccionario:
            continue
        trozos.append(texto[pos:m.end()])
        trozos.append(" " * (fin - m.end()))
        pos = fin
    trozos.append(texto[pos:])
    return "".join(trozos)


def check_binary(
    pdf_path: Path,
    analyzer: PiiAnalyzer,
) -> LayerResult:
    """Scan raw PDF bytes for high-confidence PII patterns.

    Catches PII that survived in uncompressed object streams or the cross-
    reference table and is therefore invisible to text extraction.
    """
    started = perf_counter()
    warnings: list[str] = []

    raw = pdf_path.read_bytes()
    # latin-1 decodes every byte without errors; patterns work on ASCII subset
    text = _apartar_codificado(raw.decode("latin-1", errors="replace"))

    entities: list[PiiEntity] = []

    for entity_type, pattern, score in DIRECT_REGEX_PATTERNS:
        if score < _BINARY_MIN_SCORE:
            continue

        for match in re.finditer(pattern, text, re.IGNORECASE):
            # Reject match if surrounded by binary garbage
            ctx_start = max(0, match.start() - 30)
            ctx_end = min(len(text), match.end() + 30)
            context = text[ctx_start:ctx_end]
            printable = sum(1 for c in context if c.isprintable() or c in "\r\n\t")
            if printable / max(1, len(context)) < _MIN_PRINTABLE_RATIO:
                continue

            entities.append(PiiEntity(
                entity_type=entity_type,
                start=match.start(),
                end=match.end(),
                # Lower confidence for binary-layer matches
                score=round(score * 0.6, 2),
                source="binary_scan",
            ))

    entities = _dedupe(entities)

    if entities:
        warnings.append(
            "Binary scan found PII-like patterns in raw PDF bytes. "
            "This layer has a higher false-positive rate; manual review is advised."
        )

    return LayerResult(
        layer=VerificationLayer.BINARY,
        entities_found=entities,
        risk_score=compute_layer_score(VerificationLayer.BINARY, entities),
        processing_ms=int((perf_counter() - started) * 1000),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _dedupe(entities: list[PiiEntity]) -> list[PiiEntity]:
    """Remove exact-duplicate entities (same type + span)."""
    seen: set[tuple] = set()
    result: list[PiiEntity] = []
    for e in entities:
        key = (e.entity_type, e.start, e.end)
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result
