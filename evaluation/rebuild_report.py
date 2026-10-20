"""Regenera el HTML de un informe desde su results-JSON ya guardado.

Uso:
    python -m evaluation.rebuild_report evaluation/reference/c4/resultados_digital.json evaluation/report_digital.html
"""
import json
import sys
from pathlib import Path

from evaluation.metrics import DocResult, EntityRecord, TypeResult, aggregate_results
from evaluation.report import generate_report


def _ents(raw):
    return [
        EntityRecord(
            text=e.get("text", ""),
            entity_type=e.get("entity_type", ""),
            page=e.get("page"),
            score=e.get("score", 0.0) or 0.0,
            source=e.get("source", "") or "",
        )
        for e in raw or []
    ]


def rebuild(json_path: Path, html_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    docs = []
    for d in data["doc_results"]:
        by_type = {}
        for t, v in (d.get("by_type") or {}).items():
            by_type[t] = TypeResult(
                entity_type=v.get("entity_type", t),
                tp_exact=v.get("tp_exact", 0),
                tp_partial=v.get("tp_partial", 0),
                fn=_ents(v.get("fn")),
                fp=_ents(v.get("fp")),
            )
        docs.append(DocResult(
            doc_id=d["doc_id"], doc_type=d["doc_type"], language=d["language"],
            quality=d["quality"], api_status=d["api_status"],
            processing_ms=d.get("processing_ms", 0),
            tp_exact=d.get("tp_exact", 0), tp_partial=d.get("tp_partial", 0),
            fn=_ents(d.get("fn")), fp=_ents(d.get("fp")), by_type=by_type,
        ))
    generate_report(docs, aggregate_results(docs), html_path)
    print(f"{html_path}  <-  {json_path.name}  ({len(docs)} docs)")


if __name__ == "__main__":
    rebuild(Path(sys.argv[1]), Path(sys.argv[2]))
