"""PERSON NER false-positive filter: measurement units and codes (interim)."""
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _persons(text):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "PERSON"
    ]


def test_unit_prefixed_budget_lines_not_person():
    assert _persons("Ud Par de manguitos de cuero para soldador. 50,00 4,29 214,50") == []
    assert _persons("Ud Chaleco reflectante 100,00 2,76 276,00") == []
    assert _persons("Ud Par de botas de seguridad 100,00 17,50") == []
    assert _persons("Ml Tubo de acero galvanizado por metro lineal") == []


def test_code_with_digits_not_person():
    assert _persons("EN.407 norma de referencia aplicable") == []
    assert _persons("PAC-12 identificador del paciente") == []


def test_real_names_still_detected():
    assert "Juan Pérez García" in _persons("El solicitante Juan Pérez García presenta")
    assert "Dulce María Calvo González" in _persons(
        "Dulce María Calvo González presenta la solicitud"
    )
