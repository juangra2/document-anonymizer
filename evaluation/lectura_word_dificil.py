"""Medicion de los Word de prueba de casos dificiles (data/samples/word_dificil/).

Los documentos y su inventario (inventario_datos.json: cada valor, su tipo,
donde esta y si debe eliminarse) vienen hechos.  Cada documento se sube por la
API de verdad (POST /jobs, en proceso), con las casillas del catalogo; despues
de cada uno se comprueba que el servicio sigue respondiendo.

Del fichero de salida se mira, con la busqueda de lectura_excel_dificil.py:
  - FUGA A LA VISTA: el dato sigue en lo que se ve al abrir el documento
    (texto de cuerpo, cabeceras, pies, notas, comentarios y cuadros de texto,
    tambien lo tachado de un cambio registrado; no el texto oculto) o en el
    texto de un grafico o un SmartArt;
  - FUGA RECUPERABLE: esta en el fichero pero no se ve (atributos, relaciones,
    nombres de marcador, texto alternativo, la hoja de un grafico...);
  - SOBRE-REDACCION: un parrafo de la entrada sin ningun dato del inventario ya
    no esta entero en la salida;
  - ERROR: el documento no se procesa, o la salida no se abre.
doc12 es el control sin datos: tiene que salir con el mismo texto.

Uso: python -m evaluation.lectura_word_dificil [--solo docNN]
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from lxml import etree

from evaluation.lectura_excel_dificil import (_GRAVEDAD, CORRECTO, CUELGUE, ERROR, FUGA, SOBRE, compacto,
                                              donde_esta, imagenes_escondidas, metadatos_exiftool,
                                              partes_del_zip, subir, tabla)

CORPUS = Path("data/samples/word_dificil")
SALIDAS = Path("evaluation/word_dificil/salidas")
RESULTADOS = Path("evaluation/word_dificil/resultados.json")
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_VISIBLES = ("word/document.xml", "word/header", "word/footer", "word/footnotes.xml", "word/endnotes.xml",
             "word/comments.xml", "word/charts/chart", "word/diagrams/data", "word/diagrams/drawing")


def _texto_visible_de(raiz) -> list[str]:
    """Texto de cada parrafo, sin lo oculto (w:vanish)."""
    salida = []
    for par in raiz.iter(f"{{{W}}}p", f"{{{A}}}p"):
        trozos = []
        for e in par.iter(f"{{{W}}}t", f"{{{W}}}delText", f"{{{A}}}t",
                          "{http://schemas.openxmlformats.org/drawingml/2006/chart}v"):
            run = next((x for x in e.iterancestors(f"{{{W}}}r")), None)
            if run is not None and run.find(f"{{{W}}}rPr/{{{W}}}vanish") is not None:
                continue
            trozos.append(e.text or "")
        salida.append("".join(trozos))
    for v in raiz.iter("{http://schemas.openxmlformats.org/drawingml/2006/chart}v"):
        salida.append(v.text or "")
    return salida


def parrafos_visibles(ruta: Path) -> list[str]:
    salida = []
    with zipfile.ZipFile(ruta) as z:
        for n in z.namelist():
            if n.startswith(_VISIBLES) and n.endswith(".xml"):
                try:
                    salida += _texto_visible_de(etree.fromstring(z.read(n)))
                except etree.XMLSyntaxError:
                    pass
    return salida


def casos() -> list[dict]:
    inventario = json.loads((CORPUS / "inventario_datos.json").read_text(encoding="utf-8"))
    salida = []
    for fichero in sorted(CORPUS.glob("doc*.docx")):
        datos = [{"text": v["valor"], "type": v["tipo"], "donde": v["ubicacion"]}
                 for v in inventario if v["fichero"] == fichero.name and v["debe_eliminarse"]]
        # Lo legitimo: cada parrafo de la entrada con algo de texto y sin ningun dato.
        valores = [compacto(d["text"]) for d in datos]
        legitimos = [t for t in dict.fromkeys(parrafos_visibles(fichero))
                     if len(t.strip()) >= 12 and not any(v in compacto(t) for v in valores)]
        salida.append({"id": fichero.name[:5], "fichero": fichero.name, "nombre_subida": fichero.name,
                       "que_prueba": fichero.stem[6:].replace("_", " "), "esperado": "", "rechazo": None,
                       "datos": datos, "legitimos": legitimos})
    return salida


def medir(caso: dict, r: dict) -> dict:
    import docx

    m = {"id": caso["id"], "que_prueba": caso["que_prueba"], "estado": r["estado"], "codigo": r.get("codigo"),
         "segundos": r.get("segundos"), "fugas_visibles": {}, "fugas_recuperables": {},
         "legitimos_perdidos": [], "notas": []}
    entrada = CORPUS / caso["fichero"]
    try:
        docx.Document(str(entrada))
    except Exception as exc:
        m["notas"].append(f"la ENTRADA esta mal formada (python-docx no la abre: {str(exc)[:90]})")
    if r["estado"] == "colgado":
        m["resultado"] = CUELGUE
        return m
    if r["estado"] != "completado":
        m["resultado"] = ERROR
        return m
    salida = Path(r["salida"])
    if r["extension"] != ".docx":
        m["notas"].append(f"vuelve como {r['extension']}")
    try:
        docx.Document(str(salida))
    except Exception as exc:
        m["notas"].append(f"python-docx no abre la salida: {str(exc)[:90]}")
    visibles = compacto(" ".join(parrafos_visibles(salida)))
    partes = partes_del_zip(salida)
    for dato, en in donde_esta(partes, caso["datos"]).items():
        (m["fugas_visibles"] if compacto(dato) in visibles else m["fugas_recuperables"])[dato] = en
    textos_salida = [compacto(t) for t in parrafos_visibles(salida)]
    m["legitimos_perdidos"] = [t for t in caso["legitimos"]
                               if not any(compacto(t) in s for s in textos_salida)]
    for nombre, datos in partes:
        if nombre.startswith("word/media/"):
            escondidas = imagenes_escondidas(datos) if datos[:3] == b"\xff\xd8\xff" else []
            if escondidas:
                m["fugas_recuperables"][f"imagen escondida en {nombre}"] = escondidas
            meta = compacto(metadatos_exiftool(datos))
            for d in caso["datos"]:
                if len(compacto(d["text"])) >= 5 and compacto(d["text"]) in meta:
                    m["fugas_recuperables"].setdefault(d["text"], []).append(f"{nombre} (metadatos)")
    informe = r.get("informe") or {}
    m["estado_informe"] = informe.get("status")
    m["resultado"] = (FUGA if m["fugas_visibles"] or m["fugas_recuperables"]
                      else SOBRE if m["legitimos_perdidos"] else CORRECTO)
    return m


def main(argv: list[str]) -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    lista = casos()
    if "--solo" in argv:
        lista = [c for c in lista if c["id"] == argv[argv.index("--solo") + 1]]
    SALIDAS.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    resultados = []
    for caso in lista:
        r = subir(client, caso, CORPUS, SALIDAS)
        vivo = client.get("/health").status_code == 200
        print(f"{caso['id']} {r['estado']:11s} {r.get('codigo') or '':16s} {r.get('segundos')} s  "
              f"servicio {'vivo' if vivo else 'CAIDO'}", flush=True)
        m = medir(caso, r)
        if not vivo:
            m["notas"].append("el servicio dejo de responder")
            m["resultado"] = CUELGUE
        resultados.append(m)
    resultados.sort(key=lambda m: (_GRAVEDAD[m["resultado"]], m["id"]))
    RESULTADOS.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    texto = tabla(resultados)
    RESULTADOS.with_suffix(".md").write_text(texto, encoding="utf-8")
    print("\n" + texto)
    print("\n" + ", ".join(f"{k}: {sum(1 for m in resultados if m['resultado'] == k)}" for k in _GRAVEDAD))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
