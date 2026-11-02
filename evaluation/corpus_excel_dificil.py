"""Corpus de casos dificiles de Excel (.xlsx), con datos inventados.

Cada libro esconde datos personales en un sitio donde un Excel guarda texto y
que es facil pasar por alto: cadenas compartidas que ya no usa ninguna celda,
valores guardados de formulas que traen datos de otra hoja, cabeceras
desplazadas o en la primera columna, DNI y telefonos como numero, listas
desplegables, formato condicional, cabeceras de impresion, comentarios,
conexiones, graficos con su copia de datos, texto con varios formatos en una
celda...  Y ficheros dañados normales (cortado, vacio, extension que no
corresponde).

Etiqueta de cada caso (casos.json):
  id, fichero, nombre_subida   el fichero y el nombre con que se sube
  que_prueba, esperado         el truco y lo que deberia pasar
  rechazo                      None si se tiene que procesar; el codigo de error
                               esperado si se tiene que rechazar
  datos                        valores que no pueden quedar en NINGUNA parte del
                               fichero de salida, con su tipo y donde van
  legitimos                    texto que tiene que seguir en la salida

Uso: python -m evaluation.corpus_excel_dificil [DESTINO]
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from lxml import etree
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XlImage
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import PatternFill
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table
from PIL import Image

from evaluation.corpus_imagenes import dni, formulario, iban

DESTINO = Path("evaluation/excel_dificil/corpus")

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"

P = dict(nombre="Remedios Albiach Furió", dni=dni(48219736), num="48219736", tel="663418902",
         correo="remedios.albiach@correo.es", nacimiento=dt.date(1979, 4, 21),
         telefono="663 418 902", direccion="Calle de Cuba 27, 46006 Valencia",
         iban=iban("2100", "0418", "45", "0200051332"))
Q = dict(nombre="Joan Peris Llopis", dni=dni(20581374), num="20581374", tel="611902447",
         correo="joan.peris@correo.es", nacimiento=dt.date(1985, 11, 2))
R = dict(nombre="Amparo Gisbert Soria", dni=dni(73104862), num="73104862", tel="644027318",
         correo="amparo.gisbert@correo.es", nacimiento=dt.date(1962, 6, 30))
PERSONAS = [P, Q, R]
# Personas que solo aparecen en el sitio escondido de su caso.
TECNICA = "Vicenta Ribes Mollà"
TECNICO = "Ferran Gil Aparici"
OTRA = "Neus Climent Bosch"
LEG = ["Ayudas al alquiler 2024", "Servicio de Bienestar Social"]


def _d(texto, tipo: str, donde: str) -> dict:
    return {"text": str(texto), "type": tipo, "donde": donde}


def _serie(fecha: dt.date) -> int:
    return (fecha - dt.date(1899, 12, 30)).days


# --- utilidades sobre el zip ------------------------------------------------------

def _partes(ruta: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(ruta) as z:
        return {n: z.read(n) for n in z.namelist()}


def _escribir(ruta: Path, partes: dict[str, bytes]) -> None:
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in partes.items():
            z.writestr(n, d)


def _xml(datos: bytes):
    return etree.fromstring(datos)


def _bytes(raiz) -> bytes:
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8", standalone=True)


def _anadir(partes: dict, nombre: str, datos: bytes, tipo: str, origen: str, rel_tipo: str,
            externo: str | None = None) -> str:
    """Añade la parte *nombre* (o una relacion externa) con su tipo y su
    relacion desde *origen*.  Devuelve el id de la relacion."""
    if not externo:
        partes[nombre] = datos
        tipos = _xml(partes["[Content_Types].xml"])
        o = etree.SubElement(tipos, f"{{{CT}}}Override")
        o.set("PartName", "/" + nombre)
        o.set("ContentType", tipo)
        partes["[Content_Types].xml"] = _bytes(tipos)
    carpeta, base = origen.rsplit("/", 1)
    nombre_rels = f"{carpeta}/_rels/{base}.rels"
    rels = _xml(partes[nombre_rels]) if nombre_rels in partes else etree.Element(
        f"{{{PR}}}Relationships", nsmap={None: PR})
    rid = f"rIdX{len(rels) + 1}"
    r = etree.SubElement(rels, f"{{{PR}}}Relationship")
    r.set("Id", rid)
    r.set("Type", rel_tipo)
    if externo:
        r.set("Target", externo)
        r.set("TargetMode", "External")
    else:
        r.set("Target", "/" + nombre)
    partes[nombre_rels] = _bytes(rels)
    return rid


def _cachear(partes: dict, hoja: str, celda: str, valor: str, texto: bool = True) -> None:
    """La celda con formula *celda* lleva *valor* como valor guardado."""
    raiz = _xml(partes[hoja])
    for c in raiz.iter(f"{{{S}}}c"):
        if c.get("r") == celda:
            v = c.find(f"{{{S}}}v")
            if v is None:
                v = etree.SubElement(c, f"{{{S}}}v")
            v.text = valor
            if texto:
                c.set("t", "str")
    partes[hoja] = _bytes(raiz)


def _listado(ws, cabecera=("Nombre", "DNI", "Teléfono", "Correo electrónico"), fila0: int = 1) -> None:
    for i, texto in enumerate(cabecera, 1):
        ws.cell(row=fila0, column=i, value=texto)
    for n, p in enumerate(PERSONAS, fila0 + 1):
        for i, v in enumerate([p["nombre"], p["dni"], p["telefono"] if "telefono" in p else p["tel"],
                               p["correo"]][:len(cabecera)], 1):
            ws.cell(row=n, column=i, value=v)


def _datos_listado(donde: str = "listado") -> list[dict]:
    salida = []
    for p in PERSONAS:
        salida += [_d(p["nombre"], "PERSON", donde), _d(p["dni"], "ES_DNI", donde),
                   _d(p["correo"], "EMAIL_ADDRESS", donde)]
    return salida


def _foto_con_miniatura(destino: Path) -> bytes:
    """Foto de un formulario con autor y una miniatura EXIF de la foto (ExifTool)."""
    doc = formulario({**P, "nacimiento": P["nacimiento"].strftime("%d/%m/%Y")}, 1)
    img = Image.fromarray(doc.paginas[0])
    img = img.resize((900, round(img.height * 900 / img.width)))
    foto, mini = destino / "_foto.jpg", destino / "_mini.jpg"
    img.save(foto, format="JPEG", quality=88)
    img.resize((240, round(img.height * 240 / img.width))).save(mini, format="JPEG", quality=85)
    subprocess.run(["exiftool", "-q", "-q", "-overwrite_original", f"-ThumbnailImage<={mini}",
                    f"-Artist={TECNICA}", "-GPSLatitude=39.4699", "-GPSLatitudeRef=N",
                    "-GPSLongitude=0.3763", "-GPSLongitudeRef=W", str(foto)], check=True)
    datos = foto.read_bytes()
    foto.unlink()
    mini.unlink()
    return datos


# --- los casos --------------------------------------------------------------------

def casos(destino: Path) -> list[dict]:
    salida = []

    def nuevo(id_, nombre, que, esperado, datos, legitimos=None, rechazo=None, subida=None, **extra):
        salida.append({"id": id_, "fichero": nombre, "nombre_subida": subida or nombre, "que_prueba": que,
                       "esperado": esperado, "rechazo": rechazo, "datos": datos,
                       "legitimos": legitimos if legitimos is not None else [], **extra})

    def libro():
        wb = Workbook()
        ws = wb.active
        ws.title = "Solicitantes"
        return wb, ws

    # X01 Cadenas compartidas que ya no usa ninguna celda.
    wb, ws = libro()
    _listado(ws)
    ruta = destino / "x01_cadenas_huerfanas.xlsx"
    wb.save(ruta)
    partes = _partes(ruta)
    # openpyxl guarda los textos en la celda: la tabla se crea con solo los restos.
    sst = etree.Element(f"{{{S}}}sst", nsmap={None: S})
    for texto in (OTRA, f"{OTRA} - DNI 39184027G", "Llamar a Ferran Gil Aparici"):
        si = etree.SubElement(sst, f"{{{S}}}si")
        etree.SubElement(si, f"{{{S}}}t").text = texto
    sst.set("count", "3")
    sst.set("uniqueCount", "3")
    _anadir(partes, "xl/sharedStrings.xml", _bytes(sst), "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sharedStrings+xml", "xl/workbook.xml",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings")
    _escribir(ruta, partes)
    nuevo("X01", ruta.name, "Textos en la tabla de cadenas compartidas que ninguna celda usa (restos de "
          "celdas borradas).", "La tabla se rehace solo con lo que se usa: los restos desaparecen.",
          _datos_listado() + [_d(OTRA, "PERSON", "cadena huerfana"), _d("39184027G", "ES_DNI", "cadena huerfana"),
                              _d(TECNICO, "PERSON", "cadena huerfana")], ["Nombre", "Correo electrónico"])

    # X02 Formulas que traen datos de otra hoja, con su valor guardado.
    wb, ws = libro()
    ws.title = "Datos"
    _listado(ws)
    inf = wb.create_sheet("Informe")
    inf.append(["Concepto", "Valor"])
    inf.append(["Primer DNI", "=INDEX(Datos!B2:B4,1)"])
    inf.append(["Ficha", '=CONCATENATE(Datos!A3," - ",Datos!B3)'])
    inf.append(["Correo", "=Datos!D4"])
    ruta = destino / "x02_formulas_de_otra_hoja.xlsx"
    wb.save(ruta)
    partes = _partes(ruta)
    _cachear(partes, "xl/worksheets/sheet2.xml", "B2", P["dni"])
    _cachear(partes, "xl/worksheets/sheet2.xml", "B3", f"{Q['nombre']} - {Q['dni']}")
    _cachear(partes, "xl/worksheets/sheet2.xml", "B4", R["correo"])
    _escribir(ruta, partes)
    nuevo("X02", ruta.name, "INDEX, CONCATENATE y referencia directa a la hoja de datos, con el valor "
          "calculado guardado en la celda.", "Las formulas pasan a ser el marcador y el valor guardado "
          "desaparece.", _datos_listado(), ["Primer DNI", "Ficha", "Concepto"])

    # X03 Formulas con el dato escrito dentro.
    wb, ws = libro()
    _listado(ws)
    res = wb.create_sheet("Resumen")
    res.append(["Concepto", "Valor"])
    res.append(["Busqueda", f'=VLOOKUP("{R["nombre"]}",Solicitantes!A2:B4,2,FALSE)'])
    res.append(["Aviso", f'="Llamar a {TECNICO} al "&"{P["tel"]}"'])
    ruta = destino / "x03_formulas_con_el_dato_escrito.xlsx"
    wb.save(ruta)
    nuevo("X03", ruta.name, "El dato va escrito dentro de la formula (BUSCARV con un nombre literal, "
          "un texto con un telefono).", "La formula no puede seguir llevando el nombre ni el telefono.",
          _datos_listado() + [_d(TECNICO, "PERSON", "literal en formula"),
                              _d(P["tel"], "PHONE_NUMBER", "literal en formula")], ["Busqueda", "Aviso"])

    # X04 Formula sobre columnas enteras, con valor guardado.
    wb, ws = libro()
    ws.title = "Datos"
    _listado(ws)
    inf = wb.create_sheet("Consulta")
    inf.append(["Nombre buscado", "DNI encontrado"])
    inf.append(["=Datos!A2", "=VLOOKUP(A2,Datos!A:B,2,FALSE)"])
    ruta = destino / "x04_formula_columnas_enteras.xlsx"
    wb.save(ruta)
    partes = _partes(ruta)
    _cachear(partes, "xl/worksheets/sheet2.xml", "A2", P["nombre"])
    _cachear(partes, "xl/worksheets/sheet2.xml", "B2", P["dni"])
    _escribir(ruta, partes)
    nuevo("X04", ruta.name, "BUSCARV sobre columnas enteras (Datos!A:B), sin numero de fila, con el "
          "valor guardado.", "La formula que trae el DNI pasa a ser el marcador; nada guardado queda.",
          _datos_listado(), ["Nombre buscado", "DNI encontrado"])

    # X05 Cabecera en la fila 5, con un subtitulo de dos celdas encima.
    wb, ws = libro()
    ws["A1"] = "AJUNTAMENT DE VALÈNCIA"
    ws["A2"], ws["C2"] = "Servicio de Bienestar Social", "Ejercicio 2024"
    ws["A4"] = "Listado provisional de admitidos"
    _listado(ws, ("Apellidos y nombre", "D.N.I.", "Tfno.", "E-mail"), fila0=5)
    ruta = destino / "x05_cabecera_en_fila_5.xlsx"
    wb.save(ruta)
    nuevo("X05", ruta.name, "Titulo, un subtitulo de dos celdas (parece una cabecera) y la cabecera de "
          "verdad en la fila 5, con rotulos abreviados (D.N.I., Tfno., E-mail).",
          "Cada columna de datos lleva su marcador; titulos y cabeceras se quedan.",
          _datos_listado(), ["AJUNTAMENT DE VALÈNCIA", "Servicio de Bienestar Social", "D.N.I.", "Tfno."])

    # X06 Celdas combinadas.
    wb, ws = libro()
    ws["A1"] = "Datos del solicitante"
    ws.merge_cells("A1:C1")
    ws["D1"] = "Contacto"
    _listado(ws, fila0=2)
    ws.merge_cells("A3:A4")
    ruta = destino / "x06_celdas_combinadas.xlsx"
    wb.save(ruta)
    nuevo("X06", ruta.name, "Cabecera de grupo combinada encima de la cabecera real, y un nombre en "
          "una celda combinada de dos filas.", "Los datos se sustituyen; las cabeceras se quedan.",
          [d for d in _datos_listado() if d["text"] != Q["nombre"]],
          ["Datos del solicitante", "Contacto", "Teléfono", "Correo electrónico"])

    # X07 Dos tablas en la misma hoja, con cabeceras distintas.
    wb, ws = libro()
    _listado(ws, ("Nombre", "DNI"))
    ws["A7"] = "Representantes"
    ws.append([])
    ws["A8"], ws["B8"], ws["C8"] = "Representante", "Correo", "Móvil"
    ws["A9"], ws["B9"], ws["C9"] = TECNICO, "ferran.gil@correo.es", "622 148 309"
    ws["A10"], ws["B10"], ws["C10"] = TECNICA, "vicenta.ribes@correo.es", "633 902 114"
    ruta = destino / "x07_dos_tablas_en_una_hoja.xlsx"
    wb.save(ruta)
    nuevo("X07", ruta.name, "Dos tablas en la misma hoja: la segunda, mas abajo, con otras cabeceras "
          "(correo y movil en las columnas donde la primera tiene DNI).",
          "Los datos de las dos tablas se sustituyen; las cabeceras de la segunda se quedan.",
          [d for d in _datos_listado() if d["type"] != "EMAIL_ADDRESS"]
          + [_d(TECNICO, "PERSON", "segunda tabla"), _d("ferran.gil@correo.es", "EMAIL_ADDRESS", "segunda tabla"),
             _d("622 148 309", "PHONE_NUMBER", "segunda tabla"), _d(TECNICA, "PERSON", "segunda tabla")],
          ["Representantes", "Representante", "Correo", "Móvil"])

    # X08 Cabeceras en la primera columna.
    wb, ws = libro()
    filas = [("Nombre", P["nombre"], Q["nombre"]), ("DNI", P["dni"], Q["dni"]),
             ("Teléfono", P["telefono"], "611 902 447"), ("Correo", P["correo"], Q["correo"])]
    for f in filas:
        ws.append(list(f))
    ruta = destino / "x08_cabeceras_en_columna.xlsx"
    wb.save(ruta)
    nuevo("X08", ruta.name, "Ficha en vertical: los rotulos en la columna A y cada persona en una "
          "columna.", "Los datos se sustituyen; los rotulos de la columna A se quedan.",
          [_d(v, t, "ficha vertical") for p in (P, Q) for v, t in
           [(p["nombre"], "PERSON"), (p["dni"], "ES_DNI"), (p["correo"], "EMAIL_ADDRESS")]]
          + [_d("663 418 902", "PHONE_NUMBER", "ficha vertical")], ["Nombre", "DNI", "Teléfono", "Correo"])

    # X09 Sin cabecera.
    wb, ws = libro()
    for p in PERSONAS:
        ws.append([p["nombre"], p["dni"], p["tel"], p["correo"]])
    ruta = destino / "x09_sin_cabecera.xlsx"
    wb.save(ruta)
    nuevo("X09", ruta.name, "Datos desde la fila 1, sin ninguna cabecera.",
          "Las columnas se reconocen por su contenido y se sustituyen.", _datos_listado())

    # X10 DNI guardado como numero, sin letra.
    wb, ws = libro()
    ws.append(["Nombre", "DNI", "Nº documento", "Observación"])
    for p in PERSONAS:
        ws.append([p["nombre"], int(p["num"]), int(p["num"]) + 11, "Revisado"])
    ruta = destino / "x10_dni_como_numero.xlsx"
    wb.save(ruta)
    nuevo("X10", ruta.name, "DNI guardado como numero sin letra, bajo «DNI» y bajo «Nº documento».",
          "Las dos columnas se sustituyen.",
          [_d(p["num"], "ES_DNI", "numero bajo DNI") for p in PERSONAS]
          + [_d(str(int(p["num"]) + 11), "ES_DNI", "numero bajo Nº documento") for p in PERSONAS],
          ["Nº documento", "Revisado"])

    # X11 Telefonos como numero, con cabecera que no lo dice.
    wb, ws = libro()
    ws.append(["Nombre", "Contacto", "Contacto 2"])
    for p in PERSONAS:
        ws.append([p["nombre"], int(p["tel"]), int("34" + p["tel"])])
    ruta = destino / "x11_telefonos_como_numero.xlsx"
    wb.save(ruta)
    nuevo("X11", ruta.name, "Telefonos guardados como numero (9 cifras y con 34 delante) bajo "
          "«Contacto».", "Las columnas de telefonos se sustituyen.",
          [_d(p["tel"], "PHONE_NUMBER", "Contacto") for p in PERSONAS], ["Contacto", "Contacto 2"])

    # X12 Fechas como numero de serie.
    wb, ws = libro()
    ws.append(["Nombre", "F. Nac.", "Fecha de nacimiento", "Nacimiento"])
    for p in PERSONAS:
        ws.append([p["nombre"], _serie(p["nacimiento"]), p["nacimiento"], _serie(p["nacimiento"])])
    ruta = destino / "x12_fechas_como_numero.xlsx"
    wb.save(ruta)
    nuevo("X12", ruta.name, "Fechas de nacimiento como numero de serie sin formato de fecha, bajo «F. "
          "Nac.» y «Nacimiento», y como fecha bajo «Fecha de nacimiento».",
          "Las tres columnas se sustituyen.",
          [_d(_serie(p["nacimiento"]), "DATE_OF_BIRTH", "numero de serie") for p in PERSONAS],
          ["F. Nac."])

    # X13 IBAN repartido en columnas.
    wb, ws = libro()
    ws.append(["Titular", "País", "Entidad", "Oficina", "DC", "Cuenta"])
    ws.append([P["nombre"], "ES91", "2100", "0418", "45", "0200051332"])
    ws.append([Q["nombre"], "ES80", "0049", "1500", "05", "2810498763"])
    ruta = destino / "x13_iban_repartido.xlsx"
    wb.save(ruta)
    nuevo("X13", ruta.name, "Cuenta bancaria repartida en columnas (pais, entidad, oficina, DC, cuenta).",
          "Al menos el numero de cuenta se sustituye.",
          [_d("0200051332", "IBAN_CODE", "columna Cuenta"), _d("2810498763", "IBAN_CODE", "columna Cuenta")],
          ["Titular", "Entidad", "Oficina"])

    # X14 Lista desplegable con nombres.
    wb, ws = libro()
    _listado(ws)
    ws["E1"] = "Tramitador"
    dv = DataValidation(type="list", formula1=f'"{TECNICA},{TECNICO}"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("E2:E10")
    ruta = destino / "x14_lista_desplegable.xlsx"
    wb.save(ruta)
    nuevo("X14", ruta.name, "Lista desplegable de validacion con los nombres de los tramitadores.",
          "Los nombres de la lista no pueden quedar.",
          _datos_listado() + [_d(TECNICA, "PERSON", "validacion"), _d(TECNICO, "PERSON", "validacion")],
          ["Tramitador"])

    # X15 Formato condicional con un dato en la formula.
    wb, ws = libro()
    _listado(ws)
    ws.conditional_formatting.add("A2:D10", FormulaRule(formula=[f'$A2="{OTRA}"'],
                                                        fill=PatternFill("solid", start_color="FFFF00")))
    ws.conditional_formatting.add("B2:B10", FormulaRule(formula=['$B2="39184027G"'],
                                                        fill=PatternFill("solid", start_color="FF9999")))
    ruta = destino / "x15_formato_condicional.xlsx"
    wb.save(ruta)
    nuevo("X15", ruta.name, "Reglas de formato condicional con un nombre y un DNI escritos en la formula.",
          "Los datos de las reglas no pueden quedar.",
          _datos_listado() + [_d(OTRA, "PERSON", "formato condicional"),
                              _d("39184027G", "ES_DNI", "formato condicional")])

    # X16 Cabeceras y pies de impresion, titulos de impresion.
    wb, ws = libro()
    _listado(ws)
    ws.oddHeader.left.text = f"Preparado por {TECNICA}"
    ws.oddFooter.center.text = f"Consultas: {TECNICO}, tel. 622 148 309"
    ws.evenHeader.right.text = f"Revisado por {OTRA}"
    ws.print_title_rows = "1:1"
    ruta = destino / "x16_cabeceras_de_impresion.xlsx"
    wb.save(ruta)
    nuevo("X16", ruta.name, "Cabecera y pie de impresion (pares e impares) con nombres y un telefono; "
          "filas que se repiten al imprimir.", "Los datos de cabeceras y pies se sustituyen.",
          _datos_listado() + [_d(TECNICA, "PERSON", "cabecera impresion"), _d(TECNICO, "PERSON", "pie impresion"),
                              _d("622 148 309", "PHONE_NUMBER", "pie impresion"),
                              _d(OTRA, "PERSON", "cabecera pares")], ["Preparado por", "Consultas"])

    # X17 Comentario clasico, comentario encadenado y lista de personas.
    wb, ws = libro()
    _listado(ws)
    ws["C3"].comment = Comment(f"Confirmado por telefono con {OTRA}", TECNICA)
    ruta = destino / "x17_comentarios_y_personas.xlsx"
    wb.save(ruta)
    partes = _partes(ruta)
    hilo = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><ThreadedComments xmlns="http://schemas.'
            'microsoft.com/office/spreadsheetml/2018/threadedcomments"><threadedComment ref="B2" '
            'dT="2024-03-01T10:00:00.00" personId="{11111111-1111-1111-1111-111111111111}" '
            'id="{22222222-2222-2222-2222-222222222222}"><text>Pedir copia del DNI a '
            f'{TECNICO}</text></threadedComment></ThreadedComments>').encode("utf-8")
    personas = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><personList xmlns="http://schemas.'
                'microsoft.com/office/spreadsheetml/2018/threadedcomments"><person displayName="'
                f'{TECNICA}" id="{{11111111-1111-1111-1111-111111111111}}" userId="vicenta.ribes@correo.es" '
                'providerId="AD"/></personList>').encode("utf-8")
    _anadir(partes, "xl/threadedComments/threadedComment1.xml", hilo, "application/vnd.ms-excel.threadedcomments+xml",
            "xl/worksheets/sheet1.xml", "http://schemas.microsoft.com/office/2017/10/relationships/threadedComment")
    _anadir(partes, "xl/persons/person.xml", personas, "application/vnd.ms-excel.person+xml",
            "xl/workbook.xml", "http://schemas.microsoft.com/office/2017/10/relationships/person")
    _escribir(ruta, partes)
    nuevo("X17", ruta.name, "Comentario clasico (con su autora), comentario encadenado y la lista de "
          "personas que los firma, con su correo.", "Comentarios y personas desaparecen.",
          _datos_listado() + [_d(OTRA, "PERSON", "comentario"), _d(TECNICA, "PERSON", "autora / personas"),
                              _d(TECNICO, "PERSON", "comentario encadenado"),
                              _d("vicenta.ribes@correo.es", "EMAIL_ADDRESS", "lista de personas")])

    # X18 Conexion a datos con usuario y consulta de Power Query.
    wb, ws = libro()
    _listado(ws)
    ruta = destino / "x18_conexiones_y_consulta.xlsx"
    wb.save(ruta)
    partes = _partes(ruta)
    conexiones = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><connections xmlns="'
                  f'{S}"><connection id="1" name="Padron" description="Extraccion de {TECNICO}" type="1" '
                  'refreshedVersion="6"><dbPr connection="Provider=SQLOLEDB.1;Data Source=srv-padron;'
                  'User ID=vribes;Password=Valencia2024;Initial Catalog=Padron" command="SELECT * FROM '
                  f'vecinos WHERE nombre = \'{OTRA}\'"/></connection></connections>').encode("utf-8")
    _anadir(partes, "xl/connections.xml", conexiones, "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.connections+xml", "xl/workbook.xml",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/connections")
    m = io.BytesIO()
    with zipfile.ZipFile(m, "w") as z:
        z.writestr("Formulas/Section1.m", 'section Section1;\nshared Consulta1 = let Origen = Excel.Workbook('
                   'File.Contents("C:\\Users\\vribes\\Documents\\Ayudas ' + P["nombre"] + '.xlsx")) in Origen;')
    mashup = ('<?xml version="1.0" encoding="utf-16"?><DataMashup xmlns="http://schemas.microsoft.com/'
              f'DataMashup">{base64.b64encode(m.getvalue()).decode()}</DataMashup>').encode("utf-16")
    _anadir(partes, "customXml/item1.xml", mashup, "application/xml", "xl/workbook.xml",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml")
    _escribir(ruta, partes)
    nuevo("X18", ruta.name, "Conexion a una base de datos con usuario, contraseña y una consulta con un "
          "nombre; consulta de Power Query (DataMashup) con una ruta que lleva el usuario y un nombre.",
          "Conexiones y consultas desaparecen.",
          _datos_listado() + [_d("vribes", "PERSON", "usuario en conexion / ruta"),
                              _d("Valencia2024", "PASSWORD", "contraseña en conexion"),
                              _d(OTRA, "PERSON", "consulta SQL"), _d(TECNICO, "PERSON", "descripcion conexion")])

    # X19 Grafico con su copia de datos y un titulo con nombre.
    wb, ws = libro()
    ws.append(["Beneficiario", "Importe"])
    for p, imp in zip(PERSONAS, (420.5, 380.0, 615.25)):
        ws.append([p["nombre"], imp])
    graf = BarChart()
    graf.title = f"Ayudas tramitadas por {TECNICA}"
    graf.add_data(Reference(ws, min_col=2, min_row=1, max_row=4), titles_from_data=True)
    graf.set_categories(Reference(ws, min_col=1, min_row=2, max_row=4))
    ws.add_chart(graf, "D2")
    ruta = destino / "x19_grafico_con_copia.xlsx"
    wb.save(ruta)
    partes = _partes(ruta)
    nombre_g = next(n for n in partes if n.startswith("xl/charts/chart"))
    raiz = _xml(partes[nombre_g])
    C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    for ref in raiz.iter(f"{{{C}}}strRef"):
        cache = etree.SubElement(ref, f"{{{C}}}strCache")
        etree.SubElement(cache, f"{{{C}}}ptCount").set("val", "3")
        for i, p in enumerate(PERSONAS):
            pt = etree.SubElement(cache, f"{{{C}}}pt")
            pt.set("idx", str(i))
            etree.SubElement(pt, f"{{{C}}}v").text = p["nombre"]
    partes[nombre_g] = _bytes(raiz)
    _escribir(ruta, partes)
    nuevo("X19", ruta.name, "Grafico con la copia de los nombres de las categorias y un titulo con el "
          "nombre de quien lo preparo.", "La copia se quita y el titulo no lleva el nombre.",
          [_d(p["nombre"], "PERSON", "celdas y copia del grafico") for p in PERSONAS]
          + [_d(TECNICA, "PERSON", "titulo del grafico")], ["Beneficiario", "Importe"])

    # X20 Texto con varios formatos en una celda.
    wb, ws = libro()
    ws.append(["Interesado", "Notas"])
    ws.append([CellRichText(TextBlock(InlineFont(b=True), "Remedios "), "Albiach ",
                            TextBlock(InlineFont(i=True, color="C00000"), "Furió")),
               CellRichText("Con DNI ", TextBlock(InlineFont(b=True), P["dni"][:4]), P["dni"][4:],
                            ", pide cita para el ", TextBlock(InlineFont(u="single"), "martes"), ".")])
    ws.append([Q["nombre"], CellRichText("Llamar al ", TextBlock(InlineFont(b=True), "611 902"), " 447 por la tarde.")])
    ruta = destino / "x20_texto_con_formatos.xlsx"
    wb.save(ruta)
    nuevo("X20", ruta.name, "Nombre y DNI partidos en tramos con formatos distintos dentro de la misma "
          "celda (texto enriquecido).", "El dato entero se sustituye; el resto de la nota se queda.",
          [_d(P["nombre"], "PERSON", "texto enriquecido"), _d("Albiach", "PERSON", "texto enriquecido"),
           _d(P["dni"], "ES_DNI", "texto enriquecido"), _d(Q["nombre"], "PERSON", "celda"),
           _d("611 902 447", "PHONE_NUMBER", "texto enriquecido")], ["Interesado", "pide cita", "por la tarde"])

    # X21 Tabla con autofiltro que filtra por un nombre.
    wb, ws = libro()
    _listado(ws)
    tabla = Table(displayName="Interesados", ref="A1:D4")
    ws.add_table(tabla)
    ws.auto_filter.ref = "A1:D4"
    ws.auto_filter.add_filter_column(0, [OTRA])
    ruta = destino / "x21_autofiltro_por_nombre.xlsx"
    wb.save(ruta)
    nuevo("X21", ruta.name, "Tabla de Excel con autofiltro que guarda el nombre por el que se filtro.",
          "El valor del filtro no puede quedar.", _datos_listado() + [_d(OTRA, "PERSON", "autofiltro")],
          ["Nombre", "DNI"])

    # X22 Nombres definidos con una formula que lleva el dato.
    wb, ws = libro()
    _listado(ws)
    wb.defined_names["Firmante"] = DefinedName("Firmante", attr_text=f'CONCATENATE("Vicenta"," Ribes Mollà")')
    wb.defined_names["Responsable"] = DefinedName("Responsable", attr_text=f'"{TECNICO}"')
    wb.defined_names["Telefono_contacto"] = DefinedName("Telefono_contacto", attr_text="622148309")
    ruta = destino / "x22_nombres_definidos.xlsx"
    wb.save(ruta)
    nuevo("X22", ruta.name, "Nombres definidos: uno con un texto fijo, otro con una formula que arma un "
          "nombre y otro con un telefono como numero.", "Ningun nombre definido lleva el dato.",
          _datos_listado() + [_d("Ribes Mollà", "PERSON", "nombre definido con formula"),
                              _d(TECNICO, "PERSON", "nombre definido fijo"),
                              _d("622148309", "PHONE_NUMBER", "nombre definido numero")])

    # X23 Hoja muy oculta y columna oculta.
    wb, ws = libro()
    _listado(ws, ("Nombre", "Correo electrónico"))
    ws["C1"] = "Móvil"
    for i, p in enumerate(PERSONAS, 2):
        ws.cell(row=i, column=3, value=p["tel"])
    ws.column_dimensions["C"].hidden = True
    aux = wb.create_sheet("Auxiliar")
    aux.sheet_state = "veryHidden"
    aux.append(["Nombre", "DNI"])
    aux.append([OTRA, "39184027G"])
    ruta = destino / "x23_hoja_muy_oculta.xlsx"
    wb.save(ruta)
    nuevo("X23", ruta.name, "Hoja «muy oculta» (no se puede mostrar desde Excel) y una columna oculta "
          "con telefonos.", "Lo oculto se trata como lo visible.",
          [_d(p["nombre"], "PERSON", "listado") for p in PERSONAS]
          + [_d(p["tel"], "PHONE_NUMBER", "columna oculta") for p in PERSONAS]
          + [_d(OTRA, "PERSON", "hoja muy oculta"), _d("39184027G", "ES_DNI", "hoja muy oculta")], ["Móvil"])

    # X24 Imagen con EXIF y miniatura.
    wb, ws = libro()
    _listado(ws)
    foto = _foto_con_miniatura(destino)
    img = XlImage(io.BytesIO(foto))
    ws.add_image(img, "F2")
    ruta = destino / "x24_imagen_con_exif.xlsx"
    wb.save(ruta)
    partes = _partes(ruta)
    media = next(n for n in partes if n.startswith("xl/media/"))
    partes[media] = foto
    _escribir(ruta, partes)
    nuevo("X24", ruta.name, "Foto de un formulario dentro del libro, con autora y GPS en el EXIF y una "
          "miniatura EXIF que es la foto entera en pequeño.",
          "La foto pasa por el camino de imagenes: datos tapados, sin EXIF ni miniatura.",
          _datos_listado() + [_d(TECNICA, "PERSON", "EXIF de la imagen")], imagen_incrustada=True)

    # X25 Libro con macros.
    wb, ws = libro()
    _listado(ws)
    ruta = destino / "x25_con_macros.xlsm"
    wb.save(ruta)
    partes = _partes(ruta)
    partes["[Content_Types].xml"] = partes["[Content_Types].xml"].replace(
        b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        b"application/vnd.ms-excel.sheet.macroEnabled.main+xml")
    _escribir(ruta, partes)
    nuevo("X25", ruta.name, "Libro con macros (.xlsm).", "Se rechaza con un mensaje que explica que hay "
          "que guardarlo sin macros.", [], rechazo="office_macros")

    # --- dañados normales -------------------------------------------------------
    wb, ws = libro()
    _listado(ws)
    sano = destino / "_sano.xlsx"
    wb.save(sano)
    datos = sano.read_bytes()
    sano.unlink()
    (destino / "x26_cortado.xlsx").write_bytes(datos[:len(datos) // 2])
    nuevo("X26", "x26_cortado.xlsx", "Libro cortado a la mitad (descarga interrumpida).",
          "Error claro, sin caerse.", [], rechazo="invalid_file")
    (destino / "x27_vacio.xlsx").write_bytes(b"")
    nuevo("X27", "x27_vacio.xlsx", "Fichero vacio con extension .xlsx.", "Error claro, sin caerse.", [],
          rechazo="invalid_file")
    (destino / "x28_texto_como_xlsx.xlsx").write_text("Nombre;DNI\n" + f"{P['nombre']};{P['dni']}\n",
                                                       encoding="utf-8")
    nuevo("X28", "x28_texto_como_xlsx.xlsx", "Un CSV (texto) con extension .xlsx.",
          "Error claro: no es un formato admitido.", [], rechazo="invalid_file")
    (destino / "x29_excel_como_docx.xlsx").write_bytes(datos)
    nuevo("X29", "x29_excel_como_docx.xlsx", "Un Excel de verdad subido con el nombre «informe.docx».",
          "Se reconoce por el contenido: se procesa como Excel y vuelve como .xlsx.",
          _datos_listado(), ["Nombre", "Correo electrónico"], subida="informe.docx")
    return salida


def corpus(destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    salida = casos(destino)
    (destino / "casos.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    return salida


if __name__ == "__main__":
    c = corpus(Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO)
    print(f"{len(c)} casos")
