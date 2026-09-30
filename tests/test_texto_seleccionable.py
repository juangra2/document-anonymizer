"""En un documento mixto, las paginas que no se tachan conservan su texto.

Es una propiedad que se pierde facil en cualquier refactor: basta con que una
pagina digital pase por el camino de imagen para que se entregue como una foto
de si misma.  Quien luego busca dentro del expediente —un nombre de calle, un
numero de expediente— deja de encontrar nada, y nadie se entera hasta que
alguien lo necesita.

Y la otra mitad del reparto: que la pagina escaneada de ese mismo documento SI
se mire, aunque el resto tenga mucho texto.  Antes no se miraba: dos folios de
texto mandaban el documento entero al camino digital.

Esta prueba pasa el documento por el pipeline completo, con OCR.  Es lenta a
proposito: lo que fija es el comportamiento extremo a extremo.
"""
from pathlib import Path

import fitz
import pytest

_PARRAFO = (
    "En relacion con el expediente de referencia, y a los efectos previstos en "
    "el articulo 68 de la Ley 39/2015, se requiere al interesado para que "
    "aporte la documentacion que se relaciona a continuacion en el plazo de "
    "diez dias habiles contados desde el siguiente a la recepcion. "
)


def _documento_mixto(destino: Path) -> Path:
    doc = fitz.open()
    # Pagina 1: digital, mucho texto.  Con ella sola la media pasaba de 200.
    p = doc.new_page(width=595, height=842)
    p.insert_textbox(fitz.Rect(50, 50, 545, 800),
                     "Interesado: Vicente Alborch Ferrandis, DNI 46821357G.\n\n"
                     + _PARRAFO * 8, fontsize=10)

    # Pagina 2: la copia escaneada de otro documento, sin capa de texto.
    fuente = fitz.open()
    q = fuente.new_page(width=595, height=842)
    q.insert_textbox(fitz.Rect(60, 80, 535, 400),
                     "DOCUMENTO NACIONAL DE IDENTIDAD\n\nAPELLIDOS\nRuiz Navarro\n"
                     "NOMBRE\nAna\n\nDNI 39127242X\n", fontsize=16)
    pix = q.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72))
    fuente.close()
    r = doc.new_page(width=595, height=842)
    r.insert_image(r.rect, stream=pix.tobytes("png"))

    doc.save(destino)
    doc.close()
    return destino


@pytest.fixture(scope="module")
def resultado(tmp_path_factory):
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    carpeta = tmp_path_factory.mktemp("mixto")
    entrada = _documento_mixto(carpeta / "entrada.pdf")
    salida = carpeta / "salida.pdf"
    informe = PdfAnonymizationPipeline().process(
        input_path=entrada, output_path=salida, job_id="mixto", mode="strict",
    )
    return informe, salida


def test_la_pagina_digital_conserva_su_texto_seleccionable(resultado):
    _, salida = resultado
    with fitz.open(salida) as doc:
        texto = doc[0].get_text("text")
    assert "expediente" in texto, (
        "la pagina digital se entrego sin texto seleccionable: paso por el "
        "camino de imagen sin necesitarlo"
    )


def test_y_su_dato_personal_se_quita_del_texto(resultado):
    _, salida = resultado
    with fitz.open(salida) as doc:
        texto = doc[0].get_text("text")
    assert "46821357G" not in texto


def test_la_pagina_escaneada_se_mira_aunque_el_resto_tenga_texto(resultado):
    """Lo que el umbral de 200 caracteres impedia."""
    informe, _ = resultado
    assert informe.document_type == "pdf_hybrid"
    assert informe.ocr is not None and informe.ocr.pages_ocr_processed >= 1, (
        "la copia escaneada no paso por OCR: se entrego sin mirar"
    )


def test_solo_se_rasteriza_la_pagina_que_lo_necesita(resultado):
    informe, _ = resultado
    assert informe.ocr.pages_ocr_processed == 1
