"""Una pagina enorme se reduce y se mira; antes se saltaba entera.

El atajo que habia: una pagina con una imagen de mas de 25 MP cubriendo el 70 %
se daba por "plano o foto", se le tachaban solo los codigos visuales y se
entregaba sin leer.  No es un caso raro — un A4 escaneado a 600 DPI son 35 MP, o
sea un expediente archivado a buena resolucion, y una foto de movil moderna
tambien pasa de ahi.

Medido antes de elegir el tope: sobre un certificado con letra menuda, el OCR
encuentra los mismos datos desde 600 DPI hasta 102 DPI equivalentes.  Reducir no
cuesta lectura; saltarse la pagina si.
"""
import fitz
import pytest

from app.core.config import settings
from app.services.pdf.scanned_redactor import _zoom_con_tope


def _pagina(ancho: float, alto: float) -> fitz.Page:
    doc = fitz.open()
    return doc.new_page(width=ancho, height=alto)


def _megapixeles(page: fitz.Page, zoom: float) -> float:
    return (page.rect.width * zoom) * (page.rect.height * zoom) / 1e6


def test_una_pagina_normal_no_se_toca():
    """Un A4 a 200 DPI son 3,9 MP: por debajo del tope, se renderiza igual."""
    page = _pagina(595, 842)
    zoom = _zoom_con_tope(page, 200, 8.0)
    assert zoom == pytest.approx(200 / 72)
    assert _megapixeles(page, zoom) < 8.0


def test_una_pagina_a_un_punto_por_pixel_se_reduce():
    """Lo que escriben muchos conversores de imagen a PDF: un A4 a 200 DPI
    declarado como una pagina de 58 x 82 cm.  A 200 DPI daria 30 MP."""
    page = _pagina(1654, 2338)
    sin_tope = _megapixeles(page, 200 / 72)
    assert sin_tope > 25.0
    zoom = _zoom_con_tope(page, 200, 8.0)
    assert _megapixeles(page, zoom) == pytest.approx(8.0, abs=0.1)


def test_un_plano_a0_tambien_se_reduce():
    page = _pagina(2384, 3370)
    zoom = _zoom_con_tope(page, 200, 8.0)
    assert _megapixeles(page, zoom) == pytest.approx(8.0, abs=0.1)


def test_sin_tope_configurado_se_renderiza_tal_cual():
    page = _pagina(2384, 3370)
    assert _zoom_con_tope(page, 200, 0.0) == pytest.approx(200 / 72)


def test_la_imagen_dominante_ya_no_salta_paginas():
    """La red que evita que el atajo vuelva.

    `_is_image_dominant_page` sigue existiendo porque la deteccion de plano la
    usa como una de sus senales.  Lo que no puede volver es que por si sola
    haga que una pagina se entregue sin leer.
    """
    import inspect

    from app.services.pdf import scanned_redactor

    fuente = inspect.getsource(scanned_redactor.redact_pdf_scanned)
    assert "_is_image_dominant_page" not in fuente, (
        "el bucle de paginas vuelve a decidir por imagen dominante: una pagina "
        "grande se entregaria sin mirar"
    )


def test_el_tope_por_defecto_deja_margen_de_sobra():
    """8 MP en un A4 son ~290 DPI; el OCR ya lee igual desde ~100 DPI."""
    assert settings.ocr_max_megapixels >= 4.0
