"""¿Que se pierde en las regiones que MinerU no lee, y que costaria leerlas?

Desde a23d7e8 no se pide al VLM el contenido de los bloques no textuales
("image", "image_block", "chart"): extraerlos tardaba unos 27 minutos en un
escaneo y el resultado se descartaba.  Se supuso que el repliegue al OCR de
palabras lo cubriria, pero ese repliegue solo actua si la pagina ENTERA sale
vacia.  Una figura dentro de una pagina con texto —una copia de DNI pegada, una
foto, un sello— no la lee nadie.

Compara dos formas de complementar MinerU con EasyOCR:

  A. "figuras": solo las palabras de EasyOCR dentro de los bloques que MinerU
     marco como imagen o grafico, que son los que dejo sin leer A PROPOSITO.
  B. "fuera de todo": cualquier palabra de EasyOCR que no caiga en ningun
     bloque leido por MinerU.  Ahi viven tambien bordes de tabla y texturas de
     sello, que pueden traer falsos positivos.

Para cada opcion: cuanto texto anade, cuantas entidades encuentra el analizador
en el, cuantas corresponden a datos reales de la etiqueta (recuperados) y
cuantas no (falsos positivos).
"""
from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path

import fitz

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
CORPUS = RAIZ.parent / "corpus-generator" / "output"


def _centro_en(token, rects) -> bool:
    cx = (token.rect.x0 + token.rect.x1) / 2
    cy = (token.rect.y0 + token.rect.y1) / 2
    return any(r.contains(fitz.Point(cx, cy)) for r in rects)


def _texto(tokens) -> str:
    ordenados = sorted(tokens, key=lambda t: (round(t.rect.y0 / 20), t.rect.x0))
    return " ".join(t.text for t in ordenados)


def main() -> None:
    from app.core.config import settings
    from app.services.ocr.factory import get_ocr_engine, get_word_ocr_engine
    from app.services.ocr.mineru_engine import _blocks_to_tokens, _norm_bbox_to_rect
    from app.services.pdf.full_redaction import classify_page
    from app.services.pdf.hybrid_redactor import paginas_para_ocr
    from app.services.pdf.redactor import redact_pdf_digital
    from app.services.pdf.scanned_redactor import _render_page_to_image
    from app.services.pii.analyzer import PiiAnalyzer
    from app.services.storage.temp_storage import scratch_dir
    from evaluation.lectura_salida import _aplanar, aparece
    from PIL import Image

    analizador = PiiAnalyzer()
    mineru = get_ocr_engine()
    cliente = mineru._get_client()
    easy = get_word_ocr_engine(None)
    saltar = [t.strip() for t in settings.ocr_mineru_skip_block_types.split(",") if t.strip()]

    stats = {o: collections.Counter() for o in ("A", "B")}
    fp_tipo = {o: collections.Counter() for o in ("A", "B")}
    dni_rescatados = {o: 0 for o in ("A", "B")}
    paginas_con_figura = 0
    paginas = 0

    trabajos = []
    for carpeta, etiquetas in (("scanned_mitad_pdfs", "scanned_mitad_labels"),
                               ("hybrid_mitad_pdfs", "hybrid_mitad_labels")):
        for pdf in sorted((CORPUS / carpeta).glob("*.pdf")):
            trabajos.append((pdf, CORPUS / etiquetas / f"{pdf.stem}.json"))

    for pdf, etiqueta_path in trabajos:
        etiqueta = json.loads(etiqueta_path.read_text(encoding="utf-8"))
        verdad = {_aplanar(e["text"]) for e in etiqueta["entities"]
                  if len(_aplanar(e["text"])) >= 4}
        with scratch_dir(prefix="figuras_") as td:
            origen = pdf
            ocr_idx = paginas_para_ocr(pdf)
            if "hibrido" in pdf.stem:
                origen = Path(td) / "inter.pdf"
                redact_pdf_digital(input_path=pdf, output_path=origen, job_id="m",
                                   analyzer=analizador, mode="strict")
            with fitz.open(origen) as doc:
                for i, page in enumerate(doc):
                    if i not in ocr_idx:
                        continue
                    paginas += 1
                    img = _render_page_to_image(page, dpi=settings.ocr_pdf_render_dpi)
                    ruta = Path(td) / f"p{i}.png"
                    img.save(ruta)
                    bloques = cliente.two_step_extract(Image.open(ruta).convert("RGB"),
                                                       not_extract_list=saltar or None)
                    figuras = [
                        _norm_bbox_to_rect(getattr(b, "bbox", None) or [0, 0, 1, 1],
                                           img.width, img.height)
                        for b in bloques
                        if (getattr(b, "type", "") or "").lower() in saltar
                    ]
                    leidos = [t.rect for t in _blocks_to_tokens(bloques, img.width, img.height)]
                    palabras = easy.analyze_image(image_path=ruta, page_index=i).tokens
                    if figuras:
                        paginas_con_figura += 1

                    texto_mineru = _texto(_blocks_to_tokens(bloques, img.width, img.height))
                    opciones = {
                        "A": [t for t in palabras if _centro_en(t, figuras)],
                        "B": [t for t in palabras if not _centro_en(t, leidos)],
                    }
                    for o, anadidas in opciones.items():
                        texto = _texto(anadidas)
                        stats[o]["palabras"] += len(anadidas)
                        stats[o]["caracteres"] += len(texto)
                        for e in analizador.analyze(texto, mode="strict"):
                            valor = _aplanar(texto[e.start:e.end])
                            if len(valor) < 4:
                                continue
                            stats[o]["entidades"] += 1
                            if any(aparece(valor, v) or aparece(v, valor) for v in verdad):
                                stats[o]["recuperadas"] += 1
                            else:
                                stats[o]["falsos_positivos"] += 1
                                fp_tipo[o][e.entity_type] += 1
                        antes = classify_page(texto_mineru)
                        despues = classify_page(texto_mineru + "\n" + texto)
                        if despues and not antes:
                            dni_rescatados[o] += 1
        print(f"  {pdf.stem[:44]} ok", flush=True)

    print("\n" + "=" * 72)
    print(f"paginas por OCR: {paginas}   con alguna figura que MinerU no leyo: {paginas_con_figura}")
    for o, nombre in (("A", "solo figuras"), ("B", "fuera de todo")):
        s = stats[o]
        print(f"\nOPCION {o} ({nombre})")
        print(f"  anade {s['palabras']} palabras / {s['caracteres']} caracteres")
        print(f"  entidades encontradas: {s['entidades']}   recuperadas (datos reales): "
              f"{s['recuperadas']}   falsos positivos: {s['falsos_positivos']}")
        print(f"  paginas que pasan a clasificarse como documento de identidad/impreso: "
              f"{dni_rescatados[o]}")
        if fp_tipo[o]:
            print(f"  falsos positivos por tipo: {dict(fp_tipo[o].most_common())}")


if __name__ == "__main__":
    main()
