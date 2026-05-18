"""MinerU 2.5-Pro document-parsing engine via mineru-vl-utils.

Uses MinerUClient.two_step_extract(PIL image) to extract ContentBlock objects
per page, then converts them to OcrToken with zone_hint for layout-first routing.

Integration
-----------
  pip install "mineru-vl-utils[transformers]"
  hf download opendatalab/MinerU2.5-Pro-2604-1.2B \\
      --local-dir model/MinerU2.5-Pro-2604-1.2B
  # In .env:
  OCR_ENGINE=mineru
  OCR_MINERU_BACKEND=transformers
  OCR_MINERU_MODEL_PATH=model/MinerU2.5-Pro-2604-1.2B

ContentBlock type → zone_hint mapping
--------------------------------------
  text      → body
  table     → table
  equation  → body
  image     → body   (image regions carry no text content to redact)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

from app.services.ocr.base import OcrEngine
from app.services.ocr.models import OcrPage, OcrToken

logger = logging.getLogger(__name__)


def _settings_model_path() -> str:
    from app.core.config import settings
    return settings.ocr_mineru_model_path


# ---------------------------------------------------------------------------
# Optional import — disabled gracefully when package not installed
# ---------------------------------------------------------------------------

_AVAILABLE = False
_MinerUClient: Any = None

try:
    from mineru_vl_utils import MinerUClient as _MinerUClient  # type: ignore[no-redef]
    _AVAILABLE = True
    logger.info("mineru-vl-utils loaded successfully")
except ImportError:
    logger.warning(
        "mineru-vl-utils is not installed — MinerU2.5-Pro unavailable. "
        "Install with: pip install 'mineru-vl-utils[transformers]'"
    )

# ---------------------------------------------------------------------------
# ContentBlock.type → zone_hint
# ---------------------------------------------------------------------------

_BLOCK_TYPE_TO_HINT: dict[str, str] = {
    "text":     "body",
    "table":    "table",
    "equation": "body",
    "image":    "body",
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class MinerUEngine(OcrEngine):
    """MinerU 2.5-Pro VLM engine.

    Capabilities
    ------------
    provides_layout = True   — ContentBlock.type sets zone_hint on every token.
    supports_pdf_input = False — two_step_extract() takes a PIL Image;
                                  pages are rendered externally by the redactor.

    The MinerUClient is initialised lazily on the first call to analyze_image()
    (loading a 1.2B-parameter model takes 10–30 s and uses ~4–8 GB VRAM/RAM).
    The factory's singleton ensures this happens only once per process.
    """

    def __init__(
        self,
        use_gpu: bool = True,
        backend: str = "transformers",
        model_path: str | None = None,
    ) -> None:
        self._use_gpu = use_gpu
        self._backend = backend
        self._model_path = model_path  # None → read from settings on first use
        self._client: Any = None       # lazy — loaded on first analyze_image()

        if not _AVAILABLE:
            logger.warning(
                "mineru-vl-utils not installed. "
                "Install: pip install 'mineru-vl-utils[transformers]'"
            )

    # ------------------------------------------------------------------
    # OcrEngine interface
    # ------------------------------------------------------------------

    @property
    def engine_name(self) -> str:
        return "mineru"

    @property
    def provides_layout(self) -> bool:
        return True

    @property
    def supports_pdf_input(self) -> bool:
        # two_step_extract() operates on PIL images — rasterisation is external.
        return False

    @staticmethod
    def is_available() -> bool:
        return _AVAILABLE

    def model_is_ready(self) -> bool:
        """True when the package is installed AND the model weights directory exists.

        The factory calls this before returning a MinerUEngine so that a missing
        model causes an early fallback to EasyOCR instead of a crash at inference.
        """
        if not _AVAILABLE:
            return False
        path = self._resolve_model_path()
        return path.exists() and path.is_dir()

    def analyze_image(
        self,
        image_path: Path,
        page_index: int = 0,
    ) -> OcrPage:
        """Extract structured content from a rendered page image."""
        self._require_available()

        image = Image.open(image_path).convert("RGB")
        width, height = image.size

        client = self._get_client()

        try:
            blocks = client.two_step_extract(image)
        except Exception as exc:
            logger.error(
                "MinerU two_step_extract failed on page %d: %s — returning empty page",
                page_index, exc,
            )
            return OcrPage(page_index=page_index, width=width, height=height, tokens=[])

        tokens = _blocks_to_tokens(blocks, width, height)

        return OcrPage(
            page_index=page_index,
            width=width,
            height=height,
            tokens=tokens,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazily initialise and cache the MinerUClient."""
        if self._client is not None:
            return self._client

        model_path = self._resolve_model_path()
        device = "cuda" if self._use_gpu else "cpu"

        logger.info(
            "Loading MinerU2.5-Pro model from '%s' (backend=%s, device=%s)...",
            model_path, self._backend, device,
        )

        # Always use forward slashes — MinerUClient may validate the path as a
        # HuggingFace repo_id internally if the model needs downloading, and
        # Windows backslashes fail that validation.
        posix_path = model_path.as_posix()

        # Try with explicit device kwarg; fall back silently if unsupported.
        try:
            self._client = _MinerUClient(
                model_path=posix_path,
                backend=self._backend,
                device=device,
            )
        except TypeError:
            self._client = _MinerUClient(
                model_path=posix_path,
                backend=self._backend,
            )

        logger.info("MinerU2.5-Pro model loaded.")
        return self._client

    def _resolve_model_path(self) -> Path:
        raw = self._model_path or _settings_model_path()
        return Path(raw).resolve()

    def _require_available(self) -> None:
        if not _AVAILABLE:
            raise RuntimeError(
                "mineru-vl-utils is not installed. "
                "Install: pip install 'mineru-vl-utils[transformers]'"
            )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _blocks_to_tokens(
    blocks: list[Any],
    page_width: int,
    page_height: int,
) -> list[OcrToken]:
    """Convert ContentBlock list → OcrToken list with zone_hint.

    Normalised bbox [x0, y0, x1, y1] ∈ [0, 1] → absolute pixel coordinates.
    """
    tokens: list[OcrToken] = []

    for block in blocks:
        content: str = (getattr(block, "content", "") or "").strip()
        if not content:
            continue

        block_type: str = (getattr(block, "type", "text") or "text").lower()
        zone_hint = _BLOCK_TYPE_TO_HINT.get(block_type, "body")

        bbox_norm: list[float] = getattr(block, "bbox", None) or [0.0, 0.0, 1.0, 1.0]
        rect = _norm_bbox_to_rect(bbox_norm, page_width, page_height)

        tokens.append(OcrToken(
            text=content,
            confidence=1.0,  # MinerU VLM does not produce per-token confidence
            rect=rect,
            zone_hint=zone_hint,
        ))

    return tokens


def _norm_bbox_to_rect(
    bbox: list[float],
    width: int,
    height: int,
) -> fitz.Rect:
    """Normalised [x0, y0, x1, y1] ∈ [0,1] → absolute pixel fitz.Rect."""
    if len(bbox) < 4:
        return fitz.Rect(0, 0, float(width), float(height))

    x0 = _clamp(float(bbox[0]) * width,  0.0, float(width))
    y0 = _clamp(float(bbox[1]) * height, 0.0, float(height))
    x1 = _clamp(float(bbox[2]) * width,  0.0, float(width))
    y1 = _clamp(float(bbox[3]) * height, 0.0, float(height))

    # Guard against degenerate boxes (x0 > x1 or y0 > y1)
    if x1 <= x0:
        x1 = x0 + 1.0
    if y1 <= y0:
        y1 = y0 + 1.0

    return fitz.Rect(x0, y0, x1, y1)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))
