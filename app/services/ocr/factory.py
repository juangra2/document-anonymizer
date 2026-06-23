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
_word_engine_cache: OcrEngine | None = None


def get_word_ocr_engine(primary: OcrEngine | None = None) -> OcrEngine:
    """Word/line-level OCR engine (EasyOCR) used for redaction geometry.

    The primary engine (e.g. MinerU) may only emit block-level boxes, which are
    too coarse for character-accurate redaction.  This returns a token-level
    engine for precise coordinates, reusing the primary when it already is one.
    """
    global _word_engine_cache
    if primary is not None and primary.engine_name == "easyocr":
        return primary
    if _word_engine_cache is None:
        from app.core.config import settings
        _word_engine_cache = _make_easyocr(settings.ocr_use_gpu)
    return _word_engine_cache


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
    mineru_backend: str = "transformers",
) -> OcrEngine:
    """Instantiate an OCR engine by name.

    Args:
        engine_name:     "mineru", "easyocr", or "auto".
        use_gpu:         Whether to request GPU acceleration.
        fallback_name:   Engine to use if the primary is unavailable.
        mineru_backend:  MinerUClient backend ("transformers" for MinerU2.5-Pro).
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

    if not MinerUEngine.is_available():
        logger.info("OCR engine: auto → %s (mineru-vl-utils not installed)", fallback_name)
        return create_ocr_engine(fallback_name, use_gpu, "easyocr", mineru_backend)

    engine = MinerUEngine(use_gpu=use_gpu, backend=mineru_backend)
    if not engine.model_is_ready():
        logger.warning(
            "OCR engine: auto → %s (MinerU model not found at '%s'). "
            "Download: hf download opendatalab/MinerU2.5-Pro-2604-1.2B "
            "--local-dir model/MinerU2.5-Pro-2604-1.2B",
            fallback_name, engine._resolve_model_path(),
        )
        return create_ocr_engine(fallback_name, use_gpu, "easyocr", mineru_backend)

    logger.info("OCR engine: auto → mineru (model ready at %s)", engine._resolve_model_path())
    return engine


def _try_mineru(
    use_gpu: bool,
    mineru_backend: str,
    fallback_name: str,
) -> OcrEngine:
    from app.services.ocr.mineru_engine import MinerUEngine

    if not MinerUEngine.is_available():
        logger.warning(
            "MinerU requested but mineru-vl-utils not installed — falling back to %r. "
            "Install: pip install 'mineru-vl-utils[transformers]'",
            fallback_name,
        )
        return create_ocr_engine(fallback_name, use_gpu, "easyocr", mineru_backend)

    engine = MinerUEngine(use_gpu=use_gpu, backend=mineru_backend)
    if not engine.model_is_ready():
        logger.warning(
            "MinerU requested but model not found at '%s' — falling back to %r. "
            "Download: hf download opendatalab/MinerU2.5-Pro-2604-1.2B "
            "--local-dir model/MinerU2.5-Pro-2604-1.2B",
            engine._resolve_model_path(), fallback_name,
        )
        return create_ocr_engine(fallback_name, use_gpu, "easyocr", mineru_backend)

    logger.info("OCR engine: mineru (model ready at %s)", engine._resolve_model_path())
    return engine


def _make_easyocr(use_gpu: bool) -> OcrEngine:
    from app.services.ocr.easyocr_engine import EasyOcrEngine

    logger.info("OCR engine: easyocr")
    return EasyOcrEngine(use_gpu=use_gpu)
