"""Casos dificiles 11-14 (datos inventados): lo que debe tacharse y lo que no.

Los genera scripts/casos_dificiles.py imitando documentos reales.  La version
digital se comprueba con la capa de texto de la salida; la escaneada, leyendo la
salida con OCR (lento).  Lo que fijan:

  11. banco: el IBAN y el titular se tachan tambien repetidos en el parrafo;
  12. bilingue a dos columnas: los rotulos de las dos columnas no se tachan;
  13. anexo apaisado: la pagina girada se segmenta con su geometria y el
      correo junto al borde inferior se tacha;
  14. firmas en una fila: el nombre del medio se tacha entero.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.casos_dificiles import generar  # noqa: E402

CASOS = ["c11_banco", "c12_bilingue", "c13_apaisada", "c14_firmas"]


@pytest.fixture(scope="module")
def casos(tmp_path_factory):
    destino = tmp_path_factory.mktemp("casos_dificiles")
    generar(destino)
    return destino


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in t if not unicodedata.combining(c)).upper())


def _esperado(casos, nombre):
    return json.loads((casos / f"{nombre}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("nombre", CASOS)
def test_version_digital(casos, nombre, tmp_path):
    from app.services.pdf.redactor import redact_pdf_digital
    from app.services.pii.analyzer import PiiAnalyzer

    salida = tmp_path / "out.pdf"
    redact_pdf_digital(casos / f"{nombre}_digital.pdf", salida, "t", PiiAnalyzer())
    texto = _norm(" ".join(p.get_text() for p in fitz.open(salida)))
    d = _esperado(casos, nombre)
    assert [v for v in d["tachar"] if _norm(v) in texto] == [], "datos a la vista"
    assert [c for c in d["conservar"] if _norm(c) not in texto] == [], "tachado de mas"


@pytest.mark.lento
@pytest.mark.parametrize("nombre", CASOS)
def test_version_escaneada(casos, nombre, tmp_path):
    import io

    import easyocr
    import numpy as np
    from PIL import Image

    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    salida = tmp_path / "out.pdf"
    PdfAnonymizationPipeline().process(input_path=casos / f"{nombre}_escaneado.pdf",
                                       output_path=salida, job_id="t", mode="strict")
    lector = easyocr.Reader(["es"], gpu=True, verbose=False)
    leido = ""
    for pag in fitz.open(salida):
        img = Image.open(io.BytesIO(pag.get_pixmap(dpi=200).tobytes("png"))).convert("RGB")
        leido += " ".join(t for _, t, _ in lector.readtext(np.array(img)))
    texto = _norm(leido)
    d = _esperado(casos, nombre)
    assert [v for v in d["tachar"] if _norm(v) in texto] == [], "datos a la vista"
