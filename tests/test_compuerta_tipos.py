"""La compuerta: desmarcar un tipo no puede hacer desaparecer otro.

Que la compuerta salga en verde solo demuestra algo si sabe fallar.  Aquí se le
dan corridas sintéticas: en una se pierde una fecha de nacimiento que la
referencia sí detectaba, al desmarcar DATE_TIME (el fallo real F2).
"""
from evaluation.compuerta_tipos import compare, watched_types


def _results(fn_by_doc: dict[str, list[tuple[str, str]]], recall: float) -> dict:
    return {
        "by_type": {
            "DATE_OF_BIRTH": {"recall": recall},
            "PERSON": {"recall": 1.0},
            "DATE_TIME": {"recall": 1.0},
            "EMAIL_ADDRESS": {"recall": 1.0},
        },
        "doc_results": [
            {"doc_id": doc, "fn": [{"entity_type": t, "text": x} for t, x in fns]}
            for doc, fns in fn_by_doc.items()
        ],
    }


def test_the_gate_fails_when_an_active_type_loses_a_value():
    ref = _results({"acuerdo_adscripcion_0006": []}, recall=1.0)
    prueba = _results(
        {"acuerdo_adscripcion_0006": [("DATE_OF_BIRTH", "24/10/1954")]}, recall=0.5,
    )
    ok, lines = compare(ref, prueba, {"DATE_TIME"})
    assert not ok, "la fecha de nacimiento solo la tapaba DATE_TIME"
    assert any(
        "24/10/1954" in line and "acuerdo_adscripcion_0006" in line for line in lines
    )


def test_losing_what_was_unmarked_is_expected():
    ref = _results({"d": []}, recall=1.0)
    prueba = _results(
        {"d": [("DATE_TIME", "01/02/2020"), ("EMAIL_ADDRESS", "a@b.es")]}, recall=1.0,
    )
    ok, _ = compare(ref, prueba, {"DATE_TIME", "EMAIL_ADDRESS"})
    assert ok, "perder lo que el usuario ha desmarcado es justo lo pedido"


def test_a_value_already_missed_in_the_reference_is_not_counted():
    ref = _results({"d": [("PERSON", "Rosa Cruz")]}, recall=0.9)
    prueba = _results({"d": [("PERSON", "Rosa Cruz")]}, recall=0.9)
    ok, _ = compare(ref, prueba, {"DATE_TIME"})
    assert ok, "si ya se perdía sin desmarcar nada, no lo causa el desmarcado"


def test_every_type_is_watched_except_the_unmarked_ones():
    """Sin tipos obligatorios, lo vigilado es 'todo lo que sigue activo'."""
    ref = _results({"d": []}, recall=1.0)
    assert watched_types(ref, {"DATE_TIME"}) == {
        "DATE_OF_BIRTH", "PERSON", "EMAIL_ADDRESS",
    }
    assert watched_types(ref, set()) == {
        "DATE_OF_BIRTH", "PERSON", "DATE_TIME", "EMAIL_ADDRESS",
    }
