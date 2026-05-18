from __future__ import annotations

import re

import fitz

from app.services.ocr.form_layout import (
    OcrFormLayoutAnalyzer,
    normalize_text,
    padded_rect,
)
from app.services.ocr.models import OcrToken


CASE_ROW_ID_RE = re.compile(
    r"^\s*(?:CAS|CASE|EXP|EXPTE|EXPDTE|REF)[\s\-_/.:]*[A-Z0-9]+",
    re.IGNORECASE,
)

NUMERIC_CASE_ROW_ID_RE = re.compile(
    r"^\s*(?:ID)?[\s\-_/.:]*\d{2,}",
    re.IGNORECASE,
)


class OcrTableLayoutAnalyzer:
    """
    Detector OCR de tablas.

    Cubre casos como:
    - ID caso | Persona | Contacto | Datos a anonimizar
    - CAS-001 | Diego Ferrer Demo | diego@example.com | Calle...
    - Tipo | Ejemplo ficticio
    - DNI/NIF | 00000000X
    """

    def __init__(self) -> None:
        self._form_layout = OcrFormLayoutAnalyzer()

    def rects_for_tables(
        self,
        tokens: list[OcrToken],
    ) -> list[fitz.Rect]:
        rows = self._form_layout.group_tokens_by_row(tokens)

        rects: list[fitz.Rect] = []

        rects.extend(self._rects_for_case_like_rows(rows))
        rects.extend(self._rects_for_type_example_tables(rows))
        rects.extend(self._rects_for_sensitive_header_tables(rows))

        return self._form_layout.merge_close_rects(rects)

    def _rects_for_case_like_rows(
        self,
        rows: list[list[OcrToken]],
    ) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []

        for row in rows:
            if len(row) < 2:
                continue

            first_token = row[0]

            if not self._is_case_row_id(first_token.text):
                continue

            value_tokens = row[1:]

            if not value_tokens:
                continue

            rects.append(
                padded_rect(
                    self._rect_for_tokens(value_tokens),
                    padding_x=4.0,
                    padding_y=3.0,
                )
            )

        return rects

    def _rects_for_type_example_tables(
        self,
        rows: list[list[OcrToken]],
    ) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []

        active = False
        value_x0: float | None = None
        previous_y1: float | None = None

        for row in rows:
            row_text = self._row_text(row)
            normalized = normalize_text(row_text)

            if self._is_type_example_header(normalized):
                active = True
                value_x0 = self._find_header_value_x0(row)
                previous_y1 = max(token.rect.y1 for token in row)
                continue

            if not active or value_x0 is None:
                continue

            if self._should_stop_table(row, previous_y1):
                active = False
                value_x0 = None
                previous_y1 = None
                continue

            value_tokens = [
                token
                for token in row
                if self._center_x(token) >= value_x0 - 8
                and not self._form_layout.is_label_only(token.text)
                and not self._form_layout.is_safe_static_text(token.text)
            ]

            if value_tokens:
                rects.append(
                    padded_rect(
                        self._rect_for_tokens(value_tokens),
                        padding_x=4.0,
                        padding_y=3.0,
                    )
                )

            previous_y1 = max(token.rect.y1 for token in row)

        return rects

    def _rects_for_sensitive_header_tables(
        self,
        rows: list[list[OcrToken]],
    ) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []

        active = False
        value_x0: float | None = None
        previous_y1: float | None = None

        for row in rows:
            row_text = self._row_text(row)
            normalized = normalize_text(row_text)

            if self._is_sensitive_table_header(normalized):
                active = True
                value_x0 = self._find_first_sensitive_column_x0(row)
                previous_y1 = max(token.rect.y1 for token in row)
                continue

            if not active or value_x0 is None:
                continue

            if self._should_stop_table(row, previous_y1):
                active = False
                value_x0 = None
                previous_y1 = None
                continue

            if self._is_table_header_like(normalized):
                previous_y1 = max(token.rect.y1 for token in row)
                continue

            value_tokens = [
                token
                for token in row
                if self._center_x(token) >= value_x0 - 8
                and not self._form_layout.is_label_only(token.text)
                and not self._form_layout.is_safe_static_text(token.text)
            ]

            if value_tokens:
                rects.append(
                    padded_rect(
                        self._rect_for_tokens(value_tokens),
                        padding_x=4.0,
                        padding_y=3.0,
                    )
                )

            previous_y1 = max(token.rect.y1 for token in row)

        return rects

    @staticmethod
    def _row_text(
        row: list[OcrToken],
    ) -> str:
        return " ".join(token.text for token in row).strip()

    @staticmethod
    def _center_x(
        token: OcrToken,
    ) -> float:
        return (token.rect.x0 + token.rect.x1) / 2

    @staticmethod
    def _rect_for_tokens(
        tokens: list[OcrToken],
    ) -> fitz.Rect:
        rect = fitz.Rect(tokens[0].rect)

        for token in tokens[1:]:
            rect |= token.rect

        return rect

    def _is_case_row_id(
        self,
        value: str,
    ) -> bool:
        stripped = value.strip()

        if CASE_ROW_ID_RE.match(stripped):
            return True

        return False

    @staticmethod
    def _is_type_example_header(
        normalized: str,
    ) -> bool:
        return (
            "tipo" in normalized
            and (
                "ejemplo" in normalized
                or "valor ficticio" in normalized
                or "dato ficticio" in normalized
            )
        )

    @staticmethod
    def _is_sensitive_table_header(
        normalized: str,
    ) -> bool:
        has_id = "id caso" in normalized or normalized.startswith("id ")
        has_person = "persona" in normalized or "nombre" in normalized
        has_contact = "contacto" in normalized or "email" in normalized or "correo" in normalized
        has_sensitive_data = (
            "datos a anonimizar" in normalized
            or "dato a anonimizar" in normalized
            or "informacion sensible" in normalized
            or "información sensible" in normalized
        )

        if has_id and (has_person or has_contact or has_sensitive_data):
            return True

        if "campo" in normalized and (
            "valor" in normalized
            or "valor ficticio" in normalized
            or "ejemplo" in normalized
        ):
            return True

        return False

    @staticmethod
    def _is_table_header_like(
        normalized: str,
    ) -> bool:
        header_words = {
            "campo",
            "valor",
            "valor ficticio",
            "tipo",
            "ejemplo",
            "ejemplo ficticio",
            "id caso",
            "persona",
            "contacto",
            "datos a anonimizar",
        }

        return normalized in header_words

    def _find_header_value_x0(
        self,
        row: list[OcrToken],
    ) -> float:
        candidates: list[float] = []

        for token in row:
            normalized = normalize_text(token.text)

            if (
                "ejemplo" in normalized
                or "valor" in normalized
                or "ficticio" in normalized
            ):
                candidates.append(float(token.rect.x0))

        if candidates:
            return min(candidates)

        if len(row) >= 2:
            return float(row[1].rect.x0)

        return float(row[0].rect.x1)

    def _find_first_sensitive_column_x0(
        self,
        row: list[OcrToken],
    ) -> float:
        sensitive_header_words = (
            "persona",
            "nombre",
            "contacto",
            "datos",
            "dato",
            "valor",
            "ejemplo",
        )

        candidates: list[float] = []

        for token in row:
            normalized = normalize_text(token.text)

            if any(word in normalized for word in sensitive_header_words):
                candidates.append(float(token.rect.x0))

        if candidates:
            return min(candidates)

        if len(row) >= 2:
            return float(row[1].rect.x0)

        return float(row[0].rect.x1)

    def _should_stop_table(
        self,
        row: list[OcrToken],
        previous_y1: float | None,
    ) -> bool:
        if not row:
            return True

        row_text = self._row_text(row)
        normalized = normalize_text(row_text)

        if not normalized:
            return True

        section_prefixes = (
            "seccion",
            "sección",
            "apartado",
            "anexo",
            "observaciones",
            "nota",
            "firma",
            "fecha",
            "formulario",
        )

        if normalized.startswith(section_prefixes):
            return True

        if previous_y1 is not None:
            current_y0 = min(token.rect.y0 for token in row)
            row_height = max(token.rect.height for token in row)

            if current_y0 - previous_y1 > max(45.0, row_height * 3.0):
                return True

        return False