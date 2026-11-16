"""Titulos en mayusculas que acababan como [NOMBRE] y nombres tras un rotulo
que se llevaban la linea entera (analizador: todos los formatos).

  - "SOLICITUD DE AYUDA AL ALQUILER": "AYUDA" es apellido en el padron y "AL"
    nombre y apellido; en mayusculas no hay mayuscula inicial que distinga un
    nombre, asi que un tramo en mayusculas pide un nombre de pila de verdad (3
    letras o mas) o, si son solo apellidos, el nombre detras de una coma.
  - "LISTADO DE BENEFICIARIOS - PROGRAMA...": el rotulo pegado de una
    transferencia ("BENEFICIARIOREGISTRO...") no es el plural suelto.
  - "Solicitante: Teresa Navarro Gil, con DNI ...": el nombre acaba en la coma
    que sigue (si lo de detras empieza en minuscula) o antes de "con DNI".
Y lo que no puede cambiar: un nombre en mayusculas dentro de un titulo, un
listado "APELLIDOS, NOMBRE", el rotulo pegado de una transferencia y
"Navarro Gil, Teresa" tras un rotulo.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _personas(analyzer, texto: str) -> list[str]:
    return [texto[e.start:e.end] for e in analyzer.analyze(texto, mode="strict", zone_type=None, context_text=texto)
            if e.entity_type == "PERSON"]


@pytest.mark.parametrize("texto", [
    "SOLICITUD DE AYUDA AL ALQUILER - EJERCICIO 2026\n\nDATOS DEL SOLICITANTE\n",
    "LISTADO DE BENEFICIARIOS - PROGRAMA DE RESPIRO FAMILIAR\nPág. 1\n",
    "RELACIÓN DE BENEFICIARIOS\nPROGRAMA DE AYUDA AL ALQUILER\n",
])
def test_un_titulo_en_mayusculas_no_es_un_nombre(analyzer, texto):
    assert _personas(analyzer, texto) == []


@pytest.mark.parametrize("texto,nombre", [
    ("RESOLUCIÓN DE LA SOLICITUD DE MARTA SOLER VIDAL\n", "MARTA SOLER VIDAL"),
    ("MARTA SOLER VIDAL, con DNI 29.384.751-C, comparece.", "MARTA SOLER VIDAL"),
    ("1. NAVARRO GIL, TERESA\n2. FERRER ALANDÍ, VICENT\n", "NAVARRO GIL, TERESA"),
])
def test_un_nombre_en_mayusculas_sigue_siendo_un_nombre(analyzer, texto, nombre):
    assert any(nombre in p or p in nombre and len(p) > 6 for p in _personas(analyzer, texto)), _personas(analyzer, texto)


def test_el_rotulo_pegado_de_una_transferencia_sigue(analyzer):
    texto = "ORDENANTEDE LA FUENTE SAEZ NOTARIOS C.B. BENEFICIARIOSANCHEZ PEREZ JUAN IMPORTE 300,00"
    personas = _personas(analyzer, texto)
    assert any("SANCHEZ PEREZ JUAN" in p for p in personas), personas


@pytest.mark.parametrize("texto,nombre", [
    ("Solicitante: Teresa Navarro Gil, con DNI 29.384.751-C.", "Teresa Navarro Gil"),
    ("Solicitante: Teresa Navarro Gil, mayor de edad, vecina de València.", "Teresa Navarro Gil"),
    ("Interesado: Vicent Ferrer Alandí con DNI 48.105.627-T", "Vicent Ferrer Alandí"),
    ("Solicitante: Navarro Gil, Teresa", "Navarro Gil, Teresa"),
    ("Titular: Teresa Navarro Gil.", "Teresa Navarro Gil"),
])
def test_el_nombre_tras_un_rotulo_acaba_donde_acaba(analyzer, texto, nombre):
    personas = _personas(analyzer, texto)
    assert nombre in personas, personas
    assert not any(len(p) > len(nombre) and nombre in p for p in personas), personas


def test_el_dni_tras_el_nombre_se_detecta_aparte(analyzer):
    texto = "Solicitante: Teresa Navarro Gil, con DNI 29.384.751-C."
    tipos = {(e.entity_type, texto[e.start:e.end]) for e in
             analyzer.analyze(texto, mode="strict", zone_type=None, context_text=texto)}
    assert ("ES_DNI", "29.384.751-C") in tipos
