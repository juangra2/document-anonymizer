"""Lo que la evaluacion no cuenta, y por que.

El formato "(618) 741-017" lo inventa nuestro generador de corpus (formato
mobile_parens): nueve cifras agrupadas al estilo de EE. UU., que no es como se
escribe un telefono en Espana.  Ajustar el detector a el seria ajustarse a datos
falsos.  Mientras los corpus sigan congelados se excluye por su FORMA —el resto
de telefonos siguen contando— y cuando se regeneren, sin ese formato, esta
exclusion se quita (ver _TELEFONO_FORMATO_DEL_GENERADOR).
"""
from evaluation.lectura_salida import FUGA, ResultadoDocumento, anotar
from evaluation.metrics import evaluate_document, fuera_de_alcance


def test_el_formato_del_generador_queda_fuera():
    assert fuera_de_alcance("PHONE_NUMBER", "(618) 741-017")


def test_los_telefonos_en_formato_real_siguen_contando():
    for t in ("+34 612 345 678", "612345678", "96 387 70 00", "+34-656-457-838"):
        assert not fuera_de_alcance("PHONE_NUMBER", t), t


def test_solo_afecta_a_telefonos():
    assert not fuera_de_alcance("CASE_NUMBER", "(618) 741-017")


def test_el_municipio_sigue_fuera():
    assert fuera_de_alcance("LOCATION", "Alicante")


def test_la_evaluacion_no_lo_cuenta_como_falso_negativo():
    r = evaluate_document("d", "t", "es", "scanned", "completed", 0,
                          gt_entities=[{"text": "(618) 741-017", "type": "PHONE_NUMBER"},
                                       {"text": "612 345 678", "type": "PHONE_NUMBER"}],
                          detected_entities=[])
    assert [e.text for e in r.fn] == ["612 345 678"]


def test_la_lectura_de_barras_tampoco_como_fuga():
    r = ResultadoDocumento(documento="x")
    anotar(r, "(618) 741-017", "PHONE_NUMBER", FUGA)
    anotar(r, "612 345 678", "PHONE_NUMBER", FUGA)
    assert (r.fugas, r.fuera_de_alcance) == (1, 1)
