from __future__ import annotations

import unicodedata

import cv2
import fitz
import numpy as np
from PIL import Image

from app.services.ocr.models import OcrToken


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.split())


def _find_signature_label(tokens: list[OcrToken]) -> OcrToken | None:
    for token in tokens:
        normalized = normalize_text(token.text).strip(":")

        if normalized.startswith("firma"):
            return token

    return None


def _find_footer_stop_x(
    tokens: list[OcrToken],
    signature_token: OcrToken,
    image_width: int,
) -> float:
    """
    Busca textos a la derecha de la firma tipo:
    - Documento sintético...
    - Generado...
    - Fecha...
    para no invadir el bloque derecho del pie.
    """
    signature_center_y = (signature_token.rect.y0 + signature_token.rect.y1) / 2
    candidates: list[float] = []

    for token in tokens:
        if token is signature_token:
            continue

        normalized = normalize_text(token.text)

        if not normalized:
            continue

        token_center_y = (token.rect.y0 + token.rect.y1) / 2

        close_y = abs(token_center_y - signature_center_y) < image_width * 0.08
        to_right = token.rect.x0 > signature_token.rect.x1 + 30

        if not close_y or not to_right:
            continue

        if normalized.startswith(("documento", "generado", "fecha")):
            candidates.append(float(token.rect.x0))

    if candidates:
        return min(candidates)

    return image_width * 0.60


def detect_signature_rect(
    image: Image.Image,
    tokens: list[OcrToken],
) -> fitz.Rect | None:
    """
    Detecta una firma asociada a la etiqueta 'Firma:'.

    Importante:
    - No usa la lógica genérica de PII.
    - No se fusiona con otros rectángulos.
    - Tiene límites duros para no tapar medio documento.
    """
    signature_token = _find_signature_label(tokens)

    if signature_token is None:
        return None

    rgb_image = image.convert("RGB")
    image_np = np.array(rgb_image)
    image_bgr = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)

    height, width = image_bgr.shape[:2]

    label_rect = signature_token.rect

    footer_stop_x = _find_footer_stop_x(
        tokens=tokens,
        signature_token=signature_token,
        image_width=width,
    )

    search_x0 = int(min(label_rect.x1 + width * 0.025, width - 1))
    search_x1 = int(
        min(
            search_x0 + width * 0.26,
            width * 0.52,
            footer_stop_x - width * 0.02,
        )
    )

    search_y0 = int(max(label_rect.y0 - height * 0.035, height * 0.78))
    search_y1 = int(min(label_rect.y1 + height * 0.055, height - 5))

    if search_x1 <= search_x0 or search_y1 <= search_y0:
        return None

    roi = image_bgr[search_y0:search_y1, search_x0:search_x1]

    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    threshold = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )[1]

    kernel = np.ones((2, 2), np.uint8)
    threshold = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, kernel)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(
        threshold,
        connectivity=8,
    )

    roi_height, roi_width = threshold.shape[:2]

    components: list[tuple[int, int, int, int]] = []

    for index in range(1, num_labels):
        x, y, component_width, component_height, area = stats[index]

        if area < 20:
            continue

        if component_width < 8:
            continue

        if component_height < 3:
            continue

        # Evita bordes, líneas horizontales largas o separadores.
        if component_width > roi_width * 0.92:
            continue

        if component_height > roi_height * 0.75:
            continue

        aspect_ratio = component_width / max(component_height, 1)

        if aspect_ratio > 35 and component_height <= 4:
            continue

        components.append(
            (
                int(x),
                int(y),
                int(component_width),
                int(component_height),
            )
        )

    if not components:
        return None

    min_x = min(x for x, _, component_width, _ in components)
    min_y = min(y for _, y, _, component_height in components)
    max_x = max(x + component_width for x, _, component_width, _ in components)
    max_y = max(y + component_height for _, y, _, component_height in components)

    padding_x = 10
    padding_y = 8

    rect = fitz.Rect(
        max(search_x0 + min_x - padding_x, search_x0),
        max(search_y0 + min_y - padding_y, search_y0),
        min(search_x0 + max_x + padding_x, search_x1),
        min(search_y0 + max_y + padding_y, search_y1),
    )

    # Límites duros extra.
    max_width = width * 0.28
    max_height = height * 0.08

    if rect.width > max_width:
        rect.x1 = rect.x0 + max_width

    if rect.height > max_height:
        rect.y1 = rect.y0 + max_height

    return rect