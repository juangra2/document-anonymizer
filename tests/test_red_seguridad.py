"""La red de seguridad: lo que la segunda pasada todavia lee, no se entrega.

Si lo que la segunda pasada encuentra tras tachar solo se contara como residuo
y se pidiera revision, el sistema sabria que un dato sigue a la vista y lo
entregaria igual.  La respuesta es la misma que con las paginas ilegibles
—tachar, no solo avisar—, pero proporcional:

  - dentro de una imagen pegada en una pagina con texto, la imagen entera;
  - en cualquier otro sitio, solo la zona del residuo.

Y con dos cuidados, que es lo que fijan estas pruebas: en una pagina escaneada
la "imagen" es la pagina, asi que la regla de imagen no se le aplica; y tachar
una imagen entera exige un residuo de tipo fiable o dos, porque la segunda
pasada tambien tiene falsos positivos.
"""
import fitz
from PIL import Image

from app.core.config import settings
from app.services.pdf.scanned_redactor import (
    _aplicar_red,
    _imagenes_pegadas_px,
)
from app.services.pii.models import PiiEntity

_NEGRO = (0, 0, 0)


def _lienzo() -> Image.Image:
    return Image.new("RGB", (1000, 1400), "white")


def _residuo(tipo: str, x0, y0, x1, y1):
    return (PiiEntity(tipo, 0, 5, 0.9, "segunda_pasada", "x"), [fitz.Rect(x0, y0, x1, y1)])


_IMAGEN = fitz.Rect(100, 700, 900, 1300)     # una copia pegada en la mitad de abajo


def test_un_dni_dentro_de_una_imagen_pegada_tacha_la_imagen_entera():
    img = _lienzo()
    n_img, n_zon, _ = _aplicar_red(img, [_residuo("ES_DNI", 300, 900, 450, 930)], [_IMAGEN])
    assert n_img == 1 and n_zon == 0
    assert img.getpixel((150, 750)) == _NEGRO, "una esquina de la imagen sigue a la vista"
    assert img.getpixel((850, 1250)) == _NEGRO
    assert img.getpixel((500, 300)) != _NEGRO, "se tacho texto de fuera de la imagen"


def test_un_codigo_postal_suelto_no_borra_una_imagen():
    """Puede ser un falso positivo: no basta para tachar la foto de un anexo."""
    img = _lienzo()
    n_img, n_zon, _ = _aplicar_red(img, [_residuo("POSTAL_CODE", 300, 900, 400, 930)], [_IMAGEN])
    assert n_img == 0 and n_zon == 1
    assert img.getpixel((150, 750)) != _NEGRO, "se borro la imagen por un solo codigo postal"
    assert img.getpixel((350, 915)) == _NEGRO, "y el propio residuo tiene que quedar tapado"


def test_dos_residuos_cualesquiera_en_la_misma_imagen_si():
    img = _lienzo()
    residuos = [_residuo("POSTAL_CODE", 300, 900, 400, 930),
                _residuo("PHONE_NUMBER", 300, 1000, 500, 1030)]
    n_img, _, _ = _aplicar_red(img, residuos, [_IMAGEN])
    assert n_img == 1
    assert img.getpixel((150, 750)) == _NEGRO


def test_fuera_de_las_imagenes_solo_se_repasa_la_zona():
    img = _lienzo()
    n_img, n_zon, _ = _aplicar_red(img, [_residuo("PERSON", 200, 200, 400, 230)], [_IMAGEN])
    assert n_img == 0 and n_zon == 1
    assert img.getpixel((300, 215)) == _NEGRO
    assert img.getpixel((300, 400)) != _NEGRO, "se tacho de mas fuera del residuo"


# --- que cuenta como imagen pegada --------------------------------------------

def _pagina(con_texto: bool, fraccion_imagen: float) -> fitz.Page:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if con_texto:
        page.insert_textbox(fitz.Rect(40, 40, 555, 200),
                            "Expediente de solicitud del interesado. " * 6, fontsize=10)
    lado = (fraccion_imagen * page.rect.width * page.rect.height) ** 0.5
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
    pix.clear_with(180)
    page.insert_image(fitz.Rect(60, 300, 60 + lado, 300 + lado), pixmap=pix)
    return page


def test_en_una_pagina_escaneada_la_pagina_no_es_una_imagen_pegada():
    """Riesgo 1: un residuo tacharia la pagina entera."""
    assert _imagenes_pegadas_px(_pagina(con_texto=False, fraccion_imagen=0.9), 2.0) == []


def test_un_logo_no_es_una_copia_pegada():
    assert _imagenes_pegadas_px(_pagina(con_texto=True, fraccion_imagen=0.003), 2.0) == []


def test_una_copia_pegada_si_y_en_pixeles_del_render():
    marcos = _imagenes_pegadas_px(_pagina(con_texto=True, fraccion_imagen=0.20), 2.0)
    assert len(marcos) == 1
    assert marcos[0].x0 == 120 and marcos[0].y0 == 600, "no esta en coordenadas del render"


# --- que no pueda desaparecer en silencio ---------------------------------------

def test_sin_segunda_pasada_el_arranque_avisa(monkeypatch):
    from app.core.logging import warn_if_safety_net_off

    monkeypatch.setattr(settings, "ocr_second_pass_verification", False)
    assert warn_if_safety_net_off() is True


def test_con_segunda_pasada_no_hay_aviso():
    from app.core.logging import warn_if_safety_net_off

    assert settings.ocr_second_pass_verification is True
    assert warn_if_safety_net_off() is False


# --- el aviso de "siguen legibles tras la red" -------------------------------------
# Con todos los tipos saltaria sobre todo por falsos positivos de la segunda
# pasada.  Un aviso que pide revision manual y casi nunca acierta acaba ignorado.  Solo lo dan los tipos fiables; lo
# demas se sigue tapando igual y se sigue contando, pero no pide revision.

def test_un_nombre_que_sigue_legible_no_pide_revision():
    from app.services.pdf.scanned_redactor import aviso_de_residuos

    aviso, revision = aviso_de_residuos([_residuo("PERSON", 0, 0, 1, 1)[0],
                                         _residuo("ADDRESS", 0, 0, 1, 1)[0]])
    assert aviso is None and revision is False


def test_un_dni_que_sigue_legible_si():
    from app.services.pdf.scanned_redactor import aviso_de_residuos

    aviso, revision = aviso_de_residuos([_residuo("PERSON", 0, 0, 1, 1)[0],
                                         _residuo("ES_DNI", 0, 0, 1, 1)[0]])
    assert aviso and "Manual review" in aviso and revision is True


def test_los_tipos_del_aviso():
    from app.services.pdf.scanned_redactor import TIPOS_AVISO_RED

    assert TIPOS_AVISO_RED == {"ES_DNI", "ES_NIE", "IBAN_CODE", "ES_CIF"}
