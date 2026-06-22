"""CERT_SERIAL, REGISTRY_NUMBER detection and case-file number policy."""
import pytest

from app.core.config import settings
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _entities(text):
    return [
        (e.entity_type, text[e.start:e.end])
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
    ]


def _has(text, etype, needle):
    return any(t == etype and needle in v for t, v in _entities(text))


# --- REGISTRY_NUMBER (output register / nº salida) ------------------------

def test_registry_number_detected():
    assert _has("N. Eixida / N. Salida: 00128-2022-093281",
                "REGISTRY_NUMBER", "00128-2022-093281")


def test_registry_number_requires_year_middle():
    # Middle group that is not a plausible year is not a register number.
    assert not _has("código 12-9999-34", "REGISTRY_NUMBER", "12-9999-34")


# --- CERT_SERIAL ----------------------------------------------------------

def test_cert_serial_standalone_20_digits():
    assert _has("Núm. sèrie cert 10657193681292795182",
                "CERT_SERIAL", "10657193681292795182")
    assert _has("12744083140769556755 firmado",
                "CERT_SERIAL", "12744083140769556755")


def test_cert_serial_context_hex():
    assert _has("Número de serie: 4F3A9B2C8D", "CERT_SERIAL", "4F3A9B2C8D")


def test_short_number_not_cert_serial():
    assert not any(t == "CERT_SERIAL" for t, _ in _entities("referencia 12345"))


# --- case-file (expediente) policy ----------------------------------------

def test_case_file_number_kept_by_default():
    text = "Expediente: E-03001-2022-000074-00"
    assert not _has(text, "CASE_NUMBER", "E-03001-2022-000074-00")


def test_case_file_number_redacted_when_enabled():
    text = "Expediente: E-03001-2022-000074-00"
    settings.redact_case_file_number = True
    try:
        assert _has(text, "CASE_NUMBER", "E-03001-2022-000074-00")
    finally:
        settings.redact_case_file_number = False


def test_corpus_case_number_still_redacted():
    # Letter-prefixed / slash formats are unaffected by the case-file policy.
    assert any(t == "CASE_NUMBER" for t, _ in _entities("Expediente: AMO/2019/0892/00"))
