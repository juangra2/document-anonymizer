"""Qué cambia documento a documento entre dos corridas de evaluación.

Uso:
    python -m evaluation.comparar_documentos evaluation/reference/results_ext.json evaluation/results.json

Lista, por tipo y por documento, los FN y FP que aparecen (+) y los que
desaparecen (-), y los cambios de estado de la API.  Agrupa además por tipo de
documento y calidad de escaneo: la forma más rápida de ver si un cambio es una
regresión general o se concentra en un grupo (por ejemplo, escaneados girados).
El texto se compara con los espacios normalizados.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _bag(doc: dict, key: str) -> Counter:
    return Counter(
        (x.get("entity_type"), " ".join(str(x.get("text")).split()))
        for x in doc.get(key, [])
    )


def _ok(doc: dict) -> bool:
    return bool(doc) and not str(doc.get("api_status", "")).startswith(("error", "api_error"))


def _f1(docs: list[dict]) -> tuple[float, int, int, int]:
    def n(v):
        return len(v) if isinstance(v, list) else int(v or 0)
    tp = sum(n(d["tp_exact"]) + n(d["tp_partial"]) for d in docs)
    fn = sum(n(d["fn"]) for d in docs)
    fp = sum(n(d["fp"]) for d in docs)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return (round(2 * p * r / (p + r), 4) if p + r else 0.0), tp, fn, fp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("referencia", type=Path)
    parser.add_argument("nueva", type=Path)
    args = parser.parse_args()

    ref = {d["doc_id"]: d for d in json.loads(args.referencia.read_text(encoding="utf-8"))["doc_results"]}
    new = {d["doc_id"]: d for d in json.loads(args.nueva.read_text(encoding="utf-8"))["doc_results"]}

    common = [k for k in ref if _ok(ref[k]) and _ok(new.get(k, {}))]
    print(f"## Por grupo (solo los {len(common)} documentos sin error en ambas corridas)\n")
    groups = defaultdict(lambda: ([], []))
    for k in common:
        g = f"{ref[k]['doc_type']}/{ref[k]['quality']}"
        groups[g][0].append(ref[k])
        groups[g][1].append(new[k])
    for g, (a, b) in sorted(groups.items()):
        fa, fb = _f1(a), _f1(b)
        print(f"- {g}: F1 {fa[0]} → {fb[0]}  (tp, fn, fp) {fa[1:]} → {fb[1:]}{'  ← cambia' if fa != fb else ''}")

    by_doc = defaultdict(list)
    per_type = defaultdict(Counter)
    for doc_id in sorted(set(ref) | set(new)):
        r, n = ref.get(doc_id, {}), new.get(doc_id, {})
        if r.get("api_status") != n.get("api_status"):
            by_doc[doc_id].append(f"api_status {r.get('api_status')} → {n.get('api_status')}")
        for key in ("fn", "fp"):
            a, b = _bag(r, key), _bag(n, key)
            for (t, x), c in (b - a).items():
                by_doc[doc_id].append(f"+{key.upper()} {t} «{x}» x{c}")
                per_type[t][f"+{key}"] += c
            for (t, x), c in (a - b).items():
                by_doc[doc_id].append(f"-{key.upper()} {t} «{x}» x{c}")
                per_type[t][f"-{key}"] += c

    print("\n## Por tipo (+ aparecen, - desaparecen)\n")
    for t, c in sorted(per_type.items()):
        print(f"- {t}: {dict(c)}")
    print("\n## Por documento\n")
    for doc_id, lines in by_doc.items():
        d = new.get(doc_id) or ref.get(doc_id)
        print(f"### {doc_id} ({d.get('doc_type')}, {d.get('quality')}, {d.get('language')})")
        for line in lines:
            print("- " + line)


if __name__ == "__main__":
    main()
