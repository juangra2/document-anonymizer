"""DNI/NIF con separadores de miles: XX.XXX.XXX-L (y variantes de etiqueta)."""
import pytest

from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.validators_es import is_valid_dni

analyzer = PiiAnalyzer()


def _dnis(text):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "ES_DNI"
    ]


@pytest.mark.parametrize("text", [
    "NIF nº 47.215.601-M",
    "DNI nº 47.215.601-M",
    "NIF: 47.215.601-M",
    "DNI: 47.215.601-M",
    "NIF nº: 47.215.601-M",
    "DNI número: 47.215.601-M",
    "DNI:47.215.601-M",              # sin espacio tras la etiqueta
])
def test_dotted_dni_with_label(text):
    assert _dnis(text) == ["47.215.601-M"], text


def test_dotted_dni_unlabeled_direct():
    assert "47.215.601-M" in _dnis("visto el documento con 47.215.601-M al margen")


def test_dotted_dni_without_dash():
    assert "47.215.601M" in _dnis("consta 47.215.601M en el registro")


def test_titular_labeled_dotted_dni_retyped():
    # "Titular <DNI>" — el retipado PERSON→ES_DNI cubre también el formato
    # con puntos cuando el valor es exactamente un DNI.
    assert "47.215.601-M" in _dnis("Titular: 47.215.601-M")


def test_plain_dni_not_regressed():
    assert _dnis("DNI: 12345678Z") == ["12345678Z"]
    assert "12345678Z" in _dnis("visto 12345678Z en el registro")
    assert "12345678-Z" in _dnis("DNI nº 12345678-Z")


def test_validator_accepts_dotted():
    assert is_valid_dni("47.215.601-M") is True
    assert is_valid_dni("47.215.601-A") is False   # letra de control incorrecta


def test_no_false_positive_on_dotted_numbers_without_letter():
    assert _dnis("versión 12.345.678 del programa") == []
    assert _dnis("importe 47.215.601 euros") == []
