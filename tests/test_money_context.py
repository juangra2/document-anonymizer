"""Importes económicos: MONEY los personales, BUDGET_AMOUNT los de obra.

Los de un presupuesto de obra salen con tipo propio, marcado por defecto como
todos, para que quien necesite conservar el presupuesto a la vista lo desmarque.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _de_tipo(text, tipo):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == tipo
    ]


def _money(text):
    return _de_tipo(text, "MONEY")


def _obra(text):
    return _de_tipo(text, "BUDGET_AMOUNT")


# --- (a) etiqueta próxima -------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("por importe de 2.500 € con cargo a los presupuestos municipales", "2.500"),
    ("liquidación del Impuesto sobre Bienes Inmuebles por importe de 1.234,56 €", "1.234,56"),
    ("Sanción propuesta 300 €", "300"),
    ("Cuota tributaria 845,20 €", "845,20"),
    ("TOTAL A INGRESAR 1.020,75 €", "1.020,75"),
    ("Importe ordenado: 12.000,00", "12.000,00"),
])
def test_amount_with_personal_label(text, expected):
    assert expected in _money(text), text


def test_ayuda_amount_not_vetoed_by_municipal_budget_mention():
    # "presupuestos municipales" no es una tabla de obra: el importe es personal.
    assert "2.500" in _money(
        "ayuda de urgencia social, por importe de 2.500 €, "
        "con cargo a los presupuestos municipales vigentes."
    )


# --- (b) zona financiera (etiqueta en cabecera de columna, lejos del valor) --

def test_payroll_zone_redacts_column_amounts():
    text = ("DEVENGOS\nSalario base 2.100,00\nComplemento 315,50\n"
            "TOTAL DEVENGADO 2.415,50\nLÍQUIDO A PERCIBIR 1.802,33 €")
    out = _money(text)
    for v in ("2.100,00", "315,50", "2.415,50", "1.802,33"):
        assert v in out, v


def test_transfer_block_redacts_amount_by_iban_context():
    text = "ORDENANTE JUAN PEREZ  IBAN ES13 2100 1901 5522 0055 6633  4.500,00"
    assert "4.500,00" in _money(text)


# --- (c) veto de presupuesto de obra ---------------------------------------

@pytest.mark.parametrize("text", [
    "03.01 Ud Par de manguitos de cuero para soldador 50,00 4,29 214,50",
    "m2 Solera de hormigón HA-25 15 cm 120,00 18,40 2.208,00",
    "Presupuesto de Ejecución Material (P.E.M.) 14.831,15 €",
    "Total capítulo 03 6.112,70",
    "Precio unitario 18,40",
    "Beneficio industrial (6%) 889,87 €",
    "Gastos generales (13%) 1.928,05 €",
])
def test_construction_budget_amounts_are_not_personal_money(text):
    assert _money(text) == [], text


@pytest.mark.parametrize("text", [
    "03.01 Ud Par de manguitos de cuero para soldador 50,00 4,29 214,50",
    "Presupuesto de Ejecución Material (P.E.M.) 14.831,15 €",
    "Total capítulo 03 6.112,70",
])
def test_construction_budget_amounts_have_their_own_type(text):
    """Se ofrecen con tipo propio para poder desmarcarlos."""
    assert _obra(text), text


def test_unmarking_the_new_type_leaves_the_budget_visible():
    """Quien necesita el presupuesto a la vista lo desmarca."""
    sin_obra = analyzer.with_disabled_types({"BUDGET_AMOUNT"})
    texto = "Presupuesto de Ejecución Material (P.E.M.) 14.831,15 €"
    encontrados = sin_obra.analyze(texto, mode="strict", zone_type="body")
    assert not [e for e in encontrados if e.entity_type == "BUDGET_AMOUNT"]


def test_the_professional_licence_is_still_never_an_amount():
    """Un numero de colegiado tiene forma de importe y no se anonimiza nunca."""
    texto = "CAPÍTULO 03 — Arquitecto Colegiado nº 14.226"
    assert _obra(texto) == [] and _money(texto) == []


def test_budget_zone_veto_beats_importe_column_header():
    # La cabecera "Importe" de la tabla de partidas no debe activar el gate.
    text = ("CAPÍTULO 03 — ALBAÑILERÍA\n"
            "Código Ud Descripción Cantidad Precio Importe\n"
            "03.01 m2 Fábrica de ladrillo perforado 45,00 22,10 994,50")
    assert _money(text) == []


# --- excepciones ------------------------------------------------------------

def test_colegiado_number_not_money():
    # Tiene forma de importe (14.226) y por requisito no se anonimiza.
    assert _money("Arquitecto — Colegiado nº 14.226") == []
    assert _money("Colegiado n 6.402") == []


def test_plain_numbers_without_money_shape_ignored():
    assert _money("Código postal 46410 y expediente 320702") == []
