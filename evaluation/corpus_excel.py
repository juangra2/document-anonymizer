"""Corpus de hojas de calculo (.xlsx) con datos inventados y sus etiquetas.

Imita los Excel que llegan a una administracion: listados de personas
(solicitantes, inscritos, beneficiarios), con cabeceras en castellano, en
valenciano o bilingues; la cabecera en la primera fila o debajo de un titulo;
columnas cuyo nombre no dice nada ("Col3") pero cuyo contenido si; telefonos
y DNI guardados como numero; fechas como fecha de Excel; una columna de texto
libre (observaciones) con datos dentro; y lo que un Excel guarda aparte de lo
que se ve: hojas ocultas, comentarios, formulas que copian un dato, nombres
definidos con un valor y propiedades del documento.  Importes de personas
(MONEY) con sus totales, y un presupuesto de obra: con las casillas por defecto
no se toca nada; con la de presupuesto marcada, sus precios se sustituyen.

Etiqueta de cada libro (etiquetas.json):
  datos      valores que no pueden quedar en NINGUNA parte del fichero
  columnas   por hoja: cabecera -> tipo esperado (None si la columna no es personal)
  legitimos  texto que tiene que seguir tal cual (cabeceras, titulos, conceptos)
  celdas     (opcional) por hoja: celda -> valor esperado en la salida
  marcados   (opcional) tipos que se marcan ademas de los del catalogo

Uso: python -m evaluation.corpus_excel [DESTINO]   (por defecto evaluation/excel/corpus)
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName

from evaluation.corpus_imagenes import dni, iban

DESTINO = Path("evaluation/excel/corpus")

NOMBRES = ["Teresa", "Vicent", "Marina", "Josep", "Lucía", "Andrés", "Amparo", "Joan", "Carmen", "Pau",
           "Rosa", "Miguel", "Neus", "Javier", "Pilar", "Xavier"]
APELLIDOS = ["Navarro", "Ferrer", "Soler", "Peiró", "Climent", "Sanchis", "Alandí", "Pastor", "Gil",
             "Martínez", "Llorens", "Ortega", "Bosch", "Vidal", "Monzó", "Escrivà"]
CALLES = ["Calle de Ruzafa", "Carrer de Sueca", "Avenida del Cid", "Calle de Colón", "Carrer del Mar",
          "Avenida de Aragón", "Calle de Cuba", "Carrer de Borriana"]


def _personas(n: int, semilla: int) -> list[dict]:
    rng = random.Random(semilla)
    salida = []
    for i in range(n):
        nombre = rng.choice(NOMBRES)
        apellidos = f"{rng.choice(APELLIDOS)} {rng.choice(APELLIDOS)}"
        numero = rng.randint(10_000_000, 79_999_999)
        nacimiento = dt.date(1950, 1, 1) + dt.timedelta(days=rng.randint(0, 20_000))
        salida.append({
            "nombre": nombre, "apellidos": apellidos, "completo": f"{nombre} {apellidos}",
            "dni": dni(numero), "numero": numero,
            "telefono": f"6{rng.randint(10_000_000, 99_999_999)}",
            "correo": f"{nombre.lower()}.{apellidos.split()[0].lower()}{i}@correo.es".replace("í", "i")
                      .replace("ó", "o").replace("à", "a"),
            "direccion": f"{rng.choice(CALLES)} {rng.randint(1, 120)}, 460{rng.randint(10, 25)} Valencia",
            "nacimiento": nacimiento,
            "iban": iban(f"{rng.randint(1000, 9999)}", f"{rng.randint(1000, 9999)}", f"{rng.randint(10, 99)}",
                         f"{rng.randint(10**9, 10**10 - 1)}"),
            "importe": round(rng.uniform(50, 900), 2),
        })
    return salida


def _datos(personas: list[dict], campos: list[str], tipos: dict[str, str]) -> list[dict]:
    salida = []
    for p in personas:
        for c in campos:
            valor = p[c]
            if isinstance(valor, dt.date):
                valor = valor.strftime("%d/%m/%Y")
            salida.append({"text": str(valor), "type": tipos[c]})
    return salida


_TIPOS = {"completo": "PERSON", "nombre": "PERSON", "apellidos": "PERSON", "dni": "ES_DNI",
          "numero": "ES_DNI", "telefono": "PHONE_NUMBER", "correo": "EMAIL_ADDRESS",
          "direccion": "ADDRESS", "nacimiento": "DATE_OF_BIRTH", "iban": "IBAN_CODE"}


def listado(destino: Path) -> dict:
    """Solicitantes de una ayuda: cabeceras en castellano, una fila por persona."""
    ps = _personas(12, 1)
    wb = Workbook()
    ws = wb.active
    ws.title = "Solicitantes"
    cab = ["Nº", "Nombre", "Apellidos", "DNI", "Fecha de nacimiento", "Teléfono", "Correo electrónico",
           "Domicilio", "Importe concedido", "Estado"]
    ws.append(cab)
    for i, p in enumerate(ps, 1):
        ws.append([i, p["nombre"], p["apellidos"], p["dni"], p["nacimiento"], p["telefono"], p["correo"],
                   p["direccion"], p["importe"], "Concedida" if i % 3 else "Pendiente"])
    wb.properties.creator = ps[0]["completo"]
    wb.properties.title = f"Listado revisado por {ps[1]['completo']}"
    wb.save(destino)
    return {"datos": _datos(ps, ["nombre", "apellidos", "dni", "telefono", "correo", "direccion"], _TIPOS)
            + [{"text": ps[0]["completo"], "type": "PERSON"}, {"text": ps[1]["completo"], "type": "PERSON"}],
            "columnas": {"Solicitantes": {"Nº": None, "Nombre": "PERSON", "Apellidos": "PERSON", "DNI": "ES_DNI",
                                          "Fecha de nacimiento": "DATE_OF_BIRTH", "Teléfono": "PHONE_NUMBER",
                                          "Correo electrónico": "EMAIL_ADDRESS", "Domicilio": "ADDRESS",
                                          "Importe concedido": "MONEY", "Estado": None}},
            "legitimos": cab + ["Concedida", "Pendiente"]}


def valenciano_con_titulo(destino: Path) -> dict:
    """Inscrits a un curs: titol en les primeres files i la capcalera en la fila 3."""
    ps = _personas(10, 2)
    wb = Workbook()
    ws = wb.active
    ws.title = "Inscrits"
    ws.append(["CURS DE VALENCIÀ PER A ADULTS · TARDOR 2026"])
    ws.append([])
    cab = ["Nom i cognoms", "DNI/NIE", "Telèfon", "Correu electrònic", "Data de naixement", "Nivell"]
    ws.append(cab)
    for i, p in enumerate(ps):
        ws.append([p["completo"], p["dni"], int(p["telefono"]), p["correo"], p["nacimiento"],
                   ["A2", "B1", "C1"][i % 3]])
    wb.save(destino)
    return {"datos": _datos(ps, ["completo", "dni", "telefono", "correo"], _TIPOS),
            "columnas": {"Inscrits": {"Nom i cognoms": "PERSON", "DNI/NIE": "ES_DNI", "Telèfon": "PHONE_NUMBER",
                                      "Correu electrònic": "EMAIL_ADDRESS", "Data de naixement": "DATE_OF_BIRTH",
                                      "Nivell": None}},
            "legitimos": cab + ["CURS DE VALENCIÀ PER A ADULTS · TARDOR 2026", "A2", "B1", "C1"]}


def sin_cabecera_clara(destino: Path) -> dict:
    """Columnas con nombres que no dicen nada: el contenido decide."""
    ps = _personas(15, 3)
    wb = Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    cab = ["Col1", "Col2", "Col3", "Col4", "Col5"]
    ws.append(cab)
    for i, p in enumerate(ps):
        ws.append([f"REF-{1000 + i}", p["completo"], p["dni"], p["correo"], p["iban"]])
    wb.save(destino)
    return {"datos": _datos(ps, ["completo", "dni", "correo", "iban"], _TIPOS),
            "columnas": {"Hoja1": {"Col1": None, "Col2": "PERSON", "Col3": "ES_DNI", "Col4": "EMAIL_ADDRESS",
                                   "Col5": "IBAN_CODE"}},
            "legitimos": cab + ["REF-1000", "REF-1014"]}


def con_observaciones(destino: Path) -> dict:
    """Texto libre: hay que analizar cada celda, no la columna entera."""
    ps = _personas(8, 4)
    wb = Workbook()
    ws = wb.active
    ws.title = "Expedientes"
    cab = ["Expediente", "Tipo", "Observaciones"]
    ws.append(cab)
    textos = []
    for i, p in enumerate(ps):
        if i % 2:
            obs = f"Llamar a {p['completo']} al {p['telefono']} para completar la documentación."
        else:
            obs = "Documentación completa, pendiente de informe técnico."
        textos.append(obs)
        ws.append([f"EXP-2026-{300 + i}", "Licencia de obra menor", obs])
    wb.save(destino)
    datos = []
    for i, p in enumerate(ps):
        if i % 2:
            datos += [{"text": p["completo"], "type": "PERSON"}, {"text": p["telefono"], "type": "PHONE_NUMBER"}]
    return {"datos": datos,
            "columnas": {"Expedientes": {"Expediente": None, "Tipo": None, "Observaciones": "TEXTO_LIBRE"}},
            "legitimos": cab + ["Licencia de obra menor", "Documentación completa, pendiente de informe técnico.",
                                "para completar la documentación."]}


def cargado(destino: Path) -> dict:
    """Lo que un Excel guarda aparte: hoja oculta, comentarios, formulas, nombres, propiedades."""
    ps = _personas(6, 5)
    wb = Workbook()
    ws = wb.active
    ws.title = "Beneficiarios"
    cab = ["Nombre y apellidos", "NIF", "Teléfono", "Iniciales"]
    ws.append(cab)
    for i, p in enumerate(ps, 2):
        ws.append([p["completo"], p["dni"], p["telefono"], f'=LEFT(A{i},1)&MID(A{i},FIND(" ",A{i})+1,1)'])
    ws["B2"].comment = Comment(f"Comprobado con {ps[5]['completo']}", ps[5]["completo"])
    oculta = wb.create_sheet("Datos internos")
    oculta.sheet_state = "hidden"
    oculta.append(["Responsable", "Teléfono directo"])
    oculta.append([ps[4]["completo"], ps[4]["telefono"]])
    resumen = wb.create_sheet("Resumen")
    resumen.append(["Concepto", "Valor"])
    resumen.append(["Primer beneficiario", "=Beneficiarios!A2"])
    resumen.append(["Total de beneficiarios", "=COUNTA(Beneficiarios!A2:A7)"])
    wb.defined_names["Responsable"] = DefinedName("Responsable", attr_text=f'"{ps[4]["completo"]}"')
    wb.properties.creator = ps[3]["completo"]
    wb.properties.lastModifiedBy = ps[2]["completo"]
    wb.properties.keywords = ps[0]["dni"]
    wb.save(destino)
    datos = _datos(ps, ["completo", "dni", "telefono"], _TIPOS)
    return {"datos": datos,
            "columnas": {"Beneficiarios": {"Nombre y apellidos": "PERSON", "NIF": "ES_DNI",
                                           "Teléfono": "PHONE_NUMBER", "Iniciales": "PERSON"},
                         "Datos internos": {"Responsable": "PERSON", "Teléfono directo": "PHONE_NUMBER"},
                         "Resumen": {"Concepto": None, "Valor": None}},
            "legitimos": ["Nombre y apellidos", "NIF", "Teléfono", "Concepto", "Total de beneficiarios",
                          "Primer beneficiario"]}


def con_tabla_dinamica(destino: Path) -> dict:
    """Una tabla dinamica: su copia de los datos (cache) y sus celdas ya
    calculadas en la hoja llevan los nombres.  openpyxl no las crea: se escriben
    a mano la cache, la definicion y las celdas que ocupa."""
    import zipfile

    ps = _personas(4, 6)
    wb = Workbook()
    ws = wb.active
    ws.title = "Ayudas"
    cab = ["Beneficiario", "Importe"]
    ws.append(cab)
    for p in ps:
        ws.append([p["completo"], p["importe"]])
    resumen = wb.create_sheet("Resumen dinámico")
    resumen.append(["Etiquetas de fila", "Suma de Importe"])
    for p in ps:
        resumen.append([p["completo"], p["importe"]])
    resumen.append(["Total general", round(sum(p["importe"] for p in ps), 2)])
    wb.save(destino)

    with zipfile.ZipFile(destino) as z:
        partes = {n: z.read(n) for n in z.namelist()}
    items = "".join(f'<s v="{p["completo"]}"/>' for p in ps)
    partes["xl/pivotCache/pivotCacheDefinition1.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<pivotCacheDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId1" '
        f'refreshOnLoad="0" recordCount="{len(ps)}"><cacheSource type="worksheet">'
        '<worksheetSource ref="A1:B5" sheet="Ayudas"/></cacheSource><cacheFields count="2">'
        f'<cacheField name="Beneficiario" numFmtId="0"><sharedItems count="{len(ps)}">{items}</sharedItems>'
        '</cacheField><cacheField name="Importe" numFmtId="0"><sharedItems containsNumber="1"/></cacheField>'
        '</cacheFields></pivotCacheDefinition>').encode("utf-8")
    registros = "".join(f'<r><x v="{i}"/><n v="{p["importe"]}"/></r>' for i, p in enumerate(ps))
    partes["xl/pivotCache/pivotCacheRecords1.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<pivotCacheRecords xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(ps)}">{registros}<!-- {ps[0]["dni"]} --></pivotCacheRecords>').encode("utf-8")
    partes["xl/pivotCache/_rels/pivotCacheDefinition1.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'pivotCacheRecords" Target="pivotCacheRecords1.xml"/></Relationships>').encode("utf-8")
    partes["xl/pivotTables/pivotTable1.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<pivotTableDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'name="TablaDinámica1" cacheId="1" dataCaption="Valores">'
        f'<location ref="A1:B{len(ps) + 2}" firstHeaderRow="1" firstDataRow="1" firstDataCol="1"/>'
        '</pivotTableDefinition>').encode("utf-8")
    partes["xl/pivotTables/_rels/pivotTable1.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'pivotCacheDefinition" Target="../pivotCache/pivotCacheDefinition1.xml"/></Relationships>').encode("utf-8")
    hoja_rels = "xl/worksheets/_rels/sheet2.xml.rels"
    partes[hoja_rels] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdPivot" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'pivotTable" Target="../pivotTables/pivotTable1.xml"/></Relationships>').encode("utf-8")
    libro = partes["xl/workbook.xml"].decode("utf-8")
    libro = libro.replace("</workbook>", '<pivotCaches xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
                          '2006/relationships"><pivotCache cacheId="1" r:id="rIdCache"/></pivotCaches></workbook>')
    partes["xl/workbook.xml"] = libro.encode("utf-8")
    rels = partes["xl/_rels/workbook.xml.rels"].decode("utf-8").replace(
        "</Relationships>", '<Relationship Id="rIdCache" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/pivotCacheDefinition" Target="pivotCache/pivotCacheDefinition1.xml"/></Relationships>')
    partes["xl/_rels/workbook.xml.rels"] = rels.encode("utf-8")
    tipos = partes["[Content_Types].xml"].decode("utf-8").replace("</Types>", (
        '<Override PartName="/xl/pivotCache/pivotCacheDefinition1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"/>'
        '<Override PartName="/xl/pivotCache/pivotCacheRecords1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.pivotCacheRecords+xml"/>'
        '<Override PartName="/xl/pivotTables/pivotTable1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.pivotTable+xml"/></Types>'))
    partes["[Content_Types].xml"] = tipos.encode("utf-8")
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in partes.items():
            z.writestr(n, d)
    return {"datos": _datos(ps, ["completo"], _TIPOS) + [{"text": ps[0]["dni"], "type": "ES_DNI"}],
            "columnas": {"Ayudas": {"Beneficiario": "PERSON", "Importe": "MONEY"}},
            "dinamica": {"hoja": "Resumen dinámico", "rango": f"A1:B{len(ps) + 2}"},
            "legitimos": cab}


def ayudas_con_totales(destino: Path) -> dict:
    """Importes de personas y, en otra hoja, sus totales: la suma y la media se
    quedan con su valor; el maximo y el minimo senalan a una persona."""
    ps = _personas(6, 8)
    wb = Workbook()
    ws = wb.active
    ws.title = "Ayudas"
    cab = ["Beneficiario", "NIF", "Cuantía (€)", "Concepto"]
    ws.append(cab)
    for p in ps:
        ws.append([p["completo"], p["dni"], p["importe"], "Ayuda al alquiler"])
    resumen = wb.create_sheet("Resumen")
    resumen.append(["Total concedido", "=SUM(Ayudas!C2:C7)"])
    resumen.append(["Media por persona", "=AVERAGE(Ayudas!C2:C7)"])
    resumen.append(["Ayuda más alta", "=MAX(Ayudas!C2:C7)"])
    resumen.append(["Ayuda más baja", "=MIN(Ayudas!C2:C7)"])
    resumen.append(["Número de ayudas", "=COUNTA(Ayudas!A2:A7)"])
    wb.save(destino)
    importes = [p["importe"] for p in ps]
    return {"datos": _datos(ps, ["completo", "dni"], _TIPOS),
            "columnas": {"Ayudas": {"Beneficiario": "PERSON", "NIF": "ES_DNI", "Cuantía (€)": "MONEY",
                                    "Concepto": None}},
            "celdas": {"Resumen": {"B1": f"={sum(importes):.15g}", "B2": f"={sum(importes) / len(importes):.15g}",
                                   "B3": "[IMPORTE]", "B4": "[IMPORTE]", "B5": "=COUNTA(Ayudas!A2:A7)"}},
            "legitimos": cab + ["Ayuda al alquiler", "Total concedido", "Media por persona", "Ayuda más alta",
                                "Ayuda más baja", "Número de ayudas"]}


def estadistica(destino: Path) -> dict:
    """Importes sin personas ni presupuesto (por distritos): no se tocan."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Distritos"
    filas = [["Distrito", "Solicitudes", "Importe concedido"], ["Ciutat Vella", 41, 18250.5],
             ["Extramurs", 37, 16120.0], ["Campanar", 29, 12975.25], ["Total", "=SUM(B2:B4)", "=SUM(C2:C4)"]]
    for f in filas:
        ws.append(f)
    wb.save(destino)
    return {"datos": [], "columnas": {"Distritos": {c: None for c in filas[0]}},
            "legitimos": [c for f in filas for c in f if isinstance(c, str) and not c.startswith("=")]}


def presupuesto(destino: Path) -> dict:
    """Un presupuesto de obra, sin datos personales.  Con las casillas por
    defecto (la de presupuesto viene desmarcada) no se puede tocar nada."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Presupuesto"
    filas = [["Partida", "Descripción", "Unidades", "Precio unitario", "Importe"],
             ["1.1", "Demolición de tabiquería", 24, 18.5, "=C2*D2"],
             ["1.2", "Retirada de escombros a vertedero", 3, 120.0, "=C3*D3"],
             ["2.1", "Tabique de ladrillo hueco doble", 30, 22.75, "=C4*D4"],
             ["3.1", "Pintura plástica en paredes", 85, 6.2, "=C5*D5"],
             ["", "TOTAL", "", "", "=SUM(E2:E5)"]]
    for f in filas:
        ws.append(f)
    wb.save(destino)
    textos = [c for f in filas for c in f if isinstance(c, str) and c and not c.startswith("=")]
    return {"datos": [], "columnas": {"Presupuesto": {c: None for c in filas[0]}}, "legitimos": textos}


def presupuesto_marcado(destino: Path) -> dict:
    """El mismo presupuesto, con la casilla de presupuesto marcada: precios,
    mediciones, importes y el total pasan a ser el marcador."""
    e = presupuesto(destino)
    importes = {"Unidades", "Precio unitario", "Importe"}
    return {**e, "marcados": ["BUDGET_AMOUNT"],
            "columnas": {"Presupuesto": {c: ("BUDGET_AMOUNT" if c in importes else None)
                                         for c in e["columnas"]["Presupuesto"]}},
            "celdas": {"Presupuesto": {"E6": "[IMPORTE]"}}}


def corpus(destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    etiquetas = []
    for nombre, hacer in [("listado", listado), ("valenciano_con_titulo", valenciano_con_titulo),
                          ("sin_cabecera_clara", sin_cabecera_clara), ("con_observaciones", con_observaciones),
                          ("cargado", cargado), ("con_tabla_dinamica", con_tabla_dinamica),
                          ("ayudas_con_totales", ayudas_con_totales), ("estadistica", estadistica),
                          ("presupuesto", presupuesto), ("presupuesto_marcado", presupuesto_marcado)]:
        ruta = destino / f"{nombre}.xlsx"
        e = hacer(ruta)
        etiquetas.append({"documento": ruta.name, **e})
    (destino / "etiquetas.json").write_text(json.dumps(etiquetas, ensure_ascii=False, indent=1), encoding="utf-8")
    return etiquetas


if __name__ == "__main__":
    salida = corpus(Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO)
    print(f"{len(salida)} libros")
