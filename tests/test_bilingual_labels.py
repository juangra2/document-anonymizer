"""Etiquetas bilingües valenciano/castellano con valor en línea siguiente."""
import fitz
import pytest

from app.services.layout._digital_analysis import analyze_digital_layout
from app.services.pdf.extractor import PageText, WordBox
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _ents(text):
    return [
        (e.entity_type, text[e.start:e.end])
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
    ]


# --- etiqueta bilingüe y valor en la línea siguiente -------------------------

def test_nom_nombre_next_line():
    assert ("PERSON", "JUAN") in _ents("NOM / NOMBRE\nJUAN")


def test_cognoms_apellidos_next_line():
    assert ("PERSON", "VELA RIUS") in _ents("COGNOMS / APELLIDOS\nVELA RIUS")


# --- variantes de la etiqueta compuesta --------------------------------------

def test_reversed_order_and_single_valencian():
    assert ("PERSON", "JUAN") in _ents("NOMBRE / NOM\nJUAN")
    assert ("PERSON", "JUAN") in _ents("NOM\nJUAN")
    assert ("PERSON", "GRAU") in _ents("COGNOM / APELLIDO\nGRAU")


# --- otros campos bilingües ---------------------------------------------------

@pytest.mark.parametrize("text,etype,value", [
    ("DNI / NIF\n47.215.601-M", "ES_DNI", "47.215.601-M"),
    ("DNI / NIF\n12345678Z", "ES_DNI", "12345678Z"),
    ("TELÈFON / TELÉFONO\n612 345 678", "PHONE_NUMBER", "612 345 678"),
    ("ADREÇA / DIRECCIÓN\nCalle Mayor 12", "ADDRESS", "Calle Mayor 12"),
    ("MUNICIPI / MUNICIPIO\nSueca", "ADDRESS", "Sueca"),
    ("DATA / FECHA\n06/11/2019", "DATE_TIME", "06/11/2019"),
])
def test_bilingual_fields(text, etype, value):
    assert (etype, value) in _ents(text), text


# --- guardas ------------------------------------------------------------------

def test_empty_field_does_not_absorb_next_label():
    # Campo vacío: la etiqueta siguiente no puede tomarse como valor.
    out = _ents("NOM / NOMBRE\nCOGNOMS / APELLIDOS")
    assert not any(t == "PERSON" for t, _ in out)


def test_data_fecha_requires_date_shape():
    out = _ents("DATA / FECHA\nValencia")
    assert not any(t == "DATE_TIME" for t, _ in out)


# --- no-regresión castellano ---------------------------------------------------

def test_spanish_colon_patterns_unchanged():
    assert ("PERSON", "Juan Pérez García") in _ents("Nombre: Juan Pérez García")
    assert ("ES_DNI", "12345678Z") in _ents("DNI: 12345678Z")


# --- path digital estructural: etiqueta compuesta en fila, valor debajo --------

_CW, _H = 7.0, 10.0


def _w(text, x0, y0):
    return WordBox(text=text, start=0, end=0,
                   rect=fitz.Rect(x0, y0, x0 + len(text) * _CW, y0 + _H),
                   block_no=0, line_no=0, word_no=0)


def test_digital_vertical_compound_label():
    words = [
        _w("NOM", 0, 0), _w("/", 30, 0), _w("NOMBRE", 45, 0),
        _w("JUAN", 0, 14),
    ]
    page = PageText(page_index=0, text=" ".join(w.text for w in words), words=words)
    a = analyze_digital_layout(page)
    juan = words[3]
    covered = any(
        r.x0 <= (juan.rect.x0 + juan.rect.x1) / 2 <= r.x1
        and r.y0 <= (juan.rect.y0 + juan.rect.y1) / 2 <= r.y1
        for r in a.redaction_rects
    )
    assert covered, "el valor bajo la etiqueta compuesta debe redactarse"
