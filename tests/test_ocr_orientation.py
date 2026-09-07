"""Corrección de orientación en escaneos girados y aviso de OCR ilegible.

Los umbrales se fijaron midiendo sobre el corpus: páginas escaneadas normales
dan 14.2–35.1 caracteres por token; la misma página girada, 2.1–2.8; corregida,
10.5–12.0.  Estos tests usan un OCR simulado para ser rápidos y deterministas.
"""
import fitz
from PIL import Image

from app.core.config import settings
from app.services.ocr.models import OcrPage, OcrToken
from app.services.ocr.orientation import resolve_orientation, text_quality
from app.services.pdf.scanned_redactor import _restore


def _page(words: list[str]) -> OcrPage:
    tokens = [
        OcrToken(text=w, confidence=0.9, rect=fitz.Rect(0, i * 10, 50, i * 10 + 8))
        for i, w in enumerate(words)
    ]
    return OcrPage(page_index=0, width=100, height=100, tokens=tokens)


_NOISE = _page(["8", "1", "g1", "%", ":", "~", "5", "H", "1", "2", "p", "4"])
_LEGIBLE = _page([
    "DOCUMENTO NACIONAL DE IDENTIDAD", "APELLIDOS COGNOMS", "NACIONALIDAD ESP",
    "NUM SOPORTE BH274915", "VALIDEZ 14 03 2029", "REINO DE ESPANA",
    "LUGAR DE NACIMIENTO", "IDESPBH274915<<<<<", "VELA<RIUS<<LUIS<",
])


def test_text_quality_separates_noise_from_text():
    assert text_quality(_NOISE) < settings.ocr_min_chars_per_token
    assert text_quality(_LEGIBLE) > settings.ocr_min_chars_per_token


def test_legible_first_pass_does_not_retry():
    calls = []

    def run(image):
        calls.append(image.size)
        return _LEGIBLE

    result = resolve_orientation(Image.new("RGB", (100, 200)), run)
    assert result.angle == 0
    assert len(calls) == 1, "una página legible no debe pagar pases extra"
    assert result.poor_quality is False


def test_rotated_page_is_detected_and_corrected():
    def run(image):
        # Solo la orientación apaisada (tras girar 90°) es legible.
        return _LEGIBLE if image.width > image.height else _NOISE

    result = resolve_orientation(Image.new("RGB", (100, 200)), run)
    assert result.angle in (90, 270)
    assert result.ocr_page is _LEGIBLE
    assert result.poor_quality is False


def test_illegible_in_every_orientation_is_flagged():
    # El fallo silencioso: sin este aviso el usuario creería el documento limpio.
    result = resolve_orientation(Image.new("RGB", (100, 200)), lambda img: _NOISE)
    assert result.poor_quality is True


def test_nearly_blank_page_does_not_pay_for_retries():
    calls = []

    def run(image):
        calls.append(1)
        return _page(["a", "b"])          # menos tokens que el mínimo

    resolve_orientation(Image.new("RGB", (100, 200)), run)
    assert len(calls) == 1


def test_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ocr_auto_rotate_enabled", False)
    calls = []

    def run(image):
        calls.append(1)
        return _NOISE

    result = resolve_orientation(Image.new("RGB", (100, 200)), run)
    assert result.angle == 0 and len(calls) == 1


def test_restore_returns_the_original_geometry():
    image = Image.new("RGB", (120, 300))
    for angle in (0, 90, 180, 270):
        rotated = image.rotate(angle, expand=True) if angle else image
        assert _restore(rotated, angle).size == image.size


def test_choice_between_rotations_uses_confidence_not_only_length():
    """Elegir 90° o 270° no puede decidirse por longitud de token.

    A 270° el texto sale invertido y produce tokens IGUAL de largos que a 90°
    (medido sobre un DNI real: 10.5 frente a 9.8 caracteres por token), de modo
    que la página se leía al revés y las señales del documento no disparaban.
    La confianza del OCR sí los separa (0.21 frente a 0.39).
    """
    from app.services.ocr.orientation import orientation_score

    legible = OcrPage(page_index=0, width=100, height=100, tokens=[
        OcrToken(text="DOCUMENTO NACIONAL", confidence=0.39,
                 rect=fitz.Rect(0, 0, 50, 8)),
        OcrToken(text="NACIONALIDAD ESPA", confidence=0.39,
                 rect=fitz.Rect(0, 10, 50, 18)),
    ])
    reversed_text = OcrPage(page_index=0, width=100, height=100, tokens=[
        OcrToken(text=">>>>>>>>>nyaf>>9I3", confidence=0.21,
                 rect=fitz.Rect(0, 0, 50, 8)),
        OcrToken(text="ayYo2>>>>>>*24837429", confidence=0.21,
                 rect=fitz.Rect(0, 10, 50, 18)),
    ])
    # El texto invertido tiene tokens MÁS largos...
    assert text_quality(reversed_text) > text_quality(legible)
    # ...pero la puntuación de orientación elige el legible.
    assert orientation_score(legible) > orientation_score(reversed_text)


def test_probe_downscales_for_the_angle_decision():
    from app.services.ocr.orientation import _probe

    probe = _probe(Image.new("RGB", (1654, 2338)))
    assert max(probe.size) <= settings.ocr_orientation_probe_px
