"""Corrección de orientación en escaneos girados y aviso de OCR ilegible.

Una página escaneada normal da muchos caracteres por token; girada, muy pocos;
enderezada, vuelve a dar muchos.  Estos tests usan un OCR simulado para ser
rápidos y deterministas.
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

    A 270° el texto sale invertido y produce tokens casi igual de largos que a
    90°, de modo que la página se leería al revés y las señales del documento no
    dispararían.  La confianza del OCR sí los separa.
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


def test_opposite_rotations_are_decided_at_full_resolution():
    """90° y 270° dejan la página apaisada: los separa la confianza a tamaño completo.

    Una copia reducida no sirve: en pequeño el texto de un A4 no se lee y la
    confianza sale igual en ambos giros.  Aquí una franja negra
    marca el sentido: tras girar 270° queda a la derecha y el OCR simulado lee
    texto derecho; tras girar 90° queda a la izquierda y lee texto invertido,
    con tokens más largos pero confianza baja.
    """
    image = Image.new("L", (100, 200), 255)
    image.paste(0, (0, 0, 100, 20))
    upright = OcrPage(page_index=0, width=200, height=100, tokens=[
        OcrToken(text="PRESUPUESTO GENERAL", confidence=0.7, rect=fitz.Rect(0, 0, 50, 8)),
        OcrToken(text="TECNICO REDACTOR", confidence=0.7, rect=fitz.Rect(0, 10, 50, 18)),
    ])
    upside_down = OcrPage(page_index=0, width=200, height=100, tokens=[
        OcrToken(text="ojsendnsajd>>>lejeueb", confidence=0.2, rect=fitz.Rect(0, 0, 50, 8)),
        OcrToken(text="jojopepaj>>>ooiuoej", confidence=0.2, rect=fitz.Rect(0, 10, 50, 18)),
    ])
    sizes = []

    def run(img):
        sizes.append(max(img.size))
        if img.width < img.height:
            return _NOISE
        return upright if img.getpixel((img.width - 1, img.height // 2)) == 0 else upside_down

    result = resolve_orientation(image, run)
    assert result.angle == 270
    assert result.ocr_page is upright
    assert set(sizes) == {200}, "ningún giro se decide sobre una copia reducida"
