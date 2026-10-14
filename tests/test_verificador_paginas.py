"""El verificador no se salta paginas en silencio y numera desde 1.

Prueba del plano real: la capa textual extrae con get_text(sort=True), que en
una pagina de fragmentos sueltos rellena los huecos con espacios (69.000
caracteres reales dieron 20 millones).  spaCy rechaza mas de un millon, la
capa fallaba, y la pagina quedaba sin verificar: solo un aviso en el registro,
nada en el informe.  Y el aviso de residuos decia "pages [2]" contando desde 0,
cuando los avisos en vivo cuentan desde 1.
"""
import fitz

from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification import layers
from app.services.verification.layers import analizar_por_trozos, check_textual

analyzer = PiiAnalyzer()


def _plano(ruta, fragmentos=1500):
    doc = fitz.open()
    p = doc.new_page(width=2384, height=1684)
    for i in range(fragmentos):
        x = 60 + (i * 37) % 2200
        y = 60 + (i * 23) % 1500
        p.insert_text((x, y), "h=3.45", fontsize=1.5, rotate=90 if i % 2 else 0)
    p.insert_text((1900, 1600), "Promotor: Vicent Peris Llopis", fontsize=10)
    doc.save(ruta)


def test_la_capa_textual_verifica_la_pagina_de_un_plano(tmp_path):
    ruta = tmp_path / "plano.pdf"
    _plano(ruta)
    r = check_textual(ruta, 0, analyzer, "strict")
    assert any(e.entity_type == "PERSON" for e in r.entities_found)


def test_un_texto_largo_se_analiza_por_trozos_con_sus_posiciones():
    relleno = "Linea de texto sin datos personales.\n"
    texto = relleno * 30 + "DNI: 69026956T\n" + relleno * 30
    entidades = analizar_por_trozos(analyzer, texto, "strict", maximo=400)
    dni = [e for e in entidades if e.entity_type == "ES_DNI"]
    assert dni, "el DNI se perdio al trocear"
    assert texto[dni[0].start:dni[0].end] == "69026956T"


def _verificador():
    from app.services.layout.segmenters import OcrPageSegmenter
    from app.services.verification.verifier import DocumentVerifier
    return DocumentVerifier(analyzer, None, OcrPageSegmenter())


def test_una_pagina_que_no_se_puede_verificar_sale_en_el_resultado(tmp_path, monkeypatch):
    ruta = tmp_path / "doc.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 100), "Texto sin datos.", fontsize=10)
    doc.save(ruta)

    original = layers.check_textual

    def falla_en_la_segunda(pdf, i, *a, **k):
        if i == 1:
            raise ValueError("fallo de prueba")
        return original(pdf, i, *a, **k)

    monkeypatch.setattr("app.services.verification.verifier.check_textual", falla_en_la_segunda)
    res = _verificador().verify(ruta)
    assert [(i, capa) for i, capa, _ in res.unverified] == [(1, "textual")]


def test_los_avisos_del_informe_numeran_desde_1():
    from app.schemas.report import ProcessingReport
    from app.services.pdf.pipeline import _avisos_de_verificacion
    from app.services.verification.models import DocumentVerificationResult

    res = DocumentVerificationResult(
        page_results=[], document_layer_results=[], overall_risk_score=0.42,
        risk_level="medium", critical_pages=[2], passed=True,
        total_residual_entities=2, entities_by_layer={"textual": 2},
        layers_run=["textual"], unverified=[(0, "textual", "texto demasiado largo")],
    )
    report = ProcessingReport(job_id="t", status="completed", document_type="pdf_digital")
    _avisos_de_verificacion(report, res)
    avisos = " | ".join(report.warnings)
    assert "pages [3]" in avisos
    assert "not verified" in avisos and "[1]" in avisos
    assert report.manual_review_required
    assert ("pages_not_verified", [1]) in [(a.code, a.pages) for a in report.coded_warnings]
