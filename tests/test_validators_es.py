from app.services.pii.validators_es import (
    is_valid_cif,
    is_valid_dni,
    is_valid_nie,
    is_valid_spanish_iban,
    looks_like_cadastral_reference,
)


def test_valid_dni():
    assert is_valid_dni("12345678Z")


def test_invalid_dni():
    assert not is_valid_dni("12345678A")


def test_valid_nie():
    assert is_valid_nie("X1234567L")


def test_invalid_nie():
    assert not is_valid_nie("X1234567A")


def test_valid_spanish_iban():
    assert is_valid_spanish_iban("ES91 2100 0418 4502 0005 1332")


def test_invalid_spanish_iban():
    assert not is_valid_spanish_iban("ES00 2100 0418 4502 0005 1332")


def test_valid_cif_examples():
    assert is_valid_cif("B12345674")


def test_invalid_cif():
    assert not is_valid_cif("B12345678")


def test_cadastral_reference_format():
    assert looks_like_cadastral_reference("1234567YJ2713S0001AB")