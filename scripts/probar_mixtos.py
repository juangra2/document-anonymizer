"""Pasa los documentos mixtos por el pipeline y LEE la salida, no cuenta rects.

Un rect pintado no demuestra que el dato este tapado: cada pagina de la salida
se rasteriza y se pasa por OCR, y se busca en ese texto el dato que deberia
haber desaparecido.  Se mide ademas la "negrura" de cada pagina, que es lo que
distingue una redaccion total de una selectiva.

Todos los casos comparten un solo proceso: cargar los modelos cuesta mas que
procesar los documentos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

MIXTOS = RAIZ / "samples" / "mixtos"

# --- que datos NO deben sobrevivir, por fichero ----------------------------
DNI_0003 = ["Ruiz Navarro", "39127242X", "15/05/1992", "Plaza de la Merced"]
DNI_0001 = ["Navarro Martin", "79313986G", "03/01/1964", "Avda. de Cervantes"]
NOTIF = ["Antonio Cano Molina", "65959727M", "Camino de Valencia"]
AUTOLIQ = ["Rosa Ortega Garrido", "Y3339823F", "Travesia dels Angels"]
TEXTO = ["Vicente Alborch", "46821357G"]

PRUEBAS = [
    ("caso1_expediente_con_dni.pdf",        DNI_0003),
    ("caso2a_pagina_mixta_con_texto.pdf",   DNI_0003 + TEXTO),
    ("caso2b_pagina_mixta_sin_texto.pdf",   DNI_0003 + TEXTO),
    ("caso3_girado_en_hibrido.pdf",         DNI_0001 + DNI_0003 + TEXTO),
    ("control_dni_girado_solo.pdf",         DNI_0001),
    ("caso4_dni_en_hibrido.pdf",            DNI_0003 + NOTIF + TEXTO),
    ("caso3b_girado_a4_en_hibrido.pdf",     DNI_0001 + TEXTO),
    ("control_dni_girado_a4.pdf",           DNI_0001),
    ("control_notificacion_escaneada.pdf",  NOTIF),
    ("caso5a_autoliquidacion_mas_ajena.pdf", AUTOLIQ + TEXTO),
    ("caso5b_autoliquidacion_mas_mencion.pdf", AUTOLIQ + TEXTO),
]


def aplanar(texto: str) -> str:
    fuera = " -.,:;/()<"
    for c in fuera:
        texto = texto.replace(c, "")
    return texto.upper()


def negrura(page: fitz.Page) -> float:
    """Fraccion de pixeles oscuros: 1.0 es una pagina tachada entera."""
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY)
    datos = pix.samples
    oscuros = sum(1 for b in datos if b < 60)
    return oscuros / max(1, len(datos))


def ocr_de_pagina(pdf: Path, indice: int) -> str:
    from app.services.ocr.factory import get_word_ocr_engine
    from app.services.storage.temp_storage import scratch_dir

    doc = fitz.open(pdf)
    pix = doc[indice].get_pixmap(matrix=fitz.Matrix(200 / 72.0, 200 / 72.0))
    doc.close()
    with scratch_dir(prefix="leer_salida_") as td:
        img = Path(td) / "p.png"
        img.write_bytes(pix.tobytes("png"))
        pagina = get_word_ocr_engine(None).analyze_image(image_path=img, page_index=indice)
    return " ".join(t.text for t in pagina.tokens)


def procesar(pipeline, entrada: Path, buscar: list[str]) -> None:
    salida = MIXTOS / f"{entrada.stem}_ANONIMIZADO.pdf"
    print("\n" + "=" * 74)
    print(entrada.name)
    print("=" * 74)

    informe = pipeline.process(
        input_path=entrada, output_path=salida, job_id=entrada.stem, mode="strict",
    )
    d = informe.model_dump() if hasattr(informe, "model_dump") else informe.__dict__

    for clave in ("document_type", "pages", "ocr_pages_processed",
                  "entities_detected", "entities_redacted", "status",
                  "manual_review_required", "pages_redacted_in_full"):
        if clave in d and d[clave] not in (None, []):
            print(f"  {clave:24s} = {d[clave]}")
    for w in d.get("warnings", []) or []:
        print(f"  aviso: {w[:150]}")

    doc = fitz.open(salida)
    print(f"  negrura por pagina: "
          f"{[f'{negrura(p):.2f}' for p in doc]}")
    paginas = doc.page_count
    doc.close()

    texto_total = []
    for i in range(paginas):
        texto_total.append(ocr_de_pagina(salida, i))
    plano = aplanar(" ".join(texto_total))

    fugas = [x for x in buscar if aplanar(x) in plano]
    print("  lectura OCR de la salida:")
    for dato in buscar:
        print(f"    {'FUGA ' if aplanar(dato) in plano else 'ok   '} {dato}")
    print(f"  --> {len(fugas)} fuga(s) de {len(buscar)} datos vigilados")


def main(nombres: list[str]) -> None:
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    pipeline = PdfAnonymizationPipeline()
    for nombre, buscar in PRUEBAS:
        if nombres and not any(n in nombre for n in nombres):
            continue
        entrada = MIXTOS / nombre
        if not entrada.exists():
            print(f"(falta {nombre})")
            continue
        try:
            procesar(pipeline, entrada, buscar)
        except Exception as exc:
            print(f"  ERROR procesando {nombre}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
