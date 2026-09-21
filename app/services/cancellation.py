"""Parada de un documento que ya se está procesando.

Un hilo de Python no se puede matar, y aunque se pudiera, el trabajo que ya
está en marcha dentro de la GPU seguiría corriendo.  Soltar el permiso de GPU
con un hilo suelto detrás es justo lo que agota la memoria de la tarjeta, así
que parar es PEDIR la parada y esperar a que el trozo en curso termine: entre
páginas, y entre los pasos pesados de una página.  El trozo indivisible es una
llamada al motor de OCR sobre una página, que el presupuesto por página
(`ocr_page_timeout_s`) acota.

El aviso viaja por una variable de hilo, como el avance por páginas
(`progress.py`), porque el bucle de páginas está varias capas por debajo de
quien conoce el trabajo.  Los redactores piden el testigo UNA vez, al empezar,
y lo comprueban donde toca; el testigo es un objeto normal, así que sigue
sirviendo dentro del hilo auxiliar que ejecuta el OCR con presupuesto, que no
hereda las variables de hilo de quien lo lanzó.

`JobCancelled` hereda de BaseException a propósito: el camino de redacción está
lleno de `except Exception` que se tragan el fallo de una página para no perder
el documento entero, y una parada tiene que atravesarlos.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator

_local = threading.local()


class JobCancelled(BaseException):
    """El cliente pidió parar este documento."""


class Token:
    """Testigo de parada de un documento.  Se consulta, no se espera."""

    def __init__(self, is_cancelled: Callable[[], bool] | None = None) -> None:
        self._is_cancelled = is_cancelled

    @property
    def cancelled(self) -> bool:
        return bool(self._is_cancelled and self._is_cancelled())

    def check(self) -> None:
        """Corta aquí si se pidió parar.  Si no, no hace nada."""
        if self.cancelled:
            raise JobCancelled()


# Fuera de un trabajo de la cola no hay nadie que pueda pedir la parada: el
# testigo inerte evita que cada llamador tenga que comprobar si existe.
_INERT = Token()


@contextmanager
def track(is_cancelled: Callable[[], bool]) -> Iterator[None]:
    """Atiende peticiones de parada mientras dure el bloque."""
    previous = getattr(_local, "token", None)
    _local.token = Token(is_cancelled)
    try:
        yield
    finally:
        _local.token = previous


def current() -> Token:
    """Testigo de este hilo, para guardarlo y consultarlo desde otro."""
    return getattr(_local, "token", None) or _INERT


def check() -> None:
    """Corta el procesamiento si se pidió parar este documento."""
    current().check()
