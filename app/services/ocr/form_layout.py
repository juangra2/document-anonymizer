from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import statistics
import unicodedata

import fitz

from app.services.ocr.models import OcrToken


@dataclass(frozen=True)
class FieldSpec:
    entity_type: str
    labels: tuple[str, ...]


@dataclass
class RowTokenSpan:
    start: int
    end: int
    token: OcrToken


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        entity_type="PERSON",
        labels=(
            "nombre",
            "nombre completo",
            "solicitante",
            "titular",
            "persona",
            "medico responsable",
            "médico responsable",
            "doctor",
            "dra",
            "dr",
        ),
    ),
    FieldSpec(
        entity_type="ID_NUMBER",
        labels=(
            "dni",
            "nif",
            "nie",
            "dni nif",
            "nif nie",
            "dni/ nif",
            "nif/ nie",
            "nif/nie",
            "nif nie",
        ),
    ),
    FieldSpec(
        entity_type="EMAIL_ADDRESS",
        labels=(
            "email",
            "email personal",
            "correo",
            "correo personal",
            "correo electronico",
            "correo electrónico",
            "e mail",
        ),
    ),
    FieldSpec(
        entity_type="PHONE_NUMBER",
        labels=(
            "telefono",
            "teléfono",
            "telefono movil",
            "teléfono móvil",
            "telefono alternativo",
            "teléfono alternativo",
            "movil",
            "móvil",
        ),
    ),
    FieldSpec(
        entity_type="ADDRESS",
        labels=(
            "direccion",
            "dirección",
            "direccion postal",
            "dirección postal",
            "domicilio",
        ),
    ),
    FieldSpec(
        entity_type="IBAN_CODE",
        labels=(
            "iban",
            "cuenta bancaria",
        ),
    ),
    FieldSpec(
        entity_type="PASSPORT",
        labels=(
            "pasaporte",
        ),
    ),
    FieldSpec(
        entity_type="CREDIT_CARD",
        labels=(
            "tarjeta",
            "tarjeta bancaria",
            "tarjeta de prueba",
            "tarjeta bancaria de prueba",
        ),
    ),
    FieldSpec(
        entity_type="USER_ID",
        labels=(
            "usuario",
            "usuario interno",
            "user id",
        ),
    ),
    FieldSpec(
        entity_type="IP_ADDRESS",
        labels=(
            "ip",
            "ip ultima conexion",
            "ip última conexión",
            "ip de ultima conexion",
            "ip de última conexión",
            "ip reservada",
        ),
    ),
    FieldSpec(
        entity_type="MEDICAL_ID",
        labels=(
            "paciente id",
            "identificador medico",
            "identificador médico",
            "identificador sanitario",
        ),
    ),
    FieldSpec(
        entity_type="POLICY_NUMBER",
        labels=(
            "poliza",
            "póliza",
            "seguro medico",
            "seguro médico",
        ),
    ),
    FieldSpec(
        entity_type="LICENSE_PLATE",
        labels=(
            "matricula",
            "matrícula",
            "matricula vehiculo",
            "matrícula vehículo",
        ),
    ),
    FieldSpec(
        entity_type="CASE_NUMBER",
        labels=(
            "expediente",
            "caso",
        ),
    ),
    # ---------------------------------------------------------------
    # Valenciano / Catalán
    # ---------------------------------------------------------------
    FieldSpec(
        entity_type="PERSON",
        labels=(
            "nom",
            "nom complet",
            "cognoms",
            "nom i cognoms",
            "sollicitant",
            "titular",
        ),
    ),
    FieldSpec(
        entity_type="ID_NUMBER",
        labels=(
            "dni",
            "nif",
            "nie",
            "numero d'identificacio",
            "numero d'identificacio fiscal",
        ),
    ),
    FieldSpec(
        entity_type="EMAIL_ADDRESS",
        labels=(
            "correu",
            "correu electronic",
            "correu electronico",
            "adreca electronica",
            "adreca electronica",
        ),
    ),
    FieldSpec(
        entity_type="PHONE_NUMBER",
        labels=(
            "telefon",
            "mobil",
            "telefon mobil",
        ),
    ),
    FieldSpec(
        entity_type="ADDRESS",
        labels=(
            "adreca",
            "adreça",
            "adreca postal",
            "domicili",
            "domicili fiscal",
        ),
    ),
    FieldSpec(
        entity_type="DATE_OF_BIRTH",
        labels=(
            "data de naixement",
            "data naixement",
        ),
    ),
    FieldSpec(
        entity_type="IBAN_CODE",
        labels=(
            "compte bancari",
            "iban",
        ),
    ),
    FieldSpec(
        entity_type="PASSPORT",
        labels=(
            "passaport",
        ),
    ),
    FieldSpec(
        entity_type="LICENSE_PLATE",
        labels=(
            "matricula",
            "matricula del vehicle",
        ),
    ),
    FieldSpec(
        entity_type="CASE_NUMBER",
        labels=(
            "expedient",
            "numero d'expedient",
            "ref expedient",
        ),
    ),
    FieldSpec(
        entity_type="PROFESSIONAL_LICENSE",
        labels=(
            "numero de collegiat",
            "num collegiat",
            "col legiat",
        ),
    ),
    FieldSpec(
        entity_type="POSTAL_CODE",
        labels=(
            "codi postal",
        ),
    ),
    FieldSpec(
        entity_type="CADASTRAL_REFERENCE",
        labels=(
            "referencia cadastral",
            "referencia catastral",
        ),
    ),
)


SAFE_STATIC_TEXT = {
    "formulario",
    "formulario de prueba",
    "formulario de alta",
    "formulario de alta datos ficticios",
    "datos ficticios",
    "documento sintetico para pruebas",
    "documento sintético para pruebas",
}


NON_VALUE_LABELS = {
    "firma",
    "fecha",
    "asunto",
    "de",
    "para",
    "cc",
    "cco",
}


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("/", " ")
    value = value.replace("\\", " ")
    value = value.replace("_", " ")
    value = value.replace("-", " ")
    value = value.replace(":", " ")
    value = value.replace("|", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_text(value))


def padded_rect(
    rect: fitz.Rect,
    padding_x: float = 2.0,
    padding_y: float = 2.0,
) -> fitz.Rect:
    return fitz.Rect(
        rect.x0 - padding_x,
        rect.y0 - padding_y,
        rect.x1 + padding_x,
        rect.y1 + padding_y,
    )


def rect_for_token_range(
    token: OcrToken,
    local_start: int,
    local_end: int,
) -> fitz.Rect | None:
    text = token.text

    if not text:
        return None

    token_len = len(text)

    local_start = max(0, min(local_start, token_len))
    local_end = max(0, min(local_end, token_len))

    if local_start >= local_end:
        return None

    rect = fitz.Rect(token.rect)

    if local_start == 0 and local_end == token_len:
        return padded_rect(rect)

    width = rect.x1 - rect.x0

    x0 = rect.x0 + width * (local_start / token_len)
    x1 = rect.x0 + width * (local_end / token_len)

    return padded_rect(
        fitz.Rect(
            x0,
            rect.y0,
            x1,
            rect.y1,
        )
    )


class OcrFormLayoutAnalyzer:
    def group_tokens_by_row(
        self,
        tokens: list[OcrToken],
    ) -> list[list[OcrToken]]:
        if not tokens:
            return []

        sorted_tokens = sorted(
            tokens,
            key=lambda token: (
                token.rect.y0,
                token.rect.x0,
            ),
        )

        heights = [
            token.rect.y1 - token.rect.y0
            for token in sorted_tokens
            if token.rect.y1 > token.rect.y0
        ]

        median_height = statistics.median(heights) if heights else 12
        tolerance = max(8.0, median_height * 0.75)

        rows: list[list[OcrToken]] = []

        for token in sorted_tokens:
            y_center = (token.rect.y0 + token.rect.y1) / 2

            best_row: list[OcrToken] | None = None
            best_distance: float | None = None

            for row in rows:
                row_center = sum(
                    (item.rect.y0 + item.rect.y1) / 2 for item in row
                ) / len(row)

                distance = abs(y_center - row_center)

                if distance <= tolerance and (
                    best_distance is None or distance < best_distance
                ):
                    best_row = row
                    best_distance = distance

            if best_row is None:
                rows.append([token])
            else:
                best_row.append(token)

        for row in rows:
            row.sort(key=lambda token: token.rect.x0)

        rows.sort(key=lambda row: min(token.rect.y0 for token in row))

        return rows

    def rects_for_form_values(
        self,
        tokens: list[OcrToken],
    ) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []

        rows = self.group_tokens_by_row(tokens)

        for row_index, row in enumerate(rows):
            rects.extend(self._rects_for_labeled_row(row))

            # Si una etiqueta está sola y el valor cae justo debajo.
            rects.extend(
                self._rects_for_label_above_value(
                    rows=rows,
                    row_index=row_index,
                )
            )

        return self.merge_close_rects(rects)
    

    def has_form_layout(
        self,
        tokens: list[OcrToken],
        min_labels: int = 3,
    ) -> bool:
        rows = self.group_tokens_by_row(tokens)

        labels_found = 0

        for row in rows:
            label_end_index = self._find_label_prefix_token_end_index(row)

            if label_end_index is not None:
                labels_found += 1

        return labels_found >= min_labels
    

    def _rects_for_labeled_row(
        self,
        row: list[OcrToken],
    ) -> list[fitz.Rect]:
        row_text, spans = self._build_row_text_and_spans(row)

        if not row_text.strip():
            return []

        if self.is_safe_static_text(row_text):
            return []

        # Caso 1: una única caja OCR contiene "Etiqueta: valor".
        inline_rects = self._rects_for_inline_label_value(row, spans)

        if inline_rects:
            return inline_rects

        # Caso 2: OCR por tokens separados:
        # ["Correo personal:", "diego", "demo@example com"]
        label_end_index = self._find_label_prefix_token_end_index(row)

        if label_end_index is None:
            return []

        label_tokens = row[:label_end_index]
        value_tokens = row[label_end_index:]

        if not label_tokens or not value_tokens:
            return []

        label_rect = self._rect_for_tokens(label_tokens)

        valid_value_tokens: list[OcrToken] = []

        for token in value_tokens:
            if self.is_label_only(token.text):
                continue

            if self.is_safe_static_text(token.text):
                continue

            token_center_x = (token.rect.x0 + token.rect.x1) / 2

            # Solo valores claramente a la derecha de la etiqueta.
            if token_center_x <= label_rect.x1 + 6:
                continue

            valid_value_tokens.append(token)

        if not valid_value_tokens:
            return []

        # Importante: una sola caja continua para cubrir valores partidos.
        value_rect = self._rect_for_tokens(valid_value_tokens)

        return [
            padded_rect(
                value_rect,
                padding_x=4.0,
                padding_y=3.0,
            )
        ]

    def _rects_for_inline_label_value(
        self,
        row: list[OcrToken],
        spans: list[RowTokenSpan],
    ) -> list[fitz.Rect]:
        if len(row) != 1:
            return []

        token = row[0]
        text = token.text

        if self.is_label_only(text):
            return []

        for spec in FIELD_SPECS:
            for label in sorted(spec.labels, key=len, reverse=True):
                value_start = self._inline_value_start(
                    text=text,
                    label=label,
                )

                if value_start is None:
                    continue

                if value_start >= len(text):
                    continue

                rect = rect_for_token_range(
                    token=token,
                    local_start=value_start,
                    local_end=len(text),
                )

                return [rect] if rect is not None else []

        return []

    def _inline_value_start(
        self,
        text: str,
        label: str,
    ) -> int | None:
        if self.is_label_only(text):
            return None

        raw = text.strip()

        # Caso robusto con dos puntos.
        colon_index = raw.find(":")

        if colon_index != -1:
            before_colon = raw[:colon_index]
            after_colon_index = colon_index + 1

            if self._label_matches(before_colon, label):
                while (
                    after_colon_index < len(raw)
                    and raw[after_colon_index].isspace()
                ):
                    after_colon_index += 1

                return after_colon_index

        # Caso sin dos puntos: "Nombre Diego Ferrer Demo".
        normalized = normalize_text(raw)
        normalized_label = normalize_text(label)

        if normalized.startswith(normalized_label + " "):
            return self._approx_raw_index_after_label(
                raw_text=raw,
                normalized_label=normalized_label,
            )

        return None

    def _find_label_prefix_match(
        self,
        row: list[OcrToken],
        spans: list[RowTokenSpan],
    ) -> int | None:
        """
        Devuelve la posición textual final de la etiqueta.
        Se mantiene para compatibilidad con lógica previa.
        """
        end_index = self._find_label_prefix_token_end_index(row)

        if end_index is None:
            return None

        return spans[end_index - 1].end


    def _find_label_prefix_token_end_index(
        self,
        row: list[OcrToken],
    ) -> int | None:
        """
        Devuelve el índice de token donde termina la etiqueta.

        Ejemplo:
        ["Correo personal:", "diego", "demo@example com"]
        -> devuelve 1

        ["Telefono", "alternativo:", "+34 700..."]
        -> devuelve 2
        """
        max_label_tokens = min(5, len(row))

        for end_index in range(max_label_tokens, 0, -1):
            candidate_text = " ".join(token.text for token in row[:end_index])
            candidate_text = candidate_text.rstrip(": ")

            if self.is_label_only(candidate_text):
                return end_index

            for spec in FIELD_SPECS:
                for label in spec.labels:
                    if self._label_matches(candidate_text, label):
                        return end_index

        return None

    def _rects_for_label_above_value(
        self,
        rows: list[list[OcrToken]],
        row_index: int,
    ) -> list[fitz.Rect]:
        current_row = rows[row_index]

        if not current_row:
            return []

        row_text = " ".join(token.text for token in current_row)

        if not self.is_label_only(row_text):
            return []

        if normalize_text(row_text).strip(":") in NON_VALUE_LABELS:
            return []

        next_index = row_index + 1

        if next_index >= len(rows):
            return []

        next_row = rows[next_index]

        if not next_row:
            return []

        if self.is_label_only(" ".join(token.text for token in next_row)):
            return []

        current_bottom = max(token.rect.y1 for token in current_row)
        next_top = min(token.rect.y0 for token in next_row)

        current_height = max(token.rect.y1 - token.rect.y0 for token in current_row)

        if next_top - current_bottom > current_height * 2.2:
            return []

        return [
            padded_rect(
                self._rect_for_tokens(next_row),
            )
        ]

    def _rects_for_row_range(
        self,
        spans: list[RowTokenSpan],
        start: int,
        end: int,
    ) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []

        for span in spans:
            if span.end <= start or span.start >= end:
                continue

            local_start = max(start, span.start) - span.start
            local_end = min(end, span.end) - span.start

            token = span.token

            if self.is_label_only(token.text):
                continue

            rect = rect_for_token_range(
                token=token,
                local_start=local_start,
                local_end=local_end,
            )

            if rect is not None:
                rects.append(rect)

        return rects

    @staticmethod
    def _build_row_text_and_spans(
        row: list[OcrToken],
    ) -> tuple[str, list[RowTokenSpan]]:
        parts: list[str] = []
        spans: list[RowTokenSpan] = []
        cursor = 0

        for index, token in enumerate(row):
            if index > 0:
                parts.append(" ")
                cursor += 1

            start = cursor
            parts.append(token.text)
            cursor += len(token.text)
            end = cursor

            spans.append(
                RowTokenSpan(
                    start=start,
                    end=end,
                    token=token,
                )
            )

        return "".join(parts), spans

    def _rect_for_signature_row(
        self,
        row: list[OcrToken],
    ) -> fitz.Rect | None:
        firma_token: OcrToken | None = None
        fecha_token: OcrToken | None = None

        for token in row:
            normalized = normalize_text(token.text).strip(":")

            if normalized.startswith("firma"):
                firma_token = token

            if normalized.startswith("fecha"):
                fecha_token = token

        if firma_token is None:
            return None

        row_y0 = min(token.rect.y0 for token in row)
        row_y1 = max(token.rect.y1 for token in row)
        row_height = max(18.0, row_y1 - row_y0)

        x0 = firma_token.rect.x1 + row_height * 1.3

        if fecha_token is not None:
            x1 = fecha_token.rect.x0 - row_height * 1.2
        else:
            x1 = x0 + row_height * 9

        if x1 <= x0:
            return None

        # La firma suele estar por encima de la línea "Firma:", no debajo.
        return fitz.Rect(
            x0,
            row_y0 - row_height * 1.8,
            x1,
            row_y1 + row_height * 0.4,
        )

    @staticmethod
    def _rect_for_tokens(
        tokens: list[OcrToken],
    ) -> fitz.Rect:
        rect = fitz.Rect(tokens[0].rect)

        for token in tokens[1:]:
            rect |= token.rect

        return rect

    def is_label_only(
        self,
        text: str,
    ) -> bool:
        normalized = normalize_text(text).strip(":")

        if not normalized:
            return True

        label_values = set(NON_VALUE_LABELS)

        for spec in FIELD_SPECS:
            for label in spec.labels:
                label_values.add(normalize_text(label))

        return normalized in label_values

    def is_safe_static_text(
        self,
        text: str,
    ) -> bool:
        normalized = normalize_text(text)

        if normalized in SAFE_STATIC_TEXT:
            return True

        if normalized.startswith("formulario de alta"):
            return True

        if normalized.startswith("formulario de prueba"):
            return True

        return False

    def _label_matches(
        self,
        candidate: str,
        expected_label: str,
    ) -> bool:
        candidate_norm = normalize_text(candidate).strip(":")
        expected_norm = normalize_text(expected_label)

        if candidate_norm == expected_norm:
            return True

        candidate_compact = compact_text(candidate)
        expected_compact = compact_text(expected_label)

        if candidate_compact == expected_compact:
            return True

        if not candidate_compact or not expected_compact:
            return False

        if candidate_compact[0] != expected_compact[0]:
            return False

        ratio = SequenceMatcher(
            None,
            candidate_compact,
            expected_compact,
        ).ratio()

        return ratio >= 0.86

    def _approx_raw_index_after_label(
        self,
        raw_text: str,
        normalized_label: str,
    ) -> int:
        raw_words = raw_text.split()
        normalized_words = normalize_text(raw_text).split()
        label_words = normalized_label.split()

        if len(normalized_words) < len(label_words):
            return 0

        if normalized_words[: len(label_words)] != label_words:
            return 0

        consumed_raw = " ".join(raw_words[: len(label_words)])
        index = len(consumed_raw)

        while index < len(raw_text) and raw_text[index].isspace():
            index += 1

        return index

    @staticmethod
    def merge_close_rects(
        rects: list[fitz.Rect],
    ) -> list[fitz.Rect]:
        if not rects:
            return []

        ordered = sorted(
            rects,
            key=lambda rect: (
                rect.y0,
                rect.x0,
            ),
        )

        merged: list[fitz.Rect] = []

        for rect in ordered:
            if not merged:
                merged.append(fitz.Rect(rect))
                continue

            previous = merged[-1]

            same_line = (
                abs(previous.y0 - rect.y0) < 8
                and abs(previous.y1 - rect.y1) < 10
            )

            close_x = rect.x0 <= previous.x1 + 6

            if same_line and close_x:
                previous |= rect
            else:
                merged.append(fitz.Rect(rect))

        return merged