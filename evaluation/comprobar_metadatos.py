"""Comprobacion independiente de metadatos de un PDF de salida, con ExifTool y qpdf.

No usa el codigo del servicio ni PyMuPDF: mira el fichero con dos herramientas
ajenas, que son las que propuso el ayuntamiento para verificarlo.  Dice que
queda de lo que no deberia estar en un PDF publicado:

  - diccionario de informacion (autor, creador, productor, fechas, titulo...)
  - XMP del documento, de las paginas y de las imagenes
  - anotaciones (tipo y si llevan autor), campos de formulario, marcadores
  - JavaScript (apertura, arbol de nombres, acciones) y adjuntos
  - miniaturas de pagina
  - EXIF, GPS, XMP, IPTC, comentarios y marcas del codificador dentro de las
    imagenes (el perfil de color y la marca de Adobe se conservan a proposito)
  - copias de texto: /ActualText del contenido marcado y estructura de etiquetas
  - versiones anteriores por guardado incremental

Solo imprime nombres de campo y recuentos, nunca valores: sirve tambien para los
documentos reales.

Uso:
    python -m evaluation.comprobar_metadatos salida.pdf [otra.pdf ...]

Busca las herramientas en las variables EXIFTOOL y QPDF o en el PATH.  Si falta
alguna, lo dice y termina sin comprobar (codigo 0): no son dependencias del
servicio.  Con las herramientas, el codigo es 1 si queda algo y 0 si no.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Grupos de ExifTool dentro de una imagen que no deberian quedar.
_GRUPOS_IMAGEN = {
    "IFD0": "EXIF", "IFD1": "EXIF (miniatura)", "ExifIFD": "EXIF", "InteropIFD": "EXIF",
    "GPS": "GPS", "MakerNotes": "notas del fabricante", "IPTC": "IPTC",
    "Photoshop": "Photoshop", "Ducky": "Ducky", "JFIF": "JFIF", "JFXX": "JFIF (miniatura)",
    "Comment": "comentario",
}
# Etiquetas del PDF que no son metadatos guardados por nadie (las calcula ExifTool).
_PDF_NO_METADATO = {"PDFVersion", "Linearized", "PageCount", "Encryption", "HasXFA",
                    "EmbeddedImage", "EmbeddedImageWidth", "EmbeddedImageHeight",
                    "EmbeddedImageFilter", "EmbeddedImageColorSpace", "Language", "PageLayout",
                    "PageMode"}


def herramientas() -> tuple[str, str] | None:
    exiftool = os.environ.get("EXIFTOOL") or shutil.which("exiftool")
    qpdf = os.environ.get("QPDF") or shutil.which("qpdf")
    if not exiftool or not qpdf:
        return None
    return exiftool, qpdf


def _exif(exiftool: str, ruta: Path) -> dict:
    r = subprocess.run([exiftool, "-j", "-G1", "-a", "-u", str(ruta)], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8", "replace"))[0]
    except (ValueError, IndexError):
        return {}


def _objetos(qpdf: str, ruta: Path) -> tuple[dict, dict]:
    r = subprocess.run([qpdf, "--json=2", "--json-key=qpdf", "--json-stream-data=none", str(ruta)],
                       capture_output=True)
    datos = json.loads(r.stdout.decode("utf-8", "replace"))["qpdf"][1]
    objetos, trailer = {}, {}
    for clave, valor in datos.items():
        if clave.startswith("obj:"):
            objetos[clave[4:]] = valor
        elif clave == "trailer":
            trailer = valor.get("value", {})
    return objetos, trailer


def _dic(objeto: dict) -> dict:
    if "stream" in objeto:
        return objeto["stream"].get("dict", {})
    valor = objeto.get("value")
    return valor if isinstance(valor, dict) else {}


def comprobar(ruta: Path, exiftool: str, qpdf: str) -> list[str]:
    """Lo que queda en el PDF, en frases cortas.  Lista vacia = limpio."""
    ruta = Path(ruta)
    queda: list[str] = []

    # 1. Diccionario de informacion y XMP del documento (ExifTool)
    ex = _exif(exiftool, ruta)
    info = sorted(k.split(":", 1)[1] for k in ex
                  if k.startswith("PDF:") and k.split(":", 1)[1] not in _PDF_NO_METADATO)
    if info:
        queda.append("diccionario de informacion: " + ", ".join(info))
    xmp = sorted({k.split(":")[0] for k in ex if k.startswith("XMP")})
    if xmp:
        queda.append("XMP: " + ", ".join(xmp))

    # 2. Estructura (qpdf)
    objetos, trailer = _objetos(qpdf, ruta)
    raiz = _dic(objetos.get(trailer.get("/Root", ""), {}))
    for clave, nombre in (("/OpenAction", "accion de apertura"), ("/AA", "acciones del documento"),
                          ("/AcroForm", "formulario"), ("/Outlines", "marcadores"),
                          ("/Metadata", "XMP del documento")):
        if clave in raiz:
            queda.append(f"catalogo: {nombre}")
    nombres = raiz.get("/Names")
    nombres = _dic(objetos.get(nombres, {})) if isinstance(nombres, str) else (nombres or {})
    for clave, nombre in (("/JavaScript", "JavaScript"), ("/EmbeddedFiles", "adjuntos")):
        if clave in nombres:
            queda.append(f"arbol de nombres: {nombre}")

    anotaciones: dict[str, int] = {}
    con_autor = js = adjuntos = paginas_xmp = miniaturas = imagenes_xmp = 0
    imagenes = []
    volcado = json.dumps(objetos)
    js = volcado.count('"/JS"')
    for ref, objeto in objetos.items():
        d = _dic(objeto)
        tipo, sub = d.get("/Type"), d.get("/Subtype")
        if tipo == "/Page":
            paginas_xmp += "/Metadata" in d
            miniaturas += "/Thumb" in d
            lista = d.get("/Annots", [])
            if isinstance(lista, str):
                lista = objetos.get(lista, {}).get("value", [])
            for a in lista:
                da = _dic(objetos.get(a, {})) if isinstance(a, str) else (a or {})
                st = da.get("/Subtype", "?")
                anotaciones[st] = anotaciones.get(st, 0) + 1
                con_autor += "/T" in da and st != "/Widget"
        if tipo == "/EmbeddedFile":
            adjuntos += 1
        if sub == "/Image":
            imagenes.append((ref, d))
            imagenes_xmp += "/Metadata" in d
    if anotaciones:
        queda.append("anotaciones: " + ", ".join(f"{t.lstrip('/')} x{n}" for t, n in sorted(anotaciones.items()))
                     + (f" ({con_autor} con autor)" if con_autor else ""))
    if js:
        queda.append(f"JavaScript en {js} objeto(s)")
    if adjuntos:
        queda.append(f"{adjuntos} fichero(s) adjunto(s)")
    if paginas_xmp:
        queda.append(f"XMP en {paginas_xmp} pagina(s)")
    if miniaturas:
        queda.append(f"miniatura en {miniaturas} pagina(s)")
    if imagenes_xmp:
        queda.append(f"XMP en {imagenes_xmp} objeto(s) de imagen")

    # 3. Dentro de cada imagen, tal como esta guardada (sin decodificar)
    encontrados: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for ref, _d in imagenes:
            numero = ref.split()[0]
            crudo = subprocess.run([qpdf, f"--show-object={numero}", "--raw-stream-data", str(ruta)],
                                   capture_output=True).stdout
            img = Path(tmp) / f"{numero}.bin"
            img.write_bytes(crudo)
            grupos = set()
            for clave in _exif(exiftool, img):
                grupo = clave.split(":")[0]
                if grupo.startswith("XMP"):
                    grupos.add("XMP")
                elif grupo in _GRUPOS_IMAGEN:
                    grupos.add(_GRUPOS_IMAGEN[grupo])
                elif clave == "File:Comment":
                    grupos.add("comentario")
            for g in grupos:
                encontrados[g] = encontrados.get(g, 0) + 1
    if encontrados:
        queda.append("dentro de las imagenes: " + ", ".join(f"{g} ({n})" for g, n in sorted(encontrados.items())))

    # 4. Copias de texto: /ActualText en el contenido marcado y estructura de etiquetas
    if "/StructTreeRoot" in raiz:
        queda.append("catalogo: estructura de etiquetas")
    with tempfile.TemporaryDirectory() as tmp:
        qdf = Path(tmp) / "q.pdf"
        subprocess.run([qpdf, "--qdf", "--object-streams=disable", str(ruta), str(qdf)],
                       capture_output=True)
        if qdf.exists():
            n = qdf.read_bytes().count(b"/ActualText")
            if n:
                queda.append(f"ActualText (copias de texto) x{n}")

    # 5. Versiones anteriores
    crudo = ruta.read_bytes()
    revisiones = crudo.count(b"%%EOF")
    if revisiones > 1 or re.search(rb"/Prev\s+\d+", crudo):
        queda.append(f"guardado incremental: {revisiones} revisiones")
    return queda


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    rutas = herramientas()
    if rutas is None:
        print("SALTADO: faltan ExifTool o qpdf (variables EXIFTOOL y QPDF, o en el PATH).")
        return 0
    exiftool, qpdf = rutas
    algo = False
    for ruta in argv:
        queda = comprobar(Path(ruta), exiftool, qpdf)
        print(f"{ruta}: {'limpio' if not queda else 'QUEDA'}")
        for linea in queda:
            print(f"  - {linea}")
        algo = algo or bool(queda)
    return 1 if algo else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
