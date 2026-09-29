"""La redaccion total solo se propaga con evidencia del MISMO expediente.

El caso que lo motiva (caso 5b de las pruebas manuales): una autoliquidacion
modelo 600 seguida de una notificacion corriente que decia «en relacion con el
modelo 600 presentado en esta administracion».  Las dos paginas compartian
`modelo:600`, la propagacion lo tomo por evidencia de ser el mismo impreso y la
notificacion salio ENTERA EN NEGRO.

No es una fuga: es destruir contenido legitimo.  Y esa frase esta en toda la
correspondencia tributaria — cualquier resolucion que mencione el impreso al que
responde.

El numero de modelo identifica un TIPO de impreso; el justificante o la
referencia identifican el documento.  La propagacion necesita lo segundo.
"""
from app.services.pdf.full_redaction import TAX_FORM, classify_pages, form_references


_AUTOLIQUIDACION = (
    "MODELO 600\n600-2023-362396\nAUTOLIQUIDACION - MODELO 600\n"
    "Impuesto sobre Transmisiones Patrimoniales\n"
    "SUJETO PASIVO\nApellidos y nombre: Rosa Ortega Garrido\n"
    "LIQUIDACION\nBase imponible 7.777,31\nCuota tributaria 311,09\n"
    "TOTAL A INGRESAR 311,09\nReferencia:600-2023-362396\n"
)

_SOLO_MENCIONA = (
    "AYUNTAMIENTO DE MONTSERRAT\nComunicacion al interesado\n"
    "En relacion con el modelo 600 presentado en esta administracion, se le "
    "comunica que la autoliquidacion ha sido revisada y que no procede "
    "practicar liquidacion complementaria alguna.\n"
)

_MISMO_EXPEDIENTE = (
    "AYUNTAMIENTO DE MONTSERRAT\nEjemplar para la administracion\n"
    "Referencia: 600-2023-362396\nCuota tributaria 311,09\n"
)

_SIN_RELACION = (
    "AYUNTAMIENTO DE MONTSERRAT\nServicio de Urbanismo\n"
    "Notificacion de inicio de expediente de restauracion de la legalidad.\n"
)


def test_el_numero_de_modelo_no_es_referencia():
    """Identifica un tipo de impreso, no un expediente."""
    assert form_references(_SOLO_MENCIONA) == set()


def test_el_justificante_si_es_referencia():
    assert "ref:600-2023-362396" in form_references(_AUTOLIQUIDACION)


def test_una_mencion_al_modelo_no_arrastra_a_negro():
    """El caso 5b: lo que rompia."""
    clasificadas = classify_pages([_AUTOLIQUIDACION, _SOLO_MENCIONA])
    assert clasificadas.get(0) == TAX_FORM
    assert 1 not in clasificadas, (
        "una notificacion que solo menciona el modelo sale entera en negro"
    )


def test_compartir_el_justificante_si_arrastra():
    """El segundo ejemplar del MISMO impreso debe redactarse tambien."""
    clasificadas = classify_pages([_AUTOLIQUIDACION, _MISMO_EXPEDIENTE])
    assert clasificadas.get(0) == TAX_FORM
    assert clasificadas.get(1) == TAX_FORM


def test_una_pagina_ajena_no_se_toca():
    clasificadas = classify_pages([_AUTOLIQUIDACION, _SIN_RELACION])
    assert clasificadas.get(0) == TAX_FORM
    assert 1 not in clasificadas


def test_la_propagacion_se_corta_en_la_primera_pagina_sin_evidencia():
    """No salta por encima de una pagina ajena para alcanzar otra."""
    clasificadas = classify_pages(
        [_AUTOLIQUIDACION, _SIN_RELACION, _MISMO_EXPEDIENTE]
    )
    assert clasificadas.get(0) == TAX_FORM
    assert 1 not in clasificadas
