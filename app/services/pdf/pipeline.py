import logging
from pathlib import Path
from time import perf_counter

from app.core.config import settings
from app.schemas.report import (
    PAGES_NOT_VERIFIED,
    REVIEW_RESIDUAL_PII,
    REVIEW_VERIFICATION_RISK_HIGH,
    PLAN_TITLEBLOCK_ONLY_VERIFIED,
    CodedWarning,
    ProcessingReport,
    VerificationSummary,
)
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


logger = logging.getLogger(__name__)


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
        from app.services.pii import analyzer as _analyzer_mod
        _analyzer_mod.DESCARTES_PADRON = [] if collect_entities else None
        try:
            report = self._dispatch(
                input_path, output_path, job_id, mode, inspection, collect_entities,
            )
            if collect_entities:
                report.descartes_padron = list(dict.fromkeys(_analyzer_mod.DESCARTES_PADRON))
        finally:
            _analyzer_mod.DESCARTES_PADRON = None
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

        # pdf_hybrid: PAGINA A PAGINA CON UN CRITERIO DE SUPERFICIE.
        #
        # No se decide POR DOCUMENTO: con una media de caracteres por pagina,
        # dos folios de texto tapan veinte paginas escaneadas, y una copia de
        # DNI pegada en un expediente sale intacta sin un solo aviso.
        #
        # Pero lo contrario —toda pagina con imagen al OCR— tampoco: la mayoria
        # de las paginas digitales llevan el logo del membrete.  Cada
        # pagina entera solo va al OCR si no tiene texto o si es un escaneo
        # pegado; las imagenes menores se leen recortadas en el pase digital
        # (ver ocr_imagen_pagina_completa en la config).
        paginas_ocr, sin_ocr, zona_gris = _reparto(input_path)

        if not paginas_ocr:
            # Todas las imagenes son adornos: la redaccion sobre capa de texto
            # es exacta y conserva el texto seleccionable.
            report = self._process_digital_pdf(
                input_path, output_path, job_id, mode, collect_entities,
            )
        else:
            report = redact_pdf_hybrid_secure(
                input_path=input_path, output_path=output_path,
                job_id=job_id, analyzer=self._analyzer, mode=mode,
                collect_entities=collect_entities, paginas_ocr=paginas_ocr,
            )
            self._run_verification(report, output_path, mode)

        report.ocr.pages_with_image_not_ocr = sin_ocr
        report.ocr.pages_image_grey_zone = zona_gris
        if zona_gris:
            logger.info(
                "%d pagina(s) con texto y alguna imagen de mas del %.0f %% de la "
                "pagina: sus imagenes se leyeron recortadas, sin OCR de pagina.",
                zona_gris, settings.ocr_imagen_minima * 100,
            )
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

        _avisos_de_verificacion(report, result)


def _avisos_de_verificacion(report: ProcessingReport, result: DocumentVerificationResult) -> None:
    """Lleva al informe lo que dice el verificador.  Las paginas, desde 1, como
    en los avisos del procesado; critical_pages sigue guardando el indice."""
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
            f"Residual PII detected in pages {[i + 1 for i in result.critical_pages]} "
            f"(risk={result.overall_risk_score:.2f}, level={result.risk_level}). "
            "Manual review required.",
        )
        report.status = "completed_with_warnings"
        _pedir_revision(report, REVIEW_RESIDUAL_PII)

    if not result.passed:
        _add_unique_warning(
            report,
            f"Verification risk score {result.overall_risk_score:.2f} exceeds "
            f"reject threshold {settings.verify_risk_threshold_reject}. "
            "Redaction may be incomplete.",
        )
        report.status = "completed_with_warnings"
        _pedir_revision(report, REVIEW_VERIFICATION_RISK_HIGH)

    # Document-level warnings (metadata, binary)
    for lr in result.document_layer_results:
        for w in lr.warnings:
            _add_unique_warning(report, f"[{lr.layer.value}] {w}")

    if result.plan_pages:
        _add_unique_warning(
            report,
            "Construction-plan pages: only the title block was verified: "
            f"{[i + 1 for i in result.plan_pages]}.",
        )
        report.coded_warnings.append(CodedWarning(
            code=PLAN_TITLEBLOCK_ONLY_VERIFIED, pages=[i + 1 for i in result.plan_pages]))

    # Lo que no se pudo verificar se dice: saltarlo en silencio daba por buena
    # una pagina que nadie habia mirado.
    if result.unverified:
        por_capa: dict[str, list[int]] = {}
        del_documento: list[str] = []
        for pagina, capa, _motivo in result.unverified:
            if pagina is None:
                del_documento.append(capa)
            else:
                por_capa.setdefault(capa, []).append(pagina + 1)
        detalle = "; ".join(f"{capa}: pages {sorted(p)}" for capa, p in sorted(por_capa.items()))
        if del_documento:
            detalle = "; ".join(filter(None, [detalle, f"document: {sorted(del_documento)}"]))
        _add_unique_warning(
            report,
            f"Verification could not run on part of the document and it was "
            f"not verified ({detalle}). Manual review required.",
        )
        paginas = sorted({p for ps in por_capa.values() for p in ps})
        report.coded_warnings.append(CodedWarning(code=PAGES_NOT_VERIFIED, pages=paginas))
        report.status = "completed_with_warnings"
        _pedir_revision(report, PAGES_NOT_VERIFIED)


def _pedir_revision(report: ProcessingReport, motivo: str) -> None:
    report.manual_review_required = True
    if motivo not in report.manual_review_reasons:
        report.manual_review_reasons.append(motivo)


def _reparto(input_path: Path) -> tuple[set[int], int, int]:
    """(paginas que van al OCR, paginas con imagen que no van, de ellas zona gris).

    Las dos ultimas cifras son el recuento que permite ver, en un numero y no
    en una fuga, si el criterio de superficie deja sin mirar algo que importaba.
    """
    import fitz

    from app.services.pdf.inspector import cobertura_imagenes, pagina_necesita_ocr

    paginas_ocr: set[int] = set()
    sin_ocr = 0
    zona_gris = 0
    with fitz.open(input_path) as doc:
        for indice, page in enumerate(doc):
            if pagina_necesita_ocr(page):
                paginas_ocr.add(indice)
                continue
            try:
                tiene_imagen = bool(page.get_images(full=True))
            except Exception:
                tiene_imagen = False
            if tiene_imagen:
                sin_ocr += 1
                if cobertura_imagenes(page) > 0:
                    zona_gris += 1
    return paginas_ocr, sin_ocr, zona_gris


def _add_unique_warning(report: ProcessingReport, warning: str) -> None:
    if warning not in report.warnings:
        report.warnings.append(warning)
