"""Cuándo un código QR o de barras puede quedarse visible.

QR_CODE es un tipo desactivable: quien lo desmarca quiere conservar, por
ejemplo, el QR de verificación de la sede electrónica.  Pero un mismo documento
puede llevar otro código cuyo contenido es «nombre | DNI | NSS» (lo vimos en
acuerdo_adscripcion_0002).  Desmarcar el tipo no puede publicar eso.

Regla: un código queda visible solo si QR_CODE está desactivado **y** su
contenido decodificado se conoce **y** no contiene ningún tipo obligatorio.
Todo lo demás se redacta:

* QR_CODE activo → se redacta, como siempre;
* contenido no decodificado (la vía rápida de páginas-imagen solo localiza el
  código) → se redacta: sin leerlo no se puede descartar que lleve PII;
* contenido con nombre, DNI, NIE, NSS, IBAN o fecha de nacimiento → se redacta.

Límite conocido: un contenido opaco (base64, token firmado) que esconda PII no
se reconoce como tal y quedaría visible con el tipo desactivado.
"""
from __future__ import annotations

from app.services.pii.entity_catalog import mandatory_type_ids

QR_TYPE = "QR_CODE"


def code_may_stay_visible(payload: str | None, analyzer) -> bool:
    """True si el código puede NO redactarse con la configuración de *analyzer*."""
    if QR_TYPE not in getattr(analyzer, "disabled_types", frozenset()):
        return False
    if not payload or not payload.strip():
        return False
    # El contenido se analiza SIN filtro de tipos: un obligatorio es obligatorio
    # aunque la petición desactive otros tipos.
    unfiltered = analyzer.with_disabled_types(frozenset())
    mandatory = mandatory_type_ids()
    found = unfiltered.analyze(payload, mode="strict")
    return not any(entity.entity_type in mandatory for entity in found)
