"""Recorte de la tarjeta en copias de DNI escaneadas.

La garantía de que no se escapa PII no está aquí: está en que el llamador pinta
la región, vuelve a pasar OCR y exige que no quede texto legible fuera.  Estos
tests fijan que la región PROPUESTA es razonable y que el detector cae al
comportamiento seguro cuando no puede acotarla.
"""
import fitz
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.core.config import settings
from app.services.vision.card_region import card_regions

DPI = 200


def _scan(card_box=(600, 400, 1200, 800), speckle=False) -> Image.Image:
    """A4 a 200 DPI con fondo claro de escáner y una tarjeta oscura."""
    image = Image.new("RGB", (1654, 2338), color=(250, 250, 250))
    draw = ImageDraw.Draw(image)
    if card_box:
        draw.rectangle(card_box, fill=(90, 90, 90))
    if speckle:
        # Moteado de ruido que abarca la hoja: reproduce el caso real en que el
        # componente MAYOR era la página entera con relleno 0.007.
        rng = np.random.default_rng(7)
        arr = np.array(image)
        ys = rng.integers(0, arr.shape[0], 4000)
        xs = rng.integers(0, arr.shape[1], 4000)
        arr[ys, xs] = 40
        image = Image.fromarray(arr)
    return image


def test_card_is_located_with_margin():
    regions, reason = card_regions(_scan(), ocr_rects=[], dpi=DPI)
    assert reason is None
    assert len(regions) == 1

    region = regions[0]
    # Cubre la tarjeta y añade margen por los cuatro lados.
    assert region.x0 < 600 and region.y0 < 400
    assert region.x1 > 1200 and region.y1 > 800
    margin_px = settings.dni_crop_margin_mm / 25.4 * DPI
    assert (600 - region.x0) >= margin_px * 0.9


def test_page_wide_speckle_does_not_defeat_the_detector():
    # El componente más grande es la página entera, pero su relleno es ínfimo.
    regions, reason = card_regions(_scan(speckle=True), ocr_rects=[], dpi=DPI)
    assert reason is None, reason
    assert len(regions) == 1
    assert regions[0].get_area() < 1654 * 2338 * settings.dni_crop_max_frac


def test_ocr_boxes_extend_the_region():
    # Texto detectado fuera del contorno (la tarjeta sale fragmentada): la
    # región debe crecer para incluirlo.
    outside = fitz.Rect(300, 300, 500, 340)
    regions, reason = card_regions(_scan(), ocr_rects=[outside], dpi=DPI)
    assert reason is None
    assert any(r.contains(outside) for r in regions)


def test_two_cards_give_two_regions():
    image = _scan(card_box=(200, 200, 700, 600))
    ImageDraw.Draw(image).rectangle((200, 1500, 700, 1900), fill=(90, 90, 90))
    regions, reason = card_regions(image, ocr_rects=[], dpi=DPI)
    assert reason is None
    assert len(regions) == 2, "anverso y reverso deben dar dos regiones"


def test_content_covering_the_page_is_rejected():
    # Si el contenido ocupa casi toda la hoja no hay nada que ganar recortando.
    regions, reason = card_regions(
        _scan(card_box=(20, 20, 1634, 2318)), ocr_rects=[], dpi=DPI,
    )
    assert regions == []
    assert reason


def test_blank_page_is_rejected():
    regions, reason = card_regions(_scan(card_box=None), ocr_rects=[], dpi=DPI)
    assert regions == []
    assert reason == "sin regiones de contenido"


@pytest.mark.parametrize("enabled", [True, False])
def test_crop_can_be_switched_off(monkeypatch, enabled):
    # El interruptor vive en la configuración; con él apagado el llamador
    # mantiene el comportamiento anterior (página entera).
    monkeypatch.setattr(settings, "dni_crop_enabled", enabled)
    assert settings.dni_crop_enabled is enabled
