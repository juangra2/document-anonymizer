"""Direcciones en prosa y repartidas en varias lineas.

El banco de pruebas (scripts/banco_contacto.py) mostro que no se detectaban ni
en digital ni en escaneado: "reside en la calle Ruzafa número 12, piso tercero"
y "Calle del Doctor Sumsi, 326, 2º / 46005 Valencia" con el codigo postal en la
linea siguiente.  Los patrones exigian un codigo postal en la misma linea.

En prosa se exige la palabra "número" (o "núm.", "nº") y un nombre de calle con
mayuscula: es lo que separa "la calle Ruzafa número 12" de "la calle estaba
cortada" o de un año ("el Paseo de Gracia, 2023").
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _direcciones(texto):
    return [" ".join(texto[e.start:e.end].split())
            for e in analyzer.analyze(texto, mode="strict", zone_type="body")
            if e.entity_type == "ADDRESS"]


@pytest.mark.parametrize("texto, esperado", [
    ("El interesado reside en la calle Ruzafa número 12, piso tercero, de esta ciudad.",
     "calle Ruzafa número 12, piso tercero"),
    ("con domicilio en la avenida del Puerto núm. 45, puerta 8, según el padrón",
     "avenida del Puerto núm. 45, puerta 8"),
    ("La persona interessada té el domicili en el carrer de Cuba, número 9, porta 7, de València.",
     "carrer de Cuba, número 9, porta 7"),
    ("vive en la plaza de la Reina nº 3, 2º B, desde 2019", "plaza de la Reina nº 3, 2º B"),
])
def test_direccion_en_prosa(texto, esperado):
    assert any(esperado in d for d in _direcciones(texto)), _direcciones(texto)


def test_direccion_repartida_en_dos_lineas():
    texto = "Calle del Doctor Sumsi, 326, 2º\n46005 Valencia\n(Valencia)"
    assert any("Doctor Sumsi, 326" in d and "46005" in d for d in _direcciones(texto))


@pytest.mark.parametrize("texto", [
    "La calle estaba cortada por obras durante la mañana.",
    "La calle Mayor está cortada por obras desde el lunes.",
    "Se celebró en la Plaza de Toros de Valencia.",
    "Recorrió el Camino de Santiago en 2019, etapa número 12.",
    "El Paseo de Gracia, 2023, fue el lugar elegido.",
    "Según la avenida número 4 del plan urbanístico",
])
def test_trampas_de_direccion_en_prosa(texto):
    assert not _direcciones(texto), _direcciones(texto)
