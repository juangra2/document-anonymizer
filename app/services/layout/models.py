from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

import fitz


class ZoneType(str, Enum):
    """Semantic zones within a document page.

    The pipeline segments each page into these zones before running any PII
    detector. Each zone type carries a different detection policy defined in
    zone_config.py.
    """

    HEADER     = "header"      # Cabecera: nº expediente, organismo, fecha
    FOOTER     = "footer"      # Pie: nº página, referencia, sello
    FORM       = "form"        # Área etiqueta-valor (formularios)
    TABLE      = "table"       # Tabla estructurada
    BODY       = "body"        # Texto libre
    SIGNATURE  = "signature"   # Zona de firma manuscrita
    QR_CODE    = "qr_code"     # Código QR / código de barras
    MARGINALIA = "marginalia"  # Anotaciones marginales
    UNKNOWN    = "unknown"     # No clasificado

    @property
    def auto_redact(self) -> bool:
        """True when the whole zone must be redacted without entity detection."""
        return self in {ZoneType.SIGNATURE, ZoneType.QR_CODE}


@dataclass
class ZoneToken:
    """Minimal, source-agnostic token shared across digital and OCR paths.

    Both WordBox (PyMuPDF) and OcrToken (OCR engines) are converted to this
    representation when they enter the layout layer, so the rest of the
    pipeline is independent of the document source.
    """

    text: str
    rect: fitz.Rect
    confidence: float | None = None  # None for digital-text tokens


@dataclass
class PageZone:
    """A classified region within a page with its tokens and bounding box."""

    zone_type: ZoneType
    rect: fitz.Rect
    text: str
    tokens: list[ZoneToken] = field(default_factory=list)
    confidence: float | None = None  # avg OCR confidence; None for digital
    source: Literal["digital", "ocr"] = "digital"

    @property
    def is_auto_redact(self) -> bool:
        return self.zone_type.auto_redact


@dataclass
class ZonedPage:
    """A document page fully segmented into typed zones.

    Created by the segmenters in layout/segmenters.py and consumed by the
    redactors. The zones list is ordered top-to-bottom, left-to-right.
    """

    page_index: int
    width: float
    height: float
    zones: list[PageZone] = field(default_factory=list)

    def zones_of_type(self, zone_type: ZoneType) -> list[PageZone]:
        return [z for z in self.zones if z.zone_type == zone_type]

    @property
    def auto_redact_zones(self) -> list[PageZone]:
        return [z for z in self.zones if z.is_auto_redact]

    @property
    def full_text(self) -> str:
        """Concatenated text from all zones, separated by newlines."""
        return "\n".join(z.text for z in self.zones if z.text.strip())

    @property
    def avg_confidence(self) -> float | None:
        """Average OCR confidence across all OCR zones; None for digital pages."""
        scores = [z.confidence for z in self.zones if z.confidence is not None]
        if not scores:
            return None
        return sum(scores) / len(scores)


@dataclass
class ZonedDocument:
    """All zoned pages for a single document, plus provenance metadata."""

    pages: list[ZonedPage] = field(default_factory=list)
    document_type: str = "unknown"
    ocr_engine: str | None = None  # populated when OCR was used
