"""Solo quitar metadatos: la misma limpieza que al anonimizar, sin tocar el contenido.

Lo que se fija: no queda nada oculto (centinelas de casos_metadatos, ExifTool y
qpdf), lo que se ve en la pagina sigue ahi (no se anonimiza), los comentarios se
eliminan, los campos pasan a la pagina, el texto oculto se va, y el informe dice
que el contenido no se ha revisado y si habia una firma digital.
"""
import time
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.report import CONTENT_NOT_REVIEWED, DIGITAL_SIGNATURE_INVALIDATED
from app.services.pdf.solo_metadatos import quitar_metadatos
from evaluation.comprobar_metadatos import comprobar, herramientas
from tests.casos_metadatos import CASOS, OCULTOS, VISIBLE_CAMPO, VISIBLE_LIBRE, presentes


def _texto(ruta):
    with fitz.open(ruta) as doc:
        return "\n".join(p.get_text() for p in doc)


def _codigos(informe):
    return {c.code for c in informe.coded_warnings}


@pytest.mark.parametrize("caso", list(CASOS))
def test_no_queda_nada_oculto(caso, tmp_path):
    entrada = CASOS[caso](tmp_path / f"{caso}.pdf")
    salida = tmp_path / "salida.pdf"
    quitar_metadatos(entrada, salida, "t")
    assert presentes(salida, OCULTOS) == []


@pytest.mark.skipif(herramientas() is None, reason="sin ExifTool o qpdf")
@pytest.mark.parametrize("caso", list(CASOS))
def test_la_comprobacion_independiente_la_da_limpia(caso, tmp_path):
    entrada = CASOS[caso](tmp_path / f"{caso}.pdf")
    salida = tmp_path / "salida.pdf"
    quitar_metadatos(entrada, salida, "t")
    assert comprobar(salida, *herramientas()) == []


def test_el_contenido_no_se_anonimiza(tmp_path):
    entrada = CASOS["digital_cargado"](tmp_path / "e.pdf")
    salida = tmp_path / "s.pdf"
    informe = quitar_metadatos(entrada, salida, "t")
    texto = _texto(salida)
    assert "Marta Ferrer Soler" in texto and "48291736K" in texto
    assert informe.entities_redacted == 0
    with fitz.open(entrada) as a, fitz.open(salida) as b:
        assert len(a) == len(b)
        assert [len(p.get_images()) for p in a] == [len(p.get_images()) for p in b]


def test_comentarios_fuera_y_campos_a_la_pagina(tmp_path):
    entrada = CASOS["digital_cargado"](tmp_path / "e.pdf")
    salida = tmp_path / "s.pdf"
    quitar_metadatos(entrada, salida, "t")
    texto = _texto(salida)
    assert VISIBLE_CAMPO in texto               # lo que se veia en el campo, en la pagina
    assert "ZZLIBRETEXTO" not in texto          # el comentario de texto libre, fuera
    assert VISIBLE_LIBRE.split()[0] not in texto
    with fitz.open(salida) as doc:
        assert not any(list(p.annots()) or list(p.widgets()) for p in doc)


def _pdf_con_texto_oculto(destino):
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), "VISIBLENEGRO a la vista", fontsize=11)
    p.insert_text((72, 140), "ZZBLANCOSOBREBLANCO 48291736K", fontsize=11, color=(1, 1, 1))
    p.insert_text((72, 180), "ZZINVISIBLE capa de OCR", fontsize=11, render_mode=3)
    p.insert_text((72, 260), "ZZBAJOIMAGEN 655 123 456", fontsize=11)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 10), 0)
    pix.clear_with(120)
    p.insert_image(fitz.Rect(60, 240, 400, 275), pixmap=pix, keep_proportion=False)
    p.draw_rect(fitz.Rect(60, 300, 400, 330), color=None, fill=(0, 0, 0))
    p.insert_text((72, 320), "VISIBLEBLANCOSOBRENEGRO", fontsize=11, color=(1, 1, 1))
    doc.save(destino)
    return destino


def test_quita_el_texto_oculto_y_deja_el_visible(tmp_path):
    salida = tmp_path / "s.pdf"
    informe = quitar_metadatos(_pdf_con_texto_oculto(tmp_path / "e.pdf"), salida, "t")
    texto = _texto(salida)
    for oculto in ("ZZBLANCOSOBREBLANCO", "ZZINVISIBLE", "ZZBAJOIMAGEN"):
        assert oculto not in texto, oculto
    assert presentes(salida, ["ZZBLANCOSOBREBLANCO", "ZZINVISIBLE", "ZZBAJOIMAGEN"]) == []
    assert "VISIBLENEGRO" in texto and "VISIBLEBLANCOSOBRENEGRO" in texto
    assert informe.metadata_removed.get("hidden_text") == 3


def test_el_informe_dice_que_el_contenido_no_se_ha_revisado(tmp_path):
    informe = quitar_metadatos(CASOS["digital_cargado"](tmp_path / "e.pdf"), tmp_path / "s.pdf", "t")
    assert informe.treatment == "metadata_only"
    assert CONTENT_NOT_REVIEWED in _codigos(informe)
    assert DIGITAL_SIGNATURE_INVALIDATED not in _codigos(informe)
    quitado = informe.metadata_removed
    for clave in ("document_info", "xmp", "comments", "form_fields",
                  "bookmarks", "javascript", "attachments", "photo_metadata", "thumbnails"):
        assert quitado.get(clave, 0) > 0, clave
    assert informe.detected_entities == []


def _pdf_firmado(destino):
    """Un campo de firma como lo deja Acrobat: /FT /Sig y SigFlags en el formulario."""
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Resolucion firmada", fontsize=11)
    campo = fitz.Widget()
    campo.field_name, campo.field_type = "firma", fitz.PDF_WIDGET_TYPE_TEXT
    campo.rect = fitz.Rect(72, 700, 300, 740)
    p.add_widget(campo)
    xref = next(p.widgets()).xref
    doc.xref_set_key(xref, "FT", "/Sig")
    acroform = doc.xref_get_key(doc.pdf_catalog(), "AcroForm")
    if acroform[0] == "xref":
        doc.xref_set_key(int(acroform[1].split()[0]), "SigFlags", "3")
    doc.save(destino)
    return destino


def test_avisa_de_que_la_firma_digital_deja_de_valer(tmp_path):
    informe = quitar_metadatos(_pdf_firmado(tmp_path / "f.pdf"), tmp_path / "s.pdf", "t")
    assert DIGITAL_SIGNATURE_INVALIDATED in _codigos(informe)


# --- por la cola de trabajos ---------------------------------------------------

client = TestClient(app)


def _esperar(job_id, limite=120.0):
    fin = time.time() + limite
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed", "expired"):
            return estado
        time.sleep(0.2)
    raise AssertionError("no termino")


def test_por_la_api_devuelve_el_documento_sin_anonimizar_ni_metadatos(tmp_path):
    entrada = CASOS["digital_cargado"](tmp_path / "e.pdf")
    r = client.post("/jobs?treatment=metadata_only",
                    files={"file": ("e.pdf", BytesIO(entrada.read_bytes()), "application/pdf")})
    assert r.status_code == 202
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed"
    assert estado["result"]["treatment"] == "metadata_only"
    descarga = client.get(f"/jobs/{r.json()['job_id']}/result")
    assert "sin_metadatos" in descarga.headers["content-disposition"]
    assert "contenido_sin_revisar" in descarga.headers["content-disposition"]
    salida = tmp_path / "s.pdf"
    salida.write_bytes(descarga.content)
    assert "48291736K" in _texto(salida)
    assert presentes(salida, OCULTOS) == []


def test_un_tratamiento_desconocido_se_rechaza():
    r = client.post("/jobs?treatment=otra_cosa",
                    files={"file": ("e.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")})
    assert r.status_code == 422


def _anonimizar_por_la_api(ruta):
    r = client.post("/jobs", files={"file": ("e.pdf", BytesIO(ruta.read_bytes()), "application/pdf")})
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed"
    return estado, client.get(f"/jobs/{r.json()['job_id']}/report").json()


def test_al_anonimizar_tambien_avisa_de_la_firma(tmp_path):
    estado, informe = _anonimizar_por_la_api(_pdf_firmado(tmp_path / "f.pdf"))
    assert informe["treatment"] == "anonymize"
    assert DIGITAL_SIGNATURE_INVALIDATED in {c["code"] for c in estado["result"]["coded_warnings"]}


def test_al_anonimizar_el_informe_cuenta_los_metadatos_quitados(tmp_path):
    _, informe = _anonimizar_por_la_api(CASOS["digital_cargado"](tmp_path / "e.pdf"))
    for clave in ("document_info", "xmp", "comments", "form_fields", "bookmarks", "javascript"):
        assert informe["metadata_removed"].get(clave, 0) > 0, clave
    assert CONTENT_NOT_REVIEWED not in {c["code"] for c in informe["coded_warnings"]}
