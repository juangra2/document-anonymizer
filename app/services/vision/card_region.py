"""Localización de la tarjeta dentro de un escaneo de documento de identidad.

Un DNI escaneado en plano ocupa una fracción pequeña de un A4 casi vacío:
pintar la página entera es correcto pero visualmente drástico.  Aquí se acota
la región a redactar.

La región se construye uniendo **dos señales independientes**, porque ninguna
basta por separado:

* **Componentes de contenido** (Otsu + cierre morfológico) captan la foto
  biométrica y la firma, que no producen ningún token de OCR — pero en el
  reverso la tarjeta puede salir fragmentada y el texto empezar antes del
  componente detectado.
* **Cajas de los tokens de OCR** (ya calculadas al resolver la orientación,
  así que salen gratis) captan todo el texto — pero no la foto.

Además se filtra por **relleno**, no por tamaño: el componente más grande puede
ser la hoja entera, unida por el moteado de ruido pero casi vacía, mientras que
la tarjeta está mucho más llena.  Quedarse con "el componente más grande"
fallaría.

Esta función solo PROPONE la región.  Quien la usa debe verificarla pintándola
y comprobando por OCR que no queda texto legible fuera; si queda, se redacta la
página entera.  La garantía no está en confiar en el detector, sino en medir el
resultado.
"""
from __future__ import annotations

import cv2
import fitz
import numpy as np
from PIL import Image

from app.core.config import settings


def _content_components(image: Image.Image) -> list[tuple[float, float, float, float]]:
    """Manchas de contenido sobre el fondo uniforme del escáner."""
    gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Apertura previa: quita el moteado suelto del escáner.  Sin ella, el cierre
    # posterior une esas motas en manchas grandes que acaban fusionándose con la
    # tarjeta y dejan la región cubriendo casi toda la hoja.
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )

    # Cierre proporcional a la página: une letras y bordes de una misma tarjeta.
    kernel = max(15, min(gray.shape) // 40)
    closed = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kernel, kernel)),
    )

    count, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    page_area = float(gray.shape[0] * gray.shape[1])
    boxes = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if w <= 0 or h <= 0:
            continue
        frac = (w * h) / page_area
        fill = area / float(w * h)
        if fill < settings.dni_crop_min_fill:
            continue
        if not (settings.dni_crop_min_frac <= frac <= settings.dni_crop_max_frac):
            continue
        boxes.append((float(x), float(y), float(x + w), float(y + h)))
    return boxes


def _merge_overlapping(boxes: list[fitz.Rect], gap: float) -> list[fitz.Rect]:
    """Agrupa las cajas que se tocan al inflarlas *gap* píxeles.

    Así los fragmentos de una misma tarjeta forman un rect, y un anverso y un
    reverso escaneados en la misma hoja quedan como dos.
    """
    merged: list[fitz.Rect] = []
    for box in boxes:
        current = fitz.Rect(box)
        changed = True
        while changed:
            changed = False
            for other in list(merged):
                inflated = fitz.Rect(other)
                inflated.x0 -= gap
                inflated.y0 -= gap
                inflated.x1 += gap
                inflated.y1 += gap
                if inflated.intersects(current):
                    current |= other
                    merged.remove(other)
                    changed = True
        merged.append(current)
    return merged


def card_regions(
    image: Image.Image,
    ocr_rects: list[fitz.Rect],
    dpi: int,
) -> tuple[list[fitz.Rect], str | None]:
    """Regiones candidatas a contener la tarjeta, con margen ya aplicado.

    Returns:
        (regiones, motivo_de_descarte).  Si la lista viene vacía, el motivo
        explica por qué no se puede recortar y hay que redactar la página
        entera.
    """
    components = _content_components(image)
    if not components:
        return [], "sin regiones de contenido"

    margin = settings.dni_crop_margin_mm / 25.4 * dpi
    boxes = [fitz.Rect(*c) for c in components]
    boxes += [fitz.Rect(r) for r in ocr_rects]

    regions = _merge_overlapping(boxes, gap=margin)

    page = fitz.Rect(0, 0, image.width, image.height)
    grown: list[fitz.Rect] = []
    for region in regions:
        region.x0 -= margin
        region.y0 -= margin
        region.x1 += margin
        region.y1 += margin
        grown.append(region & page)

    covered = sum(r.get_area() for r in grown)
    fraction = covered / page.get_area() if page.get_area() else 1.0
    if fraction > settings.dni_crop_max_frac:
        return [], f"la region cubriria el {fraction:.0%} de la pagina"
    if fraction < settings.dni_crop_min_frac:
        return [], f"region demasiado pequena ({fraction:.1%})"

    return grown, None
