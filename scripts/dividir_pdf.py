"""Parte un PDF grande en trozos de N páginas.

Uso:
    python scripts/dividir_pdf.py expediente.pdf
    python scripts/dividir_pdf.py expediente.pdf --paginas 25 --salida trozos/

Pensado para los expedientes del ayuntamiento, que llegan como tomos de
cientos de páginas: cada trozo se llama `<nombre>_parte_001_pag_1-10.pdf`.
Si el PDF contiene datos reales, la carpeta de salida debe estar en
`samples/real/` (ignorada por git).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import fitz


def dividir(entrada: Path, salida: Path, paginas_por_trozo: int) -> list[Path]:
    salida.mkdir(parents=True, exist_ok=True)
    creados = []
    with fitz.open(entrada) as doc:
        total = doc.page_count
        for start in range(0, total, paginas_por_trozo):
            end = min(start + paginas_por_trozo, total)
            parte = start // paginas_por_trozo + 1
            destino = salida / f"{entrada.stem}_parte_{parte:03d}_pag_{start + 1}-{end}.pdf"
            with fitz.open() as trozo:
                trozo.insert_pdf(doc, from_page=start, to_page=end - 1)
                trozo.save(destino, garbage=3, deflate=True)
            creados.append(destino)
            print(f"Creado: {destino}")
    print(f"\nListo. {total} páginas divididas en {len(creados)} ficheros.")
    return creados


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path, help="PDF a dividir.")
    parser.add_argument("--paginas", type=int, default=10, help="Páginas por trozo.")
    parser.add_argument("--salida", type=Path, default=None,
                        help="Carpeta de salida (por defecto, <nombre>_trozos junto al PDF).")
    args = parser.parse_args()
    salida = args.salida or args.pdf.with_name(f"{args.pdf.stem}_trozos")
    dividir(args.pdf, salida, args.paginas)


if __name__ == "__main__":
    main()
