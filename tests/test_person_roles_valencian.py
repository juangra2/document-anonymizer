"""Cargos de persona con etiqueta en valenciano (ampliación del bloque 2a).

La etiqueta castellana reconoce «Representante:», «Interesado:», «Técnico:»…
y refuerza la detección del nombre que sigue.  La valenciana solo conocía
«Nom», «Cognoms», «Sol·licitant» (y esta última sin el punto volado, así que
nunca casaba).  Se exige que el nombre lo detecte el CAMPO ETIQUETADO, no el NER,
que es lo que esta ampliación aporta.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _labeled_person_covers(text: str, value: str) -> bool:
    start = text.index(value)
    end = start + len(value)
    return any(
        e.entity_type == "PERSON" and e.source == "labeled_field" and e.start <= start and e.end >= end
        for e in analyzer.analyze(text, mode="strict")
    )


@pytest.mark.parametrize("text, value", [
    ("Representant: Marta Llopis Ferrer\n", "Marta Llopis Ferrer"),
    ("Representant legal: Antonio Calvo Muñoz\n", "Antonio Calvo Muñoz"),
    ("Interessada: Laia Pons Martí\n", "Laia Pons Martí"),
    ("Sol·licitant: Joan Ribes Soler\n", "Joan Ribes Soler"),
    ("Treballador/a: Paula Torres Alonso\n", "Paula Torres Alonso"),
    ("Persona treballadora: Teresa Díaz Iglesias\n", "Teresa Díaz Iglesias"),
    ("Tècnica: Rosa Vidal Mas\n", "Rosa Vidal Mas"),
    ("Arquitecte: Vicent Soler Bosch\n", "Vicent Soler Bosch"),
    ("Beneficiària: Anna Puig Serra\n", "Anna Puig Serra"),
    ("Destinatari: Jordi Mas Climent\n", "Jordi Mas Climent"),
])
def test_valencian_role_label_detects_the_name(text, value):
    assert _labeled_person_covers(text, value)


@pytest.mark.parametrize("text", [
    # Sin dos puntos no es un campo etiquetado: es prosa o un puesto.
    "Tècnic/a de Gestió\n",
    "La persona treballadora es compromet a guardar la deguda confidencialitat.\n",
    # Etiqueta sin valor.
    "Representant legal:\n",
])
def test_no_labeled_person_without_a_labeled_value(text):
    assert not any(
        e.entity_type == "PERSON" and e.source == "labeled_field"
        for e in analyzer.analyze(text, mode="strict")
    )
