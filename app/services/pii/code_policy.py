"""Cuándo un código QR o de barras puede quedarse visible.

QR_CODE es un tipo desmarcable: quien lo desmarca quiere conservar, por
ejemplo, el QR de verificación de la sede electrónica.  Pero un mismo documento
puede llevar otro código cuyo contenido es «nombre | DNI | NSS» (lo vimos en
acuerdo_adscripcion_0002).  Desmarcar el tipo del código no puede publicar lo
que el usuario sí ha pedido ocultar.

Regla: un código queda visible solo si QR_CODE está desmarcado **y** su
contenido se ha podido leer **y** ese contenido no lleva ningún tipo que siga
ACTIVO en esta petición.  Todo lo demás se redacta:

* QR_CODE activo → se redacta, como siempre;
* contenido no decodificado (la vía rápida de páginas-imagen solo localiza el
  código) → se redacta: sin leerlo no se puede descartar que lleve PII;
* contenido con un dato de un tipo activo → se redacta.

La consecuencia querida: desmarcar QR_CODE **y además** el tipo que el código
lleva dentro sí lo deja visible.  Es coherente con que todos los tipos sean
desmarcables, y queda por escrito en `entity_types_disabled` del informe.

Límite conocido: un contenido opaco (base64, token firmado) que esconda datos
no se reconoce como tal y quedaría visible con el tipo desmarcado.
"""
from __future__ import annotations

QR_TYPE = "QR_CODE"


def code_may_stay_visible(payload: str | None, analyzer) -> bool:
    """True si el código puede NO redactarse con la configuración de *analyzer*."""
    if QR_TYPE not in getattr(analyzer, "disabled_types", frozenset()):
        return False
    if not payload or not payload.strip():
        return False
    # Se analiza con la configuración de la petición: lo que el usuario ha
    # desmarcado no cuenta, y lo que sigue activo manda redactar el código.
    return not analyzer.analyze(payload, mode="strict")
