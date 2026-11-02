"""Hojas de calculo (.xlsx): la columna con datos personales conserva su
cabecera y sus celdas llevan el marcador del tipo ("[DNI]", "[NOMBRE]"), como en
Word.  No se borran columnas ni se pinta nada.

Por hoja:
  1. La cabecera: la fila (de las primeras veinte) cuyas celdas se parecen mas
     a nombres de columna conocidos; si ninguno, la primera fila con varios
     textos y datos debajo; sin ella, todo son datos.  No se toca.
  2. Cada columna se clasifica por el nombre de su cabecera (castellano y
     valenciano: "DNI", "Nom i cognoms", "Data de naixement"...) o, si no dice
     nada ("Col3"), por su contenido: si al menos el 60 % de sus valores son
     DNI o NIE validos, correos, IBAN, telefonos o nombres del padron.
  3. Columna personal: todas sus celdas de datos pasan a ser el marcador (los
     DNI y telefonos guardados como numero tambien).  Columna de texto libre
     (observaciones, descripcion...) o celda larga suelta: se analiza celda a
     celda y solo se sustituye el dato.
  4. Importes, con la regla del PDF: una columna de importes (cabecera
     "Importe", "Cuantía", "Ayuda"...) en una hoja con alguna columna personal
     es MONEY; en una hoja de presupuesto de obra (partida, precio unitario,
     medición...), precios, mediciones e importes son BUDGET_AMOUNT.  Una
     estadística sin personas ni presupuesto no se toca.
  5. Una formula que trae un dato de una columna personal (=A2,
     =LEFT(A2,1), =MAX(C2:C9)...) pasa a ser el marcador; las que solo cuentan
     se quedan, y la suma o la media de dos o mas importes de personas tambien:
     no son el dato de nadie, y se escriben con su valor, porque las celdas
     que sumaban ya llevan el marcador.  A todas se les quita el valor
     guardado (puede llevar el dato) y el libro se recalcula al abrirse.
Una columna cuyo tipo tiene la casilla desmarcada se queda como esta, con sus
formulas.
Las hojas ocultas se tratan igual que las visibles.

Lo que un Excel guarda aparte: fuera comentarios (tambien los encadenados y
sus personas), tablas dinamicas (su copia de los datos y su definicion; las
celdas que ocupaban quedan vacias con un marcador), copias de datos de los
graficos, vinculos a otros libros con su copia, conexiones y consultas,
nombres definidos con un valor fijo, hipervinculos, el usuario que comparte el
libro y las propiedades del documento.  Los nombres de hoja que son un dato
personal se cambian por "Hoja N" en todo el libro.
"""
from __future__ import annotations

import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from time import perf_counter

from lxml import etree

from app.core.errors import OfficeMacrosError, ProcessingError, UnsupportedDocumentError
from app.schemas.report import CONTENT_NOT_REVIEWED, CodedWarning, ProcessingReport
from app.services.office.barrido import barrer
from app.services.office.docx import _sin_etiqueta, marcador
from app.services.office.paquete import Paquete, limpiar_propiedades, rels_de, resolver, tipo_office, verificar
from app.services.office.verificador import revisar

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
TEXTO_LIBRE = "TEXTO_LIBRE"
DINAMICA_ELIMINADA = "[TABLA DINÁMICA ELIMINADA]"


def s(tag: str) -> str:
    return f"{{{S}}}{tag}"


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


#: Nombre de columna -> tipo.  Por orden: el primero que casa gana ("lugar de
#: nacimiento" antes que "nacimiento").
CABECERAS: list[tuple[str, list[str]]] = [
    ("PLACE_OF_BIRTH", ["lugar de nacimiento", "lloc de naixement"]),
    ("DATE_OF_BIRTH", ["fecha de nacimiento", "data de naixement", "f nacimiento", "nacimiento", "naixement",
                       "fecha nac", "f nac", "fec nac", "f naix", "data naix"]),
    ("ES_DNI", ["dni", "nif", "nie", "d n i", "n i f", "documento de identidad", "document d identitat",
                "num documento", "no documento", "n o doc", "no doc", "n doc", "numero documento",
                "doc identidad", "nif nie"]),
    ("CASE_NUMBER", ["expediente", "no expediente", "n expediente", "num expediente", "n o expediente",
                     "expedient", "expte", "no expte", "n expte"]),
    ("ES_NSS", ["seguridad social", "seguretat social", "nss", "naf"]),
    ("PASSPORT", ["pasaporte", "passaport"]),
    ("PHONE_NUMBER", ["telefono", "telefon", "tel", "tlf", "movil", "mobil"]),
    ("EMAIL_ADDRESS", ["correo", "correu", "email", "e mail", "mail"]),
    ("IBAN_CODE", ["iban", "cuenta bancaria", "compte bancari", "ccc", "cuenta corriente"]),
    ("ADDRESS", ["domicilio", "direccion", "domicili", "adreca", "direccio"]),
    ("POSTAL_CODE", ["codigo postal", "codi postal", "cp"]),
    ("LICENSE_PLATE", ["matricula"]),
    (TEXTO_LIBRE, ["observaciones", "observacions", "comentarios", "comentaris", "notas", "notes",
                   "descripcion", "descripcio", "motivo", "motiu", "asunto", "assumpte", "detalle",
                   "incidencias", "incidencies"]),
    ("PERSON", ["nombre", "nombres", "apellidos", "apellido", "nom", "cognoms", "cognom", "persona",
                "interesado", "interesada", "interessat", "solicitante", "sollicitant", "titular",
                "beneficiario", "beneficiaria", "beneficiari", "responsable", "alumno", "alumna", "alumne",
                "inscrito", "inscrita", "firmante", "socio", "socia", "soci"]),
]

#: Cabeceras de importe.  Solo cuentan en una hoja que trata de personas (MONEY)
#: o en un presupuesto de obra (BUDGET_AMOUNT), y con contenido numerico.
IMPORTES = ["importe", "importes", "import", "cuantia", "quantia", "cantidad", "quantitat", "total",
            "salario", "sueldo", "nomina", "ayuda", "ajuda", "subvencion", "subvencio", "cuota", "quota",
            "multa", "sancion", "sancio", "euros", "eur", "neto", "bruto", "liquido", "pago", "pagament",
            "abono", "precio", "preu", "medicion", "amidament", "unidades", "coste", "cost", "parcial",
            "presupuesto", "pressupost"]

_LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"


def tipo_por_cabecera(cabecera: str) -> str | None:
    n = _normal(cabecera)
    if not n:
        return None
    for tipo, claves in CABECERAS:
        if any(re.search(rf"(?:^| ){re.escape(k)}(?: |$)", n) for k in claves):
            return tipo
    return None


def es_cabecera_de_importe(cabecera: str) -> bool:
    n = _normal(cabecera)
    return bool(n) and any(re.search(rf"(?:^| ){k}(?: |$)", n) for k in IMPORTES)


def _es_importe(v: str) -> bool:
    return bool(re.fullmatch(r"-?\s*\d[\d.,\s]*(?:€|eur|euros)?", v.strip(), re.I))


#: Una cuenta repartida en columnas: cada trozo solo no parece nada; juntos, son
#: la cuenta.  Hace falta la columna de la cuenta y otra de entidad, oficina o DC:
#: "Entidad" sola es un organismo.
_TROZOS_DE_CUENTA = {"pais", "entidad", "banco", "oficina", "sucursal", "dc", "d c", "digito control",
                     "cuenta", "no cuenta", "n cuenta", "num cuenta", "numero cuenta", "n o cuenta"}
_CUENTA = {"cuenta", "no cuenta", "n cuenta", "num cuenta", "numero cuenta", "n o cuenta"}


def _cuenta_repartida(cabeceras: dict[int, str], datos: dict[int, list[str]]) -> list[int]:
    """Columnas que son trozos de una misma cuenta bancaria."""
    trozos = {col: _normal(c) for col, c in cabeceras.items() if _normal(c) in _TROZOS_DE_CUENTA}
    nombres = set(trozos.values())
    if not (nombres & _CUENTA and nombres & {"entidad", "banco", "oficina", "sucursal", "dc", "d c"}):
        return []
    salida = []
    for col, nombre in trozos.items():
        valores = [v.replace(" ", "") for v in datos.get(col, []) if v]
        patron = r"[A-Z]{2}\d{2}" if nombre == "pais" else r"\d{1,20}"
        if valores and sum(bool(re.fullmatch(patron, v)) for v in valores) >= 0.6 * len(valores):
            salida.append(col)
    return salida


def _ficha_vertical(hoja: "Hoja") -> dict[int, tuple[str, str]]:
    """Fila -> (tipo, rotulo) si la hoja es una ficha en vertical: los rotulos en
    la columna A ("Nombre", "DNI", "Teléfono"...) y cada persona en una columna.
    Hacen falta dos filas con rotulo y datos a su derecha, de dos tipos distintos
    como poco; que los rotulos sean casi toda la columna A (en un listado, la
    columna A tiene un rotulo arriba y datos debajo); y mas rotulos en la columna
    A que en la primera fila."""
    filas, textos_a = {}, 0
    for (f, col), c in hoja.celdas.items():
        if col == 1 and hoja.texto(c):
            textos_a += 1
            tipo = tipo_por_cabecera(hoja.texto(c) or "") if hoja.es_texto(c) else None
            if tipo and tipo != TEXTO_LIBRE:
                filas[f] = (tipo, hoja.texto(c))
    if len(filas) < 2 or len({t for t, _ in filas.values()}) < 2 or len(filas) < 0.6 * textos_a:
        return {}
    primera = min(f for f, _ in hoja.celdas)
    en_cabecera = sum(1 for (f, col), c in hoja.celdas.items()
                      if f == primera and col > 1 and hoja.es_texto(c) and tipo_por_cabecera(hoja.texto(c) or ""))
    con_datos = [f for f in filas if any(ff == f and cc > 1 for ff, cc in hoja.celdas)]
    return {f: filas[f] for f in con_datos} if len(con_datos) >= 2 and len(filas) > en_cabecera else {}


def _es_presupuesto(hoja: "Hoja") -> bool:
    """Una hoja de presupuesto de obra: las palabras con que el PDF lo reconoce."""
    from app.services.pii.analyzer import _BUDGET_VETO_RE

    textos = [hoja.texto(c) or "" for (f, _), c in hoja.celdas.items() if f <= 20 and hoja.es_texto(c)]
    return any(_BUDGET_VETO_RE.search(t) or _normal(t) == "partida" for t in textos)


def _es_dni(v: str) -> bool:
    v = v.upper().replace("-", "").replace(".", "").replace(" ", "")
    m = re.fullmatch(r"([XYZ]?)(\d{7,8})([A-Z])", v)
    if not m:
        return False
    numero = int({"X": "0", "Y": "1", "Z": "2"}.get(m.group(1), "") + m.group(2))
    return _LETRAS_DNI[numero % 23] == m.group(3)


def _es_telefono(v: str) -> bool:
    d = re.sub(r"[\s.\-()]", "", v)
    d = re.sub(r"^(\+34|0034)", "", d)
    if len(d) == 11 and d.startswith("34"):
        d = d[2:]                     # 34663418902: el prefijo sin "+"
    return bool(re.fullmatch(r"[6789]\d{8}", d))


_PARTICULAS = {"de", "del", "la", "las", "los", "y", "i", "e", "da", "dos", "van", "von"}


def _es_nombre(v: str) -> bool:
    from app.services.pii.gazetteer import given_names, normalize, surnames

    originales = re.findall(r"[^\W\d_]+", v)
    palabras = [normalize(p) for p in originales]
    if len(palabras) < 2 or len(palabras) > 6 or re.search(r"\d", v):
        return False
    # Un nombre va en mayusculas, salvo las particulas: "Ayuda al alquiler" no lo es.
    if any(p[0].islower() and p.lower() not in _PARTICULAS for p in originales):
        return False
    return any(p in given_names() for p in palabras) and any(p in surnames() for p in palabras[1:])


_POR_CONTENIDO = [
    ("ES_DNI", _es_dni),
    ("EMAIL_ADDRESS", lambda v: bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", v))),
    ("IBAN_CODE", lambda v: bool(re.fullmatch(r"ES\d{2}(\s?\d{4}){5}", v.strip().upper()))),
    ("PHONE_NUMBER", _es_telefono),
    ("PERSON", _es_nombre),
]


def tipo_por_contenido(valores: list[str]) -> str | None:
    valores = [v.strip() for v in valores if v and v.strip()]
    if len(valores) < 3:
        return None
    for tipo, prueba in _POR_CONTENIDO:
        if sum(1 for v in valores if prueba(v)) >= 0.6 * len(valores):
            return tipo
    largos = [v for v in valores if len(v) >= 30 and " " in v]
    if len(largos) >= 0.5 * len(valores):
        return TEXTO_LIBRE
    return None


# --- celdas ----------------------------------------------------------------------

def _col_a_num(letras: str) -> int:
    n = 0
    for c in letras:
        n = n * 26 + ord(c) - 64
    return n


def _ref(ref: str) -> tuple[int, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    return int(m.group(2)), _col_a_num(m.group(1))


class Hoja:
    def __init__(self, nombre: str, parte: str, raiz, compartidas: list[str]) -> None:
        self.nombre, self.parte, self.raiz = nombre, parte, raiz
        self.compartidas = compartidas
        self.celdas: dict[tuple[int, int], etree._Element] = {}
        for c in raiz.iter(s("c")):
            if c.get("r"):
                self.celdas[_ref(c.get("r"))] = c

    def texto(self, c) -> str | None:
        t = c.get("t")
        if t == "s":
            v = c.find(s("v"))
            return self.compartidas[int(v.text)] if v is not None and v.text else None
        if t == "inlineStr":
            return "".join(c.find(s("is")).itertext()) if c.find(s("is")) is not None else None
        v = c.find(s("v"))
        if v is None or v.text is None:
            return None
        if t in (None, "n"):
            try:
                f = float(v.text)
                return str(int(f)) if f.is_integer() else v.text
            except ValueError:
                return v.text
        return v.text

    def es_texto(self, c) -> bool:
        return c.get("t") in ("s", "inlineStr", "str") and c.find(s("f")) is None


def _poner_texto(c, texto: str) -> None:
    """La celda pasa a llevar *texto* (cadena en linea), sin formula ni valor."""
    for hijo in list(c):
        c.remove(hijo)
    c.set("t", "inlineStr")
    is_ = etree.SubElement(c, s("is"))
    t = etree.SubElement(is_, s("t"))
    t.text = texto
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _es_rotulo(texto: str) -> bool:
    return len(texto) <= 40 and "@" not in texto and sum(ch.isdigit() for ch in texto) <= 2


def _fila_cabecera(hoja: Hoja) -> int:
    """Numero de la fila de cabecera (0 si no hay).

    La primera fila con al menos dos rotulos (texto corto, sin arrobas y casi
    sin cifras) y datos debajo: un titulo encima tiene una sola celda y no
    cuenta.  Una palabra de cabecera en los datos ("Primer beneficiario",
    "...@correo.es") no la mueve.  Una fila de grupo encima ("Datos del
    solicitante | Contacto") tampoco: si la fila siguiente tiene mas nombres de
    columna conocidos, la cabecera es esa.  Si ninguna fila es asi, la que mas
    se parece a nombres de columna conocidos.
    """
    filas: dict[int, list] = {}
    for (f, col), c in hoja.celdas.items():
        if f <= 20:
            filas.setdefault(f, []).append(c)

    def de_rotulos(f: int) -> bool:
        llenas = [c for c in filas.get(f, []) if hoja.texto(c)]
        return (len(llenas) >= 2 and all(hoja.es_texto(c) and _es_rotulo(hoja.texto(c)) for c in llenas)
                and any(ff > f for ff in filas))

    def conocidos(f: int) -> int:
        return sum(1 for c in filas.get(f, []) if hoja.es_texto(c) and tipo_por_cabecera(hoja.texto(c) or ""))

    for f in sorted(filas):
        if de_rotulos(f):
            siguiente = min((ff for ff in filas if ff > f), default=None)
            if siguiente and de_rotulos(siguiente) and conocidos(siguiente) >= 2 and conocidos(siguiente) > conocidos(f):
                return siguiente
            return f
    mejor, puntos = 0, 0
    for f in sorted(filas):
        llenas = [hoja.texto(c) for c in filas[f] if hoja.texto(c)]
        if not llenas or sum(1 for t in llenas if _es_rotulo(t)) < 0.6 * len(llenas):
            continue
        # "Media por persona | =AVERAGE(...)" es una fila de datos, no una cabecera.
        if any(not hoja.es_texto(c) for c in filas[f] if hoja.texto(c) or c.find(s("f")) is not None):
            continue
        p = sum(1 for c in filas[f] if hoja.es_texto(c) and tipo_por_cabecera(hoja.texto(c) or ""))
        if p > puntos:
            mejor, puntos = f, p
    return mejor


# --- formulas --------------------------------------------------------------------

_REF_RE = re.compile(
    r"(?:(?:'((?:[^']|'')+)'|([A-Za-z_][\w.]*))!)?\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?")
# Lo que solo cuenta o mide no trae el dato.
_AGREGADOS = re.compile(r"\b(?:COUNTA?|COUNTBLANK|COUNTIFS?|ROWS|COLUMNS)\s*\([^()]*\)", re.I)
# La suma o la media de importes de personas: se queda, con su valor.
_SUMA_O_MEDIA = re.compile(r"\b(SUM|AVERAGE)\s*\(([^()]*)\)", re.I)


def _referencias(formula: str, hoja_actual: str) -> list[tuple[str, int, int, int, int]]:
    f = _AGREGADOS.sub("0", formula)
    f = re.sub(r'"[^"]*"', '""', f)
    salida = []
    for m in _REF_RE.finditer(f):
        hoja = (m.group(1) or "").replace("''", "'") or m.group(2) or hoja_actual
        c0, f0 = _col_a_num(m.group(3)), int(m.group(4))
        c1, f1 = (_col_a_num(m.group(5)), int(m.group(6))) if m.group(5) else (c0, f0)
        salida.append((hoja, f0, c0, f1, c1))
    return salida


def _totales_con_valor(formula: str, hoja_actual: str, personales, valores, con_formula) -> str:
    """Cada SUM() o AVERAGE() de dos o mas importes de personas (MONEY) pasa a
    ser su valor, calculado con los de antes de sustituir.  Si alguno sale de
    una formula, se deja: el resto de la formula decide."""
    def calcular(m):
        celdas = [(nombre, f, c)
                  for nombre, f0, c0, f1, c1 in _referencias(m.group(2), hoja_actual)
                  for f in range(f0, f1 + 1) for c in range(c0, c1 + 1)]
        if not celdas or any(personales.get((n, c), ("",))[0] != "MONEY" for n, _, c in celdas):
            return m.group(0)
        if any(clave in con_formula for clave in celdas):
            return m.group(0)
        numeros = [valores[clave] for clave in celdas if clave in valores]
        if len(numeros) < 2:
            return m.group(0)
        total = sum(numeros) if m.group(1).upper() == "SUM" else sum(numeros) / len(numeros)
        return f"{total:.15g}"
    return _SUMA_O_MEDIA.sub(calcular, formula)


# --- entrada ---------------------------------------------------------------------

def _abrir(entrada: Path) -> Paquete:
    tipo = tipo_office(entrada)
    if tipo == "xlsm":
        raise OfficeMacrosError("Excel workbook with macros: save it as .xlsx without macros.")
    if tipo != "xlsx":
        raise UnsupportedDocumentError("The file is not an Excel workbook.")
    return Paquete.abrir(entrada)


def _hojas(paq: Paquete) -> tuple[list[Hoja], list[str]]:
    compartidas = []
    sst = paq.xml("xl/sharedStrings.xml")
    if sst is not None:
        compartidas = ["".join(si.itertext()) for si in sst.findall(s("si"))]
    libro = paq.xml("xl/workbook.xml")
    destinos = paq.destinos("xl/workbook.xml")
    hojas = []
    for sh in libro.iter(s("sheet")):
        parte = destinos.get(sh.get(f"{{{R}}}id"))
        if parte and paq.xml(parte) is not None and paq.xml(parte).tag == s("worksheet"):
            hojas.append(Hoja(sh.get("name"), parte, paq.xml(parte), compartidas))
    return hojas, compartidas


def _limpiar_libro(paq: Paquete, hojas: list[Hoja], cuenta: Counter) -> None:
    """Lo que el libro guarda aparte del contenido de las celdas."""
    limpiar_propiedades(paq, cuenta)
    libro = paq.xml("xl/workbook.xml")
    for e in libro.findall(s("fileSharing")):
        cuenta["document_info"] += 1
        libro.remove(e)
    nombres = libro.find(s("definedNames"))
    if nombres is not None:
        for dn in list(nombres):
            if (dn.text or "").strip().startswith('"'):
                cuenta["defined_names"] += 1
                nombres.remove(dn)
        if not len(nombres):
            libro.remove(nombres)
    for tag in ("externalReferences", "pivotCaches"):
        for e in libro.findall(s(tag)):
            libro.remove(e)
    for nombre in paq.nombres("xl/externalLinks/"):
        cuenta["external_links"] += nombre.endswith(".xml") and "_rels" not in nombre
        paq.borrar(nombre)
    for nombre in ["xl/connections.xml"] + paq.nombres("xl/queryTables/"):
        if paq.borrar(nombre):
            cuenta["connections"] += 1
    for tabla in paq.nombres("xl/tables/"):
        raiz = paq.xml(tabla) if tabla.endswith(".xml") and "_rels" not in tabla else None
        if raiz is not None:
            for atributo in ("tableType", "connectionId"):
                raiz.attrib.pop(atributo, None)
            for col in raiz.iter(s("tableColumn")):
                col.attrib.pop("queryTableFieldId", None)
    # Comentarios: los clasicos (con su dibujo VML), los encadenados y sus personas.
    for nombre in paq.nombres("xl/"):
        if re.search(r"(^|/)comments\d*\.xml$|threadedComments|/persons/|comment\d*\.xml$", nombre):
            raiz = paq.xml(nombre)
            if raiz is not None and nombre.endswith(".xml"):
                cuenta["comments"] += sum(1 for e in raiz.iter() if isinstance(e.tag, str)
                                          and etree.QName(e).localname in ("comment", "threadedComment"))
            paq.borrar(nombre)
    for hoja in hojas:
        for tag in ("legacyDrawing", "hyperlinks"):
            for e in hoja.raiz.findall(s(tag)):
                if tag == "hyperlinks":
                    cuenta["links"] += len(e)
                hoja.raiz.remove(e)
    for nombre in paq.nombres("xl/drawings/"):
        if nombre.endswith(".vml"):
            paq.borrar(nombre)
    # Copias de datos de los graficos: se recalculan al abrir.
    for nombre in paq.nombres("xl/charts/"):
        raiz = paq.xml(nombre) if nombre.endswith(".xml") and "_rels" not in nombre else None
        if raiz is None:
            continue
        for e in list(raiz.iter(f"{{{C}}}strCache", f"{{{C}}}numCache", f"{{{C}}}multiLvlStrCache")):
            cuenta["chart_caches"] += 1
            e.getparent().remove(e)
    paq.borrar("xl/vbaProject.bin")


def _tablas_dinamicas(paq: Paquete, hojas: list[Hoja], cuenta: Counter) -> None:
    """Fuera las tablas dinamicas: su definicion, su copia de los datos y lo
    que ocupaban en la hoja (queda un marcador en la primera celda)."""
    for hoja in hojas:
        for parte in paq.destinos(hoja.parte).values():
            if not parte.startswith("xl/pivotTables/"):
                continue
            definicion = paq.xml(parte)
            loc = definicion.find(s("location")) if definicion is not None else None
            if loc is not None and loc.get("ref"):
                partes = loc.get("ref").split(":")
                (f0, c0), (f1, c1) = _ref(partes[0]), _ref(partes[-1])
                for (f, c), celda in list(hoja.celdas.items()):
                    if f0 <= f <= f1 and c0 <= c <= c1:
                        celda.getparent().remove(celda)
                        del hoja.celdas[(f, c)]
                fila = next((r for r in hoja.raiz.iter(s("row")) if r.get("r") == str(f0)), None)
                if fila is not None:
                    nueva = etree.SubElement(fila, s("c"))
                    nueva.set("r", partes[0])
                    _poner_texto(nueva, DINAMICA_ELIMINADA)
                    fila[:] = sorted(fila, key=lambda c: _ref(c.get("r"))[1])
            cuenta["pivot_tables"] += 1
    for nombre in paq.nombres("xl/pivotTables/") + paq.nombres("xl/pivotCache/"):
        paq.borrar(nombre)


def _imagenes(paq: Paquete, procesar, cuenta: Counter) -> list:
    from app.services.office.docx import _formato_imagen

    informes = []
    with tempfile.TemporaryDirectory() as tmp:
        for nombre in paq.nombres("xl/media/"):
            formato = _formato_imagen(paq.partes[nombre])
            if formato is None:
                continue
            e, sal = Path(tmp) / f"e.{formato}", Path(tmp) / f"s.{formato}"
            e.write_bytes(paq.partes[nombre])
            informes.append(procesar(e, sal, formato))
            paq.partes[nombre] = sal.read_bytes()
    # Imagenes que no se pueden revisar: fuera su anclaje.
    for dibujo in paq.nombres("xl/drawings/"):
        raiz = paq.xml(dibujo) if dibujo.endswith(".xml") and "_rels" not in dibujo else None
        if raiz is None:
            continue
        destinos = paq.destinos(dibujo)
        for blip in list(raiz.iter(f"{{{A}}}blip")):
            parte = destinos.get(blip.get(f"{{{R}}}embed"), "")
            if parte.startswith("xl/media/") and _formato_de_parte(paq, parte) is None:
                anclaje = next((a for a in blip.iterancestors() if etree.QName(a).namespace == XDR
                                and etree.QName(a).localname.endswith("Anchor")), None)
                if anclaje is not None and anclaje.getparent() is not None:
                    anclaje.getparent().remove(anclaje)
                    cuenta["images_removed"] += 1
    return informes


def _formato_de_parte(paq: Paquete, parte: str):
    from app.services.office.docx import _formato_imagen

    return _formato_imagen(paq.partes.get(parte, b""))


def _texto_suelto(paq: Paquete, analyzer, mode: str, tipos: Counter) -> None:
    """Cuadros de texto de los dibujos y cabeceras y pies de pagina."""
    for dibujo in paq.nombres("xl/drawings/"):
        raiz = paq.xml(dibujo) if dibujo.endswith(".xml") and "_rels" not in dibujo else None
        if raiz is None:
            continue
        for p in raiz.iter(f"{{{A}}}p"):
            ts = list(p.iter(f"{{{A}}}t"))
            texto = "".join(t.text or "" for t in ts)
            nuevo = _sustituir_en_texto(texto, analyzer, mode, tipos)
            if nuevo != texto and ts:
                ts[0].text = nuevo
                for t in ts[1:]:
                    t.text = ""


def spans_de(texto: str, analyzer, mode: str) -> list[tuple[int, int, str]]:
    """Los datos de *texto* (inicio, fin, tipo), sin la etiqueta y sin solapes."""
    from app.services.layout.zone_config import ALL_PII_TYPES

    if not texto or not texto.strip():
        return []
    spans = sorted(_sin_etiqueta(texto, e.start, e.end, e.entity_type)
                   for e in analyzer.analyze(texto, mode=mode, zone_type=None, context_text=texto)
                   if e.entity_type in set(ALL_PII_TYPES))
    limpios: list = []
    for sp in spans:
        if limpios and sp[0] < limpios[-1][1]:
            continue
        limpios.append(sp)
    return limpios


def _sustituir_en_texto(texto: str, analyzer, mode: str, tipos: Counter) -> str:
    for a, b, tipo in reversed(spans_de(texto, analyzer, mode)):
        texto = texto[:a] + marcador(tipo) + texto[b:]
        tipos[tipo] += 1
    return texto


def _renombrar_hojas(paq: Paquete, hojas: list[Hoja], analyzer, mode: str) -> None:
    """Un nombre de hoja que es un dato personal ("Juan Garcia") pasa a "Hoja N"
    en todo el libro: pestaña, formulas y nombres definidos."""
    libro = paq.xml("xl/workbook.xml")
    cambios = {}
    for n, (hoja, sh) in enumerate(zip(hojas, libro.iter(s("sheet"))), 1):
        if _sustituir_en_texto(hoja.nombre, analyzer, mode, Counter()) != hoja.nombre:
            cambios[hoja.nombre] = f"Hoja {n}"
            sh.set("name", cambios[hoja.nombre])
            hoja.nombre = cambios[hoja.nombre]
    if not cambios:
        return

    def cambiar(f: str) -> str:
        for viejo, nuevo in cambios.items():
            f = f.replace(f"'{viejo.replace(chr(39), chr(39) * 2)}'!", f"'{nuevo}'!").replace(f"{viejo}!", f"'{nuevo}'!")
        return f

    for hoja in hojas:
        for f in hoja.raiz.iter(s("f")):
            f.text = cambiar(f.text or "")
    for dn in libro.iter(s("definedName")):
        dn.text = cambiar(dn.text or "")
    app = paq.xml("docProps/app.xml")
    if app is not None:
        for e in app.iter():
            if isinstance(e.tag, str) and e.text in cambios:
                e.text = cambios[e.text]


def _compartidas_usadas(paq: Paquete, hojas: list[Hoja]) -> None:
    """La tabla de cadenas compartidas, solo con las que siguen en uso: las
    sustituidas no pueden quedarse ahi."""
    sst = paq.xml("xl/sharedStrings.xml")
    if sst is None:
        return
    viejas = sst.findall(s("si"))
    nuevo_indice: dict[int, int] = {}
    for hoja in hojas:
        for c in hoja.raiz.iter(s("c")):
            if c.get("t") == "s":
                v = c.find(s("v"))
                i = int(v.text)
                if i not in nuevo_indice:
                    nuevo_indice[i] = len(nuevo_indice)
                v.text = str(nuevo_indice[i])
    for si in viejas:
        sst.remove(si)
    for viejo, _ in sorted(nuevo_indice.items(), key=lambda x: x[1]):
        sst.append(viejas[viejo])
    sst.set("count", str(len(nuevo_indice)))
    sst.set("uniqueCount", str(len(nuevo_indice)))


def _es_valor_de_celda(parte: str, e) -> bool:
    """El valor de una celda (no su formula): ya se ha tratado por columnas."""
    return e.tag in (s("v"), s("t")) and any(a.tag == s("c") for a in e.iterancestors())


def anonimizar_xlsx(entrada: Path, salida: Path, analyzer, procesar_imagen, job_id: str,
                    mode: str = "strict") -> ProcessingReport:
    empezado = perf_counter()
    paq = _abrir(Path(entrada))
    hojas, _ = _hojas(paq)
    cuenta: Counter = Counter()
    tipos: Counter = Counter()
    _limpiar_libro(paq, hojas, cuenta)
    _tablas_dinamicas(paq, hojas, cuenta)
    informes_imagen = _imagenes(paq, procesar_imagen, cuenta)
    _renombrar_hojas(paq, hojas, analyzer, mode)

    # Valores numericos de antes de sustituir, para los totales que se quedan.
    valores: dict[tuple[str, int, int], float] = {}
    con_formula: set[tuple[str, int, int]] = set()
    for hoja in hojas:
        for (f, col), c in hoja.celdas.items():
            v = c.find(s("v"))
            if c.find(s("f")) is not None:
                con_formula.add((hoja.nombre, f, col))
            elif c.get("t") in (None, "n") and v is not None and v.text:
                try:
                    valores[(hoja.nombre, f, col)] = float(v.text)
                except ValueError:
                    pass

    # Clasificar columnas.
    personales: dict[tuple[str, int], tuple[str, int]] = {}      # (hoja, col) -> (tipo, fila cabecera)
    por_filas: dict[tuple[str, int], str] = {}                     # (hoja, fila) -> tipo (ficha vertical)
    columnas_info = []
    for hoja in hojas:
        vertical = _ficha_vertical(hoja)
        if vertical:
            for f, (tipo, rotulo) in vertical.items():
                if tipo not in analyzer.disabled_types:
                    por_filas[(hoja.nombre, f)] = tipo
                    columnas_info.append({"sheet": hoja.nombre, "header": rotulo, "type": tipo, "cells": 0})
            continue
        cab = _fila_cabecera(hoja)
        cols = sorted({c for (_, c) in hoja.celdas})
        clasificadas, importes = {}, []
        for col in cols:
            celda_cab = hoja.celdas.get((cab, col)) if cab else None
            cabecera = hoja.texto(celda_cab) if celda_cab is not None else None
            celdas = [c for (f, cc), c in hoja.celdas.items() if cc == col and f > cab]
            datos = [hoja.texto(c) for c in celdas if c.find(s("f")) is None]
            tipo = (tipo_por_cabecera(cabecera) if cabecera else None) or tipo_por_contenido(
                [d for d in datos if d])
            if tipo:
                clasificadas[col] = (tipo, cabecera)
            elif cabecera and es_cabecera_de_importe(cabecera) and _normal(cabecera) not in _TROZOS_DE_CUENTA:
                llenas = [d for d in datos if d]
                numericas = [d for d in llenas if _es_importe(d)]
                formulas = len(celdas) - len(datos)
                if llenas or formulas:
                    if len(numericas) + formulas >= 0.6 * (len(llenas) + formulas):
                        importes.append((col, cabecera))
        sin_clasificar = {col: hoja.texto(hoja.celdas[(cab, col)]) or "" for col in cols
                          if col not in clasificadas and cab and (cab, col) in hoja.celdas}
        datos_col = {col: [hoja.texto(c) for (f, cc), c in hoja.celdas.items() if cc == col and f > cab]
                     for col in sin_clasificar}
        for col in _cuenta_repartida(sin_clasificar, datos_col):
            clasificadas[col] = ("IBAN_CODE", sin_clasificar[col])
        if importes:
            # Un importe es dato si la hoja es un presupuesto de obra o trata de personas.
            if _es_presupuesto(hoja):
                clasificadas.update({col: ("BUDGET_AMOUNT", cabecera) for col, cabecera in importes})
            elif any(t not in (TEXTO_LIBRE, "CASE_NUMBER") for t, _ in clasificadas.values()):
                clasificadas.update({col: ("MONEY", cabecera) for col, cabecera in importes})
        for col, (tipo, cabecera) in sorted(clasificadas.items()):
            if tipo in analyzer.disabled_types:
                # Casilla desmarcada: la columna se queda, y sus formulas tambien.
                continue
            personales[(hoja.nombre, col)] = (tipo, cab)
            if tipo != TEXTO_LIBRE:
                columnas_info.append({"sheet": hoja.nombre, "header": cabecera or "", "type": tipo, "cells": 0})

    # Sustituir.
    valores_quitados: set[str] = set()
    for hoja in hojas:
        for (f, col), c in hoja.celdas.items():
            if col > 1 and (hoja.nombre, f) in por_filas:
                tipo = por_filas[(hoja.nombre, f)]
                if hoja.texto(c) and c.find(s("f")) is None:
                    if not re.fullmatch(r"\d{1,5}", hoja.texto(c).strip()):
                        valores_quitados.add(hoja.texto(c))
                    _poner_texto(c, marcador(tipo))
                    tipos[tipo] += 1
                    next(i for i in columnas_info if i["sheet"] == hoja.nombre and i["type"] == tipo)["cells"] += 1
                continue
            tipo, cab = personales.get((hoja.nombre, col), (None, 0))
            if f <= cab:
                continue
            texto = hoja.texto(c)
            if tipo and tipo != TEXTO_LIBRE and texto and c.find(s("f")) is None:
                if tipo not in ("MONEY", "BUDGET_AMOUNT") and not re.fullmatch(r"\d{1,5}", texto.strip()):
                    # Un importe ("18.5") o un numero corto (la entidad de una
                    # cuenta, "2100") puede estar en cualquier parte del XML.
                    valores_quitados.add(texto)
                _poner_texto(c, marcador(tipo))
                tipos[tipo] += 1
                info = next(i for i in columnas_info if i["sheet"] == hoja.nombre
                            and i["type"] == tipo and (i["header"] == (hoja.texto(hoja.celdas[(cab, col)])
                                                                       if cab and (cab, col) in hoja.celdas else "")))
                info["cells"] += 1
            elif texto and hoja.es_texto(c) and (tipo == TEXTO_LIBRE or (len(texto) >= 25 and " " in texto)):
                nuevo = _sustituir_en_texto(texto, analyzer, mode, tipos)
                if nuevo != texto:
                    valores_quitados.add(texto)
                    _poner_texto(c, nuevo)

    # Formulas: las que traen un dato personal pasan a ser el marcador; a todas
    # se les quita el valor guardado.
    nombres_hoja = {h.nombre for h in hojas}
    for hoja in hojas:
        for (f, col), c in hoja.celdas.items():
            formula = c.find(s("f"))
            if formula is None:
                continue
            formula.text = _totales_con_valor(formula.text or "", hoja.nombre, personales, valores, con_formula)
            trae = None
            for nombre, f0, c0, f1, c1 in _referencias(formula.text or "", hoja.nombre):
                if nombre not in nombres_hoja:
                    continue
                for cc in range(c0, c1 + 1):
                    tipo, cab = personales.get((nombre, cc), (None, 0))
                    if tipo and f1 > cab:
                        trae = tipo
                if c1 > 1:
                    # Una fila de una ficha en vertical.
                    for ff in range(f0, min(f1, f0 + 1000) + 1):
                        trae = por_filas.get((nombre, ff), trae)
            if trae:
                _poner_texto(c, marcador(trae if trae != TEXTO_LIBRE else "DATO"))
                tipos[trae] += trae != TEXTO_LIBRE
            else:
                v = c.find(s("v"))
                if v is not None:
                    c.remove(v)
                if c.get("t") == "str":
                    del c.attrib["t"]
    calc = paq.xml("xl/workbook.xml").find(s("calcPr"))
    if calc is None:
        calc = etree.SubElement(paq.xml("xl/workbook.xml"), s("calcPr"))
    calc.set("fullCalcOnLoad", "1")

    # Cabeceras y pies de pagina, cuadros de texto.
    for hoja in hojas:
        for e in hoja.raiz.iter(s("oddHeader"), s("oddFooter"), s("evenHeader"), s("evenFooter"),
                                s("firstHeader"), s("firstFooter")):
            e.text = _sustituir_en_texto(e.text or "", analyzer, mode, tipos)
    _texto_suelto(paq, analyzer, mode, tipos)
    # Lo que no es una celda: validaciones, formato condicional, filtros, titulos
    # de graficos, nombres definidos, textos dentro de formulas...
    barrer(paq, analyzer, mode, tipos, _es_valor_de_celda)
    _compartidas_usadas(paq, hojas)

    paq.guardar(Path(salida))
    _comprobar_que_se_abre(Path(salida))
    quedan = verificar(Path(salida), [v for v in valores_quitados if len(v.strip()) >= 4])
    for r in informes_imagen:
        for tipo, n in (getattr(r, "entities_by_type", None) or {}).items():
            tipos[tipo] += n
    residuales, avisos, motivos = revisar(Path(salida), analyzer, mode, quedan)
    return ProcessingReport(
        job_id=job_id,
        status="manual_review_required" if residuales else "completed",
        document_type="xlsx", pages=len(hojas),
        entities_detected=sum(tipos.values()), entities_redacted=sum(tipos.values()),
        entities_by_type=dict(tipos), residual_entities=residuales,
        manual_review_required=bool(residuales), manual_review_reasons=motivos, warnings=avisos,
        metadata_removed={k: v for k, v in cuenta.items() if v},
        replaced_columns=[i for i in columnas_info if i["cells"]],
        redaction_strategy="xlsx_column_replacement",
        processing_ms=int((perf_counter() - empezado) * 1000),
    )


def solo_metadatos_xlsx(entrada: Path, salida: Path, job_id: str = "") -> ProcessingReport:
    from app.services.image.imagen import solo_metadatos_imagen

    empezado = perf_counter()
    paq = _abrir(Path(entrada))
    hojas, _ = _hojas(paq)
    cuenta: Counter = Counter()
    _limpiar_libro(paq, hojas, cuenta)
    _imagenes(paq, lambda e, s_, f: solo_metadatos_imagen(e, s_, f), cuenta)
    paq.guardar(Path(salida))
    _comprobar_que_se_abre(Path(salida))
    return ProcessingReport(
        job_id=job_id, status="completed_with_warnings", document_type="xlsx", treatment="metadata_only",
        pages=len(hojas), warnings=["Metadata removed only: the content has NOT been reviewed or anonymized."],
        coded_warnings=[CodedWarning(code=CONTENT_NOT_REVIEWED)],
        metadata_removed={k: v for k, v in cuenta.items() if v},
        redaction_strategy="metadata_only", processing_ms=int((perf_counter() - empezado) * 1000),
    )


def _comprobar_que_se_abre(ruta: Path) -> None:
    import zipfile

    import openpyxl

    try:
        with zipfile.ZipFile(ruta) as z:
            if z.testzip() is not None:
                raise ValueError("zip dañado")
        openpyxl.load_workbook(ruta)
    except Exception as exc:
        raise ProcessingError(f"The anonymized workbook could not be reopened: {exc}") from exc
