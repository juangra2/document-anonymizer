from app.services.pii.analyzer import PiiAnalyzer


def entity_types(text: str, mode: str = "strict") -> set[str]:
    analyzer = PiiAnalyzer()
    return {entity.entity_type for entity in analyzer.analyze(text, mode=mode)}


def test_strict_detects_invalid_fictitious_dni_and_iban():
    types = entity_types(
        """
DNI/NIF 00000000X
IBAN ES91 0000 0000 0000 0000 0000
"""
    )

    assert "ES_DNI" in types
    assert "IBAN_CODE" in types


def test_strict_detects_table_without_colons():
    types = entity_types(
        """
Nombre completo Lucia Martin Pruebas
Telefono movil +34 600 00 00 00
Email personal lucia.pruebas@example.com
Direccion postal Calle Falsa 123, 28000 Madrid, España
Usuario interno usr-demo-martin-001
IP de ultima conexion 192.0.2.24
"""
    )

    assert "PERSON" in types
    assert "PHONE_NUMBER" in types
    assert "EMAIL_ADDRESS" in types
    assert "ADDRESS" in types
    assert "USER_ID" in types
    assert "IP_ADDRESS" in types


def test_strict_detects_passport_card_medical_policy_and_plate():
    types = entity_types(
        """
Pasaporte FAKE-P-742019
Tarjeta de prueba 4111 1111 1111 1111 - cad. 12/30 - CVV 123
Paciente ID PAC-DEMO-992781
Seguro medico POLIZA-FAKE-13579
Matricula 0000-XXX
"""
    )

    assert "PASSPORT" in types
    assert "CREDIT_CARD" in types
    assert "MEDICAL_ID" in types
    assert "POLICY_NUMBER" in types
    assert "LICENSE_PLATE" in types


def test_strict_detects_case_table_rows():
    results = PiiAnalyzer().analyze(
        """
CAS-001 Diego Ferrer Demo Email: diego.demo@example.com Tel: +34 700 00 00 00
CAS-002 Marta Lopez Sintetica Direccion: Plaza de Pruebas 9, Valencia
""",
        mode="strict",
    )

    assert any(entity.entity_type == "CASE_TABLE_ROW" for entity in results)


def test_balanced_can_be_less_aggressive_with_invalid_identifiers():
    types = entity_types(
        """
DNI/NIF 00000000X
IBAN ES91 0000 0000 0000 0000 0000
""",
        mode="balanced",
    )

    assert "ES_DNI" not in types
    assert "IBAN_CODE" not in types

def test_field_labels_are_not_detected_as_pii():
    analyzer = PiiAnalyzer()

    text = """
Campo Valor ficticio
Nombre completo
Fecha de nacimiento
DNI/NIF
Pasaporte
Telefono movil
Email personal
Direccion postal
IBAN
Tarjeta de prueba
Usuario interno
IP de ultima conexion
"""

    results = analyzer.analyze(text, mode="strict")

    detected_texts = [
        text[entity.start:entity.end]
        for entity in results
    ]

    forbidden = {
        "Campo",
        "Valor",
        "Valor ficticio",
        "Nombre completo",
        "Fecha de nacimiento",
        "DNI/NIF",
        "Pasaporte",
        "Telefono movil",
        "Email personal",
        "Direccion postal",
        "IBAN",
        "Tarjeta de prueba",
        "Usuario interno",
        "IP de ultima conexion",
    }

    for value in forbidden:
        assert value not in detected_texts