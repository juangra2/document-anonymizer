"""El reparto es PAGINA A PAGINA: que paginas van ENTERAS al OCR.

Decidir por documento dejaria sin OCR paginas escaneadas de un documento con
texto; decidir por la suma de superficie de las imagenes mandaria al OCR la
pagina entera por una copia de DNI pegada, y la pagina perderia su texto.

Las imagenes de una pagina con texto se leen recortadas en el pase digital
(pdf/imagenes_pegadas.py).  La pagina entera solo
va al OCR si no tiene texto o si una imagen que no es un fondo ocupa al menos
la mitad: un escaneo pegado como pagina.
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
    assert not pagina_necesita_ocr(_pagina_con_imagenes([0.0038]))


def test_una_copia_pegada_se_lee_recortada_no_con_la_pagina():
    for fraccion in (0.074, 0.20, 0.034):              # DNI, copia, firma
        assert not pagina_necesita_ocr(_pagina_con_imagenes([fraccion]))


def test_varias_miniaturas_tampoco():
    assert not pagina_necesita_ocr(_pagina_con_imagenes([0.03, 0.03, 0.03, 0.03]))


def test_un_escaneo_pegado_como_pagina_si():
    assert pagina_necesita_ocr(_pagina_con_imagenes([0.80]))


def test_un_fondo_con_el_formulario_encima_no():
    """El escudo de fondo de un formulario (media pagina): la pagina conserva su
    texto y sus etiquetas, que el camino escaneado morderia."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), 0)
    pix.clear_with(235)
    page.insert_image(fitz.Rect(60, 180, 535, 760), pixmap=pix)
    for k in range(9):
        page.insert_text((80, 230 + 40 * k), f"Campo {k}: valor del formulario", fontsize=9)
    page.insert_textbox(fitz.Rect(40, 40, 555, 150), "Cabecera del formulario. " * 10, fontsize=9)
    assert not pagina_necesita_ocr(page)


def test_una_pagina_sin_texto_siempre_va_al_ocr():
    """Una pagina escaneada no tiene nada que conservar: se mira siempre."""
    assert pagina_necesita_ocr(_pagina_con_imagenes([0.003], con_texto=False))


def test_el_umbral_se_puede_cambiar(monkeypatch):
    monkeypatch.setattr(settings, "ocr_imagen_pagina_completa", 0.15)
    assert pagina_necesita_ocr(_pagina_con_imagenes([0.20]))
