"""Bloque 6 — el plan de redacción es el único punto de salida.

Todo lo que se tacha o se cuenta pasa por el mismo filtro.  Estos tests fijan las
reglas del plan; el enganche en los redactores se verifica aparte, sobre PDF.
"""
import fitz

from app.services.pdf.redaction_plan import RedactionPlan
from app.services.pii.models import PiiEntity


def _entity(etype, start=0, end=5):
    return PiiEntity(entity_type=etype, start=start, end=end, score=0.9, source="t", text="x" * (end - start))


def test_disabled_types_neither_paint_nor_count():
    plan = RedactionPlan(disabled_types=frozenset({"PHONE_NUMBER"}))
    assert not plan.add_entity(_entity("PHONE_NUMBER"), [fitz.Rect(0, 0, 10, 10)])
    assert plan.rects == [] and plan.entities == []


def test_active_types_paint_and_count_once():
    plan = RedactionPlan()
    rect = fitz.Rect(0, 0, 10, 10)
    plan.add_entity(_entity("ES_DNI"), [rect])
    plan.add_entity(_entity("ES_DNI"), [fitz.Rect(rect)])       # misma entidad, otra zona
    assert len(plan.rects) == 1
    assert len(plan.entities) == 1, "el recuento no puede depender de zonas solapadas"


def test_entity_without_rects_is_counted_but_paints_nothing():
    plan = RedactionPlan()
    plan.add_entity(_entity("PERSON"), [])
    assert plan.rects == [] and len(plan.entities) == 1


def test_colegiado_is_never_painted_even_as_an_exception():
    plan = RedactionPlan()
    assert not plan.add_rect(fitz.Rect(0, 0, 5, 5), "PROFESSIONAL_LICENSE", structural=True)
    assert not plan.add_rect(fitz.Rect(0, 0, 5, 5), "PROFESSIONAL_LICENSE", always=True)


def test_untyped_is_always_painted_and_counted_as_structural():
    plan = RedactionPlan(disabled_types=frozenset({"PHONE_NUMBER", "ADDRESS"}))
    assert plan.add_rect(fitz.Rect(0, 0, 5, 5), None, structural=True)
    assert plan.untyped_structural == 1 and len(plan.rects) == 1


def test_named_exceptions_survive_the_filter():
    # QR con un obligatorio dentro, o página completa: se pintan con el tipo desactivado.
    plan = RedactionPlan(disabled_types=frozenset({"QR_CODE"}))
    assert plan.add_entity(_entity("QR_CODE"), [fitz.Rect(0, 0, 50, 50)], always=True)
    assert len(plan.rects) == 1
