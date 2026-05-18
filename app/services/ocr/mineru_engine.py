"""MinerU 2.5 document-parsing engine.

MinerU (https://github.com/opendatalab/MinerU) is a document understanding
framework developed by Shanghai AI Lab.  Unlike pure-OCR engines it performs
layout analysis first, classifying each page region into semantic types
(text, title, table, figure, header, footer, …) before extracting text.

This means MinerUEngine sets provides_layout=True: the OcrPageSegmenter will
use MinerU's region types directly as zone hints instead of running heuristic
form/table detection.

Package names by version
------------------------
  magic-pdf 0.7–0.9  →  import magic_pdf
  mineru 1.x–2.x     →  import mineru

Both are tried so the engine works with either installation.

Expected install (MinerU 2.5 Pro)
----------------------------------
  pip install mineru[full]
  # or
  pip install magic-pdf[full]

MinerU also requires model weights.  Download them with:
  mineru-models-download -m pipeline
  # or
  magic-pdf-models-download

Environment
-----------
  MINERU_BACKEND   pipeline_standard | pipeline_fast | vlm_openai  (default: pipeline_standard)
  CUDA_VISIBLE_DEVICES  set to control GPU selection
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import fitz

from app.services.ocr.base import OcrEngine
from app.services.ocr.models import OcrPage, OcrToken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional import — engine is disabled gracefully if MinerU is not installed
# ---------------------------------------------------------------------------

_AVAILABLE = False
_VERSION: str | None = None

try:
    import mineru as _mineru_pkg
    from mineru.model.doc_analyze_by_custom_model import doc_analyze as _doc_analyze
    from mineru.data.data_reader_writer import FileBasedDataWriter as _Writer
    try:
        from mineru.pipe.pdf_pipe import UnicodeDetectPipe as _UniPipe
        from mineru.pipe.pdf_pipe import OCRPipe as _OcrPipe
    except ImportError:
        from mineru.pipe.UnicodeDetectPipe import UnicodeDetectPipe as _UniPipe  # type: ignore[no-redef]
        from mineru.pipe.OCRPipe import OCRPipe as _OcrPipe  # type: ignore[no-redef]
    _AVAILABLE = True
    _VERSION = getattr(_mineru_pkg, "__version__", "unknown")
    logger.info("MinerU %s loaded (package: mineru)", _VERSION)
except ImportError:
    try:
        import magic_pdf as _magic_pdf_pkg  # type: ignore[import]
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze as _doc_analyze  # type: ignore[no-redef]
        from magic_pdf.data.data_reader_writer import FileBasedDataWriter as _Writer  # type: ignore[no-redef]
        try:
            from magic_pdf.pipe.pdf_pipe import UnicodeDetectPipe as _UniPipe  # type: ignore[no-redef]
            from magic_pdf.pipe.pdf_pipe import OCRPipe as _OcrPipe  # type: ignore[no-redef]
        except ImportError:
            from magic_pdf.pipe.UnicodeDetectPipe import UnicodeDetectPipe as _UniPipe  # type: ignore[no-redef]
            from magic_pdf.pipe.OCRPipe import OCRPipe as _OcrPipe  # type: ignore[no-redef]
        _AVAILABLE = True
        _VERSION = getattr(_magic_pdf_pkg, "__version__", "unknown")
        logger.info("MinerU %s loaded (package: magic_pdf)", _VERSION)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Block-type → zone-hint mapping
# ---------------------------------------------------------------------------

# MinerU region types → our zone hint strings (matching ZoneType values)
_BLOCK_TYPE_TO_HINT: dict[str, str] = {
    "text":                "body",
    "title":               "header",
    "table":               "table",
    "table_caption":       "body",
    "table_footnote":      "body",
    "figure":              "body",
    "figure_caption":      "body",
    "header":              "header",
    "footer":              "footer",
    "footnote":            "footer",
    "interline_equation":  "body",
    "abandon":             "body",   # low-confidence regions
}


class MinerUEngine(OcrEngine):
    """Document-understanding engine backed by MinerU 2.5.

    Raises RuntimeError on any call if MinerU is not installed.
    Use the factory (ocr/factory.py) so that EasyOCR is used as fallback.
    """

    def __init__(self, use_gpu: bool = True, backend: str = "pipeline_standard") -> None:
        self._use_gpu = use_gpu
        self._backend = backend

        if not _AVAILABLE:
            logger.warning(
                "MinerU is not installed. "
                "Install with: pip install mineru[full] or pip install magic-pdf[full]"
            )

        # Propagate backend preference via env var that MinerU reads
        if backend and "MINERU_BACKEND" not in os.environ:
            os.environ["MINERU_BACKEND"] = backend

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
        return True

    def analyze_image(
        self,
        image_path: Path,
        page_index: int = 0,
    ) -> OcrPage:
        """Process a single image by wrapping it in a temporary PDF."""
        self._require_available()

        with TemporaryDirectory(prefix="mineru_img_") as tmp:
            tmp_pdf = Path(tmp) / "page.pdf"
            _image_to_single_page_pdf(image_path, tmp_pdf)
            pages = self.analyze_pdf(tmp_pdf)

        if not pages:
            return OcrPage(page_index=page_index, width=0, height=0, tokens=[])

        page = pages[0]
        return OcrPage(
            page_index=page_index,
            width=page.width,
            height=page.height,
            tokens=page.tokens,
        )

    def analyze_pdf(
        self,
        pdf_path: Path,
        dpi: int = 200,  # ignored — MinerU uses PDF coordinates natively
    ) -> list[OcrPage]:
        """Process a full PDF file and return one OcrPage per page."""
        self._require_available()

        pdf_bytes = pdf_path.read_bytes()

        with TemporaryDirectory(prefix="mineru_pdf_") as tmp:
            writer = _Writer(tmp)

            try:
                model_list = _doc_analyze(pdf_bytes, ocr=True)
                pipe = _OcrPipe(pdf_bytes, model_list, writer)
            except Exception as exc:
                logger.warning(
                    "MinerU OCRPipe init failed (%s); retrying with UnicodeDetectPipe", exc
                )
                model_list = _doc_analyze(pdf_bytes, ocr=False)
                pipe = _UniPipe(pdf_bytes, model_list, writer)

            pipe.pipe_classify()
            pipe.pipe_analyze()
            pipe.pipe_parse()

            pdf_mid_data: dict[str, Any] = pipe.pdf_mid_data

        return _extract_pages(pdf_mid_data)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_available(self) -> None:
        if not _AVAILABLE:
            raise RuntimeError(
                "MinerU is not installed. "
                "Install with: pip install mineru[full]  "
                "or: pip install magic-pdf[full]"
            )

    @staticmethod
    def is_available() -> bool:
        return _AVAILABLE


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def _extract_pages(pdf_mid_data: dict[str, Any]) -> list[OcrPage]:
    """Convert MinerU's pdf_mid_data dict into a list of OcrPage."""
    pages: list[OcrPage] = []

    pdf_info_list: list[dict] = pdf_mid_data.get("pdf_info", [])

    for page_data in pdf_info_list:
        page_idx: int = page_data.get("page_idx", len(pages))
        page_size: list[float] = page_data.get("page_size", [595.0, 842.0])
        page_width = float(page_size[0]) if len(page_size) > 0 else 595.0
        page_height = float(page_size[1]) if len(page_size) > 1 else 842.0

        # MinerU stores layout blocks under different keys across versions
        blocks: list[dict] = (
            page_data.get("preproc_blocks")
            or page_data.get("para_blocks")
            or page_data.get("blocks")
            or []
        )

        tokens = _tokens_from_blocks(blocks)

        pages.append(OcrPage(
            page_index=page_idx,
            width=int(page_width),
            height=int(page_height),
            tokens=tokens,
        ))

    return pages


def _tokens_from_blocks(blocks: list[dict]) -> list[OcrToken]:
    tokens: list[OcrToken] = []

    for block in blocks:
        block_type: str = block.get("type", "text")
        zone_hint = _BLOCK_TYPE_TO_HINT.get(block_type, "body")
        block_bbox: list[float] = block.get("bbox", [0, 0, 0, 0])

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                content: str = span.get("content", span.get("text", "")).strip()
                if not content:
                    continue

                span_bbox: list[float] = span.get("bbox", block_bbox)

                if len(span_bbox) < 4:
                    span_bbox = block_bbox

                rect = fitz.Rect(
                    float(span_bbox[0]),
                    float(span_bbox[1]),
                    float(span_bbox[2]),
                    float(span_bbox[3]),
                )

                tokens.append(OcrToken(
                    text=content,
                    confidence=1.0,  # MinerU does not produce token-level confidence
                    rect=rect,
                    zone_hint=zone_hint,
                ))

    return tokens


def _image_to_single_page_pdf(image_path: Path, output_pdf: Path) -> None:
    """Wrap a single image in a PDF page (for analyze_image compatibility)."""
    from PIL import Image as _Image

    img = _Image.open(image_path).convert("RGB")
    w, h = img.size

    doc = fitz.open()
    page = doc.new_page(width=w, height=h)

    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(0, 0, w, h), stream=buf.getvalue())

    doc.save(output_pdf)
    doc.close()
