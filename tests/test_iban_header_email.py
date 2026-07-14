"""FN de IBAN: zona header sin IBAN_CODE y absorción de línea por el email."""
from app.services.layout.zone_config import get_detector_config
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _ents(text):
    return [
        (e.entity_type, text[e.start:e.end])
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
    ]


def test_iban_allowed_in_header_and_footer():
    # "FORMA DE PAGO … IBAN: …" cae como header en páginas interiores; un IBAN
    # nunca es contacto institucional de membrete.
    assert "IBAN_CODE" in get_detector_config("header").entity_types
    assert "IBAN_CODE" in get_detector_config("footer").entity_types


def test_email_no_colon_does_not_absorb_line():
    # El label "correo" sin dos puntos capturaba TODO el resto de línea como
    # EMAIL y el merge se tragaba el IBAN.
    t = ("Correo electrónico:antoniogomez@jazztel.es "
         "Cuenta bancaria:ES75 0081 6020 4853 6827 0533")
    out = _ents(t)
    assert ("EMAIL_ADDRESS", "antoniogomez@jazztel.es") in out
    assert ("IBAN_CODE", "ES75 0081 6020 4853 6827 0533") in out


def test_email_value_shape_still_matches_plain_cases():
    assert ("EMAIL_ADDRESS", "a.b@c.es") in _ents("Correo electrónico: a.b@c.es")
    assert ("EMAIL_ADDRESS", "x@y.com") in _ents("email x@y.com")


def test_iban_dashed_in_prose():
    t = "transferencia bancaria al IBAN: ES25-2100-3894-8607-5224-5927,"
    assert any(e == "IBAN_CODE" for e, _ in _ents(t))
