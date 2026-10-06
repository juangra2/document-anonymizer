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
import re
from html.parser import HTMLParser
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

# CJK / fullwidth ranges — a Spanish administrative scan contains virtually none,
# so their presence signals a hallucinated VLM block.
_CJK_RE = re.compile(r"[㐀-鿿　-〿＀-￯]")


# La misma pieza de 1-3 letras o cifras repetida 10 veces o mas hasta el final.
# Solo alfanumerica: una linea de puntos o guiones para rellenar ("Firma:
# ..........") es texto del documento, no un bucle.
_BUCLE_FINAL = re.compile(r"([0-9A-Za-z]{1,3}?)\1{9,}\s*$")


def recortar_bucle(content: str) -> tuple[str, bool]:
    """Quita un bucle de repeticion al final del bloque: (texto, si se corto).

    El VLM a veces se queda repitiendo un caracter a mitad de un bloque
    ("...28020113133333333333") y lo que venia detras no llega nunca.  No es un
    bloque degenerado —la primera parte esta bien leida— sino uno CORTADO: se
    conserva lo leido y se marca, para que lo que el bloque no llego a leer lo
    lea EasyOCR (scanned_redactor._zona_de_lo_no_leido).
    """
    m = _BUCLE_FINAL.search(content)
    if not m:
        return content, False
    return content[:m.start()].rstrip(), True


def _is_degenerate_content(content: str) -> bool:
    """True for hallucinated/repetition-loop VLM output (not real document text).

    Signals: a non-trivial fraction of CJK characters, heavy LaTeX-formula spam,
    or a single token dominating a long string (the "α₁ 与 α₂ 与 …" loop).
    Dropping these blocks keeps OCR garbage out of PII detection.
    """
    if not content:
        return False
    n = len(content)
    if n >= 40 and len(_CJK_RE.findall(content)) / n > 0.05:
        return True
    if content.count("\\alpha") >= 12 or content.count("\\therefore") >= 4:
        return True
    tokens = content.split()
    if len(tokens) >= 40:
        from collections import Counter
        most_common = Counter(tokens).most_common(1)[0][1]
        if most_common / len(tokens) > 0.30:
            return True
    return False


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
            # No se pide al VLM el contenido de bloques no textuales: sobre un
            # escaneo clasificado como una sola "image", esa extraccion tardaba
            # ~27 minutos y su resultado se descartaba por degenerado.  Sin
            # texto util, el llamador se repliega al OCR de palabras.
            from app.core.config import settings
            skip = [
                t.strip() for t in settings.ocr_mineru_skip_block_types.split(",")
                if t.strip()
            ]
            blocks = client.two_step_extract(image, not_extract_list=skip or None)
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

        self._apply_token_cap(self._client)

        logger.info("MinerU2.5-Pro model loaded.")
        return self._client

    @staticmethod
    def _apply_token_cap(client: Any) -> None:
        """Bound per-block generation so a hallucination loop can't run for
        minutes.  Best-effort: MinerU stores per-category MinerUSamplingParams
        with max_new_tokens=None (unbounded) by default."""
        from app.core.config import settings
        cap = settings.ocr_mineru_max_new_tokens
        if not cap:
            return
        params = getattr(client, "sampling_params", None)
        if not isinstance(params, dict):
            return
        try:
            for sp in params.values():
                current = getattr(sp, "max_new_tokens", None)
                if current is None or current <= 0 or current > cap:
                    sp.max_new_tokens = cap
        except Exception:
            logger.warning("Could not apply MinerU max_new_tokens cap.")

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

    HTML table blocks are split into per-row tokens so that character-position
    mapping within each token gives approximately correct redaction coordinates.
    Without splitting, a single token covering the whole form produces
    full-height vertical redaction bars (character offset ÷ HTML length × form width).
    """
    from app.core.config import settings

    tokens: list[OcrToken] = []

    for block in blocks:
        content: str = (getattr(block, "content", "") or "").strip()
        if not content:
            continue
        content, cortado = recortar_bucle(content)
        if not content:
            continue

        if _is_degenerate_content(content):
            # El contenido sale del documento: aunque sea basura del modelo,
            # puede arrastrar un nombre.  Solo se registra su tamaño, salvo que
            # alguien active LOG_PII a propósito para depurar.
            if settings.log_pii:
                logger.warning(
                    "Discarding degenerate MinerU block (likely VLM repetition "
                    "loop): %r…", content[:60],
                )
            else:
                logger.warning(
                    "Discarding degenerate MinerU block (likely VLM repetition "
                    "loop): %d characters.", len(content),
                )
            continue

        block_type: str = (getattr(block, "type", "text") or "text").lower()
        zone_hint = _BLOCK_TYPE_TO_HINT.get(block_type, "body")

        bbox_norm: list[float] = getattr(block, "bbox", None) or [0.0, 0.0, 1.0, 1.0]
        rect = _norm_bbox_to_rect(bbox_norm, page_width, page_height)

        # MinerU returns table blocks as HTML. A single token spanning the whole
        # table rect maps character positions to pixel coordinates linearly,
        # generating full-height vertical bars. Parse into per-row tokens instead.
        if block_type == "table" and content.lstrip().startswith("<"):
            row_tokens = _parse_html_table_tokens(content, rect)
            if row_tokens:
                tokens.extend(row_tokens)
                continue
            # Fall through: parsing yielded nothing, use content as plain text

        tokens.append(OcrToken(
            text=content,
            confidence=1.0,  # MinerU VLM does not produce per-token confidence
            rect=rect,
            zone_hint=zone_hint,
            cortado=cortado,
        ))

    return tokens


def _parse_html_table_tokens(
    html_content: str,
    table_rect: fitz.Rect,
) -> list[OcrToken]:
    """Parse a MinerU HTML table into per-row OcrTokens with estimated bboxes.

    MinerU returns table blocks as HTML (e.g. "<table><tr><td>Label:</td>
    <td>Value</td></tr>…</table>"). We extract each <tr> row, skip image-only
    cells (<td><img …></td>), and concatenate remaining cell texts into one
    OcrToken per row with zone_hint="form".

    The row's vertical position is estimated by dividing the table rect equally
    among all rows. Character-proportional mapping within each row token then
    places value-column text at approximately the correct horizontal position —
    far better than the full-height vertical bars from the single-token path.

    Heuristic: equal row height; MinerU does not expose per-cell bboxes.
    LEARNABLE: use actual cell bboxes when the MinerU API provides them.
    """

    class _TableParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[list[str]] = []
            self._row: list[str] | None = None
            self._cell: str | None = None
            self._skip_cell: bool = False  # True when cell contains only <img>

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag == "tr":
                self._row = []
                self._skip_cell = False
            elif tag in ("td", "th") and self._row is not None:
                self._cell = ""
                self._skip_cell = False
            elif tag == "img" and self._cell is not None:
                # Cell contains an image — mark to skip; base64 src is not text
                self._skip_cell = True

        def handle_endtag(self, tag: str) -> None:
            if tag == "tr" and self._row is not None:
                non_empty = [c for c in self._row if c.strip()]
                if non_empty:
                    self.rows.append(non_empty)
                self._row = None
                self._skip_cell = False
            elif tag in ("td", "th"):
                if not self._skip_cell and self._cell and self._cell.strip():
                    if self._row is not None:
                        self._row.append(self._cell.strip())
                self._cell = None
                self._skip_cell = False

        def handle_data(self, data: str) -> None:
            if self._cell is not None and not self._skip_cell:
                self._cell += data

    parser = _TableParser()
    try:
        parser.feed(html_content)
    except Exception:
        # Malformed HTML: strip tags and emit a single plain-text body token
        plain = re.sub(r"<[^>]+>", " ", html_content)
        plain = re.sub(r"\s+", " ", plain).strip()
        if plain:
            return [OcrToken(text=plain, confidence=0.9,
                             rect=table_rect, zone_hint="body")]
        return []

    rows = parser.rows
    if not rows:
        return []

    n_rows = len(rows)
    row_h = max((table_rect.y1 - table_rect.y0) / n_rows, 1.0)

    # Estimate the value column start.
    # Heuristic: label column (including any icon) ≈ 28% of row width.
    # LEARNABLE: actual column widths should come from per-cell bboxes once
    # the MinerU API exposes them; until then, 0.28 fits typical A4 forms.
    _VALUE_COL_FRACTION = 0.28
    table_width = table_rect.x1 - table_rect.x0
    value_col_x0 = table_rect.x0 + table_width * _VALUE_COL_FRACTION

    result: list[OcrToken] = []
    for idx, cells in enumerate(rows):
        if not cells:
            continue

        row_y0 = table_rect.y0 + idx * row_h
        row_y1 = row_y0 + row_h
        # Token rect covers only the VALUE COLUMN — label chars are to the left.
        value_rect = fitz.Rect(value_col_x0, row_y0, table_rect.x1, row_y1)

        # Join cells: if the label cell already ends with ":", add a space;
        # otherwise insert ": " so LABELED_FIELD_PATTERNS can match the row.
        if len(cells) == 1:
            row_text = cells[0]
            value_offset = None  # single-cell row, no label-value split
        elif cells[0].rstrip().endswith(":"):
            row_text = " ".join(cells)
            value_offset = len(cells[0]) + 1  # after "Label: "
        else:
            row_text = cells[0] + ": " + " ".join(cells[1:])
            value_offset = len(cells[0]) + 2  # after "Label: "

        if row_text.strip():
            result.append(OcrToken(
                text=row_text,
                confidence=1.0,
                rect=value_rect,
                zone_hint="form",
                value_offset=value_offset,
            ))

    return result


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
