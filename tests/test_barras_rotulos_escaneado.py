"""Camino escaneado: la barra de un valor no muerde el rotulo de encima.

Formulario real escaneado (caso dificil 6): con la etiqueta pequena justo encima
del valor, la barra del valor subia y tapaba el pie del rotulo ("COGNOMS",
"NOM", "Nº RECIBO", "DATA"... con un 14-34 % en negro).  Es el equivalente del
arreglo del camino digital (cajas de letra ajustadas).  Antes de pintar, si una
barra muerde el BORDE inferior de un rotulo que esta encima, empieza justo debajo.
Si el OCR junto rotulo y valor en una sola caja, la barra queda dentro de ella y
no se toca: recortarla destaparia el valor.
"""
import fitz

from app.services.ocr.models import OcrToken
from app.services.pdf.scanned_redactor import _sin_pisar_rotulos


def _t(texto, x0, y0, x1, y1):
    return OcrToken(texto, 0.9, fitz.Rect(x0, y0, x1, y1))


def test_la_barra_que_muerde_el_pie_del_rotulo_empieza_debajo():
    rotulo = _t("COGNOMS / APELLIDOS", 100, 100, 400, 124)
    barra = fitz.Rect(98, 118, 330, 160)              # sube 6 px dentro del rotulo
    (r,) = _sin_pisar_rotulos([barra], [rotulo])
    assert r.y0 >= rotulo.rect.y1 and r.y1 == 160


def test_si_el_ocr_junto_rotulo_y_valor_la_barra_no_se_toca():
    junto = _t("NOM / NOMBRE PAULA", 100, 100, 400, 160)
    barra = fitz.Rect(250, 104, 400, 156)             # el valor, dentro de la caja
    (r,) = _sin_pisar_rotulos([barra], [junto])
    assert r == barra


def test_un_valor_no_es_un_rotulo():
    valor = _t("MARTIN DELGADO", 100, 100, 400, 124)
    barra = fitz.Rect(98, 118, 330, 160)
    (r,) = _sin_pisar_rotulos([barra], [valor])
    assert r == barra
