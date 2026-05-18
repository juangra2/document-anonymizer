from pathlib import Path

import easyocr
import fitz
import numpy as np
from PIL import Image

from app.core.config import settings
from app.services.ocr.models import OcrPage, OcrToken


class EasyOcrEngine:
    def __init__(self) -> None:
        self._reader = easyocr.Reader(
            ["es", "en"],
            gpu=settings.ocr_use_gpu,
        )

    def analyze_image(
        self,
        image_path: Path,
        page_index: int = 0,
    ) -> OcrPage:
        image = Image.open(image_path).convert("RGB")
        width, height = image.size

        image_array = np.array(image)

        results = self._reader.readtext(
            image_array,
            detail=1,
            paragraph=False,
        )

        tokens: list[OcrToken] = []

        for bbox, text, confidence in results:
            if not str(text).strip():
                continue

            xs = [point[0] for point in bbox]
            ys = [point[1] for point in bbox]

            rect = fitz.Rect(
                min(xs),
                min(ys),
                max(xs),
                max(ys),
            )

            tokens.append(
                OcrToken(
                    text=str(text),
                    confidence=float(confidence),
                    rect=rect,
                )
            )

        return OcrPage(
            page_index=page_index,
            width=width,
            height=height,
            tokens=tokens,
        )