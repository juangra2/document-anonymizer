"""Text reflow — reconstruct logical lines from page geometry.

PDF text extraction (PyMuPDF) emits one visual line at a time and inserts a
``\\n`` at every soft line wrap, splitting entities such as postal addresses
across two rows (``Ctra. de las Flores, 112, …`` → ``Ctra. de las`` + ``Flores,
112, …``).  When the PII analyser sees each visual row in isolation it cannot
match the full entity.

MinerU already solves this natively: every ContentBlock is a fully reflowed
paragraph, so a MinerU body zone contains the complete address.  This module
brings the digital (PyMuPDF) path up to the same quality by grouping the visual
word-rows that belong to the same structural block into one logical zone, using
the same signal MinerU exposes as block structure — PyMuPDF's ``block_no`` — plus
geometric adjacency.

Offset invariant
----------------
The digital redactor maps an entity span back to word rectangles via
``zone.page_text_offset + local_offset`` into ``PageText.text``.  Reflow must
therefore preserve character offsets: it only ever **substitutes** an interior
soft ``\\n`` with a space (1 char → 1 char), never deletes characters.  The zone
text stays a length-preserving transform of the contiguous ``PageText`` slice, so
``rects_for_span`` still yields one rectangle per visual line.
"""
from __future__ import annotations

import re

from app.services.pdf.extractor import PageText, WordBox

# A line break is treated as a soft wrap (same logical line) only when the next
# row starts within this multiple of the median line height below the previous
# row.  Larger vertical gaps mark a paragraph break and start a new group.
_LINE_GAP_FACTOR: float = 1.6

# A row that opens with a "Label:" prefix starts a new logical field, not a
# wrapped continuation of the previous one.  Merging such rows would flatten the
# newline that the line-anchored LABELED_FIELD_PATTERNS (^label: value$) rely on.
_FIELD_START_RE = re.compile(r"^\s*[^\W\d][^:\r\n]{0,40}:")


def normalize_inline_newlines(text: str) -> str:
    """Replace interior CR/LF with spaces (length-preserving).

    Used to flatten a multi-row logical line into a single analysable string
    without shifting any character offset.
    """
    return text.replace("\r\n", "  ").replace("\n", " ").replace("\r", " ")


def group_logical_lines(
    body_rows: list[list[WordBox]],
    page_text: PageText,
) -> list[list[WordBox]]:
    """Group consecutive visual word-rows into logical lines (paragraphs).

    ``body_rows`` must be in reading order (top-to-bottom).  A row is merged into
    the previous group only when it is a **soft-wrapped continuation** of it:

    * the row is **offset-contiguous** in ``PageText.text`` — the characters
      between the end of the previous row and the start of the next are
      whitespace only (no other row's words are interleaved, e.g. a FORM row
      classified out by Y-geometry); and
    * it shares the same ``block_no`` at the junction (PyMuPDF's structural
      block, the digital analogue of a MinerU ContentBlock); and
    * it is **vertically adjacent** — the gap is at most ``_LINE_GAP_FACTOR``
      times the median line height; and
    * it does **not start a new "Label:" field** and the previous row does not
      end a sentence — both signal a new logical line, not a wrap.

    Keeping new-field rows separate preserves the per-line ``^label: value$``
    anchoring that LABELED_FIELD_PATTERNS depend on.

    Returns a list of groups, each a flat list of WordBoxes in reading order.
    """
    if not body_rows:
        return []

    heights = [
        w.rect.height
        for row in body_rows
        for w in row
        if w.rect.height > 0
    ]
    line_h = _median(heights) if heights else 10.0
    max_gap_y = line_h * _LINE_GAP_FACTOR

    groups: list[list[WordBox]] = [list(body_rows[0])]
    prev_row = body_rows[0]

    for row in body_rows[1:]:
        if row and prev_row and _should_merge(prev_row, row, page_text, max_gap_y):
            groups[-1].extend(row)
        else:
            groups.append(list(row))
        prev_row = row

    return groups


def _should_merge(
    prev_row: list[WordBox],
    row: list[WordBox],
    page_text: PageText,
    max_gap_y: float,
) -> bool:
    prev_first = min(prev_row, key=lambda w: w.start)
    prev_last = max(prev_row, key=lambda w: w.end)
    row_first = min(row, key=lambda w: w.start)
    row_last = max(row, key=lambda w: w.end)

    # Offset contiguity: only whitespace may sit between the two rows.  Any
    # non-space character means another row's words are interleaved (e.g. a FORM
    # row split out by Y-band) and the slice would not be a clean paragraph.
    between = page_text.text[prev_last.end:row_first.start]
    if between.strip():
        return False

    # Same structural block at the junction.
    if prev_last.block_no != row_first.block_no:
        return False

    # Vertical adjacency: reject paragraph breaks and unrelated distant rows.
    prev_bottom = max(w.rect.y1 for w in prev_row)
    row_top = min(w.rect.y0 for w in row)
    gap_y = row_top - prev_bottom
    if gap_y > max_gap_y:
        return False

    # A row opening a new "Label:" field is a distinct logical line — merging it
    # would destroy the newline its ^label: value$ pattern anchors on.
    row_text = page_text.text[row_first.start:row_last.end]
    if _FIELD_START_RE.match(row_text):
        return False

    # The previous row ending a sentence marks a hard break, not a wrap.
    prev_text = page_text.text[prev_first.start:prev_last.end]
    if prev_text.rstrip().endswith("."):
        return False

    return True


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
