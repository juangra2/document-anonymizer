"""Layout-first segmenters.

Each segmenter takes a page (digital or OCR) and returns a ZonedPage where
every zone carries its type, pre-computed structural redaction rects, and the
text/tokens the PII analyzer should inspect.  Redactors never call layout
analysis directly; they consume ZonedPage.

Architecture
------------
Digital pages
    DigitalPageSegmenter.segment(page_text, page_rect)
    1. Partitions word-rows into HEADER / FOOTER / middle by Y-band.
    2. Runs structural layout analysis (form field-value pairs, multi-column
       tables) on the middle rows via digital_layout.
    3. Rows that fall inside a detected layout zone → FORM zone with
       pre-computed layout_redact_rects / layout_protected_rects.
    4. Remaining middle rows → one BODY zone each (one per row so that
       page_text_offset maps directly to PageText character positions).

OCR pages
    OcrPageSegmenter.segment(ocr_page, image?)
    1. Partitions token-rows into HEADER / FOOTER / middle by Y-band.
    2. Runs OcrFormLayoutAnalyzer + OcrTableLayoutAnalyzer on middle tokens.
    3. Tokens covered by layout rects → FORM zone with layout_redact_rects.
    4. Remaining middle tokens → BODY zone.
    5. If image provided: adds SIGNATURE and QR_CODE zones.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import TYPE_CHECKING

import fitz
from PIL import Image

from app.services.layout.models import PageZone, ZoneToken, ZonedPage, ZoneType
from app.services.ocr.form_layout import OcrFormLayoutAnalyzer, padded_rect
from app.services.ocr.models import OcrPage, OcrToken
from app.services.ocr.signature_detector import detect_signature_rect
from app.services.ocr.table_layout import OcrTableLayoutAnalyzer
from app.services.pdf.digital_layout import (
    DigitalLayoutAnalysis,
    analyze_digital_layout,
)
from app.services.pdf.extractor import PageText, WordBox
from app.services.pdf.table_layout import group_words_by_visual_row
from app.services.vision.code_detector import detect_visual_code_rects

# Y-position thresholds as fraction of page height.
_HEADER_RATIO: float = 0.12
_FOOTER_RATIO: float = 0.88


# ---------------------------------------------------------------------------
# Digital segmenter
# ---------------------------------------------------------------------------

class DigitalPageSegmenter:
    """Segments a digital (selectable-text) PDF page into typed zones."""

    def segment(
        self,
        page_text: PageText,
        page_rect: fitz.Rect,
    ) -> ZonedPage:
        rows = group_words_by_visual_row(page_text.words)

        if not rows:
            return ZonedPage(
                page_index=page_text.page_index,
                width=page_rect.width,
                height=page_rect.height,
            )

        height = page_rect.height
        header_y = height * _HEADER_RATIO
        footer_y = height * _FOOTER_RATIO

        header_rows: list[list[WordBox]] = []
        footer_rows: list[list[WordBox]] = []
        middle_rows: list[list[WordBox]] = []

        for row in rows:
            cy = _row_center_y(row)
            if cy < header_y:
                header_rows.append(row)
            elif cy > footer_y:
                footer_rows.append(row)
            else:
                middle_rows.append(row)

        # Run structural analysis on the full page_text so relative positions
        # of all words are preserved.  analyze_digital_layout only reads
        # page_text.words for geometry, so passing a PageText with only the
        # middle words (but the original .text string) is safe.
        middle_page_text = PageText(
            page_index=page_text.page_index,
            text=page_text.text,
            words=[w for row in middle_rows for w in row],
        )
        layout = analyze_digital_layout(middle_page_text)

        zones: list[PageZone] = []

        for row in header_rows:
            zone = self._row_to_zone(ZoneType.HEADER, row, page_text)
            if zone:
                zones.append(zone)

        zones.extend(self._build_middle_zones(middle_rows, layout, page_text))

        for row in footer_rows:
            zone = self._row_to_zone(ZoneType.FOOTER, row, page_text)
            if zone:
                zones.append(zone)

        zones.sort(key=lambda z: z.rect.y0)

        return ZonedPage(
            page_index=page_text.page_index,
            width=page_rect.width,
            height=page_rect.height,
            zones=zones,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_middle_zones(
        self,
        middle_rows: list[list[WordBox]],
        layout: DigitalLayoutAnalysis,
        page_text: PageText,
    ) -> list[PageZone]:
        zones: list[PageZone] = []

        form_rows: list[list[WordBox]] = []
        body_rows: list[list[WordBox]] = []

        for row in middle_rows:
            row_rect = _rect_for_word_row(row)
            if _rect_in_layout_zones(row_rect, layout.layout_zone_rects):
                form_rows.append(row)
            else:
                body_rows.append(row)

        # One FORM zone that carries all pre-computed structural rects.
        # PII analysis is NOT run on this zone (layout handles all redaction).
        if form_rows and layout.redaction_rects:
            all_form_words = [w for row in form_rows for w in row]
            zones.append(PageZone(
                zone_type=ZoneType.FORM,
                rect=_rect_for_word_row(all_form_words),
                text="",  # not used for PII analysis
                tokens=[
                    ZoneToken(text=w.text, rect=w.rect)
                    for w in sorted(all_form_words, key=lambda w: (w.rect.y0, w.rect.x0))
                ],
                source="digital",
                layout_redact_rects=layout.redaction_rects,
                layout_protected_rects=layout.protected_rects,
            ))

        # One BODY zone per row so page_text_offset maps directly.
        for row in body_rows:
            zone = self._row_to_zone(ZoneType.BODY, row, page_text)
            if zone:
                zones.append(zone)

        return zones

    @staticmethod
    def _row_to_zone(
        zone_type: ZoneType,
        row: list[WordBox],
        page_text: PageText,
    ) -> PageZone | None:
        if not row:
            return None

        text_start = min(w.start for w in row)
        text_end = max(w.end for w in row)
        text = page_text.text[text_start:text_end]

        if not text.strip():
            return None

        return PageZone(
            zone_type=zone_type,
            rect=_rect_for_word_row(row),
            text=text,
            tokens=[
                ZoneToken(text=w.text, rect=w.rect)
                for w in sorted(row, key=lambda w: (w.rect.y0, w.rect.x0))
            ],
            source="digital",
            page_text_offset=text_start,
        )


# ---------------------------------------------------------------------------
# OCR segmenter
# ---------------------------------------------------------------------------

class OcrPageSegmenter:
    """Segments an OCR-processed page into typed zones."""

    def __init__(self) -> None:
        self._form_analyzer = OcrFormLayoutAnalyzer()
        self._table_analyzer = OcrTableLayoutAnalyzer()

    def segment(
        self,
        ocr_page: OcrPage,
        image: Image.Image | None = None,
    ) -> ZonedPage:
        tokens = ocr_page.tokens
        height = float(ocr_page.height)
        width = float(ocr_page.width)

        header_y = height * _HEADER_RATIO
        footer_y = height * _FOOTER_RATIO

        rows = self._form_analyzer.group_tokens_by_row(tokens)

        header_tokens: list[OcrToken] = []
        footer_tokens: list[OcrToken] = []
        middle_tokens: list[OcrToken] = []

        for row in rows:
            cy = _ocr_row_center_y(row)
            if cy < header_y:
                header_tokens.extend(row)
            elif cy > footer_y:
                footer_tokens.extend(row)
            else:
                middle_tokens.extend(row)

        zones: list[PageZone] = []

        # Header
        if header_tokens:
            zones.append(self._tokens_to_zone(ZoneType.HEADER, header_tokens))

        # Middle: structural detection first
        zones.extend(self._build_middle_zones(middle_tokens))

        # Footer
        if footer_tokens:
            zones.append(self._tokens_to_zone(ZoneType.FOOTER, footer_tokens))

        # SIGNATURE — requires image
        if image is not None:
            sig_rect = detect_signature_rect(image=image, tokens=tokens)
            if sig_rect is not None:
                zones.append(PageZone(
                    zone_type=ZoneType.SIGNATURE,
                    rect=sig_rect,
                    text="",
                    source="ocr",
                ))

        # QR / visual codes — requires image
        if image is not None:
            for qr_rect in detect_visual_code_rects(image):
                zones.append(PageZone(
                    zone_type=ZoneType.QR_CODE,
                    rect=qr_rect,
                    text="",
                    source="ocr",
                ))

        zones.sort(key=lambda z: z.rect.y0)

        return ZonedPage(
            page_index=ocr_page.page_index,
            width=width,
            height=height,
            zones=zones,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_middle_zones(
        self,
        middle_tokens: list[OcrToken],
    ) -> list[PageZone]:
        if not middle_tokens:
            return []

        form_rects = self._form_analyzer.rects_for_form_values(middle_tokens)
        table_rects = self._table_analyzer.rects_for_tables(middle_tokens)
        all_layout_rects = form_rects + table_rects

        # Merge close rects to avoid fragmenting a single form block
        merged_layout = self._form_analyzer.merge_close_rects(all_layout_rects)

        zones: list[PageZone] = []

        if merged_layout:
            covered: list[OcrToken] = []
            body_tokens: list[OcrToken] = []

            for token in middle_tokens:
                if _token_center_in_any(token, merged_layout):
                    covered.append(token)
                else:
                    body_tokens.append(token)

            if covered:
                zones.append(PageZone(
                    zone_type=ZoneType.FORM,
                    rect=_rect_for_ocr_tokens(covered),
                    text="",  # layout handles redaction; PII not run here
                    tokens=[
                        ZoneToken(text=t.text, rect=t.rect, confidence=t.confidence)
                        for t in covered
                    ],
                    confidence=_avg_token_confidence(covered),
                    source="ocr",
                    layout_redact_rects=merged_layout,
                ))

            if body_tokens:
                zones.append(self._tokens_to_zone(ZoneType.BODY, body_tokens))
        else:
            # No structural layout detected — full page is free text
            zones.append(self._tokens_to_zone(ZoneType.BODY, middle_tokens))

        return zones

    @staticmethod
    def _tokens_to_zone(
        zone_type: ZoneType,
        ocr_tokens: list[OcrToken],
    ) -> PageZone:
        text, zone_tokens = _build_ocr_zone_text_and_tokens(ocr_tokens)
        return PageZone(
            zone_type=zone_type,
            rect=_rect_for_ocr_tokens(ocr_tokens),
            text=text,
            tokens=zone_tokens,
            confidence=_avg_token_confidence(ocr_tokens),
            source="ocr",
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_ocr_zone_text_and_tokens(
    ocr_tokens: list[OcrToken],
) -> tuple[str, list[ZoneToken]]:
    """Build zone text and ZoneTokens with text_offset set.

    Tokens are sorted top-to-bottom, left-to-right before building text so
    text_offset corresponds to reading order within the zone.
    """
    sorted_tokens = sorted(ocr_tokens, key=lambda t: (t.rect.y0, t.rect.x0))

    parts: list[str] = []
    zone_tokens: list[ZoneToken] = []
    cursor = 0

    for i, token in enumerate(sorted_tokens):
        if i > 0:
            parts.append("\n")
            cursor += 1

        text_offset = cursor
        parts.append(token.text)
        cursor += len(token.text)

        zone_tokens.append(ZoneToken(
            text=token.text,
            rect=token.rect,
            confidence=token.confidence,
            text_offset=text_offset,
        ))

    return "".join(parts), zone_tokens


def _row_center_y(row: list[WordBox]) -> float:
    return sum((w.rect.y0 + w.rect.y1) / 2 for w in row) / len(row)


def _ocr_row_center_y(row: list[OcrToken]) -> float:
    return sum((t.rect.y0 + t.rect.y1) / 2 for t in row) / len(row)


def _rect_for_word_row(words: list[WordBox], padding: float = 0.0) -> fitz.Rect:
    rect = fitz.Rect(words[0].rect)
    for w in words[1:]:
        rect |= w.rect
    rect.x0 -= padding
    rect.y0 -= padding
    rect.x1 += padding
    rect.y1 += padding
    return rect


def _rect_for_ocr_tokens(tokens: list[OcrToken]) -> fitz.Rect:
    rect = fitz.Rect(tokens[0].rect)
    for t in tokens[1:]:
        rect |= t.rect
    return rect


def _avg_token_confidence(tokens: list[OcrToken]) -> float | None:
    scores = [t.confidence for t in tokens]
    if not scores:
        return None
    return sum(scores) / len(scores)


def _rect_in_layout_zones(
    rect: fitz.Rect,
    layout_zone_rects: list[fitz.Rect],
) -> bool:
    """Return True if rect's centre falls inside any layout zone."""
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    for lz in layout_zone_rects:
        expanded = fitz.Rect(lz.x0 - 2, lz.y0 - 2, lz.x1 + 2, lz.y1 + 2)
        if expanded.x0 <= cx <= expanded.x1 and expanded.y0 <= cy <= expanded.y1:
            return True
    return False


def _token_center_in_any(
    token: OcrToken,
    rects: list[fitz.Rect],
) -> bool:
    cx = (token.rect.x0 + token.rect.x1) / 2
    cy = (token.rect.y0 + token.rect.y1) / 2
    for r in rects:
        expanded = fitz.Rect(r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3)
        if expanded.x0 <= cx <= expanded.x1 and expanded.y0 <= cy <= expanded.y1:
            return True
    return False
