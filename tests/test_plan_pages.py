"""Fase (i): detección de páginas de plano de obra antes de render."""
import fitz
import pytest

from app.core.config import settings
from app.services.pdf.plan_pages import is_plan_page

# A1 apaisado en puntos (como TOMO_04 real: 2384×1684).
_A1_W, _A1_H = 2384, 1684


def _plan_pdf(n_lines=2500, with_paragraphs=False, w=_A1_W, h=_A1_H):
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    # Dibujo técnico sintético: miles de segmentos cortos.
    for i in range(n_lines):
        x = 40 + (i * 7) % (w - 120)
        y = 40 + (i * 13) % (h - 120)
        page.draw_line((x, y), (x + 30, y + 5))
    if with_paragraphs:
        para = ("Este es un párrafo largo de texto administrativo que supera "
                "con claridad los ciento cincuenta caracteres de longitud que "
                "definen un bloque de tipo párrafo en el detector de planos. ")
        for k in range(8):
            page.insert_text((60, 80 + 40 * k), para, fontsize=9)
    return doc, page


def test_vector_plan_detected():
    doc, page = _plan_pdf()
    try:
        assert is_plan_page(page) is True
    finally:
        doc.close()


def test_a4_admin_page_not_plan():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "Solicitud de licencia municipal", fontsize=11)
    try:
        assert is_plan_page(page) is False
    finally:
        doc.close()


def test_large_page_with_paragraphs_not_plan():
    # Un póster/anexo A1 con prosa no es un plano.
    doc, page = _plan_pdf(with_paragraphs=True)
    try:
        assert is_plan_page(page) is False
    finally:
        doc.close()


def test_large_page_without_graphics_not_plan():
    doc, page = _plan_pdf(n_lines=50)   # pocas líneas: no hay dominancia gráfica
    try:
        assert is_plan_page(page) is False
    finally:
        doc.close()


def test_toggle_disables_detection(monkeypatch):
    monkeypatch.setattr(settings, "plan_page_enabled", False)
    doc, page = _plan_pdf()
    try:
        assert is_plan_page(page) is False
    finally:
        doc.close()


def test_digital_redactor_skips_plan_page(tmp_path):
    # Un PDF con página-plano se procesa sin tocar la página y con aviso.
    from app.services.pdf.redactor import redact_pdf_digital
    from app.services.pii.analyzer import PiiAnalyzer

    doc, _ = _plan_pdf()
    src = tmp_path / "plan.pdf"
    doc.save(src)
    doc.close()

    out = tmp_path / "out.pdf"
    report = redact_pdf_digital(
        input_path=src, output_path=out, job_id="t",
        analyzer=PiiAnalyzer(),
    )
    assert report.manual_review_required is True
    assert any("plan" in w.lower() for w in report.warnings)
