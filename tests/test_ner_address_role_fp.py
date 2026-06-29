"""Fix 1: ADDRESS role-Dirección exclusion + minimal INE gate on PERSON."""
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _of_type(text, etype):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == etype
    ]


# --- Part B: minimal INE gate on PERSON -----------------------------------

def test_material_phrases_not_detected_as_person():
    for phrase in (
        "Hormigón Señalización",
        "Maderas Señalización",
        "Fibrocemento Prohibición",
        "Plan de Gestión de Residuos",
    ):
        assert _of_type(phrase, "PERSON") == [], phrase


def test_real_names_still_detected_as_person():
    assert "Juan Pérez García" in _of_type("El solicitante Juan Pérez García", "PERSON")
    assert "Dulce María Calvo González" in _of_type(
        "Firmado por Dulce María Calvo González", "PERSON"
    )


# --- Part A: "Dirección + role/office" is not an ADDRESS -------------------

def test_direccion_role_not_address():
    for phrase in (
        "Dirección Facultativa de la obra",
        "Dirección Técnica del proyecto",
        "Dirección General de Urbanismo",
        "Dirección de Ejecución material",
        "Dirección de Obra del edificio",
    ):
        assert _of_type(phrase, "ADDRESS") == [], phrase


def test_real_postal_direccion_still_address():
    out = _of_type("Dirección postal Calle Mayor 12, 46001 Valencia", "ADDRESS")
    assert any("Calle Mayor 12" in v for v in out)
