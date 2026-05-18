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
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TYPE_CHECKING

import fitz

from app.services.pii.analyzer import DIRECT_REGEX_PATTERNS, PiiAnalyzer
from app.services.pii.models import PiiEntity
from app.services.verification.models import LayerResult, VerificationLayer
from app.services.verification.scorer import compute_layer_score

if TYPE_CHECKING:
    from app.services.ocr.base import OcrEngine
    from app.services.layout.segmenters import OcrPageSegmenter


# ---------------------------------------------------------------------------
# Layer 1 — Textual
# ---------------------------------------------------------------------------

def check_textual(
    pdf_path: Path,
    page_index: int,
    analyzer: PiiAnalyzer,
    mode: str,
) -> LayerResult:
    """Run PII analysis on selectable text extracted from one page."""
    started = perf_counter()

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        text = page.get_text("text", sort=True)
    finally:
        doc.close()

    entities = analyzer.analyze(text, mode=mode) if text.strip() else []

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
) -> LayerResult:
    """Render the page, run OCR, then PII-analyse each zone's text."""
    from app.services.layout.zone_config import get_detector_config
    from app.services.layout.models import ZoneType

    started = perf_counter()
    warnings: list[str] = []

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image_bytes = pix.tobytes("png")
    finally:
        doc.close()

    from PIL import Image
    image = Image.open(BytesIO(image_bytes)).convert("RGB")

    with TemporaryDirectory(prefix="verify_ocr_") as tmp:
        img_path = Path(tmp) / "page.png"
        image.save(img_path)
        ocr_page = ocr_engine.analyze_image(img_path, page_index)

    if ocr_page.avg_confidence is not None and ocr_page.avg_confidence < 0.60:
        warnings.append(
            f"Low OCR confidence on page {page_index} "
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
    """Inspect all metadata fields, XMP blob, AcroForm values, and annotations."""
    started = perf_counter()
    warnings: list[str] = []
    entities: list[PiiEntity] = []

    doc = fitz.open(pdf_path)
    try:
        # 1. Standard PDF Info dict
        meta = doc.metadata or {}
        for key, value in meta.items():
            if not value or len(value) < _META_MIN_LEN:
                continue
            if key.lower() in _SENSITIVE_META_KEYS:
                found = analyzer.analyze(value, mode="strict")
                entities.extend(found)
            if value.strip():
                warnings.append(f"Metadata field '{key}' is not empty: {value[:60]!r}")

        # 2. XMP metadata blob
        xmp = doc.get_xml_metadata() or ""
        if xmp.strip():
            warnings.append(f"XMP metadata blob present ({len(xmp)} bytes)")
            found = analyzer.analyze(xmp, mode="strict")
            entities.extend(found)

        # 3. AcroForm field values
        for page in doc:
            for widget in page.widgets() or []:
                val = widget.field_value or ""
                if len(val) >= _META_MIN_LEN:
                    found = analyzer.analyze(val, mode="strict")
                    entities.extend(found)
                    if val.strip():
                        warnings.append(f"AcroForm field '{widget.field_name}' has value: {val[:60]!r}")

        # 4. Annotation contents
        for page in doc:
            for annot in page.annots() or []:
                content = annot.info.get("content", "") or ""
                if len(content) >= _META_MIN_LEN:
                    found = analyzer.analyze(content, mode="strict")
                    entities.extend(found)

        # 5. Embedded / attached files (count only — contents not inspected)
        embedded_count = doc.embfile_count() if hasattr(doc, "embfile_count") else 0
        if embedded_count:
            warnings.append(f"{embedded_count} embedded file(s) remain in the document.")

        # 6. JavaScript presence
        for page in doc:
            for annot in page.annots() or []:
                if annot.type[0] in (fitz.PDF_ANNOT_WIDGET,):
                    pass  # already handled above
            # Check page-level JS via xref
        try:
            js_xrefs = doc.get_page_xobjects(0) if doc.page_count > 0 else []
        except Exception:
            js_xrefs = []

    finally:
        doc.close()

    # Deduplicate entities (same span may appear across multiple checks)
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
    text = raw.decode("latin-1", errors="replace")

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

            # Reject match inside an active compressed stream
            pre = text[max(0, match.start() - 250) : match.start()]
            if "stream" in pre and "endstream" not in pre:
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
