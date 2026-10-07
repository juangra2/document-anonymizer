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


# --- domicilio del empleador en los contratos (defecto domicilio_institucional) -----

def test_el_domicilio_de_la_universidad_empleadora_no_cuenta():
    gt = [{"text": "C477164495", "type": "ES_CIF", "page": 1},
          {"text": "Cno. de la Paz, 81, 03002 Alicante", "type": "ADDRESS", "page": 1},
          {"text": "Carretera de la Paz, 8, 30003 Murcia", "type": "ADDRESS", "page": 1}]
    r = evaluate_document("contrato_laboral_0004", "contrato_laboral", "va", "scanned",
                          "completed", 0, gt_entities=gt, detected_entities=[])
    assert sorted(e.text for e in r.fn) == ["C477164495", "Carretera de la Paz, 8, 30003 Murcia"]


def test_en_otro_tipo_de_documento_la_direccion_tras_un_cif_si_cuenta():
    gt = [{"text": "B12345678", "type": "ES_CIF", "page": 1},
          {"text": "Calle Mayor, 3, 46001 Valencia", "type": "ADDRESS", "page": 1}]
    r = evaluate_document("contrato_prestacion_servicios_0001", "contrato_prestacion_servicios",
                          "es", "digital", "completed", 0, gt_entities=gt, detected_entities=[])
    assert len(r.fn) == 2


def test_en_un_hibrido_cuenta_la_procedencia():
    gt = [{"text": "C477164495", "type": "ES_CIF", "page": 1, "procede_de": "contrato_laboral_0032"},
          {"text": "Cno. de la Paz, 81, 03002 Alicante", "type": "ADDRESS", "page": 1,
           "procede_de": "contrato_laboral_0032"}]
    r = evaluate_document("hibrido_pagina_mixta_0020", "mixto", "es", "hibrido",
                          "completed", 0, gt_entities=gt, detected_entities=[])
    assert [e.text for e in r.fn] == ["C477164495"]


# --- lugar de la infraccion (defecto lugar_infraccion_sin_etiquetar) ----------------

def test_el_lugar_de_la_infraccion_tachado_no_cuenta_como_falso_positivo():
    r = evaluate_document("notificacion_sancionador_0001", "notificacion_sancionador", "es",
                          "digital", "completed", 0, gt_entities=[],
                          detected_entities=[{"text": "Carrer de Sant Vicent, núm. 78", "type": "ADDRESS"},
                                             {"text": "Calle Inventada, 3", "type": "ADDRESS"}])
    assert [e.text for e in r.fp] == ["Calle Inventada, 3"]
