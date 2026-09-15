"""Comprueba que la salida actual es idéntica a una referencia de `guardar_ref`.

Uso:
    python -m evaluation.identidad_contra --pdfs ../corpus-generator/output/pdfs --ref evaluation/work_ref

Reprocesa cada PDF que tenga referencia y compara el texto del PDF de salida
(espacios normalizados), entities_by_type y entities_detected.  Imprime cada
documento distinto con el primer punto donde difiere el texto y termina con
código 1 si hay alguna diferencia, para poder usarlo tras un refactor.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from app.services.pdf.pipeline import PdfAnonymizationPipeline
from evaluation.guardar_ref import snapshot


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdfs", type=Path, required=True, help="Carpeta con los PDF.")
    parser.add_argument("--ref", type=Path, required=True, help="Carpeta escrita por guardar_ref.")
    args = parser.parse_args()

    pipe = PdfAnonymizationPipeline()
    pdfs = [p for p in sorted(args.pdfs.glob("*.pdf")) if (args.ref / f"{p.stem}.json").exists()]
    text_diff = count_diff = detected_diff = 0
    with tempfile.TemporaryDirectory() as td:
        for pdf in pdfs:
            ref = json.loads((args.ref / f"{pdf.stem}.json").read_text(encoding="utf-8"))
            cur = snapshot(pipe, pdf, Path(td))
            a, b = _norm(ref["text"]), _norm(cur["text"])
            same_text = a == b
            same_counts = ref["entities_by_type"] == cur["entities_by_type"]
            same_detected = ref["entities_detected"] == cur["entities_detected"]
            text_diff += not same_text
            count_diff += not same_counts
            detected_diff += not same_detected
            if same_text and same_counts and same_detected:
                continue
            line = f"- {pdf.stem}: texto {'igual' if same_text else 'DISTINTO'}"
            if not same_counts:
                keys = set(ref["entities_by_type"]) | set(cur["entities_by_type"])
                changed = {
                    k: (ref["entities_by_type"].get(k), cur["entities_by_type"].get(k))
                    for k in sorted(keys)
                    if ref["entities_by_type"].get(k) != cur["entities_by_type"].get(k)
                }
                line += f", conteos {changed}"
            if not same_detected:
                line += f", detectadas {ref['entities_detected']} → {cur['entities_detected']}"
            print(line, flush=True)
            if not same_text:
                i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
                print(f"    ref:    «{a[max(0, i - 60):i + 60]}»")
                print(f"    actual: «{b[max(0, i - 60):i + 60]}»")

    print(f"\n{len(pdfs)} documentos: texto distinto {text_diff}, "
          f"conteos distintos {count_diff}, detectadas distintas {detected_diff}")
    sys.exit(1 if text_diff or count_diff or detected_diff else 0)


if __name__ == "__main__":
    main()
