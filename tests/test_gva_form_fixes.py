"""Formularios de la GVA: celdas solapadas, celdas-nombre,
FPs de firma/DNI y nombres en ficheros adjuntos."""
import fitz

from app.services.layout import _digital_analysis as da
from app.services.pdf.extractor import PageText, WordBox
from app.services.pdf.redactor import _standalone_name_cell
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _w(text, x0, y0, y1):
    return WordBox(text=text, start=0, end=0,
                   rect=fitz.Rect(x0, y0, x0 + len(text) * 7.0, y1),
                   block_no=0, line_no=0, word_no=0)


def _ents(text, types=None):
    return [
        (e.entity_type, text[e.start:e.end])
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if types is None or e.entity_type in types
    ]


# --- celdas solapadas (gap negativo) y conservación de la etiqueta ------------

def test_vertical_value_with_overlapping_cells():
    # Formulario compacto: la caja del valor solapa ~2.5pt con la de la
    # etiqueta (gap negativo).  Debe redactarse el valor y conservar la
    # etiqueta (rect recortado por debajo de ella).
    label = [_w("NOM", 50, 232, 241), _w("/", 73, 232, 241), _w("NOMBRE", 81, 232, 241)]
    value = [_w("JUAN", 45, 238.5, 250)]
    page = PageText(page_index=0, text="NOM / NOMBRE JUAN",
                    words=label + value)
    a = da.analyze_digital_layout(page)
    cx, cy = (value[0].rect.x0 + value[0].rect.x1) / 2, 245
    covering = [r for r in a.redaction_rects
                if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1]
    assert covering, "el valor debe cubrirse"
    assert all(r.y0 > 241 for r in covering), \
        "el rect no puede invadir la caja de la etiqueta"


# --- celda-nombre aislada -------------------------------------------------------

def test_standalone_name_cell_detected():
    assert _standalone_name_cell("ROBERTO FERNANDEZ MORAN") is not None
    assert _standalone_name_cell("VELA RIUS") is not None


def test_standalone_name_cell_negatives():
    assert _standalone_name_cell("DADES TREBALL") is None          # sin aval INE
    assert _standalone_name_cell("JUAN") is None                    # 1 token
    assert _standalone_name_cell("MODALIDAD A") is None             # sin apellido
    assert _standalone_name_cell("visto el expediente en curso") is None


# --- FP de firma dentro de palabra ("ASSIGNATURES") -----------------------------

def test_signature_label_requires_word_boundary():
    out = _ents("ASSIGNATURES MATRICULADES\n60 ECTS", {"PERSON"})
    assert out == []


# --- FP de DNI sin dígitos ("NIF DEL TUTOR") ------------------------------------

def test_nif_label_without_digits_not_dni():
    out = _ents("NIF DEL TUTOR", {"ES_DNI"})
    assert out == []
    # con valor real sigue funcionando
    assert ("ES_DNI", "12345678Z") in _ents("NIF 12345678Z", {"ES_DNI"})


# --- nombres en ficheros adjuntos ------------------------------------------------

def test_person_filename_detected():
    out = _ents("adjunto TFM_Luis_Vela.pdf al expediente", {"PERSON"})
    assert ("PERSON", "TFM_Luis_Vela.pdf") in out


def test_generic_filenames_not_detected():
    assert _ents("ver Informe_2024.pdf y Memoria_Valencia.pdf", {"PERSON"}) == []
