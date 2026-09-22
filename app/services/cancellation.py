"""Parar un documento que ya se está procesando, de dos maneras.

Hay dos intenciones distintas y cada una tiene su botón:

* **ELIMINAR** — «me he equivocado de documento».  Se para y se borra todo, sin
  entregar nada.  Levanta `JobCancelled`.
* **PARAR** — «no quiero esperar más, dame lo que haya».  Se para y se entrega
  un PDF con las páginas ya procesadas.  Levanta `JobStopped`, que los
  redactores atrapan para guardar lo que llevan.

En los dos casos parar es cooperativo: un hilo de Python no se puede matar, y
aunque se pudiera, el trabajo ya lanzado a la GPU seguiría corriendo.  Soltar el
permiso de GPU con un hilo suelto detrás es justo lo que agota la memoria de la
tarjeta, así que se pide la parada y se espera a que termine el trozo en curso:
entre páginas, y entre los pasos pesados de una página.

El aviso viaja por una variable de hilo, como el avance por páginas
(`progress.py`), porque el bucle de páginas está varias capas por debajo de
quien conoce el trabajo.  Los redactores piden el testigo UNA vez, al empezar,
y lo comprueban donde toca; el testigo es un objeto normal, así que sigue
sirviendo dentro del hilo auxiliar que ejecuta el OCR con presupuesto, que no
hereda las variables de hilo de quien lo lanzó.

Las dos excepciones heredan de BaseException a propósito: el camino de
redacción está lleno de `except Exception` que se tragan el fallo de una página
para no perder el documento entero, y una parada tiene que atravesarlos.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Literal

_local = threading.local()

StopMode = Literal["delete", "partial"]


class JobCancelled(BaseException):
    """Eliminar: se para y no se entrega nada."""


class JobStopped(BaseException):
    """Parar: se para y se entrega lo procesado hasta ahora."""


class Token:
    """Testigo de parada de un documento.  Se consulta, no se espera."""

    def __init__(self, mode: Callable[[], StopMode | None] | None = None) -> None:
        self._mode = mode
        self._honored = False

    @property
    def mode(self) -> StopMode | None:
        if self._honored or self._mode is None:
            return None
        return self._mode()

    def check(self) -> None:
        """Corta aquí si se pidió parar.  Si no, no hace nada."""
        mode = self.mode
        if mode == "delete":
            raise JobCancelled()
        if mode == "partial":
            raise JobStopped()

    def honor(self) -> None:
        """La parada ya se ha atendido; lo que queda por hacer no se corta.

        Quien atrapa `JobStopped` todavía tiene que guardar el PDF parcial y
        verificarlo, y esas fases vuelven a pasar por los mismos puntos de
        comprobación.  Sin esto, la parada se dispararía una segunda vez en
        mitad del guardado y se perdería justo lo que se quería entregar.
        """
        self._honored = True


# Fuera de un trabajo de la cola no hay nadie que pueda pedir la parada: el
# testigo inerte evita que cada llamador tenga que comprobar si existe.
_INERT = Token()


@contextmanager
def track(mode: Callable[[], StopMode | None]) -> Iterator[None]:
    """Atiende peticiones de parada mientras dure el bloque."""
    previous = getattr(_local, "token", None)
    _local.token = Token(mode)
    try:
        yield
    finally:
        _local.token = previous


@contextmanager
def without_partial() -> Iterator[None]:
    """Durante este bloque, PARAR no corta; ELIMINAR sí.

    Lo usa el camino híbrido en su pase digital previo, que recorre el
    documento entero antes de empezar el OCR.  Cortarlo ahí dejaría el
    documento intermedio a medias y el bucle siguiente leería páginas que no
    existen; como ese pase es el rápido, se le deja terminar.  Eliminar no
    espera: ahí no hay nada que entregar.
    """
    outer = current()
    previous = getattr(_local, "token", None)
    _local.token = Token(lambda: outer.mode if outer.mode == "delete" else None)
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
