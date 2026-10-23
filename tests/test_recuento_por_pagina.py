"""Recuento de datos por pagina (para el informe en PDF).

Solo se cuenta, no cambia que se detecta: en los tres caminos la suma de las
paginas da exactamente `entities_by_type`, y las paginas van numeradas desde 1.
"""
from collections import Counter
from pathlib import Path

import fitz
import pytest

from app.services.pii.analyzer import PiiAnalyzer
from tests.test_partial_stop import _FakeEngine, _pdf_escaneado, _pdf_hibrido


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _suma(por_pagina):
    total = Counter()
    for tipos in por_pagina.values():
        total.update(tipos)
    return dict(total)


def _digital(path: Path) -> Path:
    doc = fitz.open()
    for texto in ("Interesado: Marcos Villalba Tena, DNI 12345678Z",
                  "Resolucion del expediente de licencia de obra menor",
                  "Telefono de contacto: 655 123 456"):
        doc.new_page().insert_text((72, 100), texto, fontsize=11)
    doc.save(path)
    doc.close()
    return path


def test_digital(tmp_path, analyzer):
    from app.services.pdf.redactor import redact_pdf_digital

    informe = redact_pdf_digital(_digital(tmp_path / "d.pdf"), tmp_path / "s.pdf", "t", analyzer)
    assert informe.entities_by_type
    assert _suma(informe.entities_by_page) == informe.entities_by_type
    assert set(informe.entities_by_page) == {1, 3}, "la pagina 2 no tiene datos"


def _con_ocr_simulado(monkeypatch):
    import app.services.pdf.scanned_redactor as sr

    motor = _FakeEngine()
    monkeypatch.setattr(sr, "get_ocr_engine", lambda: motor)
    monkeypatch.setattr(sr, "get_word_ocr_engine", lambda *_a, **_k: motor)
    return sr


def test_escaneado(tmp_path, monkeypatch, analyzer):
    sr = _con_ocr_simulado(monkeypatch)
    informe = sr.redact_pdf_scanned(_pdf_escaneado(tmp_path / "e.pdf", 3), tmp_path / "s.pdf",
                                    "t", analyzer)
    assert _suma(informe.entities_by_page) == informe.entities_by_type
    assert set(informe.entities_by_page) <= {1, 2, 3}


def test_hibrido(tmp_path, monkeypatch, analyzer):
    import app.services.pdf.hybrid_redactor as hr

    _con_ocr_simulado(monkeypatch)
    informe = hr.redact_pdf_hybrid_secure(_pdf_hibrido(tmp_path / "h.pdf", 3), tmp_path / "s.pdf",
                                          "t", analyzer)
    assert _suma(informe.entities_by_page) == informe.entities_by_type
    assert set(informe.entities_by_page) <= {1, 2, 3}


def test_el_informe_json_lo_lleva_con_la_pagina_como_clave():
    from app.schemas.report import ProcessingReport

    r = ProcessingReport(job_id="t", status="completed", entities_by_page={2: {"PERSON": 1}})
    assert '"2": {' in r.model_dump_json() or '"2":{' in r.model_dump_json()
    assert ProcessingReport.model_validate_json(r.model_dump_json()).entities_by_page == {2: {"PERSON": 1}}


def test_las_paginas_tachadas_por_contenido_van_con_su_motivo(tmp_path, analyzer):
    """Copia de DNI y autoliquidacion: el informe en PDF explica por que."""
    from app.services.pdf.redactor import redact_pdf_digital
    from tests.test_full_redaction import _DNI_ANVERSO, _TAX

    doc = fitz.open()
    for texto in ("Resolucion del expediente de licencia de obra menor", _DNI_ANVERSO, _TAX):
        doc.new_page().insert_text((72, 100), texto, fontsize=10)
    doc.save(tmp_path / "d.pdf")
    informe = redact_pdf_digital(tmp_path / "d.pdf", tmp_path / "s.pdf", "t", analyzer)
    assert [(p.page, p.reason) for p in informe.pages_redacted_by_content] == [
        (2, "dni_scan"), (3, "tax_form")]
