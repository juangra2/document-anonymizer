"""La metrica de entidades mide "¿se detecto este valor en el documento?".

Antes emparejaba aparicion por aparicion: un nombre que sale tres veces en la
etiqueta y se detecta una contaba dos falsos negativos, aunque la geometria
tapa TODAS las apariciones de un valor detectado.  En el escaneado eran 37 de
92 falsos negativos.  Si cada aparicion queda tapada lo mide la lectura de
barras, que abre el PDF; asi cada medida mide lo suyo.

Y una pagina que sale entera en negro tapa lo que llevaba, aunque sus datos no
figuren en la lista de detectados (otros 24).  Cuenta solo si la pagina esta
DE VERDAD en negro —por la fraccion de pixeles oscuros de la salida—, no porque
el clasificador dijera que lo era.

Durante la transicion se guarda tambien el numero a la antigua, para que se vea
que parte de la mejora es contabilidad y cual es el pipeline.
"""
import fitz

from evaluation.lectura_salida import negrura
from evaluation.metrics import evaluate_document


def _ev(gt, det, negras=None):
    return evaluate_document("d", "t", "es", "scanned", "completed", 0,
                             gt_entities=gt, detected_entities=det,
                             paginas_en_negro=negras)


def _g(texto, tipo="PERSON", page=1):
    return {"text": texto, "type": tipo, "page": page}


def _d(texto, tipo="PERSON"):
    return {"text": texto, "type": tipo}


def test_un_valor_repetido_y_detectado_una_vez_no_deja_falsos_negativos():
    r = _ev([_g("Pilar Moreno Sanz")] * 3, [_d("Pilar Moreno Sanz")])
    assert (r.tp, len(r.fn), len(r.fp)) == (1, 0, 0)


def test_un_valor_que_no_se_detecta_cuenta_una_vez():
    r = _ev([_g("Pilar Moreno Sanz")] * 3, [])
    assert (r.tp, len(r.fn)) == (0, 1)


def test_detectar_varias_veces_el_mismo_valor_no_da_falsos_positivos():
    r = _ev([_g("Pilar Moreno Sanz")], [_d("Pilar Moreno Sanz")] * 4)
    assert (r.tp, len(r.fp)) == (1, 0)


def test_un_falso_positivo_repetido_cuenta_una_vez():
    r = _ev([], [_d("Campus Nord")] * 2)
    assert len(r.fp) == 1


def test_un_dato_de_una_pagina_en_negro_cuenta_como_detectado():
    r = _ev([_g("70751845G", "ES_DNI", page=2)], [], negras={2})
    assert (r.tp, len(r.fn), r.tp_pagina_negra) == (1, 0, 1)


def test_sin_pagina_en_negro_no_se_regala():
    """El clasificador pudo decir que la tachaba; si no salio negra, es FN."""
    r = _ev([_g("70751845G", "ES_DNI", page=2)], [], negras=set())
    assert (r.tp, len(r.fn)) == (0, 1)


def test_si_el_valor_tambien_esta_en_una_pagina_visible_es_falso_negativo():
    r = _ev([_g("70751845G", "ES_DNI", page=1), _g("70751845G", "ES_DNI", page=2)],
            [], negras={2})
    assert (r.tp, len(r.fn)) == (0, 1)


def test_se_guarda_tambien_la_cuenta_por_aparicion():
    r = _ev([_g("Pilar Moreno Sanz")] * 3, [_d("Pilar Moreno Sanz")])
    assert r.por_aparicion == {"tp": 1, "fn": 2, "fp": 0}


def test_la_negrura_distingue_una_pagina_tachada(tmp_path):
    doc = fitz.open()
    negra = doc.new_page()
    negra.draw_rect(negra.rect, color=(0, 0, 0), fill=(0, 0, 0))
    blanca = doc.new_page()
    blanca.insert_text((72, 72), "Texto normal de la pagina", fontsize=11)
    datos = doc.tobytes()
    n = negrura(datos)
    assert n[0] > 0.9 and n[1] < 0.05
