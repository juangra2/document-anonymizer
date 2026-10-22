"""El OCR lee la letra de control del DNI como una cifra: "...4B" -> "...48".

Pasa en escaneos (B->8, G->6, S->5, Z->2...) y con nueve cifras el patron del
DNI no salta: el dato se entregaria sin tachar.

Se recupera SOLO si las dos cosas se cumplen: hay una etiqueta de DNI o NIE
justo antes, y la cifra final se parece a la letra que exige el digito de
control.  Con esa validacion el riesgo de inventar un DNI es minimo: un numero
cualquiera tras una etiqueta solo pasa si su ultima cifra coincide por azar con
la letra que le toca (1 de cada 23, y solo para las cifras que se parecen a
alguna letra).
"""
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.validators_es import DNI_LETTERS

analyzer = PiiAnalyzer()


def _dnis(texto):
    return [texto[e.start:e.end] for e in analyzer.analyze(texto, mode="strict", zone_type="body")
            if e.entity_type in ("ES_DNI", "ES_NIE")]


def test_el_caso_del_corpus():
    texto = "Representant legal: Laura Perez Ruiz Doc. identitat: del representant:496747448"
    assert "496747448" in _dnis(texto)


def test_con_etiqueta_dni():
    assert "496747448" in _dnis("D.N.I.: 496747448")


def test_sin_etiqueta_no():
    assert "496747448" not in _dnis("Referencia del pedido 496747448")


def test_si_la_cifra_no_cuadra_con_la_letra_no():
    """49674744 exige B; un 5 se parece a la S, no a la B.

    (Tras "DNI:" a secas el campo etiquetado ya tacha el valor sea cual sea;
    aqui la etiqueta no es de esas y la letra es la unica defensa.)
    """
    assert "496747445" not in _dnis("Doc. identitat: del representant:496747445")


def test_otras_confusiones():
    # Un numero cuya letra es G, leida como 6.
    n = next(n for n in range(10_000_000, 10_100_000) if DNI_LETTERS[n % 23] == "G")
    assert f"{n}6" in _dnis(f"NIF: {n}6")


def test_nie_con_la_letra_leida_como_cifra():
    # X + 7 cifras: se valida como 0 + 7 cifras.  Uno cuya letra sea B.
    n = next(n for n in range(1_000_000, 1_100_000) if DNI_LETTERS[n % 23] == "B")
    assert f"X{n:07d}8" in _dnis(f"NIE: X{n:07d}8")


def test_la_etiqueta_es_del_valor_de_al_lado_no_del_siguiente():
    """Un telefono tras un NIE ya leido no es otro DNI de la misma etiqueta."""
    texto = "DNI/NIE:\nX1156016J\nTfno.:\n678939806"
    assert "678939806" not in _dnis(texto)
