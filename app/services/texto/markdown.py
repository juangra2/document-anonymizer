"""Que es sintaxis y que es texto en un Markdown, caracter a caracter.

Se sustituye sobre el texto ORIGINAL, sin reescribir el documento: cada
caracter lleva una zona y la sintaxis nunca se borra (como los tramos XML de
un Word: el dato desaparece, el formato se queda).

Zonas:
  t  texto (parrafos, encabezados, celdas, texto de enlaces, texto alternativo
     y titulo de imagenes, etiquetas de referencia)
  c  codigo (bloques de codigo y codigo en linea: tambien pueden llevar datos)
  d  destino de un enlace o de una imagen (mailto:, una URL con un nombre...)
  a  enlace automatico (<correo@dominio.es>): destino y texto a la vez
  h  HTML dentro del Markdown
  s  sintaxis que el analizador no lee (#, >, marcas de lista, enfasis,
     escapes, corchetes de apertura, comillas del titulo, retornos de carro):
     "**Teresa Navarro Gil**, con DNI" se lee como en un Word, sin asteriscos
  p  sintaxis que separa (barras entre celdas de una tabla, el paso del texto
     de un enlace a su destino, < > de un enlace automatico): se lee como un
     tabulador, que es como se separan las celdas de una tabla de Word, y un
     dato no cruza de un lado a otro.
Los bloques (codigo, HTML, tablas, lineas horizontales, subrayado de titulos)
salen de markdown-it, que da sus lineas; lo que va dentro de una linea se
reconoce con expresiones regulares.  Lo que no se reconozca se queda como
texto: se analiza y, si es un dato, se sustituye; nunca se deja un dato por
no saber si era sintaxis.
"""
from __future__ import annotations

import re

_FIN_DE_LINEA = re.compile(r"\r\n|\r|\n")
_PREFIJO = re.compile(r"(?:[ \t]*(?:>[ ]?|[-+*][ \t]+|\d{1,9}[.)][ \t]+))*[ \t]*(?:\[[ xX]\][ \t]+)?")
_CITA = re.compile(r"(?:[ \t]*>[ ]?)*")
_TITULO = re.compile(r"#{1,6}(?=[ \t]|$)")
_CIERRE_TITULO = re.compile(r"[ \t]+#+[ \t]*$")
_DEFINICION = re.compile(
    r"(\[)([^\]\n]+)(\]:)([ \t]*)(<[^>\n]*>|\S+)(?:([ \t]+)(\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*$")
_CODIGO = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_AUTOENLACE = re.compile(
    r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9][A-Za-z0-9.-]*)>")
_HTML = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>\n]*)?/?>|<!--.*?-->")
_TEXTO_ENLACE = r"[^\[\]\n]*(?:\[[^\[\]\n]*\][^\[\]\n]*)*"
_ENLACE = re.compile(
    r"(!?\[)(" + _TEXTO_ENLACE + r")(\]\()([ \t]*)(<[^>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)"
    r"(?:([ \t]+)(\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?([ \t]*)(\))")
_REFERENCIA = re.compile(r"(!?\[)(" + _TEXTO_ENLACE + r")(\]\[)([^\[\]\n]*)(\])")
_ESCAPE = re.compile(r"\\(?=[!-/:-@\[-`{-~])")
_ENFASIS = re.compile(r"\*+|_+|~~")
_PUNTUACION = re.compile(r"[!-/:-@\[-`{-~\u00a1-\u00bf\u2010-\u2027]")


def lineas(texto: str) -> list[tuple[int, int]]:
    """(inicio, fin) de cada linea, sin el salto: las mismas que cuenta markdown-it."""
    salida, inicio = [], 0
    for m in _FIN_DE_LINEA.finditer(texto):
        salida.append((inicio, m.start()))
        inicio = m.end()
    salida.append((inicio, len(texto)))
    return salida


class _Zonas:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.z = ["t"] * len(texto)

    def poner(self, a: int, b: int, zona: str) -> None:
        for k in range(a, b):
            self.z[k] = zona

    def libre(self, a: int, b: int) -> bool:
        return all(self.z[k] == "t" for k in range(a, b))

    def grupos(self, m: re.Match, base: int, zonas: dict[int, str]) -> None:
        for g, zona in zonas.items():
            if m.group(g) is not None:
                self.poner(base + m.start(g), base + m.end(g), zona)

    def separa(self, a: int, b: int) -> None:
        """Un separador de varios caracteres ("](", "]["): el primero separa, el resto no se lee."""
        if b > a:
            self.poner(a, a + 1, "p")
            self.poner(a + 1, b, "s")

    def destino(self, a: int, b: int) -> None:
        if b > a and self.texto[a] == "<" and self.texto[b - 1] == ">":
            self.poner(a, a + 1, "p")
            self.poner(b - 1, b, "p")
            self.poner(a + 1, b - 1, "d")
        else:
            self.poner(a, b, "d")

    def titulo(self, a: int, b: int) -> None:
        """El titulo de un enlace es texto; sus comillas, sintaxis."""
        if b - a >= 2:
            self.poner(a, a + 1, "s")
            self.poner(b - 1, b, "s")


def _en_linea(zonas: _Zonas, a: int, b: int) -> None:
    """Lo que va dentro de una linea de texto, de *a* a *b*."""
    texto = zonas.texto
    linea = texto[a:b]
    for m in _CODIGO.finditer(linea):
        if zonas.libre(a + m.start(), a + m.end()):
            zonas.grupos(m, a, {1: "s", 2: "c"})
            zonas.poner(a + m.end() - len(m.group(1)), a + m.end(), "s")
    for m in _AUTOENLACE.finditer(linea):
        if zonas.libre(a + m.start(), a + m.end()):
            zonas.poner(a + m.start(), a + m.start() + 1, "p")
            zonas.poner(a + m.end() - 1, a + m.end(), "p")
            zonas.poner(a + m.start(1), a + m.end(1), "a")
    for m in _HTML.finditer(linea):
        if zonas.libre(a + m.start(), a + m.end()):
            zonas.poner(a + m.start(), a + m.end(), "h")
    for m in _ENLACE.finditer(linea):
        if zonas.libre(a + m.start(), a + m.end()):
            zonas.grupos(m, a, {1: "s", 9: "p"})
            zonas.separa(a + m.start(3), a + m.end(3))
            zonas.destino(a + m.start(5), a + m.end(5))
            if m.group(7):
                zonas.titulo(a + m.start(7), a + m.end(7))
    for m in _REFERENCIA.finditer(linea):
        if zonas.libre(a + m.start(), a + m.end()):
            zonas.grupos(m, a, {1: "s", 5: "p"})
            zonas.separa(a + m.start(3), a + m.end(3))
    for m in _ESCAPE.finditer(linea):
        if zonas.z[a + m.start()] == "t":
            zonas.poner(a + m.start(), a + m.end(), "s")
    for m in _ENFASIS.finditer(linea):
        i, j = a + m.start(), a + m.end()
        if not zonas.libre(i, j) or (i > 0 and texto[i - 1] == "\\"):
            continue
        antes = texto[i - 1] if i > a else " "
        despues = texto[j] if j < b else " "
        izq = not despues.isspace() and (not _PUNTUACION.match(despues) or antes.isspace()
                                          or bool(_PUNTUACION.match(antes)))
        der = not antes.isspace() and (not _PUNTUACION.match(antes) or despues.isspace()
                                        or bool(_PUNTUACION.match(despues)))
        if m.group(0)[0] == "_":
            abre = izq and (not der or bool(_PUNTUACION.match(antes)))
            cierra = der and (not izq or bool(_PUNTUACION.match(despues)))
            if not (abre or cierra):
                continue        # dentro de una palabra: juan_perez@correo.es
        elif not (izq or der):
            continue            # suelto entre espacios: 2 * 3
        zonas.poner(i, j, "s")


def zonas(texto: str) -> str:
    """Una letra de zona por caracter de *texto* (ver el docstring del modulo)."""
    from markdown_it import MarkdownIt

    z = _Zonas(texto)
    filas = lineas(texto)
    especiales: dict[int, str] = {}      # linea -> "c", "h", "s" o "tabla"
    delimitadoras: set[int] = set()
    for t in MarkdownIt("commonmark").enable("table").parse(texto):
        if not t.map:
            continue
        ini, fin = t.map
        if t.type == "fence":
            especiales[ini] = "s"
            for n in range(ini + 1, fin):
                especiales[n] = "c"
            cierre = fin - 1
            if cierre > ini and re.fullmatch(r"(?:[ \t]*>[ ]?)*[ \t]*(`{3,}|~{3,})[ \t]*",
                                               texto[slice(*filas[cierre])]):
                especiales[cierre] = "s"
        elif t.type == "code_block":
            for n in range(ini, fin):
                especiales[n] = "c"
        elif t.type == "html_block":
            for n in range(ini, fin):
                especiales.setdefault(n, "h")
        elif t.type == "hr":
            especiales[ini] = "s"
        elif t.type == "heading_open" and t.markup in ("=", "-"):
            especiales[fin - 1] = "s"
        elif t.type == "table_open":
            for n in range(ini, fin):
                especiales.setdefault(n, "tabla")
            delimitadoras.add(ini + 1)

    for n, (a, b) in enumerate(filas):
        clase = especiales.get(n)
        if clase == "s" or n in delimitadoras:
            z.poner(a, b, "s")
            continue
        if clase in ("c", "h"):
            # Dentro de una cita, los ">" del principio siguen siendo sintaxis.
            cita = _CITA.match(texto, a, b)
            z.poner(a, cita.end(), "s")
            z.poner(cita.end(), b, clase)
            continue
        inicio = _PREFIJO.match(texto, a, b).end()
        z.poner(a, inicio, "s")
        titulo = _TITULO.match(texto, inicio, b)
        if titulo:
            z.poner(titulo.start(), titulo.end(), "s")
            cierre = _CIERRE_TITULO.search(texto, titulo.end(), b)
            if cierre:
                z.poner(cierre.start(), cierre.end(), "s")
        definicion = _DEFINICION.match(texto, inicio, b)
        if definicion:
            z.grupos(definicion, 0, {1: "s"})
            z.separa(definicion.start(3), definicion.end(3))
            z.destino(definicion.start(5), definicion.end(5))
            if definicion.group(7):
                z.titulo(definicion.start(7), definicion.end(7))
            continue
        if clase == "tabla":
            barras = [k for k in range(inicio, b) if texto[k] == "|" and (k == 0 or texto[k - 1] != "\\")]
            for k in barras:
                # La primera y la ultima, si abren o cierran la fila, no separan nada.
                borde = (k == barras[0] and not texto[inicio:k].strip()) or \
                        (k == barras[-1] and not texto[k + 1:b].strip())
                z.poner(k, k + 1, "s" if borde else "p")
        _en_linea(z, inicio, b)
    for k, c in enumerate(texto):
        if c == "\r":
            z.z[k] = "s"
    return "".join(z.z)


def sin_marcado(texto: str) -> str:
    """Las zonas de un texto plano: todo es texto salvo los retornos de carro."""
    return "".join("s" if c == "\r" else "t" for c in texto)


def para_analizar(texto: str, zona: str) -> tuple[str, list[int]]:
    """El texto que lee el analizador y, por cada caracter suyo, su posicion en
    el original (mas una al final).  La sintaxis "s" no se lee; cada racha de
    "p", un tabulador."""
    salida: list[str] = []
    origen: list[int] = []
    for k, (c, z) in enumerate(zip(texto, zona)):
        if z == "s":
            continue
        if z == "p":
            if salida and salida[-1] == "\t" and zona[origen[-1]] == "p":
                continue
            salida.append("\t")
        else:
            salida.append(c)
        origen.append(k)
    origen.append(len(texto))
    return "".join(salida), origen
