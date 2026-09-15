"""Compara dos corridas de evaluación: métricas globales y por tipo.

Uso:
    python -m evaluation.comparar_eval evaluation/reference/results_main.json evaluation/results.json
    python -m evaluation.comparar_eval REF NUEVA --umbral 0.01

Marca los tipos cuyo F1 se mueve al menos `--umbral`.  Solo lee los JSON que
escribe `evaluation.run --results-json`; no llama a la API.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _api_errors(data: dict) -> int:
    return sum(
        1 for r in data.get("doc_results", [])
        if str(r.get("api_status", "")).startswith(("error", "api_error"))
    )


def comparar(ref: dict, new: dict, umbral: float) -> str:
    rs, ns = ref["summary"], new["summary"]
    lines = [
        "| Métrica | Referencia | Nueva | Delta |",
        "|---|---|---|---|",
    ]
    for key in ("overall_precision", "overall_recall", "overall_f1"):
        lines.append(f"| {key} | {rs[key]} | {ns[key]} | {ns[key] - rs[key]:+.4f} |")
    for key in ("tp_exact", "tp_partial", "fn", "fp"):
        lines.append(f"| {key} | {rs[key]} | {ns[key]} | {ns[key] - rs[key]:+d} |")
    lines.append(f"| documentos con error de API | {_api_errors(ref)} | {_api_errors(new)} | |")
    lines += [
        "",
        "| Tipo | F1 ref | F1 nueva | Delta F1 | FP ref → nueva | FN ref → nueva | |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in sorted(set(ref["by_type"]) | set(new["by_type"])):
        a, b = ref["by_type"].get(t, {}), new["by_type"].get(t, {})
        fa, fb = a.get("f1"), b.get("f1")
        delta = fb - fa if fa is not None and fb is not None else None
        mark = "**se mueve**" if delta is not None and abs(delta) >= umbral else ""
        lines.append(
            f"| {t} | {fa} | {fb} | {f'{delta:+.4f}' if delta is not None else '-'} "
            f"| {a.get('fp')} → {b.get('fp')} | {a.get('fn')} → {b.get('fn')} | {mark} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("referencia", type=Path)
    parser.add_argument("nueva", type=Path)
    parser.add_argument("--umbral", type=float, default=0.02,
                        help="Delta de F1 por tipo a partir del cual se marca.")
    args = parser.parse_args()
    ref = json.loads(args.referencia.read_text(encoding="utf-8"))
    new = json.loads(args.nueva.read_text(encoding="utf-8"))
    print(f"Referencia: {args.referencia} · Nueva: {args.nueva}\n")
    print(comparar(ref, new, args.umbral))


if __name__ == "__main__":
    main()
