"""Los pasos finales del digital no rompen ni cuelgan un plano.

Un plano lleva decenas de miles de trazos y miles de fragmentos de texto de
menos de 2 pt (las cotas, que se leen con zoom).  Poner una marca por fragmento
no terminaria y se llevaria las cotas.  Solo se quita el texto diminuto que NO
se ve (por ejemplo, el contenido de un QR en blanco a 1 punto debajo de la
imagen): en blanco, invisible o tapado por una imagen dibujada despues.  Asi las cotas se quedan aunque la
pagina no se detecte como plano (un A3 no se detecta).

Las paginas de plano tampoco entran en el barrido de todas las apariciones:
tienen su propio tratamiento, que solo busca el cajetin.
"""
import time

import fitz

from app.services.pdf.redactor import quitar_texto_diminuto, redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()

_COTAS = ["h=3.45", "Ø 200", "e 1:50", "+12.50"]
_NOMBRE = "Vicent Peris Llopis"


def _documento(ruta, ancho, alto):
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((58, 300), f"Nombre y apellidos: {_NOMBRE}  DNI: 69026956T", fontsize=10)
    p.insert_text((58, 330), "Por la presente se le comunica la resolucion del expediente.", fontsize=10)
    plano = doc.new_page(width=ancho, height=alto)
    for i in range(2500):                                  # dibujo tecnico
        x = 40 + (i * 7) % (ancho - 120)
        y = 40 + (i * 13) % (alto - 120)
        plano.draw_line((x, y), (x + 30, y + 5))
    for i in range(400):                                   # cotas diminutas, visibles
        x = 60 + (i * 37) % (ancho - 160)
        y = 60 + (i * 23) % (alto - 160)
        plano.insert_text((x, y), _COTAS[i % len(_COTAS)], fontsize=1.5)
    plano.insert_text((ancho - 300, alto - 60), f"Promotor: {_NOMBRE}", fontsize=8)
    doc.save(ruta)


def _cotas(pagina):
    texto = pagina.get_text()
    return sum(texto.count(c) for c in _COTAS)


def test_plano_detectado_sale_intacto_y_rapido(tmp_path):
    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _documento(entrada, 2384, 1684)                        # A1
    antes = _cotas(fitz.open(entrada)[1])
    inicio = time.perf_counter()
    redact_pdf_digital(entrada, salida, "t", analyzer)
    assert time.perf_counter() - inicio < 60
    out = fitz.open(salida)
    assert _NOMBRE not in out[0].get_text()
    assert _cotas(out[1]) == antes, "se quitaron cotas del plano"


def test_a3_no_detectado_como_plano_conserva_las_cotas(tmp_path):
    """Un A3 no es plano para el detector: pasa por el camino normal, y aun
    asi las cotas se quedan y el nombre repetido se tacha."""
    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _documento(entrada, 1191, 842)                         # A3 apaisado
    antes = _cotas(fitz.open(entrada)[1])
    inicio = time.perf_counter()
    redact_pdf_digital(entrada, salida, "t", analyzer)
    assert time.perf_counter() - inicio < 60
    out = fitz.open(salida)
    assert _cotas(out[1]) == antes, "se quitaron cotas del A3"
    assert _NOMBRE not in " ".join(out[1].get_text().split())


# --- que texto diminuto se quita -------------------------------------------------------

def _imagen(pagina, rect):
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
    pm.clear_with(200)
    pagina.insert_image(rect, pixmap=pm)


def test_solo_se_quita_el_texto_diminuto_que_no_se_ve():
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((300, 100), "DEBAJO-DE-LA-IMAGEN", fontsize=1)    # como el texto de un QR
    _imagen(p, fitz.Rect(290, 90, 340, 110))
    _imagen(p, fitz.Rect(290, 150, 340, 170))
    p.insert_text((300, 160), "ENCIMA-DE-LA-IMAGEN", fontsize=1)    # se ve: se queda
    p.insert_text((50, 200), "INVISIBLE", fontsize=1, render_mode=3)
    p.insert_text((50, 220), "EN-BLANCO", fontsize=1, color=(1, 1, 1))
    p.insert_text((50, 240), "COTA-VISIBLE", fontsize=1)
    p.insert_text((50, 300), "Texto normal en blanco", fontsize=10, color=(1, 1, 1))

    quitar_texto_diminuto(doc)
    texto = doc[0].get_text()
    for oculto in ("DEBAJO-DE-LA-IMAGEN", "INVISIBLE", "EN-BLANCO"):
        assert oculto not in texto, oculto
    for visible in ("COTA-VISIBLE", "ENCIMA-DE-LA-IMAGEN", "Texto normal en blanco"):
        assert visible in texto, visible
