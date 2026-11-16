"""Medicion del corpus de CSV (corpus_csv.py): que se elimina, que queda, que se
sustituye de mas, y que el CSV de salida es el mismo CSV.

Para cada fichero se pasa el servicio de verdad (formatos.procesar, con las
casillas del catalogo menos las que desmarque su etiqueta) y se mira la salida:
  - FORMA: misma codificacion y BOM, mismos saltos de linea, mismo separador,
    mismo numero de registros y de campos en cada uno;
  - cada dato es ELIMINADO si no esta en el CSV de salida, o FUGA si sigue;
  - cada columna etiquetada: si es personal, todas sus celdas llevan el
    marcador de su tipo (COLUMNA BIEN); si no lo es, sus celdas no cambian;
  - cada texto legitimo es CONSERVADO si sigue tal cual, o SUSTITUIDO DE MAS;
  - un fichero sin datos tiene que salir IDENTICO, byte a byte.

Uso: python -m evaluation.lectura_csv [--solo NOMBRE]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

CORPUS = Path("evaluation/csv/corpus")
SALIDAS = Path("evaluation/csv/salidas")
RESULTADOS = Path("evaluation/csv/resultados.json")


def _saltos(texto: str) -> tuple[int, int]:
    return texto.count("\r\n"), texto.replace("\r\n", "").count("\n")


def medir(solo: str | None = None) -> dict:
    from app.services.formatos import procesar, reconocer
    from app.services.office.docx import marcador
    from app.services.pdf.pipeline import PdfAnonymizationPipeline
    from app.services.pii.entity_catalog import default_disabled_ids
    from app.services.texto.codificacion import leer
    from app.services.texto.tabla_csv import leer_csv

    _pipeline = PdfAnonymizationPipeline()
    pipeline = lambda: _pipeline                                    # noqa: E731
    etiquetas = json.loads((CORPUS / "etiquetas.json").read_text(encoding="utf-8"))
    SALIDAS.mkdir(parents=True, exist_ok=True)
    total: Counter = Counter()
    documentos = []
    for e in etiquetas:
        if solo and e["nombre"] != solo:
            continue
        entrada, salida = CORPUS / e["fichero"], SALIDAS / e["fichero"]
        formato = reconocer(entrada, e["fichero"])
        desactivados = frozenset(default_disabled_ids() | set(e.get("desmarcados", [])))
        informe = procesar(formato, "anonymize", entrada, salida, e["nombre"], pipeline, "strict", desactivados)
        original, nuevo = entrada.read_bytes(), salida.read_bytes()
        le, ls = leer(original), leer(nuevo)
        sep_e, filas_e = leer_csv(le.texto)
        sep_s, filas_s = leer_csv(ls.texto)
        r = {"nombre": e["nombre"], "estado": informe.status, "codificacion_leida": informe.text_encoding}
        forma = []
        if (ls.codificacion, ls.bom) != (le.codificacion, le.bom):
            forma.append(f"codificacion {le.codificacion}->{ls.codificacion}")
        if _saltos(ls.texto) != _saltos(le.texto):
            forma.append("saltos")
        if sep_s != sep_e:
            forma.append("separador")
        if [len(f) for f in filas_s] != [len(f) for f in filas_e]:
            forma.append("registros o campos")
        r["forma"] = forma
        bajo = ls.texto.casefold()
        r["fugas"] = [d["text"] for d in e["datos"] if d["text"].casefold() in bajo]
        r["de_mas"] = [t for t in e["legitimos"] if t not in ls.texto]

        # Columnas: por su cabecera en la fila donde esta.
        mal = []
        cab_fila = next((i for i, f in enumerate(filas_e) if any(c.valor in e["columnas"] for c in f)), None)
        if e["columnas"] and cab_fila is not None and not forma:
            cabecera = [c.valor for c in filas_e[cab_fila]]
            for nombre_col, tipo in e["columnas"].items():
                col = cabecera.index(nombre_col)
                pares = [(fe[col].valor, fs[col].valor) for fe, fs in zip(filas_e[cab_fila + 1:], filas_s[cab_fila + 1:])
                         if col < len(fe) and fe[col].valor.strip()]
                if tipo is None:
                    if any(a != b for a, b in pares):
                        mal.append(f"{nombre_col}: cambiada sin ser personal")
                elif any(b != marcador(tipo) for _, b in pares):
                    mal.append(f"{nombre_col}: no todas llevan {marcador(tipo)}")
        r["columnas_mal"] = mal
        r["columnas_bien"] = len(e["columnas"]) - len(mal) if e["columnas"] and not forma else 0
        if e["sin_datos"]:
            r["identico"] = nuevo == original
        documentos.append(r)

        total["ficheros"] += 1
        total["datos"] += len(e["datos"])
        total["fugas"] += len(r["fugas"])
        total["legitimos"] += len(e["legitimos"])
        total["de_mas"] += len(r["de_mas"])
        total["forma_mal"] += bool(forma)
        total["columnas"] += len(e["columnas"])
        total["columnas_bien"] += r["columnas_bien"]
        total["sin_datos_cambiados"] += e["sin_datos"] and not r["identico"]
        total["revision_manual"] += informe.status == "manual_review_required"
        bien = not (r["fugas"] or r["de_mas"] or forma or mal or (e["sin_datos"] and not r["identico"]))
        print(f"{'OK ' if bien else '!! '}{e['nombre']:<26} {informe.text_encoding:<7} sep {sep_e!r:<5}"
              f" datos {len(e['datos']) - len(r['fugas'])}/{len(e['datos'])}  de mas {len(r['de_mas'])}"
              f"  columnas {r['columnas_bien']}/{len(e['columnas'])}  forma {'OK' if not forma else forma}"
              f"{'  identico ' + str(r['identico']) if 'identico' in r else ''}  [{informe.status}]")
        for x in r["fugas"]:
            print(f"     FUGA: {x}")
        for x in r["de_mas"]:
            print(f"     SUSTITUIDO DE MAS: {x!r}")
        for x in mal:
            print(f"     COLUMNA: {x}")
    resultado = {"total": dict(total), "documentos": documentos}
    RESULTADOS.parent.mkdir(parents=True, exist_ok=True)
    RESULTADOS.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nTOTAL", dict(total))
    return resultado


if __name__ == "__main__":
    medir(sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else None)
