"""Prueba de un plano real: que hace el pipeline con el, pagina a pagina.

Solo abre el fichero que se le pasa POR RUTA (nunca lista data/samples/real/), y todo
lo que genera —el PDF anonimizado y las imagenes para revisarlo a ojo— va junto
al original, dentro de data/samples/real/.  Por pantalla solo salen recuentos y
tipos, nunca valores: el plano es un documento real.

    python scripts/probar_plano.py data/samples/real/plano.pdf
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
PRIVADA = (RAIZ / "data" / "samples" / "real").resolve()


def _mm(puntos: float) -> int:
    return round(puntos * 25.4 / 72)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("plano", type=Path, help="ruta del PDF, dentro de data/samples/real/")
    ap.add_argument("--dpi-previa", type=int, default=40,
                    help="resolucion de las imagenes de revision (baja: solo para ver las barras)")
    args = ap.parse_args()

    entrada = args.plano.resolve()
    if entrada.parent != PRIVADA:
        sys.exit("El plano tiene que estar en data/samples/real/: ahi van tambien sus derivados.")

    import fitz
    from app.services.pdf.pipeline import PdfAnonymizationPipeline
    from app.services.pdf.plan_pages import is_image_dominant_page, is_plan_page

    print(f"Plano: {entrada.name}")
    with fitz.open(entrada) as doc:
        for i, p in enumerate(doc):
            imgs = p.get_images(full=True)
            mayor = max(((im[2] * im[3]) for im in imgs), default=0) / 1e6
            print(f"  pag {i + 1}: {_mm(p.rect.width)}x{_mm(p.rect.height)} mm, "
                  f"texto {len(p.get_text().strip())} car., imagenes {len(imgs)} "
                  # get_cdrawings, la variante rapida (la que usa el pipeline):
                  # get_drawings construye en Python cada trazo y en un plano
                  # vectorial tardaba mas que el propio proceso.
                  f"(la mayor {mayor:.1f} MP), trazos {len(p.get_cdrawings())}, "
                  f"plano={is_plan_page(p)}, imagen_dominante={is_image_dominant_page(p)}")

    salida = PRIVADA / f"{entrada.stem}_ANONIMIZADO.pdf"
    inicio = time.perf_counter()
    rep = PdfAnonymizationPipeline().process(input_path=entrada, output_path=salida,
                                             job_id="plano", mode="strict")
    segundos = time.perf_counter() - inicio

    print(f"\nCamino: {rep.document_type}  estado: {rep.status}  tiempo: {segundos:.0f} s")
    print(f"Paginas: {rep.pages}  entidades: {rep.entities_detected}  tachados: {rep.entities_redacted}")
    print(f"Por tipo (solo recuentos): {dict(sorted(rep.entities_by_type.items()))}")
    print(f"Revision manual: {rep.manual_review_required}")
    for aviso in rep.warnings:
        print(f"  aviso: {aviso}")
    if rep.pages_redacted_in_full:
        print(f"  tachadas enteras: {[(p.page, p.reason) for p in rep.pages_redacted_in_full]}")

    # Imagenes de la salida para revisar las barras a ojo, junto al original.
    with fitz.open(salida) as doc:
        for i, p in enumerate(doc):
            p.get_pixmap(dpi=args.dpi_previa).save(PRIVADA / f"{entrada.stem}_previa_p{i + 1}.png")
    print(f"\nDerivados en data/samples/real/: {salida.name} y {entrada.stem}_previa_p*.png")


if __name__ == "__main__":
    main()
