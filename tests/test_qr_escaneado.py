"""Un QR escaneado que zbar y OpenCV no leen, zxing-cpp si.

Se sigue exigiendo DECODIFICAR para tachar un codigo: es lo que evita volver a
tachar diagramas y sellos enteros (TOMO_02, TOMO_03).  Lo que cambia es quien
decodifica.  Medido en el corpus escaneado: el detector encontraba el QR en 4
de 16 documentos; con zxing-cpp, en 11, sin ninguna deteccion en paginas sin QR
ni en el plano real.  Los 5 que siguen sin leerse son escaneos de 120-150 ppp
con el QR a ~1,4 pixeles por modulo: no los decodifica nadie.

Las imagenes son recortes de dos paginas del corpus sintetico (datos
inventados), pegados en una pagina en blanco del tamano del render.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.services.vision.code_detector import detect_redactable_codes

_DIR = Path(__file__).resolve().parents[1] / "samples" / "qr"


def _pagina_con(recorte: str, donde: tuple[int, int]) -> Image.Image:
    pagina = Image.new("RGB", (1654, 2340), "white")
    pagina.paste(Image.open(_DIR / recorte).convert("RGB"), donde)
    return pagina


@pytest.mark.parametrize("recorte, donde", [
    ("escaneado_notificacion_0009.png", (156, 2019)),
    ("escaneado_empadronamiento_0004.png", (997, 1382)),
])
def test_un_qr_escaneado_se_decodifica_y_se_tacha(recorte, donde):
    codigos = detect_redactable_codes(_pagina_con(recorte, donde))
    assert len(codigos) == 1
    rect, texto = codigos[0]
    assert texto.strip(), "se tacha porque decodifica, no por la forma"
    assert rect.x0 <= donde[0] + 40 and rect.y0 <= donde[1] + 40


def test_una_pagina_en_blanco_no_da_codigos():
    assert detect_redactable_codes(Image.new("RGB", (1654, 2340), "white")) == []
