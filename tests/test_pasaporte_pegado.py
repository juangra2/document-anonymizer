"""El numero de pasaporte pegado a su etiqueta: "PassaportPZ6203136".

En el texto extraido del corpus digital la etiqueta y el numero salen sin espacio
("Doc.:" / "PassaportPZ6203136"), y el patron exigia "pasaporte:" con dos puntos:
tres pasaportes quedaban a la vista en la muestra de c2.  La extraccion de texto
de tablas reales los pega igual.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _pasaportes(texto):
    return [texto[e.start:e.end] for e in analyzer.analyze(texto, mode="strict", zone_type="body")
            if e.entity_type == "PASSPORT"]


@pytest.mark.parametrize("texto, valor", [
    ("Doc.:\nPassaportPZ6203136\nMODIFICACIÓ PROPOSADA", "PZ6203136"),
    ("Documento:\nPasaportePZ7025106 Placa:3771-SCR", "PZ7025106"),
    ("Pasaporte: PZ1234567", "PZ1234567"),
    ("Passaport XDA123456 vigent", "XDA123456"),
])
def test_pasaporte_con_la_etiqueta_pegada_o_separada(texto, valor):
    assert valor in _pasaportes(texto), _pasaportes(texto)


def test_la_palabra_pasaporte_sin_numero_no():
    assert not _pasaportes("Debe aportar el pasaporte vigente o el DNI.")
