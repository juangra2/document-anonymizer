"""Medicion del corpus de Excel (corpus_excel.py): que se elimina, que queda,
que columnas se clasifican bien y que se toca de mas.

Para cada libro se pasa el servicio de verdad y se mira el fichero de SALIDA:
  - ABRE: el zip esta sano y openpyxl lo vuelve a abrir;
  - cada dato es ELIMINADO si no esta en ninguna parte del fichero
    (app.services.office.paquete.verificar) o FUGA si sigue en alguna;
  - cada columna etiquetada: si es personal, BIEN si todas sus celdas de datos
    (sin formula) llevan el marcador de su tipo; si no es personal, BIEN si
    ninguna celda ha cambiado (las que cambian cuentan como CELDAS DE MAS); el
    texto libre se mide por sus datos y por el texto legitimo;
  - cada texto legitimo es CONSERVADO si sigue en alguna celda, o de mas si no;
  - en un libro sin datos, ninguna celda cambia;
  - las celdas con valor esperado (totales de importes) lo llevan.
Cada libro se procesa con las casillas del catalogo, mas las que marque su
etiqueta (marcados).

Uso: python -m evaluation.lectura_excel [--solo NOMBRE]
"""
from __future__ import annotations

import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

CORPUS = Path("evaluation/excel/corpus")
SALIDAS = Path("evaluation/excel/salidas")
RESULTADOS = Path("evaluation/excel/resultados.json")


def _abre(ruta: Path) -> bool:
    import openpyxl

    try:
        with zipfile.ZipFile(ruta) as z:
            if z.testzip() is not None:
                return False
        openpyxl.load_workbook(ruta)
        return True
    except Exception:
        return False


def _columnas(ws) -> tuple[int, dict[str, int]]:
    """Fila de cabecera (la primera con las cabeceras etiquetadas) y columnas."""
    for fila in ws.iter_rows(min_row=1, max_row=20):
        nombres = {c.value: c.column for c in fila if isinstance(c.value, str)}
        if len(nombres) >= 2:
            return fila[0].row, nombres
    return 1, {}


def medir(etiqueta: dict, entrada: Path, salida: Path) -> dict:
    import openpyxl

    from app.services.office.docx import marcador
    from app.services.office.paquete import verificar

    r = {"documento": etiqueta["documento"], "abre": _abre(salida)}
    fugas = {v for _, v in verificar(salida, [d["text"] for d in etiqueta["datos"]])}
    r["datos"] = [(d["text"], d["type"], "fuga" if d["text"] in fugas else "eliminado") for d in etiqueta["datos"]]
    wb_in = openpyxl.load_workbook(entrada)
    wb_out = openpyxl.load_workbook(salida) if r["abre"] else None
    r["columnas"], r["celdas_de_mas"] = [], 0
    for hoja, columnas in etiqueta["columnas"].items():
        if wb_out is None or hoja not in wb_out.sheetnames:
            continue
        a, b = wb_in[hoja], wb_out[hoja]
        fila_cab, posicion = _columnas(a)
        for cabecera, tipo in columnas.items():
            col = posicion.get(cabecera)
            if col is None:
                continue
            filas = range(fila_cab + 1, a.max_row + 1)
            antes = [a.cell(row=f, column=col).value for f in filas]
            despues = [b.cell(row=f, column=col).value for f in filas]
            if tipo and tipo != "TEXTO_LIBRE":
                datos = [d for x, d in zip(antes, despues) if x is not None]
                bien = all(d == marcador(tipo) for d in datos)
                r["columnas"].append((hoja, cabecera, tipo, "bien" if bien else "mal"))
            elif tipo is None:
                cambian = sum(1 for x, d in zip(antes, despues)
                              if x != d and not (isinstance(x, str) and x.startswith("=")))
                r["celdas_de_mas"] += cambian
                r["columnas"].append((hoja, cabecera, None, "bien" if not cambian else "mal"))
    textos = set()
    if wb_out is not None:
        for ws in wb_out.worksheets:
            for fila in ws.iter_rows():
                textos.update(str(c.value) for c in fila if c.value is not None)
    r["legitimos"] = [(t, "conservado" if t in textos or any(t in x for x in textos) else "de_mas")
                      for t in etiqueta["legitimos"]]
    r["celdas"] = []
    for hoja, esperadas in etiqueta.get("celdas", {}).items():
        for celda, valor in esperadas.items():
            obtenido = wb_out[hoja][celda].value if wb_out is not None else None
            r["celdas"].append((hoja, celda, "bien" if obtenido == valor else "mal"))
    if "dinamica" in etiqueta and wb_out is not None:
        d = etiqueta["dinamica"]
        r["dinamica_eliminada"] = wb_out[d["hoja"]][d["rango"].split(":")[0]].value == "[TABLA DINÁMICA ELIMINADA]"
    return r


def resumen(resultados: list[dict]) -> None:
    c = Counter()
    for r in resultados:
        c["libros"] += 1
        c["abren"] += r["abre"]
        for *_, e in r["datos"]:
            c[e] += 1
        for *_, tipo, e in r["columnas"]:
            c[f"col_{'personal' if tipo else 'otra'}_{e}"] += 1
        c["celdas_de_mas"] += r["celdas_de_mas"]
        for _, e in r["legitimos"]:
            c[f"leg_{e}"] += 1
        for *_, e in r.get("celdas", []):
            c[f"celda_{e}"] += 1
    print(f"\nTOTAL: {c['libros']} libros, abren {c['abren']}/{c['libros']}")
    print(f"  datos: {c['eliminado']} eliminados / {c['fuga']} fugas")
    print(f"  columnas personales: {c['col_personal_bien']} bien / {c['col_personal_mal']} mal")
    print(f"  columnas no personales: {c['col_otra_bien']} intactas / {c['col_otra_mal']} tocadas "
          f"({c['celdas_de_mas']} celdas de mas)")
    print(f"  texto legitimo: {c['leg_conservado']} conservado / {c['leg_de_mas']} de mas")
    print(f"  celdas esperadas (totales): {c['celda_bien']} bien / {c['celda_mal']} mal")


def main(argv: list[str]) -> int:
    from app.core.config import settings
    from app.services.formatos import procesar
    from app.services.pdf.pipeline import PdfAnonymizationPipeline
    from app.services.pii.entity_catalog import default_disabled_ids

    etiquetas = json.loads((CORPUS / "etiquetas.json").read_text(encoding="utf-8"))
    if "--solo" in argv:
        etiquetas = [e for e in etiquetas if argv[argv.index("--solo") + 1] in e["documento"]]
    SALIDAS.mkdir(parents=True, exist_ok=True)
    pipeline = PdfAnonymizationPipeline()
    resultados = []
    for e in etiquetas:
        entrada, salida = CORPUS / e["documento"], SALIDAS / e["documento"]
        procesar("xlsx", "anonymize", entrada, salida, "x", lambda: pipeline,
                 settings.default_redaction_mode, default_disabled_ids() - set(e.get("marcados", [])))
        r = medir(e, entrada, salida)
        resultados.append(r)
        fugas = [t for _, t, s in r["datos"] if s == "fuga"]
        mal = [(h, cab) for h, cab, _, s in r["columnas"] if s == "mal"]
        de_mas = [t for t, s in r["legitimos"] if s == "de_mas"]
        mal += [(h, celda) for h, celda, s in r["celdas"] if s == "mal"]
        print(f"{e['documento']:28s} abre={r['abre']}  fugas={fugas}  columnas_mal={mal}  de_mas={de_mas}"
              + (f"  dinamica_eliminada={r['dinamica_eliminada']}" if "dinamica_eliminada" in r else ""),
              flush=True)
    RESULTADOS.write_text(json.dumps(resultados, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    resumen(resultados)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
