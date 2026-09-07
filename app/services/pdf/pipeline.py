from pathlib import Path
from time import perf_counter

from app.core.config import settings
from app.schemas.report import ProcessingReport, VerificationSummary
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.layout.zone_config import ALL_PII_TYPES
from app.services.ocr.factory import get_ocr_engine
from app.services.pdf.hybrid_redactor import redact_pdf_hybrid_secure
from app.services.pdf.inspector import PdfInspection, inspect_pdf_for_phase1
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pdf.scanned_redactor import redact_pdf_scanned
from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification import DocumentVerifier
from app.services.verification.models import DocumentVerificationResult


class PdfAnonymizationPipeline:
    def __init__(self) -> None:
        self._analyzer = PiiAnalyzer()
        self._ocr_engine = get_ocr_engine()
        self._segmenter = OcrPageSegmenter()
        self._verifier = DocumentVerifier(
            analyzer=self._analyzer,
            ocr_engine=self._ocr_engine,
            segmenter=self._segmenter,
        )

    def process(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str = "strict",
        collect_entities: bool = False,
        disabled_types: frozenset[str] | None = None,
    ) -> ProcessingReport:
        started = perf_counter()
        inspection = inspect_pdf_for_phase1(input_path)

        # Tipos que esta peticion no quiere anonimizar (bloque C).  Se aplica
        # con un clon del analizador que comparte el motor NER ya cargado, de
        # modo que ningun redactor cambia de firma y la verificacion posterior
        # tampoco marca como residual lo que el usuario decidio no anonimizar.
        disabled = frozenset(disabled_types or ())
        base_analyzer = self._analyzer
        base_verifier = self._verifier
        if disabled:
            self._analyzer = base_analyzer.with_disabled_types(disabled)
            self._verifier = DocumentVerifier(
                analyzer=self._analyzer,
                ocr_engine=self._ocr_engine,
                segmenter=self._segmenter,
            )
        try:
            report = self._dispatch(
                input_path, output_path, job_id, mode, inspection, collect_entities,
            )
        finally:
            self._analyzer = base_analyzer
            self._verifier = base_verifier

        report.entity_types_applied = sorted(
            t for t in ALL_PII_TYPES if t not in disabled
        )
        report.entity_types_disabled = sorted(disabled)
        report.redaction_mode = mode
        report.processing_ms = int((perf_counter() - started) * 1000)
        return report

    def _dispatch(
        self, input_path, output_path, job_id, mode, inspection, collect_entities,
    ) -> ProcessingReport:

        if settings.pdf_processing_mode == "raster_ocr":
            report = self._process_pdf_as_raster_ocr(input_path, output_path, job_id, mode, collect_entities)
        elif settings.pdf_processing_mode == "hybrid_secure":
            report = self._process_pdf_hybrid_secure(input_path, output_path, job_id, mode, inspection, collect_entities)
        else:
            report = self._process_pdf_auto(input_path, output_path, job_id, mode, inspection, collect_entities)

        return report

    # ------------------------------------------------------------------
    # Mode dispatchers
    # ------------------------------------------------------------------

    def _process_pdf_as_raster_ocr(self, input_path, output_path, job_id, mode, collect_entities=False):
        return redact_pdf_scanned(
            input_path=input_path, output_path=output_path,
            job_id=job_id, analyzer=self._analyzer, mode=mode,
            document_type="pdf_rasterized_ocr",
            collect_entities=collect_entities,
        )

    def _process_pdf_hybrid_secure(self, input_path, output_path, job_id, mode, inspection, collect_entities=False):
        if inspection.document_type == "pdf_digital":
            return self._process_digital_pdf(input_path, output_path, job_id, mode, collect_entities)
        if inspection.document_type == "pdf_scanned":
            return self._process_scanned_pdf(input_path, output_path, job_id, mode, collect_entities)
        # pdf_hybrid: if text-dominant (images are icons/QR, not rasterized text),
        # the digital pipeline already covers all PII — skip the expensive OCR pass.
        avg_chars = inspection.selectable_text_chars / max(1, inspection.pages)
        if avg_chars >= 200:
            return self._process_digital_pdf(input_path, output_path, job_id, mode, collect_entities)
        report = redact_pdf_hybrid_secure(
            input_path=input_path, output_path=output_path,
            job_id=job_id, analyzer=self._analyzer, mode=mode,
            collect_entities=collect_entities,
        )
        self._run_verification(report, output_path, mode)
        return report

    def _process_pdf_auto(self, input_path, output_path, job_id, mode, inspection, collect_entities=False):
        if inspection.document_type == "pdf_digital":
            return self._process_digital_pdf(input_path, output_path, job_id, mode, collect_entities)
        return self._process_scanned_pdf(input_path, output_path, job_id, mode, collect_entities)

    def _process_digital_pdf(self, input_path, output_path, job_id, mode, collect_entities=False):
        report = redact_pdf_digital(
            input_path=input_path, output_path=output_path,
            job_id=job_id, analyzer=self._analyzer, mode=mode,
            collect_entities=collect_entities,
        )
        self._run_verification(report, output_path, mode)
        return report

    def _process_scanned_pdf(self, input_path, output_path, job_id, mode, collect_entities=False):
        return redact_pdf_scanned(
            input_path=input_path, output_path=output_path,
            job_id=job_id, analyzer=self._analyzer, mode=mode,
            document_type="pdf_scanned",
            collect_entities=collect_entities,
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def _run_verification(
        self,
        report: ProcessingReport,
        output_path: Path,
        mode: str,
    ) -> None:
        """Run the 4-layer verifier and merge results into the report."""
        try:
            result = self._verifier.verify(output_path, mode=mode)
        except Exception as exc:
            report.warnings.append(f"Post-redaction verification failed: {exc}")
            return

        report.verification = VerificationSummary(
            overall_risk_score=result.overall_risk_score,
            risk_level=result.risk_level,
            layers_run=result.layers_run,
            critical_pages=result.critical_pages,
            entities_by_layer=result.entities_by_layer,
            total_residual_entities=result.total_residual_entities,
            passed=result.passed,
        )

        # Update residual counts from verification results
        if result.total_residual_entities > 0:
            report.residual_entities = max(
                report.residual_entities, result.total_residual_entities
            )
            for layer, count in result.entities_by_layer.items():
                key = f"residual_{layer}"
                report.residual_entities_by_type[key] = (
                    report.residual_entities_by_type.get(key, 0) + count
                )

        if result.critical_pages:
            _add_unique_warning(
                report,
                f"Residual PII detected in pages {result.critical_pages} "
                f"(risk={result.overall_risk_score:.2f}, level={result.risk_level}). "
                "Manual review required.",
            )
            report.status = "completed_with_warnings"
            report.manual_review_required = True

        if not result.passed:
            _add_unique_warning(
                report,
                f"Verification risk score {result.overall_risk_score:.2f} exceeds "
                f"reject threshold {settings.verify_risk_threshold_reject}. "
                "Redaction may be incomplete.",
            )
            report.status = "completed_with_warnings"
            report.manual_review_required = True

        # Document-level warnings (metadata, binary)
        for lr in result.document_layer_results:
            for w in lr.warnings:
                _add_unique_warning(report, f"[{lr.layer.value}] {w}")


def _add_unique_warning(report: ProcessingReport, warning: str) -> None:
    if warning not in report.warnings:
        report.warnings.append(warning)
