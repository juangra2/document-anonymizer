"""La red de seguridad no tacha entera la imagen de una firma pegada.

Requisito del ayuntamiento: la rubrica se conserva (test_signature_images_kept.py
fija que los detectores visuales de firma estan apagados).  Pero habia otra via:
la red de seguridad tacha ENTERA una imagen pegada si la segunda pasada lee
dentro un residuo de tipo fiable (PERSON incluido) o dos residuos.  Una firma
pegada pasa con facilidad del 1 % de la pagina, y la segunda pasada lee nombres
falsos en los trazos: la firma desaparecia.

Ahora una imagen con aspecto de firma (pocos trazos, poca tinta) no se tacha
entera: solo se repasa la zona del residuo, como fuera de una imagen.  Una copia
de DNI o un escrito pegado se siguen tachando enteros.
"""
from io import BytesIO

import fitz
from PIL import Image, ImageDraw

from app.services.pdf.scanned_redactor import _aplicar_red
from app.services.pii.models import PiiEntity

# Pagina A4 a 200 ppp.
_ANCHO, _ALTO = 1654, 2339
_MARCO = fitz.Rect(900, 1800, 1400, 1960)          # ~2 % de la pagina


def _pagina():
    return Image.new("RGB", (_ANCHO, _ALTO), "white")


def _firma(img):
    d = ImageDraw.Draw(img)
    x0, y0 = int(_MARCO.x0), int(_MARCO.y0)
    d.line([(x0 + 40, y0 + 110), (x0 + 120, y0 + 30), (x0 + 170, y0 + 120),
            (x0 + 260, y0 + 40), (x0 + 330, y0 + 115)], fill="navy", width=4)
    d.arc([x0 + 300, y0 + 30, x0 + 460, y0 + 130], 20, 300, fill="navy", width=4)


def _dni(img):
    d = ImageDraw.Draw(img)
    d.rectangle([int(_MARCO.x0), int(_MARCO.y0), int(_MARCO.x1), int(_MARCO.y1)], fill=(150, 170, 200))
    d.rectangle([int(_MARCO.x0) + 20, int(_MARCO.y0) + 15, int(_MARCO.x0) + 120, int(_MARCO.y1) - 15],
                fill=(60, 50, 40))


def _escrito(img):
    d = ImageDraw.Draw(img)
    for fila in range(8):
        for col in range(14):
            x = int(_MARCO.x0) + 10 + col * 34
            y = int(_MARCO.y0) + 8 + fila * 19
            d.rectangle([x, y, x + 24, y + 9], fill="black")       # palabras


def _residuo_dentro():
    centro = fitz.Rect(1050, 1860, 1200, 1890)
    return [(PiiEntity("PERSON", 0, 10, 0.8, text="x"), [centro])]


def _tinta_en_marco(img):
    recorte = img.crop((int(_MARCO.x0), int(_MARCO.y0), int(_MARCO.x1), int(_MARCO.y1))).convert("L")
    return sum(1 for p in recorte.getdata() if p < 60) / (recorte.width * recorte.height)


def test_la_firma_pegada_no_se_tacha_entera():
    img = _pagina()
    _firma(img)
    imagenes, zonas, _ = _aplicar_red(img, _residuo_dentro(), [_MARCO])
    assert imagenes == 0, "la red tacho entera la imagen de la firma"
    assert zonas == 1, "el residuo se repasa igual, solo su zona"
    assert _tinta_en_marco(img) < 0.2


def test_una_copia_de_dni_pegada_se_sigue_tachando_entera():
    img = _pagina()
    _dni(img)
    imagenes, _, _ = _aplicar_red(img, _residuo_dentro(), [_MARCO])
    assert imagenes == 1


def test_un_escrito_pegado_se_sigue_tachando_entero():
    img = _pagina()
    _escrito(img)
    imagenes, _, _ = _aplicar_red(img, _residuo_dentro(), [_MARCO])
    assert imagenes == 1


# --- el barrido del digital tampoco ------------------------------------------------------

def test_digital_el_nombre_de_la_firma_se_tacha_y_la_rubrica_se_queda(tmp_path):
    """El barrido tacha todas las apariciones del nombre, tambien el "Fdo.:"
    junto a la firma; la imagen de la rubrica, al lado, sale intacta."""
    from app.services.pdf.redactor import redact_pdf_digital
    from app.services.pii.analyzer import PiiAnalyzer

    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((58, 300), "Nombre y apellidos: Vicent Peris Llopis  DNI: 69026956T", fontsize=10)
    p.insert_text((58, 330), "Por la presente se le comunica la resolucion del expediente.", fontsize=10)
    p.insert_text((58, 640), "Fdo.: Vicent Peris Llopis", fontsize=10)
    img = _pagina()
    _firma(img)
    rubrica = img.crop((int(_MARCO.x0), int(_MARCO.y0), int(_MARCO.x1), int(_MARCO.y1)))
    caja = fitz.Rect(250, 600, 430, 658)                       # a la derecha del nombre
    buf = BytesIO()
    rubrica.save(buf, format="PNG")
    p.insert_image(caja, stream=buf.getvalue())
    doc.save(entrada)

    redact_pdf_digital(entrada, salida, "t", PiiAnalyzer())
    out = fitz.open(salida)[0]
    assert "Peris Llopis" not in out.get_text()
    assert out.get_images(), "la imagen de la firma desaparecio"
    antes = fitz.open(entrada)[0].get_pixmap(clip=caja, dpi=72).samples
    despues = out.get_pixmap(clip=caja, dpi=72).samples
    assert antes == despues, "la imagen de la firma cambio"
