"""Corpus de documentos de Word (.docx) con datos inventados y sus etiquetas.

Imita documentos administrativos (cartas, notificaciones, solicitudes,
formularios con tablas, contratos, actas), en castellano, valenciano y
bilingues, y cubre: datos en prosa y en campos etiquetados; copia de DNI
incrustada; tablas, cabeceras y pies; comentarios, control de cambios y
propiedades cargadas; documentos sin ningun dato personal.

`documento_cargado` esconde un dato en cada sitio donde un Word guarda texto:
cabecera, pie, tabla, cuadro de texto, nota al pie, comentario, texto borrado
con control de cambios, texto insertado, texto oculto, propiedades del
documento y personalizadas, miniatura, texto alternativo de una imagen, enlace,
datos XML personalizados, imagen con un DNI y objeto incrustado.

Etiqueta de cada documento (etiquetas.json):
  datos      lo que no puede quedar en NINGUNA parte del fichero (texto y tipo)
  legitimos  texto que tiene que seguir en el documento tal cual
  imagen     datos que van dentro de la imagen incrustada (se leen con OCR)

Uso: python -m evaluation.corpus_word [DESTINO]   (por defecto evaluation/word/corpus)
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import fitz
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.shared import Inches, Pt
from lxml import etree

from evaluation.corpus_imagenes import dni, dni_puntos, iban

DESTINO = Path("evaluation/word/corpus")
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
_DECL = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
         'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"')

PERSONAS = [
    dict(nombre="Teresa Navarro Gil", dni=dni_puntos(29384751), dni_plano=dni(29384751),
         telefono="676 410 293", correo="teresa.navarro@correo.es",
         direccion="Calle de Ruzafa 18, 2º A, 46004 Valencia", nacimiento="22/04/1979",
         iban=iban("2038", "6123", "41", "3000142578")),
    dict(nombre="Vicent Ferrer Alandí", dni=dni_puntos(48105627), dni_plano=dni(48105627),
         telefono="963 528 104", correo="vicent.ferrer@correo.es",
         direccion="Carrer de Sueca 61, 46006 València", nacimiento="09/11/1985",
         iban=iban("3159", "0021", "57", "2004418836")),
    dict(nombre="Marina Soler Pastor", dni=dni_puntos(73021984), dni_plano=dni(73021984),
         telefono="611 203 945", correo="marina.soler@correo.es",
         direccion="Avenida del Cid 87, 46018 Valencia", nacimiento="30/06/1990",
         iban=iban("0182", "4572", "18", "0201566874")),
]


@dataclass
class Caso:
    nombre: str
    tipo: str
    idioma: str
    datos: list[dict] = field(default_factory=list)
    legitimos: list[str] = field(default_factory=list)
    imagen: list[dict] = field(default_factory=list)


# --- piezas -----------------------------------------------------------------

def _xml_p(doc: Document, xml: str) -> None:
    doc.element.body.append(parse_xml(f'<w:p {_DECL}>{xml}</w:p>'))


def tarjeta_dni(p: dict) -> bytes:
    """Copia de un DNI (anverso) con los datos de la persona, como PNG."""
    d = fitz.open()
    page = d.new_page(width=340, height=215)
    page.draw_rect(page.rect, color=(0.5, 0.5, 0.5), fill=(0.86, 0.9, 0.93))
    page.insert_text((14, 24), "ESPAÑA", fontsize=11, fontname="hebo", color=(0.6, 0.1, 0.1))
    page.insert_text((90, 24), "DOCUMENTO NACIONAL DE IDENTIDAD", fontsize=10, fontname="hebo")
    page.draw_rect(fitz.Rect(14, 40, 92, 140), color=None, fill=(0.62, 0.66, 0.7))
    apellidos = " ".join(p["nombre"].split()[1:]).upper()
    y = 46
    for rotulo, valor in [("APELLIDOS", apellidos), ("NOMBRE", p["nombre"].split()[0].upper()),
                          ("FECHA DE NACIMIENTO", p["nacimiento"].replace("/", " ")),
                          ("VALIDO HASTA", "01 01 2031")]:
        page.insert_text((104, y), rotulo, fontsize=6.5, color=(0.3, 0.3, 0.3))
        page.insert_text((104, y + 10), valor, fontsize=9, fontname="hebo")
        y += 24
    page.insert_text((14, 168), f"DNI {p['dni_plano']}", fontsize=11, fontname="hebo")
    page.insert_text((14, 192), f"IDESPBHV122738{p['dni_plano']}<<<<<<", fontsize=8, fontname="cour")
    return page.get_pixmap(dpi=200).tobytes("png")


def _parte(doc: Document, nombre: str, tipo_contenido: str, datos: bytes, relacion: str,
           externo: bool = False) -> str:
    parte = Part(PackURI(nombre), tipo_contenido, datos, doc.part.package)
    return doc.part.relate_to(parte, relacion)


def _nota_al_pie(doc: Document, texto: str) -> None:
    xml = (f'<w:footnotes {_DECL}>'
           '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
           '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
           f'<w:footnote w:id="1"><w:p><w:r><w:t xml:space="preserve">{texto}</w:t></w:r></w:p></w:footnote>'
           '</w:footnotes>')
    _parte(doc, "/word/footnotes.xml",
           "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
           xml.encode("utf-8"), RT.FOOTNOTES)


def _postproceso(ruta: Path, *, app_company: str | None = None, miniatura: bytes | None = None,
                 custom: dict | None = None, custom_xml: str | None = None,
                 plantilla: str | None = None) -> None:
    """Lo que python-docx no sabe escribir: se añade al paquete a mano."""
    with zipfile.ZipFile(ruta) as z:
        partes = {n: z.read(n) for n in z.namelist()}
    tipos = etree.fromstring(partes["[Content_Types].xml"])
    raiz_rels = etree.fromstring(partes["_rels/.rels"])
    doc_rels = etree.fromstring(partes["word/_rels/document.xml.rels"])
    CT = "http://schemas.openxmlformats.org/package/2006/content-types"
    PR = "http://schemas.openxmlformats.org/package/2006/relationships"

    def override(nombre, tipo):
        e = etree.SubElement(tipos, f"{{{CT}}}Override")
        e.set("PartName", nombre)
        e.set("ContentType", tipo)

    def rel(raiz, rid, tipo, destino, externo=False):
        e = etree.SubElement(raiz, f"{{{PR}}}Relationship")
        e.set("Id", rid)
        e.set("Type", tipo)
        e.set("Target", destino)
        if externo:
            e.set("TargetMode", "External")

    if app_company:
        app = partes.get("docProps/app.xml", b"")
        if b"<Company>" not in app:
            app = app.replace(b"</Properties>", f"<Company>{app_company}</Company>"
                              f"<Manager>{app_company}</Manager></Properties>".encode("utf-8"))
        partes["docProps/app.xml"] = app
    if miniatura:
        partes["docProps/thumbnail.jpeg"] = miniatura
        if not any(e.get("Extension") == "jpeg" for e in tipos):
            d = etree.SubElement(tipos, f"{{{CT}}}Default")
            d.set("Extension", "jpeg")
            d.set("ContentType", "image/jpeg")
        rel(raiz_rels, "rIdThumb",
            "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
            "docProps/thumbnail.jpeg")
    if custom:
        props = "".join(
            f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{i}" name="{k}">'
            f'<vt:lpwstr>{v}</vt:lpwstr></property>' for i, (k, v) in enumerate(custom.items(), 2))
        partes["docProps/custom.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            f'{props}</Properties>').encode("utf-8")
        override("/docProps/custom.xml", "application/vnd.openxmlformats-officedocument.custom-properties+xml")
        rel(raiz_rels, "rIdCustom",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties",
            "docProps/custom.xml")
    if custom_xml:
        partes["customXml/item1.xml"] = custom_xml.encode("utf-8")
        rel(doc_rels, "rIdCustomXml",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml",
            "../customXml/item1.xml")
    if plantilla:
        ajustes = partes["word/settings.xml"]
        ajustes = ajustes.replace(b"<w:zoom", b'<w:attachedTemplate r:id="rIdPlantilla"/><w:zoom', 1)
        if b'xmlns:r=' not in ajustes[:400]:
            ajustes = ajustes.replace(b"<w:settings ", b'<w:settings xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ', 1)
        partes["word/settings.xml"] = ajustes
        rels_ajustes = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{PR}"><Relationship Id="rIdPlantilla" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
            f'Target="{plantilla}" TargetMode="External"/></Relationships>')
        partes["word/_rels/settings.xml.rels"] = rels_ajustes.encode("utf-8")
    partes["[Content_Types].xml"] = etree.tostring(tipos, xml_declaration=True, encoding="UTF-8", standalone=True)
    partes["_rels/.rels"] = etree.tostring(raiz_rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    partes["word/_rels/document.xml.rels"] = etree.tostring(doc_rels, xml_declaration=True, encoding="UTF-8",
                                                           standalone=True)
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in partes.items():
            z.writestr(n, d)


def _miniatura(texto: str) -> bytes:
    d = fitz.open()
    page = d.new_page(width=120, height=170)
    page.insert_text((6, 20), texto, fontsize=6)
    return page.get_pixmap(dpi=72).tobytes("jpeg")


# --- el documento cargado ------------------------------------------------------

def documento_cargado(destino: Path, p: dict = PERSONAS[0]) -> Caso:
    """Un dato distinto en cada sitio donde un Word guarda texto."""
    doc = Document()
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = f"Expediente 2026/4418 · Interesada: {p['nombre']}"
    sec.footer.paragraphs[0].text = f"Contacto de la interesada: {p['telefono']}"
    doc.add_heading("Solicitud de ayuda al alquiler", level=1)
    par = doc.add_paragraph("Nombre y apellidos: ")
    par.add_run(p["nombre"]).bold = True
    doc.add_paragraph(f"DNI: {p['dni']}")
    doc.add_paragraph("La persona solicitante declara que los datos son ciertos y autoriza su "
                      "comprobación por parte de la Administración.")
    t = doc.add_table(rows=3, cols=2)
    for fila, (rot, val) in enumerate([("Domicilio", p["direccion"]), ("Fecha de nacimiento", p["nacimiento"]),
                                       ("Cuenta bancaria", p["iban"])]):
        t.cell(fila, 0).text = rot
        t.cell(fila, 1).text = val
    # Cuadro de texto (VML).
    _xml_p(doc, '<w:r><w:pict><v:shape style="width:200pt;height:30pt"><v:textbox><w:txbxContent>'
           f'<w:p><w:r><w:t xml:space="preserve">Correo de aviso: {p["correo"]}</w:t></w:r></w:p>'
           '</w:txbxContent></v:textbox></v:shape></w:pict></w:r>')
    # Nota al pie.
    _xml_p(doc, '<w:r><w:t xml:space="preserve">Consta la referencia catastral del inmueble</w:t></w:r>'
           '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteReference w:id="1"/></w:r>')
    _nota_al_pie(doc, f"Titular anterior del contrato: {PERSONAS[1]['nombre']}")
    # Control de cambios: un DNI borrado (sigue dentro) y un telefono insertado.
    _xml_p(doc, '<w:r><w:t xml:space="preserve">Teléfono de contacto: </w:t></w:r>'
           '<w:del w:id="91" w:author="Registro" w:date="2026-10-20T10:00:00Z"><w:r>'
           f'<w:delText xml:space="preserve">{PERSONAS[2]["dni"]} </w:delText></w:r></w:del>'
           '<w:ins w:id="92" w:author="Registro" w:date="2026-10-20T10:00:00Z"><w:r>'
           f'<w:t xml:space="preserve">{PERSONAS[2]["telefono"]}</w:t></w:r></w:ins>')
    # Texto oculto.
    _xml_p(doc, '<w:r><w:t xml:space="preserve">Observaciones. </w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr>'
           f'<w:t xml:space="preserve">DNI del cónyuge {PERSONAS[1]["dni"]}</w:t></w:r>')
    # Enlace mailto.
    rid = doc.part.relate_to(f"mailto:{PERSONAS[1]['correo']}", RT.HYPERLINK, is_external=True)
    _xml_p(doc, '<w:r><w:t xml:space="preserve">Para dudas, escriba a la </w:t></w:r>'
           f'<w:hyperlink r:id="{rid}"><w:r><w:t>oficina de vivienda</w:t></w:r></w:hyperlink>')
    # Comentario con autor.
    par = doc.add_paragraph("Documentación revisada en el registro.")
    doc.add_comment(par.runs, text=f"Llamar a {PERSONAS[2]['nombre']} para completar",
                    author=PERSONAS[2]["nombre"], initials="MS")
    # Imagen con DNI y texto alternativo.
    doc.add_paragraph("Se adjunta copia del documento de identidad:")
    doc.add_picture(io.BytesIO(tarjeta_dni(p)), width=Inches(3.2))
    docpr = doc.inline_shapes[0]._inline.docPr
    docpr.set("descr", f"Copia del DNI de {p['nombre']}")
    docpr.set("title", p["dni"])
    # Objeto incrustado (OLE) con datos dentro.
    rid_ole = _parte(doc, "/word/embeddings/oleObject1.bin",
                     "application/vnd.openxmlformats-officedocument.oleObject",
                     f"Hoja de cálculo: {p['nombre']};{p['dni']};{p['iban']}".encode("utf-8"),
                     "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject")
    _xml_p(doc, '<w:r><w:object><v:shape id="_x0000_i1025" style="width:60pt;height:40pt"/>'
           f'<o:OLEObject Type="Embed" ProgID="Package" ShapeID="_x0000_i1025" DrawAspect="Icon" '
           f'ObjectID="_1" r:id="{rid_ole}"/></w:object></w:r>')
    doc.add_paragraph("Lo que se comunica a los efectos oportunos.")
    cp = doc.core_properties
    cp.author = p["nombre"]
    cp.last_modified_by = PERSONAS[1]["nombre"]
    cp.title = f"Solicitud de {p['nombre']}"
    cp.subject = p["dni"]
    cp.keywords = p["correo"]
    cp.comments = f"Teléfono {p['telefono']}"
    doc.save(destino)
    _postproceso(destino, app_company=f"Gestoría de {PERSONAS[2]['nombre']}",
                 miniatura=_miniatura(p["nombre"]),
                 custom={"Interesado": p["nombre"], "NIF": p["dni"]},
                 custom_xml=f"<datos><titular>{p['nombre']}</titular><nif>{p['dni']}</nif></datos>",
                 plantilla="file:///C:/Users/tnavarro/Plantillas/solicitud.dotx")
    datos = [
        {"text": p["nombre"], "type": "PERSON"}, {"text": p["dni"], "type": "ES_DNI"},
        {"text": p["telefono"], "type": "PHONE_NUMBER"}, {"text": p["direccion"], "type": "ADDRESS"},
        {"text": p["nacimiento"], "type": "DATE_OF_BIRTH"}, {"text": p["iban"], "type": "IBAN_CODE"},
        {"text": p["correo"], "type": "EMAIL_ADDRESS"},
        {"text": PERSONAS[1]["nombre"], "type": "PERSON"}, {"text": PERSONAS[1]["dni"], "type": "ES_DNI"},
        {"text": PERSONAS[1]["correo"], "type": "EMAIL_ADDRESS"},
        {"text": PERSONAS[2]["nombre"], "type": "PERSON"}, {"text": PERSONAS[2]["dni"], "type": "ES_DNI"},
        {"text": PERSONAS[2]["telefono"], "type": "PHONE_NUMBER"},
        {"text": "tnavarro", "type": "USER_ID"},
    ]
    return Caso("cargado", "solicitud", "es", datos=datos,
                legitimos=["Solicitud de ayuda al alquiler", "Nombre y apellidos:", "DNI:",
                           "La persona solicitante declara que los datos son ciertos",
                           "Domicilio", "Fecha de nacimiento", "Cuenta bancaria",
                           "Documentación revisada en el registro.",
                           "Lo que se comunica a los efectos oportunos."],
                imagen=[{"text": p["dni_plano"], "type": "ES_DNI"}])


# --- el corpus ------------------------------------------------------------------

def _doc_con(cabecera: str | None = None, pie: str | None = None) -> Document:
    doc = Document()
    estilo = doc.styles["Normal"]
    estilo.font.size = Pt(11)
    if cabecera:
        doc.sections[0].header.paragraphs[0].text = cabecera
    if pie:
        doc.sections[0].footer.paragraphs[0].text = pie
    return doc


def carta(destino: Path, p: dict) -> Caso:
    doc = _doc_con(cabecera="Ajuntament de València · Servicio de Vivienda")
    doc.add_paragraph("Valencia, 20 de octubre de 2026")
    doc.add_paragraph(f"Sra. {p['nombre']}\n{p['direccion']}")
    doc.add_paragraph("Asunto: Requerimiento de documentación")
    doc.add_paragraph(f"En relación con su solicitud, y para continuar con la tramitación, le rogamos que "
                      f"aporte en el plazo de diez días el certificado de empadronamiento. Puede contactar "
                      f"con nosotros en el teléfono de la oficina o indicarnos un número propio; nos ha "
                      f"facilitado el {p['telefono']} y el correo {p['correo']}.")
    doc.add_paragraph("Atentamente,\nLa jefa del Servicio")
    doc.save(destino)
    return Caso("carta", "carta", "es",
                datos=[{"text": p["nombre"], "type": "PERSON"}, {"text": p["direccion"], "type": "ADDRESS"},
                       {"text": p["telefono"], "type": "PHONE_NUMBER"}, {"text": p["correo"], "type": "EMAIL_ADDRESS"}],
                legitimos=["Asunto: Requerimiento de documentación", "certificado de empadronamiento",
                           "Atentamente,", "Servicio de Vivienda"])


def notificacio(destino: Path, p: dict) -> Caso:
    doc = _doc_con(pie=f"Persona interessada: {p['nombre']} · DNI {p['dni']}")
    doc.add_heading("Notificació de resolució", level=1)
    doc.add_paragraph(f"Per la present us notifiquem que la sol·licitud presentada per {p['nombre']}, "
                      f"amb DNI {p['dni']} i domicili a {p['direccion']}, ha estat resolta favorablement.")
    doc.add_paragraph("Contra aquesta resolució podeu interposar recurs de reposició en el termini d'un mes.")
    doc.save(destino)
    return Caso("notificacio", "notificacion", "va",
                datos=[{"text": p["nombre"], "type": "PERSON"}, {"text": p["dni"], "type": "ES_DNI"},
                       {"text": p["direccion"], "type": "ADDRESS"}],
                legitimos=["Notificació de resolució", "ha estat resolta favorablement",
                           "recurs de reposició"])


def formulario_bilingue(destino: Path, p: dict) -> Caso:
    doc = _doc_con(cabecera="SOL·LICITUD GENERAL / SOLICITUD GENERAL")
    doc.add_paragraph("DADES DE LA PERSONA SOL·LICITANT / DATOS DE LA PERSONA SOLICITANTE")
    filas = [("Nom i cognoms / Nombre y apellidos", p["nombre"], "PERSON"),
             ("DNI / NIF", p["dni"], "ES_DNI"),
             ("Data de naixement / Fecha de nacimiento", p["nacimiento"], "DATE_OF_BIRTH"),
             ("Domicili / Domicilio", p["direccion"], "ADDRESS"),
             ("Telèfon / Teléfono", p["telefono"], "PHONE_NUMBER"),
             ("Correu electrònic / Correo electrónico", p["correo"], "EMAIL_ADDRESS"),
             ("Compte bancari / Cuenta bancaria", p["iban"], "IBAN_CODE")]
    t = doc.add_table(rows=len(filas), cols=2)
    t.style = "Table Grid"
    for i, (rot, val, _) in enumerate(filas):
        t.cell(i, 0).text = rot
        t.cell(i, 1).text = val
    doc.add_paragraph("EXPOSA / EXPONE: Que desitja inscriure's en el curs de valencià per a adults.")
    doc.save(destino)
    return Caso("formulario_bilingue", "formulario", "bilingue",
                datos=[{"text": v, "type": tp} for _, v, tp in filas],
                legitimos=[r for r, _, _ in filas] + ["EXPOSA / EXPONE", "curs de valencià per a adults"])


def contrato(destino: Path, a: dict, b: dict) -> Caso:
    doc = _doc_con(pie="Contrato de arrendamiento de vivienda · página 1")
    doc.add_heading("CONTRATO DE ARRENDAMIENTO DE VIVIENDA", level=1)
    doc.add_paragraph("REUNIDOS")
    doc.add_paragraph(f"De una parte, {a['nombre']}, mayor de edad, con DNI {a['dni']} y domicilio en "
                      f"{a['direccion']}, en adelante la parte arrendadora.")
    doc.add_paragraph(f"De otra parte, {b['nombre']}, mayor de edad, con DNI {b['dni']}, en adelante la "
                      f"parte arrendataria, que abonará la renta mediante domiciliación en la cuenta {b['iban']}.")
    doc.add_paragraph("Ambas partes se reconocen capacidad legal suficiente para contratar y")
    doc.add_paragraph("EXPONEN")
    doc.add_paragraph("I. Que la parte arrendadora es propietaria de la vivienda descrita en el anexo.")
    doc.save(destino)
    return Caso("contrato", "contrato", "es",
                datos=[{"text": a["nombre"], "type": "PERSON"}, {"text": a["dni"], "type": "ES_DNI"},
                       {"text": a["direccion"], "type": "ADDRESS"}, {"text": b["nombre"], "type": "PERSON"},
                       {"text": b["dni"], "type": "ES_DNI"}, {"text": b["iban"], "type": "IBAN_CODE"}],
                legitimos=["CONTRATO DE ARRENDAMIENTO DE VIVIENDA", "REUNIDOS", "EXPONEN",
                           "capacidad legal suficiente para contratar"])


def acta(destino: Path) -> Caso:
    doc = _doc_con(cabecera="Acta de la Comisión de Valoración · sesión 7/2026")
    doc.add_heading("ACTA DE LA SESIÓN", level=1)
    doc.add_paragraph("Asistentes:")
    t = doc.add_table(rows=3, cols=2)
    t.style = "Table Grid"
    for i, p in enumerate(PERSONAS):
        t.cell(i, 0).text = p["nombre"]
        t.cell(i, 1).text = ["Presidenta", "Secretario", "Vocal"][i]
    doc.add_paragraph(f"Abierta la sesión, {PERSONAS[0]['nombre']} informa de las solicitudes recibidas. "
                      f"{PERSONAS[1]['nombre']} levanta acta. Se acuerda por unanimidad aprobar la propuesta.")
    doc.add_paragraph("Sin más asuntos que tratar, se levanta la sesión.")
    doc.save(destino)
    return Caso("acta", "acta", "es",
                datos=[{"text": p["nombre"], "type": "PERSON"} for p in PERSONAS],
                legitimos=["ACTA DE LA SESIÓN", "Asistentes:", "Presidenta", "Secretario", "Vocal",
                           "Se acuerda por unanimidad aprobar la propuesta",
                           "Sin más asuntos que tratar, se levanta la sesión."])


def con_dni_incrustado(destino: Path, p: dict) -> Caso:
    doc = _doc_con()
    doc.add_paragraph("Declaración responsable")
    doc.add_paragraph("La persona interesada aporta copia de su documento de identidad:")
    doc.add_picture(io.BytesIO(tarjeta_dni(p)), width=Inches(3.2))
    doc.add_paragraph("Y para que conste, firma la presente declaración.")
    doc.save(destino)
    return Caso("con_dni_incrustado", "declaracion", "es",
                legitimos=["Declaración responsable", "Y para que conste, firma la presente declaración."],
                imagen=[{"text": p["dni_plano"], "type": "ES_DNI"}])


def comentado(destino: Path, p: dict) -> Caso:
    """Comentarios, control de cambios y propiedades cargadas, con el texto visible limpio."""
    doc = _doc_con()
    par = doc.add_paragraph("Informe técnico sobre el estado de la vivienda.")
    doc.add_comment(par.runs, text=f"Revisar con {p['nombre']} ({p['telefono']})", author=p["nombre"])
    _xml_p(doc, '<w:r><w:t xml:space="preserve">La inspección se realizó el día acordado</w:t></w:r>'
           '<w:del w:id="31" w:author="Inspección" w:date="2026-10-21T09:00:00Z"><w:r>'
           f'<w:delText xml:space="preserve"> en presencia de {p["nombre"]}, DNI {p["dni"]}</w:delText>'
           '</w:r></w:del><w:r><w:t>.</w:t></w:r>')
    doc.add_paragraph("No se observan deficiencias estructurales.")
    cp = doc.core_properties
    cp.author, cp.last_modified_by, cp.subject = p["nombre"], p["nombre"], p["dni"]
    doc.save(destino)
    _postproceso(destino, app_company=f"Estudio {p['nombre']}", custom={"Propietario": p["nombre"]})
    return Caso("comentado", "informe", "es",
                datos=[{"text": p["nombre"], "type": "PERSON"}, {"text": p["dni"], "type": "ES_DNI"},
                       {"text": p["telefono"], "type": "PHONE_NUMBER"}],
                legitimos=["Informe técnico sobre el estado de la vivienda.",
                           "La inspección se realizó el día acordado",
                           "No se observan deficiencias estructurales."])


def sin_datos(destino: Path, n: int) -> Caso:
    textos = [
        ["Instrucciones para la presentación de solicitudes",
         "Las solicitudes se presentarán en el registro electrónico de la sede, de lunes a viernes.",
         "El plazo de presentación es de veinte días hábiles desde la publicación de la convocatoria.",
         "Contra la presente resolución podrá interponerse recurso de alzada."],
        ["Bases reguladores del concurs de fotografia",
         "Podran participar totes les persones majors d'edat residents a la Comunitat Valenciana.",
         "Cada participant podrà presentar un màxim de tres fotografies inèdites.",
         "El jurat estarà format per persones expertes en fotografia i comunicació."],
    ][n]
    doc = _doc_con(cabecera="Servicio de Cultura", pie="Documento informativo")
    doc.add_heading(textos[0], level=1)
    for t in textos[1:]:
        doc.add_paragraph(t)
    tabla = doc.add_table(rows=2, cols=2)
    tabla.style = "Table Grid"
    for i, (a, b) in enumerate([("Plazo", "20 días hábiles"), ("Lugar", "Registro electrónico")]):
        tabla.cell(i, 0).text, tabla.cell(i, 1).text = a, b
    doc.save(destino)
    return Caso(f"sin_datos_{n + 1}", "informativo", ["es", "va"][n],
                legitimos=textos + ["Servicio de Cultura", "Documento informativo", "Plazo", "20 días hábiles",
                                    "Registro electrónico"])


def corpus(destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    a, b, c = PERSONAS
    casos = [
        ("cargado", lambda r: documento_cargado(r, a)),
        ("carta", lambda r: carta(r, a)),
        ("carta_2", lambda r: carta(r, c)),
        ("notificacio", lambda r: notificacio(r, b)),
        ("formulario_bilingue", lambda r: formulario_bilingue(r, b)),
        ("formulario_bilingue_2", lambda r: formulario_bilingue(r, c)),
        ("contrato", lambda r: contrato(r, a, b)),
        ("acta", acta),
        ("con_dni_incrustado", lambda r: con_dni_incrustado(r, c)),
        ("comentado", lambda r: comentado(r, b)),
        ("sin_datos_1", lambda r: sin_datos(r, 0)),
        ("sin_datos_2", lambda r: sin_datos(r, 1)),
    ]
    etiquetas = []
    for nombre, hacer in casos:
        ruta = destino / f"{nombre}.docx"
        caso = hacer(ruta)
        etiquetas.append({"documento": ruta.name, "tipo": caso.tipo, "idioma": caso.idioma,
                          "datos": caso.datos, "legitimos": caso.legitimos, "imagen": caso.imagen})
    (destino / "etiquetas.json").write_text(json.dumps(etiquetas, ensure_ascii=False, indent=1), encoding="utf-8")
    return etiquetas


if __name__ == "__main__":
    salida = corpus(Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO)
    print(f"{len(salida)} documentos")
