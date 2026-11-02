"""Paquetes de Office (OOXML): un zip de partes XML con sus relaciones.

Se abren con limites (numero de partes, tamaño descomprimido, proporcion de
compresion: un zip pequeño que descomprimido ocupa gigas es un ataque), con
nombres de parte sin rutas absolutas ni "..", y el XML se lee sin resolver
entidades ni cargar nada de la red.  Al guardar se quitan del indice de tipos
y de las relaciones las partes borradas, y todas las relaciones externas
(enlaces, plantillas, imagenes enlazadas): no llevan nada que se vea en el
documento y si pueden llevar direcciones, rutas con el nombre de usuario o
correos.

`verificar` es el verificador: busca cada valor en el texto, los atributos y
las relaciones de TODAS las partes del fichero de salida.
"""
from __future__ import annotations

import posixpath
import re
import unicodedata
import zipfile
from pathlib import Path
from time import monotonic

from lxml import etree

from app.core.config import settings
from app.core.errors import FileValidationError, OfficeTooLargeError, ProcessingTimeoutError

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"

_MAX_PARTES = 10_000
_MAX_DESCOMPRIMIDO = 600 * 1024 * 1024
_MAX_PROPORCION = 300

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                          huge_tree=False, remove_blank_text=False)

_TIPOS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml": "docx",
    "application/vnd.ms-word.document.macroEnabled.main+xml": "docm",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml": "docm",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml": "xlsx",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml": "xlsm",
}


def leer_xml(datos: bytes) -> etree._Element:
    return etree.fromstring(datos, _PARSER)


def tipo_office(ruta: Path) -> str | None:
    """docx, docm, xlsx, xlsm o None, por el tipo de la parte principal."""
    try:
        with zipfile.ZipFile(ruta) as z:
            tipos = leer_xml(z.read("[Content_Types].xml"))
    except Exception:
        return None
    for o in tipos.iter(f"{{{CT}}}Override"):
        t = _TIPOS.get(o.get("ContentType", ""))
        if t:
            return t
    return None


class Paquete:
    def __init__(self, partes: dict[str, bytes]) -> None:
        self.partes = partes
        self._xml: dict[str, etree._Element] = {}

    @classmethod
    def abrir(cls, ruta: Path) -> "Paquete":
        try:
            z = zipfile.ZipFile(ruta)
        except zipfile.BadZipFile as exc:
            raise FileValidationError("The file is not a valid Office document.") from exc
        with z:
            infos = z.infolist()
            if len(infos) > _MAX_PARTES:
                raise FileValidationError("Office document with too many parts.")
            # Dos partes con el mismo nombre: se leeria una y Word o Excel
            # podrian mostrar la otra, sin anonimizar.
            if len({i.filename for i in infos}) != len(infos):
                raise FileValidationError("Office document with repeated part names.")
            total = sum(i.file_size for i in infos)
            comprimido = max(1, sum(i.compress_size for i in infos))
            if total > _MAX_DESCOMPRIMIDO or total / comprimido > _MAX_PROPORCION:
                raise FileValidationError("Office document expands to an implausible size.")
            partes = {}
            for i in infos:
                nombre = i.filename
                if (nombre.startswith(("/", "\\")) or ".." in nombre.split("/")
                        or re.match(r"^[A-Za-z]:", nombre)):
                    raise FileValidationError("Office document with an unsafe part name.")
                if nombre.endswith("/"):
                    continue
                partes[nombre] = z.read(i)
        if "[Content_Types].xml" not in partes:
            raise FileValidationError("The file is not a valid Office document.")
        return cls(partes)

    # --- partes XML ----------------------------------------------------------

    def xml(self, nombre: str) -> etree._Element | None:
        if nombre not in self._xml:
            if nombre not in self.partes:
                return None
            self._xml[nombre] = leer_xml(self.partes[nombre])
        return self._xml[nombre]

    def borrar(self, nombre: str) -> bool:
        existia = nombre in self.partes
        self.partes.pop(nombre, None)
        self._xml.pop(nombre, None)
        self.partes.pop(rels_de(nombre), None)
        self._xml.pop(rels_de(nombre), None)
        return existia

    def nombres(self, prefijo: str = "") -> list[str]:
        return sorted(n for n in self.partes if n.startswith(prefijo))

    def tipo_de(self, nombre: str) -> str:
        tipos = self.xml("[Content_Types].xml")
        for o in tipos.iter(f"{{{CT}}}Override"):
            if o.get("PartName", "").lstrip("/") == nombre:
                return o.get("ContentType", "")
        ext = nombre.rsplit(".", 1)[-1].lower()
        for d in tipos.iter(f"{{{CT}}}Default"):
            if d.get("Extension", "").lower() == ext:
                return d.get("ContentType", "")
        return ""

    def destinos(self, origen: str) -> dict[str, str]:
        """Id de relacion -> parte de destino (las internas) de una parte."""
        rels = self.xml(rels_de(origen))
        salida = {}
        if rels is None:
            return salida
        for r in rels.iter(f"{{{PR}}}Relationship"):
            if r.get("TargetMode") == "External":
                continue
            salida[r.get("Id")] = resolver(origen, r.get("Target", ""))
        return salida

    # --- guardar -------------------------------------------------------------

    def guardar(self, ruta: Path) -> None:
        for nombre, raiz in self._xml.items():
            if nombre in self.partes:
                self.partes[nombre] = etree.tostring(raiz, xml_declaration=True, encoding="UTF-8",
                                                     standalone=True)
        tipos = leer_xml(self.partes["[Content_Types].xml"])
        for o in list(tipos.iter(f"{{{CT}}}Override")):
            if o.get("PartName", "").lstrip("/") not in self.partes:
                tipos.remove(o)
        self.partes["[Content_Types].xml"] = etree.tostring(tipos, xml_declaration=True,
                                                            encoding="UTF-8", standalone=True)
        for nombre in [n for n in self.partes if n.endswith(".rels")]:
            origen = origen_de_rels(nombre)
            if origen and origen not in self.partes:
                del self.partes[nombre]
                continue
            rels = leer_xml(self.partes[nombre])
            for r in list(rels.iter(f"{{{PR}}}Relationship")):
                if r.get("TargetMode") == "External":
                    rels.remove(r)
                elif resolver(origen or "", r.get("Target", "")) not in self.partes:
                    rels.remove(r)
            self.partes[nombre] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8",
                                                 standalone=True)
        with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
            # [Content_Types].xml primero, como lo escribe Office.
            z.writestr("[Content_Types].xml", self.partes["[Content_Types].xml"])
            for nombre, datos in self.partes.items():
                if nombre != "[Content_Types].xml":
                    z.writestr(nombre, datos)


_CELDA = re.compile(rb"<(?:\w+:)?c[\s/>]")
_PARRAFO = re.compile(rb"<(?:\w+:)?p[\s/>]")


def comprobar_tamano(paq: Paquete) -> None:
    """Demasiadas celdas (Excel) o parrafos (Word) para tratarlo de una vez: se
    rechaza al subirlo, contando en el XML sin interpretarlo."""
    if "word/document.xml" in paq.partes:
        n = sum(len(_PARRAFO.findall(d)) for nombre, d in paq.partes.items()
                if nombre.startswith("word/") and nombre.endswith(".xml"))
        if n > settings.max_office_paragraphs:
            raise OfficeTooLargeError(
                f"Word document with {n} paragraphs; the maximum is {settings.max_office_paragraphs}.")
        return
    n = sum(len(_CELDA.findall(d)) for nombre, d in paq.partes.items()
            if nombre.startswith("xl/worksheets/") and nombre.endswith(".xml"))
    if n > settings.max_office_cells:
        raise OfficeTooLargeError(f"Excel workbook with {n} cells; the maximum is {settings.max_office_cells}.")


class Plazo:
    """Limite de tiempo del tratamiento de un Word o un Excel (office_timeout_s)."""

    def __init__(self, segundos: float | None = None) -> None:
        self.segundos = settings.office_timeout_s if segundos is None else segundos
        self.fin = monotonic() + self.segundos

    def comprobar(self) -> None:
        if monotonic() > self.fin:
            raise ProcessingTimeoutError(f"The Office document took longer than {self.segundos} s.")


def rels_de(nombre: str) -> str:
    carpeta, base = posixpath.split(nombre)
    return posixpath.join(carpeta, "_rels", base + ".rels")


def origen_de_rels(nombre: str) -> str | None:
    carpeta, base = posixpath.split(nombre)
    if not carpeta.endswith("_rels") and carpeta != "_rels":
        return None
    padre = posixpath.dirname(carpeta) if carpeta != "_rels" else ""
    origen = posixpath.join(padre, base[:-len(".rels")])
    return origen or None


def resolver(origen: str, destino: str) -> str:
    if destino.startswith("/"):
        return destino.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(origen), destino)).lstrip("/")


# --- propiedades ---------------------------------------------------------------

EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"


def limpiar_propiedades(paq: "Paquete", cuenta) -> None:
    """Fuera lo que el documento dice de si mismo: autor, ultimo en modificar,
    titulo, asunto, palabras clave (se vacian), empresa, responsable, plantilla,
    propiedades personalizadas, datos XML personalizados y miniatura.  Comun a
    Word y Excel."""
    core = paq.xml("docProps/core.xml")
    if core is not None:
        for hijo in list(core):
            if (hijo.text or "").strip():
                cuenta["document_info"] += 1
            core.remove(hijo)
    app = paq.xml("docProps/app.xml")
    if app is not None:
        for tag in ("Company", "Manager", "HyperlinkBase", "Template"):
            for e in app.findall(f"{{{EP}}}{tag}"):
                if (e.text or "").strip():
                    cuenta["document_info"] += 1
                app.remove(e)
    custom = paq.xml("docProps/custom.xml")
    if custom is not None:
        cuenta["custom_xml"] += len(custom)
        paq.borrar("docProps/custom.xml")
    for nombre in paq.nombres("customXml/"):
        cuenta["custom_xml"] += nombre.endswith(".xml") and "/item" in nombre and "Props" not in nombre
        paq.borrar(nombre)
    for nombre in paq.nombres("docProps/thumbnail"):
        cuenta["thumbnails"] += 1
        paq.borrar(nombre)


# --- verificador ---------------------------------------------------------------

def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFC", texto).casefold()
    return re.sub(r"\s+", " ", texto)


def _textos_de_parte(nombre: str, datos: bytes) -> list[str]:
    """El texto de una parte de dos formas: sus nodos de texto pegados (un dato
    repartido en varios tramos de formato) y separados, mas sus atributos."""
    if nombre.endswith((".xml", ".rels", ".vml")) or datos[:5] == b"<?xml":
        try:
            raiz = leer_xml(datos)
        except Exception:
            return [datos.decode("utf-8", "ignore")]
        nodos = [t for t in raiz.itertext()]
        atributos = [v for e in raiz.iter() if isinstance(e.tag, str) for v in e.attrib.values()]
        return ["".join(nodos), " ".join(nodos), " ".join(atributos)]
    if nombre.startswith(("word/media/", "xl/media/", "docProps/thumbnail")):
        return []        # las imagenes se comprueban leyendolas, no como texto
    return [datos.decode("utf-8", "ignore"), datos.decode("utf-16-le", "ignore")]


def texto_de_todas_las_partes(ruta: Path) -> str:
    with zipfile.ZipFile(ruta) as z:
        return "\n".join(t for n in z.namelist() for t in _textos_de_parte(n, z.read(n)))


def verificar(ruta: Path, valores: list[str]) -> list[tuple[str, str]]:
    """(parte, valor) de cada valor que sigue en alguna parte del fichero."""
    encontrados = []
    with zipfile.ZipFile(ruta) as z:
        for nombre in z.namelist():
            textos = [_normal(t) for t in _textos_de_parte(nombre, z.read(nombre))]
            for v in valores:
                aguja = _normal(v.strip())
                if len(aguja) >= 4 and any(aguja in t for t in textos):
                    encontrados.append((nombre, v))
    return encontrados
