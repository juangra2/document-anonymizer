"""Compuerta: ningún tipo obligatorio puede depender de uno desactivable (bloque 2b).

Compara dos corridas de evaluación sobre el mismo corpus:
  * referencia: sin tipos desactivados;
  * opcionales: con TODOS los tipos opcionales desactivados.

Un tipo obligatorio que se detecta en la referencia y se pierde con los
opcionales desactivados solo estaba cubierto por un opcional: es exactamente el
fallo de la fecha de nacimiento tapada únicamente por DATE_TIME.  La compuerta
falla (código de salida 1) y lista cada valor perdido con su documento.

Uso:
  python -m evaluation.run ... --results-json evaluation/results_gate_ref.json
  python -m evaluation.run ... --disabled-types optional --results-json evaluation/results_gate_opt.json
  python -m evaluation.mandatory_gate evaluation/results_gate_ref.json evaluation/results_gate_opt.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from app.services.pii.entity_catalog import mandatory_type_ids


def _fn_by_doc(results: dict, types: set[str]) -> dict[str, Counter]:
    """Falsos negativos por documento, restringidos a *types*, como multiconjunto."""
    out: dict[str, Counter] = {}
    for doc in results["doc_results"]:
        out[doc["doc_id"]] = Counter(
            (fn["entity_type"], fn["text"]) for fn in doc.get("fn", [])
            if fn.get("entity_type") in types
        )
    return out


def compare(ref: dict, opt: dict) -> tuple[bool, list[str]]:
    mandatory = mandatory_type_ids()
    lines: list[str] = []

    ref_types, opt_types = ref.get("by_type", {}), opt.get("by_type", {})
    lines.append("| Tipo obligatorio | Recall referencia | Recall con opcionales desactivados | Perdidos |")
    lines.append("|---|---|---|---|")
    ref_fn, opt_fn = _fn_by_doc(ref, mandatory), _fn_by_doc(opt, mandatory)
    lost: list[tuple[str, str, str]] = []
    for doc_id, fns in opt_fn.items():
        extra = fns - ref_fn.get(doc_id, Counter())
        for (etype, text), n in extra.items():
            lost.extend([(etype, doc_id, text)] * n)
    lost_by_type = Counter(t for t, _, _ in lost)

    for etype in sorted(mandatory):
        r = ref_types.get(etype, {}).get("recall")
        o = opt_types.get(etype, {}).get("recall")
        lines.append(f"| {etype} | {r if r is not None else '—'} | {o if o is not None else '—'} | {lost_by_type.get(etype, 0)} |")

    ok = not lost
    if lost:
        lines.append("\nValores obligatorios perdidos al desactivar los opcionales:")
        for etype, doc_id, text in sorted(lost)[:60]:
            lines.append(f"- {etype} · {doc_id} · «{text}»")
    return ok, lines


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ref = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    opt = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    ok, lines = compare(ref, opt)
    print("\n".join(lines))
    print("\nCOMPUERTA: " + ("OK — ningún obligatorio depende de un opcional" if ok else "FALLA"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
