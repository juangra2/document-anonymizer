from app.services.pii.analyzer import PiiAnalyzer


def test_detects_valid_dni():
    analyzer = PiiAnalyzer()

    text = "El DNI del solicitante es 12345678Z."
    results = analyzer.analyze(text)

    assert any(entity.entity_type == "ES_DNI" for entity in results)


def test_strict_detects_invalid_dni_like_value():
    analyzer = PiiAnalyzer()

    text = "El DNI del solicitante es 12345678A."
    results = analyzer.analyze(text, mode="strict")

    assert any(entity.entity_type == "ES_DNI" for entity in results)


def test_detects_email_phone_and_iban():
    analyzer = PiiAnalyzer()

    text = """
    Contacto: juan@example.com
    Teléfono: 600 123 456
    IBAN: ES91 2100 0418 4502 0005 1332
    """

    results = analyzer.analyze(text)
    types = {entity.entity_type for entity in results}

    assert "EMAIL_ADDRESS" in types
    assert "PHONE_NUMBER" in types
    assert "IBAN_CODE" in types