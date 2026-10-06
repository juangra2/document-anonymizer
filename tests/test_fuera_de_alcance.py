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


# --- QR fantasma (defecto pagina_siempre_1) ---------------------------------------

def test_un_qr_fantasma_no_cuenta_y_un_qr_real_del_mismo_documento_si():
    """hibrido_giro_dentro_0003 lleva un QR que no esta en el PDF y otro que si."""
    gt = [{"text": "NOM:x|Ana", "type": "QR_CODE", "page": 1, "procede_de": "nomina_0005"},
          {"text": "NOT:y|Luis", "type": "QR_CODE", "page": 3, "procede_de": "otro_0001"}]
    r = evaluate_document("hibrido_giro_dentro_0003", "mixto", "es", "hibrido",
                          "completed", 0, gt_entities=gt, detected_entities=[])
    assert [e.text for e in r.fn] == ["NOT:y|Luis"]
    assert [e.text for e in r.by_type["QR_CODE"].fn] == ["NOT:y|Luis"]


def test_cada_defecto_dice_que_corregir_y_que_quitar():
    from evaluation.defectos_corpus import DEFECTOS

    for d in DEFECTOS:
        assert d["generador"] and d["al_regenerar"], d["id"]
