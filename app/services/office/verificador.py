"""Segunda opinion sobre un Word o un Excel ya anonimizado, como la de los PDF.

El verificador de valores (paquete.verificar) busca los valores que se han
sustituido: no puede ver lo que no se detecto.  Este lee la SALIDA de nuevo,
entera, y pasa todo su texto por el analizador:
  - Word: cada parrafo (o fila de tabla) de cada parte de texto, como se
    analiza al anonimizar;
  - Excel: el valor de cada celda, fila a fila, en todas las hojas, tambien las
    que no se sustituyeron por columna (una ficha en vertical, una cabecera que no se
    reconocio, un numero que es un telefono);
  - en los dos, el resto del texto de todas las partes (barrido.textos_libres).
Lo que encuentra no se toca: el documento pasa a revision manual y el informe
dice cuantos posibles datos y de que tipo, nunca los valores.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from app.services.office.barrido import literales_de, textos_libres
from app.services.office.paquete import Paquete

_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_MARCADOR = re.compile(r"\[[A-ZÁÉÍÓÚÑ ]+\]")


def _parece_nombre(texto: str) -> bool:
    """Un nombre de persona del padron: al menos dos palabras, una de ellas un
    nombre de pila (la primera, o la ultima en "Apellidos, Nombre") y otra un
    apellido.  Releer la salida suelta, sin el contexto que tenia al anonimizar,
    da nombres que no lo son ("Ciutat Vella", "Vocal", "New Roman"), y la lista
    de apellidos del padron los incluye a todos."""
    from app.services.pii.gazetteer import given_names, normalize, surnames

    palabras = [normalize(p) for p in re.findall(r"[^\W\d_]+", texto)]
    if len(palabras) < 2:
        return False
    pila = palabras[-1] if "," in texto else palabras[0]
    resto = [p for p in palabras if p != pila]
    return pila in given_names() and any(p in surnames() for p in resto)


def _tipos_en(texto: str, analyzer, mode: str, cuenta: Counter) -> None:
    from app.services.office.xlsx import spans_de

    for a, b, tipo in spans_de(texto, analyzer, mode):
        dato = texto[a:b]
        if "\n" in dato.strip():
            continue            # cruza de una fila a otra
        if not re.search(r"\w", _MARCADOR.sub("", dato)):
            continue            # solo marcadores: "[NOMBRE],[NOMBRE]"
        if tipo == "PERSON" and not _parece_nombre(dato):
            continue
        cuenta[tipo] += 1


def segunda_opinion(ruta: Path, analyzer, mode: str) -> Counter:
    """Posibles datos que se siguen leyendo en el fichero de salida, por tipo."""
    paq = Paquete.abrir(Path(ruta))
    cuenta: Counter = Counter()
    if "word/document.xml" in paq.partes:
        from app.services.office.docx import _PARTES_DE_TEXTO, W, bloques

        for nombre in paq.nombres("word/"):
            if _PARTES_DE_TEXTO.match(nombre):
                for bloque in bloques(paq.xml(nombre)):
                    _tipos_en(bloque.texto, analyzer, mode, cuenta)
        principal = lambda parte, e: e.tag.startswith(f"{{{W}}}")    # noqa: E731
    else:
        from app.services.office.xlsx import _es_valor_de_celda, _hojas

        hojas, _ = _hojas(paq)
        for hoja in hojas:
            # Fila a fila, con las celdas separadas por tabuladores (como una
            # fila de tabla de Word): un nombre repartido en dos columnas se ve
            # entero.
            filas: dict[int, list[tuple[int, str]]] = {}
            for (f, col), c in hoja.celdas.items():
                texto = hoja.texto(c)
                if texto and c.find(f"{{{_S}}}f") is None:
                    filas.setdefault(f, []).append((col, texto))
            # Las filas, sin repetir, en bloques de unos 4000 caracteres (un salto
            # de linea entre fila y fila): pocas llamadas al analizador tambien en
            # una hoja de cien mil filas.
            bloque, largo = [], 0
            for texto in dict.fromkeys("\t".join(t for _, t in sorted(f)) for f in filas.values()):
                if largo + len(texto) > 4000 and bloque:
                    _tipos_en("\n".join(bloque), analyzer, mode, cuenta)
                    bloque, largo = [], 0
                bloque.append(texto)
                largo += len(texto) + 1
            if bloque:
                _tipos_en("\n".join(bloque), analyzer, mode, cuenta)
        principal = _es_valor_de_celda
    for _, e, atributo, es_formula in textos_libres(paq, principal):
        texto = e.get(atributo) if atributo is not None else (literales_de(e.text) if es_formula else e.text)
        _tipos_en(texto, analyzer, mode, cuenta)
    return cuenta


def revisar(ruta: Path, analyzer, mode: str, quedan: list) -> tuple[int, list[str], list[str]]:
    """(residuales, avisos, motivos de revision) de una salida de Office: los
    valores sustituidos que siguen (`quedan`, de paquete.verificar) y lo que
    encuentra la segunda opinion."""
    from app.schemas.report import REVIEW_RESIDUAL_PII

    sospechas = segunda_opinion(ruta, analyzer, mode)
    avisos = []
    if quedan:
        avisos.append(f"{len(quedan)} replaced values still found in the output.")
    if sospechas:
        detalle = ", ".join(f"{t}: {n}" for t, n in sorted(sospechas.items()))
        avisos.append(f"Independent check: {sum(sospechas.values())} possible personal data still "
                      f"readable in the output ({detalle}).")
    residuales = len(quedan) + sum(sospechas.values())
    return residuales, avisos, [REVIEW_RESIDUAL_PII] if residuales else []

