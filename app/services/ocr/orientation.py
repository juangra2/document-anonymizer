"""Corrección automática de orientación para páginas escaneadas.

Un escaneo con el contenido girado 90° (una tarjeta de identidad puesta de lado
sobre el cristal, un anexo apaisado) hace que el OCR devuelva **ruido**, no
vacío: sobre el DNI real medido, EasyOCR devolvía 24 tokens y 67 caracteres de
basura (`"EQUIPO EQUIP 12246A6D1 M 2 1 1 …"`).  Ese es el fallo más peligroso de
la herramienta: no se detecta ninguna entidad, no se redacta nada y **nadie
avisa**, de modo que el usuario cree que el documento está limpio.

La rotación aquí no viene declarada en el PDF (la página es A4 vertical con
`rotation=0`); está en el contenido escaneado, así que hay que deducirla del
resultado del OCR.

Métrica de calidad: **caracteres por token**.  Medido sobre el corpus:

    páginas escaneadas normales   14.2 – 35.1   (mediana 30.9)
    DNI rotado (ilegible)          2.1 –  2.8
    DNI corregido                 10.5 – 12.0

El umbral por defecto (8.0) separa los tres grupos con margen: no dispara en
páginas normales, sí en las rotas, y no vuelve a disparar una vez corregidas.

Se descartó la confianza media del OCR como métrica: es engañosa.  El modo
`rotation_info` nativo de EasyOCR daba la confianza más alta (0.829) con el
texto más ilegible, porque rota cada caja por separado y rompe el orden de
lectura — probado y descartado frente a rotar la imagen completa.
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
    """Caracteres por token: alto en texto legible, muy bajo en ruido de OCR."""
    texts = [t.text for t in ocr_page.tokens if t.text and t.text.strip()]
    if not texts:
        return 0.0
    return sum(len(t) for t in texts) / len(texts)


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

    best_angle, best_page, best_quality = 0, base, text_quality(base)
    for angle in _CANDIDATE_ANGLES:
        candidate = run_ocr(image.rotate(angle, expand=True))
        quality = text_quality(candidate)
        if quality > best_quality:
            best_angle, best_page, best_quality = angle, candidate, quality

    if best_angle:
        logger.info(
            "Página escaneada girada: se corrige %d° (calidad %.1f -> %.1f "
            "caracteres por token).", best_angle, text_quality(base), best_quality,
        )

    # Si ni la mejor orientación da texto legible, el OCR ha fallado: hay que
    # avisar en el informe en vez de devolver un documento aparentemente limpio.
    poor = best_quality < settings.ocr_min_chars_per_token
    return OrientationResult(best_angle, best_page, best_quality, poor_quality=poor)
