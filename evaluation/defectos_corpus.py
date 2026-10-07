"""Defectos conocidos del corpus sintetico: la lista UNICA.

Los corpus estan congelados para comparar contra la linea base, asi que un
defecto del generador no se arregla regenerando: se aparta en la evaluacion y
se apunta aqui.  Cada entrada dice que pasa, como se midio, que hay que
corregir en ../corpus-generator y que exclusion hay que QUITAR de la evaluacion
cuando se regenere.  La proxima regeneracion los arregla todos de una vez;
despues, esta lista vuelve a quedarse vacia (salvo lo que siga siendo cierto).

Las exclusiones se aplican en evaluation/metrics.py (fuera_de_alcance y
evaluate_document) y, la del telefono, tambien en la lectura de barras.
"""
from __future__ import annotations

import re

DEFECTOS = [
    {
        "id": "telefono_formato_eeuu",
        "que_pasa": 'El generador escribia telefonos como "(618) 741-017": nueve '
                    "cifras agrupadas al estilo de EE. UU., que no es como se "
                    "escribe un telefono en Espana. 8 de las 9 fugas de telefono "
                    "de la medicion de la red eran este formato.",
        "generador": "HECHO (e5779ad): quitado mobile_parens de "
                     "generators/pii_factory.py, con una prueba.",
        "al_regenerar": "Quitar _TELEFONO_FORMATO_DEL_GENERADOR y su uso en "
                        "fuera_de_alcance (este modulo).",
    },
    {
        "id": "pagina_siempre_1",
        "que_pasa": "El generador etiqueta TODAS las entidades con page=1 "
                    "(orchestrator.py / base_generator.py: e.get('page', 1)), "
                    "aunque en un documento de dos paginas el QR y parte de los "
                    "datos se dibujan en la segunda.  El mezclador del hibrido, "
                    "al tomar solo la primera pagina de un documento digital, "
                    "arrastra las etiquetas de la segunda: datos que no estan en "
                    "el PDF.  Medido el 06-10-2026 en hybrid_mitad: ~92 entidades "
                    "fantasma en 12 documentos (10 de ellas, QR).",
        "generador": "PENDIENTE: calcular la pagina real de cada entidad tras "
                     "renderizar (buscar su texto en cada pagina; el QR, la "
                     "pagina que lleva su imagen) en vez de suponer la 1.",
        "al_regenerar": "Quitar QR_FANTASMA (este modulo) y su filtro en "
                        "evaluate_document.  Las otras ~82 entidades fantasma "
                        "NO estan excluidas hoy: siguen contando como falsos "
                        "negativos del hibrido.",
    },
    {
        "id": "qr_ilegible_por_resolucion",
        "que_pasa": "5 documentos escaneados (contrato_laboral_0002/0004/0007/"
                    "0008, nomina_0001) llevan un QR de ~9 mm escaneado a 120-150 "
                    "ppp: ~1,4 pixeles por modulo.  No lo decodifica nada, ni "
                    "renderizando a 600 ppp, asi que tampoco quien reciba el PDF: "
                    "no se tacha y no es una fuga.  En escaneos reales a 200-300 "
                    "ppp deberian leerse y taparse.  La regla de 'lo ilegible se "
                    "tacha' sigue valiendo cuando el QR se ve pero no se lee; "
                    "aqui no se puede leer de ninguna forma.",
        "generador": "PENDIENTE: no escanear por debajo de 200 ppp, o no poner "
                     "un QR tan pequeno en los documentos que se escanean.",
        "al_regenerar": "Nada que quitar: NO se excluye, cuenta como falso "
                        "negativo de QR_CODE en el escaneado.",
    },
    {
        "id": "domicilio_institucional",
        "que_pasa": "En contrato_laboral el empleador es una universidad (get_org) y "
                    "el generador etiqueta su domicilio social como ADDRESS.  Es el "
                    "contacto de una institucion, no un dato personal: el filtro "
                    "institucional lo deja a la vista en los dos caminos (en digital, "
                    "en los 44 contratos).  Contarlo como falso negativo mide la "
                    "etiqueta, no el sistema.",
        "generador": "PENDIENTE: no etiquetar como ADDRESS el domicilio del "
                     "empleador cuando es una institucion publica (contrato_laboral.py, "
                     "la entidad employer['address']).",
        "al_regenerar": "Quitar es_domicilio_empleador (este modulo) y su filtro en "
                        "evaluate_document.",
    },
]


# --- telefono_formato_eeuu ----------------------------------------------------
_TELEFONO_FORMATO_DEL_GENERADOR = re.compile(r"\(\d{3}\) \d{3}-\d{3}")


def telefono_del_generador(tipo: str, texto: str) -> bool:
    return tipo == "PHONE_NUMBER" and bool(
        _TELEFONO_FORMATO_DEL_GENERADOR.fullmatch((texto or "").strip())
    )


# --- pagina_siempre_1: los QR fantasma ------------------------------------------
# (documento hibrido, documento de origen del QR): el QR estaba en la segunda
# pagina del origen y el hibrido solo tomo la primera.  Comprobado con el PDF de
# origen (su primera pagina no lleva ninguna imagen).
QR_FANTASMA: frozenset[tuple[str, str]] = frozenset({
    ("hibrido_escaneado_con_digital_0002", "notificacion_ayuntamiento_0009"),
    ("hibrido_escaneado_con_digital_0022", "formulario_medico_0002"),
    ("hibrido_escaneado_con_digital_0049", "notificacion_sancionador_0013"),
    ("hibrido_giro_dentro_0003", "nomina_0005"),
    ("hibrido_pagina_mixta_0010", "formulario_medico_0017"),
    ("hibrido_pagina_mixta_0020", "contrato_laboral_0032"),
    ("hibrido_pagina_mixta_0040", "nomina_0021"),
    ("hibrido_pagina_mixta_0041", "contrato_laboral_0004"),
    ("hibrido_prosa_densa_escaneada_0012", "nomina_0016"),
    ("hibrido_prosa_densa_escaneada_0021", "solicitud_generica_0017"),
})


def es_qr_fantasma(doc_id: str, entidad: dict) -> bool:
    return (entidad.get("type") == "QR_CODE"
            and (doc_id, entidad.get("procede_de")) in QR_FANTASMA)


# --- domicilio_institucional ------------------------------------------------------
def es_domicilio_empleador(doc_id: str, entidades: list[dict], indice: int) -> bool:
    """El ADDRESS justo detras del ES_CIF en un contrato laboral: el domicilio de la
    universidad empleadora.  Comprobado en las 48 etiquetas de contrato_laboral del
    corpus (digital y escaneado): siempre va en ese orden."""
    e = entidades[indice]
    if e.get("type") != "ADDRESS" or indice == 0 or entidades[indice - 1].get("type") != "ES_CIF":
        return False
    origen = e.get("procede_de") or doc_id
    return origen.startswith("contrato_laboral")
