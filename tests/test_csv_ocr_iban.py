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
    # MinerU layout of a notarial footer: "(*) C.S.V.: <value>".
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


def test_csv_con_pocas_cifras():
    """Un CSV de letras y cifras mezcladas no tiene por que llevar 8 cifras.

    Lo que descarta el falso positivo es la etiqueta al lado, no el numero de
    cifras: un CSV puede ser casi todo letras.
    """
    for valor in ("EGFGOHHL0FWCSC1MWWQP", "KISOW3DQQ12NHCV3VXWN"):
        assert any(t == "CSV" and v == valor for t, v, _ in _ents(f"CSV: {valor}")), valor


def test_csv_sin_cifras_no():
    """Una palabra larga en mayusculas tras la etiqueta no es un codigo."""
    text = "CSV: VERIFICACIONELECTRONICA del documento"
    assert not any(t == "CSV" for t, _, _ in _ents(text))


# --- OCR-tolerant IBAN ----------------------------------------------------

_MANGLED = "ES91 21OO O418 45O2 OOO5 1332"   # zeros misread as letter O


def test_ocr_iban_recovered_after_mangling():
    # The OCR-tolerant pass (mod-97 interno) recupera la forma deformada.
    out = _ents(f"IBAN: {_MANGLED}", mode="strict")
    assert any(t == "IBAN_CODE" for t, _, _ in out)


def test_ocr_iban_rejects_invalid_value():
    out = _ents("IBAN: ES00 XYZ no", mode="strict")
    assert not any(t == "IBAN_CODE" for t, _, _ in out)


def test_ocr_iban_glued_to_label():
    # IBAN glued to the preceding word (no space) — strict recognizer's leading
    # word boundary misses it; the robust pass (mod-97) recovers it.
    text = "CUENTA DEL BENEFICIARIOES1321001901552200556633: IMPORTE"
    out = _ents(text, mode="strict")
    assert any(t == "IBAN_CODE" and v == "ES1321001901552200556633" for t, v, _ in out)


def test_ocr_iban_country_letter_misread_as_5():
    # "ES" misread as "E5" (S→5) — recovered after normalisation + mod-97.
    # Contexto sin etiqueta de parte (en strict "beneficiario X" emitiría el
    # valor como PERSON etiquetado, que también se redacta).
    out = _ents("ref E51321001901552200556633 importe", mode="strict")
    assert any(t == "IBAN_CODE" and v.startswith("E5") for t, v, _ in out)


def test_ocr_iban_invalido_con_forma_sale_con_menos_confianza():
    # La politica es la misma que la del regex directo —con forma de IBAN, se
    # redacta—, tambien para las formas con erratas del OCR
    # (tests/test_forma_estropeada.py): sale, pero con menos confianza que uno
    # valido.
    from app.services.pii.analyzer import PiiAnalyzer
    (out,) = PiiAnalyzer._detect_ocr_iban("ref ES9921001901552200556699 x")
    assert out.score < 0.92
    (valido,) = PiiAnalyzer._detect_ocr_iban("ref ES9121000418450200051332 x")
    assert valido.score == 0.92
