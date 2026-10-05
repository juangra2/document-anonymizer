"""¿Por que pierde MinerU texto que NO es una figura?

La medicion de figuras_sin_leer encontro 67 datos reales que EasyOCR lee y
MinerU no, y solo 14 estaban dentro de figuras —lo que a23d7e8 le pidio no
leer—.  Los otros 53 los pierde por otra causa.  Esto la busca.  Solo
diagnostico: no cambia nada.

Para cada palabra de EasyOCR que cae fuera de todo bloque leido por MinerU, se
mira QUE bloque de MinerU hay debajo, si hay alguno, y por que no aporto texto:

  figura            bloque de imagen/grafico: no se le pide contenido (a23d7e8)
  degenerado        MinerU devolvio texto, pero el guardia de bucles del VLM lo
                    descarto (_is_degenerate_content)
  bloque vacio      MinerU detecto un bloque de texto y lo devolvio vacio
  tabla, fuera fila dentro de una tabla, pero en ninguna de las filas que se
                    sacaron de su HTML
  sin bloque        MinerU no detecto ningun bloque en ese sitio

Y para "sin bloque", donde esta: margen superior o inferior, margen lateral,
letra pequena, texto girado, o cuerpo de la pagina.

Solo cuenta palabras que forman parte de un dato real de la etiqueta: es lo que
importa, y separa la perdida que duele del ruido que da igual.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

import fitz

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
CORPUS = RAIZ.parent / "corpus-generator" / "output"

_HOLGURA = 6


def _dentro(rect: fitz.Rect, punto: fitz.Point, holgura: float = 0) -> bool:
    r = fitz.Rect(rect.x0 - holgura, rect.y0 - holgura, rect.x1 + holgura, rect.y1 + holgura)
    return r.contains(punto)


def main() -> None:
    from app.core.config import settings
    from app.services.ocr.factory import get_ocr_engine, get_word_ocr_engine
    from app.services.ocr.mineru_engine import (
        _blocks_to_tokens, _is_degenerate_content, _norm_bbox_to_rect,
    )
    from app.services.pdf.hybrid_redactor import paginas_para_ocr
    from app.services.pdf.redactor import redact_pdf_digital
    from app.services.pdf.scanned_redactor import _render_page_to_image
    from app.services.pii.analyzer import PiiAnalyzer
    from app.services.storage.temp_storage import scratch_dir
    from evaluation.lectura_salida import _aplanar, aparece
    from PIL import Image

    analizador = PiiAnalyzer()
    cliente = get_ocr_engine()._get_client()
    easy = get_word_ocr_engine(None)
    saltar = {t.strip() for t in settings.ocr_mineru_skip_block_types.split(",") if t.strip()}

    causas = collections.Counter()
    sin_bloque_donde = collections.Counter()
    tipos_por_causa = collections.defaultdict(collections.Counter)
    ejemplos = collections.defaultdict(list)

    trabajos = []
    for carpeta, etiquetas in (("scanned_mitad_pdfs", "scanned_mitad_labels"),
                               ("hybrid_mitad_pdfs", "hybrid_mitad_labels")):
        for pdf in sorted((CORPUS / carpeta).glob("*.pdf")):
            trabajos.append((pdf, CORPUS / etiquetas / f"{pdf.stem}.json"))

    for pdf, etiqueta_path in trabajos:
        etiqueta = json.loads(etiqueta_path.read_text(encoding="utf-8"))
        datos = [(e["type"], _aplanar(e["text"])) for e in etiqueta["entities"]
                 if len(_aplanar(e["text"])) >= 4]
        with scratch_dir(prefix="nolei_") as td:
            origen = pdf
            ocr_idx = paginas_para_ocr(pdf)
            if "hibrido" in pdf.stem:
                origen = Path(td) / "inter.pdf"
                redact_pdf_digital(input_path=pdf, output_path=origen, job_id="n",
                                   analyzer=analizador, mode="strict")
            with fitz.open(origen) as doc:
                for i, page in enumerate(doc):
                    if i not in ocr_idx:
                        continue
                    img = _render_page_to_image(page, dpi=settings.ocr_pdf_render_dpi)
                    ruta = Path(td) / f"p{i}.png"
                    img.save(ruta)
                    bloques = cliente.two_step_extract(Image.open(ruta).convert("RGB"),
                                                       not_extract_list=sorted(saltar) or None)
                    leidos = [t.rect for t in _blocks_to_tokens(bloques, img.width, img.height)]
                    info = []
                    for b in bloques:
                        tipo = (getattr(b, "type", "") or "").lower()
                        contenido = (getattr(b, "content", "") or "").strip()
                        rect = _norm_bbox_to_rect(getattr(b, "bbox", None) or [0, 0, 1, 1],
                                                  img.width, img.height)
                        if tipo in saltar:
                            causa = "figura"
                        elif contenido and _is_degenerate_content(contenido):
                            causa = "degenerado"
                        elif not contenido:
                            causa = "bloque vacio"
                        elif tipo == "table":
                            causa = "tabla, fuera de fila"
                        else:
                            causa = None   # bloque leido; si la palabra cae aqui, esta leida
                        info.append((rect, causa))

                    palabras = easy.analyze_image(image_path=ruta, page_index=i).tokens
                    alturas = [t.rect.height for t in palabras if t.rect.height > 0]
                    alto_med = statistics.median(alturas) if alturas else 0

                    for w in palabras:
                        c = fitz.Point((w.rect.x0 + w.rect.x1) / 2, (w.rect.y0 + w.rect.y1) / 2)
                        if any(_dentro(r, c, _HOLGURA) for r in leidos):
                            continue
                        plano = _aplanar(w.text)
                        if len(plano) < 3:
                            continue
                        # ¿forma parte de un dato real?
                        tipo_dato = next((t for t, v in datos
                                          if aparece(plano, v) or aparece(v, plano)), None)
                        if tipo_dato is None:
                            continue
                        causa = next((cz for r, cz in info if cz and _dentro(r, c)), None)
                        if causa is None:
                            causa = "sin bloque"
                            if c.y < img.height * 0.09 or c.y > img.height * 0.91:
                                donde = "margen superior/inferior"
                            elif c.x < img.width * 0.07 or c.x > img.width * 0.93:
                                donde = "margen lateral"
                            elif w.rect.height > w.rect.width * 1.5 and len(w.text) > 2:
                                donde = "texto girado"
                            elif alto_med and w.rect.height < alto_med * 0.7:
                                donde = "letra pequena"
                            else:
                                donde = "cuerpo de la pagina"
                            sin_bloque_donde[donde] += 1
                        causas[causa] += 1
                        tipos_por_causa[causa][tipo_dato] += 1
                        if len(ejemplos[causa]) < 3:
                            ejemplos[causa].append(f"{pdf.stem}: {tipo_dato}")
        print(f"  {pdf.stem[:44]} ok", flush=True)

    print("\n" + "=" * 70)
    print("PALABRAS DE DATOS REALES QUE MINERU NO LEE, POR CAUSA")
    total = sum(causas.values())
    for causa, n in causas.most_common():
        print(f"  {causa:22s} {n:4d}  ({100*n/max(1,total):4.0f} %)   tipos: "
              f"{dict(tipos_por_causa[causa].most_common(4))}")
        for e in ejemplos[causa]:
            print(f"      ej. {e}")
    if sin_bloque_donde:
        print("\n'sin bloque', segun donde estan:")
        for donde, n in sin_bloque_donde.most_common():
            print(f"  {donde:28s} {n:4d}")


if __name__ == "__main__":
    main()
