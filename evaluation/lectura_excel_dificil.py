"""Medicion del corpus de casos dificiles de Excel (corpus_excel_dificil.py).

Cada caso se sube por la API de verdad (POST /jobs, en proceso), con las
casillas del catalogo, y se espera a que termine (como mucho TIMEOUT
segundos: si no, CUELGUE).  Despues de cada uno se comprueba que el servicio
sigue respondiendo (/health).

Del fichero de salida se mira:
  - FUGA: algun dato sigue en alguna parte del fichero.  La busqueda es mas
    dura que el verificador del servicio: todas las entradas del zip (tambien
    las repetidas), el texto con y sin etiquetas, en UTF-8 y UTF-16, dentro de
    los bloques base64 y de los zip que haya dentro, sin mayusculas, acentos
    ni separadores (un DNI partido en tramos se junta).  Se separa lo que se VE
    (valor o formula de una celda, en cualquier hoja) de lo que es
    RECUPERABLE (esta en el fichero pero no en una celda).  Las imagenes de
    dentro: miniaturas y otras imagenes escondidas en el JPEG, y los
    metadatos que lee ExifTool.
  - SOBRE-REDACCION: algun texto legitimo ya no esta.
  - ERROR: lo que se tenia que procesar se rechaza o falla, o un rechazo
    esperado llega con otro codigo o sin codigo.
El verificador del propio servicio (paquete.verificar) se anota aparte.

Uso: python -m evaluation.lectura_excel_dificil [--solo ID]
"""
from __future__ import annotations

import base64
import binascii
import html
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path

CORPUS = Path("evaluation/excel_dificil/corpus")
SALIDAS = Path("evaluation/excel_dificil/salidas")
RESULTADOS = Path("evaluation/excel_dificil/resultados.json")
TIMEOUT = 600
CORRECTO, FUGA, SOBRE, ERROR, CUELGUE = "correcto", "fuga", "sobre-redaccion", "error", "cuelgue"


# --- busqueda de datos ------------------------------------------------------------

def compacto(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^0-9a-z]+", "", t)


def _textos_de_bytes(datos: bytes, profundidad: int = 0) -> list[str]:
    """Todo el texto que se puede sacar de unos bytes, de varias formas."""
    if profundidad > 3:
        return []
    textos = []
    if datos[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                for info in z.infolist():
                    textos += _textos_de_bytes(z.read(info), profundidad + 1)
            return textos
        except (zipfile.BadZipFile, ValueError, OSError):
            pass
    for codificacion in ("utf-8", "utf-16-le", "utf-16"):
        if codificacion != "utf-8" and b"\x00" not in datos[:4096]:
            continue
        s = html.unescape(datos.decode(codificacion, "ignore"))      # &#243; -> ó
        textos += [s, re.sub(r"<[^>]+>", "", s)]
        for bloque in re.findall(r"[A-Za-z0-9+/=\r\n]{200,}", s):
            try:
                textos += _textos_de_bytes(base64.b64decode(bloque), profundidad + 1)
            except (binascii.Error, ValueError):
                pass
    return textos


def partes_del_zip(ruta: Path) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(ruta) as z:
        return [(i.filename, z.read(i)) for i in z.infolist()]


def donde_esta(partes: list[tuple[str, bytes]], datos: list[dict]) -> dict[str, list[str]]:
    """dato -> partes del fichero donde sigue."""
    compactos = [(n, [compacto(t) for t in _textos_de_bytes(b)]) for n, b in partes]
    salida = {}
    for d in datos:
        aguja = compacto(d["text"])
        if len(aguja) < 5:
            continue
        en = [n for n, ts in compactos if any(aguja in t for t in ts)]
        if en:
            salida[d["text"]] = en
    return salida


def visible(ruta: Path) -> str:
    """Valores y formulas de todas las celdas de todas las hojas."""
    import openpyxl

    wb = openpyxl.load_workbook(ruta)
    trozos = []
    for ws in wb.worksheets:
        for fila in ws.iter_rows():
            trozos += [str(c.value) for c in fila if c.value is not None]
    return compacto(" ".join(trozos))


def imagenes_escondidas(datos: bytes) -> list[str]:
    """Otras imagenes metidas dentro de un JPEG (miniatura EXIF, de Photoshop,
    imagenes extra al final): cada inicio de JPEG despues del primero que se
    descodifica como imagen."""
    from PIL import Image

    salida = []
    for m in re.finditer(rb"\xff\xd8\xff", datos):
        if m.start() == 0:
            continue
        try:
            with Image.open(io.BytesIO(datos[m.start():])) as img:
                img.load()
                if img.width >= 48 and img.height >= 48:
                    salida.append(f"{img.width}x{img.height} en el byte {m.start()}")
        except Exception:
            pass
    return salida


def metadatos_exiftool(datos: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "m.jpg"
        ruta.write_bytes(datos)
        r = subprocess.run(["exiftool", "-j", "-a", "-G1", "-u", str(ruta)], capture_output=True)
    return r.stdout.decode("utf-8", "ignore")


# --- servicio ---------------------------------------------------------------------

def subir(client, caso: dict, corpus: Path = CORPUS, salidas: Path = SALIDAS) -> dict:
    entrada = corpus / caso["fichero"]
    empezado = time.time()
    r = client.post("/jobs", files={"file": (caso["nombre_subida"], entrada.read_bytes(),
                                             "application/octet-stream")})
    if r.status_code != 202:
        try:
            codigo = r.json()["detail"]["code"]
        except Exception:
            codigo = None
        return {"estado": "rechazado", "http": r.status_code, "codigo": codigo, "segundos": 0}
    job = r.json()["job_id"]
    while time.time() - empezado < TIMEOUT:
        e = client.get(f"/jobs/{job}").json()
        if e["status"] in ("completed", "failed", "expired"):
            break
        time.sleep(0.5)
    else:
        return {"estado": "colgado", "segundos": round(time.time() - empezado)}
    segundos = round(time.time() - empezado, 1)
    if e["status"] != "completed":
        return {"estado": "fallido", "codigo": (e.get("error") or {}).get("code"), "segundos": segundos}
    descarga = client.get(f"/jobs/{job}/result")
    nombre = re.search(r'filename="?([^";]+)', descarga.headers.get("content-disposition", ""))
    extension = Path(nombre.group(1)).suffix if nombre else ".bin"
    salida = salidas / f"{caso['id']}{extension}"
    salida.write_bytes(descarga.content)
    return {"estado": "completado", "salida": str(salida), "extension": extension, "segundos": segundos,
            "informe": client.get(f"/jobs/{job}/report").json()}


def medir(caso: dict, r: dict, lector=None) -> dict:
    from app.services.office.paquete import verificar

    m = {"id": caso["id"], "que_prueba": caso["que_prueba"], "esperado": caso["esperado"],
         "estado": r["estado"], "codigo": r.get("codigo"), "segundos": r.get("segundos"),
         "fugas_visibles": {}, "fugas_recuperables": {}, "legitimos_perdidos": [], "notas": []}
    entrada = CORPUS / caso["fichero"]
    if zipfile.is_zipfile(entrada) and caso["datos"]:
        faltan = [d["text"] for d in caso["datos"]
                  if not donde_esta(partes_del_zip(entrada), [d]) and len(compacto(d["text"])) >= 5]
        if faltan:
            m["notas"].append(f"datos que no estaban en la entrada (caso mal construido): {faltan}")

    if r["estado"] == "colgado":
        m["resultado"] = CUELGUE
        return m
    if caso["rechazo"]:
        bien = r["estado"] in ("rechazado", "fallido") and r.get("codigo") == caso["rechazo"]
        m["resultado"] = CORRECTO if bien else ERROR
        if not bien:
            m["notas"].append(f"se esperaba el rechazo {caso['rechazo']}")
        return m
    if r["estado"] != "completado":
        m["resultado"] = ERROR
        return m

    salida = Path(r["salida"])
    m["extension_salida"] = r["extension"]
    try:
        vis = visible(salida)
    except Exception as exc:
        m["resultado"] = ERROR
        m["notas"].append(f"la salida no se abre: {exc}")
        return m
    partes = partes_del_zip(salida)
    for dato, en in donde_esta(partes, caso["datos"]).items():
        (m["fugas_visibles"] if compacto(dato) in vis else m["fugas_recuperables"])[dato] = en
    todo = " ".join(compacto(t) for _, b in partes for t in _textos_de_bytes(b))
    m["legitimos_perdidos"] = [t for t in caso["legitimos"] if compacto(t) not in vis and compacto(t) not in todo]
    m["verificador_servicio"] = sorted({v for _, v in verificar(salida, [d["text"] for d in caso["datos"]])})
    informe = r.get("informe") or {}
    m["estado_informe"] = informe.get("status")
    m["columnas_sustituidas"] = [f"{c['sheet']}!{c['header']}={c['type']}" for c in informe.get("replaced_columns", [])]

    for nombre, datos in partes:
        if nombre.startswith("xl/media/") and datos[:3] == b"\xff\xd8\xff":
            escondidas = imagenes_escondidas(datos)
            if escondidas:
                m["fugas_recuperables"][f"imagen escondida en {nombre}"] = escondidas
            meta = compacto(metadatos_exiftool(datos))
            for d in caso["datos"]:
                if len(compacto(d["text"])) >= 5 and compacto(d["text"]) in meta:
                    m["fugas_recuperables"].setdefault(d["text"], []).append(f"{nombre} (metadatos)")
            if lector is not None:
                m["notas"].append("lectura de la imagen: " + _leer_imagen(lector, datos, caso))
    if extension_esperada(caso) and r["extension"] != extension_esperada(caso):
        m["notas"].append(f"vuelve como {r['extension']}, se esperaba {extension_esperada(caso)}")
    m["resultado"] = (FUGA if m["fugas_visibles"] or m["fugas_recuperables"]
                      else SOBRE if m["legitimos_perdidos"] else CORRECTO)
    return m


def extension_esperada(caso: dict) -> str | None:
    return ".xlsx" if caso["fichero"].endswith(".xlsx") and not caso["rechazo"] else None


def _leer_imagen(lector, datos: bytes, caso: dict) -> str:
    """Que datos del formulario de la foto se siguen leyendo en la imagen de salida."""
    import numpy as np
    from PIL import Image

    from evaluation.corpus_excel_dificil import P
    from evaluation.lectura_salida import _aplanar, aparece

    plano = np.asarray(Image.open(io.BytesIO(datos)).convert("RGB"))
    texto, _ = lector.leer(plano, False)
    valores = [P["nombre"], P["dni"], P["correo"], P["direccion"], P["telefono"], P["iban"]]
    leidos = [v for v in valores if aparece(_aplanar(v), texto)]
    return f"se leen {len(leidos)} de {len(valores)} datos del formulario" + (f": {leidos}" if leidos else "")


# --- informe ----------------------------------------------------------------------

_GRAVEDAD = {FUGA: 0, CUELGUE: 1, ERROR: 2, SOBRE: 3, CORRECTO: 4}


def tabla(resultados: list[dict]) -> str:
    filas = ["| Caso | Qué prueba | Resultado | Detalle |", "|---|---|---|---|"]
    for m in resultados:
        detalle = []
        if m["fugas_visibles"]:
            detalle.append("A LA VISTA: " + "; ".join(f"{k} ({', '.join(v)})" for k, v in m["fugas_visibles"].items()))
        if m["fugas_recuperables"]:
            detalle.append("recuperable: " + "; ".join(f"{k} ({', '.join(v)})"
                                                      for k, v in m["fugas_recuperables"].items()))
        if m["legitimos_perdidos"]:
            detalle.append("texto legitimo perdido: " + ", ".join(m["legitimos_perdidos"]))
        if m["estado"] != "completado":
            detalle.append(f"{m['estado']} ({m.get('codigo')}) en {m.get('segundos')} s")
        detalle += m["notas"]
        filas.append(f"| {m['id']} | {m['que_prueba']} | {m['resultado']} | {' · '.join(detalle) or '—'} |")
    return "\n".join(filas)


def main(argv: list[str]) -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    casos = json.loads((CORPUS / "casos.json").read_text(encoding="utf-8"))
    if "--solo" in argv:
        casos = [c for c in casos if c["id"] == argv[argv.index("--solo") + 1]]
    SALIDAS.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    crudos = []
    for caso in casos:
        r = subir(client, caso)
        vivo = client.get("/health").status_code == 200
        r["servicio_vivo"] = vivo
        crudos.append((caso, r))
        print(f"{caso['id']} {r['estado']:11s} {r.get('codigo') or '':16s} {r.get('segundos')} s  "
              f"servicio {'vivo' if vivo else 'CAIDO'}", flush=True)
    lector = None
    if any(c.get("imagen_incrustada") for c, _ in crudos):
        from evaluation.lectura_imagenes import Lector
        lector = Lector()
    resultados = []
    for caso, r in crudos:
        m = medir(caso, r, lector if caso.get("imagen_incrustada") else None)
        if not r["servicio_vivo"]:
            m["notas"].append("el servicio dejo de responder")
            m["resultado"] = CUELGUE
        resultados.append(m)
    resultados.sort(key=lambda m: (_GRAVEDAD[m["resultado"]], m["id"]))
    RESULTADOS.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    texto = tabla(resultados)
    (RESULTADOS.with_suffix(".md")).write_text(texto, encoding="utf-8")
    print("\n" + texto)
    cuenta = {k: sum(1 for m in resultados if m["resultado"] == k) for k in _GRAVEDAD}
    print("\n" + ", ".join(f"{k}: {v}" for k, v in cuenta.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
