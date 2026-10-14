"""En una pagina de plano el verificador mira solo el cajetin.

Con el verificador ya funcionando en la pagina del plano real, la capa textual
dio ~120 residuos con riesgo 0,82 (por encima del rechazo): rotulos del dibujo
leidos como nombres, codigos de cota como pasaportes y matriculas, coordenadas
como telefonos.  Revisados uno a uno: todo ruido, ningun nombre fuera del
cajetin.  El tratamiento de planos solo tacha el cajetin; el verificador hace
lo mismo, y el informe lo dice.  Un plano sin cajetin no se verifica, y tambien
se dice.
"""
import fitz

from app.services.layout.segmenters import OcrPageSegmenter
from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification.verifier import DocumentVerifier

from tests.test_plan_pages import _A1_H, _A1_W, _plan_pdf

analyzer = PiiAnalyzer()


def _verificar(ruta):
    return DocumentVerifier(analyzer, None, OcrPageSegmenter()).verify(ruta)


def _plano(ruta, con_cajetin=True):
    doc, page = _plan_pdf()
    if con_cajetin:
        x0, y0 = _A1_W - 340, _A1_H - 500
        for i in range(7):
            page.draw_line((x0, y0 + 70 * i), (_A1_W - 20, y0 + 70 * i))
        page.draw_rect(fitz.Rect(x0, y0, _A1_W - 20, _A1_H - 20))
        for k, lab in enumerate(["PROMOTOR: ACME PROMOCIONES SL",
                                 "ARQUITECTO: LOLA ROMERA MARTINEZ",
                                 "ESCALA: 1/500", "FECHA: 01/2026", "PLANO: 03"]):
            page.insert_text((x0 + 10, y0 + 30 + 55 * k), lab, fontsize=13)
    # Ruido del dibujo: cotas y coordenadas con forma de dato.
    page.insert_text((300, 300), "DNI 69026956T", fontsize=10)
    page.insert_text((300, 340), "612 345 678", fontsize=10)
    doc.save(ruta)


def test_en_un_plano_solo_se_verifica_el_cajetin(tmp_path):
    ruta = tmp_path / "plano.pdf"
    _plano(ruta)
    res = _verificar(ruta)
    textual = res.page_results[0].layer_results[0]
    tipos = {e.entity_type for e in textual.entities_found}
    assert "PERSON" in tipos, "el nombre del cajetin sin tachar tiene que verse"
    assert not tipos & {"ES_DNI", "PHONE_NUMBER"}, "se verifico el cuerpo del dibujo"
    assert res.plan_pages == [0]


def test_un_plano_sin_cajetin_sale_como_no_verificado(tmp_path):
    ruta = tmp_path / "plano.pdf"
    _plano(ruta, con_cajetin=False)
    res = _verificar(ruta)
    assert [(i, capa) for i, capa, _ in res.unverified] == [(0, "textual")]
    assert res.page_results[0].layer_results == []


def test_el_informe_dice_que_solo_se_verifico_el_cajetin():
    from app.schemas.report import ProcessingReport
    from app.services.pdf.pipeline import _avisos_de_verificacion
    from app.services.verification.models import DocumentVerificationResult

    res = DocumentVerificationResult(
        page_results=[], document_layer_results=[], overall_risk_score=0.0,
        risk_level="none", critical_pages=[], passed=True,
        total_residual_entities=0, entities_by_layer={}, layers_run=["textual"],
        plan_pages=[0],
    )
    report = ProcessingReport(job_id="t", status="completed", document_type="pdf_digital")
    _avisos_de_verificacion(report, res)
    assert any("only the title block was verified" in w and "[1]" in w for w in report.warnings)
