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
from app.services.vision.scanned_marks import detect_signature_rects, detect_stamp_rects
from app.services.ocr.table_layout import OcrTableLayoutAnalyzer
from app.services.layout._digital_analysis import (
    DigitalLayoutAnalysis,
    analyze_digital_layout,
)
from app.services.layout.reflow import group_logical_lines, normalize_inline_newlines
from app.services.pdf.extractor import PageText, WordBox
from app.services.pdf.table_layout import group_words_by_visual_row
from app.services.vision.code_detector import detect_redactable_codes

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
        # Text is populated so collect_entities can report entities from form rows
        # (e.g. LICENSE_PLATE in table cells) even though layout handles redaction.
        #
        # Use the original text slice but blank out any body-word characters that
        # fall inside the form slice range.  Rows are classified by Y-geometry, so
        # body rows can have char positions interspersed between form rows; blanking
        # only their word characters (not whitespace/newlines) prevents duplicate
        # PII detections while preserving newlines required by multi-line patterns
        # (e.g. the next-line LICENSE_PLATE pattern that needs \r?\n between label
        # and value).
        if form_rows and layout.redaction_rects:
            all_form_words = [w for row in form_rows for w in row]
            form_text_start = min(w.start for w in all_form_words)
            form_text_end = max(w.end for w in all_form_words)
            form_chars = list(page_text.text[form_text_start:form_text_end])
            for w in [w for row in body_rows for w in row]:
                rel_s = w.start - form_text_start
                rel_e = w.end - form_text_start
                if 0 <= rel_s < len(form_chars) and rel_e <= len(form_chars):
                    form_chars[rel_s:rel_e] = [' '] * (rel_e - rel_s)
            form_text = ''.join(form_chars)
            zones.append(PageZone(
                zone_type=ZoneType.FORM,
                rect=_rect_for_word_row(all_form_words),
                text=form_text,
                tokens=[
                    ZoneToken(text=w.text, rect=w.rect)
                    for w in sorted(all_form_words, key=lambda w: (w.rect.y0, w.rect.x0))
                ],
                source="digital",
                layout_redact_rects=layout.redaction_rects,
                layout_redact_types=layout.redaction_types,
                layout_protected_rects=layout.protected_rects,
                page_text_offset=form_text_start,
            ))

        # Reflow: merge soft-wrapped body rows into logical-line zones so the
        # analyzer sees complete entities (addresses, prose) instead of fragments.
        # Each group is a contiguous PageText slice → page_text_offset maps directly.
        for group in group_logical_lines(body_rows, page_text):
            zone = self._group_to_zone(ZoneType.BODY, group, page_text)
            if zone:
                zones.append(zone)

        return zones

    @staticmethod
    def _group_to_zone(
        zone_type: ZoneType,
        group: list[WordBox],
        page_text: PageText,
    ) -> PageZone | None:
        """Build a BODY zone from a logical-line group (one or more visual rows).

        The text is the contiguous PageText slice with interior soft newlines
        flattened to spaces (length-preserving), so entity offsets still map back
        to the original word boxes and rects_for_span yields one rect per line.
        """
        if not group:
            return None

        text_start = min(w.start for w in group)
        text_end = max(w.end for w in group)
        text = normalize_inline_newlines(page_text.text[text_start:text_end])

        if not text.strip():
            return None

        return PageZone(
            zone_type=zone_type,
            rect=_rect_for_word_row(group),
            text=text,
            tokens=[
                ZoneToken(text=w.text, rect=w.rect)
                for w in sorted(group, key=lambda w: (w.rect.y0, w.rect.x0))
            ],
            source="digital",
            page_text_offset=text_start,
        )

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
    """Segments an OCR-processed page into typed zones.

    Two segmentation paths:

    1. Layout-hint path (engines with provides_layout=True, e.g. MinerU):
       Tokens carry a ``zone_hint`` string.  Zones are built directly from
       those hints without running heuristic form/table detection.

    2. Heuristic path (EasyOCR and any engine without zone hints):
       Existing OcrFormLayoutAnalyzer + OcrTableLayoutAnalyzer detection.

    Regardless of path, SIGNATURE and QR_CODE zones are added via visual
    detection whenever an image is provided.
    """

    _HINT_TO_ZONE: dict[str, ZoneType] = {
        "header": ZoneType.HEADER,
        "footer": ZoneType.FOOTER,
        "table":  ZoneType.TABLE,
        "form":   ZoneType.FORM,
        "body":   ZoneType.BODY,
    }

    def __init__(self) -> None:
        self._form_analyzer = OcrFormLayoutAnalyzer()
        self._table_analyzer = OcrTableLayoutAnalyzer()

    def segment(
        self,
        ocr_page: OcrPage,
        image: Image.Image | None = None,
    ) -> ZonedPage:
        tokens = ocr_page.tokens

        # Choose segmentation strategy based on whether the engine provided hints
        has_hints = any(t.zone_hint is not None for t in tokens)

        if has_hints:
            base_zones = self._segment_from_hints(ocr_page)
        else:
            base_zones = self._segment_heuristic(ocr_page)

        # Visual zones (engine-agnostic — always require image)
        visual_zones: list[PageZone] = []
        if image is not None:
            from app.core.config import settings

            sig_rects: list[fitz.Rect] = []
            if settings.detect_visual_signatures:
                sig_rects = detect_signature_rects(image=image, tokens=tokens)
            if settings.detect_visual_stamps:
                sig_rects = sig_rects + detect_stamp_rects(image=image)
            for sig_rect in sig_rects:
                visual_zones.append(PageZone(
                    zone_type=ZoneType.SIGNATURE,
                    rect=sig_rect,
                    text="",
                    source="ocr",
                ))
            for qr_rect, qr_text in detect_redactable_codes(image):
                visual_zones.append(PageZone(
                    zone_type=ZoneType.QR_CODE,
                    rect=qr_rect,
                    text=qr_text,
                    source="ocr",
                ))

        all_zones = base_zones + visual_zones
        all_zones.sort(key=lambda z: z.rect.y0)

        return ZonedPage(
            page_index=ocr_page.page_index,
            width=float(ocr_page.width),
            height=float(ocr_page.height),
            zones=all_zones,
        )

    # ------------------------------------------------------------------
    # Path 1: engine-provided zone hints (MinerU)
    # ------------------------------------------------------------------

    def _segment_from_hints(self, ocr_page: OcrPage) -> list[PageZone]:
        """Build zones directly from token.zone_hint values."""
        from collections import defaultdict

        groups: dict[str, list[OcrToken]] = defaultdict(list)
        for token in ocr_page.tokens:
            hint = token.zone_hint or "body"
            groups[hint].append(token)

        zones: list[PageZone] = []
        for hint, hint_tokens in groups.items():
            zone_type = self._HINT_TO_ZONE.get(hint, ZoneType.BODY)
            zones.append(self._tokens_to_zone(zone_type, hint_tokens))

        return zones

    # ------------------------------------------------------------------
    # Path 2: heuristic detection (EasyOCR)
    # ------------------------------------------------------------------

    def _segment_heuristic(self, ocr_page: OcrPage) -> list[PageZone]:
        tokens = ocr_page.tokens
        height = float(ocr_page.height)

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

        if header_tokens:
            zones.append(self._tokens_to_zone(ZoneType.HEADER, header_tokens))

        zones.extend(self._build_middle_zones(middle_tokens))

        if footer_tokens:
            zones.append(self._tokens_to_zone(ZoneType.FOOTER, footer_tokens))

        return zones

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_middle_zones(
        self,
        middle_tokens: list[OcrToken],
    ) -> list[PageZone]:
        if not middle_tokens:
            return []

        typed_form = self._form_analyzer.typed_rects_for_form_values(middle_tokens)
        table_rects = self._table_analyzer.rects_for_tables(middle_tokens)

        # Se fusionan solo rects del mismo tipo, para que cada valor pueda
        # liberarse por separado.  Las tablas no anuncian tipo: van sin él.
        typed_layout = self._form_analyzer.merge_close_typed_rects(
            typed_form + [(rect, None) for rect in table_rects]
        )
        merged_layout = [rect for rect, _ in typed_layout]
        merged_types = [etype for _, etype in typed_layout]

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
                    layout_redact_types=merged_types,
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

        # Flatten any newline inside a single block's text (MinerU paragraphs may
        # carry internal wraps) so cross-line entities stay matchable.  Length is
        # preserved, so text_offset and value_start remain valid.
        token_text = normalize_inline_newlines(token.text)

        text_offset = cursor
        parts.append(token_text)
        cursor += len(token_text)

        zone_tokens.append(ZoneToken(
            text=token_text,
            rect=token.rect,
            confidence=token.confidence,
            text_offset=text_offset,
            value_start=getattr(token, "value_offset", None),
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
