"""Compuerta: desmarcar un tipo no puede hacer desaparecer otro.

Todos los tipos son desmarcables, así que ya no hay «obligatorios que dependen
de opcionales».  Lo que sigue valiendo es el principio de fondo: si una
petición desmarca DATE_TIME, lo que se tacha de los demás tipos tiene que ser
exactamente lo mismo que se tachaba antes.  Cuando no es así, un tipo estaba
cubierto de rebote por otro —el caso real fue una fecha de nacimiento que solo
se tapaba porque DATE_TIME la pillaba—, y desmarcarlo destapa datos sin que
nadie lo haya pedido.

Compara dos corridas de evaluación sobre el mismo corpus:
  * referencia: sin nada desmarcado;
  * prueba: con los tipos de `--desmarcados` fuera.

Falla (código de salida 1) si un tipo que sigue ACTIVO pierde en la prueba
algún valor que la referencia sí detectaba, y lista cada pérdida con su
documento.

Uso:
  python -m evaluation.run ... --results-json evaluation/ref.json
  python -m evaluation.run ... --disabled-types DATE_TIME,URL --results-json evaluation/prueba.json
  python -m evaluation.compuerta_tipos evaluation/ref.json evaluation/prueba.json --desmarcados DATE_TIME,URL
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _fn_by_doc(results: dict, types: set[str]) -> dict[str, Counter]:
    """Falsos negativos por documento, restringidos a *types*, como multiconjunto."""
    out: dict[str, Counter] = {}
    for doc in results["doc_results"]:
        out[doc["doc_id"]] = Counter(
            (fn["entity_type"], fn["text"]) for fn in doc.get("fn", [])
            if fn.get("entity_type") in types
        )
    return out


def watched_types(ref: dict, disabled: set[str]) -> set[str]:
    """Los tipos que siguen activos en la prueba: son los que no pueden empeorar."""
    return {t for t in ref.get("by_type", {}) if t not in disabled}


def compare(ref: dict, opt: dict, disabled: set[str]) -> tuple[bool, list[str]]:
    watched = watched_types(ref, disabled)
    lines: list[str] = []

    ref_types, opt_types = ref.get("by_type", {}), opt.get("by_type", {})
    ref_fn, opt_fn = _fn_by_doc(ref, watched), _fn_by_doc(opt, watched)

    lost: list[tuple[str, str, str]] = []
    for doc_id, fns in opt_fn.items():
        # Lo que se pierde de más respecto a la referencia; lo que ya se perdía
        # antes no lo causa el desmarcado.
        extra = fns - ref_fn.get(doc_id, Counter())
        for (etype, text), n in extra.items():
            lost.extend([(etype, doc_id, text)] * n)
    lost_by_type = Counter(t for t, _, _ in lost)

    lines.append(f"Desmarcados: {', '.join(sorted(disabled)) or '(ninguno)'}")
    lines.append("")
    lines.append("| Tipo activo | Recall referencia | Recall con los otros desmarcados | Perdidos |")
    lines.append("|---|---|---|---|")
    for etype in sorted(watched):
        r = ref_types.get(etype, {}).get("recall")
        o = opt_types.get(etype, {}).get("recall")
        lines.append(
            f"| {etype} | {r if r is not None else '—'} | "
            f"{o if o is not None else '—'} | {lost_by_type.get(etype, 0)} |"
        )

    ok = not lost
    if lost:
        lines.append("\nValores de tipos ACTIVOS perdidos al desmarcar los otros:")
        for etype, doc_id, text in sorted(lost)[:60]:
            lines.append(f"- {etype} · {doc_id} · «{text}»")
    return ok, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("referencia", type=Path)
    parser.add_argument("prueba", type=Path)
    parser.add_argument(
        "--desmarcados", required=True,
        help="Tipos desmarcados en la corrida de prueba, separados por comas.",
    )
    args = parser.parse_args()

    ref = json.loads(args.referencia.read_text(encoding="utf-8"))
    opt = json.loads(args.prueba.read_text(encoding="utf-8"))
    disabled = {t.strip().upper() for t in args.desmarcados.split(",") if t.strip()}

    ok, lines = compare(ref, opt, disabled)
    print("\n".join(lines))
    print("\nCOMPUERTA: " + (
        "OK — desmarcar esos tipos no hace desaparecer ningún otro" if ok else "FALLA"
    ))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
