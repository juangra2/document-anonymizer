"""Informe en PDF para quien no sabe informatica.

Lo que se fija: ningun valor de los datos ocultados aparece en el informe (ni
aunque el informe JSON los llevara, como con include_entities), ni el nombre
del fichero; el aviso de revision va antes que las cifras; y dice lo que tiene
que decir (tipos, paginas, paginas tachadas enteras, metadatos quitados).
"""
import time
from datetime import datetime, timezone
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.report import (
    CodedWarning,
    DetectedEntityInfo,
    PageRedactedByContent,
    PageRedactedInFull,
    ProcessingReport,
)
from app.services.informe_pdf import generar_informe_pdf

VALORES = ["Marcos Villalba Tena", "12345678Z", "655 123 456", "Calle de Cuba 14",
           "ES91 2100 0418 4502 0005 1332", "marcos.villalba@correo.es"]
FECHA = datetime(2026, 10, 23, 12, 30, tzinfo=timezone.utc)


def _texto(pdf: bytes) -> str:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(p.get_text() for p in doc)


def _todo(pdf: bytes) -> bytes:
    """Bytes del fichero y de cada flujo descomprimido: tambien lo que no se ve."""
    partes = [pdf]
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        for x in range(1, doc.xref_length()):
            partes.append(doc.xref_object(x).encode("latin-1", "replace"))
            if doc.xref_is_stream(x):
                partes.append(doc.xref_stream(x) or b"")
    return b"\n".join(partes)


def _informe(**cambios) -> ProcessingReport:
    base = dict(
        job_id="a1b2c3d4e5", status="manual_review_required", document_type="pdf_hybrid",
        pages=5, entities_detected=9, entities_redacted=9,
        entities_by_type={"PERSON": 4, "ES_DNI": 2, "PHONE_NUMBER": 3},
        entities_by_page={1: {"PERSON": 2, "ES_DNI": 1}, 3: {"PERSON": 2, "ES_DNI": 1},
                          5: {"PHONE_NUMBER": 3}},
        structural_redactions_untyped=2,
        manual_review_required=True,
        manual_review_reasons=["full_redaction_pages", "low_ocr_confidence"],
        coded_warnings=[CodedWarning(code="safety_net_still_readable", pages=[5],
                                     types=["PHONE_NUMBER"])],
        pages_redacted_in_full=[PageRedactedInFull(page=4, reason="ocr_unreadable")],
        pages_redacted_by_content=[PageRedactedByContent(page=2, reason="dni_scan")],
        metadata_removed={"document_info": 3, "comments": 2, "photo_metadata": 1},
        # Como con include_entities: el informe JSON lleva valores.  El PDF no.
        detected_entities=[DetectedEntityInfo(text=v, type="PERSON", score=1.0, source="t")
                           for v in VALORES],
        descartes_padron=["Marcos Villalba Tena"],
        warnings=["Pages fully redacted (dni_scan: [2]) 12345678Z"],
    )
    base.update(cambios)
    return ProcessingReport(**base)


def test_ningun_valor_ni_en_el_texto_ni_dentro_del_fichero():
    pdf = generar_informe_pdf(_informe(), FECHA)
    texto = _texto(pdf)
    todo = _todo(pdf).lower()
    for v in VALORES:
        assert v not in texto, v
        assert v.lower().encode() not in todo, v
        assert v.encode("utf-16-be").hex().encode() not in todo, v


def test_cabecera_con_fecha_paginas_e_identificador_y_sin_nombre_de_fichero():
    texto = " ".join(_texto(generar_informe_pdf(_informe(), FECHA)).split())
    assert "Universitat de València" in texto
    assert "23/10/2026" in texto and "5 páginas" in texto and "a1b2c3d4e5" in texto
    assert ".pdf" not in texto


def test_el_aviso_de_revision_va_antes_que_las_cifras():
    texto = " ".join(_texto(generar_informe_pdf(_informe(), FECHA)).split())
    assert texto.index("Revise el documento") < texto.index("Nombre de persona")


def test_dice_que_se_oculto_donde_y_por_que():
    texto = " ".join(_texto(generar_informe_pdf(_informe(), FECHA)).split())
    for esperado in ("Nombre de persona", "DNI / NIF", "Teléfono",
                     "copia de un documento de identidad", "no se pudo leer",
                     "Datos del documento", "Comentarios", "Fotos"):
        assert esperado in texto, esperado
    # las paginas de los avisos, contadas desde 1
    assert "página 5" in texto.lower()


def test_documento_sin_nada_que_revisar():
    informe = _informe(status="completed", manual_review_required=False,
                       manual_review_reasons=[], coded_warnings=[], pages_redacted_in_full=[],
                       pages_redacted_by_content=[])
    texto = " ".join(_texto(generar_informe_pdf(informe, FECHA)).split())
    assert "Revise el documento" not in texto
    assert "no ha señalado nada que revisar" in texto


def test_solo_metadatos_dice_que_el_contenido_no_se_ha_revisado():
    informe = _informe(treatment="metadata_only", status="completed_with_warnings",
                       manual_review_required=False, manual_review_reasons=[],
                       entities_by_type={}, entities_by_page={}, entities_detected=0,
                       coded_warnings=[CodedWarning(code="content_not_reviewed")],
                       pages_redacted_in_full=[], pages_redacted_by_content=[])
    texto = " ".join(_texto(generar_informe_pdf(informe, FECHA)).split())
    assert "NO se ha revisado" in texto
    assert "Nombre de persona" not in texto


def test_un_documento_largo_ocupa_varias_paginas_numeradas():
    por_pagina = {i: {"PERSON": 1} for i in range(1, 301)}
    pdf = generar_informe_pdf(_informe(pages=300, entities_by_page=por_pagina,
                                       entities_by_type={"PERSON": 300}), FECHA)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert len(doc) > 1
        assert "1 de" in doc[0].get_text() and f"{len(doc)} de {len(doc)}" in doc[-1].get_text()
        assert not doc.metadata.get("author")


# --- de principio a fin, por la cola -----------------------------------------

client = TestClient(app)


def _pdf_con_datos() -> bytes:
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Interesado: Marcos Villalba Tena", fontsize=11)
    p.insert_text((72, 120), "DNI: 12345678Z  Telefono: 655 123 456", fontsize=11)
    return doc.tobytes()


def _esperar(job_id):
    fin = time.time() + 120
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.2)
    raise AssertionError("no termino")


@pytest.mark.parametrize("output", ["pdf", "zip"])
def test_se_descarga_por_la_api_sin_valores(output):
    r = client.post("/jobs", files={"file": ("Expediente Marcos Villalba.pdf",
                                             BytesIO(_pdf_con_datos()), "application/pdf")})
    job_id = r.json()["job_id"]
    assert _esperar(job_id)["status"] == "completed"
    if output == "pdf":
        respuesta = client.get(f"/jobs/{job_id}/report.pdf")
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"] == "application/pdf"
        assert "informe" in respuesta.headers["content-disposition"]
        pdf = respuesta.content
    else:
        import zipfile
        zf = zipfile.ZipFile(BytesIO(client.get(f"/jobs/{job_id}/result?output=zip").content))
        assert "informe.pdf" in zf.namelist()
        pdf = zf.read("informe.pdf")
    texto = _texto(pdf)
    assert "Nombre de persona" in texto
    for v in ("Marcos", "Villalba", "12345678Z", "655 123 456"):
        assert v not in texto, v


def test_sin_trabajo_no_hay_informe():
    assert client.get("/jobs/nodeexiste/report.pdf").status_code == 404


def test_un_word_no_lleva_mapa_de_paginas_y_dice_que_es_un_word():
    informe = _informe(document_type="docx", pages=1, entities_by_page={},
                       pages_redacted_in_full=[], pages_redacted_by_content=[],
                       metadata_removed={"tracked_changes": 2, "comments": 1, "embedded_objects": 1})
    texto = " ".join(_texto(generar_informe_pdf(informe, FECHA)).split())
    assert "documento de Word" in texto and "En qué páginas" not in texto
    assert "control de cambios" in texto and "Objetos incrustados" in texto


def test_un_excel_lista_las_columnas_sustituidas():
    informe = _informe(document_type="xlsx", pages=2, entities_by_page={},
                       pages_redacted_in_full=[], pages_redacted_by_content=[],
                       replaced_columns=[{"sheet": "Solicitantes", "header": "DNI", "type": "ES_DNI", "cells": 12}],
                       metadata_removed={"pivot_tables": 1, "comments": 2})
    texto = " ".join(_texto(generar_informe_pdf(informe, FECHA)).split())
    assert "libro de Excel" in texto and "Columnas sustituidas" in texto
    assert "Solicitantes" in texto and "DNI / NIF" in texto and "Tablas dinámicas" in texto
    assert "En qué páginas" not in texto
