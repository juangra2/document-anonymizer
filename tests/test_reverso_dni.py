"""El reverso de un DNI se tapa entero, tambien girado, en JPG y dentro de un Word.

Un reverso no lleva el rotulo "DOCUMENTO NACIONAL DE IDENTIDAD": lo que lo
delata es la zona de lectura mecanica (MRZ, las tres lineas con "IDESP..." y
rellenos "<"), que lleva el numero, el nombre y los apellidos.  Contaba como una
sola señal y hacen falta dos, asi que una foto del reverso solo (girada 90
grados, con el "IDESP" mal leido por el OCR) salia con un par de barras y la
MRZ a la vista.  Una MRZ completa basta por si sola.

Reverso inventado (evaluation/corpus_imagenes.dni_reverso).
"""
import io

import docx
import numpy as np
import pytest
from PIL import Image

from app.core.config import settings
from app.services.pdf.full_redaction import DNI_SCAN, classify_page
from evaluation.corpus_imagenes import Foto, dni_reverso, fotografiar

# Lo que el OCR lee de un reverso girado: el "IDESP" mal leido, los rellenos bien.
MRZ_LEIDA = ("DOMICILIO\nC. DE LA PRUEBA 12 PISO 3\nVALENCIA\nEQUIPO 46001A1D1\n"
             "1DE5PBAA000123412345678Z<<<<<<\n9001014F3001014ESP<<<<<<<<<<<4\nPRUEBA<INVENTADA<<ANA<<<<<<<<<")


def test_una_mrz_completa_basta_sola():
    assert classify_page(MRZ_LEIDA) == DNI_SCAN


def test_una_linea_de_rellenos_no_es_una_mrz():
    assert classify_page("Firma del solicitante <<<<<<<<<<\nFecha: 03/11/2026") is None


def test_codigos_largos_sin_rellenos_no_son_una_mrz():
    texto = ("Código seguro de verificación: ABCD1234EFGH5678IJKL9012MNOP\n"
             "Referencia: 0123456789ABCDEF0123456789ABCDEF")
    assert classify_page(texto) is None


@pytest.fixture(scope="module")
def pipeline():
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    return PdfAnonymizationPipeline()


def _reverso_girado() -> bytes:
    foto, _, _ = fotografiar(dni_reverso().paginas[0], Foto(giro90=True), semilla=3)
    buf = io.BytesIO()
    Image.fromarray(foto).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _procesar_imagen(pipeline):
    from app.services.image.imagen import anonimizar_imagen

    return lambda e, s, f: anonimizar_imagen(e, s, f, lambda pe, ps: pipeline.process(
        pe, ps, "t", settings.default_redaction_mode, False, frozenset()))


def _negro(img: Image.Image) -> float:
    return float((np.asarray(img.convert("L")) < 40).mean())


def test_el_reverso_girado_en_jpg_se_tapa_entero(tmp_path, pipeline):
    entrada, salida = tmp_path / "reverso.jpg", tmp_path / "salida.jpg"
    entrada.write_bytes(_reverso_girado())
    informe = _procesar_imagen(pipeline)(entrada, salida, "jpeg")
    assert [p.reason for p in informe.pages_redacted_by_content] == [DNI_SCAN]
    assert _negro(Image.open(salida)) > 0.9


def test_el_reverso_girado_dentro_de_un_word_se_tapa_entero(tmp_path, pipeline):
    from app.services.office.docx import anonimizar_docx
    from app.services.pii.analyzer import PiiAnalyzer

    d = docx.Document()
    d.add_paragraph("Copia del documento de identidad aportada por el interesado.")
    d.add_picture(io.BytesIO(_reverso_girado()))
    entrada, salida = tmp_path / "expediente.docx", tmp_path / "salida.docx"
    d.save(entrada)
    anonimizar_docx(entrada, salida, PiiAnalyzer(), _procesar_imagen(pipeline), "t")
    imagen = next(r for r in docx.Document(salida).part.related_parts.values()
                  if r.partname.startswith("/word/media/"))
    assert _negro(Image.open(io.BytesIO(imagen.blob))) > 0.9
