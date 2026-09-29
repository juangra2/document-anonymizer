"""Pasa la lectura de salidas sobre una muestra de la linea base.

El evaluador compara listas de entidades y nunca abre el PDF anonimizado: una
barra negra pintada al lado del dato cuenta como acierto.  Esto abre la salida
y busca el dato dentro.

La muestra incluye SIEMPRE los girados: es el caso donde una lectura ingenua da
falsos "tapado", porque el OCR tampoco lee texto de lado.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
CORPUS = RAIZ.parent / "corpus-generator" / "output"

# (corpus, entradas, etiquetas, salidas, cuantos)
MUESTRA = (
    ("digital",   "pdfs",               "labels",               "evaluation/salidas_digital",   15),
    ("escaneado", "scanned_mitad_pdfs", "scanned_mitad_labels", "evaluation/salidas_escaneado", 15),
    ("hibrido",   "hybrid_mitad_pdfs",  "hybrid_mitad_labels",  "evaluation/salidas_hibrido",   10),
)


def elegir(entradas: Path, etiquetas: Path, salidas: Path, cuantos: int) -> list[tuple[Path, Path, dict]]:
    """Los girados entran siempre; el resto se sortea con semilla fija."""
    rng = random.Random(40)
    disponibles = []
    for salida in sorted(salidas.glob("*.pdf")):
        entrada = entradas / salida.name
        etiqueta = etiquetas / f"{salida.stem}.json"
        if entrada.exists() and etiqueta.exists():
            disponibles.append((entrada, salida,
                                json.loads(etiqueta.read_text(encoding="utf-8"))))
    girados = [d for d in disponibles if d[2].get("quality") == "scanned_rotated"]
    resto = [d for d in disponibles if d not in girados]
    rng.shuffle(resto)
    return (girados + resto)[:cuantos]


def main() -> None:
    from evaluation.lectura_salida import (
        comprobar_documento, guardar, imprimir, resumir,
    )

    todos = []
    for nombre, ent, lab, sal, cuantos in MUESTRA:
        salidas = RAIZ / sal
        if not salidas.is_dir():
            print(f"\n({nombre}: sin salidas guardadas en {sal}, se salta)")
            continue
        elegidos = elegir(CORPUS / ent, CORPUS / lab, salidas, cuantos)
        print(f"\n===== {nombre.upper()} — {len(elegidos)} documentos "
              f"({sum(1 for e in elegidos if e[2].get('quality') == 'scanned_rotated')} girados)")
        for entrada, salida, etiqueta in elegidos:
            r = comprobar_documento(entrada, salida, etiqueta)
            todos.append(r)
            giro = " (girado)" if etiqueta.get("quality") == "scanned_rotated" else ""
            pct = "—" if r.porcentaje_tapado is None else f"{r.porcentaje_tapado:5.1f} %"
            print(f"  {r.documento[:40]:42s}{giro:9s} tapados={r.tapados:3d} "
                  f"fugas={r.fugas:3d} no_concl={r.no_concluyentes:3d}  {pct}")
            for d in r.detalle:
                if d["estado"] == "fuga":
                    print(f"        FUGA  {d['tipo']:16s} {d['texto'][:46]}")

    resumen = resumir(todos)
    imprimir(resumen)
    guardar(todos, resumen, RAIZ / "evaluation/linea_base/lectura_barras.json")
    print("\n  guardado en evaluation/linea_base/lectura_barras.json")


if __name__ == "__main__":
    main()
