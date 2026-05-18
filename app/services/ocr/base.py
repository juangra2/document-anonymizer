from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.services.ocr.models import OcrPage


class OcrEngine(ABC):
    """Common interface for all OCR and document-parsing backends.

    Implementations
    ---------------
    EasyOcrEngine  : token-level OCR via EasyOCR (provides_layout=False).
    MinerUEngine   : document-understanding via MinerU 2.5 (provides_layout=True).

    Contract
    --------
    Every engine must implement ``analyze_image``.  Engines that can process
    full PDFs natively (e.g. MinerU) should also override ``analyze_pdf`` to
    avoid the quality loss from rasterization.  The default ``analyze_pdf``
    implementation raises ``NotImplementedError``; callers that need full-PDF
    processing must check ``engine.supports_pdf_input`` first.
    """

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Short stable identifier used in logs and reports.

        Examples: ``'easyocr'``, ``'mineru'``, ``'paddleocr'``.
        """
        ...

    # ------------------------------------------------------------------
    # Capability flags
    # ------------------------------------------------------------------

    @property
    def provides_layout(self) -> bool:
        """True when the engine pre-classifies page regions into layout zones.

        Engines with ``provides_layout=True`` (e.g. MinerU) emit tokens that
        already carry a ``zone_hint`` field.  The OCR segmenter will use those
        hints directly instead of running heuristic zone detection.
        """
        return False

    @property
    def supports_pdf_input(self) -> bool:
        """True when the engine can process a full PDF without prior rasterization.

        Engines that return True here should override ``analyze_pdf``.
        """
        return False

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    @abstractmethod
    def analyze_image(
        self,
        image_path: Path,
        page_index: int = 0,
    ) -> OcrPage:
        """Extract text tokens from a rasterized page image.

        Args:
            image_path: Path to the rendered page image (PNG / JPEG).
            page_index: Zero-based page index used for result attribution.

        Returns:
            ``OcrPage`` with token-level text, confidences, and bounding boxes
            in pixel coordinates.
        """
        ...

    def analyze_pdf(
        self,
        pdf_path: Path,
        dpi: int = 200,
    ) -> list[OcrPage]:
        """Extract text tokens from every page of a PDF file.

        The default implementation raises ``NotImplementedError``.  Engines
        that can process PDFs natively (``supports_pdf_input=True``) must
        override this method to avoid rasterization quality loss.

        Args:
            pdf_path: Path to the PDF file.
            dpi:      Rendering resolution (only relevant for raster-based
                      engines; native engines may ignore it).

        Returns:
            Ordered list of ``OcrPage`` objects, one per page.
        """
        raise NotImplementedError(
            f"{self.engine_name} does not support native PDF input. "
            "Render the PDF to images first and call analyze_image per page."
        )
