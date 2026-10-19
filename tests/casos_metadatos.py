"""Casos inventados cargados de metadatos, para los tests de limpieza de la salida.

Cada metadato lleva un centinela ZZ... unico para poder buscarlo en el fichero
entregado.  Los mismos casos de la auditoria de metadatos:

  digital_cargado    texto + foto de movil pegada (EXIF, GPS, XMP, ICC) + carga del PDF
  escaneado_cargado  una pagina que es solo una foto de movil + carga del PDF
  hibrido_cargado    pagina de texto + pagina que es foto de movil + carga del PDF
  incremental        guardado entero y despues actualizado de forma incremental: la
                     version anterior, sin tachar, sigue dentro del fichero

"Carga del PDF": diccionario de informacion, XMP del documento, de la pagina y de
la imagen, nota de comentario con autor, texto libre visible, adjunto (arbol de
nombres e icono), campo de formulario relleno, marcador, JavaScript de apertura y
miniatura.
"""
from __future__ import annotations

import io
from pathlib import Path

import fitz
from PIL import Image, ImageCms, ImageDraw

# Lo que no se ve en la pagina: no puede quedar en el fichero entregado.
OCULTOS = [
    "ZZAUTOR", "ZZCREADOR", "ZZPRODUCTOR", "ZZTITULO", "ZZASUNTO", "ZZCLAVES",
    "ZZXMPDOC", "ZZXMPPAGINA", "ZZXMPIMAGENPDF", "ZZANOTAUTOR", "ZZANOTTEXTO",
    "ZZADJUNTO", "ZZJAVASCRIPT", "ZZMARCADOR", "ZZFABRICANTE", "ZZMODELO",
    "ZZXMPFOTO", "ZZVERSIONVIEJA",
]
# Lo que si se ve (texto libre, campo relleno): pasa a la pagina y se analiza.
VISIBLE_LIBRE = "ZZLIBRETEXTO llamar al 655 123 456"
VISIBLE_CAMPO = "ZZCAMPO 48291736K"

_XMP = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
<rdf:Description rdf:about='' xmlns:dc='http://purl.org/dc/elements/1.1/' xmlns:xmp='http://ns.adobe.com/xap/1.0/'>
<dc:creator><rdf:Seq><rdf:li>{c} Marta Ferrer Soler</rdf:li></rdf:Seq></dc:creator>
<xmp:CreatorTool>{c} Word en PC-ALCALDIA-07</xmp:CreatorTool>
</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end='w'?>"""


def foto_movil(ancho: int = 600, alto: int = 800) -> bytes:
    """JPEG como el de un movil: EXIF (marca, modelo, fecha), GPS, XMP e ICC."""
    img = Image.new("RGB", (ancho, alto), (205, 200, 190))
    d = ImageDraw.Draw(img)
    for i in range(0, alto, 40):
        d.line([(0, i), (ancho, i + 30)], fill=(180 + i % 50, 170, 160), width=6)
    exif = Image.Exif()
    exif[0x010F] = "ZZFABRICANTE Samsung"
    exif[0x0110] = "ZZMODELO SM-A546B"
    exif[0x0132] = "2026:03:14 09:26:53"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (39.0, 28.0, 12.5)
    gps[3], gps[4] = "W", (0.0, 22.0, 34.1)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, exif=exif, icc_profile=icc,
             xmp=_XMP.format(c="ZZXMPFOTO").encode())
    return buf.getvalue()


def _flujo_metadata(doc: fitz.Document, owner_xref: int, centinela: str) -> None:
    x = doc.get_new_xref()
    doc.update_object(x, "<</Type/Metadata/Subtype/XML>>")
    doc.update_stream(x, _XMP.format(c=centinela).encode(), compress=False)
    doc.xref_set_key(owner_xref, "Metadata", f"{x} 0 R")


def cargar(doc: fitz.Document) -> None:
    """Toda la carga del PDF, sobre la primera pagina."""
    doc.set_metadata({
        "author": "ZZAUTOR Marta Ferrer Soler", "creator": "ZZCREADOR Microsoft Word",
        "producer": "ZZPRODUCTOR Acrobat PDFMaker", "title": "ZZTITULO Expediente",
        "subject": "ZZASUNTO Sancion", "keywords": "ZZCLAVES 48291736K",
        "creationDate": "D:20260314092653+01'00'", "modDate": "D:20260315101010+01'00'",
    })
    doc.set_xml_metadata(_XMP.format(c="ZZXMPDOC"))
    p = doc[0]
    _flujo_metadata(doc, p.xref, "ZZXMPPAGINA")
    for img in p.get_images():
        _flujo_metadata(doc, img[0], "ZZXMPIMAGENPDF")
    nota = p.add_text_annot((500, 60), "ZZANOTTEXTO revisar el DNI 48291736K")
    nota.set_info(title="ZZANOTAUTOR Jose Peris", content="ZZANOTTEXTO revisar el DNI 48291736K")
    nota.update()
    libre = p.add_freetext_annot(fitz.Rect(330, 690, 580, 715), VISIBLE_LIBRE, fontsize=9)
    libre.set_info(title="ZZANOTAUTOR Jose Peris")
    libre.update()
    doc.embfile_add("notas.txt", b"ZZADJUNTO Marta Ferrer Soler 48291736K", filename="notas.txt")
    p.add_file_annot((540, 100), b"ZZADJUNTO segunda copia", "copia.txt")
    campo = fitz.Widget()
    campo.field_name, campo.field_type = "interesado", fitz.PDF_WIDGET_TYPE_TEXT
    campo.rect, campo.field_value = fitz.Rect(330, 740, 580, 760), VISIBLE_CAMPO
    campo.text_fontsize = 9
    p.add_widget(campo)
    doc.set_toc([[1, "ZZMARCADOR Expediente de Marta Ferrer Soler", 1]])
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction",
                     "<</S/JavaScript/JS(app.alert\\('ZZJAVASCRIPT'\\))>>")
    t = doc.get_new_xref()
    doc.update_object(t, "<</Type/XObject/Subtype/Image/Width 2/Height 2"
                         "/ColorSpace/DeviceGray/BitsPerComponent 8>>")
    doc.update_stream(t, b"\x00\xff\xff\x00")
    doc.xref_set_key(p.xref, "Thumb", f"{t} 0 R")


def _texto(p: fitz.Page) -> None:
    for i, linea in enumerate([
        "AJUNTAMENT DE VALENCIA - Servicio de Sanciones",
        "Interesada: Marta Ferrer Soler   DNI: 48291736K",
        "Domicilio: Calle de Cuba 14, 3o B, 46006 Valencia",
        "Se adjunta fotografia aportada por la interesada.",
    ]):
        p.insert_text((60, 90 + 18 * i), linea, fontsize=10)


def digital_cargado(destino: Path) -> Path:
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    _texto(p)
    p.insert_image(fitz.Rect(200, 300, 395, 560), stream=foto_movil())
    cargar(doc)
    doc.save(destino)
    return destino


def escaneado_cargado(destino: Path) -> Path:
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_image(p.rect, stream=foto_movil(620, 877))
    cargar(doc)
    doc.save(destino)
    return destino


def hibrido_cargado(destino: Path) -> Path:
    doc = fitz.open()
    _texto(doc.new_page(width=595, height=842))
    q = doc.new_page(width=595, height=842)
    q.insert_image(q.rect, stream=foto_movil(620, 877))
    cargar(doc)
    doc.save(destino)
    return destino


def incremental(destino: Path) -> Path:
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    _texto(p)
    p.insert_text((60, 200), "ZZVERSIONVIEJA telefono 655 123 456", fontsize=10)
    doc.set_metadata({"author": "ZZAUTOR Marta Ferrer Soler"})
    doc.save(destino)
    doc.close()
    doc = fitz.open(destino)
    p = doc[0]
    p.add_redact_annot(p.search_for("ZZVERSIONVIEJA telefono 655 123 456")[0])
    p.apply_redactions()
    doc.set_metadata({"author": "Registro General"})
    doc.saveIncr()
    doc.close()
    return destino


CASOS = {
    "digital_cargado": digital_cargado,
    "escaneado_cargado": escaneado_cargado,
    "hibrido_cargado": hibrido_cargado,
    "incremental": incremental,
}


def todo_el_fichero(ruta: Path) -> bytes:
    """Bytes crudos + cada objeto y cada flujo descomprimido, en minusculas.

    Se buscan tambien en hexadecimal: PyMuPDF escribe el texto de pagina como <hex>.
    """
    crudo = Path(ruta).read_bytes()
    partes = [crudo]
    doc = fitz.open(ruta)
    for x in range(1, doc.xref_length()):
        try:
            partes.append(doc.xref_object(x).encode("latin-1", "replace"))
            if doc.xref_is_stream(x):
                partes.append(doc.xref_stream(x) or b"")
        except Exception:
            continue
    doc.close()
    return b"\n".join(partes).lower()


def presentes(ruta: Path, centinelas: list[str]) -> list[str]:
    todo = todo_el_fichero(ruta)
    return sorted(c for c in centinelas
                  if c.lower().encode() in todo or c.encode().hex().encode() in todo)
