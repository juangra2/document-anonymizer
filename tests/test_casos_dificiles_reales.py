"""Casos dificiles 1-6 con un documento de identidad REAL.

Los ficheros viven solo en data/samples/real/casos_dificiles/ (fuera de git; los
genera un script que tambien vive alli).  Si no estan, el test se salta.  Ningun
valor se escribe aqui ni sale en un mensaje: se leen en memoria, con OCR, de las
caras de la tarjeta, y las salidas se guardan junto a los originales.

  1. copia del DNI a tamano real en una pagina con texto;
  2. a mitad de escala;
  3. girada 90 grados;
  4. con un sello visible encima;
  5. expediente: formulario + oficio + copia escaneada;
  6. el expediente entero rasterizado, como un escaneo.
"""
import io
import re
import unicodedata
from pathlib import Path

import fitz
import pytest

BASE = Path(__file__).resolve().parents[1] / "data" / "samples" / "real" / "casos_dificiles"
CASOS = [("c1_dni_real", False), ("c2_dni_mitad", False), ("c3_dni_girado", True),
         ("c4_dni_sello", False), ("c5_expediente", False), ("c6_expediente_escaneado", False)]

pytestmark = [
    pytest.mark.lento,
    pytest.mark.skipif(not (BASE / "dni_anverso.png").exists(),
                       reason="sin los casos con datos reales en data/samples/real/casos_dificiles"),
]


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in t if not unicodedata.combining(c)).upper())


@pytest.fixture(scope="module")
def lector():
    import easyocr
    return easyocr.Reader(["es"], gpu=True, verbose=False)


@pytest.fixture(scope="module")
def campos(lector):
    """(campo, valor) de las dos caras, solo en memoria."""
    import numpy as np
    from PIL import Image

    from app.services.pii.analyzer import PiiAnalyzer

    analizador = PiiAnalyzer()
    out = []
    for cara in ("anverso", "reverso"):
        lineas = [t for _, t, _ in lector.readtext(np.array(Image.open(BASE / f"dni_{cara}.png").convert("RGB")))]
        texto = "\n".join(lineas)
        out += [(f"{cara}: {e.entity_type}", texto[e.start:e.end])
                for e in analizador.analyze(texto, mode="strict")
                if e.entity_type not in ("LOCATION", "ORGANIZATION")]
        out += [(f"{cara}: zona MRZ", l) for l in lineas if l.count("<") >= 4]
    return [(c, v) for c, v in out if len(_norm(v)) >= 5]


def _leer(lector, ruta: Path, girar: bool) -> str:
    import numpy as np
    from PIL import Image

    texto = ""
    for pag in fitz.open(ruta):
        img = Image.open(io.BytesIO(pag.get_pixmap(dpi=200).tobytes("png"))).convert("RGB")
        for g in ((0, 90, 270) if girar else (0,)):
            texto += " ".join(t for _, t, _ in lector.readtext(np.array(img.rotate(g, expand=True))))
    return _norm(texto)


@pytest.mark.parametrize("nombre,girar", CASOS)
def test_ningun_dato_del_dni_queda_legible(nombre, girar, lector, campos):
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    entrada = BASE / f"{nombre}.pdf"
    if not entrada.exists():
        pytest.skip("caso no generado")
    salidas = BASE / "salidas_test"
    salidas.mkdir(exist_ok=True)
    salida = salidas / f"{nombre}_ANON.pdf"
    PdfAnonymizationPipeline().process(input_path=entrada, output_path=salida, job_id="t", mode="strict")
    antes, despues = _leer(lector, entrada, girar), _leer(lector, salida, girar)
    # Solo cuentan los campos que el OCR lee en la entrada; el mensaje nombra
    # campos, nunca valores.
    a_la_vista = sorted({c for c, v in campos if _norm(v) in antes and _norm(v) in despues})
    assert a_la_vista == []
