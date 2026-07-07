"""Tema 1: campos ordenante/beneficiario (colon, no-colon, glued, cuenta)."""
import fitz

from app.services.layout._digital_analysis import analyze_digital_layout
from app.services.pdf.extractor import PageText, WordBox
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _ents(text, types):
    return [
        (e.entity_type, text[e.start:e.end])
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type in types
    ]


# --- colon / no-colon / next-line ------------------------------------------

def test_ordenante_colon():
    out = _ents("ORDENANTE: De La Fuente Saez Notarios C.B.", {"PERSON"})
    assert ("PERSON", "De La Fuente Saez Notarios C.B.") in out


def test_beneficiario_colon():
    out = _ents("Beneficiario: Registro Mercantil de Madrid", {"PERSON"})
    assert ("PERSON", "Registro Mercantil de Madrid") in out


def test_ordenante_no_colon():
    out = _ents("ORDENANTE De La Fuente Saez Notarios", {"PERSON"})
    assert any(v == "De La Fuente Saez Notarios" for _, v in out)


def test_ordenante_next_line():
    out = _ents("Ordenante:\nMaria Luisa Gomez Perez\n", {"PERSON"})
    assert any(v == "Maria Luisa Gomez Perez" for _, v in out)


# --- glued (MinerU bank-table rows) -----------------------------------------

def test_glued_party_labels_real_tomo01_row():
    text = ("FECHA OPERACION06/11/2019: ORDENANTEDE LA FUENTE SAEZ NOTARIOS "
            "C.B. BENEFICIARIOREGISTRO MERCANTIL DE MADRID")
    out = _ents(text, {"PERSON"})
    vals = [v for _, v in out]
    assert "DE LA FUENTE SAEZ NOTARIOS C.B." in vals
    assert "REGISTRO MERCANTIL DE MADRID" in vals


def test_lowercase_prose_beneficiario_not_matched():
    assert _ents("el beneficiario podrá ejercitar sus derechos", {"PERSON"}) == []


# --- entidad bancaria ---------------------------------------------------------

def test_entidad_colon():
    assert ("PERSON", "Banco Santander S.A.") in _ents(
        "ENTIDAD: Banco Santander S.A.", {"PERSON"})


def test_entidad_bancaria_and_banco_colon():
    assert any(v == "CaixaBank" for _, v in _ents(
        "Entidad bancaria: CaixaBank", {"PERSON"}))
    assert any(v == "Sabadell" for _, v in _ents("BANCO: Sabadell", {"PERSON"}))


def test_entidad_no_colon():
    assert any(v == "Banco Santander" for _, v in _ents(
        "Entidad Banco Santander", {"PERSON"}))


def test_glued_party_stops_before_entidad():
    out = _ents("ORDENANTEJUAN PEREZ RUIZ ENTIDADBANCO SANTANDER", {"PERSON"})
    assert any(v == "JUAN PEREZ RUIZ" for _, v in out)


def test_entidad_prose_not_matched():
    assert _ents("la entidad procederá al abono en el banco indicado",
                 {"PERSON"}) == []


# --- cuenta del beneficiario/ordenante ---------------------------------------

def test_cuenta_beneficiario_with_digits():
    out = _ents("Cuenta del beneficiario: 12345678901234567890", {"IBAN_CODE"})
    assert any(v == "12345678901234567890" for _, v in out)


def test_cuenta_beneficiario_prose_not_matched():
    assert _ents("Cuenta del beneficiario: la indicada arriba", {"IBAN_CODE"}) == []


# --- celda adyacente en path digital -----------------------------------------

_CW = 7.0
_H = 10.0


def _w(text, x0, y0):
    return WordBox(text=text, start=0, end=0,
                   rect=fitz.Rect(x0, y0, x0 + len(text) * _CW, y0 + _H),
                   block_no=0, line_no=0, word_no=0)


def test_digital_adjacent_cell_value_redacted():
    words = [
        _w("Ordenante", 0, 0),
        _w("Juan", 200, 0), _w("Perez", 240, 0), _w("Garcia", 290, 0),
    ]
    page = PageText(page_index=0, text=" ".join(w.text for w in words), words=words)
    a = analyze_digital_layout(page)
    covered = [
        w for w in words[1:]
        if any(r.x0 <= (w.rect.x0 + w.rect.x1) / 2 <= r.x1
               and r.y0 <= (w.rect.y0 + w.rect.y1) / 2 <= r.y1
               for r in a.redaction_rects)
    ]
    assert len(covered) == 3, "adjacent-cell value must be redacted"


# --- no-regresión ------------------------------------------------------------

def test_dates_untouched():
    out = _ents("Fecha: 06/11/2019", {"DATE_TIME"})
    assert any(v == "06/11/2019" for _, v in out)


def test_material_phrases_still_not_person():
    assert _ents("Hormigón Señalización", {"PERSON"}) == []
