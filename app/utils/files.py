import re
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import DocumentTooLargeError, FileValidationError


PII_FILENAME_PATTERNS = [
    re.compile(r"\b\d{8}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[XYZ]\d{7}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[A-HJNPQRSUVW]\d{7}[0-9A-J]\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+34[\s\-]?)?(?:6|7|8|9)\d{2}[\s\-]?\d{3}[\s\-]?\d{3}(?!\d)"),
    re.compile(r"\bES\d{2}(?:[\s\-]?\d{4}){5}\b", re.IGNORECASE),
]


class FileTooLargeError(DocumentTooLargeError):
    pass


async def save_upload_to_path(upload: UploadFile, destination: Path) -> int:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0

    with destination.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)

            if not chunk:
                break

            written += len(chunk)

            if written > max_bytes:
                raise FileTooLargeError(
                    f"File exceeds max size of {settings.max_upload_mb} MB"
                )

            out.write(chunk)

    return written


def assert_pdf_magic(path: Path) -> None:
    with path.open("rb") as f:
        header = f.read(5)

    if header != b"%PDF-":
        raise FileValidationError("Uploaded file is not a valid PDF.")


def sanitize_filename_stem(original_filename: str | None) -> str:
    if not original_filename:
        return "document"

    stem = Path(Path(original_filename).name).stem

    for pattern in PII_FILENAME_PATTERNS:
        stem = pattern.sub("redacted", stem)

    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")

    if not safe_stem:
        safe_stem = "document"

    return safe_stem[:80]


def build_download_filename(
    original_filename: str | None,
    suffix: str,
    extension: str,
) -> str:
    stem = sanitize_filename_stem(original_filename)
    return f"{stem}{suffix}.{extension}"


def sanitize_original_filename(original_filename: str | None) -> str | None:
    if not original_filename:
        return None

    name = Path(original_filename).name

    for pattern in PII_FILENAME_PATTERNS:
        name = pattern.sub("redacted", name)

    name = re.sub(r"[^A-Za-z0-9_.\- ]+", "_", name).strip()

    return name[:120] or None