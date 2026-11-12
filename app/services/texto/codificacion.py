"""Leer un fichero de texto en su codificacion y escribirlo en la misma.

Muchos textos de la administracion vienen en codificaciones antiguas de Windows
(cp1252) o incluso de MS-DOS (cp850): leerlos como UTF-8 rompe las tildes y
las enes, y con ellas los nombres.  El orden:
  1. con BOM (UTF-8, UTF-16, UTF-32), la que dice el BOM;
  2. si es UTF-8 valido, UTF-8 (un texto solo ASCII tambien lo es);
  3. si no, la antigua que mejor lo lee: charset-normalizer elige entre
     cp1252, cp850 y mac_roman, y entre las que lo leen igual de bien gana la
     que da mas letras del castellano y el valenciano (n con tilde, vocales
     acentuadas, c trencada...); si aun asi empatan con lecturas distintas, se
     queda cp1252, la habitual, y la lectura es DUDOSA (el informe lo dice).
Un texto no lleva bytes nulos (salvo en UTF-16/32) ni apenas caracteres de
control: si los lleva, no es texto.

La salida se escribe con la misma codificacion y el mismo BOM.  Los finales de
linea no se tocan: el texto se lee sin convertirlos.
"""
from __future__ import annotations

import codecs
import unicodedata
from dataclasses import dataclass

from app.core.errors import FileValidationError

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_ANTIGUAS = ("cp1252", "cp850", "mac_roman")
_LETRAS = set("áéíóúàèòïüñçÁÉÍÓÚÀÈÒÏÜÑÇ€ºª·¿¡")
_CONTROL_PERMITIDO = set("\t\n\r\f\v")
#: Nombre para el informe.
NOMBRES = {"utf-8": "UTF-8", "utf-16-le": "UTF-16", "utf-16-be": "UTF-16", "utf-32-le": "UTF-32",
           "utf-32-be": "UTF-32", "cp1252": "Windows-1252", "cp850": "MS-DOS (850)",
           "mac_roman": "Mac Roman", "latin_1": "ISO-8859-1"}


@dataclass
class Lectura:
    texto: str
    codificacion: str
    bom: bytes = b""
    dudosa: bool = False

    @property
    def nombre(self) -> str:
        return NOMBRES.get(self.codificacion, self.codificacion) + (" con BOM" if self.bom and
                                                                    self.codificacion == "utf-8" else "")

    def escribir(self, texto: str) -> bytes:
        return self.bom + texto.encode(self.codificacion)

    def cabe(self, texto: str) -> bool:
        try:
            texto.encode(self.codificacion)
            return True
        except UnicodeEncodeError:
            return False


def _parece_texto(texto: str) -> bool:
    if "\x00" in texto:
        return False
    control = sum(1 for c in texto if unicodedata.category(c) == "Cc" and c not in _CONTROL_PERMITIDO)
    return control <= max(2, len(texto) // 200)


def _letras(texto: str) -> int:
    return sum(1 for c in texto if c in _LETRAS)


def _antigua(datos: bytes) -> Lectura:
    from charset_normalizer import from_bytes

    candidatas: dict[str, tuple[float, str]] = {}
    for m in from_bytes(datos, cp_isolation=list(_ANTIGUAS)):
        if m.encoding in _ANTIGUAS:
            candidatas[m.encoding] = (m.chaos, str(m))
    if not candidatas:
        # Ninguna la lee sin errores: latin-1 lee cualquier byte.
        try:
            return Lectura(datos.decode("cp1252"), "cp1252", dudosa=True)
        except UnicodeDecodeError:
            return Lectura(datos.decode("latin_1"), "latin_1", dudosa=True)
    mejor = min(c for c, _ in candidatas.values())
    empatadas = [(cod, t) for cod, (c, t) in candidatas.items() if c <= mejor + 0.02]
    # Mas letras del castellano y el valenciano; a igualdad, cp1252.
    empatadas.sort(key=lambda x: (-_letras(x[1]), _ANTIGUAS.index(x[0])))
    cod, texto = empatadas[0]
    dudosa = any(t != texto and _letras(t) == _letras(texto) for _, t in empatadas[1:])
    return Lectura(texto, cod, dudosa=dudosa)


def leer(datos: bytes) -> Lectura:
    """El texto y su codificacion.  FileValidationError si no es texto."""
    for bom, cod in _BOMS:
        if datos.startswith(bom):
            try:
                texto = datos[len(bom):].decode(cod)
            except UnicodeDecodeError as exc:
                raise FileValidationError("The text file is damaged: it does not match its encoding.") from exc
            if not _parece_texto(texto):
                raise FileValidationError("The file is not a text file.")
            return Lectura(texto, cod, bom)
    if b"\x00" in datos:
        raise FileValidationError("The file is not a text file.")
    try:
        lectura = Lectura(datos.decode("utf-8"), "utf-8")
    except UnicodeDecodeError:
        lectura = _antigua(datos)
    if not _parece_texto(lectura.texto):
        raise FileValidationError("The file is not a text file.")
    return lectura
