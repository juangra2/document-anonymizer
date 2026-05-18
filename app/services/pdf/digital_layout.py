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

@dataclass
class DigitalTextSegment:
    text: str
    start: int
    end: int
    rect: fitz.Rect


SENSITIVE_TABLE_HEADER_KEYWORDS = {
    "persona",
    "nombre",
    "documento",
    "dni",
    "nie",
    "nif",
    "pasaporte",
    "contacto",
    "email",
    "correo",
    "telefono",
    "teléfono",
    "direccion",
    "dirección",
    "datos",
    "anonimizar",
    "valor",
    "ejemplo",
    "ip",
    "token",
    "usuario",
    "iban",
    "tarjeta",
}

FULL_CELL_REDACTION_HEADER_KEYWORDS = {
    "persona",
    "nombre",
    "documento",
    "dni",
    "nie",
    "nif",
    "pasaporte",
    "contacto",
    "email",
    "correo",
    "telefono",
    "teléfono",
    "direccion",
    "dirección",
    "datos",
    "anonimizar",
    "valor",
    "ejemplo",
    "ip",
    "token",
    "usuario",
    "iban",
    "tarjeta",
}

NON_SENSITIVE_TABLE_HEADER_KEYWORDS = {
    "id caso",
    "id",
    "caso",
}

TABLE_STOP_PREFIXES = {
    "observacion",
    "observación",
    "nota",
    "pagina",
    "página",
    "prueba tecnica",
    "prueba técnica",
}


def analyze_digital_layout(page_text: PageText) -> DigitalLayoutAnalysis:
    rows = group_words_by_visual_row(page_text.words)

    redaction_rects: list[fitz.Rect] = []
    protected_rects: list[fitz.Rect] = []
    layout_zone_rects: list[fitz.Rect] = []

    _collect_field_value_rows(
        rows=rows,
        redaction_rects=redaction_rects,
        protected_rects=protected_rects,
        layout_zone_rects=layout_zone_rects,
    )

    _collect_multicolumn_tables(
        rows=rows,
        redaction_rects=redaction_rects,
        protected_rects=protected_rects,
        layout_zone_rects=layout_zone_rects,
    )

    return DigitalLayoutAnalysis(
        redaction_rects=_dedupe_rects(redaction_rects),
        protected_rects=_dedupe_rects(protected_rects),
        layout_zone_rects=_dedupe_rects(layout_zone_rects),
    )


def _looks_like_non_content_segment(text: str) -> bool:
    normalized = normalize_token(text)

    if not normalized:
        return True

    # Cabeceras/pies/títulos administrativos sin valores sensibles claros.
    safe_prefixes = (
        "pagina ",
        "página ",
        "prueba tecnica",
        "prueba técnica",
        "documento sintetico",
        "documento sintético",
    )

    if normalized.startswith(safe_prefixes):
        return True

    safe_exact = {
        "campo",
        "valor ficticio",
        "id caso",
        "persona",
        "documento",
        "contacto",
        "direccion",
        "dirección",
        "datos a anonimizar",
    }

    if normalized in safe_exact:
        return True

    return False


def free_text_segments_for_analysis(
    page_text: PageText,
    layout: DigitalLayoutAnalysis,
) -> list[DigitalTextSegment]:
    """
    Devuelve segmentos visuales de texto libre que no pertenecen a tablas
    ya resueltas por layout.

    El analyzer se ejecutará sobre estos segmentos, no sobre toda la página.
    """
    rows = group_words_by_visual_row(page_text.words)

    segments: list[DigitalTextSegment] = []

    for row in rows:
        if not row:
            continue

        row_rect = _rect_for_words(row, padding=0.0)

        # Si la fila pertenece a una tabla/campo-valor, no la analizamos con regex/NER.
        if _rect_center_inside_any(row_rect, layout.layout_zone_rects):
            continue

        if _rect_overlap_ratio_with_any(row_rect, layout.layout_zone_rects) >= 0.35:
            continue

        start = min(word.start for word in row)
        end = max(word.end for word in row)

        raw_text = page_text.text[start:end]

        leading = len(raw_text) - len(raw_text.lstrip(" \t\r\n"))
        trailing_text = raw_text.rstrip(" \t\r\n")

        if not trailing_text:
            continue

        adjusted_start = start + leading
        adjusted_end = start + len(trailing_text)
        segment_text = page_text.text[adjusted_start:adjusted_end]

        if not segment_text.strip():
            continue

        if _looks_like_non_content_segment(segment_text):
            continue

        segments.append(
            DigitalTextSegment(
                text=segment_text,
                start=adjusted_start,
                end=adjusted_end,
                rect=row_rect,
            )
        )

    return segments


def filter_entity_rects_for_layout(
    rects: list[fitz.Rect],
    layout: DigitalLayoutAnalysis,
) -> list[fitz.Rect]:
    filtered: list[fitz.Rect] = []

    for rect in rects:
        if _rect_should_be_skipped(rect, layout):
            continue

        filtered.append(rect)

    return filtered


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

        table_rows = _rows_belonging_to_header_table(
            rows=rows,
            header_index=row_index,
        )

        if not table_rows:
            row_index += 1
            continue

        for cell in cells:
            protected_rects.append(_rect_for_words(cell, padding=0.8))

        column_bounds = _column_bounds_from_header(
            header_cells=cells,
            table_rows=table_rows,
        )

        sensitive_column_indexes = [
            index
            for index, cell in enumerate(cells)
            if _is_sensitive_header_cell(_cell_text(cell))
        ]

        for data_row in table_rows:
            row_text = _row_text(data_row)

            if _is_table_stop_row(row_text):
                continue

            layout_zone_rects.append(_rect_for_words(data_row, padding=1.0))

            for column_index in sensitive_column_indexes:
                if column_index >= len(column_bounds):
                    continue

                x0, x1 = column_bounds[column_index]

                value_words = [
                    word
                    for word in data_row
                    if x0 <= _center_x(word.rect) <= x1
                ]

                if not value_words:
                    continue

                if _looks_like_header_or_static_text(_row_text(value_words)):
                    continue

                header_text = _cell_text(cells[column_index])

                if _should_redact_full_cell(header_text):
                    redaction_rects.append(
                        _cell_rect_for_column_and_row(
                            row_words=data_row,
                            x0=x0,
                            x1=x1,
                            padding=0.9,
                        )
                    )
                else:
                    redaction_rects.append(_rect_for_words(value_words, padding=0.9))

        row_index += max(1, len(table_rows) + 1)


def _rows_belonging_to_header_table(
    rows: list[list[WordBox]],
    header_index: int,
) -> list[list[WordBox]]:
    header_row = rows[header_index]
    header_rect = _rect_for_words(header_row, padding=0)

    header_height = max(
        8.0,
        statistics.median(
            [
                word.rect.height
                for word in header_row
                if word.rect.height > 0
            ]
        ),
    )

    table_rows: list[list[WordBox]] = []
    previous_bottom = max(word.rect.y1 for word in header_row)

    for row in rows[header_index + 1 :]:
        if not row:
            continue

        row_top = min(word.rect.y0 for word in row)
        row_bottom = max(word.rect.y1 for word in row)
        vertical_gap = row_top - previous_bottom

        row_text = _row_text(row)

        if _is_table_stop_row(row_text):
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

    words = sorted(row, key=lambda word: word.rect.x0)

    heights = [
        word.rect.height
        for word in words
        if word.rect.height > 0
    ]

    median_height = statistics.median(heights) if heights else 10
    gap_threshold = max(8.0, median_height * 1.15)

    cells: list[list[WordBox]] = []
    current_cell: list[WordBox] = [words[0]]

    for previous, current in zip(words, words[1:]):
        gap = current.rect.x0 - previous.rect.x1

        if gap > gap_threshold:
            cells.append(current_cell)
            current_cell = [current]
        else:
            current_cell.append(current)

    if current_cell:
        cells.append(current_cell)

    return cells


def _is_sensitive_multicolumn_header(
    cells: list[list[WordBox]],
) -> bool:
    if len(cells) < 3:
        return False

    cell_texts = [
        normalize_token(_cell_text(cell))
        for cell in cells
    ]

    joined = " ".join(cell_texts)

    hits = 0

    for text in cell_texts:
        if _is_sensitive_header_cell(text):
            hits += 1

    has_table_shape = (
        "persona" in joined
        or "documento" in joined
        or "contacto" in joined
        or "direccion" in joined
        or "dirección" in joined
        or "datos" in joined
    )

    return hits >= 3 and has_table_shape


def _is_sensitive_header_cell(text: str) -> bool:
    normalized = normalize_token(text)

    if normalized in NON_SENSITIVE_TABLE_HEADER_KEYWORDS:
        return False

    return any(
        keyword in normalized
        for keyword in SENSITIVE_TABLE_HEADER_KEYWORDS
    )


def _column_bounds_from_header(
    header_cells: list[list[WordBox]],
    table_rows: list[list[WordBox]],
) -> list[tuple[float, float]]:
    header_cells = [
        sorted(cell, key=lambda word: word.rect.x0)
        for cell in header_cells
        if cell
    ]

    if not header_cells:
        return []

    starts = [
        min(word.rect.x0 for word in cell)
        for cell in header_cells
    ]

    all_words: list[WordBox] = []

    for row in table_rows:
        all_words.extend(row)

    if all_words:
        table_right = max(word.rect.x1 for word in all_words) + 4.0
    else:
        table_right = max(word.rect.x1 for cell in header_cells for word in cell) + 4.0

    bounds: list[tuple[float, float]] = []

    for index, start in enumerate(starts):
        left = start - 3.0

        if index + 1 < len(starts):
            right = starts[index + 1] - 3.0
        else:
            right = table_right

        if right > left:
            bounds.append((left, right))

    return bounds


def _has_table_like_gap(
    label_words: list[WordBox],
    value_words: list[WordBox],
) -> bool:
    if not label_words or not value_words:
        return False

    last_label = label_words[-1]
    first_value = value_words[0]

    gap = first_value.rect.x0 - last_label.rect.x1

    heights = [
        word.rect.height
        for word in label_words + value_words
        if word.rect.height > 0
    ]

    median_height = statistics.median(heights) if heights else 10

    return gap >= max(7.0, median_height * 0.65)


def _rect_should_be_skipped(
    rect: fitz.Rect,
    layout: DigitalLayoutAnalysis,
) -> bool:
    if _rect_center_inside_any(rect, layout.protected_rects):
        return True

    if _rect_overlap_ratio_with_any(rect, layout.protected_rects) >= 0.20:
        return True

    if _rect_center_inside_any(rect, layout.redaction_rects):
        return True

    return False


def _rect_center_inside_any(
    rect: fitz.Rect,
    containers: list[fitz.Rect],
) -> bool:
    center_x = _center_x(rect)
    center_y = _center_y(rect)

    for container in containers:
        expanded = _padded_rect(container, padding_x=1.5, padding_y=1.5)

        if (
            expanded.x0 <= center_x <= expanded.x1
            and expanded.y0 <= center_y <= expanded.y1
        ):
            return True

    return False


def _rect_overlap_ratio_with_any(
    rect: fitz.Rect,
    containers: list[fitz.Rect],
) -> float:
    rect_area = max(0.0, rect.width * rect.height)

    if rect_area <= 0:
        return 0.0

    best_ratio = 0.0

    for container in containers:
        intersection = rect & _padded_rect(container, padding_x=1.5, padding_y=1.5)
        intersection_area = max(0.0, intersection.width * intersection.height)
        best_ratio = max(best_ratio, intersection_area / rect_area)

    return best_ratio


def _is_table_stop_row(row_text: str) -> bool:
    normalized = normalize_token(row_text)

    if not normalized:
        return True

    return any(
        normalized.startswith(prefix)
        for prefix in TABLE_STOP_PREFIXES
    )


def _looks_like_header_or_static_text(text: str) -> bool:
    normalized = normalize_token(text)

    if not normalized:
        return True

    if _is_table_stop_row(normalized):
        return True

    if normalized in {
        "id caso",
        "persona",
        "documento",
        "contacto",
        "direccion",
        "dirección",
        "datos a anonimizar",
        "campo",
        "valor ficticio",
    }:
        return True

    return False


def _rect_for_words(
    words: list[WordBox],
    padding: float = 0.5,
) -> fitz.Rect:
    rect = fitz.Rect(words[0].rect)

    for word in words[1:]:
        rect |= word.rect

    rect.x0 -= padding
    rect.y0 -= padding
    rect.x1 += padding
    rect.y1 += padding

    return rect


def _cell_text(cell: list[WordBox]) -> str:
    return " ".join(word.text for word in cell).strip()


def _row_text(row: list[WordBox]) -> str:
    return " ".join(word.text for word in row).strip()


def _center_x(rect: fitz.Rect) -> float:
    return (rect.x0 + rect.x1) / 2


def _center_y(rect: fitz.Rect) -> float:
    return (rect.y0 + rect.y1) / 2


def _padded_rect(
    rect: fitz.Rect,
    padding_x: float,
    padding_y: float,
) -> fitz.Rect:
    return fitz.Rect(
        rect.x0 - padding_x,
        rect.y0 - padding_y,
        rect.x1 + padding_x,
        rect.y1 + padding_y,
    )


def _dedupe_rects(
    rects: list[fitz.Rect],
    tolerance: float = 0.8,
) -> list[fitz.Rect]:
    unique: list[fitz.Rect] = []

    for rect in rects:
        duplicate = False

        for existing in unique:
            if (
                abs(rect.x0 - existing.x0) <= tolerance
                and abs(rect.y0 - existing.y0) <= tolerance
                and abs(rect.x1 - existing.x1) <= tolerance
                and abs(rect.y1 - existing.y1) <= tolerance
            ):
                duplicate = True
                break

        if not duplicate:
            unique.append(rect)

    return unique

def _should_redact_full_cell(header_text: str) -> bool:
    normalized = normalize_token(header_text)

    return any(
        keyword in normalized
        for keyword in FULL_CELL_REDACTION_HEADER_KEYWORDS
    )


def _cell_rect_for_column_and_row(
    row_words: list[WordBox],
    x0: float,
    x1: float,
    padding: float = 0.8,
) -> fitz.Rect:
    if not row_words:
        return fitz.Rect(x0, 0, x1, 0)

    row_top = min(word.rect.y0 for word in row_words)
    row_bottom = max(word.rect.y1 for word in row_words)

    return fitz.Rect(
        x0 + padding,
        row_top - padding,
        x1 - padding,
        row_bottom + padding,
    )