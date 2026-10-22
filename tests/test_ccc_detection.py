"""Pre-SEPA CCC (Entidad-Oficina-DC-Cuenta, 4-4-2-10) detection."""
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _ibans(text):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "IBAN_CODE"
    ]


def test_ccc_spaced_detected():
    assert "0049 5114 91 2116158451" in _ibans("CCC 0049 5114 91 2116158451")


def test_ccc_dashed_detected():
    assert "0049-5114-91-2116158451" in _ibans("Cuenta 0049-5114-91-2116158451")


def test_iban_still_detected():
    assert "ES18 0049 5114 9121 1615 8451" in _ibans(
        "IBAN ES18 0049 5114 9121 1615 8451"
    )


def test_no_false_positive_on_phone_or_date():
    assert _ibans("Teléfono 612 345 678") == []
    assert _ibans("Fecha 06/11/2019 referencia 1234 5678") == []


def test_unseparated_20_digits_not_ccc():
    # Without separators the 4-4-2-10 shape is ambiguous → not matched as CCC.
    assert "00495114912116158451" not in _ibans("numero 00495114912116158451 fin")
