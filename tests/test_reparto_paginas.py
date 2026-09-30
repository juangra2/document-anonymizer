"""El reparto es PAGINA A PAGINA con un criterio de superficie.

Antes se decidia por documento: si la media llegaba a 200 caracteres por pagina,
el documento entero iba por el camino digital sin pasar ni una pagina por OCR.
Dos folios de texto tapaban veinte paginas escaneadas, y una copia de DNI
pegada en un expediente se entregaba intacta y sin aviso.

La correccion NO es "toda pagina con imagen al OCR": el 51 % de las paginas del
corpus digital llevan el logo del membrete, y pasarlas por OCR les quitaria el
texto seleccionable y cambiaria una redaccion exacta por otra que no lo es.  Se
mira la fraccion de pagina cubierta (ver ocr_imagen_pagina en la config).
"""
import fitz
import pytest

from app.core.config import settings
from app.services.pdf.inspector import cobertura_imagenes, pagina_necesita_ocr


def _pagina_con_imagenes(fracciones: list[float], con_texto: bool = True) -> fitz.Page:
    """Pagina A4 con imagenes que cubren esas fracciones de su superficie."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if con_texto:
        page.insert_textbox(fitz.Rect(40, 40, 555, 200),
                            "Texto corriente de un expediente administrativo. " * 6,
                            fontsize=10)
    area = page.rect.width * page.rect.height
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), 0)
    pix.clear_with(200)
    y = 220.0
    for fraccion in fracciones:
        lado = (fraccion * area) ** 0.5
        rect = fitz.Rect(40, y, 40 + lado, y + lado)
        page.insert_image(rect, pixmap=pix)
        y += lado + 4
    return page


# --- la cobertura -----------------------------------------------------------

def test_la_cobertura_es_fraccion_de_pagina_no_pixeles():
    page = _pagina_con_imagenes([0.20])
    assert cobertura_imagenes(page) == pytest.approx(0.20, abs=0.01)


def test_las_imagenes_pequenas_no_suman():
    """Los adornos no se acumulan: logos de 0,3 % quedan fuera de la suma."""
    page = _pagina_con_imagenes([0.003, 0.003, 0.003, 0.003, 0.003])
    assert cobertura_imagenes(page) == 0.0


# --- la decision ------------------------------------------------------------

def test_el_logo_del_membrete_no_manda_la_pagina_al_ocr():
    """El 51 % del corpus digital: no se le puede quitar el texto por un logo."""
    assert not pagina_necesita_ocr(_pagina_con_imagenes([0.0038]))


def test_una_copia_pegada_si():
    assert pagina_necesita_ocr(_pagina_con_imagenes([0.20]))


def test_un_dni_a_tamano_real_si():
    assert pagina_necesita_ocr(_pagina_con_imagenes([0.074]))


def test_varias_miniaturas_de_un_anexo_se_suman():
    """Cuatro fotos de 3 % no llegan una a una; juntas son 12 %."""
    page = _pagina_con_imagenes([0.03, 0.03, 0.03, 0.03])
    assert pagina_necesita_ocr(page)


def test_una_firma_escaneada_sola_no():
    """3,4 %: por debajo del umbral.  La firma la trata el camino digital."""
    assert not pagina_necesita_ocr(_pagina_con_imagenes([0.034]))


def test_una_pagina_sin_texto_siempre_va_al_ocr():
    """Una pagina escaneada no tiene nada que conservar: se mira siempre."""
    assert pagina_necesita_ocr(_pagina_con_imagenes([0.003], con_texto=False))


def test_el_umbral_se_puede_cambiar(monkeypatch):
    monkeypatch.setattr(settings, "ocr_imagen_pagina", 0.02)
    assert pagina_necesita_ocr(_pagina_con_imagenes([0.034]))
