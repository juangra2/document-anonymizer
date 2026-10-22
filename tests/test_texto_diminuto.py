"""El texto diminuto no sobrevive al tachado.

Un PDF puede llevar el contenido de un QR en texto blanco de 1 punto debajo de
la imagen ("<nombre> | <DNI>").  No se ve, pero se copia del PDF entregado.  La
barra del QR tapa la imagen y no el texto de debajo, ese texto cae en la franja
del pie, y scrub(hidden_text=True) solo quita el texto marcado como invisible,
no el de 1 punto.

Se quita de la salida, sin pintar nada, el texto de menos de 2 puntos que no se
ve: en blanco, invisible o tapado por una imagen (el diminuto que se ve, como las
cotas de un plano, se queda; ver test_texto_diminuto_planos.py).  El texto blanco
de tamaño normal NO se toca: puede ir sobre un fondo de color.
"""
import fitz

from app.services.pdf.redactor import redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def test_el_texto_de_un_punto_no_sobrevive(tmp_path):
    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 300), "Por la presente se comunica la resolucion del expediente.", fontsize=10)
    p.insert_text((520, 755), "Eduardo Hernández Álvarez | 18187660L", fontsize=1, color=(1, 1, 1))
    # Rotulo blanco de tamaño normal sobre una banda azul: se ve y se queda.
    p.draw_rect(fitz.Rect(40, 380, 555, 400), color=None, fill=(0.1, 0.2, 0.6))
    p.insert_text((50, 394), "DATOS DE LA SOLICITUD", fontsize=10, color=(1, 1, 1))
    doc.save(entrada)

    redact_pdf_digital(entrada, salida, "t", analyzer)
    texto = fitz.open(salida)[0].get_text()
    assert "18187660L" not in texto and "Eduardo" not in texto
    assert "DATOS DE LA SOLICITUD" in texto
    assert "resolucion del expediente" in texto
