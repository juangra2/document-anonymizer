"""Dos fotos de movil de un formulario (datos inventados), recto e inclinado.

Son los casos que motivaron el arreglo de las fotos: el inclinado salia tapado
entero ("ilegible") y el recto tapaba tambien las etiquetas.  Ahora ninguno se
tapa entero y ningun dato queda a la vista en la salida (se comprueba leyendola
con el OCR).
"""
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import settings

FOTOS = Path(__file__).resolve().parents[1] / "data" / "samples" / "fotos"
VALORES = ["45.892.104", "612 345 678", "alejandro.martin@correo.es", "ES21 1465 0100"]


@pytest.fixture(scope="module")
def pipeline():
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    return PdfAnonymizationPipeline()


def _leer(ruta: Path) -> str:
    from app.services.ocr.factory import get_word_ocr_engine
    from evaluation.lectura_salida import _aplanar

    return _aplanar(" ".join(t.text for t in get_word_ocr_engine(None).analyze_image(
        image_path=ruta, page_index=0).tokens))


@pytest.mark.parametrize("foto", ["formulario_recto.jpg", "formulario_inclinado.jpg"])
def test_la_foto_del_formulario_no_se_tapa_entera_ni_deja_datos(foto, tmp_path, pipeline):
    from app.services.image.imagen import anonimizar_imagen
    from evaluation.lectura_salida import _aplanar, aparece

    entrada = FOTOS / foto
    salida = tmp_path / foto
    informe = anonimizar_imagen(entrada, salida, "jpeg", lambda e, s: pipeline.process(
        e, s, "t", settings.default_redaction_mode, False, frozenset()))
    assert not informe.pages_redacted_in_full, "se ha rendido con una foto normal"
    assert Image.open(salida).size == Image.open(entrada).size
    leido_entrada, leido_salida = _leer(entrada), _leer(salida)
    assert "FORMULARIO" in leido_salida, "el titulo tiene que verse"
    for valor in VALORES:
        v = _aplanar(valor)
        if aparece(v, leido_entrada):
            assert not aparece(v, leido_salida), f"se lee {valor}"
