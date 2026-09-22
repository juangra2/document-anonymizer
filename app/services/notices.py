"""Avisos de página que el usuario tiene que ver MIENTRAS se anonimiza.

Una página que el OCR no consigue leer se tacha entera.  Enterarse de eso al
final, en el informe, llega tarde: quien ha subido un expediente de cien
páginas quiere saberlo cuando pasa, para decidir si lo para y lo escanea mejor.

Viaja por una variable de hilo, como el avance por páginas (`progress.py`) y
por el mismo motivo: quien tacha la página está varias capas por debajo de
quien conoce el trabajo, y pasar el aviso a mano obligaría a cambiar la firma
de media docena de funciones para una señal que ninguna usa.

El aviso lleva CÓDIGOS, no frases: `kind` y `reason` son estables y es el
frontend quien los traduce, que es lo que permite tener la interfaz en
valenciano sin tocar el servidor.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

_local = threading.local()

# Una página tachada entera porque no se pudo leer.
PAGE_REDACTED_IN_FULL = "page_redacted_in_full"

# Motivos, tal como se publican en la API.
OCR_UNREADABLE = "ocr_unreadable"   # el OCR leyó basura o nada legible
OCR_TIMEOUT = "ocr_timeout"         # agotó el presupuesto de tiempo de la página


@dataclass(frozen=True)
class PageNotice:
    page: int      # numeración de usuario: la primera página es la 1
    kind: str
    reason: str


NoticeCallback = Callable[[PageNotice], None]


@contextmanager
def track(callback: NoticeCallback) -> Iterator[None]:
    """Escucha los avisos del documento que se procese en este hilo."""
    previous = getattr(_local, "callback", None)
    _local.callback = callback
    try:
        yield
    finally:
        _local.callback = previous


def page_redacted_in_full(page_index: int, reason: str) -> None:
    """Avisa de que la página *page_index* (base 0) se ha tachado entera."""
    callback: NoticeCallback | None = getattr(_local, "callback", None)
    if callback is None:
        # Fuera de un trabajo de la cola no hay nadie escuchando: la ruta
        # síncrona entrega el informe completo al terminar, que ya lo dice.
        return
    callback(PageNotice(page=page_index + 1, kind=PAGE_REDACTED_IN_FULL, reason=reason))
