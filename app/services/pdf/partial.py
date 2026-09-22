"""La hoja que cierra un documento parado a medias.

El nombre del fichero se cambia al guardarlo y el informe no acompaña al PDF
cuando alguien lo reenvía por correo: esta página es la única marca que viaja
siempre con el documento.  Por eso existe, y por eso no dice nada más que lo
imprescindible.

Lo que NO puede llevar: el nombre del fichero original ni nada leído del
documento.  Un nombre de expediente suele llevar el del interesado, y esta hoja
va dentro de un PDF que se va a compartir.
"""
from __future__ import annotations

from datetime import date

import fitz

_MARGIN = 72.0
_TITLE_SIZE = 16
_BODY_SIZE = 12


def partial_notice_text(pages_done: int, pages_total: int, today: date | None = None) -> str:
    """El texto exacto de la hoja, también usado por los tests."""
    hoy = (today or date.today()).strftime("%d/%m/%Y")
    return (
        f"Documento parcial: contiene las páginas 1 a {pages_done} de "
        f"{pages_total}. Las restantes no se han anonimizado.\n\n{hoy}"
    )


def append_partial_notice(
    doc: fitz.Document,
    pages_done: int,
    pages_total: int,
    today: date | None = None,
) -> None:
    """Añade al final una página que avisa de que el documento está incompleto."""
    if doc.page_count:
        anterior = doc[doc.page_count - 1].rect
        ancho, alto = anterior.width, anterior.height
    else:  # pragma: no cover — un parcial siempre tiene al menos una página
        ancho, alto = fitz.paper_size("a4")

    page = doc.new_page(width=ancho, height=alto)
    caja = fitz.Rect(_MARGIN, _MARGIN, ancho - _MARGIN, alto / 2)

    page.insert_textbox(
        caja, "Documento anonimizado parcialmente",
        fontsize=_TITLE_SIZE, fontname="helv", align=fitz.TEXT_ALIGN_LEFT,
    )
    page.insert_textbox(
        fitz.Rect(caja.x0, caja.y0 + 36, caja.x1, caja.y1),
        partial_notice_text(pages_done, pages_total, today),
        fontsize=_BODY_SIZE, fontname="helv", align=fitz.TEXT_ALIGN_LEFT,
    )
