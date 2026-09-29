"""¿Degrada el tope de pixeles el OCR en texto pequeno?

El punto A del arreglo de las puertas de tamano propone dejar de saltarse las
paginas con imagenes enormes y, en su lugar, reducirlas antes de analizarlas.
La pregunta que decide si eso vale es si al reducir se pierde texto pequeno —la
letra menuda de un certificado, el pie de pagina, el numero de expediente—,
porque ahi es donde vive parte del PII.

Hoy una A4 escaneada a 600 DPI son 35 MP y se salta entera: sale sin mirar.  El
tope la convertiria en una imagen normal.  Esto mide que se pierde por el camino
en vez de suponerlo.

Metodo: se toma un documento con su verdad-terreno, se rasteriza a 600 DPI para
imitar el expediente archivado a buena resolucion, y se lee con OCR a distintos
topes de pixeles.  Se cuenta cuantos datos de la verdad-terreno aparecen en cada
lectura, con la misma comparacion tolerante a erratas que usa la lectura de
salidas: exigir identidad exacta castigaria al OCR por una tilde.
"""
from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
CORPUS = RAIZ.parent / "corpus-generator" / "output"

DPI_ORIGEN = 600          # el expediente archivado que hoy se salta
TOPES_MP = (None, 24.0, 12.0, 8.0, 6.0, 4.0)


def reducir(imagen: Image.Image, tope_mp: float | None) -> Image.Image:
    if tope_mp is None:
        return imagen
    mp = imagen.width * imagen.height / 1e6
    if mp <= tope_mp:
        return imagen
    factor = (tope_mp / mp) ** 0.5
    return imagen.resize(
        (max(1, int(imagen.width * factor)), max(1, int(imagen.height * factor))),
        Image.LANCZOS,
    )


def leer(imagen: Image.Image) -> str:
    from app.services.ocr.factory import get_word_ocr_engine
    from app.services.storage.temp_storage import scratch_dir

    with scratch_dir(prefix="tope_") as td:
        ruta = Path(td) / "p.png"
        imagen.save(ruta)
        pagina = get_word_ocr_engine(None).analyze_image(image_path=ruta, page_index=0)
    return " ".join(t.text for t in pagina.tokens)


def main(nombres: list[str]) -> None:
    from evaluation.lectura_salida import _aplanar, aparece

    for nombre in nombres:
        pdf = CORPUS / "pdfs" / f"{nombre}.pdf"
        etiqueta = json.loads(
            (CORPUS / "labels" / f"{nombre}.json").read_text(encoding="utf-8"))
        datos = sorted({(e["text"], e["type"]) for e in etiqueta["entities"]
                        if len(_aplanar(e["text"])) >= 4})

        with fitz.open(pdf) as doc:
            pix = doc[0].get_pixmap(
                matrix=fitz.Matrix(DPI_ORIGEN / 72.0, DPI_ORIGEN / 72.0))
        original = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")

        print(f"\n{nombre}  ({len(datos)} datos vigilados en la pagina 1)")
        print(f"  origen a {DPI_ORIGEN} DPI: {original.width}x{original.height} = "
              f"{original.width*original.height/1e6:.1f} MP")
        print(f"  {'tope':>8s} {'pixeles':>13s} {'MP':>6s} {'leidos':>8s} {'%':>6s}")

        for tope in TOPES_MP:
            img = reducir(original, tope)
            texto = _aplanar(leer(img))
            encontrados = sum(1 for valor, _ in datos if aparece(_aplanar(valor), texto))
            etiqueta_tope = "sin tope" if tope is None else f"{tope:.0f} MP"
            print(f"  {etiqueta_tope:>8s} {img.width}x{img.height:<7d} "
                  f"{img.width*img.height/1e6:6.1f} {encontrados:5d}/{len(datos):<3d} "
                  f"{100*encontrados/max(1,len(datos)):5.0f}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["certificado_empadronamiento_0001"])
