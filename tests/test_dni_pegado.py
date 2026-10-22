"""Un DNI pegado dentro de una pagina con texto no puede entregarse legible.

Una pagina con texto digital y una imagen escaneada dentro, ambos con datos
personales: el nombre, el numero de DNI y la fecha de nacimiento de la copia
pegada no pueden quedar legibles.

La prueba lee el PDF entregado, no la lista de entidades: la evaluacion por
entidades daria por buenos documentos con la copia legible.

Depende del corpus generado en el repositorio vecino; si no esta, se salta.
"""
import json
from pathlib import Path

import pytest

CORPUS = Path(__file__).resolve().parents[2] / "corpus-generator" / "output"
QUE_FALLABAN = (
    "hibrido_pagina_mixta_0020",
    "hibrido_pagina_mixta_0040",
    "hibrido_pagina_mixta_0041",
)


def _documento(nombre: str):
    pdf = CORPUS / "hybrid_mitad_pdfs" / f"{nombre}.pdf"
    etiqueta = CORPUS / "hybrid_mitad_labels" / f"{nombre}.json"
    if not pdf.exists() or not etiqueta.exists():
        pytest.skip(f"corpus hibrido no disponible ({nombre})")
    return pdf, json.loads(etiqueta.read_text(encoding="utf-8"))


@pytest.mark.lento
@pytest.mark.parametrize("nombre", QUE_FALLABAN)
def test_los_datos_del_dni_pegado_no_se_leen_en_la_salida(nombre, tmp_path):
    from app.services.pdf.pipeline import PdfAnonymizationPipeline
    from evaluation.lectura_salida import FUGA, comprobar_documento

    pdf, etiqueta = _documento(nombre)
    salida = tmp_path / "salida.pdf"
    PdfAnonymizationPipeline().process(
        input_path=pdf, output_path=salida, job_id=nombre, mode="strict",
    )

    # Solo los datos que viven en la IMAGEN pegada: es lo que se comprueba.
    solo_imagen = dict(etiqueta)
    solo_imagen["entities"] = [e for e in etiqueta["entities"] if e.get("en_imagen")]
    assert solo_imagen["entities"], "la etiqueta no marca que datos van en la imagen"

    resultado = comprobar_documento(pdf, salida, solo_imagen)
    fugas = [d for d in resultado.detalle if d["estado"] == FUGA]
    assert not fugas, (
        f"{len(fugas)} dato(s) de la copia de DNI pegada siguen legibles: "
        + ", ".join(sorted({d['tipo'] for d in fugas}))
    )
