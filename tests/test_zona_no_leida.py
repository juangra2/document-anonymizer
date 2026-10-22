"""La zona de lo que MinerU no leyo solo recoge lo que de verdad no leyo.

MinerU lee un bloque, y la caja de alguna palabra de EasyOCR asoma unos pixeles
fuera de ese bloque.  Si esa palabra entrara en la zona de lo no leido, el dato
se detectaria otra vez, con un falso positivo.

Quitarlas es seguro porque la geometria tapa TODAS las apariciones de un dato
en la pagina: si el texto ya esta en lo que leyo MinerU, se detecta ahi y la
barra se pinta tambien donde asome.
"""
import fitz

from app.services.ocr.models import OcrToken
from app.services.pdf.scanned_redactor import _zona_de_lo_no_leido


def _tok(texto: str, x0: float, y0: float, x1: float, y1: float) -> OcrToken:
    return OcrToken(text=texto, confidence=1.0, rect=fitz.Rect(x0, y0, x1, y1))


_MINERU = [_tok("Solicitante: Vicente Alborch, DNI 46821357G", 100, 100, 900, 140)]


def _textos(zona) -> str:
    return "" if zona is None else zona.text


def test_lo_que_mineru_ya_leyo_no_entra_aunque_asome():
    """La palabra asoma fuera del bloque, pero su texto ya se leyo."""
    easy = [_tok("46821357G", 880, 100, 990, 140)]       # sobresale por la derecha
    assert _zona_de_lo_no_leido(_MINERU, easy) is None


def test_lo_que_de_verdad_no_se_leyo_si_entra():
    """Un DNI pegado: MinerU no transcribe la figura, y la lee EasyOCR."""
    easy = [_tok("39127242X", 200, 900, 330, 940)]
    assert "39127242X" in _textos(_zona_de_lo_no_leido(_MINERU, easy))


def test_las_erratas_entre_motores_no_lo_cuelan():
    """El mismo dato leido con una cifra distinta sigue siendo el mismo."""
    easy = [_tok("4682I357G", 880, 100, 990, 140)]       # I por 1
    assert _zona_de_lo_no_leido(_MINERU, easy) is None


def test_una_palabra_con_parte_nueva_entra():
    """Si trae algo que MinerU no leyo, no se puede descartar."""
    easy = [_tok("Vicente Ruiz", 880, 100, 990, 140)]
    assert "Ruiz" in _textos(_zona_de_lo_no_leido(_MINERU, easy))


def test_sin_palabras_fuera_no_hay_zona():
    easy = [_tok("Vicente", 300, 105, 400, 135)]          # dentro del bloque
    assert _zona_de_lo_no_leido(_MINERU, easy) is None
