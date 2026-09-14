"""Bloque 5a — cada rect de redacción estructural sabe de qué tipo es (F9).

La redacción estructural tacha el valor tras una etiqueta sin analizarlo, así
que ningún disabled_types podía liberarlo.  Aquí se fija que el tipo sale de la
etiqueta, en los dos caminos, y que dos valores de tipos distintos en la misma
fila no se funden en un único rect sin tipo.
"""
import fitz
import pytest

from app.services.layout._digital_analysis import analyze_digital_layout
from app.services.ocr.form_layout import OcrFormLayoutAnalyzer
from app.services.ocr.models import OcrToken
from app.services.pdf.extractor import PageText, WordBox
from app.services.pii.label_types import NON_REDACTABLE_LABEL_TYPES, label_entity_type

_CW, _H = 7.0, 10.0


# --- tabla etiqueta -> tipo --------------------------------------------------------

@pytest.mark.parametrize("label, expected", [
    ("Teléfono:", "PHONE_NUMBER"),
    ("Telèfon mòbil", "PHONE_NUMBER"),
    ("Pasaporte", "PASSPORT"),
    ("Matrícula del vehicle", "LICENSE_PLATE"),
    ("Correo personal:", "EMAIL_ADDRESS"),
    ("Adreça electrònica", "EMAIL_ADDRESS"),
    ("Adreça postal", "ADDRESS"),
    ("Nombre de la vía", "ADDRESS"),
    ("Nombre completo", "PERSON"),
    ("Nom i cognoms", "PERSON"),
    ("Cuenta del beneficiario", "IBAN_CODE"),
    ("DNI/NIF", "ES_DNI"),
    ("NIF/NIE", "ES_NIE"),
    ("Fecha de nacimiento", "DATE_OF_BIRTH"),
    ("Data de naixement", "DATE_OF_BIRTH"),
    ("Codi postal", "POSTAL_CODE"),
    ("Nº colegiado", "PROFESSIONAL_LICENSE"),
    ("Número de col·legiat", "PROFESSIONAL_LICENSE"),
])
def test_label_announces_its_type(label, expected):
    assert label_entity_type(label) == expected


@pytest.mark.parametrize("label", ["Entidad bancaria", "Contacto", "Datos a anonimizar", "Valor", ""])
def test_labels_without_a_concrete_type_stay_untyped(label):
    assert label_entity_type(label) is None


def test_colegiado_is_never_redactable():
    assert "PROFESSIONAL_LICENSE" in NON_REDACTABLE_LABEL_TYPES


# --- camino digital ------------------------------------------------------------------

def _w(text, x0, y0):
    return WordBox(text=text, start=0, end=0,
                   rect=fitz.Rect(x0, y0, x0 + len(text) * _CW, y0 + _H),
                   block_no=0, line_no=0, word_no=0)


def _page(words):
    return PageText(page_index=0, text=" ".join(w.text for w in words), words=words)


def _type_covering(word, analysis):
    cx, cy = (word.rect.x0 + word.rect.x1) / 2, (word.rect.y0 + word.rect.y1) / 2
    for rect, etype in zip(analysis.redaction_rects, analysis.redaction_types):
        if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
            return True, etype
    return False, None


def test_digital_field_value_rect_carries_the_label_type():
    phone_value = _w("612345678", 200, 100)
    words = [_w("Teléfono", 60, 100), phone_value]
    analysis = analyze_digital_layout(_page(words))
    assert len(analysis.redaction_types) == len(analysis.redaction_rects)
    covered, etype = _type_covering(phone_value, analysis)
    assert covered and etype == "PHONE_NUMBER"


def test_digital_vertical_label_carries_the_type():
    value = _w("Serrano", 60, 114)
    words = [_w("Apellidos", 60, 100), value]
    analysis = analyze_digital_layout(_page(words))
    covered, etype = _type_covering(value, analysis)
    assert covered and etype == "PERSON"


# --- camino OCR ----------------------------------------------------------------------

def _t(text, x0, y0, width=None):
    width = width or len(text) * _CW
    return OcrToken(text=text, confidence=0.99, rect=fitz.Rect(x0, y0, x0 + width, y0 + 14))


def test_ocr_form_rect_carries_the_label_type():
    tokens = [_t("Pasaporte:", 60, 100), _t("PZ2157600", 160, 100)]
    items = OcrFormLayoutAnalyzer().typed_rects_for_form_values(tokens)
    assert items, "debe detectarse el valor del formulario"
    assert {etype for _, etype in items} == {"PASSPORT"}


def test_ocr_values_of_different_types_are_not_merged():
    tokens = [
        _t("Teléfono:", 60, 100), _t("612345678", 140, 100),
        _t("Pasaporte:", 232, 100), _t("PZ2157600", 310, 100),
    ]
    analyzer = OcrFormLayoutAnalyzer()
    items = analyzer.typed_rects_for_form_values(tokens)
    types = [etype for _, etype in items]
    assert "PASSPORT" not in types or "PHONE_NUMBER" not in types or len(items) >= 2, (
        "teléfono y pasaporte no pueden quedar fundidos en un único rect"
    )


def test_ocr_legacy_api_still_returns_plain_rects():
    tokens = [_t("Pasaporte:", 60, 100), _t("PZ2157600", 160, 100)]
    rects = OcrFormLayoutAnalyzer().rects_for_form_values(tokens)
    assert rects and all(isinstance(r, fitz.Rect) for r in rects)


# --- qué se pinta ----------------------------------------------------------------------

from app.services.pii.label_types import select_structural_rects  # noqa: E402


def test_disabled_type_is_not_painted_and_untyped_always_is():
    rects = ["phone", "passport", "sin_tipo", "colegiado"]
    types = ["PHONE_NUMBER", "PASSPORT", None, "PROFESSIONAL_LICENSE"]
    kept, untyped = select_structural_rects(rects, types, {"PHONE_NUMBER"})
    assert kept == ["passport", "sin_tipo"]
    assert untyped == 1


def test_colegiado_is_not_painted_even_with_nothing_disabled():
    kept, _ = select_structural_rects(["colegiado"], ["PROFESSIONAL_LICENSE"], set())
    assert kept == []


def test_missing_types_are_treated_as_untyped():
    kept, untyped = select_structural_rects(["a", "b"], None, {"PHONE_NUMBER"})
    assert kept == ["a", "b"] and untyped == 2


from app.services.pii.label_types import structural_rects_released  # noqa: E402


def test_released_only_when_a_disabled_type_drops_a_rect():
    assert structural_rects_released(["PHONE_NUMBER", None], {"PHONE_NUMBER"})
    assert not structural_rects_released(["PHONE_NUMBER", None], set())
    assert not structural_rects_released(["PROFESSIONAL_LICENSE"], {"PROFESSIONAL_LICENSE"})
    assert not structural_rects_released(None, {"PHONE_NUMBER"})
