"""Imagen de fondo: el formulario va escrito encima, no dentro de los pixeles.

Lo usan la red de seguridad (un fondo no es una copia pegada que tapar entera)
y el reparto de paginas (un fondo no convierte la pagina en un escaneo).
"""
from __future__ import annotations

import fitz


# Un fondo lleva encima un formulario: varias lineas de texto visible repartidas
# por buena parte de la imagen.  Un sello ("ES COPIA", "Compulsado", el de
# registro de entrada) son unas pocas lineas juntas, y la copia sigue siendo copia.
_LINEAS_ENCIMA_DE_UN_FONDO = 6
_ALTURA_CUBIERTA_DE_UN_FONDO = 0.40


def es_fondo(rect: fitz.Rect, bboxlog: list, trazas: list) -> bool:
    """True si sobre la imagen de `rect` va escrito texto VISIBLE de la pagina.

    Documento real: el escudo de una universidad de fondo (57,7 % de la pagina)
    con el formulario escrito encima.  La red lo tomaba por una copia pegada y
    lo tachaba entero.  Una copia pegada trae sus datos en los pixeles; un fondo
    lleva el texto de la pagina dibujado despues, encima, y repartido como un
    formulario: un sello de unas pocas lineas no la convierte en fondo.  El texto
    invisible de una capa OCR no cuenta: una copia escaneada con su capa OCR
    sigue siendo una copia.  El orden de dibujo (seqno) es la posicion en
    get_bboxlog."""
    orden = [i for i, (tipo, caja) in enumerate(bboxlog)
             if tipo == "fill-image" and (fitz.Rect(caja) & rect).get_area() >= 0.9 * rect.get_area()]
    if not orden:
        return False
    imagen_en = min(orden)
    lineas: set[int] = set()
    arriba, abajo = rect.y1, rect.y0
    for t in trazas:
        if t["type"] == 3 or t["opacity"] == 0 or t["seqno"] <= imagen_en:
            continue
        caja = fitz.Rect(t["bbox"])
        if rect.contains(fitz.Point((caja.x0 + caja.x1) / 2, (caja.y0 + caja.y1) / 2)):
            lineas.add(round(caja.y1 / 3))            # misma linea: misma base (+-3 pt)
            arriba, abajo = min(arriba, caja.y0), max(abajo, caja.y1)
    if len(lineas) < _LINEAS_ENCIMA_DE_UN_FONDO:
        return False
    return (abajo - arriba) >= _ALTURA_CUBIERTA_DE_UN_FONDO * rect.height
