"""Medicion del corpus de textos (corpus_texto.py): que se elimina, que queda,
que se sustituye de mas y que la salida conserva codificacion, saltos de linea
y sintaxis.

Para cada documento se pasa el servicio de verdad (formatos.procesar, con las
casillas por defecto) y se mira el fichero de SALIDA:
  - CODIFICACION: se lee en la misma codificacion que la entrada, con el mismo
    BOM, y con los mismos saltos de linea (cuantos \\r\\n y cuantos \\n sueltos);
  - cada dato es ELIMINADO si no esta en el texto de salida (sin distinguir
    mayusculas) o FUGA si sigue;
  - cada texto legitimo es CONSERVADO si sigue tal cual, o SUSTITUIDO DE MAS;
  - Markdown: la ESTRUCTURA que lee markdown-it (bloques, enlaces, imagenes,
    enfasis, codigo) es la misma antes y despues.  Un enlace automatico cuyo
    correo entero era el dato pasa a texto (ya no tiene direccion): no cuenta;
  - un documento sin datos tiene que salir IDENTICO, byte a byte.

Uso: python -m evaluation.lectura_texto [--solo NOMBRE]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

CORPUS = Path("evaluation/texto/corpus")
SALIDAS = Path("evaluation/texto/salidas")
RESULTADOS = Path("evaluation/texto/resultados.json")
_BOMS = {"utf-8": b"\xef\xbb\xbf", "utf-16": (b"\xff\xfe", b"\xfe\xff")}


def _decodificar(datos: bytes, codificacion: str, bom: bool) -> str | None:
    try:
        if codificacion == "utf-16":
            return datos.decode("utf-16") if datos[:2] in _BOMS["utf-16"] else None
        if codificacion == "utf-8" and bom:
            return datos[3:].decode("utf-8") if datos.startswith(_BOMS["utf-8"]) else None
        if codificacion == "utf-8" and datos.startswith(_BOMS["utf-8"]):
            return None
        return datos.decode(codificacion)
    except UnicodeDecodeError:
        return None


def _saltos(texto: str) -> tuple[int, int]:
    return texto.count("\r\n"), texto.replace("\r\n", "").count("\n")


def estructura(texto: str) -> list[tuple]:
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark").enable("table")
    salida = []
    for t in md.parse(texto):
        salida.append((t.type, t.tag, t.nesting))
        for h in t.children or []:
            if h.type in ("text", "softbreak", "html_inline") or h.markup == "autolink":
                continue
            salida.append((h.type, h.tag, h.nesting))
    return salida


def medir(solo: str | None = None) -> dict:
    from app.services.formatos import procesar, reconocer
    from app.services.pii.analyzer import PiiAnalyzer

    analizador = PiiAnalyzer()
    pipeline = type("P", (), {"_analyzer": analizador})
    etiquetas = json.loads((CORPUS / "etiquetas.json").read_text(encoding="utf-8"))
    SALIDAS.mkdir(parents=True, exist_ok=True)
    total: Counter = Counter()
    documentos = []
    for e in etiquetas:
        if solo and e["nombre"] != solo:
            continue
        entrada = CORPUS / e["fichero"]
        salida = SALIDAS / e["fichero"]
        formato = reconocer(entrada, e["fichero"])
        informe = procesar(formato, "anonymize", entrada, salida, e["nombre"], pipeline, "strict", frozenset())
        datos_entrada = entrada.read_bytes()
        datos_salida = salida.read_bytes()
        texto_entrada = _decodificar(datos_entrada, e["codificacion"], e["bom"])
        texto = _decodificar(datos_salida, e["codificacion"], e["bom"])
        r = {"nombre": e["nombre"], "formato": formato, "estado": informe.status,
             "codificacion_leida": informe.text_encoding, "residuales": informe.residual_entities}
        r["codificacion"] = "OK" if texto is not None else "MAL"
        texto = texto if texto is not None else datos_salida.decode("utf-8", "replace")
        r["saltos"] = "OK" if _saltos(texto) == _saltos(texto_entrada) else f"MAL {_saltos(texto_entrada)} -> {_saltos(texto)}"
        bajo = texto.casefold()
        r["fugas"] = [d["text"] for d in e["datos"] if d["text"].casefold() in bajo]
        r["eliminados"] = len(e["datos"]) - len(r["fugas"])
        r["de_mas"] = [t for t in e["legitimos"] if t not in texto]
        if formato == "md":
            r["estructura"] = "OK" if estructura(texto) == estructura(texto_entrada) else "MAL"
        if e["sin_datos"]:
            r["identico"] = datos_salida == datos_entrada
        documentos.append(r)
        total["datos"] += len(e["datos"])
        total["eliminados"] += r["eliminados"]
        total["fugas"] += len(r["fugas"])
        total["legitimos"] += len(e["legitimos"])
        total["de_mas"] += len(r["de_mas"])
        total["codificacion_mal"] += r["codificacion"] != "OK"
        total["saltos_mal"] += r["saltos"] != "OK"
        total["estructura_mal"] += r.get("estructura", "OK") != "OK"
        total["sin_datos_cambiados"] += e["sin_datos"] and not r["identico"]
        total["revision_manual"] += informe.status == "manual_review_required"
        marca = "OK " if not r["fugas"] and not r["de_mas"] else "!! "
        print(f"{marca}{e['nombre']:<26} {formato:<3} {informe.text_encoding:<10} datos {r['eliminados']}/{len(e['datos'])}"
              f"  de mas {len(r['de_mas'])}  cod {r['codificacion']}  saltos {r['saltos']}"
              f"{'  estructura ' + r['estructura'] if 'estructura' in r else ''}"
              f"{'  identico ' + str(r['identico']) if 'identico' in r else ''}  [{informe.status}]")
        for f in r["fugas"]:
            print(f"     FUGA: {f}")
        for f in r["de_mas"]:
            print(f"     SUSTITUIDO DE MAS: {f}")
    resultado = {"total": dict(total), "documentos": documentos}
    RESULTADOS.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nTOTAL", dict(total))
    return resultado


if __name__ == "__main__":
    solo = sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else None
    medir(solo)
