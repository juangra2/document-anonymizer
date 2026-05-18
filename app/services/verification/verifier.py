"""DocumentVerifier — orchestrates all four verification layers.

Usage
─────
    verifier = DocumentVerifier(analyzer, ocr_engine, segmenter)
    result   = verifier.verify(output_pdf_path, mode="strict")

    if not result.passed:
        raise RedactionVerificationError(...)
    if result.critical_pages:
        report.manual_review_required = True
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.ocr.base import OcrEngine
from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification.layers import (
    check_binary,
    check_metadata,
    check_ocr,
    check_textual,
)
from app.services.verification.models import (
    DocumentVerificationResult,
    LayerResult,
    PageVerificationResult,
    VerificationLayer,
)
from app.services.verification.scorer import compute_page_risk, risk_level

logger = logging.getLogger(__name__)


class DocumentVerifier:
    """Runs the configured verification layers on a redacted PDF.

    Layers run per page: Textual, OCR (if enabled).
    Layers run once per document: Metadata, Binary.
    """

    def __init__(
        self,
        analyzer: PiiAnalyzer,
        ocr_engine: OcrEngine,
        segmenter: OcrPageSegmenter,
    ) -> None:
        self._analyzer = analyzer
        self._ocr_engine = ocr_engine
        self._segmenter = segmenter

    def verify(
        self,
        pdf_path: Path,
        mode: str = "strict",
    ) -> DocumentVerificationResult:
        """Run all configured layers and return a complete verification result."""
        import fitz

        layers_cfg = self._active_layers(mode)
        logger.debug(
            "Verification starting: %s | mode=%s | layers=%s",
            pdf_path.name, mode, [l.value for l in layers_cfg],
        )

        doc = fitz.open(pdf_path)
        page_count = doc.page_count
        doc.close()

        # --- Per-page layers (Textual, OCR) ---
        page_results: list[PageVerificationResult] = []

        for page_index in range(page_count):
            page_layer_results: list[LayerResult] = []

            if VerificationLayer.TEXTUAL in layers_cfg:
                try:
                    r = check_textual(pdf_path, page_index, self._analyzer, mode)
                    page_layer_results.append(r)
                except Exception as exc:
                    logger.warning("Textual layer error page %d: %s", page_index, exc)

            if VerificationLayer.OCR in layers_cfg:
                try:
                    r = check_ocr(
                        pdf_path, page_index,
                        self._ocr_engine, self._segmenter,
                        self._analyzer, mode,
                        dpi=settings.ocr_pdf_render_dpi,
                    )
                    page_layer_results.append(r)
                except Exception as exc:
                    logger.warning("OCR layer error page %d: %s", page_index, exc)

            aggregate = compute_page_risk(page_layer_results)
            requires_review = aggregate >= settings.verify_risk_threshold_review

            page_results.append(PageVerificationResult(
                page_index=page_index,
                layer_results=page_layer_results,
                aggregate_risk_score=round(aggregate, 4),
                requires_review=requires_review,
            ))

        # --- Document-level layers (Metadata, Binary) ---
        doc_layer_results: list[LayerResult] = []

        if VerificationLayer.METADATA in layers_cfg:
            try:
                doc_layer_results.append(check_metadata(pdf_path, self._analyzer))
            except Exception as exc:
                logger.warning("Metadata layer error: %s", exc)

        if VerificationLayer.BINARY in layers_cfg:
            try:
                doc_layer_results.append(check_binary(pdf_path, self._analyzer))
            except Exception as exc:
                logger.warning("Binary layer error: %s", exc)

        # --- Aggregate ---
        page_risks = [pr.aggregate_risk_score for pr in page_results]
        doc_risks  = [lr.risk_score for lr in doc_layer_results]
        all_risks  = page_risks + doc_risks
        overall    = round(max(all_risks) if all_risks else 0.0, 4)

        critical_pages = [
            pr.page_index for pr in page_results if pr.requires_review
        ]

        passed = overall < settings.verify_risk_threshold_reject

        entities_by_layer: dict[str, int] = {}
        total = 0
        for lr in [lr for pr in page_results for lr in pr.layer_results] + doc_layer_results:
            key = lr.layer.value
            entities_by_layer[key] = entities_by_layer.get(key, 0) + lr.entity_count
            total += lr.entity_count

        layers_run = sorted(
            {l.value for l in layers_cfg},
            key=lambda v: list(VerificationLayer).index(VerificationLayer(v)),
        )

        result = DocumentVerificationResult(
            page_results=page_results,
            document_layer_results=doc_layer_results,
            overall_risk_score=overall,
            risk_level=risk_level(overall),
            critical_pages=critical_pages,
            passed=passed,
            total_residual_entities=total,
            entities_by_layer=entities_by_layer,
            layers_run=layers_run,
        )

        logger.info(
            "Verification done: risk=%.3f level=%s critical_pages=%s passed=%s",
            overall, result.risk_level, critical_pages, passed,
        )
        return result

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _active_layers(mode: str) -> set[VerificationLayer]:
        """Return the set of layers to run for the given redaction mode."""
        raw = (
            settings.verify_layers_strict
            if mode == "strict"
            else settings.verify_layers_balanced
        )
        layers: set[VerificationLayer] = set()
        for name in raw.split(","):
            name = name.strip().lower()
            try:
                layers.add(VerificationLayer(name))
            except ValueError:
                logger.warning("Unknown verification layer %r in config — skipped", name)

        # OCR layer is opt-in regardless of mode (it doubles processing time)
        if not settings.verify_ocr_layer_enabled:
            layers.discard(VerificationLayer.OCR)

        return layers
