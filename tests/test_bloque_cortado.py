"""Un bloque de MinerU que acaba en un bucle esta CORTADO: lo de detras no se leyo.

MinerU puede leer un bloque y, a mitad, entrar en un bucle que repite un
caracter ("...3333333333").  Todo lo que venia detras —telefono, correo, IBAN—
desaparece.  Descartar el bloque entero seria peor: la primera mitad esta bien
leida.  Y el complemento con EasyOCR no lo rescataria si la caja del bloque
diera por leidas las palabras que cubre.

Por eso se recorta el bucle, el bloque queda marcado como cortado, y las
palabras de EasyOCR que caen dentro de un bloque cortado cuentan como NO leidas
—salvo las que ya esten en lo que MinerU si leyo—.
"""
from types import SimpleNamespace

import fitz

from app.services.ocr.mineru_engine import _blocks_to_tokens, recortar_bucle
from app.services.ocr.models import OcrToken
from app.services.pdf.scanned_redactor import _zona_de_lo_no_leido

_CORTADO = ("Nom i cognoms: Pilar Moreno Sanz D.N.I.: 70751845G Adreca postal: "
            "Camino de Valencia, 82, 28020" + "1131" + "3" * 25)


def test_se_recorta_el_bucle_y_se_marca():
    texto, cortado = recortar_bucle(_CORTADO)
    assert cortado
    assert texto.startswith("Nom i cognoms: Pilar Moreno Sanz")
    assert "3333" not in texto and "28020" in texto


def test_un_texto_normal_no_se_toca():
    texto = "Telefon: +34-656-457-838. Correu: pilar@example.org. Total 1.000,00"
    assert recortar_bucle(texto) == (texto, False)


def test_una_linea_de_puntos_para_firmar_no_es_un_bucle():
    for texto in ("Firma del interesado: ....................",
                  "Observaciones: ______________________",
                  "Total: ----------------------"):
        assert recortar_bucle(texto) == (texto, False), texto


def test_el_bloque_cortado_llega_marcado():
    bloques = [SimpleNamespace(content=_CORTADO, type="text", bbox=[0.1, 0.4, 0.9, 0.7])]
    (token,) = _blocks_to_tokens(bloques, page_width=1000, page_height=1400)
    assert token.cortado and "3333" not in token.text


def _tok(texto, x0, y0, x1, y1, cortado=False):
    return OcrToken(text=texto, confidence=1.0, rect=fitz.Rect(x0, y0, x1, y1),
                    cortado=cortado)


def test_lo_que_cae_en_un_bloque_cortado_y_mineru_no_leyo_entra():
    mineru = [_tok(recortar_bucle(_CORTADO)[0], 100, 500, 900, 1000, cortado=True)]
    easy = [_tok("ES80", 200, 900, 260, 930), _tok("00499069", 270, 900, 380, 930)]
    zona = _zona_de_lo_no_leido(mineru, easy)
    assert zona is not None and "00499069" in zona.text


def test_lo_que_cae_en_un_bloque_cortado_pero_si_se_leyo_no_entra():
    mineru = [_tok(recortar_bucle(_CORTADO)[0], 100, 500, 900, 1000, cortado=True)]
    easy = [_tok("70751845G", 200, 600, 330, 630)]
    assert _zona_de_lo_no_leido(mineru, easy) is None


def test_en_un_bloque_entero_sigue_contando_como_leido():
    mineru = [_tok("Nom i cognoms: Pilar Moreno Sanz", 100, 500, 900, 1000)]
    easy = [_tok("00499069", 270, 900, 380, 930)]
    assert _zona_de_lo_no_leido(mineru, easy) is None
