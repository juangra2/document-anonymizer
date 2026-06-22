"""Internal digital-layout analysis for DigitalPageSegmenter.

Detects form field-value rows and multi-column tables in digital PDF pages
and returns pre-computed redaction rects.  Only imported by segmenters.py.
"""
from __future__ import annotations

from dataclasses import dataclass
import statistics

import fitz

from app.services.pdf.extractor import PageText, WordBox
from app.services.pdf.table_layout import (
    find_label_length_at_row_start,
    group_words_by_visual_row,
    normalize_token,
)


@dataclass
class DigitalLayoutAnalysis:
    redaction_rects: list[fitz.Rect]
    protected_rects: list[fitz.Rect]
    layout_zone_rects: list[fitz.Rect]


SENSITIVE_TABLE_HEADER_KEYWORDS = {
    "persona", "nombre", "documento", "dni", "nie", "nif", "pasaporte",
    "contacto", "email", "correo", "telefono", "teléfono",
    "direccion", "dirección", "datos", "anonimizar",
    "valor", "ejemplo", "ip", "token", "usuario", "iban", "tarjeta",
    "apellido", "cognom",
}

FULL_CELL_REDACTION_HEADER_KEYWORDS = SENSITIVE_TABLE_HEADER_KEYWORDS

# Short/ambiguous headers matched by EXACT normalized equality.  A substring
# match would over-trigger ("nom" inside "nomina"), so these are sensitive only
# when the whole cell equals one of them.  Covers the fragmented person-name
# columns and the electronic-signature footer table.
EXACT_SENSITIVE_HEADERS = {
    "nom", "data", "fecha",
    "primer apellido", "segundo apellido", "primer cognom", "segon cognom",
    "nom complet", "nom i cognoms",
}

# Person-identity headers: recognise a name split across columns even without a
# "persona/datos" shape word.
IDENTITY_HEADERS = {
    "nombre", "nom", "nom complet", "nom i cognoms", "apellidos", "cognoms",
    "primer apellido", "segundo apellido", "primer cognom", "segon cognom",
}

# Headers in the "Signat electrònicament per" footer table that are NOT personal
# data: the role prefix (Antefirma) and the certificate issuer (e.g. ACCV).
NON_SENSITIVE_TABLE_HEADER_KEYWORDS = {
    "id caso", "id", "caso",
    "antefirma", "emissor", "emisor", "emissor cert", "emisor cert",
    "emissor del certificat", "emisor del certificado", "expedidor",
}

# Field labels that, when they occupy a whole row on their own, carry their
# value on the row immediately below (vertical label→value layout, common in the
# address sub-form: "Nombre" / "paseo de la castellana").
VERTICAL_VALUE_LABELS = {
    "nombre", "nom", "nombre de la via", "nom de la via",
    "tipo de via", "tipus de via",
    "primer apellido", "segundo apellido", "apellidos", "apellido",
    "primer cognom", "segon cognom", "cognoms", "cognom",
    "domicilio", "direccion", "adreca", "adreça",
    "poblacion", "municipio", "localidad", "provincia",
    "codigo postal", "codi postal",
}

TABLE_STOP_PREFIXES = {
    "observacion", "observación", "nota",
    "pagina", "página", "prueba tecnica", "prueba técnica",
}


def analyze_digital_layout(page_text: PageText) -> DigitalLayoutAnalysis:
    rows = group_words_by_visual_row(page_text.words)

    redaction_rects: list[fitz.Rect] = []
    protected_rects: list[fitz.Rect] = []
    layout_zone_rects: list[fitz.Rect] = []

    _collect_field_value_rows(rows, redaction_rects, protected_rects, layout_zone_rects)
    _collect_vertical_field_value_rows(rows, redaction_rects, protected_rects, layout_zone_rects)
    _collect_multicolumn_tables(rows, redaction_rects, protected_rects, layout_zone_rects)

    return DigitalLayoutAnalysis(
        redaction_rects=_dedupe_rects(redaction_rects),
        protected_rects=_dedupe_rects(protected_rects),
        layout_zone_rects=_dedupe_rects(layout_zone_rects),
    )


def _collect_field_value_rows(
    rows: list[list[WordBox]],
    redaction_rects: list[fitz.Rect],
    protected_rects: list[fitz.Rect],
    layout_zone_rects: list[fitz.Rect],
) -> None:
    for row in rows:
        if len(row) < 2:
            continue

        row_tokens = [normalize_token(word.text) for word in row]
        label_length = find_label_length_at_row_start(row_tokens)

        if label_length is None:
            continue

        label_words = row[:label_length]
        value_words = row[label_length:]

        if not label_words or not value_words:
            continue

        if not _has_table_like_gap(label_words, value_words):
            continue

        protected_rects.append(_rect_for_words(label_words, padding=0.8))
        redaction_rects.append(_rect_for_words(value_words, padding=0.8))
        layout_zone_rects.append(_rect_for_words(row, padding=1.2))


def _collect_vertical_field_value_rows(
    rows: list[list[WordBox]],
    redaction_rects: list[fitz.Rect],
    protected_rects: list[fitz.Rect],
    layout_zone_rects: list[fitz.Rect],
) -> None:
    """Redact values laid out *below* their label (vertical label→value).

    Handles the address sub-form where a label occupies a whole row and its
    value is on the next row ("Nombre" / "paseo de la castellana").  The label
    row is protected and the value row redacted, with strict adjacency and
    horizontal-overlap guards to avoid redacting unrelated rows.
    """
    for i in range(len(rows) - 1):
        row = rows[i]
        if not row:
            continue
        if normalize_token(_row_text(row)) not in VERTICAL_VALUE_LABELS:
            continue

        label_rect = _rect_for_words(row)
        next_row = rows[i + 1]
        if not next_row:
            continue

        next_rect = _rect_for_words(next_row)
        gap = next_rect.y0 - label_rect.y1
        if gap < -2.0 or gap > max(20.0, label_rect.height * 2.2):
            continue
        # The value must sit roughly under the label's column.
        if next_rect.x1 < label_rect.x0 - 4.0 or next_rect.x0 > label_rect.x1 + 60.0:
            continue

        value_text = normalize_token(_row_text(next_row))
        if not value_text or value_text in VERTICAL_VALUE_LABELS:
            continue
        if _looks_like_header_or_static_text(value_text):
            continue
        # Don't fire when the value row is itself a label:value row (handled
        # horizontally) — i.e. it already starts with a recognised label.
        if find_label_length_at_row_start([normalize_token(w.text) for w in next_row]):
            continue

        protected_rects.append(label_rect)
        redaction_rects.append(_rect_for_words(next_row, padding=0.8))
        layout_zone_rects.append(_rect_for_words(row + next_row, padding=1.2))


def _collect_multicolumn_tables(
    rows: list[list[WordBox]],
    redaction_rects: list[fitz.Rect],
    protected_rects: list[fitz.Rect],
    layout_zone_rects: list[fitz.Rect],
) -> None:
    row_index = 0

    while row_index < len(rows):
        row = rows[row_index]
        cells = _split_row_into_visual_cells(row)

        if not _is_sensitive_multicolumn_header(cells):
            row_index += 1
            continue

        table_rows = _rows_belonging_to_header_table(rows=rows, header_index=row_index)

        if not table_rows:
            row_index += 1
            continue

        for cell in cells:
            protected_rects.append(_rect_for_words(cell, padding=0.8))

        column_bounds = _column_bounds_from_header(
            header_cells=cells, table_rows=table_rows
        )
        sensitive_column_indexes = [
            i for i, cell in enumerate(cells)
            if _is_sensitive_header_cell(_cell_text(cell))
        ]

        for data_row in table_rows:
            if _is_table_stop_row(_row_text(data_row)):
                continue

            layout_zone_rects.append(_rect_for_words(data_row, padding=1.0))

            for col_idx in sensitive_column_indexes:
                if col_idx >= len(column_bounds):
                    continue

                x0, x1 = column_bounds[col_idx]
                value_words = [
                    w for w in data_row if x0 <= _center_x(w.rect) <= x1
                ]

                if not value_words:
                    continue

                if _looks_like_header_or_static_text(_row_text(value_words)):
                    continue

                if _should_redact_full_cell(_cell_text(cells[col_idx])):
                    redaction_rects.append(
                        _cell_rect_for_column_and_row(data_row, x0, x1, padding=0.9)
                    )
                else:
                    redaction_rects.append(_rect_for_words(value_words, padding=0.9))

        row_index += max(1, len(table_rows) + 1)


def _rows_belonging_to_header_table(
    rows: list[list[WordBox]], header_index: int
) -> list[list[WordBox]]:
    header_row = rows[header_index]
    header_rect = _rect_for_words(header_row, padding=0)
    header_height = max(
        8.0,
        statistics.median([w.rect.height for w in header_row if w.rect.height > 0]),
    )

    table_rows: list[list[WordBox]] = []
    previous_bottom = max(w.rect.y1 for w in header_row)

    for row in rows[header_index + 1:]:
        if not row:
            continue

        row_top = min(w.rect.y0 for w in row)
        row_bottom = max(w.rect.y1 for w in row)
        vertical_gap = row_top - previous_bottom

        if _is_table_stop_row(_row_text(row)):
            break
        if vertical_gap > max(22.0, header_height * 2.4):
            break

        row_rect = _rect_for_words(row, padding=0)
        if row_rect.x1 < header_rect.x0 or row_rect.x0 > header_rect.x1 + 120:
            break
        if _is_sensitive_multicolumn_header(_split_row_into_visual_cells(row)):
            break

        table_rows.append(row)
        previous_bottom = row_bottom

    return table_rows


def _split_row_into_visual_cells(row: list[WordBox]) -> list[list[WordBox]]:
    if not row:
        return []

    words = sorted(row, key=lambda w: w.rect.x0)
    heights = [w.rect.height for w in words if w.rect.height > 0]
    median_height = statistics.median(heights) if heights else 10
    gap_threshold = max(8.0, median_height * 1.15)

    cells: list[list[WordBox]] = []
    current_cell: list[WordBox] = [words[0]]

    for prev, curr in zip(words, words[1:]):
        if curr.rect.x0 - prev.rect.x1 > gap_threshold:
            cells.append(current_cell)
            current_cell = [curr]
        else:
            current_cell.append(curr)

    if current_cell:
        cells.append(current_cell)

    return cells


def _is_sensitive_multicolumn_header(cells: list[list[WordBox]]) -> bool:
    if len(cells) < 3:
        return False

    cell_texts = [normalize_token(_cell_text(cell)) for cell in cells]
    joined = " ".join(cell_texts)
    hits = sum(1 for t in cell_texts if _is_sensitive_header_cell(t))

    has_shape = any(
        kw in joined for kw in ("persona", "documento", "contacto", "direccion", "datos")
    )
    if hits >= 3 and has_shape:
        return True

    # A person name split across columns (Nombre | Primer apellido | Segundo
    # apellido) has no "persona/datos" shape word but is clearly sensitive.
    identity_hits = sum(
        1 for t in cell_texts
        if t in IDENTITY_HEADERS or "apellido" in t or "cognom" in t
    )
    if identity_hits >= 2:
        return True

    # Electronic-signature footer table: Antefirma | Nom | Data | Emissor cert |
    # Núm. sèrie cert.  Recognised by the Nom+Data pair or a cert-serial header.
    esig = (
        ("nom" in cell_texts and any(t in ("data", "fecha") for t in cell_texts))
        or any("serie" in t and ("cert" in t or "certificat" in t) for t in cell_texts)
    )
    if esig and hits >= 2:
        return True

    return False


def _is_sensitive_header_cell(text: str) -> bool:
    normalized = normalize_token(text)
    if normalized in NON_SENSITIVE_TABLE_HEADER_KEYWORDS:
        return False
    if normalized in EXACT_SENSITIVE_HEADERS:
        return True
    # Certificate serial number column ("Núm. sèrie cert", "Número de serie").
    if "serie" in normalized and ("cert" in normalized or "certificat" in normalized):
        return True
    return any(kw in normalized for kw in SENSITIVE_TABLE_HEADER_KEYWORDS)


def _column_bounds_from_header(
    header_cells: list[list[WordBox]], table_rows: list[list[WordBox]]
) -> list[tuple[float, float]]:
    header_cells = [sorted(c, key=lambda w: w.rect.x0) for c in header_cells if c]
    if not header_cells:
        return []

    starts = [min(w.rect.x0 for w in cell) for cell in header_cells]
    all_words = [w for row in table_rows for w in row]
    table_right = (
        max(w.rect.x1 for w in all_words) + 4.0
        if all_words
        else max(w.rect.x1 for cell in header_cells for w in cell) + 4.0
    )

    bounds: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        left = start - 3.0
        right = starts[i + 1] - 3.0 if i + 1 < len(starts) else table_right
        if right > left:
            bounds.append((left, right))

    return bounds


def _has_table_like_gap(label_words: list[WordBox], value_words: list[WordBox]) -> bool:
    gap = value_words[0].rect.x0 - label_words[-1].rect.x1
    all_words = label_words + value_words
    heights = [w.rect.height for w in all_words if w.rect.height > 0]
    median_height = statistics.median(heights) if heights else 10
    return gap >= max(7.0, median_height * 0.65)


def _is_table_stop_row(row_text: str) -> bool:
    normalized = normalize_token(row_text)
    if not normalized:
        return True
    return any(normalized.startswith(p) for p in TABLE_STOP_PREFIXES)


def _looks_like_header_or_static_text(text: str) -> bool:
    normalized = normalize_token(text)
    if not normalized or _is_table_stop_row(normalized):
        return True
    return normalized in {
        "id caso", "persona", "documento", "contacto",
        "direccion", "dirección", "datos a anonimizar",
        "campo", "valor ficticio",
    }


def _should_redact_full_cell(header_text: str) -> bool:
    return any(kw in normalize_token(header_text) for kw in FULL_CELL_REDACTION_HEADER_KEYWORDS)


def _rect_for_words(words: list[WordBox], padding: float = 0.5) -> fitz.Rect:
    rect = fitz.Rect(words[0].rect)
    for w in words[1:]:
        rect |= w.rect
    rect.x0 -= padding
    rect.y0 -= padding
    rect.x1 += padding
    rect.y1 += padding
    return rect


def _cell_text(cell: list[WordBox]) -> str:
    return " ".join(w.text for w in cell).strip()


def _row_text(row: list[WordBox]) -> str:
    return " ".join(w.text for w in row).strip()


def _center_x(rect: fitz.Rect) -> float:
    return (rect.x0 + rect.x1) / 2


def _cell_rect_for_column_and_row(
    row_words: list[WordBox], x0: float, x1: float, padding: float = 0.8
) -> fitz.Rect:
    if not row_words:
        return fitz.Rect(x0, 0, x1, 0)
    row_top = min(w.rect.y0 for w in row_words)
    row_bottom = max(w.rect.y1 for w in row_words)
    return fitz.Rect(x0 + padding, row_top - padding, x1 - padding, row_bottom + padding)


def _padded_rect(rect: fitz.Rect, padding_x: float, padding_y: float) -> fitz.Rect:
    return fitz.Rect(rect.x0 - padding_x, rect.y0 - padding_y,
                     rect.x1 + padding_x, rect.y1 + padding_y)


def _dedupe_rects(rects: list[fitz.Rect], tolerance: float = 0.8) -> list[fitz.Rect]:
    unique: list[fitz.Rect] = []
    for rect in rects:
        if not any(
            abs(rect.x0 - e.x0) <= tolerance and abs(rect.y0 - e.y0) <= tolerance
            and abs(rect.x1 - e.x1) <= tolerance and abs(rect.y1 - e.y1) <= tolerance
            for e in unique
        ):
            unique.append(rect)
    return unique
