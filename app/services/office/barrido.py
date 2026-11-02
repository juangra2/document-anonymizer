"""Lo que no es una celda ni un parrafo normal tambien es contenido del fichero.

Un Word o un Excel guarda texto del usuario en muchos sitios ademas de las celdas
y los parrafos: listas desplegables, formato condicional, autofiltros, titulos de
graficos, nombres definidos, textos dentro de formulas, nombre y etiqueta de un
control de contenido, textos alternativos...  En vez de perseguir cada sitio,
todo ese texto pasa por el analizador, igual que el de las celdas y los parrafos.

Que se mira, en cada parte XML del paquete salvo las que no llevan texto del
usuario (estilos, temas, fuentes, numeracion, propiedades, relaciones...):
  - el texto de cada elemento, salvo el que ya se trata como contenido principal
    (el texto de los parrafos de Word y el de las celdas de Excel);
  - en las formulas (celdas, reglas, validaciones, nombres definidos), solo los
    textos entre comillas, juntos (CONCATENATE("Vicenta"," Ribes") es un
    nombre), y los numeros largos sueltos (un telefono, un DNI sin letra); las
    referencias no se tocan y la formula sigue siendo valida;
  - los atributos que guardan texto del usuario (_ATRIBUTOS), y ninguno mas: un
    atributo cualquiera puede ser el nombre de una fuente ("Times New Roman"
    parece un nombre de persona).
El dato se sustituye por el marcador de su tipo, como en el resto.
"""
from __future__ import annotations

import re
from collections import Counter

from lxml import etree

from app.services.office.paquete import Paquete

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"

#: Partes sin texto del usuario: estilos, temas, fuentes, numeracion, ajustes,
#: propiedades (se limpian aparte), relaciones, la tabla de cadenas (sus textos
#: son los de las celdas) y lo binario.
_PARTES_FUERA = re.compile(
    r"(^\[Content_Types\]\.xml$|\.rels$|^docProps/|/styles(WithEffects)?\.xml$|/theme/|/fontTable\.xml$"
    r"|/numbering\.xml$|/settings\.xml$|/webSettings\.xml$|/calcChain\.xml$|/sharedStrings\.xml$"
    r"|/printerSettings/|/media/|/embeddings/|\.bin$|\.vml$)")
#: Elementos de formula: solo sus textos entre comillas y sus numeros largos.
_FORMULAS = {(S, "f"), (S, "formula"), (S, "formula1"), (S, "formula2"), (S, "definedName"),
             (S, "calculatedColumnFormula"), (S, "totalsRowFormula"),
             ("http://schemas.microsoft.com/office/excel/2006/main", "f")}
#: Elementos que son solo referencias, formatos o identificadores.
_SIN_TEXTO = {(C, "f"), (C, "formatCode"), (S, "numFmt"),
              ("http://schemas.microsoft.com/office/excel/2006/main", "sqref")}
#: Atributos que guardan texto del usuario (nombre local); "val" solo en los
#: elementos de _VAL.
_ATRIBUTOS = {"descr", "title", "caption", "prompt", "promptTitle", "error", "errorTitle", "tooltip",
              "display", "description", "string"}
_VAL = {(S, "filter"), (S, "customFilter"), (W, "alias"), (W, "tag")}

_LITERAL = re.compile(r'"((?:[^"]|"")*)"')
_NUMERO = re.compile(r"(?<![\w$.:!])\d{6,}(?![\w.:])")


def _sustituir(texto: str, analyzer, mode: str, tipos: Counter) -> str:
    from app.services.office.xlsx import _sustituir_en_texto

    return _sustituir_en_texto(texto, analyzer, mode, tipos)


def sustituir_en_formula(formula: str, analyzer, mode: str, tipos: Counter) -> str:
    """La formula con el dato de sus textos y numeros sustituido por el marcador."""
    from app.services.office.docx import marcador
    from app.services.office.xlsx import spans_de

    literales = list(_LITERAL.finditer(formula))
    if literales:
        # Los textos, juntos: "Vicenta" & " Ribes Mollà" es un nombre.
        contenidos = [m.group(1).replace('""', '"') for m in literales]
        tramos, pos = [], 0
        for c in contenidos:
            tramos.append((pos, pos + len(c)))
            pos += len(c)
        spans = spans_de("".join(contenidos), analyzer, mode)
        if spans:
            # Cada texto pierde su trozo de dato; el marcador va en el primero.
            # De derecha a izquierda, las posiciones de lo que queda no cambian.
            for a, b, tipo in reversed(spans):
                tocados = [i for i, (t0, t1) in enumerate(tramos) if max(a, t0) < min(b, t1)]
                for i in reversed(tocados):
                    t0, t1 = tramos[i]
                    x0, x1 = max(a, t0) - t0, min(b, t1) - t0
                    contenidos[i] = (contenidos[i][:x0] + (marcador(tipo) if i == tocados[0] else "")
                                     + contenidos[i][x1:])
                tipos[tipo] += 1
            salida, fin = [], 0
            for m, c in zip(literales, contenidos):
                salida.append(formula[fin:m.start()] + '"' + c.replace('"', '""') + '"')
                fin = m.end()
            formula = "".join(salida) + formula[fin:]
    # Numeros largos fuera de los textos: un telefono o un DNI sin letra.
    dentro = [(m.start(), m.end()) for m in _LITERAL.finditer(formula)]

    def numero(m):
        if any(a <= m.start() < b for a, b in dentro):
            return m.group(0)
        nuevo = _sustituir(m.group(0), analyzer, mode, tipos)
        return m.group(0) if nuevo == m.group(0) else f'"{nuevo}"'
    return _NUMERO.sub(numero, formula)


def textos_libres(paq: Paquete, contenido_principal):
    """Cada texto de las partes que no es contenido principal, como
    (parte, elemento, atributo o None, es_formula).  `contenido_principal(parte,
    elemento)` dice si el texto de un elemento ya se trata aparte (parrafo de
    Word, valor de una celda de Excel)."""
    for nombre in paq.nombres():
        if _PARTES_FUERA.search(nombre) or not nombre.endswith(".xml"):
            continue
        try:
            raiz = paq.xml(nombre)
        except etree.XMLSyntaxError:
            continue
        if raiz is None:
            continue
        for e in raiz.iter():
            if not isinstance(e.tag, str):
                continue
            q = etree.QName(e)
            clave = (q.namespace, q.localname)
            for atributo in list(e.attrib):
                local = etree.QName(atributo).localname
                if local in _ATRIBUTOS or (local == "val" and clave in _VAL):
                    yield nombre, e, atributo, False
            if not (e.text and e.text.strip()) or clave in _SIN_TEXTO or contenido_principal(nombre, e):
                continue
            if clave in _FORMULAS:
                yield nombre, e, None, True
            elif re.search(r"[^\W\d_]|\d{6}", e.text):
                yield nombre, e, None, False


def literales_de(formula: str) -> str:
    """Lo que se lee de una formula: sus textos, juntos, y sus numeros largos."""
    textos = "".join(m.group(1).replace('""', '"') for m in _LITERAL.finditer(formula))
    sin_textos = _LITERAL.sub(" ", formula)
    return " ".join([textos] + _NUMERO.findall(sin_textos))


def barrer(paq: Paquete, analyzer, mode: str, tipos: Counter, contenido_principal) -> None:
    """Pasa por el analizador todo el texto de todas las partes que no es
    contenido principal, y sustituye lo que encuentra."""
    for _, e, atributo, es_formula in list(textos_libres(paq, contenido_principal)):
        if atributo is not None:
            nuevo = _sustituir(e.get(atributo), analyzer, mode, tipos)
            if nuevo != e.get(atributo):
                e.set(atributo, nuevo)
        elif es_formula:
            e.text = sustituir_en_formula(e.text, analyzer, mode, tipos)
        else:
            e.text = _sustituir(e.text, analyzer, mode, tipos)
