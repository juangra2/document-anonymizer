"""Telefonos separados por puntos: "612.345.678", "963.45.67.89".

Es un formato corriente en Espana y no se detectaba nunca (banco de pruebas: 6
de 6 formatos con puntos legibles en digital y en escaneado).  El punto se habia
excluido de los separadores por las IP ("95.73.233.44"); pero un telefono
espanol con su primer grupo de tres cifras empieza por 6-9, y un grupo de tres
cifras mayor que 255 no puede ser una IP.

Las trampas: el DNI con puntos ("47.215.601-M", agrupado 2-3-3) y los importes
con separador de miles ("612.345.678,00 €").
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _tipos(texto):
    return [(e.entity_type, texto[e.start:e.end])
            for e in analyzer.analyze(texto, mode="strict", zone_type="body")]


@pytest.mark.parametrize("tel", ["612.345.678", "712.345.678", "963.456.789",
                                 "612.34.56.78", "963.45.67.89", "+34 612.345.678"])
def test_un_telefono_con_puntos_se_detecta(tel):
    assert ("PHONE_NUMBER", tel) in _tipos(f"Datos facilitados: {tel}")


def test_el_dni_con_puntos_sigue_siendo_dni():
    tipos = _tipos("DNI 47.215.601-M del interesado")
    assert not any(t == "PHONE_NUMBER" for t, _ in tipos)


def test_una_ip_no_es_un_telefono():
    assert not any(t == "PHONE_NUMBER" for t, _ in _tipos("servidor 95.73.233.44 de la red"))


@pytest.mark.parametrize("importe", ["612.345.678,00 €", "963.456.789 euros", "612.345.678 €",
                                     "Presupuesto: 712.345.678,50"])
def test_un_importe_con_separador_de_miles_no_es_un_telefono(importe):
    assert not any(t == "PHONE_NUMBER" for t, _ in _tipos(importe))


# --- el prefijo internacional va dentro de la barra ---------------------------------
# "0034 612 345 678" y "(+34) 963 45 67 89" se tachaban sin el prefijo: el numero
# quedaba tapado, pero el prefijo a la vista al lado de la barra.

@pytest.mark.parametrize("tel", ["0034 612 345 678", "0034612345678", "(+34) 963 45 67 89",
                                 "(+34)612345678", "+34 612 345 678", "0034 612.345.678"])
def test_el_prefijo_va_con_el_numero(tel):
    assert ("PHONE_NUMBER", tel) in _tipos(f"Llame al {tel} por la tarde")
