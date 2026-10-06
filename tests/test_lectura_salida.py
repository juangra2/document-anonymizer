"""El instrumento con el que juzgamos al pipeline, sujeto con pruebas.

Este modulo mintio dos veces antes de estar bien, y las dos en la direccion
peligrosa —decir "tapado" de un dato que estaba a la vista—:

  1. Unia el texto de todas las paginas, asi que no podia saber si la lectura
     de la pagina donde vivia el dato era de fiar.
  2. Comparaba de forma exacta, asi que una errata del OCR de la salida
     ("7931398G" por "79313986G") convertia una fuga en un exito.

Lo que se fija aqui es la logica de decision, que es donde estaban los dos
fallos.  Son pruebas rapidas: no cargan modelos ni tocan la GPU.
"""
import pytest

from evaluation.lectura_salida import (
    FUGA,
    NO_CONCLUYENTE,
    TAPADO,
    LecturaPagina,
    ResultadoDocumento,
    anotar,
    aparece,
    decidir,
    pagina_fiable,
)


# --- la tabla de decision ---------------------------------------------------

@pytest.mark.parametrize("en_entrada, en_salida, fiable, esperado", [
    # Se lee en las dos: prueba de fuga, sin matices.
    (True,  True,  True,  FUGA),
    (True,  True,  False, FUGA),
    # Se leia y ya no, con lectura de fiar: tapado.
    (True,  False, True,  TAPADO),
    # Se leia y ya no, pero no podemos fiarnos de la lectura: no demuestra nada.
    (True,  False, False, NO_CONCLUYENTE),
    # No se leia ya en la entrada: la comprobacion no aplica.
    (False, False, True,  NO_CONCLUYENTE),
    (False, True,  True,  NO_CONCLUYENTE),
])
def test_la_tabla_de_decision(en_entrada, en_salida, fiable, esperado):
    assert decidir(en_entrada, en_salida, fiable) == esperado


def test_una_lectura_dudosa_nunca_produce_un_tapado():
    """La regla que resume todo: ante la duda, no concluyente, nunca tapado."""
    for en_salida in (True, False):
        resultado = decidir(True, en_salida, fiable=False)
        assert resultado != TAPADO


# --- la comparacion tolerante ----------------------------------------------

def test_encuentra_el_dato_aunque_el_ocr_se_coma_una_cifra():
    """El caso real: el OCR leyo una cifra menos y el DNI se dio por tapado."""
    assert aparece("79313986G", "ANIDNI7931398GREVERSOLUGAR")


def test_no_confunde_dos_datos_parecidos():
    """Tolerar erratas no puede volverse casar cualquier cosa."""
    assert not aparece("18001", "CODIGOPOSTAL18002GRANADA")
    assert not aparece("39127242X", "DNI39127999XOTRO")


def test_en_datos_cortos_no_se_tolera_ninguna_errata():
    """Con tres caracteres cualquier parecido es casualidad, asi que solo
    cuenta la coincidencia exacta.  Descartar los datos cortos del recuento es
    cosa de quien llama, no de esta funcion."""
    assert aparece("ANA", "SEMANA")        # exacta: esta ahi de verdad
    assert not aparece("ANO", "SEMANA")    # parecida: no se admite


# --- fiabilidad de la pagina ------------------------------------------------

def _pagina(indice: int, texto: str) -> LecturaPagina:
    return LecturaPagina(indice=indice, texto=texto, angulo=0)


def test_una_pagina_en_negro_es_fiable_aunque_no_se_lea_nada():
    """Redaccion total: que no se lea nada es lo correcto, no un fallo."""
    entrada = _pagina(0, "NOMBRE APELLIDOS DNI DOMICILIO FECHA DE NACIMIENTO")
    salida = _pagina(0, "")
    assert pagina_fiable(entrada, salida, negrura_salida=0.98)


def test_una_lectura_mucho_peor_que_la_de_entrada_no_es_fiable():
    entrada = _pagina(0, "A" * 400)
    salida = _pagina(0, "A" * 40)
    assert not pagina_fiable(entrada, salida, negrura_salida=0.02)


def test_una_lectura_comparable_si_es_fiable():
    entrada = _pagina(0, "A" * 400)
    salida = _pagina(0, "A" * 300)
    assert pagina_fiable(entrada, salida, negrura_salida=0.02)


def test_sin_pagina_de_salida_no_hay_nada_de_que_fiarse():
    """Una entrega parcial tiene menos paginas que la entrada."""
    assert not pagina_fiable(_pagina(0, "TEXTO LARGO"), None, negrura_salida=0.0)


# --- tipos que no se anonimizan -------------------------------------------------

def test_un_municipio_a_la_vista_no_es_una_fuga():
    """LOCATION no se anonimiza: contarlo como fuga inflaba el numero.

    Mismo criterio que el evaluador (OUT_OF_SCOPE_GT_TYPES): se separa, no se
    pierde.
    """
    r = ResultadoDocumento(documento="x")
    anotar(r, "Alicante", "LOCATION", FUGA)
    assert r.fugas == 0 and r.fuera_de_alcance == 1
    assert r.detalle[0]["fuera_de_alcance"] is True


def test_un_tipo_en_alcance_si_cuenta():
    r = ResultadoDocumento(documento="x")
    anotar(r, "ES80 0049 9069", "IBAN_CODE", FUGA)
    anotar(r, "Pilar Moreno", "PERSON", TAPADO)
    assert (r.fugas, r.tapados, r.fuera_de_alcance) == (1, 1, 0)
