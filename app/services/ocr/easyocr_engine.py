from pathlib import Path

import easyocr
import fitz
import numpy as np
from PIL import Image

from app.services.ocr.base import OcrEngine
from app.services.ocr.models import OcrPage, OcrToken


class EasyOcrEngine(OcrEngine):
    """Token-level OCR via EasyOCR.  Fallback engine when MinerU is unavailable."""

    def __init__(self, use_gpu: bool = True) -> None:
        self._reader = easyocr.Reader(["es", "en"], gpu=use_gpu)

    @property
    def engine_name(self) -> str:
        return "easyocr"

    def analyze_image(
        self,
        image_path: Path,
        page_index: int = 0,
    ) -> OcrPage:
        image = Image.open(image_path).convert("RGB")
        width, height = image.size

        results = self._reader.readtext(
            np.array(image),
            detail=1,
            paragraph=False,
        )

        tokens: list[OcrToken] = []

        for bbox, text, confidence in results:
            if not str(text).strip():
                continue

            xs = [point[0] for point in bbox]
            ys = [point[1] for point in bbox]

            tokens.append(OcrToken(
                text=str(text),
                confidence=float(confidence),
                rect=fitz.Rect(min(xs), min(ys), max(xs), max(ys)),
            ))

        return OcrPage(
            page_index=page_index,
            width=width,
            height=height,
            tokens=tokens,
        )
