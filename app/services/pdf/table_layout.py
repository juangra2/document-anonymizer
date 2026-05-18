import re
import unicodedata

import fitz

from app.services.pdf.extractor import PageText, WordBox


FIELD_LABELS: list[list[str]] = [
    ["nombre", "completo"],
    ["nombre"],
    ["persona"],

    ["fecha", "de", "nacimiento"],
    ["fecha", "nacimiento"],

    ["dni/nif"],
    ["dni", "nif"],
    ["nif/dni"],
    ["nif", "dni"],
    ["nif/nie"],
    ["nif", "nie"],
    ["nie/nif"],
    ["nie", "nif"],
    ["dni"],
    ["nif"],
    ["nie"],
    ["nie", "representante"],

    ["documento", "nacional"],
    ["documento", "nacional", "de", "identidad"],

    ["cif", "empresa", "constructora"],
    ["cif", "empresa"],
    ["cif"],

    ["pasaporte"],

    ["telefono", "alternativo"],
    ["telefono", "contacto"],
    ["telefono", "movil"],
    ["telefono"],
    ["teléfono", "alternativo"],
    ["teléfono", "contacto"],
    ["teléfono", "móvil"],
    ["teléfono"],

    ["email", "contacto"],
    ["email", "personal"],
    ["email"],
    ["correo", "personal"],
    ["correo"],

    ["direccion", "postal"],
    ["dirección", "postal"],
    ["direccion", "de", "notificacion"],
    ["dirección", "de", "notificación"],
    ["direccion"],
    ["dirección"],

    ["iban"],

    ["tarjeta", "de", "prueba"],
    ["tarjeta", "bancaria", "de", "prueba"],
    ["tarjeta", "bancaria"],
    ["tarjeta"],

    ["usuario", "interno"],
    ["usuario"],

    ["ip", "de", "ultima", "conexion"],
    ["ip", "de", "última", "conexión"],
    ["ip", "ultima", "conexion"],
    ["ip", "última", "conexión"],
    ["ip", "reservada", "para", "documentacion"],
    ["ip", "reservada", "para", "documentación"],
    ["ip"],

    ["identificador", "medico"],
    ["identificador", "médico"],
    ["identificador", "sanitario"],
    ["paciente", "id"],

    ["poliza"],
    ["póliza"],
    ["seguro", "medico"],
    ["seguro", "médico"],

    ["matricula", "vehiculo", "visita", "obra"],
    ["matrícula", "vehículo", "visita", "obra"],
    ["matricula", "vehiculo"],
    ["matrícula", "vehículo"],
    ["matricula"],
    ["matrícula"],

    ["referencia", "catastral"],

    ["expediente", "municipal"],
    ["expediente"],

    ["numero", "de", "colegiado"],
    ["número", "de", "colegiado"],
    ["n", "colegiado"],
    ["nº", "colegiado"],
    ["colegiado"],

    ["url", "documentacion"],
    ["url", "documentación"],
    ["web"],

    # --- Valenciano / Catalán ---
    ["nom", "complet"],
    ["nom", "i", "cognoms"],
    ["nom"],
    ["cognoms"],
    ["sollicitant"],
    ["data", "de", "naixement"],
    ["data", "naixement"],
    ["passaport"],
    ["telefon", "mobil"],
    ["telefon"],
    ["mobil"],
    ["correu", "electronic"],
    ["correu"],
    ["adreca", "postal"],
    ["adreça", "postal"],
    ["adreca"],
    ["adreça"],
    ["domicili", "fiscal"],
    ["domicili"],
    ["compte", "bancari"],
    ["codi", "postal"],
    ["referencia", "cadastral"],
    ["expedient", "municipal"],
    ["expedient"],
    ["numero", "d'expedient"],
    ["numero", "de", "collegiat"],
    ["num", "collegiat"],
    ["matricula", "del", "vehicle"],
]


HEADER_STARTS = {
    "campo",
    "valor",
    "valor ficticio",
    "tipo",
    "ejemplo",
    "ejemplo ficticio",
    "id caso",
    "rol",
    "contacto",
    "datos a anonimizar",
}


SAFE_VALUE_TEXTS = {
    "de prueba",
    "prueba",
    "ficticio",
    "ficticia",
    "valor ficticio",
    "dato ficticio",
    "datos ficticios",
    "documento sintetico",
    "documento sintético",
    "documento sintetico para pruebas",
    "documento sintético para pruebas",
    "la tabla anterior contiene datos ficticios",
    "tabla anterior contiene datos ficticios",
    "contiene datos ficticios",
}


SENSITIVE_VALUE_HINT_RE = re.compile(
    r"("
    r"@|"
    r"\b\d{7,}\b|"
    r"\b\d{4}[-\s]?[A-Z]{3}\b|"
    r"\bES\d{2}|"
    r"\b[A-Z]\d{7}[A-Z0-9]\b|"
    r"\b\d{8}[A-Z]\b|"
    r"\b(?:EXP|REF|CAS|PAC|POLIZA|FAKE|USR|COACV)[-/_.]?[A-Z0-9]+"
    r")",
    re.IGNORECASE,
)


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_token(value: str) -> str:
    value = strip_accents(value).lower().strip()
    value = value.strip(":;,.")
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_label_tokens(tokens: list[str]) -> list[str]:
    return [normalize_token(token) for token in tokens]


NORMALIZED_FIELD_LABELS = sorted(
    [normalize_label_tokens(label) for label in FIELD_LABELS],
    key=len,
    reverse=True,
)


def group_words_by_visual_row(words: list[WordBox]) -> list[list[WordBox]]:
    if not words:
        return []

    sorted_words = sorted(words, key=lambda word: (word.rect.y0, word.rect.x0))
    heights = [word.rect.height for word in sorted_words if word.rect.height > 0]
    avg_height = sum(heights) / len(heights) if heights else 10
    tolerance = max(3.0, avg_height * 0.55)

    rows: list[list[WordBox]] = []

    for word in sorted_words:
        y_center = (word.rect.y0 + word.rect.y1) / 2

        matched_row: list[WordBox] | None = None
        matched_distance: float | None = None

        for row in rows:
            row_center = sum((w.rect.y0 + w.rect.y1) / 2 for w in row) / len(row)
            distance = abs(y_center - row_center)

            if distance <= tolerance and (
                matched_distance is None or distance < matched_distance
            ):
                matched_row = row
                matched_distance = distance

        if matched_row is None:
            rows.append([word])
        else:
            matched_row.append(word)

    for row in rows:
        row.sort(key=lambda word: word.rect.x0)

    rows.sort(key=lambda row: min(word.rect.y0 for word in row))

    return rows


def row_starts_with_header(row_tokens: list[str]) -> bool:
    row_text = " ".join(row_tokens)

    return any(row_text.startswith(header) for header in HEADER_STARTS)


def find_label_length_at_row_start(row_tokens: list[str]) -> int | None:
    if row_starts_with_header(row_tokens):
        return None

    for label_tokens in NORMALIZED_FIELD_LABELS:
        label_len = len(label_tokens)

        if len(row_tokens) < label_len + 1:
            continue

        if row_tokens[:label_len] == label_tokens:
            return label_len

    return None


def rect_for_words(words: list[WordBox], padding: float = 0.5) -> fitz.Rect:
    rect = fitz.Rect(words[0].rect)

    for word in words[1:]:
        rect |= word.rect

    rect.x0 -= padding
    rect.y0 -= padding
    rect.x1 += padding
    rect.y1 += padding

    return rect


def rects_for_labeled_table_values(
    page_text: PageText,
    padding: float = 0.5,
) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []

    rows = group_words_by_visual_row(page_text.words)

    for row in rows:
        row_tokens = [normalize_token(word.text) for word in row]
        label_length = find_label_length_at_row_start(row_tokens)

        if label_length is None:
            continue

        label_words = row[:label_length]
        value_words = row[label_length:]

        if not value_words:
            continue

        value_text = " ".join(word.text for word in value_words).strip()

        if not value_text:
            continue

        if _looks_like_safe_value(value_text):
            continue

        if not _has_table_like_gap(label_words, value_words):
            continue

        rects.append(rect_for_words(value_words, padding=padding))

    return rects


def _has_table_like_gap(
    label_words: list[WordBox],
    value_words: list[WordBox],
) -> bool:
    if not label_words or not value_words:
        return False

    last_label = label_words[-1]
    first_value = value_words[0]

    gap = first_value.rect.x0 - last_label.rect.x1
    avg_height = sum(word.rect.height for word in label_words + value_words) / (
        len(label_words) + len(value_words)
    )

    return gap >= max(8.0, avg_height * 0.8)


def _looks_like_safe_value(value: str) -> bool:
    normalized = normalize_token(value)

    # Solo consideramos seguros valores claramente no sensibles,
    # normalmente restos de títulos o texto boilerplate corto.
    if normalized in SAFE_VALUE_TEXTS:
        return True

    if normalized.startswith("de prueba"):
        return True

    if normalized in {
        "prueba",
        "ficticio",
        "ficticia",
        "datos ficticios",
        "valor ficticio",
        "dato ficticio",
    }:
        return True

    # Si contiene un patrón sensible claro, nunca es seguro.
    if SENSITIVE_VALUE_HINT_RE.search(value):
        return False

    # Si tiene números, no lo consideramos seguro:
    # puede ser fecha, DNI, código postal, teléfono, matrícula, expediente, etc.
    if any(char.isdigit() for char in normalized):
        return False

    # Importante:
    # No marcar como seguro cualquier texto corto sin números,
    # porque puede ser un nombre: "Lucia Martin Pruebas".
    return False