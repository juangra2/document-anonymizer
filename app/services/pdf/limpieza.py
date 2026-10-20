"""En el PDF entregado no queda nada oculto ni interactivo.

Principio: todo lo que se ve en la pagina ha pasado por el analisis; lo demas,
fuera.  Dos momentos:

* `integrar_anotaciones`, al abrir el documento y ANTES del analisis: las
  anotaciones que se ven (texto libre, sellos, dibujos, subrayados) y los campos
  de formulario pasan a ser contenido de la pagina, asi que su texto se analiza y
  se tacha como el resto.  Un rotulo pintado dentro de un campo se conserva.  Lo
  que no se ve (notas de comentario, adjuntos, enlaces, sonidos, anotaciones
  ocultas) se quita sin integrarlo.
* `limpiar_para_entregar`, justo antes de guardar, en todos los caminos: quita lo
  que quede de anotaciones y campos, marcadores, JavaScript (apertura, arbol de
  nombres, acciones), adjuntos, miniaturas, metadatos XMP de documento, pagina e
  imagen, la estructura de etiquetas y el diccionario de informacion; y limpia los
  JPEG que se conservan (`limpiar_jpeg`) sin volver a comprimirlos.

El guardado entero (`incremental=False`, `garbage=4`) lo hace quien llama: es lo
que elimina las versiones anteriores y los objetos que esto deja sin referencia.
"""
from __future__ import annotations

import logging
import re

import fitz

logger = logging.getLogger(__name__)

# Anotaciones que no se ven en la pagina, o cuyo contenido no esta en lo que se ve.
_NO_VISIBLES = {
    fitz.PDF_ANNOT_TEXT, fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_FILE_ATTACHMENT,
    fitz.PDF_ANNOT_SOUND, fitz.PDF_ANNOT_MOVIE, fitz.PDF_ANNOT_LINK,
    fitz.PDF_ANNOT_SCREEN, fitz.PDF_ANNOT_REDACT, fitz.PDF_ANNOT_RICH_MEDIA,
    fitz.PDF_ANNOT_3D, fitz.PDF_ANNOT_PRINTER_MARK, fitz.PDF_ANNOT_TRAP_NET,
    fitz.PDF_ANNOT_WATERMARK, fitz.PDF_ANNOT_PROJECTION,
}
_OCULTA = fitz.PDF_ANNOT_IS_HIDDEN | fitz.PDF_ANNOT_IS_NO_VIEW

# Claves del catalogo que no son contenido de pagina.  /PageLabels (numeracion)
# y /Lang se quedan: no llevan datos y el visor los usa.  /Info dentro del
# catalogo lo deja MuPDF con su nombre de productor.  /PageMode abriria un panel
# de adjuntos o marcadores que ya no existen.
_CATALOGO = ("OpenAction", "AA", "AcroForm", "Names", "Outlines", "Metadata",
             "PieceInfo", "Threads", "SpiderInfo", "URI", "Collection", "Perms",
             "Legal", "StructTreeRoot", "MarkInfo", "Info", "PageMode")
_PAGINA = ("Metadata", "Thumb", "PieceInfo", "AA", "B", "StructParents", "Annots")


def _quitar_claves(doc: fitz.Document, xref: int, claves) -> None:
    """Borra las claves del diccionario.  xref_set_key(..., "null") las deja
    escritas como "/Clave null"; se reescribe el objeto sin ellas."""
    presentes = [c for c in claves if doc.xref_get_key(xref, c)[0] != "null"]
    for clave in presentes:
        doc.xref_set_key(xref, clave, "null")
    objeto = doc.xref_object(xref)
    limpio = re.sub(r"/(?:%s)\s+null\b" % "|".join(map(re.escape, claves)), "", objeto)
    if limpio != objeto:
        doc.update_object(xref, limpio)


def integrar_anotaciones(doc: fitz.Document) -> int:
    """Antes del analisis: lo visible a la pagina, lo invisible fuera.  Devuelve
    cuantas anotaciones y campos se han integrado."""
    integradas = 0
    for page in doc:
        for annot in list(page.annots()):
            if annot.type[0] in _NO_VISIBLES or annot.flags & _OCULTA:
                page.delete_annot(annot)
            else:
                integradas += 1
        integradas += len(list(page.widgets()))
    if integradas:
        # bake pinta la apariencia de cada anotacion y campo en el contenido de
        # la pagina y los quita; el documento deja de ser un formulario.
        doc.bake(annots=True, widgets=True)
    return integradas


def limpiar_para_entregar(doc: fitz.Document) -> None:
    """Justo antes de guardar: fuera todo lo que no es contenido de pagina."""
    for page in doc:
        for annot in list(page.annots()):
            page.delete_annot(annot)
        for widget in list(page.widgets()):
            page.delete_widget(widget)
        _quitar_claves(doc, page.xref, _PAGINA)
    doc.set_toc([])
    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except Exception:
        pass
    _quitar_claves(doc, doc.pdf_catalog(), _CATALOGO)
    contenidos = {x for page in doc for x in page.get_contents()}
    for xref in range(1, doc.xref_length()):
        try:
            _limpiar_objeto(doc, xref, contenidos)
        except Exception:  # un objeto roto no impide entregar el resto limpio
            logger.debug("objeto %d sin limpiar", xref, exc_info=True)


# Copias de texto en el contenido marcado (/Span <</ActualText (...)>> BDC): el
# tachado quita los glifos pero no estas propiedades, y copiar el texto o un
# lector de pantalla las devuelven.  Tambien el texto alternativo y la expansion
# de abreviaturas.
_COPIAS_DE_TEXTO = (b"/ActualText", b"/Alt", b"/E")


def _fin_de_cadena(datos: bytes, i: int) -> int:
    """Indice tras la cadena PDF (literal o hexadecimal) que empieza en i, o -1."""
    if datos[i:i + 1] == b"<":
        fin = datos.find(b">", i)
        return -1 if fin < 0 else fin + 1
    if datos[i:i + 1] != b"(":
        return -1
    profundidad, j = 0, i
    while j < len(datos):
        c = datos[j:j + 1]
        if c == b"\\":
            j += 2
            continue
        if c == b"(":
            profundidad += 1
        elif c == b")":
            profundidad -= 1
            if profundidad == 0:
                return j + 1
        j += 1
    return -1


def quitar_copias_de_texto(flujo: bytes) -> bytes:
    """Quita /ActualText, /Alt y /E de las propiedades del contenido marcado.

    Solo dentro de un diccionario en linea seguido de BDC; el resto del flujo,
    byte a byte igual.
    """
    if b"BDC" not in flujo or not any(c in flujo for c in _COPIAS_DE_TEXTO):
        return flujo
    salida = bytearray()
    i = 0
    for m in re.finditer(rb"<<(?:[^<>]|<[0-9A-Fa-f\s]*>|\((?:\\.|[^\\)])*\))*?>>\s*BDC", flujo, re.S):
        salida += flujo[i:m.start()]
        dic = m.group(0)
        limpio = bytearray()
        j = 0
        while j < len(dic):
            clave = next((c for c in _COPIAS_DE_TEXTO
                          if dic.startswith(c, j) and not dic[j + len(c):j + len(c) + 1].isalnum()), None)
            if clave is None:
                limpio += dic[j:j + 1]
                j += 1
                continue
            k = j + len(clave)
            while k < len(dic) and dic[k:k + 1] in b" \t\r\n":
                k += 1
            fin = _fin_de_cadena(dic, k)
            if fin < 0:
                limpio += dic[j:j + 1]
                j += 1
                continue
            j = fin
        salida += limpio
        i = m.end()
    salida += flujo[i:]
    return bytes(salida)


def _limpiar_objeto(doc: fitz.Document, xref: int, contenidos: set[int]) -> None:
    objeto = doc.xref_object(xref)
    # Propiedades con nombre (/Properties del recurso) y elementos de estructura
    if "/ActualText" in objeto or ("/Alt" in objeto and "/Type /StructElem" in objeto):
        _quitar_claves(doc, xref, ("ActualText", "Alt", "E"))
        objeto = doc.xref_object(xref)
    # Flujos de contenido: los de pagina y los formularios XObject
    if doc.xref_is_stream(xref) and ("/Subtype /Form" in objeto or xref in contenidos):
        flujo = doc.xref_stream(xref)
        limpio = quitar_copias_de_texto(flujo) if flujo else flujo
        if limpio != flujo:
            doc.update_stream(xref, limpio)
    if "/Metadata" in objeto:
        _quitar_claves(doc, xref, ("Metadata",))
    # Acciones JavaScript sueltas (de un campo o un enlace ya quitados quedan
    # huerfanas y las borra el guardado; las que cuelgan de otro sitio, aqui).
    if "/JS" in objeto or "/JavaScript" in objeto:
        _quitar_claves(doc, xref, ("JS", "AA", "OpenAction", "A", "JavaScript"))
        objeto = doc.xref_object(xref)
    if "/Subtype /Image" in objeto and re.search(r"/Filter\s*(\[\s*)?/DCTDecode\s*\]?", objeto):
        crudo = doc.xref_stream_raw(xref)
        limpio = limpiar_jpeg(crudo)
        if limpio is not None and limpio != crudo:
            doc.update_stream(xref, limpio, compress=False)
            # update_stream quita el filtro: sin el, el visor leeria el JPEG
            # como pixeles en bruto.
            doc.xref_set_key(xref, "Filter", "/DCTDecode")


# Segmentos que se conservan: perfil de color y marca de Adobe (en CMYK decide la
# transformacion de color).  Todo lo demas de APP0-APP15 y COM fuera: EXIF y XMP
# (APP1), IPTC/Photoshop (APP13), Ducky (APP12), JFIF (APP0, puede llevar una
# miniatura), comentarios.
def _conservar(marca: int, segmento: bytes) -> bool:
    if marca == 0xE2:
        return segmento[4:16] == b"ICC_PROFILE\x00"
    if marca == 0xEE:
        return segmento[4:9] == b"Adobe"
    return False


def limpiar_jpeg(datos: bytes) -> bytes | None:
    """Quita los segmentos de metadatos de un JPEG sin tocar los datos de imagen.

    Devuelve None si no se reconoce la estructura: entonces no se toca.
    """
    if not datos.startswith(b"\xff\xd8"):
        return None
    salida = bytearray(b"\xff\xd8")
    i = 2
    while i + 4 <= len(datos):
        if datos[i] != 0xFF:
            return None
        marca = datos[i + 1]
        if marca == 0xFF:            # relleno entre segmentos
            i += 1
            continue
        if marca == 0xDA:            # inicio de los datos de imagen: el resto, tal cual
            salida += datos[i:]
            return bytes(salida)
        if 0xD0 <= marca <= 0xD7 or marca == 0x01:
            salida += datos[i:i + 2]
            i += 2
            continue
        largo = int.from_bytes(datos[i + 2:i + 4], "big")
        segmento = datos[i:i + 2 + largo]
        if len(segmento) < 2 + largo or largo < 2:
            return None
        if (0xE0 <= marca <= 0xEF or marca == 0xFE) and not _conservar(marca, segmento):
            pass
        else:
            salida += segmento
        i += 2 + largo
    return None


def jpeg_con_metadatos(datos: bytes) -> bool:
    """True si el JPEG lleva algo que limpiar_jpeg quitaria (lo usa el verificador)."""
    limpio = limpiar_jpeg(datos)
    return limpio is not None and limpio != datos
