"""Corrección automática de orientación para páginas escaneadas.

Un escaneo con el contenido girado 90° (una tarjeta de identidad puesta de lado
sobre el cristal, un anexo apaisado) hace que el OCR devuelva **ruido**, no
vacío: unas decenas de tokens cortos sin sentido.  Ese es el fallo más
peligroso de la herramienta: no se detecta ninguna entidad, no se redacta nada
y **nadie avisa**, de modo que el usuario cree que el documento está limpio.

La rotación aquí no viene declarada en el PDF (la página es A4 vertical con
`rotation=0`); está en el contenido escaneado, así que hay que deducirla del
resultado del OCR.

Métrica de calidad: **caracteres por token**.  Una página normal da muchos
caracteres por token; una girada, muy pocos; la misma página enderezada vuelve
a dar muchos.  El umbral por defecto (8.0) separa los tres casos con margen: no
dispara en páginas normales, sí en las giradas, y no vuelve a disparar una vez
corregidas.

La confianza media del OCR no sirve como métrica: es engañosa.  El modo
`rotation_info` nativo de EasyOCR da confianzas altas con el texto más
ilegible, porque rota cada caja por separado y rompe el orden de lectura; por
eso se rota la imagen completa.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PIL import Image

from app.core.config import settings
from app.services.ocr.models import OcrPage

logger = logging.getLogger(__name__)

# Ángulos alternativos a probar, en orden de probabilidad sobre un escaneo.
_CANDIDATE_ANGLES = (90, 270, 180)


@dataclass(frozen=True)
class OrientationResult:
    """Orientación elegida para una página y el OCR ya hecho en ella."""

    angle: int          # grados aplicados a la imagen (0, 90, 180 o 270)
    ocr_page: OcrPage
    quality: float      # caracteres por token del resultado elegido
    poor_quality: bool  # True si ni la mejor orientación da texto legible


def text_quality(ocr_page: OcrPage) -> float:
    """Caracteres por token: alto en texto legible, muy bajo en ruido de OCR.

    Sirve para decidir SI la página está ilegible, no para elegir ENTRE dos
    giros — ver `orientation_score`.
    """
    texts = [t.text for t in ocr_page.tokens if t.text and t.text.strip()]
    if not texts:
        return 0.0
    return sum(len(t) for t in texts) / len(texts)


def orientation_score(ocr_page: OcrPage) -> float:
    """Puntuación para ELEGIR entre giros: longitud de token × confianza.

    Los caracteres por token no bastan aquí: con el giro contrario el texto sale
    invertido y produce tokens igual de largos, con lo que la página se leería
    al revés y las señales no dispararían.  El texto dado la vuelta sí se
    distingue por una confianza del OCR bastante más baja, así que se exige lo
    uno y lo otro.
    """
    tokens = [t for t in ocr_page.tokens if t.text and t.text.strip()]
    if not tokens:
        return 0.0
    confidence = sum(t.confidence for t in tokens) / len(tokens)
    return text_quality(ocr_page) * confidence


def _looks_like_noise(ocr_page: OcrPage) -> bool:
    """La página produjo tokens suficientes, pero ilegibles."""
    token_count = sum(1 for t in ocr_page.tokens if t.text and t.text.strip())
    if token_count < settings.ocr_rotate_min_tokens:
        # Página casi vacía: rotarla no aporta y evitamos 3 pases de OCR por
        # cada página en blanco de un expediente largo.
        return False
    return text_quality(ocr_page) < settings.ocr_min_chars_per_token


def resolve_orientation(image: Image.Image, run_ocr) -> OrientationResult:
    """Devuelve la mejor orientación de *image* junto con su OCR.

    `run_ocr(image) -> OcrPage` la ejecuta el llamador, que es quien sabe cómo
    guardar el fichero temporal y qué motor usar.  El pase a 0° se aprovecha
    siempre: una página normal cuesta exactamente lo de antes.
    """
    base = run_ocr(image)
    if not settings.ocr_auto_rotate_enabled or not _looks_like_noise(base):
        return OrientationResult(0, base, text_quality(base), poor_quality=False)

    # La página parece ruido: se prueban los otros giros, siempre a resolución
    # completa.  Una copia reducida no sirve para elegir: 90° y 270° dejan la
    # página apaisada con tokens igual de largos, y solo la confianza del OCR
    # distingue el texto derecho del invertido, pero en pequeño el texto de un
    # A4 no se lee: la confianza sale igual de baja en los dos giros.  A
    # resolución completa la diferencia es clara, a costa de unos segundos por
    # pase.
    best_angle, best_page = 0, base
    best_score = orientation_score(base)
    for angle in _CANDIDATE_ANGLES:
        candidate = run_ocr(image.rotate(angle, expand=True))
        score = orientation_score(candidate)
        if score > best_score:
            best_angle, best_page, best_score = angle, candidate, score
    best_quality = text_quality(best_page)

    if best_angle:
        logger.info(
            "Página escaneada girada: se corrige %d° (calidad %.1f -> %.1f "
            "caracteres por token).", best_angle, text_quality(base), best_quality,
        )

    # Si ni la mejor orientación da texto legible, el OCR ha fallado: hay que
    # avisar en el informe en vez de devolver un documento aparentemente limpio.
    poor = best_quality < settings.ocr_min_chars_per_token
    return OrientationResult(best_angle, best_page, best_quality, poor_quality=poor)
