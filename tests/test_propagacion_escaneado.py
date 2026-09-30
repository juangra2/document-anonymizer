"""El camino escaneado clasifica el documento entero, no pagina a pagina.

Por que hacia falta: el recorrido decide sobre cada pagina segun la termina, y
en ese momento aun no conoce a las vecinas.  Asi, el anexo que repite el
justificante de una autoliquidacion pero no su cabecera se entregaba con
redaccion selectiva mientras el impreso salia entero en negro.

La clasificacion se hace ahora una sola vez al terminar el recorrido, con el
texto de todas las paginas, igual que en el camino digital.
"""
import fitz
import pytest

from app.services.pdf.scanned_redactor import _propagar_redaccion_total

_IMPRESO = (
    "AUTOLIQUIDACION - MODELO 600\nSUJETO PASIVO\nRosa Ortega Garrido\n"
    "HECHO IMPONIBLE\nBase imponible 7.777,31\nCuota tributaria 311,09\n"
    "TOTAL A INGRESAR 311,09\nReferencia: 600-2023-362396\n"
)
_ANEXO_MISMO = (
    "ANEXO DE DOCUMENTACION APORTADA\nReferencia: 600-2023-362396\n"
    "Se adjunta copia del justificante de ingreso.\n"
)
_AJENO = "Notificacion de inicio de expediente de disciplina urbanistica.\n"


def _documento(paginas: int) -> fitz.Document:
    doc = fitz.open()
    for i in range(paginas):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"contenido de la pagina {i}", fontsize=14)
    return doc


def _tiene_texto(doc: fitz.Document, indice: int) -> bool:
    return bool(doc[indice].get_text("text").strip())


def test_el_anexo_con_el_mismo_justificante_se_tacha():
    doc = _documento(2)
    anadidas = _propagar_redaccion_total(
        doc, {0: _IMPRESO, 1: _ANEXO_MISMO}, ya_redactadas={0: "tax_form"},
    )
    assert 1 in anadidas
    assert not _tiene_texto(doc, 1), "la pagina arrastrada sigue legible"
    doc.close()


def test_una_pagina_ajena_no_se_toca():
    doc = _documento(2)
    anadidas = _propagar_redaccion_total(
        doc, {0: _IMPRESO, 1: _AJENO}, ya_redactadas={0: "tax_form"},
    )
    assert anadidas == {}
    assert _tiene_texto(doc, 1), "se tacho contenido legitimo"
    doc.close()


def test_no_repite_lo_que_ya_estaba_tachado():
    doc = _documento(1)
    anadidas = _propagar_redaccion_total(
        doc, {0: _IMPRESO}, ya_redactadas={0: "tax_form"},
    )
    assert anadidas == {}
    doc.close()


def test_sin_textos_no_hace_nada():
    doc = _documento(1)
    assert _propagar_redaccion_total(doc, {}, ya_redactadas={}) == {}
    assert _tiene_texto(doc, 0)
    doc.close()


def test_no_se_sale_del_documento():
    """Una entrega parcial tiene menos paginas que textos recogidos."""
    doc = _documento(1)
    anadidas = _propagar_redaccion_total(
        doc, {0: _IMPRESO, 1: _ANEXO_MISMO}, ya_redactadas={0: "tax_form"},
    )
    assert anadidas == {}
    doc.close()


def test_el_tachado_borra_de_verdad_y_no_pinta_encima():
    """Un rectangulo negro encima dejaria el contenido dentro del PDF."""
    doc = _documento(2)
    _propagar_redaccion_total(
        doc, {0: _IMPRESO, 1: _ANEXO_MISMO}, ya_redactadas={0: "tax_form"},
    )
    assert "contenido de la pagina 1" not in doc[1].get_text("text")
    doc.close()
