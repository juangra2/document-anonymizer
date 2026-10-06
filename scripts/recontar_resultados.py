"""Vuelve a contar unos resultados guardados con la contabilidad nueva.

Los resultados de antes del cambio (emparejar por valor, paginas en negro) no
guardaron la lista de detectados, solo los FN y FP.  Se reconstruye: detectado
= las apariciones de la etiqueta que no quedaron como FN, mas los FP.  Con eso
se cuenta por valor y se miran las paginas en negro en las salidas guardadas.

Es exacto en lo que importa —que valores se detectaron— con una reserva: un
acierto parcial se reconstruye con el texto de la etiqueta, asi que el reparto
exacto/parcial no es fiable (el F1 si, porque los dos cuentan como acierto).

    python scripts/recontar_resultados.py RESULTADOS.json ETIQUETAS/ SALIDAS/
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from evaluation.lectura_salida import negrura                         # noqa: E402
from evaluation.metrics import (                                       # noqa: E402
    NEGRURA_PAGINA_TACHADA, aggregate_results, evaluate_document,
)


def recontar(resultados: Path, etiquetas: Path, salidas: Path) -> dict:
    datos = json.loads(resultados.read_text(encoding="utf-8"))
    nuevos = []
    sin_salida = 0
    for doc in datos["doc_results"]:
        et = json.loads((etiquetas / f"{doc['doc_id']}.json").read_text(encoding="utf-8"))
        gt = et["entities"]
        quedan = collections.Counter((f["entity_type"], f["text"]) for f in doc["fn"])
        detectados = []
        for e in gt:
            clave = (e["type"], e["text"])
            if quedan[clave]:
                quedan[clave] -= 1
            else:
                detectados.append({"text": e["text"], "type": e["type"]})
        detectados += [{"text": f["text"], "type": f["entity_type"]} for f in doc["fp"]]

        salida = salidas / f"{doc['doc_id']}.pdf"
        if salida.exists():
            negras = {i + 1 for i, n in enumerate(negrura(salida)) if n >= NEGRURA_PAGINA_TACHADA}
        else:
            negras, sin_salida = set(), sin_salida + 1
        nuevos.append(evaluate_document(
            doc["doc_id"], doc.get("doc_type", ""), doc.get("language", ""),
            doc.get("quality", ""), doc.get("api_status", ""), doc.get("processing_ms", 0),
            gt_entities=gt, detected_entities=detectados, camino=doc.get("camino", ""),
            paginas_en_negro=negras,
        ))
    agg = aggregate_results(nuevos)
    o, a = agg["overall"], agg["overall_por_aparicion"]
    return {
        "documentos": len(nuevos),
        "sin_salida_guardada": sin_salida,
        "por_valor": {"f1": round(o.f1(), 4), "precision": round(o.precision(), 4),
                      "recall": round(o.recall(), 4), "tp": o.tp, "fn": o.fn_count,
                      "fp": o.fp_count, "tp_pagina_negra": agg["tp_pagina_negra"]},
        "por_aparicion_reconstruido": {"f1": round(a.f1(), 4), "fn": a.fn_count, "fp": a.fp_count},
        "guardado_original": {k: datos["summary"][k] for k in
                              ("overall_f1", "overall_precision", "overall_recall", "fn", "fp")},
    }


if __name__ == "__main__":
    print(json.dumps(recontar(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])),
                     ensure_ascii=False, indent=2))
