"""OCR engine factory.

Provides a single entry point for obtaining the configured OCR engine.
The engine is instantiated once and cached for the lifetime of the process.

Supported engine names (OCR_ENGINE env var)
───────────────────────────────────────────
  mineru    MinerU 2.5 — document understanding, provides_layout=True.
            Falls back to the fallback engine if MinerU is not installed.
  easyocr   EasyOCR — token-level OCR, provides_layout=False.
  auto      Try MinerU first; fall back to easyocr if unavailable.  (default)

Fallback
────────
  OCR_FALLBACK_ENGINE controls the fallback when the primary engine is
  unavailable (default: easyocr).
"""
from __future__ import annotations

import logging

from app.services.ocr.base import OcrEngine

logger = logging.getLogger(__name__)

_engine_cache: OcrEngine | None = None


def get_ocr_engine() -> OcrEngine:
    """Return the process-wide OCR engine (lazy, cached)."""
    global _engine_cache
    if _engine_cache is None:
        from app.core.config import settings
        _engine_cache = create_ocr_engine(
            engine_name=settings.ocr_engine,
            use_gpu=settings.ocr_use_gpu,
            fallback_name=settings.ocr_fallback_engine,
            mineru_backend=settings.ocr_mineru_backend,
        )
    return _engine_cache


def create_ocr_engine(
    engine_name: str,
    use_gpu: bool = True,
    fallback_name: str = "easyocr",
    mineru_backend: str = "pipeline_standard",
) -> OcrEngine:
    """Instantiate an OCR engine by name.

    Args:
        engine_name:     "mineru", "easyocr", or "auto".
        use_gpu:         Whether to request GPU acceleration.
        fallback_name:   Engine to use if the primary is unavailable.
        mineru_backend:  MinerU pipeline backend
                         ("pipeline_standard", "pipeline_fast", "vlm_openai").
    """
    name = engine_name.lower().strip()

    if name == "auto":
        return _auto_detect(use_gpu, mineru_backend, fallback_name)

    if name == "mineru":
        return _try_mineru(use_gpu, mineru_backend, fallback_name)

    if name == "easyocr":
        return _make_easyocr(use_gpu)

    logger.warning(
        "Unknown OCR engine %r — falling back to %r", engine_name, fallback_name
    )
    return create_ocr_engine(fallback_name, use_gpu, "easyocr", mineru_backend)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auto_detect(
    use_gpu: bool,
    mineru_backend: str,
    fallback_name: str,
) -> OcrEngine:
    from app.services.ocr.mineru_engine import MinerUEngine

    if MinerUEngine.is_available():
        logger.info("OCR engine: auto → mineru (MinerU available)")
        return MinerUEngine(use_gpu=use_gpu, backend=mineru_backend)

    logger.info("OCR engine: auto → %s (MinerU not available)", fallback_name)
    return create_ocr_engine(fallback_name, use_gpu, "easyocr", mineru_backend)


def _try_mineru(
    use_gpu: bool,
    mineru_backend: str,
    fallback_name: str,
) -> OcrEngine:
    from app.services.ocr.mineru_engine import MinerUEngine

    if MinerUEngine.is_available():
        logger.info("OCR engine: mineru")
        return MinerUEngine(use_gpu=use_gpu, backend=mineru_backend)

    logger.warning(
        "MinerU requested but not installed — falling back to %r. "
        "Install with: pip install mineru[full]",
        fallback_name,
    )
    return create_ocr_engine(fallback_name, use_gpu, "easyocr", mineru_backend)


def _make_easyocr(use_gpu: bool) -> OcrEngine:
    from app.services.ocr.easyocr_engine import EasyOcrEngine

    logger.info("OCR engine: easyocr")
    return EasyOcrEngine(use_gpu=use_gpu)
