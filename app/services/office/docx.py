"""Documentos de Word (.docx): el dato se sustituye por su tipo y el resultado es un Word.

No hay barras: cada dato pasa a ser un marcador con su tipo ("[DNI]",
"[NOMBRE]"), con el formato del tramo donde empezaba.  Se trabaja sobre el XML
de cada parte del paquete, no con python-docx, porque python-docx no ve
cuadros de texto, notas al pie, controles de contenido ni texto borrado con
control de cambios.

Limpieza (tambien en "solo quitar metadatos"):
  - se aceptan todos los cambios: lo borrado con control de cambios sigue
    dentro del fichero y se quita; lo insertado pasa a ser texto normal;
  - fuera comentarios, texto oculto, codigos de campo, enlaces (el texto se
    queda), textos alternativos, enlaces de datos de controles de contenido,
    identificadores de sesion de edicion (rsid) y permisos con nombre;
  - fuera propiedades del documento (autor, empresa...), propiedades
    personalizadas, datos XML personalizados, miniatura, plantilla adjunta,
    variables del documento, combinacion de correspondencia, bloques de
    creacion (glosario) y relaciones externas;
  - los objetos incrustados (OLE, graficos con sus datos) se quitan y dejan un
    marcador: llevan dentro datos que no se ven.
Las imagenes JPEG y PNG pasan por el camino de imagenes (tachado dentro de la
imagen, sin metadatos); las de otros formatos, que no se pueden revisar, se
quitan con un marcador.

Deteccion: el texto se analiza por partes (cuerpo, cada cabecera, cada pie,
notas), linea a linea en orden: un parrafo es una linea y una fila de tabla es
una linea con sus celdas separadas por tabuladores, que es como se leen un
rotulo y su valor.  Despues, cada valor detectado se busca en todo el
documento y se sustituye donde aparezca (un nombre de la cabecera repetido en
el cuerpo).
"""
from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Callable

from lxml import etree

from app.core.errors import OfficeMacrosError, ProcessingError, UnsupportedDocumentError
from app.schemas.report import CONTENT_NOT_REVIEWED, CodedWarning, ProcessingReport
from app.services.office.paquete import Paquete, limpiar_propiedades, tipo_office, verificar

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"


def w(tag: str) -> str:
    return f"{{{W}}}{tag}"


#: Marcador por tipo.  En mayusculas y entre corchetes: se distingue del texto y
#: se lee en castellano tambien en un documento en valenciano.
MARCADORES = {
    "PERSON": "NOMBRE", "ES_DNI": "DNI", "ES_NIE": "NIE", "ES_CIF": "CIF",
    "ES_NSS": "Nº SEGURIDAD SOCIAL", "PASSPORT": "PASAPORTE", "ORGANIZATION": "EMPRESA",
    "ADDRESS": "DIRECCIÓN", "POSTAL_CODE": "CÓDIGO POSTAL", "PHONE_NUMBER": "TELÉFONO",
    "EMAIL_ADDRESS": "CORREO", "IBAN_CODE": "IBAN", "MONEY": "IMPORTE", "BUDGET_AMOUNT": "IMPORTE",
    "CREDIT_CARD": "TARJETA", "DATE_OF_BIRTH": "FECHA DE NACIMIENTO",
    "PLACE_OF_BIRTH": "LUGAR DE NACIMIENTO", "DATE_TIME": "FECHA", "LICENSE_PLATE": "MATRÍCULA",
    "CADASTRAL_REFERENCE": "REFERENCIA CATASTRAL", "CASE_NUMBER": "Nº EXPEDIENTE",
    "REGISTRY_NUMBER": "Nº REGISTRO", "CSV": "CSV", "CERT_SERIAL": "Nº CERTIFICADO",
    "MEDICAL_ID": "Nº SANITARIO", "POLICY_NUMBER": "Nº PÓLIZA", "USER_ID": "USUARIO",
    "IP_ADDRESS": "IP", "URL": "WEB", "QR_CODE": "CÓDIGO QR",
}
OBJETO_ELIMINADO = "[OBJETO ELIMINADO]"
IMAGEN_ELIMINADA = "[IMAGEN ELIMINADA]"

_PARTES_DE_TEXTO = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")
_CAMBIOS_FUERA = ("del", "moveFrom", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart",
                  "moveToRangeEnd", "rPrChange", "pPrChange", "sectPrChange", "tblPrChange",
                  "trPrChange", "tcPrChange", "tblGridChange", "numberingChange", "tblPrExChange",
                  "cellDel", "customXmlDelRangeStart", "customXmlDelRangeEnd")
_CAMBIOS_DENTRO = ("ins", "moveTo")
_COMENTARIOS = ("word/comments.xml", "word/commentsExtended.xml", "word/commentsIds.xml",
                "word/commentsExtensible.xml", "word/people.xml")


def marcador(tipo: str) -> str:
    return f"[{MARCADORES.get(tipo, tipo.replace('_', ' '))}]"


def _abrir(entrada: Path) -> Paquete:
    tipo = tipo_office(entrada)
    if tipo == "docm":
        raise OfficeMacrosError("Word document with macros: save it as .docx without macros.")
    if tipo != "docx":
        raise UnsupportedDocumentError("The file is not a Word document.")
    return Paquete.abrir(entrada)


def _desenvolver(e: etree._Element) -> None:
    """Sustituye *e* por sus hijos (un enlace, un cambio insertado)."""
    padre = e.getparent()
    i = padre.index(e)
    for hijo in list(e):
        padre.insert(i, hijo)
        i += 1
    padre.remove(e)


def _quitar(e: etree._Element) -> None:
    e.getparent().remove(e)


def _run_con_texto(texto: str, plantilla: etree._Element | None = None) -> etree._Element:
    r = etree.Element(w("r"))
    if plantilla is not None:
        rpr = plantilla.find(w("rPr"))
        if rpr is not None:
            r.append(etree.fromstring(etree.tostring(rpr)))
    t = etree.SubElement(r, w("t"))
    t.text = texto
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


# --- limpieza (tambien en solo metadatos) ----------------------------------------

def _limpiar_texto(raiz: etree._Element, cuenta: Counter) -> None:
    for tag in _CAMBIOS_FUERA:
        for e in list(raiz.iter(w(tag))):
            cuenta["tracked_changes"] += tag in ("del", "moveFrom")
            _quitar(e)
    for tag in _CAMBIOS_DENTRO:
        for e in list(raiz.iter(w(tag))):
            cuenta["tracked_changes"] += 1
            _desenvolver(e)
    for e in list(raiz.iter(w("delText"))):
        _quitar(e)
    for tag in ("commentRangeStart", "commentRangeEnd", "commentReference"):
        for e in list(raiz.iter(w(tag))):
            _quitar(e)
    for vanish in list(raiz.iter(w("vanish"))):
        rpr = vanish.getparent()
        run = rpr.getparent() if rpr is not None else None
        if run is not None and run.tag == w("r"):
            cuenta["hidden_text"] += 1
            _quitar(run)
        else:
            _quitar(vanish)           # marca de parrafo oculta: el parrafo se queda
    for e in list(raiz.iter(w("instrText"))):
        cuenta["field_codes"] += 1
        _quitar(e)
    for e in list(raiz.iter(w("fldSimple"))):
        cuenta["field_codes"] += 1
        _desenvolver(e)
    for e in list(raiz.iter(w("hyperlink"))):
        if e.get(f"{{{R}}}id"):
            cuenta["links"] += 1
        _desenvolver(e)
    for tag in ("dataBinding", "permStart", "permEnd"):
        for e in list(raiz.iter(w(tag))):
            _quitar(e)
    for e in raiz.iter(f"{{{WP}}}docPr", f"{{{PIC}}}cNvPr"):
        if e.get("descr") or e.get("title"):
            cuenta["alt_text"] += e.tag == f"{{{WP}}}docPr"
        for atributo in ("descr", "title"):
            e.attrib.pop(atributo, None)
    for e in raiz.iter(f"{{{V}}}shape", f"{{{V}}}imagedata"):
        for atributo in ("alt", f"{{{O}}}title"):
            e.attrib.pop(atributo, None)
    # Objetos incrustados y graficos: fuera, con un marcador.
    for e in list(raiz.iter(w("object"))):
        cuenta["embedded_objects"] += 1
        e.getparent().replace(e, _t(OBJETO_ELIMINADO))
    for gd in list(raiz.iter(f"{{{A}}}graphicData")):
        if "chart" in gd.get("uri", "") or "diagram" in gd.get("uri", ""):
            dibujo = next((a for a in gd.iterancestors() if a.tag == w("drawing")), None)
            if dibujo is not None:
                cuenta["embedded_objects"] += 1
                dibujo.getparent().replace(dibujo, _t(OBJETO_ELIMINADO))
    # Identificadores de sesion de edicion: dicen cuando y en cuantas sesiones.
    for e in raiz.iter():
        if isinstance(e.tag, str):
            for atributo in [a for a in e.attrib if etree.QName(a).localname.startswith("rsid")]:
                del e.attrib[atributo]


def _t(texto: str) -> etree._Element:
    t = etree.Element(w("t"))
    t.text = texto
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return t


def _limpiar_paquete(paq: Paquete, cuenta: Counter) -> None:
    for nombre in paq.nombres("word/"):
        if _PARTES_DE_TEXTO.match(nombre):
            _limpiar_texto(paq.xml(nombre), cuenta)
    comentarios = paq.xml("word/comments.xml")
    if comentarios is not None:
        cuenta["comments"] += len(comentarios.findall(w("comment")))
    for nombre in _COMENTARIOS:
        paq.borrar(nombre)
    for nombre in paq.nombres("word/glossary/"):
        paq.borrar(nombre)
    for prefijo in ("word/embeddings/", "word/charts/", "word/activeX/", "word/diagrams/"):
        for nombre in paq.nombres(prefijo):
            paq.borrar(nombre)
    paq.borrar("word/vbaProject.bin")
    limpiar_propiedades(paq, cuenta)
    ajustes = paq.xml("word/settings.xml")
    if ajustes is not None:
        for tag in ("attachedTemplate", "rsids", "docVars", "mailMerge", "writeProtection"):
            for e in ajustes.findall(w(tag)):
                cuenta["document_info"] += tag in ("attachedTemplate", "docVars", "mailMerge")
                ajustes.remove(e)
        paq.borrar("word/_rels/settings.xml.rels")


# --- imagenes ------------------------------------------------------------------

def _formato_imagen(datos: bytes) -> str | None:
    if datos[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def _imagenes(paq: Paquete, procesar: Callable[[Path, Path, str], object], cuenta: Counter
              ) -> list[object]:
    """Cada imagen JPEG o PNG por el camino de imagenes; las demas, fuera."""
    informes = []
    usadas = set()
    for nombre in paq.nombres("word/"):
        if _PARTES_DE_TEXTO.match(nombre):
            usadas |= set(paq.destinos(nombre).values())
    with tempfile.TemporaryDirectory() as tmp:
        for nombre in paq.nombres("word/media/"):
            datos = paq.partes[nombre]
            formato = _formato_imagen(datos)
            if formato is None:
                continue
            entrada = Path(tmp) / f"e.{formato}"
            salida = Path(tmp) / f"s.{formato}"
            entrada.write_bytes(datos)
            informes.append(procesar(entrada, salida, formato))
            paq.partes[nombre] = salida.read_bytes()
            cuenta["images"] += 1
    # Imagenes que no se pueden revisar (EMF, WMF, GIF, SVG...): fuera.
    for nombre in paq.nombres("word/"):
        if not _PARTES_DE_TEXTO.match(nombre):
            continue
        destinos = paq.destinos(nombre)
        raiz = paq.xml(nombre)
        for dibujo in list(raiz.iter(w("drawing"), w("pict"))):
            ids = [v for e in dibujo.iter() if isinstance(e.tag, str)
                   for k, v in e.attrib.items() if etree.QName(k).namespace == R]
            malas = [i for i in ids if i in destinos and destinos[i].startswith("word/media/")
                     and _formato_imagen(paq.partes.get(destinos[i], b"")) is None]
            if malas:
                cuenta["images_removed"] += 1
                dibujo.getparent().replace(dibujo, _t(IMAGEN_ELIMINADA))
                for i in malas:
                    paq.borrar(destinos[i])
    return informes


# --- deteccion -----------------------------------------------------------------

def _parrafo_de(e: etree._Element) -> etree._Element | None:
    for a in e.iterancestors():
        if a.tag == w("p"):
            return a
    return None


def _celda_de(e: etree._Element) -> etree._Element | None:
    for a in e.iterancestors():
        if a.tag == w("tc"):
            return a
        if a.tag == w("txbxContent"):
            return None
    return None


def _tramos(p: etree._Element) -> list[tuple[etree._Element, str]]:
    """Los tramos de texto de un parrafo (sin los de parrafos anidados)."""
    salida = []
    for e in p.iter(w("t"), w("tab"), w("br"), w("cr"), w("noBreakHyphen")):
        if _parrafo_de(e) is not p:
            continue
        if e.tag == w("t"):
            salida.append((e, e.text or ""))
        elif e.tag == w("tab"):
            if e.getparent().tag == w("r"):
                salida.append((e, "\t"))
        elif e.tag == w("noBreakHyphen"):
            salida.append((e, "-"))
        else:
            salida.append((e, "\n"))
    return salida


class Bloque:
    """Texto de una parte, con el tramo XML de cada caracter."""

    def __init__(self) -> None:
        self.texto = ""
        self.tramos: list[tuple[etree._Element, int, int]] = []

    def anadir(self, tramos: list[tuple[etree._Element, str]], separador: str = "\n") -> None:
        if self.texto:
            self.texto += separador
        for e, t in tramos:
            self.tramos.append((e, len(self.texto), len(self.texto) + len(t)))
            self.texto += t


def bloques(raiz: etree._Element, maximo: int = 4000) -> list[Bloque]:
    """Lineas de la parte en orden: parrafos y filas de tabla (celdas con
    tabuladores), agrupadas en bloques de hasta *maximo* caracteres."""
    salida, actual = [], Bloque()
    for e in raiz.iter(w("p"), w("tr")):
        if e.tag == w("p"):
            if _celda_de(e) is not None:
                continue
            linea = [_tramos(e)]
        else:
            linea = []
            for celda in e.iter(w("tc")):
                if next((a for a in celda.iterancestors() if a.tag == w("tr")), None) is not e:
                    continue
                trozos = []
                for p in celda.iter(w("p")):
                    if _celda_de(p) is celda:
                        if trozos:
                            trozos.append((None, " "))
                        trozos += _tramos(p)
                linea.append(trozos)
        tramos: list = []
        for i, trozos in enumerate(linea):
            if i:
                tramos.append((None, "\t"))
            tramos += trozos
        if len(actual.texto) > maximo:
            salida.append(actual)
            actual = Bloque()
        actual.anadir([(x, t) for x, t in tramos])
    if actual.texto:
        salida.append(actual)
    return salida


def _sustituir(bloque: Bloque, inicio: int, fin: int, texto: str) -> None:
    """Cambia bloque.texto[inicio:fin] por *texto* en los tramos XML: el primero
    se queda con el marcador y el formato; los demas pierden lo cubierto."""
    primero = True
    for e, a, b in bloque.tramos:
        if e is None or b <= inicio or a >= fin:
            continue
        if e.tag != w("t"):
            if e.getparent() is not None:
                _quitar(e)
            continue
        actual = e.text or ""
        izq, der = max(0, inicio - a), min(len(actual), fin - a)
        e.text = actual[:izq] + (texto if primero else "") + actual[der:]
        e.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        primero = False


def _sin_etiqueta(texto: str, inicio: int, fin: int, tipo: str) -> tuple[int, int, str]:
    """El analizador mete a veces la etiqueta en el dato ("de contacto: 611 203
    945").  Una etiqueta acaba en dos puntos y espacio: el dato empieza tras los
    ultimos.  Dos puntos sin espacio son del dato (una hora, una direccion web).
    En un PDF esa etiqueta quedaria bajo la barra; aqui se sustituiria el texto."""
    # Un dato no cruza de una celda de tabla a otra (las celdas van separadas
    # por tabuladores): "DNI / NIF<tab>29.384.751-K" o "Teresa Navarro
    # Gil<tab>Presidenta".  Se queda el trozo mas largo, que es el valor.
    if "	" in texto[inicio:fin]:
        trozos, pos = [], inicio
        for parte in texto[inicio:fin].split("	"):
            trozos.append((len(parte.strip()), pos, pos + len(parte)))
            pos += len(parte) + 1
        _, inicio, fin = max(trozos)
        while inicio < fin and texto[inicio].isspace():
            inicio += 1
        while fin > inicio and texto[fin - 1].isspace():
            fin -= 1
    tramo = texto[inicio:fin]
    cortes = [m.end() for m in re.finditer(r":\s+", tramo)]
    if cortes and tramo[cortes[-1]:].strip():
        inicio += cortes[-1]
    return inicio, fin, tipo


def _spans(bloque: Bloque, analyzer, mode: str) -> list[tuple[int, int, str]]:
    from app.services.layout.zone_config import ALL_PII_TYPES

    tipos = set(ALL_PII_TYPES)
    entidades = analyzer.analyze(bloque.texto, mode=mode, zone_type=None, context_text=bloque.texto)
    spans = sorted(_sin_etiqueta(bloque.texto, e.start, e.end, e.entity_type)
                   for e in entidades if e.entity_type in tipos)
    # Sin solapes: gana el mas largo.
    salida: list[tuple[int, int, str]] = []
    for s in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if salida and s[0] < salida[-1][1]:
            if s[1] - s[0] > salida[-1][1] - salida[-1][0]:
                salida[-1] = s
            continue
        salida.append(s)
    return salida


def _barrido(bloque: Bloque, valores: dict[str, str], spans: list[tuple[int, int, str]]
             ) -> list[tuple[int, int, str]]:
    """Cada valor detectado en el documento, donde mas aparezca."""
    ocupado = [(a, b) for a, b, _ in spans]
    nuevos = []
    for valor, tipo in valores.items():
        for m in re.finditer(re.escape(valor), bloque.texto, re.IGNORECASE):
            if any(m.start() < b and m.end() > a for a, b in ocupado):
                continue
            nuevos.append((m.start(), m.end(), tipo))
            ocupado.append((m.start(), m.end()))
    return sorted(spans + nuevos)


# --- entrada ---------------------------------------------------------------------

def anonimizar_docx(entrada: Path, salida: Path, analyzer, procesar_imagen, job_id: str,
                    mode: str = "strict") -> ProcessingReport:
    """`procesar_imagen(entrada, salida, formato)` tacha una imagen (camino de imagenes)."""
    empezado = perf_counter()
    paq = _abrir(Path(entrada))
    cuenta: Counter = Counter()
    _limpiar_paquete(paq, cuenta)
    informes_imagen = _imagenes(paq, procesar_imagen, cuenta)

    partes = [n for n in paq.nombres("word/") if _PARTES_DE_TEXTO.match(n)]
    por_bloque = []
    valores: dict[str, str] = {}
    for nombre in partes:
        for bloque in bloques(paq.xml(nombre)):
            spans = _spans(bloque, analyzer, mode)
            por_bloque.append((bloque, spans))
            for a, b, tipo in spans:
                valor = bloque.texto[a:b].strip()
                if len(valor) >= 4:
                    valores.setdefault(valor, tipo)
    tipos: Counter = Counter()
    for bloque, spans in por_bloque:
        for a, b, tipo in sorted(_barrido(bloque, valores, spans), reverse=True):
            _sustituir(bloque, a, b, marcador(tipo))
            tipos[tipo] += 1
    for r in informes_imagen:
        for tipo, n in (getattr(r, "entities_by_type", None) or {}).items():
            tipos[tipo] += n

    paq.guardar(Path(salida))
    _comprobar_que_se_abre(Path(salida))
    quedan = verificar(Path(salida), list(valores))
    avisos = []
    if quedan:
        avisos.append(f"{len(quedan)} detected values still found in the output.")
    if cuenta["images_removed"]:
        avisos.append(f"{cuenta['images_removed']} images in formats that cannot be reviewed were removed.")
    contenido = [p for r in informes_imagen for p in (getattr(r, "pages_redacted_by_content", None) or [])]
    if contenido:
        avisos.append(f"{len(contenido)} embedded images redacted in full by content.")
    quitado = {k: v for k, v in cuenta.items() if v and k not in ("images",)}
    return ProcessingReport(
        job_id=job_id,
        status="manual_review_required" if quedan else ("completed_with_warnings" if avisos else "completed"),
        document_type="docx",
        pages=1,
        entities_detected=sum(tipos.values()),
        entities_redacted=sum(tipos.values()),
        entities_by_type=dict(tipos),
        residual_entities=len(quedan),
        manual_review_required=bool(quedan),
        warnings=avisos,
        metadata_removed=quitado,
        redaction_strategy="docx_text_replacement",
        processing_ms=int((perf_counter() - empezado) * 1000),
        ocr={"enabled": bool(informes_imagen), "images_processed": len(informes_imagen)},
    )


def solo_metadatos_docx(entrada: Path, salida: Path, job_id: str = "") -> ProcessingReport:
    from app.services.image.imagen import solo_metadatos_imagen

    empezado = perf_counter()
    paq = _abrir(Path(entrada))
    cuenta: Counter = Counter()
    _limpiar_paquete(paq, cuenta)

    def _sin_metadatos(e: Path, s: Path, formato: str):
        r = solo_metadatos_imagen(e, s, formato)
        cuenta["photo_metadata"] += r.metadata_removed.get("photo_metadata", 0)
        return None

    _imagenes(paq, _sin_metadatos, cuenta)
    paq.guardar(Path(salida))
    _comprobar_que_se_abre(Path(salida))
    return ProcessingReport(
        job_id=job_id, status="completed_with_warnings", document_type="docx", treatment="metadata_only",
        pages=1, warnings=["Metadata removed only: the content has NOT been reviewed or anonymized."],
        coded_warnings=[CodedWarning(code=CONTENT_NOT_REVIEWED)],
        metadata_removed={k: v for k, v in cuenta.items() if v and k != "images"},
        redaction_strategy="metadata_only", processing_ms=int((perf_counter() - empezado) * 1000),
    )


def _comprobar_que_se_abre(ruta: Path) -> None:
    """Un Word roto no se entrega: tiene que volver a abrirse."""
    import zipfile

    import docx

    try:
        with zipfile.ZipFile(ruta) as z:
            if z.testzip() is not None:
                raise ValueError("zip dañado")
        docx.Document(str(ruta))
    except Exception as exc:
        raise ProcessingError(f"The anonymized Word document could not be reopened: {exc}") from exc
