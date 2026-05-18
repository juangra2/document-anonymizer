from pathlib import Path
from time import perf_counter

from app.core.config import settings
from app.schemas.report import ProcessingReport
from app.services.pdf.hybrid_redactor import redact_pdf_hybrid_secure
from app.services.pdf.inspector import PdfInspection, inspect_pdf_for_phase1
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pdf.scanned_redactor import redact_pdf_scanned
from app.services.pdf.verifier import verify_no_residual_pii
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity


class PdfAnonymizationPipeline:
    def __init__(self) -> None:
        self._analyzer = PiiAnalyzer()

    def process(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str = "strict",
    ) -> ProcessingReport:
        started = perf_counter()

        inspection = inspect_pdf_for_phase1(input_path)

        if settings.pdf_processing_mode == "raster_ocr":
            report = self._process_pdf_as_raster_ocr(
                input_path=input_path,
                output_path=output_path,
                job_id=job_id,
                mode=mode,
            )
        elif settings.pdf_processing_mode == "hybrid_secure":
            report = self._process_pdf_hybrid_secure(
                input_path=input_path,
                output_path=output_path,
                job_id=job_id,
                mode=mode,
                inspection=inspection,
            )
        else:
            report = self._process_pdf_auto(
                input_path=input_path,
                output_path=output_path,
                job_id=job_id,
                mode=mode,
                inspection=inspection,
            )

        report.redaction_mode = mode
        report.processing_ms = int((perf_counter() - started) * 1000)

        return report

    def _process_pdf_as_raster_ocr(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str,
    ) -> ProcessingReport:
        return redact_pdf_scanned(
            input_path=input_path,
            output_path=output_path,
            job_id=job_id,
            analyzer=self._analyzer,
            mode=mode,
            document_type="pdf_rasterized_ocr",
        )

    def _process_pdf_hybrid_secure(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str,
        inspection: PdfInspection,
    ) -> ProcessingReport:
        if inspection.document_type == "pdf_digital":
            return self._process_digital_pdf(
                input_path=input_path,
                output_path=output_path,
                job_id=job_id,
                mode=mode,
            )

        if inspection.document_type == "pdf_scanned":
            return self._process_scanned_pdf(
                input_path=input_path,
                output_path=output_path,
                job_id=job_id,
                mode=mode,
            )

        report = redact_pdf_hybrid_secure(
            input_path=input_path,
            output_path=output_path,
            job_id=job_id,
            analyzer=self._analyzer,
            mode=mode,
        )

        digital_residual_entities, digital_residual_by_type = verify_no_residual_pii(
            output_path=output_path,
            analyzer=self._analyzer,
            mode="strict",
        )

        self._merge_residuals_into_report(
            report=report,
            residual_entities=digital_residual_entities,
            residual_by_type=digital_residual_by_type,
        )

        return report

    def _process_pdf_auto(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str,
        inspection: PdfInspection,
    ) -> ProcessingReport:
        if inspection.document_type == "pdf_digital":
            return self._process_digital_pdf(
                input_path=input_path,
                output_path=output_path,
                job_id=job_id,
                mode=mode,
            )

        return self._process_scanned_pdf(
            input_path=input_path,
            output_path=output_path,
            job_id=job_id,
            mode=mode,
        )

    def _process_digital_pdf(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str,
    ) -> ProcessingReport:
        report = redact_pdf_digital(
            input_path=input_path,
            output_path=output_path,
            job_id=job_id,
            analyzer=self._analyzer,
            mode=mode,
        )

        residual_entities, residual_by_type = verify_no_residual_pii(
            output_path=output_path,
            analyzer=self._analyzer,
            mode="strict",
        )

        self._merge_residuals_into_report(
            report=report,
            residual_entities=residual_entities,
            residual_by_type=residual_by_type,
        )

        return report

    def _process_scanned_pdf(
        self,
        input_path: Path,
        output_path: Path,
        job_id: str,
        mode: str,
    ) -> ProcessingReport:
        return redact_pdf_scanned(
            input_path=input_path,
            output_path=output_path,
            job_id=job_id,
            analyzer=self._analyzer,
            mode=mode,
            document_type="pdf_scanned",
        )

    @staticmethod
    def _merge_residuals_into_report(
        report: ProcessingReport,
        residual_entities: list[PiiEntity],
        residual_by_type: dict[str, int],
    ) -> None:
        if not residual_entities:
            return

        report.residual_entities += len(residual_entities)

        for entity_type, count in residual_by_type.items():
            report.residual_entities_by_type[entity_type] = (
                report.residual_entities_by_type.get(entity_type, 0) + count
            )

        report.status = "completed_with_warnings"
        report.manual_review_required = True

        warning = (
            "Residual PII-like entities detected after redaction. Manual review required."
        )

        if warning not in report.warnings:
            report.warnings.append(warning)