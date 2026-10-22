"""Un fondo con el formulario escrito encima no es una copia pegada.

Un formulario puede llevar de fondo el escudo de la entidad, cubriendo media
pagina.  Si la red de seguridad lo tratara como una imagen pegada (una copia de
DNI en un expediente), al leer un nombre encima del escudo tacharia la imagen
entera, y con ella media pagina.

Lo que distingue un fondo de una copia: el fondo lleva el texto VISIBLE de la
pagina encima (el formulario se escribe sobre el escudo); una copia pegada
trae sus datos dentro de los pixeles.  Un fondo no cuenta como imagen pegada:
lo que la red lea encima se repasa por zona, como en el resto de la pagina.
El texto invisible de una capa OCR no cuenta como texto encima.
"""
import fitz

from app.services.pdf.scanned_redactor import _imagenes_pegadas_px

_ESCUDO = fitz.Rect(60, 180, 535, 760)          # ~57 % de un A4


def _imagen(page, rect):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
    pix.clear_with(235)
    page.insert_image(rect, pixmap=pix)


def _formulario(texto_invisible=False):
    """Cabecera, escudo grande de fondo y los campos encima."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "SOLICITUD DE TITULO UNIVERSITARIO", fontsize=11)
    page.insert_textbox(fitz.Rect(40, 780, 555, 830),
                        "Los datos se trataran conforme a la normativa de proteccion de datos. " * 2,
                        fontsize=7)
    _imagen(page, _ESCUDO)
    campos = [("NIF", "69026956T"), ("Nombre", "Paula"), ("Apellidos", "Martin Delgado"),
              ("Fecha de nacimiento", "12/03/1990"), ("Provincia de nacimiento", "Valencia"),
              ("Municipio de nacimiento", "Sueca"), ("Observaciones", "Sin observaciones"),
              ("Importe", "52,40 EUR"), ("N. de recibo", "2024001234")]
    for k, (etiqueta, valor) in enumerate(campos):
        y = 230 + 40 * k
        page.insert_text((80, y), f"{etiqueta}:", fontsize=9,
                         render_mode=3 if texto_invisible else 0)
        page.insert_text((260, y), valor, fontsize=9, render_mode=3 if texto_invisible else 0)
    return page


def test_el_escudo_de_fondo_de_un_formulario_no_es_una_imagen_pegada():
    assert _imagenes_pegadas_px(_formulario(), 2.0) == []


def test_una_copia_pegada_sin_texto_encima_lo_sigue_siendo():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(fitz.Rect(40, 40, 555, 200),
                        "Se adjunta copia del documento de identidad del interesado. " * 5, fontsize=10)
    _imagen(page, fitz.Rect(100, 300, 450, 520))
    assert len(_imagenes_pegadas_px(page, 2.0)) == 1


def test_una_copia_con_un_sello_de_texto_encima_sigue_siendo_copia():
    """En los expedientes la fotocopia del DNI suele llevar un sello visible
    encima ("ES COPIA", "Compulsado", el de registro de entrada).  Un sello son
    unas pocas lineas juntas; un fondo de formulario, campos por toda la imagen."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(fitz.Rect(40, 40, 555, 200),
                        "Se adjunta copia del documento de identidad del interesado. " * 5, fontsize=10)
    copia = fitz.Rect(100, 300, 450, 520)
    _imagen(page, copia)
    for k, linea in enumerate(["ES COPIA", "COMPULSADO", "REGISTRO DE ENTRADA",
                               "N. 2024/012345", "12/03/2024 10:41", "Ajuntament de Valencia"]):
        page.insert_text((300, 440 + 11 * k), linea, fontsize=8)
    assert len(_imagenes_pegadas_px(page, 2.0)) == 1


def test_el_texto_invisible_de_una_capa_ocr_no_hace_fondo():
    """Una copia escaneada con capa OCR invisible encima sigue siendo una copia."""
    page = _formulario(texto_invisible=True)
    assert len(_imagenes_pegadas_px(page, 2.0)) == 1
