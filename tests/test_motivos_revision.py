"""Cada motivo de revision manual queda en el informe con un codigo.

La pantalla muestra una nota generica ("Revisalo antes de usarlo...") cuando el
documento pide revision.  Si un aviso con codigo ya explica el motivo, la
generica sobra; pero solo si explica TODOS los motivos.  Para saberlo sin
adivinar, el informe dice por que pide revision: `manual_review_reasons`.
"""
import fitz

from app.schemas.report import ProcessingReport
from app.services.pdf.pipeline import _avisos_de_verificacion
from app.services.verification.models import DocumentVerificationResult


def _resultado(**cambios):
    base = dict(page_results=[], document_layer_results=[], overall_risk_score=0.0,
                risk_level="none", critical_pages=[], passed=True,
                total_residual_entities=0, entities_by_layer={}, layers_run=["textual"])
    base.update(cambios)
    return DocumentVerificationResult(**base)


def _informe():
    return ProcessingReport(job_id="t", status="completed", document_type="pdf_digital")


def test_los_residuos_del_verificador_dejan_su_motivo():
    report = _informe()
    _avisos_de_verificacion(report, _resultado(overall_risk_score=0.42, risk_level="medium",
                                               critical_pages=[2]))
    assert report.manual_review_reasons == ["residual_pii"]


def test_un_riesgo_por_encima_del_rechazo_deja_el_suyo():
    report = _informe()
    _avisos_de_verificacion(report, _resultado(overall_risk_score=0.9, risk_level="high",
                                               critical_pages=[0], passed=False))
    assert set(report.manual_review_reasons) == {"residual_pii", "verification_risk_high"}


def test_las_paginas_sin_verificar_dejan_el_codigo_de_su_aviso():
    report = _informe()
    _avisos_de_verificacion(report, _resultado(unverified=[(0, "textual", "x")]))
    assert report.manual_review_reasons == ["pages_not_verified"]


def test_solo_verificar_el_cajetin_no_es_motivo_de_revision():
    report = _informe()
    _avisos_de_verificacion(report, _resultado(plan_pages=[0]))
    assert report.manual_review_reasons == [] and not report.manual_review_required


def test_un_plano_sin_cajetin_deja_su_motivo(tmp_path):
    from app.services.pdf.redactor import redact_pdf_digital
    from app.services.pii.analyzer import PiiAnalyzer
    from tests.test_plan_pages import _plan_pdf

    doc, _ = _plan_pdf()
    entrada = tmp_path / "plano.pdf"
    doc.save(entrada)
    report = redact_pdf_digital(entrada, tmp_path / "out.pdf", "t", PiiAnalyzer())
    assert report.manual_review_reasons == ["plan_without_titleblock"]


def test_el_resumen_del_trabajo_lleva_los_motivos():
    from app.schemas.jobs import JobResultSummary
    assert "manual_review_reasons" in JobResultSummary.model_fields
