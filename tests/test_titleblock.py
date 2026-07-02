"""Fase (ii): localización del cajetín de plano (rutas texto y visión)."""
from io import BytesIO

import fitz
from PIL import Image

from app.services.vision.titleblock import detect_titleblock

_A1_W, _A1_H = 2384, 1684


def _draw_titleblock(page, x0, y0, x1, y1, with_keywords=True):
    # Rejilla del cajetín.
    for i in range(6):
        y = y0 + (y1 - y0) * i / 5
        page.draw_line((x0, y), (x1, y))
    for j in range(3):
        x = x0 + (x1 - x0) * j / 2
        page.draw_line((x, y0), (x, y1))
    page.draw_rect(fitz.Rect(x0, y0, x1, y1))
    if with_keywords:
        labels = ["PROMOTOR: ACME SL", "ARQUITECTO: NOMBRE", "ESCALA: 1/500",
                  "FECHA: 01/2026", "PLANO: 03", "SITUACION: CALLE X"]
        for k, lab in enumerate(labels):
            page.insert_text((x0 + 8, y0 + 24 + 22 * k), lab, fontsize=12)


def _digital_plan_pdf(with_keywords=True):
    doc = fitz.open()
    page = doc.new_page(width=_A1_W, height=_A1_H)
    for i in range(300):
        x = 40 + (i * 11) % 1800
        page.draw_line((x, 40 + (i * 7) % 1400), (x + 40, 60 + (i * 7) % 1400))
    _draw_titleblock(page, _A1_W - 320, _A1_H - 480, _A1_W - 20, _A1_H - 20,
                     with_keywords=with_keywords)
    return doc, page


def test_text_route_finds_right_edge_titleblock():
    doc, page = _digital_plan_pdf()
    rect = detect_titleblock(page)
    doc.close()
    assert rect is not None
    # Debe caer en el cuadrante inferior-derecho, no cubrir el dibujo.
    assert rect.x0 > _A1_W * 0.55 and rect.y0 > _A1_H * 0.4
    assert rect.get_area() < _A1_W * _A1_H * 0.30


def test_no_keywords_no_titleblock_text_route():
    doc = fitz.open()
    page = doc.new_page(width=_A1_W, height=_A1_H)
    page.insert_text((100, 100), "ROTONDA 3 SJL-6", fontsize=10)
    rect = detect_titleblock(page)
    doc.close()
    assert rect is None


def _image_only_pdf(with_keywords=True):
    # Renderiza un plano sintético y lo embebe como imagen (sin capa de texto)
    # para forzar la ruta de visión.
    src, page = _digital_plan_pdf(with_keywords=with_keywords)
    pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
    png = pix.tobytes("png")
    src.close()
    doc = fitz.open()
    ipage = doc.new_page(width=_A1_W, height=_A1_H)
    ipage.insert_image(ipage.rect, stream=png)
    return doc, ipage


def test_vision_route_finds_titleblock_on_scanned_plan():
    doc, page = _image_only_pdf()
    rect = detect_titleblock(page)
    doc.close()
    assert rect is not None
    assert rect.x0 > _A1_W * 0.5 or rect.y0 > _A1_H * 0.5


def test_vision_route_rejects_plan_without_titleblock_keywords():
    doc, page = _image_only_pdf(with_keywords=False)
    rect = detect_titleblock(page)
    doc.close()
    assert rect is None
