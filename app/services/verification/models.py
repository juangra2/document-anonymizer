"""Data models for the 4-layer post-redaction verification system."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.services.pii.models import PiiEntity


class VerificationLayer(str, Enum):
    """The four independent verification layers run after redaction."""

    TEXTUAL  = "textual"   # PyMuPDF text extraction → PII analysis
    OCR      = "ocr"       # Render page image → OCR → PII analysis
    METADATA = "metadata"  # PDF metadata / XMP / AcroForm / attachments
    BINARY   = "binary"    # Raw byte scan for PII patterns


# Contribution weight of each layer to the overall risk score.
# Higher = more reliable signal; lower = more false-positive-prone.
LAYER_WEIGHTS: dict[VerificationLayer, float] = {
    VerificationLayer.TEXTUAL:  1.0,
    VerificationLayer.OCR:      0.9,
    VerificationLayer.METADATA: 0.7,
    VerificationLayer.BINARY:   0.5,
}

# Number of entities that saturates a layer's risk score to 1.0.
# Keeps the score bounded even when many entities are found.
LAYER_SATURATION: int = 4


@dataclass
class LayerResult:
    """Result of running one verification layer."""

    layer: VerificationLayer
    entities_found: list[PiiEntity]
    # Pre-computed weighted risk contribution for this layer [0.0–1.0].
    risk_score: float
    processing_ms: int
    warnings: list[str] = field(default_factory=list)

    @property
    def entity_count(self) -> int:
        return len(self.entities_found)


@dataclass
class PageVerificationResult:
    """Aggregated verification results for one page (Textual + OCR layers)."""

    page_index: int
    layer_results: list[LayerResult]
    # Weighted combination of all layer risk scores [0.0–1.0].
    aggregate_risk_score: float
    # True when aggregate_risk_score >= configured review threshold.
    requires_review: bool


@dataclass
class DocumentVerificationResult:
    """Complete verification result for the redacted document.

    Per-page layers (Textual, OCR) live in ``page_results``.
    Document-level layers (Metadata, Binary) live in ``document_layer_results``
    because they apply to the PDF as a whole, not to individual pages.
    """

    page_results: list[PageVerificationResult]
    document_layer_results: list[LayerResult]

    # Max of all page risk scores and document layer risk scores [0.0–1.0].
    overall_risk_score: float
    # Literal: "none" / "low" / "medium" / "high"
    risk_level: str
    # Page indices whose risk score >= review threshold.
    critical_pages: list[int]
    # True when overall_risk_score < configured reject threshold.
    passed: bool

    total_residual_entities: int
    entities_by_layer: dict[str, int]
    layers_run: list[str]
