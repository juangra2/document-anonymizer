"""Los avisos de la red de seguridad llevan codigo estable, como los de la verificacion.

El frontend los muestra en castellano a partir del codigo y no de la frase en
ingles de `warnings`.  Y llegan en el resumen del trabajo, que es lo que lee la
pantalla, no solo en el informe tecnico.
"""
from app.services.pdf.scanned_redactor import avisos_de_red
from app.services.pii.models import PiiEntity


def test_la_red_que_actua_lleva_codigo_y_paginas():
    red = {"imagenes_tachadas": 1, "zonas_repasadas": 3, "persisten": 0, "paginas": 2}
    frases, codigos, revisar = avisos_de_red(red, paginas_red=[4, 1], paginas_persisten=[], residuos=[])
    assert len(frases) == 1 and "Safety net" in frases[0]
    assert [(c.code, c.pages, c.counts) for c in codigos] == [
        ("safety_net_acted", [2, 5], {"images": 1, "zones": 3}),
    ]
    assert revisar is False


def test_lo_que_sigue_legible_tras_la_red_lleva_codigo_tipos_y_paginas():
    red = {"imagenes_tachadas": 0, "zonas_repasadas": 1, "persisten": 1, "paginas": 1}
    residuos = [PiiEntity("ES_DNI", 0, 9, 0.9, text="x"), PiiEntity("PERSON", 0, 5, 0.8, text="y")]
    frases, codigos, revisar = avisos_de_red(red, paginas_red=[0], paginas_persisten=[0],
                                             residuos=residuos)
    assert [(c.code, c.pages, c.types) for c in codigos][1] == (
        "safety_net_still_readable", [1], ["ES_DNI"],
    )
    assert revisar is True
    assert any("STILL readable" in f for f in frases)


def test_sin_red_no_hay_avisos():
    red = {"imagenes_tachadas": 0, "zonas_repasadas": 0, "persisten": 0, "paginas": 0}
    assert avisos_de_red(red, [], [], []) == ([], [], False)


def test_el_resumen_del_trabajo_lleva_los_avisos_con_codigo():
    from app.schemas.jobs import JobResultSummary
    assert "coded_warnings" in JobResultSummary.model_fields
