"""Lo que queda a la vista de una cuenta bancaria cuando algo tapa su principio.

En una foto con un objeto encima del formulario (corpus de casos dificiles de
imagenes, I11) el OCR leia solo "18 4502 0005 1332": sin la etiqueta IBAN ni el
"ES", nada lo reconocia y las ultimas cifras quedaban a la vista.  Tres o mas
grupos de cuatro cifras seguidos son el resto de una cuenta.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _tipos(texto):
    return {(e.entity_type, texto[e.start:e.end])
            for e in analyzer.analyze(texto, mode="strict", zone_type="body")}


@pytest.mark.parametrize("texto, trozo", [
    ("18 4502 0005 1332", "18 4502 0005 1332"),
    ("Cuenta: 2100 0418 4502 0005", "2100 0418 4502 0005"),
    ("4502-0005-1332", "4502-0005-1332"),
])
def test_un_trozo_de_cuenta_se_tacha(texto, trozo):
    assert ("IBAN_CODE", trozo) in _tipos(texto)


@pytest.mark.parametrize("texto", [
    "Teléfono 654 218 937",
    "Fecha 06/11/2019 referencia 1234 5678",
    "Calle de Sueca 42, 46006 Valencia",
    "Ejercicio 2024 partida 0045",
    "Importe 1.234,56 euros",
])
def test_lo_que_no_es_una_cuenta_no(texto):
    assert not [t for t in _tipos(texto) if t[0] == "IBAN_CODE"]


def test_una_tarjeta_sigue_siendo_una_tarjeta():
    tipos = _tipos("Tarjeta 4111 1111 1111 1111")
    assert ("CREDIT_CARD", "4111 1111 1111 1111") in tipos


def test_la_continuacion_de_una_cuenta_no_cuenta_aparte():
    """Una cuenta partida por un salto de linea ya se tapa entera: el trozo de la
    segunda linea no es otra deteccion (en el corpus digital de c4 contaba como
    falso positivo)."""
    texto = "N.º compte: ES09 0049 6738 1151\n6734 9902 i F.N.: 25/09/1970."
    trozos = [t for tipo, t in _tipos(texto) if tipo == "IBAN_CODE" and not t.startswith("ES")]
    assert trozos == []
    assert any(tipo == "IBAN_CODE" for tipo, _ in _tipos(texto))


def test_la_segunda_linea_de_una_cuenta_analizada_suelta():
    """La via digital analiza a veces una linea suelta: con la pagina como
    contexto, la segunda linea de una cuenta partida sigue siendo esa cuenta."""
    pagina = "IBAN pagament: ES54 0075 4667\n7744 7772 5319 i F. naix.: 29/10/1965."
    linea = "7744 7772 5319 i F. naix.: 29/10/1965."
    detectados = analyzer.analyze(linea, mode="strict", zone_type="body", context_text=pagina)
    assert not [e for e in detectados if e.entity_type == "IBAN_CODE"]
    sin_contexto = analyzer.analyze("18 4502 0005 1332", mode="strict", zone_type="body",
                                    context_text="Foto: 18 4502 0005 1332")
    assert [e for e in sin_contexto if e.entity_type == "IBAN_CODE"], "el trozo suelto se sigue tachando"
