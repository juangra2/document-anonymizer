"""Bloque 2b — la compuerta detecta que un obligatorio depende de un opcional.

Que la compuerta salga en verde solo demuestra algo si sabe fallar.  Aquí se le
dan dos corridas sintéticas: en la de opcionales desactivados se pierde una
fecha de nacimiento que en la referencia sí se detectaba (el fallo F2).
"""
from evaluation.mandatory_gate import compare


def _results(fn_by_doc: dict[str, list[tuple[str, str]]], recall: float) -> dict:
    return {
        "by_type": {"DATE_OF_BIRTH": {"recall": recall}, "PERSON": {"recall": 1.0}},
        "doc_results": [
            {"doc_id": doc, "fn": [{"entity_type": t, "text": x} for t, x in fns]}
            for doc, fns in fn_by_doc.items()
        ],
    }


def test_the_gate_fails_when_a_mandatory_value_is_lost():
    ref = _results({"acuerdo_adscripcion_0006": []}, recall=1.0)
    opt = _results({"acuerdo_adscripcion_0006": [("DATE_OF_BIRTH", "24/10/1954")]}, recall=0.5)
    ok, lines = compare(ref, opt)
    assert not ok
    assert any("24/10/1954" in line and "acuerdo_adscripcion_0006" in line for line in lines)


def test_optional_losses_do_not_fail_the_gate():
    ref = _results({"d": []}, recall=1.0)
    opt = _results({"d": [("DATE_TIME", "01/02/2020"), ("EMAIL_ADDRESS", "a@b.es")]}, recall=1.0)
    ok, _ = compare(ref, opt)
    assert ok, "perder un tipo opcional desactivado es lo esperado"


def test_a_value_already_missed_in_the_reference_is_not_counted():
    ref = _results({"d": [("PERSON", "Rosa Cruz")]}, recall=0.9)
    opt = _results({"d": [("PERSON", "Rosa Cruz")]}, recall=0.9)
    ok, _ = compare(ref, opt)
    assert ok, "si ya se perdía sin desactivar nada, no depende de un opcional"
