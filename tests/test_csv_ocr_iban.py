"""CSV (Código Seguro de Verificación) and OCR-tolerant IBAN recovery."""
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _ents(text, mode="strict"):
    return [
        (e.entity_type, text[e.start:e.end], e.source)
        for e in analyzer.analyze(text, mode=mode, zone_type="body")
    ]


# --- CSV ------------------------------------------------------------------

def test_csv_acronym_label():
    out = _ents("CSV: 12806538135277910")
    assert any(t == "CSV" and v == "12806538135277910" for t, v, _ in out)


def test_csv_full_label_spanish():
    out = _ents("Código Seguro de Verificación: A1B2C3D4E5F6G7H8")
    assert any(t == "CSV" and v == "A1B2C3D4E5F6G7H8" for t, v, _ in out)


def test_csv_requires_label():
    # A bare 17-digit number with no CSV label is not a CSV.
    assert not any(t == "CSV" for t, _, _ in _ents("referencia 12806538135277910"))


def test_csv_dotted_abbreviation_real_layout():
    # Real MinerU layout from a notarial footer: "(*) C.S.V.: <value>".
    text = ("(*) C.S.V.: 12806538135277910.\n"
            "Servicio Web de Verificación: https://www.registradores.org/csv")
    assert any(t == "CSV" and v == "12806538135277910" for t, v, _ in _ents(text))


def test_csv_value_on_next_line():
    text = "Código Seguro de Verificación\n12806538135277910\nDirección de verificación"
    assert any(t == "CSV" and v == "12806538135277910" for t, v, _ in _ents(text))


def test_csv_explanatory_note_without_value_not_matched():
    # The phrase recurs as an explanation with no code after it → no match.
    text = "Código Seguro de Verificación: este código permite contrastar la autenticidad"
    assert not any(t == "CSV" for t, _, _ in _ents(text))


# --- OCR-tolerant IBAN ----------------------------------------------------

_MANGLED = "ES91 21OO O418 45O2 OOO5 1332"   # zeros misread as letter O


def test_ocr_iban_recovered_in_balanced_mode():
    # In balanced mode the strict labeled IBAN is mod-97 validated and rejects
    # the mangled form; the OCR-tolerant pass recovers it.
    out = _ents(f"IBAN: {_MANGLED}", mode="balanced")
    assert any(t == "IBAN_CODE" for t, _, _ in out)


def test_ocr_iban_rejects_invalid_in_balanced_mode():
    out = _ents("IBAN: ES00 XYZ no", mode="balanced")
    assert not any(t == "IBAN_CODE" for t, _, _ in out)


def test_ocr_iban_requires_label():
    # No IBAN/cuenta label → the OCR-tolerant pass does not fire.
    out = [
        e for e in _ents(f"referencia {_MANGLED}", mode="balanced")
        if e[0] == "IBAN_CODE" and e[2] == "deterministic_regex"
    ]
    assert out == []
