"""El numero de pasaporte pegado a su etiqueta: "Passaport<numero>".

En el texto extraido de una tabla la etiqueta y el numero pueden salir sin
espacio ni dos puntos ("Doc.:" / "Passaport<numero>"): el patron no puede exigir
"pasaporte:" con dos puntos.
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
