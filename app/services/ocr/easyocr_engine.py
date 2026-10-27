from pathlib import Path

import easyocr
import fitz
import numpy as np
from PIL import Image

from app.core.config import settings
from app.core.dispositivo import usar_gpu
from app.services.ocr.base import OcrEngine
from app.services.ocr.models import OcrPage, OcrToken


def _soltar_memoria_gpu() -> None:
    """Devuelve a la GPU lo que PyTorch se guarda tras una lectura.

    El detector de EasyOCR sobre una pagina de 8 megapixeles reserva varios GB;
    PyTorch se los queda aunque no los use, y con el motor de layout cargado la
    siguiente lectura (otro giro, una foto enderezada) ya no cabe en una GPU de
    8 GB.  En Windows desborda a la memoria del sistema; en Linux falla.
    """
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # sin torch o sin CUDA no hay nada que soltar
        pass


class EasyOcrEngine(OcrEngine):
    """Token-level OCR via EasyOCR.  Fallback engine when MinerU is unavailable."""

    def __init__(self, use_gpu: bool = True) -> None:
        # La carpeta de pesos y el permiso de descarga se configuran porque en
        # el servidor de destino puede no haber salida a internet: los modelos
        # viajan dentro de la imagen y EasyOCR debe buscarlos ahí, no pedirlos.
        options: dict = {"gpu": usar_gpu(use_gpu)}
        if settings.easyocr_model_path:
            options["model_storage_directory"] = settings.easyocr_model_path
        if not settings.easyocr_allow_download:
            options["download_enabled"] = False
        self._reader = easyocr.Reader(["es", "en"], **options)

    @property
    def engine_name(self) -> str:
        return "easyocr"

    def palabras(self, imagen: Image.Image) -> list[OcrToken]:
        """Una pieza por palabra (sin juntar las vecinas en una linea), para
        tapar el tramo exacto de un dato dentro de una linea con su etiqueta."""
        resultados = self._reader.readtext(np.array(imagen.convert("RGB")), detail=1,
                                           paragraph=False, width_ths=0.1)
        salida = []
        for bbox, text, confidence in resultados:
            if str(text).strip():
                xs, ys = [p[0] for p in bbox], [p[1] for p in bbox]
                salida.append(OcrToken(text=str(text), confidence=float(confidence),
                                       rect=fitz.Rect(min(xs), min(ys), max(xs), max(ys))))
        return salida

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
        _soltar_memoria_gpu()

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
