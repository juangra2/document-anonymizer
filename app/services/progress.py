"""Avance por páginas del documento en proceso.

La interfaz enseña «página 34 de 118», no un indicador que gira: un expediente
grande tarda minutos y quien lo sube necesita saber que avanza.

El aviso viaja por una variable de hilo y no por el argumento de cada función
porque el bucle de páginas está tres o cuatro capas por debajo de quien conoce
el trabajo (ruta → cola → pipeline → despachador → redactor).  Pasarlo a mano
obligaría a cambiar nueve firmas para una señal que ninguna de ellas usa.

Solo informa el PRIMERO que lo reclama, y por eso el camino híbrido reclama al
entrar, antes de llamar al redactor digital: ese pase interno recorre las
mismas páginas, y sin la regla el avance llegaría al 100 % justo antes de
empezar el OCR, que es la parte lenta.  Fuera de un trabajo encolado no hay
nadie escuchando y `claim()` devuelve una función que no hace nada.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator

_local = threading.local()

ProgressCallback = Callable[[int, int], None]
PageCallback = Callable[[int], None]


def _noop(_page_index: int) -> None:
    return None


class _Reporter:
    def __init__(self, callback: ProgressCallback) -> None:
        self._callback = callback
        self._claimed = False

    def claim(self, total_pages: int) -> PageCallback:
        if self._claimed:
            return _noop
        self._claimed = True
        pages = max(0, int(total_pages))
        self._callback(0, pages)

        def on_page(page_index: int) -> None:
            # Se avisa al EMPEZAR cada página, así que las terminadas son las
            # anteriores.  El trabajo marca el total al completarse.
            self._callback(min(pages, max(0, page_index)), pages)

        return on_page


@contextmanager
def track(callback: ProgressCallback) -> Iterator[None]:
    """Escucha el avance del documento que se procese en este hilo.

    El receptor recibe (páginas terminadas, páginas totales).  El alcance es un
    documento: cada llamada empieza con el aviso sin reclamar.
    """
    previous = getattr(_local, "reporter", None)
    _local.reporter = _Reporter(callback)
    try:
        yield
    finally:
        _local.reporter = previous


def claim(total_pages: int) -> PageCallback:
    """Reclama el aviso de avance y devuelve con qué anunciar cada página.

    Devuelve una función inerte si ya lo reclamó un bucle más externo o si
    nadie está escuchando, de modo que el llamador no necesita comprobar nada.
    """
    reporter: _Reporter | None = getattr(_local, "reporter", None)
    if reporter is None:
        return _noop
    return reporter.claim(total_pages)
