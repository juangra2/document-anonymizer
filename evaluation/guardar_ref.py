"""Guarda la salida actual del pipeline como referencia de identidad.

Uso:
    python -m evaluation.guardar_ref --pdfs ../corpus-generator/output/pdfs --out evaluation/work_ref

Procesa cada PDF en proceso (sin API ni cola) con include_entities activo y sin
tipos desactivados, y escribe `<out>/<documento>.json` con el texto del PDF de
salida, entities_by_type y entities_detected.  Se usa antes de un refactor que
no debería cambiar el resultado; después, `evaluation.identidad_contra`
comprueba que nada ha cambiado.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

import fitz

from app.services.pdf.pipeline import PdfAnonymizationPipeline


def snapshot(pipe: PdfAnonymizationPipeline, pdf: Path, workdir: Path) -> dict:
    """Texto de salida y recuentos de un documento, en el formato de referencia."""
    out = workdir / "salida.pdf"
    report = pipe.process(pdf, out, "ref", "strict", True, frozenset())
    with fitz.open(out) as doc:
        text = "\n".join(page.get_text() for page in doc)
    return {
        "text": text,
        "entities_by_type": report.entities_by_type,
        "entities_detected": report.entities_detected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdfs", type=Path, required=True, help="Carpeta con los PDF.")
    parser.add_argument("--out", type=Path, required=True, help="Carpeta donde guardar la referencia.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(args.pdfs.glob("*.pdf"))[: args.limit]
    pipe = PdfAnonymizationPipeline()
    started = time.time()
    with tempfile.TemporaryDirectory() as td:
        for n, pdf in enumerate(pdfs, 1):
            data = snapshot(pipe, pdf, Path(td))
            (args.out / f"{pdf.stem}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            if n % 50 == 0 or n == len(pdfs):
                print(f"{n}/{len(pdfs)} en {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
